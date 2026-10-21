#!/usr/bin/env python3
"""Fail if real-world case data is present in the source tree.

Twice now, identifiers belonging to real third parties reached public files by
the same route: a live run was pasted into a README, a changelog entry or a
test fixture because it was convenient and realistic. Nothing caught it. A
reviewer reading an OSINT toolkit's README should not learn the name of a
company that happened to be investigated with it.

Two independent checks, because they fail differently:

1. STRUCTURAL -- identifier classes that denote one specific account or legal
   entity (an AdSense publisher id, an LEI, a UA property, a CIK) must match a
   placeholder form. This catches values nobody has seen before.

2. DENYLIST -- the specific values removed in the clean-up must never come
   back. Stored as truncated SHA-256 over the normalised token, never as
   plaintext: a denylist written out in full would republish exactly what it
   exists to keep out. This catches a re-paste of the same case.

Exit code 0 when clean, 1 with a report otherwise. Run over one or more repo
roots:

    python3 scripts/scan_for_real_world_data.py attribution-graph paytrace ...
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import sys

# ---- what counts as a placeholder ---------------------------------------- #

PLACEHOLDER = {
    "adsense": re.compile(r"^(?:ca-)?pub-(?:0+\d{0,4}|1234567890123456"
                          r"|9000000000000001|4417\d+)$"),
    "lei": re.compile(r"^EXAMPLELEI\d{10}$", re.I),
    "ua": re.compile(r"^UA-(?:0+|1234\d*)$", re.I),
}

STRUCTURAL = {
    "AdSense publisher id": (re.compile(r"\b(?:ca-)?pub-\d{10,}\b"), "adsense"),
    "LEI": (re.compile(r"\b[0-9A-Z]{18}\d{2}\b"), "lei"),
    "UA analytics id": (re.compile(r"\bUA-\d{5,}\b"), "ua"),
}

#: Truncated SHA-256 of each normalised value removed from this tree. See the
#: module docstring for why these are hashes.
DENIED = {
    "031881ff51e3707a", "0e0351b8c7f0e467", "138d96192c2444ef",
    "1997dbb2d9d235b2", "1bc1c04560d985c0", "232d6ec37612aba9",
    "288448935cec875c", "2dbf8848186cc1bb", "40e42cd442185cae",
    "488bd7ff5d840f63", "4ef737d94a6b9223", "61639015ecd92474",
    "6a7b17b57e6a1aaf", "6f393ba5c418a095", "6f4f7ac320c978dd",
    "7024826f5c869330", "70791c014e306795", "89e05e6ec8ed1483",
    "9a61cd82ee9664d0", "a1e293bd0e42180e", "a4b017c08a9b06e6",
    "ac0a31d27f1f7826", "b02874a94f7a4705", "b048addbb00f24b0",
    "b7a23f852d4b9bcc", "c1ae65993db7be39", "d63c63d1f3ef7b6d",
    "df03141ca407a0b6", "df22a34300779bc1", "e924a635917f52c3",
    "f5957e49a94bf19c", "f8c4940494710276", "fa10d09c4aba4d6d",
    "fad6cf76c055d579",
}

#: A value nobody would ship, present so the scanner can prove it still fires.
CANARY = "canary-real-world-value-do-not-ship"

SKIP_DIR = {".git", ".venv", "__pycache__", ".pytest_cache", "dist", "build",
            ".verify-venv", ".ruff_cache", "node_modules", "signatures"}
EXT = {".md", ".py", ".txt", ".yaml", ".yml", ".toml", ".cff", ".csv", ".json",
       ".sh", ".cypher", ".jsonl", ".html"}

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+-]*")


def fingerprint(value: str) -> str:
    """Normalise and hash one candidate value."""
    norm = value.strip().strip(".,;:!?\"'()[]{}<>").lower()
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def candidates(line: str) -> set[str]:
    """Every token and short word sequence a denied value could appear as.

    Values are matched as tokens and as two- and three-word phrases, because
    a company name is several words and an identifier is one.
    """
    words = _WORD.findall(line)
    out = set(words)
    for n in (2, 3):
        for i in range(len(words) - n + 1):
            out.add(" ".join(words[i:i + n]))
    return out


def files(root: pathlib.Path):
    if root.is_file():
        yield root
        return
    for f in root.rglob("*"):
        if f.is_file() and f.suffix in EXT and not (set(f.parts) & SKIP_DIR):
            yield f


def scan(roots: list[str]) -> list[tuple[str, str, str, str]]:
    """Return (check, value, location, line) for every finding."""
    bad: list[tuple[str, str, str, str]] = []
    me = pathlib.Path(__file__).resolve()
    for root in roots:
        for f in files(pathlib.Path(root)):
            if f.resolve() == me:
                continue            # this file carries the patterns by design
            try:
                text = f.read_text(errors="ignore")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                where = f"{f}:{n}"
                for label, (pat, kind) in STRUCTURAL.items():
                    for m in pat.finditer(line):
                        if not PLACEHOLDER[kind].match(m.group(0)):
                            bad.append((label, m.group(0), where,
                                        line.strip()[:90]))
                for cand in candidates(line):
                    if fingerprint(cand) in DENIED:
                        bad.append(("removed real-world value", cand, where,
                                    line.strip()[:90]))
    return bad


def main(argv: list[str]) -> int:
    roots = argv[1:] or ["."]
    bad = scan(roots)
    if not bad:
        print("clean: no real-world case data found")
        return 0
    print("real-world data must not ship in a public repository:\n")
    for check, value, where, line in sorted(set(bad)):
        print(f"  [{check}] {value}")
        print(f"      {where}\n      {line}")
    print(f"\n{len(set(bad))} finding(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
