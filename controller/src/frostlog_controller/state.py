"""The controller's one bit of memory: which presence judgment is in force.

Kept as one small JSON file rather than any command history -- the controller
acts once per change and nothing else needs remembering between runs.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger("frostlog_controller")


@dataclass(frozen=True)
class State:
    home: bool
    attempts: int = 0


def state_path() -> Path:
    env = os.environ.get("FROSTLOG_CONTROLLER_STATE")
    if env:
        return Path(env)
    state_home = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state_home) / "frostlog" / "controller.json"


def load(path: Path) -> State | None:
    """The stored judgment, or None when there is none yet or it cannot be trusted."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("ignoring unreadable state file %s: %s", path, exc)
        return None
    try:
        return State(home=bool(data["home"]), attempts=int(data.get("attempts", 0)))
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("ignoring malformed state file %s: %s", path, exc)
        return None


def save(path: Path, state: State) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(state)))
    tmp.replace(path)
