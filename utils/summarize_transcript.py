"""Convert a Scribe / WhisperX transcript JSON into a per-speaker summary.

Reads `_work/edit/transcripts/<stem>.json` and writes:
  _work/edit/<stem>.summary.json   ← compact, machine-readable
  _work/edit/<stem>.summary.md     ← human-readable walkthrough

Summary JSON shape:
{
  "source":   "<stem>",
  "duration": <seconds>,
  "language": "ro",
  "speakers": [
    {"id": "speaker_0", "turn_count": N, "total_seconds": X, "sample": "first turn..."},
    ...
  ],
  "turns": [
    {"idx": 1, "speaker": "speaker_0", "start": 0.0, "end": 3.4, "text": "..."}
  ]
}

Usage:
    python utils/summarize_transcript.py _work/edit/transcripts/3dwarfs.json
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")


def group_into_turns(words: list[dict], silence_threshold: float = 0.6) -> list[dict]:
    """Group word entries into per-speaker turns.

    Same logic shape as helpers/pack_transcripts.py: break on silence ≥ threshold
    OR speaker change.
    """
    turns: list[dict] = []
    current: list[dict] = []
    cur_start: float | None = None
    cur_speaker: str | None = None
    prev_end: float | None = None

    def flush():
        nonlocal current, cur_start, cur_speaker
        if not current:
            return
        text_parts = []
        for w in current:
            t = w.get("type", "word")
            raw = (w.get("text") or "").strip()
            if not raw:
                continue
            if t == "audio_event" and not raw.startswith("("):
                raw = f"({raw})"
            text_parts.append(raw)
        if not text_parts:
            current = []
            cur_start = None
            cur_speaker = None
            return
        text = " ".join(text_parts)
        for old, new in [(" ,", ","), (" .", "."), (" ?", "?"), (" !", "!"), (" ;", ";"), (" :", ":")]:
            text = text.replace(old, new)
        end_time = current[-1].get("end", current[-1].get("start", cur_start or 0.0))
        turns.append({
            "speaker": cur_speaker,
            "start": round(cur_start, 3),
            "end": round(end_time, 3),
            "text": text,
        })
        current = []
        cur_start = None
        cur_speaker = None

    for w in words:
        t = w.get("type", "word")
        if t == "spacing":
            s, e = w.get("start"), w.get("end")
            if s is not None and e is not None and (e - s) >= silence_threshold:
                flush()
            continue
        start = w.get("start")
        if start is None:
            continue
        speaker = w.get("speaker_id")
        if cur_speaker is not None and speaker is not None and speaker != cur_speaker:
            flush()
        if prev_end is not None and start - prev_end >= silence_threshold:
            flush()
        if cur_start is None:
            cur_start = start
            cur_speaker = speaker
        current.append(w)
        prev_end = w.get("end", start)
    flush()
    return turns


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcript_json", help="Path to a Scribe/WhisperX transcript JSON")
    ap.add_argument("--silence-threshold", type=float, default=0.6,
                    help="break turns on silence ≥ this many seconds (default 0.6)")
    args = ap.parse_args()

    src = Path(args.transcript_json).resolve()
    if not src.exists():
        sys.exit(f"file not found: {src}")

    data = json.loads(src.read_text(encoding="utf-8"))
    words = data.get("words", [])
    if not words:
        sys.exit("transcript has no words[]")

    turns = group_into_turns(words, args.silence_threshold)
    for i, t in enumerate(turns, 1):
        t["idx"] = i

    # Per-speaker stats
    speakers: dict[str, dict] = {}
    for t in turns:
        sp = t["speaker"] or "speaker_?"
        if sp not in speakers:
            speakers[sp] = {"id": sp, "turn_count": 0, "total_seconds": 0.0, "sample": t["text"]}
        speakers[sp]["turn_count"] += 1
        speakers[sp]["total_seconds"] += (t["end"] - t["start"])
    for sp in speakers.values():
        sp["total_seconds"] = round(sp["total_seconds"], 2)

    speakers_list = sorted(speakers.values(), key=lambda x: -x["total_seconds"])

    # Total duration: prefer transcript top-level if present, else last turn end
    duration = data.get("duration") or (turns[-1]["end"] if turns else 0.0)

    summary = {
        "source": src.stem,
        "duration": round(duration, 2),
        "language": data.get("language_code", ""),
        "speakers": speakers_list,
        "turns": [{"idx": t["idx"], "speaker": t["speaker"], "start": t["start"], "end": t["end"], "text": t["text"]} for t in turns],
    }

    out_dir = src.parent.parent  # _work/edit/transcripts -> _work/edit
    json_out = out_dir / f"{src.stem}.summary.json"
    md_out = out_dir / f"{src.stem}.summary.md"

    json_out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # Markdown for human reading
    lines = [
        f"# {src.stem}",
        "",
        f"- Duration: **{summary['duration']:.1f} s** ({summary['duration']/60:.1f} min)",
        f"- Language: **{summary['language']}**",
        f"- Speakers detected: **{len(speakers_list)}**",
        f"- Turns: **{len(turns)}**",
        "",
        "## Speakers",
        "",
        "| ID | turns | seconds | sample |",
        "|---|---:|---:|---|",
    ]
    for sp in speakers_list:
        sample = sp["sample"][:80].replace("|", "\\|")
        lines.append(f"| `{sp['id']}` | {sp['turn_count']} | {sp['total_seconds']:.1f} | {sample} |")
    lines.extend(["", "## Turns", ""])
    for t in turns:
        sp = t["speaker"] or "?"
        lines.append(f"- **[{t['start']:.2f}–{t['end']:.2f}] `{sp}`** {t['text']}")
    md_out.write_text("\n".join(lines), encoding="utf-8")

    print(f"  summary: {json_out.relative_to(ROOT)}")
    print(f"  markdown: {md_out.relative_to(ROOT)}")
    print()
    print(f"  speakers: {len(speakers_list)}, turns: {len(turns)}, duration: {summary['duration']:.1f}s")
    for sp in speakers_list:
        sample = sp["sample"][:70]
        print(f"    {sp['id']:<12} {sp['turn_count']:>3} turns, {sp['total_seconds']:>6.1f}s  «{sample}»")


if __name__ == "__main__":
    main()
