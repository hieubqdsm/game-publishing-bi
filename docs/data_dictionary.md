# 📚 Data Dictionary

> Mô tả chi tiết schema SQLite — dùng cho Power BI modeling và portfolio documentation.

## Tổng quan kiến trúc

Pipeline sử dụng **Star Schema** — chuẩn cho OLAP / BI workloads:

```
                    ┌─────────────────┐
                    │    dim_date     │
                    └────────┬────────┘
                             │
┌──────────────┐    ┌────────┴────────┐    ┌─────────────────────────┐
│ dim_publisher│◄───┤    dim_game     ├───►│ fact_steam_playercounts  │
└──────────────┘    └────────┬────────┘    │ fact_itunes_rankings     │
                             │             │ fact_engagement_metrics  │
                             └─────────────┴─────────────────────────┘
```

**Vì sao star schema?** Power BI/DAX tối ưu cho join 1-nhiều giữa fact (sự kiện) và dimensions (định danh). Query nhanh, model dễ hiểu.

---

## DIMENSIONS

### `dim_game` — Master game catalog

Mỗi game/app là 1 row. Cùng game có thể xuất hiện ở nhiều source (Steam + iOS).

| Column | Type | Description | Example |
|--------|------|-------------|---------|
| `game_id` | INTEGER PK | Surrogate key (auto) | `42` |
| `source_app_id` | TEXT | ID gốc từ source | `"730"` (CS2 Steam), `"1592786499"` (iOS) |
| `source` | TEXT | Nguồn crawler | `'steam'`, `'itunes'`, `'igdb'` |
| `name` | TEXT | Tên game | `"Counter-Strike 2"` |
| `genre` | TEXT | Thể loại chính | `"RPG"`, `"Strategy"` |
| `platform` | TEXT | Nền tảng | `'pc'`, `'ios'`, `'android'` |
| `release_date` | TEXT | Ngày ra mắt (ISO) | `"2023-09-27"` |
| `price_usd` | REAL | Giá USD | `0.00`, `59.99` |
| `publisher_name` | TEXT | Nhà phát hành | `"Valve"` |
| `developer_name` | TEXT | Nhà phát triển | `"Valve Corporation"` |
| `description` | TEXT | Mô tả ngắn | `"..."` |
| `raw_payload_path` | TEXT | Đường dẫn JSON gốc để audit | `"data/raw/steam/730.json"` |
| `created_at` | TEXT | Lần đầu insert | `"2026-07-27 09:00:00"` |
| `updated_at` | TEXT | Lần update cuối | `"2026-07-27 09:00:00"` |

**Constraints:** `UNIQUE(source, source_app_id)` → enable UPSERT.

---

### `dim_date` — Calendar dimension

| Column | Type | Description |
|--------|------|-------------|
| `date` | TEXT PK | ISO YYYY-MM-DD |
| `year` | INTEGER | `2026` |
| `quarter` | INTEGER | `1..4` |
| `month` | INTEGER | `1..12` |
| `day_of_week` | INTEGER | `0=Mon .. 6=Sun` |
| `is_weekend` | INTEGER | `1` nếu Sat/Sun |

**Populated:** từ 2 năm trước tới 1 năm sau hiện tại (cho projection).

---

### `dim_publisher` — Publisher master

| Column | Type | Description |
|--------|------|-------------|
| `publisher_id` | INTEGER PK | Auto |
| `name` | TEXT UNIQUE | `"Valve"`, `"MiHoYo"` |
| `country` | TEXT | ISO 2-letter `"US"`, `"CN"` |
| `website` | TEXT | URL |

---

## FACTS

### `fact_steam_playercounts` — Daily Steam snapshot

Mỗi game × mỗi ngày = 1 row.

| Column | Type | Description |
|--------|------|-------------|
| `snapshot_id` | INTEGER PK | Auto |
| `game_id` | INTEGER FK | → `dim_game.game_id` |
| `snapshot_date` | TEXT | ISO date |
| `peak_ccu` | INTEGER | Peak concurrent users hôm đó (proxy cho popularity) |
| `positive_reviews` | INTEGER | Total positive reviews (cumulative) |
| `negative_reviews` | INTEGER | Total negative reviews |
| `fetched_at` | TEXT | Timestamp crawl |

**Constraints:** `UNIQUE(game_id, snapshot_date)` → UPSERT, không duplicate khi re-run.

**Business meaning:** `peak_ccu` là proxy tốt nhất cho "active user base" của game PC (không có data downloads thực tế từ Steam).

---

### `fact_itunes_rankings` — Daily iOS chart position

| Column | Type | Description |
|--------|------|-------------|
| `ranking_id` | INTEGER PK | Auto |
| `game_id` | INTEGER FK | → `dim_game.game_id` |
| `snapshot_date` | TEXT | ISO date |
| `country` | TEXT | `"US"`, `"VN"` |
| `chart_name` | TEXT | `"top_free_games"`, `"top_grossing_games"` |
| `rank` | INTEGER | Vị trí (1 = cao nhất) |
| `fetched_at` | TEXT | Timestamp |

**Constraints:** `UNIQUE(game_id, snapshot_date, country, chart_name)`.

---

### `fact_engagement_metrics` — Long-format metrics (mở rộng)

Cho phép lưu bất kỳ metric nào từ Reddit, YouTube, reviews sentiment...

| Column | Type | Description |
|--------|------|-------------|
| `metric_id` | INTEGER PK | Auto |
| `game_id` | INTEGER FK | → `dim_game.game_id` |
| `snapshot_date` | TEXT | ISO date |
| `source` | TEXT | `"reddit"`, `"youtube"`, `"steam_reviews"`, `"activeplayer"` |
| `metric_name` | TEXT | `"mentions"`, `"views"`, `"sentiment_score"`, `"mau_android"`, `"mau_ios"`, ... |
| `metric_value` | REAL | Giá trị numeric |

**Constraints:** `UNIQUE(game_id, snapshot_date, source, metric_name)`.

**ActivePlayer.io conventions:**
- `source = 'activeplayer'` — estimated mobile player counts (NOT official).
- `metric_name` pattern: `<metric>_<platform>` where `<metric>` ∈ {`mau`, `dau`, `ccu`}
  and `<platform>` ∈ {`android`, `ios`, `combined`}. Example: `mau_android` = Monthly Active Users on Google Play.
- `metric_value` = **millions** of users (e.g. 73.2 = 73.2M MAU).
  For ranges like "3.1M – 7.6M", the midpoint (5.35) is stored.
- `snapshot_date` = `<YYYY-MM>-01` (first of month — data is monthly).
- **MAU** has 12-month rolling history (per-month rows, ~12 datapoints/game/platform).
- **CCU + DAU** are snapshot-only (current month, no history) — ActivePlayer.io
  publishes them as bucket ranges (e.g. "3.1M – 7.6M") → many top games share the
  same CCU value. Treat as order-of-magnitude, not exact ranking.
- Coverage: ~111 mobile games with history, refresh monthly.
- ToS: Tier 1 ✅ (see `docs/tos_compliance.md`).

---

## NEWS TABLES (morning briefing)

### `dim_news_source` — News sources master

| Column | Type | Description |
|--------|------|-------------|
| `source_id` | INTEGER PK | Auto |
| `source_type` | TEXT | `'rss'`, `'reddit'`, `'hackernews'`, `'steam_news'` |
| `source_name` | TEXT | `"The Verge"`, `"r/games"`, `"Hacker News"` |
| `feed_url` | TEXT | URL gốc |
| `tos_url` | TEXT | Link ToS (audit) |
| `notes` | TEXT | Ghi chú |

**Constraints:** `UNIQUE(source_type, source_name)`.

### `fact_news` — News items (dedup by URL)

| Column | Type | Description |
|--------|------|-------------|
| `news_id` | INTEGER PK | Auto |
| `source_id` | INTEGER FK | → `dim_news_source.source_id` |
| `game_id` | INTEGER FK | → `dim_game.game_id` (nullable — auto-match nếu tìm được) |
| `title` | TEXT | Tiêu đề |
| `url` | TEXT UNIQUE | **Dedup key** — cùng URL không insert lại |
| `summary` | TEXT | Tóm tắt (first 500 chars) |
| `author` | TEXT | Tác giả |
| `published_at` | TEXT | ISO datetime từ source |
| `score` | INTEGER | Reddit upvotes / HN score (null cho RSS) |
| `keywords` | TEXT | Tags auto-detected: `launch`, `update`, `layoffs`, `acquisition`, ... |
| `category` | TEXT | Loại tin: `ai` / `game` / `tech` / `steam_update` — rule-based theo nguồn + từ khóa tiêu đề (`src/transforms/classify_news.py`). Backfill: `python scripts/classify_news.py [--force]` |
| `fetched_at` | TEXT | Timestamp crawl |

**Constraints:** `UNIQUE(url)` — idempotent, re-run không duplicate.

---

## Business Metrics (computed trong Power BI/DAX)

Đây là các KPI mà JD yêu cầu — sẽ compute trong Power BI measures:

| KPI | Formula | Use case |
|-----|---------|----------|
| **DAU proxy** | `peak_ccu` (Steam) | Game popularity |
| **Review ratio** | `positive / (positive + negative)` | Quality score |
| **Cohort retention** (D1/D7/D30) | Cần MMP data — out of MVP scope | (Roadmap) |
| **Genre momentum** | `QoQ % change SUM(peak_ccu)` by genre | Trend detection |
| **Market share by country** | `COUNT games in top 100 by publisher` | Competitive landscape |
| **ROAS / LTV** | Cần UA spend data (Meta/TikTok) | Deal assessment *(Roadmap)* |
