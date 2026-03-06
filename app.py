"""
NBA Betting Intelligence Dashboard — Streamlit App

Uses BallDontLie API for games + nba_api for team stats + Kalshi for prediction market odds.
Features interactive metric weight adjustments and intelligent bet categorization.
"""

import streamlit as st
import requests
import pandas as pd
import numpy as np
import time
import math
import re
import os
from datetime import datetime, timedelta, timezone, date
from typing import Optional, Dict, List
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

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
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.stApp { background: #0d1117; color: #e6edf3; }
section[data-testid="stSidebar"] { background: #161b22 !important; border-right: 1px solid #30363d; }
section[data-testid="stSidebar"] * { color: #c9d1d9; }
#MainMenu, footer, header { visibility: hidden; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; }
th { font-size: 0.65rem; font-weight: 700; color: #8b949e; text-transform: uppercase;
     letter-spacing: 0.06em; padding: 8px 10px; border-bottom: 2px solid #30363d; text-align: left; }
td { font-size: 0.85rem; padding: 10px; border-bottom: 1px solid #21262d; vertical-align: middle; }
tr:last-child td { border-bottom: none; }
.weight-slider { margin: 12px 0; }
.green { color: #3fb950; font-weight: 700; }
.red { color: #f85149; font-weight: 700; }
.grey { color: #8b949e; }
.yellow { color: #d29922; font-weight: 700; }
/* Slider styling */
.stSlider > div > div > div > div { accent-color: #1f6feb !important; }
.stSlider [data-testid="stSliderThumb"] { background-color: #1f6feb !important; }
.stSlider [data-testid="stSliderTrackBg"] { background-color: #30363d !important; }
.stSlider [data-testid="stSliderTrackFill"] { background-color: #1f6feb !important; }
</style>
""", unsafe_allow_html=True)

# ============================================================================
# CONSTANTS
# ============================================================================

KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
BALLDONTLIE_API_BASE = "https://api.balldontlie.io/v1"

# Team abbreviation map
TEAM_ABBREVS = {
    "ATL": "ATL", "BOS": "BOS", "BKN": "BKN", "CHA": "CHA",
    "CHI": "CHI", "CLE": "CLE", "DAL": "DAL", "DEN": "DEN",
    "DET": "DET", "GSW": "GSW", "HOU": "HOU", "IND": "IND",
    "LAC": "LAC", "LAL": "LAL", "MEM": "MEM", "MIA": "MIA",
    "MIL": "MIL", "MIN": "MIN", "NOP": "NOP", "NYK": "NYK",
    "OKC": "OKC", "ORL": "ORL", "PHI": "PHI", "PHX": "PHX",
    "POR": "POR", "SAC": "SAC", "SAS": "SAS", "TOR": "TOR",
    "UTA": "UTA", "WAS": "WAS",
}

# Category colors
CATEGORY_COLORS = {
    "HOMERUN": "#f85149",
    "UNDERVALUED": "#3fb950",
    "UNDERDOG": "#58a6ff",
    "SHARP": "#d29922",
    "FADE": "#6e7681",
    "LOW EDGE": "#30363d",
}

# Metric weight explanations
METRIC_WEIGHTS = {
    "w_net": {
        "label": "📊 Net Rating",
        "description": "Team strength indicator: offensive rating minus defensive rating. Higher = better team. Adjust UP if you believe team quality is predictive of outcomes.",
    },
    "w_form": {
        "label": "🔥 Recent Form",
        "description": "Hot/cold momentum over last 10 games. Adjust UP if you think current form beats historical strength, DOWN if form is noise.",
    },
    "w_hca": {
        "label": "🏠 Home Court",
        "description": "Home team inherent advantage (~2.5 pts/game). Adjust UP for more home bias, DOWN to ignore crowd effects.",
    },
    "w_pace": {
        "label": "⚡ Pace Variance",
        "description": "Game tempo impact on win probability spread. Fast-paced games have higher variance. Adjust UP if pace affects predictability.",
    },
    "w_rest": {
        "label": "😴 Rest Advantage",
        "description": "Back-to-back fatigue penalty & rest day bonus. Adjust UP if B2B/rest days are significant, DOWN if teams manage load well.",
    },
}

# ============================================================================
# DATA FETCHING LAYER
# ============================================================================

@st.cache_data(ttl=300)
def fetch_balldontlie_games(game_date: date) -> List[Dict]:
    """Fetch games for a specific date from BallDontLie API."""
    try:
        api_key = os.environ.get("BALLDONTLIE_API_KEY", "")
        headers = {"Authorization": api_key} if api_key else {}

        url = f"{BALLDONTLIE_API_BASE}/games"
        params = {"dates[]": game_date.isoformat()}

        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        games = []
        for raw in data.get("data", []):
            home = raw.get("home_team", {})
            away = raw.get("visitor_team", {})

            home_abbr = home.get("abbreviation", "")
            away_abbr = away.get("abbreviation", "")
            status = raw.get("status", "")

            # Parse game time
            game_dt_str = raw.get("datetime") or raw.get("date", "")
            if game_dt_str and "T" in game_dt_str:
                try:
                    game_time = datetime.fromisoformat(game_dt_str.replace("Z", "+00:00"))
                except:
                    game_time = datetime.now(timezone.utc)
            else:
                game_time = datetime.now(timezone.utc)

            # Map status
            if status == "Final":
                game_status = "final"
            elif status in ("In Progress", "1st Qtr", "2nd Qtr", "3rd Qtr", "4th Qtr", "Halftime", "OT"):
                game_status = "in_progress"
            else:
                game_status = "scheduled"

            if home_abbr and away_abbr and game_status != "final":
                games.append({
                    "game_id": raw.get("id", ""),
                    "away_abbr": away_abbr,
                    "home_abbr": home_abbr,
                    "away_name": away.get("full_name", ""),
                    "home_name": home.get("full_name", ""),
                    "game_time_et": game_time.isoformat(),
                    "status": game_status,
                })

        return games
    except Exception as e:
        st.sidebar.warning(f"⚠️ BallDontLie fetch failed: {str(e)[:50]}")
        return []


@st.cache_data(ttl=60)
def fetch_all_kalshi_nba() -> Dict[str, List[Dict]]:
    """Fetch all three Kalshi NBA market types."""
    results = {}
    for market_type, series_prefix in [("moneyline", "KXNBAGAME"), ("spread", "KXNBASPREAD"), ("total", "KXNBATOTAL")]:
        try:
            url = f"{KALSHI_API_BASE}/markets"
            params = {"status": "open", "series_ticker": series_prefix, "limit": 500}
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            markets = []
            for mkt in data.get("markets", []):
                yes_bid = (mkt.get("yes_bid", 0) or 0) / 100.0
                yes_ask = (mkt.get("yes_ask", 0) or 0) / 100.0
                last_price = (mkt.get("last_price", 0) or 0) / 100.0

                if yes_bid <= 0 and yes_ask <= 0 and last_price <= 0:
                    continue

                markets.append({
                    "ticker": mkt.get("ticker", ""),
                    "title": mkt.get("title", ""),
                    "event_ticker": mkt.get("event_ticker", ""),
                    "yes_bid": yes_bid,
                    "yes_ask": yes_ask,
                    "last_price": last_price,
                    "volume": mkt.get("volume", 0),
                })

            results[market_type] = markets
        except Exception as e:
            st.sidebar.warning(f"⚠️ Kalshi {series_prefix} failed: {str(e)[:40]}")
            results[market_type] = []

    return results


@st.cache_data(ttl=3600)
def fetch_nba_stats_simple() -> Dict[str, Dict]:
    """Fetch basic NBA team stats. Returns empty dict on failure (graceful fallback)."""
    try:
        from nba_api.stats.endpoints import teamestimatedmetrics, leaguedashteamstats
        from nba_api.stats.static import teams

        stats_dict = {}

        # Get team IDs
        all_teams = teams.get_teams()
        team_id_map = {t["abbreviation"]: t["id"] for t in all_teams}

        # Dynamic season
        now = datetime.now()
        season = f"{now.year}-{now.year + 1}"

        # Fetch season metrics (E_NET_RATING, E_PACE, etc.)
        time.sleep(0.6)
        try:
            metrics = teamestimatedmetrics.TeamEstimatedMetrics(season=season).get_data_frames()[0]

            for _, row in metrics.iterrows():
                abbr = row.get("TEAM_ABBREVIATION", "")
                if abbr:
                    stats_dict[abbr] = {
                        "off_rating": row.get("E_OFF_RATING"),
                        "def_rating": row.get("E_DEF_RATING"),
                        "net_rating": row.get("E_NET_RATING"),
                        "pace": row.get("E_PACE"),
                        "last10_winpct": 0.5,  # Will be updated below
                    }
        except ConnectionError as ce:
            st.sidebar.warning(f"⚠️ nba_api connection issue — using neutral stats. Retry in a moment.")
            return {}

        # Fetch last 10 games record to compute win%
        time.sleep(0.6)
        try:
            dashboard = leaguedashteamstats.LeagueDashTeamStats(
                season=season,
                last_n_games=10,
                measure_type_detailed_defense="Base"
            ).get_data_frames()[0]

            for _, row in dashboard.iterrows():
                abbr = row.get("TEAM_ABBREVIATION", "")
                if abbr and abbr in stats_dict:
                    wins = row.get("W", 0) or 0
                    losses = row.get("L", 0) or 0
                    total = wins + losses
                    if total > 0:
                        stats_dict[abbr]["last10_winpct"] = wins / total
        except Exception as e:
            pass  # Fail silently, use 0.5 default

        return stats_dict

    except Exception as e:
        st.sidebar.warning(f"⚠️ nba_api stats failed: {str(e)[:50]}")
        return {}


# ============================================================================
# PARSING & UTILITIES
# ============================================================================

def kalshi_mid_price(mkt: Dict) -> float:
    """Extract midpoint probability from Kalshi market."""
    yes_bid = mkt.get("yes_bid", 0)
    yes_ask = mkt.get("yes_ask", 0)
    last_price = mkt.get("last_price", 0)

    if yes_bid > 0 and yes_ask > 0:
        mid = (yes_bid + yes_ask) / 2.0
    elif last_price > 0:
        mid = last_price
    else:
        return 0.5

    return max(0.01, min(0.99, mid))


def parse_kalshi_nba_ticker(ticker: str) -> Optional[Dict]:
    """Parse Kalshi NBA ticker."""
    ml_pattern = r"KXNBAGAME-\d{2}[A-Z]{3}\d{2}([A-Z]{2,3})([A-Z]{2,3})-([A-Z]{2,3})$"
    ml_match = re.match(ml_pattern, ticker)
    if ml_match:
        away_abbr, home_abbr, ticker_team = ml_match.groups()
        return {"market_type": "moneyline", "away_abbr": away_abbr, "home_abbr": home_abbr, "line": None}

    sp_pattern = r"KXNBASPREAD-\d{2}[A-Z]{3}\d{2}([A-Z]{2,3})([A-Z]{2,3})-([A-Z]{2,3})(\d+)$"
    sp_match = re.match(sp_pattern, ticker)
    if sp_match:
        away_abbr, home_abbr, ticker_team, spread_str = sp_match.groups()
        spread = float(spread_str) / 2.0
        return {"market_type": "spread", "away_abbr": away_abbr, "home_abbr": home_abbr, "line": spread}

    tot_pattern = r"KXNBATOTAL-\d{2}[A-Z]{3}\d{2}([A-Z]{2,3})([A-Z]{2,3})-(\d+)$"
    tot_match = re.match(tot_pattern, ticker)
    if tot_match:
        away_abbr, home_abbr, total_str = tot_match.groups()
        total = float(total_str) / 2.0
        return {"market_type": "total", "away_abbr": away_abbr, "home_abbr": home_abbr, "line": total}

    return None


def prob_to_american(p: float) -> str:
    """Convert probability to American odds."""
    if p >= 0.99:
        return "-10000"
    if p <= 0.01:
        return "+10000"

    if p > 0.5:
        odds = -round((p / (1 - p)) * 100)
        return str(odds)
    else:
        odds = round(((1 - p) / p) * 100)
        return f"+{odds}"


# ============================================================================
# PROBABILITY MODEL
# ============================================================================

def compute_win_probability(
    home_stats: Dict,
    away_stats: Dict,
    w_net: float = 1.0,
    w_form: float = 1.0,
    w_hca: float = 1.0,
    w_pace: float = 0.5,
    w_rest: float = 0.5,
    kalshi_market_prob: Optional[float] = None,
) -> float:
    """Compute P(home wins) using multi-factor Gaussian model with optional Kalshi calibration."""
    BASE_SIGMA = 11.0
    HCA_POINTS = 2.5

    if not home_stats or not away_stats:
        return kalshi_market_prob if kalshi_market_prob else 0.5

    home_net = home_stats.get("net_rating") or 0
    away_net = away_stats.get("net_rating") or 0
    home_form = home_stats.get("last10_winpct") or 0.5
    away_form = away_stats.get("last10_winpct") or 0.5
    home_pace = home_stats.get("pace") or 98
    away_pace = away_stats.get("pace") or 98

    net_diff = home_net - away_net
    form_diff = home_form - away_form
    rest_diff = 0

    point_spread = (
        net_diff * w_net +
        HCA_POINTS * w_hca +
        form_diff * 12 * w_form +
        rest_diff * 1.5 * w_rest
    )

    avg_pace = (home_pace + away_pace) / 2.0
    pace_norm = max(-1.0, min(1.0, (avg_pace - 98.0) / 12.0))
    sigma = BASE_SIGMA * (1.0 + pace_norm * 0.15 * w_pace)

    z = point_spread / (sigma * math.sqrt(2))
    p_win = 0.5 + 0.5 * math.erf(z)

    # Apply Kalshi calibration shrinkage if available (80% model, 20% Kalshi prior)
    if kalshi_market_prob is not None:
        p_win = 0.8 * p_win + 0.2 * kalshi_market_prob

    return max(0.01, min(0.99, p_win))


def compute_spread_probability(home_stats: Dict, away_stats: Dict, spread_line: float, kalshi_market_prob: Optional[float] = None, **weights) -> float:
    """Compute P(home covers spread) with optional Kalshi calibration."""
    if not home_stats or not away_stats:
        return kalshi_market_prob if kalshi_market_prob else 0.5

    BASE_SIGMA = 11.0
    HCA_POINTS = 2.5

    home_net = home_stats.get("net_rating") or 0
    away_net = away_stats.get("net_rating") or 0
    home_pace = home_stats.get("pace") or 98
    away_pace = away_stats.get("pace") or 98

    net_diff = home_net - away_net

    point_spread = (
        net_diff * weights.get("w_net", 1.0) +
        HCA_POINTS * weights.get("w_hca", 1.0) -
        spread_line
    )

    avg_pace = (home_pace + away_pace) / 2.0
    pace_norm = max(-1.0, min(1.0, (avg_pace - 98.0) / 12.0))
    sigma = BASE_SIGMA * (1.0 + pace_norm * 0.15 * weights.get("w_pace", 0.5))

    z = point_spread / (sigma * math.sqrt(2))
    p_cover = 0.5 + 0.5 * math.erf(z)

    # Apply Kalshi calibration shrinkage if available
    if kalshi_market_prob is not None:
        p_cover = 0.8 * p_cover + 0.2 * kalshi_market_prob

    return max(0.01, min(0.99, p_cover))


def compute_total_probability(home_stats: Dict, away_stats: Dict, total_line: float, kalshi_market_prob: Optional[float] = None, **weights) -> float:
    """Compute P(total > total_line) with optional Kalshi calibration."""
    if not home_stats or not away_stats:
        return kalshi_market_prob if kalshi_market_prob else 0.5

    home_off = home_stats.get("off_rating") or 110
    away_off = away_stats.get("off_rating") or 110
    home_pace = home_stats.get("pace") or 98
    away_pace = away_stats.get("pace") or 98

    avg_pace = (home_pace + away_pace) / 2.0
    home_pts = (home_off / 100.0) * avg_pace
    away_pts = (away_off / 100.0) * avg_pace
    pred_total = home_pts + away_pts

    sigma_total = 12.0
    z = (pred_total - total_line) / (sigma_total * math.sqrt(2))
    p_over = 0.5 + 0.5 * math.erf(z)

    # Apply Kalshi calibration shrinkage if available
    if kalshi_market_prob is not None:
        p_over = 0.8 * p_over + 0.2 * kalshi_market_prob

    return max(0.01, min(0.99, p_over))


# ============================================================================
# BET CLASSIFICATION
# ============================================================================

def classify_bet(edge: float, model_prob: float, kalshi_prob: float) -> str:
    """Classify bet by edge."""
    if edge >= 0.10 and model_prob >= 0.65:
        return "HOMERUN"
    elif edge >= 0.05 and edge > 0:
        return "UNDERVALUED"
    elif kalshi_prob <= 0.38 and model_prob >= 0.48:
        return "UNDERDOG"
    elif 0.03 <= edge < 0.05:
        return "SHARP"
    elif edge < 0:
        return "FADE"
    else:
        return "LOW EDGE"


def build_all_rows(
    espn_games: List[Dict],
    kalshi_by_type: Dict[str, List[Dict]],
    team_stats: Dict[str, Dict],
    model_weights: Dict[str, float],
) -> pd.DataFrame:
    """Build master DataFrame of markets with edges."""
    rows = []

    for game in espn_games:
        game_id = game["game_id"]
        home_abbr = game["home_abbr"]
        away_abbr = game["away_abbr"]
        game_label = f"{away_abbr} @ {home_abbr}"

        home_stats = team_stats.get(home_abbr, {})
        away_stats = team_stats.get(away_abbr, {})

        for market_type, markets in kalshi_by_type.items():
            for mkt in markets:
                parsed = parse_kalshi_nba_ticker(mkt["ticker"])
                if not parsed:
                    continue

                if (parsed["away_abbr"] != away_abbr or parsed["home_abbr"] != home_abbr):
                    continue

                kalshi_prob = kalshi_mid_price(mkt)

                if market_type == "moneyline":
                    model_prob = compute_win_probability(home_stats, away_stats, kalshi_market_prob=kalshi_prob, **model_weights)
                elif market_type == "spread":
                    model_prob = compute_spread_probability(home_stats, away_stats, parsed["line"], kalshi_market_prob=kalshi_prob, **model_weights)
                elif market_type == "total":
                    model_prob = compute_total_probability(home_stats, away_stats, parsed["line"], kalshi_market_prob=kalshi_prob, **model_weights)
                else:
                    model_prob = 0.5

                edge = model_prob - kalshi_prob
                category = classify_bet(edge, model_prob, kalshi_prob)
                if market_type == "total" and edge < 0.08:
                    category = "LOW EDGE"

                rows.append({
                    "game_id": game_id,
                    "game_label": game_label,
                    "market_type": market_type,
                    "ticker": mkt["ticker"],
                    "title": mkt["title"][:70],
                    "kalshi_prob": kalshi_prob,
                    "model_prob": model_prob,
                    "edge": edge,
                    "category": category,
                    "american_odds": prob_to_american(kalshi_prob),
                    "volume": mkt.get("volume", 0),
                })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ============================================================================
# UI RENDERING
# ============================================================================

def render_market_dataframe(df: pd.DataFrame) -> None:
    """Render markets as a readable Streamlit dataframe (not HTML)."""
    if df.empty:
        st.caption("No markets found.")
        return

    # Format for display
    display_df = df[[
        "title", "kalshi_prob", "american_odds", "model_prob", "edge", "category", "volume"
    ]].copy()

    display_df.columns = ["Contract", "Kalshi %", "Odds", "Model %", "Edge %", "Category", "Volume"]
    display_df["Kalshi %"] = display_df["Kalshi %"].apply(lambda x: f"{x:.1%}")
    display_df["Model %"] = display_df["Model %"].apply(lambda x: f"{x:.1%}")
    display_df["Edge %"] = display_df["Edge %"].apply(lambda x: f"{x:+.1%}")
    display_df["Volume"] = display_df["Volume"].apply(lambda x: f"{x:,}")

    st.dataframe(display_df, use_container_width=True, hide_index=True)


# ============================================================================
# MAIN APPLICATION
# ============================================================================

def main():
    # Sidebar
    with st.sidebar:
        st.title("⚙️ Settings")
        if st.button("🔄 Refresh All", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        now_et = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5)))
        st.caption(f"Last update: {now_et.strftime('%H:%M:%S ET')}")

    # Main: Title + Tabs
    st.title("🎯 Kalshi Betting Dashboard")

    # Create tabs for Metrics and Help
    tab_metrics, tab_help = st.tabs(["Metric Weights", "Help"])

    model_weights = {}

    with tab_metrics:
        st.subheader("Adjust Your Edge Model")
        st.caption("Change metric weights to update prediction edges across all markets")

        col1, col2, col3 = st.columns(3)

        metric_keys = list(METRIC_WEIGHTS.keys())

        for i, key in enumerate(metric_keys):
            config = METRIC_WEIGHTS[key]
            col = [col1, col2, col3][i % 3] if i < 3 else [col1, col2, col3][(i - 3) % 3]

            with col:
                weight = st.slider(
                    config['label'],
                    0.0, 2.0, 1.0 if i < 3 else 0.5, 0.1,
                    label_visibility="collapsed"
                )
                st.caption(config['label'])
                model_weights[key] = weight

    with tab_help:
        st.subheader("About Metric Weights")
        st.write("Each metric weight controls how much that factor influences your win probability prediction.")
        st.write("")

        for key, config in METRIC_WEIGHTS.items():
            with st.expander(config['label']):
                st.write(config['description'])
                st.divider()
                st.caption(f"**Range**: 0.0 (ignore) to 2.0 (double weight) | **Default**: {'1.0' if key in ['w_net', 'w_form', 'w_hca'] else '0.5'}")

    st.divider()

    # Fetch data
    with st.spinner("📡 Loading markets..."):
        today = date.today()
        games = fetch_balldontlie_games(today)
        kalshi_by_type = fetch_all_kalshi_nba()
        team_stats = fetch_nba_stats_simple()

    if not games:
        st.warning("❌ No NBA games found for today.")
        return

    if not any(kalshi_by_type.values()):
        st.warning("❌ No Kalshi markets found.")
        return

    # Build DataFrame
    df = build_all_rows(games, kalshi_by_type, team_stats, model_weights)

    if df.empty:
        st.warning("⚠️ No markets matched to games. Check team abbreviations.")
        return

    # Summary
    col1, col2, col3 = st.columns(3)
    col1.metric("Games", len(games))
    col2.metric("Markets", len(df))
    col3.metric("Positive Edge", len(df[df["edge"] > 0]))

    st.divider()

    # Top Picks
    st.subheader("🎯 Today's Top Picks")
    top_cats = ["HOMERUN", "UNDERVALUED", "UNDERDOG"]
    top_picks = df[df["category"].isin(top_cats)].sort_values("edge", ascending=False).head(3)

    if top_picks.empty:
        st.info("No high-conviction opportunities today.")
    else:
        render_market_dataframe(top_picks)

    st.divider()

    # Per-game panels
    st.subheader("📊 All Games")
    for game in games:
        game_id = game["game_id"]
        label = f"{game['away_abbr']} @ {game['home_abbr']}"
        time_str = "LIVE 🔴" if "in_progress" in game["status"].lower() else game["game_time_et"][-5:]

        with st.expander(f"{label} — {time_str}"):
            game_df = df[df["game_id"] == game_id]
            if game_df.empty:
                st.caption("No markets.")
                continue

            tab_ml, tab_sp, tab_tot = st.tabs(["Moneyline", "Spread", "Total"])

            with tab_ml:
                render_market_dataframe(game_df[game_df["market_type"] == "moneyline"])

            with tab_sp:
                render_market_dataframe(game_df[game_df["market_type"] == "spread"])

            with tab_tot:
                render_market_dataframe(game_df[game_df["market_type"] == "total"])

    st.divider()

    # Low edge
    low_edge_df = df[df["category"] == "LOW EDGE"]
    with st.expander(f"📋 Low Edge / No Profit ({len(low_edge_df)})"):
        if low_edge_df.empty:
            st.caption("None.")
        else:
            render_market_dataframe(low_edge_df)


if __name__ == "__main__":
    main()
