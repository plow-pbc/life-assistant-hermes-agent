"""post_sports.py embeds every team logo in the sports tile.

The wall shows images from itself only, so a logo reaches the kiosk as a data:
URI inside the tile. These tests load the wrapper and swap its one network seam
(open_no_redirect) for a canned answer.
"""
import base64
import importlib.util
import io
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ld-shared" / "scripts"))
import post_to_kiosk  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"logo-bytes"
DATA_URI = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
SF = "https://a.espncdn.com/i/teamlogos/mlb/500/scoreboard/sf.png"
LAD = "https://a.espncdn.com/i/teamlogos/mlb/500/scoreboard/lad.png"


def load(monkeypatch, answer):
    """The wrapper, loaded fresh, with ESPN answering `answer` (bytes or an
    exception). Returns (module, fetched URLs)."""
    # Loading the wrapper sets constants on the shared module; pin the current
    # values so monkeypatch restores them for the helper's own tests.
    for name in ("MESSAGE_FILE", "CARD", "BODY_TYPE", "TITLE", "TRANSFORM"):
        monkeypatch.setattr(post_to_kiosk, name, getattr(post_to_kiosk, name))
    spec = importlib.util.spec_from_file_location("post_sports", ROOT / "ld-sports" / "scripts" / "post_sports.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fetched = []

    def fake_open(request, *, timeout):
        fetched.append(request.full_url)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(answer)

    monkeypatch.setattr(module, "open_no_redirect", fake_open)
    return module, fetched


def test_every_logo_is_embedded_at_tile_size(monkeypatch):
    sports, fetched = load(monkeypatch, PNG)
    tile = (
        f'<span class="sp-logo"><img src="{SF}" alt="SF"></span>'
        f'<span class="sp-logo"><img alt="LAD" src="{LAD}"></span>'
    )
    assert sports.embed_logos(tile) == (
        f'<span class="sp-logo"><img src="{DATA_URI}" alt="SF"></span>'
        f'<span class="sp-logo"><img alt="LAD" src="{DATA_URI}"></span>'
    )
    assert fetched == [
        "https://a.espncdn.com/combiner/i?img=%2Fi%2Fteamlogos%2Fmlb%2F500%2Fscoreboard%2Fsf.png&h=38&w=38",
        "https://a.espncdn.com/combiner/i?img=%2Fi%2Fteamlogos%2Fmlb%2F500%2Fscoreboard%2Flad.png&h=38&w=38",
    ]


def test_the_wrapper_posts_its_tile_through_embed_logos(monkeypatch):
    sports, _ = load(monkeypatch, PNG)
    assert post_to_kiosk.TRANSFORM is sports.embed_logos


@pytest.mark.parametrize("img, answer", [
    ('<img src="https://example.com/i/teamlogos/mlb/500/sf.png">', PNG),
    ('<img src="http://a.espncdn.com/i/teamlogos/mlb/500/sf.png">', PNG),
    ('<img src="https://a.espncdn.com/i/teamlogos/../../combiner/x.png">', PNG),
    ('<img data-src="https://a.espncdn.com/i/teamlogos/mlb/500/sf.png">', PNG),
    (f'<img src="{SF}">', urllib.error.HTTPError(SF, 404, "Not Found", {}, None)),
    (f'<img src="{SF}">', TimeoutError("timed out")),
    (f'<img src="{SF}">', b"<html>not a png</html>"),
    (f'<img src="{SF}">', PNG + b"x" * (64 * 1024)),
], ids=["other-host", "http", "traversal", "no-src", "404", "timeout", "not-png", "too-big"])
def test_a_logo_that_cannot_be_embedded_stops_the_run(monkeypatch, img, answer):
    sports, _ = load(monkeypatch, answer)
    with pytest.raises(SystemExit) as exit_:
        sports.embed_logos(img)
    assert str(exit_.value.code).startswith("error:")
