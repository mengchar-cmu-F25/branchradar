"""Find high-signal semantic risks between in-flight Git branches."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


MIGRATION_RE = re.compile(r"^(?P<app>.+)/migrations/(?P<file>[0-9][^/]*)\.py$")


class BranchRadarError(RuntimeError):
    """An actionable error that can be shown directly to the user."""


@dataclass(frozen=True)
class Candidate:
    name: str
    ref: str
    commit: str
    worktree: str | None


@dataclass(frozen=True)
class Contract:
    name: str
    producer: tuple[str, ...]
    consumer: tuple[str, ...]


def _git(repo: Path, *args: str) -> str:
    command = ["git", "-C", str(repo), *args]
    try:
        result = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise BranchRadarError("git is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or "git command failed"
        raise BranchRadarError(message) from exc
    return result.stdout


def _resolve_commit(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def _base_full_ref(repo: Path, base: str) -> str | None:
    value = _git(repo, "rev-parse", "--symbolic-full-name", base).strip()
    return value if value.startswith("refs/") else None


def _worktrees(repo: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in _git(repo, "worktree", "list", "--porcelain").splitlines():
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        records.append(current)
    return records


def enumerate_candidates(
    repo: Path, base: str, include_all_branches: bool = False
) -> list[Candidate]:
    """Return deterministic local branch/detached candidates and their worktrees."""
    base_ref = _base_full_ref(repo, base)
    candidates: dict[str, Candidate] = {}

    for worktree in _worktrees(repo):
        commit = worktree.get("HEAD")
        path = worktree.get("worktree")
        if not commit or not path:
            continue
        ref = worktree.get("branch")
        if ref:
            if ref == base_ref:
                continue
            name = ref.removeprefix("refs/heads/")
        else:
            ref = commit
            name = f"detached-{commit[:12]}"
        candidates[ref] = Candidate(name, ref, commit, path)

    if include_all_branches:
        output = _git(
            repo,
            "for-each-ref",
            "--format=%(refname)\t%(objectname)",
            "refs/heads",
        )
        for line in output.splitlines():
            ref, commit = line.split("\t", 1)
            if ref == base_ref or ref in candidates:
                continue
            name = ref.removeprefix("refs/heads/")
            candidates[ref] = Candidate(name, ref, commit, None)

    return sorted(candidates.values(), key=lambda item: (item.name, item.ref))


def _changed_paths(repo: Path, base: str, ref: str) -> list[str]:
    output = _git(
        repo,
        "diff",
        "--name-only",
        "-z",
        "--no-ext-diff",
        f"{base}...{ref}",
    )
    return sorted(path for path in output.split("\0") if path)


def load_contracts(path: Path | None) -> list[Contract]:
    if path is None:
        return []
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BranchRadarError(f"cannot read config {path}: {exc}") from exc

    raw_contracts = document.get("contracts", {})
    if not isinstance(raw_contracts, dict):
        raise BranchRadarError("config 'contracts' must be a table")

    contracts: list[Contract] = []
    for name, value in sorted(raw_contracts.items()):
        if not isinstance(value, dict):
            raise BranchRadarError(f"contract '{name}' must be a table")
        producer = value.get("producer")
        consumer = value.get("consumer")
        if not _string_list(producer) or not _string_list(consumer):
            raise BranchRadarError(
                f"contract '{name}' needs non-empty producer and consumer string lists"
            )
        contracts.append(Contract(name, tuple(producer), tuple(consumer)))
    return contracts


def _string_list(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and item for item in value
    )


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    normalized = (pattern[2:] if pattern.startswith("./") else pattern for pattern in patterns)
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in normalized)


def _footprint(paths: list[str], contracts: list[Contract]) -> dict[str, Any]:
    migrations: dict[str, list[str]] = {}
    for path in paths:
        match = MIGRATION_RE.match(path)
        if match:
            migrations.setdefault(match.group("app"), []).append(path)

    contract_hits: dict[str, dict[str, list[str]]] = {}
    for contract in contracts:
        producer = [path for path in paths if _matches(path, contract.producer)]
        consumer = [path for path in paths if _matches(path, contract.consumer)]
        if producer or consumer:
            contract_hits[contract.name] = {
                "producer": producer,
                "consumer": consumer,
            }

    return {
        "changed_paths": paths,
        "django_migrations": migrations,
        "contracts": contract_hits,
    }


def _migration_risks(
    left: dict[str, Any], right: dict[str, Any]
) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    left_apps = left["footprint"]["django_migrations"]
    right_apps = right["footprint"]["django_migrations"]
    for app in sorted(set(left_apps) & set(right_apps)):
        risks.append(
            {
                "kind": "parallel_django_migrations",
                "severity": "high",
                "branches": [left["name"], right["name"]],
                "subject": app,
                "reason": f"both branches change Django migrations in '{app}'",
                "evidence": {
                    left["name"]: left_apps[app],
                    right["name"]: right_apps[app],
                },
            }
        )
    return risks


def _contract_risks(
    left: dict[str, Any], right: dict[str, Any]
) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    left_hits = left["footprint"]["contracts"]
    right_hits = right["footprint"]["contracts"]
    for name in sorted(set(left_hits) & set(right_hits)):
        evidence: list[dict[str, Any]] = []
        if left_hits[name]["producer"] and right_hits[name]["consumer"]:
            evidence.append(
                {
                    "producer_branch": left["name"],
                    "producer_paths": left_hits[name]["producer"],
                    "consumer_branch": right["name"],
                    "consumer_paths": right_hits[name]["consumer"],
                }
            )
        if right_hits[name]["producer"] and left_hits[name]["consumer"]:
            evidence.append(
                {
                    "producer_branch": right["name"],
                    "producer_paths": right_hits[name]["producer"],
                    "consumer_branch": left["name"],
                    "consumer_paths": left_hits[name]["consumer"],
                }
            )
        if evidence:
            risks.append(
                {
                    "kind": "api_contract_overlap",
                    "severity": "high",
                    "branches": [left["name"], right["name"]],
                    "subject": name,
                    "reason": (
                        f"one branch changes '{name}' producers while the other "
                        "changes its consumers"
                    ),
                    "evidence": evidence,
                }
            )
    return risks


def scan(
    repo: Path,
    base: str = "main",
    config: Path | None = None,
    include_all_branches: bool = False,
) -> dict[str, Any]:
    """Analyze in-flight branches and return a JSON-serializable report."""
    repo = repo.resolve()
    _git(repo, "rev-parse", "--git-dir")
    base_commit = _resolve_commit(repo, base)
    if config is None:
        default_config = repo / ".branchradar.toml"
        config = default_config if default_config.is_file() else None
    contracts = load_contracts(config)

    branches: list[dict[str, Any]] = []
    for candidate in enumerate_candidates(repo, base, include_all_branches):
        paths = _changed_paths(repo, base_commit, candidate.commit)
        branches.append(
            {
                "name": candidate.name,
                "ref": candidate.ref,
                "commit": candidate.commit,
                "worktree": candidate.worktree,
                "footprint": _footprint(paths, contracts),
            }
        )

    risks: list[dict[str, Any]] = []
    for index, left in enumerate(branches):
        for right in branches[index + 1 :]:
            risks.extend(_migration_risks(left, right))
            risks.extend(_contract_risks(left, right))
    risks.sort(key=lambda risk: (risk["branches"], risk["kind"], risk["subject"]))

    return {
        "schema_version": 1,
        "repository": str(repo),
        "base": {"ref": base, "commit": base_commit},
        "branches": branches,
        "risks": risks,
    }


def render_text(report: dict[str, Any]) -> str:
    base = report["base"]
    lines = [
        f"BranchRadar: {len(report['branches'])} branches against "
        f"{base['ref']} ({base['commit'][:12]})"
    ]
    for branch in report["branches"]:
        footprint = branch["footprint"]
        labels: list[str] = []
        migrations = footprint["django_migrations"]
        if migrations:
            labels.append("migrations=" + ",".join(migrations))
        for name, hits in footprint["contracts"].items():
            roles = [role for role in ("producer", "consumer") if hits[role]]
            labels.append(f"{name}=" + "+".join(roles))
        details = f"; {'; '.join(labels)}" if labels else ""
        location = f" @ {branch['worktree']}" if branch["worktree"] else ""
        lines.append(
            f"- {branch['name']} ({len(footprint['changed_paths'])} files){location}{details}"
        )

    lines.append(f"Risks: {len(report['risks'])}")
    for risk in report["risks"]:
        left, right = risk["branches"]
        lines.append(
            f"- [{risk['severity'].upper()}] {risk['kind']}: {left} <-> {right}: "
            f"{risk['reason']}"
        )
        if risk["kind"] == "parallel_django_migrations":
            for branch in risk["branches"]:
                lines.append(f"  {branch}: {', '.join(risk['evidence'][branch])}")
        else:
            for edge in risk["evidence"]:
                lines.append(
                    f"  {edge['producer_branch']} producer: "
                    f"{', '.join(edge['producer_paths'])}"
                )
                lines.append(
                    f"  {edge['consumer_branch']} consumer: "
                    f"{', '.join(edge['consumer_paths'])}"
                )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="branchradar",
        description="Find semantic risks between in-flight Git branches.",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", default="main")
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--all-local-branches",
        action="store_true",
        help="also scan local branches that are not checked out in a worktree",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = scan(args.repo, args.base, args.config, args.all_local_branches)
    except BranchRadarError as exc:
        print(f"branchradar: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 1 if report["risks"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
