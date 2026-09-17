# Plugin and backend installation

## Ownership

| Owner | Files/actions |
| --- | --- |
| `omarchy plugin add/update/remove` | Widget git checkout, enablement and updates |
| Backend package or source installer | Launcher, native keyboard gate and support files |
| `omadev setup` | Host release bindings, agent skill links and backend registration |
| Widget | Read-only detection; explicit install/setup buttons |

The repository has a root `manifest.json`. Its `barWidget` entry point is
`plugin/dry.omadev/Omadev.qml`. Omarchy rejects symlinks anywhere in a plugin clone,
so the backend's small refusal guards are regular executable files, not symlinks.
The backend package no longer installs a widget copy.

## Normal installation

```bash
omarchy plugin add https://github.com/llstrk/omadev --enable
```

Open the widget. It checks `capabilities --json` without running setup or changing
files. It shows a missing/incompatible/build-required/unconfigured state instead
of enabling session controls against an unknown launcher. Use the explicit button
to open a terminal, read what will change and confirm. Cancel leaves everything
unchanged. A failed installation stays visible in the terminal; retry is explicit.

The source installation button builds an independent snapshot from the trusted
plugin checkout under `$XDG_DATA_HOME/omadev/backends/<unique-release>`. It does not
run sudo or install system dependencies. Supply a C compiler, pkg-config and Wayland
1.26+ development files first. A failure before the native build succeeds does not
replace the launcher. A setup failure retains the snapshot for diagnosis and can
leave the new backend registered but unconfigured; custom bindings remain intact.

Snapshots are intentional: pointing the launcher directly into the plugin checkout
would let a routine plugin update change Python code while leaving an older native
library behind. Neither git-managed plugin updates nor removal own installed backend
snapshots. Old releases are kept until the user removes them after stopping sessions
that use them. Package users can install a compatible package and run `omadev setup`
instead. The currently pinned v0.2.0 package archive predates this protocol.

## Backend protocol

`omadev capabilities --json` returns `application: "omadev"`, `protocol: 1`, a version,
an absolute launcher path, `nativeHelperPresent`, `bindingsConfigured`, `bindingsStatus` and `state`.
It is read-only and does not require a widget installation. Helper presence and
configuration are preflight checks, not proof of runtime ABI compatibility; session
startup still requires the native handshake before publishing readiness.

`omadev setup` writes `$XDG_CONFIG_HOME/omadev/backend.json` atomically with
`schemaVersion: 1` and the absolute launcher. Discovery prefers this registration,
then `~/.local/bin/omadev`, then PATH. A selected existing backend with an unsupported
protocol is reported as incompatible rather than silently replaced by another PATH
copy. Stale registrations pointing to missing files can fall back to another installed
backend. Nested widgets only probe their session's `OMADEV_ROOT` backend.

The widget probes initially, every 5 seconds while not ready, and every 30 seconds
while ready, and immediately when the panel opens. A manual backend install/setup is
detected without rewriting the widget.
Timeouts and execution failures report unavailable, retain the last session list, and
offer only a readiness recheck, never installation as a recovery action. A verified
protocol mismatch (or an explicit unknown-capabilities command) reports incompatible.
If unavailability persists, inspect `omadev capabilities --json` in a host terminal.
A broken installation can be reinstalled explicitly by running
`~/.config/omarchy/plugins/dry.omadev/install.sh` in that terminal. This is a manual
recovery path, not an automatic response to failed probes.

Unconfigured host bindings do not block starting uncaptured sessions or stopping and
listing sessions. Capture still requires acknowledged release bindings. If setup
recognizes a custom block it cannot migrate, the widget explains that manual editing
is required and does not offer a setup button that would repeatedly fail. Installation/setup cannot be
triggered over the widget IPC API, on load, or by a nested widget. The helper also
requires a real terminal and confirmation, and refuses installation/setup actions
inside a nest. The CLI's `omadev setup` can configure the current nest's private
configuration for nested development; it does not install a backend, and shared
agent-skill directories outside that private home are skipped with an explanation.

## Migrating an existing copied widget

`omadev setup` deliberately leaves both old copies and git-managed widgets untouched,
and warns if the existing widget is not git-managed.
The plugin manager refuses an already-installed ID, so back up the old copy explicitly
before using `plugin add`. Preserve `shell.json`: the plugin ID stays `dry.omadev`, so
its settings/layout can remain in place.

```bash
if [[ -e ~/.config/omarchy/plugins/dry.omadev/.git ]]; then
  echo 'Already git-managed. Use: omarchy plugin update dry.omadev'
else
  backup="$HOME/.local/state/omadev/widget-backup-$(date +%s%N)"
  mkdir -p ~/.local/state/omadev &&
    mv ~/.config/omarchy/plugins/dry.omadev "$backup" &&
    omarchy plugin add https://github.com/llstrk/omadev --enable
fi
```

Inspect the existing installation before migrating. If `.git` exists, use
`omarchy plugin update` instead. If installation fails, move the backup back and rescan plugins. Omarchy's
running QML engine can retain old plugin code; a human may need to restart the shell
after replacing a copied installation. Do not remove the old backend until sessions
using it have stopped and the new backend is ready.

For development, `--plugin /path/to/omadev` now takes the repository root, not the old
`plugin/dry.omadev` subdirectory. Source-linked plugins still need nest-local shell
restarts after edits because the recursive file watcher does not follow links.
