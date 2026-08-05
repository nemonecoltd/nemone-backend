# nemone-backend — 프로젝트 문서

## 개요

맛매치(MatMatch) 서비스와 네모네 홈페이지 게시판을 동시에 지원하는 **공용 백엔드 API 서버**.  
FastAPI + SQLAlchemy + PostgreSQL 구조로, Nginx 리버스 프록시를 통해 외부에 노출된다.

- **레포**: `https://github.com/nemonecoltd/nemone-backend`
- **포트**: 8080 (PM2 프로세스명: `backend`)
- **VM 경로**: `/home/nemonecoltd/nemone-network/backend`
- **프레임워크**: FastAPI (Python) + SQLAlchemy + uvicorn

---

## 서비스 구조

```
nemone-backend (공용 API, :8080)
├── 맛매치 콘텐츠 API   /posts, /specials, /comments, /likes
├── 네모네 홈 게시판    /news
└── 분석 API           /analytics

matmatch/
├── admin/             관리자 페이지 (Next.js, PM2 admin)
├── frontend/          사용자 페이지 (Next.js, PM2 frontend)
└── backend/           → nemone-backend 레포 (공용)

nemone-home/
└── 홈페이지 게시판    → 동일 백엔드 /news API 사용
```

---

## 파일 구조

```
backend/
├── main.py                FastAPI 전체 (모델 + 라우터 단일 파일)
├── requirements.txt       Python 의존성
├── ecosystem.config.js    PM2 실행 설정
├── .env                   환경변수 (절대 커밋 금지)
├── .env.example           환경변수 템플릿 (커밋용)
├── .gitignore
├── GEMINI.md              AI 어시스턴트용 프로젝트 지침
├── logs/                  PM2 로그 (gitignore)
├── static/thumbnails/     업로드 이미지 파일 (gitignore)
└── venv/                  Python 가상환경 (gitignore)
```

---

## DB 모델

| 테이블 | 설명 |
|--------|------|
| `posts` | 맛매치 콘텐츠 (제목, 본문, 카테고리, 영상URL, 조회수) |
| `comments` | 댓글 (Supabase user_id 기반) |
| `likes` | 좋아요 (post_id + user_id 복합) |
| `specials` | 스페셜 기획전 (post_ids를 JSON 문자열로 저장) |
| `nemone_news` | 홈페이지 게시판 |
| `daily_stats` | 일별 방문자/조회수 통계 |

- **DB**: GCP Cloud SQL PostgreSQL (VM에서 IP 직접 접속)
- **ORM**: SQLAlchemy 2.0 (declarative_base 방식)

---

## API 목록

### 콘텐츠 (Posts)
| Method | 경로 | 설명 |
|--------|------|------|
| GET | `/posts` | 전체 목록 (category 필터 가능) |
| GET | `/posts/ranking` | 점수 기반 상위 3개 |
| GET | `/posts/{id}` | 단건 조회 |
| GET | `/posts/{id}/adjacent` | 이전/다음 글 |
| POST | `/posts` | 글 작성 (이미지 업로드 포함) |
| PUT | `/posts/{id}` | 수정 |
| DELETE | `/posts/{id}` | 삭제 |

### 댓글 / 좋아요
| Method | 경로 | 설명 |
|--------|------|------|
| GET | `/posts/{id}/comments` | 댓글 목록 |
| POST | `/posts/{id}/comments` | 댓글 작성 |
| DELETE | `/posts/{id}/comments/{cid}` | 댓글 삭제 |
| POST | `/posts/{id}/likes/toggle` | 좋아요 토글 |
| GET | `/posts/{id}/likes/status` | 좋아요 수/여부 |

### 스페셜
| Method | 경로 | 설명 |
|--------|------|------|
| GET | `/specials` | 전체 목록 |
| GET | `/specials/main` | 메인 핀 기획전 |
| GET | `/specials/{id}` | 단건 (포스트 정보 포함) |
| POST | `/specials` | 생성 |
| PUT | `/specials/{id}` | 수정 |
| DELETE | `/specials/{id}` | 삭제 |

### 뉴스 (홈페이지 게시판)
| Method | 경로 | 인증 |
|--------|------|------|
| GET | `/news` | 없음 |
| POST | `/news` | `x-news-secret` 헤더 필수 |
| DELETE | `/news/{id}` | `x-news-secret` 헤더 필수 |

### 분석
| Method | 경로 | 설명 |
|--------|------|------|
| POST | `/analytics/log-visitor` | 방문자 카운트 |
| POST | `/analytics/log-view/{id}` | 조회수 카운트 |
| GET | `/analytics/summary` | 통계 요약 |

---

## 환경변수 (`.env`)

```
DB_HOST=              # GCP Cloud SQL IP
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=          # Cloud SQL postgres 비밀번호
GEMINI_API_KEY=       # Google AI API 키
SUPABASE_JWT_SECRET=       # Supabase JWT 검증용
SUPABASE_SERVICE_ROLE_KEY= # Supabase 서비스 롤 키
NEWS_SECRET_KEY=      # 홈페이지 게시판 관리 비밀번호
GCS_BUCKET_NAME=nemoneai-thumbnails  # GCS 썸네일 버킷
```

---

## 배포 방식 (수동 — CI/CD 미연결)

```bash
# 1. 로컬에서 파일 전송
scp main.py nemonecoltd@<VM_IP>:/home/nemonecoltd/nemone-network/backend/main.py

# 2. VM에서 재시작
pm2 restart backend
pm2 logs backend --lines 20
```

> 서버 자원 부족으로 VM 내 빌드 불가. 프론트엔드는 로컬 빌드 후 결과물 전송.

---

## 알려진 기술 부채

| 항목 | 내용 | 우선순위 |
|------|------|---------|
| 관리자 API 인증 없음 | POST/PUT/DELETE /posts, /specials에 인증 없어 외부 호출 가능 | 높음 |
| `post_ids` JSON 문자열 | Special의 포스트 연결을 Text에 JSON으로 저장, 무결성 없음 | 낮음 |

---

## 2026-05-29 작업 내역

### 문제 발견
- `main.py`에 DB 비밀번호(`Looa2002!!`)가 평문 하드코딩된 채로 GitHub 커밋됨
- `NEWS_SECRET_KEY`도 소스 코드에 하드코딩

### 수정 내용

#### 1. 환경변수 분리 (`main.py`)
- `python-dotenv` `load_dotenv()` 추가
- `DB_CONFIG` 딕셔너리 제거 → `os.getenv()` 직접 사용
- `NEWS_SECRET_KEY` → `os.getenv("NEWS_SECRET_KEY")` 로 변경

#### 2. `.env.example` 생성
- 실제 값 없이 키 이름만 담은 템플릿 파일 GitHub에 커밋

#### 3. `.env` 보강
- Supabase 키, NEWS_SECRET_KEY 항목 추가
- DB 비밀번호 교체 (`Looa2002!!` → 새 비밀번호)

#### 4. GitHub 레포 정리
- 기존 backend/.git이 matmatch 전체를 추적하고 있던 문제 발견
- `rm -rf .git` 후 backend 파일만으로 새로 초기화
- `https://github.com/nemonecoltd/nemone-backend.git` 으로 분리 push

#### 5. VM 수동 배포
- `scp`로 `main.py` 전송 후 `pm2 restart backend` 완료
- `Application startup complete` 확인

---

## 이미지 스토리지 구조 (GCS)

- **버킷**: `nemoneai-thumbnails` (GCP Cloud Storage, 서울 리전)
- **공개 URL 형식**: `https://storage.googleapis.com/nemoneai-thumbnails/{파일명}`
- **인증**: VM 기본 서비스 계정 ADC 사용 (JSON 키 불필요)
- **VM 스코프**: "모든 Cloud API 전체 액세스" 설정 필요
- **Nginx**: `/thumbnails/` 블록 제거 완료 (GCS 직접 서빙)

---

## 2026-05-30 작업 내역

### 성능 및 경량화

#### 1. 죽은 의존성 제거
- `google-generativeai` (98MB+), `jinja2` 제거
- `requirements.txt` 및 venv에서 삭제

#### 2. `GET /posts` 응답 최적화
- `limit=100` 페이지네이션 추가
- `body_text` → HTML 태그 제거 후 200자 요약본만 반환 (2.5MB → 수십KB)
- 응답 형식: `{"total": N, "posts": [...]}`

#### 3. 랭킹 API N+1 쿼리 → SQL 단일 쿼리
- 기존: 포스트 수 × 2번 DB 쿼리 (N+1)
- 수정: LEFT JOIN 서브쿼리로 DB 쿼리 1번

#### 4. 조회수/방문자 카운트 원자 연산
- 기존: Python에서 읽고 쓰는 방식 (race condition)
- 수정: `UPDATE ... SET view_count = view_count + 1` 원자 연산

#### 5. SQLAlchemy 커넥션 풀 튜닝
- `pool_size=3, max_overflow=5, pool_timeout=30, pool_recycle=1800`

#### 6. 썸네일 GCS 이전
- 기존: VM 로컬 디스크 `static/thumbnails/` (120MB, 110개 파일)
- 수정: GCS `nemoneai-thumbnails` 버킷으로 이전
- DB `image_url` 전체 업데이트 (posts 100건, specials 3건)
- VM 디스크 120MB 확보

### 프론트엔드 SEO 수정

#### 7. API 응답 포맷 대응
- `sitemap.ts`, `generateStaticParams`, 카테고리 페이지 — 신규 `{total, posts}` 포맷 대응

#### 8. 메타태그 / OG 태그 개선
- 홈페이지: OG 태그, Twitter Card 추가
- 카테고리 페이지: 영어 → 한국어 메타데이터, OG 태그 추가
- 포스트 description: HTML 태그 제거 후 사용

### 남은 작업
- [ ] 관리자 API 인증 (POST/PUT/DELETE /posts, /specials)

---

## 2026-07-24 작업 내역 — 구글 서치콘솔 크롤링 오류 리뷰 후속 조치

외부에서 받은 GSC 크롤링 오류 분석을 그대로 반영하지 않고 항목별로 실제 원인을 재확인, 실제로 문제였던 2건만 수정.

### 1. 썸네일 원본 그대로 GCS 업로드 → 리사이즈+WebP 압축
- `upload_to_gcs()`가 원본 이미지를 압축 없이 그대로 업로드하고 있어 일부 썸네일이 800KB대로 커짐 — Googlebot 이미지 로드 타임아웃 리스크로 판단
- PIL로 최대 가로 1600px 리사이즈 + WebP(quality=85) 변환 후 업로드하도록 수정, 확장자도 `.webp`로 강제
- PIL이 파일을 못 열면(포맷 미지원 등) 원본 바이트를 그대로 업로드하는 폴백 유지

### 2. GCS 버킷 CORS 미설정
- `nemoneai-thumbnails` 버킷에 CORS 자체가 없어 `access-control-allow-origin` 헤더가 응답에 전혀 없던 것을 `curl -sI`로 직접 확인
- 서버 서비스 계정에 `storage.buckets.get/update` 권한이 없어 코드/API로 직접 수정 불가 → 아래 설정을 사용자가 터미널(`gsutil cors set`)로 직접 적용
  ```json
  [{"origin": ["*"], "method": ["GET", "HEAD"], "responseHeader": ["Content-Type"], "maxAgeSeconds": 3600}]
  ```
- 적용 후 `curl` 재검증(캐시 우회) — `access-control-allow-origin: *` 정상 확인, OPTIONS 프리플라이트도 `GET,HEAD` 허용으로 정상 응답

---

## 2026-07-27 작업 내역 — 히어로 타이틀 반응형 폰트 개선 + 배포 인프라 상태 재확인

### 1. 히어로 타이틀 폰트 fluid typography로 교체
- `frontend/src/app/(main)/HomeContent.tsx`의 히어로 `h2`가 `text-4xl md:text-7xl lg:text-8xl`(768px/1024px 계단식)이라, 768px 미만 전 구간(320px 작은 폰부터 767px까지)이 전부 고정 36px — 좁은 화면일수록 상대적으로 과도하게 커 보이던 원인
- `text-[clamp(1.75rem,7vw+0.3rem,6rem)]`로 교체해 화면 폭에 비례해 연속적으로 스케일되도록 수정

### 2. 배포 과정에서 발견한 인프라 정보 미갱신 2건
- 로컬 matmatch 백엔드가 일요일부터 계속 떠있던 프로세스라, `.env`가 msm-db(34.50.63.89)로 이미 전환된 뒤에도 프로세스 메모리엔 마이그레이션 전 구 Cloud SQL IP(34.64.236.78, 이미 정지됨)가 남아있어 `/posts?limit=10000` 등 SSG 데이터 페치가 전부 타임아웃 — 프로세스 재시작으로 해결(env는 프로세스 시작 시 1회만 로드되므로 `.env` 변경 후 재기동 필요)
- 프론트 배포 시도 시 로컬 메모리 절차가 구 서버(`nemonecoltd@34.64.98.113`, 이미 정지)를 가리키고 있었음 — msm VM(`ubuntu@34.64.111.65`)에서 pm2 프로세스 실제 cwd(`~/apps/matmatch_frontend`) 확인 후 정상 배포, 관련 로컬 배포 메모리 갱신

---

## 2026-08-05 작업 내역 — 소식 수정(PUT) API 신설 + "지금여기" 잔존 콘텐츠 정리

now의 PACE 리브랜딩 과정에서 home.nemoneai.com 어드민에 소식 수정 기능이 없다는 걸 발견해 백엔드에 추가, 이어서 matmatch 풋터에 구 브랜드명("지금여기")이 남아있다는 피드백을 받아 원인 추적.

### 1. `PUT /news/{news_id}` 엔드포인트 신설
- 기존엔 `POST /news`(등록)·`DELETE /news/{id}`만 있어 어드민에서 수정이 불가능했음
- 기존 `DELETE` 엔드포인트 바로 앞에 `PUT` 추가, 인증 방식은 기존 라우트와 동일하게 `x-news-secret` 헤더를 `NEWS_SECRET_KEY`와 비교하는 방식 재사용

### 2. "지금여기"/"NOW HERE" 잔존 텍스트 — 원인은 UI가 아니라 DB 콘텐츠
- 풋터 코드 자체엔 구 브랜드명이 없었음 — 실제로는 이미 발행된 기사 8건(id: 48, 92, 135, 155, 162, 168, 183, 184)의 제목/본문/태그에 "지금여기"·"NOW HERE"·"[지금여기 / NOW HERE]" 문자열이 그대로 박혀있던 것이 원인
- SQLAlchemy로 직접 `UPDATE posts SET title=..., body_text=... WHERE id IN (...)` 실행해 전부 "NEMONE PACE"로 치환(합성 표기 `[지금여기 / NOW HERE]`는 중복 치환 방지를 위해 먼저 처리), `tags` 컬럼도 id 48·155 두 건 별도 치환
- DB를 고쳤는데도 홈페이지에 반영이 안 돼 추적한 결과, 서로 독립적인 캐시 레이어 3곳이 동시에 걸려있었음: ① 백엔드 인메모리 `_top_ranking_cache`(스케줄러가 하루 2번만 갱신, `pm2 restart backend`로 즉시 갱신) ② Next.js가 빌드 간에도 유지하는 `.next/cache` fetch 캐시(`rm -rf .next/cache` 후 재빌드 필요) ③ 실제 배포 자체를 새로 하지 않으면 서버에 반영 안 됨 — 세 곳을 모두 처리한 뒤 8개 게시글 URL 전부 curl로 재검증
