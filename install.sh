#!/usr/bin/env bash
# Install omadev on Omarchy. Run as your normal user, either from a checkout
# (./install.sh) or straight from GitHub:
#   curl -fsSL https://raw.githubusercontent.com/llstrk/omadev/main/install.sh | bash
set -euo pipefail
repo=https://github.com/llstrk/omadev
config=${XDG_CONFIG_HOME:-$HOME/.config}

if [[ -n ${BASH_SOURCE[0]:-} && -x "$(dirname "${BASH_SOURCE[0]}")/bin/omadev" ]]; then
  src=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
else
  src=${XDG_DATA_HOME:-$HOME/.local/share}/omadev
  if [[ -d $src/.git ]]; then git -C "$src" pull --ff-only; else git clone "$repo" "$src"; fi
fi
echo "omadev source: $src"

# The command.
mkdir -p "$HOME/.local/bin"
ln -sfn "$src/bin/omadev" "$HOME/.local/bin/omadev"

# The bar widget: a copy, not a link, because the shell only hot-reloads real
# directories under ~/.config/omarchy/plugins.
mkdir -p "$config/omarchy/plugins"
rm -rf "$config/omarchy/plugins/dry.omadev"
cp -r "$src/plugin/dry.omadev" "$config/omarchy/plugins/dry.omadev"
# The running shell learns about a new plugin only after a rescan, and the
# rescan is asynchronous; give it a moment before enabling.
enabled=0
if omarchy-shell shell rescanPlugins >/dev/null 2>&1; then
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if omarchy plugin enable dry.omadev >/dev/null 2>&1; then enabled=1; break; fi
    sleep 0.5
  done
fi

# Host keys: Super+Alt+D opens the sessions panel, Super+Shift+Alt+D captures
# or releases keys for the focused session.
bindings=$config/hypr/bindings.lua
if ! grep -q '^-- omadev:' "$bindings" 2>/dev/null; then
  cat >> "$bindings" <<'LUA'

-- omadev: Super + Alt + D opens the sessions panel in the bar. With a session
-- window focused, Super + Shift + Alt + D hands it every key until the same
-- chord is pressed again.
o.bind("SUPER + ALT + D", "Omadev sessions", "omarchy-shell dry.omadev toggle")
o.bind("SUPER + SHIFT + ALT + D", "Capture keys for the focused omadev session", "env OMADEV_ALLOW_CAPTURE=1 omadev focus")
hl.define_submap("nested", function()
  hl.bind("SUPER + SHIFT + ALT + D", hl.dsp.submap("reset"), { description = "Release keys from the omadev session" })
end)
LUA
  hyprctl reload >/dev/null 2>&1 || true
fi

# The agent skill, for whichever agents are set up here.
for dir in "$HOME/.claude/skills" "$HOME/.codex/skills" "$HOME/.agents/skills"; do
  [[ -d $dir ]] && ln -sfn "$src/skill" "$dir/omadev"
done

if (( enabled )); then
  echo "omadev installed. Restart the bar once so it loads the widget:  omarchy restart shell"
else
  echo "omadev installed, but the bar widget could not be enabled yet (is the shell running?)."
  echo "Run:  omarchy restart shell && omarchy plugin enable dry.omadev"
fi
echo "Then: omadev start   (or Super+Alt+D, n)"
