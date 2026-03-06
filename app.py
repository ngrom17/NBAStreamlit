"""
NBA Betting Intelligence Dashboard — Streamlit App

Uses BallDontLie API for games + nba_api for team stats + Kalshi for prediction market odds.
Features interactive model conviction tilts and intelligent bet categorization.

Conviction Tilts (all on main screen):
  - Net Rating: Team strength (offensive - defensive rating differential)
  - Recent Form: Hot/cold momentum over last 10 games
  - Home Court: Inherent home court advantage
  - Pace Variance: Game speed impact on win probability spread
  - Rest Advantage: Back-to-back fatigue penalty / rest days bonus
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
.conviction-card { background: #161b22; border-radius: 8px; padding: 16px; border-left: 4px solid #58a6ff; }
.conviction-title { color: #58a6ff; font-weight: 700; font-size: 0.9rem; text-transform: uppercase; margin-bottom: 8px; }
.conviction-desc { color: #8b949e; font-size: 0.8rem; line-height: 1.4; margin-bottom: 12px; }
.conviction-slider { margin: 12px 0; }
.green { color: #3fb950; font-weight: 700; }
.red { color: #f85149; font-weight: 700; }
.grey { color: #8b949e; }
.yellow { color: #d29922; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

# ============================================================================
# CONSTANTS
# ============================================================================

KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
BALLDONTLIE_API_BASE = "https://api.balldontlie.io/v1"

# Team abbreviation map (BallDontLie → Kalshi, should be same)
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

# Conviction tilt explanations
CONVICTIONS = {
    "w_net": {
        "label": "📊 Net Rating",
        "description": "Team strength indicator: offensive rating minus defensive rating. Higher = better team. Adjust UP if you believe strength matchups are predictive.",
        "min": 0.0, "max": 2.0, "default": 1.0,
    },
    "w_form": {
        "label": "🔥 Recent Form",
        "description": "Hot/cold momentum over last 10 games. Adjust UP if you think current form beats historical strength, DOWN if form is noise.",
        "min": 0.0, "max": 2.0, "default": 1.0,
    },
    "w_hca": {
        "label": "🏠 Home Court",
        "description": "Home team inherent advantage (~2.5 pts/game). Adjust UP for more home bias, DOWN to ignore crowd effects.",
        "min": 0.0, "max": 2.0, "default": 1.0,
    },
    "w_pace": {
        "label": "⚡ Pace Variance",
        "description": "Game tempo impact on win probability spread. Fast-paced games have higher variance. Adjust UP if you believe pace affects spread.",
        "min": 0.0, "max": 2.0, "default": 0.5,
    },
    "w_rest": {
        "label": "😴 Rest Advantage",
        "description": "Back-to-back fatigue penalty & rest day bonus. Adjust UP if B2B/rest days are significant, DOWN if teams manage load well.",
        "min": 0.0, "max": 2.0, "default": 0.5,
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
        from nba_api.stats.endpoints import teamestimatedmetrics
        from nba_api.stats.static import teams

        stats_dict = {}

        # Get team IDs
        all_teams = teams.get_teams()
        team_id_map = {t["abbreviation"]: t["id"] for t in all_teams}

        # Dynamic season
        now = datetime.now()
        season = f"{now.year}-{now.year + 1}"

        # Fetch metrics
        time.sleep(0.6)
        metrics = teamestimatedmetrics.TeamEstimatedMetrics(season=season).get_data_frames()[0]

        for _, row in metrics.iterrows():
            abbr = row.get("TEAM_ABBREVIATION", "")
            if abbr:
                stats_dict[abbr] = {
                    "off_rating": row.get("E_OFF_RATING"),
                    "def_rating": row.get("E_DEF_RATING"),
                    "net_rating": row.get("E_NET_RATING"),
                    "pace": row.get("E_PACE"),
                    "last10_winpct": 0.5,  # Simplified: no per-game log
                }

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
    # Moneyline: KXNBAGAME-{YYMONDD}{AWAY}{HOME}-{TEAM}
    ml_pattern = r"KXNBAGAME-\d{2}[A-Z]{3}\d{2}([A-Z]{2,3})([A-Z]{2,3})-([A-Z]{2,3})$"
    ml_match = re.match(ml_pattern, ticker)
    if ml_match:
        away_abbr, home_abbr, ticker_team = ml_match.groups()
        return {"market_type": "moneyline", "away_abbr": away_abbr, "home_abbr": home_abbr, "line": None}

    # Spread: KXNBASPREAD-{YYMONDD}{AWAY}{HOME}-{TEAM}{N}
    sp_pattern = r"KXNBASPREAD-\d{2}[A-Z]{3}\d{2}([A-Z]{2,3})([A-Z]{2,3})-([A-Z]{2,3})(\d+)$"
    sp_match = re.match(sp_pattern, ticker)
    if sp_match:
        away_abbr, home_abbr, ticker_team, spread_str = sp_match.groups()
        spread = float(spread_str) / 2.0
        return {"market_type": "spread", "away_abbr": away_abbr, "home_abbr": home_abbr, "line": spread}

    # Total: KXNBATOTAL-{YYMONDD}{AWAY}{HOME}-{N}
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
) -> float:
    """Compute P(home wins) using multi-factor Gaussian model."""
    BASE_SIGMA = 11.0
    HCA_POINTS = 2.5

    if not home_stats or not away_stats:
        return 0.5

    home_net = home_stats.get("net_rating") or 0
    away_net = away_stats.get("net_rating") or 0
    home_form = home_stats.get("last10_winpct") or 0.5
    away_form = away_stats.get("last10_winpct") or 0.5
    home_pace = home_stats.get("pace") or 98
    away_pace = away_stats.get("pace") or 98

    net_diff = home_net - away_net
    form_diff = home_form - away_form
    rest_diff = 0  # Simplified: no rest day data from BallDontLie

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

    return max(0.01, min(0.99, p_win))


def compute_spread_probability(home_stats: Dict, away_stats: Dict, spread_line: float, **weights) -> float:
    """Compute P(home covers spread)."""
    if not home_stats or not away_stats:
        return 0.5

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

    return max(0.01, min(0.99, p_cover))


def compute_total_probability(home_stats: Dict, away_stats: Dict, total_line: float, **weights) -> float:
    """Compute P(total > total_line)."""
    if not home_stats or not away_stats:
        return 0.5

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

                # Match to game
                if (parsed["away_abbr"] != away_abbr or parsed["home_abbr"] != home_abbr):
                    continue

                kalshi_prob = kalshi_mid_price(mkt)

                # Compute model probability
                if market_type == "moneyline":
                    model_prob = compute_win_probability(home_stats, away_stats, **model_weights)
                elif market_type == "spread":
                    model_prob = compute_spread_probability(home_stats, away_stats, parsed["line"], **model_weights)
                elif market_type == "total":
                    model_prob = compute_total_probability(home_stats, away_stats, parsed["line"], **model_weights)
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

def render_pick_card(row: pd.Series) -> str:
    """Render pick card."""
    color = CATEGORY_COLORS.get(row["category"], "#30363d")
    edge_color = "#3fb950" if row["edge"] > 0.05 else "#ff6b6b" if row["edge"] < 0 else "#d29922"

    return f"""
    <div style="background: #161b22; border-radius: 10px; padding: 16px; border-left: 4px solid {color}; margin-bottom: 10px;">
      <div style="color: {color}; font-weight: 700; font-size: 0.85rem; text-transform: uppercase; margin-bottom: 8px;">
        {row['category']}
      </div>
      <div style="color: #e6edf3; font-weight: 700; font-size: 1rem; margin-bottom: 4px;">
        {row['game_label']}
      </div>
      <div style="color: #8b949e; font-size: 0.8rem; margin-bottom: 8px;">
        {row['title'][:60]}...
      </div>
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 0.8rem;">
        <div><div style="color: #8b949e;">Model</div><div style="color: #e6edf3; font-weight: 700;">{row['model_prob']:.1%}</div></div>
        <div><div style="color: #8b949e;">Kalshi</div><div style="color: #e6edf3; font-weight: 700;">{row['kalshi_prob']:.1%}</div></div>
      </div>
      <div style="margin-top: 8px; padding-top: 8px; border-top: 1px solid #30363d;">
        <div style="color: {edge_color}; font-weight: 700;">Edge: {row['edge']:+.1%}</div>
        <div style="color: #8b949e; font-size: 0.75rem; margin-top: 4px;">{row['american_odds']}</div>
      </div>
    </div>
    """


def render_market_table(df: pd.DataFrame) -> None:
    """Render market table."""
    if df.empty:
        st.caption("No markets found.")
        return

    table_html = """
    <table style="width: 100%; border-collapse: collapse; margin: 8px 0;">
      <thead>
        <tr style="border-bottom: 2px solid #30363d; color: #8b949e;">
          <th style="text-align: left; padding: 8px; font-size: 0.7rem; text-transform: uppercase;">Contract</th>
          <th style="text-align: center; padding: 8px; font-size: 0.7rem; text-transform: uppercase;">Kalshi %</th>
          <th style="text-align: center; padding: 8px; font-size: 0.7rem; text-transform: uppercase;">Odds</th>
          <th style="text-align: center; padding: 8px; font-size: 0.7rem; text-transform: uppercase;">Model %</th>
          <th style="text-align: center; padding: 8px; font-size: 0.7rem; text-transform: uppercase;">Edge</th>
          <th style="text-align: center; padding: 8px; font-size: 0.7rem; text-transform: uppercase;">Category</th>
          <th style="text-align: right; padding: 8px; font-size: 0.7rem; text-transform: uppercase;">Vol</th>
        </tr>
      </thead>
      <tbody>
    """

    for _, row in df.iterrows():
        edge_color = "#3fb950" if row["edge"] > 0.05 else "#f85149" if row["edge"] < -0.02 else "#d29922"
        cat_color = CATEGORY_COLORS.get(row["category"], "#30363d")

        table_html += f"""
        <tr style="border-bottom: 1px solid #21262d;">
          <td style="padding: 10px; color: #e6edf3; font-size: 0.8rem;">{row['title'][:50]}</td>
          <td style="padding: 10px; color: #e6edf3; text-align: center; font-weight: 600;">{row['kalshi_prob']:.1%}</td>
          <td style="padding: 10px; color: #8b949e; text-align: center; font-size: 0.75rem;">{row['american_odds']}</td>
          <td style="padding: 10px; color: #e6edf3; text-align: center; font-weight: 600;">{row['model_prob']:.1%}</td>
          <td style="padding: 10px; color: {edge_color}; text-align: center; font-weight: 700;">{row['edge']:+.1%}</td>
          <td style="padding: 10px; text-align: center;">
            <div style="background: {cat_color}20; color: {cat_color}; padding: 4px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 600; text-transform: uppercase;">
              {row['category']}
            </div>
          </td>
          <td style="padding: 10px; color: #8b949e; text-align: right; font-size: 0.75rem;">{row['volume']:,}</td>
        </tr>
        """

    table_html += "</tbody></table>"
    st.markdown(table_html, unsafe_allow_html=True)


# ============================================================================
# MAIN APPLICATION
# ============================================================================

def main():
    # Sidebar: Sport selector
    with st.sidebar:
        st.title("⚙️ Settings")
        sport = st.radio("Sport", ["NBA", "MLB", "Weather"], index=0, label_visibility="collapsed")
        if sport != "NBA":
            st.info(f"{sport} coming soon...")
            return

        st.divider()
        if st.button("🔄 Refresh All", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        now_et = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-5)))
        st.caption(f"Last update: {now_et.strftime('%H:%M:%S ET')}")

    # Main screen: Title + Conviction tilts
    st.title("🎯 Kalshi Betting Dashboard")
    st.subheader("Model Conviction Tilts")

    # Render conviction sliders in 2-column grid
    col1, col2, col3 = st.columns(3)

    conviction_keys = list(CONVICTIONS.keys())
    model_weights = {}

    for i, key in enumerate(conviction_keys):
        config = CONVICTIONS[key]
        col = [col1, col2, col3][i % 3] if i < 3 else [col1, col2, col3][(i - 3) % 3]

        with col:
            st.markdown(f"""
            <div class="conviction-card">
              <div class="conviction-title">{config['label']}</div>
              <div class="conviction-desc">{config['description']}</div>
            </div>
            """, unsafe_allow_html=True)

            weight = st.slider(
                config['label'],
                config['min'], config['max'], config['default'], 0.1,
                label_visibility="collapsed"
            )
            model_weights[key] = weight

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
        st.write("DEBUG: Games loaded:", len(games))
        st.write("DEBUG: Kalshi markets:", sum(len(m) for m in kalshi_by_type.values()))
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
        cols = st.columns(len(top_picks))
        for col, (_, row) in zip(cols, top_picks.iterrows()):
            with col:
                st.markdown(render_pick_card(row), unsafe_allow_html=True)

    st.divider()

    # Per-game panels
    st.subheader("📊 All Games")
    for game in games:
        game_id = game["game_id"]
        label = f"{game['away_abbr']} @ {game['home_abbr']}"
        time_str = "LIVE 🔴" if "in_progress" in game["status"].lower() else game["game_time_et"][-5:]

        with st.expander(f"{label} — {time_str}", expanded=False):
            game_df = df[df["game_id"] == game_id]
            if game_df.empty:
                st.caption("No markets.")
                continue

            tab_ml, tab_sp, tab_tot = st.tabs(["Moneyline", "Spread", "Total"])

            with tab_ml:
                render_market_table(game_df[game_df["market_type"] == "moneyline"])

            with tab_sp:
                render_market_table(game_df[game_df["market_type"] == "spread"])

            with tab_tot:
                render_market_table(game_df[game_df["market_type"] == "total"])

    st.divider()

    # Low edge
    low_edge_df = df[df["category"] == "LOW EDGE"]
    with st.expander(f"📋 Low Edge / No Profit ({len(low_edge_df)})"):
        if low_edge_df.empty:
            st.caption("None.")
        else:
            render_market_table(low_edge_df)


if __name__ == "__main__":
    main()
