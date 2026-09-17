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

if command -v "${CC:-cc}" >/dev/null && command -v pkg-config >/dev/null && pkg-config --exists 'wayland-client >= 1.26' 'wayland-server >= 1.26'; then
  if ! "$src/bin/build-input-gate"; then
    # Do not accidentally enable an older helper against the updated launcher.
    rm -f "$src/libexec/input-gate/keyboard-gate.so"
    echo "omadev: optional input-gate build failed; continuing legacy installation. Rebuild before using --input-gate." >&2
  fi
else
  rm -f "$src/libexec/input-gate/keyboard-gate.so"
  echo "omadev: optional --input-gate needs a C compiler, pkg-config and Wayland 1.26+ headers; build later with $src/bin/build-input-gate" >&2
fi

mkdir -p "$HOME/.local/bin"
ln -sfn "$src/bin/omadev" "$HOME/.local/bin/omadev"
exec "$src/bin/omadev" setup
