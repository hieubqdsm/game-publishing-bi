"""
Pipeline scheduler — chạy 1 lệnh, tự gọi crawler theo giờ.

KHÔNG cần cron/crontab/launchd. Chỉ cần để script này chạy trong 1 terminal.
Tự sleep + wake theo lịch.

Usage:
    python scripts/serve_pipeline.py              # chạy lịch mặc định
    python scripts/serve_pipeline.py --once       # chạy 1 lần rồi thoát (test)
    python scripts/serve_pipeline.py --dry-run    # xem lịch, không chạy

Schedule mặc định:
    Mỗi giờ :00  → Steam crawl (top 100 CCU)
    Mỗi giờ :30  → iTunes crawl (US + VN games)
    Mỗi 4h (:00) → News crawl (RSS gaming + AI + Hacker News)

Logs: logs/pipeline-scheduler.log
"""
from __future__ import annotations

import sys
import time as time_mod
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import click
from loguru import logger

from config import LOG_LEVEL, ensure_dirs, LOGS_DIR

logger.remove()
logger.add(sys.stderr, level=LOG_LEVEL)
logger.add(
    LOGS_DIR / "pipeline-scheduler.log",
    level=LOG_LEVEL,
    rotation="10 MB",
    retention="30 days",
)

# Project root (resolve từ file này: scripts/serve_pipeline.py → parent.parent)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Python binary: ưu tiên venv, fallback sys.executable (python đang chạy scheduler)
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
PYTHON_BIN = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def _run(cmd_desc: str, cmd: list[str], timeout: int = 600) -> bool:
    """Run 1 command, log result. Trả True nếu exit 0."""
    import subprocess
    now = datetime.now().strftime("%H:%M:%S")
    logger.info(f"[{now}] ▶ {cmd_desc}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        # tail output để log ngắn
        out = (result.stdout or "").strip().splitlines()[-5:]
        for line in out:
            logger.info(f"  {line}")
        if result.returncode != 0:
            err = (result.stderr or "").strip().splitlines()[-3:]
            for line in err:
                logger.warning(f"  ⚠ {line}")
        logger.success(f"✓ {cmd_desc} done (exit {result.returncode})")
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        logger.error(f"✗ {cmd_desc} TIMEOUT (>{timeout // 60}min)")
        return False
    except Exception as e:
        logger.error(f"✗ {cmd_desc} failed: {e}")
        return False


def _should_run_steam(now: datetime) -> bool:
    """Mỗi giờ :00."""
    return now.minute == 0


def _should_run_itunes(now: datetime) -> bool:
    """Mỗi giờ :30."""
    return now.minute == 30


def _should_run_news(now: datetime) -> bool:
    """Mỗi 4h: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00."""
    return now.hour % 4 == 0 and now.minute == 0


@click.command()
@click.option("--once", is_flag=True, help="Chạy 1 lần rồi thoát (test)")
@click.option("--dry-run", is_flag=True, help="Xem lịch, không chạy")
def main(once: bool, dry_run: bool):
    """Scheduler — tự gọi crawler theo giờ."""
    ensure_dirs()

    logger.info("=" * 60)
    logger.info("🎮 Game BI Pipeline Scheduler")
    logger.info("=" * 60)
    logger.info("Schedule:")
    logger.info("  ⏰ Mỗi giờ :00 → Steam crawl (top 100 CCU)")
    logger.info("  ⏰ Mỗi giờ :30 → iTunes crawl (US + VN games)")
    logger.info("  ⏰ Mỗi 4h (:00) → News crawl (RSS + AI + Hacker News)")
    logger.info("")

    if dry_run:
        logger.info("DRY RUN — không chạy, chỉ show lịch")
        return

    # Map task → đã chạy lần cuối (tránh chạy 2 lần trong cùng phút)
    last_run: dict[str, str] = {}

    while True:
        now = datetime.now()
        slot = now.strftime("%Y-%m-%d %H:%M")

        # Steam
        if _should_run_steam(now) and last_run.get("steam") != slot:
            last_run["steam"] = slot
            if not dry_run:
                _run("Steam crawl", [
                    PYTHON_BIN,
                    "scripts/run_daily.py", "--source", "steam",
                ])

        # iTunes
        if _should_run_itunes(now) and last_run.get("itunes") != slot:
            last_run["itunes"] = slot
            if not dry_run:
                _run("iTunes crawl", [
                    PYTHON_BIN,
                    "scripts/run_daily.py", "--source", "itunes",
                ])

        # News (mỗi 4h)
        if _should_run_news(now) and last_run.get("news") != slot:
            last_run["news"] = slot
            if not dry_run:
                # Thời điểm bắt đầu (UTC, format DB) — để lọc tin MỚI cho digest Lark
                from datetime import datetime as _dt, timezone as _tz
                news_started_at = _dt.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
                ok = _run("News crawl", [
                    PYTHON_BIN,
                    "scripts/run_news.py",
                    "--hours", "6",  # crawl 6h gần nhất (chống miss tin giữa 2 lần chạy)
                ], timeout=900)  # news gồm Steam News per-game, mạng chậm cần dư thời gian
                if not ok:
                    # Crawl bị kill/timeout → run_news chưa kịp gửi digest.
                    # Scheduler tự gửi digest một phần (nếu có tin mới) để không bỏ lỡ.
                    try:
                        from src.notify.digest import send_news_digest
                        logger.info("News crawl không xong — gửi digest một phần qua Lark")
                        send_news_digest(news_started_at)
                    except Exception as e:
                        logger.warning(f"Gửi digest bù lỗi: {e}")

        if once:
            logger.info("--once mode → exiting")
            break

        # Sleep 30s rồi check lại (đảm bảo không miss :00/:30)
        time_mod.sleep(30)


if __name__ == "__main__":
    main()
