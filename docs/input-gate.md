# Keyboard input gate

The input gate is required for every new session, including plain `omadev`, `start`, `ensure`, and launches from the panel. No flag is needed and there is no opt-out. The old `--input-gate` flag remains accepted for script compatibility. Existing legacy sessions are not changed in place; stop and restart them. `ensure` refuses to reuse a legacy session.

## Build and enable

A source checkout needs a C compiler, `pkg-config`, and the Wayland 1.26+ client/server development headers:

```bash
./bin/build-input-gate
```

`install.sh` requires these tools and builds the helper before installing the launcher or updating host configuration. Missing tools or a failed compilation remove any stale generated helper and stop installation. Startup also refuses if the helper is missing or cannot initialize; it never falls back to legacy input. The package recipe builds the library and declares architecture-specific packages and Wayland dependencies. The native helper requires v0.3.0 or later. After tagging a release, refresh the package recipe's source version and archive checksum before publishing the package.

For human capture, install this version's launcher and run its `omadev setup` to configure release bindings and register the backend. Install/update the widget independently with `omarchy plugin add/update`. Merely starting a gated nest for automated tests does not require changing the host binding. Setup writes the absolute launcher path into backend registration and the release binding, avoiding an older `omadev` earlier on the compositor's PATH without editing widget source, and includes an independent emergency escape. It migrates the complete shipped block from another installation path, including a deleted or moved checkout, without rewriting customized blocks. Gated capture requires the shipped binding block in the immediate host's configuration. Restart existing nests after upgrading: new nests record the immediate host's home, configuration path and session bus so widget commands cannot accidentally inspect the private clone or notify the wrong session.

```bash
omadev start 2
omadev input-status 2
omadev list --json
```

Select the session in the panel to capture it. Super+Alt+D releases it through `omadev release`, which waits for key cleanup before resetting host routing. Do not replace that binding with a direct submap reset: the controller will notice a submap change, but that polling fallback is not an instantaneous acknowledged handoff. Manual changes to host bindings after setup require the same care.

If normal release fails, it reports the failure and does **not** silently reset routing. **Super+Shift+Alt+D** is a separate emergency escape executed directly by the host compositor. It resets routing even if the launcher cannot run, then attempts a warning notification. It deliberately bypasses cleanup acknowledgement: stop the nest if keys remain held. The notification is best-effort and is not required for the escape to work. This chord is for the human only, never agent automation.

Agents must not capture the real host keyboard. They can use `run N wtype`, compositor dispatch, shell IPC, and screenshots without capturing. The internal control socket is not an agent automation API.

## States

- **uncaptured:** host keyboard events are discarded; virtual test keyboards remain usable.
- **pending:** explicit capture is authorized, but keys held before capture must be released first. The widget displays `RELEASE HELD KEYS`.
- **captured:** host keyboard events are delivered. Deliberate modifier holds work normally.
- **revoked:** focus was lost or the capture generation expired. Returning focus or renewing the old generation cannot recapture.
- **unavailable:** native readiness or its heartbeat cannot be verified. The widget displays `INPUT STATUS UNKNOWN`; the capture command refuses when native readiness is unavailable. Unknown status is not a promise that input is blocked, since a failed UI reader can coexist with a functioning capture controller.

`omadev input-status N`, `list --json`'s `input_gate`, and the nested widget's `dry.omadev status` expose the actual gate state. The widget uses one persistent `input-status --watch` reader rather than spawning Python five times a second; a reader heartbeat timeout clears stale UI state. Watchers intentionally pin the compositor PID: after a session restart, start a new watcher rather than attributing the replacement's input state to the old instance. The old modifier-clearing workaround is disabled for gated sessions and remains only for legacy sessions.

## Implementation

`libexec/input-gate/keyboard-gate.so` wraps the host-facing `wl_keyboard` listener in the nested compositor, before Aquamarine/Hyprland updates keyboard state. It does not intercept the server-side virtual keyboard protocol. It strips its bootstrap preload from applications launched by that compositor, preserving any original preload. No host compositor is preloaded or restarted.

The launcher owns the Python controller (`libexec/input_gate.py`) independently of omarchy-shell. It issues 750 ms leases, renewed while the intended nest remains focused in the nested submap. Each lease includes a fresh capture generation, the compositor PID, and the native keyboard focus epoch. Opening a generation also waits for a Wayland sync callback on the backend's event queue, so input already sent by the host is processed before the gate can open. A request delayed across a focus change or compositor restart cannot authorize the new focus/session by accident.

The native watchdog runs on the compositor's main event loop. Closing the gate releases only keys it actually forwarded, without synthesizing key-downs or injecting releases through virtual devices. Focus leave revokes capture immediately at the Wayland boundary. Missing, malformed, expired, or wrong-PID/focus-epoch leases fail closed. Controller failure expires the lease; a stalled compositor cannot acknowledge release, so the CLI refuses to claim success or reset host routing prematurely.

The release command serializes with capture/switching, closes all running guarded sessions, waits for native acknowledgement, then resets the host submap. It also works with legacy sessions. The release fallback removes a dead controller's lease and still requires acknowledgement.

A controller also repairs a stale `nested` host submap after it automatically
revokes a capture, such as when focus leaves the nest. It retries at most twice
per second for five seconds, stopping as soon as routing is restored; idle
nests never poll the host. It resets routing only when every live native gate on that host reports
`uncaptured`, generation zero and zero forwarded keys. Unknown, pending,
captured, legacy or starting sessions prevent automatic reset; records whose
compositor is confirmed dead are ignored, whichever host they came from. Recovery takes
the capture lock without waiting, so switching capture to another nest cannot
deadlock the controller or have its new routing reset. Other host submaps are
left alone. Orderly nest shutdown checks again after removing its session record
and releasing its slot. If no controller remains alive, automatic polling is
unavailable; the acknowledged release command remains the fallback.

Startup waits for a fresh native protocol/status handshake from the correct compositor PID and at least one intercepted keyboard. A missing or incompatible hook causes startup to fail rather than silently running unguarded. This checks hook installation, not every possible future Wayland/Aquamarine behavioral change.

The filter handles both direct `wl_proxy_destroy` and marshal-with-destroy cleanup. The latter matters for generated Wayland clients. Controller/native status and lease files live in the slot's private runtime state, not in host configuration. They contain no key text or keycode history. This is input routing isolation, not a security sandbox against same-user processes or arbitrary plugins.

## Validation and rollout

Validated locally with Hyprland 0.56.2-2, Aquamarine 0.15.0-2, Wayland 1.26.0-1, and Quickshell 0.3.1-1. Other stack versions and aarch64 builds still require validation.

Automated integration runs only inside an owned disposable parent nest, using child compositors to model host-to-nest input. Run builds and tests in a visible terminal there:

```bash
# Claim from the real host without capturing its input:
omadev ensure 1 --owner input-gate-integration
# Then run inside that nest's visible terminal:
cd /path/to/omadev
./tests/run-input-gate-tests
```

Coverage includes initially blocked input, pre-held capture keys, actual Ctrl/Shift/Alt/Super combinations, virtual typing, acknowledged release, focus loss/return, two-nest switching, controller stalls, stale generation renewal, repeated capture/release, config reload, German XKB layout, and both keyboard-proxy destruction paths, application key-repeat cancellation, dead-controller release fallback, and the installed emergency callback inside a disposable child compositor. Unit tests cover controller validation, freshness/PID checks, mandatory defaults and missing-helper refusal, CLI capture guards, release ordering, and binding migration. `tests/gated-session-smoke.py` additionally checks real launcher startup, virtual terminal typing, widget state, and a nest-local shell restart.

When validating another supported stack or keyboard setup, a human should check:

1. Look at and click an uncaptured session while using host shortcuts. No host text or modifiers should reach it.
2. Capture using both the panel and keyboard selection, including while the selection key is held. Release the key and confirm normal typing starts.
3. Hold modifiers, release capture with Super+Alt+D, and switch between two sessions. Neither apps nor compositor bindings should retain a modifier.
4. Repeat with the user's actual keyboard layout, repeat behavior, IME, and any custom capture submap/bindings. The initial controller supports the shipped `nested` submap only.
5. Confirm the installed package/installer migration, then repeat after an actual controller/compositor failure.

Do not infer these physical keyboard checks from the synthetic integration suite. Automated tests do not capture the human's host keyboard. Host deployment and physical keyboard testing are separate steps.
