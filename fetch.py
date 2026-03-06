"""
Data fetching layer — BallDontLie games, Kalshi markets, NBA stats via stats.nba.com.
Uses the same data pipeline as the NBA ML model (kyleskom/NBA-Machine-Learning-Sports-Betting).
"""

import os
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd
import requests
import streamlit as st

# Add src/ to path so we can import NBA ML utilities
DASHBOARD_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DASHBOARD_DIR))

from config import (
    KALSHI_API_BASE,
    BALLDONTLIE_API_BASE,
    NBA_STATS_URL,
    NBA_STATS_HEADERS,
    FULL_TO_ABBREV,
)

SCHEDULE_PATH = DASHBOARD_DIR / "Data" / "nba-2025-UTC.csv"


# ============================================================================
# BALLDONTLIE — TODAY'S GAMES
# ============================================================================

@st.cache_data(ttl=300)
def fetch_games(game_date: date) -> List[Dict]:
    """
    Fetch today's NBA games from BallDontLie.
    Returns: [{game_id, home_abbr, away_abbr, home_name, away_name, tipoff_utc, status}]
    """
    try:
        api_key = os.environ.get("BALLDONTLIE_API_KEY", "")
        headers = {"Authorization": api_key} if api_key else {}
        resp = requests.get(
            f"{BALLDONTLIE_API_BASE}/games",
            params={"dates[]": game_date.isoformat()},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()

        games = []
        for raw in resp.json().get("data", []):
            home = raw.get("home_team", {})
            away = raw.get("visitor_team", {})
            home_abbr = home.get("abbreviation", "")
            away_abbr = away.get("abbreviation", "")
            status = raw.get("status", "")

            dt_str = raw.get("datetime") or raw.get("date", "")
            try:
                tipoff = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            except Exception:
                tipoff = datetime.utcnow()

            if status == "Final":
                game_status = "final"
            elif status in ("In Progress", "1st Qtr", "2nd Qtr", "3rd Qtr", "4th Qtr", "Halftime", "OT"):
                game_status = "in_progress"
            else:
                game_status = "scheduled"

            if home_abbr and away_abbr and game_status != "final":
                games.append({
                    "game_id": raw.get("id", ""),
                    "home_abbr": home_abbr,
                    "away_abbr": away_abbr,
                    "home_name": home.get("full_name", ""),
                    "away_name": away.get("full_name", ""),
                    "tipoff_utc": tipoff.isoformat(),
                    "status": game_status,
                })
        return games

    except Exception as e:
        st.sidebar.error(f"❌ BallDontLie failed: {str(e)[:60]}")
        return []


# ============================================================================
# KALSHI — ALL THREE MARKET TYPES
# ============================================================================

@st.cache_data(ttl=60)
def fetch_kalshi_markets() -> Dict[str, List[Dict]]:
    """
    Fetch Kalshi NBA markets: moneyline, spread, total.
    Returns: {"moneyline": [...], "spread": [...], "total": [...]}
    Each market dict has: ticker, title, event_ticker, yes_bid, yes_ask, last_price, volume
    """
    results = {}
    for market_type, prefix in [
        ("moneyline", "KXNBAGAME"),
        ("spread", "KXNBASPREAD"),
        ("total", "KXNBATOTAL"),
    ]:
        try:
            api_key = os.environ.get("KALSHI_API_KEY", "")
            headers = {"Authorization": api_key} if api_key else {}
            resp = requests.get(
                f"{KALSHI_API_BASE}/markets",
                params={"status": "open", "series_ticker": prefix, "limit": 500},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            markets = []
            for mkt in resp.json().get("markets", []):
                yes_bid   = (mkt.get("yes_bid",    0) or 0) / 100.0
                yes_ask   = (mkt.get("yes_ask",    0) or 0) / 100.0
                last_price = (mkt.get("last_price", 0) or 0) / 100.0
                if yes_bid <= 0 and yes_ask <= 0 and last_price <= 0:
                    continue
                markets.append({
                    "ticker":       mkt.get("ticker", ""),
                    "title":        mkt.get("title", ""),
                    "event_ticker": mkt.get("event_ticker", ""),
                    "yes_bid":      yes_bid,
                    "yes_ask":      yes_ask,
                    "last_price":   last_price,
                    "volume":       mkt.get("volume", 0),
                })
            results[market_type] = markets
        except Exception as e:
            st.sidebar.warning(f"⚠️ Kalshi {prefix}: {str(e)[:40]}")
            results[market_type] = []
    return results


# ============================================================================
# NBA STATS — stats.nba.com (same pipeline as NBA ML model)
# ============================================================================

@st.cache_data(ttl=3600)
def fetch_nba_stats() -> Optional[pd.DataFrame]:
    """
    Fetch current-season per-game team stats from stats.nba.com.
    Returns a raw DataFrame with 30 rows and 54 columns (TEAM_ID, TEAM_NAME, + 52 stats).
    Columns match exactly what the XGBoost model was trained on.
    Returns None on failure.
    """
    now = datetime.now()
    yr = now.year if now.month >= 10 else now.year - 1
    season = f"{yr}-{str(yr + 1)[2:]}"  # "2025-26"

    url = NBA_STATS_URL.format(season=season)

    for attempt in range(3):
        try:
            time.sleep(1.0 + attempt)
            resp = requests.get(url, headers=NBA_STATS_HEADERS, timeout=20)
            resp.raise_for_status()
            result_sets = resp.json().get("resultSets", [])
            if not result_sets:
                continue
            rs = result_sets[0]
            df = pd.DataFrame(data=rs["rowSet"], columns=rs["headers"])
            st.sidebar.caption(f"📊 Stats: {len(df)}/30 teams loaded")
            return df
        except Exception as e:
            if attempt == 2:
                st.sidebar.warning(f"⚠️ NBA stats failed: {str(e)[:50]}")
                return None

    return None


@st.cache_data(ttl=3600)
def load_schedule() -> Optional[pd.DataFrame]:
    """
    Load season schedule CSV (used for rest days calculation).
    """
    try:
        df = pd.read_csv(SCHEDULE_PATH, parse_dates=["Date"], date_format="%d/%m/%Y %H:%M")
        return df
    except Exception as e:
        st.sidebar.warning(f"⚠️ Schedule not found: {str(e)[:50]}")
        return None


def compute_rest_days(team_full_name: str, today: datetime, schedule_df: pd.DataFrame) -> int:
    """
    Calculate days of rest for a team before today's game.
    Returns 2 (league average) if unavailable.
    """
    if schedule_df is None:
        return 2
    try:
        games = schedule_df[
            (schedule_df["Home Team"] == team_full_name) |
            (schedule_df["Away Team"] == team_full_name)
        ]
        prev = games.loc[games["Date"] <= today].sort_values("Date", ascending=False).head(1)["Date"]
        if len(prev) > 0:
            last_date = prev.iloc[0]
            return (timedelta(days=1) + today - last_date).days
        return 7  # First game of season
    except Exception:
        return 2
