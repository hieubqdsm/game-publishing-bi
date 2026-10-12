"""
BaseCrawler: shared infrastructure cho mọi crawler.

Cung cấp:
- HTTP session (requests) với retry (tenacity) + rate limiting
- Lưu raw JSON response vào data/raw/<source>/ để audit
- Logging thống nhất (loguru)
- Helper upsert vào SQLite

Mọi crawler con kế thừa và implement `run()`.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional

import requests
import yaml
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import PROJECT_ROOT, RAW_DIR, LOG_LEVEL
from src.storage.db import get_connection
from src.utils.dns_fallback import install_dns_fallback

# ISP DNS thỉnh thoảng chặn/fail từng domain (vd api.steampowered.com bên VNPT)
# → wrap getaddrinfo fallback qua DoH 1.1.1.1 cho domain trong DNS_FALLBACK_HOSTS
install_dns_fallback()


# ---- Logger setup (chỉ configure 1 lần) -----------------------------------
logger.remove()
logger.add(
    PROJECT_ROOT / "logs" / "pipeline.log",
    level=LOG_LEVEL,
    rotation="10 MB",
    retention="30 days",
    encoding="utf-8",
)
logger.add(sys.stderr, level=LOG_LEVEL)


# ---- Load sources.yaml -----------------------------------------------------
def load_source_config(source: str) -> dict[str, Any]:
    """Load config của 1 source từ config/sources.yaml."""
    cfg_path = PROJECT_ROOT / "config" / "sources.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        all_cfg = yaml.safe_load(f)
    if source not in all_cfg:
        raise KeyError(f"Source '{source}' not in config/sources.yaml")
    return all_cfg[source]


# ---- Retry-decorated request ----------------------------------------------
class RateLimitError(Exception):
    """Raised khi API trả 429 Too Many Requests và retry đã hết."""


class BaseCrawler:
    """
    Base class cho mọi crawler. Cung cấp:
      - self.session: requests.Session (connection pooling)
      - self.cfg:     config từ sources.yaml
      - self.source:  tên source ('steam' | 'itunes' | 'igdb')
      - get_json():   HTTP GET với retry + rate limit, trả parsed JSON
      - save_raw():   persist JSON response ra data/raw/<source>/<date>/<name>.json
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.cfg = load_source_config(source)
        self.raw_dir = RAW_DIR / source
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "GameBI-Pipeline/0.1 (portfolio project)"})
        # Rate limit (ms giữa các request)
        self._rate_limit_ms: int = int(self.cfg.get("rate_limit_ms", 500))
        self._last_request_ts: float = 0.0

    # ---- HTTP helpers -----------------------------------------------------
    def _respect_rate_limit(self) -> None:
        """Đảm bảo khoảng cách giữa 2 request ≥ rate_limit_ms."""
        elapsed = (time.monotonic() - self._last_request_ts) * 1000
        if elapsed < self._rate_limit_ms:
            time.sleep((self._rate_limit_ms - elapsed) / 1000.0)
        self._last_request_ts = time.monotonic()

    @retry(
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout, RateLimitError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def get_json(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Any:
        """GET request → JSON. Tự retry + rate limit. Raise HTTPError nếu 4xx/5xx."""
        self._respect_rate_limit()
        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=30)
        except (requests.ConnectionError, requests.Timeout):
            logger.warning(f"[{self.source}] network error → {url}")
            raise
        if resp.status_code == 429:
            logger.warning(f"[{self.source}] 429 rate-limited → {url}")
            raise RateLimitError(url)
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout, RateLimitError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def post_json(
        self,
        url: str,
        data: Optional[dict[str, Any]] = None,
        json_body: Any = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Any:
        """POST request → JSON (dùng cho IGDB). Tự retry + rate limit."""
        self._respect_rate_limit()
        try:
            resp = self.session.post(url, data=data, json=json_body, headers=headers, timeout=30)
        except (requests.ConnectionError, requests.Timeout):
            logger.warning(f"[{self.source}] network error → {url}")
            raise
        if resp.status_code == 429:
            raise RateLimitError(url)
        resp.raise_for_status()
        return resp.json()

    # ---- Raw persistence --------------------------------------------------
    def save_raw(self, name: str, payload: Any, snapshot_date: Optional[str] = None) -> Path:
        """
        Lưu raw JSON response ra:
            data/raw/<source>/<YYYY-MM-DD>/<name>.json
        Trả về Path để ghi vào dim_game.raw_payload_path (audit trail).
        """
        d = snapshot_date or date.today().isoformat()
        out_dir = self.raw_dir / d
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{name}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return out_path

    # ---- DB helpers -------------------------------------------------------
    def upsert_game(
        self,
        *,
        source_app_id: str,
        name: str,
        genre: Optional[str] = None,
        platform: Optional[str] = None,
        release_date: Optional[str] = None,
        price_usd: Optional[float] = None,
        publisher_name: Optional[str] = None,
        developer_name: Optional[str] = None,
        description: Optional[str] = None,
        raw_payload_path: Optional[str] = None,
    ) -> int:
        """
        UPSERT vào dim_game. Trả về game_id (surrogate key).
        Idempotent: chạy nhiều lần với cùng (source, source_app_id) chỉ update.
        """
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO dim_game (
                    source_app_id, source, name, genre, platform,
                    release_date, price_usd, publisher_name, developer_name,
                    description, raw_payload_path, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(source, source_app_id) DO UPDATE SET
                    name=excluded.name,
                    genre=excluded.genre,
                    platform=excluded.platform,
                    release_date=excluded.release_date,
                    price_usd=excluded.price_usd,
                    publisher_name=excluded.publisher_name,
                    developer_name=excluded.developer_name,
                    description=excluded.description,
                    raw_payload_path=excluded.raw_payload_path,
                    updated_at=datetime('now')
                """,
                (
                    str(source_app_id), self.source, name, genre, platform,
                    release_date, price_usd, publisher_name, developer_name,
                    description, raw_payload_path,
                ),
            )
            row = conn.execute(
                "SELECT game_id FROM dim_game WHERE source=? AND source_app_id=?",
                (self.source, str(source_app_id)),
            ).fetchone()
            return row["game_id"]

    def close(self) -> None:
        self.session.close()

    # ---- Subclass contract -----------------------------------------------
    def run(self, max_items: int = 100) -> dict[str, int]:
        """
        Entry point chính. Crawler con phải override.
        Trả dict stats: {"games_crawled": N, "facts_inserted": M}
        """
        raise NotImplementedError

    # ---- Context manager (cho `with`) ------------------------------------
    def __enter__(self) -> "BaseCrawler":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
