#!/usr/bin/env python3
"""Assemble `attribution_portable.py`: the whole analysis chain in one file.

The skill shipped instructions pointing at a package from PyPI, and the chat
sandboxes it was written for refuse to install one. So the assistant reported
"command not found" and fell back to ad-hoc web lookups -- losing bands,
correlation groups, the evidence package and the audit record, which is to say
everything the toolkit is for.

This bundles the real modules, verbatim, into a single file that needs only
the standard library. It does **not** rewrite them. A second implementation of
the scoring rules would drift from the first and the dead one would be the one
somebody "fixes" -- the defect this project has already shipped four times. So
the bundle embeds the exact source, a test asserts it still matches, and
`VERIFY.sh` rebuilds it.

Two shims make it work without third-party packages:

- `httpx` is imported at module scope by `paytrace.net`, which the package's
  `__init__` re-exports. The shim raises on *use*, so an accidental network
  call in offline mode fails with a sentence explaining the contract rather
  than an AttributeError.
- `yaml` is no longer needed: `CaseScope.load` and `load_catalog` import it
  lazily, and the bundle builds its scope in Python.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import pathlib
import sys
import zlib

HERE = pathlib.Path(__file__).resolve()
BASE = HERE.parents[2]

#: Roots to bundle, in import order.
PACKAGES = {
    "attribution_graph": BASE / "attribution-graph" / "src" / "attribution_graph",
    "paytrace": BASE / "paytrace" / "src" / "paytrace",
    "attribution_suite": BASE / "attribution-suite" / "src" / "attribution_suite",
}

#: Modules deliberately left out. Each one would pull a third-party package in
#: for a feature that cannot work offline anyway.
#: Deliberately minimal. The first version excluded anything that touched the
#: network, and the package `__init__` files promptly failed importing their
#: own siblings. Only modules whose third-party dependency cannot be shimmed
#: are left out; everything else is bundled and simply never called offline.
EXCLUDE = {
    "paytrace.agent.agent",          # needs the anthropic SDK
    "attribution_suite.mcp_server",  # needs the mcp SDK
}

SHIM = '''
class _OfflineBase(Exception):
    """Base for every stand-in attribute of an absent network library.

    An `Exception` subclass on purpose, so the two things real code does with
    these names at import time both work: `class PinnedTransport(
    httpx.AsyncHTTPTransport)` subclasses one, and `except httpx.HTTPError`
    needs one to be catchable. Constructing it raises, which is the point --
    an accidental request in offline mode must fail with a sentence that
    explains the contract, not silently return nothing. A fetch that quietly
    returned nothing would be reported as a source checked and found empty,
    which is the single worst thing this toolkit can say.
    """

    _qual = "httpx"

    def __init__(self, *a, **k):
        raise RuntimeError(
            f"{type(self)._qual} was used, but this is the offline bundle: it "
            "has no network and makes no requests. Fetch the URL with your "
            "own browsing tool, save it under the name the plan gave, and run "
            "again.")


class _OfflineModule:
    """A module object whose every attribute is a usable stand-in class."""

    def __init__(self, name):
        self._name = name
        self._cache = {}

    def __getattr__(self, attr):
        if attr.startswith("__") and attr.endswith("__"):
            raise AttributeError(attr)
        if attr not in self._cache:
            qual = f"{self._name}.{attr}"
            self._cache[attr] = type(attr, (_OfflineBase,), {"_qual": qual})
        return self._cache[attr]


def _install_shims():
    import sys as _sys
    for name in ("httpx",):
        if name in _sys.modules:
            continue
        try:
            __import__(name)
        except ImportError:
            mod = _OfflineModule(name)
            mod.__spec__ = None
            mod.__name__ = name
            _sys.modules[name] = mod
'''

LOADER = '''
import base64 as _b64
import importlib.abc as _abc
import importlib.machinery as _mach
import sys as _sys
import zlib as _zlib


class _BundleLoader(_abc.Loader):
    def __init__(self, name): self.name = name

    def create_module(self, spec): return None

    def exec_module(self, module):
        # A synthetic `__file__`. Several modules build a path from it at
        # import time -- `paytrace.catalog` locates its registry data that way
        # -- and without one the bundle failed at import with a bare NameError.
        # The path does not exist, which is correct: the package data is not
        # bundled, so anything that reads it fails where it reads, saying so,
        # rather than at import of something unrelated.
        module.__file__ = f"<bundled>/{self.name.replace('.', '/')}.py"
        raw = _zlib.decompress(_b64.b64decode(_SOURCES[self.name]))
        exec(compile(raw, f"<bundled {self.name}>", "exec"), module.__dict__)


class _BundleFinder(_abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in _SOURCES:
            return None
        spec = _mach.ModuleSpec(fullname, _BundleLoader(fullname),
                                is_package=fullname in _PACKAGES)
        if fullname in _PACKAGES:
            spec.submodule_search_locations = []
        return spec


def _install_finder():
    if not any(isinstance(f, _BundleFinder) for f in _sys.meta_path):
        _sys.meta_path.insert(0, _BundleFinder())
'''


class IncompleteLayout(RuntimeError):
    """A package this bundle must embed is not checked out."""


def iter_modules() -> dict[str, tuple[str, bool]]:
    """{dotted name: (source, is_package)} for everything to embed.

    Refuses a partial build. Without this it happily produced an eight-module
    bundle from a single-repo checkout, and that bundle *appeared* to work:
    the finder only claims modules it embedded, so `import attribution_graph`
    fell straight through to whatever was installed. The tests passed, on a
    file that would have failed on the machine it was built for -- which is
    the whole point of the bundle and precisely what those tests assert.
    """
    missing = [pkg for pkg, root in PACKAGES.items() if not root.is_dir()]
    if missing:
        raise IncompleteLayout(
            "cannot build the offline bundle: "
            + ", ".join(sorted(missing))
            + " not checked out beside this repository. A partial bundle "
              "silently falls through to installed packages and looks like it "
              "works, so this refuses to write one.")
    out: dict[str, tuple[str, bool]] = {}
    for pkg, root in PACKAGES.items():
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            # The built bundle lives in the skill directory, inside the
            # package being bundled. Without this it embeds itself, and every
            # rebuild embeds the previous embedding.
            if "skill" in path.parts:
                continue
            rel = path.relative_to(root)
            parts = list(rel.parts)
            is_pkg = parts[-1] == "__init__.py"
            if is_pkg:
                parts = parts[:-1]
            else:
                parts[-1] = parts[-1][:-3]
            name = ".".join([pkg, *parts]) if parts else pkg
            if name in EXCLUDE:
                continue
            out[name] = (path.read_text(), is_pkg)
    return out


def build() -> str:
    modules = iter_modules()
    payload = {
        name: base64.b64encode(zlib.compress(src.encode(), 9)).decode()
        for name, (src, _p) in modules.items()
    }
    packages = sorted(n for n, (_s, p) in modules.items() if p)
    digest = hashlib.sha256(
        "".join(f"{k}:{v}" for k, v in sorted(payload.items())).encode()
    ).hexdigest()

    lines = [
        '#!/usr/bin/env python3',
        '"""attribution-portable — the attribution chain, offline, in one file.',
        '',
        'Generated by scripts/build_portable.py from the published packages.',
        'Do not edit: change the packages and rebuild, or the two diverge and',
        'the copy nobody runs is the one that gets corrected.',
        '',
        f'Modules: {len(modules)}   Source digest: {digest[:32]}',
        '',
        'Requires Python 3.11+ and nothing else. No network, no installation.',
        '',
        'Usage:',
        '    python attribution_portable.py example.com -a "ticket 123"',
        '',
        'Run it once with no saved documents to get the list of URLs to fetch.',
        '"""',
        '',
        f'__version__ = "{_suite_version()}"',
        f'SOURCE_DIGEST = "{digest}"',
        '',
        SHIM,
        '',
        f'_PACKAGES = {packages!r}',
        '',
        '_SOURCES = {',
    ]
    for name in sorted(payload):
        lines.append(f'    {name!r}: "{payload[name]}",')
    lines += [
        '}',
        '',
        LOADER,
        '',
        '_install_shims()',
        '_install_finder()',
        '',
        '',
        'def main(argv=None):',
        '    from attribution_suite.portable import main as _main',
        '    return _main(argv)',
        '',
        '',
        'if __name__ == "__main__":',
        '    raise SystemExit(main())',
        '',
    ]
    return "\n".join(lines)


def _suite_version() -> str:
    spec = importlib.util.spec_from_file_location(
        "_v", PACKAGES["attribution_suite"] / "_version.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod.__version__


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default="attribution_portable.py")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the file at --out is not what would be "
                         "built now, without writing it")
    a = ap.parse_args()

    try:
        text = build()
    except IncompleteLayout as e:
        print(e)
        return 2
    out = pathlib.Path(a.out)
    if a.check:
        if not out.exists():
            print(f"{out} does not exist")
            return 1
        if out.read_text() != text:
            print(f"{out} is stale: the packages have changed since it was "
                  f"built. Run: python {HERE.name} -o {out}")
            return 1
        print(f"{out} is in sync with the packages")
        return 0
    out.write_text(text)
    size = len(text.encode())
    print(f"wrote {out} — {size/1024:.0f} KB, "
          f"{len(iter_modules())} modules, python {sys.version_info.major}."
          f"{sys.version_info.minor}+")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
