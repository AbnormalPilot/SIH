"""Component 8 — tests for the robots.txt gate.

The gate is the project's ethical claim, so it gets a real test suite. The
first test is a regression test: Python's stdlib ``urllib.robotparser`` does
not implement ``*`` wildcards and returns ALLOWED for Skyscanner's fare pages,
which are in fact Disallowed. That silent mis-allow is the exact failure this
module exists to prevent.
"""
import pytest

from pushpak import config
from pushpak.collect.robots import RobotsFile, RobotsGate, _pattern_to_regex


SKYSCANNER_EXCERPT = """
User-agent: *
User-agent: AhrefsBot
Disallow: /*/day-view/
Disallow: /prices-calendar
Disallow: /transport/*
Disallow: /g/*
Allow: /g/banana/api/context
Crawl-Delay: 2

User-agent: Leikibot
Disallow: /
"""


def test_wildcard_disallow_blocks_the_real_skyscanner_fare_path():
    """Regression: stdlib robotparser returns ALLOWED here. We must not."""
    rf = RobotsFile(SKYSCANNER_EXCERPT)
    allowed, rule, _ = rf.evaluate("pushpak-research-bot", "/transport/flights/del/bom/260915/")
    assert allowed is False
    assert rule == "Disallow: /transport/*"


def test_stdlib_robotparser_would_have_got_this_wrong():
    """Documents *why* this module exists. If stdlib is ever fixed, this fails."""
    import urllib.robotparser
    p = urllib.robotparser.RobotFileParser()
    p.parse(SKYSCANNER_EXCERPT.splitlines())
    assert p.can_fetch("*", "/transport/flights/del/bom/260915/") is True, (
        "stdlib now honours wildcards — the custom matcher may be simplifiable"
    )


def test_non_fare_path_is_still_allowed():
    """Prove the gate discriminates rather than blanket-denying."""
    rf = RobotsFile(SKYSCANNER_EXCERPT)
    allowed, _, _ = rf.evaluate("pushpak-research-bot", "/news/")
    assert allowed is True


def test_longest_match_wins_and_allow_beats_disallow_on_a_tie():
    rf = RobotsFile(SKYSCANNER_EXCERPT)
    blocked, rule, _ = rf.evaluate("pushpak-research-bot", "/g/something")
    assert blocked is False and rule == "Disallow: /g/*"
    allowed, rule2, _ = rf.evaluate("pushpak-research-bot", "/g/banana/api/context")
    assert allowed is True and rule2 == "Allow: /g/banana/api/context"


def test_end_anchor_is_honoured():
    rf = RobotsFile("User-agent: *\nDisallow: /SearchResults.aspx$\n")
    assert rf.evaluate("bot", "/SearchResults.aspx")[0] is False
    assert rf.evaluate("bot", "/SearchResults.aspx/more")[0] is True


def test_query_string_patterns_match():
    rf = RobotsFile("User-agent: *\nDisallow: /*?debug*\n")
    assert rf.evaluate("bot", "/page?debug=1")[0] is False
    assert rf.evaluate("bot", "/page?x=1")[0] is True


def test_empty_disallow_means_allow_everything():
    rf = RobotsFile("User-agent: *\nDisallow:\n")
    assert rf.evaluate("bot", "/anything")[0] is True


def test_crawl_delay_is_read_from_the_matching_group():
    rf = RobotsFile(SKYSCANNER_EXCERPT)
    assert rf.evaluate("pushpak-research-bot", "/news/")[2] == 2.0


def test_specific_agent_group_beats_the_wildcard_group():
    text = """
User-agent: *
Disallow: /

User-agent: pushpak-research-bot
Disallow: /private/
"""
    rf = RobotsFile(text)
    assert rf.evaluate("pushpak-research-bot", "/public/")[0] is True
    assert rf.evaluate("pushpak-research-bot", "/private/")[0] is False
    assert rf.evaluate("someotherbot", "/public/")[0] is False


def test_pattern_compilation_escapes_regex_metacharacters():
    rx = _pattern_to_regex("/a.b+c/")
    assert rx.match("/a.b+c/")
    assert not rx.match("/axbxc/")


def test_gate_fails_closed_when_robots_is_unreachable(monkeypatch):
    gate = RobotsGate()
    gate._cache["https://blocked.example"] = (
        None, {"status": None, "bytes": 0, "error": "ConnectError: refused",
               "fetched_at": "2026-08-26T00:00:00+00:00"},
    )
    v = gate.check("https://blocked.example/flights/search")
    assert v.allowed is False
    assert v.reason == "ROBOTS_UNREACHABLE"
    assert "fail-closed" in v.rule


def test_gate_fails_closed_on_http_error(monkeypatch):
    gate = RobotsGate()
    gate._cache["https://err.example"] = (
        None, {"status": 503, "bytes": 0, "error": None,
               "fetched_at": "2026-08-26T00:00:00+00:00"},
    )
    v = gate.check("https://err.example/flights/search")
    assert v.allowed is False and v.reason == "ROBOTS_HTTP_503"


def test_crawl_delay_never_falls_below_the_configured_floor():
    gate = RobotsGate()
    gate._cache["https://fast.example"] = (
        RobotsFile("User-agent: *\nCrawl-delay: 0.1\n"),
        {"status": 200, "bytes": 30, "error": None, "fetched_at": "x"},
    )
    v = gate.check("https://fast.example/anything")
    assert v.crawl_delay >= config.DEFAULT_CRAWL_DELAY


def test_user_agent_token_is_our_own_and_not_laundered():
    gate = RobotsGate()
    assert gate.ua_token == "pushpak-research-bot"
    assert "googlebot" not in gate.user_agent.lower()


def test_respect_robots_is_on():
    """A tripwire: nobody should be able to quietly disable the gate."""
    assert config.RESPECT_ROBOTS is True
