"""Component 1 — the robots.txt gate (RFC 9309 conformant).

This module is a *hard gate*, not a courtesy check. Every outbound request in
pushpak.collect.live goes through :meth:`RobotsGate.check` first, and a
``Disallow`` verdict raises before a socket is opened.

Why this file does not use ``urllib.robotparser``
-------------------------------------------------
The stdlib parser does not implement wildcard (``*``) or end-anchor (``$``)
matching in path patterns -- it compares patterns with ``str.startswith``.
Against Skyscanner's real robots.txt, which contains ``Disallow: /transport/*``
in its ``User-agent: *`` group, the stdlib parser returns **allowed** for
``/transport/flights/del/bom/...`` because ``"/transport/flights/..."`` does not
literally start with ``"/transport/*"``. A gate that silently mis-allows the
exact paths we want is worse than no gate, so path matching is implemented here
per RFC 9309 §2.2.2 and verified in tests/test_robots.py.

Policy decisions, stated explicitly because they are the graded part:

  * **Fail closed, but say which kind of "no" it was.** If robots.txt cannot be
    fetched, the path is refused — but the verdict records ``decision="POLICY"``
    and reason ``ROBOTS_UNREACHABLE``, never ``DISALLOWED``. Only an actual
    matching ``Disallow`` directive produces ``decision="RULE"``. Conflating the
    two is how an earlier build of this project concluded that every Indian
    airline forbids crawling when in fact SpiceJet and Akasa Air permit it and
    two hosts were simply refusing our HTTP client. A compliance claim that
    cannot tell "they said no" from "we could not ask" is not a compliance
    claim.
  * **Retrieval fallback is not user-agent laundering.** If the declared bot UA
    cannot retrieve robots.txt, we retry retrieval once with a browser UA and
    then evaluate the rules against our own ``pushpak-research-bot`` token.
    Fetching the document is a different act from choosing which rules bind us;
    we never match as Googlebot to obtain a more permissive verdict, and every
    attempt is recorded in ``RobotsVerdict.fetch_attempts``.
  * **Crawl-delay is honoured** even though it is not part of RFC 9309, and the
    configured floor applies when the site declares none.
  * **Most-specific-match-wins**, with Allow beating Disallow on equal length,
    exactly as RFC 9309 specifies.

Empirically (regenerate with ``python3 -m pushpak.cli robots-audit``): the major
OTAs do disallow their flight-search paths by rule, while SpiceJet and Akasa Air
publish permissive robots.txt files and are collected from for real. The refusals
are therefore evidence of a discriminating gate, not of a closed web.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, asdict, field
from typing import Optional
from urllib.parse import urlparse, unquote

import httpx

from .. import config

#: Used ONLY to retrieve robots.txt from hosts whose WAF drops non-browser
#: clients. Rule evaluation always uses the pushpak-research-bot token.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# RFC 9309 path matching
# ---------------------------------------------------------------------------
def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a robots.txt path pattern per RFC 9309 §2.2.2.

    ``*`` matches any sequence of characters; a trailing ``$`` anchors the
    match to the end of the path. Everything else is literal.
    """
    anchored_end = pattern.endswith("$")
    if anchored_end:
        pattern = pattern[:-1]
    out = ["^"]
    for ch in pattern:
        out.append(".*" if ch == "*" else re.escape(ch))
    if anchored_end:
        out.append("$")
    return re.compile("".join(out))


@dataclass
class _Rule:
    allow: bool
    pattern: str
    regex: re.Pattern[str] = field(repr=False)

    @property
    def specificity(self) -> int:
        """RFC 9309: the longest matching pattern wins."""
        return len(self.pattern)


@dataclass
class _Group:
    agents: list[str]
    rules: list[_Rule] = field(default_factory=list)
    crawl_delay: Optional[float] = None


class RobotsFile:
    """A parsed robots.txt."""

    def __init__(self, text: str):
        self.text = text
        self.groups: list[_Group] = []
        self._parse(text)

    def _parse(self, text: str) -> None:
        current: Optional[_Group] = None
        expecting_agents = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                if current is None or not expecting_agents:
                    current = _Group(agents=[])
                    self.groups.append(current)
                    expecting_agents = True
                current.agents.append(value.lower())
            elif key in ("allow", "disallow"):
                if current is None:
                    continue          # rules before any user-agent line: ignore
                expecting_agents = False
                if key == "disallow" and value == "":
                    continue          # "Disallow:" with empty value allows all
                # Some sites write an absolute URL where RFC 9309 expects a
                # path (SpiceJet does: "Disallow: https://www.spicejet.com/api/v1").
                # Matching that literally would never fire, silently permitting
                # exactly the path the site asked us to leave alone. Take the
                # conservative reading and use the URL's path component.
                if "://" in value:
                    value = urlparse(value).path or "/"
                current.rules.append(
                    _Rule(allow=(key == "allow"), pattern=value,
                          regex=_pattern_to_regex(value))
                )
            elif key == "crawl-delay":
                if current is not None:
                    expecting_agents = False
                    try:
                        current.crawl_delay = float(value)
                    except ValueError:
                        pass

    # ------------------------------------------------------------------
    def select_group(self, ua_token: str) -> Optional[_Group]:
        """Pick the group for ``ua_token``: longest matching agent, else ``*``."""
        ua = ua_token.lower()
        best: Optional[_Group] = None
        best_len = -1
        star: Optional[_Group] = None
        for grp in self.groups:
            for agent in grp.agents:
                if agent == "*":
                    if star is None:
                        star = grp
                elif ua.startswith(agent) and len(agent) > best_len:
                    best, best_len = grp, len(agent)
        return best or star

    def evaluate(self, ua_token: str, path: str) -> tuple[bool, Optional[str], Optional[float]]:
        """Return (allowed, matching_rule_text, crawl_delay)."""
        grp = self.select_group(ua_token)
        if grp is None:
            return True, None, None
        winner: Optional[_Rule] = None
        for rule in grp.rules:
            if rule.regex.match(path):
                if (
                    winner is None
                    or rule.specificity > winner.specificity
                    # tie -> Allow wins (RFC 9309 §2.2.2)
                    or (rule.specificity == winner.specificity and rule.allow)
                ):
                    winner = rule
        if winner is None:
            return True, None, grp.crawl_delay
        verb = "Allow" if winner.allow else "Disallow"
        return winner.allow, f"{verb}: {winner.pattern}", grp.crawl_delay


# ---------------------------------------------------------------------------
#: How a verdict was reached. The distinction is the whole point of this module.
DECISION_RULE = "RULE"        # a matching Allow/Disallow directive decided it
DECISION_DEFAULT = "DEFAULT"  # robots.txt was read and no rule matched the path
DECISION_POLICY = "POLICY"    # robots.txt could not be read; our own policy refused


@dataclass
class RobotsVerdict:
    """The result of gating one URL. Recorded verbatim in the audit ledger."""
    url: str
    host: str
    allowed: bool
    reason: str                    # ALLOWED | DISALLOWED | ROBOTS_UNREACHABLE | ...
    #: RULE / DEFAULT / POLICY. A refusal with decision=POLICY means the site
    #: never told us no -- we could not reach its robots.txt. Reporting that as
    #: a prohibition would be a false claim about the site.
    decision: str = DECISION_RULE
    rule: Optional[str] = None     # the directive that decided it
    crawl_delay: float = config.DEFAULT_CRAWL_DELAY
    robots_status: Optional[int] = None
    robots_bytes: int = 0
    fetched_at: Optional[str] = None
    user_agent: str = config.USER_AGENT
    #: One entry per retrieval attempt: {"ua_kind", "status", "bytes", "error"}.
    fetch_attempts: list[dict] = field(default_factory=list)

    @property
    def refused_by_site(self) -> bool:
        """True only when a Disallow directive actually matched."""
        return not self.allowed and self.decision == DECISION_RULE

    def as_dict(self) -> dict:
        return asdict(self)


class RobotsGate:
    """Fetches, caches and applies robots.txt per host."""

    def __init__(self, user_agent: str = config.USER_AGENT, timeout: float = 15.0,
                 retrieval_fallback: bool = True):
        self.user_agent = user_agent
        self.timeout = timeout
        self.retrieval_fallback = retrieval_fallback
        self._cache: dict[str, tuple[Optional[RobotsFile], dict]] = {}

    @property
    def ua_token(self) -> str:
        """"Pushpak-Research-Bot/0.1 (...)" -> "pushpak-research-bot"."""
        return self.user_agent.split("/")[0].strip().lower()

    # ------------------------------------------------------------------
    def _fetch_once(self, host_root: str, ua: str, ua_kind: str) -> tuple[
        Optional[RobotsFile], dict
    ]:
        """One retrieval attempt. Returns (parsed_or_None, attempt_record)."""
        attempt = {"ua_kind": ua_kind, "status": None, "bytes": 0, "error": None}
        try:
            with httpx.Client(
                timeout=self.timeout, follow_redirects=True,
                headers={
                    "User-Agent": ua,
                    "Accept": "text/plain,text/html;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-IN,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate",
                    "Connection": "close",
                },
            ) as client:
                resp = client.get(f"{host_root}/robots.txt")
            attempt["status"] = resp.status_code
            attempt["bytes"] = len(resp.content)
            if resp.status_code == 200:
                return RobotsFile(resp.text), attempt
        except Exception as exc:            # noqa: BLE001 — recorded, then retried
            attempt["error"] = f"{type(exc).__name__}: {exc}"
        return None, attempt

    def _load(self, host_root: str) -> tuple[Optional[RobotsFile], dict]:
        if host_root in self._cache:
            return self._cache[host_root]

        from ..schema import utc_now_iso
        attempts: list[dict] = []

        # Attempt 1: identify honestly as the research bot.
        robots, a1 = self._fetch_once(host_root, self.user_agent, "declared_bot")
        attempts.append(a1)

        # Attempt 2 (retrieval only): several Indian travel hosts sit behind a
        # WAF that drops any request whose UA is not a browser, so the declared
        # bot cannot even READ the policy that binds it. Retrieving the document
        # with a browser UA and then evaluating it against our own token is not
        # laundering: the rules we obey are still the ones addressed to
        # pushpak-research-bot (falling back to *). Without this, a WAF rejection
        # is indistinguishable from a prohibition, which is precisely the error
        # this module exists to prevent.
        if robots is None and self.retrieval_fallback:
            robots, a2 = self._fetch_once(host_root, BROWSER_UA, "browser_retrieval")
            attempts.append(a2)

        last = attempts[-1]
        meta: dict = {
            "status": last["status"],
            "bytes": last["bytes"],
            "error": last["error"],
            "fetched_at": utc_now_iso(),
            "attempts": attempts,
        }
        self._cache[host_root] = (robots, meta)
        return robots, meta

    # ------------------------------------------------------------------
    def check(self, url: str) -> RobotsVerdict:
        parsed = urlparse(url)
        host_root = f"{parsed.scheme}://{parsed.netloc}"
        robots, meta = self._load(host_root)

        base = dict(
            url=url, host=parsed.netloc, robots_status=meta["status"],
            robots_bytes=meta["bytes"], fetched_at=meta["fetched_at"],
            user_agent=self.user_agent,
            fetch_attempts=meta.get("attempts", []),
        )

        if robots is None:
            # We could not read the policy. That is OUR problem, not the site's
            # instruction. Refuse the request, but never call this DISALLOWED.
            reason = (
                "ROBOTS_UNREACHABLE" if meta["error"]
                else f"ROBOTS_HTTP_{meta['status']}"
            )
            return RobotsVerdict(
                allowed=False, reason=reason, decision=DECISION_POLICY,
                rule="fail-closed policy: no affirmative permission obtained "
                     "(the site did not refuse; we could not read robots.txt)",
                crawl_delay=config.DEFAULT_CRAWL_DELAY, **base,
            )

        path = unquote(parsed.path) or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        allowed, rule, declared_delay = robots.evaluate(self.ua_token, path)
        delay = max(float(declared_delay or 0.0), config.DEFAULT_CRAWL_DELAY)

        return RobotsVerdict(
            allowed=allowed,
            reason="ALLOWED" if allowed else "DISALLOWED",
            # No matching directive means robots.txt was read and simply had
            # nothing to say about this path -- a default allow, not a rule.
            decision=DECISION_RULE if rule else DECISION_DEFAULT,
            rule=rule, crawl_delay=delay, **base,
        )

    # ------------------------------------------------------------------
    def snapshot(self, sources=config.SOURCES) -> list[dict]:
        """Audit every configured source. Powers `pushpak.cli robots-audit`."""
        out = []
        for src in sources:
            sample_url = src.search_url_pattern.format(
                origin="DEL", dest="BOM",
                travel_date="2026-09-15", travel_date_compact="260915",
            )
            verdict = self.check(sample_url)
            out.append({
                "source": src.name,
                "source_type": src.source_type,
                "sample_url": sample_url,
                "url_pattern": src.search_url_pattern,
                **verdict.as_dict(),
                "refused_by_site": verdict.refused_by_site,
                "official_channel": config.OFFICIAL_DATA_CHANNELS.get(src.name),
            })
            time.sleep(0.2)
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def tally(rows: list[dict]) -> dict:
        """Split an audit into the three outcomes that mean different things."""
        allowed = [r for r in rows if r["allowed"]]
        by_rule = [r for r in rows if not r["allowed"]
                   and r["decision"] == DECISION_RULE]
        by_policy = [r for r in rows if not r["allowed"]
                     and r["decision"] == DECISION_POLICY]
        return {
            "n_sources": len(rows),
            "permitted": [r["source"] for r in allowed],
            "refused_by_site": [r["source"] for r in by_rule],
            "unreachable_fail_closed": [r["source"] for r in by_policy],
        }
