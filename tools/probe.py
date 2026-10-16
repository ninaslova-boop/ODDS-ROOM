"""Temporary: does ESPN publish football injuries for the current season?"""
import datetime as dt
import requests
S = requests.Session(); S.headers["User-Agent"] = "Mozilla/5.0"
def get(u, **kw):
    try:
        r = S.get(u, timeout=25, **kw); print(f"### {r.status_code} {u} {kw.get('params','')}"); return r.json() if r.ok else None
    except Exception as e:
        print(f"### ERR {u}: {e}"); return None
for slug in ("fra.1", "esp.1", "eng.1"):
    j = get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/injuries")
    if j: print("  injuries keys", list(j)[:10], "teams", len(j.get("injuries", []) or []), str(j)[:300])
    t = get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams")
    teams = [x["team"] for x in (((t or {}).get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams", [])]
    if teams:
        tid = teams[0]["id"]; print("  team", teams[0]["displayName"], tid)
        x = get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams/{tid}", params={"enable": "roster,injuries"})
        if x: print("  team keys", list((x.get("team") or {}).keys())[:30]); print("  injuries in team:", str((x.get("team") or {}).get("injuries"))[:300])
        c = get(f"https://sports.core.api.espn.com/v2/sports/soccer/leagues/{slug}/teams/{tid}/injuries")
        if c: print("  core injuries", str(c)[:400])
        r = get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams/{tid}/roster")
        if r:
            ath = r.get("athletes") or []
            inj = [(a.get("displayName"), a.get("injuries"), (a.get("status") or {}).get("type")) for a in ath if a.get("injuries")]
            print("  roster", len(ath), "with injuries", len(inj), str(inj[:3])[:400])
    sb = get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard")
    ev = [e for e in (sb or {}).get("events", []) if e["status"]["type"]["state"] == "pre"]
    if ev:
        sm = get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary", params={"event": ev[0]["id"]})
        if sm: print("  summary keys", list(sm.keys())); print("  summary injuries", str(sm.get("injuries"))[:400])
