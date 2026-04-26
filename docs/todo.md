# TODO — Voice-to-video chunking pipeline

Discussed 2026-04-26. Not yet implemented.

## Goal

Workflow for: write script → TTS → split into ≤14s chunks → submit each chunk to Seedance/Dreamina for lip-synced video generation → swap the AI's wrong-word audio back to the original voice → concat → caption → publish.

## Step 0 — Generate `voice.mp3` (two paths)

### Model picker (rule)

| Operation | EL model |
|---|---|
| Text-to-Speech (Path A, all generation runs) | **`eleven_v3`** |
| Voice Changer / Speech-to-Speech (Path B) | **`eleven_multilingual_v2`** (v3 doesn't support voice-changer yet) |

Hardcode this in each utility, don't expose as a flag — easier to keep consistent across runs.

### Path A — TTS (script → voice)

Cleanest path. Write the script as text with per-line speaker tags, send to ElevenLabs v3, get back one mixed MP3.

```
script.txt — example:
[Anna] Ai văzut-o pe noua vecină?
[Marius] Aia cu păr superb, ochi albaștri și corp perfect?
[Anna] Da.
[Marius] Nu am observat-o deloc.
```

Plus a voice-id mapping (config file or `.env`):
```
EL_VOICE_ANNA=21m00Tcm4TlvDq8ikWAM
EL_VOICE_MARIUS=pNInz6obpgDQGcFmaJgB
```

Implementation:
- For each line, call `https://api.elevenlabs.io/v1/text-to-speech/{voice_id}` with the line's text and the speaker's voice_id
- Model: **`eleven_v3`** (always, for TTS)
- Concatenate the per-line MP3s with a small inter-line silence (≈300 ms) → `voice.mp3`
- Write a `script_timing.json` so the downstream splitter has speaker tags + line offsets without needing to diarize

### Path B — Voice changer (act it → swap voices per speaker)

You record yourself acting 2–3 parts, then transform each speaker's segments into different target voices.

ElevenLabs Voice Changer (`/v1/speech-to-speech/{voice_id}`) is **one voice per call** — so multi-voice requires:

1. Record `raw.mp3` (you doing all parts, sequentially or with natural turns)
2. **Transcribe + diarize** raw.mp3 → speaker turns with timestamps. Use `helpers/transcribe.py` (Scribe — better diarization for this) with `--num-speakers <N>`.
3. **Slice by speaker turn** — `ffmpeg -ss <start> -t <dur> -c copy raw.mp3 turn_NN_speakerX.mp3` for each turn.
4. **Voice-change each slice** — POST each `turn_NN_speakerX.mp3` to `/v1/speech-to-speech/<voice_id_for_speakerX>`, save response as `turn_NN.swapped.mp3`.
5. **Stitch back** — `ffmpeg -i concat list -c copy voice.mp3` re-concatenating in original order. Each turn keeps its original timing because slice durations are preserved by Voice Changer.

Caveats:
- **Overlap handling:** if you talk over yourself (two parts overlapping), diarization can't split them — you'd need to record cleanly without overlap.
- **Transition smoothness:** boundary between voice-changed slices may have a subtle click. Add 30 ms `afade` in/out per slice when stitching (same trick CLAUDE.md §4 uses for the dialogue-replacement pipeline).
- **API rate / quota:** N turns = N API calls. Voice Changer is more expensive than TTS in credits.

Recommendation: Path A is simpler, deterministic, and cheaper. Path B preserves your acting cadence/emotion (which TTS doesn't reproduce as well), at the cost of complexity. Pick per project.

---

## Full pipeline

```
script.txt OR raw.mp3
   │  Path A: TTS per line with EL voice IDs (eleven_v3 model)
   │  Path B: record → diarize → slice by speaker → voice-change each → stitch
   ▼
voice.mp3 (full multi-speaker dialogue)
   │  Existing helpers/transcribe.py (Scribe) or transcribe_whisper.py (WhisperX)
   │  Outputs: word-level timestamps, diarization → speaker count, SRT
   ▼
_work/edit/transcripts/voice.json
_work/edit/transcripts/voice.srt
   │  NEW utils/split_voice.py
   │  • If duration ≤ 14s: single chunk, skip splitting
   │  • If > 14s: find silences via ffmpeg silencedetect, cut into ≤14s pieces
   │  • Each chunk wrapped as MP4 (black 854×480 + AAC, lavfi color source)
   │  • Emit per-chunk manifest entries (incl. speaker tag if known from transcript)
   ▼
_work/edit/chunks/
  01.mp3 + 01.mp4 + 01.srt   (per-chunk SRT, timestamps reset to 0)
  02.mp3 + 02.mp4 + 02.srt
  …
  manifest.json   ← per chunk: idx, mp3, mp4, srt, start_in_full, end_in_full,
                    duration, text, speakers[]
  manifest.md     ← human-readable copy-paste for the gen-tool submission UI
   │  Submit each .mp4 (+ SRT / text from manifest) to Seedance/Dreamina
   ▼
_work/edit/chunks/
  01.gen.mp4      ← lip-sync good, words wrong
  02.gen.mp4
  …
   │  utils/mp4_replace_audio.py (already built) — swap each gen.mp4 audio for chunks/NN.mp3
   ▼
_work/edit/chunks/
  01.final.mp4    ← good lipsync + correct voice
  02.final.mp4
  …
   │  NEW utils/concat_chunks.py — stitch *.final.mp4 in order, no re-encode
   ▼
_work/edit/final.mp4
   │  Existing pipeline §6–§9: transcribe → ASS → burn → publish
   ▼
_work/edit/final_subs.mp4 → Buffer
```

## To build

### 0a. `utils/tts_from_script.py` — Path A (TTS)

- **Input:** `script.txt` with `[speaker_tag] line` per line; `.env` voice-id map; `--model eleven_v3`
- **Per line:** POST to `/v1/text-to-speech/{voice_id}`, save as `lines/NNN.mp3`
- **Concat:** `ffmpeg concat` with 300 ms silence between lines → `_work/voice.mp3`
- **Sidecar:** `_work/voice.script_timing.json` — `[{idx, speaker, start, duration, text}]` (lets the splitter skip a separate diarization pass)

### 0b. `utils/voice_change_multi.py` — Path B (record → multi-voice swap)

User records `raw.mp3` acting all parts with **clear pauses between turns** (no overlap). Pipeline auto-detects turns by silence, names slices in a self-describing format that carries the voice ID and character name in the filename, optionally voice-changes them, and either auto-stitches OR hands the directory off to Audition/Premiere for manual timeline assembly.

**Filename convention (per-slice):**

```
part_<part_idx>_<voice_id>_<character_name>_<turn_idx>.mp3
```

- `part_idx` — global recording session index (default 0; supports multi-recording sessions)
- `voice_id` — ElevenLabs target voice ID for this character (embedded so each file is API-ready without a separate config lookup)
- `character_name` — human-readable name (Anna, Marius…)
- `turn_idx` — 1-based per-character sequence (Anna's 1st turn, Anna's 2nd, …)

Plus a global `seq_<NNN>_` prefix is added on disk so files **sort by recording order** in any file manager and timeline import. Example:

```
seq_001_part_0_21m00Tcm4TlvDq8ikWAM_Anna_1.mp3       # Anna speaks first
seq_002_part_0_pNInz6obpgDQGcFmaJgB_Marius_1.mp3     # then Marius
seq_003_part_0_21m00Tcm4TlvDq8ikWAM_Anna_2.mp3       # back to Anna
seq_004_part_0_pNInz6obpgDQGcFmaJgB_Marius_2.mp3     # then Marius
```

**Implementation:**

- **Input:** `raw.mp3`, `--num-speakers N`, character→voice-id map (config file or `--map "0:Anna:21m00...,1:Marius:pNInz...""`)
- **Transcribe + diarize** via `helpers/transcribe.py` (Scribe; better diarization for this).
- **Group consecutive same-speaker words** into turns (use `pack_transcripts.group_into_phrases`).
- **Slice each turn:** `ffmpeg -ss S -t D -c copy raw.mp3 <slice_filename>` using the convention above. Slices land in `_work/raw_slices/`.
- **Two terminal options after slicing:**
  - **Auto:** `--apply` flag → POST each slice to `/v1/speech-to-speech/{voice_id}` with `model_id=eleven_multilingual_v2` (v3 doesn't support voice-changer yet), save next to the original as `<basename>.swapped.mp3`, then stitch with 30 ms `afade` boundaries → `_work/voice.mp3`.
  - **Manual:** default — stop after slicing. User drags `_work/raw_slices/` into Audition or Premiere where filename order = timeline order. They handle voice-change in the EL Studio UI per file, drop the swapped versions on their own timeline.
- **Sidecar:** `_work/raw_slices/manifest.json` — `[{seq, slice_file, swapped_file, character, voice_id, start_in_raw, end_in_raw, duration, text}]`. Always written, both modes.
- **Sidecar:** `_work/raw_slices/timeline.csv` — Premiere/Audition-friendly EDL-lite (filename, in-point in raw.mp3, duration, character) so the user can import as markers.
- **Quota guard:** in `--apply` mode, sum total slice duration and prompt user before running (Voice Changer credits are ~5× TTS).

### 1. Pre-split: transcribe + SRT + speaker count

Reuses existing `helpers/transcribe.py` or `helpers/transcribe_whisper.py`. The latter already supports `--srt`. The transcript JSON gives us speaker count for free (`len(set(w.speaker_id for w in words))`).

No new code — just standard usage:
```powershell
.\.venv\Scripts\python.exe helpers\transcribe_whisper.py "_work\voice.mp3" --srt
# or with Scribe for better diarization:
.\.venv\Scripts\python.exe helpers\transcribe.py "_work\voice.mp3" --num-speakers <N> --edit-dir _work\edit
```

Output the speaker count somewhere visible (echo at end of run, or note in `manifest.md`).

### 2. `utils/split_voice.py` — splitter + MP4 wrapper

Splits a voice MP3 into ≤14s chunks at natural silence boundaries, wraps each as MP4.

- **Input:** `voice.mp3`, optional `--transcript <path>` (JSON), `--max-duration 14`, `--silence-min 0.5`
- **Output dir:** `_work/edit/chunks/` (auto-detect `_work/` parent like `mp3_to_mp4.py`)
- **Short-circuit:** if total MP3 duration ≤ `--max-duration`, **skip splitting** — emit one chunk (still wrap as MP4, still write manifest with one entry).
- **Per chunk:**
  - `NN.mp3` — extracted audio (no re-encode, just `-c copy` from the original)
  - `NN.mp4` — black 854×480 @ 25 fps + AAC 192k (reuse `mp3_to_mp4.py` lavfi pattern inline)
  - `NN.srt` — per-chunk SRT with timestamps reset to 0 (offset from `start_in_full`); only if a transcript was supplied
- **`manifest.json`:**
  ```json
  [
    {
      "idx": 1,
      "mp3": "01.mp3",
      "mp4": "01.mp4",
      "srt": "01.srt",
      "start_in_full": 0.000,
      "end_in_full": 12.840,
      "duration": 12.840,
      "text": "...",
      "speakers": ["speaker_0", "speaker_1"]
    }
  ]
  ```
- **`manifest.md`:** human-readable table per chunk + summary line with total speaker count, total duration, chunk count — copy-paste-friendly for the Seedance submission UI.
- **Splitting strategy** (default): pure silence-detect via ffmpeg `silencedetect` filter, greedy-fill 14s buckets, prefer cuts at silences ≥0.5s.
- **`--transcript <path>` flag:** transcript-aware. Combine adjacent phrases until next phrase would exceed 14s; cut at the gap before. Cuts always land in a silence, never mid-word.

### 2. `utils/concat_chunks.py`

Stitch the corrected `NN.final.mp4` chunks into one MP4.

- Uses ffmpeg concat demuxer (no re-encode, fast)
- Validates all chunks have same resolution/fps/codec before stitching
- Output: `_work/edit/final.mp4` (drops in to existing §6–§9 pipeline as-is)

## Reused as-is

- `utils/mp4_replace_audio.py` — per-chunk audio swap after gen tool returns the lip-synced MP4
- `helpers/transcribe.py` / `helpers/transcribe_whisper.py` — get dialogue text for `manifest` (and for §6 caption pass)
- `utils/mp3_to_mp4.py` — currently writes a single MP4 from a single MP3; the splitter can either call it as a subprocess per chunk or share the same `lavfi` ffmpeg pattern inline. Sharing inline is faster (one ffmpeg invocation per chunk vs python startup overhead).

## Resolved

- **Chunking strategy:** silence-detect, greedy-fill 14s buckets. Transcript-aware via `--transcript` flag for natural phrase cuts.
- **Submission format:** MP4 (black-video wrap of the chunk's MP3) + per-chunk SRT + dialogue text from manifest.
- **Pre-split transcribe:** required step — produces speaker count + SRT + per-word timestamps that drive both the chunk decisions and the Seedance submission metadata.

## Open questions to resolve before building

1. **Filename convention from Seedance/Dreamina output**
   - Comes back as `01.mp4` (we rename/move) → `01.final.mp4`?
   - Or `01_generated.mp4` → `01.final.mp4`?
   - Or whatever the tool names it (we map by order)?
   - Affects how `mp4_replace_audio.py` is wrapped — likely a small batch script `utils/restore_chunks.py` to walk the chunks dir.

2. **Concat method when source chunks differ in fps/codec**
   - The lip-synced gen output may have slightly different params than our 854×480 black wraps. Concat demuxer requires identical codec/timebase.
   - Likely fix: re-encode in `concat_chunks.py` output. Slower but bulletproof.
   - Or normalize per-chunk `.final.mp4` first.

## Status

- [ ] Answer open questions 1–2
- [ ] Implement `utils/tts_from_script.py` (Path A — TTS via EL v3)
- [ ] Implement `utils/voice_change_multi.py` (Path B — record → multi-voice swap)
- [ ] Implement `utils/split_voice.py` (transcribe is reused, no new transcribe code)
- [ ] Implement `utils/concat_chunks.py`
- [ ] Optional `utils/restore_chunks.py` wrapper around `mp4_replace_audio.py`
- [ ] Update `CLAUDE.md` with the chunking pipeline once built
- [ ] End-to-end test on a real script + TTS (Path A) run
- [ ] End-to-end test on Path B (acted recording → multi-voice swap)
