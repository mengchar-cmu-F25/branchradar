# BranchRadar falsification report

Date: 2026-09-02. The initial research recommendation was to archive BranchRadar
as a small local script/guide. That recommendation has been superseded: **continue
delivery of a usable v0.1 local CLI**, with the evidence and limits below intact.

## What was tested

`cases.json` records ten retrospectively selected public migration-conflict
reports and two pull-request heads for each. At validation time, the current
GitHub PR refs must still equal the recorded SHAs. This does not establish that
the two heads were simultaneous pre-incident snapshots; the sample therefore
cannot measure warning lead time or support a "would have warned" claim.

The sources span 2018–2026 and include Mozilla Add-ons, Mozilla Treeherder,
OpenStax, Freedom of the Press Foundation, and smaller Django projects. The
script fetches those public Git objects into temporary bare repositories and
does not execute any third-party code.

Run from the repository root:

```bash
python3 research/evaluate.py
```

On Git 2.46.1 and Python 3.12.2, the 2026-09-02 run produced:

| Measure | Result |
| --- | ---: |
| Current PR refs matched recorded heads | 10/10 |
| Selected pairs mechanically clean under `git merge-tree` | 10/10 |
| Selected known parallel-conflict cases with expected subject detected | 9/9 |
| Expected app subjects detected | 9 |
| Extra app subjects reported | 2 |

This is a known-positive, conflict-enriched selection with no representative
negative sample or independent labeling. The 9/9 and two-extra-subject counts
are descriptive only; they are not precision, recall, prevalence, or product
accuracy estimates.

The extra warnings are both in OpenStax #1770. One PR head contains
squash-equivalent changes from the other PR, but the commits do not share Git
ancestry. A path-only comparison therefore calls `books` and `news` exclusive
changes even though only `pages` was the actionable fork. This is a concrete
false-positive mechanism, not a hypothetical caveat.

Digiplan #178 is a real migration-graph failure but its recorded PR heads are
stacked: the right head is the merge base of the pair. It is correctly outside
the proposed independent-branch claim, and demonstrates that BranchRadar is
not a general Django migration validator.

## Native and existing-tool boundary

- Git's official `merge-tree` command performs a commit-tree merge without
  reading either the index or working tree. It returned clean for all ten
  retained pairs. Git supplies the primitives, but no native command attaches
  Django app semantics across dirty worktrees.
- Django detects multiple leaves in the migration graph present in one
  checkout and offers `makemigrations --merge`. It does not compare two
  separate unmerged or uncommitted worktrees.
- `django-linear-migrations` deliberately writes a per-app
  `max_migration.txt`, turning parallel migrations into a normal source-control
  conflict. It is a stronger integration-time solution for teams willing to
  change their project and migration workflow.
- `django-migration-checker` and its GitHub Action statically inspect the graph
  in one checkout. Both repositories are archived; they still show that a
  standalone current-graph checker is not novel.
- GitHub's `pr-conflict-detector` compares overlapping files and line ranges
  across open PRs. It is earlier than merge, but different migration filenames
  do not overlap, so it does not cover this Django-specific case.

Primary references:

- [Git `merge-tree` documentation](https://git-scm.com/docs/git-merge-tree)
- [Django `makemigrations` documentation](https://docs.djangoproject.com/en/dev/ref/django-admin/#makemigrations)
- [django-linear-migrations](https://github.com/adamchainz/django-linear-migrations/tree/4aea86132fe88103fe989a14ba134a9696b2986f)
- [django-migration-checker](https://github.com/tonyo/django-migration-checker)
- [Django Migration Checker Action](https://github.com/hardcoretech/django-migration-checker-action/tree/e918d9914bedba3c33eaffda00045a98c3e438cc)
- [GitHub PR Conflict Detector](https://github.com/github-community-projects/pr-conflict-detector/tree/5ad41837188f5f227858d6f3d346cc3965735bfa)

`cases.json` contains only public identifiers, factual labels, URLs, and commit
hashes. It does not copy or redistribute third-party source code or issue prose,
so third-party repository licenses are not incorporated into this repository.

## Initial research decision

The narrow detector recovered the expected app subject in all nine selected
known parallel-conflict cases while `merge-tree` returned clean for the retained
pairs. That is enough to keep a small local utility or publish the method as a
guide, but not to claim real-world accuracy or earlier warning.

It is not enough to justify a standalone product:

- no evidence yet shows that the affected teams use multiple local worktrees
  or want a pre-commit scanner;
- the unique implementation is a thin composition of `git worktree list`,
  `git diff`, and a migration-path match;
- established alternatives already cover current-checkout, integration-time,
  and remote-PR workflows;
- squash merges create non-actionable warnings unless the tool grows patch-ID
  or graph semantics, which would violate the deliberately tiny scope.

The initial recommendation was to stop product work until three independent
teams reported a monthly problem and one supplied a real dirty multi-worktree
session where the warning changed coordination before a PR. This was a demand
threshold, not a technical obstacle to shipping the existing local utility.

## Current delivery decision

Continue toward a publicly usable v0.1: an installable CLI, a short reproducible
demo, actionable file evidence, and tests of the actual dirty-worktree workflow.
The integration test now drives real temporary Git worktrees through no warning,
uncommitted same-app migration warning, removal of one side, and a different-app
negative control. This proves the local workflow, not real-world adoption or
pre-incident performance on the public cases above.

The scope remains the existing migration-path and configured API-path checks.
No server, graph inference, or hosted integration is needed for this release.
Field trials will guide later improvements; missing demand evidence does not
prevent users from trying the working CLI.
