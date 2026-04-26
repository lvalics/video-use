"""Transcribe a video/audio with WhisperX (Whisper Large v3 + diarization).

Same model Adobe Captioneer uses (large-v3, 2.9 GB), runs locally on CUDA.
Adds pyannote diarization so multi-speaker videos get speaker labels matching
the schema produced by helpers/transcribe.py (ElevenLabs Scribe).

Output: <edit_dir>/transcripts/<stem>.json with the same shape Scribe produces:
- words[] with type="word" or type="spacing", start/end/text/speaker_id
- spacing entries cover the gaps between consecutive words so
  pack_transcripts.py's silence-based phrase grouping keeps working

Cached: skips upload if the output file already exists (use --force to re-run).

Usage:
    python helpers/transcribe_whisper.py <input>
    python helpers/transcribe_whisper.py <input> --num-speakers 3
    python helpers/transcribe_whisper.py <input> --min-speakers 2 --max-speakers 5
    python helpers/transcribe_whisper.py <input> --srt
    python helpers/transcribe_whisper.py <input> --no-diarize     # transcription only
    python helpers/transcribe_whisper.py <input> --device cpu     # fallback if no GPU
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Suppress noisy startup warnings before importing whisperx.
os.environ.setdefault("PYTHONWARNINGS", "ignore")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import whisperx  # noqa: E402
from whisperx.diarize import DiarizationPipeline  # noqa: E402


def load_env_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    env = Path(__file__).resolve().parent.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            keys[k.strip()] = v.strip().strip('"').strip("'")
    return keys


def to_scribe_words(aligned_segments: list[dict]) -> tuple[list[dict], str]:
    """Convert WhisperX aligned segments to Scribe's words[] schema.

    Each WhisperX word looks like {word, start, end, speaker?, score?}.
    We emit alternating 'word' and 'spacing' entries — the latter cover the
    silent gap between consecutive words so pack_transcripts.py's silence
    detection picks the same phrase boundaries it would on Scribe output.
    """
    words: list[dict] = []
    text_parts: list[str] = []
    prev_end: float | None = None
    prev_speaker: str | None = None

    for seg in aligned_segments:
        for w in seg.get("words", []):
            text = (w.get("word") or "").strip()
            if not text:
                continue
            start = w.get("start")
            end = w.get("end")
            if start is None or end is None:
                # Some words (numerics, single chars) have no per-word timing
                # in whisperx alignment. Use segment bounds as fallback.
                start = start if start is not None else seg.get("start")
                end = end if end is not None else seg.get("end")
                if start is None or end is None:
                    continue

            speaker = w.get("speaker") or seg.get("speaker") or prev_speaker

            if prev_end is not None and start > prev_end:
                words.append({
                    "text": " ",
                    "start": float(prev_end),
                    "end": float(start),
                    "type": "spacing",
                    "speaker_id": speaker,
                })

            words.append({
                "text": text,
                "start": float(start),
                "end": float(end),
                "type": "word",
                "speaker_id": speaker,
            })
            text_parts.append(text)
            prev_end = end
            prev_speaker = speaker

    full_text = " ".join(text_parts)
    return words, full_text


def write_srt(segments: list[dict], path: Path) -> None:
    def fmt(t: float) -> str:
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t - h * 3600 - m * 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        start = seg.get("start") or 0.0
        end = seg.get("end") or start
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = seg.get("speaker")
        prefix = f"[{speaker}] " if speaker else ""
        lines.append(str(i))
        lines.append(f"{fmt(start)} --> {fmt(end)}")
        lines.append(prefix + text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("input", help="path to audio/video file")
    ap.add_argument("--edit-dir", type=Path, default=Path("_work/edit"),
                    help="output base dir (default: _work/edit)")
    ap.add_argument("--model", default="large-v3", help="Whisper model (default: large-v3)")
    ap.add_argument("--language", default="ro", help="ISO code, default 'ro' (Romanian)")
    ap.add_argument("--num-speakers", type=int, default=None, help="exact speaker count")
    ap.add_argument("--min-speakers", type=int, default=None)
    ap.add_argument("--max-speakers", type=int, default=None)
    ap.add_argument("--no-diarize", action="store_true",
                    help="skip pyannote diarization (transcription + alignment only)")
    ap.add_argument("--srt", action="store_true",
                    help="also write a clean .srt next to the JSON")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    ap.add_argument("--compute-type", default=None,
                    help="default: float16 on cuda, int8 on cpu")
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--force", action="store_true", help="ignore cache")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    src = Path(args.input).resolve()
    if not src.exists():
        sys.exit(f"file not found: {src}")

    edit_dir = args.edit_dir.resolve()
    out_dir = edit_dir / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{src.stem}.json"

    if out_path.exists() and not args.force:
        print(f"  cached: {out_path}")
        return

    compute_type = args.compute_type or ("float16" if args.device == "cuda" else "int8")

    print(f"  loading {args.model} ({args.device}, {compute_type})")
    model = whisperx.load_model(
        args.model, device=args.device, compute_type=compute_type, language=args.language
    )

    print(f"  loading audio: {src.name}")
    audio = whisperx.load_audio(str(src))

    print("  transcribing")
    result = model.transcribe(audio, batch_size=args.batch_size, language=args.language)

    print("  aligning word timestamps")
    align_model, align_meta = whisperx.load_align_model(
        language_code=result["language"], device=args.device
    )
    result = whisperx.align(
        result["segments"], align_model, align_meta, audio, args.device,
        return_char_alignments=False,
    )

    if not args.no_diarize:
        keys = load_env_keys()
        hf_token = keys.get("HUGGINGFACE_API_KEY") or keys.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_API_KEY")
        if not hf_token:
            sys.exit("HUGGINGFACE_API_KEY not in .env (or HF_TOKEN env var). Use --no-diarize to skip diarization.")

        print("  diarizing speakers")
        # Pin to 3.1 — pyannote.audio 4.x defaults to community-1 which needs
        # a separate ToS approval. 3.1 + segmentation-3.0 + wespeaker is the
        # same stack used by Adobe Captioneer and our existing ToS covers it.
        diar = DiarizationPipeline(
            model_name="pyannote/speaker-diarization-3.1",
            token=hf_token,
            device=args.device,
        )
        diar_kwargs = {}
        if args.num_speakers is not None:
            diar_kwargs["num_speakers"] = args.num_speakers
        if args.min_speakers is not None:
            diar_kwargs["min_speakers"] = args.min_speakers
        if args.max_speakers is not None:
            diar_kwargs["max_speakers"] = args.max_speakers
        diar_segments = diar(audio, **diar_kwargs)
        result = whisperx.assign_word_speakers(diar_segments, result)

    words, full_text = to_scribe_words(result.get("segments", []))

    payload = {
        "language_code": args.language,
        "language_probability": 1.0,
        "text": full_text,
        "words": words,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  saved: {out_path.relative_to(edit_dir.parent)} ({out_path.stat().st_size // 1024} KB)")
    print(f"    words: {sum(1 for w in words if w['type'] == 'word')}")

    if args.srt:
        srt_path = out_path.with_suffix(".srt")
        write_srt(result.get("segments", []), srt_path)
        print(f"    srt:   {srt_path.relative_to(edit_dir.parent)}")


if __name__ == "__main__":
    main()
