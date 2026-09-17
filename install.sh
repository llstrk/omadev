#!/usr/bin/env bash
# Install omadev on Omarchy without the package: run as your normal user, either
# from a checkout (./install.sh) or straight from GitHub:
#   curl -fsSL https://raw.githubusercontent.com/llstrk/omadev/main/install.sh | bash
# Host keys, backend registration and agent skills are `omadev setup`.
# The widget is installed independently with `omarchy plugin add`,
# which the package's post-install asks you to run too.
set -euo pipefail
if [[ -n ${OMADEV_SLOT:-} ]]; then
  echo 'omadev: install the backend from the host, not inside a nest.' >&2
  exit 1
fi
repo=https://github.com/llstrk/omadev

if [[ -n ${BASH_SOURCE[0]:-} && -x "$(dirname "${BASH_SOURCE[0]}")/bin/omadev" ]]; then
  src=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
else
  src=${XDG_DATA_HOME:-$HOME/.local/share}/omadev
  if [[ -d $src/.git ]]; then git -C "$src" pull --ff-only; else git clone "$repo" "$src"; fi
fi
# A git-managed widget must not become a live backend through a symlink:
# plugin update/remove must remain independent from backend installation.
case "$src/" in
  "$HOME/.config/omarchy/plugins/"*|"${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/"*)
    exec python3 "$src/plugin/dry.omadev/backend.py" install
    ;;
esac
echo "omadev source: $src"

if command -v "${CC:-cc}" >/dev/null && command -v pkg-config >/dev/null && pkg-config --exists 'wayland-client >= 1.26' 'wayland-server >= 1.26'; then
  if ! "$src/bin/build-input-gate"; then
    # Do not accidentally enable an older helper against the updated launcher.
    rm -f "$src/libexec/input-gate/keyboard-gate.so"
    echo "omadev: required input-gate build failed; installation stopped." >&2
    exit 1
  fi
else
  rm -f "$src/libexec/input-gate/keyboard-gate.so"
  echo "omadev: installation requires a C compiler, pkg-config and Wayland 1.26+ headers." >&2
  exit 1
fi

mkdir -p "$HOME/.local/bin"
ln -sfn "$src/bin/omadev" "$HOME/.local/bin/omadev"
exec "$src/bin/omadev" setup
