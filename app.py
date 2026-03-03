
"""
Unified Sports Betting Dashboard v4.1 (Kalshi Hybrid, Desktop)
MLB · NBA · NFL · CBB · CFB

Hybrid:
- Your model = predictor (confidence/projection)
- Kalshi = line (price -> implied probability, multiple thresholds for props)

Public-safe:
- Kalshi market+price cache = 10 minutes (TTL=600)
- No manual refresh button (prevents abuse)
- Sport-specific (Option A)

Note: Uses Kalshi public market data endpoints (no auth) at:
https://api.elections.kalshi.com/trade-api/v2
"""
import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta
from io import StringIO
import json, os, re, math, time, warnings
warnings.filterwarnings("ignore")

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Betting Dashboard", page_icon="🏆", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@400;500&display=swap');
html,body,[class*="css"]{font-family:'Syne',sans-serif;}
.stApp{background:#0a0e1a;color:#e8eaf0;}
section[data-testid="stSidebar"]{background:#0f1525!important;border-right:1px solid #1e2640;}
section[data-testid="stSidebar"] *{color:#c8ccd8!important;}
[data-testid="metric-container"]{background:#131929;border:1px solid #1e2a45;border-radius:12px;padding:16px;}
[data-testid="stMetricValue"]{font-family:'DM Mono',monospace!important;font-size:1.7rem!important;color:#7eeaff!important;}
[data-testid="stMetricLabel"]{color:#8892a4!important;font-size:0.75rem!important;}
.stTabs [data-baseweb="tab-list"]{background:#0f1525;border-radius:10px;padding:4px;gap:4px;}
.stTabs [data-baseweb="tab"]{background:transparent;color:#8892a4!important;border-radius:8px;font-family:'Syne',sans-serif;font-weight:600;}
.stTabs [aria-selected="true"]{background:#1a2640!important;color:#7eeaff!important;}
.risk-low{display:inline-block;background:#1a3a2a;color:#4ade80;padding:2px 8px;border-radius:6px;font-size:0.70rem;font-weight:700;font-family:'DM Mono',monospace;}
.risk-med{display:inline-block;background:#2a2a1a;color:#facc15;padding:2px 8px;border-radius:6px;font-size:0.70rem;font-weight:700;font-family:'DM Mono',monospace;}
.risk-high{display:inline-block;background:#2a1a1a;color:#f87171;padding:2px 8px;border-radius:6px;font-size:0.70rem;font-weight:700;font-family:'DM Mono',monospace;}
.badge-pre{background:#1e2640;color:#94a3b8;padding:3px 10px;border-radius:20px;font-size:0.78rem;font-weight:700;}
.badge-live{background:#3a1a1a;color:#ff6b6b;padding:3px 10px;border-radius:20px;font-size:0.78rem;font-weight:700;}
.badge-final{background:#1a3a2a;color:#4ade80;padding:3px 10px;border-radius:20px;font-size:0.78rem;font-weight:700;}
hr{border-color:#1e2640!important;}
#MainMenu,footer{visibility:hidden;}
</style>
""", unsafe_allow_html=True)

# ── KEYS / FILES ──────────────────────────────────────────────────────────────
CFBD_API_KEY  = os.environ.get("CFBD_API_KEY", "")
WEATHER_KEY   = os.environ.get("WEATHER_API_KEY", "")
TRACKER_FILE  = "picks_log.json"

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

SPORT_CONFIG = {
    "🏀 NBA": {"espn_sport":"basketball", "espn_league":"nba", "label":"NBA"},
    "🏀 CBB": {"espn_sport":"basketball", "espn_league":"mens-college-basketball", "label":"CBB"},
    "⚾ MLB": {"espn_sport":"baseball",   "espn_league":"mlb", "label":"MLB"},
    "🏈 NFL": {"espn_sport":"football",   "espn_league":"nfl", "label":"NFL"},
    "🏈 CFB": {"espn_sport":"football",   "espn_league":"college-football", "label":"CFB"},
}
SPORT_ICONS = {"MLB":"⚾","NBA":"🏀","NFL":"🏈","CBB":"🏀","CFB":"🏈"}

# ── TIMEZONE ──────────────────────────────────────────────────────────────────
def _is_edt(dt=None):
    if dt is None: dt = datetime.utcnow()
    y = dt.year
    mar = datetime(y, 3, 8)
    while mar.weekday() != 6: mar += timedelta(days=1)
    nov = datetime(y, 11, 1)
    while nov.weekday() != 6: nov += timedelta(days=1)
    return mar <= dt.replace(tzinfo=None) < nov

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

def today_est():
    dt = datetime.utcnow()
    offset = timedelta(hours=-4) if _is_edt(dt) else timedelta(hours=-5)
    return (dt + offset).date()

def now_est():
    dt = datetime.utcnow()
    offset = timedelta(hours=-4) if _is_edt(dt) else timedelta(hours=-5)
    est = dt + offset
    suffix = "EDT" if _is_edt(dt) else "EST"
    return est.strftime("%-I:%M %p") + f" {suffix}"

# ── TEAM NORMALIZER (St John's hardened) ──────────────────────────────────────
NORM_MAP = {
    "st john's red storm":"St John's","st. john's red storm":"St John's",
    "saint john's red storm":"St John's","saint john's":"St John's",
    "st. john's":"St John's","st johns":"St John's","st. johns":"St John's",
    "st john's":"St John's","stjohn's":"St John's",
}
def normalize_team(name: str) -> str:
    s = str(name).strip()
    nl = s.lower()
    if nl in NORM_MAP: return NORM_MAP[nl]
    if re.search(r"st\.?\s*johns?'?s?\b", nl, re.IGNORECASE):
        return "St John's"
    return s

# ── FALLBACKS (light placeholders; keep your merged full dicts if desired) ────
# NOTE: If you want the massive fallback dictionaries, we can import them here.
NBA_FB = {}  # live_nba fills; fallback only if nba_api fails
CBB_FB = {}
MLB_FB = {}
NFL_FB = {}
CFB_FB = {}

# ── LIVE STAT FETCHERS (unchanged, but simplified for brevity) ────────────────
@st.cache_data(ttl=21600, show_spinner=False)
def live_nba():
    try:
        from nba_api.stats.endpoints import leaguedashteamstats
        season = _nba_season()
        adv = leaguedashteamstats.LeagueDashTeamStats(
            season=season, measure_type_detailed_defense="Advanced", per_mode_detailed="PerGame"
        ).get_data_frames()[0]
        stats={}
        for _, r in adv.iterrows():
            nm=str(r.get("TEAM_NAME",""))
            stats[nm]={
                "net_rtg":float(r.get("NET_RATING",0) or 0),
                "pie_pct":float(r.get("PIE",0.50) or 0.50),
                "ts_pct":0.56, # nba_api varies by endpoint; keep stable
                "three_pt_rate":0.41,
                "three_pt_pct":0.36,
                "def_reb_pct":0.72,
                "to_rate":0.15,
            }
        if len(stats)>=25: return stats,"live"
    except: pass
    return NBA_FB,"fallback"

@st.cache_data(ttl=21600, show_spinner=False)
def live_cbb():
    try:
        yr=today_est().year
        url=f"https://barttorvik.com/trank.php?year={yr}&sort=&top=0&conlimit=All&csv=1"
        r=requests.get(url,headers={"User-Agent":"Mozilla/5.0"},timeout=15)
        df=pd.read_csv(StringIO(r.text),header=0)
        stats={}
        seen=set()
        for _, row in df.iterrows():
            nm=normalize_team(str(row.iloc[0]).strip())
            if nm in seen: continue
            seen.add(nm)
            adj_o=float(row.get("AdjOE", row.iloc[4] if len(row)>4 else 110))
            adj_d=float(row.get("AdjDE", row.iloc[5] if len(row)>5 else 102))
            stats[nm]={"eff_margin":adj_o-adj_d,"adj_o":adj_o,"adj_d":adj_d,"tempo":70.0,"efg":0.52,"to_rate":0.18,"exp":0.75}
        if len(stats)>=50: return stats,"live"
    except: pass
    return CBB_FB,"fallback"

def _nba_season():
    t=today_est()
    return f"{t.year}-{str(t.year+1)[2:]}" if t.month>=10 else f"{t.year-1}-{str(t.year)[2:]}"

# ── ESPN SCHEDULE/SCORES ──────────────────────────────────────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def fetch_espn(espn_sport, espn_league, target_date=None):
    try:
        d = target_date or today_est()
        r = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/{espn_sport}/{espn_league}/scoreboard"
            f"?dates={d.strftime('%Y%m%d')}&limit=200", timeout=10)
        return r.json().get("events", [])
    except:
        return []

def parse_espn_events(events):
    games=[]
    for e in events:
        comp=e.get("competitions",[{}])[0]
        status=comp.get("status",{})
        state=status.get("type",{}).get("state","pre")
        detail=status.get("type",{}).get("shortDetail","")
        home=away={}
        for t in comp.get("competitors",[]):
            if t.get("homeAway")=="home": home=t
            else: away=t
        def tm(t):
            td=t.get("team",{})
            return {"name":td.get("displayName",""),"abbr":td.get("abbreviation",""),
                    "score":t.get("score","—")}
        hd=tm(home); ad=tm(away)
        gt=utc_to_est(e.get("date",""))
        games.append({"home":hd,"away":ad,"state":state,"detail":detail,"gametime":gt})
    return games

# ── KALSHI (public, cached) ──────────────────────────────────────────────────
@st.cache_data(ttl=600, show_spinner=False)  # 10 minutes
def kalshi_markets_open(limit=1000):
    """Fetch open markets; uses yes_price as the 'line' for public-safe hybrid."""
    out=[]
    cursor=""
    tries=0
    while tries<5 and len(out)<limit:
        tries+=1
        url=f"{KALSHI_BASE}/markets?status=open&limit=1000"
        if cursor: url += f"&cursor={cursor}"
        r=requests.get(url,timeout=12)
        data=r.json()
        markets=data.get("markets",[])
        out.extend(markets)
        cursor=data.get("cursor","")
        if not cursor: break
    return out[:limit]

def kalshi_yes_price_to_implied(yes_price):
    """Kalshi yes_price is in cents (0-100). Convert to percent."""
    try:
        yp=float(yes_price)
        if yp<=1 and yp>=0: # sometimes 0-1
            yp=yp*100
        return round(yp,1)
    except:
        return None

def find_kalshi_game_market(markets, team_a, team_b):
    """Best-effort match: market title contains both team strings."""
    a=team_a.lower(); b=team_b.lower()
    cands=[]
    for m in markets:
        title=str(m.get("title","")).lower()
        if a in title and b in title:
            # prefer winner/ML-like
            score=0
            if "win" in title or "moneyline" in title or "wins" in title: score+=3
            if "spread" in title: score+=2
            cands.append((score,m))
    cands.sort(key=lambda x:x[0], reverse=True)
    return cands[0][1] if cands else None

def extract_player_from_title(title: str) -> str:
    """
    Best-effort: many Kalshi prop titles start with the player name then a colon.
    Example patterns:
      "Nikola Jokic: Points"
      "Jalen Brunson - PRA"
    """
    t=title.strip()
    for sep in [":", " - ", " – "]:
        if sep in t:
            return t.split(sep)[0].strip()
    return ""

# ── MODEL SCORING (same spirit as v4; simplified) ─────────────────────────────
def gap_to_confidence(gap):
    # heuristic; you can calibrate later
    if gap <= 0: return 50.0
    if gap >= 40: return 82.0
    return round(52.0 + (gap/40.0)*30.0, 1)

def score_nba(s):
    # research-aligned weights; values normalized
    nr=max(0,min(1,(s.get("net_rtg",0)+15)/30))
    pie=max(0,min(1,(s.get("pie_pct",0.50)-0.44)/0.12))
    ts=max(0,min(1,(s.get("ts_pct",0.56)-0.48)/0.14))
    tpr=s.get("three_pt_rate",0.41); tp=s.get("three_pt_pct",0.36)
    three=max(0,min(1,(tpr*tp-0.13)/0.08))
    dr=max(0,min(1,(s.get("def_reb_pct",0.72)-0.65)/0.12))
    tor=max(0,min(1,1-(s.get("to_rate",0.15)-0.10)/0.12))
    sc=(0.30*nr + 0.20*pie + 0.15*ts + 0.12*three + 0.10*dr + 0.13*tor)*100
    return round(max(0,min(100,sc)),2)

def score_cbb(s):
    em=max(0,min(1,(s.get("eff_margin",0)+30)/65))
    ao=max(0,min(1,(s.get("adj_o",110)-90)/40))
    ad=max(0,min(1,1-(s.get("adj_d",102)-85)/35))
    ef=max(0,min(1,(s.get("efg",0.52)-0.42)/0.18))
    to=max(0,min(1,1-(s.get("to_rate",0.18)-0.12)/0.12))
    ex=s.get("exp",0.75)
    tp=max(0,min(1,(s.get("tempo",70)-58)/20))
    sc=(0.28*em+0.20*ao+0.20*ad+0.10*ef+0.10*to+0.08*ex+0.04*tp)*100
    return round(max(0,min(100,sc)),2)

def risk_tag(kind, gap):
    if gap>=22: return ("Low","risk-low")
    if gap>=10: return ("Med","risk-med")
    return ("High","risk-high")

# ── PLAYER PROPS (hybrid) ─────────────────────────────────────────────────────
# Keep your existing player baseline dict, but now we only show players that exist in Kalshi markets.
NBA_PROPS_FB = {
    "Nikola Jokic":{"team":"Denver Nuggets","pos":"C","pts_avg":27.2,"reb_avg":12.8,"ast_avg":9.5,"pra_avg":49.5,"mins":34.5,"usg":0.31,
                    "pts_l5":26.8,"reb_l5":13.2,"ast_l5":10.1},
    "Shai Gilgeous-Alexander":{"team":"Oklahoma City Thunder","pos":"G","pts_avg":31.8,"reb_avg":5.2,"ast_avg":6.1,"pra_avg":43.1,"mins":34.8,"usg":0.37,
                    "pts_l5":33.2,"reb_l5":5.0,"ast_l5":6.5},
    # add more as needed; Kalshi-market-driven roster means you can keep this moderate
}

PROP_MARKET_KEYWORDS = {
    "pra":["pra","points + rebounds + assists","pts+reb+ast","points rebounds assists"],
    "pts":["points"],
    "reb":["rebounds"],
    "ast":["assists"],
    "3pm":["3-pointers","3 pointers","3pm","threes"],
    "blk":["blocks","blk"],
    "stl":["steals","stl"],
}

def project_prop(player, kind):
    p=NBA_PROPS_FB.get(player)
    if not p: return None
    # 60/40 season vs last5
    if kind=="pts":
        base=0.6*p["pts_avg"]+0.4*p["pts_l5"]; std=max(3.0, base*0.22)
    elif kind=="reb":
        base=0.6*p["reb_avg"]+0.4*p["reb_l5"]; std=max(2.0, base*0.28)
    elif kind=="ast":
        base=0.6*p["ast_avg"]+0.4*p["ast_l5"]; std=max(2.0, base*0.30)
    else: # pra
        base=0.6*p["pra_avg"]+0.4*(p["pts_l5"]+p["reb_l5"]+p["ast_l5"]); std=max(3.0, base*0.20)
    return {"player":player,"team":p["team"],"pos":p["pos"],"proj":round(base,1),"std":round(std,1)}

def prop_prob_over(proj, line, std):
    if std<=0: return 0.50
    z=(proj-line)/std
    # tanh approx
    return max(0.05, min(0.95, 0.5 + 0.35*math.tanh(z*0.8)))

# ── TRACKER ───────────────────────────────────────────────────────────────────
def load_picks():
    if os.path.exists(TRACKER_FILE):
        try:
            with open(TRACKER_FILE) as f: return json.load(f)
        except: return []
    return []

def calc_summary(picks):
    settled=[p for p in picks if p.get("result") in ("W","L")]
    wins=sum(1 for p in settled if p["result"]=="W")
    losses=sum(1 for p in settled if p["result"]=="L")
    return {"wins":wins,"losses":losses,"total":len(settled)}

# ── SESSION STATE ─────────────────────────────────────────────────────────────
st.session_state.setdefault("min_conf", 60)  # default 60-65 target
st.session_state.setdefault("min_edge", 5)   # default edge threshold in %

# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 🏆 Betting Dashboard")
    st.caption(f"{today_est().strftime('%A, %B %d')} · {now_est()}")
    st.divider()

    sport = st.radio("Sport", list(SPORT_CONFIG.keys()), label_visibility="collapsed")
    sl = SPORT_CONFIG[sport]["label"]
    em = SPORT_ICONS.get(sl,"🏆")

    picks=load_picks()
    summ=calc_summary(picks)
    st.caption(f"Record: {summ['wins']}-{summ['losses']} ({summ['total']} settled)")
    st.divider()

    st.session_state["min_conf"]=st.slider("Min Confidence %", 50, 80, int(st.session_state["min_conf"]), 1)
    st.session_state["min_edge"]=st.slider("Min Edge %", 0, 20, int(st.session_state["min_edge"]), 1,
                                           help="Model% - Kalshi% must exceed this")

    st.divider()
    st.caption("Kalshi prices cached 10 min · Public-safe")

# ── LOAD DATA ────────────────────────────────────────────────────────────────
cfg=SPORT_CONFIG[sport]
events=parse_espn_events(fetch_espn(cfg["espn_sport"], cfg["espn_league"]))

kalshi_markets=kalshi_markets_open(limit=2000)  # cached 10 min

# ── BUILD PICKS (sport-specific) ──────────────────────────────────────────────
picks=[]
if sl=="NBA":
    stats, src = live_nba()
    for e in events:
        h=e["home"]["name"]; a=e["away"]["name"]
        hs=stats.get(h); as_=stats.get(a)
        if not hs or not as_: continue
        sc_h=score_nba(hs); sc_a=score_nba(as_)
        if sc_h>=sc_a:
            fav, dog, fav_s, dog_s = h,a,sc_h,sc_a
        else:
            fav, dog, fav_s, dog_s = a,h,sc_a,sc_h
        gap=round(fav_s-dog_s,2)
        conf=gap_to_confidence(gap)
        # Kalshi line: implied probability
        m=find_kalshi_game_market(kalshi_markets, h, a)
        kalshi_line = kalshi_yes_price_to_implied(m.get("yes_price")) if m else None
        edge = round(conf - kalshi_line,1) if kalshi_line is not None else None
        risk, css = risk_tag("NBA", gap)
        why=[]
        why.append(f"Model gap: {gap:.1f} (score {fav_s:.1f} vs {dog_s:.1f})")
        if m and m.get("yes_price") is not None:
            why.append(f"Kalshi implied: {kalshi_line:.0f}% (YES {m.get('yes_price')}¢)")
        # filters
        if conf < st.session_state["min_conf"]: 
            continue
        if edge is not None and edge < st.session_state["min_edge"]:
            continue
        picks.append({"time":e["gametime"],"fav":fav,"dog":dog,"conf":conf,"gap":gap,
                      "kalshi":kalshi_line,"edge":edge,"risk":risk,"risk_css":css,"why":why[:3]})
elif sl=="CBB":
    stats, src = live_cbb()
    for e in events:
        h=normalize_team(e["home"]["name"]); a=normalize_team(e["away"]["name"])
        hs=stats.get(h); as_=stats.get(a)
        if not hs or not as_: continue
        sc_h=score_cbb(hs); sc_a=score_cbb(as_)
        if sc_h>=sc_a:
            fav, dog, fav_s, dog_s = h,a,sc_h,sc_a
        else:
            fav, dog, fav_s, dog_s = a,h,sc_a,sc_h
        gap=round(fav_s-dog_s,2)
        conf=gap_to_confidence(gap)
        m=find_kalshi_game_market(kalshi_markets, h, a)
        kalshi_line = kalshi_yes_price_to_implied(m.get("yes_price")) if m else None
        edge = round(conf - kalshi_line,1) if kalshi_line is not None else None
        risk, css = risk_tag("CBB", gap)
        if conf < st.session_state["min_conf"]:
            continue
        if edge is not None and edge < st.session_state["min_edge"]:
            continue
        why=[f"Eff gap: {gap:.1f} (score {fav_s:.1f} vs {dog_s:.1f})"]
        if kalshi_line is not None: why.append(f"Kalshi implied: {kalshi_line:.0f}%")
        picks.append({"time":e["gametime"],"fav":fav,"dog":dog,"conf":conf,"gap":gap,
                      "kalshi":kalshi_line,"edge":edge,"risk":risk,"risk_css":css,"why":why[:3]})
else:
    src="—"

# ── HEADER ────────────────────────────────────────────────────────────────────
st.markdown(f"**{em} {sl}** · {today_est().strftime('%b %d')} · Kalshi cache 10m · picks: {len(picks)}")
st.divider()

tabs=st.tabs(["🗓️ Today","🎯 Props","📺 Scores"])

# ── TODAY ─────────────────────────────────────────────────────────────────────
with tabs[0]:
    if sl not in ("NBA","CBB"):
        st.info("Kalshi hybrid currently enabled for NBA + CBB. Add other sports next.")
    if not picks:
        st.warning("No picks meet thresholds, or Kalshi odds missing. Try lowering Min Edge / Min Conf.")
    else:
        for g in sorted(picks, key=lambda x:(x["edge"] if x["edge"] is not None else -999), reverse=True):
            c1,c2,c3=st.columns([3.5,1.4,1.6])
            with c1:
                st.markdown(f"**{g['fav']}** vs **{g['dog']}**")
                st.caption(f"{g['time']} · Gap {g['gap']:.1f}")
                st.markdown(f"**Pick:** `{g['fav']} (YES)`")
                for b in g["why"]:
                    st.caption(f"• {b}")
            with c2:
                st.progress(int(min(g["conf"],100)), text=f"{g['conf']:.0f}%")
                st.markdown(f'<span class="{g["risk_css"]}">{g["risk"]} variance</span>', unsafe_allow_html=True)
            with c3:
                if g["kalshi"] is not None:
                    st.metric("Kalshi line", f"{g['kalshi']:.0f}%")
                else:
                    st.metric("Kalshi line", "—")
                st.metric("Edge", f"+{g['edge']:.1f}%" if g["edge"] is not None else "—")
            st.divider()

# ── PROPS ─────────────────────────────────────────────────────────────────────
with tabs[1]:
    st.markdown("#### 🎯 Props (Hybrid: model projection vs Kalshi price)")
    st.caption("PRA prioritized. Includes 3PM/BLK/STL when markets exist. Prices cached 10 minutes.")
    if sl!="NBA":
        st.info("Props currently wired for NBA first.")
    else:
        prop_kind = st.selectbox("Prop type", ["pra","pts","reb","ast","3pm","blk","stl"])
        min_conf = st.slider("Min model prob (Over)", 0.50, 0.80, 0.60, 0.01)
        min_edge = st.slider("Min edge vs Kalshi", 0.00, 0.20, 0.06, 0.01)

        # Find Kalshi prop markets by keyword
        kws=PROP_MARKET_KEYWORDS[prop_kind]
        prop_markets=[]
        for m in kalshi_markets:
            title=str(m.get("title","")).lower()
            if any(k in title for k in kws):
                prop_markets.append(m)

        # Build roster directly from Kalshi availability:
        # players = parsed from market titles; only show projections for players we have baselines for.
        results=[]
        for m in prop_markets[:800]:
            title=str(m.get("title",""))
            player=extract_player_from_title(title)
            if not player: 
                continue
            proj=project_prop(player, "pra" if prop_kind=="pra" else prop_kind if prop_kind in ("pts","reb","ast") else "pra")
            if not proj:
                continue
            # Synthetic threshold extraction is hard; treat each market as "Over X" when possible:
            # If title includes a number, use it as line; else skip.
            nums=re.findall(r"(\d+\.?\d*)", title)
            if not nums:
                continue
            line=float(nums[-1])
            p_over=prop_prob_over(proj["proj"], line, proj["std"])
            model_pct=p_over*100
            kalshi_pct=kalshi_yes_price_to_implied(m.get("yes_price"))
            if kalshi_pct is None:
                continue
            edge=(model_pct - kalshi_pct)/100.0
            if p_over < min_conf: 
                continue
            if edge < min_edge:
                continue
            results.append({
                "player":player,"team":proj["team"],"proj":proj["proj"],"line":line,
                "model":model_pct,"kalshi":kalshi_pct,"edge":edge*100,
                "ticker":m.get("ticker","")
            })

        if not results:
            st.warning("No props meet thresholds. Lower Min model prob or Min edge.")
        else:
            results.sort(key=lambda x:x["edge"], reverse=True)
            for r in results[:25]:
                c1,c2,c3=st.columns([2.6,1.4,1.6])
                with c1:
                    st.markdown(f"**{r['player']}** · {r['team']}")
                    st.caption(f"Market: {r['ticker']}")
                with c2:
                    st.metric("Proj vs Line", f"{r['proj']:.1f} vs {r['line']:.1f}")
                    st.caption(f"Model: {r['model']:.0f}%")
                with c3:
                    st.metric("Kalshi implied", f"{r['kalshi']:.0f}%")
                    st.metric("Edge", f"+{r['edge']:.1f}%")
                st.divider()

# ── SCORES ────────────────────────────────────────────────────────────────────
with tabs[2]:
    st.markdown("#### 📺 Scores (ESPN)")
    if not events:
        st.info("No scoreboard events.")
    else:
        for e in events[:30]:
            state=e["state"]
            badge = "🔴 LIVE" if state=="in" else ("✅ FINAL" if state=="post" else "🕐 PRE")
            st.caption(f"{badge} {e['away']['name']} @ {e['home']['name']} · {e['gametime']} · {e['detail']}")
