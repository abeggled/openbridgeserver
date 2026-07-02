import io
import re
import sys
import textwrap
from pathlib import Path


def _workflow_text() -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / ".github" / "workflows" / "lxc-template.yml").read_text(encoding="utf-8")


def _obs_update_text() -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / "scripts" / "obs-update").read_text(encoding="utf-8")


def _extract_checksum_injection_script(workflow: str) -> str:
    """Extract the Python HEREDOC used for checksum injection."""
    m = re.search(r"python3 <<'PYEOF'[^\n]*\n(.*?)\n[ \t]*PYEOF", workflow, re.DOTALL)
    assert m, "Could not find PYEOF block"
    return textwrap.dedent(m.group(1))


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
    assert 'TMP_ADMIN=$(mktemp /usr/local/bin/obs-admin.XXXXXX)' in obs_update
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
    # Nightly builds are labelled distinctly in the menu
    assert "(nightly" in obs_update


def test_updater_fails_closed_when_sha256_missing():
    """obs-update must abort (exit 1) when no SHA-256 is found, not warn-and-continue."""
    obs_update = _obs_update_text()
    # The fail-open warning line must be gone
    assert "skipping integrity check" not in obs_update
    # The fail-closed error and exit must be present
    assert "Integrity check is required" in obs_update
    assert "exit 1" in obs_update


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
    assert first_run.count(fake_hash) == 1

    second_run = run_script(first_run)
    assert second_run.count("### Checksums") == 1, "Duplicate checksum section on second run"
    assert second_run.count(fake_hash) == 1
