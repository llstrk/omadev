-- omadev: Hyprland config for a nested Omarchy session.
--
-- Runs a private copy of the user's ~/.config/hypr/hyprland.lua so bindings, look and feel,
-- window rules and theme all match the host, but swaps out the two modules
-- that must not run twice on one machine: Omarchy's session autostart (systemd
-- environment import, power profiles, udiskie, first-run provisioning) and the
-- user's own autostart. Nested-only extras go in ~/.config/hypr/omadev.lua.

local root = os.getenv("OMADEV_ROOT")
local home = os.getenv("HOME")
local omarchy_path = os.getenv("OMARCHY_PATH") or "/usr/share/omarchy"

local function seed()
  package.loaded["default.hypr.autostart"] = true
  package.loaded["hypr.autostart"] = true
end

-- bootstrap.lua wipes package.loaded for Omarchy modules every time the user
-- config dofile()s it, so re-seed right after each bootstrap run.
local real_dofile = dofile
dofile = function(path, ...)
  local result = real_dofile(path, ...)
  if type(path) == "string" and path:match("bootstrap%.lua$") then
    seed()
  end
  return result
end

seed()
local user_config = home .. "/.config/hypr/hyprland.lua"
local probe = io.open(user_config, "r")
if probe then
  probe:close()
  real_dofile(user_config)
else
  real_dofile(omarchy_path .. "/default/hypr/bootstrap.lua")
  seed()
  require("default.hypr.omarchy")
  require("default.hypr.toggles")
end
dofile = real_dofile

local require_optional = require("default.hypr.require_optional")
require_optional.module("hypr.omadev")

-- The nested output. Hyprland names the host window WAYLAND-1. Without a
-- the output follows the host window: "preferred" is whatever the window is
-- now, and the Wayland backend reports a new mode on resize.
local scale = tonumber(os.getenv("OMADEV_SCALE") or "1") or 1
hl.monitor({ output = "WAYLAND-1", mode = "preferred", position = "0x0", scale = scale })

-- Keep the checkout and safety overrides first, even if the user's Lua
-- config or the checkout's envs.lua reordered PATH. Avoid duplicate entries.
local first = { root .. "/overlay", omarchy_path .. "/bin" }
for entry in (os.getenv("PATH") or "/usr/local/bin:/usr/bin"):gmatch("[^:]+") do
  if entry ~= first[1] and entry ~= first[2] then table.insert(first, entry) end
end
hl.env("OMARCHY_PATH", omarchy_path)
hl.env("PATH", table.concat(first, ":"))

hl.on("hyprland.start", function()
  -- Use this launcher, not an older installed omadev found on PATH.
  hl.exec_cmd("'" .. (root .. "/../bin/omadev"):gsub("'", "'\\''") .. "' _publish")
  hl.exec_cmd("quickshell -p '" .. (root .. "/keeper"):gsub("'", "'\\''") .. "'")
  if os.getenv("OMADEV_NO_SHELL") ~= "1" then
    hl.exec_cmd("omarchy-launch-shell")
  end
end)
