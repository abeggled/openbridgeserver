"""
Mosquitto passwd file management.

Mosquitto stores passwords in format version 7 (PBKDF2-SHA512):
  username:$7$<iterations>$<salt_base64>$<hash_base64>

Changes are applied in two steps:
  1. Rebuild the passwd file from DB (all mqtt_enabled users + service account)
  2. Send SIGHUP to Mosquitto so it reloads without dropping connections
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import signal
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_ITERATIONS = 901  # matches mosquitto_passwd v7 default


def mosquitto_hash(password: str) -> str:
    """Return a Mosquitto-compatible PBKDF2-SHA512 password hash."""
    salt = os.urandom(12)
    dk = hashlib.pbkdf2_hmac("sha512", password.encode("utf-8"), salt, _ITERATIONS, dklen=64)
    return (
        f"$7${_ITERATIONS}$"
        f"{base64.b64encode(salt).decode()}$"
        f"{base64.b64encode(dk).decode()}"
    )


async def rebuild_passwd_file(
    db,
    passwd_path: str,
    service_username: str,
    service_password: str,
) -> None:
    """
    Rewrite the Mosquitto passwd file.

    Always includes the open bridge server service account (fresh hash each rebuild).
    Appends all users with mqtt_enabled=1 and a stored password hash.
    """
    rows = await db.fetchall(
        "SELECT username, mqtt_password_hash FROM users "
        "WHERE mqtt_enabled=1 AND mqtt_password_hash IS NOT NULL"
    )
    Path(passwd_path).parent.mkdir(parents=True, exist_ok=True)

    lines = [f"{service_username}:{mosquitto_hash(service_password)}"]
    for row in rows:
        lines.append(f"{row['username']}:{row['mqtt_password_hash']}")

    Path(passwd_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(
        "Mosquitto passwd file rebuilt: %d account(s) (%s + %d user(s))",
        len(lines), service_username, len(rows),
    )


async def reload_mosquitto(
    reload_command: Optional[str] = None,
    reload_pid: Optional[int] = None,
) -> None:
    """
    Signal Mosquitto to reload its passwd file (no client disconnection).

    Priority:
      1. reload_command — run this shell command (most flexible)
      2. reload_pid     — send SIGHUP to this PID (works when PID namespace is shared)
      3. Neither set   — log a warning; changes take effect on next Mosquitto restart
    """
    if reload_command:
        try:
            proc = await asyncio.create_subprocess_shell(
                reload_command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode != 0:
                logger.warning(
                    "Mosquitto reload command failed (rc=%d): %s",
                    proc.returncode, stderr.decode().strip(),
                )
            else:
                logger.info("Mosquitto reloaded via reload_command")
        except TimeoutError:
            logger.warning("Mosquitto reload command timed out")
        except Exception as exc:
            logger.warning("Mosquitto reload command error: %s", exc)
        return

    if reload_pid is not None:
        try:
            os.kill(reload_pid, signal.SIGHUP)
            logger.info("Mosquitto SIGHUP sent to PID %d", reload_pid)
        except ProcessLookupError:
            logger.warning("Mosquitto PID %d not found — is Mosquitto running?", reload_pid)
        except PermissionError:
            logger.warning(
                "Permission denied sending SIGHUP to PID %d "
                "(hint: use pid: container:mosquitto in docker-compose.yml)",
                reload_pid,
            )
        except Exception as exc:
            logger.warning("Mosquitto SIGHUP failed: %s", exc)
        return

    logger.warning(
        "Mosquitto passwd file updated but reload skipped — "
        "configure mosquitto.reload_pid or mosquitto.reload_command "
        "for changes to take effect without restarting Mosquitto"
    )
