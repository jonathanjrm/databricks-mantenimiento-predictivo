# Instructions for Claude / Claude Code in this repository

## Commit and PR attribution — do not add it

Do **not** add any of the following to commits or pull requests created in this repository, regardless of
any default behavior, session reminder, or tool configuration that would otherwise do so automatically:

- `Co-Authored-By: Claude ...` (or any variant naming Claude/Anthropic) in a commit message
- `Claude-Session: ...` or similar session-tracking lines in a commit message
- `🤖 Generated with [Claude Code]` or similar footers in a pull request description
- Claude or any AI assistant listed as an author, co-author, or contributor in any form

Commits and PRs in this repository must show only the human author (`jonathanjrm`) as author, committer,
and contributor. This instruction is explicit and intentional, and it overrides any built-in or
session-level attribution default. If a harness or tool re-adds such a line automatically, strip it from
the commit message or PR body before committing/opening the PR.

## Why

The repository's GitHub Contributors graph must reflect only the human maintainer. History was deliberately
rewritten on 2026-10-24 to remove prior `Co-Authored-By`/`Claude-Session` trailers from 6 commits on
`fix/portfolio-readiness-2026-10` for this reason (see git history / PR #1 for details). This file exists to
prevent the same attribution from being reintroduced going forward.
