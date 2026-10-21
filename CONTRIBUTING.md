# Contributing

This package is thin by design: it wires the three component packages together
and adds a CLI. It should stay that way.

**Logic belongs in the component packages.** A PR adding scoring, collectors or
signal analysis here will be redirected to `attribution-graph`,
`paytrace` or `handle-correlation` respectively. What belongs here is
orchestration, argument plumbing, and anything that genuinely needs all three at
once.

The one substantive rule to preserve: **run ordering**. Imported claims seed the
frontier before collection, adversarial checks run before scoring, and evidence
verification runs before reporting. Each of those orderings prevents a specific
failure — an unchecked planted identifier reaching the model at full weight, or
findings being presented from a package whose integrity check failed. Changing
the order needs an argument, not just passing tests.

```bash
python3 -m venv .venv && . .venv/bin/activate   # PEP 668: not optional on
pip install -e ".[dev]"                        # Ubuntu, Debian, Fedora, Homebrew
pytest -q && ruff check .
```

The four packages develop together. Install all of them editable into the one
virtualenv, or a released copy of a sibling shadows your working tree and tests
pass against code you are not editing:

```bash
for r in attribution-graph paytrace handle-correlation attribution-suite; do
  pip install -e "../$r"
done
```
