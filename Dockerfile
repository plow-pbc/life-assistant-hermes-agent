# The Plow cloud image: this repo's agent, built for an exe.dev VM.
#
# No agent content of its own — the persona and skills copied below are the
# tracked files agent-mgr bind-mounts into the fleet container. Context is the
# repo root, so those copies are the product content: `docker build .`
#
# The tag is an immutable `base-<sha>` naming one commit of the base's source
# repo, plow-pbc/plow-hermes-agent. It is never moved: every tenant VM inherits
# this exact filesystem while holding that owner's Plow credential, so a moving
# tag would substitute code underneath them.
FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-974f80f3f40a59726823b70afebe5ffb1a48070d@sha256:84b46cbb9e7f6ea87825bb7a5e04d0071faa03c6e49e66e7b052dbaa0fdf3c1d

# Flat, the same layout compose.override.yml produces at /opt/data/skills: every
# SKILL.md names an absolute skills path and every wrapper hops ../../ld-shared
# off its own realpath, so the three have to land as siblings. Copied root-owned
# and world-readable, never chowned to the agent's uid, so a turn cannot write
# to the skill it is running. The base ships its own SOUL.md; this replaces it.
COPY runtime/SOUL.md /var/lib/hermes/SOUL.md
COPY ld-calendar-nudge/   /var/lib/hermes/skills/ld-calendar-nudge/
COPY ld-dashboard/        /var/lib/hermes/skills/ld-dashboard/
COPY ld-morning-triage/   /var/lib/hermes/skills/ld-morning-triage/
COPY ld-morning-updates/  /var/lib/hermes/skills/ld-morning-updates/
COPY ld-payments/         /var/lib/hermes/skills/ld-payments/
COPY ld-setup/            /var/lib/hermes/skills/ld-setup/
COPY ld-shared/           /var/lib/hermes/skills/ld-shared/
COPY ld-sports/           /var/lib/hermes/skills/ld-sports/
COPY ld-viewer-dev/       /var/lib/hermes/skills/ld-viewer-dev/
COPY ld-weather/          /var/lib/hermes/skills/ld-weather/
COPY ld-weekly-digest/    /var/lib/hermes/skills/ld-weekly-digest/

# Normalize whatever modes the checkout carried, preserving the executable bit:
# several SKILL.md files invoke a script by bare path, so a blanket 0644 makes
# them fail with Permission denied. Ownership is left as root.
# -mindepth 1: the skills root itself is the base's, root-owned and sticky so a
# turn cannot rename a baked skill out of the scan path. Recursing over it would
# reset that mode and leave the directory unwritable for the gateway's own
# bundled-skill install, which then scans nothing.
RUN find /var/lib/hermes/skills -mindepth 1 -type d -exec chmod 0755 {} + \
 && find /var/lib/hermes/skills -mindepth 1 -type f ! -perm -u+x -exec chmod 0644 {} + \
 && find /var/lib/hermes/skills -mindepth 1 -type f -perm -u+x -exec chmod 0755 {} + \
 && chmod 0644 /var/lib/hermes/SOUL.md

# The one rewrite. Every path in this repo's content is written against the
# fleet's HERMES_HOME (/opt/data); this runtime's is /var/lib/hermes. It is a
# pure prefix substitution — /opt/data/skills, /opt/data/ld, /opt/data/.env and
# /opt/data/cron all keep their own names — done to the image's copy so the
# tracked files stay the fleet's. Hermes' own scanner refuses an unexpanded
# variable in a skill, which is why this is a literal and not ${HERMES_HOME}.
RUN find /var/lib/hermes/SOUL.md /var/lib/hermes/skills -type f \
      \( -name '*.md' -o -name '*.py' -o -name '*.json' \) \
      -exec sed -i 's|/opt/data|/var/lib/hermes|g' {} +

# The calendar strip's schedule. Cloud image only: the fleet container has no
# systemd, so ld-shared/scripts/calendar_feed.py ships there and nothing calls
# it until agent-mgr grows a command slot. Both units name /var/lib/hermes
# outright — they land in /etc/systemd, which the rewrite above does not walk.
COPY runtime/life-calendar-feed.service runtime/life-calendar-feed.timer /etc/systemd/system/
RUN systemctl enable life-calendar-feed.timer

# The instance directory the producers read and ld-setup writes. Nothing exists
# before first boot, so the image creates it empty: an unset-up agent is routed
# to ld-setup by SOUL.md, exactly as on the fleet.
RUN install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/ld
