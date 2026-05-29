from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from sqlalchemy.sql import func
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import uvicorn
import os
import uuid
import shutil
import re
import json
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

engine = create_engine(SQLALCHEMY_DATABASE_URL)
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

Base.metadata.create_all(bind=engine)

app = FastAPI()

# 업로드 용량 제한 확장 (50MB)
@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    content_length = request.headers.get('content-length')
    if content_length and int(content_length) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Request entity too large")
    return await call_next(request)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PHYSICAL_DIR = os.path.join(BASE_DIR, "static", "thumbnails")
os.makedirs(PHYSICAL_DIR, exist_ok=True)

app.mount("/thumbnails", StaticFiles(directory=PHYSICAL_DIR), name="thumbnails")

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

# --- API 엔드포인트 (오직 Form 데이터만 받는 안정적인 구조) ---

@app.get("/posts")
def get_posts(category: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Post)
    if category:
        query = query.filter(Post.category == category)
    return query.order_by(Post.id.desc()).all()

@app.get("/posts/ranking")
def get_top_ranking(db: Session = Depends(get_db)):
    posts = db.query(Post).all()
    ranking_data = []
    for p in posts:
        comment_count = db.query(Comment).filter(Comment.post_id == p.id).count()
        like_count = db.query(Like).filter(Like.post_id == p.id).count()
        score = (p.view_count or 0) + (comment_count * 2) + (like_count * 3)
        ranking_data.append({"id": p.id, "title": p.title, "category": p.category, "score": score, "created_at": p.created_at})
    ranking_data.sort(key=lambda x: (x["score"], x["created_at"]), reverse=True)
    return ranking_data[:3]

@app.get("/posts/{post_id}")
def get_post(post_id: int, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post: raise HTTPException(status_code=404, detail="Post not found")
    return post

@app.get("/posts/{post_id}/adjacent")
def get_adjacent_posts(post_id: int, db: Session = Depends(get_db)):
    current = db.query(Post).filter(Post.id == post_id).first()
    if not current: raise HTTPException(status_code=404, detail="Post not found")
    
    prev_post = db.query(Post).filter(Post.id < post_id).order_by(Post.id.desc()).first()
    next_post = db.query(Post).filter(Post.id > post_id).order_by(Post.id.asc()).first()
    
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
    db: Session = Depends(get_db)
):
    try:
        image_web_url = ""
        if image_file and image_file.filename:
            file_ext = os.path.splitext(image_file.filename)[1]
            unique_name = f"{uuid.uuid4()}{file_ext}"
            save_path = os.path.join(PHYSICAL_DIR, unique_name)
            with open(save_path, "wb") as buffer:
                shutil.copyfileobj(image_file.file, buffer)
            image_web_url = f"https://nemoneai.com/thumbnails/{unique_name}"
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
        return db_post
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

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
    db: Session = Depends(get_db)
):
    db_post = db.query(Post).filter(Post.id == post_id).first()
    if not db_post: raise HTTPException(status_code=404, detail="수정할 게시물을 찾을 수 없습니다.")
    try:
        image_web_url = db_post.image_url
        if image_file and image_file.filename:
            file_ext = os.path.splitext(image_file.filename)[1]
            unique_name = f"{uuid.uuid4()}{file_ext}"
            save_path = os.path.join(PHYSICAL_DIR, unique_name)
            with open(save_path, "wb") as buffer:
                shutil.copyfileobj(image_file.file, buffer)
            image_web_url = f"https://nemoneai.com/thumbnails/{unique_name}"
        elif video_url:
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
        return db_post
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/posts/{post_id}")
def delete_post(post_id: int, db: Session = Depends(get_db)):
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

@app.post("/analytics/log-visitor")
async def log_visitor(db: Session = Depends(get_db)):
    today = date.today()
    stat = db.query(DailyStat).filter(DailyStat.date == today).first()
    if not stat:
        stat = DailyStat(date=today, visitors=1, total_views=0)
        db.add(stat)
    else: stat.visitors += 1
    db.commit()
    return {"status": "ok"}

@app.post("/analytics/log-view/{post_id}")
async def log_view(post_id: int, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if post: post.view_count = (post.view_count or 0) + 1
    today = date.today()
    stat = db.query(DailyStat).filter(DailyStat.date == today).first()
    if not stat:
        stat = DailyStat(date=today, visitors=0, total_views=1)
        db.add(stat)
    else: stat.total_views += 1
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
    db: Session = Depends(get_db)
):
    try:
        image_web_url = ""
        if image_file and image_file.filename:
            file_ext = os.path.splitext(image_file.filename)[1]
            unique_name = f"special_{uuid.uuid4()}{file_ext}"
            save_path = os.path.join(PHYSICAL_DIR, unique_name)
            with open(save_path, "wb") as buffer:
                shutil.copyfileobj(image_file.file, buffer)
            image_web_url = f"https://nemoneai.com/thumbnails/{unique_name}"

        db_special = Special(
            title=title, description=description, post_ids=post_ids,
            is_main=is_main, tags=tags, bg_image_url=image_web_url
        )
        db.add(db_special)
        db.commit()
        db.refresh(db_special)
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
    db: Session = Depends(get_db)
):
    db_special = db.query(Special).filter(Special.id == special_id).first()
    if not db_special:
        raise HTTPException(status_code=404, detail="Special not found")
    
    try:
        if image_file and image_file.filename:
            file_ext = os.path.splitext(image_file.filename)[1]
            unique_name = f"special_{uuid.uuid4()}{file_ext}"
            save_path = os.path.join(PHYSICAL_DIR, unique_name)
            with open(save_path, "wb") as buffer:
                shutil.copyfileobj(image_file.file, buffer)
            db_special.bg_image_url = f"https://nemoneai.com/thumbnails/{unique_name}"

        db_special.title = title
        db_special.description = description
        db_special.post_ids = post_ids
        db_special.is_main = is_main
        db_special.tags = tags
        db.commit()
        db.refresh(db_special)
        return db_special
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/specials/{special_id}")
def delete_special(special_id: int, db: Session = Depends(get_db)):
    db_special = db.query(Special).filter(Special.id == special_id).first()
    if not db_special:
        raise HTTPException(status_code=404, detail="Special not found")
    db.delete(db_special)
    db.commit()
    return {"message": "Successfully deleted", "id": special_id}

# --- NEMONE NEWS 관련 API ---

NEWS_SECRET_KEY = os.getenv("NEWS_SECRET_KEY", "nemone1234!")

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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
