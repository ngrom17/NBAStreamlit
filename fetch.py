"""
Data fetching layer — BallDontLie, Kalshi, nba_api with proper caching.
"""

import streamlit as st
import requests
import os
import time
from datetime import datetime
from typing import List, Dict, Optional
from config import KALSHI_API_BASE, BALLDONTLIE_API_BASE


@st.cache_data(ttl=300)
def fetch_games(game_date) -> List[Dict]:
    """
    Fetch NBA games for a specific date from BallDontLie API.

    Returns:
        List of dicts with keys: game_id, home_abbr, away_abbr, home_name, away_name, status, tipoff_utc
    """
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
                    game_time = datetime.utcnow()
            else:
                game_time = datetime.utcnow()

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
                    "tipoff_utc": game_time.isoformat(),
                    "status": game_status,
                })

        return games
    except Exception as e:
        st.sidebar.error(f"❌ BallDontLie fetch failed: {str(e)[:60]}")
        return []


@st.cache_data(ttl=60)
def fetch_kalshi_markets() -> Dict[str, List[Dict]]:
    """
    Fetch all three Kalshi NBA market types: moneyline, spread, total.

    Returns:
        Dict with keys: "moneyline", "spread", "total"
        Each contains list of market dicts with keys: ticker, title, event_ticker, yes_bid, yes_ask, last_price, volume
    """
    results = {}
    for market_type, series_prefix in [
        ("moneyline", "KXNBAGAME"),
        ("spread", "KXNBASPREAD"),
        ("total", "KXNBATOTAL")
    ]:
        try:
            api_key = os.environ.get("KALSHI_API_KEY", "")
            headers = {"Authorization": api_key} if api_key else {}

            url = f"{KALSHI_API_BASE}/markets"
            params = {"status": "open", "series_ticker": series_prefix, "limit": 500}

            resp = requests.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            markets = []
            for mkt in data.get("markets", []):
                # Price extraction: convert cents to decimal 0-1 scale
                yes_bid = (mkt.get("yes_bid", 0) or 0) / 100.0
                yes_ask = (mkt.get("yes_ask", 0) or 0) / 100.0
                last_price = (mkt.get("last_price", 0) or 0) / 100.0

                # Skip markets with no pricing
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
def fetch_team_stats() -> Dict[str, Dict]:
    """
    Fetch NBA team stats using nba_api: net ratings, pace, and last-10 win %.

    Returns:
        Dict keyed by team abbreviation with keys: off_rating, def_rating, net_rating, pace, last10_winpct
        Returns empty dict on any failure (graceful fallback).
    """
    try:
        from nba_api.stats.endpoints import teamestimatedmetrics, leaguedashteamstats
        from nba_api.stats.static import teams

        stats_dict = {}

        # Get team IDs mapping
        all_teams = teams.get_teams()
        team_id_map = {t["abbreviation"]: t["id"] for t in all_teams}

        # Compute season string (month-aware)
        # NBA season runs Oct-Jun; if current month < 10, season started last year
        now = datetime.now()
        yr = now.year if now.month >= 10 else now.year - 1
        season = f"{yr}-{str(yr + 1)[2:]}"  # March 2026 → "2025-26"

        # Fetch 1: Season metrics (E_NET_RATING, E_PACE, E_OFF_RATING, E_DEF_RATING)
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
        except Exception as e:
            st.sidebar.warning(f"⚠️ nba_api metrics failed: {str(e)[:50]}")
            return {}

        # Fetch 2: Last 10 games record (for form)
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
            # Fail silently, use 0.5 default
            pass

        return stats_dict

    except ImportError:
        st.sidebar.error("❌ nba_api not installed. Run: pip install nba_api")
        return {}
    except Exception as e:
        st.sidebar.warning(f"⚠️ nba_api stats failed: {str(e)[:50]}")
        return {}
