"""
NBA Betting Intelligence Dashboard — Streamlit UI.

Fetches games from BallDontLie, markets from Kalshi, team stats from nba_api,
and displays interactive betting edges with adjustable metric weights.
"""

import streamlit as st
import pandas as pd
from datetime import date, datetime, timezone, timedelta
from dotenv import load_dotenv
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# Load environment variables from .env
load_dotenv()

from config import CATEGORY_COLORS, METRIC_WEIGHTS_CONFIG
from fetch import fetch_games, fetch_kalshi_markets, fetch_team_stats
from model import build_all_rows


# ============================================================================
# PAGE CONFIG & STYLING
# ============================================================================

st.set_page_config(
    page_title="Kalshi Betting Dashboard",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.stApp { background: #0d1117; color: #e6edf3; }
section[data-testid="stSidebar"] { background: #161b22 !important; border-right: 1px solid #30363d; }
section[data-testid="stSidebar"] * { color: #c9d1d9; }
#MainMenu, footer, header { visibility: hidden; }
.stDataFrame { font-size: 0.85rem; }
.stMetric { text-align: center; }
/* Slider styling — blue accent */
.stSlider [role="slider"] { accent-color: #1f6feb !important; }
.stSlider [data-testid="stSliderThumb"] { background-color: #1f6feb !important; }
</style>
""", unsafe_allow_html=True)


# ============================================================================
# MAIN APPLICATION
# ============================================================================

def main():
    # ========== SIDEBAR ==========
    with st.sidebar:
        st.title("⚙️ Settings")
        if st.button("🔄 Refresh All", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        # Timestamp (Eastern, DST-aware)
        et_tz = ZoneInfo("America/New_York")
        now_et = datetime.now(et_tz)
        st.caption(f"Last update: {now_et.strftime('%H:%M:%S %Z')}")

    # ========== MAIN CONTENT ==========
    st.title("🎯 NBA Betting Intelligence")

    # ========== TABS: METRIC WEIGHTS + HELP ==========
    tab_metrics, tab_help = st.tabs(["Metric Weights", "Help"])

    model_weights = {}

    with tab_metrics:
        st.subheader("Adjust Your Edge Model")
        st.caption("Change metric weights to update prediction edges across all markets")

        col1, col2, col3 = st.columns(3)

        metric_keys = list(METRIC_WEIGHTS_CONFIG.keys())

        for i, key in enumerate(metric_keys):
            config = METRIC_WEIGHTS_CONFIG[key]
            col = [col1, col2, col3][i % 3]

            with col:
                weight = st.slider(
                    config['label'],
                    0.0, 2.0,
                    config['default'],
                    0.1,
                    label_visibility="collapsed"
                )
                st.caption(config['label'])
                model_weights[key] = weight

    with tab_help:
        st.subheader("About Metric Weights")
        st.write("Each metric weight controls how much that factor influences your win probability prediction.")
        st.write("")

        for key, config in METRIC_WEIGHTS_CONFIG.items():
            with st.expander(config['label']):
                st.write(config['description'])
                st.divider()
                default_str = f"{config['default']}"
                st.caption(f"**Range:** 0.0 (ignore) to 2.0 (double weight) | **Default:** {default_str}")

    st.divider()

    # ========== DATA FETCHING ==========
    with st.spinner("📡 Loading markets..."):
        today = date.today()
        games = fetch_games(today)
        kalshi_by_type = fetch_kalshi_markets()
        team_stats = fetch_team_stats()

    if not games:
        st.warning("❌ No NBA games found for today.")
        return

    if not any(kalshi_by_type.values()):
        st.warning("❌ No Kalshi markets found.")
        return

    # ========== BUILD MASTER DATAFRAME ==========
    df = build_all_rows(games, kalshi_by_type, team_stats, model_weights)

    if df.empty:
        st.warning("⚠️ No markets matched to games. Check team abbreviations.")
        return

    # Update sidebar with stats
    with st.sidebar:
        st.divider()
        st.caption(f"📊 **Game Stats**")
        st.caption(f"Games: {len(games)}")
        st.caption(f"Markets: {len(df)}")
        st.caption(f"Team stats: {len(team_stats)}/30")

    # ========== SUMMARY METRICS ==========
    col1, col2, col3 = st.columns(3)
    col1.metric("Games Today", len(games))
    col2.metric("Markets Loaded", len(df))
    col3.metric("Positive Edge", (df['edge'] > 0).sum())

    st.divider()

    # ========== TOP PICKS ==========
    st.subheader("⭐ Today's Top Picks")

    top_picks = df[df['category'].isin(['HOMERUN', 'UNDERVALUED', 'UNDERDOG'])].nlargest(3, 'edge')

    if not top_picks.empty:
        cols = st.columns(len(top_picks))
        for col, (_, row) in zip(cols, top_picks.iterrows()):
            with col:
                color = CATEGORY_COLORS.get(row['category'], '#30363d')
                st.markdown(f"""
                <div style="border-left: 4px solid {color}; padding: 12px; background: #161b22; border-radius: 4px;">
                <strong>{row['game_label']}</strong><br/>
                <small style="color: #8b949e;">{row['title'][:50]}...</small><br/>
                <span style="color: {color}; font-weight: bold;">{row['edge']:+.1%}</span> Edge<br/>
                <small>{row['kalshi_prob']:.0%} @ {row['american_odds']}</small>
                </div>
                """, unsafe_allow_html=True)
    else:
        st.info("No high-conviction opportunities today.")

    st.divider()

    # ========== ALL GAMES ==========
    st.subheader("📊 All Games")

    # Group by game
    for game_id, game_df in df.groupby('game_id'):
        game_label = game_df.iloc[0]['game_label']

        with st.expander(f"{game_label}", expanded=False):
            # Tabs for moneyline, spread, total
            tab_ml, tab_sp, tab_tot = st.tabs(["Moneyline", "Spread", "Total"])

            for tab, market_type in [(tab_ml, "moneyline"), (tab_sp, "spread"), (tab_tot, "total")]:
                with tab:
                    market_df = game_df[game_df['market_type'] == market_type]
                    if market_df.empty:
                        st.caption("No markets for this type.")
                        continue

                    display_df = market_df[[
                        "title", "kalshi_prob", "american_odds", "model_prob", "edge", "category", "volume"
                    ]].copy()

                    display_df.columns = ["Contract", "Kalshi %", "Odds", "Model %", "Edge %", "Category", "Volume"]
                    display_df["Kalshi %"] = display_df["Kalshi %"].apply(lambda x: f"{x:.1%}")
                    display_df["Model %"] = display_df["Model %"].apply(lambda x: f"{x:.1%}")
                    display_df["Edge %"] = display_df["Edge %"].apply(lambda x: f"{x:+.1%}")
                    display_df["Volume"] = display_df["Volume"].apply(lambda x: f"{x:,}" if x else "0")

                    st.dataframe(display_df, use_container_width=True, hide_index=True)

    st.divider()

    # ========== LOW EDGE / NO PROFIT ==========
    low_edge_df = df[df['category'] == 'LOW EDGE']

    with st.expander(f"📉 Low Edge / No Profit ({len(low_edge_df)} markets)", expanded=False):
        if not low_edge_df.empty:
            display_df = low_edge_df[[
                "game_label", "title", "kalshi_prob", "model_prob", "edge", "category", "volume"
            ]].copy()

            display_df.columns = ["Game", "Contract", "Kalshi %", "Model %", "Edge %", "Category", "Volume"]
            display_df["Kalshi %"] = display_df["Kalshi %"].apply(lambda x: f"{x:.1%}")
            display_df["Model %"] = display_df["Model %"].apply(lambda x: f"{x:.1%}")
            display_df["Edge %"] = display_df["Edge %"].apply(lambda x: f"{x:+.1%}")
            display_df["Volume"] = display_df["Volume"].apply(lambda x: f"{x:,}" if x else "0")

            st.dataframe(display_df, use_container_width=True, hide_index=True)
        else:
            st.caption("All markets show positive or meaningful edge!")


if __name__ == "__main__":
    main()
