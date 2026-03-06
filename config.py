"""
Configuration — API endpoints, constants, team maps, slider config.
"""

# ============================================================================
# API ENDPOINTS
# ============================================================================

KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
BALLDONTLIE_API_BASE = "https://api.balldontlie.io/v1"

# stats.nba.com endpoint — same URL used by NBA ML model
NBA_STATS_URL = (
    "https://stats.nba.com/stats/leaguedashteamstats"
    "?Conference=&DateFrom=&DateTo=&Division=&GameScope=&GameSegment="
    "&Height=&ISTRound=&LastNGames=0&LeagueID=00&Location=&MeasureType=Base"
    "&Month=0&OpponentTeamID=0&Outcome=&PORound=0&PaceAdjust=N&PerMode=PerGame"
    "&Period=0&PlayerExperience=&PlayerPosition=&PlusMinus=N&Rank=N"
    "&Season={season}&SeasonSegment=&SeasonType=Regular+Season"
    "&ShotClockRange=&StarterBench=&TeamID=0&TwoWay=0&VsConference=&VsDivision="
)

NBA_STATS_HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0.1 Safari/605.1.15"
    ),
}

# ============================================================================
# TEAM MAPPINGS
# ============================================================================

# 3-letter Kalshi/BallDontLie abbreviation → NBA full team name
ABBREV_TO_FULL = {
    "ATL": "Atlanta Hawks",
    "BOS": "Boston Celtics",
    "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets",
    "CHI": "Chicago Bulls",
    "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks",
    "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors",
    "HOU": "Houston Rockets",
    "IND": "Indiana Pacers",
    "LAC": "LA Clippers",
    "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks",
    "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans",
    "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic",
    "PHI": "Philadelphia 76ers",
    "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers",
    "SAC": "Sacramento Kings",
    "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz",
    "WAS": "Washington Wizards",
}

# Reverse mapping: full name → abbrev
FULL_TO_ABBREV = {v: k for k, v in ABBREV_TO_FULL.items()}
# Handle alternate spellings returned by stats.nba.com
FULL_TO_ABBREV["Los Angeles Clippers"] = "LAC"
FULL_TO_ABBREV["Los Angeles Lakers"] = "LAL"

# All valid 3-letter codes
TEAM_ABBREVS = set(ABBREV_TO_FULL.keys())

# ============================================================================
# BET CATEGORIES
# ============================================================================

CATEGORY_COLORS = {
    "HOMERUN": "#f85149",
    "UNDERVALUED": "#3fb950",
    "UNDERDOG": "#58a6ff",
    "SHARP": "#d29922",
    "FADE": "#6e7681",
    "LOW EDGE": "#30363d",
}

BET_THRESHOLDS = {
    "HOMERUN":    {"edge": 0.10, "model_prob": 0.65},
    "UNDERVALUED": {"edge": 0.05},
    "UNDERDOG":   {"kalshi_prob": 0.38, "model_prob": 0.48},
    "SHARP":      {"edge_min": 0.03, "edge_max": 0.05},
    "TOTAL_MIN_EDGE": 0.08,
}

# ============================================================================
# METRIC WEIGHT SLIDERS (for moneyline tilt on top of XGBoost)
# ============================================================================

METRIC_WEIGHTS_CONFIG = {
    "w_xgb": {
        "label": "🤖 XGBoost Weight",
        "default": 1.0,
        "description": (
            "Weight of the 68.9%-accurate XGBoost ML model prediction. "
            "At 1.0 the model runs at full strength. "
            "Lower to rely more on Kalshi market prices (edge → 0). "
            "Above 1.0 extrapolates beyond the model probability and is clamped to [1%, 99%]."
        ),
    },
}
