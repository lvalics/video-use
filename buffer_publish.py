#!/usr/bin/env python3
"""Buffer GraphQL API client — list channels, schedule posts.

API docs: https://developers.buffer.com/guides/getting-started.html

Reads BUFFER_API_KEY from .env at project root or env var.

Usage:
    python buffer_publish.py orgs
    python buffer_publish.py channels [--org-id ID]
    python buffer_publish.py schedule --channel-id ID [--channel-id ID ...] \\
        --text "caption" \\
        [--video-url URL | --image-url URL] \\
        [--due-at 2026-05-01T10:00:00Z]   # omit for --queue
        [--queue]                          # add to queue instead of scheduling

Media note: Buffer fetches the URL you pass — it must be publicly reachable.
Local files won't work. Host via S3 / Cloudflare R2 / presigned URL first.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.buffer.com"


def load_api_key() -> str:
    for candidate in [Path(__file__).resolve().parent / ".env", Path(".env")]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "BUFFER_API_KEY":
                    return v.strip().strip('"').strip("'")
    v = os.environ.get("BUFFER_API_KEY", "")
    if not v:
        sys.exit("BUFFER_API_KEY not found in .env or environment")
    return v


def gql(query: str, token: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "buffer-publish-cli/1.0 (+python)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:500]}")
    if payload.get("errors"):
        sys.exit(f"GraphQL errors: {json.dumps(payload['errors'], indent=2)}")
    return payload["data"]


def cmd_orgs(token: str) -> None:
    data = gql(
        "query { account { organizations { id name ownerEmail } } }", token
    )
    for org in data["account"]["organizations"]:
        print(f"  {org['id']}  {org['name']}  ({org['ownerEmail']})")


def cmd_channels(token: str, org_id: str | None) -> None:
    if not org_id:
        data = gql("query { account { organizations { id name } } }", token)
        orgs = data["account"]["organizations"]
    else:
        orgs = [{"id": org_id, "name": org_id}]

    for org in orgs:
        print(f"\n# org {org['id']}  {org.get('name','')}")
        q = """
        query GetChannels($orgId: String!) {
          channels(input: {organizationId: $orgId}) {
            id name displayName service isQueuePaused
          }
        }"""
        data = gql(q, token, {"orgId": org["id"]})
        for ch in data["channels"]:
            paused = "  [PAUSED]" if ch.get("isQueuePaused") else ""
            print(
                f"  {ch['id']}  {ch['service']:12s} "
                f"{ch.get('displayName') or ch.get('name')}{paused}"
            )


def cmd_schedule(
    token: str,
    channel_ids: list[str],
    text: str,
    video_url: str | None,
    image_url: str | None,
    due_at: str | None,
    queue: bool,
) -> None:
    if queue and due_at:
        sys.exit("--queue and --due-at are mutually exclusive")
    if not queue and not due_at:
        sys.exit("must pass either --queue or --due-at ISO8601")
    if video_url and image_url:
        sys.exit("--video-url and --image-url are mutually exclusive")

    assets_block = ""
    if video_url:
        assets_block = f'assets: {{ videos: [{{ url: "{video_url}" }}] }}'
    elif image_url:
        assets_block = f'assets: {{ images: [{{ url: "{image_url}" }}] }}'

    if queue:
        scheduling = "mode: addToQueue"
    else:
        scheduling = f'mode: customScheduled, dueAt: "{due_at}"'

    # Escape text for inline GraphQL string literal.
    escaped_text = (
        text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    )

    for cid in channel_ids:
        mutation = f"""
        mutation CreatePost {{
          createPost(input: {{
            text: "{escaped_text}",
            channelId: "{cid}",
            schedulingType: automatic,
            {scheduling}
            {("," if assets_block else "") + " " + assets_block if assets_block else ""}
          }}) {{
            ... on PostActionSuccess {{
              post {{ id text assets {{ id mimeType }} }}
            }}
            ... on MutationError {{ message }}
          }}
        }}"""
        data = gql(mutation, token)
        result = data["createPost"]
        if "post" in result:
            p = result["post"]
            print(f"  ✓ {cid}  post={p['id']}  assets={len(p.get('assets') or [])}")
        else:
            print(f"  ✗ {cid}  error: {result.get('message','unknown')}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("orgs", help="list organizations")

    pc = sub.add_parser("channels", help="list channels (all orgs by default)")
    pc.add_argument("--org-id")

    ps = sub.add_parser("schedule", help="create a scheduled post")
    ps.add_argument("--channel-id", action="append", required=True, help="repeatable")
    ps.add_argument("--text", required=True)
    ps.add_argument("--video-url")
    ps.add_argument("--image-url")
    ps.add_argument("--due-at", help="ISO8601 UTC, e.g. 2026-05-01T10:00:00Z")
    ps.add_argument("--queue", action="store_true", help="add to queue instead of custom-scheduling")

    args = p.parse_args()
    token = load_api_key()

    if args.cmd == "orgs":
        cmd_orgs(token)
    elif args.cmd == "channels":
        cmd_channels(token, args.org_id)
    elif args.cmd == "schedule":
        cmd_schedule(
            token,
            args.channel_id,
            args.text,
            args.video_url,
            args.image_url,
            args.due_at,
            args.queue,
        )


if __name__ == "__main__":
    main()
