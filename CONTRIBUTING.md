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

## Contributor License Agreement (brief)

By submitting a contribution you grant JKL Thinking a perpetual, worldwide,
non-exclusive, royalty-free license to use, modify, and redistribute that
contribution, and to relicense it — including under FSL-1.1-Apache-2.0 and
under the Apache License, Version 2.0 when a version converts. You confirm
you have the right to grant this.

The full CLA text and the automated pull-request check take effect with the
first external pull request. Until that check is published, sign by doing
both of the following:

1. Tick the CLA box on the pull-request template.
2. Include a trailer on the commit:

   `Signed-off-by: Your Name`

   Use the name you contribute under. Do not put an email address in the
   commit or the pull request if this repository's identifier scan would
   treat it as a live identifier; the published CLA check will collect the
   signing identity when it lands.

Placeholder: the CLA document URL and bot name will be written here when the
public repository is opened.

## Reporting a security issue

See [SECURITY.md](SECURITY.md). Please do not open a public issue for one.
