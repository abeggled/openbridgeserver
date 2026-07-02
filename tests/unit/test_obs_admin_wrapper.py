from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_obs_admin_wrapper_parses_systemd_env_file_without_sourcing(tmp_path: Path):
    app_dir = tmp_path / "opt" / "obs"
    (app_dir / "obs").mkdir(parents=True)
    python_path = app_dir / "venv" / "bin" / "python3"
    python_path.parent.mkdir(parents=True)
    python_path.write_text(
        """#!/bin/sh
printf '%s\\n' "cwd=$PWD"
printf '%s\\n' "OBS_CONFIG=$OBS_CONFIG"
printf '%s\\n' "OBS_DATABASE__PATH=$OBS_DATABASE__PATH"
printf '%s\\n' "OPENTWS_CONFIG=$OPENTWS_CONFIG"
printf '%s\\n' "OPENTWS_DATABASE__PATH=$OPENTWS_DATABASE__PATH"
printf '%s\\n' "OBS_MOSQUITTO__RELOAD_COMMAND=${OBS_MOSQUITTO__RELOAD_COMMAND-unset}"
printf '%s\\n' "args=$*"
""",
        encoding="utf-8",
    )
    python_path.chmod(0o755)

    env_file = tmp_path / "obs.env"
    env_file.write_text(
        "\n".join(
            [
                "# systemd EnvironmentFile syntax, not shell code",
                "OBS_DATABASE__PATH=/data/obs.db",
                "OBS_CONFIG=/data/config with spaces.yaml",
                "OPENTWS_DATABASE__PATH=/legacy/opentws.db",
                "OPENTWS_CONFIG=/legacy/config.yaml",
                "OBS_MOSQUITTO__RELOAD_COMMAND=systemctl reload mosquitto",
            ]
        ),
        encoding="utf-8",
    )

    wrapper = Path(__file__).resolve().parents[2] / "scripts" / "obs-admin"
    env = {
        "PATH": os.environ["PATH"],
        "OBS_ADMIN_APP_DIR": str(app_dir),
        "OBS_ADMIN_ENV_FILE": str(env_file),
        "OBS_ADMIN_PYTHON": str(python_path),
    }

    result = subprocess.run(
        ["/bin/sh", str(wrapper), "status"],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        f"cwd={app_dir}",
        "OBS_CONFIG=/data/config with spaces.yaml",
        "OBS_DATABASE__PATH=/data/obs.db",
        "OPENTWS_CONFIG=/legacy/config.yaml",
        "OPENTWS_DATABASE__PATH=/legacy/opentws.db",
        "OBS_MOSQUITTO__RELOAD_COMMAND=unset",
        "args=-m obs.admin_cli status",
    ]
