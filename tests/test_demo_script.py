"""The demo is a deliverable, so it is tested like one.

It lived outside every repository, which is how its claims drifted from the
tool: a beat that demoted only one orientation of a claim, a refusal message
that could never print, and a runbook asserting a portfolio size the command
does not produce. None of that was visible to CI because CI had never seen the
file.

Each beat runs in a subprocess, which is what a presenter actually does.
"""

import subprocess
import sys
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[1] / "demo" / "ncs_demo.py"


def _run(args, cwd, timeout=600):
    return subprocess.run([sys.executable, str(DEMO), *args], cwd=cwd,
                          capture_output=True, text=True, timeout=timeout)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """One corpus, built once, by the real `paytrace-index build`."""
    d = tmp_path_factory.mktemp("demo")
    r = _run(["setup"], d)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "corpus ready" in r.stdout
    assert (d / "ncs-demo" / "corpus.db").exists()
    return d


def test_the_demo_script_ships_with_the_package():
    assert DEMO.exists(), f"{DEMO} is missing; the runbook tells people to run it"


def test_beat_1_separates_boilerplate_from_a_discriminating_account(world):
    r = _run(["declaration"], world)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 boilerplate, 1 discriminating" in r.stdout
    assert "holders=16" in r.stdout


def test_beat_2_names_the_payee_from_sellers_json(world):
    r = _run(["payee"], world)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Example Media Holdings Ltd" in r.stdout
    assert "the ad system, not the site" in r.stdout


def test_beat_3_portfolio_count_matches_what_the_runbook_claims(world):
    """The runbook prints this list. It claimed a count; now the count is
    asserted, because a number in a runbook that the command does not produce
    is a correction made on stage."""
    db = world / "ncs-demo" / "corpus.db"
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; from paytrace.index import _main; "
         "sys.exit(_main(['lookup', '--db', sys.argv[1], '--seller', "
         "'adnet.example/156423']))", str(db)],
        capture_output=True, text=True, cwd=world, timeout=300)
    domains = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(domains) == 16, f"the runbook says sixteen; got {len(domains)}"
    assert "target-site.example" in domains


def test_beat_4_finds_the_scrubbed_identifier_in_the_archive(world):
    r = _run(["archive"], world)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "NO — removed" in r.stdout
    assert "1635 days" in r.stdout


def test_beat_5_runs_all_four_planted_identifier_checks(world):
    """Three of the four used to be reachable; asymmetry was called by nothing.
    And demotion is asserted in BOTH claim orientations, because the one the
    real collectors emit was the one that escaped it."""
    r = _run(["planted"], world)
    assert r.returncode == 0, r.stdout + r.stderr
    for check in ("load_bearing", "temporal_depth", "asymmetry"):
        assert check in r.stdout, f"{check} is not reported"
    assert "not checked" not in r.stdout, "a check could not run"
    assert "CORROBORATED" in r.stdout and "INERT" in r.stdout
    planted = r.stdout.split("INERT", 1)[1]
    assert "domain -> id: weight 0" in planted
    assert "id -> domain: weight 0" in planted, \
        "the orientation real collectors emit was not demoted"


def test_beat_6_injection_is_defeated_only_with_the_guards_on(world):
    r = _run(["injection"], world)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "NAIVE" in r.stdout and "DEFENDED" in r.stdout
    naive = r.stdout.split("NAIVE", 1)[1].split("DEFENDED", 1)[0]
    defended = r.stdout.split("DEFENDED", 1)[1]
    assert "Northwind Hosting Cooperative" in naive, \
        "the naive run no longer adopts the planted entity; the beat is void"
    assert "adopted from retrieved data" in naive
    assert "Example Media Holdings Ltd" in defended
    assert "derived from the evidence graph" in defended
    assert r.stdout.count("likely_injection") == 2, \
        "both runs must flag the attempt"


def test_the_injection_beat_refuses_live_with_a_reason(world):
    """It needs a poisoned world. The refusal used to be shadowed by a generic
    one, so the specific message could never print."""
    r = _run(["injection", "--live", "x.example"], world)
    assert r.returncode != 0
    assert "poisoning a real site's ads.txt is not ours to do" in (
        r.stdout + r.stderr)


def test_fixture_output_is_deterministic(world):
    """The runbook promises rehearsal matches the room."""
    a = _run(["planted"], world)
    b = _run(["planted"], world)
    assert a.stdout == b.stdout


def test_beat_7_binds_both_addresses_and_draws_neither(world):
    """The beat exists because the arithmetic cannot tell the two cases apart.

    An operator who embeds a journalist's avatar hash produces a binding as
    exact as the operator's own. What separates them is corroboration from a
    source that is not the page -- so the beat fails if the planted address
    reaches the map, and it fails just as hard if the arithmetic stops
    binding it, because a check that quietly stopped matching would look
    identical to a check that is working.
    """
    r = _run(["report"], world)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "requests made: 0" in r.stdout, \
        "the binding is computed; a request here means it started probing"
    assert r.stdout.count("gravatar-md5") == 2
    assert "j.doe@unrelated-newsroom.example" in r.stdout, \
        "the planted address belongs in the indicator table"

    fence = r.stdout.split("```mermaid")[1].split("```")[0]
    assert "flowchart LR" in fence
    assert "j.doe" not in fence and "e640848975a41092a2d56593a41ae743" not in fence
    assert "adnet.example/156423" in fence, "the payout chain is corroborated"
    assert "not a probability" in r.stdout

    assert "UNEXPECTED" not in r.stdout
    assert "displays the address's avatar" in r.stdout, \
        "the caveat must reach the executive summary, not only the code"
