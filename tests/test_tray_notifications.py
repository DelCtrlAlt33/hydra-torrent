# hydra_tray.py has no libtorrent dependency (it only talks to the daemon
# over HTTP), so unlike test_torrent_engine.py this belongs in the main
# tier and must stay importable there.

from hydra_tray import HydraTray


class _FakeSelf:
    """Enough of a HydraTray instance for _check_completions/_fire_notification
    to run against, without pulling in pystray/a real tray icon."""

    def __init__(self):
        self._notified = None
        self._icon = None
        self.fired = []

    def _fire_notification(self, name, transfer):
        self.fired.append((name, transfer))


# ---------------------------------------------------------------------------
# Premature toast on a raw progress spike (see CHANGELOG.md 2026-08-23
# "the toast fired immediately, but the progress bar looked healthy")
# ---------------------------------------------------------------------------

def test_check_completions_ignores_progress_spike_while_status_says_downloading():
    """The actual bug: the old check fired on
    `status in ('Seeding', 'Finished') or progress >= 100` - and
    hydra_daemon.py's _monitor_download writes the raw `progress` field
    from libtorrent's s.progress every single poll, independent of
    whether its own (fixed, race-protected) completion check has accepted
    the torrent as genuinely done yet. A real, healthily-downloading
    torrent could transiently read progress=100 while status correctly
    still said 'Downloading' - confirmed live, a real download fired
    "Download finished" immediately while the on-screen progress bar was
    still visibly moving. status is the signal the daemon guarantees is
    honest; this proves the tray now trusts only that.
    """
    fake = _FakeSelf()
    transfers = [{"name": "RealDownload", "status": "Downloading", "progress": 100.0}]

    HydraTray._check_completions(fake, transfers)  # first call: seeds the baseline
    assert fake.fired == [], "first poll must only seed the baseline, never notify directly"

    HydraTray._check_completions(fake, transfers)  # second call: should still not fire
    assert fake.fired == [], (
        "a torrent with status='Downloading' must never fire the completion toast, "
        "no matter what its raw progress value reads"
    )


def test_check_completions_fires_once_when_status_actually_transitions_to_seeding():
    """The other half: a genuine completion (status really becomes
    Seeding/Finished) must still fire exactly once, not get lost while
    fixing the false-positive above.
    """
    fake = _FakeSelf()
    downloading = [{"name": "RealDownload", "status": "Downloading", "progress": 42.0}]
    HydraTray._check_completions(fake, downloading)
    assert fake.fired == []

    seeding = [{"name": "RealDownload", "status": "Seeding", "progress": 100.0}]
    HydraTray._check_completions(fake, seeding)
    assert len(fake.fired) == 1
    assert fake.fired[0][0] == "RealDownload"

    # Polling again with the same state must not re-fire.
    HydraTray._check_completions(fake, seeding)
    assert len(fake.fired) == 1
