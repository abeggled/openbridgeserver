"""owserver must only be reachable locally — it has no authentication (#1288)."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _socket_dropin() -> str:
    text = (ROOT / "tools" / "_lxc-inner.sh").read_text(encoding="utf-8")
    m = re.search(
        r"cat > /etc/systemd/system/owserver\.socket\.d/override\.conf << 'EOF'\n(.*?)\nEOF\n",
        text,
        re.DOTALL,
    )
    assert m, "LXC template must install a drop-in for owserver.socket"
    return m.group(1)


def test_lxc_socket_dropin_resets_packaged_listen_stream():
    lines = _socket_dropin().splitlines()
    assert lines[0] == "[Socket]"
    # An empty ListenStream= clears the package's bare `ListenStream=4304`
    # (all interfaces); without it systemd would add a second listener.
    assert lines.index("ListenStream=") < lines.index("ListenStream=127.0.0.1:4304")


def test_lxc_socket_dropin_listens_on_loopback_only():
    streams = [line.split("=", 1)[1] for line in _socket_dropin().splitlines() if line.startswith("ListenStream=")]
    assert [s for s in streams if s] == ["127.0.0.1:4304"]


def test_compose_owserver_port_is_published_on_loopback_only():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    ports = compose["services"]["owserver"].get("ports", [])
    assert ports, "expected the owserver port to stay published for local debugging"
    for port in ports:
        assert str(port).startswith("127.0.0.1:"), port
