"""텔레그램 알림 전송 — now_back의 notification.send_alert와 동일한 방식(같은 봇/챗으로 전송)."""
import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def send_alert(message: str) -> None:
    """텔레그램 알림 전송. 토큰 미설정 시 로그만 출력."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    print(f"[alert] {message}")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"🍽️ MATMATCH 알림\n{message}"},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"[alert] 텔레그램 전송 실패: {e}")
