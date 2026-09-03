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
FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-380a99729e906c3080cdf30e522c20f883e8e89a@sha256:2b8d1d2d48105ebc050ba855d2e88d25902cf6d0b558db3cb9e9c6083f13838e

# Flat, the same layout compose.override.yml produces at /opt/data/skills: every
# SKILL.md names an absolute skills path and every wrapper hops ../../ld-shared
# off its own realpath, so the three have to land as siblings.
#
# Copied root-owned, and that lasts exactly until the first boot: the runtime
# reconciles its bundled skills into $HERMES_HOME/skills and chowns what it
# seeds to uid 10000, so in a RUNNING container every directory and file below
# is the agent's. Measured on this image: as uid 10000 a turn appends to a
# SKILL.md it is running and renames a whole skill out of the scan path, both
# succeeding. Do not read the root ownership here as a guarantee about runtime
# -- it is the state of the layer, not of the agent's home.
#
# What does hold is /opt/hermes/skills, the base's bundled copy outside every
# home: unwritable to uid 10000 (measured), which is why an image update still
# reaches a skill the agent has not customised. The base ships its own SOUL.md;
# this replaces it, and first boot re-asserts root ownership on that one file.
COPY runtime/SOUL.md /var/lib/hermes/SOUL.md
COPY ld-calendar-nudge/   /var/lib/hermes/skills/ld-calendar-nudge/
COPY ld-dashboard/        /var/lib/hermes/skills/ld-dashboard/
COPY ld-morning-triage/   /var/lib/hermes/skills/ld-morning-triage/
COPY ld-morning-updates/  /var/lib/hermes/skills/ld-morning-updates/
COPY ld-setup/            /var/lib/hermes/skills/ld-setup/
COPY ld-shared/           /var/lib/hermes/skills/ld-shared/
COPY ld-wall-setup/       /var/lib/hermes/skills/ld-wall-setup/
COPY ld-sports/           /var/lib/hermes/skills/ld-sports/
COPY ld-viewer-dev/       /var/lib/hermes/skills/ld-viewer-dev/
COPY ld-weather/          /var/lib/hermes/skills/ld-weather/
COPY ld-weekly-digest/    /var/lib/hermes/skills/ld-weekly-digest/

# Normalize whatever modes the checkout carried, preserving the executable bit:
# several SKILL.md files invoke a script by bare path, so a blanket 0644 makes
# them fail with Permission denied. Ownership is left as root.
# -mindepth 1: the skills root itself is the base's, root-owned and sticky, and
# recursing over it would reset that mode and leave the directory unwritable for
# the gateway's own bundled-skill install, which then scans nothing. Sticky here
# stops a turn unlinking an entry it does NOT own; after first boot it owns every
# skill under this root, so it does not stop the rename -- see above.
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

# The calendar strip's schedule, as a supervised service beside the gateway.
# It names /var/lib/hermes outright — the run script lands in /etc/s6-overlay,
# which the rewrite above does not walk.
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
# to ld-setup by SOUL.md, exactly as on the fleet.
RUN install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/ld
