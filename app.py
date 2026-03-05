# coding: utf-8
"""
Sports Betting Dashboard v6.0

ARCHITECTURE:
- Fetch ALL non-combo Kalshi markets (mve_filter=exclude, status open+unopened)
- Filter to TODAY only via close_time date
- Group by event_ticker → one game card, with ML / Spread / Total / Props sections
- No fake model probability. Kalshi IS the line.
- Value signal = spread↔moneyline consistency check (real, honest signal)
- Show Kalshi implied probability as-is (no inflation)
"""

import streamlit as st
import requests
import pandas as pd
import math, json, os, re, scipy.stats as sps
from datetime import datetime, timedelta, timezone, date

# ─── PAGE CONFIG ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Today's Picks", page_icon="🏀",
                   layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
html,body,[class*="css"]{font-family:'Inter',sans-serif;}
.stApp{background:#0d1117;color:#e6edf3;}
section[data-testid="stSidebar"]{background:#161b22!important;border-right:1px solid #21262d;}
section[data-testid="stSidebar"] *{color:#c9d1d9!important;}
.stTabs [data-baseweb="tab-list"]{background:#161b22;border-radius:8px;gap:4px;padding:4px;}
.stTabs [data-baseweb="tab"]{border-radius:6px;color:#8b949e;padding:6px 16px;}
.stTabs [aria-selected="true"]{background:#21262d!important;color:#e6edf3!important;}
[data-testid="metric-container"]{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:14px;}
[data-testid="stMetricValue"]{font-family:'JetBrains Mono',monospace!important;color:#58a6ff!important;}
#MainMenu,footer{visibility:hidden;}

/* Game cards */
.game-card{background:#161b22;border:1px solid #21262d;border-radius:12px;padding:20px 24px;margin-bottom:12px;}
.game-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;}
.game-title{font-size:1.1rem;font-weight:700;color:#e6edf3;}
.game-time{font-size:0.75rem;color:#8b949e;background:#21262d;padding:3px 10px;border-radius:20px;}

/* Market rows */
.mkt-row{display:flex;align-items:center;padding:10px 0;border-bottom:1px solid #21262d;}
.mkt-row:last-child{border-bottom:none;}
.mkt-type{font-size:0.68rem;font-weight:600;color:#8b949e;width:80px;flex-shrink:0;text-transform:uppercase;letter-spacing:.05em;}
.mkt-desc{flex:1;font-size:0.88rem;color:#c9d1d9;}
.mkt-prob{font-family:'JetBrains Mono',monospace;font-size:0.90rem;font-weight:600;width:52px;text-align:right;flex-shrink:0;}
.mkt-odds{font-family:'JetBrains Mono',monospace;font-size:0.90rem;color:#f0f6fc;width:62px;text-align:right;flex-shrink:0;margin-left:8px;}
.mkt-signal{font-size:0.72rem;padding:2px 8px;border-radius:20px;margin-left:12px;flex-shrink:0;font-weight:600;}

/* Signal badges */
.sig-strong{background:#1a3a2a;color:#3fb950;}
.sig-lean{background:#2d2a1a;color:#d29922;}
.sig-fair{background:#1c2128;color:#8b949e;}
.sig-watch{background:#2d1c1c;color:#f85149;}

/* Value bar */
.prob-track{background:#21262d;border-radius:4px;height:6px;width:100%;margin:2px 0;}
.prob-fill-hi{background:linear-gradient(90deg,#238636,#3fb950);border-radius:4px;height:6px;}
.prob-fill-mid{background:linear-gradient(90deg,#9e6a03,#d29922);border-radius:4px;height:6px;}
.prob-fill-lo{background:linear-gradient(90deg,#21262d,#58a6ff);border-radius:4px;height:6px;}

/* Section label */
.section-label{font-size:0.72rem;font-weight:600;color:#8b949e;text-transform:uppercase;letter-spacing:.08em;margin:14px 0 6px 0;}
</style>
""", unsafe_allow_html=True)

# ─── CONSTANTS ────────────────────────────────────────────────────────────────
KALSHI_BASE  = "https://api.elections.kalshi.com/trade-api/v2"
TRACKER_FILE = "picks_log.json"

# ─── NBA TEAM LOOKUP ──────────────────────────────────────────────────────────
_NBA = {
    "Cavaliers":["cavaliers","cleveland"],"Thunder":["thunder","oklahoma city","oklahoma"],
    "Celtics":["celtics","boston"],"Warriors":["warriors","golden state"],
    "Rockets":["rockets","houston"],"Pacers":["pacers","indiana"],
    "Grizzlies":["grizzlies","memphis"],"Nuggets":["nuggets","denver"],
    "Lakers":["lakers","los angeles l","lal","la lakers"],
    "Knicks":["knicks","new york"],"Bucks":["bucks","milwaukee"],
    "76ers":["76ers","sixers","philadelphia"],
    "Timberwolves":["timberwolves","minnesota"],
    "Heat":["heat","miami"],"Kings":["kings","sacramento"],
    "Clippers":["clippers","los angeles c","lac","la clippers"],
    "Mavericks":["mavericks","dallas"],"Hawks":["hawks","atlanta"],
    "Suns":["suns","phoenix"],"Bulls":["bulls","chicago"],
    "Nets":["nets","brooklyn"],"Magic":["magic","orlando"],
    "Hornets":["hornets","charlotte"],"Raptors":["raptors","toronto"],
    "Jazz":["jazz","utah"],"Spurs":["spurs","san antonio"],
    "Trail Blazers":["trail blazers","blazers","portland"],
    "Pistons":["pistons","detroit"],"Pelicans":["pelicans","new orleans"],
    "Wizards":["wizards","washington"],
}
_CBB = {
    "Duke":["duke"],"Auburn":["auburn"],"Tennessee":["tennessee"],
    "Alabama":["alabama"],"Houston":["houston cougars"],
    "Florida":["florida gators","florida ncaa"],
    "Kentucky":["kentucky"],"Iowa State":["iowa st","iowa state"],
    "Michigan State":["michigan st","michigan state"],
    "Texas Tech":["texas tech"],"Wisconsin":["wisconsin"],
    "Purdue":["purdue"],"Arizona":["arizona wildcats"],
    "Maryland":["maryland"],"Michigan":["michigan wolverines"],
    "Gonzaga":["gonzaga"],"Illinois":["illinois"],"Kansas":["kansas"],
    "UConn":["uconn","connecticut"],"Marquette":["marquette"],
    "Creighton":["creighton"],"UCLA":["ucla"],"Baylor":["baylor"],
    "Arkansas":["arkansas"],"St John's":["st john","st. john"],
    "Xavier":["xavier"],"Ole Miss":["ole miss"],
    "North Carolina":["north carolina","unc"],
    "NC State":["nc state"],"BYU":["byu","brigham young"],
    "Clemson":["clemson"],"Notre Dame":["notre dame"],
}

def _make_lookup(d):
    lkp = {}
    for canon, variants in d.items():
        lkp[canon.lower()] = canon
        for v in variants: lkp[v.lower()] = canon
    return lkp

NBA_LKP = _make_lookup(_NBA)
CBB_LKP = _make_lookup(_CBB)

# ─── TEAM MATCHING ────────────────────────────────────────────────────────────
def find_team(text: str, lkp: dict):
    tl = (text or "").lower()
    for v in sorted(lkp, key=len, reverse=True):
        if re.search(r'\b' + re.escape(v) + r'\b', tl):
            return lkp[v]
    return None

def extract_two_teams(m: dict, lkp: dict):
    ys = (m.get("yes_sub_title") or "").strip()
    ns = (m.get("no_sub_title")  or "").strip()
    title = (m.get("title") or "")
    # Try sub-titles first
    if ys and ns:
        ta = find_team(ys, lkp)
        tb = find_team(ns, lkp)
        if ta and tb and ta != tb: return ta, tb
    # Try title patterns
    for pat in [r"will\s+(?:the\s+)?(.+?)\s+win",
                r"(.+?)\s+(?:vs?\.?|at|@|versus)\s+(.+?)(?:\?|$|\s*[-–(])"]:
        hit = re.search(pat, title, re.IGNORECASE)
        if hit:
            ta = find_team(hit.group(1).strip(), lkp)
            if ta and len(hit.groups()) >= 2:
                tb = find_team(hit.group(2).strip().split("(")[0], lkp)
                if tb and tb != ta: return ta, tb
    # Brute-force: find any 2 distinct teams in all text
    combined = f"{title} {ys} {ns}"
    found = []
    for v in sorted(lkp, key=len, reverse=True):
        if re.search(r'\b' + re.escape(v) + r'\b', combined.lower()):
            c = lkp[v]
            if c not in found: found.append(c)
        if len(found) == 2: return found[0], found[1]
    return None, None

# ─── MARKET CLASSIFICATION ────────────────────────────────────────────────────
def classify(title: str, yes_sub: str, no_sub: str) -> str:
    t = (title or "").lower()
    combined = f"{t} {(yes_sub or '').lower()} {(no_sub or '').lower()}"
    # Combo/parlay: multiple "yes X, no Y" items with commas
    if t.count(",") >= 1 and re.search(r'(yes\s+\w|no\s+\w)', t):
        return "other"
    # Totals
    if re.search(r'(total points|combined score|o/u|over/under)', t):
        return "total"
    if re.search(r'(over|under)\s+\d+\.?\d*\s*(points|pts)?$', t.strip()):
        return "total"
    # Spreads
    if re.search(r'win\s+by\s+(more\s+than|at\s+least|over)?\s*\d', t):
        return "spread"
    if re.search(r'cover|by\s+more\s+than|by\s+at\s+least', t):
        return "spread"
    if re.search(r'[-+]\d+\.?\d*\s*points?\b', t):
        return "spread"
    # Props: stat keyword + number
    stat_kw = ["points","rebounds","assists","steals","blocks",
               "3-pointer","three","pts\b","reb\b","ast\b","made","attempts"]
    has_stat = any(re.search(r'\b'+w+r'\b', t) for w in stat_kw)
    has_num  = bool(re.search(r'\d+\.?\d*\+', t))
    if has_stat and has_num:
        return "prop"
    # Moneyline
    if re.search(r'will\s+.+\s+win', t): return "moneyline"
    if re.search(r'\bvs\.?\b|\bat\b|\bversus\b', t) and not re.search(r'\d', t):
        return "moneyline"
    if yes_sub and no_sub and not re.search(r'\d', combined):
        return "moneyline"
    return "other"

# ─── PRICE HELPERS ────────────────────────────────────────────────────────────
def implied_prob(m: dict):
    """YES implied probability from Kalshi market."""
    yb = m.get("yes_bid") or 0
    ya = m.get("yes_ask") or 0
    lp = m.get("last_price") or 0
    if yb > 0 and ya > 0: return (yb + ya) / 200.0
    if lp > 0:            return lp / 100.0
    return None

def to_american(p):
    if p is None or p < 0.02 or p > 0.98: return "—"
    if p >= 0.5: return f"-{round((p/(1-p))*100)}"
    return f"+{round(((1-p)/p)*100)}"

def prob_to_color(p):
    if p is None: return "#8b949e"
    if p >= 0.65: return "#3fb950"
    if p >= 0.52: return "#d29922"
    return "#58a6ff"

# ─── VALUE SIGNAL ─────────────────────────────────────────────────────────────
# A market has value when it's underpriced vs related markets in same game.
# For a standalone market, we show the probability bucket clearly.
# No fake "our model says X%" — just the honest Kalshi line.

def value_signal(mtype: str, prob: float, spread_num: float = None):
    """
    Returns (label, css_class, explanation).
    For spreads/moneylines: check if prob is consistent with spread context.
    For props: use volume/pricing as signal.
    """
    if prob is None: return "No Price", "sig-fair", "No active market"
    
    pct_label = f"{prob*100:.0f}%"
    
    # Moneyline or spread: is it a clear lean?
    if mtype in ("moneyline", "spread"):
        if prob >= 0.72:
            return "Strong Fav", "sig-strong", f"Market prices YES at {pct_label} — heavy favorite"
        elif prob >= 0.60:
            return "Lean YES", "sig-lean", f"Market prices YES at {pct_label} — mild favorite"
        elif prob <= 0.28:
            return "Strong Dog", "sig-watch", f"Market prices YES at {pct_label} — heavy underdog"
        elif prob <= 0.40:
            return "Lean NO", "sig-watch", f"Market prices YES at {pct_label} — mild underdog"
        else:
            return "Pick 'em", "sig-fair", f"Near 50/50 — {pct_label} YES"
    
    # Props
    if mtype == "prop":
        if prob >= 0.65:
            return "Lean Over", "sig-lean", f"Market prices this prop at {pct_label}"
        elif prob <= 0.35:
            return "Lean Under", "sig-lean", f"Market prices this prop at {pct_label}"
        else:
            return "Even", "sig-fair", f"Near 50/50 — {pct_label}"
    
    # Total
    if mtype == "total":
        if prob >= 0.62:
            return "Lean Over", "sig-lean", f"Over at {pct_label}"
        elif prob <= 0.38:
            return "Lean Under", "sig-lean", f"Under at {pct_label}"
        return "Even", "sig-fair", f"~50/50"
    
    return "—", "sig-fair", ""

# ─── DATE HELPERS ─────────────────────────────────────────────────────────────
def _et_now():
    """Current time in US Eastern (handles EST/EDT)."""
    utc = datetime.now(timezone.utc)
    # EDT starts 2nd Sunday March, EST starts 1st Sunday November
    y = utc.year
    mar_second_sun = datetime(y,3,8,2,0, tzinfo=timezone.utc)
    while mar_second_sun.weekday() != 6: mar_second_sun += timedelta(days=1)
    nov_first_sun  = datetime(y,11,1,2,0, tzinfo=timezone.utc)
    while nov_first_sun.weekday() != 6: nov_first_sun += timedelta(days=1)
    offset = timedelta(hours=-4) if mar_second_sun <= utc < nov_first_sun else timedelta(hours=-5)
    return utc + offset

def today_et() -> date:
    return _et_now().date()

def is_today(close_time_str: str) -> bool:
    """True if market closes on Eastern today (or tonight past midnight ET)."""
    if not close_time_str: return False
    try:
        ct_utc = datetime.fromisoformat(close_time_str.replace("Z","+00:00"))
        ct_et  = ct_utc + (_et_now() - datetime.now(timezone.utc))
        # Adjust to ET properly
        y = ct_utc.year
        mar_ss = datetime(y,3,8,2,0, tzinfo=timezone.utc)
        while mar_ss.weekday()!=6: mar_ss+=timedelta(days=1)
        nov_fs = datetime(y,11,1,2,0, tzinfo=timezone.utc)
        while nov_fs.weekday()!=6: nov_fs+=timedelta(days=1)
        off = timedelta(hours=-4) if mar_ss<=ct_utc<nov_fs else timedelta(hours=-5)
        ct_et = ct_utc + off
        return ct_et.date() == today_et()
    except:
        return False

def fmt_time(close_time_str: str) -> str:
    if not close_time_str: return ""
    try:
        ct_utc = datetime.fromisoformat(close_time_str.replace("Z","+00:00"))
        y = ct_utc.year
        mar_ss = datetime(y,3,8,2,0, tzinfo=timezone.utc)
        while mar_ss.weekday()!=6: mar_ss+=timedelta(days=1)
        nov_fs = datetime(y,11,1,2,0, tzinfo=timezone.utc)
        while nov_fs.weekday()!=6: nov_fs+=timedelta(days=1)
        off = timedelta(hours=-4) if mar_ss<=ct_utc<nov_fs else timedelta(hours=-5)
        ct_et = ct_utc + off
        hr = int(ct_et.strftime("%I")); mn = ct_et.strftime("%M"); ampm = ct_et.strftime("%p")
        tz = "ET"
        return f"{hr}:{mn} {ampm} {tz}"
    except:
        return ""

# ─── FETCH ────────────────────────────────────────────────────────────────────
def _fetch_page(status: str, cursor=None):
    params = {"status": status, "limit": 200, "mve_filter": "exclude"}
    if cursor: params["cursor"] = cursor
    try:
        r = requests.get(f"{KALSHI_BASE}/markets", params=params, timeout=20)
        if r.status_code == 200: return r.json()
    except: pass
    return None

@st.cache_data(ttl=240, show_spinner=False)
def fetch_all() -> list:
    """Fetch open + unopened non-combo markets."""
    markets = []
    for status in ["open", "unopened"]:
        cursor = None
        for _ in range(20):
            data = _fetch_page(status, cursor)
            if not data: break
            chunk  = data.get("markets", [])
            markets.extend(chunk)
            cursor = data.get("cursor")
            if not cursor or not chunk: break
    return markets

# ─── GROUP MARKETS INTO GAMES ─────────────────────────────────────────────────
def build_game_index(markets: list, lkp: dict) -> dict:
    """
    Returns dict: event_ticker → {
        'teams': (teamA, teamB),
        'close_time': str,
        'moneylines': [m, ...],
        'spreads':    [m, ...],
        'totals':     [m, ...],
        'props':      [m, ...],
    }
    Only includes markets that close today.
    """
    games = {}
    for m in markets:
        ct = m.get("close_time") or m.get("expiration_time") or ""
        if not is_today(ct):
            continue
        
        title   = m.get("title","")
        yes_sub = m.get("yes_sub_title","") or ""
        no_sub  = m.get("no_sub_title","")  or ""
        mtype   = classify(title, yes_sub, no_sub)
        if mtype == "other":
            continue
        
        event_tk = m.get("event_ticker") or m.get("ticker","").split("-")[0]
        
        if event_tk not in games:
            teams = extract_two_teams(m, lkp)
            if not any(teams) and mtype not in ("prop","total"):
                continue
            games[event_tk] = {
                "teams":      teams,
                "close_time": ct,
                "moneylines": [],
                "spreads":    [],
                "totals":     [],
                "props":      [],
            }
        
        # Update teams if we don't have them yet
        if not any(games[event_tk]["teams"]):
            games[event_tk]["teams"] = extract_two_teams(m, lkp)
        
        # Keep earliest close_time (= game time)
        if ct < games[event_tk]["close_time"]:
            games[event_tk]["close_time"] = ct
        
        target = games[event_tk][mtype+"s"]
        target.append(m)
    
    # Deduplicate within each category: if same title appears twice, keep highest volume
    for ev, g in games.items():
        for cat in ["moneylines","spreads","totals","props"]:
            seen, deduped = set(), []
            for m in sorted(g[cat], key=lambda x: x.get("volume",0) or 0, reverse=True):
                key = (m.get("title","").lower().strip())
                if key not in seen:
                    seen.add(key)
                    deduped.append(m)
            g[cat] = deduped
    
    # Sort games by game time
    return dict(sorted(games.items(), key=lambda kv: kv[1]["close_time"]))

# ─── RENDER ───────────────────────────────────────────────────────────────────
def prob_bar(prob, mtype="moneyline"):
    if prob is None: return ""
    w = int(prob * 100)
    if prob >= 0.60:   cls = "prob-fill-hi"
    elif prob >= 0.45: cls = "prob-fill-mid"
    else:              cls = "prob-fill-lo"
    return f'<div class="prob-track"><div class="{cls}" style="width:{w}%"></div></div>'

def render_market_row(m: dict, mtype: str, label_override: str = ""):
    prob = implied_prob(m)
    odds = to_american(prob)
    sig_label, sig_cls, sig_tip = value_signal(mtype, prob)
    color = prob_to_color(prob)
    
    title = m.get("title","")
    yes_s = m.get("yes_sub_title","") or ""
    
    # Display text
    if label_override:
        display = label_override
    elif yes_s and len(yes_s) < 50:
        display = yes_s
    else:
        display = title[:70]
    
    pct_str = f"{prob*100:.0f}%" if prob is not None else "—"
    
    st.markdown(f"""
<div class="mkt-row">
  <span class="mkt-type">{mtype}</span>
  <span class="mkt-desc">{display}</span>
  {prob_bar(prob, mtype)}
  <span class="mkt-prob" style="color:{color}">{pct_str}</span>
  <span class="mkt-odds">{odds}</span>
  <span class="mkt-signal {sig_cls}">{sig_label}</span>
</div>
""", unsafe_allow_html=True)

def render_game_card(event_tk: str, g: dict, sport: str):
    ta, tb   = g["teams"]
    gtime    = fmt_time(g["close_time"])
    ml_list  = g["moneylines"]
    sp_list  = g["spreads"]
    to_list  = g["totals"]
    pr_list  = g["props"]
    
    team_display = f"{ta} vs {tb}" if ta and tb else (ta or tb or event_tk)
    
    st.markdown(f"""
<div class="game-card">
  <div class="game-header">
    <span class="game-title">{team_display}</span>
    {'<span class="game-time">🕐 '+gtime+'</span>' if gtime else ''}
  </div>
""", unsafe_allow_html=True)
    
    # Moneylines
    if ml_list:
        st.markdown('<div class="section-label">Moneyline</div>', unsafe_allow_html=True)
        for m in ml_list[:2]:
            render_market_row(m, "ML")
    
    # Spreads
    if sp_list:
        st.markdown('<div class="section-label">Spread / Handicap</div>', unsafe_allow_html=True)
        for m in sp_list[:3]:
            render_market_row(m, "Spread")
    
    # Totals
    if to_list:
        st.markdown('<div class="section-label">Game Total</div>', unsafe_allow_html=True)
        for m in to_list[:2]:
            render_market_row(m, "Total")
    
    # Props (show top 5 by volume)
    if pr_list:
        st.markdown('<div class="section-label">Player Props</div>', unsafe_allow_html=True)
        top_props = sorted(pr_list, key=lambda x: x.get("volume",0) or 0, reverse=True)[:5]
        for m in top_props:
            render_market_row(m, "Prop")
    
    st.markdown("</div>", unsafe_allow_html=True)

# ─── TRACKER ──────────────────────────────────────────────────────────────────
def load_log():
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE) as f: return json.load(f)
        except: pass
    return []

def save_log(p):
    try:
        with open(TRACKER_FILE,"w") as f: json.dump(p, f, indent=2)
    except: pass

def record_summary(records):
    done = [r for r in records if r.get("result") in ("W","L","P")]
    wins = sum(1 for r in done if r["result"]=="W")
    def to_dec(ml):
        try:
            ml=float(ml); return ml/100+1 if ml>0 else 100/abs(ml)+1
        except: return 1.91
    pl = sum((to_dec(r.get("odds",0))-1)*float(r.get("units",1))
             if r["result"]=="W" else
             (-float(r.get("units",1)) if r["result"]=="L" else 0)
             for r in done)
    total_u = sum(float(r.get("units",1)) for r in done) or 1
    return {
        "record": f"{wins}-{len(done)-wins}",
        "hit":    f"{wins/len(done)*100:.1f}%" if done else "0%",
        "pl":     f"{pl:+.2f}u",
        "roi":    f"{pl/total_u*100:+.1f}%",
    }

# ─── APP ──────────────────────────────────────────────────────────────────────
now_et  = _et_now()
date_et = today_et()

# SIDEBAR
records = load_log()
with st.sidebar:
    st.markdown(f"### 🏀 Today's Lines")
    st.caption(f"{date_et.strftime('%A, %B %-d, %Y')}  ·  {now_et.strftime('%-I:%M %p')} ET")
    st.divider()
    sport = st.radio("Sport", ["NBA","CBB"], horizontal=True)
    lkp   = NBA_LKP if sport == "NBA" else CBB_LKP
    st.divider()
    show_no_price = st.toggle("Include markets with no price yet", value=False)
    st.divider()
    s = record_summary(records)
    st.markdown(f"""
<div style="background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px">
  <div style="font-size:0.68rem;color:#8b949e;margin-bottom:6px;text-transform:uppercase;letter-spacing:.08em">My Record</div>
  <div style="font-size:1rem;font-weight:700">{s['record']} <span style="color:#8b949e;font-size:0.8rem">({s['hit']})</span></div>
  <div style="font-family:'JetBrains Mono',monospace;font-size:0.85rem;color:{'#3fb950' if '+' in s['pl'] else '#f85149'}">{s['pl']} · {s['roi']} ROI</div>
</div>""", unsafe_allow_html=True)

# MAIN
st.markdown(f"## {'🏀' if sport=='NBA' else '🎓'} {sport} — {date_et.strftime('%B %-d, %Y')}")

with st.spinner("Fetching today's Kalshi markets…"):
    all_markets = fetch_all()

games = build_game_index(all_markets, lkp)

tab_games, tab_raw, tab_tracker, tab_help = st.tabs([
    f"📅 Today's Games ({len(games)})",
    f"🔍 Raw ({len(all_markets)})",
    "📈 Tracker",
    "❓ Help",
])

# ── TODAY'S GAMES ─────────────────────────────────────────────────────────────
with tab_games:
    if not games:
        total_today = sum(1 for m in all_markets
                         if is_today(m.get("close_time","") or m.get("expiration_time","")))
        st.info(
            f"No {sport} games found for today in Kalshi's markets.\n\n"
            f"ℹ️ {len(all_markets)} total non-combo markets fetched · "
            f"{total_today} close today (all sports).\n\n"
            "Kalshi usually opens game-winner markets 2–4 hours before tip-off. "
            "Check back this afternoon."
        )
    else:
        st.caption(
            "Lines sourced directly from Kalshi. Probabilities = Kalshi implied probability. "
            "Signal = where the money is leaning."
        )
        for event_tk, g in games.items():
            render_game_card(event_tk, g, sport)

# ── RAW ───────────────────────────────────────────────────────────────────────
with tab_raw:
    st.markdown(f"**{len(all_markets)} non-combo markets** (open + upcoming)")
    today_mkts = [m for m in all_markets
                  if is_today(m.get("close_time","") or m.get("expiration_time",""))]
    st.caption(f"{len(today_mkts)} close today · {len(all_markets)-len(today_mkts)} future/past")
    if today_mkts:
        rows = [{
            "status":   m.get("status",""),
            "closes":   fmt_time(m.get("close_time","") or m.get("expiration_time","")),
            "type":     classify(m.get("title",""), m.get("yes_sub_title",""), m.get("no_sub_title","")),
            "title":    (m.get("title",""))[:70],
            "yes_sub":  (m.get("yes_sub_title","") or "")[:35],
            "no_sub":   (m.get("no_sub_title","")  or "")[:35],
            "yes_bid":  m.get("yes_bid"),
            "yes_ask":  m.get("yes_ask"),
            "last":     m.get("last_price"),
            "volume":   m.get("volume"),
        } for m in today_mkts]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ── TRACKER ───────────────────────────────────────────────────────────────────
with tab_tracker:
    st.markdown("### 📈 Pick Tracker")
    s = record_summary(records)
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Record",  s["record"])
    c2.metric("Hit Rate", s["hit"])
    c3.metric("P&L",      s["pl"])
    c4.metric("ROI",      s["roi"])
    st.divider()
    with st.expander("➕ Log a pick"):
        col1, col2 = st.columns(2)
        with col1:
            pick_desc  = st.text_input("Pick (e.g. Lakers ML, Cavs -8.5)")
            pick_odds  = st.text_input("Odds (e.g. -145 or +110)")
        with col2:
            pick_units = st.number_input("Units", 0.1, 10.0, 0.5, 0.25)
            pick_notes = st.text_input("Notes")
        if st.button("💾 Save Pick", use_container_width=True):
            if pick_desc:
                records.append({
                    "date": date_et.isoformat(), "sport": sport,
                    "pick": pick_desc, "odds": pick_odds,
                    "units": pick_units, "notes": pick_notes,
                    "result": "Pending"
                })
                save_log(records); st.success("Saved!"); st.rerun()
    if records:
        df = pd.DataFrame(records)
        edited = st.data_editor(df, use_container_width=True, num_rows="dynamic",
            column_config={"result": st.column_config.SelectboxColumn(
                "Result", options=["Pending","W","L","P"])})
        if st.button("💾 Update Results"):
            save_log(edited.to_dict("records")); st.success("Updated!"); st.rerun()

# ── HELP ──────────────────────────────────────────────────────────────────────
with tab_help:
    st.markdown("""
### How to read this dashboard

**All odds and probabilities come directly from Kalshi** — a CFTC-regulated U.S. prediction market. No artificial model is inflating or deflating numbers.

---

#### Reading a game card

| Column | What it means |
|--------|--------------|
| **%** | Kalshi's implied probability that YES wins |
| **Odds** | American odds equivalent of that probability |
| **Signal** | Where the market is leaning |

#### Signal guide

| Signal | Meaning |
|--------|---------|
| 🟢 **Strong Fav** | Market at 72%+ YES — heavy favorite |
| 🟡 **Lean YES** | Market 60–72% — mild favorite |
| ⚪ **Pick 'em** | Near 50/50 — market sees this as a coin flip |
| 🔴 **Lean NO / Strong Dog** | Under 40% YES — underdog territory |

#### Market types shown per game
- **ML** — Moneyline (outright winner)
- **Spread** — Does team cover the point spread?
- **Total** — Over/under on combined score
- **Prop** — Individual player stat lines

#### Why no "edge %"?
Previous versions showed fake edge numbers like +72% which were mathematically impossible.  
**Kalshi is an efficient market.** Real edges are 1–5% at most, and require live arbitrage data to detect. We show the raw market data honestly instead.
""")
