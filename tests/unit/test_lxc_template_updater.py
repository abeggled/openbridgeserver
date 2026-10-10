import io
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path


def _workflow_text() -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / ".github" / "workflows" / "lxc-template.yml").read_text(encoding="utf-8")


def _release_workflow_text() -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


def _obs_update_text() -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / "scripts" / "obs-update").read_text(encoding="utf-8")


def _extract_checksum_injection_script(workflow: str) -> str:
    """Extract the Python HEREDOC used for checksum injection."""
    m = re.search(r"python3 <<'PYEOF'[^\n]*\n(.*?)\n[ \t]*PYEOF", workflow, re.DOTALL)
    assert m, "Could not find PYEOF block"
    return textwrap.dedent(m.group(1))


def _extract_channel_info_script(obs_update: str) -> str:
    """Extract the channel-manifest-parsing Python snippet embedded in `python3 -c "..."`."""
    m = re.search(r'CHANNEL_INFO=\$\(echo "\$MANIFEST" \| python3 -c "\n(.*?)\n"\)', obs_update, re.DOTALL)
    assert m, "Could not find CHANNEL_INFO python block"
    return textwrap.dedent(m.group(1))


def _extract_asset_info_script(obs_update: str, target: str) -> str:
    """Extract the asset-selection Python snippet embedded in `python3 -c "..."`."""
    m = re.search(r'ASSET_INFO=\$\(echo "\$ALL_RELEASES" \| python3 -c "\n(.*?)\n"\)', obs_update, re.DOTALL)
    assert m, "Could not find ASSET_INFO python block"
    return m.group(1).replace("'${TARGET}'", repr(target))


def test_updater_uses_release_bundle_filename_for_download_and_extract():
    obs_update = _obs_update_text()

    assert 'BUNDLE_FILENAME=$(basename "$BUNDLE_URL")' in obs_update
    assert 'curl -fL "$BUNDLE_URL" -o "$TMP/$BUNDLE_FILENAME"' in obs_update
    assert 'tar -xzf "$TMP/$BUNDLE_FILENAME" -C "$INSTALL_DIR"' in obs_update
    assert '"$TMP/app-bundle.tar.gz"' not in obs_update


def test_updater_verifies_checksum_against_downloaded_filenames():
    obs_update = _obs_update_text()

    # Primary path: SHA-256 embedded in release notes body (stable/RC)
    assert "sha256:" in obs_update
    assert "sha256sum -c -" in obs_update
    # Secondary path: .sha256 sidecar asset (nightly builds)
    assert "sha256url:" in obs_update
    assert "sha256sum -c" in obs_update
    # Fallback path: legacy .sha512 release asset for releases predating the
    # SHA-256 migration (enables rollback/downgrade to older versions)
    assert "sha512url:" in obs_update
    assert "sha512sum -c" in obs_update
    # All paths are dispatched from the same CHECKSUM_LINE variable
    assert "CHECKSUM_LINE" in obs_update


def test_release_lxc_workflow_packages_obs_admin():
    workflow = _workflow_text()
    obs_update = _obs_update_text()

    # Bundle creation and initial rootfs install are in the workflow
    assert "requirements.txt obs-update -C scripts obs-admin" in workflow
    assert 'sudo cp    scripts/obs-admin   "$ROOTFS/opt/obs/"' in workflow
    assert 'sudo cp scripts/obs-admin "$ROOTFS/usr/local/bin/obs-admin"' in workflow
    # Self-update logic lives in scripts/obs-update
    assert 'tar -tzf "$TMP/$BUNDLE_FILENAME" > "$TMP/bundle-files.txt"' in obs_update
    assert "BUNDLE_HAS_OBS_ADMIN=false" in obs_update
    assert "grep -Eq '^(\\./)?obs-admin$'" in obs_update
    assert 'if [[ "$BUNDLE_HAS_OBS_ADMIN" == "true" ]]; then' in obs_update
    # Self-update writes to a temp file and renames atomically (no in-place
    # overwrite of a binary that may still be executing) — see issue #942 P1.
    assert "TMP_ADMIN=$(mktemp /usr/local/bin/obs-admin.XXXXXX)" in obs_update
    assert 'install -m 755 "$INSTALL_DIR/obs-admin" "$TMP_ADMIN"' in obs_update
    assert 'mv -f "$TMP_ADMIN" /usr/local/bin/obs-admin' in obs_update


def test_updater_supports_nightly_flag():
    obs_update = _obs_update_text()

    # Default-off
    assert "SHOW_NIGHTLIES=false" in obs_update
    # Both long and short flag are accepted
    assert "--nightly|-n) SHOW_NIGHTLIES=true" in obs_update
    # Nightly tags use the date-based naming pattern
    assert "nightly-" in obs_update
    assert r"nightly-(\d{4})(\d{2})(\d{2})" in obs_update
    # Menu labels carry no type suffix — the tag name itself (stable "X.Y.Z",
    # RC "X.Y.Z-RCn", nightly "nightly-YYYYMMDD") already makes the type
    # obvious; only the installed version gets a "(current)" marker.
    assert 'MARKER="  (current)"' in obs_update
    assert 'LABELS+=("$TAG${MARKER}")' in obs_update


def test_updater_fails_closed_when_sha256_missing():
    """obs-update must abort (exit 1) when no SHA-256 is found, not warn-and-continue."""
    obs_update = _obs_update_text()
    # The fail-open warning line must be gone
    assert "skipping integrity check" not in obs_update
    # The fail-closed error and exit must be present
    assert "Integrity check is required" in obs_update
    assert "exit 1" in obs_update


def test_asset_info_script_does_not_crash_when_bundle_asset_missing():
    """A release with no app-bundle asset (e.g. still uploading) must degrade to
    empty output, not crash with an unhandled TypeError on bundle['name']."""
    obs_update = _obs_update_text()
    script = _extract_asset_info_script(obs_update, "1.2.3")

    releases = [
        {
            "tag_name": "1.2.3",
            "assets": [{"name": "openbridgeserver-lxc_1.2.3_amd64.tar.zst", "browser_download_url": "https://example/x"}],
            "body": "Release notes without an embedded sha256 block",
        }
    ]

    old_stdin, old_stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(json.dumps(releases))
    buf = io.StringIO()
    sys.stdout = buf
    try:
        exec(script, {})  # noqa: S102
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout

    lines = buf.getvalue().splitlines()
    assert lines == ["", ""], "missing bundle asset must yield empty BUNDLE_URL/CHECKSUM_LINE, not a crash"


def test_checksum_injection_is_idempotent(tmp_path):
    """Running the checksum injection step twice must not produce duplicate sections."""
    workflow = _workflow_text()
    script = _extract_checksum_injection_script(workflow)

    marker = "<!-- LXC_INSERT -->"
    fake_hash = "a" * 64
    fake_name = "openbridgeserver-app-bundle_1.0.0.tar.gz"

    sha_file = tmp_path / f"{fake_name}.sha256"
    sha_file.write_text(f"{fake_hash}  {fake_name}\n")

    release_body_path = tmp_path / "release_body.txt"
    release_body_path.write_text(f"# Release\n\n{marker}\n")

    def run_script(input_body: str) -> str:
        release_body_path.write_text(input_body)
        ns: dict = {}
        patched = script.replace(
            "glob.glob('artifacts/**/*.sha256', recursive=True)",
            f"glob.glob('{tmp_path}/**/*.sha256', recursive=True)",
        ).replace(
            "'/tmp/release_body.txt'",
            f"'{release_body_path}'",
        )
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            exec(patched, ns)  # noqa: S102
        finally:
            sys.stdout = old_stdout
        return buf.getvalue()

    first_run = run_script(f"# Release\n\n{marker}\n")
    assert first_run.count("### Checksums") == 1
    assert first_run.count("**App Bundle**") == 1
    assert re.search(r"App Bundle.*?```\s*" + fake_hash, first_run, re.DOTALL)
    assert first_run.count(fake_hash) == 1

    second_run = run_script(first_run)
    assert second_run.count("### Checksums") == 1, "Duplicate checksum section on second run"
    assert second_run.count(fake_hash) == 1


def test_release_workflow_writes_docker_digest_to_canary_manifest():
    workflow = _release_workflow_text()

    # docker/build-push-action needs an id for steps.build.outputs.digest to exist
    assert "id: build\n        uses: docker/build-push-action@v7" in workflow
    assert "uses: ./.github/actions/update-ops-manifest" in workflow
    assert "channel: canary" in workflow
    assert "token: ${{ secrets.OPS_REPO_TOKEN }}" in workflow
    assert '"digest": "${{ steps.build.outputs.digest }}"' in workflow
    assert '"image": "ghcr.io/${{ github.repository }}"' in workflow


def test_release_workflow_skips_manifest_update_without_secret():
    workflow = _release_workflow_text()

    # secrets context is not allowed in `if:` — guard must go through a job-level env var
    assert "HAS_OPS_TOKEN: ${{ secrets.OPS_REPO_TOKEN != '' }}" in workflow
    assert "if: ${{ env.HAS_OPS_TOKEN == 'true' }}" in workflow


def test_lxc_workflow_writes_bundle_info_to_canary_manifest():
    workflow = _workflow_text()

    assert "OPS_REPO: abeggled/openbridgeserver-ops" in workflow
    assert 'BUNDLE_FILE="openbridgeserver-app-bundle_${VERSION}.tar.gz"' in workflow
    assert "uses: ./.github/actions/update-ops-manifest" in workflow
    assert '"lxc": {"version": "${{ steps.bundle-info.outputs.version }}"' in workflow
    # This job downloads artifacts but previously never checked out the repo —
    # the composite action reference (./.github/actions/...) requires it to exist locally.
    assert "uses: actions/checkout@v7" in workflow


def test_lxc_workflow_substitutes_ops_repo_placeholder_in_obs_update():
    workflow = _workflow_text()
    obs_update = _obs_update_text()

    assert "s|__OPS_REPO__|${{ env.OPS_REPO }}|g" in workflow
    assert 'OPS_REPO="__OPS_REPO__"' in obs_update


def test_updater_supports_channel_flag():
    obs_update = _obs_update_text()

    assert 'CHANNEL=""' in obs_update
    assert '--channel=*) CHANNEL="${arg#--channel=}" ;;' in obs_update
    assert "raw.githubusercontent.com/${OPS_REPO}/main/channels/${CHANNEL}.json" in obs_update
    # Channel mode reuses the existing sha256 verification path (CHECKSUM_LINE
    # dispatch already handles the "sha256:<hex>" prefix for the interactive flow).
    assert 'CHECKSUM_LINE="sha256:${CHANNEL_SHA256}"' in obs_update


def test_updater_errors_clearly_when_channel_has_no_published_version():
    obs_update = _obs_update_text()

    assert "Error: channel '$CHANNEL' has no published version yet." in obs_update
    assert 'if [[ -z "$TARGET" ]]; then' in obs_update


def _run_channel_info_script(obs_update: str, manifest: dict) -> list[str]:
    script = _extract_channel_info_script(obs_update)
    old_stdin, old_stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(json.dumps(manifest))
    buf = io.StringIO()
    sys.stdout = buf
    try:
        exec(script, {})  # noqa: S102
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout
    return buf.getvalue().splitlines()


def test_channel_info_script_extracts_lxc_fields_from_full_manifest():
    obs_update = _obs_update_text()
    manifest = {
        "channel": "stable",
        "version": "2026.7.0",
        "docker": {"image": "ghcr.io/abeggled/openbridgeserver", "digest": "sha256:abc"},
        "lxc": {
            "version": "2026.7.0",
            "asset_url": "https://example.invalid/openbridgeserver-app-bundle_2026.7.0.tar.gz",
            "sha256": "d" * 64,
        },
        "promoted_at": "2026-07-15T10:00:00Z",
        "promoted_by": "starwarsfan",
    }

    lines = _run_channel_info_script(obs_update, manifest)

    assert lines == [
        "2026.7.0",
        "https://example.invalid/openbridgeserver-app-bundle_2026.7.0.tar.gz",
        "d" * 64,
    ]


def test_channel_info_script_yields_empty_lines_when_lxc_not_yet_published():
    obs_update = _obs_update_text()
    manifest = {"channel": "canary", "version": None, "docker": None, "lxc": None, "promoted_at": None, "promoted_by": None}

    lines = _run_channel_info_script(obs_update, manifest)

    assert lines == ["", "", ""]


def _run_version_list_script(releases: list[dict], *, show_nightlies: bool) -> list[str]:
    """Run the VERSION_LIST Python snippet as bash would hand it to python3."""
    m = re.search(
        r'VERSION_LIST=\$\(echo "\$ALL_RELEASES" \| SHOW_NIGHTLIES="\$SHOW_NIGHTLIES" python3 -c "\n(.*?)\n"\)', _obs_update_text(), re.DOTALL
    )
    assert m, "Could not find VERSION_LIST python block"
    # Inside bash double quotes `\$` reaches python3 as a plain `$`.
    script = m.group(1).replace("\\$", "$")

    old_stdin, old_stdout = sys.stdin, sys.stdout
    old_env = os.environ.get("SHOW_NIGHTLIES")
    os.environ["SHOW_NIGHTLIES"] = "true" if show_nightlies else "false"
    sys.stdin = io.StringIO(json.dumps(releases))
    buf = io.StringIO()
    sys.stdout = buf
    try:
        exec(script, {})  # noqa: S102
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout
        if old_env is None:
            del os.environ["SHOW_NIGHTLIES"]
        else:
            os.environ["SHOW_NIGHTLIES"] = old_env
    return buf.getvalue().splitlines()


def _release(tag: str, published_at: str | None, *, prerelease: bool = False) -> dict:
    return {"tag_name": tag, "draft": False, "prerelease": prerelease, "published_at": published_at}


def test_version_list_orders_release_above_same_month_nightlies():
    """#1323: 2026.10.0 published after nightly-20261008 must be listed first,
    not below every nightly of October (patch 0 vs day of month 8)."""
    releases = [
        _release("nightly-20261008", "2026-10-08T09:10:57Z", prerelease=True),
        _release("2026.10.0", "2026-10-08T19:40:34Z"),
        _release("nightly-20261007", "2026-10-07T08:53:09Z", prerelease=True),
        _release("nightly-20261001", "2026-10-01T09:06:15Z", prerelease=True),
        _release("nightly-20260930", "2026-09-30T08:43:49Z", prerelease=True),
        _release("2026.9.1", "2026-09-08T20:22:13Z"),
        _release("2026.9.0", "2026-09-07T19:57:15Z"),
    ]

    lines = _run_version_list_script(releases, show_nightlies=True)

    assert lines == [
        "stable 2026.10.0",
        "nightly nightly-20261008",
        "nightly nightly-20261007",
        "nightly nightly-20261001",
        "nightly nightly-20260930",
        "stable 2026.9.1",
    ]


def test_version_list_without_nightlies_keeps_two_stables_newest_first():
    releases = [
        _release("nightly-20261008", "2026-10-08T09:10:57Z", prerelease=True),
        _release("2026.9.1", "2026-09-08T20:22:13Z"),
        _release("2026.10.0", "2026-10-08T19:40:34Z"),
        _release("2026.10.1-RC1", "2026-10-09T10:00:00Z", prerelease=True),
        _release("2026.9.0", "2026-09-07T19:57:15Z"),
    ]

    lines = _run_version_list_script(releases, show_nightlies=False)

    assert lines == ["rc 2026.10.1-RC1", "stable 2026.10.0", "stable 2026.9.1"]


def test_version_list_puts_release_without_published_at_last():
    releases = [
        _release("2026.9.1", None),
        _release("2026.10.0", "2026-10-08T19:40:34Z"),
    ]

    lines = _run_version_list_script(releases, show_nightlies=False)

    assert lines == ["stable 2026.10.0", "stable 2026.9.1"]


def test_updater_follows_redirects_of_transferred_repos():
    """A transferred repo answers the API with 301 + JSON body; -f alone lets it through (#1322)."""
    obs_update = _obs_update_text()

    assert 'ALL_RELEASES=$(curl -sfL "https://api.github.com/repos/${REPO}/releases")' in obs_update
    assert 'MANIFEST=$(curl -sfL "https://raw.githubusercontent.com/' in obs_update
    assert not re.search(r"curl -sf ", obs_update)


def _run_version_recovery(tmp_path: Path, version: str | None, stamp: str | None) -> tuple[str, str | None]:
    """Run the startup block of obs-update (up to the migration recovery) against a fake install dir."""
    obs_update = _obs_update_text()
    m = re.search(r"^CURRENT=\$\(cat .*?^fi\n", obs_update, re.DOTALL | re.MULTILINE)
    assert m, "Could not find CURRENT / migration recovery block"
    install_dir = tmp_path / "opt-obs"
    (install_dir / "obs").mkdir(parents=True)
    if version is not None:
        (install_dir / "version").write_text(version + "\n")
    if stamp is not None:
        (install_dir / "obs" / "version").write_text(stamp + "\n")
    script = f'set -euo pipefail\nINSTALL_DIR="{install_dir}"\n{m.group(0)}printf "%s" "$CURRENT"\n'
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    version_file = install_dir / "version"
    return result.stdout, version_file.read_text().strip() if version_file.exists() else None


def test_version_recovery_restores_real_version_after_migration_patch(tmp_path):
    current, written = _run_version_recovery(tmp_path, "2026.99.0", "2026.10.0")

    assert current == "2026.10.0"
    assert written == "2026.10.0"


def test_version_recovery_leaves_regular_version_untouched(tmp_path):
    current, written = _run_version_recovery(tmp_path, "2026.11.0", "2026.10.0")

    assert current == "2026.11.0"
    assert written == "2026.11.0"


def test_version_recovery_keeps_patch_tag_without_version_stamp(tmp_path):
    current, written = _run_version_recovery(tmp_path, "2026.99.0", None)

    assert current == "2026.99.0"
    assert written == "2026.99.0"
