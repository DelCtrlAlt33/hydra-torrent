"""The Windows installer opens a firewall port for inbound peers. It must be the
port libtorrent actually listens on (config.PEER_PORT). hydra_app.py can't be
imported off Windows (and imports config too late to use it directly), so the
installer carries a literal; this keeps that literal in step with config.
"""
import os
import re

import config


def _installer_source():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hydra_app.py")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_installer_opens_the_port_libtorrent_listens_on():
    m = re.search(r"peer_port = '(\d+)'", _installer_source())
    assert m, "hydra_app.py no longer defines peer_port for the firewall rule"
    assert int(m.group(1)) == config.PEER_PORT


def test_installer_no_longer_opens_stale_ports():
    src = _installer_source()
    assert "localport=6002" not in src
    assert "localport=6881" not in src
