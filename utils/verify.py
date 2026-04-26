"""Check Buffer ingest status for scheduled posts.

Reads _work/edit/post_ids.json (written by utils/publish.py) and queries each
post for video durationMs/width. Run from project root:
    .\\.venv\\Scripts\\python.exe utils\\verify.py
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from buffer_publish import load_api_key, gql

ids = json.loads((ROOT / "_work" / "edit" / "post_ids.json").read_text(encoding="utf-8"))
token = load_api_key()
QUERY = 'query { post(input: { id: "%s" }) { assets { ... on VideoAsset { video { durationMs width height } } } } }'

for name, pid in ids.items():
    d = gql(QUERY % pid, token)
    post = d.get("post") or {}
    assets = post.get("assets") or []
    v = assets[0].get("video") if assets and isinstance(assets[0], dict) else None
    if v and v.get("durationMs", 0) > 0:
        w, h, ms = v["width"], v["height"], v["durationMs"]
        print(f"  ✓ {name:10s} {w}x{h} {ms}ms")
    else:
        print(f"  ⏳ {name:10s} not ingested yet: {v}")
