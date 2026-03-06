"""
Probability model — Gaussian win probability, ticker parsing, bet classification.
"""

import math
import re
from typing import Dict, List, Optional
import pandas as pd
from config import CATEGORY_COLORS, BET_THRESHOLDS


# ============================================================================
# TICKER PARSING
# ============================================================================

def parse_kalshi_ticker(ticker: str) -> Optional[Dict]:
    """
    Parse Kalshi NBA ticker format and extract market details.

    Ticker formats:
      KXNBAGAME-{YYMONDD}{AWAY3}{HOME3}-{TEAM3}  → moneyline
      KXNBASPREAD-{YYMONDD}{AWAY3}{HOME3}-{TEAM3}{N}  → spread, line = N + 0.5
      KXNBATOTAL-{YYMONDD}{AWAY3}{HOME3}-{N}  → total, line = N + 0.5

    Returns:
        Dict with keys: market_type, away_abbr, home_abbr, line (if applicable)
        Or None if parsing fails.
    """
    # Moneyline
    ml_pattern = r"KXNBAGAME-\d{2}[A-Z]{3}\d{2}([A-Z]{2,3})([A-Z]{2,3})-[A-Z]{2,3}$"
    ml_match = re.match(ml_pattern, ticker)
    if ml_match:
        away_abbr, home_abbr = ml_match.groups()
        return {"market_type": "moneyline", "away_abbr": away_abbr, "home_abbr": home_abbr, "line": None}

    # Spread
    sp_pattern = r"KXNBASPREAD-\d{2}[A-Z]{3}\d{2}([A-Z]{2,3})([A-Z]{2,3})-[A-Z]{2,3}(\d+)$"
    sp_match = re.match(sp_pattern, ticker)
    if sp_match:
        away_abbr, home_abbr, spread_str = sp_match.groups()
        # CRITICAL FIX: suffix encodes (line - 0.5), so line = suffix + 0.5
        line = float(spread_str) + 0.5
        return {"market_type": "spread", "away_abbr": away_abbr, "home_abbr": home_abbr, "line": line}

    # Total
    tot_pattern = r"KXNBATOTAL-\d{2}[A-Z]{3}\d{2}([A-Z]{2,3})([A-Z]{2,3})-(\d+)$"
    tot_match = re.match(tot_pattern, ticker)
    if tot_match:
        away_abbr, home_abbr, total_str = tot_match.groups()
        # CRITICAL FIX: suffix encodes (line - 0.5), so line = suffix + 0.5
        line = float(total_str) + 0.5
        return {"market_type": "total", "away_abbr": away_abbr, "home_abbr": home_abbr, "line": line}

    return None


# ============================================================================
# KALSHI PRICE EXTRACTION
# ============================================================================

def kalshi_mid_price(mkt: Dict) -> float:
    """
    Extract midpoint probability from Kalshi market.

    Prefers (yes_bid + yes_ask) / 2, falls back to last_price, then 0.5.
    Clamped to [0.01, 0.99].
    """
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


def prob_to_american(p: float) -> str:
    """Convert probability (0-1) to American odds string."""
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
# PROBABILITY MODELS
# ============================================================================

def compute_win_probability(
    home_stats: Dict,
    away_stats: Dict,
    weights: Dict[str, float],
    kalshi_prior: float,
) -> float:
    """
    Compute P(home wins) using Gaussian model with Kalshi calibration.

    Model:
      point_spread = net_diff*w_net + HCA*w_hca + form_diff*12*w_form
      sigma = 11.0 * (1 + pace_norm*0.15*w_pace)
      p_raw = 0.5 + 0.5*erf(point_spread / (sigma*sqrt(2)))
      p_final = 0.8*p_raw + 0.2*kalshi_prior   # Shrink toward Kalshi

    If no team stats available: return kalshi_prior (edge = 0, honest uncertainty)
    """
    BASE_SIGMA = 11.0
    HCA_POINTS = 2.5

    if not home_stats or not away_stats:
        return kalshi_prior

    # Extract stats
    home_net = home_stats.get("net_rating") or 0
    away_net = away_stats.get("net_rating") or 0
    home_form = home_stats.get("last10_winpct") or 0.5
    away_form = away_stats.get("last10_winpct") or 0.5
    home_pace = home_stats.get("pace") or 98
    away_pace = away_stats.get("pace") or 98

    # Compute point spread
    net_diff = home_net - away_net
    form_diff = home_form - away_form

    point_spread = (
        net_diff * weights.get("w_net", 1.0) +
        HCA_POINTS * weights.get("w_hca", 1.0) +
        form_diff * 12.0 * weights.get("w_form", 1.0)
    )

    # Compute sigma with pace adjustment
    avg_pace = (home_pace + away_pace) / 2.0
    pace_norm = max(-1.0, min(1.0, (avg_pace - 98.0) / 12.0))
    sigma = BASE_SIGMA * (1.0 + pace_norm * 0.15 * weights.get("w_pace", 0.5))

    # Gaussian CDF
    z = point_spread / (sigma * math.sqrt(2))
    p_raw = 0.5 + 0.5 * math.erf(z)

    # Shrink toward Kalshi (80% model, 20% market)
    p_final = 0.8 * p_raw + 0.2 * kalshi_prior

    return max(0.01, min(0.99, p_final))


def compute_spread_probability(
    home_stats: Dict,
    away_stats: Dict,
    spread_line: float,
    weights: Dict[str, float],
    kalshi_prior: float,
) -> float:
    """
    Compute P(home covers spread) with Kalshi calibration.

    Model:
      point_spread = net_diff*w_net + HCA*w_hca - spread_line
      (Note: form is NOT used for spread)
    """
    BASE_SIGMA = 11.0
    HCA_POINTS = 2.5

    if not home_stats or not away_stats:
        return kalshi_prior

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
    p_raw = 0.5 + 0.5 * math.erf(z)

    p_final = 0.8 * p_raw + 0.2 * kalshi_prior

    return max(0.01, min(0.99, p_final))


def compute_total_probability(
    home_stats: Dict,
    away_stats: Dict,
    total_line: float,
    weights: Dict[str, float],
    kalshi_prior: float,
) -> float:
    """
    Compute P(total > total_line) with Kalshi calibration.

    Model:
      pred_total = ((home_off + away_off) / 100.0) * avg_pace
      p_raw = 0.5 + 0.5*erf((pred_total - total_line) / (12*sqrt(2)))
    """
    if not home_stats or not away_stats:
        return kalshi_prior

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
    p_raw = 0.5 + 0.5 * math.erf(z)

    p_final = 0.8 * p_raw + 0.2 * kalshi_prior

    return max(0.01, min(0.99, p_final))


# ============================================================================
# BET CLASSIFICATION
# ============================================================================

def classify_bet(edge: float, model_prob: float, kalshi_prob: float, market_type: str = "moneyline") -> str:
    """
    Classify a bet by edge and probabilities.

    Priority order (first match wins):
      1. HOMERUN: edge >= 0.10 AND model_prob >= 0.65
      2. UNDERVALUED: edge >= 0.05
      3. UNDERDOG: kalshi_prob <= 0.38 AND model_prob >= 0.48
      4. SHARP: 0.03 <= edge < 0.05
      5. FADE: edge < 0
      6. LOW EDGE: everything else

    Special case: totals capped at LOW EDGE unless edge >= TOTAL_MIN_EDGE
    """
    t = BET_THRESHOLDS

    if edge >= t["HOMERUN"]["edge"] and model_prob >= t["HOMERUN"]["model_prob"]:
        return "HOMERUN"
    elif kalshi_prob <= t["UNDERDOG"]["kalshi_prob"] and model_prob >= t["UNDERDOG"]["model_prob"]:
        return "UNDERDOG"
    elif edge >= t["UNDERVALUED"]["edge"]:
        return "UNDERVALUED"
    elif t["SHARP"]["edge_min"] <= edge < t["SHARP"]["edge_max"]:
        return "SHARP"
    elif edge < 0:
        return "FADE"
    else:
        # LOW EDGE default, but totals require higher threshold
        if market_type == "total" and edge < t["TOTAL_MIN_EDGE"]:
            return "LOW EDGE"
        return "LOW EDGE"


# ============================================================================
# ROW BUILDER
# ============================================================================

def build_all_rows(
    games: List[Dict],
    kalshi_by_type: Dict[str, List[Dict]],
    team_stats: Dict[str, Dict],
    weights: Dict[str, float],
) -> pd.DataFrame:
    """
    Build master DataFrame of all markets with model probabilities, edges, and classifications.
    """
    rows = []

    for game in games:
        game_id = game["game_id"]
        home_abbr = game["home_abbr"]
        away_abbr = game["away_abbr"]
        game_label = f"{away_abbr} @ {home_abbr}"

        home_stats = team_stats.get(home_abbr, {})
        away_stats = team_stats.get(away_abbr, {})

        # Loop through each market type and its markets
        for market_type, markets in kalshi_by_type.items():
            for mkt in markets:
                # Parse ticker
                parsed = parse_kalshi_ticker(mkt["ticker"])
                if not parsed:
                    continue

                # Match to game by team abbreviations
                if (parsed["away_abbr"] != away_abbr or parsed["home_abbr"] != home_abbr):
                    continue

                # Extract Kalshi price
                kalshi_prob = kalshi_mid_price(mkt)

                # Compute model probability
                if market_type == "moneyline":
                    model_prob = compute_win_probability(home_stats, away_stats, weights, kalshi_prob)
                elif market_type == "spread":
                    model_prob = compute_spread_probability(home_stats, away_stats, parsed["line"], weights, kalshi_prob)
                elif market_type == "total":
                    model_prob = compute_total_probability(home_stats, away_stats, parsed["line"], weights, kalshi_prob)
                else:
                    model_prob = 0.5

                # Compute edge and classify
                edge = model_prob - kalshi_prob
                category = classify_bet(edge, model_prob, kalshi_prob, market_type)

                rows.append({
                    "game_id": game_id,
                    "game_label": game_label,
                    "market_type": market_type,
                    "ticker": mkt["ticker"],
                    "title": mkt["title"][:70],  # Truncate for display
                    "kalshi_prob": kalshi_prob,
                    "model_prob": model_prob,
                    "edge": edge,
                    "category": category,
                    "american_odds": prob_to_american(kalshi_prob),
                    "volume": mkt.get("volume", 0),
                })

    return pd.DataFrame(rows) if rows else pd.DataFrame()
