"""
Central configuration loader for the Sensor Tower pipeline.

Loads environment variables from `.env` (via python-dotenv) and exposes
them as typed constants. All other modules import from here — never read
os.environ directly outside this file.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

# ---- Project paths ---------------------------------------------------------
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DATA_DIR: Final[Path] = PROJECT_ROOT / "data"
RAW_DIR: Final[Path] = DATA_DIR / "raw"
PROCESSED_DIR: Final[Path] = DATA_DIR / "processed"
LOGS_DIR: Final[Path] = PROJECT_ROOT / "logs"

# Sub-dirs cho raw JSON từng nguồn
RAW_STEAM_DIR: Final[Path] = RAW_DIR / "steam"
RAW_ITUNES_DIR: Final[Path] = RAW_DIR / "itunes"
RAW_IGDB_DIR: Final[Path] = RAW_DIR / "igdb"

# ---- Load .env -------------------------------------------------------------
load_dotenv(PROJECT_ROOT / ".env")


def _get_bool(key: str, default: bool = False) -> bool:
    """Parse a boolean env var (accepts true/false/1/0/yes/no)."""
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


# ---- API credentials -------------------------------------------------------
STEAM_API_KEY: Final[str] = os.getenv("STEAM_API_KEY", "")
TWITCH_CLIENT_ID: Final[str] = os.getenv("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET: Final[str] = os.getenv("TWITCH_CLIENT_SECRET", "")

# ---- API server (Power BI / cloudflare tunnel auth) ------------------------
# Bất kỳ random string nào — dùng để bảo vệ API khi expose ra internet.
# Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
API_KEY: Final[str] = os.getenv("API_KEY", "")

# ---- Lark notify (custom bot webhook — không tốn API quota) ----------------
LARK_WEBHOOK_URL: Final[str] = os.getenv("LARK_WEBHOOK_URL", "")
LARK_WEBHOOK_SECRET: Final[str] = os.getenv("LARK_WEBHOOK_SECRET", "")

# ---- DNS fallback (DoH) — chống ISP DNS chặn/fail từng domain --------------
# Domain trong list này sẽ resolve qua Cloudflare DoH (1.1.1.1) khi system
# DNS fail. Bỏ trống để tắt. Nhiều domain cách nhau bằng dấu phẩy.
# (VNPT hay chặn cả cụm Steam: api + store + community)
DNS_FALLBACK_HOSTS: Final[list[str]] = [
    h.strip()
    for h in os.getenv(
        "DNS_FALLBACK_HOSTS",
        "api.steampowered.com,store.steampowered.com,steamcommunity.com",
    ).split(",")
    if h.strip()
]


def validate_credentials() -> list[str]:
    """Return list of missing required credentials (empty = OK)."""
    missing: list[str] = []
    if STEAM_ENABLED and not STEAM_API_KEY:
        missing.append("STEAM_API_KEY")
    if IGDB_ENABLED and not TWITCH_CLIENT_ID:
        missing.append("TWITCH_CLIENT_ID")
    if IGDB_ENABLED and not TWITCH_CLIENT_SECRET:
        missing.append("TWITCH_CLIENT_SECRET")
    return missing


# ---- Database --------------------------------------------------------------
SQLITE_PATH: Final[Path] = Path(os.getenv("SQLITE_PATH", "./data/sensortower.db"))
# Resolve to absolute path relative to project root if not already
if not SQLITE_PATH.is_absolute():
    SQLITE_PATH = PROJECT_ROOT / SQLITE_PATH


# ---- Pipeline behavior -----------------------------------------------------
LOG_LEVEL: Final[str] = os.getenv("LOG_LEVEL", "INFO").upper()
MAX_GAMES_PER_SOURCE: Final[int] = _get_int("MAX_GAMES_PER_SOURCE", 100)

# Toggle từng crawler — cho phép chạy riêng lẻ khi dev
STEAM_ENABLED: Final[bool] = _get_bool("STEAM_ENABLED", True)
ITUNES_ENABLED: Final[bool] = _get_bool("ITUNES_ENABLED", True)
IGDB_ENABLED: Final[bool] = _get_bool("IGDB_ENABLED", True)


def ensure_dirs() -> None:
    """Create data/raw/* and logs/ directories if missing."""
    for d in (
        DATA_DIR, RAW_DIR, PROCESSED_DIR, LOGS_DIR,
        RAW_STEAM_DIR, RAW_ITUNES_DIR, RAW_IGDB_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


# ---- Eagerly ensure dirs on import (safe, idempotent) ----------------------
ensure_dirs()
