# coding: utf-8
"""
Sports Betting Dashboard — v5.0
KEY FIX: No series_ticker filter. The Kalshi API does NOT use simple series
tickers like KXNBAGAME for game-level markets. Instead we fetch ALL open markets
(no filter), then identify NBA/CBB games by scanning titles for team names.
This matches exactly what the Raw Markets diagnostic showed: tickers like
KXMVECROSSCATEGORY-S2026... with titles "yes Los Angeles C, no Charlotte wins..."
"""

import streamlit as st
import requests
import pandas as pd
import math, json, os, re
from datetime import datetime, timedelta, timezone

# ─── PAGE CONFIG ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Betting Dashboard", page_icon="🏆",
                   layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;700;800&family=DM+Mono:wght@400;500&display=swap');
html,body,[class*="css"]{font-family:'Syne',sans-serif;}
.stApp{background:#0a0e1a;color:#e8eaf0;}
section[data-testid="stSidebar"]{background:#0f1525!important;border-right:1px solid #1e2640;}
section[data-testid="stSidebar"] *{color:#c8ccd8!important;}
[data-testid="metric-container"]{background:#131929;border:1px solid #1e2a45;border-radius:12px;padding:16px;}
[data-testid="stMetricValue"]{font-family:'DM Mono',monospace!important;font-size:1.6rem!important;color:#7eeaff!important;}
[data-testid="stMetricLabel"]{color:#8892a4!important;font-size:0.75rem!important;}
.card{background:#131929;border:1px solid #1e2a45;border-radius:14px;padding:20px 24px;margin-bottom:16px;}
.strong-pick{border-left:4px solid #4ade80;}
.lean-pick{border-left:4px solid #facc15;}
.badge-strong{display:inline-block;background:#1a3a2a;color:#4ade80;padding:3px 10px;border-radius:20px;font-size:0.78rem;font-weight:700;}
.badge-lean{display:inline-block;background:#2a2a1a;color:#facc15;padding:3px 10px;border-radius:20px;font-size:0.78rem;font-weight:700;}
.badge-watch{display:inline-block;background:#1e2640;color:#94a3b8;padding:3px 10px;border-radius:20px;font-size:0.78rem;font-weight:700;}
.prob-bar-bg{background:#1e2640;border-radius:8px;height:10px;width:100%;margin:4px 0 12px 0;}
.prob-bar-fill{background:linear-gradient(90deg,#1a6fff,#7eeaff);border-radius:8px;height:10px;}
hr{border-color:#1e2640!important;}
#MainMenu,footer{visibility:hidden;}
</style>
""", unsafe_allow_html=True)

# ─── CONSTANTS ────────────────────────────────────────────────────────────────
KALSHI_BASE  = "https://api.elections.kalshi.com/trade-api/v2"
TRACKER_FILE = "picks_log.json"

# NBA team name variants (nickname + city) for title matching
NBA_TEAM_NAMES = {
    # nickname: city/alt forms
    "Cavaliers":     ["cavaliers","cleveland","cle"],
    "Thunder":       ["thunder","oklahoma city","okc"],
    "Celtics":       ["celtics","boston","bos"],
    "Warriors":      ["warriors","golden state","gsw"],
    "Rockets":       ["rockets","houston","hou"],
    "Pacers":        ["pacers","indiana","ind"],
    "Grizzlies":     ["grizzlies","memphis","mem"],
    "Nuggets":       ["nuggets","denver","den"],
    "Lakers":        ["lakers","los angeles l","lal"],
    "Knicks":        ["knicks","new york","nyk"],
    "Bucks":         ["bucks","milwaukee","mil"],
    "76ers":         ["76ers","sixers","philadelphia","phi"],
    "Timberwolves":  ["timberwolves","minnesota","min"],
    "Heat":          ["heat","miami","mia"],
    "Kings":         ["kings","sacramento","sac"],
    "Clippers":      ["clippers","los angeles c","lac"],
    "Mavericks":     ["mavericks","dallas","dal"],
    "Hawks":         ["hawks","atlanta","atl"],
    "Suns":          ["suns","phoenix","phx","pho"],
    "Bulls":         ["bulls","chicago","chi"],
    "Nets":          ["nets","brooklyn","bkn"],
    "Magic":         ["magic","orlando","orl"],
    "Hornets":       ["hornets","charlotte","cha"],
    "Raptors":       ["raptors","toronto","tor"],
    "Jazz":          ["jazz","utah","uta"],
    "Spurs":         ["spurs","san antonio","sas"],
    "Trail Blazers": ["trail blazers","blazers","portland","por"],
    "Pistons":       ["pistons","detroit","det"],
    "Pelicans":      ["pelicans","new orleans","nop"],
    "Wizards":       ["wizards","washington","was"],
}

# Build flat lookup: any variant → canonical nickname
NBA_LOOKUP = {}
for nickname, variants in NBA_TEAM_NAMES.items():
    NBA_LOOKUP[nickname.lower()] = nickname
    for v in variants:
        NBA_LOOKUP[v.lower()] = nickname

# CBB team name variants
CBB_TEAM_NAMES = {
    "Duke":["duke"], "Auburn":["auburn"], "Tennessee":["tennessee"],
    "Alabama":["alabama"], "Houston":["houston"], "Florida":["florida","gators"],
    "Kentucky":["kentucky"], "Iowa State":["iowa st","iowa state"],
    "Michigan State":["michigan st","michigan state"],
    "Texas Tech":["texas tech"], "Wisconsin":["wisconsin"],
    "Purdue":["purdue"], "Arizona":["arizona","wildcats"],
    "Maryland":["maryland"], "Michigan":["michigan"],
    "Gonzaga":["gonzaga"], "Illinois":["illinois"],
    "Kansas":["kansas"], "UConn":["uconn","connecticut"],
    "Marquette":["marquette"], "Creighton":["creighton"],
    "UCLA":["ucla"], "Baylor":["baylor"], "Arkansas":["arkansas"],
    "St John's":["st john","st. john","saint john"],
    "Xavier":["xavier"], "Ole Miss":["ole miss"],
    "Texas":["texas longhorns","ut austin"],
    "BYU":["byu","brigham young"], "Clemson":["clemson"],
    "Notre Dame":["notre dame"], "Georgetown":["georgetown"],
    "Villanova":["villanova"], "Butler":["butler"],
    "Cincinnati":["cincinnati"], "Memphis":["memphis tigers"],
    "North Carolina":["north carolina","unc","tar heels"],
    "NC State":["nc state","n.c. state"],
}
CBB_LOOKUP = {}
for nickname, variants in CBB_TEAM_NAMES.items():
    CBB_LOOKUP[nickname.lower()] = nickname
    for v in variants:
        CBB_LOOKUP[v.lower()] = nickname

# ─── TIME HELPERS ─────────────────────────────────────────────────────────────
def _is_edt():
    dt = datetime.utcnow()
    y  = dt.year
    mar = datetime(y,3,8)
    while mar.weekday()!=6: mar+=timedelta(days=1)
    nov = datetime(y,11,1)
    while nov.weekday()!=6: nov+=timedelta(days=1)
    return mar<=dt<nov

def now_eastern():
    off = timedelta(hours=-4 if _is_edt() else -5)
    est = datetime.utcnow()+off
    return est.strftime(f"{str(int(est.strftime('%I')))}:%M %p ")+("EDT" if _is_edt() else "EST")

def today_eastern():
    off = timedelta(hours=-4 if _is_edt() else -5)
    return (datetime.utcnow()+off).date()

def pct(x): return f"{x*100:.0f}%" if x is not None else "—"

def to_american(p):
    if p is None or p<=0.01 or p>=0.99: return "—"
    return f"-{round((p/(1-p))*100)}" if p>=0.5 else f"+{round(((1-p)/p)*100)}"

# ─── KALSHI FETCH — NO SERIES FILTER ─────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_open_markets() -> list:
    """
    Fetch ALL open markets from Kalshi with NO series_ticker filter.
    Pages through up to 5000 markets. We then filter by title client-side.
    This is the correct approach — series_ticker filtering was returning 0.
    """
    markets = []
    cursor  = None
    for _ in range(25):  # 25 pages × 200 = 5000 max
        params = {"status": "open", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            r = requests.get(f"{KALSHI_BASE}/markets", params=params, timeout=20)
        except Exception as e:
            break
        if r.status_code != 200:
            break
        data   = r.json()
        chunk  = data.get("markets", [])
        markets.extend(chunk)
        cursor = data.get("cursor")
        if not cursor or not chunk:
            break
    return markets

# ─── IDENTIFY NBA/CBB GAME MARKETS ────────────────────────────────────────────
def find_team_in_text(text: str, lookup: dict):
    """Return canonical team name if any variant found in text, else None."""
    tl = text.lower()
    # Try longest matches first to avoid partial matches
    for variant in sorted(lookup.keys(), key=len, reverse=True):
        if re.search(r'\b' + re.escape(variant) + r'\b', tl):
            return lookup[variant]
    return None

def is_simple_game_winner(m: dict) -> bool:
    """
    Return True if this market is a simple 'Will X win?' moneyline market
    (not a parlay, combo, spread, total, or prop).
    """
    title = (m.get("title") or "").lower()
    # Exclude obvious non-moneylines
    exclusions = [
        "points", "rebounds", "assists", "spread", "over", "under",
        "total", "quarter", "half", "first", "last", "combo", "parlay",
        "both", "either", "any", "all", ",yes", ",no", "yes,", "no,",
        "+", "3pm", "steals", "blocks", "threes", "props",
    ]
    for excl in exclusions:
        if excl in title:
            return False
    return True

def extract_teams_from_market(m: dict, lookup: dict):
    """
    Extract two team names from a Kalshi market dict.
    Tries sub_titles first, then title parsing.
    """
    yes_sub = (m.get("yes_sub_title") or "").strip()
    no_sub  = (m.get("no_sub_title")  or "").strip()

    if yes_sub and no_sub:
        ta = find_team_in_text(yes_sub, lookup)
        tb = find_team_in_text(no_sub, lookup)
        if ta and tb and ta != tb:
            return ta, tb

    title = m.get("title") or ""
    # Pattern: "Will X win?" or "X vs Y" or "X at Y"
    for pat in [
        r"will\s+(.+?)\s+win",
        r"(.+?)\s+(?:vs?\.?|at|@)\s+(.+?)(?:\?|$|\s*[-–(])",
    ]:
        hit = re.search(pat, title, re.IGNORECASE)
        if hit:
            part_a = hit.group(1).strip()
            ta = find_team_in_text(part_a, lookup)
            if ta:
                # For "Will X win?" we need the opponent from subtitle
                if "win" in pat and no_sub:
                    tb = find_team_in_text(no_sub, lookup)
                    if tb and tb != ta:
                        return ta, tb
                elif len(hit.groups()) >= 2:
                    part_b = hit.group(2).strip().split("(")[0].strip()
                    tb = find_team_in_text(part_b, lookup)
                    if tb and tb != ta:
                        return ta, tb

    # Last resort: find any two distinct teams mentioned in title+subtitles
    combined = f"{title} {yes_sub} {no_sub}"
    found = []
    for variant in sorted(lookup.keys(), key=len, reverse=True):
        if re.search(r'\b' + re.escape(variant) + r'\b', combined.lower()):
            canon = lookup[variant]
            if canon not in found:
                found.append(canon)
        if len(found) == 2:
            break
    if len(found) == 2:
        return found[0], found[1]

    return None, None

def filter_sport_markets(all_markets: list, sport: str):
    """Filter all open markets down to single-game moneylines for a sport."""
    lookup = NBA_LOOKUP if sport == "NBA" else CBB_LOOKUP
    results = []
    for m in all_markets:
        title = (m.get("title") or "").lower()
        combined = f"{title} {(m.get('yes_sub_title') or '').lower()} {(m.get('no_sub_title') or '').lower()}"

        # Must contain at least one team name
        has_team = any(
            re.search(r'\b' + re.escape(v) + r'\b', combined)
            for v in lookup.keys()
        )
        if not has_team:
            continue

        # Must look like a game winner (not prop/parlay/spread/total)
        if not is_simple_game_winner(m):
            continue

        ta, tb = extract_teams_from_market(m, lookup)
        if not ta or not tb:
            continue

        results.append((m, ta, tb))
    return results

# ─── PRICE EXTRACTION ─────────────────────────────────────────────────────────
def kalshi_prob(m: dict):
    try:
        yb,ya,lp = m.get("yes_bid"), m.get("yes_ask"), m.get("last_price")
        if yb and ya and yb>0 and ya>0: return (yb+ya)/200.0
        if lp and lp>0: return lp/100.0
    except: pass
    return None

# ─── MODEL ────────────────────────────────────────────────────────────────────
NBA_RATINGS = {
    "Cavaliers":14.2, "Thunder":12.1, "Celtics":10.8, "Warriors":9.3,
    "Rockets":8.1, "Pacers":7.4, "Grizzlies":6.2, "Nuggets":5.8,
    "Lakers":5.1, "Knicks":4.9, "Bucks":4.2, "76ers":3.7,
    "Timberwolves":3.1, "Heat":2.8, "Kings":2.1, "Clippers":1.4,
    "Mavericks":0.8, "Hawks":-0.5, "Suns":-1.2, "Bulls":-1.8,
    "Nets":-2.4, "Magic":-3.1, "Hornets":-3.8, "Raptors":-4.2,
    "Jazz":-5.1, "Spurs":-5.8, "Trail Blazers":-6.4,
    "Pistons":-7.1, "Pelicans":-7.8, "Wizards":-9.2,
}
CBB_RATINGS = {
    "Duke":28.4, "Auburn":26.1, "Houston":25.8, "Florida":24.9,
    "Alabama":23.7, "Tennessee":22.8, "Iowa State":22.1, "Michigan State":21.4,
    "Texas Tech":20.8, "St John's":20.2, "Wisconsin":19.7, "Kentucky":19.1,
    "Memphis":18.6, "Purdue":18.1, "Arizona":17.9, "Ole Miss":17.4,
    "Maryland":17.1, "Michigan":16.8, "Gonzaga":16.4, "Illinois":16.1,
    "Xavier":15.8, "Kansas":15.4, "UConn":15.1, "North Carolina":14.7,
    "Texas":14.2, "Arkansas":13.8, "Marquette":13.0, "BYU":13.1,
    "Creighton":12.8, "UCLA":12.4, "Clemson":12.1, "Baylor":10.4,
    "Notre Dame":9.8, "Georgetown":9.0, "Butler":10.0, "NC State":11.0,
    "Villanova":11.5, "Cincinnati":9.5,
}

def _sig(x):
    try: return 1/(1+math.exp(-x))
    except: return 0.5

def get_rating(team: str, sport: str) -> float:
    ratings = NBA_RATINGS if sport=="NBA" else CBB_RATINGS
    if team in ratings: return ratings[team]
    tl = team.lower()
    for k,v in ratings.items():
        if k.lower() in tl or tl in k.lower(): return v
    return 0.0

def model_win_prob(team_a: str, team_b: str, sport: str) -> float:
    """team_a is YES side. Returns P(team_a wins). Home court +2.5 for team_a."""
    ra = get_rating(team_a, sport)
    rb = get_rating(team_b, sport)
    return _sig((ra + 2.5 - rb) / 6.0)

# ─── PICK LOGIC ───────────────────────────────────────────────────────────────
def make_picks(game_markets: list, sport: str, min_edge: float) -> list:
    picks = []
    for m, team_a, team_b in game_markets:
        kp = kalshi_prob(m)
        if kp is None:
            continue
        mp_a = model_win_prob(team_a, team_b, sport)
        mp_b = 1.0 - mp_a
        kp_a, kp_b = kp, 1.0-kp
        edge_a = mp_a - kp_a
        edge_b = mp_b - kp_b
        if edge_a >= edge_b:
            pick, opp, mp, kp_s, edge = team_a, team_b, mp_a, kp_a, edge_a
        else:
            pick, opp, mp, kp_s, edge = team_b, team_a, mp_b, kp_b, edge_b
        if edge < min_edge:
            continue
        if   edge>=0.10 and mp>=0.65: tier="STRONG"
        elif edge>=0.05 and mp>=0.58: tier="LEAN"
        else:                          tier="WATCH"
        picks.append({"pick":pick,"opp":opp,"model":mp,"kalshi":kp_s,
                      "edge":edge,"tier":tier,"american":to_american(kp_s),
                      "title":m.get("title",""),"ticker":m.get("ticker","")})
    picks.sort(key=lambda x: x["edge"], reverse=True)
    return picks

# ─── TRACKER ──────────────────────────────────────────────────────────────────
def load_picks():
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE) as f: return json.load(f)
        except: return []
    return []

def save_picks(p):
    try:
        with open(TRACKER_FILE,"w") as f: json.dump(p,f,indent=2)
    except: pass

def a2d(ml):
    try:
        ml=float(ml)
        return ml/100+1 if ml>0 else 100/abs(ml)+1
    except: return 1.91

def summary(picks, sport=None):
    f=[p for p in picks if not sport or p.get("sport","").upper()==sport.upper()]
    s=[p for p in f if p.get("result") in ("W","L","P")]
    wins=len([p for p in s if p["result"]=="W"])
    pl=sum((a2d(p.get("odds"))-1)*float(p.get("units",1)) if p["result"]=="W"
           else (-float(p.get("units",1)) if p["result"]=="L" else 0) for p in s)
    wgr=sum(float(p.get("units",1)) for p in s)
    return {"wins":wins,"losses":len([p for p in s if p["result"]=="L"]),
            "hit":round(wins/len(s)*100,1) if s else 0.0,
            "pl":round(pl,2),"roi":round(pl/wgr*100,1) if wgr else 0.0}

# ─── RENDER CARD ──────────────────────────────────────────────────────────────
def tier_badge(tier):
    if tier=="STRONG": return '<span class="badge-strong">🔥 Strong Pick</span>'
    if tier=="LEAN":   return '<span class="badge-lean">🎯 Lean</span>'
    return '<span class="badge-watch">👀 Watch</span>'

def render_card(p, sport):
    tier = p["tier"]
    css  = "strong-pick" if tier=="STRONG" else ("lean-pick" if tier=="LEAN" else "")
    bw   = int(p["model"]*100)

    # Plain-English explanation
    diff = abs(p["model"]-p["kalshi"])*100
    conf = "strongly" if p["model"]>=0.70 else ("moderately" if p["model"]>=0.60 else "slightly")
    expl = (f"Our model {conf} favors <b>{p['pick']}</b> ({pct(p['model'])} win chance) "
            f"vs. Kalshi's market ({pct(p['kalshi'])}). "
            f"That's a <b>{diff:.0f}pt edge</b> — the crowd may be underrating {p['pick']}. "
            f"Based on {'net rating (points scored/allowed per 100 possessions)' if sport=='NBA' else 'adjusted efficiency margin (KenPom-style power rating)'}.")

    st.markdown(f"""
<div class="card {css}">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
    <div>
      <span style="font-size:1.2rem;font-weight:800">{p['pick']}</span>
      <span style="color:#5a6478;font-size:0.9rem"> vs {p['opp']}</span>
    </div>
    {tier_badge(tier)}
  </div>
  <div style="display:flex;gap:32px;margin:12px 0 10px 0">
    <div>
      <div style="color:#8892a4;font-size:0.70rem;margin-bottom:2px">MODEL WIN %</div>
      <div style="font-family:'DM Mono',monospace;font-size:1.3rem;color:#7eeaff;font-weight:700">{pct(p['model'])}</div>
    </div>
    <div>
      <div style="color:#8892a4;font-size:0.70rem;margin-bottom:2px">KALSHI LINE</div>
      <div style="font-family:'DM Mono',monospace;font-size:1.3rem;color:#e8eaf0;font-weight:700">{pct(p['kalshi'])}</div>
    </div>
    <div>
      <div style="color:#8892a4;font-size:0.70rem;margin-bottom:2px">EDGE</div>
      <div style="font-family:'DM Mono',monospace;font-size:1.3rem;color:#4ade80;font-weight:700">+{pct(p['edge'])}</div>
    </div>
    <div>
      <div style="color:#8892a4;font-size:0.70rem;margin-bottom:2px">AMERICAN ODDS</div>
      <div style="font-family:'DM Mono',monospace;font-size:1.3rem;color:#facc15;font-weight:700">{p['american']}</div>
    </div>
  </div>
  <div class="prob-bar-bg"><div class="prob-bar-fill" style="width:{bw}%"></div></div>
  <div style="color:#8892a4;font-size:0.80rem;line-height:1.5">{expl}</div>
</div>
""", unsafe_allow_html=True)

# ─── SESSION STATE ────────────────────────────────────────────────────────────
st.session_state.setdefault("sport",    "NBA")
st.session_state.setdefault("min_edge", 3)

# ─── SIDEBAR ──────────────────────────────────────────────────────────────────
all_picks = load_picks()
with st.sidebar:
    st.markdown("### 🏆 Betting Dashboard")
    st.caption(f"{today_eastern().strftime('%A, %B %d')} · {now_eastern()}")
    st.divider()
    sport = st.radio("Sport", ["NBA","CBB"], index=0, label_visibility="collapsed")
    st.session_state["sport"] = sport
    st.divider()
    st.session_state["min_edge"] = st.slider(
        "Min Edge %", 0, 20, st.session_state["min_edge"], 1,
        help="Edge = Model Win% − Kalshi Market%")
    st.caption("Markets refresh every 5 min.")
    st.divider()
    summ = summary(all_picks)
    pl_col = "#4ade80" if summ["pl"]>=0 else "#f87171"
    st.markdown(f"""
<div style="background:#131929;border:1px solid #1e2a45;border-radius:10px;padding:12px 16px">
  <div style="font-size:0.70rem;color:#8892a4;margin-bottom:6px">📈 ALL-TIME RECORD</div>
  <div style="font-size:1.1rem;font-weight:800">{summ['wins']}-{summ['losses']} <span style="font-size:0.80rem;color:#8892a4">({summ['hit']}%)</span></div>
  <div style="font-family:'DM Mono',monospace;color:{pl_col};font-size:0.90rem;margin-top:4px">{'+' if summ['pl']>=0 else ''}{summ['pl']}u · ROI {summ['roi']}%</div>
</div>""", unsafe_allow_html=True)

# ─── MAIN ─────────────────────────────────────────────────────────────────────
sport    = st.session_state["sport"]
min_edge = st.session_state["min_edge"] / 100.0

st.markdown(f"### {'🏀' if sport in ('NBA','CBB') else '🏆'} {sport} · {today_eastern().strftime('%b %d, %Y')}")

with st.spinner("Loading markets from Kalshi…"):
    all_markets = fetch_all_open_markets()

if not all_markets:
    st.error("Could not reach Kalshi API. Check your internet connection.")
    st.stop()

# Filter to sport-specific game markets
game_markets = filter_sport_markets(all_markets, sport)
picks        = make_picks(game_markets, sport, min_edge)

tabs = st.tabs([
    f"🗓️ Picks ({len(picks)})",
    f"📋 All {sport} Games ({len(game_markets)})",
    f"🔍 Raw Markets ({len(all_markets)})",
    "📈 Tracker",
    "❓ How it works",
])

# ── PICKS ─────────────────────────────────────────────────────────────────────
with tabs[0]:
    strong = [p for p in picks if p["tier"]=="STRONG"]
    lean   = [p for p in picks if p["tier"]=="LEAN"]
    watch  = [p for p in picks if p["tier"]=="WATCH"]

    if not picks:
        st.info(
            f"No {sport} game picks meet the **{st.session_state['min_edge']}% edge** threshold today.\n\n"
            "Try lowering Min Edge % in the sidebar, or check the **All Games** tab to see "
            "what Kalshi has priced (markets open closer to game time, typically afternoon ET)."
        )
    else:
        with st.expander("📖 What do these numbers mean?", expanded=False):
            st.markdown("""
**Model Win %** — Our model's estimate based on season stats (net rating for NBA, efficiency margin for CBB).

**Kalshi Line** — What the crowd is trading on Kalshi (implied probability of winning).

**Edge** — The gap between our model and the crowd. Positive = we think the team is more likely to win than the market believes.

**American Odds** — Kalshi line converted to standard sportsbook format. `-150` means bet $150 to win $100. `+130` means bet $100 to win $130.

**Tiers:** 🔥 Strong = Edge ≥10% + Model ≥65%. 🎯 Lean = Edge ≥5% + Model ≥58%. 👀 Watch = smaller edge.
""")
        if strong:
            st.markdown("#### 🔥 Strong Picks")
            for p in strong: render_card(p, sport)
        if lean:
            st.markdown("#### 🎯 Leans")
            for p in lean: render_card(p, sport)
        if watch:
            st.markdown("#### 👀 Watch List")
            for p in watch[:5]: render_card(p, sport)

# ── ALL GAMES ─────────────────────────────────────────────────────────────────
with tabs[1]:
    st.markdown(f"#### {sport} game markets found in Kalshi")
    if not game_markets:
        st.warning(
            f"No {sport} game markets found in {len(all_markets)} total open markets. "
            "This usually means tonight's games haven't been listed yet on Kalshi "
            "(they typically open mid-afternoon ET). Check back later or see Raw Markets."
        )
    else:
        rows=[]
        for m,ta,tb in game_markets:
            kp=kalshi_prob(m)
            mp_a=model_win_prob(ta,tb,sport)
            rows.append({
                "Match":    f"{ta} vs {tb}",
                "Kalshi %": pct(kp) if kp else "—",
                "Model %":  pct(mp_a),
                "Edge":     pct(mp_a-(kp or 0.5)),
                "American": to_american(kp) if kp else "—",
                "Ticker":   m.get("ticker","")[:40],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ── RAW MARKETS ───────────────────────────────────────────────────────────────
with tabs[2]:
    st.markdown(f"#### All {len(all_markets)} open markets from Kalshi (unfiltered)")
    st.caption("Use this to verify what the API is actually returning.")
    if all_markets:
        raw_rows=[{
            "ticker": m.get("ticker","")[:50],
            "title":  (m.get("title",""))[:80],
            "yes_sub":(m.get("yes_sub_title","") or "")[:40],
            "no_sub": (m.get("no_sub_title","") or "")[:40],
            "yes_bid":m.get("yes_bid"),
            "yes_ask":m.get("yes_ask"),
        } for m in all_markets[:200]]
        st.dataframe(pd.DataFrame(raw_rows), use_container_width=True, hide_index=True)

# ── TRACKER ───────────────────────────────────────────────────────────────────
with tabs[3]:
    st.markdown("#### 📈 Pick Tracker")
    sport_summ=summary(all_picks, sport)
    c1,c2,c3,c4=st.columns(4)
    c1.metric("Record",  f"{sport_summ['wins']}-{sport_summ['losses']}")
    c2.metric("Hit Rate",f"{sport_summ['hit']}%")
    c3.metric("P&L",     f"{'+' if sport_summ['pl']>=0 else ''}{sport_summ['pl']}u")
    c4.metric("ROI",     f"{'+' if sport_summ['roi']>=0 else ''}{sport_summ['roi']}%")
    st.divider()
    with st.expander("➕ Log a pick"):
        b_team =st.text_input("Team you're picking")
        b_opp  =st.text_input("Opponent")
        b_odds =st.text_input("Odds (e.g. -150 or +120)","")
        b_units=st.number_input("Units",0.1,10.0,0.5,0.25)
        b_notes=st.text_input("Notes (optional)","")
        if st.button("Save Pick"):
            if b_team:
                all_picks.append({"date":today_eastern().isoformat(),"sport":sport,
                    "team":b_team,"opp":b_opp,"odds":b_odds,"units":b_units,
                    "notes":b_notes,"result":"Pending"})
                save_picks(all_picks); st.success("Saved!"); st.rerun()
            else: st.error("Enter the team name.")
    if all_picks:
        dfp=pd.DataFrame(all_picks)
        ed=st.data_editor(dfp, use_container_width=True, num_rows="dynamic",
            column_config={"result":st.column_config.SelectboxColumn(
                "Result",options=["Pending","W","L","P"])})
        if st.button("Save Results"):
            save_picks(ed.to_dict("records")); st.success("Updated."); st.rerun()

# ── HOW IT WORKS ──────────────────────────────────────────────────────────────
with tabs[4]:
    st.markdown("""
### ❓ How This App Works

**Where do the odds come from?**
All lines come directly from **Kalshi** — a regulated U.S. prediction market where real money trades on game outcomes. The prices reflect the crowd's true probability estimate.

**What is the model?**
- **NBA**: Net rating (points scored minus allowed per 100 possessions) + 2.5pt home-court adjustment
- **CBB**: Adjusted efficiency margin (KenPom-style)

**What is Edge?** Model Win% minus Kalshi Market%. Positive = we think the team is underpriced.

| Tier | Edge | Model | Meaning |
|------|------|-------|---------|
| 🔥 Strong | ≥10% | ≥65% | High conviction |
| 🎯 Lean | ≥5% | ≥58% | Moderate conviction |
| 👀 Watch | <5% | Any | Monitor only |

*This app is for educational/entertainment purposes. Bet responsibly.*
""")
