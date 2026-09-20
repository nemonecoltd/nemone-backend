"""전자책 판매 — 상품/권한(entitlement)/읽기 진행률 API (지시서 5장).

핵심 원칙: **권한 판정은 전부 서버에서 한다.** 클라이언트가 보낸 "나 구매했어요"는 절대
신뢰하지 않는다. user_id는 반드시 deps.verify_supabase_user가 검증한 토큰에서만 얻는다.

Phase 1에는 결제가 없다. 권한은 /entitlements/grant(관리자 전용)로 수동 부여해 구조를
먼저 검증하고, 결제 연동과 orders 테이블은 Phase 2에서 붙인다.
"""
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import (
    Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Session

from deps import verify_supabase_user

router = APIRouter()


# ── 모델 ────────────────────────────────────────────────────────────────────
def define_models(Base):
    """main.py의 Base를 받아 모델을 정의한다(순환 import 방지).
    main.py가 Base.metadata.create_all()을 부르기 전에 호출되어야 테이블이 생성된다."""

    class Product(Base):
        __tablename__ = "products"
        # 'civilization:full', 'civilization:p2' 형태
        id = Column(String, primary_key=True, index=True)
        book_slug = Column(String, nullable=False, index=True)
        kind = Column(String, nullable=False)  # 'full' | 'part' | 'bundle'
        part_no = Column(Integer, nullable=True)
        title = Column(String, nullable=False)
        price = Column(Integer, nullable=False, default=0)
        active = Column(Boolean, default=True)

    class Entitlement(Base):
        __tablename__ = "entitlements"
        id = Column(Integer, primary_key=True, index=True)
        user_id = Column(String, nullable=False, index=True)  # Supabase UUID
        product_id = Column(String, nullable=False, index=True)
        granted_at = Column(DateTime, default=func.now())
        source = Column(String, nullable=False)  # 'purchase' | 'manual' | 'gift'
        __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_entitlement"),)

    class ReadingProgress(Base):
        __tablename__ = "reading_progress"
        user_id = Column(String, primary_key=True)
        book_slug = Column(String, primary_key=True)
        chapter = Column(String, nullable=False)
        percent = Column(Integer, default=0)
        updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    return Product, Entitlement, ReadingProgress


# main.py가 define_models 호출 후 여기에 주입한다
Product = None
Entitlement = None
ReadingProgress = None
get_db = None


def bind(product_model, entitlement_model, progress_model, db_dependency):
    global Product, Entitlement, ReadingProgress, get_db
    Product = product_model
    Entitlement = entitlement_model
    ReadingProgress = progress_model
    get_db = db_dependency


def _db():
    """main.py의 get_db를 런타임에 위임 — bind() 전에 참조되지 않도록 감싼다."""
    yield from get_db()


# ── 권한 판정 (지시서 5-3장) ────────────────────────────────────────────────
# 장 슬러그가 'p{부}c{장}' 형태로 부 번호를 품고 있어, 책 구조를 백엔드에 중복 정의하지 않고
# 슬러그만으로 소속 부를 판정한다. (지시서 최초본이 3·4·5부 장 범위를 한 장씩 잘못 적어
# 정정한 전례가 있어, 사람이 손으로 옮겨 적는 매핑 테이블 자체를 두지 않는 편이 안전하다.)
_CHAPTER_RE = re.compile(r"^p(\d+)c\d+$")


def chapter_part_no(chapter_slug: str) -> Optional[int]:
    m = _CHAPTER_RE.match(chapter_slug)
    return int(m.group(1)) if m else None


def can_read(chapter_slug: str, book_slug: str, product_ids: set) -> bool:
    """이 사용자가 해당 장을 읽을 수 있는가.

    - `{book}:full` 보유 → 전 장
    - `{book}:p{N}` 보유 → N부 소속 장만
    - 프롤로그/에필로그처럼 부에 속하지 않는 장은 full로만 열람 가능
      (프롤로그는 무료라 애초에 이 함수를 거치지 않는다)
    """
    if f"{book_slug}:full" in product_ids:
        return True
    part_no = chapter_part_no(chapter_slug)
    return part_no is not None and f"{book_slug}:p{part_no}" in product_ids


def owned_product_ids(db: Session, user_id: str, book_slug: Optional[str] = None) -> set:
    q = db.query(Entitlement.product_id).filter(Entitlement.user_id == user_id)
    ids = {row[0] for row in q.all()}
    if book_slug:
        ids = {pid for pid in ids if pid.startswith(f"{book_slug}:")}
    return ids


# ── 스키마 ──────────────────────────────────────────────────────────────────
class GrantIn(BaseModel):
    user_id: str
    product_id: str
    source: str = "manual"


class ProgressIn(BaseModel):
    chapter: str
    percent: int = 0


# ── 조회 API (로그인 필요) ──────────────────────────────────────────────────
@router.get("/entitlements/me")
def my_entitlements(viewer: dict = Depends(verify_supabase_user), db: Session = Depends(_db)):
    rows = db.query(Entitlement).filter(Entitlement.user_id == viewer["id"]).all()
    return [
        {"id": r.id, "product_id": r.product_id, "source": r.source, "granted_at": r.granted_at}
        for r in rows
    ]


@router.get("/entitlements/check")
def check_entitlement(
    product_id: str,
    viewer: dict = Depends(verify_supabase_user),
    db: Session = Depends(_db),
):
    owned = (
        db.query(Entitlement)
        .filter(Entitlement.user_id == viewer["id"], Entitlement.product_id == product_id)
        .first()
        is not None
    )
    return {"product_id": product_id, "owned": owned}


@router.get("/entitlements/chapters")
def readable_chapters(
    book: str,
    chapters: str = "",
    viewer: dict = Depends(verify_supabase_user),
    db: Session = Depends(_db),
):
    """목차 화면이 한 번의 호출로 전체 배지를 그릴 수 있게, 열람 가능한 장 슬러그를 돌려준다.
    (장마다 따로 물어보면 25번 요청이 나간다.)
    `chapters`는 쉼표로 구분된 슬러그 목록이며, 생략하면 보유 상품 정보만 반환한다."""
    ids = owned_product_ids(db, viewer["id"], book)
    requested = [c for c in chapters.split(",") if c]
    return {
        "book": book,
        "products": sorted(ids),
        "full": f"{book}:full" in ids,
        "readable": [c for c in requested if can_read(c, book, ids)],
    }


# ── 관리자 API (Phase 1 수동 부여) ──────────────────────────────────────────
def register_admin_routes(app_router: APIRouter, verify_admin):
    """x-admin-secret 의존성은 main.py에 있어 주입받는다."""

    @app_router.post("/entitlements/grant", dependencies=[Depends(verify_admin)])
    def grant(payload: GrantIn, db: Session = Depends(_db)):
        exists = (
            db.query(Entitlement)
            .filter(
                Entitlement.user_id == payload.user_id,
                Entitlement.product_id == payload.product_id,
            )
            .first()
        )
        if exists:
            return {"status": "already_granted", "id": exists.id}
        row = Entitlement(
            user_id=payload.user_id, product_id=payload.product_id, source=payload.source
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {"status": "granted", "id": row.id}

    @app_router.delete("/entitlements/{entitlement_id}", dependencies=[Depends(verify_admin)])
    def revoke(entitlement_id: int, db: Session = Depends(_db)):
        row = db.query(Entitlement).filter(Entitlement.id == entitlement_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="권한을 찾을 수 없습니다")
        db.delete(row)
        db.commit()
        return {"status": "revoked"}


# ── 읽기 진행률 ─────────────────────────────────────────────────────────────
@router.get("/reading-progress/{book}")
def get_progress(book: str, viewer: dict = Depends(verify_supabase_user), db: Session = Depends(_db)):
    row = (
        db.query(ReadingProgress)
        .filter(ReadingProgress.user_id == viewer["id"], ReadingProgress.book_slug == book)
        .first()
    )
    if not row:
        return {"book": book, "chapter": None, "percent": 0}
    return {"book": book, "chapter": row.chapter, "percent": row.percent, "updated_at": row.updated_at}


@router.put("/reading-progress/{book}")
def put_progress(
    book: str,
    payload: ProgressIn,
    viewer: dict = Depends(verify_supabase_user),
    db: Session = Depends(_db),
):
    percent = max(0, min(100, payload.percent))
    row = (
        db.query(ReadingProgress)
        .filter(ReadingProgress.user_id == viewer["id"], ReadingProgress.book_slug == book)
        .first()
    )
    if row:
        row.chapter = payload.chapter
        row.percent = percent
        row.updated_at = datetime.utcnow()
    else:
        db.add(
            ReadingProgress(
                user_id=viewer["id"], book_slug=book, chapter=payload.chapter, percent=percent
            )
        )
    db.commit()
    return {"status": "saved", "chapter": payload.chapter, "percent": percent}
