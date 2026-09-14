"""Where the controller's files are: an explicit setting, else the XDG place for them."""

import os
from pathlib import Path


def xdg_path(override: str, base_variable: str, base_default: Path, name: str) -> Path:
    """``$override`` if set; otherwise ``$base_variable`` (or its default) ``/frostlog/name``."""
    explicit = os.environ.get(override)
    if explicit:
        return Path(explicit)
    base = os.environ.get(base_variable) or str(base_default)
    return Path(base) / "frostlog" / name
