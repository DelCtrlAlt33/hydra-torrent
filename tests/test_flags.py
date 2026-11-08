"""Bundled country flags (static/flags, flag-icons, MIT).

Served by Hydra itself so the browser never asks a third-party image host for
them (a request made outside the VPN-bound torrent session). These checks need
only the standard library, so they run in the main CI job.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAGS = os.path.join(ROOT, "static", "flags")

# anything that could make an SVG do more than draw a flag
_UNSAFE = re.compile(
    r"<script|javascript:|\son\w+\s*=|<foreignObject|<iframe|<embed|<object|"
    r"xlink:href\s*=\s*[\"']https?:|<!ENTITY|<!DOCTYPE[^>]*\[|data:text/html",
    re.IGNORECASE,
)


def _flag_files():
    return sorted(f for f in os.listdir(FLAGS) if f.endswith(".svg"))


def test_flags_are_bundled_with_their_licence():
    assert len(_flag_files()) >= 240
    assert os.path.isfile(os.path.join(FLAGS, "LICENSE-flag-icons.txt"))


def test_every_flag_is_a_plain_two_letter_svg_with_nothing_executable_in_it():
    bad = []
    for name in _flag_files():
        if not re.fullmatch(r"[a-z]{2}\.svg", name):
            bad.append(f"{name}: not a two-letter code")
            continue
        with open(os.path.join(FLAGS, name), encoding="utf-8") as f:
            text = f.read()
        if not re.match(r"\s*(<\?xml[^>]*\?>\s*)?<svg\b", text):
            bad.append(f"{name}: not a plain svg")
        if _UNSAFE.search(text):
            bad.append(f"{name}: contains script/handler/external reference")
    assert not bad, "\n".join(bad)


def test_every_country_on_the_peer_map_has_a_flag():
    with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as f:
        html = f.read()
    block = re.search(r"const COUNTRY_COORDS = \{([\s\S]*?)\n\};", html).group(1)
    codes = {c.lower() for c in re.findall(r"\b([A-Z]{2}):\[", block)}
    have = {f[:-4] for f in _flag_files()}
    assert codes, "could not read the map's country list"
    assert not (codes - have), f"map countries without a flag: {sorted(codes - have)}"


def test_the_peer_list_uses_the_local_flags_and_no_third_party_host():
    with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as f:
        html = f.read()
    assert "/flags/${code}.svg" in html
    assert "flagcdn" not in html
