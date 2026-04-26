"""Replace the audio track of an MP4 with a different MP3.

Video stream is copied (no re-encode); audio is encoded to AAC 192k.
Output stops at the shorter of video/audio (-shortest).
Output filename: <video_basename>_<audio_basename>.mp4

Output directory:
- If the video lives somewhere under a `_work/` folder, output goes to that
  project's `_work/edit/` (CLAUDE.md convention).
- Otherwise, output sits next to the video.
- Override either with `--out-dir <path>`.

Usage:
    python utils/mp4_replace_audio.py video.mp4 new_audio.mp3
    python utils/mp4_replace_audio.py video.mp4 new_audio.mp3 --out-dir D:/elsewhere
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input_mp4", help="path to MP4 file (video source)")
    ap.add_argument("input_mp3", help="path to MP3 file (replacement audio)")
    ap.add_argument("--out-dir", help="override output directory")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not on PATH")

    video = Path(args.input_mp4).resolve()
    audio = Path(args.input_mp3).resolve()
    for p in (video, audio):
        if not p.exists():
            sys.exit(f"file not found: {p}")

    out_dir = resolve_out_dir(video, Path(args.out_dir).resolve() if args.out_dir else None)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{video.stem}_{audio.stem}.mp4"
    print(f"Video    : {video}")
    print(f"Audio    : {audio}")
    print(f"Output   : {out}")
    print()

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video),
        "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
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
