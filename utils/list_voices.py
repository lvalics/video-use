"""Fetch the user's ElevenLabs voices and save to voices.json (project root).

Reads ELEVENLABS_API_KEY from .env. Re-run any time to refresh after adding/
cloning new voices in the EL UI.

Usage:
    .\\.venv\\Scripts\\python.exe utils\\list_voices.py
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
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


def main() -> None:
    key = load_api_key()
    r = requests.get(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": key, "Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    voices = []
    for v in data.get("voices", []):
        labels = v.get("labels") or {}
        voices.append({
            "name": v.get("name", ""),
            "voice_id": v.get("voice_id", ""),
            "category": v.get("category", ""),
            "language": labels.get("language", ""),
            "accent": labels.get("accent", ""),
            "gender": labels.get("gender", ""),
            "age": labels.get("age", ""),
            "description": labels.get("description", ""),
            "preview_url": v.get("preview_url", ""),
        })
    voices.sort(key=lambda x: (x["category"], x["name"].lower()))

    out = ROOT / "voices.json"
    out.write_text(json.dumps(voices, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(voices)} voices to {out.relative_to(ROOT)}")
    print()
    print(f"  {'Name':<28} {'Category':<12} {'Lang':<5} {'Gender':<8} voice_id")
    print(f"  {'-'*28} {'-'*12} {'-'*5} {'-'*8} {'-'*22}")
    for v in voices:
        print(f"  {v['name']:<28} {v['category']:<12} {v['language']:<5} {v['gender']:<8} {v['voice_id']}")


if __name__ == "__main__":
    main()
