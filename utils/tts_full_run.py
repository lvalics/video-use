"""Generate TTS audio for every turn in a translated summary JSON.

Reads <summary>.en.summary.json (or any summary), iterates the turns[] list,
calls EL TTS (eleven_v3) per turn with the speaker's assigned voice_id,
saves each as turn_NNN_speaker_X.mp3 in _work/edit/tts_turns/.

Skips:
- turns marked "type": "sfx"
- turns whose text starts with "(" (sound-effect markers)
- turns whose speaker has no voice_id assigned
- already-generated files (unless --force)

Usage:
    python utils/tts_full_run.py _work/edit/3dwarfs.en.summary.json --limit 5
    python utils/tts_full_run.py _work/edit/3dwarfs.en.summary.json --all
    python utils/tts_full_run.py <summary.json> --force --limit 5
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

import requests

MODEL_ID = "eleven_v3"  # locked: TTS uses v3, voice-changer uses multilingual_v2 (per docs/todo.md)


def load_api_key() -> str:
    env = ROOT / ".env"
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == "ELEVENLABS_API_KEY":
            return v.strip().strip('"').strip("'")
    sys.exit("ELEVENLABS_API_KEY not found in .env")


def tts(text: str, voice_id: str, api_key: str) -> bytes:
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": text,
            "model_id": MODEL_ID,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": True,
            },
        },
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.content


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("summary_json", help="Path to *.summary.json (translated)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--limit", type=int, default=None, help="generate first N eligible turns only")
    g.add_argument("--all", action="store_true", help="generate every turn")
    ap.add_argument("--force", action="store_true", help="regenerate even if MP3 exists")
    ap.add_argument("--out-subdir", default="tts_turns", help="output subdir under summary's parent (default tts_turns)")
    args = ap.parse_args()

    if args.limit is None and not args.all:
        sys.exit("must pass either --limit N or --all")

    src = Path(args.summary_json).resolve()
    if not src.exists():
        sys.exit(f"file not found: {src}")
    summary = json.loads(src.read_text(encoding="utf-8"))

    voice_by_speaker = {s["id"]: s["voice_id"] for s in summary.get("speakers", [])}
    api_key = load_api_key()

    out_dir = src.parent / args.out_subdir
    out_dir.mkdir(parents=True, exist_ok=True)

    eligible: list[dict] = []
    for t in summary.get("turns", []):
        text = (t.get("text") or "").strip()
        if t.get("type") == "sfx" or not text or text.startswith("("):
            continue
        if not voice_by_speaker.get(t.get("speaker"), ""):
            continue
        eligible.append(t)

    if args.limit is not None:
        eligible = eligible[:args.limit]

    print(f"summary    : {src.relative_to(ROOT)}")
    print(f"out dir    : {out_dir.relative_to(ROOT)}")
    print(f"model      : {MODEL_ID}")
    print(f"to generate: {len(eligible)} turn(s)")
    print()

    manifest: list[dict] = []
    skipped_existing = 0
    for t in eligible:
        idx = t.get("idx", 0)
        spk = t["speaker"]
        vid = voice_by_speaker[spk]
        text = t["text"].strip()
        out_path = out_dir / f"turn_{idx:03d}_{spk}.mp3"

        entry = {
            "idx": idx, "speaker": spk, "voice_id": vid,
            "start": t.get("start"), "end": t.get("end"),
            "file": out_path.name, "text": text,
        }

        if out_path.exists() and not args.force:
            print(f"  cached  turn_{idx:03d} {spk:<10} «{text[:50]}»")
            manifest.append(entry)
            skipped_existing += 1
            continue

        try:
            audio = tts(text, vid, api_key)
            out_path.write_bytes(audio)
            kb = len(audio) // 1024
            print(f"  OK      turn_{idx:03d} {spk:<10} {kb:>4} KB  «{text[:50]}»")
            manifest.append(entry)
        except Exception as e:
            print(f"  FAIL    turn_{idx:03d} {spk:<10}  {e}")

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"manifest   : {manifest_path.relative_to(ROOT)}")
    print(f"generated  : {len(manifest) - skipped_existing}, cached: {skipped_existing}")


if __name__ == "__main__":
    main()
