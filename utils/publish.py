"""Schedule the rendered MP4 to TikTok + IG Reel + FB Reel via Buffer.

Edit the per-run constants (URL, DUE_AT, THUMB_MS, TEXT) below for each session,
then run from project root:
    .\\.venv\\Scripts\\python.exe utils\\publish.py

Writes the resulting post IDs to _work/edit/post_ids.json so utils/verify.py
can re-check ingest later.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from buffer_publish import load_api_key, gql

# === Per-run constants — edit these per session ===
URL = "https://litter.catbox.moe/i3inru.mp4"
DUE_AT = "2026-04-26T06:00:00Z"
THUMB_MS = 5000
TEXT = (
    "Memorie selectivă: nivel avansat 🧠\n\n"
    "#romaniatiktok #comedy #fyp #umor #cuplu #relatii #vecina #pentrutine"
)

CHANNELS = [
    ("TikTok",    "69e35c21031bfa423c18c737", ""),
    ("Instagram", "69e24808031bfa423c142a8d", "metadata: { instagram: { type: reel, shouldShareToFeed: true } },"),
    ("Facebook",  "69e24825031bfa423c142b0b", "metadata: { facebook: { type: reel } },"),
]

token = load_api_key()
escaped = TEXT.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

post_ids = {}
for name, cid, meta in CHANNELS:
    mutation = f"""
    mutation CreatePost {{
      createPost(input: {{
        text: "{escaped}",
        channelId: "{cid}",
        {meta}
        schedulingType: automatic,
        mode: customScheduled, dueAt: "{DUE_AT}",
        assets: {{ videos: [{{
          url: "{URL}",
          metadata: {{ thumbnailOffset: {THUMB_MS} }}
        }}] }}
      }}) {{
        ... on PostActionSuccess {{ post {{ id }} }}
        ... on MutationError {{ message }}
      }}
    }}
    """
    try:
        data = gql(mutation, token)
        result = data["createPost"]
        if "post" in result:
            pid = result["post"]["id"]
            post_ids[name] = pid
            print(f"  ✓ {name:10s} post={pid}")
        else:
            print(f"  ✗ {name:10s} error: {result.get('message','unknown')}")
    except SystemExit as e:
        print(f"  ✗ {name:10s} HTTP/GraphQL error: {e}")

out = ROOT / "_work" / "edit" / "post_ids.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(post_ids, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nSaved {len(post_ids)}/{len(CHANNELS)} post IDs to {out.relative_to(ROOT)}")
