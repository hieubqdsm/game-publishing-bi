"""
FastAPI server — serve SQLite data cho Power BI qua REST API.

Power BI connector: "Web" → URL endpoint → parse JSON (hoặc CSV qua ?format=csv).
Auth: header `X-API-Key: <key>` (key trong .env `API_KEY=`).
Auto-docs (Swagger UI): GET /docs

Endpoints:
  GET /api/health                       — DB status + row counts
  GET /api/tables                       — list available tables
  GET /api/tables/{name}?format=csv     — raw table (json | csv)
  GET /api/gacha/revenue?month=&game=   — pre-joined flat (game+month+rank+rev+scope)
  GET /api/news?hours=24&source=&category=   — news joined with source (filter theo loại tin)
  GET /api/stats/summary                — KPIs overview

Run: python scripts/serve_api.py
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader

from config import API_KEY, PROCESSED_DIR, SQLITE_PATH
from src.storage.db import get_connection


# ---- Security: API key header ---------------------------------------------
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """
    Verify X-API-Key header.

    Nếu API_KEY chưa set trong .env → cho phép localhost không cần key (dev mode).
    Nếu API_KEY đã set → bắt buộc header đúng.
    """
    if not API_KEY:
        # Dev mode: no key configured → allow all (localhost only recommended)
        return "dev-mode"
    if api_key is None or api_key != API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid X-API-Key header. "
                   "Set API_KEY in .env và gửi header 'X-API-Key: <key>'.",
        )
    return api_key


# ---- App ------------------------------------------------------------------
app = FastAPI(
    title="Game BI API",
    description="REST API serving SQLite data cho Power BI / BI tools. "
                "Auth: header `X-API-Key`. Docs: /docs",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


# ---- Helpers ---------------------------------------------------------------

def _df_to_csv_stream(df: pd.DataFrame) -> StreamingResponse:
    """Convert DataFrame → CSV StreamingResponse (Power BI loads như file)."""
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "inline; filename=data.csv"},
    )


def _respond(df: pd.DataFrame, fmt: str) -> Any:
    """Return JSON (records) hoặc CSV stream dựa trên ?format=."""
    if fmt.lower() == "csv":
        return _df_to_csv_stream(df)
    # JSON: list of records (Power BI "Web" connector parse được)
    # NaN/NaT không hợp lệ trong JSON — chuyển thành null
    df = df.astype(object).where(df.notna(), None)
    return JSONResponse(content=df.to_dict(orient="records"))


def _allowed_tables() -> list[str]:
    """Return list of user tables (exclude sqlite internal)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_wal' "
            "ORDER BY name"
        ).fetchall()
    return [r["name"] for r in rows]


# ---- Endpoints -------------------------------------------------------------

@app.get("/")
def root() -> dict:
    """API info — không cần auth."""
    return {
        "name": "Game BI API",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": [
            "/api/health", "/api/tables", "/api/tables/{name}",
            "/api/gacha/revenue", "/api/news", "/api/stats/summary",
            "/downloads (browse CSV files)", "/downloads/{filename} (download CSV)",
        ],
        "auth": "X-API-Key header required (see .env API_KEY)" if API_KEY else "dev mode (no key)",
    }


# ---- CSV Downloads (browser-friendly, cho Power BI / Excel) ----------------

# Các file CSV an toàn cho public download (chỉ flat views + dim_date)
DOWNLOADABLE_CSVS = {
    "gacha_flat.csv", "steam_flat.csv", "steam_hourly_flat.csv",
    "itunes_flat.csv", "news_flat.csv", "dim_date.csv",
    "ua_flat.csv", "kpis_flat.csv", "retention_flat.csv",
}


@app.get("/downloads", response_class=HTMLResponse)
def downloads_index() -> HTMLResponse:
    """Trang HTML list CSV files để download qua browser (không cần auth)."""
    rows_html = []
    for name in sorted(DOWNLOADABLE_CSVS):
        path = PROCESSED_DIR / name
        if path.exists():
            size_kb = path.stat().st_size / 1024
            rows_html.append(
                f'<tr><td><a href="/downloads/{name}">📊 {name}</a></td>'
                f'<td>{size_kb:.1f} KB</td>'
                f'<td><a href="/downloads/{name}" class="btn">⬇ Download</a></td></tr>'
            )
        else:
            rows_html.append(f'<tr><td>⏳ {name}</td><td>(chưa export)</td><td>—</td></tr>')
    return HTMLResponse(f"""
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Game BI — CSV Downloads</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; }}
  h1 {{ color: #333; }}
  table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
  th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
  th {{ background: #f5f5f5; }}
  a {{ color: #0066cc; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .btn {{ background: #0066cc; color: white; padding: 6px 16px; border-radius: 4px; }}
  .btn:hover {{ background: #004499; }}
  .note {{ background: #fff3cd; padding: 12px; border-radius: 4px; margin: 20px 0; font-size: 14px; }}
</style></head>
<body>
<h1>🎮 Game BI — CSV Downloads</h1>
<p>Tải CSV files để load vào Power BI / Excel.</p>
<div class="note">
  💡 <b>Power BI:</b> Get Data → CSV → paste URL hoặc tải file về máy trước.<br>
  💡 <b>API access</b> (real-time): xem <code>/docs</code> — cần header <code>X-API-Key</code>.
</div>
<table>
  <thead><tr><th>File</th><th>Size</th><th>Action</th></tr></thead>
  <tbody>
    {''.join(rows_html)}
  </tbody>
</table>
<hr>
<p><small>Refresh CSV: <code>python scripts/export_csv.py --flat</code> trên server.</small></p>
</body></html>
    """)


@app.get("/downloads/{filename}")
def download_csv(filename: str):
    """Download 1 CSV file (browser download, không cần auth)."""
    if filename not in DOWNLOADABLE_CSVS:
        raise HTTPException(404, f"File '{filename}' không hợp lệ. Xem /downloads.")
    path = PROCESSED_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"File '{filename}' chưa export. Chạy: python scripts/export_csv.py --flat")
    return FileResponse(
        path=str(path),
        media_type="text/csv",
        filename=filename,
    )


@app.get("/api/health", tags=["monitoring"])
def health(api_key: str = Depends(verify_api_key)) -> dict:
    """DB status + row counts per table — for monitoring/alerting."""
    tables = _allowed_tables()
    counts: dict[str, int] = {}
    with get_connection() as conn:
        for t in tables:
            counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        # DB file info
        db_size = SQLITE_PATH.stat().st_size if SQLITE_PATH.exists() else 0
    return {
        "status": "ok",
        "db_path": str(SQLITE_PATH),
        "db_size_bytes": db_size,
        "db_size_mb": round(db_size / 1e6, 2),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "tables": counts,
    }


@app.get("/api/tables", tags=["data"])
def list_tables(api_key: str = Depends(verify_api_key)) -> dict:
    """List all available tables to query."""
    return {"tables": _allowed_tables()}


@app.get("/api/tables/{table_name}", tags=["data"])
def get_table(
    table_name: str,
    format: str = Query("json", pattern="^(json|csv)$"),
    limit: int = Query(0, ge=0, description="0 = no limit"),
    api_key: str = Depends(verify_api_key),
) -> Any:
    """
    Return raw table as JSON records or CSV stream.

    Power BI: dùng `format=csv` để load như file, hoặc JSON để expand record.
    """
    allowed = _allowed_tables()
    if table_name not in allowed:
        raise HTTPException(
            status_code=404,
            detail=f"Table '{table_name}' not found. Available: {allowed}",
        )
    sql = f"SELECT * FROM {table_name}"
    if limit > 0:
        sql += f" LIMIT {limit}"
    with get_connection() as conn:
        df = pd.read_sql_query(sql, conn)
    return _respond(df, format)


@app.get("/api/gacha/revenue", tags=["gacha"])
def gacha_revenue(
    month: str | None = Query(None, description="Filter by snapshot_month 'YYYY-MM'"),
    game: str | None = Query(None, description="Filter by game name (case-insensitive)"),
    scope: str | None = Query(None, description="Filter by scope: combined|global|cn|jp"),
    format: str = Query("json", pattern="^(json|csv)$"),
    api_key: str = Depends(verify_api_key),
) -> Any:
    """
    Pre-joined flat table: game + monthly revenue (BI-ready, không cần build model).

    Columns: game_id, name, slug, icon_url, publisher, first_seen_month,
             snapshot_month, rank, revenue_usd, scope, source, fetched_at
    """
    sql = """
        SELECT g.game_id, g.name, g.slug, g.icon_url, g.publisher,
               g.first_seen_month,
               r.snapshot_month, r.rank, r.revenue_usd, r.scope, r.source,
               r.fetched_at
        FROM fact_gacha_revenue r
        JOIN dim_gacha_game g ON r.game_id = g.game_id
        WHERE 1=1
    """
    params: list[Any] = []
    if month:
        sql += " AND r.snapshot_month = ?"
        params.append(month)
    if game:
        sql += " AND LOWER(g.name) LIKE LOWER(?)"
        params.append(f"%{game}%")
    if scope:
        sql += " AND LOWER(r.scope) = LOWER(?)"
        params.append(scope)
    sql += " ORDER BY r.snapshot_month DESC, r.rank ASC"
    with get_connection() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    return _respond(df, format)


@app.get("/api/gacha/latest", tags=["gacha"])
def gacha_latest_month(
    top_n: int = Query(50, ge=1, le=500),
    format: str = Query("json", pattern="^(json|csv)$"),
    api_key: str = Depends(verify_api_key),
) -> Any:
    """Top N games by revenue cho tháng mới nhất — cho 'latest snapshot' view."""
    sql = """
        SELECT g.name, g.slug, g.icon_url, g.first_seen_month,
               r.snapshot_month, r.rank, r.revenue_usd, r.scope, r.source
        FROM fact_gacha_revenue r
        JOIN dim_gacha_game g ON r.game_id = g.game_id
        WHERE r.snapshot_month = (SELECT MAX(snapshot_month) FROM fact_gacha_revenue)
        ORDER BY r.rank ASC
        LIMIT ?
    """
    with get_connection() as conn:
        df = pd.read_sql_query(sql, conn, params=[top_n])
    return _respond(df, format)


@app.get("/api/news", tags=["news"])
def news(
    hours: int = Query(24, ge=1, le=24 * 90, description="Lookback hours"),
    source_type: str | None = Query(None, description="rss|ai_rss|hackernews|reddit|steam_news"),
    category: str | None = Query(None, description="ai|game|tech|steam_update"),
    format: str = Query("json", pattern="^(json|csv)$"),
    api_key: str = Depends(verify_api_key),
) -> Any:
    """News items joined với source — cho news dashboard."""
    sql = """
        SELECT n.news_id, n.title, n.url, n.summary, n.author,
               n.published_at, n.score, n.keywords, n.category, n.fetched_at,
               s.source_type, s.source_name
        FROM fact_news n
        JOIN dim_news_source s ON n.source_id = s.source_id
        WHERE n.published_at >= datetime('now', ?)
    """
    params: list[Any] = [f"-{hours} hours"]
    if source_type:
        sql += " AND s.source_type = ?"
        params.append(source_type)
    if category:
        sql += " AND n.category = ?"
        params.append(category)
    sql += " ORDER BY n.published_at DESC"
    with get_connection() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    return _respond(df, format)


@app.get("/api/stats/summary", tags=["stats"])
def stats_summary(api_key: str = Depends(verify_api_key)) -> dict:
    """KPIs tổng — total revenue, top games, month range, news count. Cho overview cards."""
    with get_connection() as conn:
        gacha = conn.execute(
            """
            SELECT COUNT(DISTINCT snapshot_month) as months,
                   COUNT(DISTINCT game_id) as games,
                   COUNT(*) as facts,
                   COALESCE(ROUND(SUM(revenue_usd)/1e6, 1), 0) as total_revenue_musd,
                   MIN(snapshot_month) as first_month,
                   MAX(snapshot_month) as last_month
            FROM fact_gacha_revenue
            """
        ).fetchone()
        # Top 5 games by latest month revenue
        top5 = conn.execute(
            """
            SELECT g.name, r.revenue_usd, r.scope, r.snapshot_month
            FROM fact_gacha_revenue r
            JOIN dim_gacha_game g ON r.game_id = g.game_id
            WHERE r.snapshot_month = (SELECT MAX(snapshot_month) FROM fact_gacha_revenue)
            ORDER BY r.rank LIMIT 5
            """
        ).fetchall()
        news_count = conn.execute(
            "SELECT COUNT(*) FROM fact_news WHERE published_at >= datetime('now','-24 hours')"
        ).fetchone()[0]
    return {
        "gacha": {
            "months_tracked": gacha["months"],
            "games_tracked": gacha["games"],
            "total_facts": gacha["facts"],
            "total_revenue_musd": gacha["total_revenue_musd"],
            "range": {"first": gacha["first_month"], "last": gacha["last_month"]},
            "top5_latest": [
                {"name": r["name"], "revenue_usd": r["revenue_usd"],
                 "scope": r["scope"], "month": r["snapshot_month"]}
                for r in top5
            ],
        },
        "news_last_24h": news_count,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
