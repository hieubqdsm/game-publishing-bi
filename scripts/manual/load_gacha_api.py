"""
Load gacha revenue trực tiếp từ revenue.ennead.cc internal API → SQLite.

Thay thế workflow copy-paste HTML table: trang render table bằng JS gọi
`/api/revenue` (kèm auth flow) — script này replicate flow đó bằng requests:

  1. POST /api/auth/token (X-Client-Token: random uuid) → {nonce, signature, timestamp}
  2. GET  /api/revenue  + headers X-Nonce/X-Signature/X-Timestamp (session cookie)

Response: {"games": [{id, name, name_en, publisher, region, icon,
                      monthly_data: {"MM-YYYY": {total_revenue, ...}}, ...}]}

→ UPSERT dim_gacha_game + fact_gacha_revenue (rank tự tính theo revenue
trong tháng, idempotent). Reuse upsert helpers của parse_gacha_html.py.

Usage:
  python scripts/manual/load_gacha_api.py                # fetch + load
  python scripts/manual/load_gacha_api.py --file x.json  # load JSON có sẵn
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

import requests
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config import LOG_LEVEL, ensure_dirs
from scripts.manual.parse_gacha_html import (
    set_first_seen_month,
    upsert_game,
    upsert_revenue,
)
from src.storage.db import get_connection, init_schema

logger.remove()
logger.add(sys.stderr, level=LOG_LEVEL)

BASE = "https://revenue.ennead.cc"
SOURCE_TAG = "ennead_api"


def fetch_revenue_api() -> dict:
    """Replicate auth flow của web client rồi GET /api/revenue."""
    client_token = str(uuid.uuid4())
    sess = requests.Session()
    sess.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
    auth = sess.post(
        f"{BASE}/api/auth/token",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Client-Token": client_token,
            "X-Request-Path": "/revenue",
            "X-Request-Method": "GET",
        },
        timeout=30,
    )
    auth.raise_for_status()
    tok = auth.json()

    resp = sess.get(
        f"{BASE}/api/revenue",
        headers={
            "Accept": "application/json",
            "X-Client-Token": client_token,
            "X-Timestamp": tok["timestamp"],
            "X-Request-Path": "/revenue",
            "X-Signature": tok["signature"],
            "X-Nonce": tok["nonce"],
            "X-Request-Method": "GET",
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def load_payload(payload: dict, source: str = SOURCE_TAG) -> dict:
    """UPSERT toàn bộ games/monthly_data vào DB. Trả stats.

    API trả nhiều entry cho cùng game_id (mỗi region một entry: GLOBAL/CN/JP).
    Schema có UNIQUE(game_id, snapshot_month) nên aggregate: tổng revenue
    các region trong tháng → 1 row scope='combined' (khớp semantics HTML cũ).
    """
    games = payload["games"]
    games_added = facts_inserted = facts_updated = 0

    # Gộp các entry trùng game_id: name/meta lấy từ entry đầu, revenue cộng dồn
    merged: dict[str, dict] = {}
    for g in games:
        name = (g.get("name_en") or g.get("name") or "").strip()
        if not name:
            continue
        entry = merged.setdefault(
            g["id"], {"meta": g, "name": name, "monthly": {}}
        )
        for month_key, md in (g.get("monthly_data") or {}).items():
            rev = md.get("total_revenue") or 0
            mm, yyyy = month_key.split("-")
            month_iso = f"{yyyy}-{mm}"
            entry["monthly"][month_iso] = entry["monthly"].get(month_iso, 0) + float(rev)

    # Rank: revenue giảm dần trong mỗi tháng
    by_month: dict[str, list[tuple]] = {}  # month → [(meta, name, revenue)]
    for gid, entry in merged.items():
        for month_iso, rev in entry["monthly"].items():
            if rev > 0:
                by_month.setdefault(month_iso, []).append(
                    (entry["meta"], entry["name"], rev)
                )
    for month in by_month:
        by_month[month].sort(key=lambda r: r[2], reverse=True)

    for month, recs in sorted(by_month.items()):
        for rank, (g, name, rev) in enumerate(recs, start=1):
            game_id = upsert_game(name, g["id"], g.get("icon"))
            with get_connection() as conn:
                existed_before = conn.execute(
                    "SELECT 1 FROM fact_gacha_revenue WHERE game_id=?", (game_id,)
                ).fetchone()
            if not existed_before:
                games_added += 1
            if upsert_revenue(game_id, month, rank, rev, "combined", source):
                facts_inserted += 1
            else:
                facts_updated += 1
            set_first_seen_month(game_id, month)

    months = sorted(by_month)
    logger.success(
        f"DONE: {len(games)} games | {facts_inserted} inserted, "
        f"{facts_updated} updated | months: {months[0]} → {months[-1]} ({len(months)})"
    )
    return {
        "games_total": len(games),
        "games_added": games_added,
        "facts_inserted": facts_inserted,
        "facts_updated": facts_updated,
        "months": [months[0], months[-1]],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Load từ JSON file thay vì fetch API")
    parser.add_argument("--save", help="Save response JSON ra file (audit)")
    args = parser.parse_args()

    ensure_dirs()
    init_schema()

    if args.file:
        payload = json.loads(Path(args.file).read_text())
        logger.info(f"=== LOAD GACHA API JSON: {args.file} ===")
    else:
        logger.info(f"=== FETCH {BASE}/api/revenue ===")
        payload = fetch_revenue_api()
        if args.save:
            Path(args.save).write_text(json.dumps(payload, ensure_ascii=False))
            logger.info(f"Saved raw JSON → {args.save}")

    stats = load_payload(payload)
    logger.info(f"=== STATS: {stats} ===")

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT g.game_id) games, COUNT(DISTINCT r.snapshot_month) months,
                   COUNT(*) facts, MIN(r.snapshot_month) first_month, MAX(r.snapshot_month) last_month
            FROM fact_gacha_revenue r JOIN dim_gacha_game g ON r.game_id = g.game_id
            """
        ).fetchone()
    logger.info(
        f"DB: {row['games']} games, {row['facts']} facts, "
        f"{row['first_month']} → {row['last_month']}"
    )
