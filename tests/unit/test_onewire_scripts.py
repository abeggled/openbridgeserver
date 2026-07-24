from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
SHOULD_RUN = SCRIPTS_DIR / "obs-onewire-should-run.sh"
CONFIGURE = SCRIPTS_DIR / "obs-onewire-configure.sh"


def _run(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    full_env = {"PATH": os.environ["PATH"], **env}
    return subprocess.run(
        ["/bin/sh", str(script)],
        check=False,
        env=full_env,
        capture_output=True,
        text=True,
    )


class TestShouldRun:
    def test_exits_zero_when_usb_all_true(self):
        result = _run(SHOULD_RUN, {"OBS_ONEWIRE__USB_ALL": "true"})
        assert result.returncode == 0

    def test_exits_zero_when_pbm_devices_set(self):
        result = _run(SHOULD_RUN, {"OBS_ONEWIRE__PBM_DEVICES": "/dev/ttyUSB0"})
        assert result.returncode == 0

    def test_exits_zero_when_both_set(self):
        result = _run(
            SHOULD_RUN,
            {"OBS_ONEWIRE__USB_ALL": "true", "OBS_ONEWIRE__PBM_DEVICES": "/dev/ttyUSB0"},
        )
        assert result.returncode == 0

    def test_exits_nonzero_when_neither_set(self):
        result = _run(SHOULD_RUN, {})
        assert result.returncode != 0

    def test_exits_nonzero_when_usb_all_is_not_literally_true(self):
        result = _run(SHOULD_RUN, {"OBS_ONEWIRE__USB_ALL": "false"})
        assert result.returncode != 0

    def test_exits_nonzero_when_pbm_devices_is_empty_string(self):
        result = _run(SHOULD_RUN, {"OBS_ONEWIRE__PBM_DEVICES": ""})
        assert result.returncode != 0


class TestConfigure:
    def test_writes_usb_all_line(self, tmp_path: Path):
        conf = tmp_path / "owfs.conf"
        result = _run(
            CONFIGURE,
            {"OBS_ONEWIRE__USB_ALL": "true", "OBS_ONEWIRE_CONF_PATH": str(conf)},
        )
        assert result.returncode == 0
        content = conf.read_text()
        assert "server: usb = all" in content
        assert "server: port = 4304" in content
        assert "server: pbm" not in content

    def test_writes_single_pbm_device_line(self, tmp_path: Path):
        conf = tmp_path / "owfs.conf"
        result = _run(
            CONFIGURE,
            {"OBS_ONEWIRE__PBM_DEVICES": "/dev/ttyUSB0", "OBS_ONEWIRE_CONF_PATH": str(conf)},
        )
        assert result.returncode == 0
        content = conf.read_text()
        assert "server: pbm = /dev/ttyUSB0" in content
        assert "server: usb = all" not in content

    def test_writes_multiple_comma_separated_pbm_device_lines(self, tmp_path: Path):
        conf = tmp_path / "owfs.conf"
        result = _run(
            CONFIGURE,
            {
                "OBS_ONEWIRE__PBM_DEVICES": "/dev/serial/by-id/usb-FTDI_a,/dev/serial/by-id/usb-FTDI_b",
                "OBS_ONEWIRE_CONF_PATH": str(conf),
            },
        )
        assert result.returncode == 0
        content = conf.read_text()
        assert "server: pbm = /dev/serial/by-id/usb-FTDI_a" in content
        assert "server: pbm = /dev/serial/by-id/usb-FTDI_b" in content

    def test_writes_both_usb_all_and_pbm_devices(self, tmp_path: Path):
        conf = tmp_path / "owfs.conf"
        result = _run(
            CONFIGURE,
            {
                "OBS_ONEWIRE__USB_ALL": "true",
                "OBS_ONEWIRE__PBM_DEVICES": "/dev/ttyUSB0",
                "OBS_ONEWIRE_CONF_PATH": str(conf),
            },
        )
        assert result.returncode == 0
        content = conf.read_text()
        assert "server: usb = all" in content
        assert "server: pbm = /dev/ttyUSB0" in content

    def test_custom_port_overrides_default(self, tmp_path: Path):
        conf = tmp_path / "owfs.conf"
        result = _run(
            CONFIGURE,
            {"OBS_ONEWIRE__PORT": "4305", "OBS_ONEWIRE_CONF_PATH": str(conf)},
        )
        assert result.returncode == 0
        content = conf.read_text()
        assert "server: port = 4305" in content
        assert "server: port = 4304" not in content

    def test_writes_only_port_line_when_nothing_configured(self, tmp_path: Path):
        conf = tmp_path / "owfs.conf"
        result = _run(CONFIGURE, {"OBS_ONEWIRE_CONF_PATH": str(conf)})
        assert result.returncode == 0
        content = conf.read_text()
        assert "server: usb" not in content
        assert "server: pbm" not in content
        assert "server: port = 4304" in content
