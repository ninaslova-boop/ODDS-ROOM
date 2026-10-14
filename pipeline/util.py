"""Shared helpers: HTTP with retries, JSON files, name matching, odds maths."""
from __future__ import annotations

import datetime as dt
import difflib
import json
import logging
import math
import os
import re
import time
import unicodedata
from pathlib import Path

import requests

log = logging.getLogger("pipeline")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "app" / "data"
CACHE_DIR = ROOT / "data" / "cache"

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept": "*/*"})


def http_get(url: str, params: dict | None = None, headers: dict | None = None,
             retries: int = 3, timeout: int = 30, as_json: bool = True):
    """GET a URL. Return parsed JSON (or text). Raise the last error after all retries."""
    last = None
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                last = RuntimeError(f"429 from {url}")
                continue
            r.raise_for_status()
            if as_json:
                return r.json()
            return decode_bytes(r.content)
        except Exception as e:  # noqa: BLE001 - network errors of many kinds
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last  # type: ignore[misc]


def decode_bytes(b: bytes) -> str:
    """football-data.co.uk CSVs can be UTF-8 (with BOM) or cp1252."""
    if b.startswith(b"\xef\xbb\xbf"):
        b = b[3:]
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", errors="replace")


def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data, compact: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(d: dt.datetime) -> str:
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> dt.datetime:
    s = s.replace("Z", "+00:00")
    d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


# ---------- names ----------
_STOP = {"fc", "cf", "afc", "sc", "ac", "as", "ss", "sv", "fk", "bk", "if", "cd", "ud", "rc", "rcd", "sd",
         "club", "de", "the", "calcio", "1", "vfb", "vfl", "tsg", "fsv", "bsc", "ssc", "us", "ogc", "losc", "stade"}

ALIASES = {
    # football-data.co.uk short names -> common long names (normalized form)
    "man united": "manchester united", "man utd": "manchester united", "man city": "manchester city",
    "nott m forest": "nottingham forest", "nottm forest": "nottingham forest", "wolves": "wolverhampton wanderers",
    "spurs": "tottenham", "tottenham hotspur": "tottenham", "newcastle united": "newcastle",
    "sheffield weds": "sheffield wednesday", "sheff wed": "sheffield wednesday", "sheff utd": "sheffield united",
    "west brom": "west bromwich albion", "qpr": "queens park rangers", "brighton and hove albion": "brighton",
    "ath madrid": "atletico madrid", "atletico de madrid": "atletico madrid", "ath bilbao": "athletic bilbao",
    "athletic club": "athletic bilbao", "betis": "real betis", "sociedad": "real sociedad", "celta": "celta vigo",
    "espanol": "espanyol", "vallecano": "rayo vallecano", "alaves": "deportivo alaves",
    "inter": "inter milan", "internazionale": "inter milan", "milan": "ac milan", "roma": "as roma",
    "verona": "hellas verona", "parma calcio 1913": "parma",
    "bayern munich": "bayern", "fc bayern munchen": "bayern", "bayern munchen": "bayern",
    "dortmund": "borussia dortmund", "m gladbach": "borussia monchengladbach", "monchengladbach": "borussia monchengladbach",
    "leverkusen": "bayer leverkusen", "ein frankfurt": "eintracht frankfurt", "fc koln": "koln", "cologne": "koln",
    "hoffenheim": "hoffenheim", "mainz": "mainz 05", "rb leipzig": "leipzig", "heidenheim": "heidenheim",
    "paris sg": "paris saint germain", "psg": "paris saint germain", "st etienne": "saint etienne",
    "marseille": "marseille", "olympique marseille": "marseille", "olympique lyonnais": "lyon",
    # ESPN UEFA names -> football-data.co.uk names
    "sporting cp": "sp lisbon", "sporting lisbon": "sp lisbon", "sporting": "sp lisbon", "braga": "sp braga",
    "union st gilloise": "st gilloise", "union saint gilloise": "st gilloise", "olympiacos": "olympiakos",
    "psv": "psv eindhoven", "az": "az alkmaar", "twente enschede": "twente", "fenerbahce sk": "fenerbahce",
    "besiktas jk": "besiktas", "red star belgrade": "crvena zvezda", "lille osc": "lille",
    "losc lille": "lille", "nice": "nice", "rennes": "rennes", "stade rennais": "rennes",
}


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("-", " ").replace(".", " ").replace("'", "")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = ALIASES.get(s, s)
    toks = [t for t in s.split() if t not in _STOP]
    s2 = " ".join(toks) or s
    return ALIASES.get(s2, s2)


def match_name(name: str, candidates: list[str], cutoff: float = 0.72) -> str | None:
    """Return the best candidate for `name` using normalized names."""
    if not candidates:
        return None
    n = norm_name(name)
    normed = {norm_name(c): c for c in candidates}
    if n in normed:
        return normed[n]
    # token containment ("tottenham" vs "tottenham hotspur")
    for k, c in normed.items():
        if n and k and (n in k.split() or k in n.split() or n.startswith(k + " ") or k.startswith(n + " ")):
            return c
    best = difflib.get_close_matches(n, list(normed), n=1, cutoff=cutoff)
    return normed[best[0]] if best else None


# ---------- odds ----------
def american_to_decimal(a) -> float | None:
    try:
        a = float(str(a).replace("+", ""))
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    return round(1 + (a / 100 if a > 0 else 100 / -a), 3)


def to_float(x) -> float | None:
    try:
        if x is None or x == "":
            return None
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def setup_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ---------- run mode and HTTP cache ----------
# "full": refresh every source. "quick" (the 30-minute runs): refresh only fast-changing data
# (fixtures near today, lineups, injuries) and reuse cached copies of the heavy sources.
RUN = {"mode": "full"}


def is_quick() -> bool:
    return RUN["mode"] == "quick"


def cached_get(url: str, params: dict | None = None, headers: dict | None = None, as_json: bool = True,
               always_fresh: bool = False):
    """GET with a disk copy. Quick mode returns the copy when there is one.
    When a fresh download fails, the last good copy is used instead."""
    import hashlib
    key = hashlib.sha1((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()[:20]
    path = CACHE_DIR / "http" / f"{key}.json"
    old = read_json(path)
    if old is not None and is_quick() and not always_fresh:
        return old["body"]
    try:
        body = http_get(url, params=params, headers=headers, as_json=as_json)
    except Exception:
        if old is not None:
            log.warning("using cached copy of %s", url)
            return old["body"]
        raise
    write_json(path, {"url": url, "t": iso(now_utc()), "body": body})
    return body
