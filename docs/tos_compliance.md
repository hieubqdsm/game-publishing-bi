# ⚖️ Terms of Service Compliance Log

> Tài liệu này ghi lại tình trạng ToS của từng nguồn data mà pipeline sử dụng.
> Cập nhật mỗi khi thêm nguồn mới hoặc khi platform thay đổi policy.
>
> **Nguyên tắc:** Chỉ sử dụng data qua (1) API chính thức, (2) public RSS, hoặc
> (3) báo cáo đã publish công khai. **KHÔNG** scrap trang login-gated.

---

## ✅ Đang sử dụng (Tier 1 — xanh)

### Steam Web API
- **Endpoint:** `https://api.steampowered.com`, `https://store.steampowered.com/api`
- **ToS:** https://steamcommunity.com/dev/apiterms
- **Auth:** API key (free, https://steamcommunity.com/dev/apikey)
- **Rate limit:** ~200 req / 5 phút (chính sách không công bố chính thức)
- **Bản quyền:** Steam Subscriber Agreement — data dùng cho mục đích cá nhân/research OK
- **Cách dùng:** GetAppList, GetSchemaForGame, store appdetails endpoint
- **Cập nhật:** 2026-07-27

### iTunes Search API
- **Endpoint:** `https://itunes.apple.com/search`, `https://rss.applemarketingtools.com`
- **ToS:** https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/
- **Auth:** KHÔNG cần key (public)
- **Rate limit:** ~20 req/phút (khuyến nghị)
- **Cách dùng:** App metadata, top charts RSS (JSON)
- **Cập nhật:** 2026-07-27

### IGDB (Twitch)
- **Endpoint:** `https://api.igdb.com/v4`
- **ToS:** https://www.twitch.tv/p/legal/developer-agreement/
- **Auth:** Twitch OAuth (Client Credentials)
- **Rate limit:** 4 req/s concurrent, 10k req/tháng (free tier)
- **Giới hạn:** Chỉ non-commercial use. Commercial cần partnership.
- **Cách dùng:** Game catalog enrichment (genres, platforms, screenshots)
- **Cập nhật:** 2026-07-27

### RSS feeds (Gaming news outlets)
- **Endpoints:** The Verge, IGN, Eurogamer, PCGamer, Rock Paper Shotgun
- **ToS:** RSS được publish cho mục đích syndication — consume hợp pháp
- **Auth:** Không cần
- **Rate limit:** Tự throttle 1s/request (polite)
- **Cách dùng:** Morning news briefing, filter theo 24h
- **Cập nhật:** 2026-07-27

### Hacker News (Firebase API)
- **Endpoint:** `https://hacker-news.firebaseio.com/v0`
- **ToS:** https://news.ycombinator.com/tos (public API, no auth)
- **Auth:** Không cần
- **Rate limit:** Không nghiêm ngặt
- **Cách dùng:** Industry news (funding, layoff, M&A) — filter keyword game-related
- **Cập nhật:** 2026-07-27

### Steam News API
- **Endpoint:** `https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/`
- **ToS:** https://steamcommunity.com/dev/apiterms
- **Auth:** Steam API key (cùng key với Steam crawler)
- **Cách dùng:** Patch notes, DLC announcements cho game Steam đang track
- **Cập nhật:** 2026-07-27

### Gacha revenue — Tier 1 ✅ (manual HTML input)
- **Nguồn:** revenue.ennead.cc (và tương tự) — data gốc Sensor Tower mobile estimates
- **Method:** **Human-in-the-loop** — user copy HTML table từ browser → parser → SQLite.
  KHÔNG scrap tự động (revenue.ennead.cc bot-blocked, Reddit no-auth đóng cửa từ 5/2026).
- **Pipeline:** `scripts/manual/parse_gacha_html.py` (BeautifulSoup parse HTML table)
- **Audit:** mỗi fact có `source` column ('ennead' | 'manual') + `fetched_at` timestamp
- **Caveat:** Revenue = mobile only (PC/console excluded). Data là ước tính, không phải số thật.
- **Quyết định:** Manual mode = hợp lệ + transparent. Auto-scrap bị block + sai ToS.
- **Cập nhật:** 2026-07-28

### ActivePlayer.io — Tier 1 ✅ (automated fetch, rate-limited)
- **Nguồn:** https://activeplayer.io — estimated MAU/DAU/CCU mobile games by platform (Android/iOS)
- **Endpoint:** `https://activeplayer.io/<game-slug>/` — HTML per-game page (no JSON API)
- **ToS:** https://activeplayer.io/terms-and-conditions/ (read 2026-08-10)
- **robots.txt:** `User-agent: * / Disallow: /wp-admin/` — game pages Allow cho mọi bot
  (verify: https://activeplayer.io/robots.txt)
- **Method:** `BaseCrawler` HTTP fetch với rate limit 6s + jitter 0-3s giữa requests (~7s/game).
  ToS không có clause cấm scraping/crawling/bots (chỉ "Reproduction prohibited" áp cho
  design/text — facts như numbers không có copyright).
- **Auth:** Không cần (công khai, không login, không paywall)
- **Rate limit:** Polite 6s+jitter. 123 game/tháng = ~15 phút, way dưới ngưỡng bất thường.
- **Disclaimer site:** *"All data are from various sources we estimate the values"* — data là
  **estimate** (model từ download trends), không phải số publisher thật.
- **Pipeline:** `src/crawlers/activeplayer_crawler.py` (BeautifulSoup parse HTML table)
  → CLI: `python scripts/run_activeplayer.py`
- **Audit:** mỗi fact có `source='activeplayer'` trong `fact_engagement_metrics` + `fetched_at` timestamp
- **Coverage:** ~123 mobile games (directory), 12 tháng history (Aug 2025 → Jul 2026),
  tách Google Play / App Store riêng. 1 số game (Candy Crush) không có history table → skip.
- **Quyết định:** Auto-fetch rate-limited = hợp lệ (vs gacha manual). ToS không cấm, robots allow,
  không login. Data label rõ "estimated" trong dashboard. Đáng tin hơn các site Tier 3
  (Sensor Tower/data.ai/SteamDB) vì những site đó có clause cấm rõ + login-gated.
- **Cập nhật:** 2026-08-10

---

## ⚠️ Tier 2 — Grey zone (chưa dùng, có thể thêm sau)

### Google Play Store
- **Tình trạng:** ToS cấm automated access, không có API public general-purpose
- **Giải pháp thay thế:** Chỉ dùng `google-play-scraper` library với số lượng nhỏ
  cho mục đích demo, document rõ ràng rủi ro
- **Quyết định:** Chưa thêm vào MVP

### Google Trends
- **Tình trạng:** Không có API chính thức, PyTrends dùng internal endpoint
- **Rủi ro:** Dễ bị 429 (rate limit)
- **Quyết định:** Có thể thêm với sample nhỏ nếu cần trend signal

---

## ❌ Tier 3 — Tránh (login-gated, ToS cấm scrap)

| Source | Lý do tránh | Thay thế |
|--------|-------------|----------|
| **Sensor Tower** | Enterprise SaaS, login-gated, ToS cấm automated access. Giá $30k-150k/năm. | Build pipeline thay thế từ Tier 1 |
| **data.ai (ex-App Annie)** | Login-gated, premium API. Đã thuộc Sensor Tower. | (như trên) |
| **AppMagic** | Login-gated, ToS-conditioned, không có free tier | (như trên) |
| **SteamDB** | ToS cấm scrap rõ ràng, Cloudflare bot detection | Dùng upstream Steam Web API |

**Về Sensor Tower:** JD nhắc tới nhưng thực tế không thể craw hợp pháp.
Tham khảo các báo cáo công khai họ publish tại `sensortower.com/blog` —
đây là data họ tự release cho báo chí, có thể download + cite.

---

## 🔄 Quy trình khi thêm nguồn mới

Trước khi tích hợp bất kỳ nguồn data mới nào, phải trả lời được:

1. [ ] Có API chính thức không? Link tài liệu?
2. [ ] ToS có cho phép automated access không? (trích đoạn cụ thể)
3. [ ] Cần auth gì? (API key, OAuth, cookie?)
4. [ ] Rate limit là bao nhiêu?
5. [ ] Có giới hạn commercial use không?
6. [ ] Nếu scrap HTML — robots.txt có disallow không?

Nếu câu trả lời không rõ ràng → **không thêm**. Ưu tiên ethics over convenience.

---

## 📝 Maintenance log

| Date | Source | Action | Note |
|------|--------|--------|------|
| 2026-07-27 | Steam, iTunes, IGDB | Initial ToS check | Tất cả Tier 1, hợp pháp |
| 2026-08-10 | ActivePlayer.io | Initial ToS check + add source | Tier 1: ToS không cấm scrap, robots allow, no login. Auto-fetch rate-limited OK. |
