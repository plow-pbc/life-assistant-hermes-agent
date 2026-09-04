#!/bin/sh
# Give the agent back the OWNER bit on its own home.
#
# WHY THIS EXISTS (local dev harness only)
# ----------------------------------------
# The base image's own boot repair, plow-init's `harden_home()`, ends the
# home directory as `root:hermes 03770`: it deliberately fchowns the OWNER to
# root ("owning the directory is what lets root unlink a root-owned SOUL.md").
# The comment there also notes the runtime's auth store chmods that same
# directory "on every write". Those two facts collide: once the auth store's
# next write drops the mode from 03770 back to 0700 while the owner is still
# root, the home is `root:hermes 0700` -- and uid 10000 (hermes), which is only
# a GROUP member, loses all access to its own $HERMES_HOME. The gateway's
# drain-control loop then fails every second with
#   drain-control: failed to read /var/lib/hermes/.drain_request.json:
#   [Errno 13] Permission denied
# and cron's `mkdir /var/lib/hermes/cron` fails the same way, which surfaces to
# the household as "Hermes paused". Whether the collision lands is a boot-timing
# race, which is why some rebuilds come up clean and some come up wedged.
#
# THE FIX
# -------
# harden_home's own docstring names the intended resting state: "0700
# hermes:hermes -- the agent owns its own home". We restore exactly that by
# putting the OWNER back to hermes. We do NOT widen the mode: at 0700 with
# owner=hermes the agent has full access, and any later auth-store `chmod 0700`
# is then a no-op rather than a lockout. SOUL.md and skills/ are left untouched
# -- harden_home's handling of those is orthogonal to the home-root owner bug.
#
# ORDERING: this oneshot depends on plow-init (so it runs AFTER harden_home),
# and hermes-gateway depends on THIS (so the drain loop never starts against a
# root-owned home). See the dependencies.d entries.
#
# This is a harness/permissions repair. It does not touch Hermes core logic.
set -eu

PATH=/command:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin
export PATH

HOME_DIR=/var/lib/hermes

# Refuse anything but a real directory at that exact path -- never follow a
# symlink an agent turn could have dropped there, matching the O_NOFOLLOW /
# O_DIRECTORY guard harden_home uses on the same path.
if [ -L "$HOME_DIR" ] || [ ! -d "$HOME_DIR" ]; then
  echo "[life-home-perms] $HOME_DIR is not a real directory; skipping" >&2
  exit 0
fi

owner=$(stat -c %u "$HOME_DIR" 2>/dev/null || echo "")
hermes_uid=$(id -u hermes 2>/dev/null || echo 10000)

if [ "$owner" = "$hermes_uid" ]; then
  echo "[life-home-perms] $HOME_DIR already owned by hermes ($hermes_uid); nothing to do"
  exit 0
fi

# Non-recursive and owner-only: correct just the home-root directory's owner
# back to hermes. Contents are already hermes-owned (the runtime seeds and
# chowns them); only the top-level directory's owner was flipped to root.
if chown -h hermes:hermes "$HOME_DIR" 2>/dev/null; then
  echo "[life-home-perms] restored $HOME_DIR owner to hermes ($hermes_uid)"
else
  echo "[life-home-perms] WARNING: could not chown $HOME_DIR (rootless?); continuing" >&2
fi

exit 0
