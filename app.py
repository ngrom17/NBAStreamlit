"""
Unified Sports Betting Dashboard v4.0 (Desktop, PrizePicks-style)
MLB · NBA · NFL · CBB · CFB

Focus:
- Accuracy-first models (advanced stats where available; safe fallbacks)
- Clean pick cards + 2–3 bullet "why" blurbs
- Decision filters (min confidence + min model gap)
- Sidebar = decision tools (record, filters, injuries, line move tracker)
- NBA Props tab (Kalshi-style multi-level lines; synthetic levels for stability)
- Hardened St John's normalization
- Validation layer (dedupe matchups, hide low-edge picks)

Notes:
- "Option A": sport-specific view (sidebar sport drives the app).
- Props use synthetic Kalshi line levels (no fragile scraping).
"""
import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta
from io import StringIO
import json, os, time, warnings, re, math
warnings.filterwarnings("ignore")

# ── PAGE CONFIG ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Betting Dashboard", page_icon="🏆", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@400;500&display=swap');
html,body,[class*="css"]{font-family:'Syne',sans-serif;}
.stApp{background:#0a0e1a;color:#e8eaf0;}
section[data-testid="stSidebar"]{background:#0f1525!important;border-right:1px solid #1e2640;}
section[data-testid="stSidebar"] *{color:#c8ccd8!important;}
[data-testid="metric-container"]{background:#131929;border:1px solid #1e2a45;border-radius:12px;padding:16px;}
[data-testid="stMetricValue"]{font-family:'DM Mono',monospace!important;font-size:1.8rem!important;color:#7eeaff!important;}
[data-testid="stMetricLabel"]{color:#8892a4!important;font-size:0.75rem!important;}
.dataframe{font-family:'DM Mono',monospace;font-size:0.82rem;}
thead tr th{background:#131929!important;color:#7eeaff!important;font-family:'Syne',sans-serif!important;font-weight:700!important;border-bottom:2px solid #1e2a45!important;}
tbody tr:hover td{background:#1a2235!important;}
.stButton button{background:linear-gradient(135deg,#1a6fff,#0ea5e9)!important;color:white!important;border:none!important;border-radius:8px!important;font-family:'Syne',sans-serif!important;font-weight:700!important;padding:0.5rem 1.5rem!important;}
.stTabs [data-baseweb="tab-list"]{background:#0f1525;border-radius:10px;padding:4px;gap:4px;}
.stTabs [data-baseweb="tab"]{background:transparent;color:#8892a4!important;border-radius:8px;font-family:'Syne',sans-serif;font-weight:600;}
.stTabs [aria-selected="true"]{background:#1a2640!important;color:#7eeaff!important;}
.score-card{background:#131929;border:1px solid #1e2a45;border-radius:12px;padding:14px 18px;margin-bottom:8px;}
.score-card.live{border-color:#ff6b6b44;background:#1a1020;}
.score-card.final{border-color:#4ade8033;}
.risk-low{display:inline-block;background:#1a3a2a;color:#4ade80;padding:2px 8px;border-radius:6px;font-size:0.70rem;font-weight:700;font-family:'DM Mono',monospace;}
.risk-med{display:inline-block;background:#2a2a1a;color:#facc15;padding:2px 8px;border-radius:6px;font-size:0.70rem;font-weight:700;font-family:'DM Mono',monospace;}
.risk-high{display:inline-block;background:#2a1a1a;color:#f87171;padding:2px 8px;border-radius:6px;font-size:0.70rem;font-weight:700;font-family:'DM Mono',monospace;}
.badge-live{background:#3a1a1a;color:#ff6b6b;padding:3px 10px;border-radius:20px;font-size:0.78rem;font-weight:700;animation:pulse 1.5s infinite;}
.badge-final{background:#1a3a2a;color:#4ade80;padding:3px 10px;border-radius:20px;font-size:0.78rem;font-weight:700;}
.badge-pre{background:#1e2640;color:#94a3b8;padding:3px 10px;border-radius:20px;font-size:0.78rem;font-weight:700;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.6}}
hr{border-color:#1e2640!important;}
#MainMenu,footer{visibility:hidden;}
</style>
""", unsafe_allow_html=True)

# ── KEYS / FILES ─────────────────────────────────────────────────────────────
ODDS_API_KEY = "8762561865c3719f114b2d815aca3041"
CFBD_API_KEY = os.environ.get("CFBD_API_KEY", "")
WEATHER_KEY  = os.environ.get("WEATHER_API_KEY", "")
TRACKER_FILE = "picks_log.json"

SPORT_CONFIG = {
    "⚾ MLB": {"key":"baseball_mlb",          "espn_sport":"baseball",   "espn_league":"mlb",                     "label":"MLB"},
    "🏀 NBA": {"key":"basketball_nba",        "espn_sport":"basketball", "espn_league":"nba",                     "label":"NBA"},
    "🏈 NFL": {"key":"americanfootball_nfl",  "espn_sport":"football",   "espn_league":"nfl",                     "label":"NFL"},
    "🏀 CBB": {"key":"basketball_ncaab",      "espn_sport":"basketball", "espn_league":"mens-college-basketball", "label":"CBB"},
    "🏈 CFB": {"key":"americanfootball_ncaaf","espn_sport":"football",   "espn_league":"college-football",        "label":"CFB"},
}
SPORT_ICONS = {"MLB":"⚾","NBA":"🏀","NFL":"🏈","CBB":"🏀","CFB":"🏈"}

# ── TIMEZONE (Eastern) ───────────────────────────────────────────────────────
def _is_edt(dt=None):
    if dt is None: dt = datetime.utcnow()
    y = dt.year
    mar = datetime(y, 3, 8)
    while mar.weekday() != 6: mar += timedelta(days=1)   # 2nd Sunday March
    nov = datetime(y, 11, 1)
    while nov.weekday() != 6: nov += timedelta(days=1)   # 1st Sunday Nov
    return mar <= dt.replace(tzinfo=None) < nov

def now_est():
    dt = datetime.utcnow()
    offset = timedelta(hours=-4) if _is_edt(dt) else timedelta(hours=-5)
    est = dt + offset
    suffix = "EDT" if _is_edt(dt) else "EST"
    return est.strftime("%-I:%M %p") + f" {suffix}"

def today_est():
    dt = datetime.utcnow()
    offset = timedelta(hours=-4) if _is_edt(dt) else timedelta(hours=-5)
    return (dt + offset).date()

def utc_to_est(utc_str):
    try:
        utc_str = utc_str.replace("Z","").replace("+00:00","")
        dt = datetime.fromisoformat(utc_str)
        offset = timedelta(hours=-4) if _is_edt(dt) else timedelta(hours=-5)
        est = dt + offset
        suffix = "EDT" if _is_edt(dt) else "EST"
        return est.strftime("%-I:%M %p") + f" {suffix}"
    except:
        return ""

# ── TEAM NORMALIZATION (hardened St John's) ──────────────────────────────────
NORM_MAP = {
    "st john's red storm":"St John's","st. john's red storm":"St John's",
    "saint john's red storm":"St John's","saint john's":"St John's",
    "st. john's":"St John's","st johns":"St John's","st. johns":"St John's",
    "st john's":"St John's","stjohn's":"St John's",
    "unc tar heels":"North Carolina","north carolina tar heels":"North Carolina",
    "usc trojans":"USC","miami fl":"Miami","miami (fl)":"Miami",
    "ole miss rebels":"Ole Miss","mississippi rebels":"Ole Miss",
    "pitt panthers":"Pittsburgh","pitt":"Pittsburgh",
    "uconn huskies":"UConn","connecticut":"UConn",
}
def normalize_team(name: str) -> str:
    s = str(name).strip()
    nl = s.lower()
    if nl in NORM_MAP:
        return NORM_MAP[nl]
    if re.search(r"st\.?\s*johns?'?s?\b", nl, re.IGNORECASE):
        return "St John's"
    return s

# ── FALLBACK STATS (safe defaults) ───────────────────────────────────────────
# MLB v4: Pythagorean Win%, Bullpen FIP, wRC+, Starter FIP
MLB_FB = {'LAD': {'win_pct': 0.642, 'run_diff_pg': 1.8, 'bullpen_era': 3.45, 'last10': 0.7, 'ops': 0.788},
 'ATL': {'win_pct': 0.617, 'run_diff_pg': 1.5, 'bullpen_era': 3.62, 'last10': 0.6, 'ops': 0.762},
 'PHI': {'win_pct': 0.599, 'run_diff_pg': 1.3, 'bullpen_era': 3.55, 'last10': 0.6, 'ops': 0.758},
 'BAL': {'win_pct': 0.58, 'run_diff_pg': 1.1, 'bullpen_era': 3.7, 'last10': 0.5, 'ops': 0.745},
 'HOU': {'win_pct': 0.574, 'run_diff_pg': 1.0, 'bullpen_era': 3.8, 'last10': 0.5, 'ops': 0.741},
 'NYY': {'win_pct': 0.568, 'run_diff_pg': 0.9, 'bullpen_era': 3.9, 'last10': 0.5, 'ops': 0.738},
 'MIL': {'win_pct': 0.562, 'run_diff_pg': 0.8, 'bullpen_era': 3.95, 'last10': 0.5, 'ops': 0.735},
 'CLE': {'win_pct': 0.556, 'run_diff_pg': 0.7, 'bullpen_era': 4.0, 'last10': 0.5, 'ops': 0.731},
 'MIN': {'win_pct': 0.549, 'run_diff_pg': 0.5, 'bullpen_era': 4.1, 'last10': 0.4, 'ops': 0.728},
 'BOS': {'win_pct': 0.543, 'run_diff_pg': 0.4, 'bullpen_era': 4.15, 'last10': 0.5, 'ops': 0.725},
 'SD': {'win_pct': 0.537, 'run_diff_pg': 0.3, 'bullpen_era': 4.2, 'last10': 0.4, 'ops': 0.721},
 'SEA': {'win_pct': 0.531, 'run_diff_pg': 0.2, 'bullpen_era': 4.25, 'last10': 0.4, 'ops': 0.718},
 'TOR': {'win_pct': 0.525, 'run_diff_pg': 0.1, 'bullpen_era': 4.3, 'last10': 0.4, 'ops': 0.715},
 'TB': {'win_pct': 0.519, 'run_diff_pg': 0.0, 'bullpen_era': 4.35, 'last10': 0.4, 'ops': 0.711},
 'SF': {'win_pct': 0.512, 'run_diff_pg': -0.1, 'bullpen_era': 4.4, 'last10': 0.4, 'ops': 0.708},
 'NYM': {'win_pct': 0.506, 'run_diff_pg': -0.2, 'bullpen_era': 4.5, 'last10': 0.4, 'ops': 0.705},
 'STL': {'win_pct': 0.5, 'run_diff_pg': -0.3, 'bullpen_era': 4.55, 'last10': 0.3, 'ops': 0.701},
 'DET': {'win_pct': 0.494, 'run_diff_pg': -0.4, 'bullpen_era': 4.6, 'last10': 0.3, 'ops': 0.698},
 'TEX': {'win_pct': 0.488, 'run_diff_pg': -0.5, 'bullpen_era': 4.7, 'last10': 0.3, 'ops': 0.695},
 'ARI': {'win_pct': 0.481, 'run_diff_pg': -0.6, 'bullpen_era': 4.75, 'last10': 0.3, 'ops': 0.691},
 'CHC': {'win_pct': 0.475, 'run_diff_pg': -0.7, 'bullpen_era': 4.8, 'last10': 0.3, 'ops': 0.688},
 'CIN': {'win_pct': 0.469, 'run_diff_pg': -0.8, 'bullpen_era': 4.9, 'last10': 0.3, 'ops': 0.685},
 'KC': {'win_pct': 0.463, 'run_diff_pg': -0.9, 'bullpen_era': 4.95, 'last10': 0.3, 'ops': 0.681},
 'MIA': {'win_pct': 0.457, 'run_diff_pg': -1.0, 'bullpen_era': 5.0, 'last10': 0.2, 'ops': 0.678},
 'PIT': {'win_pct': 0.451, 'run_diff_pg': -1.1, 'bullpen_era': 5.1, 'last10': 0.2, 'ops': 0.675},
 'LAA': {'win_pct': 0.444, 'run_diff_pg': -1.2, 'bullpen_era': 5.15, 'last10': 0.2, 'ops': 0.671},
 'OAK': {'win_pct': 0.438, 'run_diff_pg': -1.3, 'bullpen_era': 5.2, 'last10': 0.2, 'ops': 0.668},
 'COL': {'win_pct': 0.42, 'run_diff_pg': -1.8, 'bullpen_era': 5.5, 'last10': 0.2, 'ops': 0.645},
 'WSH': {'win_pct': 0.432, 'run_diff_pg': -1.4, 'bullpen_era': 5.3, 'last10': 0.2, 'ops': 0.661},
 'CWS': {'win_pct': 0.4, 'run_diff_pg': -2.0, 'bullpen_era': 5.8, 'last10': 0.1, 'ops': 0.621}}
# Expand with your larger dict if desired; these are safe placeholders.

MLB_NAME_MAP = {'Arizona Diamondbacks': 'ARI',
 'Atlanta Braves': 'ATL',
 'Baltimore Orioles': 'BAL',
 'Boston Red Sox': 'BOS',
 'Chicago Cubs': 'CHC',
 'Chicago White Sox': 'CWS',
 'Cincinnati Reds': 'CIN',
 'Cleveland Guardians': 'CLE',
 'Colorado Rockies': 'COL',
 'Detroit Tigers': 'DET',
 'Houston Astros': 'HOU',
 'Kansas City Royals': 'KC',
 'Los Angeles Angels': 'LAA',
 'Los Angeles Dodgers': 'LAD',
 'Miami Marlins': 'MIA',
 'Milwaukee Brewers': 'MIL',
 'Minnesota Twins': 'MIN',
 'New York Mets': 'NYM',
 'New York Yankees': 'NYY',
 'Oakland Athletics': 'OAK',
 'Philadelphia Phillies': 'PHI',
 'Pittsburgh Pirates': 'PIT',
 'San Diego Padres': 'SD',
 'San Francisco Giants': 'SF',
 'Seattle Mariners': 'SEA',
 'St. Louis Cardinals': 'STL',
 'Tampa Bay Rays': 'TB',
 'Texas Rangers': 'TEX',
 'Toronto Blue Jays': 'TOR',
 'Washington Nationals': 'WSH',
 'Athletics': 'OAK',
 'Guardians': 'CLE'}

# NBA v4: includes PIE%, TS%, 3PT%, Def Reb%, TO Rate (fallback only)
NBA_FB = {'Boston Celtics': {'net_rtg': 10.2,
                    'off_rtg': 122.5,
                    'def_rtg': 112.3,
                    'pace': 99.1,
                    'last10': 0.7,
                    'wins': 58,
                    'losses': 24},
 'Oklahoma City Thunder': {'net_rtg': 9.8,
                           'off_rtg': 120.8,
                           'def_rtg': 111.0,
                           'pace': 100.2,
                           'last10': 0.7,
                           'wins': 57,
                           'losses': 25},
 'Cleveland Cavaliers': {'net_rtg': 9.1,
                         'off_rtg': 118.9,
                         'def_rtg': 109.8,
                         'pace': 97.5,
                         'last10': 0.6,
                         'wins': 55,
                         'losses': 27},
 'Minnesota Timberwolves': {'net_rtg': 8.4,
                            'off_rtg': 116.2,
                            'def_rtg': 107.8,
                            'pace': 98.8,
                            'last10': 0.6,
                            'wins': 53,
                            'losses': 29},
 'Denver Nuggets': {'net_rtg': 7.9,
                    'off_rtg': 117.8,
                    'def_rtg': 109.9,
                    'pace': 98.2,
                    'last10': 0.6,
                    'wins': 51,
                    'losses': 31},
 'New York Knicks': {'net_rtg': 7.2,
                     'off_rtg': 115.4,
                     'def_rtg': 108.2,
                     'pace': 96.8,
                     'last10': 0.5,
                     'wins': 49,
                     'losses': 33},
 'Memphis Grizzlies': {'net_rtg': 6.8,
                       'off_rtg': 116.1,
                       'def_rtg': 109.3,
                       'pace': 101.5,
                       'last10': 0.5,
                       'wins': 48,
                       'losses': 34},
 'LA Clippers': {'net_rtg': 6.1,
                 'off_rtg': 114.8,
                 'def_rtg': 108.7,
                 'pace': 97.2,
                 'last10': 0.5,
                 'wins': 46,
                 'losses': 36},
 'Golden State Warriors': {'net_rtg': 5.4,
                           'off_rtg': 116.2,
                           'def_rtg': 110.8,
                           'pace': 99.8,
                           'last10': 0.5,
                           'wins': 44,
                           'losses': 38},
 'Houston Rockets': {'net_rtg': 5.1,
                     'off_rtg': 113.5,
                     'def_rtg': 108.4,
                     'pace': 100.4,
                     'last10': 0.5,
                     'wins': 43,
                     'losses': 39},
 'Indiana Pacers': {'net_rtg': 4.8,
                    'off_rtg': 118.9,
                    'def_rtg': 114.1,
                    'pace': 104.2,
                    'last10': 0.5,
                    'wins': 42,
                    'losses': 40},
 'Dallas Mavericks': {'net_rtg': 4.2,
                      'off_rtg': 115.1,
                      'def_rtg': 110.9,
                      'pace': 98.5,
                      'last10': 0.4,
                      'wins': 40,
                      'losses': 42},
 'Milwaukee Bucks': {'net_rtg': 3.8,
                     'off_rtg': 114.8,
                     'def_rtg': 111.0,
                     'pace': 99.1,
                     'last10': 0.4,
                     'wins': 39,
                     'losses': 43},
 'Phoenix Suns': {'net_rtg': 3.1,
                  'off_rtg': 113.9,
                  'def_rtg': 110.8,
                  'pace': 98.8,
                  'last10': 0.4,
                  'wins': 37,
                  'losses': 45},
 'Sacramento Kings': {'net_rtg': 2.4,
                      'off_rtg': 115.2,
                      'def_rtg': 112.8,
                      'pace': 100.5,
                      'last10': 0.4,
                      'wins': 35,
                      'losses': 47},
 'Miami Heat': {'net_rtg': 1.8,
                'off_rtg': 111.8,
                'def_rtg': 110.0,
                'pace': 96.5,
                'last10': 0.4,
                'wins': 34,
                'losses': 48},
 'Orlando Magic': {'net_rtg': 1.2,
                   'off_rtg': 108.9,
                   'def_rtg': 107.7,
                   'pace': 95.8,
                   'last10': 0.4,
                   'wins': 33,
                   'losses': 49},
 'Los Angeles Lakers': {'net_rtg': 0.8,
                        'off_rtg': 112.4,
                        'def_rtg': 111.6,
                        'pace': 99.2,
                        'last10': 0.4,
                        'wins': 32,
                        'losses': 50},
 'Atlanta Hawks': {'net_rtg': -0.5,
                   'off_rtg': 113.8,
                   'def_rtg': 114.3,
                   'pace': 101.2,
                   'last10': 0.3,
                   'wins': 30,
                   'losses': 52},
 'Brooklyn Nets': {'net_rtg': -2.1,
                   'off_rtg': 109.5,
                   'def_rtg': 111.6,
                   'pace': 98.5,
                   'last10': 0.3,
                   'wins': 27,
                   'losses': 55},
 'Toronto Raptors': {'net_rtg': -2.8,
                     'off_rtg': 110.2,
                     'def_rtg': 113.0,
                     'pace': 97.8,
                     'last10': 0.3,
                     'wins': 25,
                     'losses': 57},
 'Chicago Bulls': {'net_rtg': -3.4,
                   'off_rtg': 111.8,
                   'def_rtg': 115.2,
                   'pace': 98.9,
                   'last10': 0.3,
                   'wins': 24,
                   'losses': 58},
 'Philadelphia 76ers': {'net_rtg': -3.0,
                        'off_rtg': 110.5,
                        'def_rtg': 113.5,
                        'pace': 97.8,
                        'last10': 0.3,
                        'wins': 25,
                        'losses': 57},
 'Utah Jazz': {'net_rtg': -5.1,
               'off_rtg': 109.8,
               'def_rtg': 114.9,
               'pace': 99.5,
               'last10': 0.2,
               'wins': 21,
               'losses': 61},
 'New Orleans Pelicans': {'net_rtg': -5.8,
                          'off_rtg': 109.2,
                          'def_rtg': 115.0,
                          'pace': 98.2,
                          'last10': 0.2,
                          'wins': 20,
                          'losses': 62},
 'San Antonio Spurs': {'net_rtg': -6.5,
                       'off_rtg': 108.5,
                       'def_rtg': 115.0,
                       'pace': 99.8,
                       'last10': 0.2,
                       'wins': 19,
                       'losses': 63},
 'Portland Trail Blazers': {'net_rtg': -7.2,
                            'off_rtg': 108.1,
                            'def_rtg': 115.3,
                            'pace': 100.1,
                            'last10': 0.2,
                            'wins': 18,
                            'losses': 64},
 'Charlotte Hornets': {'net_rtg': -8.1,
                       'off_rtg': 107.8,
                       'def_rtg': 115.9,
                       'pace': 99.4,
                       'last10': 0.2,
                       'wins': 17,
                       'losses': 65},
 'Detroit Pistons': {'net_rtg': -8.9,
                     'off_rtg': 107.2,
                     'def_rtg': 116.1,
                     'pace': 98.8,
                     'last10': 0.2,
                     'wins': 16,
                     'losses': 66},
 'Washington Wizards': {'net_rtg': -10.2,
                        'off_rtg': 106.5,
                        'def_rtg': 116.7,
                        'pace': 99.2,
                        'last10': 0.1,
                        'wins': 14,
                        'losses': 68}}

NBA_NAME_MAP = {'Los Angeles Lakers': 'Los Angeles Lakers',
 'LA Lakers': 'Los Angeles Lakers',
 'Los Angeles Clippers': 'LA Clippers',
 'LA Clippers': 'LA Clippers',
 'Golden State Warriors': 'Golden State Warriors',
 'GS Warriors': 'Golden State Warriors',
 'Oklahoma City Thunder': 'Oklahoma City Thunder',
 'OKC Thunder': 'Oklahoma City Thunder',
 'New York Knicks': 'New York Knicks',
 'NY Knicks': 'New York Knicks',
 'Minnesota Timberwolves': 'Minnesota Timberwolves',
 'Portland Trail Blazers': 'Portland Trail Blazers',
 'San Antonio Spurs': 'San Antonio Spurs',
 'New Orleans Pelicans': 'New Orleans Pelicans',
 'Memphis Grizzlies': 'Memphis Grizzlies',
 'Philadelphia 76ers': 'Philadelphia 76ers',
 'Cleveland Cavaliers': 'Cleveland Cavaliers',
 'Boston Celtics': 'Boston Celtics',
 'Denver Nuggets': 'Denver Nuggets',
 'Dallas Mavericks': 'Dallas Mavericks',
 'Milwaukee Bucks': 'Milwaukee Bucks',
 'Miami Heat': 'Miami Heat',
 'Atlanta Hawks': 'Atlanta Hawks',
 'Indiana Pacers': 'Indiana Pacers',
 'Chicago Bulls': 'Chicago Bulls',
 'Toronto Raptors': 'Toronto Raptors',
 'Brooklyn Nets': 'Brooklyn Nets',
 'Sacramento Kings': 'Sacramento Kings',
 'Phoenix Suns': 'Phoenix Suns',
 'Houston Rockets': 'Houston Rockets',
 'Utah Jazz': 'Utah Jazz',
 'Detroit Pistons': 'Detroit Pistons',
 'Washington Wizards': 'Washington Wizards',
 'Charlotte Hornets': 'Charlotte Hornets',
 'Orlando Magic': 'Orlando Magic'}

# NFL v4 fallback
NFL_FB = {'Kansas City Chiefs': {'epa_off': 0.182, 'epa_def': -0.145, 'to_margin': 8, 'win_pct': 0.812, 'pts_diff': 9.8},
 'Philadelphia Eagles': {'epa_off': 0.158, 'epa_def': -0.128, 'to_margin': 6, 'win_pct': 0.75, 'pts_diff': 8.2},
 'San Francisco 49ers': {'epa_off': 0.142, 'epa_def': -0.138, 'to_margin': 5, 'win_pct': 0.719, 'pts_diff': 7.5},
 'Baltimore Ravens': {'epa_off': 0.168, 'epa_def': -0.082, 'to_margin': 4, 'win_pct': 0.719, 'pts_diff': 7.1},
 'Buffalo Bills': {'epa_off': 0.151, 'epa_def': -0.095, 'to_margin': 5, 'win_pct': 0.688, 'pts_diff': 6.8},
 'Houston Texans': {'epa_off': 0.128, 'epa_def': -0.072, 'to_margin': 3, 'win_pct': 0.656, 'pts_diff': 5.5},
 'Dallas Cowboys': {'epa_off': 0.112, 'epa_def': -0.088, 'to_margin': 3, 'win_pct': 0.625, 'pts_diff': 5.2},
 'Detroit Lions': {'epa_off': 0.135, 'epa_def': 0.018, 'to_margin': 2, 'win_pct': 0.625, 'pts_diff': 5.0},
 'Miami Dolphins': {'epa_off': 0.125, 'epa_def': 0.042, 'to_margin': 1, 'win_pct': 0.594, 'pts_diff': 4.5},
 'Cincinnati Bengals': {'epa_off': 0.118, 'epa_def': 0.025, 'to_margin': 2, 'win_pct': 0.563, 'pts_diff': 4.1},
 'Los Angeles Rams': {'epa_off': 0.105, 'epa_def': -0.015, 'to_margin': 1, 'win_pct': 0.563, 'pts_diff': 3.8},
 'Los Angeles Chargers': {'epa_off': 0.088, 'epa_def': -0.022, 'to_margin': 2, 'win_pct': 0.531, 'pts_diff': 3.2},
 'Tampa Bay Buccaneers': {'epa_off': 0.095, 'epa_def': 0.028, 'to_margin': 1, 'win_pct': 0.531, 'pts_diff': 3.5},
 'Washington Commanders': {'epa_off': 0.072, 'epa_def': 0.038, 'to_margin': 0, 'win_pct': 0.469, 'pts_diff': 1.8},
 'Cleveland Browns': {'epa_off': 0.068, 'epa_def': -0.025, 'to_margin': 0, 'win_pct': 0.5, 'pts_diff': 2.2},
 'Pittsburgh Steelers': {'epa_off': 0.052, 'epa_def': -0.055, 'to_margin': 1, 'win_pct': 0.5, 'pts_diff': 2.0},
 'Green Bay Packers': {'epa_off': 0.075, 'epa_def': 0.085, 'to_margin': 0, 'win_pct': 0.469, 'pts_diff': 1.1},
 'Seattle Seahawks': {'epa_off': 0.048, 'epa_def': 0.062, 'to_margin': -1, 'win_pct': 0.438, 'pts_diff': 0.5},
 'Minnesota Vikings': {'epa_off': 0.055, 'epa_def': 0.088, 'to_margin': -2, 'win_pct': 0.438, 'pts_diff': 0.4},
 'Jacksonville Jaguars': {'epa_off': 0.058, 'epa_def': 0.032, 'to_margin': -1, 'win_pct': 0.469, 'pts_diff': 1.2},
 'Indianapolis Colts': {'epa_off': 0.042, 'epa_def': 0.082, 'to_margin': -1, 'win_pct': 0.406, 'pts_diff': 0.1},
 'New York Giants': {'epa_off': 0.018, 'epa_def': 0.088, 'to_margin': -2, 'win_pct': 0.375, 'pts_diff': -0.8},
 'New Orleans Saints': {'epa_off': 0.012, 'epa_def': 0.118, 'to_margin': -2, 'win_pct': 0.344, 'pts_diff': -2.0},
 'Tennessee Titans': {'epa_off': 0.015, 'epa_def': 0.108, 'to_margin': -3, 'win_pct': 0.344, 'pts_diff': -1.5},
 'Chicago Bears': {'epa_off': -0.005, 'epa_def': 0.108, 'to_margin': -3, 'win_pct': 0.313, 'pts_diff': -2.8},
 'Las Vegas Raiders': {'epa_off': 0.005, 'epa_def': 0.118, 'to_margin': -4, 'win_pct': 0.313, 'pts_diff': -2.5},
 'New England Patriots': {'epa_off': -0.012, 'epa_def': 0.115, 'to_margin': -3, 'win_pct': 0.281, 'pts_diff': -3.2},
 'Denver Broncos': {'epa_off': -0.025, 'epa_def': 0.102, 'to_margin': -2, 'win_pct': 0.281, 'pts_diff': -3.5},
 'Atlanta Falcons': {'epa_off': -0.031, 'epa_def': 0.105, 'to_margin': -5, 'win_pct': 0.25, 'pts_diff': -4.0},
 'New York Jets': {'epa_off': -0.042, 'epa_def': 0.108, 'to_margin': -6, 'win_pct': 0.219, 'pts_diff': -5.0},
 'Arizona Cardinals': {'epa_off': -0.052, 'epa_def': 0.108, 'to_margin': -5, 'win_pct': 0.188, 'pts_diff': -5.8},
 'Carolina Panthers': {'epa_off': -0.068, 'epa_def': 0.112, 'to_margin': -7, 'win_pct': 0.156, 'pts_diff': -7.2}}

NFL_ABBR = {'KC': 'Kansas City Chiefs',
 'PHI': 'Philadelphia Eagles',
 'SF': 'San Francisco 49ers',
 'BAL': 'Baltimore Ravens',
 'BUF': 'Buffalo Bills',
 'HOU': 'Houston Texans',
 'DAL': 'Dallas Cowboys',
 'DET': 'Detroit Lions',
 'MIA': 'Miami Dolphins',
 'CIN': 'Cincinnati Bengals',
 'LA': 'Los Angeles Rams',
 'LAC': 'Los Angeles Chargers',
 'TB': 'Tampa Bay Buccaneers',
 'WAS': 'Washington Commanders',
 'CLE': 'Cleveland Browns',
 'PIT': 'Pittsburgh Steelers',
 'JAX': 'Jacksonville Jaguars',
 'GB': 'Green Bay Packers',
 'SEA': 'Seattle Seahawks',
 'MIN': 'Minnesota Vikings',
 'IND': 'Indianapolis Colts',
 'NYG': 'New York Giants',
 'NO': 'New Orleans Saints',
 'TEN': 'Tennessee Titans',
 'CHI': 'Chicago Bears',
 'LV': 'Las Vegas Raiders',
 'NE': 'New England Patriots',
 'DEN': 'Denver Broncos',
 'ATL': 'Atlanta Falcons',
 'NYJ': 'New York Jets',
 'ARI': 'Arizona Cardinals',
 'CAR': 'Carolina Panthers'}

# CBB / CFB fallbacks (light)
CBB_FB = {'Auburn': {'eff_margin': 28.5,
            'adj_o': 122.1,
            'adj_d': 93.6,
            'efg': 0.558,
            'to_rate': 0.158,
            'exp': 0.85,
            'tempo': 72.1,
            'seed': 1},
 'Duke': {'eff_margin': 27.2,
          'adj_o': 121.8,
          'adj_d': 94.6,
          'efg': 0.551,
          'to_rate': 0.162,
          'exp': 0.6,
          'tempo': 71.8,
          'seed': 1},
 'Houston': {'eff_margin': 26.8,
             'adj_o': 118.4,
             'adj_d': 91.6,
             'efg': 0.532,
             'to_rate': 0.17,
             'exp': 0.9,
             'tempo': 68.5,
             'seed': 1},
 'Florida': {'eff_margin': 25.9,
             'adj_o': 120.2,
             'adj_d': 94.3,
             'efg': 0.545,
             'to_rate': 0.165,
             'exp': 0.75,
             'tempo': 70.2,
             'seed': 2},
 'Tennessee': {'eff_margin': 25.4,
               'adj_o': 117.8,
               'adj_d': 92.4,
               'efg': 0.528,
               'to_rate': 0.172,
               'exp': 0.88,
               'tempo': 67.8,
               'seed': 2},
 'Kansas': {'eff_margin': 24.1,
            'adj_o': 119.6,
            'adj_d': 95.5,
            'efg': 0.541,
            'to_rate': 0.16,
            'exp': 0.78,
            'tempo': 71.5,
            'seed': 2},
 'Iowa State': {'eff_margin': 23.8,
                'adj_o': 118.9,
                'adj_d': 95.1,
                'efg': 0.538,
                'to_rate': 0.163,
                'exp': 0.82,
                'tempo': 70.8,
                'seed': 2},
 'Purdue': {'eff_margin': 23.2,
            'adj_o': 120.4,
            'adj_d': 97.2,
            'efg': 0.555,
            'to_rate': 0.155,
            'exp': 0.92,
            'tempo': 69.1,
            'seed': 3},
 'Alabama': {'eff_margin': 22.7,
             'adj_o': 121.0,
             'adj_d': 98.3,
             'efg': 0.562,
             'to_rate': 0.175,
             'exp': 0.55,
             'tempo': 73.5,
             'seed': 3},
 'Michigan State': {'eff_margin': 22.1,
                    'adj_o': 117.5,
                    'adj_d': 95.4,
                    'efg': 0.53,
                    'to_rate': 0.168,
                    'exp': 0.95,
                    'tempo': 68.8,
                    'seed': 3},
 'Wisconsin': {'eff_margin': 21.8,
               'adj_o': 116.8,
               'adj_d': 95.0,
               'efg': 0.525,
               'to_rate': 0.155,
               'exp': 0.98,
               'tempo': 65.2,
               'seed': 3},
 'Arizona': {'eff_margin': 21.4,
             'adj_o': 119.2,
             'adj_d': 97.8,
             'efg': 0.548,
             'to_rate': 0.17,
             'exp': 0.65,
             'tempo': 72.1,
             'seed': 3},
 'Marquette': {'eff_margin': 20.8,
               'adj_o': 118.1,
               'adj_d': 97.3,
               'efg': 0.54,
               'to_rate': 0.162,
               'exp': 0.8,
               'tempo': 70.5,
               'seed': 4},
 "St John's": {'eff_margin': 20.5,
               'adj_o': 117.8,
               'adj_d': 97.3,
               'efg': 0.536,
               'to_rate': 0.165,
               'exp': 0.72,
               'tempo': 71.2,
               'seed': 4},
 'Texas Tech': {'eff_margin': 20.2,
                'adj_o': 116.5,
                'adj_d': 96.3,
                'efg': 0.522,
                'to_rate': 0.16,
                'exp': 0.85,
                'tempo': 67.5,
                'seed': 4},
 'Kentucky': {'eff_margin': 19.8,
              'adj_o': 117.2,
              'adj_d': 97.4,
              'efg': 0.535,
              'to_rate': 0.168,
              'exp': 0.58,
              'tempo': 71.8,
              'seed': 4},
 'UConn': {'eff_margin': 19.4,
           'adj_o': 116.9,
           'adj_d': 97.5,
           'efg': 0.532,
           'to_rate': 0.165,
           'exp': 0.75,
           'tempo': 68.2,
           'seed': 5},
 'Gonzaga': {'eff_margin': 19.1,
             'adj_o': 118.5,
             'adj_d': 99.4,
             'efg': 0.545,
             'to_rate': 0.158,
             'exp': 0.78,
             'tempo': 73.8,
             'seed': 5},
 'Baylor': {'eff_margin': 18.6,
            'adj_o': 116.2,
            'adj_d': 97.6,
            'efg': 0.528,
            'to_rate': 0.172,
            'exp': 0.7,
            'tempo': 70.1,
            'seed': 5},
 'Illinois': {'eff_margin': 18.2,
              'adj_o': 115.8,
              'adj_d': 97.6,
              'efg': 0.525,
              'to_rate': 0.17,
              'exp': 0.82,
              'tempo': 69.5,
              'seed': 5},
 'San Diego State': {'eff_margin': 9.8,
                     'adj_o': 109.5,
                     'adj_d': 99.7,
                     'efg': 0.475,
                     'to_rate': 0.185,
                     'exp': 0.88,
                     'tempo': 65.8,
                     'seed': 11},
 'NC State': {'eff_margin': 9.4,
              'adj_o': 110.2,
              'adj_d': 100.8,
              'efg': 0.48,
              'to_rate': 0.192,
              'exp': 0.75,
              'tempo': 68.1,
              'seed': 11},
 'Grand Canyon': {'eff_margin': 8.6,
                  'adj_o': 110.5,
                  'adj_d': 101.9,
                  'efg': 0.485,
                  'to_rate': 0.182,
                  'exp': 0.9,
                  'tempo': 67.2,
                  'seed': 12},
 'McNeese': {'eff_margin': 8.2,
             'adj_o': 110.2,
             'adj_d': 102.0,
             'efg': 0.482,
             'to_rate': 0.185,
             'exp': 0.88,
             'tempo': 66.8,
             'seed': 12}}
CFB_FB = {'Georgia': {'sp_plus': 27.8, 'off_sp': 38.2, 'def_sp': 12.5, 'home_edge': 3.5, 'sos_rank': 8, 'win_pct': 0.917},
 'Ohio State': {'sp_plus': 26.5, 'off_sp': 42.1, 'def_sp': 15.6, 'home_edge': 3.5, 'sos_rank': 12, 'win_pct': 0.875},
 'Alabama': {'sp_plus': 25.2, 'off_sp': 39.5, 'def_sp': 14.3, 'home_edge': 3.5, 'sos_rank': 15, 'win_pct': 0.833},
 'Michigan': {'sp_plus': 22.8, 'off_sp': 35.8, 'def_sp': 13.0, 'home_edge': 3.5, 'sos_rank': 18, 'win_pct': 0.833},
 'Texas': {'sp_plus': 21.5, 'off_sp': 36.2, 'def_sp': 14.7, 'home_edge': 3.5, 'sos_rank': 22, 'win_pct': 0.792},
 'Penn State': {'sp_plus': 20.2, 'off_sp': 33.5, 'def_sp': 13.3, 'home_edge': 3.5, 'sos_rank': 20, 'win_pct': 0.792},
 'Oregon': {'sp_plus': 19.8, 'off_sp': 35.1, 'def_sp': 15.3, 'home_edge': 3.5, 'sos_rank': 25, 'win_pct': 0.75},
 'Notre Dame': {'sp_plus': 19.1, 'off_sp': 32.8, 'def_sp': 13.7, 'home_edge': 3.0, 'sos_rank': 28, 'win_pct': 0.75},
 'Florida State': {'sp_plus': 18.4, 'off_sp': 31.5, 'def_sp': 13.1, 'home_edge': 3.5, 'sos_rank': 30, 'win_pct': 0.708},
 'Clemson': {'sp_plus': 17.8, 'off_sp': 29.8, 'def_sp': 12.0, 'home_edge': 3.5, 'sos_rank': 32, 'win_pct': 0.708},
 'LSU': {'sp_plus': 17.2, 'off_sp': 30.5, 'def_sp': 13.3, 'home_edge': 3.5, 'sos_rank': 18, 'win_pct': 0.667},
 'Oklahoma': {'sp_plus': 16.5, 'off_sp': 32.1, 'def_sp': 15.6, 'home_edge': 3.5, 'sos_rank': 35, 'win_pct': 0.667},
 'Tennessee': {'sp_plus': 15.8, 'off_sp': 31.8, 'def_sp': 16.0, 'home_edge': 3.5, 'sos_rank': 22, 'win_pct': 0.625},
 'USC': {'sp_plus': 15.2, 'off_sp': 33.5, 'def_sp': 18.3, 'home_edge': 3.0, 'sos_rank': 28, 'win_pct': 0.625},
 'Boise State': {'sp_plus': 10.5, 'off_sp': 23.8, 'def_sp': 13.3, 'home_edge': 4.0, 'sos_rank': 55, 'win_pct': 0.5},
 'Iowa': {'sp_plus': 11.2, 'off_sp': 20.5, 'def_sp': 9.3, 'home_edge': 3.5, 'sos_rank': 30, 'win_pct': 0.5}}

CBB_SEED_HISTORY = {(5,12):{"upset_rate":0.35},(6,11):{"upset_rate":0.37},(7,10):{"upset_rate":0.40},(8,9):{"upset_rate":0.49}}

# NBA props fallback (Kalshi-style synthetic levels)
NBA_PROPS_FB = {
    "Nikola Jokic": {"team":"Denver Nuggets","pos":"C","usg_rate":0.312,"mins":34.5,"pts_avg":27.2,"reb_avg":12.8,"ast_avg":9.5,"pts_last5":26.8,"reb_last5":13.2,"ast_last5":10.1},
    "Shai Gilgeous-Alexander":{"team":"Oklahoma City Thunder","pos":"G","usg_rate":0.368,"mins":34.8,"pts_avg":31.8,"reb_avg":5.2,"ast_avg":6.1,"pts_last5":33.2,"reb_last5":5.0,"ast_last5":6.5},
}

# Opponent vs position (light fallback)
NBA_OPP_DEF = {"Washington Wizards":{"G_pts":28.5,"F_pts":27.0,"C_pts":30.5,"G_reb":5.8,"F_reb":8.2,"C_reb":13.5}}

# ── LIVE DATA FETCHERS ───────────────────────────────────────────────────────
@st.cache_data(ttl=21600, show_spinner=False)
def live_mlb():
    try:
        import pybaseball
        pybaseball.cache.enable()
        yr = today_est().year
        standings_raw = pybaseball.standings(yr)
        stats = {}
        if standings_raw:
            for div in standings_raw:
                for _, r in div.iterrows():
                    nm = str(r.get("Tm",""))
                    w  = float(r.get("W",0) or 0)
                    l  = float(r.get("L",1) or 1)
                    rs = float(r.get("RS", r.get("R",0)) or 0)
                    ra = float(r.get("RA",0) or 0)
                    g  = max(w+l,1)
                    ab = _mlb_abbr(nm)
                    if ab:
                        fb = MLB_FB.get(ab,{})
                        # Pythagorean from runs; exponent stable default 2.0
                        pyth = (rs**2)/((rs**2)+(ra**2)) if (rs>0 or ra>0) else w/g
                        stats[ab] = {
                            "pyth_win": round(pyth,3),
                            "run_diff_pg": round((rs-ra)/g,3),
                            "bullpen_fip": fb.get("bullpen_fip",4.20),
                            "wrc_plus": fb.get("wrc_plus",100),
                            "last10": fb.get("last10",0.50),
                            "starter_fip": fb.get("starter_fip",4.20),
                        }
        for k,v in MLB_FB.items():
            if k not in stats: stats[k]=v
        if len(stats) >= 10: return stats,"live"
    except:
        pass
    return MLB_FB,"fallback"

@st.cache_data(ttl=21600, show_spinner=False)
def live_nba():
    try:
        from nba_api.stats.endpoints import leaguedashteamstats
        season = _nba_season()
        time.sleep(0.6)
        adv = leaguedashteamstats.LeagueDashTeamStats(
            season=season, measure_type_detailed_defense="Advanced", per_mode_detailed="PerGame"
        ).get_data_frames()[0]
        stats = {}
        for _, r in adv.iterrows():
            nm = str(r.get("TEAM_NAME",""))
            fb = NBA_FB.get(nm,{})
            # nba_api advanced frame has NET_RATING, PIE, TM_TOV_PCT, etc depending on endpoint/version
            pie = float(r.get("PIE", fb.get("pie_pct",0.50)) or fb.get("pie_pct",0.50))
            tov = float(r.get("TM_TOV_PCT", fb.get("to_rate",0.15)) or fb.get("to_rate",0.15))
            if tov > 1: tov = tov/100.0
            stats[nm] = {
                "net_rtg": float(r.get("NET_RATING", fb.get("net_rtg",0)) or fb.get("net_rtg",0)),
                "pie_pct": pie,
                "ts_pct": fb.get("ts_pct",0.555),
                "three_pt_pct": fb.get("three_pt_pct",0.360),
                "three_pt_rate": fb.get("three_pt_rate",0.410),
                "def_reb_pct": fb.get("def_reb_pct",0.720),
                "to_rate": tov,
                "pace": float(r.get("PACE", fb.get("pace",99)) or fb.get("pace",99)),
                "last10": fb.get("last10",0.50),
            }
        if len(stats) >= 20: return stats,"live"
    except:
        pass
    return NBA_FB,"fallback"

@st.cache_data(ttl=21600, show_spinner=False)
def live_nfl():
    try:
        import nfl_data_py as nfl
        yr = today_est().year if today_est().month >= 9 else today_est().year-1
        pbp = nfl.import_pbp_data([yr],downcast=True,cache=False)
        if pbp is None or pbp.empty: raise Exception()
        plays = pbp[pbp["play_type"].isin(["pass","run"])]
        off = plays.groupby("posteam")["epa"].mean().reset_index(); off.columns=["team","epa_off"]
        dfn = plays.groupby("defteam")["epa"].mean().reset_index(); dfn.columns=["team","epa_def"]
        pbp["to"] = pbp["interception"].fillna(0)+pbp["fumble_lost"].fillna(0)
        tog = pbp.groupby("posteam")["to"].sum().reset_index(); tog.columns=["team","to_given"]
        tot = pbp.groupby("defteam")["to"].sum().reset_index(); tot.columns=["team","to_taken"]
        mg = off.merge(dfn,on="team",how="outer").merge(tog,on="team",how="left").merge(tot,on="team",how="left")
        stats={}
        for _, r in mg.iterrows():
            ab=str(r["team"]); full = NFL_ABBR.get(ab)
            if not full: continue
            fb = NFL_FB.get(full,{})
            to_m = int(float(r.get("to_taken",0) or 0) - float(r.get("to_given",0) or 0))
            stats[full]={
                "epa_off": float(r.get("epa_off",0) or 0),
                "epa_def": float(r.get("epa_def",0) or 0),
                "to_margin": to_m,
                "pyth_win": fb.get("pyth_win",0.50),
                "third_down_pct": fb.get("third_down_pct",0.38),
                "rz_td_pct": fb.get("rz_td_pct",0.58),
                "home_epa": fb.get("home_epa",0.08),
                "away_epa": fb.get("away_epa",0.04),
            }
        if len(stats) >= 20: return stats,"live"
    except:
        pass
    return NFL_FB,"fallback"

@st.cache_data(ttl=21600, show_spinner=False)
def live_cbb():
    try:
        yr = today_est().year
        url = f"https://barttorvik.com/trank.php?year={yr}&sort=&top=0&conlimit=All&csv=1"
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=15)
        if r.status_code != 200 or len(r.text) < 500: raise Exception()
        df = pd.read_csv(StringIO(r.text), header=0)
        stats={}
        seen=set()
        for _, row in df.iterrows():
            nm = normalize_team(str(row.iloc[0]).strip())
            if nm in seen: continue
            seen.add(nm)
            adj_o = _sf(row, ["AdjOE"], 110, 110)
            adj_d = _sf(row, ["AdjDE"], 102, 102)
            efg   = _sf(row, ["EFG%","eFG%"], 50.0, 50.0)
            tempo = _sf(row, ["AdjTempo","Tempo"], 70.0, 70.0)
            rec   = str(row.get("Rec", row.iloc[3] if len(row)>3 else "0-0"))
            w,l = _pr(rec)
            fb = CBB_FB.get(nm,{})
            stats[nm] = {"eff_margin":adj_o-adj_d,"adj_o":adj_o,"adj_d":adj_d,"efg":(efg/100 if efg>1 else efg),
                         "to_rate":fb.get("to_rate",0.18),"exp":fb.get("exp",0.75),"tempo":tempo,"seed":fb.get("seed"),
                         "win_pct":w/max(w+l,1)}
        for k,v in CBB_FB.items():
            if k not in stats: stats[k]=v
        if len(stats) >= 50: return stats,"live"
    except:
        pass
    return CBB_FB,"fallback"

@st.cache_data(ttl=21600, show_spinner=False)
def live_cfb():
    try:
        if not CFBD_API_KEY: raise Exception()
        yr = today_est().year if today_est().month >= 8 else today_est().year-1
        hdr={"Authorization":f"Bearer {CFBD_API_KEY}"}
        sp_r = requests.get(f"https://api.collegefootballdata.com/ratings/sp?year={yr}", headers=hdr, timeout=10)
        rec_r = requests.get(f"https://api.collegefootballdata.com/records?year={yr}", headers=hdr, timeout=10)
        sp_d = sp_r.json() if sp_r.status_code==200 else []
        rec_d = rec_r.json() if rec_r.status_code==200 else []
        rec_map={}
        for r in rec_d:
            t=str(r.get("team","")); tot=r.get("total",{})
            w,l=tot.get("wins",0),tot.get("losses",0)
            rec_map[t]=w/max(w+l,1)
        stats={}
        for item in sp_d:
            nm=str(item.get("team",""))
            sp=float(item.get("rating",0) or 0)
            off=float(item.get("offense",{}).get("rating",0) or 0)
            deff=float(item.get("defense",{}).get("rating",0) or 0)
            fb=CFB_FB.get(nm,{})
            stats[nm]={"sp_plus":sp,"off_sp":off,"def_sp":abs(deff),
                       "home_edge":fb.get("home_edge",3.5),"sos_rank":fb.get("sos_rank",60),
                       "win_pct":rec_map.get(nm,fb.get("win_pct",0.50))}
        for k,v in CFB_FB.items():
            if k not in stats: stats[k]=v
        if len(stats) >= 30: return stats,"live"
    except:
        pass
    return CFB_FB,"fallback"

# ── ESPN: scoreboard + injuries ──────────────────────────────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def fetch_espn(espn_sport, espn_league, target_date=None):
    try:
        d = target_date or today_est()
        r = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/{espn_sport}/{espn_league}/scoreboard",
            params={"dates": d.strftime("%Y%m%d"), "limit":100},
            timeout=10
        )
        return r.json().get("events", [])
    except:
        return []

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_injuries(espn_sport, espn_league):
    try:
        r = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/{espn_sport}/{espn_league}/injuries",
            timeout=10
        )
        data = r.json()
        inj = {}
        for item in data.get("injuries", []):
            team = item.get("team", {}).get("displayName", "")
            for p in item.get("injuries", []):
                status = (p.get("status","") or "").lower()
                if status in ("out","doubtful","questionable"):
                    name = p.get("athlete", {}).get("displayName","")
                    pos  = p.get("athlete", {}).get("position", {}).get("abbreviation","")
                    inj.setdefault(team, []).append(f"{name} ({pos}) {status.title()}")
        return inj
    except:
        return {}

def parse_espn_events(events):
    games=[]
    for e in events:
        comp = e.get("competitions",[{}])[0]
        status=comp.get("status",{})
        state=status.get("type",{}).get("state","pre")
        detail=status.get("type",{}).get("shortDetail","")
        home=away={}
        for t in comp.get("competitors",[]):
            if t.get("homeAway")=="home": home=t
            else: away=t
        def tm(t):
            td=t.get("team",{})
            return {"name": td.get("displayName", td.get("shortDisplayName","")),
                    "short": td.get("shortDisplayName", td.get("abbreviation","")),
                    "logo": td.get("logo",""),
                    "score": t.get("score","—"),
                    "rec": (t.get("records",[{}])[0].get("summary","") if t.get("records") else ""),
                    "rank": t.get("curatedRank",{}).get("current","")}
        hd,ad=tm(home),tm(away)
        gt = utc_to_est(e.get("date",""))
        games.append({"home":hd,"away":ad,"state":state,"detail":detail,"gametime":gt})
    games.sort(key=lambda x:{"in":0,"pre":1,"post":2}.get(x["state"],3))
    return games

# ── WEATHER (optional) ───────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_weather(lat, lon, city, has_roof):
    if has_roof: return {"city":city,"roof":True}
    if not WEATHER_KEY: return None
    try:
        r = requests.get("https://api.openweathermap.org/data/2.5/weather",
                         params={"lat":lat,"lon":lon,"appid":WEATHER_KEY,"units":"imperial"},timeout=8)
        d = r.json()
        temp=round(d["main"]["temp"]); wind=round(d["wind"]["speed"])
        desc=d["weather"][0]["description"].title()
        precip=d.get("rain",{}).get("1h", d.get("snow",{}).get("1h",0))
        impact=[]
        if wind>=20: impact.append("⚠️ Heavy wind")
        if precip>0: impact.append(f"🌧️ {precip:.1f}mm")
        return {"city":city,"roof":False,"temp":temp,"wind":wind,"desc":desc,"precip":precip,"impact":impact}
    except:
        return None

# ── ODDS ─────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def fetch_odds(sport_key):
    try:
        r = requests.get(
            f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/",
            params={"apiKey":ODDS_API_KEY,"regions":"us","markets":"h2h,spreads",
                    "oddsFormat":"american","dateFormat":"iso"},
            timeout=10
        )
        d=r.json()
        return d if isinstance(d,list) else []
    except:
        return []

def _extract_all_books(g, home, away):
    book_data={}
    for bk in g.get("bookmakers",[]):
        bk_key=bk.get("key","")
        bd={"home_ml":None,"away_ml":None,"spread":None}
        for mkt in bk.get("markets",[]):
            if mkt.get("key")=="h2h":
                for o in mkt.get("outcomes",[]):
                    if o.get("name")==home: bd["home_ml"]=o.get("price")
                    if o.get("name")==away: bd["away_ml"]=o.get("price")
            if mkt.get("key")=="spreads":
                for o in mkt.get("outcomes",[]):
                    if o.get("name")==home and bd["spread"] is None:
                        bd["spread"]=o.get("point")
        book_data[bk_key]=bd
    best_home=best_away=None; best_home_book=best_away_book=""
    for bk,bd in book_data.items():
        if bd["home_ml"] is not None and (best_home is None or bd["home_ml"]>best_home):
            best_home=bd["home_ml"]; best_home_book=bk
        if bd["away_ml"] is not None and (best_away is None or bd["away_ml"]>best_away):
            best_away=bd["away_ml"]; best_away_book=bk
    spreads=[bd["spread"] for bd in book_data.values() if bd["spread"] is not None]
    consensus_spread=max(set(spreads), key=spreads.count) if spreads else None
    return book_data,best_home,best_home_book,best_away,best_away_book,consensus_spread

def _gametime_from_odds(g):
    return utc_to_est(g.get("commence_time",""))

def ml_to_implied(ml):
    try:
        ml=float(ml)
        if ml<0: return round(abs(ml)/(abs(ml)+100)*100,2)
        return round(100/(ml+100)*100,2)
    except:
        return None

def gap_to_confidence(gap):
    # heuristic mapping (calibration is a future enhancement)
    if gap <= 0:  return 50.0
    if gap >= 40: return 82.0
    return round(52.0 + (gap/40.0)*30.0, 2)

# ── MODELS (weights) ─────────────────────────────────────────────────────────
DEFAULT_WEIGHTS = {
    # MLB v4: works even if some teams only have legacy win_pct/ops/bullpen_era
    "MLB": {"Pyth Win%":18, "Run Diff/G":22, "Bullpen FIP":20, "wRC+":20, "Starter FIP":12, "Last 10":8},
    # NBA v4
    "NBA": {"Net Rating":30, "PIE%":20, "TS%":15, "3PT Rate":12, "Def Reb%":10, "TO Rate":13},
    # NFL v4
    "NFL": {"EPA Off":25, "EPA Def":25, "TO Margin":12, "Pyth Win%":12, "3rd Down%":14, "RZ TD%":12},
    "CBB": {"Eff Margin":28, "Adj O":20, "Adj D":20, "EFG%":10, "TO Rate":10, "Experience":8, "Tempo":4},
    "CFB": {"SP+":35, "Off SP+":20, "Def SP+":20, "Home Edge":10, "Win%":10, "SOS":5},
}

def _get_w(sport,key):
    defaults=DEFAULT_WEIGHTS[sport]
    raw=st.session_state.get(f"w_{sport}_{key}", defaults[key])
    total=sum(st.session_state.get(f"w_{sport}_{k}", defaults[k]) for k in defaults)
    return raw/max(total,1)

def score_mlb(s):
    """Score MLB team. Works with either v4 fields (pyth_win, bullpen_fip, wrc_plus, starter_fip)
    or legacy v3 fields (win_pct, bullpen_era, ops)."""
    # Pythagorean win% (fallback to Win%)
    pw = float(s.get("pyth_win", s.get("pyth_win_pct", s.get("win_pct", 0.50))) or 0.50)

    # Run differential per game
    rd = float(s.get("run_diff_pg", 0.0) or 0.0)
    rd_n = max(0, min(1, (rd + 3) / 6))

    # Bullpen quality (prefer FIP; fallback to ERA)
    bullpen_fip = s.get("bullpen_fip", None)
    if bullpen_fip is None:
        # convert bullpen_era into a fip-like scale (approx): treat ERA as FIP proxy
        bullpen_fip = float(s.get("bullpen_era", 4.20) or 4.20)
    bullpen_n = max(0, min(1, 1 - (float(bullpen_fip) - 2.5) / 3.5))

    # Starter quality (FIP if available)
    starter_fip = float(s.get("starter_fip", 4.20) or 4.20)
    starter_n = max(0, min(1, 1 - (starter_fip - 2.5) / 3.5))

    # Offense quality (prefer wRC+; fallback to OPS)
    wrc_plus = s.get("wrc_plus", None)
    if wrc_plus is None:
        ops = float(s.get("ops", 0.720) or 0.720)
        # map OPS ~[.62,.80] to wRC+ ~[70,130]
        wrc_plus = 70 + max(0, min(1, (ops - 0.62) / 0.18)) * 60
    wrc_n = max(0, min(1, (float(wrc_plus) - 70) / 60))

    ln = float(s.get("last10", 0.50) or 0.50)

    w = {k: _get_w("MLB", k) for k in DEFAULT_WEIGHTS["MLB"]}
    return round((w["Pyth Win%"] * pw +
                  w["Run Diff/G"] * rd_n +
                  w["Bullpen FIP"] * bullpen_n +
                  w["wRC+"] * wrc_n +
                  w["Starter FIP"] * starter_n +
                  w.get("Last 10", 0) * ln) * 100, 2)

def score_nba(s, b2b=False):
    """Score NBA team using v4 factors with graceful fallbacks to legacy fields."""
    net = float(s.get("net_rtg", 0.0) or 0.0)
    nr = max(0, min(1, (net + 15) / 30))

    pie_raw = s.get("pie_pct", None)
    if pie_raw is None:
        # rough proxy: map net rating to PIE-ish band
        pie_raw = 0.50 + max(-0.06, min(0.06, net / 250))
    pie = max(0, min(1, (float(pie_raw) - 0.44) / 0.12))

    ts_raw = s.get("ts_pct", None)
    if ts_raw is None:
        # proxy from offensive rating
        off = float(s.get("off_rtg", 110.0) or 110.0)
        ts_raw = 0.55 + max(-0.05, min(0.05, (off - 110) / 400))
    ts = max(0, min(1, (float(ts_raw) - 0.48) / 0.14))

    tpr = float(s.get("three_pt_rate", 0.41) or 0.41)
    tp  = float(s.get("three_pt_pct", 0.36) or 0.36)
    three_score = max(0, min(1, (tpr * tp - 0.13) / 0.08))

    drb_raw = float(s.get("def_reb_pct", 0.72) or 0.72)
    drb = max(0, min(1, (drb_raw - 0.65) / 0.12))

    tor_raw = float(s.get("to_rate", 0.15) or 0.15)
    tor = max(0, min(1, 1 - (tor_raw - 0.10) / 0.12))

    w = {k: _get_w("NBA", k) for k in DEFAULT_WEIGHTS["NBA"]}
    sc = (w["Net Rating"] * nr +
          w["PIE%"] * pie +
          w["TS%"] * ts +
          w["3PT Rate"] * three_score +
          w["Def Reb%"] * drb +
          w["TO Rate"] * tor) * 100

    penalty = 8 if b2b else 0
    if tpr > 0.45 and tp < 0.350:
        penalty += 3
    return round(max(0, min(100, sc - penalty)), 2)

def score_nfl(s, is_home=False):
    """Score NFL team using v4 factors with graceful fallbacks."""
    base_epa = float(s.get("epa_off", 0.0) or 0.0)
    loc_epa = s.get("home_epa" if is_home else "away_epa", base_epa)
    eo = max(0, min(1, (float(loc_epa) + 0.3) / 0.6))
    ed = max(0, min(1, (0.3 - float(s.get("epa_def", 0.0) or 0.0)) / 0.6))

    tm = max(0, min(1, (float(s.get("to_margin", 0) or 0) + 12) / 24))

    pw = float(s.get("pyth_win", s.get("win_pct", 0.50)) or 0.50)

    td3_raw = float(s.get("third_down_pct", 0.38) or 0.38)
    td3 = max(0, min(1, (td3_raw - 0.25) / 0.28))

    rz_raw = float(s.get("rz_td_pct", 0.58) or 0.58)
    rz = max(0, min(1, (rz_raw - 0.40) / 0.36))

    w = {k: _get_w("NFL", k) for k in DEFAULT_WEIGHTS["NFL"]}
    return round((w["EPA Off"] * eo +
                  w["EPA Def"] * ed +
                  w["TO Margin"] * tm +
                  w["Pyth Win%"] * pw +
                  w["3rd Down%"] * td3 +
                  w["RZ TD%"] * rz) * 100, 2)

def score_cbb(s):
    em=max(0,min(1,(s.get("eff_margin",0)+30)/65))
    ao=max(0,min(1,(s.get("adj_o",100)-90)/40))
    ad=max(0,min(1,1-(s.get("adj_d",105)-85)/35))
    ef=max(0,min(1,(s.get("efg",0.5)-0.42)/0.18))
    to=max(0,min(1,1-(s.get("to_rate",0.18)-0.12)/0.12))
    ex=s.get("exp",0.7)
    tp=max(0,min(1,(s.get("tempo",70)-58)/20))
    w={k:_get_w("CBB",k) for k in DEFAULT_WEIGHTS["CBB"]}
    return round((w["Eff Margin"]*em + w["Adj O"]*ao + w["Adj D"]*ad + w["EFG%"]*ef + w["TO Rate"]*to + w["Experience"]*ex + w["Tempo"]*tp)*100,2)

def score_cfb(s):
    sp=max(0,min(1,(s.get("sp_plus",0)+10)/50))
    op=max(0,min(1,(s.get("off_sp",0)+5)/55))
    dp=max(0,min(1,1-(s.get("def_sp",5)-5)/30))
    he=max(0,min(1,s.get("home_edge",3.5)/5))
    wp=s.get("win_pct",0.5)
    so=max(0,min(1,1-(s.get("sos_rank",50)-1)/130))
    w={k:_get_w("CFB",k) for k in DEFAULT_WEIGHTS["CFB"]}
    return round((w["SP+"]*sp + w["Off SP+"]*op + w["Def SP+"]*dp + w["Home Edge"]*he + w["Win%"]*wp + w["SOS"]*so)*100,2)

# ── B2B (NBA) ────────────────────────────────────────────────────────────────
def get_b2b_teams(espn_sport, espn_league):
    try:
        yesterday = today_est() - timedelta(days=1)
        events = fetch_espn(espn_sport, espn_league, yesterday)
        b2b=set()
        for e in events:
            comp=e.get("competitions",[{}])[0]
            for t in comp.get("competitors",[]):
                nm=t.get("team",{}).get("displayName","")
                if nm: b2b.add(nm)
        return b2b
    except:
        return set()

# ── PICK REASONING + RISK ─────────────────────────────────────────────────────
def get_risk_tag(sl, fs_, ds_, gap, sp):
    if sl=="NBA":
        tpr=max(fs_.get("three_pt_rate",0.41), ds_.get("three_pt_rate",0.41))
        if tpr>0.45: return "High", "risk-high"
        if gap>=25:  return "Low", "risk-low"
        return "Med", "risk-med"
    if sl=="MLB":
        sf_diff=abs(fs_.get("starter_fip",4.2)-ds_.get("starter_fip",4.2))
        if sf_diff<0.30: return "High", "risk-high"
        if gap>=20: return "Low","risk-low"
        return "Med","risk-med"
    if sl=="NFL":
        sv=abs(sp) if sp else 0
        if sv<=3: return "High","risk-high"
        if gap>=18: return "Low","risk-low"
        return "Med","risk-med"
    # CBB/CFB
    if gap>=20: return "Low","risk-low"
    if gap>=10: return "Med","risk-med"
    return "High","risk-high"

def build_reasoning(sl, fav, dog, fs_, ds_, b2b_f, b2b_d, line_move, fav_inj, dog_inj):
    bullets=[]
    if sl=="NBA":
        nr_gap = (fs_.get("net_rtg",0) - ds_.get("net_rtg",0))
        if abs(nr_gap)>=5: bullets.append(f"Net Rating gap: {nr_gap:+.1f}")
        pie_gap = (fs_.get("pie_pct",0.50) - ds_.get("pie_pct",0.50))
        if abs(pie_gap)>=0.02: bullets.append(f"PIE impact edge: {pie_gap:+.3f}")
        ts_gap = (fs_.get("ts_pct",0.555) - ds_.get("ts_pct",0.555))
        if abs(ts_gap)>=0.02: bullets.append(f"TS% efficiency edge: {ts_gap:+.1%}")
        if b2b_d: bullets.append(f"⚠️ {dog} B2B (fatigue penalty)")
        if b2b_f: bullets.append(f"⚠️ {fav} B2B (model penalty)")
    elif sl=="MLB":
        p_gap = fs_.get("pyth_win",0.50) - ds_.get("pyth_win",0.50)
        if abs(p_gap)>=0.04: bullets.append(f"Pyth Win% gap: {p_gap:+.3f}")
        sf_gap = ds_.get("starter_fip",4.2) - fs_.get("starter_fip",4.2)
        if abs(sf_gap)>=0.40: bullets.append(f"Starter FIP edge: {sf_gap:+.2f} (lower better)")
        wrc_gap = fs_.get("wrc_plus",100) - ds_.get("wrc_plus",100)
        if abs(wrc_gap)>=8: bullets.append(f"wRC+ edge: {wrc_gap:+.0f}")
    elif sl=="NFL":
        epa_gap = fs_.get("epa_off",0) - ds_.get("epa_off",0)
        if abs(epa_gap)>=0.08: bullets.append(f"EPA edge: {epa_gap:+.3f}")
        td_gap = fs_.get("third_down_pct",0.38) - ds_.get("third_down_pct",0.38)
        if abs(td_gap)>=0.04: bullets.append(f"3rd down edge: {td_gap:+.1%}")
        rz_gap = fs_.get("rz_td_pct",0.58) - ds_.get("rz_td_pct",0.58)
        if abs(rz_gap)>=0.04: bullets.append(f"Red zone TD edge: {rz_gap:+.1%}")
    elif sl=="CBB":
        em_gap=fs_.get("eff_margin",0)-ds_.get("eff_margin",0)
        if abs(em_gap)>=5: bullets.append(f"Efficiency margin gap: {em_gap:+.1f}")
    else: # CFB
        sp_gap=fs_.get("sp_plus",0)-ds_.get("sp_plus",0)
        if abs(sp_gap)>=5: bullets.append(f"SP+ gap: {sp_gap:+.1f}")
    # Situational (max 1)
    if line_move: bullets.append(f"Line move: {line_move}")
    elif fav_inj: bullets.append(f"⚠️ Injury: {fav_inj[0]}")
    elif dog_inj: bullets.append(f"⚠️ Injury: {dog_inj[0]}")
    return bullets[:3]

# ── TRACKER ───────────────────────────────────────────────────────────────────
def load_picks():
    if os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE) as f: return json.load(f)
    return []

def save_picks(picks):
    with open(TRACKER_FILE,"w") as f: json.dump(picks,f,indent=2)

def a2d(ml):
    try:
        ml=float(ml)
        return ml/100+1 if ml>0 else 100/abs(ml)+1
    except:
        return 1.91

def calc_summary(picks):
    settled=[p for p in picks if p.get("result") in ("W","L","P")]
    wins=len([p for p in settled if p["result"]=="W"])
    losses=len([p for p in settled if p["result"]=="L"])
    pl=sum((a2d(p.get("odds"))-1)*float(p.get("units",1)) if p["result"]=="W"
           else(-float(p.get("units",1)) if p["result"]=="L" else 0) for p in settled)
    wagered=sum(float(p.get("units",1)) for p in settled)
    return {"wins":wins,"losses":losses,"hit_rate":round(wins/len(settled)*100,2) if settled else 0,
            "pl":round(pl,2),"roi":round(pl/wagered*100,2) if wagered>0 else 0,"pending":len(picks)-len(settled)}

def calc_streak(picks):
    f=[p for p in picks if p.get("result") in ("W","L")]
    if not f: return ""
    f=sorted(f,key=lambda x:x.get("date",""))
    streak=1; last=f[-1]["result"]
    for p in reversed(f[:-1]):
        if p["result"]==last: streak+=1
        else: break
    return f"{'🔥' if last=='W' else '❄️'} {streak}-{last}"

# ── HELPERS ──────────────────────────────────────────────────────────────────
def _mlb_abbr(name):
    for full,ab in MLB_NAME_MAP.items():
        if full.lower() in str(name).lower() or str(name).lower() in full.lower():
            return ab
    return MLB_NAME_MAP.get(str(name).strip())

def _nba_season():
    t=today_est()
    return f"{t.year}-{str(t.year+1)[2:]}" if t.month>=10 else f"{t.year-1}-{str(t.year)[2:]}"

def _sf(row, keys, default, fallback):
    for k in keys:
        try:
            v=row.get(k)
            if v is not None: return float(v)
        except: pass
    try: return float(default)
    except: return fallback

def _pr(s):
    try:
        p=str(s).split("-")
        return int(p[0]), int(p[1])
    except:
        return 0,0

def _fuzzy(name,db,fallback=None):
    nl=str(name).lower()
    for k,v in db.items():
        if k.lower() in nl or nl in k.lower():
            return k,v
    return (name, fallback if fallback is not None else {})

# ── UI: Scorecard ────────────────────────────────────────────────────────────
def logo_html(logo_url, name, size=28):
    if logo_url:
        return (f'<img src="{logo_url}" width="{size}" height="{size}" '
                f'style="border-radius:50%;vertical-align:middle;margin-right:4px" />')
    return f'<span style="background:#1e2a45;color:#7eeaff;padding:2px 6px;border-radius:6px;font-size:0.7rem;font-family:DM Mono,monospace">{name[:3].upper()}</span> '

def score_card_html(g):
    hd,ad,state=g["home"],g["away"],g["state"]
    cls="live" if state=="in" else ("final" if state=="post" else "")
    if state=="in": sb=f'<span class="badge-live">🔴 {g["detail"]}</span>'
    elif state=="post": sb=f'<span class="badge-final">✅ Final</span>'
    else: sb=f'<span class="badge-pre">🕐 {g["gametime"]}</span>'
    return f"""<div class="score-card {cls}" style="display:flex;align-items:center;justify-content:space-between">
      <div style="flex:1;display:flex;align-items:center;gap:6px">{logo_html(ad.get('logo',''),ad['short'])}
        <div><div style="font-weight:700;color:#e8eaf0">{ad['short']}</div>
        <div style="font-size:0.72rem;color:#5a6478;font-family:'DM Mono',monospace">{ad['rec']}</div></div></div>
      <div style="display:flex;gap:10px;align-items:center;margin:0 14px">
        <span style="font-family:'DM Mono',monospace;font-size:1.5rem;color:#7eeaff">{ad['score']}</span>
        <span style="color:#2a3450">–</span>
        <span style="font-family:'DM Mono',monospace;font-size:1.5rem;color:#7eeaff">{hd['score']}</span>
      </div>
      <div style="flex:1;text-align:right;display:flex;align-items:center;justify-content:flex-end;gap:6px">
        <div><div style="font-weight:700;color:#e8eaf0">{hd['short']}</div>
        <div style="font-size:0.72rem;color:#5a6478;font-family:'DM Mono',monospace;text-align:right">{hd['rec']}</div></div>
        {logo_html(hd.get('logo',''),hd['short'])}
      </div>
      <div style="margin-left:18px;min-width:130px;text-align:center">{sb}</div>
    </div>"""

# ── GAME BUILD / VALIDATION ───────────────────────────────────────────────────
def _make_row(sl, fav, dog, fav_full, dog_full, fav_is_home, sp, gt, bk_data, best_h, best_h_bk, best_a, best_a_bk, fs_, ds_, b2b_teams, injuries):
    # Scores
    if sl=="MLB":
        fs_score, ds_score = score_mlb(fs_), score_mlb(ds_)
    elif sl=="NBA":
        b2b_f = (fav_full in b2b_teams) or (fav in b2b_teams)
        b2b_d = (dog_full in b2b_teams) or (dog in b2b_teams)
        fs_score, ds_score = score_nba(fs_, b2b_f), score_nba(ds_, b2b_d)
    elif sl=="NFL":
        fs_score, ds_score = score_nfl(fs_, fav_is_home), score_nfl(ds_, not fav_is_home)
    elif sl=="CBB":
        fs_score, ds_score = score_cbb(fs_), score_cbb(ds_)
    else:
        fs_score, ds_score = score_cfb(fs_), score_cfb(ds_)

    gap = round(fs_score - ds_score, 2)
    conf = gap_to_confidence(gap)

    # Determine rating tier
    if gap >= 28: rating="🟢 STRONG"
    elif gap >= 16: rating="🟡 LEAN"
    elif gap >= 6: rating="⚪ TOSS-UP"
    else: rating="🔵 DOG VALUE"

    sv = abs(sp) if sp is not None else 0.0
    if gap >= 16:
        pick = f"✅ {fav} -{sv:.1f}" if sv >= 3 else f"✅ {fav} ML"
    elif gap >= 6:
        pick = f"🟡 {fav} ML (lean)"
    else:
        pick = f"🔵 {dog} ML (dog value)" if gap < 0 else "⚪ Pass"

    # odds selection
    fav_ml = best_h if fav_is_home else best_a
    dog_ml = best_a if fav_is_home else best_h
    fav_bk = best_h_bk if fav_is_home else best_a_bk
    dog_bk = best_a_bk if fav_is_home else best_h_bk

    fav_imp = ml_to_implied(fav_ml) if fav_ml is not None else None
    edge = (conf - fav_imp) if fav_imp is not None else None
    edge_str = f"+{edge:.1f}%" if edge is not None and edge > 0 else "—"

    # Line movement tracker
    move_flag=""
    game_key=f"{fav}v{dog}"
    st.session_state.setdefault("odds_open", {})
    if fav_ml is not None:
        if game_key not in st.session_state["odds_open"]:
            st.session_state["odds_open"][game_key]=fav_ml
        else:
            diff = float(fav_ml) - float(st.session_state["odds_open"][game_key])
            if abs(diff) >= 5:
                move_flag = f"{'📈' if diff>0 else '📉'} {diff:+.0f}"

    # Injuries
    fav_inj = injuries.get(fav_full,[]) + injuries.get(fav,[])
    dog_inj = injuries.get(dog_full,[]) + injuries.get(dog,[])

    # Risk + reasoning
    risk_label, risk_cls = get_risk_tag(sl, fs_, ds_, gap, sp)
    if sl=="NBA":
        b2b_f = (fav_full in b2b_teams) or (fav in b2b_teams)
        b2b_d = (dog_full in b2b_teams) or (dog in b2b_teams)
    else:
        b2b_f=b2b_d=False
    reasoning = build_reasoning(sl, fav, dog, fs_, ds_, b2b_f, b2b_d, move_flag, fav_inj, dog_inj)

    # Alt line suggestion (simple)
    alt = "—"
    if gap >= 28 and sv >= 7: alt = f"Alt -{int(sv-4)} to -{int(sv-2)}"
    elif gap >= 16 and sv >= 5: alt = f"Alt -{int(sv-3)}"

    return {
        "Time": gt,
        "Favorite": fav,
        "Underdog": dog,
        "Pick": pick,
        "Gap": gap,
        "Conf%": conf,
        "Rating": rating,
        "Fav ML": f"{fav_ml:+.0f} ({str(fav_bk)[:2].upper()})" if fav_ml is not None else "—",
        "Dog ML": f"{dog_ml:+.0f} ({str(dog_bk)[:2].upper()})" if dog_ml is not None else "—",
        "Fav Impl%": f"{fav_imp:.2f}%" if fav_imp is not None else "—",
        "Edge%": edge_str,
        "Spread": f"{fav} -{sv:.1f}" if sv else "—",
        "Alt Spread": alt,
        "Line Move": move_flag if move_flag else "—",
        "Risk": risk_label,
        "_risk_cls": risk_cls,
        "_reasoning": reasoning,
        "_fav_inj": fav_inj,
        "_dog_inj": dog_inj,
    }

def parse_games(odds_data, sl, team_stats, injuries, b2b_teams):
    rows=[]
    seen=set()
    for g in odds_data:
        home_full=g.get("home_team",""); away_full=g.get("away_team","")
        matchup_key=tuple(sorted([home_full.lower(), away_full.lower()]))
        if matchup_key in seen: continue
        seen.add(matchup_key)

        gt=_gametime_from_odds(g)
        bk_data,best_h,best_h_bk,best_a,best_a_bk,sp=_extract_all_books(g, home_full, away_full)

        # Determine favorite by spread (preferred) else ML
        if sp is not None:
            fav_is_home = (sp < 0)
        elif best_h is not None and best_a is not None:
            fav_is_home = (best_h <= best_a)  # more negative = stronger favorite
        else:
            fav_is_home = True

        fav_full = home_full if fav_is_home else away_full
        dog_full = away_full if fav_is_home else home_full

        # Team mapping + stat lookup
        if sl=="MLB":
            fav = MLB_NAME_MAP.get(fav_full, fav_full[:3].upper())
            dog = MLB_NAME_MAP.get(dog_full, dog_full[:3].upper())
            fs_ = team_stats.get(fav, MLB_FB.get(fav,{}))
            ds_ = team_stats.get(dog, MLB_FB.get(dog,{}))
        elif sl=="NBA":
            fav = NBA_NAME_MAP.get(fav_full, fav_full)
            dog = NBA_NAME_MAP.get(dog_full, dog_full)
            fav, fs_ = _fuzzy(fav, team_stats, NBA_FB.get(fav,{}))
            dog, ds_ = _fuzzy(dog, team_stats, NBA_FB.get(dog,{}))
        elif sl=="NFL":
            fav, fs_ = _fuzzy(fav_full, team_stats, NFL_FB.get(fav_full,{}))
            dog, ds_ = _fuzzy(dog_full, team_stats, NFL_FB.get(dog_full,{}))
        elif sl=="CBB":
            fav, fs_ = _fuzzy(normalize_team(fav_full), team_stats, CBB_FB.get(normalize_team(fav_full),{}))
            dog, ds_ = _fuzzy(normalize_team(dog_full), team_stats, CBB_FB.get(normalize_team(dog_full),{}))
        else:
            fav, fs_ = _fuzzy(fav_full, team_stats, CFB_FB.get(fav_full,{}))
            dog, ds_ = _fuzzy(dog_full, team_stats, CFB_FB.get(dog_full,{}))

        row=_make_row(sl, fav, dog, fav_full, dog_full, fav_is_home, sp, gt, bk_data, best_h, best_h_bk, best_a, best_a_bk, fs_, ds_, b2b_teams, injuries)
        rows.append(row)

    # Validation filter: remove low-signal picks
    min_gap = st.session_state.get("min_gap", 0)
    min_conf = st.session_state.get("min_conf", 0)
    filtered=[]
    for r in sorted(rows, key=lambda x:x["Gap"], reverse=True):
        if r["Gap"] < min_gap: continue
        if r["Conf%"] < min_conf: continue
        filtered.append(r)
    return filtered

# ── PROPS (synthetic Kalshi levels) ───────────────────────────────────────────
def project_prop(player_name, prop_type, opp_team, spread=5.0, total=220.0, b2b=False):
    p = NBA_PROPS_FB.get(player_name)
    if not p: return None
    pos = p.get("pos","G")
    opp = NBA_OPP_DEF.get(opp_team, {})
    # Weighted avg (60% season, 40% last5) for stability
    def wavg(season, last5): return season*0.60 + last5*0.40

    if prop_type=="pts":
        base = wavg(p["pts_avg"], p["pts_last5"])
        league_avg = {"G":24.5,"F":25.2,"C":27.5}.get(pos,25.0)
        opp_allowed = opp.get(f"{pos}_pts", league_avg)
        opp_adj = (opp_allowed - league_avg)*0.35
        blowout_adj = -1.8 if spread>=12 else (-0.8 if spread>=8 else 0)
        b2b_adj = -1.5 if b2b else 0
        proj = base + opp_adj + blowout_adj + b2b_adj
        std = max(2.5, base*0.22)
    elif prop_type=="reb":
        base = wavg(p["reb_avg"], p["reb_last5"])
        league_avg = {"G":4.5,"F":7.0,"C":11.5}.get(pos,6.0)
        opp_allowed = opp.get(f"{pos}_reb", league_avg)
        opp_adj = (opp_allowed - league_avg)*0.30
        proj = base + opp_adj + (-0.8 if b2b else 0)
        std = max(1.5, base*0.28)
    elif prop_type=="ast":
        base = wavg(p["ast_avg"], p["ast_last5"])
        proj = base + (-0.5 if b2b else 0)
        std = max(1.5, base*0.32)
    else:  # pra
        pts = project_prop(player_name,"pts",opp_team,spread,total,b2b)
        reb = project_prop(player_name,"reb",opp_team,spread,total,b2b)
        ast = project_prop(player_name,"ast",opp_team,spread,total,b2b)
        if not pts or not reb or not ast: return None
        proj = pts["proj"] + reb["proj"] + ast["proj"]
        std = max(3.0, proj*0.20)
    return {"player":player_name,"team":p["team"],"pos":pos,"opp":opp_team,"proj":round(max(0,proj),1),"std":round(std,1),"mins":p.get("mins",30.0),"usg":p.get("usg_rate",0.25)}

def prop_conf(proj, line, std):
    if std <= 0: return 50.0
    z = (proj - line)/std
    conf = 50.0 + 35.0*math.tanh(z*0.8)
    return round(min(85.0, max(15.0, conf)), 1)

# ── SESSION STATE ─────────────────────────────────────────────────────────────
st.session_state.setdefault("odds_open", {})
st.session_state.setdefault("min_conf", 0)
st.session_state.setdefault("min_gap", 0)

# ── SIDEBAR (decision tools) ─────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🏆 Betting Dashboard")
    st.caption(f"{today_est().strftime('%A, %B %d')} · {now_est()}")
    st.divider()

    sport = st.radio("Sport", list(SPORT_CONFIG.keys()), label_visibility="collapsed")
    sl = SPORT_CONFIG[sport]["label"]
    em = SPORT_ICONS.get(sl,"🏆")

    # Tracker summary
    picks = load_picks()
    summ = calc_summary(picks)
    streak = calc_streak(picks)
    pl_col = "#4ade80" if summ["pl"] >= 0 else "#f87171"
    st.markdown(f"""
<div style="background:#131929;border:1px solid #1e2a45;border-radius:10px;padding:10px 14px;margin-bottom:8px">
  <div style="font-size:0.72rem;color:#8892a4;margin-bottom:4px">📈 SEASON</div>
  <div style="display:flex;justify-content:space-between;align-items:center">
    <span style="font-size:1.05rem;font-weight:800;color:#e8eaf0">{summ['wins']}-{summ['losses']}</span>
    <span style="font-family:'DM Mono',monospace;color:{pl_col};font-size:0.85rem">{'+' if summ['pl']>=0 else ''}{summ['pl']}u</span>
  </div>
  <div style="font-size:0.70rem;color:#5a6478">{summ['hit_rate']:.1f}% · ROI {summ['roi']:.1f}% · {streak}</div>
</div>
""", unsafe_allow_html=True)

    # Filters
    st.session_state["min_conf"] = st.slider("Min Confidence %", 0, 80, int(st.session_state["min_conf"]), 5)
    st.session_state["min_gap"]  = st.slider("Min Model Gap", 0, 20, int(st.session_state["min_gap"]), 2,
                                             help="Higher = fewer picks, more selective")
    st.divider()

    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.session_state["odds_open"] = {}
        st.rerun()

    st.caption("Cache: stats 6hr · odds 5min · scores 60s")

# ── LOAD DATA (sport-specific) ───────────────────────────────────────────────
cfg = SPORT_CONFIG[sport]
with st.spinner(f"Loading {sl}…"):
    if sl=="MLB": team_stats, src = live_mlb()
    elif sl=="NBA": team_stats, src = live_nba()
    elif sl=="NFL": team_stats, src = live_nfl()
    elif sl=="CBB": team_stats, src = live_cbb()
    else: team_stats, src = live_cfb()

    odds_raw = fetch_odds(cfg["key"])
    injuries = fetch_injuries(cfg["espn_sport"], cfg["espn_league"])
    b2b_teams = get_b2b_teams(cfg["espn_sport"], cfg["espn_league"]) if sl=="NBA" else set()
    games = parse_games(odds_raw, sl, team_stats, injuries, b2b_teams)

    espn_today = parse_espn_events(fetch_espn(cfg["espn_sport"], cfg["espn_league"]))

# ── HEADER ───────────────────────────────────────────────────────────────────
src_icon = "🟢" if src=="live" else "🟡"
st.markdown(f"**{em} {sl}** · {today_est().strftime('%b %d')} · {src_icon} {src} · {len(games)} filtered picks")

st.divider()

# ── TABS ─────────────────────────────────────────────────────────────────────
tabs = st.tabs(["🗓️ Today", "🎯 Props", "📺 Scores", "📋 Stats", "⚙️ Settings", "📈 Tracker"])

# ══════════════════════════════════════════════════════════════════════════════
# TAB: TODAY (Pick cards)
# ══════════════════════════════════════════════════════════════════════════════
with tabs[0]:
    if not games:
        st.info("No picks meet your filters. Lower Min Confidence / Min Gap to see more.")
    else:
        # limit: show 1–3 picks per game is inherently satisfied by one pick per game here.
        for g in games:
            c1,c2,c3 = st.columns([3.5,1.4,1.6])
            with c1:
                st.markdown(f"**{g['Favorite']}** vs **{g['Underdog']}**")
                st.caption(f"{g['Time']} · {g['Rating']}")
                st.markdown(f"**`{g['Pick']}`**")
                for b in g.get("_reasoning",[]):
                    st.caption(f"• {b}")
                # small flags
                flags=[]
                if g.get("_fav_inj"): flags.append("🏥 Inj")
                if g.get("_dog_inj"): flags.append("🏥 Opp Inj")
                if g.get("Line Move","—")!="—": flags.append(f"{g['Line Move']}")
                if flags: st.caption("  ·  ".join(flags))
                if g.get("Alt Spread","—")!="—":
                    st.caption(f"💡 Alt line: {g['Alt Spread']}")
            with c2:
                st.progress(int(min(g["Conf%"],100)), text=f"{g['Conf%']:.0f}%")
                st.markdown(f'<span class="{g["_risk_cls"]}">{g["Risk"]} variance</span>', unsafe_allow_html=True)
                st.caption(f"Gap {g['Gap']:.1f}")
            with c3:
                st.metric("Fav ML", g.get("Fav ML","—"))
                st.caption(f"Edge: {g.get('Edge%','—')}")
                if g.get("Spread","—")!="—": st.caption(g["Spread"])
            st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# TAB: PROPS (NBA only, synthetic Kalshi levels)
# ══════════════════════════════════════════════════════════════════════════════
with tabs[1]:
    st.markdown("#### 🎯 NBA Props (Kalshi-style levels)")
    st.caption("Stable mode: synthetic line levels around projection. Verify real Kalshi lines before betting.")
    if sl != "NBA":
        st.info("Switch to 🏀 NBA to see props.")
    else:
        p1,p2,p3 = st.columns([1.4,1.2,1.2])
        prop_type = p1.selectbox("Prop", ["pts","reb","ast","pra"], format_func=lambda x:{"pts":"Points","reb":"Rebounds","ast":"Assists","pra":"PRA"}[x])
        min_edge = p2.slider("Min Edge %", 0, 30, 6, 1)
        min_conf = p3.slider("Min Conf %", 0, 85, 60, 5)

        # Determine today's matchups from games list (fav/dog are display; we use teams from props fallback)
        today_teams=set()
        for g in games:
            today_teams.add(g["Favorite"])
            today_teams.add(g["Underdog"])

        results=[]
        for player, pb in NBA_PROPS_FB.items():
            team = pb.get("team","")
            # If team isn't in today's matchup list, skip (best effort)
            if today_teams and team not in today_teams:
                continue
            # Pick a best-effort opponent
            opp = None
            for g in games:
                if team == g["Favorite"]:
                    opp = g["Underdog"]; spread = 6.0; break
                if team == g["Underdog"]:
                    opp = g["Favorite"]; spread = 6.0; break
            if not opp:
                opp = "Washington Wizards"
                spread = 6.0

            proj = project_prop(player, prop_type, opp, spread=spread)
            if not proj: 
                continue

            # synthetic Kalshi thresholds
            base = proj["proj"]
            line_low  = round(base * 0.88, 1)
            line_mid  = round(base * 0.94, 1)
            line_high = round(base * 1.05, 1)

            conf_low  = prop_conf(base, line_low, proj["std"])
            conf_mid  = prop_conf(base, line_mid, proj["std"])
            conf_high = prop_conf(base, line_high, proj["std"])

            best_line = line_mid
            best_conf = conf_mid
            edge_pct = round((base - best_line)/max(best_line,1)*100, 1)

            if edge_pct < min_edge or best_conf < min_conf:
                continue

            results.append({
                "Player": player,
                "Team": team,
                "Opp": opp,
                "Proj": base,
                "Line (low)": line_low,
                "Line (mid)": line_mid,
                "Line (high)": line_high,
                "Edge%": edge_pct,
                "Conf%": best_conf,
                "Conf low": conf_low,
                "Conf mid": conf_mid,
                "Conf high": conf_high,
                "Std": proj["std"],
            })

        if not results:
            st.info("No props match filters. Lower Min Edge / Min Conf.")
        else:
            results.sort(key=lambda x:(x["Edge%"], x["Conf%"]), reverse=True)
            for r in results[:20]:
                c1,c2,c3,c4 = st.columns([2.6,1.2,1.2,2.0])
                with c1:
                    st.markdown(f"**{r['Player']}** — {r['Team']}")
                    st.caption(f"vs {r['Opp']}")
                with c2:
                    st.metric("Proj", f"{r['Proj']}")
                    st.caption(f"Std ~{r['Std']}")
                with c3:
                    st.metric("Edge", f"+{r['Edge%']}%")
                    st.caption(f"Conf {r['Conf%']}%")
                with c4:
                    st.caption("Kalshi levels:")
                    st.caption(f"✅ {r['Line (low)']} → {r['Conf low']:.0f}%")
                    st.caption(f"🟡 {r['Line (mid)']} → {r['Conf mid']:.0f}%")
                    st.caption(f"⚠️ {r['Line (high)']} → {r['Conf high']:.0f}%")
                st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# TAB: SCORES
# ══════════════════════════════════════════════════════════════════════════════
with tabs[2]:
    st.markdown("#### 📺 Scores (ESPN)")
    if not espn_today:
        st.info("No scoreboard data.")
    else:
        for g in espn_today:
            st.markdown(score_card_html(g), unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB: STATS
# ══════════════════════════════════════════════════════════════════════════════
with tabs[3]:
    st.markdown(f"#### 📋 Team Stats — {sl}")
    st.caption(f"Source: {src}")
    if sl=="MLB":
        rows=[{"Team":k,"Score":score_mlb(v),"Pyth":v.get("pyth_win"),"RD/G":v.get("run_diff_pg"),"BP FIP":v.get("bullpen_fip"),"wRC+":v.get("wrc_plus"),"SP FIP":v.get("starter_fip")} for k,v in team_stats.items()]
    elif sl=="NBA":
        rows=[{"Team":k,"Score":score_nba(v),"Net":v.get("net_rtg"),"PIE":v.get("pie_pct"),"TS":v.get("ts_pct"),"3P rate":v.get("three_pt_rate"),"DefReb":v.get("def_reb_pct"),"TO":v.get("to_rate")} for k,v in team_stats.items()]
    elif sl=="NFL":
        rows=[{"Team":k,"Score":score_nfl(v),"EPA off":v.get("epa_off"),"EPA def":v.get("epa_def"),"TO":v.get("to_margin"),"Pyth":v.get("pyth_win"),"3rd":v.get("third_down_pct"),"RZ":v.get("rz_td_pct")} for k,v in team_stats.items()]
    elif sl=="CBB":
        rows=[{"Team":k,"Score":score_cbb(v),"Eff":v.get("eff_margin"),"AdjO":v.get("adj_o"),"AdjD":v.get("adj_d"),"Tempo":v.get("tempo")} for k,v in team_stats.items()]
    else:
        rows=[{"Team":k,"Score":score_cfb(v),"SP+":v.get("sp_plus"),"Off":v.get("off_sp"),"Def":v.get("def_sp"),"Win%":v.get("win_pct")} for k,v in team_stats.items()]
    df=pd.DataFrame(rows).sort_values("Score", ascending=False)
    st.dataframe(df, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB: SETTINGS (weights)
# ══════════════════════════════════════════════════════════════════════════════
with tabs[4]:
    st.markdown("#### ⚙️ Settings — Weights")
    st.caption("Weights auto-normalize. Adjust for experimentation; defaults are research-aligned.")
    sport_key = st.selectbox("Configure sport:", ["MLB","NBA","NFL","CBB","CFB"], index=["MLB","NBA","NFL","CBB","CFB"].index(sl))
    defaults=DEFAULT_WEIGHTS[sport_key]
    for stat, dv in defaults.items():
        st.session_state[f"w_{sport_key}_{stat}"] = st.slider(f"{stat}", 0, 100, int(st.session_state.get(f"w_{sport_key}_{stat}", dv)), 1)

# ══════════════════════════════════════════════════════════════════════════════
# TAB: TRACKER
# ══════════════════════════════════════════════════════════════════════════════
with tabs[5]:
    st.markdown("#### 📈 Tracker")
    picks = load_picks()
    summ = calc_summary(picks)
    st.metric("Overall", f"{summ['wins']}-{summ['losses']}")
    st.metric("Hit rate", f"{summ['hit_rate']}%")
    st.metric("P&L", f"{'+' if summ['pl']>=0 else ''}{summ['pl']}u")
    st.metric("ROI", f"{'+' if summ['roi']>=0 else ''}{summ['roi']}%")
    st.divider()
    with st.expander("➕ Log a pick"):
        c1,c2,c3 = st.columns(3)
        ps = c1.selectbox("Sport", ["MLB","NBA","NFL","CBB","CFB"], index=["MLB","NBA","NFL","CBB","CFB"].index(sl))
        bet_type = c2.selectbox("Bet type", ["ML","Spread","Prop","Other"])
        units = c3.number_input("Units", 0.1, 10.0, 0.5, 0.25)
        fav = st.text_input("Favorite / Player")
        dog = st.text_input("Opponent / Game")
        odds = st.text_input("Odds (e.g. -110)")
        notes = st.text_input("Notes")
        if st.button("Save pick"):
            if fav:
                picks.append({"date":today_est().isoformat(),"sport":ps,"bet_type":bet_type,"favorite":fav,"underdog":dog,
                              "odds":odds,"units":units,"notes":notes,"result":"Pending"})
                save_picks(picks)
                st.success("Saved.")
                st.rerun()
            else:
                st.error("Enter at least a Favorite/Player.")

    if picks:
        st.divider()
        df=pd.DataFrame(picks)
        ed = st.data_editor(df, use_container_width=True, num_rows="dynamic",
                            column_config={"result": st.column_config.SelectboxColumn("Result", options=["Pending","W","L","P"])})
        if st.button("Save results"):
            save_picks(ed.to_dict("records"))
            st.success("Updated.")
            st.rerun()
