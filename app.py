import streamlit as st
import requests, re, math
import pandas as pd
from datetime import datetime, timedelta, timezone

st.set_page_config(page_title="NBA Lines", page_icon="🏀", layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500&display=swap');
html,body,[class*="css"]{font-family:'Inter',sans-serif;}
.stApp{background:#0d1117;color:#e6edf3;}
section[data-testid="stSidebar"]{background:#161b22!important;border-right:1px solid #30363d;}
section[data-testid="stSidebar"] *{color:#c9d1d9;}
#MainMenu,footer,header{visibility:hidden;}
table{width:100%;border-collapse:collapse;margin:4px 0 12px 0;}
th{font-size:.63rem;font-weight:700;color:#8b949e;text-transform:uppercase;letter-spacing:.06em;
   padding:6px 10px;border-bottom:2px solid #30363d;text-align:left;}
td{font-size:.85rem;padding:8px 10px;border-bottom:1px solid #21262d;vertical-align:middle;}
tr:last-child td{border-bottom:none;}
.mono{font-family:'JetBrains Mono',monospace;}
.green{color:#3fb950;font-weight:700;}
.red{color:#f85149;font-weight:700;}
.grey{color:#8b949e;}
.sec{font-size:.68rem;font-weight:700;color:#58a6ff;text-transform:uppercase;
     letter-spacing:.08em;margin:16px 0 4px;}
</style>
""", unsafe_allow_html=True)

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

# ── ESPN abbrev → full team name ───────────────────────────────────────────────
ESPN_MAP = {
    "ATL":"Atlanta Hawks","BOS":"Boston Celtics","BKN":"Brooklyn Nets",
    "CHA":"Charlotte Hornets","CHI":"Chicago Bulls","CLE":"Cleveland Cavaliers",
    "DAL":"Dallas Mavericks","DEN":"Denver Nuggets","DET":"Detroit Pistons",
    "GS":"Golden State Warriors","HOU":"Houston Rockets","IND":"Indiana Pacers",
    "LAC":"Los Angeles Clippers","LAL":"Los Angeles Lakers","MEM":"Memphis Grizzlies",
    "MIA":"Miami Heat","MIL":"Milwaukee Bucks","MIN":"Minnesota Timberwolves",
    "NO":"New Orleans Pelicans","NY":"New York Knicks","OKC":"Oklahoma City Thunder",
    "ORL":"Orlando Magic","PHI":"Philadelphia 76ers","PHX":"Phoenix Suns",
    "POR":"Portland Trail Blazers","SAC":"Sacramento Kings","SA":"San Antonio Spurs",
    "TOR":"Toronto Raptors","UTAH":"Utah Jazz","WSH":"Washington Wizards",
}

# ── Aliases for fuzzy matching Kalshi text ─────────────────────────────────────
ALIASES = {
    "thunder":"Oklahoma City Thunder","cavaliers":"Cleveland Cavaliers",
    "celtics":"Boston Celtics","rockets":"Houston Rockets",
    "warriors":"Golden State Warriors","pacers":"Indiana Pacers",
    "grizzlies":"Memphis Grizzlies","nuggets":"Denver Nuggets",
    "lakers":"Los Angeles Lakers","knicks":"New York Knicks","new york k":"New York Knicks",
    "bucks":"Milwaukee Bucks","76ers":"Philadelphia 76ers","sixers":"Philadelphia 76ers",
    "timberwolves":"Minnesota Timberwolves","heat":"Miami Heat",
    "kings":"Sacramento Kings","clippers":"Los Angeles Clippers",
    "mavericks":"Dallas Mavericks","hawks":"Atlanta Hawks","suns":"Phoenix Suns",
    "bulls":"Chicago Bulls","nets":"Brooklyn Nets","magic":"Orlando Magic",
    "hornets":"Charlotte Hornets","raptors":"Toronto Raptors","jazz":"Utah Jazz",
    "spurs":"San Antonio Spurs","trail blazers":"Portland Trail Blazers",
    "blazers":"Portland Trail Blazers","pistons":"Detroit Pistons",
    "pelicans":"New Orleans Pelicans","wizards":"Washington Wizards",
    "oklahoma city":"Oklahoma City Thunder","cleveland":"Cleveland Cavaliers",
    "boston":"Boston Celtics","houston":"Houston Rockets",
    "golden state":"Golden State Warriors","indiana":"Indiana Pacers",
    "memphis":"Memphis Grizzlies","denver":"Denver Nuggets",
    "new york":"New York Knicks","milwaukee":"Milwaukee Bucks",
    "philadelphia":"Philadelphia 76ers","minnesota":"Minnesota Timberwolves",
    "miami":"Miami Heat","sacramento":"Sacramento Kings","dallas":"Dallas Mavericks",
    "atlanta":"Atlanta Hawks","phoenix":"Phoenix Suns","chicago":"Chicago Bulls",
    "brooklyn":"Brooklyn Nets","orlando":"Orlando Magic","charlotte":"Charlotte Hornets",
    "toronto":"Toronto Raptors","utah":"Utah Jazz","san antonio":"San Antonio Spurs",
    "portland":"Portland Trail Blazers","detroit":"Detroit Pistons",
    "new orleans":"New Orleans Pelicans","washington":"Washington Wizards",
    "los angeles l":"Los Angeles Lakers","la lakers":"Los Angeles Lakers",
    "los angeles c":"Los Angeles Clippers","la clippers":"Los Angeles Clippers",
}

def find_team(text):
    t = (text or "").lower()
    for k in sorted(ALIASES, key=len, reverse=True):
        if re.search(r'\b' + re.escape(k) + r'\b', t):
            return ALIASES[k]
    return None

# ── Net ratings (2025-26) ──────────────────────────────────────────────────────
NET = {
    "Oklahoma City Thunder":12.1,"Cleveland Cavaliers":11.8,"Boston Celtics":10.2,
    "Houston Rockets":7.9,"Golden State Warriors":7.1,"Indiana Pacers":6.8,
    "Memphis Grizzlies":5.9,"Denver Nuggets":5.4,"Los Angeles Lakers":4.8,
    "New York Knicks":4.5,"Milwaukee Bucks":3.9,"Philadelphia 76ers":3.4,
    "Minnesota Timberwolves":3.0,"Miami Heat":2.6,"Sacramento Kings":1.9,
    "Los Angeles Clippers":1.2,"Dallas Mavericks":0.7,"Atlanta Hawks":-0.6,
    "Phoenix Suns":-1.4,"Chicago Bulls":-2.1,"Brooklyn Nets":-2.7,
    "Orlando Magic":-3.3,"Charlotte Hornets":-4.0,"Toronto Raptors":-4.5,
    "Utah Jazz":-5.3,"San Antonio Spurs":-6.0,"Portland Trail Blazers":-6.8,
    "Detroit Pistons":9.2,   # updated: 45-15 record this season
    "New Orleans Pelicans":-8.1,"Washington Wizards":-9.5,
}

def win_prob(home, away):
    h, a = NET.get(home), NET.get(away)
    if h is None or a is None: return None
    z = ((h - a) / 2.5 + 2.5) / (11.0 * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))

def cover_prob(team, line, home, away):
    h, a = NET.get(home), NET.get(away)
    if h is None or a is None: return None
    margin = (h - a) / 2.5 + 2.5          # expected home margin
    adj = (margin if team == home else -margin) - line
    z = adj / (11.0 * math.sqrt(2))
    return 0.5 * (1 + math.erf(z))

def kprob(m):
    yb = m.get("yes_bid") or 0
    ya = m.get("yes_ask") or 0
    lp = m.get("last_price") or 0
    if yb > 0 and ya > 0: return round((yb + ya) / 200, 4)
    if lp > 0: return round(lp / 100, 4)
    return None

def am_odds(p):
    if p is None or p < 0.02 or p > 0.98: return "—"
    return f"-{round(p/(1-p)*100)}" if p >= 0.5 else f"+{round((1-p)/p*100)}"

def edge(mp, kp):
    if mp is None or kp is None: return "—", ""
    e = (mp - kp) * 100
    return f"{e:+.1f}%", ("green" if e > 2 else "red" if e < -2 else "grey")

# ── Time helpers ───────────────────────────────────────────────────────────────
def _off(u):
    y = u.year
    m2 = datetime(y,3,8,2,tzinfo=timezone.utc)
    while m2.weekday() != 6: m2 += timedelta(days=1)
    n1 = datetime(y,11,1,2,tzinfo=timezone.utc)
    while n1.weekday() != 6: n1 += timedelta(days=1)
    return timedelta(hours=-4) if m2 <= u < n1 else timedelta(hours=-5)

def now_et():
    u = datetime.now(timezone.utc)
    return u + _off(u)

def fmt_time(iso):
    if not iso: return ""
    try:
        u = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        et = u + _off(u)
        return f"{int(et.strftime('%-I'))}:{et.strftime('%M %p')} ET"
    except: return ""

# ── STEP 1: ESPN scoreboard ────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def get_espn_games(date_str):
    """
    Hit ESPN's free scoreboard API.
    Returns list of {away, home, time_et, status, date_iso}
    """
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date_str}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return [], str(e)

    games = []
    for ev in data.get("events", []):
        comp = ev.get("competitions", [{}])[0]
        home = away = None
        for t in comp.get("competitors", []):
            abbr = t.get("team", {}).get("abbreviation", "")
            full = ESPN_MAP.get(abbr) or t.get("team", {}).get("displayName", abbr)
            if t.get("homeAway") == "home": home = full
            else: away = full
        date_iso = ev.get("date", "")
        status   = ev.get("status", {}).get("type", {}).get("description", "Scheduled")
        games.append({"away": away, "home": home,
                      "time_et": fmt_time(date_iso),
                      "status": status, "date_iso": date_iso})
    return games, None

# ── STEP 2: Kalshi events for kxnbagame ───────────────────────────────────────
@st.cache_data(ttl=180, show_spinner=False)
def get_kalshi_events():
    """
    Fetch all kxnbagame events (any status = open, closed, unopened).
    Markets open 1-2 days ahead, so just grab everything and match.
    Returns list of {event_ticker, title, markets:[...]}
    """
    events = []
    seen   = set()
    for status in [None, "open", "closed", "unopened"]:
        cursor = None
        for _ in range(20):
            params = {"series_ticker": "kxnbagame",
                      "with_nested_markets": "true", "limit": 200}
            if status: params["status"] = status
            if cursor: params["cursor"] = cursor
            try:
                r = requests.get(f"{KALSHI}/events", params=params, timeout=20)
                if r.status_code != 200: break
                d = r.json()
                for ev in d.get("events", []):
                    tk = ev.get("event_ticker", "")
                    if tk and tk not in seen:
                        seen.add(tk)
                        events.append(ev)
                cursor = d.get("cursor")
                if not cursor or not d.get("events"): break
            except: break
        if events: break  # first successful call gives us everything
    return events

@st.cache_data(ttl=180, show_spinner=False)
def get_all_markets_for_event(event_ticker):
    markets = []
    cursor  = None
    for _ in range(10):
        params = {"event_ticker": event_ticker, "limit": 200}
        if cursor: params["cursor"] = cursor
        try:
            r = requests.get(f"{KALSHI}/markets", params=params, timeout=15)
            if r.status_code != 200: break
            d = r.json()
            markets.extend(d.get("markets", []))
            cursor = d.get("cursor")
            if not cursor or not d.get("markets"): break
        except: break
    return markets

# ── STEP 3: Match ESPN game → Kalshi event ────────────────────────────────────
def city_of(name):
    """'Dallas Mavericks' → 'dallas' | 'New Orleans Pelicans' → 'new orleans'"""
    parts = (name or "").split()
    return " ".join(parts[:-1]).lower()

def nick_of(name):
    return (name or "").split()[-1].lower()

def team_hit(name, text):
    t = text.lower()
    return (bool(re.search(r'\b' + re.escape(city_of(name)) + r'\b', t)) or
            bool(re.search(r'\b' + re.escape(nick_of(name)) + r'\b', t)))

def match_event(game, kalshi_events):
    """Return best-matching Kalshi event for an ESPN game, or None."""
    home, away = game.get("home"), game.get("away")
    if not home or not away: return None
    best_tk, best_score = None, 0
    for ev in kalshi_events:
        title = ev.get("title", "")
        h = team_hit(home, title)
        a = team_hit(away, title)
        score = (2 if h else 0) + (2 if a else 0)
        if score > best_score:
            best_score, best_tk = score, ev.get("event_ticker")
    # Require both teams matched (score=4)
    return best_tk if best_score >= 4 else None

# ── STEP 4: Classify market type ──────────────────────────────────────────────
def classify(title):
    t = (title or "").lower()
    if re.search(r'win(s)? by|cover|by more than|by at least|\bspread\b', t): return "spread"
    if re.search(r'[-+]\d+\.?\d*\s*points?\b', t): return "spread"
    if re.search(r'total points|combined|over/under', t): return "total"
    if re.search(r'\b(over|under)\s+\d', t): return "total"
    stats = r'\b(points?|rebounds?|assists?|steals?|blocks?|3-pointer|threes?|made)\b'
    if re.search(stats, t) and re.search(r'\d+\.?\d*\+', t): return "prop"
    return "moneyline"

# ── STEP 5: Render ─────────────────────────────────────────────────────────────
def build_table(rows):
    h = ("<table><tr><th>Market</th><th>Kalshi</th>"
         "<th>Odds</th><th>Model</th><th>Edge</th></tr>")
    for label, kp, mp in rows:
        kpct = f"{kp*100:.0f}%" if kp is not None else "—"
        mpct = f"{mp*100:.0f}%" if mp is not None else "—"
        o    = am_odds(kp)
        e, cls = edge(mp, kp)
        h += (f"<tr><td>{label}</td><td class='mono'>{kpct}</td>"
              f"<td class='mono'>{o}</td><td class='mono'>{mpct}</td>"
              f"<td class='mono {cls}'>{e}</td></tr>")
    return h + "</table>"

def render(game, markets):
    home = game["home"] or "?"
    away = game["away"] or "?"
    hn, an = NET.get(home), NET.get(away)
    mp_home = win_prob(home, away)
    mp_away = (1 - mp_home) if mp_home else None

    st.markdown(f"### {away} @ {home}  ·  {game['time_et']}")
    if hn is not None and an is not None:
        st.caption(f"Net ratings — {home.split()[-1]}: {hn:+.1f} | "
                   f"{away.split()[-1]}: {an:+.1f} | home court +2.5")

    if not markets:
        st.warning("No Kalshi markets found for this game.")
        return

    ml, sp, to, pr = [], [], [], []
    seen = set()

    for m in markets:
        title = m.get("title", "")
        key   = title.lower().strip()
        if key in seen: continue
        seen.add(key)

        kp  = kprob(m)
        typ = classify(title)
        ys  = m.get("yes_sub_title", "") or title
        lbl = ys[:62]

        if typ == "moneyline":
            t_yn = find_team(ys) or find_team(title)
            mp = mp_home if t_yn == home else (mp_away if t_yn == away else None)
            ml.append((lbl, kp, mp))

        elif typ == "spread":
            nums = re.findall(r'\d+\.?\d*', title)
            mp_sp = None
            if nums:
                try:
                    line = float(nums[-1])
                    t_sp = find_team(lbl) or find_team(title)
                    if t_sp in (home, away):
                        mp_sp = cover_prob(t_sp, line, home, away)
                except: pass
            sp.append((lbl, kp, mp_sp))

        elif typ == "total":
            to.append((lbl, kp, None))

        elif typ == "prop" and kp is not None:
            pr.append((lbl, kp, None))

    if ml:
        st.markdown('<div class="sec">Moneyline</div>', unsafe_allow_html=True)
        st.markdown(build_table(ml[:3]), unsafe_allow_html=True)
    if sp:
        st.markdown('<div class="sec">Spread</div>', unsafe_allow_html=True)
        st.markdown(build_table(sorted(sp, key=lambda x: x[1] or 0, reverse=True)[:6]), unsafe_allow_html=True)
    if to:
        st.markdown('<div class="sec">Game Total</div>', unsafe_allow_html=True)
        st.markdown(build_table(to[:3]), unsafe_allow_html=True)
    if pr:
        st.markdown('<div class="sec">Player Props</div>', unsafe_allow_html=True)
        st.markdown(build_table(sorted(pr, key=lambda x: x[1] or 0, reverse=True)[:12]), unsafe_allow_html=True)

    st.divider()
    st.caption(f"{len(markets)} markets loaded from Kalshi")

# ── MAIN ──────────────────────────────────────────────────────────────────────
et_now    = now_et()
date_str  = et_now.strftime("%Y%m%d")

with st.spinner("Loading…"):
    games, espn_err    = get_espn_games(date_str)
    kalshi_events      = get_kalshi_events()

# ── SIDEBAR ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"### 🏀 NBA  ·  {et_now.strftime('%b %-d')}")
    st.caption(f"{et_now.strftime('%-I:%M %p')} ET")
    st.divider()

    if espn_err:
        st.error(f"ESPN error: {espn_err}")
    if not games:
        st.warning("No games from ESPN.")
        sel = None
    else:
        def glabel(g):
            aw = (g["away"] or "?").split()[-1]
            hm = (g["home"] or "?").split()[-1]
            return f"{aw} @ {hm}  ·  {g['time_et']}"
        sel = st.radio("Game", range(len(games)), format_func=lambda i: glabel(games[i]))

    st.divider()
    st.caption(f"{len(games)} ESPN games  ·  {len(kalshi_events)} Kalshi events")
    st.caption("Edge = model − Kalshi.\nModel: net rating + home court (σ=11).")

# ── MAIN PANEL ─────────────────────────────────────────────────────────────────
if games and sel is not None:
    g        = games[sel]
    best_tk  = match_event(g, kalshi_events)

    if best_tk:
        # Merge nested markets + full /markets fetch for that event
        nested_ev  = next((e for e in kalshi_events if e.get("event_ticker") == best_tk), {})
        nested     = nested_ev.get("markets", [])
        fetched    = get_all_markets_for_event(best_tk)
        all_dict   = {m["ticker"]: m for m in nested}
        for m in fetched: all_dict[m["ticker"]] = m
        render(g, list(all_dict.values()))
    else:
        st.markdown(f"### {g['away']} @ {g['home']}  ·  {g['time_et']}")
        # Show all Kalshi events so we can debug what's there
        st.warning("No Kalshi event matched this game. Markets may not be open yet, "
                   "or the team names don't match.")
        if kalshi_events:
            st.markdown("**Available Kalshi kxnbagame events:**")
            st.dataframe(pd.DataFrame([{
                "event_ticker": e.get("event_ticker",""),
                "title": e.get("title",""),
                "markets": len(e.get("markets",[])),
            } for e in kalshi_events]), hide_index=True, use_container_width=True)
elif not games:
    st.markdown(f"## 🏀 NBA — {et_now.strftime('%B %-d, %Y')}")
    st.info("No games found on ESPN today.")
