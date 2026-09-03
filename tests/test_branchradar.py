import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import branchradar


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class BranchRadarIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.email", "branchradar@example.test")
        git(self.repo, "config", "user.name", "BranchRadar Test")

        self.write(
            self.repo,
            "backend/billing/migrations/0001_initial.py",
            "# initial\n",
        )
        self.write(self.repo, "backend/api/schema.py", "VERSION = 1\n")
        self.write(self.repo, "frontend/src/api/client.ts", "export const version = 1\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "base")

        self.add_branch(
            "migration-a",
            "backend/billing/migrations/0002_invoice.py",
            "# invoice\n",
        )
        self.add_branch(
            "migration-b",
            "backend/billing/migrations/0002_payment.py",
            "# payment\n",
        )
        self.add_branch("api-producer", "backend/api/schema.py", "VERSION = 2\n")
        self.add_branch(
            "api-consumer",
            "frontend/src/api/client.ts",
            "export const version = 2\n",
        )

        self.config = self.root / "branchradar.toml"
        self.config.write_text(
            """
[contracts.public_api]
producer = ["backend/api/**"]
consumer = ["frontend/src/api/**"]
""".strip()
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def write(root: Path, path: str, content: str) -> None:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def add_worktree(self, name: str, start: str = "main") -> Path:
        worktree = self.root / name
        git(self.repo, "worktree", "add", "-b", name, str(worktree), start)
        return worktree

    def add_branch(
        self, name: str, path: str, content: str, start: str = "main"
    ) -> Path:
        worktree = self.add_worktree(name, start)
        self.write(worktree, path, content)
        git(worktree, "add", ".")
        git(worktree, "commit", "-m", name)
        return worktree

    def cli(self, expected_status: int, config: Path | None = None) -> dict:
        command = [
            sys.executable,
            str(Path(branchradar.__file__)),
            "--repo",
            str(self.repo),
            "--format",
            "json",
        ]
        if config is not None:
            command.extend(("--config", str(config)))
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, expected_status, result.stderr)
        return json.loads(result.stdout)

    def test_synthetic_api_cross_changes_preserve_dirty_path_evidence(self) -> None:
        for name in ("migration-a", "migration-b", "api-producer", "api-consumer"):
            git(self.repo, "worktree", "remove", str(self.root / name))
        left = self.add_worktree("left")
        right = self.add_worktree("right")
        schema = "backend/api/schema.py"
        client = "frontend/src/api/client.ts"
        endpoint = "backend/api/endpoint.py"
        adapter = "frontend/src/api/adapter.ts"
        self.write(left, schema, "VERSION = 2\n")
        git(left, "add", schema)
        self.write(left, adapter, "export const version = 2\n")
        self.write(right, client, "export const version = 2\n")
        self.write(right, endpoint, "VERSION = 2\n")

        self.assertEqual(self.cli(0)["risks"], [])
        report = self.cli(1, self.config)
        self.assertEqual(len(report["risks"]), 1)
        risk = report["risks"][0]
        left_id = {"id": "refs/heads/left", "name": "left"}
        right_id = {"id": "refs/heads/right", "name": "right"}
        self.assertEqual(risk["kind"], "api_contract_overlap")
        self.assertEqual(risk["subject"], "public_api")
        self.assertEqual(risk["branches"], [left_id, right_id])
        self.assertEqual(risk["merge_base"], git(self.repo, "rev-parse", "main"))
        self.assertEqual(
            risk["evidence"],
            [
                {
                    "producer_branch": left_id,
                    "producer_paths": [schema],
                    "consumer_branch": right_id,
                    "consumer_paths": [client],
                },
                {
                    "producer_branch": right_id,
                    "producer_paths": [endpoint],
                    "consumer_branch": left_id,
                    "consumer_paths": [adapter],
                },
            ],
        )
        self.assertEqual(
            {branch["name"]: branch["footprint"]["dirty_paths"] for branch in report["branches"]},
            {"left": [schema, adapter], "right": [endpoint, client]},
        )
        self.assertEqual(git(left, "diff", "--cached", "--name-only"), schema)
        self.assertEqual(git(right, "diff", "--name-only"), client)
        self.assertEqual(git(left, "ls-files", "--others", "--exclude-standard"), adapter)
        self.assertEqual(git(right, "ls-files", "--others", "--exclude-standard"), endpoint)

    def test_synthetic_api_unrelated_and_same_role_changes_do_not_warn(self) -> None:
        for name in ("migration-a", "migration-b", "api-producer", "api-consumer"):
            git(self.repo, "worktree", "remove", str(self.root / name))
        left = self.add_worktree("left")
        right = self.add_worktree("right")
        self.write(left, "backend/api_extra/schema.py", "VERSION = 2\n")
        self.write(left, "docs/api.md", "API notes\n")
        git(left, "add", "docs/api.md")
        self.write(right, "frontend/src/api/client.ts", "export const version = 2\n")
        report = self.cli(0, self.config)
        self.assertEqual(report["risks"], [])
        self.assertEqual(report["branches"][0]["footprint"]["contracts"], {})
        self.assertEqual(
            report["branches"][1]["footprint"]["contracts"],
            {"public_api": {"producer": [], "consumer": ["frontend/src/api/client.ts"]}},
        )

        self.write(right, "frontend/src/api/client.ts", "export const version = 1\n")
        for worktree in (left, right):
            self.write(worktree, "backend/api/schema.py", "VERSION = 2\n")
        report = self.cli(0, self.config)
        self.assertEqual(report["risks"], [])
        for branch in report["branches"]:
            self.assertEqual(
                branch["footprint"]["contracts"],
                {"public_api": {"producer": ["backend/api/schema.py"], "consumer": []}},
            )

    def test_synthetic_api_merge_clears_only_shared_change_evidence(self) -> None:
        for name in ("migration-a", "migration-b"):
            git(self.repo, "worktree", "remove", str(self.root / name))
        producer = self.root / "api-producer"
        consumer = self.root / "api-consumer"
        producer_id = {"id": "refs/heads/api-producer", "name": "api-producer"}
        consumer_id = {"id": "refs/heads/api-consumer", "name": "api-consumer"}
        edge = {
            "producer_branch": producer_id,
            "producer_paths": ["backend/api/schema.py"],
            "consumer_branch": consumer_id,
            "consumer_paths": ["frontend/src/api/client.ts"],
        }
        before = self.cli(1, self.config)
        self.assertEqual(len(before["risks"]), 1)
        self.assertEqual(before["risks"][0]["evidence"], [edge])

        git(consumer, "merge", "--no-edit", "api-producer")
        self.assertEqual(self.cli(0, self.config)["risks"], [])

        new_path = "backend/api/new_endpoint.py"
        self.write(producer, new_path, "VERSION = 3\n")
        after = self.cli(1, self.config)
        self.assertEqual(len(after["risks"]), 1)
        risk = after["risks"][0]
        self.assertEqual(risk["kind"], "api_contract_overlap")
        self.assertEqual(risk["subject"], "public_api")
        self.assertEqual(risk["branches"], [consumer_id, producer_id])
        self.assertEqual(risk["merge_base"], git(producer, "rev-parse", "HEAD"))
        self.assertEqual(risk["evidence"], [{**edge, "producer_paths": [new_path]}])

    def test_detects_migration_and_contract_risks(self) -> None:
        report = branchradar.scan(self.repo, config=self.config)

        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(
            [item["name"] for item in report["branches"]],
            ["api-consumer", "api-producer", "migration-a", "migration-b"],
        )
        self.assertEqual(
            [
                (
                    risk["kind"],
                    [branch["name"] for branch in risk["branches"]],
                    risk["subject"],
                )
                for risk in report["risks"]
            ],
            [
                (
                    "api_contract_overlap",
                    ["api-consumer", "api-producer"],
                    "public_api",
                ),
                (
                    "parallel_django_migrations",
                    ["migration-a", "migration-b"],
                    "backend/billing",
                ),
            ],
        )

        migration = report["risks"][1]
        self.assertEqual(
            migration["evidence"][0]["paths"],
            ["backend/billing/migrations/0002_invoice.py"],
        )
        self.assertEqual(
            [item["branch"]["id"] for item in migration["evidence"]],
            ["refs/heads/migration-a", "refs/heads/migration-b"],
        )

    def test_cli_json_is_deterministic_and_machine_readable(self) -> None:
        command = [
            sys.executable,
            str(Path(branchradar.__file__)),
            "--repo",
            str(self.repo),
            "--config",
            str(self.config),
            "--format",
            "json",
        ]
        first = subprocess.run(command, capture_output=True, text=True)
        second = subprocess.run(command, capture_output=True, text=True)

        self.assertEqual(first.returncode, 1)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(len(json.loads(first.stdout)["risks"]), 2)

    def test_cli_from_subdirectory_uses_repository_root_config(self) -> None:
        for name in ("migration-a", "migration-b"):
            git(self.repo, "worktree", "remove", str(self.root / name))
        (self.repo / ".branchradar.toml").write_text(
            self.config.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(Path(branchradar.__file__)),
            "--format",
            "json",
        ]
        subdirectory = self.repo / "backend/api"

        warning = subprocess.run(
            command, cwd=subdirectory, capture_output=True, text=True
        )
        self.assertEqual(warning.returncode, 1, warning.stderr)
        report = json.loads(warning.stdout)
        self.assertEqual(report["repository"], str(self.repo.resolve()))
        self.assertEqual(
            [risk["kind"] for risk in report["risks"]],
            ["api_contract_overlap"],
        )

        git(
            self.repo,
            "worktree",
            "remove",
            str(self.root / "api-consumer"),
        )
        clean = subprocess.run(
            command, cwd=subdirectory, capture_output=True, text=True
        )
        self.assertEqual(clean.returncode, 0, clean.stderr)
        clean_report = json.loads(clean.stdout)
        self.assertEqual(clean_report["repository"], str(self.repo.resolve()))
        self.assertEqual(clean_report["risks"], [])

    def test_unchecked_branch_requires_opt_in(self) -> None:
        git(self.repo, "branch", "parked", "main")

        default = branchradar.scan(self.repo, config=self.config)
        all_branches = branchradar.scan(
            self.repo, config=self.config, include_all_branches=True
        )

        self.assertNotIn("parked", [item["name"] for item in default["branches"]])
        self.assertIn("parked", [item["name"] for item in all_branches["branches"]])

    def test_cli_tracks_uncommitted_migration_risk_lifecycle(self) -> None:
        for name in ("migration-a", "migration-b", "api-producer", "api-consumer"):
            git(self.repo, "worktree", "remove", str(self.root / name))
        invoice = self.add_worktree("invoice")
        payment = self.add_worktree("payment")
        command = [
            sys.executable,
            str(Path(branchradar.__file__)),
            "--repo",
            str(self.repo),
            "--format",
            "json",
        ]

        def cli(expected_status: int) -> dict:
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, expected_status, result.stderr)
            return json.loads(result.stdout)

        self.assertEqual(cli(0)["risks"], [])

        invoice_path = "backend/billing/migrations/0002_invoice.py"
        payment_path = "backend/billing/migrations/0002_payment.py"
        self.write(invoice, invoice_path, "# staged, not committed\n")
        git(invoice, "add", invoice_path)
        self.write(payment, payment_path, "# untracked, not committed\n")
        warning = cli(1)
        self.assertEqual(len(warning["risks"]), 1)
        risk = warning["risks"][0]
        self.assertEqual(risk["kind"], "parallel_django_migrations")
        self.assertEqual(risk["subject"], "backend/billing")
        self.assertEqual(
            [item["paths"] for item in risk["evidence"]],
            [[invoice_path], [payment_path]],
        )
        self.assertEqual(git(invoice, "rev-parse", "HEAD"), git(payment, "rev-parse", "HEAD"))
        self.assertEqual(git(invoice, "status", "--porcelain"), f"A  {invoice_path}")
        self.assertEqual(git(payment, "status", "--porcelain"), f"?? {payment_path}")

        (payment / payment_path).unlink()
        self.assertEqual(cli(0)["risks"], [])

        self.write(payment, "backend/orders/migrations/0002_order.py", "# different app\n")
        self.assertEqual(cli(0)["risks"], [])

    def test_stacked_shared_and_identical_history_are_not_parallel_changes(self) -> None:
        self.add_branch(
            "stacked",
            "backend/billing/migrations/0003_stacked.py",
            "# stacked\n",
            start="migration-a",
        )
        merged = self.add_worktree("merged", start="migration-a")
        git(merged, "merge", "--no-edit", "migration-b")
        self.add_branch(
            "api-stacked",
            "frontend/src/api/stacked.ts",
            "export const stacked = true\n",
            start="api-producer",
        )
        self.add_branch(
            "shared-common",
            "backend/orders/migrations/0001_initial.py",
            "# common\n",
        )
        self.add_branch("shared-a", "docs/a.txt", "a\n", start="shared-common")
        self.add_branch("shared-b", "docs/b.txt", "b\n", start="shared-common")
        git(self.repo, "branch", "same-tip-a", "migration-a")
        git(self.repo, "branch", "same-tip-b", "migration-a")

        report = branchradar.scan(
            self.repo, config=self.config, include_all_branches=True
        )
        risk_pairs = {
            (risk["kind"], frozenset(branch["name"] for branch in risk["branches"]))
            for risk in report["risks"]
        }

        for names in (
            ("migration-a", "stacked"),
            ("migration-a", "merged"),
            ("migration-b", "merged"),
            ("shared-a", "shared-b"),
            ("same-tip-a", "same-tip-b"),
        ):
            self.assertNotIn(
                ("parallel_django_migrations", frozenset(names)), risk_pairs
            )
        self.assertNotIn(
            ("api_contract_overlap", frozenset(("api-producer", "api-stacked"))),
            risk_pairs,
        )
        self.assertFalse(
            any(risk["subject"] == "backend/orders" for risk in report["risks"])
        )

    def test_dirty_and_renamed_paths_are_part_of_the_footprint(self) -> None:
        dirty = self.add_worktree("dirty")
        self.write(dirty, "backend/api/schema.py", "VERSION = 3\n")
        git(dirty, "add", "backend/api/schema.py")
        self.write(
            dirty,
            "frontend/src/api/client.ts",
            "export const version = 3\n",
        )
        self.write(
            dirty,
            "backend/billing/migrations/0002_dirty.py",
            "# untracked\n",
        )
        unsafe_path = "frontend/src/api/escape\x1b[31m.ts"
        self.write(dirty, unsafe_path, "unsafe name\n")

        renamed = self.add_worktree("renamed")
        git(
            renamed,
            "mv",
            "backend/api/schema.py",
            "backend/api/schema_v2.py",
        )
        git(renamed, "commit", "-m", "rename api schema")

        report = branchradar.scan(self.repo, config=self.config)
        footprints = {item["name"]: item["footprint"] for item in report["branches"]}

        self.assertEqual(
            footprints["dirty"]["dirty_paths"],
            [
                "backend/api/schema.py",
                "backend/billing/migrations/0002_dirty.py",
                "frontend/src/api/client.ts",
                unsafe_path,
            ],
        )
        self.assertIn("backend/api/schema.py", footprints["renamed"]["changed_paths"])
        self.assertIn(
            "backend/api/schema_v2.py", footprints["renamed"]["changed_paths"]
        )
        self.assertTrue(
            any(
                "dirty" in [branch["name"] for branch in risk["branches"]]
                and risk["kind"] == "parallel_django_migrations"
                for risk in report["risks"]
            )
        )

        text = branchradar.render_text(report)
        self.assertNotIn("\x1b", text)
        self.assertIn("\\x1b[31m.ts", text)

    def test_dirty_reversion_uses_effective_worktree_state(self) -> None:
        reverted = self.add_branch(
            "reverted",
            "backend/billing/migrations/0002_reverted.py",
            "# committed then removed\n",
        )
        (reverted / "backend/billing/migrations/0002_reverted.py").unlink()

        report = branchradar.scan(self.repo, config=self.config)
        reverted_branch = next(
            branch for branch in report["branches"] if branch["name"] == "reverted"
        )
        pairs = {
            frozenset(branch["name"] for branch in risk["branches"])
            for risk in report["risks"]
            if risk["kind"] == "parallel_django_migrations"
        }

        self.assertEqual(reverted_branch["footprint"]["changed_paths"], [])
        self.assertEqual(
            reverted_branch["footprint"]["dirty_paths"],
            ["backend/billing/migrations/0002_reverted.py"],
        )
        self.assertNotIn(frozenset(("reverted", "migration-a")), pairs)
        self.assertNotIn(frozenset(("reverted", "migration-b")), pairs)

    def test_dirty_base_worktree_is_an_explicit_candidate(self) -> None:
        self.write(self.repo, "backend/api/schema.py", "VERSION = 4\n")
        git(self.repo, "add", "backend/api/schema.py")
        self.write(
            self.repo,
            "frontend/src/api/client.ts",
            "export const version = 4\n",
        )
        self.write(
            self.repo,
            "backend/billing/migrations/0002_base_dirty.py",
            "# untracked on base\n",
        )

        report = branchradar.scan(self.repo, config=self.config)
        base = next(
            branch
            for branch in report["branches"]
            if branch["name"] == "main (working tree)"
        )

        self.assertEqual(base["id"], "working-tree:refs/heads/main")
        self.assertEqual(
            base["footprint"]["dirty_paths"],
            [
                "backend/api/schema.py",
                "backend/billing/migrations/0002_base_dirty.py",
                "frontend/src/api/client.ts",
            ],
        )
        self.assertTrue(
            any(
                "main (working tree)"
                in [branch["name"] for branch in risk["branches"]]
                and risk["kind"] == "parallel_django_migrations"
                for risk in report["risks"]
            )
        )

    def test_unicode_controls_and_non_utf8_paths_are_safe(self) -> None:
        relative_bytes = b"frontend/src/api/invalid-\xff.ts"
        bidi_bytes = "frontend/src/api/reverse\u202ename.ts".encode()
        blob = subprocess.run(
            ["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"],
            input=b"unsafe names\n",
            capture_output=True,
            check=True,
        ).stdout.strip()
        index = self.root / "unsafe.index"
        environment = {**os.environ, "GIT_INDEX_FILE": str(index)}
        subprocess.run(
            ["git", "-C", str(self.repo), "read-tree", "main"],
            env=environment,
            check=True,
        )
        index_entries = b"".join(
            b"100644 " + blob + b"\t" + path + b"\0"
            for path in (relative_bytes, bidi_bytes)
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "update-index", "-z", "--index-info"],
            input=index_entries,
            env=environment,
            check=True,
        )
        tree = subprocess.run(
            ["git", "-C", str(self.repo), "write-tree"],
            env=environment,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        commit = git(self.repo, "commit-tree", tree, "-p", "main", "-m", "unsafe names")
        git(self.repo, "update-ref", "refs/heads/unsafe-names", commit)

        report = branchradar.scan(
            self.repo, config=self.config, include_all_branches=True
        )
        unsafe_branch = next(
            branch for branch in report["branches"] if branch["name"] == "unsafe-names"
        )
        invalid_path = os.fsdecode(relative_bytes)

        self.assertIn(invalid_path, unsafe_branch["footprint"]["changed_paths"])
        text = branchradar.render_text(report)
        self.assertNotIn("\u202e", text)
        self.assertNotIn("\udcff", text)
        self.assertIn("\\u202e", text)
        self.assertIn("\\udcff", text)
        text.encode("utf-8")
        json.dumps(report, sort_keys=True).encode("utf-8")


class BranchRadarUnitTest(unittest.TestCase):
    def test_globs_are_anchored_and_path_aware(self) -> None:
        self.assertTrue(
            branchradar._matches("backend/api/schema.py", ("backend/*/schema.py",))
        )
        self.assertFalse(
            branchradar._matches(
                "backend/api/v2/schema.py", ("backend/*/schema.py",)
            )
        )
        self.assertTrue(
            branchradar._matches(
                "backend/api/v2/schema.py", ("backend/**/schema.py",)
            )
        )
        self.assertTrue(branchradar._matches("schema.py", ("**/schema.py",)))
        self.assertTrue(
            branchradar._matches("backend/a\nb/schema.py", ("backend/**/schema.py",))
        )
        self.assertTrue(branchradar._matches("[api].py", ("[api].py",)))
        self.assertFalse(branchradar._matches("a.py", ("[api].py",)))

    def test_control_characters_are_escaped(self) -> None:
        self.assertEqual(
            branchradar._escape_controls("a\n\t\x1b\x7f\u202e\udcffb"),
            "a\\x0a\\x09\\x1b\\x7f\\u202e\\udcffb",
        )

    def test_multiple_merge_bases_fail_deterministically(self) -> None:
        first = "a" * 40
        second = "b" * 40
        with mock.patch.object(
            branchradar, "_git", return_value=f"{second}\n{first}\n"
        ):
            with self.assertRaisesRegex(
                branchradar.BranchRadarError,
                rf"multiple merge bases.*{first[:12]}, {second[:12]}",
            ):
                branchradar._merge_base(Path("."), "left", "right")

    def test_prunable_and_detached_worktrees_have_safe_identities(self) -> None:
        commit = "a" * 40
        records = [
            {
                "worktree": "/missing",
                "HEAD": commit,
                "branch": "refs/heads/stale",
                "prunable": "gitdir file points to non-existent location",
            },
            {"worktree": "/one", "HEAD": commit, "detached": ""},
            {"worktree": "/two", "HEAD": commit, "detached": ""},
        ]
        with mock.patch.object(
            branchradar, "_base_full_ref", return_value="refs/heads/main"
        ), mock.patch.object(branchradar, "_worktrees", return_value=records):
            candidates = branchradar.enumerate_candidates(Path("."), "main")

        self.assertEqual(len(candidates), 2)
        self.assertEqual(len({candidate.id for candidate in candidates}), 2)
        self.assertNotIn("stale", [candidate.name for candidate in candidates])

        footprint = {
            "django_migrations": {"app": ["app/migrations/0002_x.py"]}
        }
        left = {"id": "left", "name": "same", "footprint": footprint}
        right = {"id": "right", "name": "same", "footprint": footprint}
        risk = branchradar._migration_risks(left, right, commit)[0]
        self.assertEqual(
            [item["branch"]["id"] for item in risk["evidence"]],
            ["left", "right"],
        )


if __name__ == "__main__":
    unittest.main()
