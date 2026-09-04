# BranchRadar

BranchRadar flags two kinds of potential coordination risk between Git worktrees,
including changes that have not been committed yet:

- parallel Django migrations in the same app;
- API producer changes on one branch overlapping consumer changes on another.

These are path-level warnings, not proof of a broken migration graph or API.
Use the reported branches and files to decide what needs a joint review.

It reads Git metadata, committed diffs, and the effective files in checked-out
worktrees, including untracked files. It does not merge branches, run agents, or
build a general code graph.

If the chosen base branch is checked out and dirty, its working tree appears as a
separate candidate. Clean candidates at the resolved base commit are omitted
(including unchecked local branches), so `main`, `origin/main`, and a commit ID
omit the same clean candidates when they resolve to the same commit.

## Quick start

BranchRadar requires Python 3.11+ and Git.

```bash
git clone https://github.com/mengchar-cmu-F25/branchradar.git
cd branchradar
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
branchradar --repo /path/to/repo --base main
```

To install the fixed v0.1.2 release without cloning, create and activate a virtual
environment as above, then install its wheel:

```bash
python -m pip install https://github.com/mengchar-cmu-F25/branchradar/releases/download/v0.1.2/branchradar-0.1.2-py3-none-any.whl
```

Exit status is `1` when warnings are found, `0` when none are found, and `2` for usage,
configuration, or Git errors. **Exit `0` can also mean no branch pairs were compared.**
Check the reported scope and compared-pair count before interpreting a clean result.

By default, only checked-out worktrees are candidates; `--all-local-branches` also
includes unchecked local branches. Remote-tracking refs are not candidates, and
BranchRadar does not fetch branches or pull requests. A single-checkout CI job
usually compares zero pairs unless you explicitly prepare other local candidates.
`--format json` provides machine-readable output; it does not establish CI coverage.

## Try it before your first commit

After installation, paste this into a shell. It creates an isolated temporary Git
repository with two worktrees; the empty migration files are path-only demo inputs,
not an executable Django project.

```bash
br_demo=$(mktemp -d)
git init -b main "$br_demo/repo"
git -C "$br_demo/repo" -c user.name=Demo -c user.email=demo@example.test commit --allow-empty -m base
git -C "$br_demo/repo" worktree add -b invoice "$br_demo/invoice"
git -C "$br_demo/repo" worktree add -b payment "$br_demo/payment"
branchradar --repo "$br_demo/repo"                # No pairs compared; exit 0

mkdir -p "$br_demo/invoice/billing/migrations" "$br_demo/payment/billing/migrations"
touch "$br_demo/invoice/billing/migrations/0002_invoice.py"
touch "$br_demo/payment/billing/migrations/0002_payment.py"
branchradar --repo "$br_demo/repo"                # Risks: 1; exit 1 (expected)

rm "$br_demo/payment/billing/migrations/0002_payment.py"
branchradar --repo "$br_demo/repo"                # No pairs compared; exit 0

mkdir -p "$br_demo/payment/orders/migrations"
touch "$br_demo/payment/orders/migrations/0002_order.py"
branchradar --repo "$br_demo/repo"                # 1 pair, different apps: Risks: 0
```

Nothing is pushed, and your existing repositories are untouched. Run the commands
interactively: the expected warning exits with `1`, so a script using `set -e`
would stop there. The temporary demo remains at `$br_demo` for inspection.

## Act on a warning

- **Parallel migrations:** inspect both files and their dependencies with the
  other branch's owner. Decide the merge order and run Django's migration checks
  and project tests on the combined result. Do not delete real migrations just to
  clear a warning; the deletion above only removes a disposable demo file.
- **API overlap:** review the named producer and consumer together, then run their
  contract or integration tests. Narrow the configured paths if unrelated changes
  are routinely flagged.
- **No warnings:** if pairs were compared, no enabled path rule matched those
  pairs. If the count is zero, cross-branch risks were not assessed. Neither result
  validates migration contents, API compatibility, or overall merge safety.

Run it again after coordinating or rebasing: it reads the current state every
time. Squash-equivalent changes without shared Git ancestry can still produce
warnings; inspect the reported paths before treating a finding as actionable.

## Local state and hooks

Scanning disables Git's index auto-refresh and optional locks. Each Git call clears
inherited repository-context variables (including `GIT_DIR`, `GIT_WORK_TREE`, and
`GIT_INDEX_FILE`) so a hook uses each scanned worktree's own repository and index.
Git configuration and authentication settings are retained, including command-scope
configuration; this is not isolation from arbitrary configuration overrides such as
`core.worktree`.

The report describes the effective working-tree state, not a proposed commit.
Staged and unstaged paths are listed as dirty, but an unstaged edit can cancel a
staged change in the compared files. Running from a pre-commit hook does not validate
the staged snapshot or a temporary index used by a partial commit.

## API contract configuration

Create `.branchradar.toml` at the repository root (or pass `--config`):

```toml
[contracts.public_api]
producer = ["backend/api/**", "openapi/**"]
consumer = ["frontend/src/api/**"]
```

API overlap checks are opt-in: without a configuration file, only migration checks
run. The only top-level key is `contracts`; each contract accepts only `producer`
and `consumer`, both non-empty lists of path patterns. Unknown keys fail with exit
`2` rather than silently disabling checks.

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
The additive `scope` field is `worktrees` or `worktrees_and_local_branches`;
`compared_pairs` counts pairs actually checked, and `branches` lists the candidates
used. JSON readers should tolerate added fields. The legacy `severity: "high"`
value is retained for compatibility; it is not a calibrated severity or probability.
Text output labels findings `[REVIEW]` to emphasize that they are advisory.

## Development

```bash
python -m unittest discover -s tests -v
```

The integration tests create disposable Git repositories and worktrees with
synthetic files; they do not scan your projects or fetch external repositories.
Run only the API dirty-state, negative-control, and merge-lifecycle scenarios with
`python -m unittest discover -s tests -v -k synthetic`.

Version 0.1 intentionally uses path evidence rather than AST inference. Configure narrow
patterns for useful signal; add deeper semantic analysis only if real misses justify it.
The [research report](research/README.md) records the public-case evidence and its limits.
