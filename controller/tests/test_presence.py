import subprocess

from frostlog_controller.presence import is_home, parse_active_ssids


def _result(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_parse_active_ssids_picks_only_active_lines() -> None:
    output = "yes:Home\nno:Neighbour\nno:Coffee Shop\n"
    assert parse_active_ssids(output) == ["Home"]


def test_parse_active_ssids_unescapes_colons_in_the_ssid() -> None:
    # nmcli -t escapes a literal ':' inside a field as '\:'; only the first,
    # unescaped ':' on the line separates ACTIVE from SSID.
    output = "yes:5\\:30 Garage\n"
    assert parse_active_ssids(output) == ["5:30 Garage"]


def test_parse_active_ssids_ignores_blank_lines() -> None:
    assert parse_active_ssids("\nyes:Home\n\n") == ["Home"]


def test_parse_active_ssids_no_active_network() -> None:
    assert parse_active_ssids("no:Home\n") == []


def test_is_home_true_when_active_ssid_matches() -> None:
    assert is_home("Home", runner=lambda: _result("yes:Home\n")) is True


def test_is_home_false_for_a_non_home_active_ssid() -> None:
    assert is_home("Home", runner=lambda: _result("yes:Cafe Wi-Fi\n")) is False


def test_is_home_false_when_nothing_is_active() -> None:
    assert is_home("Home", runner=lambda: _result("no:Home\n")) is False


def test_is_home_unknown_when_nmcli_is_missing() -> None:
    def runner() -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("nmcli")

    assert is_home("Home", runner=runner) is None


def test_is_home_unknown_when_nmcli_times_out() -> None:
    def runner() -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="nmcli", timeout=10)

    assert is_home("Home", runner=runner) is None


def test_is_home_unknown_when_nmcli_exits_nonzero() -> None:
    assert is_home("Home", runner=lambda: _result("", returncode=1, stderr="boom")) is None
