"""
News crawler entry point — morning game news briefing.

Usage:
    python scripts/run_news.py                # default 24h
    python scripts/run_news.py --hours 48     # 48h
    python scripts/run_news.py --hours 6      # 6h (sáng dậy đọc tin nửa ngày)

Pipeline: RSS (Verge/Polygon/IGN/...) + Reddit (r/games...) + Steam News API.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import click
from loguru import logger

from config import LOG_LEVEL, ensure_dirs
from src.storage.db import init_schema

logger.remove()
logger.add(sys.stderr, level=LOG_LEVEL)


@click.command()
@click.option("--hours", type=int, default=24,
              help="Số giờ lookback (default: 24)")
@click.option("--source", type=click.Choice(["rss", "ai", "reddit", "steam", "hackernews"]),
              default=None, help="Chỉ chạy 1 nguồn (mặc định: chạy hết)")
def main(hours: int, source: str | None):
    """Fetch game news từ RSS + AI + Reddit + Steam News API + Hacker News."""
    ensure_dirs()
    init_schema()

    # Thời điểm bắt đầu — dùng để lọc tin MỚI (fetched_at >= start) gửi Lark
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    logger.info(f"=== NEWS CRAWL | hours={hours}, source={source or 'ALL'} ===")

    from src.crawlers.news_crawler import NewsCrawler
    crawler = NewsCrawler(hours=hours)

    if source == "rss":
        from src.crawlers.news_crawler import RSS_FEEDS
        n = sum(crawler.fetch_rss_feed(f) for f in RSS_FEEDS)
        logger.success(f"RSS done: {n} items")
    elif source == "ai":
        from src.crawlers.news_crawler import AI_RSS_FEEDS
        n = sum(crawler.fetch_rss_feed(f, source_type="ai_rss") for f in AI_RSS_FEEDS)
        logger.success(f"AI news done: {n} items")
    elif source == "reddit":
        from src.crawlers.news_crawler import REDDIT_SUBREDDITS
        n = sum(crawler.fetch_reddit_subreddit(s) for s in REDDIT_SUBREDDITS)
        logger.success(f"Reddit done: {n} items")
    elif source == "hackernews":
        n = crawler.fetch_hacker_news()
        logger.success(f"Hacker News done: {n} items")
    elif source == "steam":
        n = crawler.fetch_steam_news()
        logger.success(f"Steam news done: {n} items")
    else:
        stats = crawler.run()
        logger.info(f"=== STATS: {stats} ===")

    # Quick summary in terminal
    from src.storage.db import get_connection
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT s.source_type, COUNT(*) as n, MAX(n.published_at) as latest
            FROM fact_news n
            JOIN dim_news_source s ON n.source_id = s.source_id
            WHERE n.published_at >= datetime('now', ?)
            GROUP BY s.source_type
            ORDER BY n DESC
            """,
            (f"-{hours} hours",),
        ).fetchall()

    logger.info("=== NEWS SUMMARY (last {}h) ===".format(hours))
    for r in rows:
        logger.info(f"  {r['source_type']:12s} {r['n']:>4d} items | latest: {r['latest']}")

    # Gửi digest tin MỚI của lần chạy này qua Lark webhook (nếu có cấu hình)
    from src.notify.digest import send_news_digest
    send_news_digest(started_at)

    sys.exit(0)


if __name__ == "__main__":
    main()
