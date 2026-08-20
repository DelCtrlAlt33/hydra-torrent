"""
Tests for daemon_models.py - the Pydantic schemas that define Hydra's
REST/WebSocket API contract. This file explicitly documents itself as
having "no imports from other hydra modules" so it stays importable
anywhere - these tests hold it to that contract implicitly by importing
nothing else either, and check the schema behavior that the rest of the
app (and any future client) relies on: required vs optional fields,
defaults, and enum membership.
"""
import pytest
from pydantic import ValidationError

from daemon_models import (
    TransferStatus,
    TransferState,
    AddMagnetRequest,
    AddTorrentUrlRequest,
    SearchRequest,
    VPNStatus,
    ConfigResponse,
    ConfigUpdate,
    RssRule,
    AddRssRuleRequest,
    PatchRssRuleRequest,
    SetFilePrioritiesRequest,
    FilePriorityItem,
    IntegrationHealth,
    HealthStatus,
    UpdateStatus,
)


# ---------------------------------------------------------------------------
# TransferState - the core object streamed to the UI for every torrent
# ---------------------------------------------------------------------------

def test_transfer_state_only_requires_name():
    # Everything else must have a safe default - the UI renders this
    # object continuously as torrents are added, and a missing default
    # would crash the whole status feed over one incomplete torrent.
    state = TransferState(name="Some.Torrent")
    assert state.status == "Queued"
    assert state.progress == 0.0
    assert state.paused is False
    assert state.vpn_paused is False
    assert state.pieces == []
    assert state.error is None


def test_transfer_state_requires_name():
    with pytest.raises(ValidationError):
        TransferState()


def test_transfer_status_enum_values():
    # These string values are the actual wire contract with the frontend -
    # renaming one without updating the UI would silently break status
    # display, so pin them explicitly.
    assert TransferStatus.Downloading == "Downloading"
    assert TransferStatus.Seeding == "Seeding"
    assert TransferStatus.Complete == "Complete"
    assert set(TransferStatus) == {
        TransferStatus.Queued, TransferStatus.Checking, TransferStatus.Downloading,
        TransferStatus.Seeding, TransferStatus.Paused, TransferStatus.Failed,
        TransferStatus.Complete,
    }


# ---------------------------------------------------------------------------
# Request models - required-field enforcement
# ---------------------------------------------------------------------------

def test_add_magnet_request_requires_magnet():
    with pytest.raises(ValidationError):
        AddMagnetRequest()
    req = AddMagnetRequest(magnet="magnet:?xt=urn:btih:abc123")
    assert req.save_path is None


def test_add_torrent_url_request_requires_url():
    with pytest.raises(ValidationError):
        AddTorrentUrlRequest()


def test_search_request_defaults_to_jackett_mode():
    # "online" mode (built-in 1337x/Nyaa/TPB scraping) was removed
    # 2026-08-20 - jackett is now the only sensible default, since it's
    # the mode that doesn't hardcode any specific indexer into Hydra itself.
    req = SearchRequest(query="ubuntu")
    assert req.mode == "jackett"
    assert req.server is None


# ---------------------------------------------------------------------------
# ConfigUpdate - every field optional (PATCH semantics: only send what changes)
# ---------------------------------------------------------------------------

def test_config_update_allows_empty_partial_patch():
    # A PATCH /config request with no fields set must be valid - the
    # daemon route only applies whatever the caller actually included.
    update = ConfigUpdate()
    assert update.plex_url is None
    assert update.vpn_required is None


def test_config_update_accepts_single_field():
    update = ConfigUpdate(vpn_required=True)
    assert update.vpn_required is True
    assert update.plex_url is None  # untouched fields stay None, not defaulted


def test_config_response_has_safe_defaults_for_unconfigured_daemon():
    # A brand-new install with no hydra_config.json yet should still
    # produce a valid ConfigResponse rather than the /config route 500ing.
    cfg = ConfigResponse()
    assert cfg.auto_move_to_plex is True
    assert cfg.vpn_required is False
    assert cfg.seed_ratio == 0.0


# ---------------------------------------------------------------------------
# RSS Auto-DL rules
# ---------------------------------------------------------------------------

def test_rss_rule_requires_created_timestamp():
    # `created` has no default - every rule must record when it was added.
    with pytest.raises(ValidationError):
        RssRule(id="r1", name="Test Rule", query="show name", quality="1080p")


def test_rss_rule_defaults():
    rule = RssRule(id="r1", name="Test Rule", query="show name", quality="1080p", created=1700000000.0)
    assert rule.enabled is True
    assert rule.episode_mode == "pack"
    assert rule.season is None
    assert rule.matched_count == 0
    assert rule.matched_titles == []


def test_add_rss_rule_request_defaults_quality_1080p():
    req = AddRssRuleRequest(name="Test Rule")
    assert req.quality == "1080p"
    assert req.episode_mode == "pack"
    assert req.query is None


def test_patch_rss_rule_request_all_optional():
    patch = PatchRssRuleRequest()
    assert patch.enabled is None
    assert patch.quality is None


# ---------------------------------------------------------------------------
# VPN status
# ---------------------------------------------------------------------------

def test_vpn_status_requires_connected_flag():
    with pytest.raises(ValidationError):
        VPNStatus()
    status = VPNStatus(connected=False)
    assert status.iface is None
    assert status.public_ip is None


# ---------------------------------------------------------------------------
# File priorities
# ---------------------------------------------------------------------------

def test_set_file_priorities_request_wraps_a_list():
    req = SetFilePrioritiesRequest(files=[
        FilePriorityItem(index=0, priority=7),
        FilePriorityItem(index=1, priority=0),
    ])
    assert len(req.files) == 2
    assert req.files[1].priority == 0  # 0 = skip this file


def test_file_priority_item_requires_index_and_priority():
    with pytest.raises(ValidationError):
        FilePriorityItem(index=0)


# ---------------------------------------------------------------------------
# Health status
# ---------------------------------------------------------------------------

def test_integration_health_unconfigured_is_not_an_error():
    health = IntegrationHealth(configured=False, reachable=False)
    assert health.error is None  # "not set up" is distinct from "broken"


def test_integration_health_requires_configured_and_reachable():
    with pytest.raises(ValidationError):
        IntegrationHealth()


def test_health_status_bundles_vpn_jackett_plex():
    status = HealthStatus(
        vpn=VPNStatus(connected=True, iface="wg0", vpn_ip="10.0.0.5"),
        jackett=IntegrationHealth(configured=True, reachable=True),
        plex=IntegrationHealth(configured=False, reachable=False),
        jellyfin=IntegrationHealth(configured=False, reachable=False),
    )
    assert status.vpn.connected is True
    assert status.jackett.reachable is True
    assert status.plex.configured is False
    assert status.jellyfin.configured is False


def test_update_status_requires_available_flag_and_current_version():
    with pytest.raises(ValidationError):
        UpdateStatus()


def test_update_status_defaults_when_no_update_found():
    status = UpdateStatus(update_available=False, current_version="1.0.4")
    assert status.latest_version is None
    assert status.release_url is None
    assert status.error is None
