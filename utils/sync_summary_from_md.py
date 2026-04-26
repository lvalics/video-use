"""Sync a summary JSON from a hand-edited summary markdown.

Workflow:
1. utils/summarize_transcript.py produces .summary.json + .summary.md
2. User hand-edits the .md: assigns voice IDs in the Speakers table,
   reassigns specific turns to new/different speakers, optionally
   adds extra speaker rows for splits (e.g. speaker_3 → split into
   speaker_3, speaker_4, speaker_5).
3. This script re-reads the .md and rewrites the .json so downstream
   tooling (TTS / voice-changer) sees the corrected mapping.

Markdown shape expected:
  ## Speakers
  | ID | turns | seconds | sample |
  |---|---:|---:|---|
  | `speaker_0` | 46 | 241.3 | sample text | <voice_id>
  ...
  ## Turns
  - **[1.48–1.98] `speaker_0`** Some line of text

Usage:
    python utils/sync_summary_from_md.py _work/edit/3dwarfs.summary.md
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")


SPEAKER_ROW = re.compile(
    r"^\|\s*`([^`]+)`\s*\|\s*\d+\s*\|\s*[\d.]+\s*\|\s*(.+?)\s*\|(.*)$"
)
TURN_LINE = re.compile(
    r"^-\s*\*\*\[([\d.]+)[–-]([\d.]+)\]\s*`([^`]+)`\*\*\s*(.+)$"
)


def parse_md(md_path: Path) -> tuple[dict[str, str], list[dict]]:
    """Return (speakers_voice_id_map, turns_list).

    speakers_voice_id_map: {speaker_id: voice_id}
    turns_list: [{"start", "end", "speaker", "text"}]
    """
    voice_ids: dict[str, str] = {}
    turns: list[dict] = []
    section: str | None = None

    for line in md_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("## Speakers"):
            section = "speakers"
            continue
        if s.startswith("## Turns"):
            section = "turns"
            continue
        if s.startswith("## "):
            section = None
            continue

        if section == "speakers":
            m = SPEAKER_ROW.match(s)
            if m:
                spk = m.group(1)
                trailing = m.group(3).strip()
                # voice id is whatever non-empty token sits after the closing pipe
                if trailing:
                    voice_ids[spk] = trailing.split()[0].strip("`")
                else:
                    voice_ids[spk] = ""
        elif section == "turns":
            m = TURN_LINE.match(s)
            if m:
                turns.append({
                    "start": float(m.group(1)),
                    "end": float(m.group(2)),
                    "speaker": m.group(3),
                    "text": m.group(4).strip(),
                })

    return voice_ids, turns


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("summary_md", help="Path to the *.summary.md")
    args = ap.parse_args()

    md = Path(args.summary_md).resolve()
    if not md.exists():
        sys.exit(f"file not found: {md}")
    json_path = md.with_suffix(".json")
    if not json_path.exists():
        sys.exit(f"sibling JSON not found: {json_path}")

    voice_ids, turns = parse_md(md)
    if not turns:
        sys.exit("no turns parsed from markdown")
    if not voice_ids:
        sys.exit("no speakers parsed from markdown")

    # Recompute per-speaker stats from the (possibly re-assigned) turns
    speakers: dict[str, dict] = {}
    for i, t in enumerate(turns, 1):
        sp = t["speaker"]
        if sp not in speakers:
            speakers[sp] = {
                "id": sp,
                "voice_id": voice_ids.get(sp, ""),
                "turn_count": 0,
                "total_seconds": 0.0,
                "sample": t["text"],
            }
        speakers[sp]["turn_count"] += 1
        speakers[sp]["total_seconds"] += (t["end"] - t["start"])
    for sp in speakers.values():
        sp["total_seconds"] = round(sp["total_seconds"], 2)

    speakers_list = sorted(speakers.values(), key=lambda x: -x["total_seconds"])

    # Carry duration / language from the existing JSON if present
    old = json.loads(json_path.read_text(encoding="utf-8"))
    duration = old.get("duration") or (turns[-1]["end"] if turns else 0.0)
    language = old.get("language", "")

    new_summary = {
        "source": old.get("source", md.stem.removesuffix(".summary")),
        "duration": round(duration, 2),
        "language": language,
        "speakers": speakers_list,
        "turns": [
            {"idx": i, "speaker": t["speaker"], "start": t["start"], "end": t["end"], "text": t["text"]}
            for i, t in enumerate(turns, 1)
        ],
    }

    json_path.write_text(json.dumps(new_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  updated: {json_path.relative_to(ROOT)}")
    print()
    print(f"  speakers: {len(speakers_list)}, turns: {len(turns)}, duration: {new_summary['duration']:.1f}s")
    for sp in speakers_list:
        sample = sp["sample"][:60]
        vid = sp["voice_id"] or "—"
        print(f"    {sp['id']:<12} voice={vid:<24} {sp['turn_count']:>3} turns, {sp['total_seconds']:>6.1f}s  «{sample}»")


if __name__ == "__main__":
    main()
