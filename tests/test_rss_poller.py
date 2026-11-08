"""RssPoller: a release is only recorded as matched when it was actually added.

Before the fix, engine.add_magnet() returned normally after a 10 minute
metadata timeout, so the rule recorded the title as downloaded even though
nothing was. add_magnet now raises TimeoutError, and the poller must treat
any exception as "not added" so the next poll retries it.
"""
import pytest

import rss_poller


@pytest.fixture
def poller_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(rss_poller, "load_config",
                        lambda: {"indexing_server": "http://jackett.invalid", "jackett_api_key": "k"})

    def make(add_fn, results):
        monkeypatch.setattr(rss_poller, "search_jackett", lambda *a, **k: results)
        return rss_poller.RssPoller(add_fn, rules_path=str(tmp_path / "rules.json"))
    return make


def _results(*names):
    return [{"filename": n, "magnet": f"magnet:?xt=urn:btih:{i}", "seeders": 10 - i}
            for i, n in enumerate(names)]


def test_failed_add_is_not_recorded_and_is_retried_next_poll(poller_factory):
    calls = {"n": 0}

    def flaky_add(magnet):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("no metadata")

    poller = poller_factory(flaky_add, _results("Show.S01.1080p.WEB"))
    rule = poller.add_rule("Show", "1080p")

    poller._check_rule(rule)
    assert rule["matched_titles"] == []
    assert rule["matched_count"] == 0

    poller._check_rule(rule)          # next poll: same release is still a candidate
    assert rule["matched_titles"] == ["Show.S01.1080p.WEB"]
    assert rule["matched_count"] == 1


def test_check_is_recorded_before_slow_adds_finish(poller_factory):
    """A rule used to show 'never checked' until every add finished, and an add
    can wait minutes for metadata. The check is recorded as soon as the search
    and matching are done; pending_adds says how many matches are still adding."""
    import threading
    release = threading.Event()
    started = threading.Event()

    def slow_add(magnet):
        started.set()
        release.wait(timeout=10)

    poller = poller_factory(slow_add, _results("Show.S01.1080p.A"))
    rule = poller.add_rule("Show", "1080p")

    worker = threading.Thread(target=poller._check_rule, args=(rule,))
    worker.start()
    try:
        assert started.wait(timeout=5), "the add never started"
        # add still blocked, yet the check is already on record
        assert rule["last_checked"] > 0
        assert rule["pending_adds"] == 1
        assert rule["matched_count"] == 0
    finally:
        release.set()
        worker.join(timeout=10)

    assert rule["pending_adds"] == 0
    assert rule["matched_titles"] == ["Show.S01.1080p.A"]


def test_overlapping_check_of_the_same_rule_does_not_re_add(poller_factory):
    """While adds are in flight the titles aren't recorded yet, so a second check
    (check-now or the periodic poll) would add the same releases again."""
    import threading
    release = threading.Event()
    started = threading.Event()
    calls = []

    def slow_add(magnet):
        calls.append(magnet)
        started.set()
        release.wait(timeout=10)

    poller = poller_factory(slow_add, _results("Show.S01.1080p.A"))
    rule = poller.add_rule("Show", "1080p")

    worker = threading.Thread(target=poller._check_rule, args=(rule,))
    worker.start()
    try:
        assert started.wait(timeout=5)
        poller._check_rule(rule)          # overlaps the first: must be a no-op
        assert len(calls) == 1
    finally:
        release.set()
        worker.join(timeout=10)
    assert len(calls) == 1


def test_a_broad_rule_adds_at_most_the_cap_per_check_healthiest_first(poller_factory):
    added = []
    names = [f"Thing.{i:03d}.1080p" for i in range(25)]
    # results() gives earlier names more seeders, so the first ones are healthiest
    poller = poller_factory(lambda magnet: added.append(magnet), _results(*names))
    rule = poller.add_rule("Thing", "Any")

    poller._check_rule(rule)

    assert len(added) == rss_poller.MAX_ADDS_PER_CHECK == 10
    assert rule["matched_count"] == 10
    assert rule["pending_adds"] == 0
    # the 10 healthiest, not an arbitrary 10
    assert set(rule["matched_titles"]) == set(names[:10])


def test_releases_over_the_cap_are_picked_up_by_later_checks(poller_factory):
    added = []
    names = [f"Thing.{i:03d}.1080p" for i in range(25)]
    poller = poller_factory(lambda magnet: added.append(magnet), _results(*names))
    rule = poller.add_rule("Thing", "Any")

    for _ in range(3):
        poller._check_rule(rule)

    assert rule["matched_count"] == 25            # 10 + 10 + 5, none lost or repeated
    assert len(set(rule["matched_titles"])) == 25
    assert len(added) == 25


def test_a_rule_under_the_cap_is_unaffected(poller_factory):
    added = []
    poller = poller_factory(lambda magnet: added.append(magnet), _results("A.2020.1080p", "B.2021.1080p", "C.2022.1080p"))
    rule = poller.add_rule("X", "1080p")

    poller._check_rule(rule)

    assert rule["matched_count"] == 3


def test_pending_adds_does_not_survive_a_restart(tmp_path):
    import json
    path = tmp_path / "rules.json"
    path.write_text(json.dumps([{
        "id": "r1", "name": "Show", "query": "Show", "quality": "Any", "season": None,
        "episode_mode": "pack", "start_episode": None, "enabled": True, "created": 1.0,
        "last_checked": 5.0, "matched_count": 0, "matched_titles": [], "pending_adds": 3,
    }]), encoding="utf-8")
    poller = rss_poller.RssPoller(lambda m: None, rules_path=str(path))
    assert poller.get_one("r1")["pending_adds"] == 0


def test_one_slow_or_failing_release_does_not_block_the_others(poller_factory):
    def add(magnet):
        if magnet.endswith(":0"):
            raise TimeoutError("no metadata")

    poller = poller_factory(add, _results("Show.S01.1080p.A", "Show.S02.1080p.B"))
    rule = poller.add_rule("Show", "1080p")

    poller._check_rule(rule)
    assert rule["matched_titles"] == ["Show.S02.1080p.B"]
    assert rule["matched_count"] == 1
