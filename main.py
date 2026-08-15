from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form, Request, Header, BackgroundTasks
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Date, update, or_
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from sqlalchemy.sql import func
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from google.cloud import storage as gcs_storage
from apscheduler.schedulers.background import BackgroundScheduler
import uvicorn
import os
import uuid
import re
import json
import threading
import asyncio
import urllib.request
from datetime import date, datetime, timedelta

load_dotenv()

# --- [병목 해결] 에디터 내 대용량 데이터(1MB 이상) 허용 설정 ---
import multipart
try:
    multipart.multipart.MAX_FIELDS_SIZE = 100 * 1024 * 1024 # 100MB 확장
    multipart.multipart.MAX_PART_SIZE = 100 * 1024 * 1024   # 100MB 확장 (1024KB 병목 해결)
except:
    pass
# -----------------------------------------------------------

SQLALCHEMY_DATABASE_URL = (
    f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME')}"
)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=3,
    max_overflow=5,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,  # 커넥션 재사용 전 헬스체크 — 없으면 끊긴 커넥션을 그대로 쓰다 "server closed the connection unexpectedly"로 요청이 실패함
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Post(Base):
    __tablename__ = "posts"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    body_text = Column(Text)
    category = Column(String, default="Taste")
    image_url = Column(String, nullable=True)
    content_type = Column(String, default="YOUTUBE")
    video_url = Column(String, nullable=True)
    tags = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now())
    view_count = Column(Integer, default=0)

    comments = relationship("Comment", back_populates="post", cascade="all, delete-orphan")
    likes = relationship("Like", back_populates="post", cascade="all, delete-orphan")

class Comment(Base):
    __tablename__ = "comments"
    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id"))
    user_id = Column(String) # Supabase UUID
    user_name = Column(String)
    user_image = Column(String, nullable=True)
    content = Column(Text)
    created_at = Column(DateTime, default=func.now())
    post = relationship("Post", back_populates="comments")

class Like(Base):
    __tablename__ = "likes"
    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id"))
    user_id = Column(String) # Supabase UUID
    created_at = Column(DateTime, default=func.now())
    post = relationship("Post", back_populates="likes")

class Special(Base):
    __tablename__ = "specials"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    description = Column(Text)
    bg_image_url = Column(String, nullable=True)
    post_ids = Column(Text)  # JSON list of post IDs: [1, 2, 3]
    is_main = Column(Integer, default=0) # 1 if pinned to main, 0 otherwise
    tags = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now())

class NemoneNews(Base):
    __tablename__ = "nemone_news"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    content = Column(Text)
    created_at = Column(DateTime, default=func.now())

class DailyStat(Base):
    __tablename__ = "daily_stats"
    date = Column(Date, primary_key=True, default=func.current_date())
    visitors = Column(Integer, default=0)
    total_views = Column(Integer, default=0)

class PostView(Base):
    """조회 발생 시각 로그 — 주간 랭킹에서 '최근 N일 조회수'를 계산하기 위함.
    Post.view_count(전체 누적)와 별개로 오늘부터 쌓임 — 과거 조회는 소급 불가."""
    __tablename__ = "post_views"
    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id"), index=True)
    viewed_at = Column(DateTime, default=func.now(), index=True)

Base.metadata.create_all(bind=engine)

app = FastAPI()

# 업로드 용량 제한 확장 (50MB)
@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    content_length = request.headers.get('content-length')
    if content_length and int(content_length) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Request entity too large")
    return await call_next(request)

GCS_BUCKET = os.getenv("GCS_BUCKET_NAME", "nemoneai-thumbnails")
_gcs_client = None

def upload_to_gcs(file_obj, filename: str) -> str:
    """썸네일 업로드 — 원본을 그대로 올리면 용량이 커서(수백KB~) Googlebot 크롤링 시 렌더링
    타임아웃의 원인이 될 수 있어, 리사이즈+WebP 압축 후 업로드(now_back의 이미지 처리 방식과 동일).
    PIL로 못 여는 파일(원본이 이미 손상됐거나 특수 포맷)이면 원본 그대로 업로드해 업로드 자체는 실패하지 않게 함."""
    global _gcs_client
    if _gcs_client is None:
        _gcs_client = gcs_storage.Client()
    bucket = _gcs_client.bucket(GCS_BUCKET)

    raw_bytes = file_obj.read()
    try:
        from PIL import Image
        import io as _io
        img = Image.open(_io.BytesIO(raw_bytes))
        img = img.convert("RGB")
        max_width = 1600
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)))
        buf = _io.BytesIO()
        img.save(buf, format="WEBP", quality=85)
        upload_bytes = buf.getvalue()
        filename = os.path.splitext(filename)[0] + ".webp"
        content_type = "image/webp"
    except Exception:
        upload_bytes = raw_bytes
        content_type = None

    blob = bucket.blob(filename)
    blob.upload_from_string(upload_bytes, content_type=content_type)
    return f"https://storage.googleapis.com/{GCS_BUCKET}/{filename}"

# Nginx에서 CORS(Access-Control-Allow-Origin: *)를 이미 추가하고 있으므로, 
# 백엔드에서는 중복 추가를 방지하기 위해 CORSMiddleware를 사용하지 않습니다.
# 대신 브라우저의 OPTIONS(Preflight) 요청에 200 OK만 응답하도록 라우팅합니다.
@app.options("/{full_path:path}")
def preflight_handler(request: Request, full_path: str):
    from fastapi.responses import JSONResponse
    return JSONResponse(content={"message": "OK"})

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

ADMIN_SECRET_KEY = os.getenv("ADMIN_SECRET_KEY")
NEWS_SECRET_KEY = os.getenv("NEWS_SECRET_KEY")

async def verify_admin(x_admin_secret: Optional[str] = Header(None)):
    if not ADMIN_SECRET_KEY or x_admin_secret != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")

# --- API 엔드포인트 (오직 Form 데이터만 받는 안정적인 구조) ---

@app.get("/posts")
def get_posts(category: Optional[str] = None, q: Optional[str] = None, skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    query = db.query(Post)
    if category:
        query = query.filter(Post.category == category)
    if q:
        q = q.strip()
        pattern = f"%{q}%"
        conditions = [Post.title.ilike(pattern), Post.body_text.ilike(pattern), Post.category.ilike(pattern), Post.tags.ilike(pattern)]
        if q.lstrip("#").isdigit():
            conditions.append(Post.id == int(q.lstrip("#")))
        query = query.filter(or_(*conditions))
    total = query.count()
    posts = query.order_by(Post.id.desc()).offset(skip).limit(limit).all()
    def summarize(post):
        d = {c.name: getattr(post, c.name) for c in post.__table__.columns}
        d["body_text"] = re.sub(r'<[^>]+>', '', d.get("body_text") or "")[:200]
        return d
    return {"total": total, "posts": [summarize(p) for p in posts]}

_top_ranking_cache: list = []

def refresh_top_ranking():
    """주간 TOP10 랭킹 재계산 — 하루 2회(한국시간 자정/낮 12시)만 실행, 매 요청마다 재계산하지 않도록 캐싱.
    조회수/댓글수/좋아요수를 각각 별도 필드로 보관 (어드민 TOP10 표시용) + 가중합산 score(공개 TOP3용).

    주의: 예전엔 Post.created_at(게시일)로 후보를 걸러서 "최근에 쓴 글"만 후보가 됐고, 그 안에서도
    좋아요/댓글/조회수는 전체기간 누적이라 오래된 글이 게시일 창 안에 있으면 계속 상위권을 차지했음.
    지금은 좋아요/댓글/조회수 자체를 각각 최근 N일로 스코프해서, 게시일과 무관하게
    "이번 주에 실제로 인기 있는 글"을 잡도록 수정."""
    global _top_ranking_cache
    db = SessionLocal()
    try:
        def _query(since: datetime, limit: int):
            view_counts = (
                db.query(PostView.post_id, func.count(PostView.id).label("view_count"))
                .filter(PostView.viewed_at >= since)
                .group_by(PostView.post_id)
                .subquery()
            )
            comment_counts = (
                db.query(Comment.post_id, func.count(Comment.id).label("comment_count"))
                .filter(Comment.created_at >= since)
                .group_by(Comment.post_id)
                .subquery()
            )
            like_counts = (
                db.query(Like.post_id, func.count(Like.id).label("like_count"))
                .filter(Like.created_at >= since)
                .group_by(Like.post_id)
                .subquery()
            )
            view_count_expr = func.coalesce(view_counts.c.view_count, 0)
            comment_count_expr = func.coalesce(comment_counts.c.comment_count, 0)
            like_count_expr = func.coalesce(like_counts.c.like_count, 0)
            score_expr = view_count_expr + comment_count_expr * 2 + like_count_expr * 3

            return (
                db.query(
                    Post.id, Post.title, Post.category, Post.image_url, Post.created_at,
                    view_count_expr.label("view_count"),
                    comment_count_expr.label("comment_count"),
                    like_count_expr.label("like_count"),
                    score_expr.label("score"),
                )
                .outerjoin(view_counts, Post.id == view_counts.c.post_id)
                .outerjoin(comment_counts, Post.id == comment_counts.c.post_id)
                .outerjoin(like_counts, Post.id == like_counts.c.post_id)
                .filter(score_expr > 0)
                .order_by(score_expr.desc(), Post.created_at.desc())
                .limit(limit)
                .all()
            )

        # 최근 7일 우선, 10개 미만이면 30일로 확장 (post_views는 도입 시점부터 쌓이므로 초기엔 30일 폴백이 자주 걸릴 수 있음)
        now = datetime.utcnow()
        results = _query(now - timedelta(days=7), 10)
        if len(results) < 10:
            results = _query(now - timedelta(days=30), 10)

        _top_ranking_cache = [
            {
                "id": r.id, "title": r.title, "category": r.category, "image_url": r.image_url,
                "created_at": r.created_at, "view_count": r.view_count,
                "comment_count": r.comment_count, "like_count": r.like_count, "score": r.score,
            }
            for r in results
        ]
    finally:
        db.close()

@app.get("/posts/ranking")
def get_top_ranking():
    return _top_ranking_cache[:3]

@app.get("/admin/ranking/top10", dependencies=[Depends(verify_admin)])
def get_admin_top_ranking():
    return _top_ranking_cache

@app.get("/posts/{post_id}")
def get_post(post_id: int, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post: raise HTTPException(status_code=404, detail="Post not found")
    return post

@app.get("/posts/{post_id}/adjacent")
def get_adjacent_posts(post_id: int, db: Session = Depends(get_db)):
    current = db.query(Post).filter(Post.id == post_id).first()
    if not current: raise HTTPException(status_code=404, detail="Post not found")
    
    prev_post = db.query(Post).filter(Post.id < post_id, Post.category == current.category).order_by(Post.id.desc()).first()
    next_post = db.query(Post).filter(Post.id > post_id, Post.category == current.category).order_by(Post.id.asc()).first()
    
    return {
        "prev": {"id": prev_post.id, "title": prev_post.title, "category": prev_post.category} if prev_post else None,
        "next": {"id": next_post.id, "title": next_post.title, "category": next_post.category} if next_post else None
    }

@app.post("/posts")
async def create_post(
    title: str = Form(...),
    body_text: str = Form(...),
    category: str = Form("Taste"),
    content_type: str = Form("YOUTUBE_LONG"),
    video_url: Optional[str] = Form(""),
    tags: Optional[str] = Form(""),
    image_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    _: None = Depends(verify_admin)
):
    try:
        image_web_url = ""
        if image_file and image_file.filename:
            file_ext = os.path.splitext(image_file.filename)[1]
            unique_name = f"{uuid.uuid4()}{file_ext}"
            image_web_url = await asyncio.to_thread(upload_to_gcs, image_file.file, unique_name)
        elif video_url:
            youtube_match = re.search(r"(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|e(?:mbed)?)\/|.*[?&]v=)|youtu\.be\/|youtube\.com\/shorts\/)([^\"&?\/\s]{11})", video_url, re.I)
            if youtube_match:
                video_id = youtube_match.group(1)
                image_web_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
            elif "spotify.com" in video_url:
                image_web_url = "https://nemoneai.com/thumbnails/podcast_default.jpg"

        db_post = Post(
            title=title, body_text=body_text, category=category,
            content_type=content_type, video_url=video_url,
            image_url=image_web_url, tags=tags, view_count=0
        )
        db.add(db_post)
        db.commit()
        db.refresh(db_post)
        threading.Thread(target=_revalidate_post, args=(db_post.id,), daemon=True).start()
        return db_post
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

def _revalidate_post(post_id: int):
    try:
        secret = os.getenv("ADMIN_SECRET_KEY", "")
        url = f"http://127.0.0.1:3000/api/revalidate?path=/posts/{post_id}&secret={secret}"
        urllib.request.urlopen(url, timeout=3)
    except Exception:
        pass

def _revalidate_special(special_id: Optional[int] = None):
    """스페셜 생성/수정/삭제 시 목록(/special)과 해당 상세 페이지 ISR 캐시 즉시 무효화.
    기존엔 이 갱신 훅이 없어 revalidate=3600(1시간) 시간 경과 전까지 새 스페셜이 목록에 안 보이던 문제."""
    try:
        secret = os.getenv("ADMIN_SECRET_KEY", "")
        urllib.request.urlopen(f"http://127.0.0.1:3000/api/revalidate?path=/special&secret={secret}", timeout=3)
        if special_id:
            urllib.request.urlopen(f"http://127.0.0.1:3000/api/revalidate?path=/special/{special_id}&secret={secret}", timeout=3)
    except Exception:
        pass

@app.put("/posts/{post_id}")
async def update_post(
    post_id: int,
    title: str = Form(...),
    body_text: str = Form(...),
    category: str = Form("Taste"),
    content_type: str = Form("YOUTUBE_LONG"),
    video_url: Optional[str] = Form(""),
    tags: Optional[str] = Form(""),
    image_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    _: None = Depends(verify_admin)
):
    db_post = db.query(Post).filter(Post.id == post_id).first()
    if not db_post: raise HTTPException(status_code=404, detail="수정할 게시물을 찾을 수 없습니다.")
    try:
        image_web_url = db_post.image_url
        if image_file and image_file.filename:
            file_ext = os.path.splitext(image_file.filename)[1]
            unique_name = f"{uuid.uuid4()}{file_ext}"
            image_web_url = await asyncio.to_thread(upload_to_gcs, image_file.file, unique_name)
        elif video_url and not db_post.image_url:
            # 기존 이미지가 없을 때만 자동 생성 (덮어쓰기 방지)
            youtube_match = re.search(r"(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|e(?:mbed)?)\/|.*[?&]v=)|youtu\.be\/|youtube\.com\/shorts\/)([^\"&?\/\s]{11})", video_url, re.I)
            if youtube_match:
                video_id = youtube_match.group(1)
                image_web_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
            elif "spotify.com" in video_url:
                image_web_url = "https://nemoneai.com/thumbnails/podcast_default.jpg"

        db_post.title = title
        db_post.body_text = body_text
        db_post.category = category
        db_post.content_type = content_type
        db_post.video_url = video_url
        db_post.tags = tags
        db_post.image_url = image_web_url
        db.commit()
        db.refresh(db_post)
        threading.Thread(target=_revalidate_post, args=(post_id,), daemon=True).start()
        return db_post
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/posts/{post_id}")
def delete_post(post_id: int, db: Session = Depends(get_db), _: None = Depends(verify_admin)):
    db_post = db.query(Post).filter(Post.id == post_id).first()
    if not db_post: raise HTTPException(status_code=404, detail="삭제할 게시물을 찾을 수 없습니다.")
    db.delete(db_post)
    db.commit()
    return {"message": "Successfully deleted", "id": post_id}

# --- 기타 API ---
@app.get("/posts/{post_id}/comments")
def get_comments(post_id: int, db: Session = Depends(get_db)):
    return db.query(Comment).filter(Comment.post_id == post_id).order_by(Comment.created_at.asc()).all()

@app.post("/posts/{post_id}/comments")
def create_comment(post_id: int, user_id: str = Form(...), user_name: str = Form(...), content: str = Form(...), user_image: Optional[str] = Form(None), db: Session = Depends(get_db)):
    db_comment = Comment(post_id=post_id, user_id=user_id, user_name=user_name, user_image=user_image, content=content)
    db.add(db_comment)
    db.commit()
    db.refresh(db_comment)
    return db_comment

@app.delete("/posts/{post_id}/comments/{comment_id}")
def delete_comment(post_id: int, comment_id: int, user_id: str, db: Session = Depends(get_db)):
    db_comment = db.query(Comment).filter(Comment.id == comment_id, Comment.post_id == post_id).first()
    # 관리자 우회권한을 위해서는 별도 처리가 필요하지만 일단 user_id로 검증
    if not db_comment or (db_comment.user_id != user_id and user_id != "admin_uuid_placeholder"):
        raise HTTPException(status_code=403, detail="Permission denied")
    db.delete(db_comment)
    db.commit()
    return {"message": "Comment deleted"}

@app.delete("/users/{user_id}")
def delete_user_data(user_id: str, db: Session = Depends(get_db)):
    """[심사 대응] 사용자 데이터(댓글, 좋아요) 영구 삭제 (계정 탈퇴 처리)"""
    try:
        db.query(Comment).filter(Comment.user_id == user_id).delete()
        db.query(Like).filter(Like.user_id == user_id).delete()
        db.commit()
        return {"message": "User data successfully deleted"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/posts/{post_id}/likes/toggle")
def toggle_like(post_id: int, user_id: str = Form(...), db: Session = Depends(get_db)):
    existing = db.query(Like).filter(Like.post_id == post_id, Like.user_id == user_id).first()
    if existing: db.delete(existing)
    else: db.add(Like(post_id=post_id, user_id=user_id))
    db.commit()
    return {"liked": not existing}

@app.get("/posts/{post_id}/likes/status")
def get_like_status(post_id: int, user_id: Optional[str] = None, db: Session = Depends(get_db)):
    total = db.query(Like).filter(Like.post_id == post_id).count()
    liked = db.query(Like).filter(Like.post_id == post_id, Like.user_id == user_id).first() is not None if user_id else False
    return {"total": total, "user_liked": liked}

@app.post("/analytics/log-view/{post_id}")
async def log_view(post_id: int, db: Session = Depends(get_db)):
    db.execute(update(Post).where(Post.id == post_id).values(view_count=func.coalesce(Post.view_count, 0) + 1))
    db.add(PostView(post_id=post_id))  # 주간 랭킹의 "최근 N일 조회수" 계산용 로그
    today = date.today()
    stat = db.query(DailyStat).filter(DailyStat.date == today).first()
    if not stat:
        db.add(DailyStat(date=today, visitors=0, total_views=1))
    else:
        db.execute(update(DailyStat).where(DailyStat.date == today).values(total_views=DailyStat.total_views + 1))
    db.commit()
    return {"status": "ok"}

@app.get("/analytics/summary")
def get_analytics_summary(days: int = 30, db: Session = Depends(get_db)):
    start_date = date.today() - timedelta(days=days)
    stats = db.query(DailyStat).filter(DailyStat.date >= start_date).order_by(DailyStat.date.asc()).all()
    top_posts = db.query(Post).order_by(Post.view_count.desc()).limit(10).all()
    return {"daily": stats, "top_posts": [{"id": p.id, "title": p.title, "views": p.view_count or 0} for p in top_posts]}

# --- SPECIAL 관련 API ---

@app.get("/specials")
def get_specials(db: Session = Depends(get_db)):
    return db.query(Special).order_by(Special.created_at.desc()).all()

@app.get("/specials/main")
def get_main_special(db: Session = Depends(get_db)):
    # is_main이 1인 것 중 가장 최신 것 하나를 가져옴
    special = db.query(Special).filter(Special.is_main == 1).order_by(Special.created_at.desc()).first()
    if not special:
        return None
    
    # 묶인 게시물 정보도 함께 리턴 (프론트엔드 편의성)
    try:
        ids = json.loads(special.post_ids)
        posts = db.query(Post).filter(Post.id.in_(ids)).all()
        # 순서 유지를 위해 재정렬
        post_map = {p.id: p for p in posts}
        ordered_posts = [post_map[pid] for pid in ids if pid in post_map]
        
        result = {
            "id": special.id,
            "title": special.title,
            "description": special.description,
            "bg_image_url": special.bg_image_url,
            "post_ids": special.post_ids,
            "is_main": special.is_main,
            "tags": special.tags,
            "created_at": special.created_at,
            "posts": ordered_posts
        }
        return result
    except Exception as e:
        print(f"Error parsing special posts: {e}")
        return special

@app.get("/specials/{special_id}")
def get_special_detail(special_id: int, db: Session = Depends(get_db)):
    special = db.query(Special).filter(Special.id == special_id).first()
    if not special:
        raise HTTPException(status_code=404, detail="Special not found")
    
    # 묶인 게시물 정보 함께 리턴
    try:
        ids = json.loads(special.post_ids)
        posts = db.query(Post).filter(Post.id.in_(ids)).all()
        post_map = {p.id: p for p in posts}
        ordered_posts = [post_map[pid] for pid in ids if pid in post_map]
        
        result = {
            "id": special.id,
            "title": special.title,
            "description": special.description,
            "bg_image_url": special.bg_image_url,
            "post_ids": special.post_ids,
            "is_main": special.is_main,
            "tags": special.tags,
            "created_at": special.created_at,
            "posts": ordered_posts
        }
        return result
    except Exception as e:
        print(f"Error parsing special posts: {e}")
        return special

@app.post("/specials")
async def create_special(
    title: str = Form(...),
    description: str = Form(...),
    post_ids: str = Form(...), # "[1, 2, 3]" JSON string
    is_main: int = Form(0),
    tags: Optional[str] = Form(""),
    image_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    _: None = Depends(verify_admin)
):
    try:
        image_web_url = ""
        if image_file and image_file.filename:
            file_ext = os.path.splitext(image_file.filename)[1]
            unique_name = f"special_{uuid.uuid4()}{file_ext}"
            image_web_url = await asyncio.to_thread(upload_to_gcs, image_file.file, unique_name)

        db_special = Special(
            title=title, description=description, post_ids=post_ids,
            is_main=is_main, tags=tags, bg_image_url=image_web_url
        )
        db.add(db_special)
        db.commit()
        db.refresh(db_special)
        threading.Thread(target=_revalidate_special, args=(db_special.id,), daemon=True).start()
        return db_special
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/specials/{special_id}")
async def update_special(
    special_id: int,
    title: str = Form(...),
    description: str = Form(...),
    post_ids: str = Form(...),
    is_main: int = Form(0),
    tags: Optional[str] = Form(""),
    image_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    _: None = Depends(verify_admin)
):
    db_special = db.query(Special).filter(Special.id == special_id).first()
    if not db_special:
        raise HTTPException(status_code=404, detail="Special not found")
    
    try:
        if image_file and image_file.filename:
            file_ext = os.path.splitext(image_file.filename)[1]
            unique_name = f"special_{uuid.uuid4()}{file_ext}"
            db_special.bg_image_url = await asyncio.to_thread(upload_to_gcs, image_file.file, unique_name)

        db_special.title = title
        db_special.description = description
        db_special.post_ids = post_ids
        db_special.is_main = is_main
        db_special.tags = tags
        db.commit()
        db.refresh(db_special)
        threading.Thread(target=_revalidate_special, args=(special_id,), daemon=True).start()
        return db_special
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/specials/{special_id}")
def delete_special(special_id: int, db: Session = Depends(get_db), _: None = Depends(verify_admin)):
    db_special = db.query(Special).filter(Special.id == special_id).first()
    if not db_special:
        raise HTTPException(status_code=404, detail="Special not found")
    db.delete(db_special)
    db.commit()
    threading.Thread(target=_revalidate_special, daemon=True).start()
    return {"message": "Successfully deleted", "id": special_id}

# --- NEMONE NEWS 관련 API ---

@app.get("/news")
def get_news(skip: int = 0, limit: int = 5, db: Session = Depends(get_db)):
    total = db.query(NemoneNews).count()
    news_list = db.query(NemoneNews).order_by(NemoneNews.created_at.desc()).offset(skip).limit(limit).all()
    return {"total": total, "news": news_list}

@app.post("/news")
def create_news(
    request: Request,
    title: str = Form(...),
    content: str = Form(...),
    db: Session = Depends(get_db)
):
    # 헤더에 숨겨진 비밀번호 확인
    secret = request.headers.get("x-news-secret")
    if secret != NEWS_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    db_news = NemoneNews(title=title, content=content)
    db.add(db_news)
    db.commit()
    db.refresh(db_news)
    return db_news

@app.put("/news/{news_id}")
def update_news(
    news_id: int,
    request: Request,
    title: str = Form(...),
    content: str = Form(...),
    db: Session = Depends(get_db)
):
    secret = request.headers.get("x-news-secret")
    if secret != NEWS_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    db_news = db.query(NemoneNews).filter(NemoneNews.id == news_id).first()
    if not db_news:
        raise HTTPException(status_code=404, detail="News not found")

    db_news.title = title
    db_news.content = content
    db.commit()
    db.refresh(db_news)
    return db_news

@app.delete("/news/{news_id}")
def delete_news(news_id: int, request: Request, db: Session = Depends(get_db)):
    secret = request.headers.get("x-news-secret")
    if secret != NEWS_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    db_news = db.query(NemoneNews).filter(NemoneNews.id == news_id).first()
    if not db_news:
        raise HTTPException(status_code=404, detail="News not found")
    
    db.delete(db_news)
    db.commit()
    return {"message": "Successfully deleted", "id": news_id}

refresh_top_ranking()

# 서버는 UTC 기준 — 한국시간(KST=UTC+9) 자정(00:00)/낮 12시(12:00)에 맞춰 UTC 15:00, 03:00에 실행
ranking_scheduler = BackgroundScheduler()
ranking_scheduler.add_job(refresh_top_ranking, 'cron', hour=15, minute=5, id='ranking_kst_midnight')
ranking_scheduler.add_job(refresh_top_ranking, 'cron', hour=3, minute=5, id='ranking_kst_noon')

# GA4 리포트(국가별 제외, now_back과 동일 패턴) — KST 6/9/12/15/18/21/23:59시 일간 +
# 매주 월요일 07:00 주간 + 매월 1일 10:00 월간. UTC = KST-9h.
# 로컬 개발서버에서 실수로 같이 발송되는 걸 막기 위해 TELEGRAM_BOT_TOKEN/CHAT_ID를
# 프로덕션 .env에만 넣어둠(로컬은 미설정 → notification.send_alert가 조용히 스킵).
import ga4_service
ranking_scheduler.add_job(ga4_service.send_ga4_report, 'cron', hour=21, minute=20, id='ga4_kst_0600')
ranking_scheduler.add_job(ga4_service.send_ga4_report, 'cron', hour=0, minute=20, id='ga4_kst_0900')
ranking_scheduler.add_job(ga4_service.send_ga4_report, 'cron', hour=3, minute=20, id='ga4_kst_1200')
ranking_scheduler.add_job(ga4_service.send_ga4_report, 'cron', hour=6, minute=20, id='ga4_kst_1500')
ranking_scheduler.add_job(ga4_service.send_ga4_report, 'cron', hour=9, minute=20, id='ga4_kst_1800')
ranking_scheduler.add_job(ga4_service.send_ga4_report, 'cron', hour=12, minute=20, id='ga4_kst_2100')
ranking_scheduler.add_job(ga4_service.send_ga4_report, 'cron', hour=14, minute=59, id='ga4_kst_2359')
ranking_scheduler.add_job(ga4_service.send_weekly_ga4_report, 'cron', day_of_week='sun', hour=22, minute=10, id='ga4_weekly_kst_mon_0700')
ranking_scheduler.add_job(ga4_service.send_monthly_ga4_report, 'cron', day=1, hour=1, minute=10, id='ga4_monthly_kst_1st_1000')

ranking_scheduler.start()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
