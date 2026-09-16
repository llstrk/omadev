# Omadev keyboard gate feasibility

Tested 2026-09-16 on Hyprland 0.56.2-2 (commit efb50993780079460b0cbed1363e2166a2de1d9f), Aquamarine 0.15.0-2.

## Findings

The nested backend exposes its host-backed keyboard as `wl_keyboard`, distinct from virtual keyboards such as wtype. In the nested compositor's initial Lua config:

```lua
hl.device({ name = "wl_keyboard", enabled = false })
```

This blocks host-backed key delivery to applications. `wtype` still works. Toggling only that named keyboard enabled/disabled at runtime also works.

However, **disabled does not mean the backend's internal key state is frozen or empty**. CKeyboard updates pressed/XKB state regardless of m_enabled; InputManager checks enabled downstream before delivering keys/modifiers. We proved this by sending Super_R down while disabled, enabling, and typing A. The app received A with Qt MetaModifier (268435456), despite receiving no Super press while disabled. `hl.is_key_down("Super_R")` returned false throughout this particular synthetic sequence, so it is not sufficient to validate cleanliness of the backend's XKB state.

Sending releases through the parent while the gate was closed, then enabling, produced an unmodified F. Sending a forwarded Shift release before closing the gate delivered the application's release event; subsequent virtual H arrived unmodified.

## Verified scenarios

- Host-backed A blocked from app with gate disabled in initial config.
- Virtual B delivered with gate disabled.
- Host-backed Super press blocked from app while disabled.
- Reopening without a clean handoff leaks retained Super state into A.
- Host-backed C works with gate enabled after Super release.
- Closing again blocks host-backed D, while virtual E works.
- Releases while disabled, then enabling, yield unmodified F.
- Forwarded Shift receives a key-up before gate closure; virtual H remains clean afterward.

These are behavioral probes of a proposed primitive and handoff ordering, not a finished capture controller. Capture-shortcut release waiting, asynchronous focus loss, controller failure, config reload, backend reconnect, arbitrary key ranges, and physical keyboard interaction remain integration cases to implement and test. We did not diagnose the original stuck-Super event from the PR test.

## Initial config-only option (superseded by source-gate prototype below)

A practical Omadev implementation could default `wl_keyboard` to disabled and explicitly coordinate a clean capture/release handoff, keeping virtual input available. The gate must be set in compositor startup config, not after shell readiness. Release forwarded keys while delivery is still enabled before closing; clear backend-held state before enabling. Do not enable solely on a window gaining focus or on a periodically polled shell flag. A controller must acknowledge transitions and fail closed.

For the stronger literal guarantee that **no host key updates any nested keyboard state before capture**, `enabled=false` is insufficient. That requires gating upstream of CKeyboard's state updates, for example backend/compositor support or a protocol-layer gate. Avoid promising that stronger property from a config-only implementation.

## Source-gate prototype

`keyboard-gate.c` implements the stronger boundary as an experimental native helper, loaded only into the disposable compositor via LD_PRELOAD. It intercepts the stable libwayland-client keyboard listener API before Aquamarine/Hyprland receives key and modifier events. It does not intercept the compositor's virtual-keyboard server protocol. The bootstrap removes its own preload/environment from subsequently launched applications, preserving any pre-existing preload.

The gate starts closed, tracks pre-capture physical holds separately from forwarded keys, and requires all pre-capture keys to be released before opening. A generation-numbered monotonic lease authorizes capture. Closing or lease expiry releases only the keys actually forwarded, on the compositor's main event loop. Focus leave releases forwarded keys and revokes that capture generation. Returning focus or renewing an expired generation cannot reopen capture; a fresh explicit generation is required. Keycodes are not logged by the final helper.

`test-native-gate.py` drives real parent-to-child Wayland input and checks the application's events, including modifier masks. Final passing scenarios:

1. Initial host A blocked; virtual B delivered.
2. Super held before capture keeps the gate closed; releasing it allows an unmodified F.
3. Closing while Shift is held emits its key-up without waiting for another host event; virtual G remains unmodified.
4. Deliberate captured Super works; focus leave releases it; focus return remains blocked until explicit recapture.
5. An expired lease releases held Control and blocks host D while virtual E still works.
6. Renewing the expired generation does not silently reopen capture; a new generation admits an unmodified X.

The test uses explicit lease writes to simulate the capture controller. It does **not** yet integrate with Omadev's production capture UI or launcher. The current implementation is a feasibility prototype, not a shipped fix. Production work still needs a guarded capture controller, atomic acknowledged release before resetting the host submap, readiness/status reporting, packaging/build support for the native helper, and compatibility tests. A polling-only controller must not claim an instantaneous release guarantee.

The first native focus-return test exposed a fixture issue: `send_key_state` bypasses the parent's physical bookkeeping, so the parent's subsequent enter snapshot can reintroduce a synthetic held key. The corrected test sends its simulated physical release after the child re-enters, rather than while the child is unfocused. No physical host key capture was needed.

Native test evidence is in `native-evidence/`: `run.txt`, `results.json`, `probe.png`, `visible-test-terminal.png`, and runtime logs. The final source compiled with `-Wall -Wextra -Werror` (unused callback parameters exempted), and the final regression run passed. The screenshot was inspected.

## Test isolation and artifacts

`test-gate.py` ran in a visible terminal in owned Omadev slot 3. It launched a disposable child compositor inside that nest and sent synthetic key events from the owned parent to that child. The real host's focus, keyboard capture, bindings, and configuration were not changed. The parent keyboard injection used explicit child window addresses. The child application records press/release text and modifiers through IPC.

- `evidence/run.txt`: readable test transcript.
- `evidence/results.json`: structured event observations.
- `evidence/probe.png`: final app screenshot.
- `evidence/child.log`, `evidence/probe.log`: runtime logs.
- `source/`: upstream source snapshots used in analysis (Hyprland commit above; Aquamarine v0.15.0).

The child and parent nest were stopped, and the parent nest's private home cleaned. The existing Omadev checkout and its uncommitted changes were not modified. No production gate has been installed.
