import pytest

from spotify_player_v2 import (
    parse_sheet_rows,
    sync_playlist_once,
    load_csv_mapping,
    create_app,
    load_spotify_creds,
)


def test_parse_sheet_rows_and_normalize():
    rows = [
        ["name", "spotify_link"],
        ["Alice", "https://open.spotify.com/track/123?si=abc"],
        ["Bob", "spotify:track:456"],
        ["", "spotify:track:789"],
    ]
    mapping, ordered = parse_sheet_rows(rows, name_col="name", link_col="spotify_link")
    assert mapping == {
        "spotify:track:123": "Alice",
        "spotify:track:456": "Bob",
        "spotify:track:789": "Anonymous",
    }
    assert ordered == [
        "spotify:track:123",
        "spotify:track:456",
        "spotify:track:789",
    ]


def test_sync_playlist_once_adds_missing_tracks():
    class DummySpotify:
        def __init__(self, existing):
            self.existing = existing
            self.added = []

        def playlist_items(self, playlist_id, fields=None, offset=0, additional_types=None):
            return {
                "items": [{"track": {"uri": uri}} for uri in self.existing],
                "next": None,
            }

        def playlist_add_items(self, playlist_id, uris):
            self.added.extend(uris)

    sp = DummySpotify(["spotify:track:1"])
    sync_playlist_once(sp, "pid", ["spotify:track:1", "spotify:track:2", "spotify:track:3"])
    assert sp.added == ["spotify:track:2", "spotify:track:3"]


def test_sync_playlist_once_noop_when_up_to_date(capsys):
    class DummySpotify:
        def __init__(self, existing):
            self.existing = existing
            self.added = []

        def playlist_items(self, playlist_id, fields=None, offset=0, additional_types=None):
            return {
                "items": [{"track": {"uri": uri}} for uri in self.existing],
                "next": None,
            }

        def playlist_add_items(self, playlist_id, uris):
            self.added.extend(uris)

    sp = DummySpotify(["spotify:track:1"])
    sync_playlist_once(sp, "pid", ["spotify:track:1"])
    assert sp.added == []


def test_load_csv_mapping(tmp_path):
    csv_content = (
        "name,spotify_link\n"
        "Alice,https://open.spotify.com/track/111\n"
        "Bob,spotify:track:222\n"
        ",spotify:track:333\n"
    )
    p = tmp_path / "tracks.csv"
    p.write_text(csv_content)

    mapping, ordered = load_csv_mapping(str(p))

    assert mapping == {
        "spotify:track:111": "Alice",
        "spotify:track:222": "Bob",
        "spotify:track:333": "Anonymous",
    }
    assert ordered == [
        "spotify:track:111",
        "spotify:track:222",
        "spotify:track:333",
    ]


def test_create_app_renders_images():
    class DummySpotify:
        def current_playback(self, additional_types=None):
            return {
                "is_playing": True,
                "item": {
                    "uri": "spotify:track:111",
                    "name": "Song",
                    "artists": [{"name": "Art"}],
                    "album": {"images": [{"url": "http://cover"}]},
                },
            }

    mapping = {"spotify:track:111": "Alice"}
    contrib = {"Alice": "/imgs/a.png"}
    app = create_app(DummySpotify(), mapping, contrib)
    with app.test_client() as c:
        data = c.get("/").get_data(as_text=True)
        assert "http://cover" in data
        assert "/imgs/a.png" in data


def test_load_spotify_creds(tmp_path):
    p = tmp_path / "creds.json"
    p.write_text('{"client_id":"id","client_secret":"sec","redirect_uri":"uri"}')
    cid, sec, uri = load_spotify_creds(str(p))
    assert (cid, sec, uri) == ("id", "sec", "uri")
