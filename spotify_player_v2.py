from __future__ import annotations

import os
import time
from typing import Dict, List, Tuple, TYPE_CHECKING
if TYPE_CHECKING:
    from flask import Flask


# Mapping from contributor name to avatar/thumbnail path used in the web UI.
CONTRIBUTOR_IMAGES: Dict[str, str] = {}




def normalize_uri(raw: str) -> str:
    """Normalize a Spotify URL or URI to a spotify:track:.. URI."""
    uri = raw.strip().split("?")[0]
    if "open.spotify.com" in uri:
        try:
            track_id = uri.split("/track/")[1].split("/")[0]
        except IndexError:
            raise ValueError(f"Malformed track URL: {raw}")
        uri = f"spotify:track:{track_id}"
    return uri


def parse_sheet_rows(
    rows: List[List[str]],
    name_col: str = "name",
    link_col: str = "spotify_link",
) -> Tuple[Dict[str, str], List[str]]:
    """Parse sheet rows into a mapping of track URI -> contributor name."""
    if not rows:
        return {}, []

    header = [h.strip() for h in rows[0]]
    try:
        name_idx = header.index(name_col)
        link_idx = header.index(link_col)
    except ValueError as exc:
        raise KeyError(f"Missing expected column: {exc}") from exc

    mapping: Dict[str, str] = {}
    ordered: List[str] = []

    for row in rows[1:]:
        if len(row) <= max(name_idx, link_idx):
            continue
        contributor = row[name_idx].strip() or "Anonymous"
        link = row[link_idx].strip()
        if not link:
            continue
        try:
            uri = normalize_uri(link)
        except ValueError:
            continue
        mapping[uri] = contributor
        ordered.append(uri)
    return mapping, ordered


def load_sheet_mapping(
    sheet_id: str,
    tab: str | None = None,
    name_col: str = "name",
    link_col: str = "spotify_link",
    service_json: str | None = None,
    api_key: str | None = None,
) -> Tuple[Dict[str, str], List[str]]:
    """Fetch sheet rows using gspread and return mapping and order.

    Either ``service_json`` or ``api_key`` must be provided. ``service_json`` is
    used for service-account authentication while ``api_key`` allows reading
    public sheets without OAuth.
    """
    import gspread

    if service_json:
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_file(
            service_json,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets.readonly",
                "https://www.googleapis.com/auth/drive.readonly",
            ],
        )
        client = gspread.authorize(creds)
    elif api_key:
        client = gspread.auth.api_key(api_key)
    else:
        raise ValueError("service_json or api_key required")
    sheet = client.open_by_key(sheet_id)
    ws = sheet.worksheet(tab or sheet.sheet1.title)
    rows = ws.get_all_values()
    return parse_sheet_rows(rows, name_col=name_col, link_col=link_col)


def load_csv_mapping(
    path: str,
    name_col: str = "name",
    link_col: str = "spotify_link",
) -> Tuple[Dict[str, str], List[str]]:
    """Read mapping from a local CSV file."""
    import csv

    mapping: Dict[str, str] = {}
    ordered: List[str] = []

    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            contributor = (row.get(name_col) or "").strip() or "Anonymous"
            link = (row.get(link_col) or "").strip()
            if not link:
                continue
            try:
                uri = normalize_uri(link)
            except ValueError:
                continue
            mapping[uri] = contributor
            ordered.append(uri)
    return mapping, ordered


def load_spotify_creds(path: str) -> Tuple[str, str, str]:
    """Return ``(client_id, client_secret, redirect_uri)`` from JSON file."""
    import json

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    try:
        return data["client_id"], data["client_secret"], data["redirect_uri"]
    except KeyError as exc:
        raise KeyError(f"Missing key in {path}: {exc}") from exc


def init_spotify(creds_json: str | None = None) -> "spotipy.Spotify":
    """Initialise Spotipy using env vars or a credentials JSON file."""
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    cid = os.getenv("SPOTIFY_CLIENT_ID")
    secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect = os.getenv("SPOTIFY_REDIRECT_URI")

    if not (cid and secret and redirect):
        creds_json = creds_json or os.getenv("SPOTIFY_CREDS_JSON") or "spotify_creds.json"
        if os.path.exists(creds_json):
            cid, secret, redirect = load_spotify_creds(creds_json)

    if not (cid and secret and redirect):
        raise SystemExit("Spotify credentials missing: set env vars or SPOTIFY_CREDS_JSON")

    auth = SpotifyOAuth(
        client_id=cid,
        client_secret=secret,
        redirect_uri=redirect,
        scope="playlist-modify-public playlist-modify-private user-read-playback-state",
        open_browser=False,
    )
    return spotipy.Spotify(auth_manager=auth)


def sync_playlist_once(sp, playlist_id: str, track_uris: List[str]) -> None:
    """Add any URIs missing from *playlist_id* in original order."""
    existing: List[str] = []
    offset = 0
    while True:
        resp = sp.playlist_items(
            playlist_id,
            fields="items.track.uri,next",
            offset=offset,
            additional_types=["track"],
        )
        existing.extend(
            [it["track"]["uri"] for it in resp["items"] if it.get("track")]
        )
        if resp.get("next"):
            offset += len(resp["items"])
        else:
            break

    to_add = [uri for uri in track_uris if uri not in existing]
    if not to_add:
        print("Playlist already up-to-date ✨")
        return

    chunk = 100
    for i in range(0, len(to_add), chunk):
        sp.playlist_add_items(playlist_id, to_add[i : i + chunk])
        time.sleep(0.2)


template = """<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta http-equiv=\"refresh\" content=\"10\">
  <title>Now Playing</title>
  <style>
    html { font-family: system-ui, sans-serif; background:#111; color:#fefefe; text-align:center; }
    img.cover { width:45vw; margin-top:4vh; box-shadow:0 0 15px #000; }
    img.contrib { width:10vw; height:10vw; border-radius:50%; object-fit:cover; margin-top:2vh; }
    .track { font-size:4vw; margin:2vh 0; }
    .artist { font-size:3vw; opacity:0.8; }
    .contrib { font-size:2.5vw; margin-top:4vh; color:#0fa9e6; }
  </style>
</head>
<body>
  {% if track_name %}
    {% if cover_url %}<img class=\"cover\" src=\"{{ cover_url }}\" alt=\"cover\" />{% endif %}
    <div class=\"track\">{{ track_name }}</div>
    <div class=\"artist\">{{ artists }}</div>
    <div class=\"contrib\">
      {% if contrib_img %}<img class=\"contrib\" src=\"{{ contrib_img }}\" alt=\"{{ contributor }}\" />{% endif %}
      added by {{ contributor }}
    </div>
  {% else %}
    <p>Nothing playing right now …</p>
  {% endif %}
</body>
</html>"""


def create_app(
    sp,
    mapping: Dict[str, str],
    contributor_image_mapping: Dict[str, str] | None = None,
) -> "Flask":
    from flask import Flask, render_template_string
    app = Flask(__name__)
    contrib_imgs = contributor_image_mapping or {}

    @app.route("/")
    def show_now_playing():
        current = sp.current_playback(additional_types=["track"]) if sp else None
        if current and current.get("is_playing") and current.get("item"):
            item = current["item"]
            uri = item["uri"]
            contributor = mapping.get(uri, "someone")
            images = item.get("album", {}).get("images") or []
            cover = images[0]["url"] if images else None
            return render_template_string(
                template,
                track_name=item["name"],
                artists=", ".join(a["name"] for a in item["artists"]),
                contributor=contributor,
                cover_url=cover,
                contrib_img=contrib_imgs.get(contributor),
            )
        return render_template_string(
            template,
            track_name=None,
            artists=None,
            contributor=None,
            cover_url=None,
            contrib_img=None,
        )

    return app


def main() -> None:
    required = ["SPOTIFY_PLAYLIST_ID"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise SystemExit(f"Missing env vars: {', '.join(missing)}")

    csv_path = os.getenv("CSV_PATH")
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    service_json = os.getenv("GOOGLE_SERVICE_JSON")
    api_key = os.getenv("GOOGLE_API_KEY")

    if csv_path:
        mapping, ordered = load_csv_mapping(csv_path, "name", "spotify_link")
    else:
        if not sheet_id:
            raise SystemExit("Specify GOOGLE_SHEET_ID or CSV_PATH")
        if not (service_json or api_key):
            raise SystemExit("Provide GOOGLE_SERVICE_JSON or GOOGLE_API_KEY")
        mapping, ordered = load_sheet_mapping(
            sheet_id,
            os.getenv("GOOGLE_SHEET_TAB"),
            "name",
            "spotify_link",
            service_json=service_json,
            api_key=api_key,
        )
    sp = init_spotify(os.getenv("SPOTIFY_CREDS_JSON"))
    sync_playlist_once(sp, os.environ["SPOTIFY_PLAYLIST_ID"], ordered)
    app = create_app(sp, mapping, CONTRIBUTOR_IMAGES)
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
