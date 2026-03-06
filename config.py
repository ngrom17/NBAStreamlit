"""
Configuration — API endpoints, constants, team abbreviations, slider config.
"""

# API Endpoints
KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
BALLDONTLIE_API_BASE = "https://api.balldontlie.io/v1"

# 30 NBA team 3-letter abbreviations
TEAM_ABBREVS = {
    "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW",
    "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK",
    "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS"
}

# Bet category colors (hex)
CATEGORY_COLORS = {
    "HOMERUN": "#f85149",
    "UNDERVALUED": "#3fb950",
    "UNDERDOG": "#58a6ff",
    "SHARP": "#d29922",
    "FADE": "#6e7681",
    "LOW EDGE": "#30363d",
}

# Metric weight slider configuration
METRIC_WEIGHTS_CONFIG = {
    "w_net": {
        "label": "📊 Net Rating",
        "default": 1.0,
        "description": "Team strength indicator: offensive rating minus defensive rating. Higher = better team. Adjust UP if you believe team quality is predictive of outcomes."
    },
    "w_form": {
        "label": "🔥 Recent Form",
        "default": 1.0,
        "description": "Hot/cold momentum over last 10 games. Adjust UP if you think current form beats historical strength, DOWN if form is noise."
    },
    "w_hca": {
        "label": "🏠 Home Court",
        "default": 1.0,
        "description": "Home team inherent advantage (~2.5 pts/game). Adjust UP for more home bias, DOWN to ignore crowd effects."
    },
    "w_pace": {
        "label": "⚡ Pace Variance",
        "default": 0.5,
        "description": "Game tempo impact on win probability spread. Fast-paced games have higher variance. Adjust UP if pace affects predictability."
    },
    "w_rest": {
        "label": "😴 Rest Advantage",
        "default": 0.5,
        "description": "Back-to-back fatigue penalty & rest day bonus. Adjust UP if B2B/rest days are significant, DOWN if teams manage load well."
    },
}

# Classification thresholds
BET_THRESHOLDS = {
    "HOMERUN": {"edge": 0.10, "model_prob": 0.65},
    "UNDERVALUED": {"edge": 0.05},
    "UNDERDOG": {"kalshi_prob": 0.38, "model_prob": 0.48},
    "SHARP": {"edge_min": 0.03, "edge_max": 0.05},
    "FADE": {"edge_max": 0},
    "TOTAL_MIN_EDGE": 0.08,  # totals capped at LOW EDGE unless edge >= this
}
