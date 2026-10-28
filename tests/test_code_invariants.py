"""Structural self-audit.

The same class of defect recurred: an object built and never
called, a flag parsed and never read, a docstring claiming a protection the code
does not implement. Every one passed the test suite, because the suite tested
the parts rather than the seams.

These are cheap structural assertions that would have caught them.
"""

import ast
import inspect
import re

import pytest

PACKAGES = ("attribution_graph", "paytrace", "handle_correlation",
            "attribution_suite")


def _module_source(name):
    import importlib

    return inspect.getsource(importlib.import_module(name))


# ---- wiring ---------------------------------------------------------------- #

def test_fetcher_uses_the_egress_it_is_given():
    """EgressPool was held beside a client that never used it, so the manifest
    recorded a vantage point no request used."""
    from paytrace.net import Fetcher

    src = inspect.getsource(Fetcher)
    assert "def client_for" in src
    assert "egress.proxy_url()" in src


def test_runner_passes_what_it_builds():
    """run_case() built a Fetcher, a PolicyEngine and an EvidenceLog and
    connected none of them; a run wrote an empty package and reported
    evidence_verified=True."""
    from attribution_suite import runner

    src = inspect.getsource(runner.run_case)
    assert "evidence_log=ev" in src
    assert "policy=policy" in src

    # The egress is built by a helper now, because an ambient proxy has to be
    # reported rather than silently ignored. The invariant is unchanged -- the
    # Fetcher is handed a real pool -- so follow the call instead of pinning
    # the literal to one function.
    assert "egress=_egress_pool(" in src
    assert "EgressPool.from_case" in inspect.getsource(runner._egress_pool)


def test_assess_passes_declared_dependence():
    """Claim.dependence_class existed and assess() never gathered it, so
    'declared always wins' was true of the helper and false of production."""
    from attribution_graph import scoring

    src = inspect.getsource(scoring.assess)
    assert "group_declared" in src
    assert "adjust_for_dependence(per_group, group_collectors, group_declared)" in src


def test_resolver_compares_like_with_like():
    """A nats threshold was compared against a probability, which silently
    disabled every inferential merge."""
    from attribution_graph import resolve as resolve_mod

    src = inspect.getsource(resolve_mod)
    assert "scored.append((a.log_odds" in src
    assert "MERGE_LOG_ODDS_THRESHOLD" in src
    assert "a.probability >= merge_threshold" not in src


# ---- claims match implementations ------------------------------------------ #

@pytest.mark.parametrize("package", PACKAGES)
def test_no_module_claims_a_protection_it_lacks(package):
    """`max_bytes` sliced an already-materialised body while its comment
    described a streaming cap."""
    src = _module_source(package)
    if "stops at the cap" in src or "stops reading at the limit" in src:
        assert "aiter_bytes" in src


def test_core_network_claim_is_accurate():
    """attribution-graph contains screenshot.py, which navigates URLs with a
    browser. The unqualified 'no network I/O' claim was false; the qualified
    'the inference core performs no network I/O' is not."""
    import pathlib

    # Skip rather than pass vacuously. With no siblings checked out the glob
    # returns nothing, the loop body never runs, and the test reports success
    # while having examined zero files -- a green result covering nothing,
    # which is the failure mode this test exists to prevent elsewhere.
    readmes = list(pathlib.Path(__file__).parents[2].glob("*/README.md"))
    if not readmes:
        pytest.skip("no sibling repositories checked out")

    for readme in readmes:
        text = readme.read_text()
        assert "**No network I/O**" not in text, (
            f"{readme} makes an unqualified no-network-IO claim while "
            "screenshot.py lives in the inference package")


# ---- public surface -------------------------------------------------------- #

@pytest.mark.parametrize("package", PACKAGES)
def test_everything_in_all_is_importable(package):
    import importlib

    mod = importlib.import_module(package)
    missing = [n for n in getattr(mod, "__all__", []) if not hasattr(mod, n)]
    assert not missing, f"{package}.__all__ lists unreachable names: {missing}"


def test_no_cli_flag_is_silently_ignored():
    """--screenshots was accepted and did nothing. A flag that lies is worse
    than one that is absent."""
    from attribution_suite import cli

    src = inspect.getsource(cli)
    for m in re.finditer(r'add_argument\(\s*"--([\w-]+)"', src):
        attr = m.group(1).replace("-", "_")
        read = (re.search(rf"\ba\.{attr}\b", src)
                or re.search(rf'getattr\(a,\s*"{attr}"', src)
                or re.search(rf"\b{attr}=", src))
        assert read, f"--{m.group(1)} is parsed but never read"


# ---- language consistency -------------------------------------------------- #

@pytest.mark.parametrize("package", PACKAGES)
def test_no_package_advertises_calibration_it_does_not_have(package):
    """PyPI descriptions and module docstrings are the public scientific
    claim, and they said 'calibrated' after the methodology said otherwise."""
    src = _module_source(package)
    first = src.split("\n\n")[0].lower()
    assert "calibrated" not in first, (
        f"{package} docstring claims calibration; the model is unvalidated")


def test_bands_are_evidence_strength_not_probability():
    from attribution_graph.scoring import Band

    names = {b.value for b in Band}
    assert "ATTRIBUTED" not in names and "PROBABLE" not in names
    assert "STRONG_EVIDENCE" in names


#: Dependencies whose assignment-without-use has been an actual defect here.
WIRED_ATTRS = ("policy", "evidence_log", "egress", "index", "resolver")


def test_ast_finds_no_unused_constructor_wiring():
    """A dependency assigned in __init__ and read nowhere is the shape of
    every wiring defect found so far.

    Scoped to the class, plus reads on something other than `self` anywhere in
    the four packages. Both halves are needed and each was wrong alone:

    - Per-FILE counting flagged `ReplayFetcher.egress`, which exists to
      satisfy the fetcher interface and is read by `paytrace` and the runner.
      A stand-in class implements an interface precisely so that other modules
      can read it.
    - Counting package-wide instead made the test vacuous: `self.evidence_log`
      is read inside `Fetcher`, so a *different* class assigning it and never
      reading it passed. Verified by planting exactly that and watching the
      test go green.

    So: read on `self` within the same class, or read on some other object
    somewhere. Neither, and it is wired to nothing.
    """
    import importlib
    import pathlib

    roots = [pathlib.Path(importlib.import_module(p).__file__).parent
             for p in PACKAGES]
    files = [f for root in roots for f in root.rglob("*.py")
             if "__pycache__" not in str(f)
             and "attribution_portable.py" not in f.name]

    trees = {}
    external: dict[str, int] = {}
    for f in files:
        try:
            trees[f] = ast.parse(f.read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(trees[f]):
            if (isinstance(node, ast.Attribute)
                    and isinstance(node.ctx, ast.Load)
                    and not (isinstance(node.value, ast.Name)
                             and node.value.id == "self")):
                external[node.attr] = external.get(node.attr, 0) + 1

    checked = 0
    for f, tree in trees.items():
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            init = next((n for n in cls.body if isinstance(n, ast.FunctionDef)
                         and n.name == "__init__"), None)
            if init is None:
                continue
            assigned = {
                tgt.attr
                for stmt in ast.walk(init) if isinstance(stmt, ast.Assign)
                for tgt in stmt.targets
                if isinstance(tgt, ast.Attribute)
                and isinstance(tgt.value, ast.Name) and tgt.value.id == "self"
                and tgt.attr in WIRED_ATTRS
            }
            if not assigned:
                continue
            self_reads = {
                n.attr for n in ast.walk(cls)
                if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load)
                and isinstance(n.value, ast.Name) and n.value.id == "self"
            }
            for attr in sorted(assigned):
                checked += 1
                assert attr in self_reads or external.get(attr, 0) > 0, (
                    f"{f.name}: {cls.name}.{attr} is assigned in __init__, "
                    f"never read in {cls.name}, and read on nothing else "
                    "anywhere — the wiring defect pattern")

    assert checked, "nothing was scanned — this test would pass vacuously"


def test_no_fetcher_is_closed_in_its_own_event_loop():
    """`asyncio.run(fetcher.aclose())` after `asyncio.run(engine.run())` closes
    the client from a second loop, and real connections cannot be: it crashed
    `attribution run` and `paytrace run` on every real network while every
    mocked test passed. Work and cleanup must share one loop."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    srcs = [p for pkg in ("attribution-graph", "paytrace", "handle-correlation",
                          "attribution-suite")
            for p in (root / pkg / "src").rglob("*.py") if (root / pkg).is_dir()]
    if not srcs:
        pytest.skip("no sibling repositories checked out")
    bad = [f"{p.name}:{i}" for p in srcs
           for i, line in enumerate(p.read_text().splitlines(), 1)
           if re.search(r"asyncio\.run\([^)]*\.aclose\(\)", line.split("#")[0])]
    assert not bad, f"fetcher closed in its own event loop: {bad}"
