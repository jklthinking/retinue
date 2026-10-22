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

## Contribution license / 贡献许可与 CLA

The project uses AGPL-3.0-only with separate commercial licensing. Before a
contribution is merged, read and sign [CLA v1](CLA.md): the individual form
for your own rights, and the corporate form when an employer or organization
holds the rights. You retain copyright and expressly permit sublicensing,
including commercial terms, and grant the stated patent license. Identify
third-party material and preserve its notices; do not grant rights you lack.

To sign, check “I have read and agree to CLA.md (individual or corporate
agreement)” in your pull request. Checking the box records your agreement to
CLA v1. Corporate contributors must also separately sign the corporate
agreement through a maintainer-designated private channel; maintainers verify
corporate authority before merging. No CLA bot is used. Do not put private
identity or contact details in a public PR. Existing contributions are not
signed retroactively by this file.

合并前须签署 [CLA v1](CLA.md)（个人版；机构贡献者另行签署机构版）。
贡献者保留版权，并同意维护者以包括商业授权在内的其他条款再许可，另授予专利
许可。如何签署：在 PR 中勾选「我已阅读并同意 CLA.md（个人或机构版）」即视为
同意 CLA v1；机构贡献者另行通过维护者指定的非公开渠道签署机构版，维护者在
合并前核验机构权限。不使用 CLA 机器人。第三方材料须注明来源与许可，不得
无权授权，也不在公开 PR 填写私人身份或联系方式。

## Reporting a security issue

See [SECURITY.md](SECURITY.md). Please do not open a public issue for one.
