# VENDORED from plow-pbc/agent-index-client @ a014b64a06efcfa3dd2e8236c2807b8ae893cc3f
#   path: standalone/agent_index_client.py   owner: eng-550
# Copied, not fetched: that repo is PRIVATE, so a build-time curl 404s.
# To update: copy the file again from that repo and bump the sha above.
"""Publish one agent's token usage to the Agent Index.

    agent_index_client.py --agent life --login      # once: prove your GitHub
    agent_index_client.py --agent life              # then: report usage
    agent_index_client.py --agent life --dry-run    # show what would be sent
    agent_index_client.py --agent life --tags       # tags already in use
    agent_index_client.py --agent life --story ID --title T [--body B] [--tag T]...
    agent_index_client.py --self-check

Collects from two places, because neither alone covers a real machine:
  * agentsview, the same index the Builder Index client reads. Rich and correct
    for claude and codex. Measured on v0.38.1: grok reports zero, fixed
    upstream in 0.39.0; hermes reports zero with no fix known.
  * the Hermes store directly, because of that hermes gap — Hermes is what our
    own agents run on, so relying on agentsview alone puts them on the board at
    zero.

Sends day x model token counts and nothing else: no prompts, no task titles,
no file paths, no costs. Identity is your GitHub account, proven once by device
flow; the token is stored 0600 and only ever sent to github.com and the index.
"""
import json, os, sqlite3, subprocess, sys, time, urllib.error, urllib.request
from collections import defaultdict

CLIENT_ID = "Ov23lirUZHTGqWCMVUXV"          # public by design; device flow uses no secret
API = os.environ.get("AGENT_INDEX_API", "https://agent-index-server.vercel.app")
TOKEN_PATH = os.path.expanduser("~/.agent-index/token")
KEYS = ("input", "output", "cache_read", "cache_write")

# Collectors append here when a read genuinely FAILED, as opposed to finding
# nothing. Without the distinction a broken agentsview or a SQLite error is
# reported as an idle agent, which is the one thing this client must never do:
# it publishes a number people compare agents on.
FAILURES = []


def _post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json",
                                          "accept": "application/json", **headers},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def login():
    """GitHub device flow. Works with no browser on this machine and no secret."""
    # No scope. The server only reads the `login` field of GET /user, which is
    # public profile data and needs no scope at all — verified against a token
    # holding gist/read:org/repo and NOT read:user, which still returned it.
    # This token is forwarded on every report, so it should grant as close to
    # nothing as GitHub allows.
    _, d = _post("https://github.com/login/device/code",
                 {"client_id": CLIENT_ID, "scope": ""}, {})
    if "device_code" not in d:
        sys.exit(f"github refused the device request: {d}")
    print(f"\n  Open {d['verification_uri']} and enter:  {d['user_code']}\n")
    deadline = time.time() + int(d.get("expires_in", 900))
    interval = int(d.get("interval", 5))
    while time.time() < deadline:
        time.sleep(interval)
        _, t = _post("https://github.com/login/oauth/access_token",
                     {"client_id": CLIENT_ID, "device_code": d["device_code"],
                      "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}, {})
        if t.get("access_token"):
            os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
            fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(t["access_token"])
            print(f"  Signed in. Token stored at {TOKEN_PATH} (0600).")
            return t["access_token"]
        if t.get("error") == "slow_down":
            interval += int(t.get("interval", 5))
        elif t.get("error") not in ("authorization_pending", None):
            sys.exit(f"  device flow failed: {t.get('error_description') or t['error']}")
    sys.exit("  code expired, run --login again")


def token():
    if os.path.exists(TOKEN_PATH):
        return open(TOKEN_PATH).read().strip()
    sys.exit("no token — run with --login first")


def from_agentsview(days):
    """date -> model -> counters, for whatever agentsview covers."""
    exe = next((p for p in (os.path.expanduser("~/.local/bin/agentsview"),
                            "/opt/homebrew/bin/agentsview", "/usr/local/bin/agentsview")
                if os.access(p, os.X_OK)), None)
    if not exe:
        print("  agentsview not installed — skipping that collector")
        return {}
    try:
        raw = subprocess.run([exe, "usage", "daily", "--json"], capture_output=True,
                             text=True, timeout=120).stdout
        rows = json.loads(raw)
    except Exception as e:
        # Say it. Swallowing this made a broken agentsview indistinguishable
        # from an agent that did nothing, and the index would show it idle.
        FAILURES.append(f"agentsview: {type(e).__name__}: {e}")
        return {}
    rows = rows if isinstance(rows, list) else rows.get("daily") or rows.get("data") or []
    out = {}
    for r in rows[-days:]:
        models = {}
        for m in r.get("modelBreakdowns") or []:
            name = m.get("modelName") or m.get("model")
            if not name:
                continue
            models[name] = {"input": m.get("inputTokens") or 0,
                            "output": m.get("outputTokens") or 0,
                            "cache_read": m.get("cacheReadTokens") or 0,
                            "cache_write": m.get("cacheCreationTokens") or 0}
        if models:
            out[r["date"]] = models
    return out


def from_hermes(days, home=None):
    """Hermes' own store, which agentsview indexes but reports as all zeros.

    Its four counters are disjoint (prompt = input + cache_read + cache_write)
    and reasoning is a subset of output, so nothing here is double counted.
    """
    home = home or os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    db = os.path.join(home, "state.db")
    if not os.path.exists(db):
        # Say so. A wrong HERMES_HOME otherwise reports zero tokens, which
        # reads as an idle agent rather than a misconfiguration — and inside a
        # container nobody is watching the path resolve.
        print(f"  no Hermes store at {db} (set HERMES_HOME if that is wrong)")
        return {}
    # started_at is a unix epoch float, not a string — date() on it alone
    # returns NULL and every row silently vanishes.
    q = """SELECT date(started_at,'unixepoch','localtime') AS d,
                  COALESCE(model,'unknown') AS m,
                  SUM(COALESCE(input_tokens,0)), SUM(COALESCE(output_tokens,0)),
                  SUM(COALESCE(cache_read_tokens,0)), SUM(COALESCE(cache_write_tokens,0))
           FROM sessions
           WHERE started_at >= strftime('%s', 'now', ?) GROUP BY d, m"""
    out = defaultdict(dict)
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for d, m, i, o, cr, cw in c.execute(q, (f"-{days} days",)):
            if d and (i or o or cr or cw):
                out[d][m] = {"input": i, "output": o, "cache_read": cr, "cache_write": cw}
    except sqlite3.Error as e:
        FAILURES.append(f"hermes store {db}: {e}")
        return {}
    return dict(out)


def merge(*sources):
    """Same (day, model) from two collectors adds up rather than one winning."""
    out = defaultdict(lambda: defaultdict(lambda: dict.fromkeys(KEYS, 0)))
    for src in sources:
        for date, models in src.items():
            for model, row in models.items():
                for k in KEYS:
                    out[date][model][k] += int(row.get(k) or 0)
    return [{"date": d, "models": [{"model": m, **v} for m, v in sorted(ms.items())]}
            for d, ms in sorted(out.items())]


def tags():
    """Tags already in use across the index, commonest first.

    Pick from these rather than inventing a near-duplicate: "Orders & returns"
    and "Order returns" would split one bar in two and nothing would line up
    across agents.
    """
    req = urllib.request.Request(f"{API}/v1/tags", headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("tags", [])


def publish_story(agent, argv):
    """Publish one thing this agent did: a title, what happened, up to 3 tags."""
    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default
    story_id = opt("--story")
    title = opt("--title")
    if not story_id or not title:
        sys.exit("--story ID and --title TEXT are both required")
    chosen = [argv[i + 1] for i, a in enumerate(argv) if a == "--tag"][:3]
    if len(chosen) > 3:
        sys.exit("at most 3 tags")
    body = {
        "story_id": story_id, "title": title, "body": opt("--body", ""),
        "tags": chosen,
        "images": [{"url": u, "caption": ""} for i, a in enumerate(argv) if a == "--image" for u in [argv[i + 1]]],
    }
    code, out = _post(f"{API}/v1/stories?agent_id={agent}", body,
                      {"authorization": f"Bearer {token()}"})
    print(f"  {code} {out}")
    sys.exit(0 if code == 200 else 1)


def main(argv):
    if "--self-check" in argv:
        return self_check()
    if "--login" in argv:
        login()
        if "--agent" not in argv:
            return
    agent = argv[argv.index("--agent") + 1] if "--agent" in argv else os.environ.get("AGENT_ID")
    if not agent:
        sys.exit(__doc__)
    if "--tags" in argv:
        for t in tags():
            print(f"  {t['tag']:<28} {t['uses']} uses across {t['agents']} agent(s)")
        return
    if "--story" in argv:
        return publish_story(agent, argv)
    days = int(argv[argv.index("--days") + 1]) if "--days" in argv else 28
    payload = {"days": merge(from_agentsview(days), from_hermes(days))}
    total = sum(m[k] for d in payload["days"] for m in d["models"] for k in KEYS)
    for f in FAILURES:
        print(f"  COLLECTOR FAILED — {f}")
    print(f"  agent={agent} days={len(payload['days'])} tokens={total:,}")
    if not payload["days"]:
        if FAILURES:
            # Reporting nothing here would publish "idle" for an agent we simply
            # failed to read. Exit non-zero so a supervisor notices.
            sys.exit("  every collector failed and nothing was collected — NOT reporting")
        print("  nothing collected — check HERMES_HOME and that agentsview is installed")
    if "--dry-run" in argv:
        return print(json.dumps(payload, indent=1)[:2000])
    if not payload["days"]:
        return print("  nothing to report")
    code, body = _post(f"{API}/v1/usage?agent_id={agent}", payload,
                       {"authorization": f"Bearer {token()}"})
    print(f"  {code} {body}")
    sys.exit(0 if code == 200 else 1)


def self_check():
    a = {"2026-09-01": {"gpt": {"input": 1, "output": 2, "cache_read": 0, "cache_write": 0}}}
    h = {"2026-09-01": {"gpt": {"input": 10, "output": 0, "cache_read": 5, "cache_write": 0}},
         "2026-08-31": {"opus": {"input": 3, "output": 4, "cache_read": 0, "cache_write": 0}}}
    m = merge(a, h)
    assert [d["date"] for d in m] == ["2026-08-31", "2026-09-01"], m
    gpt = [x for x in m[1]["models"] if x["model"] == "gpt"][0]
    assert gpt == {"model": "gpt", "input": 11, "output": 2, "cache_read": 5, "cache_write": 0}, gpt
    assert merge({}, {}) == [], "no data must send no days, not a day of zeros"
    assert from_hermes(28, home="/nonexistent") == {}, "a missing store is empty, not a crash"

    # Hermes stores started_at as a unix epoch float. Reading it as a string
    # makes date() return NULL and every row disappear silently, which is
    # exactly how this was broken the first time.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "state.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE sessions (started_at REAL, model TEXT, input_tokens INT,"
                  " output_tokens INT, cache_read_tokens INT, cache_write_tokens INT)")
        c.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)",
                  (time.time() - 3600, "gpt-5.5", 11, 22, 33, 44))
        c.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)",
                  (time.time() - 400 * 86400, "old", 1, 1, 1, 1))
        c.commit(); c.close()
        got = from_hermes(28, home=tmp)
        assert len(got) == 1, f"recent session must be collected, old one excluded: {got}"
        row = list(got.values())[0]["gpt-5.5"]
        assert row == {"input": 11, "output": 22, "cache_read": 33, "cache_write": 44}, row

    print("self-check OK — merge sums both collectors, ordering, empty store, and epoch dates")


if __name__ == "__main__":
    main(sys.argv[1:])
