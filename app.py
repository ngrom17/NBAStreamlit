"""
NBA Betting Intelligence Dashboard — Streamlit App

Integrates ESPN game data, live Kalshi prediction market odds, and nba_api team stats
to identify positive-edge betting opportunities. Features interactive model tilts (weight sliders)
and intelligent bet categorization (Homerun, Undervalued, Underdog, Sharp, Fade, Low Edge).
"""

import streamlit as st
import requests
import pandas as pd
import numpy as np
import time
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List

# ============================================================================
# PAGE CONFIG & STYLING
# ============================================================================

st.set_page_config(
    page_title="NBA Betting Intelligence",
    page_icon="🏀",
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

# ESPN to Kalshi team abbreviation corrections
ESPN_TO_KALSHI = {
    "GS": "GSW",
    "SA": "SAS",
    "NY": "NYK",
    "NO": "NOP",
}

# Category colors (hex)
CATEGORY_COLORS = {
    "HOMERUN": "#f85149",
    "UNDERVALUED": "#3fb950",
    "UNDERDOG": "#58a6ff",
    "SHARP": "#d29922",
    "FADE": "#6e7681",
    "LOW EDGE": "#30363d",
}

# Kalshi series prefixes
KALSHI_SERIES = {
    "moneyline": "KXNBAGAME",
    "spread": "KXNBASPREAD",
    "total": "KXNBATOTAL",
}

# ============================================================================
# DATA FETCHING LAYER
# ============================================================================

@st.cache_data(ttl=120)
def fetch_espn_games() -> List[Dict]:
    """Fetch today's NBA games from ESPN scoreboard API."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={today}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        games = []
        for event in data.get("events", []):
            competitors = event.get("competitions", [{}])[0].get("competitors", [])
            if len(competitors) < 2:
                continue

            away = competitors[0]
            home = competitors[1]

            away_abbr = away.get("team", {}).get("abbreviation", "")
            home_abbr = home.get("team", {}).get("abbreviation", "")

            # Apply ESPN → Kalshi correction
            away_abbr = ESPN_TO_KALSHI.get(away_abbr, away_abbr)
            home_abbr = ESPN_TO_KALSHI.get(home_abbr, home_abbr)

            game_time = event.get("date", "")
            status = event.get("competitions", [{}])[0].get("status", {}).get("type", "")

            games.append({
                "game_id": event.get("id", ""),
                "away_abbr": away_abbr,
                "home_abbr": home_abbr,
                "away_name": away.get("team", {}).get("displayName", ""),
                "home_name": home.get("team", {}).get("displayName", ""),
                "game_time_et": game_time,
                "status": status,
                "away_score": int(away.get("score", 0)),
                "home_score": int(home.get("score", 0)),
            })

        return games
    except Exception as e:
        st.sidebar.warning(f"⚠️ ESPN fetch failed: {str(e)[:50]}")
        return []


def fetch_kalshi_markets(series_prefix: str) -> List[Dict]:
    """Fetch Kalshi markets for a specific series prefix."""
    try:
        url = f"{KALSHI_API_BASE}/markets"
        params = {
            "status": "open",
            "series_ticker": series_prefix,
            "limit": 500,
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        markets = []
        for mkt in data.get("markets", []):
            # Price extraction (cents to 0-1 scale)
            yes_bid = (mkt.get("yes_bid", 0) or 0) / 100.0
            yes_ask = (mkt.get("yes_ask", 0) or 0) / 100.0
            last_price = (mkt.get("last_price", 0) or 0) / 100.0

            # Skip markets with no pricing data
            if yes_bid <= 0 and yes_ask <= 0 and last_price <= 0:
                continue

            markets.append({
                "ticker": mkt.get("ticker", ""),
                "title": mkt.get("title", ""),
                "event_ticker": mkt.get("event_ticker", ""),
                "series_ticker": mkt.get("series_ticker", ""),
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "last_price": last_price,
                "volume": mkt.get("volume", 0),
                "status": mkt.get("status", ""),
            })

        return markets
    except Exception as e:
        st.sidebar.warning(f"⚠️ Kalshi {series_prefix} fetch failed: {str(e)[:40]}")
        return []


@st.cache_data(ttl=60)
def fetch_all_kalshi_nba() -> Dict[str, List[Dict]]:
    """Fetch all three Kalshi NBA market types."""
    return {
        "moneyline": fetch_kalshi_markets(KALSHI_SERIES["moneyline"]),
        "spread": fetch_kalshi_markets(KALSHI_SERIES["spread"]),
        "total": fetch_kalshi_markets(KALSHI_SERIES["total"]),
    }


@st.cache_data(ttl=3600)
def fetch_nba_team_stats() -> Dict[str, Dict]:
    """Fetch live NBA team stats from nba_api with graceful fallback."""
    try:
        from nba_api.stats.endpoints import teamestimatedmetrics, leaguedashteamstats, teamdashboardbygeneralsplits
        from nba_api.stats.static import teams

        stats_dict = {}
        failed_teams = []

        # Fetch all teams' IDs
        all_teams = teams.get_teams()
        team_id_map = {t["abbreviation"]: t["id"] for t in all_teams}

        # Dynamic season calculation
        now = datetime.now()
        season = f"{now.year}-{now.year + 1}"

        # Phase 1: Estimated metrics (off/def/net rating, pace)
        time.sleep(0.6)
        metrics = teamestimatedmetrics.TeamEstimatedMetrics(season=season).get_data_frames()[0]
        metrics_dict = {}
        for _, row in metrics.iterrows():
            abbr = row.get("TEAM_ABBREVIATION", "")
            if abbr:
                metrics_dict[abbr] = {
                    "off_rating": row.get("E_OFF_RATING"),
                    "def_rating": row.get("E_DEF_RATING"),
                    "net_rating": row.get("E_NET_RATING"),
                    "pace": row.get("E_PACE"),
                }

        # Phase 2: Recent form (last 10 games)
        time.sleep(0.6)
        dash = leaguedashteamstats.LeagueDashTeamStats(
            season=season,
            measure_type_detailed_defense="Base",
            per_mode_simple="PerGame"
        ).get_data_frames()[0]
        form_dict = {}
        for _, row in dash.iterrows():
            abbr = row.get("TEAM_ABBREVIATION", "")
            if abbr:
                w = row.get("W", 0)
                l = row.get("L", 0)
                w_pct = w / (w + l) if (w + l) > 0 else 0.5
                form_dict[abbr] = {"last10_winpct": w_pct}

        # Phase 3: Home/Away splits (per team, expensive but cached hourly)
        time.sleep(0.6)
        for abbr, team_id in list(team_id_map.items())[:30]:
            try:
                time.sleep(0.3)  # Extra conservative rate limiting
                split = teamdashboardbygeneralsplits.TeamDashboardByGeneralSplits(
                    team_id=team_id,
                    season=season,
                    measure_type_detailed_defense="Base",
                    per_mode_simple="PerGame"
                ).get_data_frames()[1]  # LocationTeamDashboard index

                home_data = split[split["GROUP_VALUE"] == "Home"]
                away_data = split[split["GROUP_VALUE"] == "Road"]

                home_w_pct = 0.5
                away_w_pct = 0.5

                if not home_data.empty:
                    hw = home_data.iloc[0].get("W", 0)
                    hl = home_data.iloc[0].get("L", 0)
                    home_w_pct = hw / (hw + hl) if (hw + hl) > 0 else 0.5

                if not away_data.empty:
                    aw = away_data.iloc[0].get("W", 0)
                    al = away_data.iloc[0].get("L", 0)
                    away_w_pct = aw / (aw + al) if (aw + al) > 0 else 0.5

                if abbr not in stats_dict:
                    stats_dict[abbr] = {}
                stats_dict[abbr].update({
                    "home_winpct": home_w_pct,
                    "away_winpct": away_w_pct,
                })
            except Exception as e:
                failed_teams.append(abbr)
                continue

        # Log failures if any
        if failed_teams:
            st.sidebar.warning(f"⚠️ nba_api: {len(failed_teams)}/30 teams incomplete")

        # Merge all data
        for abbr in metrics_dict:
            if abbr not in stats_dict:
                stats_dict[abbr] = {}
            stats_dict[abbr].update(metrics_dict[abbr])

        for abbr in form_dict:
            if abbr not in stats_dict:
                stats_dict[abbr] = {}
            stats_dict[abbr].update(form_dict[abbr])

        return stats_dict

    except Exception as e:
        st.sidebar.warning(f"⚠️ nba_api stats failed: {str(e)[:50]}")
        return {}


# ============================================================================
# PARSING & UTILITY FUNCTIONS
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


def parse_kalshi_nba_ticker(ticker: str, title: str) -> Optional[Dict]:
    """Parse Kalshi NBA ticker to extract game info and market type."""
    # Moneyline: KXNBAGAME-{YYMONDD}{AWAY}{HOME}-{TEAM}
    ml_pattern = r"KXNBAGAME-\d{2}[A-Z]{3}\d{2}([A-Z]{2,3})([A-Z]{2,3})-([A-Z]{2,3})$"
    ml_match = re.match(ml_pattern, ticker)
    if ml_match:
        away_abbr, home_abbr, ticker_team = ml_match.groups()
        return {
            "market_type": "moneyline",
            "away_abbr": away_abbr,
            "home_abbr": home_abbr,
            "ticker_team": ticker_team,
            "line": None,
        }

    # Spread: KXNBASPREAD-{YYMONDD}{AWAY}{HOME}-{TEAM}{N}
    sp_pattern = r"KXNBASPREAD-\d{2}[A-Z]{3}\d{2}([A-Z]{2,3})([A-Z]{2,3})-([A-Z]{2,3})(\d+)$"
    sp_match = re.match(sp_pattern, ticker)
    if sp_match:
        away_abbr, home_abbr, ticker_team, spread_str = sp_match.groups()
        # Spread is stored as 2x the actual line (e.g., 11 = 5.5)
        spread = float(spread_str) / 2.0
        return {
            "market_type": "spread",
            "away_abbr": away_abbr,
            "home_abbr": home_abbr,
            "ticker_team": ticker_team,
            "line": spread,
        }

    # Total: KXNBATOTAL-{YYMONDD}{AWAY}{HOME}-{N}
    tot_pattern = r"KXNBATOTAL-\d{2}[A-Z]{3}\d{2}([A-Z]{2,3})([A-Z]{2,3})-(\d+)$"
    tot_match = re.match(tot_pattern, ticker)
    if tot_match:
        away_abbr, home_abbr, total_str = tot_match.groups()
        total = float(total_str) / 2.0
        return {
            "market_type": "total",
            "away_abbr": away_abbr,
            "home_abbr": home_abbr,
            "ticker_team": None,
            "line": total,
        }

    return None


def prob_to_american(p: float) -> str:
    """Convert probability (0-1) to American odds string."""
    if p >= 0.99:
        return "-10000"
    if p <= 0.01:
        return "+10000"

    if p > 0.5:
        # Favorite
        odds = -round((p / (1 - p)) * 100)
        return str(odds)
    else:
        # Underdog
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
    home_rest_days: int = 2,
    away_rest_days: int = 2,
) -> float:
    """Compute P(home team wins) using multi-factor Gaussian model."""
    BASE_SIGMA = 11.0
    HCA_POINTS = 2.5

    if not home_stats or not away_stats:
        return 0.5

    # Extract factors with fallback to neutral values
    home_net = home_stats.get("net_rating") or 0
    away_net = away_stats.get("net_rating") or 0
    home_form = home_stats.get("last10_winpct") or 0.5
    away_form = away_stats.get("last10_winpct") or 0.5
    home_pace = home_stats.get("pace") or 98
    away_pace = away_stats.get("pace") or 98

    net_diff = home_net - away_net
    form_diff = home_form - away_form
    rest_diff = home_rest_days - away_rest_days

    # Build point spread estimate
    point_spread = (
        net_diff * w_net +
        HCA_POINTS * w_hca +
        form_diff * 12 * w_form +
        rest_diff * 1.5 * w_rest
    )

    # Adjust sigma based on pace (faster games → higher variance)
    avg_pace = (home_pace + away_pace) / 2.0
    pace_norm = max(-1.0, min(1.0, (avg_pace - 98.0) / 12.0))
    sigma = BASE_SIGMA * (1.0 + pace_norm * 0.15 * w_pace)

    # Normal CDF to get win probability
    z = point_spread / (sigma * math.sqrt(2))
    p_win = 0.5 + 0.5 * math.erf(z)

    return max(0.01, min(0.99, p_win))


def compute_spread_probability(
    home_stats: Dict,
    away_stats: Dict,
    spread_line: float,
    **weights,
) -> float:
    """Compute P(home team covers spread_line)."""
    if not home_stats or not away_stats:
        return 0.5

    BASE_SIGMA = 11.0
    HCA_POINTS = 2.5

    home_net = home_stats.get("net_rating") or 0
    away_net = away_stats.get("net_rating") or 0
    home_form = home_stats.get("last10_winpct") or 0.5
    away_form = away_stats.get("last10_winpct") or 0.5
    home_pace = home_stats.get("pace") or 98
    away_pace = away_stats.get("pace") or 98

    net_diff = home_net - away_net
    form_diff = home_form - away_form
    rest_diff = weights.get("home_rest_days", 2) - weights.get("away_rest_days", 2)

    w_net = weights.get("w_net", 1.0)
    w_form = weights.get("w_form", 1.0)
    w_hca = weights.get("w_hca", 1.0)
    w_pace = weights.get("w_pace", 0.5)
    w_rest = weights.get("w_rest", 0.5)

    point_spread = (
        net_diff * w_net +
        HCA_POINTS * w_hca +
        form_diff * 12 * w_form +
        rest_diff * 1.5 * w_rest -
        spread_line
    )

    avg_pace = (home_pace + away_pace) / 2.0
    pace_norm = max(-1.0, min(1.0, (avg_pace - 98.0) / 12.0))
    sigma = BASE_SIGMA * (1.0 + pace_norm * 0.15 * w_pace)

    z = point_spread / (sigma * math.sqrt(2))
    p_cover = 0.5 + 0.5 * math.erf(z)

    return max(0.01, min(0.99, p_cover))


def compute_total_probability(
    home_stats: Dict,
    away_stats: Dict,
    total_line: float,
    **weights,
) -> float:
    """Compute P(total points > total_line)."""
    if not home_stats or not away_stats:
        return 0.5

    home_off = home_stats.get("off_rating") or 110
    away_off = away_stats.get("off_rating") or 110
    home_pace = home_stats.get("pace") or 98
    away_pace = away_stats.get("pace") or 98

    # Predicted total: sum of both teams' expected points
    avg_pace = (home_pace + away_pace) / 2.0
    home_pts = (home_off / 100.0) * avg_pace
    away_pts = (away_off / 100.0) * avg_pace
    pred_total = home_pts + away_pts

    # CDF with sigma = 12 (total standard deviation)
    sigma_total = 12.0
    z = (pred_total - total_line) / (sigma_total * math.sqrt(2))
    p_over = 0.5 + 0.5 * math.erf(z)

    return max(0.01, min(0.99, p_over))


# ============================================================================
# BET CLASSIFICATION & AGGREGATION
# ============================================================================

def classify_bet(edge: float, model_prob: float, kalshi_prob: float) -> str:
    """Classify a bet into a category based on edge and confidence."""
    # Priority order: first match wins
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
    """Build master DataFrame of all markets with probabilities and edges."""
    rows = []

    for game in espn_games:
        game_id = game["game_id"]
        home_abbr = game["home_abbr"]
        away_abbr = game["away_abbr"]
        game_label = f"{away_abbr} @ {home_abbr}"

        # Get team stats (may be incomplete or missing)
        home_stats = team_stats.get(home_abbr, {})
        away_stats = team_stats.get(away_abbr, {})

        # Process each market type
        for market_type, markets in kalshi_by_type.items():
            for mkt in markets:
                parsed = parse_kalshi_nba_ticker(mkt["ticker"], mkt["title"])
                if not parsed:
                    continue

                # Check if this market belongs to this game
                if (parsed["away_abbr"] != away_abbr or
                    parsed["home_abbr"] != home_abbr):
                    continue

                # Get market probabilities
                kalshi_prob = kalshi_mid_price(mkt)

                # Compute model probability based on market type
                if market_type == "moneyline":
                    model_prob = compute_win_probability(
                        home_stats, away_stats,
                        w_net=model_weights["w_net"],
                        w_form=model_weights["w_form"],
                        w_hca=model_weights["w_hca"],
                        w_pace=model_weights["w_pace"],
                        w_rest=model_weights["w_rest"],
                    )
                elif market_type == "spread":
                    model_prob = compute_spread_probability(
                        home_stats, away_stats,
                        parsed["line"],
                        w_net=model_weights["w_net"],
                        w_form=model_weights["w_form"],
                        w_hca=model_weights["w_hca"],
                        w_pace=model_weights["w_pace"],
                        w_rest=model_weights["w_rest"],
                    )
                elif market_type == "total":
                    model_prob = compute_total_probability(
                        home_stats, away_stats,
                        parsed["line"],
                        w_net=model_weights["w_net"],
                        w_form=model_weights["w_form"],
                        w_hca=model_weights["w_hca"],
                        w_pace=model_weights["w_pace"],
                        w_rest=model_weights["w_rest"],
                    )
                else:
                    model_prob = 0.5

                # Calculate edge
                edge = model_prob - kalshi_prob

                # Cap total markets at LOW EDGE unless huge edge
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
                    "home_abbr": home_abbr,
                    "away_abbr": away_abbr,
                })

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


# ============================================================================
# UI RENDERING FUNCTIONS
# ============================================================================

def render_pick_card(row: pd.Series) -> str:
    """Render a single pick card as HTML."""
    color = CATEGORY_COLORS.get(row["category"], "#30363d")
    edge_color = "#3fb950" if row["edge"] > 0.05 else "#ff6b6b" if row["edge"] < 0 else "#d29922"

    html = f"""
    <div style="background: #161b22; border-radius: 10px; padding: 16px;
                border-left: 4px solid {color}; margin-bottom: 10px;">
      <div style="color: {color}; font-weight: 700; font-size: 0.85rem;
                  text-transform: uppercase; margin-bottom: 8px;">
        {row['category']}
      </div>
      <div style="color: #e6edf3; font-weight: 700; font-size: 1rem; margin-bottom: 4px;">
        {row['game_label']}
      </div>
      <div style="color: #8b949e; font-size: 0.8rem; margin-bottom: 8px;">
        {row['title'][:60]}...
      </div>
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 0.8rem;">
        <div>
          <div style="color: #8b949e;">Model</div>
          <div style="color: #e6edf3; font-weight: 700;">{row['model_prob']:.1%}</div>
        </div>
        <div>
          <div style="color: #8b949e;">Kalshi</div>
          <div style="color: #e6edf3; font-weight: 700;">{row['kalshi_prob']:.1%}</div>
        </div>
      </div>
      <div style="margin-top: 8px; padding-top: 8px; border-top: 1px solid #30363d;">
        <div style="color: {edge_color}; font-weight: 700;">
          Edge: {row['edge']:+.1%}
        </div>
        <div style="color: #8b949e; font-size: 0.75rem; margin-top: 4px;">
          {row['american_odds']}
        </div>
      </div>
    </div>
    """
    return html


def render_market_table(df: pd.DataFrame) -> None:
    """Render markets as an HTML table."""
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
          <td style="padding: 10px; color: #e6edf3; font-size: 0.8rem;">
            {row['title'][:50]}
          </td>
          <td style="padding: 10px; color: #e6edf3; text-align: center; font-weight: 600;">
            {row['kalshi_prob']:.1%}
          </td>
          <td style="padding: 10px; color: #8b949e; text-align: center; font-size: 0.75rem;">
            {row['american_odds']}
          </td>
          <td style="padding: 10px; color: #e6edf3; text-align: center; font-weight: 600;">
            {row['model_prob']:.1%}
          </td>
          <td style="padding: 10px; color: {edge_color}; text-align: center; font-weight: 700;">
            {row['edge']:+.1%}
          </td>
          <td style="padding: 10px; text-align: center;">
            <div style="background: {cat_color}20; color: {cat_color}; padding: 4px 8px;
                        border-radius: 4px; font-size: 0.7rem; font-weight: 600; text-transform: uppercase;">
              {row['category']}
            </div>
          </td>
          <td style="padding: 10px; color: #8b949e; text-align: right; font-size: 0.75rem;">
            {row['volume']:,}
          </td>
        </tr>
        """

    table_html += """
      </tbody>
    </table>
    """
    st.markdown(table_html, unsafe_allow_html=True)


# ============================================================================
# MAIN APPLICATION
# ============================================================================

def main():
    # Sidebar controls
    with st.sidebar:
        st.title("⚙️ Model Tuning")
        st.caption("Adjust conviction weights → probabilities update live")

        w_net = st.slider(
            "📊 Net Rating Weight",
            0.0, 2.0, 1.0, 0.1,
            help="Team strength indicator (pts/100 poss)"
        )
        w_form = st.slider(
            "🔥 Recent Form Weight",
            0.0, 2.0, 1.0, 0.1,
            help="Hot/cold momentum (last 10 games)"
        )
        w_hca = st.slider(
            "🏠 Home Court Weight",
            0.0, 2.0, 1.0, 0.1,
            help="Home court advantage boost"
        )
        w_pace = st.slider(
            "⚡ Pace Variance Weight",
            0.0, 2.0, 0.5, 0.1,
            help="Game pace uncertainty (high pace = more variance)"
        )
        w_rest = st.slider(
            "😴 Rest Advantage Weight",
            0.0, 2.0, 0.5, 0.1,
            help="Rest days impact (B2B fatigue)"
        )

        st.divider()

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Status", "🟢 LIVE")
        with col2:
            if st.button("🔄 Refresh", use_container_width=True):
                st.cache_data.clear()
                st.rerun()

        now_et = datetime.now(timezone.utc).astimezone(
            timezone(timedelta(hours=-5))
        )
        st.caption(f"Last update: {now_et.strftime('%H:%M:%S ET')}")

    model_weights = {
        "w_net": w_net,
        "w_form": w_form,
        "w_hca": w_hca,
        "w_pace": w_pace,
        "w_rest": w_rest,
    }

    # Fetch all data
    with st.spinner("📡 Loading NBA data..."):
        espn_games = fetch_espn_games()
        kalshi_by_type = fetch_all_kalshi_nba()
        team_stats = fetch_nba_team_stats()

    if not espn_games:
        st.warning("❌ No NBA games found for today or ESPN API unavailable.")
        return

    if not any(kalshi_by_type.values()):
        st.warning("❌ No Kalshi markets found. Check API status.")
        return

    # Build master DataFrame
    df = build_all_rows(espn_games, kalshi_by_type, team_stats, model_weights)

    if df.empty:
        st.warning("⚠️ No markets matched to ESPN games. Check team abbreviations.")
        return

    # Title & summary
    st.title("🏀 NBA Betting Intelligence")
    st.subheader("Kalshi Prediction Market Analysis")

    col1, col2, col3 = st.columns(3)
    col1.metric("Games", len(espn_games))
    col2.metric("Markets", len(df))
    col3.metric("Positive Edge", len(df[df["edge"] > 0]))

    st.divider()

    # Top Picks section
    st.subheader("🎯 Today's Top Picks")

    top_cats = ["HOMERUN", "UNDERVALUED", "UNDERDOG"]
    top_picks = df[df["category"].isin(top_cats)].sort_values("edge", ascending=False).head(3)

    if top_picks.empty:
        st.info("No high-conviction opportunities identified today.")
    else:
        cols = st.columns(len(top_picks))
        for col, (_, row) in zip(cols, top_picks.iterrows()):
            with col:
                st.markdown(render_pick_card(row), unsafe_allow_html=True)

    st.divider()

    # Per-game panels
    st.subheader("📊 All Games")

    for game in espn_games:
        game_id = game["game_id"]
        label = f"{game['away_abbr']} @ {game['home_abbr']}"

        # Game time
        time_str = "LIVE 🔴" if "inprogress" in game["status"].lower() else game["game_time_et"][-5:]

        with st.expander(f"{label} — {time_str}", expanded=False):
            # Stats row
            home_abbr = game["home_abbr"]
            away_abbr = game["away_abbr"]

            home_stats = team_stats.get(home_abbr, {})
            away_stats = team_stats.get(away_abbr, {})

            if home_stats and away_stats:
                c1, c2, c3 = st.columns(3)
                net_diff = (home_stats.get("net_rating", 0) or 0) - (away_stats.get("net_rating", 0) or 0)
                form_diff = (home_stats.get("last10_winpct", 0) or 0) - (away_stats.get("last10_winpct", 0) or 0)
                pace = ((home_stats.get("pace", 98) or 98) + (away_stats.get("pace", 98) or 98)) / 2

                c1.metric("Net Rating Diff", f"{net_diff:+.1f} pts")
                c2.metric("Form Diff (L10)", f"{form_diff*100:+.0f}%")
                c3.metric("League Pace", f"{pace:.1f}")

            # Market tables by type
            game_df = df[df["game_id"] == game_id]

            if game_df.empty:
                st.caption("No markets for this game.")
                continue

            tab_ml, tab_sp, tab_tot = st.tabs(["Moneyline", "Spread", "Total"])

            with tab_ml:
                ml_df = game_df[game_df["market_type"] == "moneyline"]
                if ml_df.empty:
                    st.caption("No moneyline markets.")
                else:
                    render_market_table(ml_df)

            with tab_sp:
                sp_df = game_df[game_df["market_type"] == "spread"]
                if sp_df.empty:
                    st.caption("No spread markets.")
                else:
                    render_market_table(sp_df)

            with tab_tot:
                tot_df = game_df[game_df["market_type"] == "total"]
                if tot_df.empty:
                    st.caption("No total markets.")
                else:
                    render_market_table(tot_df)

    st.divider()

    # Low edge section
    low_edge_df = df[df["category"] == "LOW EDGE"]
    with st.expander(f"📋 Low Edge / No Profit ({len(low_edge_df)} markets)", expanded=False):
        if low_edge_df.empty:
            st.caption("None.")
        else:
            render_market_table(low_edge_df)


if __name__ == "__main__":
    main()
