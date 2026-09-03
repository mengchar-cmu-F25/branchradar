# BranchRadar

BranchRadar flags two kinds of potential coordination risk between Git worktrees,
including changes that have not been committed yet:

- parallel Django migrations in the same app;
- API producer changes on one branch overlapping consumer changes on another.

These are path-level warnings, not proof of a broken migration graph or API.
Use the reported branches and files to decide what needs a joint review.

It reads Git metadata, committed diffs, and staged, unstaged, and untracked paths in
checked-out worktrees. It does not merge branches, run agents, or build a general code
graph.

If the chosen base branch is checked out and dirty, its working tree appears as a
separate candidate; a clean base is omitted.

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

Exit status is `1` when risks are found, `0` when none are found, and `2` for usage or Git
errors. Add `--format json` for CI output. By default, only checked-out worktrees are
analyzed; `--all-local-branches` includes other local branches.

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
branchradar --repo "$br_demo/repo"                # Risks: 0; exit 0

mkdir -p "$br_demo/invoice/billing/migrations" "$br_demo/payment/billing/migrations"
touch "$br_demo/invoice/billing/migrations/0002_invoice.py"
touch "$br_demo/payment/billing/migrations/0002_payment.py"
branchradar --repo "$br_demo/repo"                # Risks: 1; exit 1 (expected)

rm "$br_demo/payment/billing/migrations/0002_payment.py"
branchradar --repo "$br_demo/repo"                # Risks: 0; exit 0

mkdir -p "$br_demo/payment/orders/migrations"
touch "$br_demo/payment/orders/migrations/0002_order.py"
branchradar --repo "$br_demo/repo"                # Different apps: Risks: 0
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
- **No warnings:** no configured path overlap was found. BranchRadar does not
  validate migration contents, API compatibility, or overall merge safety.

Run it again after coordinating or rebasing: it reads the current state every
time. Squash-equivalent changes without shared Git ancestry can still produce
warnings; inspect the reported paths before treating a finding as actionable.

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
The [research report](research/README.md) records the public-case evidence and its limits.
