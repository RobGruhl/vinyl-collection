"""Hold records up to the webcam; save one sharp frame per record.

Start with nothing in front of the camera: the first second is taken as the
empty scene. A frame is saved once a record covers the box drawn on the
preview, the picture has held still for --hold seconds, and it looks different
from the last saved frame. You hear a "tink" for each save.

Keys (preview window focused):
  q quit · space force-save · u undo last save · b re-take empty scene
"""

import argparse
import subprocess
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

SAVE_SOUND = "/System/Library/Sounds/Tink.aiff"
UNDO_SOUND = "/System/Library/Sounds/Bottle.aiff"


# Signatures are 160x90 blurred color thumbnails.
SIG_W, SIG_H = 160, 90
# Box (in signature coords) a held-up record must cover: upper-center of frame.
BOX_ROWS, BOX_COLS = slice(18, 40), slice(48, 112)
GRID_ROWS, GRID_COLS = 3, 4  # must divide SIG_H, SIG_W


def signature(frame):
    s = cv2.resize(frame, (SIG_W, SIG_H), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(s, (5, 5), 0).astype(np.int16)


def sharpness(frame):
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (640, 360), interpolation=cv2.INTER_AREA)
    return cv2.Laplacian(g, cv2.CV_64F).var()


def diff(a, b):
    return float(np.abs(a - b).mean())


def motion(a, b):
    """Largest per-cell change on a coarse grid, so movement in one corner counts."""
    d = np.abs(a - b).mean(axis=2)
    cells = d.reshape(GRID_ROWS, SIG_H // GRID_ROWS, GRID_COLS, SIG_W // GRID_COLS).mean(axis=(1, 3))
    return float(cells.max())


def coverage(sig, empty):
    """Fraction of the box that differs from the empty scene."""
    d = np.abs(sig[BOX_ROWS, BOX_COLS] - empty[BOX_ROWS, BOX_COLS]).max(axis=2)
    return float((d > 30).mean())


def beep(sound):
    subprocess.Popen(["afplay", sound])


def next_index(out: Path) -> int:
    nums = [int(p.stem) for p in out.glob("*.jpg") if p.stem.isdigit()]
    return max(nums, default=0) + 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=0, help="AVFoundation camera index (0 = built-in)")
    ap.add_argument("--out", type=Path, default=Path("captures"))
    ap.add_argument("--hold", type=float, default=1.0, help="seconds of stillness before saving")
    ap.add_argument("--motion", type=float, default=8.0, help="per-cell frame-to-frame diff counted as movement")
    ap.add_argument("--cover", type=float, default=0.35, help="fraction of the box a record must cover")
    ap.add_argument("--change", type=float, default=18.0, help="min diff from last save to count as a new record")
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    idx = next_index(args.out)

    cap = cv2.VideoCapture(args.camera, cv2.CAP_AVFOUNDATION)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    if not cap.isOpened():
        raise SystemExit("Could not open camera — check System Settings › Privacy & Security › Camera for your terminal app.")

    # Let auto-exposure settle.
    t0 = time.time()
    while time.time() - t0 < 1.5:
        cap.read()

    ok, frame = cap.read()
    h, w = frame.shape[:2]
    print(f"camera {args.camera}: {w}x{h}  saving to {args.out}/  starting at {idx:04d}", flush=True)

    prev = signature(frame)
    empty = prev
    last_saved_sig = None
    saved_paths = []
    window = deque()          # (time, frame, sig) for the current still stretch
    still_since = None
    armed = True              # becomes False after a save until the scene changes
    status = "ready"

    def save(frame, sig, reason):
        nonlocal idx, last_saved_sig, armed, status
        path = args.out / f"{idx:04d}.jpg"
        cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        saved_paths.append(path)
        print(f"saved {path} ({reason})", flush=True)
        beep(SAVE_SOUND)
        idx += 1
        last_saved_sig = sig
        armed = False
        status = f"saved {path.name}"

    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        now = time.time()
        sig = signature(frame)
        moving = motion(sig, prev) > args.motion
        prev = sig

        if moving:
            window.clear()
            still_since = None
            if last_saved_sig is None or diff(sig, last_saved_sig) > args.change:
                armed = True
        else:
            if still_since is None:
                still_since = now
            window.append((now, frame, sig))
            while window and now - window[0][0] > args.hold:
                window.popleft()

        held = 0.0 if still_since is None else now - still_since
        is_new = last_saved_sig is None or diff(sig, last_saved_sig) > args.change
        covered = coverage(sig, empty) >= args.cover
        if armed and covered and held >= args.hold and is_new:
            best_frame, best_sig = max(((f, s) for _, f, s in window), key=lambda fs: sharpness(fs[0]))
            save(best_frame, best_sig, "auto")
            window.clear()

        key = -1
        if not args.no_preview:
            view = cv2.resize(frame, (960, int(960 * h / w)))
            vh = view.shape[0]
            bar = int(min(held / args.hold, 1.0) * 960) if armed and covered else 0
            cv2.rectangle(view, (0, 0), (bar, 8), (0, 200, 0), -1)
            box = ((BOX_COLS.start * 960 // SIG_W, BOX_ROWS.start * vh // SIG_H),
                   (BOX_COLS.stop * 960 // SIG_W, BOX_ROWS.stop * vh // SIG_H))
            cv2.rectangle(view, *box, (0, 200, 0) if covered else (0, 0, 255), 2)
            state = "MOVING" if moving else ("hold..." if armed and covered else ("cover the box" if armed else status))
            label = f"#{idx:04d}  {state}  [q quit, space save, u undo, b empty]"
            cv2.putText(view, label, (12, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
            cv2.putText(view, label, (12, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.imshow("vinyl capture", view)
            key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            break
        if key == ord("b"):
            empty = sig
            print("re-took empty scene", flush=True)
        if key == ord(" "):
            save(frame, sig, "manual")
        if key == ord("u") and saved_paths:
            p = saved_paths.pop()
            p.unlink(missing_ok=True)
            idx -= 1
            last_saved_sig = None
            armed = False
            beep(UNDO_SOUND)
            print(f"undid {p}", flush=True)
            status = f"undid {p.name}"

    cap.release()
    cv2.destroyAllWindows()
    print(f"done — {len(saved_paths)} saved this session", flush=True)


if __name__ == "__main__":
    main()
