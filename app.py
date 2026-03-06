"""
NBA Betting Intelligence Dashboard
XGBoost 68.9% accuracy model (kyleskom/NBA-ML) + Kalshi markets + EV + Kelly Criterion
"""

import streamlit as st
import pandas as pd
from datetime import date, datetime
from dotenv import load_dotenv
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

load_dotenv()

from config import CATEGORY_COLORS, METRIC_WEIGHTS_CONFIG
from fetch import fetch_games, fetch_kalshi_markets, fetch_nba_stats, load_schedule
from model import build_all_rows

# ============================================================================
# PAGE CONFIG
# ============================================================================

st.set_page_config(
    page_title="NBA Betting Intelligence",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.stApp { background: #0d1117; color: #e6edf3; }
section[data-testid="stSidebar"] { background: #161b22 !important; border-right: 1px solid #30363d; }
section[data-testid="stSidebar"] * { color: #c9d1d9; }
#MainMenu, footer, header { visibility: hidden; }
/* Blue slider accent */
.stSlider [role="slider"] { accent-color: #1f6feb !important; }
</style>
""", unsafe_allow_html=True)


# ============================================================================
# HELPERS
# ============================================================================

def render_market_table(df: pd.DataFrame):
    """Render a Kalshi market DataFrame as a styled dataframe."""
    if df.empty:
        st.caption("No markets.")
        return
    disp = df[[
        "title", "kalshi_prob", "american_odds",
        "model_prob", "edge", "ev", "kelly", "category", "volume"
    ]].copy()
    disp.columns = [
        "Contract", "Kalshi %", "Odds",
        "Model %", "Edge %", "EV ($100)", "Kelly %", "Category", "Volume"
    ]
    disp["Kalshi %"] = disp["Kalshi %"].apply(lambda x: f"{x:.1%}")
    disp["Model %"]  = disp["Model %"].apply(lambda x: f"{x:.1%}")
    disp["Edge %"]   = disp["Edge %"].apply(lambda x: f"{x:+.1%}")
    disp["EV ($100)"] = disp["EV ($100)"].apply(lambda x: f"${x:+.2f}")
    disp["Kelly %"]  = disp["Kelly %"].apply(lambda x: f"{x:.1f}%")
    disp["Volume"]   = disp["Volume"].apply(lambda x: f"{x:,}" if x else "0")
    st.dataframe(disp, use_container_width=True, hide_index=True)


# ============================================================================
# MAIN
# ============================================================================

def main():
    # ---------- SIDEBAR ----------
    with st.sidebar:
        st.title("⚙️ Settings")
        if st.button("🔄 Refresh All", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        et_tz = ZoneInfo("America/New_York")
        now_et = datetime.now(et_tz)
        st.caption(f"Last update: {now_et.strftime('%H:%M:%S %Z')}")
        st.caption("Model: XGBoost 68.9% accuracy")

    # ---------- TITLE ----------
    st.title("🎯 NBA Betting Intelligence")
    st.caption("Powered by XGBoost ML model (68.9% accuracy) · Kalshi prediction markets · EV + Kelly sizing")

    # ---------- METRIC WEIGHT SLIDERS ----------
    with st.expander("⚙️ Model Tilts", expanded=True):
        st.caption("Adjust these to tune how much weight the XGBoost prediction carries vs Kalshi market price")
        cols = st.columns(len(METRIC_WEIGHTS_CONFIG))
        weights = {}
        for col, (key, cfg) in zip(cols, METRIC_WEIGHTS_CONFIG.items()):
            with col:
                weights[key] = st.slider(
                    cfg["label"],
                    0.0, 2.0 if key == "w_xgb" else 1.0,
                    cfg["default"],
                    0.05 if key == "w_xgb" else 0.1,
                    help=cfg["description"],
                )

    st.divider()

    # ---------- DATA FETCH ----------
    with st.spinner("📡 Loading games, markets, and model data..."):
        today = date.today()
        games       = fetch_games(today)
        kalshi      = fetch_kalshi_markets()
        stats_df    = fetch_nba_stats()
        schedule_df = load_schedule()

    if not games:
        st.warning("❌ No NBA games today.")
        return
    if not any(kalshi.values()):
        st.warning("❌ No Kalshi markets found.")
        return

    # ---------- BUILD MASTER TABLE ----------
    df = build_all_rows(games, kalshi, stats_df, schedule_df, weights)

    if df.empty:
        st.warning("⚠️ No markets matched to today's games.")
        return

    # Sidebar stats
    with st.sidebar:
        st.divider()
        st.caption(f"🏀 Games: {len(games)}")
        st.caption(f"📈 Markets: {len(df)}")
        st.caption(f"✅ +EV bets: {(df['ev'] > 0).sum()}")

    # ---------- SUMMARY METRICS ----------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Games Today", len(games))
    c2.metric("Kalshi Markets", len(df))
    c3.metric("Positive Edge", (df["edge"] > 0).sum())
    c4.metric("Positive EV", (df["ev"] > 0).sum())

    st.divider()

    # ---------- TOP PICKS ----------
    st.subheader("⭐ Today's Top Picks")

    top = df[df["category"].isin(["HOMERUN", "UNDERVALUED", "UNDERDOG"])].nlargest(3, "ev")

    if not top.empty:
        cols = st.columns(len(top))
        for col, (_, row) in zip(cols, top.iterrows()):
            color = CATEGORY_COLORS.get(row["category"], "#30363d")
            ev_sign = "+" if row["ev"] >= 0 else ""
            with col:
                st.markdown(f"""
                <div style="border-left:4px solid {color};padding:14px;background:#161b22;border-radius:6px;margin-bottom:8px;">
                <div style="font-size:0.7rem;color:#8b949e;font-weight:700;letter-spacing:0.06em;">{row['category']}</div>
                <div style="font-weight:700;font-size:1rem;">{row['game_label']}</div>
                <div style="font-size:0.8rem;color:#8b949e;margin:4px 0;">{row['title'][:55]}...</div>
                <div style="display:flex;gap:16px;margin-top:8px;">
                  <span style="color:{color};font-weight:700;font-size:1.1rem;">{row['edge']:+.1%}</span>
                  <span style="color:#3fb950;font-weight:700;">EV {ev_sign}{row['ev']:.1f}</span>
                  <span style="color:#58a6ff;">Kelly {row['kelly']:.1f}%</span>
                </div>
                <div style="font-size:0.75rem;color:#8b949e;margin-top:4px;">
                  Kalshi {row['kalshi_prob']:.0%} · Model {row['model_prob']:.0%} · {row['american_odds']}
                </div>
                </div>
                """, unsafe_allow_html=True)
    else:
        st.info("No high-conviction opportunities detected today.")

    st.divider()

    # ---------- ALL GAMES ----------
    st.subheader("📊 All Games")

    for game_id, gdf in df.groupby("game_id"):
        label = gdf.iloc[0]["game_label"]
        status_icon = "🔴 LIVE" if games[0]["status"] == "in_progress" else ""
        with st.expander(f"{label}  {status_icon}", expanded=False):
            tab_ml, tab_sp, tab_tot = st.tabs(["Moneyline", "Spread", "Total"])
            for tab, mtype in [(tab_ml, "moneyline"), (tab_sp, "spread"), (tab_tot, "total")]:
                with tab:
                    render_market_table(gdf[gdf["market_type"] == mtype])

    st.divider()

    # ---------- LOW EDGE ----------
    low_df = df[df["category"] == "LOW EDGE"]
    with st.expander(f"📉 Low Edge / No Profit ({len(low_df)})", expanded=False):
        if not low_df.empty:
            render_market_table(low_df)
        else:
            st.caption("All markets show meaningful edge today!")

    # ---------- MODEL DISCLAIMER ----------
    with st.sidebar:
        st.divider()
        with st.expander("ℹ️ Model Info"):
            st.caption("**XGBoost Moneyline**: 68.9% accuracy")
            st.caption("**XGBoost O/U**: 50.1% accuracy")
            st.caption("**EV**: Expected value per $100 bet")
            st.caption("**Kelly %**: Bankroll % to wager")
            st.caption("Training data: 2012–2026 NBA seasons")


if __name__ == "__main__":
    main()
