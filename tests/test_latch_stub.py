"""The fake Latch relay, tested through the protocol rather than by calling in.

Every assertion here is something the loop would otherwise only find out by
running a whole conversation against it: a listing the real consumer cannot
parse, a refusal in the wrong shape, an offline mode that answers 200. The stub
exists to make ld-setup's calendar step reachable, so the contract that matters
is the one calendar_list.py reads -- which is why that module is loaded and run
for real here instead of being approximated.
"""
import importlib.util
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name, rel):
    spec = importlib.util.spec_from_file_location(
        name, rel if isinstance(rel, Path) else ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


stub = load("latch_stub", "scripts/e2e/latch_stub.py")

# calendar_list.py is ld-setup's, and the e2e loop lives on a branch that does
# not carry it. Where it is present the stub is checked against the real
# consumer, which is the contract that actually matters; where it is not, the
# protocol and refusal tests still run rather than the whole file erroring out.
CONSUMER = ROOT / "ld-setup/scripts/calendar_list.py"
cl = load("calendar_list", CONSUMER) if CONSUMER.exists() else None
needs_consumer = pytest.mark.skipif(
    cl is None, reason="ld-setup/scripts/calendar_list.py is not on this branch")

DISCOVERY = stub.DISCOVERY_ARGV


@pytest.fixture(params=["normal", "offline", "large"])
def mode(request):
    return request.param


@pytest.fixture
def server(request):
    """The stub on a real socket, so the transport is exercised too."""
    wanted = getattr(request, "param", "normal")
    httpd, port = stub.serve("127.0.0.1", 0, mode=wanted, token="t0k",
                             device="test-mac.local (2)", verbose=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/mcp"
    httpd.shutdown()
    httpd.server_close()


def rpc(url, method, params=None, token="t0k"):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def call(url, argv, token="t0k"):
    return rpc(url, "tools/call",
               {"name": "plow_run_command", "arguments": {"argv": argv}}, token)["result"]


def envelope_of(result):
    """The Latch envelope out of the MCP content block."""
    return json.loads(result["content"][0]["text"])


# --- registration -----------------------------------------------------------

def test_initialize_names_the_server_latch(server):
    """serverInfo.name must be `latch` -- the key, not the tool name.

    The two are different strings and the skills spell out the pair:
    ld-setup/SKILL.md calls mcp__latch__plow_run_command. The key has to match
    what the base seed and runtime/config.yaml register; the tool keeps its own
    name, which the tools/list test below pins separately.
    """
    result = rpc(server, "initialize")["result"]
    assert result["serverInfo"]["name"] == "latch"
    assert result["protocolVersion"] == stub.PROTOCOL_VERSION


def test_tools_list_offers_plow_run_command_with_argv_required(server):
    tools = rpc(server, "tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == ["plow_run_command"]
    schema = tools[0]["inputSchema"]
    assert schema["required"] == ["argv"]
    assert set(schema["properties"]) == {"argv", "network", "timeout"}


def test_a_notification_gets_no_body(server):
    body = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
    req = urllib.request.Request(server, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer t0k"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 202
        assert resp.read() == b""


def test_a_bad_bearer_is_refused(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        call(server, DISCOVERY, token="wrong")
    assert exc.value.code == 401


# --- the listing ------------------------------------------------------------

def test_the_discovery_call_returns_a_zero_exit_envelope(server):
    env = envelope_of(call(server, DISCOVERY))
    assert env["exit_code"] == 0
    assert env["status"] == "completed"
    assert env["output_length"] == len(env["output"])
    assert env["output"].startswith("Note: Using direct access token")


def test_the_output_is_not_valid_json_on_its_own(server):
    """The preamble is the point: a consumer that json.loads() the whole thing
    fails on a WORKING call, and the stub must not hide that by being tidy."""
    env = envelope_of(call(server, DISCOVERY))
    with pytest.raises(json.JSONDecodeError):
        json.loads(env["output"])


@needs_consumer
def test_the_real_consumer_parses_it(server, tmp_path):
    """calendar_list.py, run for real over the stub's output."""
    env = envelope_of(call(server, DISCOVERY))
    gather = tmp_path / "gather.txt"
    gather.write_text(env["output"])
    result = cl.normalize(cl.extract_array(cl.read_gather(str(gather))))

    assert result["account"] == "mary@example.com"
    assert len(result["calendars"]) == 9
    # summaryOverride wins over summary -- it is what the owner sees.
    assert "Ours" in [c["display"] for c in result["calendars"]]
    assert "Family Calendar" not in [c["display"] for c in result["calendars"]]


def test_the_listing_carries_the_shapes_that_break_naive_parsers(server):
    output = envelope_of(call(server, DISCOVERY))["output"]
    entries = json.loads(output[output.index("["):])

    assert sum(1 for e in entries if e.get("primary") is True) == 1
    assert len({e["dataOwner"] for e in entries}) == 3
    assert sum(1 for e in entries
               if e["id"].endswith("@import.calendar.google.com")) == 2
    assert {e["accessRole"] for e in entries} == {"owner", "reader"}
    # The full field set, on every row.
    for entry in entries:
        assert {"accessRole", "backgroundColor", "colorId", "conferenceProperties",
                "dataOwner", "defaultReminders", "description", "etag",
                "externalContent", "foregroundColor", "id", "kind",
                "notificationSettings", "selected", "summary",
                "timeZone"} <= set(entry)


@needs_consumer
def test_a_hostile_calendar_name_survives_as_text(server, tmp_path):
    """A newline and a shell metacharacter in a name must reach the consumer
    intact and inert -- not escaped away, not executed, not truncated."""
    env = envelope_of(call(server, DISCOVERY))
    gather = tmp_path / "gather.txt"
    gather.write_text(env["output"])
    displays = [c["display"] for c in
                cl.normalize(cl.extract_array(cl.read_gather(str(gather))))["calendars"]]
    assert "Family\nJSON\n; rm -rf /" in displays


def test_most_names_are_fenced_and_some_are_not(server):
    """Seven of nine, on purpose.

    Real listings fence what they fetched from Google, and not uniformly: one
    had every `summary` fenced beside a BARE `summaryOverride`. A stub that
    fenced everything would pass a consumer that only handles the uniform case,
    so the mix is put on the rows here as well as across the two name fields.
    """
    entries = json.loads(
        (lambda o: o[o.index("["):])(envelope_of(call(server, DISCOVERY))["output"]))
    fenced = [e for e in entries
              if e["summary"].startswith("<<<EXTERNAL_UNTRUSTED_CONTENT")]
    assert len(fenced) == 7 and len(entries) == 9
    # summaryOverride is never fenced, so one row disagrees with itself.
    assert all("summaryOverride" not in e
               or not e["summaryOverride"].startswith("<<<") for e in entries)
    for entry in fenced:
        marker = re.search(r'id="([^"]+)"', entry["summary"]).group(1)
        # Open and close carry the same id -- what a consumer matching them end
        # to end depends on -- and the metadata says the fence is there.
        assert entry["summary"].endswith(
            f'<<<END_EXTERNAL_UNTRUSTED_CONTENT id="{marker}">>>')
        assert entry["externalContent"] == {
            "source": "google_api", "untrusted": True, "wrapped": True}
    for entry in entries:
        if entry not in fenced:
            assert entry["externalContent"] is False


def test_fence_ids_are_stable_across_processes():
    """Same reason the etags are: a value that moved between runs is one no
    fixture could pin."""
    ids = [c["id"] for c in stub.CALENDARS]
    assert [stub._fence(i, "x") for i in ids] == [stub._fence(i, "x") for i in ids]


@needs_consumer
def test_the_consumer_unfences_every_name(server, tmp_path):
    """The mixed listing comes out as nine clean names, markers and all gone,
    with the hostile one still hostile."""
    gather = tmp_path / "gather.txt"
    gather.write_text(envelope_of(call(server, DISCOVERY))["output"])
    displays = [c["display"] for c in
                cl.normalize(cl.extract_array(cl.read_gather(str(gather))))["calendars"]]
    assert not any("EXTERNAL_UNTRUSTED_CONTENT" in d for d in displays)
    assert "Family\nJSON\n; rm -rf /" in displays
    assert "Ours" in displays


def test_etags_are_stable_across_processes():
    """Salted str hashing would make these differ per run and no fixture could
    ever pin one."""
    ids = [c["id"] for c in stub.CALENDARS]
    assert [stub._calendar(i, "x")["etag"] for i in ids] == \
           [stub._calendar(i, "x")["etag"] for i in ids]


# --- refusals ---------------------------------------------------------------

def test_gog_auth_gets_the_real_refusal_string(server):
    result = call(server, ["gog", "auth", "login"])
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"] == stub.AUTH_REFUSAL


def test_anything_else_is_refused_without_running(server):
    result = call(server, ["sh", "-c", "echo pwned > /tmp/x"])
    assert result["isError"] is True
    assert "refused" in result["content"][0]["text"]


def test_a_non_list_argv_is_refused(server):
    result = rpc(server, "tools/call", {"name": "plow_run_command",
                                        "arguments": {"argv": "gog calendar"}})["result"]
    assert result["isError"] is True


def test_an_unknown_tool_is_a_jsonrpc_error(server):
    reply = rpc(server, "tools/call", {"name": "plow_write_file", "arguments": {}})
    assert reply["error"]["code"] == -32602


# --- modes ------------------------------------------------------------------

@pytest.mark.parametrize("server", ["offline"], indirect=True)
def test_offline_answers_503_in_the_real_shape(server):
    """An unpaired Mac is an HTTP failure, not a tool result -- a stub that
    returned isError here would let a consumer pass that cannot handle the
    real thing."""
    with pytest.raises(urllib.error.HTTPError) as exc:
        call(server, DISCOVERY)
    assert exc.value.code == 503
    assert json.load(exc.value)["detail"] == "test-mac.local (2) is not connected"


@pytest.mark.parametrize("server", ["offline"], indirect=True)
def test_offline_refuses_before_auth(server):
    """Whatever you send an unpaired device, it is still unpaired."""
    with pytest.raises(urllib.error.HTTPError) as exc:
        call(server, DISCOVERY, token="wrong")
    assert exc.value.code == 503


@needs_consumer
@pytest.mark.parametrize("server", ["large"], indirect=True)
def test_large_mode_is_big_enough_for_the_runtime_to_persist(server, tmp_path):
    """The relay never returns a path -- Hermes persists an oversized result to
    /tmp/hermes-results/call_<id>.txt itself. So `large` returns genuinely large
    output and lets the real persistence run, rather than faking an envelope no
    relay produces. It must still parse."""
    env = envelope_of(call(server, DISCOVERY))
    assert env["output_length"] > 200_000
    gather = tmp_path / "gather.txt"
    gather.write_text(env["output"])
    result = cl.normalize(cl.extract_array(cl.read_gather(str(gather))))
    assert result["account"] == "mary@example.com"
    assert len(result["calendars"]) > 500


@needs_consumer
def test_the_persisted_envelope_shape_still_reaches_the_same_answer(tmp_path):
    """The shape Hermes hands the model for an oversized result, built from the
    stub's own output -- proof the two delivery paths agree."""
    env = {"exit_code": 0, "handle": "stub-0001",
           "output": stub.listing_text("normal")}
    gather = tmp_path / "gather.txt"
    gather.write_text(json.dumps({"result": json.dumps(env)}))
    result = cl.normalize(cl.extract_array(cl.read_gather(str(gather))))
    assert result["account"] == "mary@example.com"
