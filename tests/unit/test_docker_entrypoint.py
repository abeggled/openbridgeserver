"""Container entrypoint — per-instance JWT secret bootstrap.

Docker-Pendant zum LXC-First-Boot (tools/_lxc-inner.sh): ohne diesen Schritt
läuft eine frische Compose-Installation dauerhaft auf dem Platzhalter-Secret
aus docker-compose.yml.
"""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "docker-entrypoint.sh"
# Gibt das an die Anwendung durchgereichte Secret aus – bzw. eine Markierung,
# wenn der Entrypoint die Variable gar nicht gesetzt hat.
ECHO_SECRET = ["sh", "-c", 'printf %s "${OBS_SECURITY__JWT_SECRET-<unset>}"']


def _run(data_dir: Path, secret: str | None, *, config: str | None = None) -> subprocess.CompletedProcess[str]:
    # Der Interpreter des Testlaufs zuerst: der Entrypoint liest config.yaml mit PyYAML.
    env = {
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin",
        "OBS_DATA_DIR": str(data_dir),
    }
    if secret is not None:
        env["OBS_SECURITY__JWT_SECRET"] = secret
    if config is not None:
        config_file = data_dir / "config.yaml"
        config_file.write_text(config, encoding="utf-8")
        env["OBS_CONFIG"] = str(config_file)
    result = subprocess.run(
        ["sh", str(ENTRYPOINT), *ECHO_SECRET],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result


@pytest.mark.parametrize("secret", [None, "", "changeme", "change-this-to-a-random-secret-min-32-chars"])
def test_generates_and_persists_secret_for_unset_and_placeholder_values(tmp_path: Path, secret: str | None) -> None:
    passed_through = _run(tmp_path, secret).stdout

    secret_file = tmp_path / "secrets" / "jwt-secret"
    assert secret_file.is_file()
    stored = secret_file.read_text(encoding="utf-8").strip()
    assert len(stored) >= 32
    assert passed_through == stored


def test_generated_secret_is_not_world_readable(tmp_path: Path) -> None:
    _run(tmp_path, None)

    secret_file = tmp_path / "secrets" / "jwt-secret"
    assert stat.S_IMODE(secret_file.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE((tmp_path / "secrets").stat().st_mode) & 0o077 == 0


def test_first_start_notice_stays_out_of_the_commands_stdout(tmp_path: Path) -> None:
    result = _run(tmp_path, None)

    # stdout gehoert dem exec'ten Kommando — `docker compose run obs obs-admin --json`
    # muss weiterhin reines JSON liefern.
    assert result.stdout == (tmp_path / "secrets" / "jwt-secret").read_text(encoding="utf-8").strip()
    assert "generated a per-instance JWT secret" in result.stderr


def test_second_start_reuses_the_persisted_secret(tmp_path: Path) -> None:
    first = _run(tmp_path, None).stdout
    second = _run(tmp_path, None).stdout

    assert first == second


def test_operator_secret_is_never_replaced(tmp_path: Path) -> None:
    operator_secret = "an-operator-chosen-secret-with-enough-entropy"

    assert _run(tmp_path, operator_secret).stdout == operator_secret
    assert not (tmp_path / "secrets").exists()


def test_config_yaml_secret_wins_over_generation(tmp_path: Path) -> None:
    # Env-Variable schlaegt config.yaml — ein leerer Platzhalter darf ein dort
    # gesetztes Secret weder ueberschreiben noch eine Generierung ausloesen,
    # sonst entwertet der naechste Start alle ausgegebenen Tokens.
    result = _run(tmp_path, "", config="security:\n  jwt_secret: a-secret-from-the-mounted-config-file\n")

    assert result.stdout == "<unset>"
    assert not (tmp_path / "secrets").exists()


@pytest.mark.parametrize(
    "config",
    [
        "security:\n  jwt_secret: changeme\n",
        "security:\n  jwt_secret:\n",
        "server:\n  port: 8080\n",
        "not: [valid",
    ],
)
def test_generates_when_the_config_defines_no_real_secret(tmp_path: Path, config: str) -> None:
    result = _run(tmp_path, None, config=config)

    assert result.stdout == (tmp_path / "secrets" / "jwt-secret").read_text(encoding="utf-8").strip()


def test_dockerfile_runs_the_entrypoint_before_the_server() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]' in dockerfile
    assert 'CMD ["python", "-m", "obs"]' in dockerfile
    assert "COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh" in dockerfile


def test_compose_leaves_the_jwt_secret_to_the_entrypoint() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    # Ein Platzhalter-Default hier würde den Generierungspfad des Entrypoints
    # aushebeln, sobald er nicht mehr in dessen Platzhalterliste steht.
    assert "OBS_SECURITY__JWT_SECRET: ${OBS_JWT_SECRET:-}" in compose
