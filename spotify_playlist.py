"""Build a Spotify playlist of every album in docs/collection.json, full albums, artist A-Z.

Setup: create an app at https://developer.spotify.com/dashboard with redirect URI
http://127.0.0.1:8888/callback, then pass its Client ID (PKCE login, no secret needed):

  poetry run python spotify_playlist.py --client-id ID --dry-run   # show album matches only
  poetry run python spotify_playlist.py --client-id ID             # create the playlist

Album matches are cached in spotify_matches.json; set an entry to a Spotify album ID to
override a wrong match, to a list of track IDs to add just those tracks, or to null to
leave that record out.
"""

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

REDIRECT = "http://127.0.0.1:8888/callback"
SCOPES = "playlist-modify-public playlist-modify-private"
TOKEN = Path(".spotify_token.json")
MATCHES = Path("spotify_matches.json")
API = "https://api.spotify.com/v1"


def login(client_id):
    if TOKEN.exists():
        tok = json.loads(TOKEN.read_text())
        if tok["expires_at"] > time.time() + 60:
            return tok["access_token"]
        if tok.get("refresh_token"):
            return save_token(token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                             "client_id": client_id}), tok["refresh_token"])

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT, "scope": SCOPES,
        "code_challenge_method": "S256", "code_challenge": challenge, "state": state})

    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Spotify connected. You can close this tab.</h2>")

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 8888), Handler)
    print("Opening Spotify login in your browser...", flush=True)
    webbrowser.open(url)
    while "code" not in got and "error" not in got:
        server.handle_request()
    if got.get("error") or got.get("state") != state:
        raise SystemExit(f"Spotify login failed: {got}")
    return save_token(token_request({"grant_type": "authorization_code", "code": got["code"],
                                     "redirect_uri": REDIRECT, "client_id": client_id, "code_verifier": verifier}))


def token_request(form):
    req = urllib.request.Request("https://accounts.spotify.com/api/token", data=urllib.parse.urlencode(form).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def save_token(tok, old_refresh=None):
    tok["expires_at"] = time.time() + tok["expires_in"]
    tok.setdefault("refresh_token", old_refresh)
    TOKEN.write_text(json.dumps(tok))
    TOKEN.chmod(0o600)
    return tok["access_token"]


def api(token, method, path, body=None, raise_http=False):
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode() if body is not None else None
    for _ in range(5):
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After", "2")) + 1)
                continue
            if raise_http:
                raise
            raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:300]}")
    raise SystemExit(f"{method} {path}: rate limited too many times")


def norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


def find_album(token, artist, title, fmt):
    va = artist == "Various Artists"
    queries = [f"album:{title}" + ("" if va else f" artist:{artist}"),
               f"{title} soundtrack" if va else f"{artist} {title}",
               title]
    single_ok = fmt not in ("LP",)

    def artist_ok(a):
        if va:
            return a["album_type"] == "compilation" or any(x["name"] == "Various Artists" for x in a["artists"])
        return any(norm(artist) == norm(x["name"]) for x in a["artists"]) or norm(artist) == norm(", ".join(x["name"] for x in a["artists"]))

    for q in queries:
        items = api(token, "GET", "/search?" + urllib.parse.urlencode({"q": q, "type": "album", "limit": 10}))["albums"]["items"]
        cands = [a for a in items if a and artist_ok(a) and (single_ok or a["total_tracks"] >= 5)
                 and (norm(title) in norm(a["name"]) or norm(a["name"]) in norm(title))]
        if cands:
            # exact title first, then full albums, then fewest extra words (skips deluxe/remaster variants)
            pick = min(cands, key=lambda a: (norm(a["name"]) != norm(title), a["album_type"] != "album",
                                             len(norm(a["name"])) - len(norm(title))))
            return {"id": pick["id"], "name": pick["name"], "artist": ", ".join(x["name"] for x in pick["artists"]),
                    "tracks": pick["total_tracks"]}
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--name", default="Our Record Collection")
    args = ap.parse_args()

    token = login(args.client_id)
    records = json.loads(Path("docs/collection.json").read_text())
    the = lambda s: (s or "").lower().removeprefix("the ")
    records.sort(key=lambda r: (the(r["artist"]), r["year"] or "", the(r["title"])))
    matches = json.loads(MATCHES.read_text()) if MATCHES.exists() else {}

    for r in records:
        key = f'{r["artist"]}|{r["title"]}'
        if key not in matches:
            matches[key] = find_album(token, r["artist"], r["title"], r["format"])
            MATCHES.write_text(json.dumps(matches, indent=1, ensure_ascii=False))
        m = matches[key]
        if isinstance(m, (str, list)):  # hand override: album ID or list of track IDs
            print(f"  override  {key}")
        elif m:
            flag = "" if norm(m["name"]) == norm(r["title"]) else "   <-- check"
            print(f'  ok        {key}  ->  {m["artist"]} — {m["name"]} ({m["tracks"]} tracks){flag}')
        else:
            print(f"  MISSING   {key}")

    picks = list(dict.fromkeys(json.dumps(m if isinstance(m, (str, list)) else m["id"]) for r in records
                               if (m := matches.get(f'{r["artist"]}|{r["title"]}'))))  # dedupe, keep order
    print(f"\n{len(picks)} of {len(records)} records found on Spotify")
    if args.dry_run:
        return

    uris = []
    for pick in map(json.loads, picks):
        if isinstance(pick, list):
            uris += [f"spotify:track:{t}" for t in pick]
            continue
        url = f"/albums/{pick}/tracks?limit=50"
        while url:
            page = api(token, "GET", url)
            uris += [t["uri"] for t in page["items"]]
            url = page.get("next")
    pl = api(token, "POST", "/me/playlists", {
        "name": args.name, "public": True,
        "description": "Every record on our shelf, full albums, artist A–Z. Browse the covers: https://robgruhl.github.io/vinyl-collection/"})
    endpoint = "items"  # newer name; older apps use /tracks
    for i in range(0, len(uris), 100):
        try:
            api(token, "POST", f'/playlists/{pl["id"]}/{endpoint}', {"uris": uris[i:i + 100]}, raise_http=True)
        except urllib.error.HTTPError:
            if endpoint == "tracks":
                raise
            endpoint = "tracks"
            api(token, "POST", f'/playlists/{pl["id"]}/{endpoint}', {"uris": uris[i:i + 100]})
    print(f"{len(uris)} tracks added")
    print(pl["external_urls"]["spotify"])


if __name__ == "__main__":
    main()
