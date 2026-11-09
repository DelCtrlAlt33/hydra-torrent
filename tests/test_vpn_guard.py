"""
Tests for vpn_guard.py's interface-detection logic - the core of Hydra's
VPN kill switch. Everything downstream (pausing torrents, blocking real-IP
traffic) depends on detect_vpn_interface() correctly identifying a live VPN
adapter and correctly EXCLUDING look-alike virtual adapters (VMware,
VirtualBox, Hyper-V, Bluetooth PAN) that would otherwise cause a false
"VPN connected" reading - the one failure mode that actually matters here,
since it's a silent privacy leak, not a crash.

psutil and the `wg` subprocess call are mocked throughout - this must never
depend on the machine actually running a VPN to pass.
"""
import socket
import time
from unittest.mock import Mock, patch

import pytest

import vpn_guard


def _cmd_result(returncode=0, stdout="", stderr=""):
    return Mock(returncode=returncode, stdout=stdout, stderr=stderr)


def _stats(isup=True):
    return Mock(isup=isup)


def _addr(ip="10.0.0.5"):
    return Mock(family=socket.AF_INET, address=ip)


@pytest.mark.parametrize("iface_name", [
    "wg0",
    "NordLynx",
    "Mullvad",
    "ProtonVPN",
    "Cisco AnyConnect",
    "ExpressVPN Tap Adapter",   # contains both 'tap' and 'expressvpn' - still a real VPN
    "tun0",
    "PIA WireGuard",
])
def test_detects_known_vpn_provider_interfaces(iface_name):
    with patch("vpn_guard.psutil.net_if_stats", return_value={iface_name: _stats()}), \
         patch("vpn_guard.psutil.net_if_addrs", return_value={iface_name: [_addr("10.8.0.2")]}), \
         patch("vpn_guard._wg_handshake_ok", return_value=True):
        iface, ip = vpn_guard.detect_vpn_interface()

    assert iface == iface_name
    assert ip == "10.8.0.2"


@pytest.mark.parametrize("iface_name", [
    "Ethernet",
    "Wi-Fi",
    "Loopback Pseudo-Interface 1",
    "Bluetooth Network Connection",
    "Tailscale",   # mesh network with no internet route unless an exit node is set
    "tailscale0",
])
def test_no_false_positive_on_ordinary_interfaces(iface_name):
    with patch("vpn_guard.psutil.net_if_stats", return_value={iface_name: _stats()}), \
         patch("vpn_guard.psutil.net_if_addrs", return_value={iface_name: [_addr()]}):
        iface, ip = vpn_guard.detect_vpn_interface()

    assert iface is None
    assert ip is None


@pytest.mark.parametrize("iface_name", [
    "VMware Virtual TAP Adapter",       # matches 'tap' pattern, excluded by 'vmware'
    "VirtualBox Host-Only TAP Adapter", # matches 'tap' pattern, excluded by 'virtualbox'
    "Hyper-V Virtual Ethernet Adapter for VPN Switch",  # matches 'vpn' catch-all, excluded by 'hyper-v'
])
def test_excludes_virtual_adapters_that_look_like_vpns(iface_name):
    with patch("vpn_guard.psutil.net_if_stats", return_value={iface_name: _stats()}), \
         patch("vpn_guard.psutil.net_if_addrs", return_value={iface_name: [_addr()]}):
        iface, ip = vpn_guard.detect_vpn_interface()

    assert iface is None
    assert ip is None


def test_user_configured_ignore_list_skips_a_lan_only_tunnel():
    # A WireGuard link to the home LAN matches the 'wg' pattern but has no
    # internet route; the user can tell Hydra to ignore it.
    ifaces = {"wg-home": [_addr("10.0.0.7")], "Mullvad": [_addr("10.64.0.2")]}
    with patch("vpn_guard.psutil.net_if_stats", return_value={k: _stats() for k in ifaces}), \
         patch("vpn_guard.psutil.net_if_addrs", return_value=ifaces), \
         patch("vpn_guard._wg_handshake_ok", return_value=True), \
         patch("vpn_guard._configured_ignores", return_value=["wg-home"]):
        iface, ip = vpn_guard.detect_vpn_interface()

    assert iface == "Mullvad"
    assert ip == "10.64.0.2"


def test_ignore_list_accepts_list_or_comma_string(monkeypatch):
    import config
    monkeypatch.setattr(config, "load_config", lambda: {"vpn_ignore_interfaces": " Home , LAN-VPN ,"})
    assert vpn_guard._configured_ignores() == ["home", "lan-vpn"]
    monkeypatch.setattr(config, "load_config", lambda: {"vpn_ignore_interfaces": ["Home"]})
    assert vpn_guard._configured_ignores() == ["home"]
    monkeypatch.setattr(config, "load_config", lambda: {"vpn_ignore_interfaces": 5})
    assert vpn_guard._configured_ignores() == []


def test_skips_matching_interface_that_is_administratively_down():
    with patch("vpn_guard.psutil.net_if_stats", return_value={"tun0": _stats(isup=False)}), \
         patch("vpn_guard.psutil.net_if_addrs", return_value={"tun0": [_addr()]}):
        iface, ip = vpn_guard.detect_vpn_interface()

    assert iface is None
    assert ip is None


def test_treats_wireguard_with_stale_handshake_as_disconnected():
    # The critical kill-switch case: the OS reports the wg0 adapter as
    # "up," but the tunnel has silently died (no recent peer handshake).
    # Reporting this as connected would leave torrent traffic exposed on
    # the real interface while the UI still shows "VPN: connected."
    with patch("vpn_guard.psutil.net_if_stats", return_value={"wg0": _stats(isup=True)}), \
         patch("vpn_guard.psutil.net_if_addrs", return_value={"wg0": [_addr()]}), \
         patch("vpn_guard._wg_handshake_ok", return_value=False):
        iface, ip = vpn_guard.detect_vpn_interface()

    assert iface is None
    assert ip is None


def test_returns_none_none_on_psutil_error_instead_of_raising():
    # A kill switch that crashes on a transient psutil error is worse than
    # one that fails closed - detect_vpn_interface must degrade to "no
    # VPN detected" rather than propagate the exception.
    with patch("vpn_guard.psutil.net_if_stats", side_effect=OSError("boom")):
        iface, ip = vpn_guard.detect_vpn_interface()

    assert iface is None
    assert ip is None



# ---------------------------------------------------------------------------
# _wg_handshake_ok - regression coverage for a real bug found via live
# testing on this machine: WireGuard-for-Windows only answers `wg show`
# from an elevated process. Hydra's daemon doesn't (and shouldn't) run
# elevated, and the code's `sudo` fallback is Linux-only logic - on
# Windows it used to fall through to "tunnel is dead" for every real,
# working VPN connection. Confirmed with a real `wg show wgpia0
# latest-handshakes` → "Permission denied" on this exact box before the fix.
# ---------------------------------------------------------------------------

def test_wg_handshake_windows_permission_denied_is_unknown_not_dead(monkeypatch):
    monkeypatch.setattr(vpn_guard.os, "name", "nt")
    denied = _cmd_result(returncode=1, stderr="Unable to access interface: Permission denied")
    with patch("vpn_guard.subprocess.run", return_value=denied) as mock_run:
        assert vpn_guard._wg_handshake_ok("wgpia0") is True
    mock_run.assert_called_once()  # must NOT attempt the sudo fallback on Windows


def test_wg_handshake_linux_permission_denied_still_tries_sudo_fallback(monkeypatch):
    # The original Linux use case (CAP_NET_ADMIN missing, sudoers rule
    # grants it) must keep working - the Windows fix must not break it.
    monkeypatch.setattr(vpn_guard.os, "name", "posix")
    denied = _cmd_result(returncode=1, stderr="Unable to access interface: Operation not permitted")
    recent = _cmd_result(returncode=0, stdout=f"somepeer\t{int(time.time())}\n")
    with patch("vpn_guard.subprocess.run", side_effect=[denied, recent]) as mock_run:
        assert vpn_guard._wg_handshake_ok("wg0") is True
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[1].args[0][0] == "sudo"


def test_wg_handshake_true_for_recent_handshake():
    recent = _cmd_result(returncode=0, stdout=f"somepeer\t{int(time.time())}\n")
    with patch("vpn_guard.subprocess.run", return_value=recent):
        assert vpn_guard._wg_handshake_ok("wg0") is True


def test_wg_handshake_false_for_stale_handshake():
    stale_ts = int(time.time()) - 999  # older than _WG_HANDSHAKE_MAX_AGE (180s)
    stale = _cmd_result(returncode=0, stdout=f"somepeer\t{stale_ts}\n")
    with patch("vpn_guard.subprocess.run", return_value=stale):
        assert vpn_guard._wg_handshake_ok("wg0") is False


def test_wg_handshake_false_when_never_connected():
    never = _cmd_result(returncode=0, stdout="somepeer\t0\n")
    with patch("vpn_guard.subprocess.run", return_value=never):
        assert vpn_guard._wg_handshake_ok("wg0") is False


def test_wg_handshake_suppresses_console_window_on_windows(monkeypatch):
    # Regression test for a real, live bug found via manual testing:
    # subprocess.run(['wg', 'show', ...]) without CREATE_NO_WINDOW briefly
    # flashes a console window every time it's called - and VPNGuard calls
    # this every 2 seconds on Windows whenever a wg-pattern interface is
    # present, so without this flag it's a persistent visible flicker,
    # not a one-off.
    monkeypatch.setattr(vpn_guard.os, "name", "nt")
    # CREATE_NO_WINDOW only exists in the real subprocess module on
    # Windows - inject it so this test's assertion is meaningful when run
    # (as CI does) on Linux, instead of raising AttributeError.
    monkeypatch.setattr(vpn_guard.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    ok = _cmd_result(returncode=0, stdout=f"somepeer\t{int(time.time())}\n")
    with patch("vpn_guard.subprocess.run", return_value=ok) as mock_run:
        vpn_guard._wg_handshake_ok("wgpia0")

    _, kwargs = mock_run.call_args
    assert kwargs.get("creationflags") == 0x08000000


def test_wg_handshake_no_creationflags_on_non_windows(monkeypatch):
    # CREATE_NO_WINDOW doesn't exist on non-Windows subprocess - must not
    # be referenced there at all, or this raises AttributeError on Linux.
    monkeypatch.setattr(vpn_guard.os, "name", "posix")
    ok = _cmd_result(returncode=0, stdout=f"somepeer\t{int(time.time())}\n")
    with patch("vpn_guard.subprocess.run", return_value=ok) as mock_run:
        vpn_guard._wg_handshake_ok("wg0")

    _, kwargs = mock_run.call_args
    assert "creationflags" not in kwargs


def test_wg_handshake_true_when_wg_binary_not_installed():
    with patch("vpn_guard.subprocess.run", side_effect=FileNotFoundError()):
        assert vpn_guard._wg_handshake_ok("wg0") is True


def test_prefers_first_ipv4_address_and_ignores_ipv6():
    ipv6_addr = Mock(family=socket.AF_INET6, address="fe80::1")
    ipv4_addr = _addr("10.8.0.9")
    with patch("vpn_guard.psutil.net_if_stats", return_value={"wg0": _stats()}), \
         patch("vpn_guard.psutil.net_if_addrs", return_value={"wg0": [ipv6_addr, ipv4_addr]}), \
         patch("vpn_guard._wg_handshake_ok", return_value=True):
        iface, ip = vpn_guard.detect_vpn_interface()

    assert ip == "10.8.0.9"


# ---------------------------------------------------------------------------
# 2026-11-10 audit: a fresh Linux install could never see a WireGuard VPN, and
# a VPN that came back on a new address was not noticed.
# ---------------------------------------------------------------------------

def test_wg_handshake_sudo_refusing_is_unknown_not_dead(monkeypatch):
    # No sudoers rule for the service user (every install that did not get one
    # by hand): 'wg show' is denied, then sudo itself refuses. That is "cannot
    # check", and used to be reported as "tunnel is dead".
    monkeypatch.setattr(vpn_guard.os, "name", "posix")
    monkeypatch.setattr(vpn_guard, "_warned_cannot_verify", set())
    denied = _cmd_result(returncode=1, stderr="Unable to access interface: Operation not permitted")
    refused = _cmd_result(returncode=1, stderr="sudo: a password is required\n")
    with patch("vpn_guard.subprocess.run", side_effect=[denied, refused]) as mock_run:
        assert vpn_guard._wg_handshake_ok("wg0") is True
    assert mock_run.call_args_list[1].args[0][:2] == ["sudo", "-n"], "sudo must never be able to prompt"


def test_wg_handshake_still_false_when_wg_itself_fails_under_sudo(monkeypatch):
    # sudo worked, wg did not (e.g. not a WireGuard interface): unchanged.
    monkeypatch.setattr(vpn_guard.os, "name", "posix")
    denied = _cmd_result(returncode=1, stderr="Unable to access interface: Operation not permitted")
    wg_failed = _cmd_result(returncode=1, stderr="Unable to access interface: No such device")
    with patch("vpn_guard.subprocess.run", side_effect=[denied, wg_failed]):
        assert vpn_guard._wg_handshake_ok("wg0") is False


def _run_guard_through(statuses):
    """Feed VPNGuard a sequence of get_status() results (the first is consumed
    by start()) and return the callbacks it fired."""
    guard = vpn_guard.VPNGuard(check_interval=0.01)
    remaining = list(statuses)
    fired = []

    def fake_status():
        status = remaining.pop(0)
        if not remaining:
            guard._stop.set()
        return status

    guard.get_status = fake_status
    with patch("vpn_guard._win_notify_thread"):
        guard.start(lambda connected, iface, ip: fired.append((connected, iface, ip)))
        guard._thread.join(timeout=5)
    return fired


def test_guard_reports_a_new_vpn_address_while_still_connected():
    fired = _run_guard_through([
        (True, "wg0", "10.0.0.5"),
        (True, "wg0", "10.0.0.5"),     # nothing changed: no callback
        (True, "wg0", "10.9.9.9"),     # reconnected between two polls
    ])
    assert fired == [(True, "wg0", "10.9.9.9")]


def test_guard_still_reports_plain_drop_and_reconnect_once_each():
    fired = _run_guard_through([
        (True, "wg0", "10.0.0.5"),
        (False, None, None),
        (False, None, None),
        (True, "wg0", "10.0.0.5"),
    ])
    assert fired == [(False, None, None), (True, "wg0", "10.0.0.5")]
