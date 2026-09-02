#!/usr/bin/env bash
# Stage this checkout's skills the way the Dockerfile bakes them, so the
# container can bind-mount them instead of being rebuilt for every edit.
#
# The rewrite is the whole reason a staging tree exists. Every path in this
# repo's content is written against the fleet's HERMES_HOME (/opt/data); this
# image's is /var/lib/hermes, and the Dockerfile does that substitution to its
# own copy. A raw bind mount of the checkout would put /opt/data paths back
# into a container that has no such directory, and every script the skill names
# would fail with "No such file or directory".
#
# Modes match the Dockerfile too: several SKILL.md files invoke a script by
# bare path, so the executable bit has to survive.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

rm -rf "$STAGING"
mkdir -p "$STAGING/skills"

for skill in "$REPO_DIR"/ld-*/; do
  cp -R "$skill" "$STAGING/skills/$(basename "$skill")"
done
cp "$REPO_DIR/runtime/SOUL.md" "$STAGING/SOUL.md"

# Same three finds the Dockerfile runs, minus the ownership it cannot set here.
find "$STAGING/skills" -mindepth 1 -type d -exec chmod 0755 {} +
find "$STAGING/skills" -mindepth 1 -type f ! -perm -u+x -exec chmod 0644 {} +
find "$STAGING/skills" -mindepth 1 -type f -perm -u+x -exec chmod 0755 {} +
chmod 0644 "$STAGING/SOUL.md"

find "$STAGING/SOUL.md" "$STAGING/skills" -type f \
  \( -name '*.md' -o -name '*.py' -o -name '*.json' \) \
  -exec sed -i '' 's|/opt/data|/var/lib/hermes|g' {} +

echo "staged $(find "$STAGING/skills" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ') skills + SOUL.md into $STAGING"
