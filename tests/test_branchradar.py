import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

    def add_branch(self, name: str, path: str, content: str) -> None:
        worktree = self.root / name
        git(self.repo, "worktree", "add", "-b", name, str(worktree), "main")
        self.write(worktree, path, content)
        git(worktree, "add", ".")
        git(worktree, "commit", "-m", name)

    def test_detects_migration_and_contract_risks(self) -> None:
        report = branchradar.scan(self.repo, config=self.config)

        self.assertEqual(
            [item["name"] for item in report["branches"]],
            ["api-consumer", "api-producer", "migration-a", "migration-b"],
        )
        self.assertEqual(
            [(risk["kind"], risk["branches"], risk["subject"]) for risk in report["risks"]],
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
            migration["evidence"]["migration-a"],
            ["backend/billing/migrations/0002_invoice.py"],
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

    def test_unchecked_branch_requires_opt_in(self) -> None:
        git(self.repo, "branch", "parked", "main")

        default = branchradar.scan(self.repo, config=self.config)
        all_branches = branchradar.scan(
            self.repo, config=self.config, include_all_branches=True
        )

        self.assertNotIn("parked", [item["name"] for item in default["branches"]])
        self.assertIn("parked", [item["name"] for item in all_branches["branches"]])


if __name__ == "__main__":
    unittest.main()
