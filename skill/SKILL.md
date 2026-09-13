---
name: omadev
description: >
  Use when testing or developing Omarchy shell plugins, bar widgets, themes,
  Hyprland config, or omarchy-* scripts on this machine without touching the
  user's live desktop. Runs Omarchy in nested Hyprland windows (slots 1-9).
  Triggers: omadev, nested session, nest, test a plugin, screenshot a
  widget, drive the shell from a script, parallel teammates on Omarchy work.
---

# omadev for agents

`omadev` runs extra Omarchy sessions as windows inside the user's
desktop. A nest is a real Hyprland plus a real omarchy-shell on the same
kernel and user: sysfs, hwmon, i2c and the system bus are the real hardware.
Only the compositor, the session bus, and the Omarchy config (a private
reflink clone per nest) are separate. Source and README: the directory `omadev --help` lives in (`readlink -f $(command -v omadev)`).

## Rules

- Claim a slot with `ensure N --owner <your-name>`; every nest has its own copy of the
  Omarchy config, so nothing you change in it reaches the host or other nests. `ensure`
  refuses a slot that belongs to someone else (another owner, or the user's own unowned session).
- Never capture the user's keyboard: `omadev focus`, the bar widget's capture click and
  Super+Shift+Alt+D are for the human at the machine. `focus` refuses without
  OMADEV_ALLOW_CAPTURE=1; do not set it. Drive a nest with `run`, `hyprctl N dispatch`,
  `shell` and `screenshot`; you never need the host's focus.
- Hardware writes inside a nest are real writes to this laptop. Keep power
  limit, DDC and similar write paths behind a dry-run flag unless the user
  asked for a live test, and never write the same device from two nests.
- Never run `omarchy restart shell` on the host, `omarchy update`, or
  anything touching systemd user units from inside a nest. The host session
  may be locked; nests work fine while it is, and so do screenshots, but
  `focus` cannot move the host's focus.
- Stop your nest when done and `clean` its home after you have taken what
  you need from `diff`.

## Workflow

```bash
# Claim a slot (idempotent). Prints JSON with the nest's environment.
omadev ensure 3 --owner <your-name> [--path <omarchy checkout>] [--plugin <worktree>]

# Drive it without focusing it
omadev shell 3 <ipc-target> <method> [args]    # e.g. shell 3 shell listPlugins
omadev run 3 <command...>                       # runs with the nest's env and private HOME
omadev run 3 omarchy restart shell              # reload only this nest's shell
omadev hyprctl 3 clients -j
omadev hyprctl 3 dispatch 'hl.dsp.exec_cmd("ghostty")'   # compositor actions
omadev run 3 wtype "some text"                  # into the focused app; SUPER+... binds do not fire from wtype
omadev screenshot 3 [file.png]                  # prints the path
omadev log 3 [-f] [--all]                       # shell + app output; --all keeps benign noise

# Inspect and finish
omadev diff 3                                   # config/state the nest changed vs the host
omadev stop 3                                   # blocks until the slot is free
omadev clean 3                                  # remove the nest's private home
omadev list --json                              # all slots, owners, pids, paths; state free|starting|running|stopping|unreachable
```

## Facts that save time

- Ready condition is `ensure` returning, or `shell N shell ping` answering
  `ok`. Do not sleep and guess.
- A nest starts as a copy of the host's `~/.config/omarchy`, so every host
  plugin is already there; you only need `--plugin` to swap one for your own
  checkout. `--plugin <dir>` links the checkout into the nest under its
  manifest id, at creation or later through `ensure` on a running nest. `list --json` shows the
  links under `plugins`. Use a git worktree per teammate and merge branches.
- A `--plugin` link is a symlink, and the shell's `inotifywait -r` watcher does not follow
  symlinked directories: edits to a linked plugin do NOT hot-reload. After editing, run
  `omadev run N omarchy restart shell` (nest-safe) to load them.
- `unreachable` in `list` means a record exists but this environment cannot reach the nest's
  compositor socket under $XDG_RUNTIME_DIR/hypr (an agent sandbox without it). Nothing is deleted;
  run omadev from an environment that has the runtime directory. Sessions without an owner are
  the user's own: claim your own with `ensure N --owner <name>`.
- Browsers: a nest has a fresh, empty Chromium-family profile, so the browser opens inside the nest
  (with no logins).
- Plugin QML errors appear in `log N` as `WARN qml: Plugin widget <id> failed: ...`.
- Virtual-keyboard input (`run N wtype ...`) reaches the focused app but never
  fires the compositor's own bindings. To do what a SUPER binding would do,
  dispatch its action: `hyprctl N dispatch 'hl.dsp.focus({ workspace = "2" })'`.
  To open a widget's panel use its IPC: `shell N shell toggle <plugin-id> '{}'`.
- `screenshot` works even while the host is locked. If it ever blocks, the
  nest has not rendered a frame yet; retry after a second.
- The nest's private bus does not activate portals or at-spi (the host owns
  those and the Hyprland portal crashes against a nested compositor), so
  portal-based file dialogs and screen sharing are unavailable inside a nest.
  Crash notifications in a nest are real crashes, from any process.
- A new session's host window lands on the shared host workspace `omadev` without taking focus
  from the user, gridded evenly with the other sessions (floating, recomputed on start/stop).
  The user switches there with the panel or `focus N` (user-only). `--place tile` tiles it on the
  workspace that was active at start instead.
- Closing a session's window on the host ends the session (same as `stop N`).
- The nested output always follows the host window's size (so screenshots are
  the size of the window on the host; there is no `--size`). Scale is 1 (`--scale`).
- Nest windows all have the same title; the launcher and widget find them by PID.
