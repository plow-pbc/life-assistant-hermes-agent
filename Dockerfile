# The Plow cloud image: this repo's agent, built for an exe.dev VM.
#
# No agent content of its own — the persona and skills copied below are the
# tracked files this repo owns. Context is the
# repo root, so those copies are the product content: `docker build .`
#
# The tag is an immutable `base-<sha>` naming one commit of the base's source
# repo, plow-pbc/plow-hermes-agent. It is never moved: every tenant VM inherits
# this exact filesystem while holding that owner's Plow credential, so a moving
# tag would substitute code underneath them.
FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-84e38bb6b7d17463eba095a9b17c96b352c57feb@sha256:fcb9ad623f460b10f852a785931aa4870fba15f056edf58a3b6502d7846a5404

# This replaces the base's own SOUL.md; first boot re-asserts root ownership
# on that file, which is what the trailing chmod answers.
COPY runtime/SOUL.md /var/lib/hermes/SOUL.md
COPY LICENSE NOTICE /usr/share/doc/life-assistant/

# Shipped at /opt/hermes/skills, outside every home, so a bind-mounted home
# still receives it and an image update still reaches an uncustomised skill
# -- both via the base runtime's reconcile into whichever home this gets.
COPY ld-calendar-nudge/   /opt/hermes/skills/ld-calendar-nudge/
COPY ld-dashboard/        /opt/hermes/skills/ld-dashboard/
COPY ld-email-inbox/      /opt/hermes/skills/ld-email-inbox/
COPY ld-morning-triage/   /opt/hermes/skills/ld-morning-triage/
COPY ld-morning-updates/  /opt/hermes/skills/ld-morning-updates/
COPY ld-setup/            /opt/hermes/skills/ld-setup/
COPY ld-shared/           /opt/hermes/skills/ld-shared/
COPY ld-wall-setup/       /opt/hermes/skills/ld-wall-setup/
COPY ld-sports/           /opt/hermes/skills/ld-sports/
COPY ld-weather/          /opt/hermes/skills/ld-weather/
COPY ld-weekly-digest/    /opt/hermes/skills/ld-weekly-digest/

# Normalize whatever modes the checkout carried, preserving the executable bit:
# several SKILL.md files invoke a script by bare path, so a blanket 0644 makes
# them fail with Permission denied. Ownership is left as root.
# -mindepth 1: the skills root itself is the base's, root-owned and sticky, and
# recursing over it would reset that mode and leave the directory unwritable for
# the gateway's own bundled-skill install, which then scans nothing. Sticky here
# stops a turn unlinking an entry it does NOT own; after first boot it owns every
# skill under this root, so it does not stop the rename -- see above.
RUN find /opt/hermes/skills -mindepth 1 -type d -exec chmod 0755 {} + \
 && find /opt/hermes/skills -mindepth 1 -type f ! -perm -u+x -exec chmod 0644 {} + \
 && find /opt/hermes/skills -mindepth 1 -type f -perm -u+x -exec chmod 0755 {} + \
 && chmod 0644 /var/lib/hermes/SOUL.md

# The unattended producer's own copy, outside every home and out of the agent's
# reach.
#
# What the supervisor runs every 300s must not be a file a turn can rewrite.
# Everything under $HERMES_HOME/skills belongs to uid 10000 in a running
# container -- the runtime chowns what it seeds on every boot -- so scheduling
# the copy that lives there would turn one prompt-injected edit into code that
# runs unattended, forever, holding the relay credential and the household's
# calendar. This copy is root-owned and 0755/0644 in a root-owned directory:
# the agent can read it and cannot change it.
#
# The home copy stays exactly as it is -- it is what the agent reads, edits and
# runs by hand during setup, and taking that away would take the skill with it.
# Only the SCHEDULE points here.
COPY ld-shared/ /opt/plow/ld-shared/
RUN chown -R root:root /opt/plow \
 && find /opt/plow -type d -exec chmod 0755 {} + \
 && find /opt/plow -type f -exec chmod 0644 {} +

# The calendar strip's schedule, as a supervised service beside the gateway.
# The run script lands in /etc/s6-overlay, outside the skills tree.
# The usage reporter, fetched at build from the commit vendor/client.pin names
# and checked against the hash beside it. Fetched rather than committed because
# plow-pbc/agent-index-client owns that file; pinned rather than tracked from a
# branch because this runs inside an agent holding a live credential, and a
# moving reference would substitute unreviewed code under it. The checksum is
# the second half: a sha in a URL is only as good as the host serving it.
#
# Root-owned under /opt/plow, like the calendar producer and for the same
# reason: the copy in the agent's home belongs to uid 10000 in a running
# container, so scheduling that one would run whatever a turn last wrote there.
COPY vendor/client.pin /opt/plow/agent-index-client.pin
RUN set -eu; \
    sha="$(sed -n 's/^sha=//p' /opt/plow/agent-index-client.pin)"; \
    want="$(sed -n 's/^sha256=//p' /opt/plow/agent-index-client.pin)"; \
    path="$(sed -n 's/^path=//p' /opt/plow/agent-index-client.pin)"; \
    curl -fsS --max-time 60 -o /opt/plow/agent-index-client.py \
      "https://raw.githubusercontent.com/plow-pbc/agent-index-client/${sha}/${path}"; \
    got="$(sha256sum /opt/plow/agent-index-client.py | cut -d' ' -f1)"; \
    [ "$got" = "$want" ] || { echo "agent-index client is $got, pin says $want" >&2; exit 1; }; \
    chmod 0644 /opt/plow/agent-index-client.py

COPY image/s6-overlay/ /etc/s6-overlay/

# The process timezone, resolved from this household's config before any
# service starts. The base image sets none; every cron schedule this agent
# registers fires in whatever this leaves behind.
COPY --chmod=0755 image/cont-init.d/10-life-timezone /etc/cont-init.d/10-life-timezone

# Onboarding's own assets, and NOT under the home. Hermes refuses to deliver a
# model-emitted MEDIA: path whose prefix is on its media denylist -- /etc /proc
# /sys /dev /root /boot /var/log /var/lib /var/run -- and this runtime's whole
# HERMES_HOME is /var/lib/hermes, so a GIF parked beside the skills is dropped
# with "Skipping unsafe MEDIA directive path" and the opener arrives as text
# with no picture and no error anywhere the owner or the agent can see.
# /srv is outside that list, which is the whole reason for the path.
#
# Root-owned and world-readable like the skills: a turn sends this file, it
# does not get to replace it.
COPY docs/onboarding-v2/assets/ /srv/plow-assets/
RUN chmod 0755 /srv/plow-assets && chmod 0644 /srv/plow-assets/*

# The instance directory the producers read and ld-setup writes. Nothing exists
# before first boot, so the image creates it empty: an unset-up agent is routed
# to ld-setup by SOUL.md.
RUN install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/ld
