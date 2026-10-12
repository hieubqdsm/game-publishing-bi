"""
Backfill cột fact_news.category cho các tin đã có trong DB.

Mặc định: chỉ điền các dòng NULL/rỗng (idempotent — chạy lại không đổi gì).
--force: phân loại lại TẤT CẢ (khi cải thiện rule trong classify_news.py).

Usage:
    python scripts/classify_news.py            # fill các tin chưa có category
    python scripts/classify_news.py --force    # re-classify toàn bộ
    python scripts/classify_news.py --dry-run  # xem trước, không ghi
"""
from __future__ import annotations

import sys

import click

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from src.storage.db import get_connection, init_schema
from src.transforms.classify_news import classify_article


def classify_all(force: bool, dry_run: bool) -> None:
    init_schema()  # đảm bảo cột category tồn tại (migration)
    with get_connection() as conn:
        where = "" if force else "WHERE category IS NULL OR TRIM(category) = ''"
        rows = conn.execute(
            f"""
            SELECT n.news_id, n.title, n.summary, s.source_type, s.source_name
            FROM fact_news n
            JOIN dim_news_source s ON n.source_id = s.source_id
            {where}
            """
        ).fetchall()

        if not rows:
            click.echo("Không có dòng nào cần phân loại. Thoát.")
            return

        counts: dict[str, int] = {}
        updates: list[tuple[str, int]] = []
        for r in rows:
            cat = classify_article(r["source_type"], r["source_name"],
                                   r["title"], r["summary"])
            counts[cat] = counts.get(cat, 0) + 1
            updates.append((cat, r["news_id"]))

        click.echo(f"Phân loại {len(rows)} tin:")
        for cat, n in sorted(counts.items(), key=lambda x: -x[1]):
            click.echo(f"  {cat:14} {n:5} ({n * 100 // len(rows)}%)")

        if dry_run:
            click.echo("(dry-run — không ghi DB)")
            return

        conn.executemany(
            "UPDATE fact_news SET category = ? WHERE news_id = ?", updates
        )
        click.echo(f"✓ Đã update {len(updates)} dòng.")


@click.command()
@click.option("--force", is_flag=True, help="Phân loại lại toàn bộ (không chỉ NULL)")
@click.option("--dry-run", is_flag=True, help="Xem trước phân bố, không ghi DB")
def main(force: bool, dry_run: bool) -> None:
    classify_all(force, dry_run)


if __name__ == "__main__":
    main()
