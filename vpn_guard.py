import socket
import threading
import logging

import psutil

logger = logging.getLogger('hydra_torrent')

# Interface name fragments that identify PIA's WireGuard adapter
_PIA_PATTERNS = ['wgpia', 'pia']


def detect_pia_interface():
    """
    Find the active PIA VPN interface.
    Returns (interface_name, ip_address) or (None, None).
    Requires the interface to be UP and have a valid IPv4 address.
    """
    try:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()

        for iface_name, addr_list in addrs.items():
            iface_lower = iface_name.lower()
            if not any(pat in iface_lower for pat in _PIA_PATTERNS):
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


class VPNGuard:
    """
    Monitors PIA VPN connectivity and fires a callback when status changes.
    Designed to run in a background daemon thread — no GUI imports.
    """

    def __init__(self, check_interval=30):
        self.check_interval = check_interval
        self._callback = None
        self._thread = None
        self._stop = threading.Event()
        self._last_connected = None

    def get_status(self):
        """
        Returns (is_connected: bool, iface_name: str | None, ip: str | None)
        """
        iface, ip = detect_pia_interface()
        return (iface is not None), iface, ip

    def is_connected(self):
        iface, _ = detect_pia_interface()
        return iface is not None

    def start(self, on_change_callback):
        """
        Start background monitoring thread.
        on_change_callback(is_connected: bool, iface: str | None, ip: str | None)
        is called whenever VPN connects or disconnects.
        """
        self._callback = on_change_callback
        # Seed initial state without firing callback
        connected, _, _ = self.get_status()
        self._last_connected = connected
        self._stop.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="vpn-guard")
        self._thread.start()
        logger.info(f"VPNGuard started — initial state: {'connected' if connected else 'disconnected'} (interval={self.check_interval}s)")

    def stop(self):
        self._stop.set()

    def _monitor_loop(self):
        while not self._stop.wait(self.check_interval):
            try:
                connected, iface, ip = self.get_status()
                if connected != self._last_connected:
                    self._last_connected = connected
                    if self._callback:
                        try:
                            self._callback(connected, iface, ip)
                        except Exception as e:
                            logger.error(f"VPNGuard callback error: {e}")
                    logger.info(f"VPN status changed: {'connected (' + ip + ')' if connected else 'DISCONNECTED'}")
            except Exception as e:
                logger.error(f"VPNGuard monitor error: {e}")
