# BranchRadar

BranchRadar finds two high-signal risks between Git worktrees before merge:

- parallel Django migrations in the same app;
- API producer changes on one branch overlapping consumer changes on another.

It only reads Git metadata and diffs. It does not merge branches, run agents, or build a
general code graph.

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

Paths are repository-relative shell-style patterns. A risk is reported when one branch
matches a contract's `producer` patterns and another matches its `consumer` patterns.
The JSON report includes the exact paths behind every finding.

## Development

```bash
python -m unittest discover -s tests -v
```

Version 0.1 intentionally uses path evidence rather than AST inference. Configure narrow
patterns for useful signal; add deeper semantic analysis only if real misses justify it.
