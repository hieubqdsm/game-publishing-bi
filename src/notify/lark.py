"""
Lark notify — gửi tin nhắn qua Custom Bot webhook (một chiều, không tốn API quota).

Chỉ dùng webhook custom bot trong group Lark:
  POST LARK_WEBHOOK_URL  body JSON {"timestamp", "sign", "msg_type", ...}
Nếu bot bật "Signature Verification" thì cần ký:
  sign = base64( HMAC-SHA256(key="{timestamp}\\n{secret}", msg="") )

Nguyên tắc: module này là transport thuần — KHÔNG query DB, KHÔNG raise
(notification fail không được làm hỏng pipeline crawler).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import requests
from loguru import logger

from config import LARK_WEBHOOK_SECRET, LARK_WEBHOOK_URL

_TIMEOUT = 10  # giây


def _sign(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _post(payload: dict) -> bool:
    """POST payload (kèm sign nếu có secret) tới webhook. Trả True nếu Lark nhận."""
    if not LARK_WEBHOOK_URL:
        logger.debug("[lark] LARK_WEBHOOK_URL chưa cấu hình — bỏ qua gửi tin")
        return False

    if LARK_WEBHOOK_SECRET:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = _sign(ts, LARK_WEBHOOK_SECRET)

    try:
        resp = requests.post(
            LARK_WEBHOOK_URL,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception as e:  # noqa: BLE001 — notify không được làm crash pipeline
        logger.warning(f"[lark] gửi webhook lỗi: {e}")
        return False

    # Lark trả {"code":0,"msg":"success"} khi OK
    if data.get("code") == 0:
        return True
    logger.warning(f"[lark] Lark từ chối tin: {data}")
    return False


def send_text(text: str) -> bool:
    """Gửi tin nhắn text thuần (hỗ trợ @user nếu bật mention trong group)."""
    return _post({"msg_type": "text", "content": {"text": text}})


def send_card(title: str, markdown: str, note: str | None = None,
              header_color: str = "blue") -> bool:
    """Gửi card có header màu + thân lark_md.

    lark_md hỗ trợ: **bold**, [text](url), xuống dòng \\n.
    header_color: blue | green | turquoise | violet | orange | red...
    """
    elements: list[dict] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": markdown}},
    ]
    if note:
        elements.append({
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": note}],
        })
    card = {
        "config": {"wide_markdown_mode": True},
        "header": {
            "template": header_color,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }
    return _post({"msg_type": "interactive", "card": card})
