# Keyboard gate prototype

Development worktree: `/home/dry/Work/omadev-input-gate`, branch `input-gate`.

This is an experimental native Wayland keyboard filter, not yet connected to Omadev's launcher, capture UI, or packaging. The worktree starts from committed `main` at `335bace`; it deliberately does not include the original checkout's uncommitted changes.

## Files

- `keyboard-gate.c`: source-boundary gate with capture generations, expiry, and held-key cleanup.
- `test-native-gate.py`: isolated integration test of the native gate.
- `test-device-gate.py`: earlier experiment demonstrating why disabling the keyboard alone is insufficient.
- `probe/shell.qml`: visible test application recording received keyboard events.
- `REPORT.md`: findings and limitations from the prototype investigation.

Historical logs, screenshots, and reference source snapshots remain at `/home/dry/Work/tries/omadev-input-gate/`. They describe tests performed before copying the prototype into this worktree. Generated binaries, environment snapshots, and test outputs are not included in Git.

## Run

Run the build and tests in a visible terminal inside an owned Omadev nest with owner `input-gate-research`, never on the host. The test creates a disposable child compositor inside that nest and cleans it up afterward. It does not capture the real host keyboard.

```bash
cd /home/dry/Work/omadev-input-gate/experiments/input-gate
cc -Wall -Wextra -Werror -Wno-unused-parameter -shared -fPIC \
  keyboard-gate.c -o keyboard-gate.so -ldl \
  $(pkg-config --cflags --libs wayland-client wayland-server)
set -o pipefail
mkdir -p native-evidence
python -u test-native-gate.py 2>&1 | tee native-evidence/run.txt
```

The native test requires Hyprland, Quickshell, wtype, grim, a C compiler, and Wayland development headers. See the report for untested production integration cases. Do not install or preload this prototype into the host compositor.
