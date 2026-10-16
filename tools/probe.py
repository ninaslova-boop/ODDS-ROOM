"""Temporary: print the structure of lineup / goalie sources (run in GitHub Actions)."""
import datetime as dt
import json

import requests

S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0"


def get(url, **kw):
    try:
        r = S.get(url, timeout=25, **kw)
        print(f"\n### {r.status_code} {url}")
        return r.json() if r.ok else None
    except Exception as e:  # noqa: BLE001
        print(f"\n### ERR {url}: {e}")
        return None


def shape(o, depth=0, maxd=4, key=""):
    pad = "  " * depth
    if depth > maxd:
        return
    if isinstance(o, dict):
        for k, v in list(o.items())[:40]:
            if isinstance(v, (dict, list)):
                print(f"{pad}{k}: {type(v).__name__}{'[' + str(len(v)) + ']' if isinstance(v, list) else ''}")
                shape(v, depth + 1, maxd, k)
            else:
                print(f"{pad}{k}: {str(v)[:70]}")
    elif isinstance(o, list) and o:
        shape(o[0], depth, maxd, key)


today = dt.date.today()
ESPN = "https://site.api.espn.com/apis/site/v2/sports"
# ---------------- NHL ----------------
for d in (today, today + dt.timedelta(days=1)):
    sb = get(f"{ESPN}/hockey/nhl/scoreboard", params={"dates": d.strftime("%Y%m%d")})
    if not sb:
        continue
    pre = [e for e in sb.get("events", []) if e["status"]["type"]["state"] == "pre"]
    print("NHL pre events", d, len(pre))
    if pre:
        e = pre[0]
        print("--- scoreboard competition keys")
        shape(e["competitions"][0], maxd=2)
        sm = get(f"{ESPN}/hockey/nhl/summary", params={"event": e["id"]})
        if sm:
            print("--- summary top keys", list(sm.keys()))
            for k in ("rosters", "probables", "goalies", "boxscore", "injuries"):
                if k in sm:
                    print("--- summary", k)
                    shape(sm[k], maxd=3)
        break
sch = get("https://api-web.nhle.com/v1/schedule/now")
if sch:
    games = [g for w in sch.get("gameWeek", []) for g in w.get("games", []) if g.get("gameState") in ("FUT", "PRE")]
    print("nhle future games", len(games))
    if games:
        gid = games[0]["id"]
        land = get(f"https://api-web.nhle.com/v1/gamecenter/{gid}/landing")
        if land:
            print("--- landing keys", list(land.keys()))
            if "matchup" in land:
                shape(land["matchup"], maxd=4)
        rr = get(f"https://api-web.nhle.com/v1/gamecenter/{gid}/right-rail")
        if rr:
            print("--- right-rail keys", list(rr.keys()))
# ---------------- NBA ----------------
for d in (today, today + dt.timedelta(days=1)):
    sb = get(f"{ESPN}/basketball/nba/scoreboard", params={"dates": d.strftime("%Y%m%d")})
    if not sb:
        continue
    pre = [e for e in sb.get("events", []) if e["status"]["type"]["state"] == "pre"]
    print("NBA pre events", d, len(pre))
    if pre:
        sm = get(f"{ESPN}/basketball/nba/summary", params={"event": pre[0]["id"]})
        if sm:
            print("--- summary top keys", list(sm.keys()))
            for k in ("rosters", "boxscore", "injuries", "lastFiveGames"):
                if k in sm:
                    print("--- summary", k)
                    shape(sm[k], maxd=3)
        break
# ---------------- EuroLeague ----------------
y = today.year if today.month >= 9 else today.year - 1
for base in ("https://api-live.euroleague.net/v2", "https://feeds.incrowdsports.com/provider/euroleague-feeds/v2"):
    j = get(f"{base}/competitions/E/seasons/E{y}/games")
    data = (j or {}).get("data") if isinstance(j, dict) else j
    if not data:
        continue
    fut = [g for g in data if not g.get("played")]
    past = [g for g in data if g.get("played")]
    print("EL future", len(fut), "past", len(past))
    if fut:
        g = sorted(fut, key=lambda g: g.get("utcDate") or g.get("date"))[0]
        code = g.get("gameCode")
        print("next game", code, g.get("utcDate"), g["local"]["club"].get("code"), g["road"]["club"].get("code"))
        shape(g, maxd=2)
        for path in (f"{base}/competitions/E/seasons/E{y}/games/{code}",
                     f"{base}/competitions/E/seasons/E{y}/games/{code}/stats",
                     f"{base}/competitions/E/seasons/E{y}/clubs/{g['local']['club'].get('code')}/people",
                     f"https://live.euroleague.net/api/Header?gamecode={code}&seasoncode=E{y}",
                     f"https://live.euroleague.net/api/Boxscore?gamecode={code}&seasoncode=E{y}"):
            x = get(path)
            if x is not None:
                shape(x, maxd=3)
    if past:
        g = sorted(past, key=lambda g: g.get("utcDate") or g.get("date"))[-1]
        code = g.get("gameCode")
        x = get(f"{base}/competitions/E/seasons/E{y}/games/{code}/stats")
        if x is not None:
            print("--- past game stats")
            shape(x, maxd=4)
    break
