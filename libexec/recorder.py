"""Captioned recordings of a nest: `omadev record N start|caption|stop|status`.

A detached recorder grabs frames with grim (several staggered workers, about
10 frames a second together, repeats dropped) into ~/.local/state/omadev/N/record/frames, named
by the time each was taken. `caption` appends a timestamped line to
captions.jsonl. `stop` ends the recorder and encodes: every frame lasts until
the next one, so the video keeps real time, and each caption is burned in from
its timestamp until the next caption (an empty caption clears it).

The recorder process runs with the host's environment and gives grim the
nest's (env.json). When it ends, for any reason, it runs meta.json's
`on_exit` command: omadev uses that to take the recording mark off the nest's
host window and put the window back in the grid.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

WORKERS = 3
STAGGER = 0.11
MAX_SECONDS = 600
FPS = 10

# Recordings are mostly text that has to be read. Frames are always taken at
# the nest's full pixel size (grim's own -s downscaling is slow and smears
# text); a quality only decides the frame format and the encode.
#   high (default): lossless PNG frames, crf 16, 4:2:0 - plays everywhere.
#   max: crf 12 and 4:4:4 (no colour subsampling): the sharpest coloured text,
#        but many browsers and phones will not play 4:4:4 H.264.
#   draft: JPEG frames, at most 1280 wide, crf 26: small files for long takes.
QUALITIES = {
    "draft": {"frames": "jpeg", "max_width": 1280, "crf": 26, "pix_fmt": "yuv420p", "preset": "veryfast"},
    "high": {"frames": "png", "max_width": None, "crf": 16, "pix_fmt": "yuv420p", "preset": "slow"},
    "max": {"frames": "png", "max_width": None, "crf": 12, "pix_fmt": "yuv444p", "preset": "slow"},
}
DEFAULT_QUALITY = "high"
FRAME_EXT = {"png": ".png", "jpeg": ".jpg"}


class RecordError(Exception):
    pass


def paths(base: Path) -> dict[str, Path]:
    return {"dir": base, "frames": base / "frames", "captions": base / "captions.jsonl",
            "meta": base / "meta.json", "log": base / "recorder.log", "env": base / "env.json",
            "pinned": base / "pinned"}


def read_meta(base: Path) -> dict:
    try:
        return json.loads(paths(base)["meta"].read_text())
    except (OSError, ValueError):
        return {}


def recording(base: Path) -> bool:
    pid = read_meta(base).get("pid")
    if not pid:
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return b"recorder.py" in cmdline and b"capture" in cmdline


def start(base: Path, out: Path, env: dict[str, str], max_seconds: int = MAX_SECONDS, fps: float = FPS,
          output_scale: float = 1.0, quality: str = DEFAULT_QUALITY,
          on_exit: list[str] | None = None, extra: dict | None = None) -> None:
    """Start the detached recorder. `env` is the nest's environment (for grim);
    the recorder itself inherits this process's (the host's). output_scale is
    the nest's scale: grim scales the layout size, so -s 2 gives a scale-2
    output's full pixels."""
    if quality not in QUALITIES:
        raise RecordError(f"unknown quality {quality!r} ({', '.join(QUALITIES)})")
    if recording(base):
        raise RecordError(f"already recording to {read_meta(base).get('file')}; stop it first")
    if not shutil.which("grim") or not shutil.which("ffmpeg"):
        raise RecordError("recording needs grim and ffmpeg")
    p = paths(base)
    shutil.rmtree(p["frames"], ignore_errors=True)
    p["frames"].mkdir(parents=True)
    p["captions"].write_text("")
    scale = round(output_scale, 3)
    fmt = QUALITIES[quality]["frames"]
    meta = {"file": str(out), "started": time.time_ns(), "scale": scale, "max_seconds": max_seconds,
            "quality": quality, "fps": fps, "on_exit": on_exit or [], **(extra or {})}
    p["env"].write_text(json.dumps(env))
    p["meta"].write_text(json.dumps(meta, indent=2) + "\n")  # on_exit must be there before the recorder can end
    with open(p["log"], "w") as log:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "capture", str(base), str(scale),
                                 str(max_seconds), str(fps), fmt], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                start_new_session=True)
    meta["pid"] = proc.pid
    p["meta"].write_text(json.dumps(meta, indent=2) + "\n")
    deadline = time.time() + 10
    while not any(p["frames"].iterdir()):
        if proc.poll() is not None or time.time() > deadline:
            stop_recorder(base)
            raise RecordError(f"no frames from the nest; see {p['log']}")
        time.sleep(0.1)


def caption(base: Path, text: str) -> None:
    if not recording(base):
        raise RecordError("not recording; start with: omadev record N start [file]")
    with open(paths(base)["captions"], "a") as f:
        f.write(json.dumps({"t": time.time_ns(), "text": text}) + "\n")


def stop_recorder(base: Path) -> None:
    pid = read_meta(base).get("pid")
    if pid and recording(base):
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.time() + 5
        while recording(base) and time.time() < deadline:
            time.sleep(0.1)


def font_file() -> str:
    for pattern in ("JetBrainsMono Nerd Font:bold", "monospace:bold", "sans:bold"):
        try:
            found = subprocess.run(["fc-match", pattern, "-f", "%{file}"], capture_output=True, text=True,
                                   timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            found = ""
        if found and Path(found).is_file():
            return found
    raise RecordError("no font for captions (fc-match found none)")


def frame_times(frames_dir: Path) -> list[tuple[int, Path]]:
    frames = []
    for f in [*frames_dir.glob("*.jpg"), *frames_dir.glob("*.png")]:
        try:
            frames.append((int(f.stem), f))
        except ValueError:
            continue
    return sorted(frames)


def captions_in(base: Path) -> list[tuple[int, str]]:
    out = []
    for line in paths(base)["captions"].read_text().splitlines() if paths(base)["captions"].exists() else []:
        try:
            entry = json.loads(line)
            out.append((int(entry["t"]), str(entry["text"])))
        except (ValueError, KeyError, TypeError):
            continue
    return sorted(out)


def caption_spans(captions: list[tuple[int, str]], t0: int, end: float) -> list[tuple[float, float, str]]:
    """(from, to, text) in seconds from t0; a caption lasts until the next one."""
    spans = []
    for i, (t, text) in enumerate(captions):
        a = max(0.0, (t - t0) / 1e9)
        b = (captions[i + 1][0] - t0) / 1e9 if i + 1 < len(captions) else end
        if text.strip() and b > a:
            spans.append((a, min(b, end), text))
    return spans


def frame_size(frame: Path) -> tuple[int, int]:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                              "-of", "csv=p=0", str(frame)], capture_output=True, text=True, timeout=10).stdout
        w, h = out.strip().split(",")
        return int(w), int(h)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 1920, 1080



def encode(base: Path, sheet: bool = False) -> tuple[Path, float, Path | None]:
    p = paths(base)
    meta = read_meta(base)
    out = Path(meta.get("file") or base / "recording.mp4")
    frames = frame_times(p["frames"])
    if len(frames) < 2:
        raise RecordError("fewer than two frames were recorded")
    t0 = frames[0][0]
    gaps = sorted(b[0] - a[0] for a, b in zip(frames, frames[1:]))
    # The last frame lasts until the recorder stopped. Repeated frames are not
    # kept, so a typical gap could be seconds long; it is only the fallback.
    ended = int(meta.get("ended") or 0)
    tail = ended - frames[-1][0] if ended > frames[-1][0] else gaps[len(gaps) // 2]
    q = QUALITIES.get(meta.get("quality") or "", QUALITIES["draft"])  # recordings from before qualities: JPEG
    concat = base / "concat.txt"
    with open(concat, "w") as f:
        for i, (t, frame) in enumerate(frames):
            nxt = frames[i + 1][0] if i + 1 < len(frames) else t + tail
            f.write(f"file '{frame}'\nduration {(nxt - t) / 1e9:.4f}\n")
        f.write(f"file '{frames[-1][1]}'\n")
    end = (frames[-1][0] + tail - t0) / 1e9
    filters = []
    if q["max_width"]:
        filters.append(f"scale='min(iw,{q['max_width']})':-2:flags=lanczos")
    # Even sizes for H.264; TV range, so JPEG frames do not come out as full-range yuvj420p.
    filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2:out_range=tv")
    spans = caption_spans(captions_in(base), t0, end)
    if spans:
        font = font_file()
        width, height = frame_size(frames[0][1])
        if q["max_width"] and width > q["max_width"]:
            height = round(height * q["max_width"] / width)  # captions are sized for the encoded frame
        size, pad, margin = max(14, round(height * 0.028)), max(6, round(height * 0.016)), round(height * 0.05)
        for i, (a, b, text) in enumerate(spans):
            text_file = base / f"caption-{i}.txt"
            text_file.write_text(text)
            filters.append(
                f"drawtext=fontfile='{font}':textfile='{text_file}':expansion=none:fontsize={size}:fontcolor=white"
                f":box=1:boxcolor=black@0.72:boxborderw={pad}:x=(w-text_w)/2:y=h-text_h-{margin}"
                f":enable='between(t,{a:.3f},{b:.3f})'")
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
                           # Constant rate (the fps filter): frames repeat until the next change, which
                           # costs little, and every player agrees on the length; an MP4 whose last
                           # frame lasts seconds in VFR reports itself too short. -t ends it on time.
                           "-vf", ",".join([f"fps={float(meta.get('fps') or FPS):g}", *filters]),
                           "-t", f"{end:.3f}", "-c:v", "libx264", "-pix_fmt", q["pix_fmt"],
                           "-crf", str(q["crf"]), "-preset", q["preset"], "-tune", "stillimage",
                           "-movflags", "+faststart", str(out)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RecordError(f"ffmpeg failed: {proc.stderr.strip()[-500:]}")
    sheet_file = None
    if sheet:
        sheet_file = out.with_suffix(".sheet.jpg")
        step = max(1, len(frames) // 30)
        proc = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
                               "-vf", f"{','.join(filters)},select='not(mod(n\\,{step}))',scale=480:-2,tile=6x5",
                               "-fps_mode", "passthrough", "-frames:v", "1", str(sheet_file)],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise RecordError(f"contact sheet failed: {proc.stderr.strip()[-500:]}")
    return out, end, sheet_file


def capture(base: Path, scale: float, max_seconds: int, fps: float = FPS, fmt: str = "jpeg") -> None:
    """The recorder: staggered grim workers until SIGTERM, max_seconds, or the
    nest goes away. Each worker takes a frame at most every WORKERS/fps seconds:
    together about `fps`, when grim keeps up."""
    period = WORKERS / max(0.5, fps)
    frames = paths(base)["frames"]
    try:
        nest_env = json.loads(paths(base)["env"].read_text())
    except (OSError, ValueError):
        nest_env = dict(os.environ)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    failures = [0]
    # A frame identical to the one before it is dropped: the earlier frame then
    # lasts until the next change. Text screens sit still most of the time.
    last = {"t": 0, "digest": b""}
    lock = threading.Lock()
    grim_format = ["-t", "png", "-l", "3"] if fmt == "png" else ["-t", "jpeg", "-q", "92"]
    ext = FRAME_EXT.get(fmt, ".jpg")

    def keep(t: int, part: Path, name: Path) -> None:
        digest = hashlib.blake2b(part.read_bytes(), digest_size=16).digest()
        with lock:
            if t > last["t"] and digest == last["digest"]:
                part.unlink(missing_ok=True)
                return
            if t > last["t"]:
                last.update(t=t, digest=digest)
            part.rename(name)

    def worker() -> None:
        while not stop.is_set():
            began = time.monotonic()
            t = time.time_ns()
            name = frames / f"{t}{ext}"
            part = name.with_suffix(".part")  # a frame cut off by stop must not reach the encoder
            proc = subprocess.run(["grim", *grim_format, "-s", str(scale), str(part)],
                                  env=nest_env, capture_output=True)
            if proc.returncode == 0:
                keep(t, part, name)
            if proc.returncode != 0:
                failures[0] += 1
                if failures[0] > 50:
                    stop.set()  # the nest is gone or will not hand out frames
                time.sleep(0.2)
            else:
                failures[0] = 0
                stop.wait(max(0.0, period - (time.monotonic() - began)))

    threads = []
    for _ in range(WORKERS):
        threads.append(threading.Thread(target=worker, daemon=True))
        threads[-1].start()
        time.sleep(STAGGER)
    stop.wait(max_seconds)
    stop.set()
    for t in threads:
        t.join(timeout=5)
    meta = read_meta(base)
    if meta:
        meta["ended"] = time.time_ns()
        tmp = paths(base)["meta"].with_suffix(".tmp")
        tmp.write_text(json.dumps(meta, indent=2) + "\n")
        tmp.replace(paths(base)["meta"])
    on_exit = meta.get("on_exit") or []
    if on_exit:
        try:
            subprocess.run(on_exit, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"on_exit failed: {exc}", file=sys.stderr)


if __name__ == "__main__" and len(sys.argv) in (6, 7) and sys.argv[1] == "capture":
    capture(Path(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4]), float(sys.argv[5]),
            sys.argv[6] if len(sys.argv) == 7 else "jpeg")
