from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.ops_manifest_patch import apply_patch

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "ops_manifest_patch.py"


def test_apply_patch_merges_docker_block_into_empty_manifest():
    result = apply_patch(
        None,
        {"docker": {"image": "ghcr.io/abeggled/openbridgeserver", "digest": "sha256:abc"}},
        channel="canary",
        version="2026.8.0",
        actor="release-ci",
        now="2026-07-24T10:00:00Z",
    )

    assert result["docker"] == {"image": "ghcr.io/abeggled/openbridgeserver", "digest": "sha256:abc"}
    assert result["channel"] == "canary"
    assert result["version"] == "2026.8.0"
    assert result["promoted_at"] == "2026-07-24T10:00:00Z"
    assert result["promoted_by"] == "release-ci"


def test_apply_patch_preserves_docker_block_when_patching_lxc():
    existing = {
        "channel": "canary",
        "version": "2026.8.0",
        "docker": {"image": "ghcr.io/abeggled/openbridgeserver", "digest": "sha256:abc"},
        "lxc": None,
        "promoted_at": "2026-07-24T09:00:00Z",
        "promoted_by": "release-ci",
    }

    result = apply_patch(
        existing,
        {"lxc": {"version": "2026.8.0", "asset_url": "https://example.invalid/bundle.tar.gz", "sha256": "def"}},
        channel="canary",
        version="2026.8.0",
        actor="release-ci",
        now="2026-07-24T10:05:00Z",
    )

    assert result["docker"] == existing["docker"]
    assert result["lxc"] == {"version": "2026.8.0", "asset_url": "https://example.invalid/bundle.tar.gz", "sha256": "def"}
    assert result["promoted_at"] == "2026-07-24T10:05:00Z"


def test_apply_patch_does_not_mutate_existing_dict():
    existing = {"channel": "canary", "version": None, "docker": None, "lxc": None, "promoted_at": None, "promoted_by": None}

    apply_patch(existing, {"docker": {"image": "x", "digest": "sha256:y"}}, channel="canary", version="1", actor="a", now="n")

    assert existing["docker"] is None


def test_cli_writes_and_merges_across_two_invocations(tmp_path):
    target = tmp_path / "canary.json"

    first = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--file",
            str(target),
            "--patch-json",
            json.dumps({"docker": {"image": "ghcr.io/abeggled/openbridgeserver", "digest": "sha256:abc"}}),
            "--channel",
            "canary",
            "--version",
            "2026.8.0",
            "--actor",
            "release-ci",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, first.stderr

    second = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--file",
            str(target),
            "--patch-json",
            json.dumps({"lxc": {"version": "2026.8.0", "asset_url": "https://example.invalid/bundle.tar.gz", "sha256": "def"}}),
            "--channel",
            "canary",
            "--version",
            "2026.8.0",
            "--actor",
            "release-ci",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert second.returncode == 0, second.stderr

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["docker"] == {"image": "ghcr.io/abeggled/openbridgeserver", "digest": "sha256:abc"}
    assert written["lxc"] == {"version": "2026.8.0", "asset_url": "https://example.invalid/bundle.tar.gz", "sha256": "def"}


def test_cli_rejects_invalid_patch_json(tmp_path):
    target = tmp_path / "canary.json"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--file", str(target), "--patch-json", "{not json", "--channel", "canary", "--version", "1", "--actor", "a"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "not valid JSON" in result.stderr
    assert not target.exists()
