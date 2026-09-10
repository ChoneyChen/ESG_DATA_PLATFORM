#!/bin/sh

set -eu

if command -v node >/dev/null 2>&1; then
  exec node "$@"
fi

for candidate in \
  "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node" \
  "$HOME/.cache/codex-runtimes/codex-secondary-runtime/dependencies/node/bin/node"
do
  if [ -x "$candidate" ]; then
    exec "$candidate" "$@"
  fi
done

candidate="$(find "$HOME/.cache/codex-runtimes" -path '*/dependencies/node/bin/node' -type f -perm -u+x 2>/dev/null | head -n 1 || true)"
if [ -n "$candidate" ]; then
  exec "$candidate" "$@"
fi

printf '%s\n' 'Node.js 20+ was not found. Install Node.js before running the local database.' >&2
exit 127
