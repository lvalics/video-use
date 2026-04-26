# Project: Seedance/Dreamina video → replace audio + burn subtitles

Daily workflow. Source material is always:
- 1 vertical MP4 from Seedance/Dreamina (AI-generated dialogue, wrong voices).
- N ElevenLabs MP3s, one per speaker, each containing all of that speaker's lines in order with natural inter-phrase silences.

Goal: replace the video's audio track with the correct voices aligned to each speaker turn, then burn captions in black-bold-on-white-box style.

Inputs land in `_work/`. All outputs go to `_work/edit/`. Never write inside the `skills/` tree.

**Caption-only mode**: if the source video already has correct audio (no separate ElevenLabs MP3s in `_work/`), skip §2–§5 entirely and run §1 → §6 → §7 → §8 → §9. Verified 2026-04-26 (`troc.mp4`, 1080×1920, 15s).

---

## Host environment (Windows)

This project runs on **Windows 11** (PowerShell 7 + Git Bash). Earlier Linux/WSL absolute paths (`/home/lvali/...`) are no longer valid here. Current layout:

| What | Path |
|---|---|
| Project root | `D:\www_2026\_test\video-use\` |
| `helpers/` | Transcription / packing helpers — `transcribe.py` (ElevenLabs Scribe API), `transcribe_whisper.py` (local Whisper Large v3 + pyannote diarization), `pack_transcripts.py`, … |
| `utils/` | Reusable tooling — Buffer publish (`publish.py`, `verify.py`); video helpers (`mp3_to_mp4.py`, `mp4_replace_audio.py`); EL/TTS pipeline (`list_voices.py`, `summarize_transcript.py`, `sync_summary_from_md.py`, `tts_test_voices.py`, `tts_full_run.py`) |
| `_work/` | Inputs (source MP4 + speaker MP3s) — never committed |
| `_work/edit/` | All run artifacts (subs, final MP4, frame samples, post IDs, upload URL) |
| `.env` (`ELEVENLABS_API_KEY` + `BUFFER_API_KEY` + `HUGGINGFACE_API_KEY`) | `D:\www_2026\_test\video-use\.env` |
| FFmpeg | `D:\ffmpeg\bin\ffmpeg.exe` (on USER PATH) |
| Python | `py` launcher → 3.12 in venv (3.14 also installed system-wide; ML libs lag 3.14 by months — keep venv on 3.12) |

**Python venv (one-time per project)** — pinned to 3.12 because WhisperX/pyannote pin `torch~=2.8` and `ctranslate2==4.4` neither of which has 3.14 wheels yet:

```powershell
cd D:\www_2026\_test\video-use
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install requests
# Local-transcription stack (~5 GB total, only if using transcribe_whisper.py):
.\.venv\Scripts\python.exe -m pip install whisperx nvidia-cublas-cu12 nvidia-cudnn-cu12
.\.venv\Scripts\python.exe -m pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu126
# subsequently: .\.venv\Scripts\python.exe helpers\transcribe_whisper.py ...
```

**UTF-8 console** — Windows defaults to cp1252 and crashes printing Romanian chars / emoji. Before running anything that emits `text` from Scribe JSON or Buffer responses:

```powershell
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

Inside Python scripts, also call `sys.stdout.reconfigure(encoding="utf-8")` at startup.

**Font substitution** — `DejaVu Sans` is **not** present on Windows. Use **Arial** with `Bold=1` in the ASS style — visually equivalent for the caption-box style at 1080×1920 (verified 2026-04-26). `fc-list` doesn't apply; check `%WINDIR%\Fonts\` directly.

---

## Transcribers — pick per run

Two helpers, **identical JSON output schema**, swap freely. `pack_transcripts.py` and the ASS builder don't care which produced the JSON.

### `helpers/transcribe.py` — ElevenLabs Scribe (cloud)

- Pros: best-in-class diarization, audio-event tags (laughter etc.), no local install.
- Cons: API quota, network round-trip, uploads audio to a third party.
- Use when: **dialogue-replacement mode** (multi-speaker source where speaker accuracy matters for cue map building). Verified solid on 2-speaker clips; expect to remain the better choice on 3–5 person dialog where short utterances ("Da", "Nu", "Ah") get bounced between speakers.

### `helpers/transcribe_whisper.py` — local Whisper Large v3 + pyannote diarization

- Pros: free, runs on the 4090 (~22s including model load for a 15s clip), nothing leaves the box. Same Whisper Large v3 model Adobe Captioneer uses.
- Cons: diarization slightly weaker than Scribe on tight back-and-forth (verified: confused "Da" speaker on `troc.mp4`); word end-times include trailing punctuation/silence (e.g. `perfect?` got 8.94→11.06 instead of 8.94→9.52); Whisper auto-cleans disfluencies (drops "Ah... Ă" filler).
- Use when: **caption-only mode**, or any run where speaker labels are nice-to-have but not load-bearing. Also when offline or rate-limited.

#### One-time HuggingFace setup for `transcribe_whisper.py`

Pyannote 4.x routes the canonical `speaker-diarization-3.1` pipeline through 4 separate gated repos. **You must accept ToS on all four** or the pipeline 403s halfway through:

1. https://huggingface.co/pyannote/speaker-diarization-3.1
2. https://huggingface.co/pyannote/segmentation-3.0
3. https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM (loaded as embedding backbone)
4. https://huggingface.co/pyannote/speaker-diarization-community-1 (pyannote 4.x rewires "3.1" through this — accepting "3.1" alone is insufficient)

Token: generate a Read token at https://huggingface.co/settings/tokens, paste into `.env` as `HUGGINGFACE_API_KEY=hf_...`. Reference benchmark vs Scribe lives at `_work/edit/transcripts/troc.scribe.json` + `_work/edit/compare.py` for future comparisons.

### Cache locations

- HuggingFace: `C:\Users\lvali\.cache\huggingface\hub\` (~3.5 GB after first WhisperX run)
- Don't activate Symlinks/Developer Mode warning is harmless — `huggingface_hub` falls back to copies; just uses more disk.

---

## Story-to-video TTS pipeline (RO source → EN voices)

Use this when the source is a **finished Romanian video** (Seedance/AI-generated narration with wrong voices) and you want to re-voice it in English with one ElevenLabs voice per character. Each step is idempotent / cached.

### Steps

1. **Transcribe + diarize** the source.
   ```powershell
   .\.venv\Scripts\python.exe helpers\transcribe.py "_work\<video>.mp4" --edit-dir _work\edit
   ```
   Use Scribe for multi-speaker stories (better diarization than WhisperX). For mono-narrator clips, WhisperX is fine.

2. **Summarize per speaker** — collapse word-level JSON into a clean per-speaker summary you can hand-edit.
   ```powershell
   .\.venv\Scripts\python.exe utils\summarize_transcript.py _work\edit\transcripts\<video>.json
   ```
   Outputs `_work/edit/<video>.summary.json` and `.summary.md`. Scribe usually undercounts speakers when several characters share a similar voice (e.g. 3 dwarves collapse into one `speaker_3`); split them in the markdown.

3. **Hand-edit the summary markdown.** Open `_work/edit/<video>.summary.md`:
   - Look up each role's voice in `voices.json`, paste the `voice_id` after the closing `|` on that speaker's row in the Speakers table.
   - Re-assign turns to new speaker IDs by editing the `` `speaker_X` `` tag inline in the Turns list (split a "dwarves" speaker into `speaker_3`/`speaker_4`/`speaker_5` per dwarf).
   - Add new rows to the Speakers table for any splits.

4. **Sync the JSON** from the edited markdown:
   ```powershell
   .\.venv\Scripts\python.exe utils\sync_summary_from_md.py _work\edit\<video>.summary.md
   ```

5. **Translate to English.** For one story, do it in-session by reading the JSON, translating in chat, writing `_work/edit/<video>.en.summary.json` directly. For batch automation, build `utils/translate_to_en.py` against an LLM API.
   - **Drop sound-effect markers** like `(intro music)`, `(laughter)`, `(door slam)` — they're already in the source video, no TTS needed. Either delete the turn or mark `"type": "sfx"`.
   - Preserve fairy-tale tone, character voice consistency, and the closing "Și-am încălecat pe-o șa…" Romanian convention (translate idiomatically, not literally).

6. **Smoke-test voices** — one substantive line per speaker:
   ```powershell
   .\.venv\Scripts\python.exe utils\tts_test_voices.py _work\edit\<video>.en.summary.json
   ```
   Output in `_work/edit/voice_tests/`. Listen. Swap any voice IDs in the markdown, re-run `sync_summary_from_md.py`, re-test.

7. **Generate all turns** once voices are locked:
   ```powershell
   .\.venv\Scripts\python.exe utils\tts_full_run.py _work\edit\<video>.en.summary.json --all
   ```
   - `--limit N` for partial runs (e.g. `--limit 5` first to validate before going full).
   - `--force` to regenerate cached MP3s.
   - Skips turns marked `"type": "sfx"` and any text starting with `(`.
   - Writes `_work/edit/tts_turns/turn_NNN_speaker_X.mp3` (filename sorts in timeline order) plus `manifest.json` with timing + voice metadata per turn.
   - Throughput on EL `eleven_v3`: ~1 turn/sec. A 70-turn story = ~75s wall time, ~4–5 MB total audio.

### Assembly: automated vs Premiere

The N individual turn MP3s now need to land on one continuous timeline as `_work/voice.mp3`, which feeds the chunker → Seedance pipeline (see `docs/todo.md`).

- **Premiere (current preferred path):** drag the entire `_work/edit/tts_turns/` bin into a Premiere audio track sorted by Name. Filename ordering matches turn order. Adjust gaps, ducking, music; export `_work/voice.mp3`.
- **`manifest.json` carries original timing** — if you want a turn to land at its source-video time (e.g. stepmother's line at 57.18s), the start/end is right there per entry.
- **Automated concat (not yet built):** `utils/concat_turns.py` would do sequential concat with a configurable gap (default 300 ms). Add when Premiere becomes the bottleneck — for one-off content the manual mix has more value.

### Voice catalog

`voices.json` at the project root holds the full EL voice library: `name`, `voice_id`, `category` (cloned / generated / premade / professional), `language`, `gender`, etc. Sorted by category. Refresh after adding/cloning new voices in EL Studio:

```powershell
.\.venv\Scripts\python.exe utils\list_voices.py
```

### EL model rules (don't mix)

| Operation | Model |
|---|---|
| TTS — text → speech (`tts_full_run.py`, `tts_test_voices.py`) | **`eleven_v3`** |
| Voice Changer — speech-to-speech (Path B in `docs/todo.md`) | **`eleven_multilingual_v2`** (v3 doesn't support voice-changer yet) |

Hardcoded as `MODEL_ID` constant in each utility, not exposed as a flag.

### Voice settings used

```json
{ "stability": 0.5, "similarity_boost": 0.75, "style": 0.0, "use_speaker_boost": true }
```

Fine for fairy-tale narration. Raise `style` for more emotional delivery; lower `stability` to add variation across re-rolls.

### Speaker-split workflow note

Scribe lumps similar voices into one `speaker_id`. The fix is purely manual editing of the markdown's Turns list — no clever clustering needed. The summary tools round-trip cleanly through `summarize_transcript.py` → hand-edit → `sync_summary_from_md.py`. After that, the rest of the pipeline (test → full run → assembly) treats every speaker as independent, voiced by the EL voice you mapped.

### Reference run (2026-04-26, `3dwarfs.mp4`)

- 6:10 source, 1561 words, Scribe detected 4 speakers → user split into 7 (1 narrator + 1 stepmother + 1 Maria + 3 dwarves + 1 bad sister)
- 70 turns → 1 dropped as sfx (`(muzică de introducere)`) → 69 generated
- ~75s on EL v3, 4.59 MB total
- Artifacts kept under `_work/edit/` for future regression / accuracy reference

---

## Fixed pipeline

### 1. Inventory

```bash
ffprobe -v error -show_entries stream=codec_type,codec_name,width,height,duration \
  -of default=nw=1 "_work/<video>.mp4"
```

Note resolution, duration, audio codec. Vertical 720×1280 is typical.

**If aspect is narrower than 9:16 (0.5625 w/h), normalize BEFORE subtitles.** Facebook Reels rejects anything narrower (e.g. 704×1280 = 0.55). Padding after burn means the subtitles are baked for the old frame and sit at the wrong height on the padded output. Do it here:

```bash
# Check: width / height < 0.5625  →  pad to 720×1280
ffmpeg -y -hide_banner -loglevel error -i "_work/<video>.mp4" \
  -vf "pad=720:1280:(ow-iw)/2:0:color=black" \
  -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -c:a copy \
  "_work/<video>_9x16.mp4"
# then use <video>_9x16.mp4 for the rest of the pipeline
```

Set the ASS `PlayResX/PlayResY` to the NORMALIZED dimensions (e.g. 720×1280), not the source's. Same for `Fontsize` and `MarginV` — they're in PlayRes units.

### 2. Transcribe all three (video + each MP3)

Pick the transcriber per the "Transcribers" section above. Both write `_work/edit/transcripts/<stem>.json` with the same schema, so `pack_transcripts.py` is identical downstream.

**Scribe (cloud)** — best for dialogue-replacement, key in `.env` as `ELEVENLABS_API_KEY`. If Scribe returns 401, ask user for a fresh key — don't loop.

```powershell
# video first
.\.venv\Scripts\python.exe helpers\transcribe.py "_work\<video>.mp4" --num-speakers <N> --edit-dir _work\edit

# MP3s in parallel (PowerShell jobs)
$j1 = Start-Job { .\.venv\Scripts\python.exe helpers\transcribe.py "_work\<mp3_1>" --edit-dir _work\edit }
$j2 = Start-Job { .\.venv\Scripts\python.exe helpers\transcribe.py "_work\<mp3_2>" --edit-dir _work\edit }
$j1, $j2 | Wait-Job | Receive-Job
```

**WhisperX (local)** — best for caption-only or offline runs, uses `HUGGINGFACE_API_KEY` from `.env`:

```powershell
.\.venv\Scripts\python.exe helpers\transcribe_whisper.py "_work\<video>.mp4" --num-speakers <N>
# add --srt to also write a clean .srt next to the JSON
# add --no-diarize for transcription-only (skips pyannote, faster)
```

Then pack regardless of which transcriber was used:

```powershell
.\.venv\Scripts\python.exe helpers\pack_transcripts.py --edit-dir _work\edit
type _work\edit\takes_packed.md
```

Read `takes_packed.md` — it shows each phrase with `[start-end]` timestamps per file. The video transcript exposes the speaker-turn cue times; each MP3 transcript exposes phrase boundaries.

### 3. Build the cue map

Manually from `takes_packed.md`, write a table:

| cue @ video (s) | mp3 | mp3 slice (s) | line |
|---|---|---|---|
| 0.06 | Brielle | 0.08–2.34 | "Când ai avut prima experiență sexuală?" |
| 2.22 | Matheus | 0.12–2.70 | "Clasa a doua, școala generală." |
| … | … | … | … |

- `cue @ video` = start of the speaker turn in the video transcript.
- `mp3 slice` = `[start-end]` of that same phrase inside that speaker's MP3.
- The slice's phrase-start word should land at the cue time on the output timeline.

**Confirm the plan in plain English with the user before running ffmpeg.**

### 4. Build audio + mux

One ffmpeg pass. Per phrase: `atrim` → `asetpts=PTS-STARTPTS` → 30ms `afade` in/out → `adelay` to cue time. Then `amix`.

**Critical: `duration=longest`, not `first`.** `duration=first` truncates at the first input's length.

Template:

```bash
ffmpeg -y -hide_banner -loglevel error \
  -i "_work/<video>.mp4" \
  -i "_work/<speaker1>.mp3" \
  -i "_work/<speaker2>.mp3" \
  -filter_complex "
    [1:a]atrim=<a>:<b>,asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.03,afade=t=out:st=<dur-0.03>:d=0.03,adelay=<ms>|<ms>[s1];
    [2:a]atrim=<a>:<b>,asetpts=PTS-STARTPTS,afade=t=in:st=0:d=0.03,afade=t=out:st=<dur-0.03>:d=0.03,adelay=<ms>|<ms>[s2];
    ...
    [s1][s2]...amix=inputs=N:duration=longest:normalize=0,aformat=channel_layouts=stereo,atrim=0:<video_dur>[aout]
  " \
  -map 0:v -map "[aout]" -c:v copy -c:a aac -b:a 192k _work/edit/final.mp4
```

Fade `st` value = slice duration − 0.03. `adelay` takes milliseconds, repeat per channel separated by `|`.

Conversational overlaps of ~100–200ms between opposite speakers are fine — read as natural dialog. Only tighten trims if user asks for zero overlap.

### 5. Verify audio

Re-transcribe `_work/edit/final.mp4` and confirm each word lands on its expected cue:

```powershell
Move-Item -Force _work\edit\transcripts\final.json _work\edit\transcripts\final.json.old -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe helpers\transcribe.py `
  _work\edit\final.mp4 --num-speakers <N> --edit-dir _work\edit
.\.venv\Scripts\python.exe -c "
import json
d=json.load(open('_work/edit/transcripts/final.json'))
for w in d.get('words',[]):
    if w.get('type')=='word':
        print(f\"  [{w['start']:5.2f}-{w['end']:5.2f}] S{w.get('speaker_id','?')[-1]} {w['text']}\")
"
```

Each cue's first word should appear within ~100ms of the target cue time. If way off, silent `amix` truncation happened — verify `duration=longest`.

### 6. Build ASS subtitles

**Do NOT use SRT + force_style for burn-in.** libass uses default PlayRes 384×288 for SRT, so MarginV values for 1280-tall video get clipped off-screen. Write a full ASS with `PlayResX`/`PlayResY` matching the video.

Chunking rule: 2–3 words per cue, break on punctuation or phrase end. Romanian punctuation → `,` and `.` and `?` are natural breaks. Keep the longest line ≤ ~20 chars to avoid line-wrap inside the box.

Style (tested on 720×1280, matches the black-bold-on-white-box reference):

```
[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Def,DejaVu Sans,54,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,1,0,0,0,100,100,0,0,3,14,0,2,40,40,420,1

[Events]
Format: Layer, Start, End, Style, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.08,0:00:00.68,Def,0,0,0,,Când ai avut
...
```

Colors in ASS are `&HAABBGGRR` (AA=00 = opaque). `BorderStyle=3` = opaque box, `Outline=14` sets the padding inside the box. `Alignment=2` = bottom-center. `MarginV=420` sits in the lower third of a 720×1280 frame.

Font `DejaVu Sans` is available on Linux (`/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`). On Windows substitute `Arial`. Don't invent a different font name or libass will silently substitute.

**For 1080×1920 sources** (Dreamina sometimes), set `PlayResX/Y` to match and scale style values 1.5×:

```
PlayResX: 1080
PlayResY: 1920
Style: Def,Arial,81,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,1,0,0,0,100,100,0,0,3,21,0,2,60,60,630,1
```

(Fontsize 54→81, Outline 14→21, MarginL/R 40→60, MarginV 420→630.) Verified 2026-04-26.

### 7. Burn subtitles (LAST)

```bash
ffmpeg -y -hide_banner -loglevel error -i _work/edit/final.mp4 \
  -vf "ass=_work/edit/master.ass" \
  -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -c:a copy \
  _work/edit/final_subs.mp4
```

Subtitles are the final filter pass. Never composite anything on top afterward.

### 8. Verify subtitles visually

Sample a frame from each caption window:

```bash
for t in 1.5 3.0 4.8 6.7 9.8; do
  ffmpeg -y -hide_banner -loglevel error -i _work/edit/final_subs.mp4 \
    -ss $t -vframes 1 "_work/edit/verify/t${t}.png"
done
```

Open each. If captions are missing or off-screen → `PlayResX/Y` mismatch. If black-on-black or invisible → color bytes wrong (should be `&H00000000` primary, `&H00FFFFFF` back).

### 9. Publish (optional)

Schedules `_work/edit/final_subs.mp4` to TikTok + Instagram Reels + Facebook Reel via Buffer.

**Always ask the user first** for:
- Caption text per platform (Romanian, the tone is "funny always" — propose 5–8 options, they pick which goes to which platform). Never post without confirmation.
- Hashtag tail (default: `#romaniatiktok #comedy #fyp #umor #scoalagenerala #mate #pentrutine` — confirm or swap).
- Date/time. Convert Europe/Bucharest → UTC (EEST = UTC+3 Apr–Oct, EET = UTC+2 Nov–Mar).

#### 9a. Upload to a public URL

Buffer **lazy-fetches** the media — not at `createPost` time, but asynchronously sometime after, and again when the post fires. You can see this by querying the post after creation:

```python
gql('query { post(input: { id: "<id>" }) { assets { ... on VideoAsset { video { durationMs width height } } } } }', token)
# durationMs=0, dim=0x0 → Buffer hasn't fetched yet. No preview in the dashboard.
# durationMs=11959, dim=720x1280 → Buffer ingested successfully. Preview visible.
```

This means the upload URL must stay alive until Buffer ingests it. **tmpfiles.org's 60-min TTL is NOT enough** — by the time Buffer tries to ingest (and certainly by post time), the URL is dead, the preview is broken, and the post fails silently.

Verified hosts (2026-04-23):

| Host | TTL | Status | Note |
|---|---|---|---|
| `transfer.sh` | — | dead | DNS/connection fails |
| `0x0.st` | — | disabled | "AI botnet spam" message |
| `catbox.moe` | permanent | ingests OK but **Buffer rejects** | HEAD returns `content-length: 0` |
| `tmpfiles.org` | 60 min | ingests but **expires before post** | silent failure — no preview, no publish |
| `filebin.net` | 6 days | broken | redirects to signed S3 URL with 15-min expiry |
| **`litterbox.catbox.moe`** | **up to 72h** | **works** | direct URL, real content-length, Buffer ingests within ~15s |

```bash
# Upload (select 72h, 24h, 12h, or 1h)
curl -sS --max-time 180 \
  -F "reqtype=fileupload" \
  -F "time=72h" \
  -F "fileToUpload=@_work/edit/final_subs.mp4" \
  https://litterbox.catbox.moe/resources/internals/api.php
# → https://litter.catbox.moe/abc123.mp4

URL="https://litter.catbox.moe/abc123.mp4"
curl -sI "$URL" | grep -i content-length   # must be nonzero
```

**Always verify ingest after creating/editing a post** — query `durationMs` and `width`. If still 0 after 30s, the URL is unreachable or the aspect ratio was rejected.

#### 9b. Schedule via Buffer

`buffer_publish.py` lives at the project root; `BUFFER_API_KEY` is in `.env`. Channels (Scuza Perfecta org `68a83c8f018d512de98d40c0`):

| Platform | displayName | channelId |
|---|---|---|
| TikTok | glumede2bani | `69e35c21031bfa423c18c737` |
| Instagram | alteglumede2bani | `69e24808031bfa423c142a8d` |
| Facebook | GlumeDe2bani | `69e24825031bfa423c142b0b` |

**TikTok** — `buffer_publish.py schedule` works directly (no `metadata` needed):

```bash
python3 buffer_publish.py schedule \
  --channel-id 69e35c21031bfa423c18c737 \
  --text "Răspunsul care te lasă mut 2 secunde. 📐

#romaniatiktok #comedy #fyp ..." \
  --video-url "$URL" \
  --due-at 2026-04-24T05:00:00Z
```

**Instagram & Facebook** — Buffer requires a platform-specific `metadata.{instagram|facebook}.type`. The shipped `buffer_publish.py` doesn't expose it, so run inline:

```python
# See git history for 2026-04-23 session for the exact block.
# Key inputs per platform:
#   Instagram: metadata: { instagram: { type: reel, shouldShareToFeed: true } }
#   Facebook:  metadata: { facebook:  { type: reel } }
# Both valid enums: post, story, reel (FB); add short/carousel/... for IG.
```

Minimal working inline call:

```python
import sys; sys.path.insert(0, '.')
from buffer_publish import load_api_key, gql
token = load_api_key()
mutation = f'''
mutation CreatePost {{
  createPost(input: {{
    text: "{text_escaped}",
    channelId: "{channel_id}",
    metadata: {{ instagram: {{ type: reel, shouldShareToFeed: true }} }},
    schedulingType: automatic,
    mode: customScheduled, dueAt: "{due_at_iso_utc}",
    assets: {{ videos: [{{ url: "{public_url}" }}] }}
  }}) {{
    ... on PostActionSuccess {{ post {{ id }} }}
    ... on MutationError {{ message }}
  }}
}}'''
print(gql(mutation, token))
```

Escape rule for inline `text`: `\\` → `\\\\`, `"` → `\\"`, `\n` → `\\n` (see `buffer_publish.py:131`).

**Known gotcha in `buffer_publish.py channels`**: query uses `$orgId: String!` but Buffer's schema wants `OrganizationId!`. Pass it directly or patch the script. Schema introspection — to discover new required fields when the API changes:

```python
q = 'query { __type(name: "InstagramPostMetadataInput") { inputFields { name type { name kind ofType { name kind } } } } }'
```

PostType enums verified 2026-04-23:
- `PostType` (IG): `post, reel, story, short, whats_new, offer, event, carousel, ghost_post, thread`
- `PostTypeFacebook`: `post, story, reel`

#### 9c. Video thumbnails (cover frames)

Without a thumbnail, Buffer's dashboard shows an empty gray tile and platforms auto-pick the first video frame — often a fade-in or a weak expression. Set a thumbnail explicitly.

`VideoAssetInput` accepts either:

- **`thumbnailUrl: String`** — a public JPG/PNG URL. Max control.
- **`metadata.thumbnailOffset: Int`** — milliseconds into the video; Buffer extracts that frame server-side. Easiest path.

Verified working 2026-04-23 (TikTok, IG Reel, FB Reel all accept the field):

```graphql
assets: { videos: [{
  url: "$URL",
  metadata: { thumbnailOffset: 3000 }   # 3000ms = 3.0s in
}] }
```

Pick the offset by sampling a frame first:

```bash
ffmpeg -y -hide_banner -loglevel error -ss 3.0 -i final_subs.mp4 \
  -vframes 1 _work/edit/verify/thumb_candidate.png
```

Rule of thumb: pick a frame where the speaker has a clean, mid-expression face (not mid-blink, not mid-word). Avoid the first second — fade-ins and platform-overlaid "AI"/watermark badges ruin the thumbnail. For short jokes, a frame ~25–40% into the clip usually beats the punchline frame (which gives the joke away on the grid).

**Retrofitting thumbnails on already-scheduled posts** — use `editPost` instead of delete+recreate:

```graphql
mutation { editPost(input: {
  id: "<postId>",
  schedulingType: automatic,      # required
  mode: customScheduled,          # required
  dueAt: "2026-04-27T06:00:00Z",  # required
  metadata: { instagram: { type: reel, shouldShareToFeed: true } },  # REQUIRED for IG/FB — otherwise
                                                                      # Buffer rejects with "requires a type"
  assets: { videos: [{
    url: "https://litter.catbox.moe/abc123.mp4",
    metadata: { thumbnailOffset: 22500 }
  }] }
}) { ... on PostActionSuccess { post { id } } ... on MutationError { message } } }
```

Gotchas:

1. `editPost` with `assets` wipes metadata — you must re-pass `metadata: { instagram: {...} }` / `metadata: { facebook: {...} }` for IG/FB. TikTok doesn't need metadata.
2. The `deletePost` payload is a union of `DeletePostSuccess { id }` and `VoidMutationError { message }` — neither has a `success` field.
3. `Query.post` takes `input: { id: "..." }`, not a bare `id` argument.
4. Subtype fragments on `assets` use `... on VideoAsset` / `ImageAsset` / `DocumentAsset` (not `PostVideoAsset`).

#### 9e. Aspect ratio constraints

Facebook Reels requires **exactly 9:16** (0.5625 w/h). A 704×1280 source (0.55) is rejected with *"Video aspect ratio is too narrow for Facebook Reels."* Two fixes:

- **Pad to 720×1280** (add 8px black bars each side):
  ```bash
  ffmpeg -y -i in.mp4 -vf "pad=720:1280:(ow-iw)/2:0:color=black" \
    -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -c:a copy out.mp4
  ```
- Or fall back to a regular FB post: `metadata: { facebook: { type: post } }` — but no Reel distribution.

TikTok and Instagram Reels are more permissive on aspect; 704×1280 goes through fine.

---

## Hard rules (don't skip)

1. **`amix duration=longest normalize=0`**. `duration=first` silently truncates. `normalize=1` over-attenuates sparse mixes.
2. **30ms `afade` on every slice**, in and out. Otherwise audible clicks at every phrase boundary.
3. **`asetpts=PTS-STARTPTS` after `atrim`** — required before `adelay` works correctly.
4. **ASS, not SRT, for burn-in.** SRT through libass uses default PlayRes that doesn't match vertical video.
5. **Font name must exist on this system.** `DejaVu Sans` is the safe default. Check `fc-list | grep -i bold` if changing.
6. **Subtitles last.** Never overlay on top of subtitles.
7. **Re-transcribe output to verify cue alignment** before declaring done.
8. **Don't re-transcribe cached sources.** Scribe outputs are immutable for a given source file.
9. **Confirm cue map in plain English before the ffmpeg pass.**

## Anti-patterns seen on this project

- Using `duration=first` in `amix` → output silent after first slice ends.
- SRT + `force_style=MarginV=420` on vertical video → subs off-screen (libass default PlayRes is 384×288).
- Letting Scribe run `--num-speakers 4` on a 12-second clip with short "Da." turn → S3 hallucinated with zero-duration words. Use `--num-speakers 2` for the final output (dialog has 2 distinct voices after replacement).
- Transcribing output for verification without first `mv`ing the cached JSON — `transcribe.py` skips when cached.

## Files

- `_work/<video>.mp4` — source video (untouched).
- `_work/<speaker>.mp3` — source voices (untouched).
- `_work/edit/transcripts/*.json` — cached transcripts (Scribe or WhisperX, same schema).
- `_work/edit/transcripts/troc.scribe.json` + `_work/edit/compare.py` — Scribe vs WhisperX reference benchmark from 2026-04-26 `troc.mp4` test. Keep as ground truth for future transcriber accuracy regressions.
- `_work/edit/takes_packed.md` — phrase-level view, the reading artifact.
- `_work/edit/<stem>.summary.json` + `<stem>.summary.md` — per-speaker summary (TTS pipeline). The MD is the hand-edit surface; the JSON is regenerated by `sync_summary_from_md.py`.
- `_work/edit/<stem>.en.summary.json` — translated, EN-text version of the summary, fed into the TTS run.
- `_work/edit/voice_tests/` — one MP3 per speaker, smoke-test of voice fit before the full run.
- `_work/edit/tts_turns/` — per-turn MP3s (`turn_NNN_speaker_X.mp3`) + `manifest.json`. Filename order = timeline order. Drag straight into Premiere.
- `_work/edit/master.ass` — subtitles.
- `_work/edit/final.mp4` — video + replaced audio, no subs.
- `_work/edit/final_subs.mp4` — deliverable (replaced audio + burned subs).
- `_work/edit/verify/` — frame samples for visual QA.
- `_work/edit/post_ids.json`, `_work/edit/litter_url.txt` — Buffer publish artifacts (per run).
- `_work/edit/project.md` — session log (append one section per run).
- `voices.json` (project root) — EL voice library cache; refreshed by `utils/list_voices.py`.

## Env

- `ELEVENLABS_API_KEY` (Scribe), `BUFFER_API_KEY` (publish), `HUGGINGFACE_API_KEY` (WhisperX) — all in `D:\www_2026\_test\video-use\.env`. No quotes, no trailing newline required. If Scribe 401, ask user for a fresh key.
- `ffmpeg`, `ffprobe` on PATH (`D:\ffmpeg\bin`). On Windows the burn-in font is `Arial Bold`, not `DejaVu Sans` — see Host environment section.

## Skill reference

The project root **is** the working tree — helpers live in `helpers/`, reusable tooling in `utils/`. Helpers: `transcribe.py`, `transcribe_whisper.py`, `pack_transcripts.py`, `timeline_view.py`, `render.py`, `grade.py`. For the daily caption-only workflow, only `transcribe.py` (or `transcribe_whisper.py`) and `pack_transcripts.py` are used — audio rebuild and subtitle burn are raw ffmpeg as shown above, because `render.py` assumes per-segment video cuts which this task doesn't need.
