from pathlib import Path

from frostlog_controller.state import State, load, save


def test_load_missing_file_returns_none(tmp_path: Path) -> None:
    assert load(tmp_path / "controller.json") is None


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state" / "controller.json"
    save(path, State(home=True, attempts=2))
    assert load(path) == State(home=True, attempts=2)


def test_load_malformed_json_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "controller.json"
    path.write_text("not json")
    assert load(path) is None


def test_load_missing_home_key_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "controller.json"
    path.write_text('{"attempts": 1}')
    assert load(path) is None


def test_load_defaults_attempts_to_zero(tmp_path: Path) -> None:
    path = tmp_path / "controller.json"
    path.write_text('{"home": false}')
    assert load(path) == State(home=False, attempts=0)
