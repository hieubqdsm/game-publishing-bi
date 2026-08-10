"""
Streamlit dashboard — inspect data đã crawl.

Chạy:  streamlit run dashboard/app.py

Không phải Power BI dashboard (user sẽ tự build .pbix).
Dashboard này dùng để:
  - Verify hàng ngày "hôm nay crawler gom được gì"
  - Quick inspection trên Mac mà không cần mở Power BI
  - Debug data quality issues
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

# Ensure project root on sys.path (khi chạy từ dashboard/ folder)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.db import get_connection


# ---- Page config ---------------------------------------------------------
st.set_page_config(
    page_title="Game BI — Crawler Inspector",
    page_icon="🎮",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---- Cached DB loaders ---------------------------------------------------
@st.cache_data(ttl=60)  # cache 60s — đủ để refresh sau khi crawl mới
def load_dim_game() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql("SELECT * FROM dim_game ORDER BY name", conn)


@st.cache_data(ttl=60)
def load_fact_rankings() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql("SELECT * FROM fact_itunes_rankings", conn)


@st.cache_data(ttl=60)
def load_fact_steam() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql("SELECT * FROM fact_steam_playercounts", conn)


@st.cache_data(ttl=60)
def load_dim_publisher() -> pd.DataFrame:
    with get_connection() as conn:
        return pd.read_sql("SELECT * FROM dim_publisher ORDER BY name", conn)


@st.cache_data(ttl=60)
def load_fact_news(hours: int = 24, lookback: str = "24h") -> pd.DataFrame:
    """Load news items trong khoảng lookback.
    lookback: "6h", "12h", "24h", "48h", "72h", "7d", "30d", hoặc "all".
    Nếu lookback != "all", dùng hours để tương thích ngược.
    """
    with get_connection() as conn:
        if lookback == "all":
            modifier = None
        elif lookback.endswith("d"):
            modifier = f"-{int(lookback[:-1])} days"
        else:
            modifier = f"-{hours} hours"

        if modifier is None:
            df = pd.read_sql(
                """
                SELECT n.*, s.source_type, s.source_name,
                       g.name as game_name
                FROM fact_news n
                JOIN dim_news_source s ON n.source_id = s.source_id
                LEFT JOIN dim_game g ON n.game_id = g.game_id
                ORDER BY n.published_at DESC
                """,
                conn,
            )
        else:
            df = pd.read_sql(
                """
                SELECT n.*, s.source_type, s.source_name,
                       g.name as game_name
                FROM fact_news n
                JOIN dim_news_source s ON n.source_id = s.source_id
                LEFT JOIN dim_game g ON n.game_id = g.game_id
                WHERE n.published_at >= datetime('now', ?)
                ORDER BY n.published_at DESC
                """,
                conn,
                params=(modifier,),
            )
    return _utc_to_vn(df, ["published_at", "fetched_at"])


def _utc_to_vn(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Convert UTC datetime columns to Vietnam time (UTC+7) for display."""
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce").dt.tz_convert("Asia/Ho_Chi_Minh").dt.tz_localize(None)
    return df


@st.cache_data(ttl=60)
def load_table_rowcounts() -> dict[str, int]:
    from src.storage.db import get_table_rowcounts
    return get_table_rowcounts()


# ---- Page routing via URL query param (giữ trang khi reload, dùng ?page=news) -----
PAGES = [
    "📊 Portfolio Overview",
    "📰 Daily News",
    "🏆 Rankings & Trends",
    "🎮 Steam CCU",
    "🎭 Genre & Publisher",
    "📈 Genre Trends",
    "🔍 Game Detail",
    "💼 Deal Evaluation",
    "💰 Gacha Revenue",
    "📢 UA Performance",
    "📊 Retention & DAU",
]
# Slug cho URL (vd: ?page=news) — ngắn gọn, dễ nhớ
PAGE_SLUGS = [
    "overview",
    "news",
    "rankings",
    "steam-ccu",
    "genre-publisher",
    "genre-trends",
    "game-detail",
    "deal-evaluation",
    "gacha-revenue",
    "ua-performance",
    "retention-dau",
]
SLUG_TO_PAGE = dict(zip(PAGE_SLUGS, PAGES))
PAGE_TO_SLUG = dict(zip(PAGES, PAGE_SLUGS))

# Đọc page từ URL query param, mặc định overview
qp = st.query_params
slug = qp.get("page", "overview")
page = SLUG_TO_PAGE.get(slug, PAGES[0])


def render_sidebar_data_links() -> None:
    """Render khối DB info + CSV Downloads + Live API endpoints ở cuối sidebar.

    Tách ra function để giữ menu "Trang" ở trên cùng — khối này gọi sau menu.
    """
    # Show DB path
    from config import SQLITE_PATH
    st.sidebar.write("**DB:**")
    st.sidebar.code(str(SQLITE_PATH.relative_to(PROJECT_ROOT)))

    # Row counts
    counts = load_table_rowcounts()
    st.sidebar.write("**Rows per table:**")
    for t, n in counts.items():
        st.sidebar.write(f"`{t}`: **{n:,}**")

    # ---- CSV Downloads (Power BI / Excel) ----
    st.sidebar.divider()
    st.sidebar.write("**📥 CSV Downloads (Power BI):**")
    try:
        import socket
        # Get LAN IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        lan_ip = "localhost"

    api_base = f"http://{lan_ip}:8000"
    st.sidebar.markdown(f"API: [`{api_base}/downloads`]({api_base}/downloads)")
    csv_files = ["gacha_flat", "steam_flat", "steam_hourly_flat", "itunes_flat",
                 "news_flat", "dim_date", "ua_flat", "kpis_flat", "retention_flat"]
    for f in csv_files:
        st.sidebar.markdown(f"&nbsp;&nbsp;[`{f}.csv`]({api_base}/downloads/{f}.csv)")
    st.sidebar.caption("CSV: tải file tĩnh. Cần re-export để update.")

    # Live API endpoints (Power BI Web connector — real-time from DB)
    st.sidebar.write("**🔌 Live API (Power BI Web connector):**")
    api_endpoints = [
        ("Gacha revenue (LIVE)", "/api/gacha/revenue?format=csv"),
        ("Steam hourly CCU (LIVE)", "/api/tables/fact_steam_hourly_ccu?format=csv"),
        ("iTunes rankings (LIVE)", "/api/tables/fact_itunes_rankings?format=csv"),
        ("News 48h (LIVE)", "/api/news?hours=48&format=csv"),
        ("UA campaigns (LIVE)", "/api/tables/sample_ua_campaigns?format=csv"),
        ("KPIs DAU/Retention (LIVE)", "/api/tables/sample_daily_kpis?format=csv"),
        ("Retention Cookie Cats (LIVE)", "/api/tables/sample_cookie_cats?format=csv"),
        ("Health check", "/api/health"),
        ("Swagger docs", "/docs"),
    ]
    for label, path in api_endpoints:
        st.sidebar.markdown(f"&nbsp;&nbsp;[`{label}`]({api_base}{path})")
    st.sidebar.caption("Live API: data realtime từ DB. Power BI → Get Data → Web → URL + header `X-API-Key`.")


# ---- Sidebar: navigation + global state ---------------------------------
# THỨ TỰ: Title → Menu trang (trên cùng) → DB/CSV/API (dưới)
st.sidebar.title("🎮 Game BI")
st.sidebar.caption("Crawler data inspector")

# Refresh button — clear cache
if st.sidebar.button("🔄 Refresh data", help="Clear cache, đọc lại từ DB"):
    st.cache_data.clear()
    st.rerun()

# Menu trang — đặt NGAY SAU title/refresh để dễ tiếp cận nhất
st.sidebar.markdown("#### 📑 Trang")
for p, slug_i in zip(PAGES, PAGE_SLUGS):
    is_current = (p == page)
    label = f"👉 {p}" if is_current else p
    # use_container_width để button chiếm hết sidebar, nhìn như menu dọc
    if st.sidebar.button(label, key=f"nav_{slug_i}", use_container_width=True):
        if not is_current:
            st.query_params["page"] = slug_i
            st.rerun()
st.sidebar.caption("💡 URL `?page=...` giữ trang khi reload/F5.")

# Khối DB info + CSV + Live API — đẩy xuống dưới menu trang
st.sidebar.divider()
render_sidebar_data_links()
st.sidebar.divider()
st.sidebar.caption("Power BI dashboard build riêng — đây chỉ là inspector.")


# =========================================================================
# PAGE 1: PORTFOLIO OVERVIEW
# =========================================================================
if page == PAGES[0]:
    st.title("📊 Portfolio Overview")
    st.caption("Tổng quan data đã crawl từ các nguồn")

    games = load_dim_game()
    rankings = load_fact_rankings()
    steam = load_fact_steam()
    pubs = load_dim_publisher()

    # KPI cards
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Games", len(games))
    col2.metric("iTunes Games", len(games[games["source"] == "itunes"]))
    col3.metric("Steam Games", len(games[games["source"] == "steam"]))
    col4.metric("IGDB Games", len(games[games["source"] == "igdb"]))
    col5.metric("Publishers", len(pubs))

    st.divider()

    # Games by source
    st.subheader("Games by source")
    source_counts = games["source"].value_counts().reset_index()
    source_counts.columns = ["Source", "Count"]
    st.dataframe(source_counts, use_container_width=True, hide_index=True)

    # Recent crawl activity
    st.subheader("📅 Recent crawl activity")
    if not rankings.empty:
        recent = (
            rankings.groupby("snapshot_date")
            .agg(rankings=("ranking_id", "count"),
                 unique_games=("game_id", "nunique"),
                 countries=("country", "nunique"))
            .reset_index()
            .sort_values("snapshot_date", ascending=False)
            .head(14)
        )
        st.dataframe(recent, use_container_width=True, hide_index=True)
    else:
        st.info("No iTunes ranking data yet.")

    if not steam.empty:
        st.write("**Steam snapshots:**")
        recent_steam = (
            steam.groupby("snapshot_date")
            .agg(games=("game_id", "nunique"),
                 total_peak_ccu=("peak_ccu", "sum"))
            .reset_index()
            .sort_values("snapshot_date", ascending=False)
            .head(14)
        )
        st.dataframe(recent_steam, use_container_width=True, hide_index=True)
    else:
        st.info("No Steam data yet (cần STEAM_API_KEY).")

    # Last crawl timestamp
    st.divider()
    st.subheader("🕐 Last update")
    if not games.empty:
        last = games["updated_at"].max()
        st.write(f"Last game update: **{last}**")


# =========================================================================
# PAGE 2: DAILY NEWS (morning briefing)
# =========================================================================
elif page == PAGES[1]:
    st.title("📰 Daily News")
    st.caption("Morning game news briefing — RSS + Hacker News + Steam News")

    # Hours selector
    col_h1, col_h2 = st.columns([1, 3])
    with col_h1:
        lookback = st.selectbox(
            "Lookback",
            ["1 ngày", "3 ngày", "1 tuần", "1 tháng", "1 năm", "Tất cả"],
            index=0,
        )

    # Map lookback label sang modifier SQLite
    lookback_map = {
        "1 ngày": ("1d", 24),
        "3 ngày": ("3d", 72),
        "1 tuần": ("7d", 168),
        "1 tháng": ("30d", 720),
        "1 năm": ("365d", 8760),
        "Tất cả": ("all", None),
    }
    lookback_key, hours = lookback_map[lookback]

    # Reset pagination khi đổi lookback (tránh hiển thị trang cũ trên data mới)
    if st.session_state.get("news_lookback") != lookback:
        st.session_state["news_lookback"] = lookback
        st.session_state["news_page"] = 1

    news = load_fact_news(hours or 24, lookback_key)

    if news.empty:
        st.warning(
            f"❌ Chưa có news trong {lookback}. "
            f"Chạy: `python scripts/run_news.py --hours {hours or 24}` rồi refresh."
        )
        st.stop()

    # KPI cards
    col1, col2, col3, col4 = st.columns(4)
    col1.metric(f"News ({lookback.lower()})", len(news))
    col2.metric("Sources", news["source_name"].nunique())
    col3.metric("Matched games", news["game_id"].notna().sum())
    col4.metric("With keywords", news["keywords"].notna().sum() - (news["keywords"] == "").sum())

    st.divider()

    # Filters
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        sources = ["All"] + sorted(news["source_name"].unique())
        sel_source = st.selectbox("Filter by source", sources)
    with col_f2:
        # Parse keywords thành list unique
        all_keywords = set()
        for kws in news["keywords"].dropna():
            if kws:
                all_keywords.update(kws.split(","))
        kw_options = ["All"] + sorted(all_keywords)
        sel_keyword = st.selectbox("Filter by keyword", kw_options)
    with col_f3:
        search = st.text_input("🔍 Search title", "")

    # Apply filters
    filtered = news.copy()
    if sel_source != "All":
        filtered = filtered[filtered["source_name"] == sel_source]
    if sel_keyword != "All":
        filtered = filtered[filtered["keywords"].str.contains(sel_keyword, na=False)]
    if search:
        filtered = filtered[filtered["title"].str.contains(search, case=False, na=False)]

    st.write(f"**Showing {len(filtered)} of {len(news)} news items**")

    # Display news as cards (cherry-picked style, không phải raw table)
    import plotly.express as px

    # Keyword distribution chart
    st.subheader("🏷️ News by keyword")
    kw_series = (
        news["keywords"].dropna()
        .str.split(",")
        .explode()
        .value_counts()
        .head(10)
        .reset_index()
    )
    kw_series.columns = ["Keyword", "Count"]
    if not kw_series.empty:
        fig = px.bar(kw_series, x="Count", y="Keyword", orientation="h",
                     title=f"Top keywords ({lookback.lower()})")
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=350)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No keywords detected.")

    # News by source breakdown
    st.divider()
    col_s1, col_s2 = st.columns([2, 3])
    with col_s1:
        st.subheader("📡 By source")
        src_counts = news["source_name"].value_counts().reset_index()
        src_counts.columns = ["Source", "Count"]
        st.dataframe(src_counts, use_container_width=True, hide_index=True)

    with col_s2:
        st.subheader("📅 Timeline")
        news_dt = news.copy()
        news_dt["hour"] = pd.to_datetime(news_dt["published_at"]).dt.floor("H")
        timeline = news_dt.groupby("hour").size().reset_index(name="count")
        if not timeline.empty:
            fig = px.bar(timeline, x="hour", y="count",
                         title=f"News per hour ({lookback.lower()})")
            fig.update_layout(height=300, xaxis_title="", yaxis_title="items")
            st.plotly_chart(fig, use_container_width=True)

    # News list (cherry-picked display) với pagination
    st.divider()

    # Lookback RIÊNG cho news feed (mặc định theo lookback trên, nhưng user có thể chỉnh)
    col_feed1, col_feed2 = st.columns([1, 3])
    with col_feed1:
        feed_lookback = st.selectbox(
            "Feed lookback",
            ["1 ngày", "3 ngày", "1 tuần", "1 tháng", "1 năm", "Tất cả"],
            index=["1 ngày", "3 ngày", "1 tuần", "1 tháng", "1 năm", "Tất cả"].index(lookback),
            key="news_feed_lookback",
        )
    feed_key, feed_hours = lookback_map[feed_lookback]

    # Re-query data riêng cho feed (có thể khác lookback phía trên)
    feed_news = load_fact_news(feed_hours or 24, feed_key)

    # Áp dụng filter tương tự lên feed
    feed_filtered = feed_news.copy()
    if sel_source != "All":
        feed_filtered = feed_filtered[feed_filtered["source_name"] == sel_source]
    if sel_keyword != "All":
        feed_filtered = feed_filtered[feed_filtered["keywords"].str.contains(sel_keyword, na=False)]
    if search:
        feed_filtered = feed_filtered[feed_filtered["title"].str.contains(search, case=False, na=False)]

    PAGE_SIZE = 15
    total_items = len(feed_filtered)
    n_pages = max(1, (total_items + PAGE_SIZE - 1) // PAGE_SIZE)

    st.subheader(f"📋 News feed ({total_items} items)")

    # Reset pagination khi đổi feed_lookback hoặc filter
    feed_state_key = f"{feed_lookback}|{sel_source}|{sel_keyword}|{search}"
    if st.session_state.get("news_feed_state") != feed_state_key:
        st.session_state["news_feed_state"] = feed_state_key
        st.session_state["news_page"] = 1

    # Sort by date (newest first) then by score
    display_cols = ["published_at", "source_name", "title", "summary", "author",
                    "keywords", "score", "url"]
    display_df = feed_filtered[display_cols].copy() if not feed_filtered.empty else feed_filtered
    display_df["published_at"] = pd.to_datetime(display_df["published_at"]).dt.strftime("%m-%d %H:%M")

    # Pagination state
    if "news_page" not in st.session_state:
        st.session_state["news_page"] = 1
    st.session_state["news_page"] = max(1, min(st.session_state["news_page"], n_pages))
    page_num = st.session_state["news_page"]

    # Pagination buttons row
    if n_pages > 1:
        cols = st.columns(11)
        # Prev button
        if cols[0].button("‹", key="news_prev", disabled=(page_num <= 1),
                          help="Previous page", use_container_width=True):
            st.session_state["news_page"] -= 1
            st.rerun()
        # Page number buttons (show up to 9 around current)
        start_p = max(1, page_num - 4)
        end_p = min(n_pages, start_p + 8)
        btn_idx = 1
        for p in range(start_p, end_p + 1):
            if btn_idx <= 9:
                label = f"**{p}**" if p == page_num else str(p)
                if cols[btn_idx].button(
                    str(p), key=f"news_p{p}",
                    disabled=(p == page_num), use_container_width=True,
                ):
                    st.session_state["news_page"] = p
                    st.rerun()
                btn_idx += 1
        # Next button
        if cols[10].button("›", key="news_next", disabled=(page_num >= n_pages),
                           help="Next page", use_container_width=True):
            st.session_state["news_page"] += 1
            st.rerun()
        st.caption(f"Page {page_num} of {n_pages} — "
                   f"showing {(page_num-1)*PAGE_SIZE+1}-{min(page_num*PAGE_SIZE, total_items)} of {total_items}")

    start_idx = (page_num - 1) * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_df = display_df.iloc[start_idx:end_idx]

    # Render as cards with summary + author
    for _, row in page_df.iterrows():
        title = row["title"]
        url = row["url"]
        src = row["source_name"]
        time_str = row["published_at"]
        kw = row["keywords"]
        score = row["score"]
        summary = row.get("summary", "") or ""
        author = row.get("author", "") or ""

        kw_str = f" *`{kw}`*" if kw else ""
        score_str = f" ⬆{score}" if pd.notna(score) and score else ""
        author_str = f" · ✍ {author}" if author else ""
        summary_str = f"\n\n> {summary[:200]}{'...' if len(summary) > 200 else ''}" if summary else ""

        st.markdown(
            f"**[{time_str}]** `[{src}]` [{title}]({url}){kw_str}{score_str}{author_str}{summary_str}",
        )
        st.markdown("")


# =========================================================================
# PAGE 3: RANKINGS & TRENDS
# =========================================================================
elif page == PAGES[2]:
    st.title("🏆 Rankings & Trends")
    st.caption("iTunes top chart rankings theo country & chart type")

    rankings = load_fact_rankings()
    games = load_dim_game()

    if rankings.empty:
        st.warning("No ranking data yet. Chạy `python scripts/run_daily.py --source itunes`")
        st.stop()

    # Join with game names
    df = rankings.merge(
        games[["game_id", "name", "genre", "publisher_name"]],
        on="game_id", how="left"
    )

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        countries = sorted(df["country"].unique())
        sel_countries = st.multiselect("Country", countries, default=countries)
    with col2:
        charts = sorted(df["chart_name"].unique())
        sel_charts = st.multiselect("Chart", charts, default=charts)
    with col3:
        dates = sorted(df["snapshot_date"].unique(), reverse=True)
        sel_date = st.selectbox("Snapshot date", dates, index=0)

    mask = (
        df["country"].isin(sel_countries)
        & df["chart_name"].isin(sel_charts)
        & (df["snapshot_date"] == sel_date)
    )
    filtered = df[mask].sort_values(["country", "chart_name", "rank"])

    st.subheader(f"Top games — {sel_date}")
    if filtered.empty:
        st.info("No data cho filter này.")
    else:
        # Display as grouped table
        display_cols = ["rank", "name", "genre", "publisher_name", "country", "chart_name"]
        st.dataframe(
            filtered[display_cols],
            use_container_width=True,
            hide_index=True,
            height=500,
        )

    # Rank trajectory chart (if multiple snapshots)
    st.divider()
    st.subheader("📈 Rank trajectory")
    if len(dates) > 1:
        sel_games_traj = st.multiselect(
            "Chọn games để xem trajectory",
            sorted(df["name"].dropna().unique()),
            max_selections=10,
        )
        if sel_games_traj:
            traj = df[df["name"].isin(sel_games_traj)].copy()
            traj = traj.sort_values("snapshot_date")
            fig = px.line(
                traj, x="snapshot_date", y="rank", color="name",
                markers=True, title="Rank over time (lower = better)",
            )
            fig.update_yaxes(autorange="reversed")  # rank 1 on top
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Chọn ít nhất 1 game để xem trajectory.")
    else:
        st.info("Cần ít nhất 2 snapshot dates để vẽ trajectory. Chạy crawler thêm vài ngày.")


# =========================================================================
# PAGE 3.5: 🎮 STEAM CCU RANKINGS
# =========================================================================
elif page == PAGES[3]:
    st.title("🎮 Steam CCU Rankings")
    st.caption("Top 100 Steam games by concurrent players — hourly snapshot via Steam Web API")

    steam_df = load_fact_steam()
    games_df = load_dim_game()

    if steam_df.empty:
        st.warning(
            "❌ Chưa có Steam data. Cần:\n"
            "1. `STEAM_API_KEY` trong `.env` (đăng ký: steamcommunity.com/dev/apikey)\n"
            "2. Chạy: `python scripts/run_daily.py --source steam`\n"
            "3. Cron đã setup hourly — đợi vài giờ để có history"
        )
        st.stop()

    # Join game metadata
    df = steam_df.merge(
        games_df[["game_id", "name", "genre", "publisher_name", "developer_name", "price_usd"]],
        on="game_id", how="left",
    )

    # Latest snapshot
    latest_date = df["snapshot_date"].max()
    latest = df[df["snapshot_date"] == latest_date].copy()
    n_dates = df["snapshot_date"].nunique()

    # KPIs
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Games tracked", latest["game_id"].nunique())
    col2.metric("Total CCU (now)", f"{latest['peak_ccu'].sum():,}")
    col3.metric("Snapshots", n_dates)
    col4.metric("Latest snapshot", str(latest_date))

    st.divider()

    # Filter by publisher (optional)
    colf1, colf2 = st.columns(2)
    with colf1:
        top_n = st.slider("Top N games", 10, 100, 25, step=5)
    with colf2:
        publishers = ["All"] + sorted(
            p for p in latest["publisher_name"].dropna().unique() if p
        )
        sel_pub = st.selectbox("Filter by publisher", publishers)

    filtered = latest.copy()
    if sel_pub != "All":
        filtered = filtered[filtered["publisher_name"] == sel_pub]
    filtered = filtered.nlargest(top_n, "peak_ccu")

    # === Top N leaderboard ===
    st.subheader(f"🏆 Top {len(filtered)} by CCU — {latest_date}")
    display = filtered.copy()
    # Sentiment %
    display["sentiment_%"] = (
        display["positive_reviews"] /
        (display["positive_reviews"] + display["negative_reviews"]) * 100
    ).round(1)
    display["sentiment"] = display["sentiment_%"].apply(
        lambda x: f"{x:.0f}%" if x == x else "—"
    )
    display = display[[
        "name", "peak_ccu", "publisher_name", "genre", "price_usd",
        "positive_reviews", "negative_reviews", "sentiment",
    ]].copy()
    display.columns = [
        "Game", "Peak CCU", "Publisher", "Genre", "Price ($)",
        "Reviews +", "Reviews −", "Sentiment",
    ]
    st.dataframe(display, use_container_width=True, hide_index=True, height=450)

    st.divider()

    # === CCU trajectory (hourly timeline từ fact_steam_hourly_ccu) ===
    st.subheader("📈 CCU Trajectory (hourly)")
    with get_connection() as conn:
        hourly_df = pd.read_sql_query(
            """
            SELECT h.snapshot_ts, h.peak_ccu, g.name
            FROM fact_steam_hourly_ccu h
            JOIN dim_game g ON h.game_id = g.game_id
            ORDER BY h.snapshot_ts DESC
            """,
            conn,
        )

    if hourly_df.empty:
        st.info("⏳ Chưa có hourly data. Scheduler sẽ craw mỗi giờ → timeline sẽ render.")
    else:
        n_hours = hourly_df["snapshot_ts"].nunique()
        n_games_hourly = hourly_df["name"].nunique()
        st.caption(f"📊 {n_games_hourly} games × {n_hours} hourly snapshots")

        # Game selector for timeline
        sel_traj = st.multiselect(
            "🎮 Games so sánh CCU timeline",
            sorted(hourly_df.groupby("name")["peak_ccu"].max()
                   .nlargest(15).index.tolist()),
            default=hourly_df.groupby("name")["peak_ccu"].max()
                   .nlargest(3).index.tolist(),
            max_selections=10,
            key="steam_hourly_traj",
        )
        if sel_traj:
            traj = hourly_df[hourly_df["name"].isin(sel_traj)].copy()
            traj = traj.sort_values(["name", "snapshot_ts"])
            fig = px.line(
                traj, x="snapshot_ts", y="peak_ccu", color="name",
                markers=True, title="Steam CCU over time (hourly snapshots)",
                labels={"peak_ccu": "Peak Concurrent Players",
                        "snapshot_ts": "Time (hourly)"},
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Chọn ít nhất 1 game để xem timeline.")

    st.divider()

    # === Publisher market share (CCU) ===
    st.subheader("📊 Publisher Market Share (by CCU)")
    pub_share = (
        latest.groupby("publisher_name")["peak_ccu"].sum()
        .reset_index()
        .sort_values("peak_ccu", ascending=False)
    )
    # Top 10 + Other
    top10 = pub_share.head(10).copy()
    other_ccu = pub_share.iloc[10:]["peak_ccu"].sum()
    if other_ccu > 0:
        top10 = pd.concat([top10, pd.DataFrame([{"publisher_name": "Other", "peak_ccu": other_ccu}])])
    fig_pie = px.pie(
        top10, values="peak_ccu", names="publisher_name",
        title=f"CCU share by publisher — {latest_date}",
    )
    st.plotly_chart(fig_pie, use_container_width=True)


# =========================================================================
# PAGE 4: GENRE & PUBLISHER
# =========================================================================
elif page == PAGES[4]:
    st.title("🎭 Genre & Publisher Analysis")
    st.caption("Phân tích market share theo thể loại và publisher")

    games = load_dim_game()
    rankings = load_fact_rankings()

    if games.empty:
        st.warning("No data yet.")
        st.stop()

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Games by genre")
        genre_counts = games["genre"].fillna("(unknown)").value_counts().reset_index()
        genre_counts.columns = ["Genre", "Count"]
        import plotly.express as px
        fig = px.bar(
            genre_counts.head(15), x="Count", y="Genre", orientation="h",
            title=f"Top {min(15, len(genre_counts))} genres",
        )
        fig.update_layout(yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Games by publisher")
        pub_counts = games["publisher_name"].fillna("(unknown)").value_counts().reset_index()
        pub_counts.columns = ["Publisher", "Count"]
        fig = px.bar(
            pub_counts.head(15), x="Count", y="Publisher", orientation="h",
            title=f"Top {min(15, len(pub_counts))} publishers",
        )
        fig.update_layout(yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # Cross-source analysis
    st.subheader("🔀 Cross-source distribution")
    cross = (
        games.groupby(["source", "genre"])
        .size()
        .reset_index(name="count")
        .sort_values(["source", "count"], ascending=[True, False])
    )
    st.dataframe(cross, use_container_width=True, hide_index=True)

    # Publisher detail table
    st.divider()
    st.subheader("📋 Publisher detail")
    pub_detail = (
        games.groupby("publisher_name")
        .agg(
            total_games=("game_id", "count"),
            genres=("genre", lambda x: ", ".join(sorted(set(x.dropna()))) or "—"),
            sources=("source", lambda x: ", ".join(sorted(set(x))) or "—"),
        )
        .reset_index()
        .sort_values("total_games", ascending=False)
    )
    st.dataframe(pub_detail, use_container_width=True, hide_index=True)


# =========================================================================
# PAGE 5: GAME DETAIL
# =========================================================================
elif page == PAGES[6]:
    st.title("🔍 Game Detail")
    st.caption("Deep dive vào 1 game cụ thể")

    games = load_dim_game()
    rankings = load_fact_rankings()
    steam = load_fact_steam()

    if games.empty:
        st.warning("No games in DB yet.")
        st.stop()

    # Game selector
    sel_game_name = st.selectbox(
        "Chọn game", sorted(games["name"].dropna().unique())
    )

    game_rows = games[games["name"] == sel_game_name]
    if game_rows.empty:
        st.error("Game not found.")
        st.stop()

    # If multiple rows (cross-source), let user pick
    if len(game_rows) > 1:
        options = game_rows.apply(
            lambda r: f"{r['source']} ({r['source_app_id']})", axis=1
        ).tolist()
        sel_idx = st.radio("Có nhiều row (cross-source)", options)
        sel_row = game_rows.iloc[options.index(sel_idx)]
    else:
        sel_row = game_rows.iloc[0]

    game_id = sel_row["game_id"]

    # Metadata
    st.subheader(f"🎮 {sel_row['name']}")
    meta_col1, meta_col2, meta_col3 = st.columns(3)
    meta_col1.write(f"**Source:** {sel_row['source']}")
    meta_col1.write(f"**App ID:** `{sel_row['source_app_id']}`")
    meta_col2.write(f"**Genre:** {sel_row['genre'] or '—'}")
    meta_col2.write(f"**Platform:** {sel_row['platform'] or '—'}")
    meta_col3.write(f"**Release:** {sel_row['release_date'] or '—'}")
    meta_col3.write(f"**Price:** ${sel_row['price_usd']:.2f}" if pd.notna(sel_row['price_usd']) else "**Price:** —")

    st.write(f"**Publisher:** {sel_row['publisher_name'] or '—'}")
    st.write(f"**Developer:** {sel_row['developer_name'] or '—'}")
    if sel_row['description']:
        st.write(f"**Description:**")
        st.caption(sel_row['description'])

    # Ranking history
    st.divider()
    st.subheader("🏆 iTunes ranking history")
    game_rankings = rankings[rankings["game_id"] == game_id].sort_values("snapshot_date")
    if game_rankings.empty:
        st.info("No iTunes ranking data cho game này.")
    else:
        st.dataframe(game_rankings, use_container_width=True, hide_index=True)

    # Steam stats
    st.divider()
    st.subheader("🎮 Steam player counts")
    game_steam = steam[steam["game_id"] == game_id].sort_values("snapshot_date")
    if game_steam.empty:
        st.info("No Steam data cho game này.")
    else:
        st.dataframe(game_steam, use_container_width=True, hide_index=True)

    # Raw payload link
    st.divider()
    if sel_row['raw_payload_path']:
        st.subheader("📂 Raw payload")
        raw_path = PROJECT_ROOT / sel_row['raw_payload_path']
        st.code(str(raw_path))
        if raw_path.exists():
            with st.expander("View raw JSON"):
                import json
                st.json(json.loads(raw_path.read_text(encoding="utf-8")))
        else:
            st.warning(f"Raw file không tồn tại: {raw_path}")


# =========================================================================
# PAGE 6: GENRE TRENDS (emerging / declining genres)
# =========================================================================
elif page == PAGES[5]:
    st.title("📈 Genre Trends")
    st.caption("Emerging vs declining genres — recommend deal sourcing direction")

    games = load_dim_game()
    rankings = load_fact_rankings()
    steam = load_fact_steam()

    if games.empty:
        st.warning("No data yet.")
        st.stop()

    import plotly.express as px

    # ---- Section 1: Current genre distribution --------------------------
    st.subheader("🎯 Current genre distribution")
    genre_counts = (
        games["genre"].fillna("(unknown)").value_counts().reset_index()
    )
    genre_counts.columns = ["Genre", "Count"]
    fig = px.pie(
        genre_counts.head(10), values="Count", names="Genre",
        title=f"Distribution of {len(games)} tracked games by genre",
    )
    fig.update_layout(height=400)
    st.plotly_chart(fig, use_container_width=True)

    # ---- Section 2: Genre momentum (Steam CCU proxy) -------------------
    st.divider()
    st.subheader("🚀 Genre momentum (Steam CCU proxy)")
    if not steam.empty:
        steam_with_genre = steam.merge(
            games[["game_id", "genre", "name"]], on="game_id", how="left"
        )
        genre_momentum = (
            steam_with_genre.groupby("genre")
            .agg(
                games=("game_id", "nunique"),
                total_peak_ccu=("peak_ccu", "sum"),
                avg_peak_ccu=("peak_ccu", "mean"),
            )
            .reset_index()
            .sort_values("total_peak_ccu", ascending=False)
        )
        st.dataframe(genre_momentum, use_container_width=True, hide_index=True)

        fig = px.bar(
            genre_momentum.head(15), x="total_peak_ccu", y="genre", orientation="h",
            title="Total peak CCU by genre (Steam)",
        )
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=450)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Steam data chưa có (chạy `--source steam`). Hiện không tính được momentum.")

    # ---- Section 3: Genre by source (cross-platform) -------------------
    st.divider()
    st.subheader("🔀 Cross-platform genre comparison")
    cross = (
        games.groupby(["genre", "source"])
        .size()
        .reset_index(name="count")
        .pivot(index="genre", columns="source", values="count")
        .fillna(0)
        .astype(int)
    )
    cross["total"] = cross.sum(axis=1)
    cross = cross.sort_values("total", ascending=False).head(15)
    st.dataframe(cross, use_container_width=True)

    # ---- Section 4: iTunes ranking movement (genre-level) --------------
    st.divider()
    st.subheader("🏆 iTunes ranking presence by genre")
    if not rankings.empty:
        rank_genre = rankings.merge(
            games[["game_id", "genre"]], on="game_id", how="left"
        )
        genre_rank = (
            rank_genre.groupby("genre")
            .agg(
                top10_appearances=("rank", lambda x: (x <= 10).sum()),
                avg_rank=("rank", "mean"),
                total_appearances=("rank", "count"),
            )
            .reset_index()
            .sort_values("top10_appearances", ascending=False)
        )
        st.dataframe(genre_rank, use_container_width=True, hide_index=True)
    else:
        st.info("Chưa có iTunes ranking data.")

    # ---- Section 5: Emerging genres (insight heuristic) ---------------
    st.divider()
    st.subheader("💡 Emerging genre signals")
    st.caption("Heuristic: genres có >3 games + publisher đa dạng (không độc quyền)")
    emerging = (
        games.groupby("genre")
        .agg(
            games=("game_id", "count"),
            publishers=("publisher_name", "nunique"),
        )
        .reset_index()
    )
    emerging["competition_ratio"] = emerging["publishers"] / emerging["games"]
    # Genre "healthy" = nhiều games + nhiều publishers (không bị 1 pub độc quyền)
    healthy = emerging[(emerging["games"] >= 2) & (emerging["publishers"] >= 2)]
    if not healthy.empty:
        st.write("**Genres có competition lành mạnh (đáng sourcing):**")
        st.dataframe(
            healthy.sort_values("games", ascending=False),
            use_container_width=True, hide_index=True
        )
    else:
        st.info("Cần thêm data để detect emerging genres (hiện chỉ có 1 genre dominant).")


# =========================================================================
# PAGE 7: DEAL EVALUATION — 8 tiêu chí data-validated cho VN market
# =========================================================================
elif page == PAGES[7]:
    st.title("💼 Deal Evaluation — VN Market")
    st.caption("8 tiêu chí data-validated từ top VN iTunes + VNG/Garena portfolio pattern")

    # ---- Input -----------------------------------------------------------
    st.subheader("📝 Game Submission")
    input_mode = st.radio(
        "Game source",
        ["📋 Chọn từ DB", "✍️ Nhập manual"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if input_mode == "📋 Chọn từ DB":
        with get_connection() as conn:
            db_games = pd.read_sql_query(
                """SELECT DISTINCT name, genre, source FROM dim_game
                   WHERE name IS NOT NULL ORDER BY name""", conn)
            gacha_games = pd.read_sql_query(
                """SELECT DISTINCT name, NULL as genre, 'gacha' as source
                   FROM dim_gacha_game ORDER BY name""", conn)
        all_games = pd.concat([db_games, gacha_games], ignore_index=True)
        sel_idx = st.selectbox(
            "Chọn game để evaluate",
            range(len(all_games)),
            format_func=lambda i: f"{all_games.iloc[i]['name']} ({all_games.iloc[i]['source']})",
        )
        game_name = all_games.iloc[sel_idx]["name"]
        db_genre = all_games.iloc[sel_idx]["genre"]
        default_genre = db_genre if db_genre else "Action"
    else:
        game_name = st.text_input("Game name", value="", placeholder="vd: Pixel Dungeon 3")
        default_genre = "Action"

    col_in1, col_in2, col_in3 = st.columns(3)
    with col_in1:
        genre = st.selectbox("Genre", [
            "Action", "Adventure", "Strategy", "Sports",  # top VN genres
            "RPG", "Roleplaying", "Casual", "Puzzle", "Simulation",
            "Racing", "Board", "Music", "MMO", "Other"])
    with col_in2:
        monetization = st.selectbox("Monetization", [
            "F2P + IAP", "F2P + Ads", "F2P + Hybrid",
            "Premium (paid)", "Subscription", "Paid + DLC"])
    with col_in3:
        has_vn_publisher = st.selectbox("VN publishing partner?", [
            "VNG", "Garena", "VTC Mobile", "Other VN publisher",
            "Self-publish", "No VN partner yet"])

    col_in4, col_in5 = st.columns(2)
    with col_in4:
        target_cpi = st.number_input("Target CPI VN (USD)", min_value=0.0, value=0.80, step=0.10)
        est_ltv = st.number_input("Estimated LTV (USD)", min_value=0.0, value=1.50, step=0.10)
    with col_in5:
        multiplayer = st.selectbox("Multiplayer/Social", [
            "Yes — multiplayer/esport", "Yes — co-op/social",
            "No — single player", "No — but viral potential"])
        tech_req = st.selectbox("Hardware requirement", [
            "Low (runs on $100 phones)", "Medium ($200-300 phones)",
            "High ($400+ phones)", "Flagship only"])

    # ---- Compute 8 scores (data-validated weights) ---------------------
    st.divider()
    st.subheader("🎯 VN Market Scorecard (8 factors)")

    scores = {}
    notes = {}

    # 1. F2P (VETO — 0 paid games in top 40 VN)
    f2p_models = ["F2P + IAP", "F2P + Ads", "F2P + Hybrid"]
    if monetization in f2p_models:
        scores["1. F2P Model"] = 10
        notes["1. F2P Model"] = "✅ F2P — phù hợp VN (100% top 40 VN là free)"
    else:
        scores["1. F2P Model"] = 0
        notes["1. F2P Model"] = "🔴 VETO: 0 paid games trong top VN. Market reject paid."

    # 2. VN Publisher (25% weight — top 10 VN = 100% have VN publisher)
    vn_pubs = ["VNG", "Garena", "VTC Mobile", "Other VN publisher"]
    if has_vn_publisher in vn_pubs:
        scores["2. VN Publisher"] = 10
        notes["2. VN Publisher"] = f"✅ {has_vn_publisher} — proven VN publisher (top 10 = 100% VN-published)"
    elif has_vn_publisher == "Self-publish":
        scores["2. VN Publisher"] = 5
        notes["2. VN Publisher"] = "⚠️ Self-publish — cần payment gateway + localize team"
    else:
        scores["2. VN Publisher"] = 2
        notes["2. VN Publisher"] = "🔴 No VN partner — rủi ro cao (payment + localize + legal)"

    # 3. Payment integration (correlated with VN publisher)
    if has_vn_publisher in vn_pubs:
        scores["3. Payment Gateway"] = 9
        notes["3. Payment Gateway"] = "✅ VN publisher = có payment gateway (Momo/ZaloPay/Zing)"
    elif has_vn_publisher == "Self-publish":
        scores["3. Payment Gateway"] = 4
        notes["3. Payment Gateway"] = "⚠️ Cần tự tích hợp Momo/ZaloPay — tốn thời gian"
    else:
        scores["3. Payment Gateway"] = 2
        notes["3. Payment Gateway"] = "🔴 Không có payment VN → user không nạp được"

    # 4. Multiplayer/Social (top 5 VN = 100% multiplayer)
    if "multiplayer" in multiplayer.lower() or "esport" in multiplayer.lower():
        scores["4. Multiplayer/Social"] = 10
        notes["4. Multiplayer/Social"] = "✅ Multiplayer — top 5 VN (Liên Quân, Free Fire, PUBG) đều multiplayer"
    elif "co-op" in multiplayer.lower() or "social" in multiplayer.lower():
        scores["4. Multiplayer/Social"] = 8
        notes["4. Multiplayer/Social"] = "✅ Co-op/social — retention booster"
    elif "viral" in multiplayer.lower():
        scores["4. Multiplayer/Social"] = 6
        notes["4. Multiplayer/Social"] = "🟡 Viral potential — cần UA strategy mạnh"
    else:
        scores["4. Multiplayer/Social"] = 4
        notes["4. Multiplayer/Social"] = "⚠️ Single-player — khó retain trong thị trường VN multiplayer-heavy"

    # 5. Genre fit (Action/Adventure/Strategy/Sports = top VN)
    top_vn_genres = ["Action", "Adventure", "Strategy", "Sports"]
    mid_vn_genres = ["Casual", "Simulation", "Puzzle", "RPG", "Roleplaying"]
    if genre in top_vn_genres:
        scores["5. Genre Fit"] = 10
        notes["5. Genre Fit"] = f"✅ {genre} = top VN genre (best ranks #2-14)"
    elif genre in mid_vn_genres:
        scores["5. Genre Fit"] = 7
        notes["5. Genre Fit"] = f"🟡 {genre} = mid VN genre (demand OK nhưng không top)"
    else:
        scores["5. Genre Fit"] = 4
        notes["5. Genre Fit"] = f"⚠️ {genre} = niche ở VN"

    # 6. Technical (VN = mostly mid-range phones)
    if "Low" in tech_req:
        scores["6. Hardware Fit"] = 10
        notes["6. Hardware Fit"] = "✅ Low req — tiếp cận 90% thiết bị VN"
    elif "Medium" in tech_req:
        scores["6. Hardware Fit"] = 7
        notes["6. Hardware Fit"] = "🟡 Medium — tiếp cận ~60% thiết bị VN"
    elif "High" in tech_req:
        scores["6. Hardware Fit"] = 4
        notes["6. Hardware Fit"] = "⚠️ High — chỉ flagship, giới hạn reach VN"
    else:
        scores["6. Hardware Fit"] = 1
        notes["6. Hardware Fit"] = "🔴 Flagship only — loại 90% thị trường VN"

    # 7. Localization (VN publisher = auto-localized)
    if has_vn_publisher in vn_pubs:
        scores["7. Localization"] = 9
        notes["7. Localization"] = "✅ VN publisher = localize tiếng Việt (VNG pattern)"
    elif has_vn_publisher == "Self-publish":
        scores["7. Localization"] = 5
        notes["7. Localization"] = "⚠️ Cần tự dịch + QA tiếng Việt"
    else:
        scores["7. Localization"] = 3
        notes["7. Localization"] = "🔴 Chưa có kế hoạch localize"

    # 8. UA Economics (CPI vs LTV)
    if est_ltv > 0 and target_cpi > 0:
        roas = est_ltv / target_cpi
        if roas >= 2:
            scores["8. UA Economics"] = 10
            notes["8. UA Economics"] = f"✅ ROAS {roas:.1f}x — VN CPI thấp, LTV cao = profitable"
        elif roas >= 1.2:
            scores["8. UA Economics"] = 6
            notes["8. UA Economics"] = f"🟡 ROAS {roas:.1f}x — marginal, cần optimize"
        else:
            scores["8. UA Economics"] = 2
            notes["8. UA Economics"] = f"🔴 ROAS {roas:.1f}x — LTV < CPI, lỗ"
    else:
        roas = 0
        scores["8. UA Economics"] = 5
        notes["8. UA Economics"] = "Chưa có CPI/LTV data — assume neutral"

    # Weighted score
    weights = {
        "1. F2P Model": 0.15,
        "2. VN Publisher": 0.25,
        "3. Payment Gateway": 0.15,
        "4. Multiplayer/Social": 0.10,
        "5. Genre Fit": 0.10,
        "6. Hardware Fit": 0.10,
        "7. Localization": 0.10,
        "8. UA Economics": 0.05,
    }
    # Veto: if F2P = 0 → cap total at 30%
    f2p_vetoed = scores["1. F2P Model"] == 0

    weighted_total = sum(scores[k] * weights[k] for k in scores) * 10  # scale to 100
    if f2p_vetoed:
        weighted_total = min(weighted_total, 30)

    # Display
    col_s1, col_s2, col_s3 = st.columns(3)
    col_s1.metric("Weighted Score", f"{weighted_total:.0f}/100")
    col_s2.metric("ROAS", f"{roas:.2f}x" if roas > 0 else "N/A")

    if f2p_vetoed:
        rec, rec_color = "🔴 PASS (VETO: Paid game)", "red"
    elif weighted_total >= 75:
        rec, rec_color = "🟢 PURSUE", "green"
    elif weighted_total >= 50:
        rec, rec_color = "🟡 WATCH", "orange"
    else:
        rec, rec_color = "🔴 PASS", "red"
    col_s3.markdown(
        f"<h3 style='color:{rec_color}; margin:0;'>{rec}</h3>",
        unsafe_allow_html=True,
    )

    # Score table with notes
    score_data = []
    for k in scores:
        score_data.append({
            "Factor": k,
            "Score": f"{scores[k]}/10",
            "Weight": f"{weights[k]*100:.0f}%",
            "Weighted": f"{scores[k]*weights[k]*10:.1f}",
            "Assessment": notes[k],
        })
    st.dataframe(pd.DataFrame(score_data), use_container_width=True, hide_index=True)

    # Bar chart
    fig = px.bar(
        x=list(scores.values()), y=list(scores.keys()), orientation="h",
        range_x=[0, 10], title="Score by factor (8 criteria)",
        color=list(scores.values()),
        color_continuous_scale=["red", "yellow", "green"],
        labels={"x": "Score (0-10)", "y": ""},
    )
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=350)
    st.plotly_chart(fig, use_container_width=True)

    # ---- Competitor benchmark (từ DB) -----------------------------
    st.divider()
    st.subheader("📊 Competitor Benchmark (từ DB)")
    st.caption(f"So sánh với games cùng genre '{genre}' trong DB")

    with get_connection() as conn:
        bench_games = pd.read_sql_query(
            """
            SELECT g.name, g.genre, g.publisher_name, g.source, g.price_usd,
                   f.peak_ccu, f.positive_reviews, f.negative_reviews
            FROM dim_game g
            LEFT JOIN fact_steam_playercounts f ON g.game_id = f.game_id
            WHERE g.genre LIKE ? AND g.source IN ('steam', 'itunes')
            GROUP BY g.game_id
            ORDER BY f.peak_ccu DESC NULLS LAST LIMIT 10
            """,
            conn, params=(f"%{genre}%",),
        )
        gacha_bench = pd.read_sql_query(
            """
            SELECT g.name, g.publisher, r.revenue_usd, r.rank
            FROM fact_gacha_revenue r
            JOIN dim_gacha_game g ON r.game_id = g.game_id
            WHERE r.snapshot_month = (SELECT MAX(snapshot_month) FROM fact_gacha_revenue)
            ORDER BY r.revenue_usd DESC LIMIT 10
            """, conn,
        )

    col_b1, col_b2 = st.columns(2)
    with col_b1:
        st.write(f"**🎮 Steam/iTunes: '{genre}' competitors**")
        if not bench_games.empty:
            display = bench_games[["name", "source", "publisher_name", "peak_ccu"]].copy()
            display["peak_ccu"] = display["peak_ccu"].apply(
                lambda x: f"{x:,.0f}" if pd.notna(x) else "—")
            display.columns = ["Game", "Platform", "Publisher", "Peak CCU"]
            st.dataframe(display, use_container_width=True, hide_index=True)
        else:
            st.info(f"Không có game '{genre}' trong DB.")
    with col_b2:
        st.write("**💰 Top gacha revenue (latest month)**")
        if not gacha_bench.empty:
            display = gacha_bench[["name", "publisher", "revenue_usd", "rank"]].copy()
            display["revenue"] = display["revenue_usd"].apply(lambda x: f"${x/1e6:.1f}M")
            display = display[["rank", "name", "publisher", "revenue"]]
            display.columns = ["Rank", "Game", "Publisher", "Revenue"]
            st.dataframe(display, use_container_width=True, hide_index=True)
        else:
            st.info("Chưa có gacha data.")

    # ---- ROAS projection ----------------------------------------------
    st.divider()
    st.subheader("💰 ROAS Projection")
    if roas > 0:
        col_r1, col_r2, col_r3 = st.columns(3)
        col_r1.metric("ROAS (LTV/CPI)", f"{roas:.2f}x",
                      delta="✅ profitable" if roas > 1 else "❌ loss")
        deal_cost = st.session_state.get("deal_cost", 50000)
        users_needed = int(deal_cost / max(est_ltv - target_cpi, 0.01))
        col_r2.metric("Users to break even", f"{users_needed:,}")
        col_r3.metric("Margin/user", f"${est_ltv - target_cpi:.2f}")
        st.write("**Sensitivity (varying CPI):**")
        cpi_range = [target_cpi * f for f in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]]
        sens_data = []
        for cpi in cpi_range:
            roas_sens = est_ltv / cpi if cpi > 0 else 0
            sens_data.append({
                "CPI": f"${cpi:.2f}", "ROAS": f"{roas_sens:.2f}x",
                "Profitable": "✅" if roas_sens > 1 else "❌",
            })
        st.dataframe(pd.DataFrame(sens_data), use_container_width=True, hide_index=True)
    else:
        st.warning("Cần nhập LTV và CPI để tính ROAS.")


# =========================================================================
# PAGE 8: 💰 Gacha Revenue
# =========================================================================
elif page == PAGES[8]:
    st.title("💰 Gacha Revenue Tracker")
    st.caption(
        "Top 50 mobile gacha revenue hàng tháng — OCR từ r/gachagaming monthly reports. "
        "Source gốc: Sensor Tower mobile estimates (PC/console excluded)."
    )

    @st.cache_data(ttl=300)
    def load_gacha_revenue() -> pd.DataFrame:
        with get_connection() as conn:
            df = pd.read_sql_query(
                """
                SELECT g.name as game, g.game_id, g.icon_url, g.first_seen_month,
                       r.snapshot_month, r.rank, r.revenue_usd, r.scope, r.source
                FROM fact_gacha_revenue r
                JOIN dim_gacha_game g ON r.game_id = g.game_id
                ORDER BY r.snapshot_month DESC, r.rank ASC
                """,
                conn,
            )
        return df

    gacha = load_gacha_revenue()

    if gacha.empty:
        st.warning(
            "❌ Chưa có gacha revenue data. "
            "Mở revenue.ennead.cc → copy HTML table → save file → "
            "`python scripts/manual/parse_gacha_html.py data/manual/gacha_2026-06.html`"
        )
    else:
        # KPIs
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Months tracked", gacha["snapshot_month"].nunique())
        col2.metric("Games tracked", gacha["game_id"].nunique())
        col3.metric("Total facts", len(gacha))
        latest_month = gacha["snapshot_month"].max()
        n_months = gacha["snapshot_month"].nunique()
        latest_total = gacha[gacha["snapshot_month"] == latest_month]["revenue_usd"].sum()
        col4.metric(f"Revenue ({latest_month})",
                    f"${latest_total/1e6:.1f}M")

        st.divider()

        # Filters
        colf1, colf2 = st.columns(2)
        with colf1:
            all_games = sorted(gacha["game"].unique())
            selected_games = st.multiselect(
                "🎮 Games so sánh (trend lines)",
                all_games,
                default=[g for g in ["Genshin Impact", "Honkai: Star Rail",
                                     "Love and Deepspace", "Wuthering Waves"]
                         if g in all_games] or all_games[:5],
            )
        with colf2:
            top_n = st.slider("Top N games (market share)", 5, 30, 10)

        st.subheader("📈 Revenue Trend")
        st.caption("Monthly revenue (USD) cho games đã chọn — xem momentum qua thời gian")
        if selected_games:
            trend_df = (
                gacha[gacha["game"].isin(selected_games)]
                .pivot_table(index="snapshot_month", columns="game",
                             values="revenue_usd", aggfunc="sum")
                .fillna(0)
                .sort_index()
            )
            st.plotly_chart(
                px.line(trend_df, x=trend_df.index, y=trend_df.columns,
                        labels={"value": "Revenue (USD)", "snapshot_month": "Month"},
                        title="Monthly Revenue Trend"),
                use_container_width=True,
            )
        else:
            st.info("Chọn ít nhất 1 game ở filter trên.")

        st.divider()
        st.subheader("📊 Market Share (latest month)")
        latest_data = gacha[gacha["snapshot_month"] == latest_month].nlargest(top_n, "revenue_usd")
        fig_bar = px.bar(
            latest_data, x="revenue_usd", y="game", orientation="h",
            labels={"revenue_usd": "Revenue (USD)", "game": ""},
            title=f"Top {top_n} Gacha Games — {latest_month}",
        )
        fig_bar.update_yaxes(autorange="reversed")
        st.plotly_chart(fig_bar, use_container_width=True)

        st.divider()
        st.subheader("🏆 Latest Month — Full Ranking")
        latest_full = gacha[gacha["snapshot_month"] == latest_month][
            ["rank", "game", "scope", "revenue_usd"]
        ].sort_values("rank")
        display = latest_full.copy()
        display["revenue"] = display["revenue_usd"].apply(lambda x: f"${x/1e6:.2f}M")
        display = display[["rank", "game", "scope", "revenue"]]
        display.columns = ["Rank", "Game", "Scope", "Revenue (est)"]
        st.dataframe(display, use_container_width=True, hide_index=True)
        st.caption(
            f"💡 Data parsed từ HTML table (source: {gacha['source'].iloc[0]}). "
            f"Revenue = mobile estimates (PC/console excluded)."
        )

        st.divider()
        # === Total Revenue by Game (all months tracked) ===
        st.subheader("💰 Total Revenue by Game (all months)")
        st.caption(
            f"Tổng revenue mỗi game từ {gacha['snapshot_month'].min()} → "
            f"{gacha['snapshot_month'].max()} ({n_months} tháng)"
        )
        totals = (
            gacha.groupby(["game", "scope"], as_index=False)
            .agg(
                total_revenue_usd=("revenue_usd", "sum"),
                months_tracked=("snapshot_month", "nunique"),
                avg_monthly=("revenue_usd", "mean"),
                best_month_revenue=("revenue_usd", "max"),
            )
            .sort_values("total_revenue_usd", ascending=False)
        )
        # Filter scope (optional)
        scopes = ["All"] + sorted(totals["scope"].dropna().unique())
        sel_scope_total = st.selectbox(
            "Filter by scope", scopes, key="gacha_total_scope"
        )
        totals_view = totals.copy()
        if sel_scope_total != "All":
            totals_view = totals_view[totals_view["scope"] == sel_scope_total]

        # Top N slider
        top_total = st.slider(
            "Top N games (total revenue)", 10, 100, 25, step=5, key="gacha_total_top"
        )
        totals_view = totals_view.head(top_total)

        # Bar chart
        fig_total = px.bar(
            totals_view, x="total_revenue_usd", y="game", orientation="h",
            labels={"total_revenue_usd": "Total Revenue (USD)", "game": ""},
            title=f"Top {len(totals_view)} Games — Cumulative Revenue ({n_months} months)",
        )
        fig_total.update_yaxes(autorange="reversed")
        st.plotly_chart(fig_total, use_container_width=True)

        # Table with full detail
        table = totals_view.copy()
        table["Total Rev"] = table["total_revenue_usd"].apply(lambda x: f"${x/1e6:.1f}M")
        table["Avg Monthly"] = table["avg_monthly"].apply(lambda x: f"${x/1e6:.1f}M")
        table["Best Month"] = table["best_month_revenue"].apply(lambda x: f"${x/1e6:.1f}M")
        table = table[["game", "scope", "Total Rev", "months_tracked", "Avg Monthly", "Best Month"]]
        table.columns = ["Game", "Scope", "Total Revenue", "Months", "Avg/Month", "Best Month"]
        st.dataframe(table, use_container_width=True, hide_index=True)
        st.caption(
            f"💡 Tổng revenue {n_months} tháng: ${totals_view['total_revenue_usd'].sum()/1e6:.0f}M. "
            f"Avg/Month = tổng chia số tháng track được (games mới ra sẽ ít tháng hơn)."
        )


# =========================================================================
# PAGE 10: 📢 UA PERFORMANCE (CPI/CTR/CVR/ROAS — simulated)
# =========================================================================
elif page == PAGES[9]:
    st.title("📢 UA Performance")
    st.caption("⚠️ SIMULATED data — based on gacha revenue × industry benchmarks. Not actual ad spend.")

    with st.expander("📖 Cách đọc thông số UA"):
        st.markdown("""
        **Tam giác User Acquisition (UA):**
        ```
        Người dùng thấy QUẢNG CÁO (Impression)
                 ↓ Click vào quảng cáo ← CTR đo ở đây
            Vào App Store page
                 ↓ Tải game (Install) ← CVR đo ở đây
            Mở game, chơi
        ```

        | Chỉ số | Ý nghĩa | Công thức | Benchmark tốt |
        |--------|---------|-----------|--------------|
        | **CPI** | Chi phí chạy ads cho 1 lượt tải | Spend ÷ Installs | VN $0.20-2, US $2-5 |
        | **CTR** | % người click sau khi thấy ad | Clicks ÷ Impressions | >2% = creative tốt |
        | **CVR** | % người tải sau khi click | Installs ÷ Clicks | >20% = App Store page tốt |
        | **ROAS** | Tỷ suất hoàn vốn ad spend | LTV ÷ CPI | >1.0x = có lãi |

        **Luồng mua user:** Muốn giảm CPI → tăng CTR (làm creative đẹp hơn) hoặc tăng CVR (tối ưu App Store page).
        """)

    @st.cache_data(ttl=300)
    def load_ua_data() -> pd.DataFrame:
        with get_connection() as conn:
            return pd.read_sql_query("SELECT * FROM sample_ua_campaigns ORDER BY snapshot_date DESC", conn)

    ua = load_ua_data()
    if ua.empty:
        st.warning("Chưa có UA data. Chạy: python scripts/load_external_datasets.py")
        st.stop()

    # KPIs
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Spend", f"${ua['spend_usd'].sum()/1e6:.1f}M")
    col2.metric("Total Installs", f"{ua['installs'].sum():,}")
    col3.metric("Avg CPI", f"${ua['cpi'].mean():.2f}")
    col4.metric("Avg CTR", f"{ua['ctr'].mean()*100:.1f}%")
    col5.metric("Avg CVR", f"{ua['cvr'].mean()*100:.1f}%")

    st.divider()

    # Filters
    colf1, colf2 = st.columns(2)
    with colf1:
        games = sorted(ua["game_name"].unique())
        sel_games = st.multiselect("🎮 Games", games, default=games[:5], key="ua_games")
    with colf2:
        regions = sorted(ua["region"].unique())
        sel_regions = st.multiselect("Region", regions, default=regions, key="ua_regions")

    filtered = ua[ua["game_name"].isin(sel_games) & ua["region"].isin(sel_regions)]

    # === Spend by region ===
    st.subheader("💰 Spend by Region")
    st.caption("💡 Mỹ chi nhiều nhất (budget lớn) nhưng CPI đắt. VN chi ít nhưng mua được nhiều installs (CPI rẻ).")
    spend_by_region = filtered.groupby("region")["spend_usd"].sum().reset_index()
    fig = px.bar(spend_by_region, x="region", y="spend_usd",
                 labels={"spend_usd": "Spend (USD)", "region": ""},
                 title="UA Spend Distribution")
    st.plotly_chart(fig, use_container_width=True)

    # === CPI comparison by region ===
    st.subheader("💵 CPI by Region (benchmark)")
    st.caption("💡 Cột càng ngắn = CPI càng rẻ = mua user rẻ hơn. VN luôn thấp nhất → UA hiệu quả nhất ở VN.")
    cpi_data = filtered.groupby(["region", "game_name"])["cpi"].mean().reset_index()
    fig_cpi = px.bar(cpi_data, x="region", y="cpi", color="game_name",
                     labels={"cpi": "CPI (USD)", "region": ""},
                     title="Cost Per Install by Region",
                     barmode="group")
    st.plotly_chart(fig_cpi, use_container_width=True)

    # === CTR vs CVR scatter ===
    st.subheader("🎯 CTR vs CVR (creative performance)")
    st.caption("💡 Góc trên-phải (CTR cao + CVR cao) = creative tốt. Kích thước chấm = spend. Màu = region.")
    fig_scatter = px.scatter(
        filtered, x="ctr", y="cvr", color="region", size="spend_usd",
        hover_data=["game_name", "ad_network"],
        labels={"ctr": "CTR (click rate)", "cvr": "CVR (install rate)"},
        title="Creative Performance: CTR vs CVR",
    )
    fig_scatter.update_traces(marker=dict(opacity=0.7))
    st.plotly_chart(fig_scatter, use_container_width=True)

    # === ROAS by region ===
    st.divider()
    st.subheader("📈 ROAS D30 by Region")
    st.caption("💡 ROAS > 1.0x = có lãi. VN 7.7x (CPI thấp) = cực kỳ profitable. US/JP cần LTV cao hơn.")
    roas_data = filtered.groupby("region").agg(
        avg_roas=("roas_d30", "mean"),
        avg_cpi=("cpi", "mean"),
        total_spend=("spend_usd", "sum"),
    ).reset_index()
    display = roas_data.copy()
    display["ROAS"] = display["avg_roas"].apply(lambda x: f"{x:.1f}x")
    display["CPI"] = display["avg_cpi"].apply(lambda x: f"${x:.2f}")
    display["Spend"] = display["total_spend"].apply(lambda x: f"${x/1e6:.1f}M")
    display["Profitable"] = display["avg_roas"].apply(lambda x: "✅" if x >= 1 else "❌")
    st.dataframe(display[["region", "ROAS", "CPI", "Spend", "Profitable"]],
                 use_container_width=True, hide_index=True)
    st.caption("💡 VN có ROAS cao nhất do CPI thấp. US/JP cần LTV cao hơn để profitable.")


# =========================================================================
# PAGE 11: 📊 RETENTION & DAU (sample data)
# =========================================================================
elif page == PAGES[10]:
    st.title("📊 Retention & DAU")
    st.caption("⚠️ SAMPLE data — Cookie Cats (real) + simulated DAU/KPIs from benchmarks.")

    with st.expander("📖 Cách đọc thông số Retention & DAU"):
        st.markdown("""
        **Retention (Tỷ lệ giữ chân):** % user quay lại sau N ngày kể từ lần cài đầu.
        - **D1**: quay lại sau 1 ngày → RPG gacha benchmark: **40-50%** (tốt)
        - **D7**: vẫn chơi sau 7 ngày → benchmark: **15-25%**
        - **D30**: vẫn chơi sau 30 ngày → benchmark: **8-12%**
        - D30 < 5% = game giữ chân kém → cần cải thiện content/liveops

        **DAU/MAU:**
        - **DAU** (Daily Active Users): số user unique active mỗi ngày
        - **MAU** (Monthly Active Users): số user unique active mỗi tháng
        - **Stickiness** = DAU ÷ MAU × 100% → >20% = game "dính" cao

        **Monetization:**
        - **ARPU**: doanh thu trung bình mỗi user/tháng (RPG gacha: $1.5-5)
        - **ARPDAU**: doanh thu trung bình mỗi user/ngày
        - **IAP conversion**: % user chuyển từ free → paying (gacha: 5-15%)

        **Quality:**
        - **Crash rate**: % session bị crash → <1% = ổn, >2% = critical
        """)

    # === Cookie Cats: real retention A/B test ===
    st.subheader("🧪 Cookie Cats A/B Test (real data — 90K users)")
    st.caption("Test: di chuyển gate từ level 30 → 40. Kết quả: Gate 30 retention cao hơn → giữ gate 30.")

    @st.cache_data(ttl=300)
    def load_cookie_cats() -> pd.DataFrame:
        with get_connection() as conn:
            return pd.read_sql_query("SELECT * FROM sample_cookie_cats", conn)

    cc = load_cookie_cats()
    if not cc.empty:
        col_cc1, col_cc2, col_cc3 = st.columns(3)
        # Retention by version
        cc_stats = cc.groupby("version").agg(
            d1=("retention_1", "mean"),
            d7=("retention_7", "mean"),
            users=("userid", "count"),
            avg_rounds=("sum_gamerounds", "mean"),
        ).reset_index()
        for _, r in cc_stats.iterrows():
            st.write(f"**Gate {r['version']}:** D1={r['d1']*100:.1f}% | D7={r['d7']*100:.1f}% | "
                     f"{r['users']:,} users | avg {r['avg_rounds']:.0f} rounds")

        # Retention bar chart
        cc_melt = cc_stats.melt(id_vars=["version"], value_vars=["d1", "d7"],
                                var_name="Retention", value_name="Rate")
        fig_cc = px.bar(cc_melt, x="Retention", y="Rate", color="version",
                        barmode="group", title="Retention: Gate 30 vs Gate 40",
                        labels={"Rate": "Retention Rate", "version": "Gate"})
        fig_cc.update_yaxes(tickformat=".0%")
        st.plotly_chart(fig_cc, use_container_width=True)

        # Session distribution
        st.write("**Session distribution (game rounds played):**")
        st.caption("💡 Đa số user chơi 1-20 rounds rồi nghỉ. Đuôi dài bên phải = core gamers/whales.")
        fig_hist = px.histogram(cc[cc["sum_gamerounds"] < 200], x="sum_gamerounds",
                                color="version", nbins=50,
                                labels={"sum_gamerounds": "Game Rounds", "version": "Gate"},
                                title="Session length distribution (capped at 200 rounds)")
        st.plotly_chart(fig_hist, use_container_width=True)

    st.divider()

    # === Simulated DAU/Retention for gacha games ===
    st.subheader("📈 DAU & Retention (simulated — top gacha games)")

    @st.cache_data(ttl=300)
    def load_kpis() -> pd.DataFrame:
        with get_connection() as conn:
            return pd.read_sql_query("SELECT * FROM sample_daily_kpis ORDER BY snapshot_date", conn)

    kpis = load_kpis()
    if not kpis.empty:
        # Game selector
        sel_kpi_games = st.multiselect(
            "🎮 Games", sorted(kpis["game_name"].unique()),
            default=sorted(kpis["game_name"].unique())[:5],
            key="kpi_games",
        )
        kpi_filtered = kpis[kpis["game_name"].isin(sel_kpi_games)]

        # DAU trend
        st.write("**DAU trend (30 days):**")
        st.caption("💡 Đường cao hơn = DAU lớn hơn = game phổ biến hơn. Biến động ±15%/ngày = tự nhiên (cuối tuần cao hơn).")
        fig_dau = px.line(kpi_filtered, x="snapshot_date", y="dau", color="game_name",
                          title="Daily Active Users (simulated)", markers=True,
                          labels={"dau": "DAU", "snapshot_date": "Date"})
        st.plotly_chart(fig_dau, use_container_width=True)

        # Retention comparison
        col_r1, col_r2 = st.columns(2)
        with col_r1:
            st.write("**D1 Retention:**")
            st.caption("💡 Thanh dài hơn = retention tốt hơn. D1 > 45% = tốt (RPG gacha benchmark).")
            latest = kpi_filtered[kpi_filtered["snapshot_date"] == kpi_filtered["snapshot_date"].max()]
            fig_d1 = px.bar(latest.sort_values("d1_retention", ascending=True),
                            x="d1_retention", y="game_name", orientation="h",
                            labels={"d1_retention": "D1 Retention", "game_name": ""},
                            title="D1 Retention by game", range_x=[0, 0.6])
            fig_d1.update_xaxes(tickformat=".0%")
            st.plotly_chart(fig_d1, use_container_width=True)
        with col_r2:
            st.write("**D7/D30 Retention:**")
            st.caption("💡 D30 càng gần D1 = giữ chân tốt. D30 < 5% = cảnh báo.")
            ret_melt = latest.melt(id_vars=["game_name"],
                                   value_vars=["d7_retention", "d30_retention"],
                                   var_name="Metric", value_name="Rate")
            fig_ret = px.bar(ret_melt, x="game_name", y="Rate", color="Metric",
                            barmode="group", title="D7 vs D30 Retention",
                            labels={"game_name": ""})
            fig_ret.update_yaxes(tickformat=".0%")
            fig_ret.update_xaxes(tickangle=45)
            st.plotly_chart(fig_ret, use_container_width=True)

        # Full KPI table
        st.divider()
        st.write("**📋 Full KPI Summary (latest snapshot):**")
        st.caption("💡 DAU = active users/ngày | ARPU = doanh thu/user/tháng | D1/D7/D30 = retention | IAP% = paying user rate | Crash = % session crash")
        display = latest[["game_name", "dau", "mau", "arpu", "arpdau",
                          "d1_retention", "d7_retention", "d30_retention",
                          "iap_conversion_pct", "crash_rate_pct"]].copy()
        display["DAU"] = display["dau"].apply(lambda x: f"{x:,}")
        display["MAU"] = display["mau"].apply(lambda x: f"{x:,}")
        display["ARPU"] = display["arpu"].apply(lambda x: f"${x:.2f}")
        display["ARPDAU"] = display["arpdau"].apply(lambda x: f"${x:.4f}")
        display["D1"] = display["d1_retention"].apply(lambda x: f"{x*100:.0f}%")
        display["D7"] = display["d7_retention"].apply(lambda x: f"{x*100:.0f}%")
        display["D30"] = display["d30_retention"].apply(lambda x: f"{x*100:.0f}%")
        display["IAP %"] = display["iap_conversion_pct"].apply(lambda x: f"{x*100:.1f}%")
        display["Crash"] = display["crash_rate_pct"].apply(lambda x: f"{x:.1f}%")
        st.dataframe(display[["game_name", "DAU", "MAU", "ARPU", "ARPDAU",
                              "D1", "D7", "D30", "IAP %", "Crash"]],
                     use_container_width=True, hide_index=True)


# ---- Footer --------------------------------------------------------------
st.sidebar.divider()
st.sidebar.caption(
    "💡 **Tip:** Chạy `python scripts/run_daily.py` rồi nhấn 🔄 Refresh"
)
