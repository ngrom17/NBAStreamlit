"""
Prediction engine — wraps kyleskom/NBA-ML XGBoost model.
Produces P(home_win), P(away_win), P(over), P(under) per game.
Adds Expected Value and Kelly Criterion vs Kalshi prices.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import xgboost as xgb

DASHBOARD_DIR = Path(__file__).resolve().parent
MODEL_DIR = DASHBOARD_DIR / "Models" / "XGBoost_Models"
ACCURACY_PATTERN = re.compile(r"XGBoost_(\d+(?:\.\d+)?)%_")

from config import (
    ABBREV_TO_FULL,
    BET_THRESHOLDS,
    CATEGORY_COLORS,
    METRIC_WEIGHTS_CONFIG,
)

# ============================================================================
# MODEL LOADING (lazy, cached in module globals)
# ============================================================================

_xgb_ml = None
_xgb_uo = None
_xgb_ml_cal = None
_xgb_uo_cal = None


def _select_model_path(kind: str) -> Path:
    candidates = list(MODEL_DIR.glob(f"*{kind}*.json"))
    if not candidates:
        raise FileNotFoundError(f"No XGBoost {kind} model in {MODEL_DIR}")

    def score(p):
        m = ACCURACY_PATTERN.search(p.name)
        return (float(m.group(1)) if m else 0.0, p.stat().st_mtime)

    return max(candidates, key=score)


def _load_models():
    global _xgb_ml, _xgb_uo
    if _xgb_ml is None:
        ml_path = _select_model_path("ML")
        _xgb_ml = xgb.Booster()
        _xgb_ml.load_model(str(ml_path))
    if _xgb_uo is None:
        uo_path = _select_model_path("UO")
        _xgb_uo = xgb.Booster()
        _xgb_uo.load_model(str(uo_path))


def _predict_probs(model, data: np.ndarray) -> np.ndarray:
    """
    Run XGBoost prediction. Returns shape (n_games, 2): [[P(class0), P(class1)], ...]
    ML model: positive class = home team wins (1).
    UO model: positive class = over (1).
    """
    raw = model.predict(xgb.DMatrix(data))
    if raw.ndim == 1:
        return np.column_stack([1.0 - raw, raw])
    return raw


# ============================================================================
# KALSHI TICKER PARSING
# ============================================================================

def parse_kalshi_ticker(ticker: str) -> Optional[Dict]:
    """Parse Kalshi NBA ticker → {market_type, away_abbr, home_abbr, line}."""
    ml = re.match(r"KXNBAGAME-\d{2}[A-Z]{3}\d{2}([A-Z]{2,3})([A-Z]{2,3})-[A-Z]{2,3}$", ticker)
    if ml:
        away, home = ml.groups()
        return {"market_type": "moneyline", "away_abbr": away, "home_abbr": home, "line": None}

    sp = re.match(r"KXNBASPREAD-\d{2}[A-Z]{3}\d{2}([A-Z]{2,3})([A-Z]{2,3})-[A-Z]{2,3}(\d+)$", ticker)
    if sp:
        away, home, n = sp.groups()
        return {"market_type": "spread", "away_abbr": away, "home_abbr": home, "line": float(n) + 0.5}

    tot = re.match(r"KXNBATOTAL-\d{2}[A-Z]{3}\d{2}([A-Z]{2,3})([A-Z]{2,3})-(\d+)$", ticker)
    if tot:
        away, home, n = tot.groups()
        return {"market_type": "total", "away_abbr": away, "home_abbr": home, "line": float(n) + 0.5}

    return None


def kalshi_mid_price(mkt: Dict) -> float:
    bid, ask, last = mkt.get("yes_bid", 0), mkt.get("yes_ask", 0), mkt.get("last_price", 0)
    if bid > 0 and ask > 0:
        return max(0.01, min(0.99, (bid + ask) / 2.0))
    if last > 0:
        return max(0.01, min(0.99, last))
    return 0.5


def prob_to_american(p: float) -> str:
    if p >= 0.99:
        return "-10000"
    if p <= 0.01:
        return "+10000"
    if p > 0.5:
        return str(-round((p / (1 - p)) * 100))
    return f"+{round(((1 - p) / p) * 100)}"


# ============================================================================
# EXPECTED VALUE & KELLY CRITERION
# ============================================================================

def _american_to_decimal(american_odds: float) -> float:
    """Convert American odds to decimal (includes stake: +150 → 2.50, -110 → 1.91)."""
    if american_odds >= 100:
        return round(1 + american_odds / 100, 2)
    return round(1 + 100 / abs(american_odds), 2)


def expected_value(p_win: float, american_odds: float) -> float:
    """EV = (P_win × payout) - (P_loss × 100) per $100 wagered."""
    p_loss = 1 - p_win
    payout = american_odds if american_odds > 0 else (100 / abs(american_odds)) * 100
    return round(p_win * payout - p_loss * 100, 2)


def kelly_criterion(american_odds: float, p_win: float) -> float:
    """Kelly fraction as % of bankroll. Returns 0 if negative."""
    decimal = _american_to_decimal(american_odds)
    fraction = round(100 * (decimal * p_win - (1 - p_win)) / decimal, 2)
    return max(fraction, 0.0)


# ============================================================================
# BET CLASSIFICATION
# ============================================================================

def classify_bet(edge: float, model_prob: float, kalshi_prob: float, market_type: str = "moneyline") -> str:
    t = BET_THRESHOLDS
    if edge >= t["HOMERUN"]["edge"] and model_prob >= t["HOMERUN"]["model_prob"]:
        return "HOMERUN"
    if kalshi_prob <= t["UNDERDOG"]["kalshi_prob"] and model_prob >= t["UNDERDOG"]["model_prob"]:
        return "UNDERDOG"
    if edge >= t["UNDERVALUED"]["edge"]:
        return "UNDERVALUED"
    if t["SHARP"]["edge_min"] <= edge < t["SHARP"]["edge_max"]:
        return "SHARP"
    if edge < 0:
        return "FADE"
    if market_type == "total" and edge < t["TOTAL_MIN_EDGE"]:
        return "LOW EDGE"
    return "LOW EDGE"


# ============================================================================
# GAME FEATURE BUILDER
# ============================================================================

def build_game_features(
    home_full: str,
    away_full: str,
    stats_df: pd.DataFrame,
    schedule_df,
    today: datetime,
) -> Optional[pd.Series]:
    """
    Build 106-feature vector matching the XGBoost training format exactly:
    home_stats (52) + away_stats (52) + Days-Rest-Home + Days-Rest-Away.
    stats_df must be raw 54-col DataFrame (TEAM_ID, TEAM_NAME, 52 stats).
    """
    from fetch import compute_rest_days

    home_rows = stats_df[stats_df["TEAM_NAME"] == home_full]
    away_rows = stats_df[stats_df["TEAM_NAME"] == away_full]
    if home_rows.empty or away_rows.empty:
        return None

    home_series = home_rows.iloc[0]
    away_series = away_rows.iloc[0]

    # Concatenate home + away — duplicate TEAM_ID/TEAM_NAME columns are handled by drop
    combined = pd.concat([home_series, away_series])
    # Drop both TEAM_ID and TEAM_NAME occurrences (matching training pipeline)
    combined = combined.drop(labels=["TEAM_ID", "TEAM_NAME"], errors="ignore")
    combined["Days-Rest-Home"] = compute_rest_days(home_full, today, schedule_df)
    combined["Days-Rest-Away"] = compute_rest_days(away_full, today, schedule_df)

    return combined


# ============================================================================
# MASTER ROW BUILDER
# ============================================================================

def build_all_rows(
    games: List[Dict],
    kalshi_by_type: Dict[str, List[Dict]],
    stats_df: Optional[pd.DataFrame],
    schedule_df,
    weights: Dict[str, float],
) -> pd.DataFrame:
    """
    Build master DataFrame with XGBoost predictions, EV, Kelly, edges for all Kalshi markets.
    """
    _load_models()
    today = datetime.today()
    rows = []

    for game in games:
        home_abbr = game["home_abbr"]
        away_abbr = game["away_abbr"]
        game_label = f"{away_abbr} @ {home_abbr}"

        home_full = ABBREV_TO_FULL.get(home_abbr)
        away_full = ABBREV_TO_FULL.get(away_abbr)

        # Build feature vector if stats are available
        features = None
        home_prob_xgb = 0.5  # default: even odds
        away_prob_xgb = 0.5
        stats_loaded = stats_df is not None and not stats_df.empty and home_full and away_full

        if stats_loaded:
            feat = build_game_features(home_full, away_full, stats_df, schedule_df, today)
            if feat is not None:
                features = feat

        if features is not None:
            try:
                data = features.values.astype(float).reshape(1, -1)
                ml_probs = _predict_probs(_xgb_ml, data)[0]
                # ml_probs[0] = P(away), ml_probs[1] = P(home)
                away_prob_xgb = float(ml_probs[0])
                home_prob_xgb = float(ml_probs[1])
            except Exception:
                pass

        for market_type, markets in kalshi_by_type.items():
            for mkt in markets:
                parsed = parse_kalshi_ticker(mkt["ticker"])
                if not parsed:
                    continue
                if parsed["away_abbr"] != away_abbr or parsed["home_abbr"] != home_abbr:
                    continue

                kalshi_prob = kalshi_mid_price(mkt)
                kalshi_american = int(prob_to_american(kalshi_prob).replace("+", ""))

                if market_type == "moneyline":
                    # YES contract is for whichever team is in the ticker suffix
                    # parsed already has home_abbr; suffix matches home or away
                    ticker_team = mkt["ticker"].split("-")[-1]
                    if ticker_team == parsed["home_abbr"]:
                        model_prob = home_prob_xgb
                    else:
                        model_prob = away_prob_xgb

                elif market_type == "spread":
                    # Use XGBoost home_prob adjusted by spread line
                    if features is not None and parsed["line"] is not None:
                        try:
                            frame = features.copy()
                            frame["OU"] = parsed["line"]
                            data = frame.values.astype(float).reshape(1, -1)
                            ou_probs = _predict_probs(_xgb_uo, data)[0]
                            model_prob = float(ou_probs[1])  # P(home covers)
                        except Exception:
                            model_prob = kalshi_prob  # fallback: no edge
                    else:
                        model_prob = kalshi_prob

                elif market_type == "total":
                    if features is not None and parsed["line"] is not None:
                        try:
                            frame = features.copy()
                            frame["OU"] = parsed["line"]
                            data = frame.values.astype(float).reshape(1, -1)
                            ou_probs = _predict_probs(_xgb_uo, data)[0]
                            model_prob = float(ou_probs[1])  # P(over)
                        except Exception:
                            model_prob = kalshi_prob
                    else:
                        model_prob = kalshi_prob
                else:
                    model_prob = kalshi_prob

                # Apply weight tilts on top of XGBoost base
                w_xgb = weights.get("w_xgb", 1.0)
                # Blend XGBoost prediction with Kalshi market (w_xgb controls strength)
                blended = w_xgb * model_prob + (1 - w_xgb) * kalshi_prob
                model_prob = max(0.01, min(0.99, blended))

                edge = model_prob - kalshi_prob
                category = classify_bet(edge, model_prob, kalshi_prob, market_type)

                # EV and Kelly vs Kalshi American odds
                try:
                    ev = expected_value(model_prob, kalshi_american)
                    kelly = kelly_criterion(kalshi_american, model_prob)
                except Exception:
                    ev = 0.0
                    kelly = 0.0

                rows.append({
                    "game_id":      game["game_id"],
                    "game_label":   game_label,
                    "market_type":  market_type,
                    "ticker":       mkt["ticker"],
                    "title":        mkt["title"][:70],
                    "kalshi_prob":  kalshi_prob,
                    "model_prob":   model_prob,
                    "edge":         edge,
                    "category":     category,
                    "american_odds": prob_to_american(kalshi_prob),
                    "ev":           ev,
                    "kelly":        kelly,
                    "volume":       mkt.get("volume", 0),
                    "stats_loaded": stats_loaded,
                })

    return pd.DataFrame(rows) if rows else pd.DataFrame()
