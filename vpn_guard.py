import os
import socket
import threading
import time
import logging

import psutil

logger = logging.getLogger('hydra_torrent')

# Interface name fragments covering all major VPN clients.
# Checked as substrings (case-insensitive) against the adapter name.
_VPN_PATTERNS = [
    # WireGuard (generic)
    'wg',
    # PIA
    'wgpia', 'pia',
    # NordVPN (NordLynx = WireGuard, OpenVPN uses TAP)
    'nordlynx', 'nord',
    # OpenVPN / generic TAP-TUN adapters
    'tun', 'tap', 'ovpn', 'openvpn',
    # Mullvad
    'mullvad',
    # ProtonVPN
    'proton', 'pvpn',
    # ExpressVPN
    'expressvpn', 'xvpn',
    # Tailscale
    'tailscale',
    # Cisco AnyConnect
    'anyconnect',
    # Generic catch-all
    'vpn',
]

# Adapters that match the patterns above but are NOT VPNs — excluded.
_VPN_EXCLUDE = [
    'vmware', 'virtualbox', 'vbox', 'hyper-v', 'loopback', 'bluetooth',
]


def detect_vpn_interface():
    """
    Find any active VPN interface.
    Works with PIA, NordVPN, ExpressVPN, Mullvad, ProtonVPN, OpenVPN,
    WireGuard, Tailscale, Cisco AnyConnect, and any adapter whose name
    contains a known VPN keyword.
    Returns (interface_name, ip_address) or (None, None).
    """
    try:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()

        for iface_name, addr_list in addrs.items():
            iface_lower = iface_name.lower()

            # Must match a VPN pattern
            if not any(pat in iface_lower for pat in _VPN_PATTERNS):
                continue

            # Must NOT be a known non-VPN virtual adapter
            if any(ex in iface_lower for ex in _VPN_EXCLUDE):
                continue

            # Interface must be up
            if iface_name in stats and not stats[iface_name].isup:
                continue

            # Find the IPv4 address
            for addr in addr_list:
                if addr.family == socket.AF_INET and addr.address:
                    return iface_name, addr.address
    except Exception as e:
        logger.error(f"VPN detection error: {e}")

    return None, None


# Backward-compat alias
detect_pia_interface = detect_vpn_interface


def get_public_ip(timeout=5, source_ip=None):
    """
    Fetch the current public IP as seen by the internet.
    If source_ip is given, bind the outgoing request to that address so the
    request exits through the VPN interface rather than the default route.
    Tries two services for reliability. Returns an IP string or None.
    """
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.connection import allowed_gai_family
    import urllib3

    class SourceIPAdapter(HTTPAdapter):
        def __init__(self, source_address, **kwargs):
            self._source_address = source_address
            super().__init__(**kwargs)

        def init_poolmanager(self, *args, **kwargs):
            kwargs['source_address'] = (self._source_address, 0)
            super().init_poolmanager(*args, **kwargs)

    session = requests.Session()
    if source_ip:
        adapter = SourceIPAdapter(source_ip)
        session.mount('https://', adapter)
        session.mount('http://', adapter)

    for url in ('https://api.ipify.org', 'https://icanhazip.com'):
        try:
            resp = session.get(url, timeout=timeout)
            ip = resp.text.strip()
            if ip:
                return ip
        except Exception:
            continue
    return None


def _win_notify_thread(wake_event, stop_event):
    """
    Windows-only daemon thread.

    Calls NotifyAddrChange() in a loop — each call blocks until Windows
    detects any IP address change (interface up/down, DHCP renewal, VPN
    connect/disconnect).  Sets wake_event immediately so the monitor loop
    fires within milliseconds instead of waiting for the next poll cycle.

    Falls back silently if the Win32 API is unavailable.

    IMPORTANT: when WaitForSingleObject times out we MUST call
    CancelIPChangeNotify before the overlapped struct goes out of scope.
    If we don't, the kernel holds a dangling pointer to the freed ctypes
    struct and writes completion data to it when the next IP change occurs,
    causing a crash/access violation.
    """
    try:
        import ctypes
        import ctypes.wintypes as wt

        kernel32  = ctypes.WinDLL('kernel32',  use_last_error=True)
        iphlpapi  = ctypes.WinDLL('iphlpapi',  use_last_error=True)

        class OVERLAPPED(ctypes.Structure):
            _fields_ = [
                ('Internal',     ctypes.c_ulong),
                ('InternalHigh', ctypes.c_ulong),
                ('Offset',       wt.DWORD),
                ('OffsetHigh',   wt.DWORD),
                ('hEvent',       wt.HANDLE),
            ]

        WAIT_OBJECT_0    = 0x00000000
        WAIT_TIMEOUT_VAL = 0x00000102

        while not stop_event.is_set():
            # Fresh auto-reset event for each notification cycle
            h_event = kernel32.CreateEventW(None, False, False, None)
            if not h_event:
                time.sleep(1)
                continue

            overlapped        = OVERLAPPED()
            overlapped.hEvent = h_event
            handle            = wt.HANDLE()

            # Arms an async notification — returns ERROR_IO_PENDING (997)
            iphlpapi.NotifyAddrChange(ctypes.byref(handle), ctypes.byref(overlapped))

            # Use a 30 s timeout so the loop can respond to stop_event without
            # timing out on every normal network change.
            wait_result = kernel32.WaitForSingleObject(h_event, 30000)

            if wait_result != WAIT_OBJECT_0:
                # Timed out (or error) — the kernel still has a pointer to
                # `overlapped`.  Cancel the pending IO BEFORE this local
                # variable goes out of scope, otherwise we get a crash when
                # the next network event fires.
                iphlpapi.CancelIPChangeNotify(ctypes.byref(overlapped))

            kernel32.CloseHandle(h_event)

            if stop_event.is_set():
                break

            if wait_result == WAIT_OBJECT_0:
                # Windows just told us an IP address changed — wake the monitor NOW
                wake_event.set()

    except Exception as e:
        logger.warning(f"Win32 NotifyAddrChange unavailable, relying on polling: {e}")


class VPNGuard:
    """
    Monitors VPN connectivity and fires a callback when status changes.

    On Windows, detection is near-instant (<100 ms) via NotifyAddrChange().
    A 2-second polling loop runs as a fallback on all platforms.
    """

    def __init__(self, check_interval=2):
        self.check_interval = check_interval
        self._callback      = None
        self._thread        = None
        self._stop          = threading.Event()
        self._wake          = threading.Event()   # set by Win32 notify thread
        self._last_connected = None

    def get_status(self):
        """Returns (is_connected: bool, iface_name: str | None, ip: str | None)"""
        iface, ip = detect_vpn_interface()
        return (iface is not None), iface, ip

    def is_connected(self):
        iface, _ = detect_vpn_interface()
        return iface is not None

    def start(self, on_change_callback):
        """
        Start background monitoring.
        on_change_callback(is_connected, iface, ip) fires on every status change.
        """
        self._callback = on_change_callback
        connected, _, _ = self.get_status()
        self._last_connected = connected
        self._stop.clear()
        self._wake.clear()

        # Windows: instant notification thread
        if os.name == 'nt':
            threading.Thread(
                target=_win_notify_thread,
                args=(self._wake, self._stop),
                daemon=True,
                name="vpn-guard-notify",
            ).start()

        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="vpn-guard",
        )
        self._thread.start()

        notify_mode = "Win32 NotifyAddrChange + " if os.name == 'nt' else ""
        logger.info(
            f"VPNGuard started — initial: {'connected' if connected else 'disconnected'} "
            f"({notify_mode}{self.check_interval}s fallback poll)"
        )

    def stop(self):
        self._stop.set()
        self._wake.set()   # unblock the waiting monitor loop immediately

    def _monitor_loop(self):
        while not self._stop.is_set():
            # Block until Win32 wakes us OR the fallback poll interval expires
            self._wake.wait(timeout=self.check_interval)
            self._wake.clear()

            if self._stop.is_set():
                break

            try:
                connected, iface, ip = self.get_status()
                if connected != self._last_connected:
                    self._last_connected = connected
                    if self._callback:
                        try:
                            self._callback(connected, iface, ip)
                        except Exception as e:
                            logger.error(f"VPNGuard callback error: {e}")
                    logger.info(
                        f"VPN status changed: "
                        f"{'connected (' + ip + ')' if connected else 'DISCONNECTED'}"
                    )
            except Exception as e:
                logger.error(f"VPNGuard monitor error: {e}")
