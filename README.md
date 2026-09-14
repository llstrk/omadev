# omadev

Run Omarchy sessions as windows inside the one you are using. No VM, no
reboot, no `omarchy dev link`: each nest is a nested Hyprland with its own
omarchy-shell, pointed at whatever Omarchy source tree you choose. Up to nine
run side by side in slots 1-9, each with its own checkout if you like.

Because it is the same kernel and the same user, plugins in the nest see the
real hardware: `/sys/class/power_supply`, RAPL power limits, the DDC i2c bus,
hwmon, the system bus. Only the compositor and the session bus are separate.

![Six omadev sessions tiled on the omadev workspace, each a full Omarchy with its own bar, one of them capturing keys](docs/sessions.jpg)

*Six sessions on the `omadev` workspace, each a full Omarchy with its own bar; the middle one is capturing keys.*

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/llstrk/omadev/main/install.sh | bash
omarchy restart shell
```

Or clone and run `./install.sh`. Either way the script links `omadev` into
`~/.local/bin` and runs `omadev setup`, which does the three things that live
in your home: it copies the bar widget into `~/.config/omarchy/plugins` and
enables it, appends the two host bindings below to `~/.config/hypr/bindings.lua`,
and links the agent skill into `~/.claude/skills`, `~/.codex/skills` and
`~/.agents/skills` where those exist. Nothing needs root, and `omadev setup`
is idempotent: run it again after an update to refresh the widget copy.
Running the script again updates a curl-installed copy in `~/.local/share/omadev`.

As a package (`pkg/PKGBUILD`, built for the Omarchy package repository), the
code lands in `/usr/lib/omadev` with `/usr/bin/omadev` linked to it, and the
post-install message asks you to run `omadev setup` once as your user.

The bindings the installer appends to `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + ALT + D", "Omadev sessions", "omarchy-shell dry.omadev toggle")
hl.define_submap("nested", function()
  hl.bind("SUPER + ALT + D", hl.dsp.submap("reset"), { description = "Release keys from the omadev session" })
end)
```

The bar widget `dry.omadev` (in `plugin/`) is the visual side. It must be a
real directory under `~/.config/omarchy/plugins`: the shell watches that tree
with `inotifywait -r`, which does not descend into symlinked directories, so a
symlinked plugin never hot-reloads, which is why the installer copies it. On the host it is one icon,
lit while sessions run, that opens a panel of the open sessions: a row per
session with focus-and-capture and stop actions, and a + button that
starts a new session in the first free slot. The panel takes the keyboard:
j/k move, Enter focuses and captures, and 1–9 focus and capture the corresponding
session. While the panel is open, each visible session window has its number in
the center. Opening the panel places the outline without flashing. Selecting a row
moves the outline to its visible host window, then flashes that window without
changing focus. The outline, flash and numbers use the theme's magenta palette color. Number shortcuts wait for the outline to arrive before focusing and closing the
panel, then play only the session's red capture pulse (no extra selection flash).
n starts a session, d (or x) marks the selected session red, and a second press
confirms stopping it. The row immediately shows “Stopping…” and selection advances
to the next running session (or the previous one at the end). Navigation skips
unavailable rows and keeps the same session selected as stopped rows disappear.
Moving selection or closing cancels confirmation.
w goes to the omadev workspace, Esc closes. It also closes
when it loses keyboard focus, for instance on a workspace switch, except
right after n: a session's window mapping makes the compositor take focus
away for a moment, and the panel takes it back, whoever started the session. Inside a session it shows
whether the host is passing keys there and releases them on click. A full-window
pulse uses theme red when capture starts and theme magenta when keys are released; while
keys are captured a small box under the widget says how to release them. That label
shrinks with the session's width so the bar's centre block does not run into
it when the window is tiled narrow: full text, then a single word, then only
the icon and slot number (the red highlight still shows capture). A read-only
modifier readout remains visible at every size: `mods: none`, `mods: Super+Ctrl`,
or `mods: ?` when unavailable. A larger box below the release hint shows individual
modifier labels, with held keys highlighted in the theme color. The box remains
visible after release if any modifiers still appear held. It reports the nested compositor's key-down state
for Super, Ctrl, Alt, Shift and AltGr, not the host's physical keyboard or an
app's internal modifier state. When capture starts, the widget takes a fresh
snapshot and sends key-up events for reported held modifier keys to that nest,
after verifying it still has host focus and capture. It sends no key-downs and
does not keep clearing modifiers during normal typing. Capture/release timing
is unchanged; the displayed labels themselves remain read-only. The two thresholds are widget settings, `compactBelow` (1300) and `iconOnlyBelow` (900). A new
plugin id needs one `omarchy restart shell` before the bar picks it up.

A new session's window opens on the host workspace named `omadev`, shared by
all sessions, without switching to it or taking focus from what you were
doing. The launcher installs a window rule for that into the running host
compositor (class `aquamarine`, floating, no initial focus, silent workspace,
render-unfocused so a hidden nest keeps getting frames)
through `hyprctl eval`; nothing is written to your Hyprland config, and a
host `hyprctl reload` drops the rule until the next start re-adds it. There the sessions are laid out as an even grid of
floating windows, recomputed whenever one starts or stops (dwindle would keep
halving whichever window was focused last). Press w in the sessions panel to go to that workspace,
or jump to one session from its row or with `omadev focus N`; drag it back
into a tiled layout whenever you like. `--place tile` (or `OMADEV_PLACE=tile`)
tiles the window on the workspace you were on when you started it instead.

Closing a session's window on the host ends the session: the launcher watches
for its window and asks the nested compositor to exit when it is gone, since
Hyprland itself keeps running without an output.

## Use

```bash
omadev                                  # lowest free slot, installed /usr/share/omarchy
omadev start 2 --path ~/Projects/omarchy   # slot 2 runs a checkout
omadev --scale 1.25                     # HiDPI nest; the size always follows the window
omadev --no-shell                       # compositor only
omadev list                             # the nine slots
omadev start --detach                   # return once the nest's shell answers (start otherwise blocks)
omadev log 2 -f                         # follow nest 2's shell and app output (not the compositor's)
```

`start` without `--detach` stays in the foreground for the life of the nest,
like running Hyprland by hand; Ctrl-C ends it, compositor and all. Scripts and
agents can use `ensure` or `--detach` to launch the nest without blocking. That
only detaches the session launcher: substantial tasks inside the nest should
still run in visible foreground apps or terminals. `omarchy nested ...` is not routed by the Omarchy CLI, which only
discovers commands in its own bin directory; call `omadev` directly.

Super+Alt+D opens the sessions panel; each row focuses its nest and captures
keys for it. Super+Alt+D releases captured keys. The keyboard icon on a panel
row focuses and captures that session; the icon inside a session toggles capture.
Capture focuses the window and verifies focus in a separate compositor round trip
before entering passthrough. Release resets the host submap immediately.
Capture is for the person at the keyboard: `omadev focus` refuses unless
`OMADEV_ALLOW_CAPTURE=1` is explicitly set. The widget sets it for user actions
and offers no capture method over its IPC interface. Ctrl-C in the launching terminal or `omadev stop N` ends a
nest; `omadev stop all` ends every nest. `stop` waits until the slot
is free (up to 15 s; `--kill` then sends SIGTERM to a compositor that ignores
the exit request) and returns once the slot can be started again. Stopping
ends everything that carries the nest's environment, including apps that
detached from the compositor and commands started with `omadev run`; a nest
that is still starting can be stopped too. `list` shows
a nest as `starting` until it has published its record and as `stopping` on
the way out. Each slot's launcher.log stays under the runtime directory for
post-mortems.

Starting several nests at once is safe: the launcher holds a lock on the slot
(a `flock`, released only when the launcher exits, so it is never stale) before
anything is launched, so concurrent starts without a slot number get distinct
slots and two starts of the same number leave one of them refused.

The nested compositor occasionally stalls in its handshake with the host
(its log stops while listing buffer formats, it never gets an output, and
nothing inside starts). The launcher treats a nest that has not published
its record within 15 s as stalled, kills that compositor tree and launches
again, up to three times, so a start either yields a working nest or fails
loudly; it never leaves a headless compositor behind.

A slot is alive when its compositor socket (`$XDG_RUNTIME_DIR/hypr/<sig>/`)
accepts a connection; the recorded PID is only a fallback. A session record
that can be neither reached nor verified, as inside an agent sandbox with its own PID
namespace or without the runtime directory, lists as `unreachable` rather than
`free`, and only the launcher that wrote a record ever removes it.

From any host terminal you can drive a nest without focusing it. The slot
number is optional when only one nest runs or when you are inside one:

```bash
omadev run 2 omarchy restart shell      # restart only nest 2's shell
omadev shell 2 shell listPlugins        # omarchy-shell IPC in nest 2
omadev hyprctl 2 clients                # hyprctl against nest 2
omadev hyprctl 2 dispatch 'hl.dsp.exec_cmd("ghostty")'
omadev run 2 wtype "text"               # type into the focused app (compositor binds do not fire)
omadev list --json                      # every nest's environment, owner, pid; state free|starting|running|stopping|unreachable
OMADEV_ALLOW_CAPTURE=1 omadev focus 2   # focus nest 2 and capture keys (user-only guard); no number: the focused nest
```

Nested-only Hyprland config goes in `~/.config/hypr/omadev.lua`; it is copied into each new nest and loaded after the normal config. Edit the nest's private copy for live changes, or start a fresh nest to pick up host edits.

## Every nest has its own home and runtime dir

A nest never shares Omarchy config with the host or with other nests. Its
home under `~/.local/state/omadev/N/home` is a directory of symlinks to the
real home, except:

- `~/.config/omarchy`, `~/.config/hypr` and `~/.local/state/omarchy`, which are reflink clones
  (btrfs copy-on-write: instant, and no blocks of their own until a file is
  written). shell.json, plugins, themes and toggles in the nest are the nest's.
  A config directory that is itself a symlink (a dotfiles checkout) is copied
  as a directory, so the nest never writes through the link.
- Chromium-family browser profiles (`~/.config/chromium` and friends), which
  start empty: those browsers are single-instance per profile, so through a
  linked profile a browser started in the nest would just open a window in
  the one already running on the host.

Everything else, projects, keys, app configs, is the real file. Symlinks
inside the cloned trees are kept private too: one pointing elsewhere in
the same tree is re-pointed into the clone, one pointing outside it (a
`shell.json` from a dotfiles checkout) becomes a copy. The nest also gets its
own `.bashrc` and login profile, which select the checkout before running yours and restore checkout/PATH ordering afterwards. A Bash-only source wrapper redirects Omarchy's environment bootstrap to the nest's selection, so aliases and functions load from the checkout rather than the host. `BASH_ENV` applies the same selection to non-interactive Bash scripts. No global bootstrap or `/etc/omarchy.conf` is modified.

The nest's `XDG_RUNTIME_DIR` is private in the same way: `$XDG_RUNTIME_DIR/omadev-N`
(short on purpose: Unix socket paths are capped at 107 bytes and the host
compositor's control socket is reached through it), where the host's sockets and the directories that hold
them (Wayland, PipeWire, PulseAudio, GnuPG and SSH agents, keyring, `hypr/`
with every compositor's control sockets) are symlinks, and directories of plain
files start empty. Per-session state such as a screen recorder's "already
recording" file, dconf, or the quickshell instance list is therefore the
nest's own: omareel on the host and omareel in a nest no longer see each other.

```bash
omadev start 3 --detach --plugin ~/worktrees/dell-monitor-fix
omadev run 3 omarchy plugin enable dry.dell-monitor   # only nest 3's shell.json changes
omadev diff 3                                          # what the nest changed
omadev stop 3 && omadev clean 3                        # the home is kept until clean
```

`--plugin <dir>` links a checkout, typically a git worktree, into the nest's
plugins directory under its manifest id, so each teammate works on its own
branch and the orchestrator merges branches instead of copying files. It also
works through `ensure` on a running nest. `--reuse` keeps the previous home of
a slot instead of cloning a fresh one. Reusing a home from an older release privatizes its previously shared Hyprland directory first. `diff` includes Hyprland config but excludes linked checkouts (use git there) and churny state such as clipboard history.

## Checkout sessions and safety boundaries

`--path` selects checkout code and defaults, not a fresh install: existing user config and generated theme state are copied. A valid user `shell.json` replaces the checkout's default shell config entirely. The installed Hyprland and Quickshell binaries are still used.

At startup, omadev sources the checkout's `default/uwsm/default`, followed by the private-home view of `~/.config/uwsm/default`. It preserves added, changed, empty and unset exported variables for commands run through `omadev run`. It does not run UWSM itself or its full `env.d` startup chain. Bash startup is checkout-aware; other interactive shells' own startup files are not rewritten.

The shell and checkout commands inherit `PATH` in this order: omadev safety overlays, the selected checkout's `bin`, then the remaining inherited paths. Changes to shell code need `omadev run N omarchy restart shell`; use `omadev hyprctl N reload` for Hyprland config changes.

Known host-affecting commands are guarded:

- `sudo`, `pkexec`, `systemctl`, broad app/service restart helpers and app/hardware theme helpers are refused inside a nest. Run intentional host operations from a host terminal.
- `omarchy refresh config` only accepts paths inside private `hypr/` or `omarchy/` config, rejecting traversal and symlinks leading outside those trees. `omarchy refresh hyprland` now changes only the nest.
- `omarchy theme set` builds the private theme using the stock headless mode, then reloads only the nest's compositor and shell. It skips post-theme hooks, app config writes, terminal signals and keyboard lighting. The nested desktop updates, but there is no animated theme transition or host-app retinting.

These are guardrails, **not a security sandbox**. Projects, keys, most app config, cache and data directories, host sockets, processes and hardware remain shared. Absolute executable paths, direct file writes, custom scripts and direct D-Bus/hardware access can bypass the guards. Only run trusted checkouts and plugins.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node tests/panel.cjs
```

The checkout tests use disposable homes and harmless command stubs. The router integration test also exercises the installed Omarchy router if available. They require neither a compositor nor root privileges.

## For agents and orchestrators

`skill/SKILL.md` (linked into the agent skill directories by the installer) is
the agent-facing guide.

**Prefer work the user can see.** Use the actual app for UI work, and run builds,
tests, servers and other substantial tasks in a visible terminal inside the
session. Keep progress and results visible instead of hiding the main work in
headless commands, detached tmux sessions, background jobs or logs alone. Quick
probes, IPC, readiness checks and genuinely background services can stay headless;
explicit user requests and technical requirements can override the preference.
Do not take host focus, capture the user's keyboard or switch their workspace
just to make the work visible.

For example (replace the project path):

```bash
omadev hyprctl 3 dispatch \
  'hl.dsp.exec_cmd("ghostty --title=omadev-3-build-tests --wait-after-command=true -e bash -lc \"cd ~/Work/my-project && ./bin/build && ./bin/test\"")'
```

The terminal is launched asynchronously, but the task runs in its foreground.
`--wait-after-command` leaves the output visible when it finishes.

Other commands for driving the nest:

```bash
omadev ensure 3 --owner teammate-1 --plugin ~/wt/fix   # idempotent claim, waits for the shell, JSON out
omadev list --json                                            # slots, owners, pids, paths, environments
omadev screenshot 3 [file]                                    # grim inside the nest
omadev run 3 wtype "text"                                     # into the focused app, no host focus needed
omadev hyprctl 3 dispatch 'hl.dsp.exec_cmd("ghostty")'        # compositor-level actions
```

## What is shared and what is not

| | Nest |
|---|---|
| `~/.config/omarchy` (shell.json, plugins, themes, current theme) | private reflink clone per nest |
| `~/.config/hypr/*.lua` | private reflink clone per nest |
| Hardware via sysfs, hwmon, i2c, system bus | shared, real values |
| `OMARCHY_PATH` | the nest's own (`--path`) |
| Compositor, Wayland socket, Hyprland instance | separate |
| Session bus (notifications, tray, MPRIS) | private to the nest by default |
| `XDG_RUNTIME_DIR` | private; host sockets (Wayland, PipeWire, agents, `hypr/`) linked in |
| systemd user units, portals, fcitx5, polkit agent | host only |

## How it stays out of the host's way

- **Autostart.** Omarchy's session autostart is skipped in the nest. It would
  import the nest's `WAYLAND_DISPLAY` into the systemd user manager, start a
  second udiskie and reset power profiles. The nest runs only the shell, an
  idle inhibitor and a small script that publishes its environment.
- **`omarchy restart shell`.** The stock command takes the host's `OMARCHY_PATH`
  from the systemd environment and kills every quickshell running that config
  on any display, which is the host bar. `libexec/overlay/` shadows it with a
  version that only touches the nest's display. The checkout's own router still handles argument parsing, aliases and help, but its final dispatch honors overlays, including for `omarchy restart-shell`.
- **`uwsm-app` and `systemd-run`.** The real ones hand commands to
  `wayland-wm-app-daemon` or the user's systemd manager, which spawn them with
  the host's environment, so SUPER+Return would open a terminal on the host and
  SUPER+B a browser. The overlays run the command directly in the nest instead.
- **Idle and lock.** The launcher disables the shell's automatic screensaver
  and lock cycle using a stay-awake marker in the nest's private state, including
  reused homes. The host's idle settings are untouched. A small Quickshell
  "keeper" also holds a Wayland idle inhibitor as an extra safeguard, but idle
  prevention does not depend on that surface remaining visible. The session
  widget also disables idle at startup (retrying until the idle service is ready),
  so sessions created by an older installed launcher are protected too. `--idle`
  skips this enforcement, clears the private marker in the current launcher, and
  turns the inhibitor off for testing idle behavior.
- **Window size.** The nested output follows the host window. Hyprland's
  Wayland backend resizes the output, but the compositor neither re-arranges
  its layers and windows nor tells clients about the new mode until the
  monitor rule is re-applied. The keeper polls the output size twice a second
  and re-applies the rule on a change, so bar, background and windows follow
  within about a second. There is no fixed-size mode.
- **Logs.** The `systemd-cat` shim retags the nested shell's journal output
  as `omadev-N`, so `omadev log N` reads one nest without
  PID archaeology. The compositor's own log stays in its runtime directory.
- **Session bus.** `dbus-run-session` gives the nest a private bus so the
  nested shell can own `org.freedesktop.Notifications`. The bus uses a
  generated config whose service directory omits the portal and at-spi
  services: the Hyprland portal segfaults against a nested compositor and
  at-spi loops without systemd, and the host provides both anyway. The
  activation environment is pointed at the nest, so anything else it
  activates connects to the nested compositor.

## Limits

- The nested compositor has no DRM outputs. Monitor hotplug, DPMS, lid
  handling, hyprsunset and clamshell logic cannot be tested here.
- Anything that writes hardware state writes the real hardware. Keep write
  paths behind a dry-run flag while iterating.
- Commands that touch systemd user units or the package system (`omarchy
  update`, `omarchy dev link`) act on the host. Do not run them in the nest.
- A nest whose window is on a workspace you are not looking at keeps
  rendering, at the host's `misc.render_unfocused_fps` (15 by default): the
  window rule the launcher installs marks nest windows render-unfocused, so
  screenshots and GUI tests inside a hidden nest work. Whether that also holds
  while the host is locked is not verified.
- Every nest logs a few warnings because the host already owns the
  session-wide service: portal app-ID registration, the polkit agent, at-spi,
  and Quickshell's duplicate-IPC-handler notice for user plugins.
  `omadev log N` hides them; `--all` shows them.
- No portals inside a nest: portal file dialogs and screen sharing fall back
  or fail there. Apps still run; grim screenshots still work.
- The host tray does not show apps running in the nest, and host notifications
  do not appear in the nest.
- Nine slots, each with its own copy of `~/.config/omarchy`: a host plugin
  edit reaches a nest only when the nest is restarted (or the plugin is linked).
- The host cannot tell nest windows apart by title; the widget and the
  `focus` command find them by the compositor's PID.

## Layout

```
bin/omadev                 launcher and every control command (Python 3, standard library only);
                                   its hidden `_publish` runs inside the nest on start to record the environment;
                                   `setup` installs the user-level parts (widget copy, bindings, skill links)
pkg/                               PKGBUILD and install script for the Omarchy package repository
libexec/hyprland.lua               nested Hyprland config (wraps the private copy of your hyprland.lua)
libexec/bash-env                   checkout-aware Bash bootstrap, used by BASH_ENV and private startup files
libexec/overlay/                   CLI dispatch, safe refresh/theme/restart, launcher shims and host-command guards
libexec/refuse-host-command        shared refusal implementation for guard symlinks
libexec/keeper/shell.qml           per-nest helper: idle inhibitor + follow-the-window resize
tests/                            disposable checkout/environment tests and panel behavior tests
```
