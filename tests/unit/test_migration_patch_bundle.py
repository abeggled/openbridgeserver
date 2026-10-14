"""tools/build-migration-patch-bundle.sh — patch release for the organisation move (#1322)."""

import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "build-migration-patch-bundle.sh"
TAG = "2026.99.0"
BUNDLE = f"openbridgeserver-app-bundle_{TAG}.tar.gz"

# Byte offset right after `cp "$INSTALL_DIR/obs-update" /usr/local/bin/obs-update`
# in the heredoc obs-update of 2026.4.5–2026.5.2 and 2026.6.x, for the test repo
# starwarsfan/obs-ng and for abeggled/openbridgeserver. bash resumes reading the
# replaced script at exactly this offset.
LEGACY_RESUME_OFFSETS = [3074, 3081, 3636, 3643]


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("patch")
    subprocess.run(
        [str(TOOL), "--repo", "open-bridge-project/openbridgeserver", "--ops-repo", "open-bridge-project/ops", "--out", str(out)],
        check=True,
        capture_output=True,
    )
    return out


def _patched_obs_update(built: Path) -> str:
    with tarfile.open(built / BUNDLE) as tar:
        return tar.extractfile("obs-update").read().decode()


def test_bundle_contains_only_obs_update_and_obs_admin(built):
    with tarfile.open(built / BUNDLE) as tar:
        assert sorted(tar.getnames()) == ["obs-admin", "obs-update"]


def test_patched_obs_update_points_to_new_repo(built):
    script = _patched_obs_update(built)

    assert 'REPO="open-bridge-project/openbridgeserver"' in script
    assert 'OPS_REPO="open-bridge-project/ops"' in script
    assert "__REPO__" not in script
    assert subprocess.run(["bash", "-n"], input=script, text=True, check=False).returncode == 0


def test_sidecars_and_release_body_carry_the_bundle_checksum(built):
    data = (built / BUNDLE).read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()

    assert (built / f"{BUNDLE}.sha256").read_text().split() == [sha256, BUNDLE]
    assert (built / f"{BUNDLE}.sha512").read_text().split() == [hashlib.sha512(data).hexdigest(), BUNDLE]

    # Same extraction obs-update uses on the release body.
    obs_update = (ROOT / "scripts" / "obs-update").read_text()
    m = re.search(r'ASSET_INFO=\$\(echo "\$ALL_RELEASES" \| python3 -c "\n(.*?)\n"\)', obs_update, re.DOTALL)
    code = m.group(1).replace("'${TARGET}'", repr(TAG))
    release = {
        "tag_name": TAG,
        "body": (built / "release-notes.md").read_text(),
        "assets": [{"name": BUNDLE, "browser_download_url": f"https://x/{BUNDLE}"}],
    }
    old_stdin, old_stdout = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = io.StringIO(json.dumps([release])), io.StringIO()
    try:
        exec(code, {})  # noqa: S102
        out = sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout
    assert out.splitlines()[1] == f"sha256:{sha256}"


@pytest.mark.parametrize("offset", LEGACY_RESUME_OFFSETS)
def test_legacy_in_place_self_update_lands_in_hand_off(built, tmp_path, offset):
    """An old obs-update that overwrites itself with cp resumes in the new file at its own offset."""
    install_dir = tmp_path / "opt-obs"
    install_dir.mkdir()
    (install_dir / "version").write_text("2026.12.0\n")
    stub = tmp_path / "stub"
    stub.mkdir()
    log = tmp_path / "systemctl.log"
    (stub / "systemctl").write_text(f'#!/bin/sh\necho "$*" >> "{log}"\n')
    (stub / "systemctl").chmod(0o755)
    new_script = tmp_path / "new-obs-update"
    new_script.write_text(_patched_obs_update(built))
    running = tmp_path / "obs-update"

    head = (
        "#!/bin/bash\nset -euo pipefail\n"
        f'INSTALL_DIR="{install_dir}"\nSERVICE="obs"\nTARGET="{TAG}"\n'
        f'echo "$TARGET" > /dev/null\nsystemctl stop "$SERVICE"\n'
    )
    cp_line = f'cp "{new_script}" "{running}"\n'
    filler = offset - len(head.encode()) - len(cp_line.encode())
    assert filler > 2
    head += "#" * (filler - 1) + "\n"
    tail = 'echo "$TARGET" > "$INSTALL_DIR/version"\nsystemctl start "$SERVICE"\necho "OLD TAIL"\n'
    running.write_text(head + cp_line + tail)
    assert len((head + cp_line).encode()) == offset

    result = subprocess.run(
        ["bash", str(running)],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{stub}:/usr/bin:/bin"},
    )

    assert result.returncode == 0, result.stderr
    assert "now fetches releases from open-bridge-project/openbridgeserver" in result.stdout
    assert "OLD TAIL" not in result.stdout
    assert log.read_text().splitlines() == ["stop obs", "start obs"]
    # The hand-off keeps the real version instead of writing the patch tag.
    assert (install_dir / "version").read_text().strip() == "2026.12.0"


def test_fresh_run_skips_hand_off_and_reaches_version_menu(built, tmp_path):
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "curl").write_text("#!/bin/sh\necho '[]'\n")
    (stub / "curl").chmod(0o755)
    script = tmp_path / "obs-update"
    script.write_text(_patched_obs_update(built))

    result = subprocess.run(
        ["bash", str(script)],
        input="0\n",
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": f"{stub}:/usr/bin:/bin"},
    )

    assert result.returncode == 0, result.stderr
    assert "Fetching release information..." in result.stdout
    assert "Cancelled." in result.stdout
    assert "Run obs-update again" not in result.stdout
