"""IndexNow — 새 글이 게시되는 즉시 Bing 등 참여 검색엔진에 알려 색인을 앞당긴다
(2026-09-11, fire-your-seo-agency 스킬 권고, now 백엔드와 동일 패턴).
키 파일은 frontend가 /{key}.txt로 서빙(키 소유 증명용) — frontend/src/app/{key}.txt/route.ts.
실패해도 조용히 무시(색인 자체엔 지장 없음, 사이트맵이 항상 백업 경로)."""
import logging

import requests

logger = logging.getLogger(__name__)

INDEXNOW_KEY = "9a3e959ce55c270129aa44b48b5a1c95"
INDEXNOW_HOST = "nemoneai.com"


def ping_indexnow(urls: list) -> None:
    if not urls:
        return
    try:
        requests.post(
            "https://api.indexnow.org/indexnow",
            json={
                "host": INDEXNOW_HOST,
                "key": INDEXNOW_KEY,
                "keyLocation": f"https://{INDEXNOW_HOST}/{INDEXNOW_KEY}.txt",
                "urlList": urls,
            },
            timeout=5,
        )
        logger.info("[indexnow] %d개 URL 핑 전송", len(urls))
    except Exception as e:
        logger.warning("[indexnow] 핑 실패(무시): %s", e)
