"""Where a run's output goes.

Everything used to land directly in `--out`, so a second investigation
overwrote the first one's report, evidence package and audit trail. Each
`--domain` run now gets `<out>/<domain>/`.

These drive the CLI with `run_case` stubbed: the layout is decided before any
collection happens, and binding the test to the network would make it slow and
flaky for no extra coverage.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from attribution_suite import cli


class _Stop(Exception):
    """Raised by the stub once it has recorded the directory.

    The directory is chosen before collection starts, so there is nothing after
    that point for these tests to assert. Emulating the full result object
    instead would make this file track every field the reporting code touches,
    which is a different test's job.
    """


@pytest.fixture
def no_collection(monkeypatch):
    """Record the directory each run is given, then stop it."""
    seen: list[str] = []

    def fake_run_case(case_path, out, **kw):
        seen.append(str(out))
        Path(out).mkdir(parents=True, exist_ok=True)
        # A real run leaves a report behind, and the "already used" check looks
        # for exactly that, so the stub leaves one too -- otherwise a second
        # run of one domain would reuse the directory and the repeat test would
        # pass for the wrong reason.
        (Path(out) / "attribution_report.md").write_text("# stub\n")
        raise _Stop

    monkeypatch.setattr(cli, "run_case", fake_run_case)
    return seen


def run_cli(*argv) -> None:
    """Drive the CLI until collection would begin."""
    with pytest.raises(_Stop):
        cli.main(["run", *argv])


# ---- the layout ------------------------------------------------------------ #

def test_a_domain_run_gets_its_own_directory(tmp_path, no_collection):
    run_cli("--domain", "example.com", "--authorization", "t",
              "--out", str(tmp_path / "out"))
    assert no_collection == [str(tmp_path / "out" / "example.com")]
    assert (tmp_path / "out" / "example.com" / "case.yaml").is_file(), \
        "the synthesised case file belongs in the per-domain directory too"


def test_two_domains_do_not_overwrite_each_other(tmp_path, no_collection):
    """The reported defect: one `out` folder for every search."""
    out = tmp_path / "out"
    for domain in ("example.com", "iana.org"):
        run_cli("--domain", domain, "--authorization", "t", "--out", str(out))
    assert no_collection == [str(out / "example.com"), str(out / "iana.org")]
    for domain in ("example.com", "iana.org"):
        assert (out / domain / "attribution_report.md").is_file(), domain


def test_a_repeat_of_one_domain_does_not_destroy_the_first_run(
        tmp_path, no_collection):
    """These directories hold an evidence package and an audit trail.

    The manifest commits to the bytes retrieved and the trail records what was
    asked and when. Replacing one in place destroys a record somebody may
    already be relying on, and does it silently -- `secure_mkdir` passes
    `exist_ok=True` and the writes truncate. A suffixed sibling costs a
    directory and keeps both.
    """
    out = tmp_path / "out"
    for _ in range(3):
        run_cli("--domain", "example.com", "--authorization", "t", "--out", str(out))
    assert no_collection == [
        str(out / "example.com"),
        str(out / "example.com-2"),
        str(out / "example.com-3"),
    ]
    # The first report is still the first report.
    assert (out / "example.com" / "attribution_report.md").is_file()


def test_a_directory_the_user_pre_created_is_used_as_is(
        tmp_path, no_collection):
    """"Already used" means a run's output is there, not that the path exists.

    Someone who creates `out/example.com` in advance, or points `--out` at a
    checkout, should not be pushed into `-2`.
    """
    out = tmp_path / "out"
    (out / "example.com").mkdir(parents=True)
    run_cli("--domain", "example.com", "--authorization", "t", "--out", str(out))
    assert no_collection == [str(out / "example.com")]


# ---- the domain is user input ---------------------------------------------- #

@pytest.mark.parametrize("hostile", [
    "../../etc/passwd",
    "/etc/passwd",
    "..",
    "a/b/c",
    "example.com/../../..",
])
def test_no_domain_can_place_output_outside_the_named_directory(
        tmp_path, no_collection, hostile):
    """`--domain` becomes a directory name, so it is reduced and checked.

    A value like `../../etc` must not put an evidence package somewhere the
    user did not name.
    """
    out = (tmp_path / "out").resolve()
    run_cli("--domain", hostile, "--authorization", "t", "--out", str(out))
    assert no_collection, "the run never reached collection"
    written = Path(no_collection[0]).resolve()
    assert written.is_relative_to(out), f"{written} escaped {out}"
    assert written.parent == out, "output was nested deeper than one level"


def test_an_empty_domain_is_refused_before_anything_is_written(
        tmp_path, no_collection, capsys):
    """`--domain ""` is not a domain, and must not become a directory.

    It is rejected where the other missing-argument cases are, so nothing
    reaches collection and no `domain`-named fallback directory appears.
    """
    out = tmp_path / "out"
    assert cli.main(["run", "--domain", "", "--authorization", "t",
                     "--out", str(out)]) == 2
    assert "--domain" in capsys.readouterr().err
    assert not no_collection
    assert not out.exists(), "an empty domain created an output directory"


def test_the_directory_name_is_the_normalised_host(tmp_path, no_collection):
    """A pasted URL and a capitalised host are the same investigation."""
    out = tmp_path / "out"
    run_cli("--domain", "https://Example.COM/ads.txt", "--authorization", "t",
              "--out", str(out))
    assert no_collection == [str(out / "example.com")]


# ---- the batch route shares the same code path ----------------------------- #

def test_the_batch_route_does_not_nest_the_domain_twice(
        tmp_path, no_collection):
    """`--domains FILE` built `<out>/<domain>` itself.

    With the single-run path now naming the directory, leaving that in place
    produced `<out>/<domain>/<domain>`. One code path, named in one place.
    """
    listing = tmp_path / "domains.txt"
    listing.write_text("example.com\niana.org\n")
    out = tmp_path / "out"
    # A batch deliberately survives one entry failing, so the stub's sentinel
    # is caught per domain and never leaves `main`. The directories it was
    # handed are the thing under test.
    cli.main(["run", "--domains", str(listing), "--authorization", "t",
              "--out", str(out)])
    assert no_collection == [str(out / "example.com"), str(out / "iana.org")]


# ---- a case file is the user's own layout ---------------------------------- #

def test_a_case_file_run_writes_where_it_was_told(tmp_path, no_collection):
    """Unchanged on purpose.

    A case file can carry several seeds, so there is no one domain to name the
    directory after, and the person who wrote it chose the layout.
    """
    case = tmp_path / "case.yaml"
    case.write_text(
        "case_ref: X\nauthorization: t\nseeds: [domain:a.example, "
        "domain:b.example]\n")
    out = tmp_path / "out"
    run_cli("--case", str(case), "--out", str(out))
    assert no_collection == [str(out)]


# ---- the helpers ----------------------------------------------------------- #

def test_case_dir_keeps_counting_past_a_crowded_root(tmp_path):
    """Nine hundred and ninety-nine runs of one domain is not a crash."""
    out = tmp_path / "out"
    for n in list(range(1, 4)):
        d = out / ("example.com" if n == 1 else f"example.com-{n}")
        d.mkdir(parents=True)
        (d / "attribution_report.md").write_text("x")
    assert cli._case_dir(out, "example.com") == out / "example.com-4"


@pytest.mark.parametrize("hostile", [
    r"a\b\c",                       # a Windows separator
    "C:",                           # a Windows drive
    "host name with spaces",
    "a\tb",
    "a\nb",
    "%2e%2e%2f",
    "a;rm -rf b",
    "a$(whoami)",
    "‮evil",                   # a right-to-left override
    "ünïcode.example",
])
def test_the_directory_name_holds_only_hostname_characters(hostile):
    """A property of the slug, not of containment on this OS.

    On Linux only `/` separates, and that is already gone by the time the
    character filter runs -- so a containment assertion cannot tell whether the
    filter is there, which is how the first version of this test passed with it
    removed. What the filter is actually for is everything else that should
    never reach a path: a Windows separator or drive letter, whitespace, shell
    metacharacters, a bidirectional override that makes a directory name
    display as something other than what it is.
    """
    slug = cli._domain_slug(hostile)
    assert slug, "the slug must never be empty"
    assert re.fullmatch(r"[a-z0-9._\-]+", slug), f"{hostile!r} -> {slug!r}"
    assert "/" not in slug and "\\" not in slug
    assert ".." not in slug
    assert slug == slug.strip("._-"), "a leading or trailing separator survived"
