"""Pointer input for a nest: a minimal Wayland client, standard library only.

`omadev click` and `omadev move` run this with the nest's environment. It
moves the pointer with zwlr_virtual_pointer_v1 and, for modifiers, holds them
on a zwp_virtual_keyboard_v1 for the length of the click, so a Shift+click
needs no second process to time against. Both protocols are the ones wtype
and wlrctl use; Hyprland offers them to every client.

Coordinates are pixels of the nest's output as `omadev screenshot` saves them,
or logical (layout) coordinates with logical=True.
"""

from __future__ import annotations

import os
import socket
import struct
import subprocess
import time

BUTTONS = {"left": 0x110, "right": 0x111, "middle": 0x112}
# Modifier masks of an evdev/pc keymap, the one xkbcli compiles below.
MODIFIERS = {"shift": 1 << 0, "ctrl": 1 << 2, "control": 1 << 2, "alt": 1 << 3, "super": 1 << 6, "logo": 1 << 6}

POINTER_MANAGER = "zwlr_virtual_pointer_manager_v1"
KEYBOARD_MANAGER = "zwp_virtual_keyboard_manager_v1"


class InputError(Exception):
    pass


def modifier_mask(names: list[str]) -> int:
    mask = 0
    for name in names:
        key = name.strip().lower()
        if key not in MODIFIERS:
            raise InputError(f"unknown modifier {name!r} (shift, ctrl, alt, super)")
        mask |= MODIFIERS[key]
    return mask


def _string(text: str) -> bytes:
    data = text.encode() + b"\0"
    return struct.pack("<I", len(data)) + data + b"\0" * (-len(data) % 4)


def message(obj: int, opcode: int, payload: bytes = b"") -> bytes:
    return struct.pack("<II", obj, (8 + len(payload)) << 16 | opcode) + payload


def _now() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


class Client:
    def __init__(self, display: str | None = None, runtime: str | None = None):
        display = display or os.environ.get("WAYLAND_DISPLAY") or "wayland-0"
        path = display if display.startswith("/") else os.path.join(
            runtime or os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", display)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.connect(path)
        except OSError as exc:
            raise InputError(f"cannot connect to the nest's compositor at {path}: {exc.strerror}") from None
        self.next_id = 2
        self.buffer = b""
        self.globals: dict[str, tuple[int, int]] = {}
        self.done: set[int] = set()
        self.registry = self.new_id()
        self.send(message(1, 1, struct.pack("<I", self.registry)))  # wl_display.get_registry
        self.roundtrip()

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id - 1

    def send(self, data: bytes, fds: list[int] | None = None) -> None:
        if fds:
            socket.send_fds(self.sock, [data], fds)
        else:
            self.sock.sendall(data)

    def roundtrip(self) -> None:
        callback = self.new_id()
        self.send(message(1, 0, struct.pack("<I", callback)))  # wl_display.sync
        while callback not in self.done:
            self.read_events()

    def read_events(self) -> None:
        chunk = self.sock.recv(65536)
        if not chunk:
            raise InputError("the compositor closed the connection")
        self.buffer += chunk
        while len(self.buffer) >= 8:
            obj, word = struct.unpack_from("<II", self.buffer)
            size, opcode = word >> 16, word & 0xFFFF
            if len(self.buffer) < size:
                return
            body, self.buffer = self.buffer[8:size], self.buffer[size:]
            self.event(obj, opcode, body)

    def event(self, obj: int, opcode: int, body: bytes) -> None:
        if obj == 1 and opcode == 0:  # wl_display.error
            _, code, length = struct.unpack_from("<III", body)
            text = body[12:12 + length - 1].decode(errors="replace")
            raise InputError(f"compositor error {code}: {text}")
        if obj == self.registry and opcode == 0:  # wl_registry.global
            name, length = struct.unpack_from("<II", body)
            interface = body[8:8 + length - 1].decode()
            version = struct.unpack_from("<I", body, 8 + length + (-length % 4))[0]
            self.globals.setdefault(interface, (name, version))
        elif opcode == 0 and obj not in (1, self.registry):
            self.done.add(obj)  # wl_callback.done; other objects' events are not needed

    def bind(self, interface: str, version: int) -> int:
        if interface not in self.globals:
            raise InputError(f"the compositor does not offer {interface}")
        name, offered = self.globals[interface]
        obj = self.new_id()
        self.send(message(self.registry, 0, struct.pack("<I", name) + _string(interface)
                          + struct.pack("<II", min(version, offered), obj)))
        return obj

    def close(self) -> None:
        self.sock.close()


class Pointer:
    def __init__(self, client: Client):
        self.client = client
        manager = client.bind(POINTER_MANAGER, 1)
        self.obj = client.new_id()
        client.send(message(manager, 0, struct.pack("<II", 0, self.obj)))  # create_virtual_pointer(seat=null)

    def move_to(self, x: int, y: int, width: int, height: int) -> None:
        self.client.send(message(self.obj, 1, struct.pack("<IIIII", _now(), x, y, width, height)))  # motion_absolute
        self.frame()

    def button(self, code: int, pressed: bool) -> None:
        self.client.send(message(self.obj, 2, struct.pack("<III", _now(), code, 1 if pressed else 0)))
        self.frame()

    def frame(self) -> None:
        self.client.send(message(self.obj, 4))

    def destroy(self) -> None:
        self.client.send(message(self.obj, 8))


def compile_keymap() -> bytes:
    try:
        text = subprocess.run(["xkbcli", "compile-keymap", "--layout", "us"], capture_output=True, text=True,
                              check=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise InputError(f"modifiers need xkbcli (libxkbcommon) to build a keymap: {exc}") from None
    return text.encode() + b"\0"


class Keyboard:
    """A virtual keyboard used only to hold modifiers."""

    def __init__(self, client: Client):
        self.client = client
        manager = client.bind(KEYBOARD_MANAGER, 1)
        seat = client.bind("wl_seat", 1)
        self.obj = client.new_id()
        client.send(message(manager, 0, struct.pack("<II", seat, self.obj)))  # create_virtual_keyboard
        keymap = compile_keymap()
        fd = os.memfd_create("omadev-keymap")
        try:
            os.write(fd, keymap)
            client.send(message(self.obj, 0, struct.pack("<II", 1, len(keymap))), [fd])  # keymap(xkb_v1, fd, size)
        finally:
            os.close(fd)

    def modifiers(self, mask: int) -> None:
        self.client.send(message(self.obj, 2, struct.pack("<IIII", mask, 0, 0, 0)))

    def destroy(self) -> None:
        self.client.send(message(self.obj, 3))


def pointer_input(x: int, y: int, width: int, height: int, button: str | None = None,
                  mods: list[str] | None = None, clicks: int = 1,
                  display: str | None = None, runtime: str | None = None) -> None:
    """Move to (x, y) of a width x height output; with `button`, click it `clicks`
    times while holding `mods`."""
    if not (0 <= x < width and 0 <= y < height):
        raise InputError(f"{x},{y} is outside the nest's output ({width}x{height})")
    code = None
    if button is not None:
        if button not in BUTTONS:
            raise InputError(f"unknown button {button!r} (left, right, middle)")
        code = BUTTONS[button]
    mask = modifier_mask(mods or [])
    client = Client(display, runtime)
    try:
        pointer = Pointer(client)
        keyboard = Keyboard(client) if mask else None
        if keyboard:
            keyboard.modifiers(mask)
        pointer.move_to(x, y, width, height)
        client.roundtrip()
        time.sleep(0.08)  # let the surface see the pointer (and modifiers) arrive before the press
        if code is not None:
            for i in range(clicks):
                pointer.button(code, True)
                client.roundtrip()
                time.sleep(0.04)
                pointer.button(code, False)
                client.roundtrip()
                if i + 1 < clicks:
                    time.sleep(0.06)
            time.sleep(0.05)
        if keyboard:
            keyboard.modifiers(0)
            client.roundtrip()
            keyboard.destroy()
        pointer.destroy()
        client.roundtrip()
    finally:
        client.close()
