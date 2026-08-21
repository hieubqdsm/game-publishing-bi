# 🎮 Game Publishing BI Pipeline

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> A data pipeline collecting real game-industry data from public sources (Steam, iTunes, RSS news, ActivePlayer.io) → SQLite star schema → Power BI. Built as a portfolio project simulating the work of a **Game Publishing BI Analyst** — market trends, deal evaluation, player engagement.

> 🤖 **Opening this project with an AI agent (ZCode/Claude/Cursor)?** Read [`AGENTS.md`](AGENTS.md) first — it contains full context so the agent can pick up where you left off.

---

## 🇬🇧 English

### 🎯 What this project does

Personal portfolio project modeling the day-to-day work of a **BI Analyst in mobile/PC game publishing**. The pipeline collects:

| Data type | Source | Frequency |
|-----------|--------|-----------|
| Player counts, reviews, prices (PC) | Steam Web API | Hourly |
| App info, top charts (iOS US+VN) | iTunes Search API | Every 30 min |
| Gaming + AI news (10 RSS outlets) | RSS + Hacker News + Steam News | Every 4h |
| Gacha revenue (top 50/month) | Manual HTML import (Sensor Tower public reports) | Monthly |
| Mobile MAU/DAU/CCU (~123 games) | ActivePlayer.io | Monthly |
| Game catalog metadata | IGDB API | Daily |

**Pipeline features:** hourly scheduler (no cron needed), FastAPI server for Power BI Web connector, CSV exports, Streamlit inspector dashboard (12 pages), data quality checks, ToS-compliant rate limiting, idempotent upserts.

### ⚖️ Legal & Ethics

This project **only** uses:
- Official platform APIs (per their ToS)
- Public RSS feeds
- Publicly published reports

It does **NOT** scrape Sensor Tower, data.ai, AppMagic or SteamDB (login-gated / ToS-prohibited). Full ethics log: [`docs/tos_compliance.md`](docs/tos_compliance.md).

### 🚀 Quick Start

```bash
git clone git@github.com:hieubqdsm/sensortower.git
cd sensortower
python -m venv .venv
source .venv/bin/activate       # Mac/Linux — Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # fill in real API keys (all free)

python scripts/init_db.py       # create SQLite schema + dim_date
python scripts/run_daily.py --dry-run   # verify credentials, no crawling
python scripts/run_daily.py     # full pipeline run
python scripts/run_news.py      # news crawl (RSS + HN + Steam News)
```

**API keys (all free):**

| Service | Sign up | Requirement |
|---------|---------|-------------|
| Steam Web API | https://steamcommunity.com/dev/apikey | Steam login |
| Twitch / IGDB | https://dev.twitch.tv/console | Create app → Client ID + Secret |
| iTunes Search | — | Free public API |
| ActivePlayer.io | — | Free public pages |

### 🖥️ Running components

```bash
python scripts/serve_pipeline.py   # background scheduler: Steam hourly, iTunes :30, news every 4h
python scripts/serve_api.py        # FastAPI on :8000 — JSON/CSV endpoints for Power BI (X-API-Key auth)
python scripts/export_csv.py --flat # BI-ready flat CSV exports
streamlit run dashboard/app.py     # 12-page data inspector on :8501
python scripts/data_quality.py     # freshness/anomaly/integrity alerts
```

Dashboard pages: Portfolio Overview · Daily News · Rankings & Trends · Steam CCU · Genre & Publisher · Genre Trends · Game Detail · Deal Evaluation · Gacha Revenue · UA Performance · Retention & DAU · Mobile Players.

### 📁 Project structure

```
sensortower/
├── config/          # settings, source configs (rate limits, ToS URLs)
├── src/
│   ├── crawlers/    # Steam, iTunes, IGDB, News, ActivePlayer crawlers
│   ├── transforms/  # raw → dim/fact tables
│   ├── storage/     # SQLite schema + helpers
│   ├── api/         # FastAPI app
│   └── pipeline.py  # orchestrator (failure isolation per source)
├── scripts/         # CLI entry points
├── dashboard/       # Streamlit inspector
├── powerbi/         # Power BI connection guide
└── docs/            # data dictionary, ToS compliance, methodology
```

### 📊 Data model

Star schema in SQLite — full dictionary at [`docs/data_dictionary.md`](docs/data_dictionary.md):

```
dim_game ◄──── fact_steam_playercounts      (daily + hourly CCU)
   ▲          ── fact_steam_hourly_ccu
dim_date      ── fact_itunes_rankings       (US + VN charts)
   ▲          ── fact_news                  (RSS/HN/Steam news)
dim_publisher ── fact_gacha_revenue         (monthly top-50)
dim_news_source fact_engagement_metrics     (ActivePlayer MAU/DAU/CCU)
```

### 🛣️ Roadmap

- [x] MVP: Steam + iTunes + IGDB → SQLite → Power BI
- [x] News pipeline (gaming + AI RSS, Hacker News, Steam News)
- [x] Hourly pipeline scheduler (`serve_pipeline.py`)
- [x] FastAPI server + CSV exports for Power BI
- [x] Streamlit inspector dashboard (12 pages)
- [x] Deal evaluation scorecard (VN-validated criteria)
- [x] Gacha revenue tracker + ActivePlayer mobile metrics
- [ ] Power BI dashboard (user-built, see `powerbi/data_sources.md`)
- [ ] IGDB crawler activation (needs Twitch key)
- [ ] Cloudflare tunnel for public API access

### 📝 License

MIT — see [LICENSE](LICENSE).

---

## 🇻🇳 Tiếng Việt

> Pipeline thu thập data thật từ các nguồn công khai (Steam, iTunes, RSS news, ActivePlayer.io) → SQLite → Power BI dashboard. Phục vụ mục đích **Game Publishing BI Analyst** (đánh giá portfolio, market trend, deal assessment).

### 🎯 Mục tiêu dự án

Project cá nhân mô phỏng công việc thực tế của vị trí **BI Analyst trong ngành mobile/PC game publishing**:

| Loại data | Nguồn | Tần suất |
|-----------|-------|----------|
| Player counts, reviews, prices (PC) | Steam Web API | Mỗi giờ |
| App info, top charts (iOS US+VN) | iTunes Search API | Mỗi 30 phút |
| Tin game + AI (10 nguồn RSS) | RSS + Hacker News + Steam News | Mỗi 4h |
| Gacha revenue (top 50/tháng) | Import HTML thủ công (báo cáo public của Sensor Tower) | Hàng tháng |
| Mobile MAU/DAU/CCU (~123 game) | ActivePlayer.io | Hàng tháng |
| Game catalog metadata | IGDB API | Daily |

**Tính năng pipeline:** scheduler tự chạy theo giờ (không cần cron), FastAPI server cho Power BI Web connector, export CSV, dashboard Streamlit inspector (12 trang), data quality checks, rate limit theo ToS, UPSERT idempotent.

### ⚖️ Tuyên bố pháp lý & Đạo đức

Dự án **CHỈ** sử dụng API chính thức (theo ToS), public RSS feeds, và báo cáo đã publish công khai.

**KHÔNG** scrap Sensor Tower, data.ai, AppMagic, SteamDB (login-gated / ToS cấm). Chi tiết: [`docs/tos_compliance.md`](docs/tos_compliance.md).

### 🚀 Quick Start

```bash
git clone git@github.com:hieubqdsm/sensortower.git
cd sensortower
python -m venv .venv
source .venv/bin/activate       # Mac/Linux — Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # điền API key thật (đều free)

python scripts/init_db.py       # tạo schema SQLite + dim_date
python scripts/run_daily.py --dry-run   # kiểm tra credentials, không crawl
python scripts/run_daily.py     # chạy full pipeline
python scripts/run_news.py      # crawl tin tức
```

**API keys (đều free):**

| Service | Đăng ký | Cần gì |
|---------|---------|--------|
| Steam Web API | https://steamcommunity.com/dev/apikey | Login Steam |
| Twitch / IGDB | https://dev.twitch.tv/console | Tạo app → Client ID + Secret |
| iTunes Search | — | Free public API |
| ActivePlayer.io | — | Trang public free |

### 🖥️ Chạy các thành phần

```bash
python scripts/serve_pipeline.py    # scheduler nền: Steam mỗi giờ, iTunes :30, news mỗi 4h
python scripts/serve_api.py         # FastAPI :8000 — JSON/CSV cho Power BI (auth X-API-Key)
python scripts/export_csv.py --flat # export CSV phẳng sẵn cho BI
streamlit run dashboard/app.py      # inspector 12 trang tại :8501
python scripts/data_quality.py      # cảnh báo freshness/anomaly/integrity
```

Dashboard gồm 12 trang: Portfolio Overview · Daily News · Rankings & Trends · Steam CCU · Genre & Publisher · Genre Trends · Game Detail · Deal Evaluation · Gacha Revenue · UA Performance · Retention & DAU · Mobile Players.

### 📊 Data Model (Star Schema)

Xem chi tiết tại [`docs/data_dictionary.md`](docs/data_dictionary.md):

```
dim_game ◄──── fact_steam_playercounts      (CCU daily + hourly)
   ▲          ── fact_steam_hourly_ccu
dim_date      ── fact_itunes_rankings       (chart US + VN)
   ▲          ── fact_news                  (RSS/HN/Steam news)
dim_publisher ── fact_gacha_revenue         (top-50 hàng tháng)
dim_news_source fact_engagement_metrics     (ActivePlayer MAU/DAU/CCU)
```

### 🛣️ Roadmap

- [x] **MVP**: Steam + iTunes + IGDB → SQLite → Power BI
- [x] News pipeline (RSS gaming + AI, Hacker News, Steam News)
- [x] Pipeline scheduler theo giờ (`serve_pipeline.py`)
- [x] FastAPI server + CSV exports cho Power BI
- [x] Dashboard Streamlit inspector (12 trang)
- [x] Deal evaluation scorecard (tiêu chí validated VN)
- [x] Gacha revenue tracker + ActivePlayer mobile metrics
- [ ] Power BI dashboard (user tự build theo `powerbi/data_sources.md`)
- [ ] Kích hoạt IGDB crawler (cần Twitch key)
- [ ] Cloudflare tunnel public API

### 📝 License

MIT — xem [LICENSE](LICENSE).
