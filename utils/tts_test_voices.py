"""Generate one short MP3 per speaker using the first turn's text.

Validates that every voice_id in the summary works with EL TTS before
committing to the full multi-turn generation run. Quick smoke test.

Usage:
    python utils/tts_test_voices.py _work/edit/3dwarfs.summary.json
    python utils/tts_test_voices.py <summary.json> --model eleven_multilingual_v2
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

import requests


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


def tts(text: str, voice_id: str, model_id: str, api_key: str) -> bytes:
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "text": text,
            "model_id": model_id,
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
    ap.add_argument("summary_json", help="Path to *.summary.json")
    ap.add_argument("--model", default="eleven_v3",
                    help="EL model id (default eleven_v3)")
    args = ap.parse_args()

    src = Path(args.summary_json).resolve()
    if not src.exists():
        sys.exit(f"file not found: {src}")
    summary = json.loads(src.read_text(encoding="utf-8"))

    api_key = load_api_key()
    out_dir = src.parent / "voice_tests"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Pick a meaningful test line per speaker: the longest turn whose text isn't
    # a parenthetical / sound-effect tag. Capped at 200 chars so the test stays cheap.
    by_speaker: dict[str, list[dict]] = {}
    for t in summary["turns"]:
        text = (t.get("text") or "").strip()
        if not text or text.startswith("("):
            continue
        by_speaker.setdefault(t["speaker"], []).append(t)
    first_turn: dict[str, dict] = {}
    for sp, turns in by_speaker.items():
        # Prefer turns up to ~200 chars; pick the longest under the cap.
        candidates = sorted(turns, key=lambda t: len(t["text"]), reverse=True)
        chosen = next((t for t in candidates if len(t["text"]) <= 200), candidates[0])
        first_turn[sp] = chosen

    print(f"Testing {len(summary['speakers'])} voices via model={args.model}")
    print(f"Output dir: {out_dir.relative_to(ROOT)}")
    print()

    for spk in summary["speakers"]:
        sid = spk["id"]
        vid = spk["voice_id"]
        if not vid:
            print(f"  SKIP {sid:<12} no voice_id assigned")
            continue
        turn = first_turn.get(sid)
        if not turn:
            print(f"  SKIP {sid:<12} no turn found")
            continue
        text = turn["text"]
        # Trim test text to keep it short (~80 chars)
        sample = text if len(text) <= 120 else text[:117] + "..."
        out_path = out_dir / f"{sid}_{vid[:8]}.mp3"
        try:
            audio = tts(sample, vid, args.model, api_key)
            out_path.write_bytes(audio)
            print(f"  OK   {sid:<12} voice={vid[:8]}…  {out_path.stat().st_size // 1024:>3} KB  «{sample[:60]}»")
        except Exception as e:
            print(f"  FAIL {sid:<12} voice={vid[:8]}…  {e}")

    print()
    print("Listen to the files and tell me which voices need swapping (if any).")


if __name__ == "__main__":
    main()
