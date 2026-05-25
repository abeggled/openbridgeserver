"""Discovers and loads OBS logic block plugins at startup.

Two discovery paths run in order:

1. **Python entry points** (group ``obs.logic_blocks``)
   Plugins installed via ``pip install obs-plugin-*`` declare themselves here.
   The entry point value must be the module to import; the module registers
   its node types via ``@register_node_type`` at import time.

2. **plugins/ directory** (configured via ``OBS_PLUGINS_DIR`` or ``plugins_dir``
   in config.yaml).  Any ``*.py`` file that is not prefixed with ``_`` is
   imported.  Useful for local/dev plugins without a full Python package.

For the plugins/ directory path, :func:`watch_plugins_dir` can be started as an
asyncio task to pick up file additions, modifications, and deletions without a
server restart.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Maps module-name → list of type_names registered by that file.
# Used by hot-reload to unregister stale types before re-importing.
_file_types: dict[str, list[str]] = {}


def _mod_name(path: Path) -> str:
    return f"_obs_plugin_{path.stem}"


def _load_file(path: Path) -> list[str]:
    """Load or reload a single plugin file.

    Stale type registrations from a previous load of the same file are removed
    before the module is re-executed, so renamed or deleted node types don't
    linger in the registry.

    Returns the list of type names newly registered by this file.
    """
    from obs.logic.plugin_registry import _registry, _unregister

    mod_name = _mod_name(path)

    # Unregister types contributed by a previous load of this file.
    for t in _file_types.pop(mod_name, []):
        _unregister(t)

    # Drop the stale module so the exec below picks up code changes.
    sys.modules.pop(mod_name, None)

    before = frozenset(_registry.keys())
    try:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            logger.warning("Could not create module spec for %s", path)
            return []
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        logger.exception("Plugin failed to load: %s", path)
        sys.modules.pop(mod_name, None)
        return []

    new_types = sorted(frozenset(_registry.keys()) - before)
    _file_types[mod_name] = new_types
    return new_types


def _unload_file(path: Path) -> list[str]:
    """Unregister all types contributed by *path* and remove it from sys.modules.

    Returns the list of removed type names.
    """
    from obs.logic.plugin_registry import _unregister

    mod_name = _mod_name(path)
    removed = _file_types.pop(mod_name, [])
    for t in removed:
        _unregister(t)
    sys.modules.pop(mod_name, None)
    return removed


def load_plugins(plugins_dir: str | None = None) -> int:
    """Discover and load all available logic block plugins.

    Returns the number of modules successfully loaded.
    """
    count = 0

    # 1. Entry points (pip-installed plugins)
    try:
        from importlib.metadata import entry_points

        for ep in entry_points(group="obs.logic_blocks"):
            try:
                ep.load()
                logger.info("Plugin loaded (entry point): %s = %s", ep.name, ep.value)
                count += 1
            except Exception:
                logger.exception("Plugin failed to load (entry point): %s", ep.name)
    except Exception:
        logger.exception("Error reading obs.logic_blocks entry points")

    # 2. plugins/ directory (local files)
    if plugins_dir:
        p = Path(plugins_dir)
        if not p.is_dir():
            logger.warning("plugins_dir %r is not a directory — skipping", plugins_dir)
        else:
            for path in sorted(p.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                types = _load_file(path)
                if types:
                    logger.info("Plugin loaded (file): %s — types: %s", path.name, types)
                    count += 1

    return count


async def watch_plugins_dir(plugins_dir: str) -> None:
    """Watch *plugins_dir* and hot-reload plugin files as they change.

    Handles three events:

    * **added / modified** — the file is (re)loaded; stale registrations from
      a previous version of the same file are removed first.
    * **deleted** — all types contributed by the file are unregistered.

    Start as an asyncio background task after :func:`load_plugins`::

        asyncio.create_task(watch_plugins_dir(settings.plugins_dir))

    The coroutine runs until cancelled (e.g. on server shutdown).
    Requires ``watchfiles`` (already a transitive dep of ``uvicorn[standard]``).
    """
    try:
        from watchfiles import Change, awatch
    except ImportError:
        logger.warning("watchfiles not installed — plugin hot-reload disabled. Install with: pip install watchfiles")
        return

    p = Path(plugins_dir)
    if not p.is_dir():
        logger.warning("watch_plugins_dir: %r is not a directory — not watching", plugins_dir)
        return

    logger.info("Plugin hot-reload active — watching %s", p)

    try:
        async for changes in awatch(p):
            for raw_change, raw_path in changes:
                path = Path(raw_path)
                if path.suffix != ".py" or path.name.startswith("_"):
                    continue
                if raw_change in (Change.added, Change.modified):
                    types = _load_file(path)
                    if types:
                        logger.info("Plugin reloaded: %s — types: %s", path.name, types)
                    else:
                        logger.warning("Plugin reload produced no types: %s", path.name)
                elif raw_change == Change.deleted:
                    removed = _unload_file(path)
                    if removed:
                        logger.info("Plugin unloaded: %s — removed types: %s", path.name, removed)
    except Exception:
        logger.exception("Plugin watcher crashed — hot-reload disabled")
