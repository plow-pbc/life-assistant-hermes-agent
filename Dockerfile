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
FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-23e56996dffa13eee5c9088bde3e5b5a6c30e07a@sha256:fe4533fd9793c20a93bf6f306d02935e1fdcdd9532a3b884b5ba68c8e48a69b8

# Flat, all as siblings directly under the skills root: every
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
