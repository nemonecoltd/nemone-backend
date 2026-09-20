"""공용 의존성 — 로그인 사용자 검증.

이 모듈은 프로젝트 내 다른 모듈을 import하지 않는다(순환 참조 방지 기준점).

배경(2026-09-20): 맛매치 백엔드에는 로그인 검증이 아예 없었다. 댓글·좋아요는 클라이언트가
보낸 user_id를 Form 필드로 그대로 받아 신뢰하는 구조라, 누구나 남의 user_id를 보내면
그 사람인 척할 수 있었다. 전자책 권한(entitlements)처럼 돈이 걸린 리소스에 같은 구조를
쓰면 "아무나 남의 user_id로 전권 열람"이 되므로, PACE(now_back/deps.py)에서 이미 검증된
방식을 그대로 이식한다.

검증 방식: 클라이언트가 보낸 Supabase access token을 Supabase Auth에 직접 물어봐(introspect)
실제 사용자 정보를 받아온다. JWT 서명을 로컬에서 직접 디코드하지 않는 이유는 토큰 폐기·
로그아웃이 즉시 반영되고, 시크릿 회전 시에도 코드가 깨지지 않기 때문이다.
"""
import os
from typing import Optional

import requests
from fastapi import Header, HTTPException

ADMIN_EMAIL = "nemonecoltd@gmail.com"

# 프론트와 같은 프로젝트를 가리키는 변수명이 NEXT_PUBLIC_ 접두사로 저장돼 있어 그대로 읽는다.
# (PACE는 SUPABASE_URL을 쓰지만 이 레포 .env 키는 NEXT_PUBLIC_SUPABASE_URL이다 — 둘 다 허용)
def _supabase_url() -> str:
    return os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL", "")


def verify_supabase_user(authorization: Optional[str] = Header(None)) -> dict:
    """Authorization: Bearer <access_token> → 실제 로그인 사용자 {id, email}.

    클라이언트가 보내는 user_id를 신뢰하지 않는다. 권한이 걸린 모든 신규 엔드포인트는
    이 의존성을 통해 user_id를 얻어야 한다.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    token = authorization.split(" ", 1)[1]

    supabase_url = _supabase_url()
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip().strip('"')
    if not supabase_url or not service_key:
        # 설정 누락을 401(로그인 실패)로 감추면 원인 파악이 어려워 500으로 분리
        raise HTTPException(status_code=500, detail="인증 설정이 누락되었습니다")

    try:
        res = requests.get(
            f"{supabase_url}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": service_key},
            timeout=5,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="로그인 확인에 실패했습니다")

    if res.status_code != 200:
        raise HTTPException(status_code=401, detail="유효하지 않은 로그인입니다")

    data = res.json()
    return {"id": data["id"], "email": data.get("email", "") or ""}


def verify_supabase_user_optional(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    """verify_supabase_user의 비필수 버전 — 토큰이 없거나 유효하지 않으면 401 대신 None.
    '로그인했으면 구매 여부까지, 아니면 공개 정보만' 같은 화면에서 쓴다."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        return verify_supabase_user(authorization)
    except HTTPException:
        return None
