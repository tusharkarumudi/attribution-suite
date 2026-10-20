"""A whole investigation against the REAL network.

This is the test that would have caught the expensive failures. Both crashed
*after* collection and before any output was written, and both were invisible
to the mocked suite because mock transports hold no connections:

* `attribution run` closed its fetcher in a second `asyncio.run`, raising
  "Event loop is closed" — no report at all.
* `attribution ask` fell back to a sync adapter inside a running loop, raising
  "asyncio.run() cannot be called from a running event loop".

Marked `network` and excluded by default. CI runs them on a schedule:
`pytest -m network`.
"""

from __future__ import annotations

import pytest
import yaml

pytestmark = pytest.mark.network

#: A real, stable, uncontroversial site. It publishes no ads.txt, so the run
#: ends with an honest negative — which is exactly what must be written out.
LIVE_DOMAIN = "pypi.org"


def _case(tmp_path, budget: int = 25):
    path = tmp_path / "case.yaml"
    path.write_text(yaml.safe_dump({
        "case_ref": f"live-{LIVE_DOMAIN}",
        "authorization": "automated live smoke test, read-only",
        "seeds": [f"domain:{LIVE_DOMAIN}"],
        "entity_types_allowed": ["Company"],
        "audit_path": str(tmp_path / "audit.jsonl"),
        "robots_policy": "respect",
        "pivot_radius": 1,
        "max_requests": budget,
    }, sort_keys=False))
    return path


def test_a_live_run_writes_its_report(tmp_path):
    """Collection completing is not the bar: the outputs must exist.

    The event-loop bug let a run gather everything and then die before writing,
    leaving an empty directory and a traceback.
    """
    from attribution_suite import runner

    res = runner.run_case(str(_case(tmp_path)), tmp_path / "out")
    names = {p.name for p in res.outputs}
    assert "attribution_report.md" in names, f"no report written: {names}"
    assert (tmp_path / "out" / "attribution_report.md").read_text().strip()
    assert res.stats.get("result_valid") is True


def test_a_live_run_records_what_it_could_not_reach(tmp_path):
    """An INCOMPLETE result must say which retrievals were lost. Silence here
    is what made four rounds of diagnosis necessary."""
    from attribution_suite import runner

    res = runner.run_case(str(_case(tmp_path)), tmp_path / "out")
    if res.stats.get("collection_blocked"):
        assert any("blocked:" in w for w in res.warnings), \
            "blocked retrievals must name their URLs"
        assert "blocked_on_subject" in res.stats


def test_a_live_ask_completes(tmp_path):
    """`attribution ask` crashed at the pivot on every non-offline run."""
    from attribution_suite import Investigator

    inv = Investigator(offline=False).investigate(
        f"who operates {LIVE_DOMAIN}?", out=str(tmp_path / "ask"))
    assert inv.pivot is not None, "the pivot step must run to completion"
