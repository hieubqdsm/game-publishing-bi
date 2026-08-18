"""
ActivePlayer.io crawler entry point — estimated mobile MAU/DAU/CCU by platform.

Usage:
    python scripts/run_activeplayer.py                # fetch all mobile games (~15 min)
    python scripts/run_activeplayer.py --max 5        # smoke test 5 games
    python scripts/run_activeplayer.py --slugs pubg-mobile,garena-free-fire
    python scripts/run_activeplayer.py --no-fetch     # re-parse HTML files in data/raw/

Data: ESTIMATED player counts (modeled from download trends), NOT official.
ToS: Tier 1 ✅ — see docs/tos_compliance.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import click
from loguru import logger

from config import LOG_LEVEL, ensure_dirs
from src.storage.db import init_schema

logger.remove()
logger.add(sys.stderr, level=LOG_LEVEL)


@click.command()
@click.option("--max", "max_items", type=int, default=None,
              help="Giới hạn số game (cho test). Mặc định: fetch hết.")
@click.option("--slugs", type=str, default=None,
              help="Comma-separated slugs để fetch cụ thể (vd: pubg-mobile,roblox)")
@click.option("--no-fetch", is_flag=True, default=False,
              help="Skip HTTP fetch, chỉ re-parse HTML có sẵn trong data/raw/activeplayer/")
@click.option("--dry-run", is_flag=True, default=False,
              help="Chỉ print config, không fetch")
def main(max_items: int | None, slugs: str | None, no_fetch: bool, dry_run: bool):
    """Fetch estimated mobile player counts từ ActivePlayer.io."""
    ensure_dirs()
    init_schema()

    from src.crawlers.activeplayer_crawler import ActivePlayerCrawler

    slug_list = [s.strip() for s in slugs.split(",")] if slugs else None

    logger.info("=== ACTIVEPLAYER CRAWL ===")
    logger.info(f"  max_items={max_items}, slugs={slug_list or '(discover)'}, "
                f"no_fetch={no_fetch}, dry_run={dry_run}")

    if dry_run:
        crawler = ActivePlayerCrawler()
        logger.info(f"  source: {crawler.source}")
        logger.info(f"  base_url: {crawler.cfg.get('base_url')}")
        logger.info(f"  rate_limit_ms: {crawler.cfg.get('rate_limit_ms')} + jitter {crawler.cfg.get('jitter_ms', 0)}")
        logger.info(f"  raw_dir: {crawler.raw_dir}")
        crawler.close()
        logger.info("Dry run — no fetch performed.")
        return

    with ActivePlayerCrawler() as crawler:
        stats = crawler.run(max_items=max_items, slugs=slug_list, skip_fetch=no_fetch)

    # Summary
    logger.success("=== DONE ===")
    for k, v in stats.items():
        logger.info(f"  {k}: {v}")

    # Quick sanity check: top MAU from DB
    try:
        from src.storage.db import get_connection
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT g.name, e.metric_name, e.metric_value
                FROM fact_engagement_metrics e
                JOIN dim_game g ON e.game_id = g.game_id
                WHERE e.source='activeplayer' AND e.metric_name LIKE 'mau_%'
                  AND e.snapshot_date = (SELECT MAX(snapshot_date) FROM fact_engagement_metrics
                                          WHERE source='activeplayer')
                ORDER BY e.metric_value DESC LIMIT 5
            """).fetchall()
            if rows:
                logger.info("Top 5 mobile games by MAU (latest month):")
                for r in rows:
                    logger.info(f"  {r['metric_name']:15s} {r['metric_value']:>8.1f}M  {r['name']}")
    except Exception as e:
        logger.warning(f"Sanity check failed (non-fatal): {e}")

    sys.exit(0)


if __name__ == "__main__":
    main()
