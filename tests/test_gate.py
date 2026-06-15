"""Commit-mode gate regression tests for machine-driver — proving the three Δ-hardening
behaviors that the rest of the suite never exercises (every other test runs propose-mode):

  Δ1 reversibility-in-gate · Box-0 commit hard-deny · the apply_mutation chokepoint.

Each test stands up a REAL temp git repo (git init + an initial commit so HEAD exists),
drives a commit-mode goal, and asserts on ACTUAL commits via `git rev-list --count` and on
the driver-log.jsonl event stream — not on the driver's say-so. Deterministic, no network,
keyless (no AAR signing identity ⇒ no node dependency). Skipped cleanly where git is absent.

worker_cmd is a no-op ("true") exactly as specified — the GATE and the mutation chokepoint
are what's under test here, not the worker. Because `true` leaves the tree clean (and git
won't create an empty commit), each test seeds one real working-tree change so the chokepoint
has something to commit when the gate says commit; when the gate says propose / hard-deny,
that seeded change is correctly left uncommitted in the tree.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRIVER = ROOT / "driver.py"

RATIFIED = {"proposed_by": "p", "ratified_by": "council", "autonomy_ceiling": 1}     # ratified_by != proposed_by
SELF_RATIFIED = {"proposed_by": "p", "ratified_by": "p", "autonomy_ceiling": 1}      # self-ratified => Box-0 hard-deny


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(repo),
                          capture_output=True, text=True, check=True).stdout


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")   # local identity so commits never fail
    _git(repo, "config", "user.name", "t")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")   # HEAD baseline for rev-list


def _commit_count(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "HEAD").strip())


@unittest.skipUnless(shutil.which("git"), "needs git on PATH")
class CommitModeGate(unittest.TestCase):
    def _drive(self, tmp: Path, contract: dict, task: dict) -> subprocess.CompletedProcess:
        repo = tmp / "repo"
        _init_repo(repo)
        (repo / "change.txt").write_text("work")   # seed a real diff for the chokepoint
        self._repo, self._before = repo, _commit_count(repo)
        goal = {
            "goal": "gate", "repo": str(repo), "mode": "commit",
            "worker_cmd": "true", "verify_cmd": "true", "tick_seconds": 0,
            "contract": contract, "tasks": [task],
        }
        (tmp / "goal.json").write_text(json.dumps(goal))
        return subprocess.run([sys.executable, str(DRIVER), str(tmp / "goal.json")],
                              capture_output=True, text=True, cwd=str(tmp))

    def _events(self, tmp: Path) -> list:
        log = tmp / "driver-log.jsonl"
        return [json.loads(l)["event"] for l in log.read_text().splitlines()] if log.exists() else []

    def test_reversible_ratified_commits(self):
        # reversible + independently-ratified contract + ceiling 1 + commit -> COMMITS.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            r = self._drive(tmp, RATIFIED,
                            {"id": "t1", "goal": "x", "reversible": True, "status": "pending"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(_commit_count(self._repo), self._before + 1,
                             "reversible + ratified + ceiling1 must produce a commit")
            self.assertIn("commit", self._events(tmp))

    def test_irreversible_default_does_not_commit(self):
        # `reversible` omitted -> defaults to False (irreversible). At ceiling 1 with no
        # irreversible_min_trust the bar is strictest, so the gate falls to propose: NO commit.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            r = self._drive(tmp, RATIFIED,
                            {"id": "t1", "goal": "x", "status": "pending"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(_commit_count(self._repo), self._before,
                             "irreversible action at ceiling 1 must NOT commit (reversibility bar)")
            self.assertNotIn("commit", self._events(tmp))
            self.assertNotIn("commit_denied", self._events(tmp))   # propose path, not a refusal

    def test_self_ratified_is_hard_denied(self):
        # Gate would allow (reversible + ceiling1) but the contract is self-ratified
        # (ratified_by == proposed_by) -> Box-0 HARD-DENY at the chokepoint: NO commit,
        # and a `commit_denied` event is recorded (loud refusal, not a silent downgrade).
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            r = self._drive(tmp, SELF_RATIFIED,
                            {"id": "t1", "goal": "x", "reversible": True, "status": "pending"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(_commit_count(self._repo), self._before,
                             "self-ratified contract must be hard-denied (no commit)")
            ev = self._events(tmp)
            self.assertIn("commit_denied", ev)
            self.assertNotIn("commit", ev)


if __name__ == "__main__":
    unittest.main()
