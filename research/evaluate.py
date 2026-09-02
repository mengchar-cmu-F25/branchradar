"""Replay the public PR pairs documented in cases.json.

The script only fetches public Git objects into temporary bare repositories. It
does not execute third-party project code or change any checkout.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import branchradar  # noqa: E402


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def pair_subjects(report: dict, left: str, right: str) -> set[str]:
    names = {left, right}
    return {
        risk["subject"]
        for risk in report["risks"]
        if risk["kind"] == "parallel_django_migrations"
        and {branch["name"] for branch in risk["branches"]} == names
    }


def evaluate(case: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="branchradar-public-") as directory:
        repo = Path(directory) / "repo.git"
        subprocess.run(
            ["git", "init", "--bare", str(repo)],
            check=True,
            capture_output=True,
            text=True,
        )
        remote = f"https://github.com/{case['repository']}.git"
        git(
            repo,
            "fetch",
            "--filter=blob:none",
            "--no-tags",
            remote,
            f"refs/pull/{case['left']['pr']}/head:refs/heads/left",
            f"refs/pull/{case['right']['pr']}/head:refs/heads/right",
        )

        left = git(repo, "rev-parse", "left").stdout.strip()
        right = git(repo, "rev-parse", "right").stdout.strip()
        if left != case["left"]["sha"] or right != case["right"]["sha"]:
            raise RuntimeError(f"{case['id']}: fetched PR head does not match pinned SHA")

        merge_base = git(repo, "merge-base", "left", "right").stdout.strip()
        git(repo, "update-ref", "refs/heads/base", merge_base)

        native_merge = git(repo, "merge-tree", "--write-tree", "left", "right", check=False)
        if native_merge.returncode not in (0, 1):
            raise RuntimeError(
                f"{case['id']}: git merge-tree failed: {native_merge.stderr.strip()}"
            )

        report = branchradar.scan(repo, base="base", include_all_branches=True)
        found = pair_subjects(report, "left", "right")
        expected = set(case["expected_actionable_subjects"])

        return {
            "id": case["id"],
            "classification": case["classification"],
            "heads_verified": True,
            "merge_base": merge_base,
            "git_merge_tree_clean": native_merge.returncode == 0,
            "expected_subjects": sorted(expected),
            "found_subjects": sorted(found),
            "true_positive_subjects": sorted(found & expected),
            "false_positive_subjects": sorted(found - expected),
            "false_negative_subjects": sorted(expected - found),
        }


def main() -> int:
    document = json.loads((Path(__file__).with_name("cases.json")).read_text())
    results = [evaluate(case) for case in document["cases"]]
    in_scope = [
        result
        for result in results
        if result["classification"] == "parallel_pr_migration_conflict"
    ]
    true_positives = sum(len(result["true_positive_subjects"]) for result in in_scope)
    false_positives = sum(len(result["false_positive_subjects"]) for result in results)
    false_negatives = sum(len(result["false_negative_subjects"]) for result in in_scope)

    output = {
        "schema_version": 1,
        "summary": {
            "retained_pr_heads_verified": sum(
                result["heads_verified"] for result in results
            ),
            "selected_event_pairs": len(results),
            "native_git_clean_selected_pairs": sum(
                result["git_merge_tree_clean"] for result in results
            ),
            "selected_known_parallel_events": len(in_scope),
            "selected_known_parallel_events_detected": sum(
                bool(result["true_positive_subjects"]) for result in in_scope
            ),
            "expected_subjects_detected": true_positives,
            "extra_subjects": false_positives,
            "expected_subjects_missed": false_negatives,
        },
        "limitations": [
            (
                "The cases were selected from known migration-conflict reports; they "
                "cannot estimate precision, recall, prevalence, or warning lead time."
            ),
            (
                "The retained pull-request heads are verified against their current "
                "GitHub refs, not established as simultaneous pre-incident snapshots."
            ),
            (
                "Public Git history cannot preserve the pre-commit dirty-worktree "
                "state that is BranchRadar's narrowest claimed advantage."
            ),
        ],
        "results": results,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
