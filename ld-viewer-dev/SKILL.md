---
name: ld-viewer-dev
description: Develop the wall dashboard's viewer app — edit the household life-dashboard repo, push to its main (that IS the deploy; the Pi pulls, builds, health-checks and flips atomically), verify the pushed SHA is live via /api/version, and diagnose over SSH. Use when asked to change, fix, or improve the dashboard, its cards' rendering, or the kiosk viewer, or to sync it from the upstream template.
---

# Life Dashboard — Viewer Development

You edit real viewer code and your changes reach the wall with no human gate.
The guardrails are recoverability — the Pi's build gate, health checks, and
atomic rollback — never permission prompts. Your job is to make the change,
deploy it by pushing, and **prove it went live**.

## Repos

- **Household repo** — what this kiosk deploys from. A push to its `main` IS
  the deploy: a systemd user timer on the Pi fetches every 2 minutes, builds
  into a fresh release dir, health-checks, and atomically flips `~/ld-current`.
- **Upstream template** — `plow-pbc/life-dashboard`: canonical viewer code,
  the Pi-side updater, and the canonical wire protocol at
  `docs/kiosk-protocol.md`. The copy at
  `/opt/data/skills/ld-shared/references/kiosk-protocol.md` is vendored from
  it; when they disagree, the template's copy governs.

## Workspace & credentials

- Workspace clone: `/opt/data/ld-dev/repo`
- Deploy key (push access to the household repo): `/opt/data/ld-dev/ssh/deploy_key`
- Pi SSH key: `/opt/data/ld-dev/ssh/pi_key`

Git over the deploy key — always via `GIT_SSH_COMMAND`, never a hosted-key
assumption:

    GIT_SSH_COMMAND='ssh -i /opt/data/ld-dev/ssh/deploy_key -o IdentitiesOnly=yes' \
      git -C /opt/data/ld-dev/repo pull --ff-only

    GIT_SSH_COMMAND='ssh -i /opt/data/ld-dev/ssh/deploy_key -o IdentitiesOnly=yes' \
      git -C /opt/data/ld-dev/repo push origin main

SSH to the Pi — plain user, **no sudo** (everything you need is a systemd
`--user` unit or a file in the home):

    ssh -i /opt/data/ld-dev/ssh/pi_key so@rpi5 '<command>'

## The development loop

1. **Refresh the workspace.** Clone into `/opt/data/ld-dev/repo` if absent,
   else pull `--ff-only` (recipes above).
2. **Edit.** Run `npm test` locally when node is available in this container;
   otherwise rely on the updater's build gate — it runs `npm ci`,
   `npm run build`, and `npm test` on the Pi before anything flips.
3. **Deploy = push.** Commit, push to household `main`, and note the pushed
   SHA: `git -C /opt/data/ld-dev/repo rev-parse HEAD`.
4. **Verify:**

       /opt/data/skills/ld-viewer-dev/scripts/verify_deploy.py <sha>

   It polls `GET <kiosk>/api/version` (base URL = `DASHBOARD_ENDPOINT_URL`
   minus its `/api/message` suffix, sent with the `DASHBOARD_TOKEN` bearer —
   the kiosk 401s off-box version reads without it) until the kiosk reports
   your SHA.
   Exit 0 = live. The default `--timeout 600` covers the 2-minute fetch cycle
   plus a build.
5. **On exit 1 (timeout), diagnose** — see below — and report what the Pi
   recorded, verbatim.

## Hard rule — what counts as success

**Success is claimed ONLY on a live SHA match** — `verify_deploy.py` exiting 0.
A push that "went through" is not a deploy; the updater may have failed the
build, failed a health check, and rolled back. When that happens, read the
result the updater recorded and **report it verbatim, never paraphrased**:

    ssh -i /opt/data/ld-dev/ssh/pi_key so@rpi5 'cat ~/ld-releases/state/last-result.json'

A rolled-back SHA is pinned in `~/ld-releases/state/bad-sha` and is not retried
until a new push — so the fix is always another commit, never a re-run.

## Diagnostics (SSH, `so@rpi5`, plain user)

    ssh -i /opt/data/ld-dev/ssh/pi_key so@rpi5 'journalctl --user -u life-dashboard-viewer -n 200'
    ssh -i /opt/data/ld-dev/ssh/pi_key so@rpi5 'systemctl --user restart life-dashboard-viewer'
    ssh -i /opt/data/ld-dev/ssh/pi_key so@rpi5 'cat ~/ld-releases/state/last-result.json'

The SSH key is for reads, restarts, and repair — **never the deploy path**.
Deploying by editing files on the Pi directly leaves the kiosk running code no
repo holds; always go through the push.

## Upstream sync chore

When asked to sync from upstream, merge the template into the household repo —
it is an ordinary deploy afterwards:

    cd /opt/data/ld-dev/repo
    git remote add template git@github.com:plow-pbc/life-dashboard.git 2>/dev/null || true
    GIT_SSH_COMMAND='ssh -i /opt/data/ld-dev/ssh/deploy_key -o IdentitiesOnly=yes' git fetch template
    git merge template/main
    # resolve, push (= deploy), then verify the merge SHA like any change.
