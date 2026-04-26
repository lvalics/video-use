"""Convert MP3 -> MP4 with a black video layer.

Mirrors a Premiere export with an empty video track on top of the audio:
black 854x480 @ 25fps, AAC 192k. Output stops when the MP3 ends.
Output filename: <input_basename>_<rounded_seconds>sec.mp4

Output directory:
- If the input lives somewhere under a `_work/` folder, output goes to that
  project's `_work/edit/` (CLAUDE.md convention).
- Otherwise, output sits next to the input.
- Override either with `--out-dir <path>`.

Usage:
    python utils/mp3_to_mp4.py path/to/song.mp3
    python utils/mp3_to_mp4.py path/to/song.mp3 --out-dir D:/elsewhere
"""
from __future__ import annotations
import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def resolve_out_dir(src: Path, override: Path | None) -> Path:
    if override:
        return override
    for parent in src.parents:
        if parent.name == "_work":
            return parent / "edit"
    return src.parent


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return float(out.replace(",", "."))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input_mp3", help="path to MP3 file")
    ap.add_argument("--out-dir", help="override output directory")
    args = ap.parse_args()

    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        sys.exit("ffmpeg/ffprobe not on PATH")

    src = Path(args.input_mp3).resolve()
    if not src.exists():
        sys.exit(f"file not found: {src}")

    duration = ffprobe_duration(src)
    seconds = round(duration)
    out_dir = resolve_out_dir(src, Path(args.out_dir).resolve() if args.out_dir else None)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{src.stem}_{seconds}sec.mp4"

    print(f"Input    : {src}")
    print(f"Duration : {duration:.3f}s (rounded -> {seconds}s)")
    print(f"Video    : 854x480 @ 25fps black")
    print(f"Output   : {out}")
    print()

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=854x480:r=25:d={duration}",
        "-i", str(src),
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        str(out),
    ]
    rc = subprocess.run(cmd).returncode
    if rc != 0:
        sys.exit(f"ffmpeg failed (exit {rc})")
    print(f"\nOK -> {out}")


if __name__ == "__main__":
    main()
