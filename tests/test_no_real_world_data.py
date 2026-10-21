"""The repositories must not carry identifiers belonging to real third parties.

This exists because the same mistake happened twice. A live run produces
exactly the realistic sample a README wants, so the payee of an investigated
site ended up on a public PyPI project page, and the companies a register
returned by accident ended up in test fixtures. Neither was caught by review.

`scripts/scan_for_real_world_data.py` is the check; this file proves it works
and runs it over every repository present.
"""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
          / "scripts" / "scan_for_real_world_data.py")
PACKAGES = ("attribution-graph", "paytrace", "handle-correlation",
            "attribution-suite")


def _scanner():
    spec = importlib.util.spec_from_file_location("scan_rwd", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_scanner_exists_and_is_executable():
    assert SCRIPT.exists(), f"{SCRIPT} is missing; the gate calls it"


@pytest.mark.parametrize("pkg", PACKAGES)
def test_no_real_world_data_in_repository(pkg):
    repo = ROOT / pkg
    if not repo.exists():
        pytest.skip(f"{pkg} is not checked out beside this one")
    findings = _scanner().scan([str(repo)])
    assert not findings, "\n".join(
        f"{c}: {v} at {w}" for c, v, w, _ in sorted(set(findings)))


def test_the_denylist_still_fires(tmp_path):
    """The control. A denylist of hashes is silent when it breaks.

    If the normalisation, the tokeniser or the hash changes, every value
    stops matching and the check passes on anything -- so it is shown here to
    still catch a value it is meant to catch.
    """
    scan = _scanner()
    f = tmp_path / "notes.md"
    f.write_text(f"The operator is {scan.CANARY} according to the register.\n")
    findings = scan.scan([str(tmp_path)])
    assert findings, "the denylist matched nothing; the check is inert"
    assert any(scan.CANARY in v for _, v, _, _ in findings)


def test_the_denylist_matches_multi_word_names(tmp_path):
    """A company name is several words, an identifier is one.

    The tokeniser has to produce both. A single-token-only version of this
    check passed happily on a two-word company name.
    """
    scan = _scanner()
    words = scan.CANARY.split("-")
    assert len(words) >= 3
    phrase = " ".join(words[:3])
    assert scan.fingerprint(phrase) not in scan.DENIED, \
        "pick a phrase that is not itself denied"
    f = tmp_path / "x.md"
    f.write_text(f"prefix {scan.CANARY} suffix")
    assert scan.scan([str(tmp_path)]), "the canary token was not found"


def test_a_real_looking_publisher_id_is_rejected(tmp_path):
    """The structural half, which catches values nobody has seen before.

    A denylist only knows what has already leaked. An AdSense id pasted from
    a run tomorrow is new, and must still fail.
    """
    scan = _scanner()
    # Assembled at runtime: written as one literal, this file would itself be
    # a file carrying a real-looking publisher id, and the check would -- quite
    # correctly -- fail on it.
    pid = "pub-" + "7391048256" + "113742"
    (tmp_path / "README.md").write_text(f"the payee is google.com/{pid}\n")
    findings = scan.scan([str(tmp_path)])
    assert any(c == "AdSense publisher id" for c, _, _, _ in findings)


def test_placeholder_identifiers_are_accepted(tmp_path):
    """And the check must not fire on the placeholders the tests do use,
    or it would be turned off within a week."""
    scan = _scanner()
    (tmp_path / "README.md").write_text(
        "google.com/pub-0000000000000314 ca-pub-1234567890123456\n"
        "lei:EXAMPLELEI0000000001  UA-0000000\n"
        "adrelay.example/7kQmZ2bXr4TnWp8Ls other-site.example\n")
    assert not scan.scan([str(tmp_path)])
