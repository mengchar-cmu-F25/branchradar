# BranchRadar

BranchRadar finds two high-signal risks between Git worktrees before merge:

- parallel Django migrations in the same app;
- API producer changes on one branch overlapping consumer changes on another.

It reads Git metadata, committed diffs, and staged, unstaged, and untracked paths in
checked-out worktrees. It does not merge branches, run agents, or build a general code
graph.

If the chosen base branch is checked out and dirty, its working tree appears as a
separate candidate; a clean base is omitted.

## Quick start

BranchRadar requires Python 3.11+ and Git.

```bash
python -m pip install -e .
branchradar --repo /path/to/repo --base main
```

Exit status is `1` when risks are found, `0` when clean, and `2` for usage or Git
errors. Add `--format json` for CI output. By default, only checked-out worktrees are
analyzed; `--all-local-branches` includes other local branches.

## API contract configuration

Create `.branchradar.toml` at the repository root (or pass `--config`):

```toml
[contracts.public_api]
producer = ["backend/api/**", "openapi/**"]
consumer = ["frontend/src/api/**"]
```

Patterns are case-sensitive and anchored to repository-relative `/` paths. `*` and `?`
never cross `/`; `**` does, and `**/` also matches zero directory levels. All other
characters are literal.
For example, `backend/*/schema.py` matches one directory level while
`backend/**/schema.py` matches any number. A leading `./` is ignored.

A risk is reported when changes exclusive to one branch after the pair's mutual merge
base match a contract's `producer` patterns and the other branch's exclusive changes
match its `consumer` patterns. This avoids treating stacked or shared history as two
independent changes. The JSON report includes the exact paths and merge base behind
every finding.

Renames are represented as a deleted path plus an added path. BranchRadar always
disables Git rename detection so similarity settings cannot change the report.

JSON reports use `schema_version = 2`. Branches referenced by risks and evidence are
`{"id": ..., "name": ...}` objects so detached worktrees and repeated display names
cannot overwrite each other.

## Development

```bash
python -m unittest discover -s tests -v
```

Version 0.1 intentionally uses path evidence rather than AST inference. Configure narrow
patterns for useful signal; add deeper semantic analysis only if real misses justify it.
