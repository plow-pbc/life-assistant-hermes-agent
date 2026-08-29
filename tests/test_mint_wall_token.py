"""The wall's token is minted once, shipped only in files, and never printed.

Behaviour, not shape: the dotenv gains exactly three lines with the leading
newline (the fixture's dotenv deliberately ends WITHOUT one, so a bare append
would splice onto PLOW_CHAT_TOKEN and take the instance off its chat), a
second run appends nothing, the two files the agent ships are mode 600, and
the token appears in those files and nowhere on stdout.
"""
import importlib.util
import io
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "mint_wall_token", ROOT / "ld-setup" / "scripts" / "mint_wall_token.py")
mwt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mwt)

PI = "raspberrypi.local"
SEED = "PLOW_CHAT_TOKEN=tok_chat"  # no trailing newline, on purpose
ICAL = "https://calendar.google.com/calendar/ical/secret%40group.calendar.google.com/private-abc123/basic.ics"


@pytest.fixture
def home(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(SEED)
    return dotenv, tmp_path / "ld"


def run(home, capsys, pi=PI, user="pi", ical=None):
    """Feed the answers the way the sheet does: one JSON object on stdin.
    ical=None leaves the key out (keep what pi.env holds)."""
    dotenv, ld = home
    payload = {"pi_address": pi, "pi_user": user}
    if ical is not None:
        payload["ical_url"] = ical
    rc = mwt.main(stdin=io.StringIO(json.dumps(payload)),
                  dotenv_path=str(dotenv), ld_dir=str(ld))
    return rc, capsys.readouterr().out


def refuse(home, **payload):
    dotenv, ld = home
    with pytest.raises(SystemExit) as e:
        mwt.main(stdin=io.StringIO(json.dumps(payload)),
                 dotenv_path=str(dotenv), ld_dir=str(ld))
    assert dotenv.read_text() == SEED
    assert not ld.exists()
    return str(e.value)


def token_in(dotenv):
    (tok,) = re.findall(r"^DASHBOARD_TOKEN=(\S+)$", dotenv.read_text(), re.MULTILINE)
    return tok


def test_a_first_run_appends_three_lines_and_ships_the_token_in_two_files_only(home, capsys):
    dotenv, ld = home
    rc, out = run(home, capsys)
    assert rc == 0
    tok = token_in(dotenv)
    assert len(tok) >= 32  # secrets.token_urlsafe(24)
    assert dotenv.read_text() == (
        SEED + f"\nDASHBOARD_ENDPOINT_URL=http://{PI}:5174/api/message\n"
        f"DASHBOARD_TOKEN={tok}\nDASHBOARD_DELIVERY=latch\n"
    )
    assert (ld / "pi.env").read_text() == f"ICAL_URL=\nDASHBOARD_TOKEN={tok}\n"
    assert (ld / "dashboard.hdr").read_text() == f"Authorization: Bearer {tok}\n"
    for name in ("pi.env", "dashboard.hdr"):
        assert oct((ld / name).stat().st_mode & 0o777) == "0o600", name
    assert tok not in out
    # Bare key=value lines, nothing shell-wrapped: the agent lifts each value
    # straight into an ssh argv element. apt-get, not apt (whose "WARNING:
    # ... stable CLI interface" the skill reads as a failed phase); `sudo env`
    # so env_reset cannot drop the frontend; the template repo positionally,
    # which bootstrap.sh requires (${1:?usage}).
    assert ("pi_line_1=sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y"
            " nodejs npm git chromium fonts-noto-color-emoji\n") in out
    assert ("pi_line_2=curl -fsSL https://raw.githubusercontent.com/plow-pbc/life-dashboard/main/updater/bootstrap.sh"
            " | sh -s -- https://github.com/plow-pbc/life-dashboard.git\n") in out


def test_a_second_run_appends_nothing_re_ships_the_same_token_and_still_never_prints_it(home, capsys):
    """The Pi holds the first token; a re-mint would lock the producers out."""
    dotenv, ld = home
    run(home, capsys)
    after_first = dotenv.read_text()
    tok = token_in(dotenv)
    (ld / "dashboard.hdr").unlink()  # a lost /opt/data/ld/ still has something to ship after a re-run
    rc, out = run(home, capsys)
    assert rc == 0
    assert dotenv.read_text() == after_first
    assert (ld / "dashboard.hdr").read_text() == f"Authorization: Bearer {tok}\n"
    assert (ld / "pi.env").read_text() == f"ICAL_URL=\nDASHBOARD_TOKEN={tok}\n"
    assert tok not in out
    assert f"already minted: DASHBOARD_ENDPOINT_URL=http://{PI}:5174/api/message" in out
    assert "pi_line_1=" in out and "pi_line_2=" in out


def test_a_new_address_re_points_the_endpoint_and_keeps_the_token(home, capsys):
    """A replaced Pi must receive the cards: the endpoint line converges to the
    address the owner gave THIS run, while the token -- which the new Pi gets
    via pi.env in Phase 3 -- is never re-minted."""
    dotenv, ld = home
    run(home, capsys)
    tok = token_in(dotenv)
    rc, out = run(home, capsys, pi="192.168.1.50")
    assert rc == 0
    assert "DASHBOARD_ENDPOINT_URL=http://192.168.1.50:5174/api/message\n" in dotenv.read_text()
    assert f"http://{PI}:" not in dotenv.read_text()
    assert token_in(dotenv) == tok
    assert "re-pointed: DASHBOARD_ENDPOINT_URL=http://192.168.1.50:5174/api/message" in out
    assert tok not in out


@pytest.mark.parametrize("bad", ["pi; rm -rf /", "raspberrypi.local/x", "10.0.0.5 --", ""],
                         ids=["semicolon", "slash", "space", "empty"])
def test_an_address_that_is_not_a_host_refuses_before_touching_anything(home, bad):
    """The address lands inside a URL in the dotenv and inside the curl the
    agent runs through Latch, so anything but [A-Za-z0-9.-] is refused by
    name, and nothing has been written when it is."""
    assert "pi address" in refuse(home, pi_address=bad, pi_user="pi")


@pytest.mark.parametrize("bad", ["collector.example", "134744072", "8.8.8.8", "raspberrypi"],
                         ids=["public-name", "decimal-ip", "public-ip", "bare-hostname"])
def test_a_public_address_refuses_the_bearer_stays_on_the_household_network(home, bad):
    """These pass the charset but reach off the household network: a public
    hostname, a dotless decimal that curl reads as 8.8.8.8, and a public IP.
    The wall's bearer rides every request to this host, so all refuse."""
    assert "household" in refuse(home, pi_address=bad, pi_user="pi")


def test_an_omitted_ical_url_keeps_the_feed_already_in_pi_env(home, capsys):
    """Omitted is not blank: the idempotent re-run the sheet prescribes must
    not erase the feed a later re-point or Pi rebuild ships."""
    dotenv, ld = home
    run(home, capsys, ical=ICAL)
    rc, out = run(home, capsys)
    assert rc == 0
    tok = token_in(dotenv)
    assert (ld / "pi.env").read_text() == f"ICAL_URL={ICAL}\nDASHBOARD_TOKEN={tok}\n"
    assert ICAL not in out


@pytest.mark.parametrize("seed_delivery", ["", "DASHBOARD_DELIVERY=direct\n"],
                         ids=["absent", "direct"])
def test_a_pre_latch_dotenv_converges_delivery_to_latch(home, capsys, seed_delivery):
    """A direct-POST install re-run through this setup keeps its token and
    endpoint but gains DASHBOARD_DELIVERY=latch -- without it every producer
    would POST directly at a Pi only the Mac can reach. An absent key is
    appended; a direct one is rewritten in place, never duplicated."""
    dotenv, ld = home
    dotenv.write_text(SEED + f"\nDASHBOARD_ENDPOINT_URL=http://{PI}:5174/api/message\n"
                      "DASHBOARD_TOKEN=tok_wall\n" + seed_delivery)
    rc, out = run(home, capsys)
    assert rc == 0
    assert dotenv.read_text().count("DASHBOARD_DELIVERY") == 1
    assert "DASHBOARD_DELIVERY=latch\n" in dotenv.read_text()
    assert "converged: DASHBOARD_DELIVERY=latch" in out


@pytest.mark.parametrize("bad", ["sam odio", "pi;id", "-oProxyCommand=x"],
                         ids=["space", "semicolon", "dash-option"])
def test_a_login_that_is_not_a_user_refuses_before_touching_anything(home, bad):
    """pi_user rides an ssh argv element in Phase 3; the interview's charset
    rule is enforced here in code so a missed refusal there still cannot put
    shell-relevant characters on the Mac."""
    assert "pi user" in refuse(home, pi_address=PI, pi_user=bad)


def test_an_endpoint_without_its_token_refuses_rather_than_shipping_a_blank(home, capsys):
    """Half a dotenv (someone deleted the TOKEN line) must not produce a
    pi.env with DASHBOARD_TOKEN= blank -- that disables the Pi's API."""
    dotenv, ld = home
    dotenv.write_text(SEED + f"\nDASHBOARD_ENDPOINT_URL=http://{PI}:5174/api/message\n")
    with pytest.raises(SystemExit) as e:
        mwt.main(stdin=io.StringIO(json.dumps({"pi_address": PI, "pi_user": "pi"})),
                 dotenv_path=str(dotenv), ld_dir=str(ld))
    assert "DASHBOARD_TOKEN" in str(e.value)
    assert not ld.exists()


@pytest.mark.parametrize(("ical", "expected"), [
    (ICAL, ICAL),
    (None, ""),
], ids=["given", "omitted"])
def test_ical_url_lands_only_in_pi_env(home, capsys, ical, expected):
    """The Pi's calendar tile reads the feed URL directly from pi.env; it is
    a private feed, so it must never appear on stdout. Omitted leaves the
    line blank."""
    dotenv, ld = home
    rc, out = run(home, capsys, ical=ical)
    assert rc == 0
    tok = token_in(dotenv)
    assert (ld / "pi.env").read_text() == f"ICAL_URL={expected}\nDASHBOARD_TOKEN={tok}\n"
    assert ICAL not in out
