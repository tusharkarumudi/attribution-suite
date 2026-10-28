"""Offline mode: the chain with no network and nothing installed.

This exists because the skill did not work where it was meant to. A chat
sandbox refuses `pip install`, so the assistant got "command not found" and
fell back to ad-hoc web lookups -- no bands, no correlation groups, no
evidence package, no audit trail. Every property the toolkit provides was
exactly what got dropped, and nothing in the repository noticed, because
nothing here had ever run the way a user runs it.

The first test is the one that matters: the bundle and the installed packages
must produce the same report from the same documents. If they ever diverge,
the bundle is a second implementation and the whole design is void.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from attribution_suite.portable import (
    Corpus,
    ReplayFetcher,
    _is_fetchable,
    _is_sensitive,
    filename_for,
    run,
    url_key,
)

BUILD = Path(__file__).resolve().parents[1] / "scripts" / "build_portable.py"
SKILL_DIR = (Path(__file__).resolve().parents[1] / "src" / "attribution_suite"
             / "skill")
WORKSPACE = Path(__file__).resolve().parents[2]


def _buildable():
    """Skip when the bundle cannot be built here.

    Building embeds all four packages, so it needs all four checked out. CI
    clones siblings on a best-effort basis and tolerates their absence, and a
    workspace assertion that hard-fails there reports a checkout layout as a
    defect. The checked-in bundle is still tested in every layout by the
    documentation tests below, which do not rebuild it.
    """
    missing = [r for r in ("attribution-graph", "paytrace")
               if not (WORKSPACE / r).is_dir()]
    if missing:
        pytest.skip(f"not the four-repository layout: {', '.join(missing)} "
                    "not checked out, so the bundle cannot be built")

ADS_TXT = (
    "OWNERDOMAIN=examplemedia.example\n"
    "adrelay.example, 7kQmZ2bXr4TnWp8Ls, DIRECT, f08c47fec0942fa0\n"
)
SELLERS = json.dumps({"sellers": [
    {"seller_id": "7kQmZ2bXr4TnWp8Ls", "name": "Example Medya Ltd.",
     "domain": "examplemedia.example", "seller_type": "PUBLISHER"}]})


@pytest.fixture(autouse=True)
def _never_write_into_the_repo(tmp_path, monkeypatch):
    """Offline mode writes its audit trail to the working directory, which is
    the point -- but under pytest the working directory is the repository, and
    a stray `<case>-audit.jsonl` ended up inside a built sdist. Caught by
    VERIFY.sh, which is the only reason it was not shipped."""
    monkeypatch.chdir(tmp_path)


def _write(d: Path, url: str, body: str, seq: int = 1) -> Path:
    p = d / filename_for(url, seq)
    p.write_text(body)
    return p


@pytest.fixture
def corpus_dir(tmp_path):
    d = tmp_path / "fetched"
    d.mkdir()
    _write(d, "https://viewer-site.example/ads.txt", ADS_TXT, 1)
    _write(d, "https://viewer-site.example/app-ads.txt", "# none\n", 2)
    _write(d, "https://examplemedia.example/ads.txt", ADS_TXT, 3)
    _write(d, "https://examplemedia.example/app-ads.txt", "# none\n", 4)
    _write(d, "https://adrelay.example/sellers.json", SELLERS, 5)
    _write(d, "https://www.adrelay.example/sellers.json", '{"sellers":[]}', 6)
    return d


# ---- the property the whole design rests on -------------------------------- #

def test_the_bundle_and_the_packages_produce_the_same_report(tmp_path, corpus_dir):
    """No second implementation. The bundle embeds the real modules, so the
    same documents must give the same report -- otherwise this is a fork of
    the scoring rules and the dead copy is the one someone will 'fix'."""
    _buildable()
    bundle = tmp_path / "attribution_portable.py"
    r = subprocess.run([sys.executable, str(BUILD), "-o", str(bundle)],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    assert bundle.exists()

    args = ["viewer-site.example", "-a", "t", "-d", str(corpus_dir)]
    from_bundle = subprocess.run([sys.executable, str(bundle), *args],
                                 capture_output=True, text=True, timeout=900,
                                 cwd=tmp_path)
    from_pkg = subprocess.run(
        [sys.executable, "-m", "attribution_suite.portable", *args],
        capture_output=True, text=True, timeout=900, cwd=tmp_path)
    assert from_bundle.returncode == from_pkg.returncode, from_bundle.stderr

    def _norm(text):
        return [ln for ln in text.splitlines()
                if not ln.startswith(("- Generated:", "- Retention expiry:"))]

    assert _norm(from_bundle.stdout) == _norm(from_pkg.stdout)
    assert "Executive summary" in from_bundle.stdout


def test_the_bundle_serves_the_modules_itself(tmp_path):
    """The deployment condition, asserted where it can actually be observed.

    The previous version ran the bundle and checked it produced a report --
    which it did, by falling through to the installed packages, because the
    finder only claims modules it embedded and `-I` does not exclude the
    interpreter's own site-packages. It passed on an eight-module bundle built
    from a single-repo checkout: exactly the file that would have failed on
    the machine the bundle exists for. So this asks where the modules came
    from instead of whether the run succeeded.
    """
    _buildable()
    bundle = tmp_path / "attribution_portable.py"
    subprocess.run([sys.executable, str(BUILD), "-o", str(bundle)],
                   capture_output=True, timeout=600, check=True)
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('ap', {str(bundle)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "import attribution_graph, paytrace, paytrace.collectors\n"
        "for mod in (attribution_graph, paytrace, paytrace.collectors):\n"
        "    print(mod.__name__, mod.__file__)\n")
    r = subprocess.run([sys.executable, str(probe)], capture_output=True,
                       text=True, timeout=900, cwd=tmp_path)
    assert r.returncode == 0, r.stderr[-2000:]
    for line in r.stdout.strip().splitlines():
        name, origin = line.split(" ", 1)
        assert origin.startswith("<bundled>"), (
            f"{name} was loaded from {origin}, not from the bundle — the "
            "bundle is incomplete and is silently using installed packages")


def test_the_bundle_runs_on_a_machine_without_the_packages(tmp_path, corpus_dir):
    """And it still has to actually work, with a stock interpreter."""
    _buildable()
    bundle = tmp_path / "attribution_portable.py"
    subprocess.run([sys.executable, str(BUILD), "-o", str(bundle)],
                   capture_output=True, timeout=600, check=True)
    python = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
    r = subprocess.run(
        [python, "-I", str(bundle), "viewer-site.example",
         "-a", "t", "-d", str(corpus_dir)],
        capture_output=True, text=True, timeout=900, cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "Attribution assessment" in r.stdout


def test_the_checked_in_bundle_is_not_stale(tmp_path):
    """`--check` is what VERIFY.sh runs. A bundle built from older sources is
    the divergence this design exists to prevent, arriving by neglect."""
    _buildable()
    bundle = tmp_path / "b.py"
    subprocess.run([sys.executable, str(BUILD), "-o", str(bundle)],
                   capture_output=True, timeout=600, check=True)
    ok = subprocess.run([sys.executable, str(BUILD), "--check", "-o", str(bundle)],
                        capture_output=True, text=True, timeout=600)
    assert ok.returncode == 0, ok.stdout

    bundle.write_text(bundle.read_text() + "\n# tampered\n")
    bad = subprocess.run([sys.executable, str(BUILD), "--check", "-o", str(bundle)],
                         capture_output=True, text=True, timeout=600)
    assert bad.returncode == 1, "the staleness check cannot fail, so it guards nothing"
    assert "stale" in bad.stdout


# ---- the honesty properties ------------------------------------------------ #

def test_a_truncated_document_is_detected_not_declared():
    """The failure that prompted this: a browsing tool silently cut the large
    sellers.json files short. A truncated document read as 'the seller is not
    in here' turns *not checked* into *checked and absent*, which is the one
    inversion this toolkit exists to prevent. So it is detected by parsing,
    never by asking the assistant to be honest about its own tool."""
    c = Corpus()
    c.add("https://a.example/sellers.json", SELLERS)
    c.add("https://b.example/sellers.json", SELLERS[:60])
    assert c.get("https://a.example/sellers.json")["truncated"] is False
    assert c.get("https://b.example/sellers.json")["truncated"] is True


def test_a_truncated_read_is_recorded_on_the_fetcher():
    c = Corpus()
    c.add("https://b.example/sellers.json", '{"sellers":[{"seller_')
    f = ReplayFetcher(c)
    import asyncio
    asyncio.run(f.get("https://b.example/sellers.json"))
    assert f.truncated_reads
    assert "incomplete" in f.truncated_reads[0][1]


def test_the_report_says_the_retrievals_were_not_its_own(corpus_dir):
    """A report produced this way must not be mistaken for a verified one.
    The digests commit to what was saved, not to what the origin served."""
    text, code = run("viewer-site.example", "t", corpus_dir)
    assert code == 0
    assert "OFFLINE REPLAY" in text
    assert "not to what the origin served" in text
    assert "## Documents read" in text
    assert "SHA-256" in text


def test_a_document_saved_under_the_wrong_name_is_named_not_ignored(corpus_dir):
    """Silence here would be the worst outcome: an investigation missing its
    ads.txt, reporting a confident absence."""
    (corpus_dir / "ads.txt").write_text(ADS_TXT)
    text, _code = run("viewer-site.example", "t", corpus_dir)
    assert "were not used" in text
    assert "ads.txt" in text


def test_an_audit_trail_is_written(tmp_path, corpus_dir, monkeypatch):
    """"Your ticket numbers and reasons are only in this chat" was a real
    cost of running without the toolkit. It costs one append-only file."""
    monkeypatch.chdir(tmp_path)
    run("viewer-site.example", "ticket ACME-1234: vendor review", corpus_dir)
    trails = list(tmp_path.glob("*-audit.jsonl"))
    assert trails, "no audit trail was written"
    body = trails[0].read_text()
    assert "ticket ACME-1234" in body


# ---- the plan -------------------------------------------------------------- #

def test_the_plan_comes_from_the_collectors_not_a_list(tmp_path):
    """The plan is whatever the real collectors asked for and did not find, so
    it cannot go stale against them. A hand-maintained list would."""
    text, code = run("viewer-site.example", "t", tmp_path)
    assert code == 3
    assert "https://viewer-site.example/ads.txt" in text
    assert "save as: 01-ads_txt-viewer-site.example-" in text


def test_the_plan_puts_the_payment_chain_first(tmp_path):
    text, _ = run("viewer-site.example", "t", tmp_path)
    body = text[text.index("## Fetch these"):]
    first = body.index("ads.txt")
    assert all(first < body.index(x) for x in ("rdap.org", "crt.sh")
               if x in body)


def test_sensitive_paths_are_held_back_by_default(tmp_path):
    """The toolkit probes these under an authorised, rate-limited case. An
    assistant fetching them from a chat is unauthenticated probing of someone
    else's server, carrying the user's own address."""
    text, _ = run("viewer-site.example", "t", tmp_path)
    assert "/.env" not in text
    assert "/.git/config" not in text
    assert "left out of this plan on purpose" in text

    # With the flag they are no longer held back. They are still never
    # *blocking* -- a secret left exposed is not part of the payment chain --
    # so they appear once the chain is complete, among the optional sources.
    opened, _ = run("viewer-site.example", "t", tmp_path, include_sensitive=True)
    assert "left out of this plan on purpose" not in opened


def test_opening_up_sensitive_paths_offers_them_once_the_chain_is_done(corpus_dir):
    opened, code = run("viewer-site.example", "t", corpus_dir,
                       include_sensitive=True)
    assert code == 0
    assert "/.env" in opened or "/.git/config" in opened


@pytest.mark.parametrize("url,ok", [
    ("http://localhost:8000/search", False),
    ("https://127.0.0.1/x", False),
    ("https://10.1.2.3/x", False),
    ("https://yente.internal/x", False),
    ("https://crt.sh/?q=x", True),
])
def test_unreachable_hosts_are_never_in_the_plan(url, ok):
    """A collector may point at a service the operator self-hosts -- Yente
    defaults to localhost. Asking an assistant to fetch that is at best
    confusing and at worst a request against its own machine."""
    assert _is_fetchable(url) is ok


def test_the_run_terminates_once_the_chain_is_complete(corpus_dir):
    """There is always another register to try, so waiting for 'nothing left
    to want' never finishes -- the first version looped forever asking for
    archive snapshots. What remains is offered, not required."""
    text, code = run("viewer-site.example", "t", corpus_dir)
    assert code == 0
    assert "Corroboration still available" in text
    assert "every absence in this report means 'not checked'" in text


# ---- keying ---------------------------------------------------------------- #

def test_a_url_with_a_path_round_trips(tmp_path):
    """Google's sellers.json lives under a path. Rebuilding the URL from the
    readable part of the filename dropped it, so the document that had been
    fetched for it never matched and was silently unused."""
    url = "https://storage.googleapis.com/adx-rtb-dictionaries/sellers.json"
    d = tmp_path / "f"
    d.mkdir()
    _write(d, url, '{"sellers":[]}', 7)
    corpus = Corpus.from_dir(d)
    assert corpus.get(url) is not None


def test_the_sequence_number_is_cosmetic(tmp_path):
    """A file keeps working if it is renumbered or fetched out of order."""
    url = "https://x.example/ads.txt"
    d = tmp_path / "f"
    d.mkdir()
    (d / f"99-ads_txt-x.example-{url_key(url)}.txt").write_text("x\n")
    assert Corpus.from_dir(d).get(url) is not None


def test_sensitive_detection_follows_the_toolkits_own_list():
    assert _is_sensitive("https://x.example/.env")
    assert _is_sensitive("https://x.example/wp-json/wp/v2/users")
    assert not _is_sensitive("https://x.example/ads.txt")


# ---- the docs must describe the tool that actually ships ------------------- #

def test_the_skill_ships_the_bundle_it_tells_people_to_run():
    """The original failure in one line: the skill told an assistant to run
    something that was not there. Checking that the documented command exists
    is cheap and would have caught it."""
    bundle = SKILL_DIR / "attribution_portable.py"
    assert bundle.exists(), "SKILL.md tells the assistant to run this"
    skill_md = (SKILL_DIR / "SKILL.md").read_text()
    install_md = (SKILL_DIR / "INSTALL.md").read_text()
    assert "python attribution_portable.py" in skill_md
    assert "python attribution_portable.py" in install_md


def test_every_flag_the_docs_show_being_passed_actually_exists():
    """Documented flags are a promise.

    Scoped to flags shown on a line that invokes the portable tool, and NOT
    to a hardcoded allowlist. The first version filtered to a known set, so
    planting `--nonexistent-flag` in the docs passed it -- a test that can
    only catch the flags someone remembered to list is a test of the list.
    """
    bundle = SKILL_DIR / "attribution_portable.py"
    help_text = subprocess.run(
        [sys.executable, "-I", str(bundle), "--help"],
        capture_output=True, text=True, timeout=600).stdout

    docs = {name: (SKILL_DIR / name).read_text()
            for name in ("SKILL.md", "INSTALL.md", "README.md")}
    docs["command"] = (SKILL_DIR / "commands" / "ad-attribution.md").read_text()

    found: list[tuple[str, str]] = []
    for name, text in docs.items():
        for line in text.splitlines():
            if "attribution_portable.py" not in line:
                continue
            # Only what comes AFTER the command on that line. The mode-check
            # one-liner runs `attribution-suite --version || python
            # attribution_portable.py --help`, and attributing the first
            # command's flags to the second is a false report.
            tail = line.split("attribution_portable.py", 1)[1]
            tail = tail.split("||")[0].split("&&")[0]
            for flag in re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", tail):
                found.append((name, flag))

    assert found, "no portable invocation with flags appears in the docs at all"
    for name, flag in found:
        assert flag in help_text, (
            f"{name} shows `{flag}` being passed to attribution_portable.py, "
            "but the tool has no such flag")


def test_the_bundle_runs_on_a_bare_interpreter():
    """`-I` is isolated mode: no user site-packages, no PYTHONPATH, no cwd on
    the path. If it needs anything beyond the standard library, this fails."""
    bundle = SKILL_DIR / "attribution_portable.py"
    r = subprocess.run([sys.executable, "-I", str(bundle), "--help"],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    assert "no network" in r.stdout.lower() or "offline" in r.stdout.lower() \
        or "fetch" in r.stdout.lower()
