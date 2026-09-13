#!/usr/bin/env bash
# Install omadev on Omarchy without the package: run as your normal user, either
# from a checkout (./install.sh) or straight from GitHub:
#   curl -fsSL https://raw.githubusercontent.com/llstrk/omadev/main/install.sh | bash
# The user-level parts (bar widget, host keys, agent skill) are `omadev setup`,
# which the package's post-install asks you to run too.
set -euo pipefail
repo=https://github.com/llstrk/omadev

if [[ -n ${BASH_SOURCE[0]:-} && -x "$(dirname "${BASH_SOURCE[0]}")/bin/omadev" ]]; then
  src=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
else
  src=${XDG_DATA_HOME:-$HOME/.local/share}/omadev
  if [[ -d $src/.git ]]; then git -C "$src" pull --ff-only; else git clone "$repo" "$src"; fi
fi
echo "omadev source: $src"

mkdir -p "$HOME/.local/bin"
ln -sfn "$src/bin/omadev" "$HOME/.local/bin/omadev"
exec "$src/bin/omadev" setup
