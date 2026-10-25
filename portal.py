"""네모네 포털(nemoneai.com `/`) 데이터 — 서비스별 피드 수집(스냅샷)과 통합 조회 API.

설계(2026-10-24)
- 포털은 다른 서비스의 DB를 직접 읽지 않는다. 각 서비스가 이미 공개하고 있는 조회 API만 서버에서 호출한다
  (브라우저가 서비스 여러 곳을 직접 부르지 않음).
- 서비스별 최신 **성공** 스냅샷 1행만 유지한다. 수집이 실패하면 기존 스냅샷을 덮어쓰지 않고 ok=false 만 표시한다.
- 이미지는 자체 스토리지(GCS 등)만 쓴다. 네이버 CDN 등 외부 원본이면 그 항목은 제외한다.
- AIM(맛매치) 글은 요청 시 DB에서 조회한다. PACE·PLANTS 소속 Special(5·6)의 글은 그 서비스 소유라 AIM 몫에서 뺀다.
- LII는 매일 갱신되는 '오늘의 분석' 2건을 보여 준다(5년 재무 기반 분석 요약, 매수·매도 표현 없음).
"""
import ast
import json
import logging
import re
import threading
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import text

logger = logging.getLogger("portal")
router = APIRouter(prefix="/portal")

KST = timezone(timedelta(hours=9))
PACE_BASE = "https://now.nemoneai.com/api-now"
PLANTS_BASE = "https://plants.nemoneai.com"
LII_BASE = "https://msm.nemoneai.com"
AIM_BASE = "https://nemoneai.com"
# PACE·PLANTS가 주인인 맛매치 Special — AIM 몫에서 제외(frontend/src/lib/serviceHome.ts 와 같은 번호)
SERVICE_SPECIAL_IDS = (5, 6)
ALLOWED_IMAGE_HOSTS = ("storage.googleapis.com", "plants.nemoneai.com", "nemoneai.com", "now.nemoneai.com")
YOUTUBE_CHANNEL_ID = "UCXQyHkjZNGNWNmm3Tio9Amg"            # https://www.youtube.com/@nemoneaim
YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@nemoneaim"
PODCAST_RSS = "https://anchor.fm/s/10cc660d0/podcast/rss"    # 맛의문명사 | 네모네AIM (애플·스포티파이가 받아 가는 원본 피드)
PODCAST_URL = "https://podcasts.apple.com/kr/podcast/%EB%A7%9B%EC%9D%98%EB%AC%B8%EB%AA%85%EC%82%AC-%EB%84%A4%EB%AA%A8%EB%84%A4aim/id1861121835"
BLOG_RSS = "https://rss.blog.naver.com/nemoneaim.xml"
BLOG_URL = "https://blog.naver.com/nemoneaim"
# 오늘의 채널 썸네일은 외부 플랫폼 이미지라 별도 허용 목록(서비스 피드의 '자체 스토리지만' 규칙과 분리)
CHANNEL_IMAGE_HOSTS = ("i.ytimg.com", "i1.ytimg.com", "i2.ytimg.com", "i3.ytimg.com", "img.youtube.com",
                       "d3t3ozftmdmh3i.cloudfront.net", "blogthumb.pstatic.net", "postfiles.pstatic.net", "blogpfthumb-phinf.pstatic.net")
CATEGORY_AIM = {"Taste": "TASTE", "Culture": "MIND", "Life": "LIFE", "Tech": "FUTURE"}

_engine = None
_aim_ranking_provider = None  # main.py가 주간 랭킹 캐시(refresh_top_ranking 결과)를 돌려주는 함수를 주입한다


def set_aim_ranking_provider(fn) -> None:
    global _aim_ranking_provider
    _aim_ranking_provider = fn


def _get_json(url: str, timeout: int = 6) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "nemone-portal-collector/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def _get_text(url: str, timeout: int = 8) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; nemone-portal-collector/1.0)", "Accept": "application/xml,text/xml,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read().decode("utf-8", errors="replace")


def _rfc_to_kst_iso(value: Optional[str]) -> Optional[str]:
    """RSS pubDate(RFC 2822)·Atom published(ISO) → KST ISO 문자열."""
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
    except Exception:  # noqa: BLE001
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).isoformat()


def _channel_image(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    u = url.replace("http://", "https://", 1)
    return u if urllib.parse.urlparse(u).netloc.lower() in CHANNEL_IMAGE_HOSTS else None


def _clean(raw: Optional[str], limit: int = 80) -> str:
    s = re.sub(r"<[^>]*>", " ", raw or "")
    s = unescape(s).replace(" ", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _image_ok(url: Optional[str], base: str = "") -> Optional[str]:
    """자체 스토리지 이미지만 통과. 상대경로는 서비스 주소로 절대화."""
    if not url:
        return None
    if url.startswith("/"):
        url = base.rstrip("/") + url
    host = urllib.parse.urlparse(url).netloc.lower()
    return url if host in ALLOWED_IMAGE_HOSTS else None


def _utm(url: str, service: str, slot: str) -> str:
    if not url.startswith("http"):
        return url
    q = urllib.parse.urlencode({"utm_source": "nemone_portal", "utm_medium": "today", "utm_content": f"{service}_{slot}"})
    return url + ("&" if "?" in url else "?") + q


def _parse_end_date(date_range: str) -> Optional[date]:
    """'2026.10.22. ~ 2026.10.28.' → 마지막 날짜"""
    found = re.findall(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", date_range or "")
    if not found:
        return None
    y, m, d = (int(x) for x in found[-1])
    try:
        return date(y, m, d)
    except ValueError:
        return None


# ───────────────────────── 서비스별 수집기(피드 규격으로 정규화) ─────────────────────────
def _pace_item(p: dict, today: date) -> dict:
    end = _parse_end_date(p.get("date_range") or "")
    badge = None
    if end is not None and 0 <= (end - today).days <= 7:
        badge = "마감임박"
    elif p.get("is_new"):
        badge = "NEW"
    region = (p.get("region") or "").strip()
    return {
        "id": str(p["id"]),
        "title": p["title"],
        "summary": _clean(" · ".join(x for x in (region, p.get("date_range")) if x), 80),
        "image": _image_ok(p.get("image_url")),  # 외부 원본(KOPIS 등)이면 None — 랭킹 순위는 유지하고 화면에서 대체 이미지를 쓴다
        "url": f"https://now.nemoneai.com/posts/{p['id']}",
        "category": "PLACE",
        "badge": badge,
        "tags": [t for t in (region, "팝업스토어") if t],
        "published_at": p.get("created_at"),
    }


def collect_pace(limit: int = 10) -> dict:
    """ranking: PACE 인기 순위 TOP5(실시간일 필요 없음 — 30분 스냅샷). items: 이미지가 있는 항목(관찰 영역용)."""
    rows = _get_json(f"{PACE_BASE}/places/popular?limit=30")
    today = datetime.now(KST).date()
    rows = [p for p in (rows if isinstance(rows, list) else []) if p.get("title")]
    ranking = []
    for i, p in enumerate(rows[:5], start=1):
        it = _pace_item(p, today)
        it["rank"] = i
        ranking.append(it)
    items = [it for it in (_pace_item(p, today) for p in rows) if it["image"]][:limit]
    return {"service": "pace", "updated_at": datetime.now(KST).isoformat(), "items": items, "ranking": ranking}


def _guide_tags(raw) -> list:
    if isinstance(raw, list):
        return [str(x) for x in raw][:3]
    try:
        v = ast.literal_eval(raw) if isinstance(raw, str) else []
        return [str(x) for x in v][:3] if isinstance(v, list) else []
    except Exception:  # noqa: BLE001
        return []


def collect_plants(limit: int = 10) -> dict:
    data = _get_json(f"{PLANTS_BASE}/api/guides?limit=40")
    rows = data.get("items", data) if isinstance(data, dict) else data
    items = []
    # 최신순으로 정렬한 뒤, 직접 쓴 글(original)을 앞으로(안정 정렬이라 그 안에서는 최신순 유지)
    ordered = sorted(rows or [], key=lambda g: g.get("published_at") or "", reverse=True)
    ordered.sort(key=lambda g: 0 if g.get("source") == "original" else 1)
    for g in ordered:
        image = _image_ok(g.get("thumbnail_url"), PLANTS_BASE)
        if not image or not g.get("title"):
            continue
        items.append({
            "id": g["slug"],
            "title": g["title"],
            "summary": _clean(g.get("summary"), 80),
            "image": image,
            "url": f"{PLANTS_BASE}/guide/{g['slug']}",
            "category": "LIFE",
            "badge": None,
            "tags": _guide_tags(g.get("tags")),
            "published_at": g.get("published_at"),
        })
        if len(items) >= limit:
            break
    return {"service": "plants", "updated_at": datetime.now(KST).isoformat(), "items": items}


def collect_plants_magazine(limit: int = 5) -> dict:
    """PLANTS 매거진(맛매치 Special #6 소속 기사) 최신순. 1번 항목만 본문 앞부분을 요약으로 붙인다."""
    rows = _get_json(f"{PLANTS_BASE}/api/magazine")
    rows = sorted(rows if isinstance(rows, list) else [], key=lambda m: m.get("created_at") or "", reverse=True)
    items = []
    for i, m in enumerate(rows[:limit]):
        summary = ""
        if i == 0:
            try:
                summary = _clean(_get_json(f"{PLANTS_BASE}/api/magazine/{m['id']}").get("body_text"), 80)
            except Exception:  # noqa: BLE001 — 요약은 부가 정보
                summary = ""
        items.append({
            "id": str(m["id"]),
            "title": m["title"],
            "summary": summary,
            "image": _image_ok(m.get("image_url"), PLANTS_BASE),
            "url": f"{PLANTS_BASE}/guide/magazine/{m['id']}",
            "category": "LIFE",
            "badge": None,
            "tags": [],
            "published_at": m.get("created_at"),
        })
    return {"service": "plants_magazine", "updated_at": datetime.now(KST).isoformat(), "items": items}


def collect_lii(limit: int = 2) -> dict:
    """LII '오늘의 분석' — 5년 재무 기반 분석 요약. 매수·매도 판단 문구는 가져오지 않는다(요약 첫 문장과 4개 점수만)."""
    data = _get_json(f"{LII_BASE}/api/company/ai-view-picks?limit={limit}")
    rows = data.get("data", data) if isinstance(data, dict) else data
    if isinstance(rows, dict):  # {"data": {"items": [...]}} 형태
        rows = rows.get("items", [])
    items = []
    for c in rows or []:
        summary = c.get("summary")
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except Exception:  # noqa: BLE001
                try:
                    summary = ast.literal_eval(summary)
                except Exception:  # noqa: BLE001
                    summary = {}
        sentences = (summary or {}).get("sentences") or []
        first = _clean(sentences[0].get("text") if sentences else "", 110)
        sc = c.get("five_axis_scores") or {}
        if isinstance(sc, str):
            try:
                sc = json.loads(sc)
            except Exception:  # noqa: BLE001
                try:
                    sc = ast.literal_eval(sc)
                except Exception:  # noqa: BLE001
                    sc = {}
        items.append({
            "id": c["ticker"],
            "title": c["name"],
            "summary": first,
            "image": None,
            "url": f"{LII_BASE}/company/{c['ticker']}/ai-view",
            "category": "FUTURE",
            "badge": None,
            "tags": [],
            "scores": {k: sc.get(k) for k in ("growth", "profitability", "valuation", "financial_health")},
            "published_at": c.get("generated_at"),
        })
    return {"service": "lii", "updated_at": datetime.now(KST).isoformat(), "items": items}


def collect_youtube(limit: int = 5) -> dict:
    """유튜브 채널 RSS(Atom) — 쇼츠·실시간 방송은 제외하고 정식 영상만."""
    import xml.etree.ElementTree as ET
    ns = {"a": "http://www.w3.org/2005/Atom", "m": "http://search.yahoo.com/mrss/"}
    root = ET.fromstring(_get_text(f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"))
    items = []
    for e in root.findall("a:entry", ns):
        link = (e.find("a:link", ns).get("href") if e.find("a:link", ns) is not None else "") or ""
        title = _clean(e.findtext("a:title", namespaces=ns), 90)
        if "/shorts/" in link or "실시간 스트림" in title or not link:
            continue
        vid = (e.findtext("{http://www.youtube.com/xml/schemas/2015}videoId") or link.rsplit("=", 1)[-1])
        th = e.find("m:group/m:thumbnail", ns)
        items.append({
            "id": vid, "title": title, "summary": "", "image": _channel_image(th.get("url") if th is not None else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"),
            "url": link, "category": "YOUTUBE", "badge": None, "tags": [], "published_at": _rfc_to_kst_iso(e.findtext("a:published", namespaces=ns)),
        })
        if len(items) >= limit:
            break
    return {"service": "youtube", "updated_at": datetime.now(KST).isoformat(), "items": items}


def collect_podcast(limit: int = 5) -> dict:
    """맛의문명사 팟캐스트 RSS — 에피소드 제목·이미지·재생 시간. 링크는 RSS의 에피소드 페이지."""
    import xml.etree.ElementTree as ET
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    ch = ET.fromstring(_get_text(PODCAST_RSS)).find("channel")
    show_img = ch.find("itunes:image", ns)
    show_img = show_img.get("href") if show_img is not None else None
    items = []
    for it in ch.findall("item")[:limit]:
        im = it.find("itunes:image", ns)
        dur = (it.findtext("itunes:duration", namespaces=ns) or "").strip()
        items.append({
            "id": (it.findtext("guid") or it.findtext("link") or "")[-40:], "title": _clean(it.findtext("title"), 90),
            "summary": f"재생 {dur}" if dur else "", "image": _channel_image((im.get("href") if im is not None else None) or show_img),
            "url": it.findtext("link") or PODCAST_URL, "category": "PODCAST", "badge": None, "tags": [], "published_at": _rfc_to_kst_iso(it.findtext("pubDate")),
        })
    return {"service": "podcast", "updated_at": datetime.now(KST).isoformat(), "items": [x for x in items if x["title"]]}


def collect_blog(limit: int = 5) -> dict:
    """네이버 블로그 RSS — 제목·요약·본문 첫 이미지. 링크의 추적 파라미터(fromRss 등)는 뗀다."""
    import xml.etree.ElementTree as ET
    ch = ET.fromstring(_get_text(BLOG_RSS)).find("channel")
    items = []
    for it in ch.findall("item")[:limit]:
        desc = it.findtext("description") or ""
        m = re.search(r'<img[^>]*src="([^"]+)"', desc)
        link = (it.findtext("link") or "").split("?")[0] or BLOG_URL
        items.append({
            "id": link.rsplit("/", 1)[-1], "title": _clean(it.findtext("title"), 90), "summary": _clean(desc, 70),
            "image": _channel_image(unescape(m.group(1)) if m else None), "url": link, "category": "BLOG", "badge": None, "tags": [],
            "published_at": _rfc_to_kst_iso(it.findtext("pubDate")),
        })
    return {"service": "blog", "updated_at": datetime.now(KST).isoformat(), "items": [x for x in items if x["title"]]}


COLLECTORS = {"pace": collect_pace, "plants": collect_plants, "plants_magazine": collect_plants_magazine, "lii": collect_lii,
              "youtube": collect_youtube, "podcast": collect_podcast, "blog": collect_blog}
# ───────────────────────── 스냅샷 저장 ─────────────────────────
def init_tables(engine) -> None:
    global _engine
    _engine = engine
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS portal_feed (
                service TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                fetched_at TIMESTAMP NOT NULL,
                ok BOOLEAN NOT NULL DEFAULT TRUE
            )
        """))
        pk = "SERIAL PRIMARY KEY" if engine.dialect.name == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS portal_clicks (
                id {pk},
                service TEXT NOT NULL,
                slot TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
        """))


def save_snapshot(engine, service: str, feed: dict) -> bool:
    """성공(항목 1개 이상)일 때만 덮어쓴다. 실패·빈 응답이면 기존 스냅샷 유지하고 ok=false 만 표시."""
    now = datetime.now(KST).replace(tzinfo=None)
    with engine.begin() as conn:
        if feed and feed.get("items"):
            res = conn.execute(text("UPDATE portal_feed SET payload=:p, fetched_at=:t, ok=TRUE WHERE service=:s"),
                               {"p": json.dumps(feed, ensure_ascii=False), "t": now, "s": service})
            if not res.rowcount:
                conn.execute(text("INSERT INTO portal_feed (service, payload, fetched_at, ok) VALUES (:s,:p,:t,TRUE)"),
                             {"s": service, "p": json.dumps(feed, ensure_ascii=False), "t": now})
            return True
        conn.execute(text("UPDATE portal_feed SET ok=FALSE WHERE service=:s"), {"s": service})
        return False


def refresh_all(engine=None) -> dict:
    engine = engine or _engine
    result = {}
    for name, fn in COLLECTORS.items():
        try:
            result[name] = save_snapshot(engine, name, fn())
        except Exception as e:  # noqa: BLE001 — 한 서비스 실패가 다른 서비스 수집을 막지 않는다
            logger.warning("[portal] %s 수집 실패(기존 스냅샷 유지): %s", name, str(e)[:120])
            try:
                with engine.begin() as conn:
                    conn.execute(text("UPDATE portal_feed SET ok=FALSE WHERE service=:s"), {"s": name})
            except Exception:  # noqa: BLE001
                pass
            result[name] = False
    return result


def start(scheduler, engine) -> None:
    """서버 기동 시: 테이블 준비 + 즉시 1회 수집(백그라운드) + 30분 주기. 기존 스케줄러에 얹는다."""
    init_tables(engine)
    scheduler.add_job(refresh_all, "cron", minute="7,37", id="portal_refresh", kwargs={"engine": engine},
                      max_instances=1, coalesce=True)
    threading.Timer(3.0, refresh_all, kwargs={"engine": engine}).start()


def load_snapshots(engine) -> dict:
    out = {}
    with engine.connect() as conn:
        for r in conn.execute(text("SELECT service, payload, fetched_at, ok FROM portal_feed")):
            try:
                feed = json.loads(r.payload)
            except Exception:  # noqa: BLE001
                continue
            feed["_fetched_at"] = str(r.fetched_at) if r.fetched_at else None
            feed["_ok"] = bool(r.ok)
            out[r.service] = feed
    return out


# ───────────────────────── AIM(맛매치) 글: 요청 시 DB 조회 ─────────────────────────
def aim_items(engine, limit: int = 40) -> list:
    with engine.connect() as conn:
        owned = set()
        for r in conn.execute(text("SELECT post_ids FROM specials WHERE id IN (:a, :b)"),
                              {"a": SERVICE_SPECIAL_IDS[0], "b": SERVICE_SPECIAL_IDS[1]}):
            try:
                owned |= {int(x) for x in json.loads(r.post_ids)}
            except Exception:  # noqa: BLE001
                pass
        rows = conn.execute(text(
            "SELECT id, title, body_text, category, image_url, created_at FROM posts ORDER BY created_at DESC LIMIT :n"
        ), {"n": limit * 2}).fetchall()
    items = []
    for p in rows:
        if p.id in owned:
            continue  # PACE·PLANTS 소속 기사는 그 서비스 몫
        image = _image_ok(p.image_url)
        if not image:
            continue
        items.append({
            "id": str(p.id),
            "title": p.title,
            "summary": _clean(re.sub(r"^\s*\d+\.\s*", "", re.sub(r"<[^>]*>", " ", p.body_text or "").strip()), 80),
            "image": image,
            "url": f"/posts/{p.id}",  # 내부 링크(UTM 없음 — 정본 URL 변형을 만들지 않는다)
            "category": CATEGORY_AIM.get(p.category, "LIFE"),
            "badge": None,
            "tags": [],
            "published_at": str(p.created_at) if p.created_at else None,
        })
        if len(items) >= limit:
            break
    return items


def aim_ranking(engine, limit: int = 5) -> list:
    """맛매치 주간 랭킹(하루 2회 재계산되는 TOP10 캐시). PACE·PLANTS 소속 기사는 그 서비스 몫이라 뺀다."""
    if _aim_ranking_provider is None:
        return []
    rows = list(_aim_ranking_provider() or [])
    with engine.connect() as conn:
        owned = set()
        for r in conn.execute(text("SELECT post_ids FROM specials WHERE id IN (:a, :b)"),
                              {"a": SERVICE_SPECIAL_IDS[0], "b": SERVICE_SPECIAL_IDS[1]}):
            try:
                owned |= {int(x) for x in json.loads(r.post_ids)}
            except Exception:  # noqa: BLE001
                pass
        rows = [r for r in rows if r["id"] not in owned][:limit]
        bodies = {}
        if rows:
            first = rows[0]["id"]
            b = conn.execute(text("SELECT body_text FROM posts WHERE id = :i"), {"i": first}).first()
            bodies[first] = b.body_text if b else ""
    items = []
    for i, p in enumerate(rows, start=1):
        items.append({
            "id": str(p["id"]),
            "title": p["title"],
            "summary": _clean(re.sub(r"^\s*\d+\.\s*", "", re.sub(r"<[^>]*>", " ", bodies.get(p["id"], "")).strip()), 80),
            "image": _image_ok(p.get("image_url")),
            "url": f"/posts/{p['id']}",
            "category": CATEGORY_AIM.get(p.get("category"), "LIFE"),
            "badge": None,
            "tags": [],
            "published_at": str(p["created_at"]) if p.get("created_at") else None,
            "rank": i,
        })
    return items


def news_items(engine, limit: int = 4) -> list:
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, title, created_at FROM nemone_news ORDER BY created_at DESC LIMIT :n"), {"n": limit}).fetchall()
        return [{"id": r.id, "title": r.title, "url": f"https://home.nemoneai.com/notice/{r.id}/",
                 "date": str(r.created_at)[:10].replace("-", ".") if r.created_at else ""} for r in rows]
    except Exception:  # noqa: BLE001
        return []


# ───────────────────────── 통합 응답 ─────────────────────────
SERVICE_LABEL = {"pace": "네모네 PACE", "aim": "네모네 AIM", "plants": "네모네 Plants", "lii": "네모네 LII"}


def _slot(item: dict, service: str, slot: str) -> dict:
    it = dict(item)
    it["service"] = service
    it["service_label"] = SERVICE_LABEL.get(service, service)
    it["url"] = _utm(it["url"], service, slot)
    it["slot"] = slot
    return it


def _panel(key: str, category: str, title: str, sub: str, link: str, service: str, items: list, ranked: bool) -> Optional[dict]:
    if not items:
        return None
    return {
        "key": key, "category": category, "title": title, "sub": sub, "link": link, "ranked": ranked,
        "items": [_slot(it, service, f"{key}_{'rank' if ranked else 'item'}{i + 1}") for i, it in enumerate(items[:5])],
    }


def build_today(engine) -> dict:
    snaps = load_snapshots(engine)
    pace_feed = snaps.get("pace") or {}
    pace = pace_feed.get("items", [])
    pace_rank = pace_feed.get("ranking", [])
    plants = (snaps.get("plants") or {}).get("items", [])
    plants_mag = (snaps.get("plants_magazine") or {}).get("items", [])
    lii = (snaps.get("lii") or {}).get("items", [])
    try:
        aim = aim_items(engine)
    except Exception as e:  # noqa: BLE001
        logger.warning("[portal] AIM 조회 실패: %s", str(e)[:120])
        aim = []
    try:
        aim_rank = aim_ranking(engine)
    except Exception as e:  # noqa: BLE001
        logger.warning("[portal] AIM 랭킹 조회 실패: %s", str(e)[:120])
        aim_rank = []
    taste = [a for a in aim if a["category"] == "TASTE"]

    # 오늘의 발견: ① PACE 인기 TOP5 ② 맛매치(AIM) 주간 인기 TOP5 ③ PLANTS 매거진 최신 5 — 비는 패널은 숨긴다
    discover = [p for p in (
        _panel("pace", "PLACE", "지금 뜨는 곳 TOP 5", "최근 48시간 조회·좋아요 기준", "https://now.nemoneai.com/ranking/place", "pace", pace_rank, True),
        _panel("aim", "AIM", "이번 주 인기 글 TOP 5", "네모네 AIM 주간 랭킹", "/aim", "aim", aim_rank, True),
        _panel("plants", "LIFE", "플랜트 매거진", "Plants 매거진 최신 글", "https://plants.nemoneai.com/guide/magazine", "plants", plants_mag, False),
    ) if p]

    used = {(p["items"][i]["service"], p["items"][i]["id"]) for p in discover for i in range(len(p["items"]))}

    def mk(pool: list, svc: str, prefix: str, n: int) -> list:
        res = []
        for it in pool:
            if len(res) >= n:
                break
            if (svc, it["id"]) in used:
                continue
            res.append(_slot(it, svc, f"{prefix}{len(res) + 1}"))
        return res

    observe_pool = {
        "place": mk(pace, "pace", "obs_place", 5),
        "taste": mk(taste, "aim", "obs_taste", 5),
        "plant": mk(plants, "plants", "obs_plant", 5),
        "mind": [],
        "future": mk(lii, "lii", "obs_future", 5),
    }
    mixed = []
    for i in range(5):
        for key in ("taste", "place", "plant"):
            if i < len(observe_pool[key]):
                mixed.append(observe_pool[key][i])
    other_aim = mk([a for a in aim if a["category"] != "TASTE"], "aim", "obs_aim", 3)
    mixed = (mixed[:3] + other_aim[:1] + mixed[3:])[:5]
    observe = {"all": [dict(m, slot=f"obs_all{i + 1}") for i, m in enumerate(mixed)], **observe_pool}

    status = {k: ("ok" if (snaps.get(k) or {}).get("_ok") and (snaps.get(k) or {}).get("items") else "stale" if snaps.get(k) else "empty")
              for k in ("pace", "plants", "plants_magazine", "lii", "youtube", "podcast", "blog")}
    status["aim"] = "ok" if aim else "empty"
    channels = [p for p in (
        _panel("youtube", "YOUTUBE", "유튜브", "맛의문명사 | 네모네AIM 채널", YOUTUBE_CHANNEL_URL, "youtube", (snaps.get("youtube") or {}).get("items", []), False),
        _panel("podcast", "PODCAST", "팟캐스트", "맛의문명사 에피소드", PODCAST_URL, "podcast", (snaps.get("podcast") or {}).get("items", []), False),
        _panel("blog", "BLOG", "네이버 블로그", "네모네AIM 블로그", BLOG_URL, "blog", (snaps.get("blog") or {}).get("items", []), False),
    ) if p]
    return {
        "date": datetime.now(KST).date().isoformat(),
        "generated_at": datetime.now(KST).isoformat(),
        "discover": discover,
        "channels": channels,
        "lii": [_slot(x, "lii", f"lii{i + 1}") for i, x in enumerate(lii[:2])],
        "observe": observe,
        "aim_latest": [_slot(x, "aim", f"aim{i + 1}") for i, x in enumerate(aim[:6])],
        "service_images": {},
        "news": news_items(engine),
        "status": status,
    }


@router.get("/today")
def portal_today(response: Response):
    response.headers["Cache-Control"] = "public, max-age=600"
    try:
        return build_today(_engine)
    except Exception as e:  # noqa: BLE001 — 500 대신 빈 응답(포털은 빈 상태로도 정상 표시)
        logger.warning("[portal] today 실패: %s", str(e)[:160])
        return {"date": datetime.now(KST).date().isoformat(), "generated_at": None, "discover": [], "lii": [], "observe": {
            "all": [], "place": [], "taste": [], "plant": [], "mind": [], "future": []}, "aim_latest": [],
            "service_images": {}, "news": [], "status": {"error": True}}


_click_lock = threading.Lock()
_click_recent: dict = {}


@router.post("/click")
async def portal_click(request: Request):
    """포털 → 서비스 이동 클릭 기록(beacon). 개인정보 없음. 같은 IP의 과도한 호출은 무시."""
    try:
        body = await request.json()
        service = str(body.get("service", ""))[:20]
        slot = str(body.get("slot", ""))[:40]
        ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()
        now = datetime.now(KST)
        with _click_lock:
            recent = [t for t in _click_recent.get(ip, []) if (now - t).total_seconds() < 60]
            if len(recent) >= 30 or not re.fullmatch(r"[a-z0-9_\-]{1,20}", service) or not re.fullmatch(r"[a-z0-9_\-]{1,40}", slot):
                return Response(status_code=204)
            recent.append(now)
            _click_recent[ip] = recent
        with _engine.begin() as conn:
            conn.execute(text("INSERT INTO portal_clicks (service, slot, created_at) VALUES (:s,:l,:t)"),
                         {"s": service, "l": slot, "t": now.replace(tzinfo=None)})
    except Exception:  # noqa: BLE001
        pass
    return Response(status_code=204)
