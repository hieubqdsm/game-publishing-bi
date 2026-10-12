"""
News digest → Lark group.

Gọi sau mỗi lần news crawl (scripts/run_news.py) — gửi card tóm tắt:
  - Số tin mới theo category (ai / game / tech / steam_update)
  - Headlines nổi bật (mới nhất, tối đa 2 tin mỗi nguồn để tránh 1 báo tràn card)

Chỉ đếm tin có fetched_at >= thời điểm bắt đầu crawl (fetched_at chỉ ghi lúc
INSERT lần đầu nên đây chính xác là tin MỚI của lần chạy này, kể cả khi
scheduler chạy chồng nhau).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger

from src.notify import lark
from src.storage.db import get_connection

MAX_HEADLINES = 8
MAX_PER_SOURCE = 2
CAT_LABELS = {
    "ai": "🤖 AI",
    "game": "🎮 Game",
    "tech": "📱 Tech",
    "steam_update": "🛠️ Steam",
}


def _utcnow_db_format() -> str:
    """Format giống DEFAULT (datetime('now')) của SQLite: 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def send_news_digest(crawl_started_at: str | None = None) -> bool:
    """Gửi digest tin mới từ lần crawl vừa xong. Trả False nếu không có gì mới."""
    since = crawl_started_at or (
        datetime.now(timezone.utc) - timedelta(minutes=30)
    ).strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT n.title, n.url, n.category, s.source_name
            FROM fact_news n
            JOIN dim_news_source s ON n.source_id = s.source_id
            WHERE n.fetched_at >= ?
            ORDER BY n.published_at DESC
            """,
            (since,),
        ).fetchall()

    if not rows:
        logger.info("[lark] Không có tin mới — bỏ qua digest")
        return False

    # Phân bố theo category (đầy đủ, gồm steam_update)
    counts: dict[str, int] = {}
    for r in rows:
        cat = r["category"] or "tech"
        counts[cat] = counts.get(cat, 0) + 1
    breakdown = " · ".join(
        f"{CAT_LABELS.get(c, c)}: {n}" for c, n in sorted(counts.items(), key=lambda x: -x[1])
    )

    # Headlines: bỏ steam_update, tối đa MAX_PER_SOURCE mỗi nguồn
    headlines: list[dict] = []
    per_source: dict[str, int] = {}
    for r in rows:
        if r["category"] == "steam_update":
            continue
        src = r["source_name"]
        if per_source.get(src, 0) >= MAX_PER_SOURCE:
            continue
        per_source[src] = per_source.get(src, 0) + 1
        headlines.append(r)
        if len(headlines) >= MAX_HEADLINES:
            break

    lines = [f"**{len(rows)} tin mới** — {breakdown}", ""]
    for r in headlines:
        title = (r["title"] or "").strip()[:100]
        lines.append(f"**[{r['source_name']}]** [{title}]({r['url']})")
    if not headlines:
        lines.append("(toàn bộ tin mới là Steam updates)")

    now_vn = datetime.now(timezone.utc) + timedelta(hours=7)
    ok = lark.send_card(
        title=f"📰 News crawl xong — {now_vn.strftime('%H:%M %d/%m')}",
        markdown="\n".join(lines),
        note="Chi tiết: dashboard 📰 Daily News (localhost:8501) · nguồn: RSS + AI + HN + Steam",
    )
    if ok:
        logger.success(f"[lark] Đã gửi digest {len(rows)} tin mới")
    return ok
