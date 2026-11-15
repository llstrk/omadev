#!/usr/bin/env python3
"""A fullscreen GTK 4 window that logs pointer input as JSON lines, for
tests/click-integration.py: clicks with their button, count, position and held
modifiers, and the last hover position on exit. Usage: click-target.py LOG"""

import json
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

LOG = sys.argv[1]
MODS = {"shift": Gdk.ModifierType.SHIFT_MASK, "ctrl": Gdk.ModifierType.CONTROL_MASK,
        "alt": Gdk.ModifierType.ALT_MASK, "super": Gdk.ModifierType.SUPER_MASK}


def log(entry):
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def activate(app):
    win = Gtk.ApplicationWindow(application=app, title="omadev click target")
    area = Gtk.DrawingArea()
    win.set_child(area)
    click = Gtk.GestureClick(button=0)

    def pressed(gesture, n_press, x, y):
        state = gesture.get_current_event_state()
        log({"event": "click", "button": gesture.get_current_button(), "n": n_press,
             "x": round(x), "y": round(y), "mods": sorted(k for k, m in MODS.items() if state & m)})

    click.connect("pressed", pressed)
    area.add_controller(click)
    motion = Gtk.EventControllerMotion()
    motion.connect("motion", lambda _c, x, y: log({"event": "motion", "x": round(x), "y": round(y)}))
    area.add_controller(motion)
    win.connect("map", lambda *_: log({"event": "ready"}))
    win.fullscreen()
    win.present()


app = Gtk.Application(application_id="dev.omadev.ClickTarget")
app.connect("activate", activate)
app.run([])
