#!/usr/bin/env python3
"""Merge a JSON patch into an openbridgeserver-ops channel manifest file.

Used by .github/actions/update-ops-manifest to write the Docker digest (from
release.yml) and the LXC bundle info (from lxc-template.yml) into the same
channels/<channel>.json file without either workflow clobbering the other's
block — see AGENTS.MD "Adding a New Adapter"-style docs for the composite
action that drives this from CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def apply_patch(existing: dict | None, patch: dict, *, channel: str, version: str, actor: str, now: str) -> dict:
    result = dict(existing) if existing else {}
    for key, value in patch.items():
        result[key] = value
    result["channel"] = channel
    result["version"] = version
    result["promoted_at"] = now
    result["promoted_by"] = actor
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="Path to channels/<channel>.json to update (created if missing)")
    parser.add_argument("--patch-json", required=True, help="JSON object to shallow-merge, e.g. '{\"docker\": {...}}'")
    parser.add_argument("--channel", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args(argv)

    try:
        patch = json.loads(args.patch_json)
    except json.JSONDecodeError as exc:
        print(f"Error: --patch-json is not valid JSON: {exc}", file=sys.stderr)
        return 1

    path = Path(args.file)
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    result = apply_patch(existing, patch, channel=args.channel, version=args.version, actor=args.actor, now=now)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {path}: version={result['version']}, channel={result['channel']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
