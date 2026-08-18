"""
ActivePlayerCrawler — estimated MAU/DAU/CCU mobile games by platform.

Source: https://activeplayer.io/<game-slug>/ — HTML pages per-game (no JSON API).
Coverage: ~123 mobile games, 12-month rolling history, split Google Play / App Store.

ToS compliance: Tier 1 ✅ (see docs/tos_compliance.md).
- robots.txt: only /wp-admin/ disallowed → game pages Allow for all bots.
- ToS: no anti-scraping clause. No login. No paywall.
- Method: rate-limited HTTP fetch (6s + 0-3s jitter between requests).
- Data is ESTIMATE (modeled from download trends) — labeled in dashboard.

Stores into:
- dim_game (source='activeplayer', platform='mobile')
- fact_engagement_metrics (source='activeplayer', metric_name='mau_<platform>' etc.)
"""
from __future__ import annotations

import random
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import PROJECT_ROOT
from src.crawlers.base import BaseCrawler, RateLimitError
from src.storage.db import get_connection


# ---- Constants -------------------------------------------------------------

MONTH_TO_NUM = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Slugs to skip when scraping the mobile category directory — these are non-game
# pages (status checkers, calculators, listicles, legal pages).
SLUG_SKIP_KEYWORDS = {
    "status-checker", "down-right", "top-100", "top-20", "how-we-collect",
    "terms", "privacy", "disclaimer", "policy", "contact", "about",
    "sample", "home-", "request-a-game", "twitch-statistics", "twitch",
    "terminal-commands", "roblox-id", "ranking", "stats-tracker",
    "calculator", "v-bucks", "robux", "riot-points", "all-game-counters",
    "live-player-count", "category", "tag", "author", "feed",
}

# Hints to classify a page as mobile vs PC vs console. Mobile-category crawl only
# returns mobile games, but a few slugs are dual-platform (e.g. fortnite, roblox).
# We keep them — the parser records `platform` per historical table anyway.
PC_SLUG_HINTS = {
    "counter-strike", "dota-2", "team-fortress", "deadlock", "marvel-rivals",
    "xdefiant", "valorant", "overwatch", "apex-legends", "escape-from-tarkov",
    "elden-ring", "path-of-exile", "diablo", "world-of-warcraft", "final-fantasy-xiv",
    "starcraft", "halo-infinite", "sea-of-thieves", "rocket-league", "rainbow-six",
    "destiny-2", "the-division", "black-myth-wukong", "dragons-dogma", "starfield",
    "warfame", "cs-go", "cs2",
}


# ---- Parsing helpers (pure functions) --------------------------------------

def parse_count(text: str) -> Optional[float]:
    """
    Parse a player-count string into millions (float).

    '73.2M'        -> 73.2
    '900K'         -> 0.9
    '1.2B'         -> 1200.0
    '15.0M – 25.0M' -> 20.0  (midpoint of range)
    '6,387,911'    -> 6.387911  (raw count → divide by 1e6)
    'N/A'          -> None
    """
    if not text:
        return None
    s = text.strip().replace(",", "").replace("+", "").replace(" ", "")
    if not s or s.lower() in ("n/a", "na", "-"):
        return None

    # Handle ranges: "3.1M-7.6M" or "3.1M–7.6M" or "3.1M—7.6M"
    range_match = re.match(r"^([0-9.]+[KMB]?)[-\u2014\u2013]([0-9.]+[KMB])$", s)
    if range_match:
        low = _apply_suffix(range_match.group(1))
        high = _apply_suffix(range_match.group(2))
        if low is not None and high is not None:
            return (low + high) / 2.0

    return _apply_suffix(s)


def _apply_suffix(s: str) -> Optional[float]:
    """
    Convert to a float in MILLIONS of users.

    '73.2M'  -> 73.2       (M suffix = already in millions)
    '900K'   -> 0.9        (K = thousands → /1000 to get millions)
    '1.2B'   -> 1200.0     (B = billions → *1000)
    '6387911' -> 6.387911  (raw count, no suffix → /1e6)
    """
    mult: float
    body = s
    if s.endswith("K"):
        mult, body = 1e-3, s[:-1]      # thousands → millions
    elif s.endswith("M"):
        mult, body = 1.0, s[:-1]       # already millions
    elif s.endswith("B"):
        mult, body = 1e3, s[:-1]       # billions → millions
    else:
        mult = 1e-6                    # raw count → millions
    try:
        return float(body) * mult
    except ValueError:
        return None


def parse_month(text: str) -> Optional[str]:
    """
    Parse 'July 2026' / 'Jul 2026' / '2026-07' → 'YYYY-MM'.
    Returns None if not a recognizable month.
    """
    if not text:
        return None
    s = text.strip()

    # ISO: 2026-07 or 2026/07
    m = re.match(r"^(\d{4})[-/](\d{1,2})$", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}"

    # 'July 2026' / 'Jul 2026'
    m = re.match(r"^([A-Za-z]+)\s+(\d{4})$", s)
    if m:
        name, year = m.group(1).lower(), int(m.group(2))
        if name in MONTH_TO_NUM:
            return f"{year:04d}-{MONTH_TO_NUM[name]:02d}"

    return None


def humanize_name(slug: str) -> str:
    """'pubg-mobile' → 'PUBG Mobile'. Keeps acronyms together."""
    # Known acronyms to uppercase
    acronyms = {"pubg", "mmorpg", "rpg", "moba", "fps", "pvp", "pve", "ios", "apk",
                "usa", "uk", "vr", "id"}
    words = []
    for w in slug.split("-"):
        w = w.strip()
        if not w:
            continue
        if w.lower() in acronyms:
            words.append(w.upper())
        else:
            words.append(w[0].upper() + w[1:])
    return " ".join(words)


# ---- Crawler ---------------------------------------------------------------

class ActivePlayerCrawler(BaseCrawler):
    """
    Crawl estimated mobile player counts from ActivePlayer.io.

    Each game has its own page at /<slug>/ with 12-month MAU history, split by
    Google Play (Android) and App Store (iOS). We fetch HTML, parse the historical
    tables with BeautifulSoup, and UPSERT into fact_engagement_metrics.
    """

    def __init__(self) -> None:
        super().__init__("activeplayer")
        self._jitter_ms: int = int(self.cfg.get("jitter_ms", 0))

    # ---- HTTP (HTML) ------------------------------------------------------
    @retry(
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout, RateLimitError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def get_html(self, url: str) -> str:
        """GET request → raw HTML. Same retry/rate-limit policy as get_json."""
        self._respect_rate_limit()
        # Extra jitter (politeness): sleep 0-jitter_ms after the base rate limit
        if self._jitter_ms > 0:
            time.sleep(random.uniform(0, self._jitter_ms) / 1000.0)
        try:
            resp = self.session.get(url, timeout=30)
        except (requests.ConnectionError, requests.Timeout):
            logger.warning(f"[activeplayer] network error → {url}")
            raise
        if resp.status_code == 429:
            logger.warning(f"[activeplayer] 429 rate-limited → {url}")
            raise RateLimitError(url)
        resp.raise_for_status()
        return resp.text

    # ---- Raw HTML persistence --------------------------------------------
    def save_raw_html(self, slug: str, html: str, snapshot_date: Optional[str] = None) -> Path:
        """Save raw HTML for audit. data/raw/activeplayer/<YYYY-MM-DD>/<slug>.html"""
        d = snapshot_date or date.today().isoformat()
        out_dir = self.raw_dir / d
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{slug}.html"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        return out_path

    # ---- Discovery: list mobile game slugs -------------------------------
    def fetch_mobile_game_slugs(self) -> list[str]:
        """
        Discover mobile game slugs by paginating /category/mobile/page/N/.
        Returns deduplicated list of slugs. Falls back to a hardcoded list on error.
        """
        all_slugs: set[str] = set()
        for page in range(1, 20):  # safety cap
            url = (
                "https://activeplayer.io/category/mobile/"
                if page == 1
                else f"https://activeplayer.io/category/mobile/page/{page}/"
            )
            try:
                html = self.get_html(url)
            except Exception as e:
                logger.warning(f"[activeplayer] directory page {page} failed: {e}")
                break
            slugs = self._extract_game_slugs(html)
            if not slugs:
                break
            before = len(all_slugs)
            all_slugs.update(slugs)
            added = len(all_slugs) - before
            logger.info(f"[activeplayer] directory page {page}: +{added} slugs (total {len(all_slugs)})")
            # Stop when a page returns mostly duplicates (end of pagination)
            if added == 0 and page > 1:
                break

        if not all_slugs:
            logger.error("[activeplayer] directory crawl empty — using fallback list")
            return FALLBACK_MOBILE_SLUGS

        return sorted(all_slugs)

    @staticmethod
    def _extract_game_slugs(html: str) -> list[str]:
        """Extract candidate game slugs from a directory/category page."""
        soup = BeautifulSoup(html, "lxml")
        slugs: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            # Match https://activeplayer.io/<slug>/  (exactly one path segment)
            m = re.match(r"^https?://activeplayer\.io/([a-z0-9][a-z0-9-]+)/?$", href)
            if not m:
                continue
            slug = m.group(1)
            if any(kw in slug for kw in SLUG_SKIP_KEYWORDS):
                continue
            if len(slug) < 3:
                continue
            slugs.add(slug)
        return sorted(slugs)

    # ---- HTML parsing ----------------------------------------------------
    def parse_page(self, html: str, slug: str) -> dict[str, Any]:
        """
        Parse one ActivePlayer.io game page.

        Returns:
            {
              "name": str,
              "platforms": {platform_key: [{"month": "YYYY-MM", "mau_m": float, "change_m": float|None, "pct": str|None}]},
              "current": {platform_key: {"ccu_m": float|None, "dau_m": float|None, "mau_m": float|None}},
              "downloads": {platform_key: str},
              "rating": {platform_key: float|None},
            }
        """
        soup = BeautifulSoup(html, "lxml")

        # Game name: prefer <h1> (page title), fallback to humanized slug
        h1 = soup.find("h1")
        name = h1.get_text(strip=True) if h1 else humanize_name(slug)
        # Strip trailing emojis / "Live Player Count" suffixes
        name = re.sub(r"\s*(Live Player Count[^|]*)?(Game Statistics[^|]*)?$", "", name, flags=re.I).strip()
        name = name.split("\n")[0].strip() if name else humanize_name(slug)

        # Find all tables
        tables = soup.find_all("table")
        if not tables:
            return {"name": name, "platforms": {}, "current": {},
                    "downloads": {}, "rating": {}}

        # Identify the "store meta" table (has 'Store data' or 'Rating' header)
        store_meta = {}
        history_tables: list[BeautifulSoup] = []
        for t in tables:
            rows = t.find_all("tr")
            if not rows:
                continue
            header_cells = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
            if any("store data" in h or "rating" in h for h in header_cells):
                store_meta = self._parse_store_meta(rows)
            elif self._is_mau_table(header_cells):
                history_tables.append(t)

        # Map each history table to a platform. ActivePlayer.io layouts put a
        # platform-specific H4 ("Google Play Player Statistics" / "App Store
        # Player Statistics") in document order BEFORE the history tables.
        # We traverse the document once, tracking the most recent platform heading
        # seen, and assign it to each history table we encounter.
        platforms: dict[str, list[dict]] = {}
        platform_by_table = self._map_tables_to_platforms(soup, history_tables)
        for t, platform in zip(history_tables, platform_by_table):
            rows_data = self._parse_history_rows(t)
            if rows_data:
                # If same platform appears twice (rare), keep the longer series
                if platform in platforms and len(platforms[platform]) >= len(rows_data):
                    continue
                platforms[platform] = rows_data

        # Extract current snapshot (CCU/DAU/MAU) — usually paragraphs near the top.
        current = self._parse_current_snapshot(soup, list(platforms.keys()))

        return {
            "name": name,
            "platforms": platforms,
            "current": current,
            "downloads": store_meta.get("downloads", {}),
            "rating": store_meta.get("rating", {}),
        }

    @staticmethod
    def _is_mau_table(header_cells: list[str]) -> bool:
        """
        Identify a Monthly Active Users (MAU) history table.

        ACCEPTED (MAU tables — these match ActivePlayer.io's mobile layout):
          - ['month', 'estimated players', ...]      (canonical mobile layout)
          - ['month', 'average monthly players', ...] (variant with DAU column)

        REJECTED (non-MAU — Steam-style or unrelated):
          - ['month', 'daily average', 'peak players', ...]  (Steam CCU, not mobile MAU)
          - ['period', 'change', 'percentage', ...]          (MoM growth table)
          - ['platform:', 'availability:', ...]              (platform meta)
        """
        joined = " | ".join(header_cells).lower()
        if "month" not in joined:
            return False
        # Accept mobile MAU tables
        if "estimated players" in joined or "average monthly players" in joined:
            return True
        # Reject Steam-style CCU tables explicitly
        if "daily average" in joined or "peak players" in joined:
            return False
        # Reject growth tables
        if "percentage" in joined and "change" in joined and "month" not in header_cells[0].lower():
            return False
        return False

    @staticmethod
    def _parse_store_meta(rows) -> dict[str, dict]:
        """Parse the 'Store data' table → {downloads: {gp, ios}, rating: {gp, ios}}."""
        out: dict[str, dict] = {"downloads": {}, "rating": {}}
        for r in rows:
            cells = [c.get_text(strip=True) for c in r.find_all(["th", "td"])]
            if len(cells) < 3:
                continue
            label = cells[0].lower()
            gp, ios = cells[1], cells[2]
            if "download" in label:
                out["downloads"] = {"android": gp, "ios": ios}
            elif "rating" in label:
                out["rating"] = {
                    "android": _extract_rating(gp),
                    "ios": _extract_rating(ios),
                }
        return out

    @staticmethod
    def _map_tables_to_platforms(soup, history_tables: list) -> list[str]:
        """
        Match each history table to a platform.

        ActivePlayer.io pages list platform headings ("Google Play Player
        Statistics", "App Store Player Statistics") in document order BEFORE
        the history tables — so the i-th platform heading corresponds to the
        i-th history table. We collect platform headings in order, then zip.

        Falls back to positional ['android','ios','combined'] when no platform
        heading is found.
        """
        if not history_tables:
            return []

        # Collect all platform-bearing headings, in document order.
        platform_headings: list[str] = []
        for el in soup.find_all(["h2", "h3", "h4", "h5"]):
            text = el.get_text(" ", strip=True).lower()
            if "google play" in text or "android" in text:
                platform_headings.append("android")
            elif "app store" in text or "apple" in text or text.endswith(" ios"):
                platform_headings.append("ios")
            elif "combined" in text or "total player" in text:
                platform_headings.append("combined")

        # Dedupe consecutive duplicates (a heading may appear in TOC + body)
        deduped: list[str] = []
        for p in platform_headings:
            if not deduped or deduped[-1] != p:
                deduped.append(p)

        if len(deduped) >= len(history_tables):
            return deduped[:len(history_tables)]

        # Fallback: positional assignment
        positional = ["android", "ios", "combined"]
        return [positional[i] if i < len(positional) else f"unknown_{i}"
                for i in range(len(history_tables))]

    @staticmethod
    def _parse_history_rows(table) -> list[dict]:
        """Parse a historical MAU table → [{month, mau_m, change_m, pct}]."""
        rows = table.find_all("tr")
        if len(rows) < 2:
            return []
        out: list[dict] = []
        for r in rows[1:]:  # skip header
            cells = [c.get_text(strip=True) for c in r.find_all(["td", "th"])]
            if len(cells) < 2 or not cells[0]:
                continue
            month = parse_month(cells[0])
            if not month:
                continue
            mau = parse_count(cells[1])
            if mau is None:
                continue
            change = parse_count(cells[2]) if len(cells) > 2 else None
            pct = cells[3].replace("%", "").strip() if len(cells) > 3 else None
            out.append({"month": month, "mau_m": mau, "change_m": change, "pct": pct})
        return out

    @staticmethod
    def _parse_current_snapshot(soup, platform_keys: list[str]) -> dict[str, dict]:
        """
        Extract current CCU / DAU / MAU ranges from the page body.

        ActivePlayer.io pages render a per-platform block after each H4 heading
        like "Google Play Player Statistics" / "App Store Player Statistics".
        Each block is a <div> containing label-value pairs:
            Current Players  3.1M - 7.6M      (CCU range)
            Daily Active Users  15.0M - 25.0M (DAU range)
            Monthly Active Users  90.0M - 150.0M
            Downloads  1,000,000,000+

        Returns {platform: {"ccu_m": float|None, "dau_m": float|None,
                            "mau_m": float|None, "downloads": str|None}}.
        Values are midpoints of ranges, in millions.
        """
        out: dict[str, dict] = {}
        # Map H4 platform heading → current platform bucket
        for h4 in soup.find_all(["h3", "h4"]):
            heading_text = h4.get_text(" ", strip=True).lower()
            if "google play" in heading_text or "android" in heading_text:
                platform = "android"
            elif "app store" in heading_text or "apple" in heading_text or heading_text.endswith(" ios"):
                platform = "ios"
            elif "combined" in heading_text or "total player" in heading_text:
                platform = "combined"
            else:
                continue
            # Find the next sibling <div> containing the label/value pairs
            sib = h4.next_sibling
            container = None
            while sib is not None:
                if hasattr(sib, "name") and sib.name == "div" and sib.get_text(strip=True):
                    container = sib
                    break
                sib = sib.next_sibling
            if container is None:
                continue
            block_text = container.get_text(" ", strip=True)
            metrics = ActivePlayerCrawler._extract_snapshot_metrics(block_text)
            if metrics:
                out[platform] = metrics
        return out

    @staticmethod
    def _extract_snapshot_metrics(text: str) -> dict[str, Any]:
        """
        From a text block like
          'Current Players 3.1M - 7.6M Daily Active Users 15.0M - 25.0M
           Monthly Active Users 90.0M - 150.0M Downloads 1,000,000,000+'
        extract ccu_m / dau_m / mau_m / downloads.
        """
        out: dict[str, Any] = {
            "ccu_m": None, "dau_m": None, "mau_m": None, "downloads": None,
        }
        # Each label is followed by a value (range or single number) up to the
        # next label. Capture greedy-but-bounded.
        patterns = {
            "ccu_m": r"current players\s+([0-9.,]+\s*[KMB]?\s*[-\u2013\u2014]?\s*[0-9.,]*\s*[KMB]?)",
            "dau_m": r"daily active users\s+([0-9.,]+\s*[KMB]?\s*[-\u2013\u2014]?\s*[0-9.,]*\s*[KMB]?)",
            "mau_m": r"monthly active users\s+([0-9.,]+\s*[KMB]?\s*[-\u2013\u2014]?\s*[0-9.,]*\s*[KMB]?)",
            "downloads": r"downloads\s+([0-9.,]+\+?[KMB]?)",
        }
        for key, pat in patterns.items():
            m = re.search(pat, text, re.I)
            if not m:
                continue
            raw = m.group(1).strip()
            if key == "downloads":
                out[key] = raw
            else:
                out[key] = parse_count(raw)
        return out

    # ---- DB upserts ------------------------------------------------------
    def upsert_game_from_page(self, name: str, slug: str, raw_path: str,
                              downloads: dict | None = None,
                              rating: dict | None = None) -> int:
        """UPSERT into dim_game with source='activeplayer'. Returns game_id."""
        description_parts = []
        if downloads:
            description_parts.append(f"Downloads: GP={downloads.get('android','-')} iOS={downloads.get('ios','-')}")
        if rating:
            description_parts.append(f"Rating: GP={rating.get('android','-')} iOS={rating.get('ios','-')}")
        description = " | ".join(description_parts)[:500] if description_parts else None

        return self.upsert_game(
            source_app_id=slug,
            name=name,
            genre=None,        # left NULL; enrichable later via name-match to iTunes
            platform="mobile",
            publisher_name=None,
            description=description,
            raw_payload_path=raw_path,
        )

    def upsert_metric(self, game_id: int, snapshot_month: str,
                      metric_name: str, value: float | None) -> bool:
        """
        UPSERT one metric into fact_engagement_metrics.
        snapshot_month: 'YYYY-MM' → stored as 'YYYY-MM-01' (snapshot_date is a date).
        metric_name: 'mau_android' | 'mau_ios' | 'dau_android' | ...
        Returns True if a new row was inserted (vs updated).
        """
        if value is None:
            return False
        snapshot_date = f"{snapshot_month}-01"
        with get_connection() as conn:
            existing = conn.execute(
                """SELECT metric_id FROM fact_engagement_metrics
                   WHERE game_id=? AND snapshot_date=? AND source='activeplayer' AND metric_name=?""",
                (game_id, snapshot_date, metric_name),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO fact_engagement_metrics
                    (game_id, snapshot_date, source, metric_name, metric_value)
                VALUES (?, ?, 'activeplayer', ?, ?)
                ON CONFLICT(game_id, snapshot_date, source, metric_name) DO UPDATE SET
                    metric_value=excluded.metric_value,
                    fetched_at=datetime('now')
                """,
                (game_id, snapshot_date, metric_name, value),
            )
            return existing is None

    # ---- Orchestration ---------------------------------------------------
    def run(self, max_items: Optional[int] = None,
            slugs: Optional[list[str]] = None,
            skip_fetch: bool = False) -> dict[str, int]:
        """
        Discover mobile games → fetch each page → parse → UPSERT.

        Args:
            max_items: limit number of games (for testing).
            slugs: explicit slug list (skip discovery).
            skip_fetch: if True, re-parse HTML files already in data/raw/activeplayer/.
        """
        today = date.today().isoformat()
        stats = {
            "games_fetched": 0, "games_ok": 0, "games_failed": 0,
            "games_skipped_no_history": 0,
            "metrics_inserted": 0, "metrics_updated": 0,
        }

        if slugs is None and not skip_fetch:
            logger.info("[activeplayer] discovering mobile game slugs…")
            slugs = self.fetch_mobile_game_slugs()
            logger.info(f"[activeplayer] {len(slugs)} slugs discovered")
        elif slugs is None and skip_fetch:
            # Re-parse existing HTML files
            raw_today = self.raw_dir / today
            if not raw_today.exists():
                # Find most recent date dir
                date_dirs = sorted([d for d in self.raw_dir.iterdir() if d.is_dir()])
                if not date_dirs:
                    logger.error(f"[activeplayer] no HTML in {self.raw_dir}")
                    return stats
                raw_today = date_dirs[-1]
                logger.info(f"[activeplayer] re-parsing HTML from {raw_today}")
            slugs = [p.stem for p in raw_today.glob("*.html")]
            logger.info(f"[activeplayer] {len(slugs)} HTML files to re-parse")

        if max_items:
            slugs = slugs[:max_items]
            logger.info(f"[activeplayer] capped to {len(slugs)} slugs (--max)")

        for i, slug in enumerate(slugs, 1):
            stats["games_fetched"] += 1
            logger.info(f"[activeplayer] ({i}/{len(slugs)}) {slug}")
            try:
                if skip_fetch:
                    # Try to read from existing raw
                    raw_path_obj = self._find_raw_html(slug)
                    if raw_path_obj is None:
                        logger.warning(f"[activeplayer] {slug}: no raw HTML, skip")
                        stats["games_failed"] += 1
                        continue
                    html = raw_path_obj.read_text(encoding="utf-8")
                    raw_path = str(raw_path_obj.relative_to(PROJECT_ROOT))
                else:
                    url = f"https://activeplayer.io/{slug}/"
                    html = self.get_html(url)
                    raw_path_obj = self.save_raw_html(slug, html, today)
                    raw_path = str(raw_path_obj.relative_to(PROJECT_ROOT))

                data = self.parse_page(html, slug)
                has_history = bool(data["platforms"])
                has_snapshot = bool(data.get("current"))
                if not has_history and not has_snapshot:
                    logger.warning(f"[activeplayer] {slug}: no history table & no snapshot, skip")
                    stats["games_skipped_no_history"] += 1
                    continue

                game_id = self.upsert_game_from_page(
                    data["name"], slug, raw_path,
                    downloads=data.get("downloads"), rating=data.get("rating"),
                )

                # Insert each (platform × month) MAU row from historical tables.
                for platform, rows in data["platforms"].items():
                    for row in rows:
                        inserted = self.upsert_metric(
                            game_id, row["month"], f"mau_{platform}", row["mau_m"]
                        )
                        if inserted:
                            stats["metrics_inserted"] += 1
                        else:
                            stats["metrics_updated"] += 1

                # Insert current-snapshot DAU/CCU for the latest month. Snapshot
                # is a rolling "last 30 days" range — we only use it for CCU and
                # DAU (which have no 12-month history). MAU history comes from the
                # historical table above, which is more precise than the snapshot.
                today_iso = date.today().isoformat()
                current_month = today_iso[:7]
                for platform, snap in data.get("current", {}).items():
                    for metric_key, metric_name in (
                        ("ccu_m", f"ccu_{platform}"),
                        ("dau_m", f"dau_{platform}"),
                    ):
                        value = snap.get(metric_key)
                        if value is None:
                            continue
                        inserted = self.upsert_metric(
                            game_id, current_month, metric_name, value
                        )
                        if inserted:
                            stats["metrics_inserted"] += 1
                        else:
                            stats["metrics_updated"] += 1

                n_hist = sum(len(v) for v in data["platforms"].values())
                n_snap = sum(1 for s in data.get("current", {}).values()
                             for k in ("ccu_m", "dau_m") if s.get(k) is not None)
                logger.info(
                    f"[activeplayer] {slug} → '{data['name']}' "
                    f"history={n_hist} datapoints, snapshot={n_snap} metrics"
                )
                stats["games_ok"] += 1

            except Exception as e:
                logger.warning(f"[activeplayer] {slug} FAILED: {e}")
                stats["games_failed"] += 1
                continue

        logger.info(f"[activeplayer] DONE — {stats}")
        return stats

    def _find_raw_html(self, slug: str) -> Optional[Path]:
        """Find the most recent raw HTML for a slug (for --no-fetch mode)."""
        if not self.raw_dir.exists():
            return None
        date_dirs = sorted([d for d in self.raw_dir.iterdir() if d.is_dir()], reverse=True)
        for d in date_dirs:
            p = d / f"{slug}.html"
            if p.exists():
                return p
        return None


def _extract_rating(text: str) -> Optional[float]:
    """'4.3 / 5' → 4.3. Returns None if not parseable."""
    if not text:
        return None
    m = re.match(r"\s*([0-9.]+)", text)
    return float(m.group(1)) if m else None


# ---- Fallback slug list (used only if directory crawl fails) ---------------
# Curated mobile games known to have history tables on ActivePlayer.io.
FALLBACK_MOBILE_SLUGS = [
    "pubg-mobile", "garena-free-fire", "arena-of-valor", "subway-surfers",
    "block-blast", "clash-royale", "brawl-stars", "mobile-legends-bang-bang",
    "roblox", "free-fire-max", "honor-of-kings", "whiteout-survival",
    "raid-shadow-legends", "summoners-war", "monopoly-go", "clash-of-clans",
    "genshin-impact", "last-war-survival", "ragnarok-origin-global", "afk-arena",
    "afk-journey", "archero-2", "boom-beach", "8-ball-pool", "aquapark-io",
    "hole-io", "paper-io-2", "mob-control", "draw-joust", "love-and-deepspace",
    "solo-leveling-arise", "wuthering-waves", "night-crows", "ea-sports-fc-soccer-mobile-26",
    "efootball", "king-of-avalon", "rise-of-kingdoms", "mobile-strike",
]
