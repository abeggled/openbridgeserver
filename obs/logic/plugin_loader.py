"""Discovers and loads OBS logic block plugins at startup.

Two discovery paths run in order:

1. **Python entry points** (group ``obs.logic_blocks``)
   Plugins installed via ``pip install obs-plugin-*`` declare themselves here.
   The entry point value must be the module to import; the module registers
   its node types via ``@register_node_type`` at import time.

2. **plugins/ directory** (configured via ``OBS_PLUGINS_DIR`` or ``plugins_dir``
   in config.yaml).  Any ``*.py`` file that is not prefixed with ``_`` is
   imported.  Useful for local/dev plugins without a full Python package.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


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
                mod_name = f"_obs_plugin_{path.stem}"
                try:
                    spec = importlib.util.spec_from_file_location(mod_name, path)
                    if spec is None or spec.loader is None:
                        logger.warning("Could not create module spec for %s", path)
                        continue
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[mod_name] = mod
                    spec.loader.exec_module(mod)  # type: ignore[union-attr]
                    logger.info("Plugin loaded (file): %s", path)
                    count += 1
                except Exception:
                    logger.exception("Plugin failed to load (file): %s", path)

    return count
