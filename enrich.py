"""Look up each record in collection.csv on MusicBrainz and fetch its cover art.

Results are cached in mb_cache.json (keyed by "artist|title"), so re-running
only queries rows that are new or were edited. Covers land in covers/<image id>.jpg.
Rows with artist "?" are skipped; give them a crop instead (see crops.json).
"""

import csv
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = "vinyl-collection/0.1 ( https://github.com/RobGruhl/vinyl-collection )"
CACHE = Path("mb_cache.json")
COVERS = Path("covers")


def get(url, raw=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            return data if raw else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code != 503 or attempt == 4:
                raise
            time.sleep(2 * (attempt + 1))  # MusicBrainz throttling


def mb(path, **params):
    data = get(f"https://musicbrainz.org/ws/2/{path}?" + urllib.parse.urlencode({**params, "fmt": "json"}))
    time.sleep(1.1)  # MusicBrainz asks for <= 1 request/second
    return data


def norm(s):
    return "".join(c for c in s.lower() if c.isalnum())


def search(artist, title, soundtrack):
    q = f'releasegroup:"{title}"'
    if artist != "Various Artists":
        q += f' AND artist:"{artist}"'
    if soundtrack:
        q += " AND secondarytype:soundtrack"
    groups = [g for g in mb("release-group/", query=q, limit=10).get("release-groups", []) if g.get("score", 0) >= 90]
    exact = [g for g in groups if norm(g["title"]) == norm(title)]
    return (exact or groups or [None])[0]


def fetch_cover(mbid, out):
    """Release-group front image, else the first release under it that has one."""
    try:
        out.write_bytes(get(f"https://coverartarchive.org/release-group/{mbid}/front-500", raw=True))
        return True
    except urllib.error.HTTPError:
        pass
    for rel in mb("release", **{"release-group": mbid, "limit": 15}).get("releases", []):
        if rel.get("cover-art-archive", {}).get("front"):
            try:
                out.write_bytes(get(f'https://coverartarchive.org/release/{rel["id"]}/front-500', raw=True))
                return True
            except urllib.error.HTTPError:
                continue
    return False


def main():
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    COVERS.mkdir(exist_ok=True)
    rows = list(csv.DictReader(open("collection.csv")))
    for row in rows:
        if row["artist"] == "?" or row["title"].startswith("("):
            continue
        key = f'{row["artist"]}|{row["title"]}'
        if key not in cache:
            try:
                rg = search(row["artist"], row["title"], "Soundtrack" in row["notes"])
            except urllib.error.URLError as e:
                print(f"  ! search failed for {key}: {e}")
                continue
            cache[key] = {"mbid": rg["id"], "year": (rg.get("first-release-date") or "")[:4],
                          "mb_title": rg["title"]} if rg else {"mbid": None}
            CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
        hit = cache[key]
        stem = Path(row["image"]).stem
        out = COVERS / f"{stem}.jpg"
        if hit.get("mbid") and not out.exists() and not hit.get("no_art"):
            if not fetch_cover(hit["mbid"], out):
                hit["no_art"] = True
                CACHE.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
        status = "art" if out.exists() else ("no art" if hit.get("mbid") else "NO MATCH")
        print(f'{stem}  {status:8}  {hit.get("year", ""):4}  {key}  -> {hit.get("mb_title", "")}')


if __name__ == "__main__":
    main()
