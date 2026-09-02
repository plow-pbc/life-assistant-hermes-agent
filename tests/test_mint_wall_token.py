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


def test_the_staged_path_is_the_seam_the_sheet_uses(home, capsys, tmp_path):
    """The sheet tells the turn to stage its answers with the file tool and pass
    the path -- pi_address and pi_user are the owner's own words, and a heredoc
    composed around them is a command built out of their input. Every other test
    here drives stdin, so the path the sheet actually names needs its own.
    """
    dotenv, ld = home
    staged = tmp_path / "wall.json"
    staged.write_text(json.dumps({"pi_address": PI, "pi_user": "pi"}))
    assert mwt.main(argv=["--input", str(staged)],
                    dotenv_path=str(dotenv), ld_dir=str(ld)) == 0
    assert "DASHBOARD_ENDPOINT_URL" in dotenv.read_text()

    # A path that is not there is named, not swallowed: the turn staged it, so
    # a missing file means the staging failed and the operator needs to know.
    with pytest.raises(SystemExit) as refusal:
        mwt.main(argv=["--input", str(tmp_path / "absent.json")],
                 dotenv_path=str(dotenv), ld_dir=str(ld))
    assert "could not read" in str(refusal.value)

    # And argv is for the path and nothing else -- no answer ever rides it.
    with pytest.raises(SystemExit) as refusal:
        mwt.main(argv=["--pi-address", PI], dotenv_path=str(dotenv), ld_dir=str(ld))
    assert "usage:" in str(refusal.value)


def refuse(home, **payload):
    dotenv, ld = home
    with pytest.raises(SystemExit) as e:
        mwt.main(stdin=io.StringIO(json.dumps(payload)),
                 dotenv_path=str(dotenv), ld_dir=str(ld))
    assert dotenv.read_text() == SEED
    assert not ld.exists()
    return str(e.value)


def test_stdin_that_is_not_the_answer_object_refuses(home):
    """The two refusal branches of the stdin seam: non-JSON, and a key the
    contract does not name (a typo would otherwise be silently ignored)."""
    dotenv, ld = home
    with pytest.raises(SystemExit) as e:
        mwt.main(stdin=io.StringIO("not json"), dotenv_path=str(dotenv), ld_dir=str(ld))
    assert "not one JSON object" in str(e.value)
    assert dotenv.read_text() == SEED
    assert not ld.exists()
    assert "unknown keys" in refuse(home, pi_address=PI, pi_user="pi", pi_adress="typo")


def token_in(dotenv):
    (tok,) = re.findall(r"^DASHBOARD_TOKEN=(\S+)$", dotenv.read_text(), re.MULTILINE)
    return tok


def test_a_first_run_appends_three_lines_and_ships_the_token_in_two_files_only(home, capsys):
    dotenv, ld = home
    # user="so", not the default: proves pi_target and DASHBOARD_PI_USER carry
    # the ANSWERED login, indistinguishable from a hardcoded "pi" otherwise.
    rc, out = run(home, capsys, user="so")
    assert rc == 0
    tok = token_in(dotenv)
    assert len(tok) >= 32  # secrets.token_urlsafe(24)
    assert dotenv.read_text() == (
        SEED + f"\nDASHBOARD_ENDPOINT_URL=http://{PI}:5174/api/message\n"
        f"DASHBOARD_TOKEN={tok}\nDASHBOARD_DELIVERY=latch\nDASHBOARD_PI_USER=so\n"
    )
    assert f"pi_target=so@{PI}\n" in out
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


@pytest.mark.parametrize("bad", ["pi; rm -rf /", "raspberrypi.local/x", "10.0.0.5 --"],
                         ids=["semicolon", "slash", "space"])
def test_an_address_that_is_not_a_host_refuses_before_touching_anything(home, bad):
    """The address lands inside a URL in the dotenv and inside the curl the
    agent runs through Latch, so anything but [A-Za-z0-9.-] is refused by
    name, and nothing has been written when it is."""
    assert "pi address" in refuse(home, pi_address=bad, pi_user="pi")


@pytest.mark.parametrize("bad", ["collector.example", "134744072", "8.8.8.8", "raspberrypi"],
                         ids=["public-name", "decimal-ip", "public-ip", "bare-hostname"])
def test_a_public_address_refuses_the_bearer_stays_on_the_household_network(home, bad):
    """These pass the charset but fail the household gate: a public hostname,
    a dotless decimal that curl reads as 8.8.8.8, a public IP, and a bare
    hostname (refused not as off-network but because household_host has no
    bare-hostname fallback at all -- see its docstring). The wall's bearer
    rides every request to this host."""
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


def test_a_resume_with_no_answers_recovers_the_whole_install_state(home, capsys):
    """{} on stdin after a first run: the address comes back out of
    DASHBOARD_ENDPOINT_URL and the login out of DASHBOARD_PI_USER, so an
    unattended (cron) resume re-emits everything without re-asking the owner
    -- and without guessing an ssh login."""
    dotenv, ld = home
    run(home, capsys, user="so")
    after_first = dotenv.read_text()
    rc = mwt.main(stdin=io.StringIO("{}"), dotenv_path=str(dotenv), ld_dir=str(ld))
    out = capsys.readouterr().out
    assert rc == 0
    assert dotenv.read_text() == after_first
    assert f"already minted: DASHBOARD_ENDPOINT_URL=http://{PI}:5174/api/message" in out


def test_the_login_is_persisted_and_a_changed_one_converges_in_place(home, capsys):
    dotenv, ld = home
    run(home, capsys, user="so")
    assert "DASHBOARD_PI_USER=so\n" in dotenv.read_text()
    rc, out = run(home, capsys, user="sam")
    assert rc == 0
    assert dotenv.read_text().count("DASHBOARD_PI_USER") == 1
    assert "DASHBOARD_PI_USER=sam\n" in dotenv.read_text()
    assert "remembered: DASHBOARD_PI_USER=sam" in out


@pytest.mark.parametrize("missing,named", [("pi_address", "pi_address"), ("pi_user", "pi_user")])
def test_a_first_run_missing_an_answer_refuses_by_name_instead_of_guessing(home, missing, named):
    """With nothing in the dotenv to fall back on, the refusal names the one
    answer only the owner can supply -- a guessed ssh login (the old failure:
    pi@<address> against a Pi whose login is not pi) must never happen."""
    payload = {"pi_address": PI, "pi_user": "pi"}
    del payload[missing]
    assert named in refuse(home, **payload)


def test_a_pre_fix_dotenv_resumed_unattended_refuses_for_the_login_by_name(home):
    """The production upgrade scenario this commit exists for: an install
    minted before DASHBOARD_PI_USER existed (endpoint + token, no login),
    resumed with {} by a cron turn. The address recovers; the login cannot --
    the refusal names pi_user and nothing is written."""
    dotenv, ld = home
    seeded = SEED + f"\nDASHBOARD_ENDPOINT_URL=http://{PI}:5174/api/message\nDASHBOARD_TOKEN=tok_wall\n"
    dotenv.write_text(seeded)
    with pytest.raises(SystemExit) as e:
        mwt.main(stdin=io.StringIO("{}"), dotenv_path=str(dotenv), ld_dir=str(ld))
    assert "pi_user" in str(e.value)
    assert dotenv.read_text() == seeded
    assert not ld.exists()


@pytest.mark.parametrize("address,named", [
    ("192.168.1.50", "pi_user"),   # valid new address, no login: the remembered login belongs to the OLD Pi
    ("10.0.0.5 --", "pi address"),  # malformed: address validity surfaces first, not a login re-ask
    ("8.8.8.8", "household"),       # public: same ordering contract
], ids=["new-address", "malformed", "public"])
def test_a_repoint_without_a_login_refuses_naming_the_actual_problem(home, capsys, address, named):
    """One refusal matrix for a changed address arriving without pi_user:
    a valid new address asks for the new Pi's login (never silently pairs it
    with the old one -- the wrong-ssh-target bug this script prevents); a
    bad address surfaces its own error first. Always side-effect-free."""
    dotenv, ld = home
    run(home, capsys, user="so")
    before = dotenv.read_text()
    with pytest.raises(SystemExit) as e:
        mwt.main(stdin=io.StringIO(json.dumps({"pi_address": address})),
                 dotenv_path=str(dotenv), ld_dir=str(ld))
    assert named in str(e.value)
    if named != "pi_user":
        assert "pi_user" not in str(e.value)
    assert dotenv.read_text() == before


def test_a_mixed_case_address_converges_and_stays_idempotent_across_a_resume(home, capsys):
    """DNS is case-insensitive and urlsplit lowercases on recovery, so a
    mixed-case answer must not leave an endpoint that every later {} resume
    're-points' to its own lowercase twin."""
    dotenv, ld = home
    run(home, capsys, pi="RaspberryPi.LOCAL")
    after_first = dotenv.read_text()
    assert "DASHBOARD_ENDPOINT_URL=http://raspberrypi.local:5174/api/message\n" in after_first
    rc = mwt.main(stdin=io.StringIO("{}"), dotenv_path=str(dotenv), ld_dir=str(ld))
    out = capsys.readouterr().out
    assert rc == 0
    assert dotenv.read_text() == after_first
    assert "re-pointed" not in out


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



def test_two_mints_at_once_leave_one_token_in_both_files(home, capsys, run_concurrently):
    """The bearer is minted once and written twice -- pi.env and dashboard.hdr.

    The dotenv read decides whether one already exists, so two runs that both
    read "no" mint two: the Pi authenticates with one bearer and the Mac ships
    the other, every file looks correct, and the wall goes quiet. That is the
    loss the lock spans the read and both writes to prevent.
    """
    dotenv, ld = home

    def mint():
        return lambda: mwt.main(stdin=io.StringIO(json.dumps({"pi_address": PI, "pi_user": "pi"})),
                                dotenv_path=str(dotenv), ld_dir=str(ld))

    assert not run_concurrently(mint(), mint())
    capsys.readouterr()

    header = (ld / "dashboard.hdr").read_text().strip()
    token = header.split("Bearer ", 1)[1]
    assert f"DASHBOARD_TOKEN={token}" in (ld / "pi.env").read_text(), (
        "the Pi and the Mac are holding different bearers")
    assert (dotenv.read_text().count("DASHBOARD_TOKEN=")) == 1, (
        "the dotenv carries two tokens")


def test_a_staged_input_is_consumed_on_success_and_kept_on_refusal(home, tmp_path):
    """It can hold an ical_url and the Pi's address, so one left behind is a
    second copy of the owner's own words with no reader. Removed only on
    success: after a refusal the turn's next move is to fix what it staged."""
    dotenv, ld = home
    staged = tmp_path / "wall-a1b2c3d4.json"

    staged.write_text(json.dumps({"pi_address": PI, "pi_user": "pi"}))
    assert mwt.main(argv=["--input", str(staged)],
                    dotenv_path=str(dotenv), ld_dir=str(ld)) == 0
    assert not staged.exists(), "the staged answers outlived the mint"

    staged.write_text(json.dumps({"pi_address": "not a host", "pi_user": "pi"}))
    with pytest.raises(SystemExit):
        mwt.main(argv=["--input", str(staged)], dotenv_path=str(dotenv), ld_dir=str(ld))
    assert staged.exists(), "a refusal deleted the file the turn has to fix"


def test_an_input_that_is_a_file_this_writes_is_refused(home, tmp_path):
    """The staged file is consumed on success, so an input that IS the dotenv or
    one of the two private files would delete it under a success line."""
    dotenv, ld = home
    for target in (dotenv, ld / "pi.env", ld / "dashboard.hdr"):
        with pytest.raises(SystemExit) as refusal:
            mwt.main(argv=["--input", str(target)],
                     dotenv_path=str(dotenv), ld_dir=str(ld))
        assert "--input is a file this writes" in str(refusal.value)
    assert dotenv.read_text() == SEED, "the dotenv was touched"


def test_a_staged_input_that_cannot_be_removed_is_reported(home, tmp_path, monkeypatch):
    """Not swallowed, and not reported as a clean run either.

    The staged file can hold an ical_url and the Pi's address, so one left
    behind is a second copy of the owner's own words with no reader. But the
    private writes DID land -- the token is on disk in both files -- and a
    caller told only "could not remove" would re-run a mint that already
    succeeded. So the refusal carries both halves and exits non-zero.
    """
    dotenv, ld = home
    staged = tmp_path / "wall-a1b2c3d4.json"
    staged.write_text(json.dumps({"pi_address": PI, "pi_user": "pi"}))

    def refuse_remove(path):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(mwt.os, "remove", refuse_remove)

    with pytest.raises(SystemExit) as refusal:
        mwt.main(argv=["--input", str(staged)], dotenv_path=str(dotenv), ld_dir=str(ld))

    message = str(refusal.value)
    assert "could not remove the staged answers" in message
    assert "pi.env" in message and "dashboard.hdr" in message, (
        "the caller is not told the private writes landed")
    assert staged.exists(), "the file the message says to delete is gone"
    # And they really did land: this is a partial success, reported as one.
    assert (ld / "dashboard.hdr").read_text().startswith("Authorization: Bearer ")
    assert "DASHBOARD_TOKEN=" in (ld / "pi.env").read_text()
