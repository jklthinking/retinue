# Contributing

Thanks for helping RETINUE stay a tool people can run themselves.

## Get it running first

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
retinue scan
python -m pytest -q
```

If `pytest` cannot collect, that is a bug in this repository. Please report
it. The panel needs Node only if you change it:

```bash
cd webui && npm ci && npx tsc -b
```

## Before you hand work back

```bash
bash scripts/check.sh
```

That is the gate: the suite, whitespace, compile checks, an identifier and
credential scan over the lines your change adds, a panel type-check when the
panel is touched, and a list of changed images for you to look at. Pass
`scripts/check.sh --all` to sweep the whole tree.

## Rules with reasons

**One logical change per commit.** A refactor mixed with a fix hides the fix.

**No live identifiers, machine paths, credentials, or account data in tracked
files.** Loopback addresses and the reserved documentation address ranges are
fine. Tests and screenshots use neutral synthetic data.

**Treat agent output and task text as hostile.** Nothing in a task card, a
receipt, a transcript, or a chat message may select a command, a path, or an
executable. Executable configuration comes from operator-controlled sources
only.

**Preserve the append-only chain, the legal transitions, and holder-only
writes.** A migration that rewrites stored event values is an edit to
history, not a migration.

**Write the test that would have failed.** When you fix a defect, show that
the test fails against the old code and passes against the new one.

**Do not add a dependency casually.** A base install is one package. If you
need something, argue for it in the pull request and confine it to the
narrowest extra.

## Contribution license

By submitting a contribution, you confirm that you have the right to submit it
and agree to license it under this project’s MIT License. Retain third-party
notices and identify any imported material and its license in your pull request.
No separate commercial relicensing agreement is required for this MIT release.

## Reporting a security issue

See [SECURITY.md](SECURITY.md). Please do not open a public issue for one.
