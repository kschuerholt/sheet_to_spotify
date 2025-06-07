"""
Skeleton: Collaborative Playlist Contributor Display & Sync
===========================================================
This standalone Python script demonstrates all the moving parts you need to

1. Read a **pre‑filled Google Sheet** that maps Spotify tracks → contributor names.
2. Push those tracks (once) into a chosen Spotify playlist.
3. Spin up a tiny Flask web server that shows the **currently‑playing track** and
   who added it, suitable for full‑screen display on an iPad / projector.

The emphasis is on structure—replace TODOs with your own glue code, secrets,
and error‑handling.  Designed for Python 3.11+, but works on 3.9+ with minimal
changes.

Dependencies (add these to requirements.txt):
--------------------------------------------
flask~=3.0.2
spotipy~=2.23.0
gspread~=6.1.0
google-auth~=2.29.0

Secrets / Environment
---------------------
Set the following environment variables **before** running:

SPOTIFY_CLIENT_ID      = "<your app client id>"
SPOTIFY_CLIENT_SECRET  = "<client secret>"
SPOTIFY_REDIRECT_URI   = "http://localhost:8888/callback"  # must match your app settings
SPOTIFY_PLAYLIST_ID    = "spotify:playlist:xxxxxxxxxxxx"
GOOGLE_SERVICE_JSON    = "/full/path/to/service_account.json"  # download from Google Cloud
GOOGLE_SHEET_ID        = "1AbCDeFGhiJkLmnopQRstuVWXYZ1234567890"
GOOGLE_SHEET_TAB       = "Form Responses 1"  # optional – default sheet/tab name

Run with:
$ python playlist_contributor_display.py
Then open http://localhost:5000 on the playback computer or any networked
device.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, List, Tuple

import flask
import gspread
import spotipy
from flask import Flask, render_template_string
from google.oauth2.service_account import Credentials
from spotipy.oauth2 import SpotifyOAuth

###############################################################################
# 1. Google Sheet → mapping dict
###############################################################################

def load_sheet_mapping(sheet_id: str, tab: str | None = None) -> Tuple[Dict[str, str], List[str]]:
    """Return (mapping, ordered_uri_list) from the given Google Sheet.

    Assumes each row looks like: Timestamp | Name | Track URI | Notes …
    The Track URI column may be a full `https://open.spotify.com/track/...` URL
    or a raw `spotify:track:` URI.
    """

    # OAuth using a service account – easier because no interactive login.
    creds = Credentials.from_service_account_file(os.environ["GOOGLE_SERVICE_JSON"], scopes=[
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ])
    client = gspread.authorize(creds)
    sheet = client.open_by_key(sheet_id)
    ws = sheet.worksheet(tab or sheet.sheet1.title)

    rows = ws.get_all_values()
    header = rows.pop(0)

    # naive header guess – customise as needed
    name_idx = header.index("Name")
    uri_idx = header.index("Track URI") if "Track URI" in header else header.index("Song link or URI")

    mapping: Dict[str, str] = {}
    ordered: List[str] = []
    for r in rows:
        contributor = r[name_idx].strip() or "Anonymous"
        raw_uri = r[uri_idx].strip()
        if not raw_uri:
            continue
        # normalise URL → URI
        uri = raw_uri.split("?")[0]
        if "open.spotify.com" in uri:
            # URL – extract the segment after /track/
            try:
                track_id = uri.split("/track/")[1].split("/")[0]
            except IndexError:
                continue  # malformed
            uri = f"spotify:track:{track_id}"
        mapping[uri] = contributor
        ordered.append(uri)

    return mapping, ordered

###############################################################################
# 2. Spotify helpers
###############################################################################

def init_spotify() -> spotipy.Spotify:
    auth = SpotifyOAuth(
        scope="playlist-modify-public playlist-modify-private user-read-playback-state",
        open_browser=False,
    )
    return spotipy.Spotify(auth_manager=auth)

def sync_playlist_once(sp: spotipy.Spotify, playlist_id: str, track_uris: List[str]) -> None:
    """Add any URIs missing from *playlist_id* in original order.
    Only runs once at startup – no rescans during runtime.
    """
    existing_uris: List[str] = []
    offset = 0
    while True:
        resp = sp.playlist_items(playlist_id, fields="items.track.uri,next", offset=offset, additional_types=["track"])
        existing_uris.extend([it["track"]["uri"] for it in resp["items"] if it.get("track")])
        if resp.get("next"):
            offset += len(resp["items"])
        else:
            break

    to_add = [uri for uri in track_uris if uri not in existing_uris]
    if not to_add:
        print("Playlist already up‑to‑date ✨")
        return

    print(f"Adding {len(to_add)} tracks → playlist …")
    CHUNK = 100  # Spotify limit per call
    for i in range(0, len(to_add), CHUNK):
        sp.playlist_add_items(playlist_id, to_add[i : i + CHUNK])
        time.sleep(0.2)  # be nice to the API
    print("Sync complete ✔️")

###############################################################################
# 3. Simple Flask display
###############################################################################

template = """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta http-equiv="refresh" content="10"> <!-- auto‑refresh every 10s -->
  <title>Now Playing</title>
  <style>
    html { font-family: system-ui, sans-serif; background:#111; color:#fefefe; text-align:center; }
    .track { font-size:4vw; margin:2vh 0; }
    .artist { font-size:3vw; opacity:0.8; }
    .contrib { font-size:2.5vw; margin-top:4vh; color:#0fa9e6; }
  </style>
</head>
<body>
  {% if track_name %}
    <div class="track">{{ track_name }}</div>
    <div class="artist">{{ artists }}</div>
    <div class="contrib">added by {{ contributor }}</div>
  {% else %}
    <p>Nothing playing right now …</p>
  {% endif %}
</body>
</html>"""

app = Flask(__name__)
SP: spotipy.Spotify | None = None
MAPPING: Dict[str, str] = {}

@app.route("/")
def show_now_playing():
    current = SP.current_playback(additional_types=["track"]) if SP else None
    if current and current.get("is_playing") and current.get("item"):
        item = current["item"]
        uri = item["uri"]
        contributor = MAPPING.get(uri, "someone")
        return render_template_string(
            template,
            track_name=item["name"],
            artists=", ".join(a["name"] for a in item["artists"]),
            contributor=contributor,
        )
    return render_template_string(template, track_name=None, artists=None, contributor=None)

###############################################################################
# 4. Bootstrap + run
###############################################################################

def main():
    global SP, MAPPING

    # 0. Validate env
    required = [
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
        "SPOTIFY_REDIRECT_URI",
        "SPOTIFY_PLAYLIST_ID",
        "GOOGLE_SERVICE_JSON",
        "GOOGLE_SHEET_ID",
    ]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise SystemExit(f"Missing env vars: {', '.join(missing)}")

    # 1. Load mapping once at startup
    print("Fetching contributor mapping from Google Sheet …")
    MAPPING, ordered_uris = load_sheet_mapping(
        os.environ["GOOGLE_SHEET_ID"], os.getenv("GOOGLE_SHEET_TAB")
    )
    print(f"Loaded {len(MAPPING)} contributions")

    # 2. Spotify auth + one‑time sync
    SP = init_spotify()
    sync_playlist_once(SP, os.environ["SPOTIFY_PLAYLIST_ID"], ordered_uris)

    # 3. Run Flask – 0.0.0.0 so LAN devices can open it
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
