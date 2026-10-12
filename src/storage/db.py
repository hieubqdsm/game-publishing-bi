"""
SQLite connection & schema helpers.

Schema follows a star-schema design (see docs/data_dictionary.md):
- Dimensions: dim_game, dim_date, dim_publisher
- Facts:      fact_steam_playercounts, fact_itunes_rankings, fact_engagement_metrics

Tables use INTEGER PRIMARY KEY autoincrement for surrogate keys
and composite UNIQUE constraints to enable UPSERT (idempotent daily runs).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config import SQLITE_PATH

# ---- DDL -------------------------------------------------------------------
# Tách schema ra thành string để init_db.py và tests dùng chung.

SCHEMA_SQL = """
-- =========================================================
-- DIMENSIONS
-- =========================================================

CREATE TABLE IF NOT EXISTS dim_game (
    game_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source_app_id   TEXT NOT NULL,             -- Steam appid / iTunes trackId / IGDB id
    source          TEXT NOT NULL,             -- 'steam' | 'itunes' | 'igdb'
    name            TEXT NOT NULL,
    genre           TEXT,
    platform        TEXT,                       -- 'pc' | 'ios' | 'android'
    release_date    TEXT,                       -- ISO YYYY-MM-DD
    price_usd       REAL,
    publisher_name  TEXT,
    developer_name  TEXT,
    description     TEXT,
    raw_payload_path TEXT,                      -- đường dẫn tới raw JSON để audit
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(source, source_app_id)
);

CREATE INDEX IF NOT EXISTS idx_dim_game_source   ON dim_game(source);
CREATE INDEX IF NOT EXISTS idx_dim_game_genre    ON dim_game(genre);

-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_date (
    date            TEXT PRIMARY KEY,           -- ISO YYYY-MM-DD
    year            INTEGER NOT NULL,
    quarter         INTEGER NOT NULL,            -- 1..4
    month           INTEGER NOT NULL,            -- 1..12
    day_of_week     INTEGER NOT NULL,            -- 0=Mon .. 6=Sun
    is_weekend      INTEGER NOT NULL DEFAULT 0   -- 0/1
);

-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_publisher (
    publisher_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    country         TEXT,
    website         TEXT
);

-- =========================================================
-- FACTS
-- =========================================================

-- Steam: player counts & reviews (daily snapshot)
CREATE TABLE IF NOT EXISTS fact_steam_playercounts (
    snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         INTEGER NOT NULL,
    snapshot_date   TEXT NOT NULL,
    peak_ccu        INTEGER,                    -- peak concurrent users (today)
    positive_reviews INTEGER,
    negative_reviews INTEGER,
    fetched_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(game_id, snapshot_date),
    FOREIGN KEY (game_id) REFERENCES dim_game(game_id)
);
CREATE INDEX IF NOT EXISTS idx_fact_steam_date ON fact_steam_playercounts(snapshot_date);

-- iTunes: top chart rankings (daily snapshot per country/chart)
CREATE TABLE IF NOT EXISTS fact_itunes_rankings (
    ranking_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         INTEGER NOT NULL,
    snapshot_date   TEXT NOT NULL,
    country         TEXT NOT NULL,              -- ISO 2-letter ('US','VN')
    chart_name      TEXT NOT NULL,              -- 'top_free_games' etc.
    rank            INTEGER NOT NULL,
    fetched_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(game_id, snapshot_date, country, chart_name),
    FOREIGN KEY (game_id) REFERENCES dim_game(game_id)
);
CREATE INDEX IF NOT EXISTS idx_fact_itunes_date ON fact_itunes_rankings(snapshot_date);

-- Engagement metrics (generic long-format, dùng cho Reddit/YT/reviews sentiment)
CREATE TABLE IF NOT EXISTS fact_engagement_metrics (
    metric_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         INTEGER NOT NULL,
    snapshot_date   TEXT NOT NULL,
    source          TEXT NOT NULL,              -- 'reddit' | 'youtube' | 'steam_reviews'
    metric_name     TEXT NOT NULL,              -- 'mentions' | 'views' | 'sentiment_score'
    metric_value    REAL,
    fetched_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(game_id, snapshot_date, source, metric_name),
    FOREIGN KEY (game_id) REFERENCES dim_game(game_id)
);
CREATE INDEX IF NOT EXISTS idx_fact_engagement_date ON fact_engagement_metrics(snapshot_date);

-- =========================================================
-- NEWS (morning briefing — daily game news digest)
-- =========================================================

CREATE TABLE IF NOT EXISTS dim_news_source (
    source_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type  TEXT NOT NULL,             -- 'rss' | 'reddit' | 'steam_news'
    source_name  TEXT NOT NULL,             -- 'The Verge', 'r/games', 'Steam'
    feed_url     TEXT,
    tos_url      TEXT,
    notes        TEXT,
    UNIQUE(source_type, source_name)
);

CREATE TABLE IF NOT EXISTS fact_news (
    news_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    INTEGER NOT NULL,
    game_id      INTEGER,                   -- FK nếu match được với dim_game (nullable)
    title        TEXT NOT NULL,
    url          TEXT NOT NULL UNIQUE,      -- dedup key: cùng URL không insert lại
    summary      TEXT,
    author       TEXT,
    published_at TEXT NOT NULL,             -- ISO datetime từ source
    score        INTEGER,                   -- Reddit upvotes, Steam upvotes (nullable cho RSS)
    keywords     TEXT,                      -- comma-separated tags phát hiện được
    category     TEXT,                      -- 'ai' | 'game' | 'tech' | 'steam_update' (classify_news)
    fetched_at   TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (source_id) REFERENCES dim_news_source(source_id),
    FOREIGN KEY (game_id) REFERENCES dim_game(game_id)
);
CREATE INDEX IF NOT EXISTS idx_fact_news_published   ON fact_news(published_at);
CREATE INDEX IF NOT EXISTS idx_fact_news_source      ON fact_news(source_id);
CREATE INDEX IF NOT EXISTS idx_fact_news_game        ON fact_news(game_id);

-- =========================================================
-- GACHA REVENUE — mobile gacha game monthly revenue tracker
-- Input: HTML table paste hàng tháng (revenue.ennead.cc hoặc tương tự).
-- Source gốc: Sensor Tower mobile estimates (PC/console excluded).
-- Schema tách dim_gacha_game riêng (không nhồi vào dim_game vì metadata khác).
-- =========================================================

-- Dimension: gacha game (auto-created từ tên game trong HTML table)
CREATE TABLE IF NOT EXISTS dim_gacha_game (
    game_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL UNIQUE,        -- "Love and Deepspace"
    slug             TEXT NOT NULL,               -- "love-and-deepspace" (URL-friendly)
    icon_url         TEXT,                        -- từ <img alt src> trong HTML
    publisher        TEXT,
    first_seen_month TEXT,                        -- 'YYYY-MM' tháng đầu xuất hiện
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dim_gacha_game_slug ON dim_gacha_game(slug);

-- Fact: monthly revenue snapshot (1 row / game / tháng, idempotent UPSERT)
CREATE TABLE IF NOT EXISTS fact_gacha_revenue (
    fact_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         INTEGER NOT NULL,             -- FK → dim_gacha_game
    snapshot_month  TEXT NOT NULL,                -- 'YYYY-MM' (tháng của data, không phải tháng input)
    rank            INTEGER NOT NULL,             -- 1..N vị trí trong tháng đó
    revenue_usd     REAL NOT NULL,                -- ước tính mobile revenue (USD)
    scope           TEXT,                         -- 'combined' | 'global' | 'cn' | 'jp' (region breakdown)
    source          TEXT DEFAULT 'ennead',        -- 'ennead' | 'reddit' | 'manual' (audit nguồn input)
    fetched_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (game_id) REFERENCES dim_gacha_game(game_id),
    UNIQUE(game_id, snapshot_month)               -- UPSERT key (idempotent: re-parse không duplicate)
);
CREATE INDEX IF NOT EXISTS idx_fact_gacha_month ON fact_gacha_revenue(snapshot_month);
CREATE INDEX IF NOT EXISTS idx_fact_gacha_rank  ON fact_gacha_revenue(snapshot_month, rank);
CREATE INDEX IF NOT EXISTS idx_fact_gacha_game  ON fact_gacha_revenue(game_id);

-- =========================================================
-- STEAM HOURLY CCU — timestamp granularity cho CCU trajectory
-- Scheduler crawls hourly → mỗi giờ 1 snapshot per game
-- =========================================================
CREATE TABLE IF NOT EXISTS fact_steam_hourly_ccu (
    snapshot_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id        INTEGER NOT NULL,
    snapshot_ts    TEXT NOT NULL,                -- ISO timestamp 'YYYY-MM-DD HH:MM:SS' (full hour)
    peak_ccu       INTEGER NOT NULL,
    fetched_at     TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (game_id) REFERENCES dim_game(game_id),
    UNIQUE(game_id, snapshot_ts)                 -- 1 row/game/hour
);
CREATE INDEX IF NOT EXISTS idx_steam_hourly_ts   ON fact_steam_hourly_ccu(snapshot_ts);
CREATE INDEX IF NOT EXISTS idx_steam_hourly_game ON fact_steam_hourly_ccu(game_id, snapshot_ts);
"""


# ---- Connection helpers ----------------------------------------------------

@contextmanager
def get_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """
    Context manager trả về SQLite connection.
    Bật foreign_keys và row_factory = Row để truy cập theo tên cột.
    """
    path = db_path or SQLITE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(db_path: Path | None = None) -> None:
    """Tạo toàn bộ tables + indexes nếu chưa có (idempotent)."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate(conn)


def _migrate(conn) -> None:
    """Migrations cho DB đã tồn tại (CREATE TABLE IF NOT EXISTS không thêm cột mới)."""
    migrations = [
        # fact_news.category — thêm 2026-09: phân loại ai/game/tech/steam_update
        ("fact_news", "category", "ALTER TABLE fact_news ADD COLUMN category TEXT"),
    ]
    for table, column, ddl in migrations:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if column not in cols:
            conn.execute(ddl)


def get_table_rowcounts(db_path: Path | None = None) -> dict[str, int]:
    """Trả về số dòng mỗi table — dùng cho health check."""
    tables = [
        "dim_game", "dim_date", "dim_publisher",
        "dim_news_source", "fact_news",
        "fact_steam_playercounts", "fact_itunes_rankings", "fact_engagement_metrics",
        "dim_gacha_game", "fact_gacha_revenue",
        "fact_steam_hourly_ccu",
    ]
    out: dict[str, int] = {}
    with get_connection(db_path) as conn:
        for t in tables:
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return out


if __name__ == "__main__":
    # Chạy trực tiếp để test connection nhanh
    init_schema()
    print("Schema initialized. Row counts:")
    for t, n in get_table_rowcounts().items():
        print(f"  {t:30s} {n:>8d}")
