"""Conformance smoke tests for machine-driver — stdlib unittest, no deps.

Each test runs the real driver as a subprocess against a throwaway goal, with the
Telegram env unset so alerts degrade to stdout (no pings). Run:

    python3 -m unittest discover -s tests -v
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DRIVER = Path(__file__).resolve().parent.parent / "driver.py"


def write_goal(d, **over):
    base = {
        "repo": str(d), "worker_cmd": "true", "tick_seconds": 0,
        "tasks": [{"id": "t1", "goal": "x", "status": "pending"},
                  {"id": "t2", "goal": "y", "status": "pending"}],
    }
    base.update(over)
    p = Path(d) / "goal.json"
    p.write_text(json.dumps(base))
    return p


def run_driver(goal_path):
    env = dict(os.environ)
    env.pop("TELEGRAM_BOT_TOKEN", None)
    env.pop("TELEGRAM_CHAT_ID", None)
    return subprocess.run([sys.executable, str(DRIVER), str(goal_path)],
                          capture_output=True, text=True, env=env)


def aar_events(d):
    return [json.loads(l)["event"] for l in (Path(d) / "aar.jsonl").read_text().splitlines()]


class DriverConformance(unittest.TestCase):
    def test_happy_path_emits_aar_and_completes(self):
        with tempfile.TemporaryDirectory() as d:
            g = write_goal(d, goal="happy", verify_cmd="true",
                           contract={"proposed_by": "p", "ratified_by": "council", "autonomy_ceiling": 1})
            r = run_driver(g)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            ev = aar_events(d)
            self.assertIn("verified", ev)
            self.assertEqual(ev[-1], "goal_complete")

    def test_governor_halts_and_returns_2(self):
        with tempfile.TemporaryDirectory() as d:
            g = write_goal(d, goal="gov", verify_cmd="true", budget={"max_worker_runs": 1},
                           tasks=[{"id": f"t{i}", "goal": "z", "status": "pending"} for i in range(3)])
            r = run_driver(g)
            self.assertEqual(r.returncode, 2)
            self.assertIn("BUDGET HALT", r.stdout)

    def test_no_verifier_forces_propose_and_quarantines(self):
        with tempfile.TemporaryDirectory() as d:
            g = write_goal(d, goal="noverify", mode="commit", max_attempts=1,
                           tasks=[{"id": "t1", "goal": "x", "status": "pending"}],
                           contract={"proposed_by": "p", "ratified_by": "council", "autonomy_ceiling": 3})
            r = run_driver(g)
            self.assertIn("mode=propose", r.stdout)   # no verifier -> trust 0 -> propose, despite mode=commit
            self.assertIn("QUARANTINED", r.stdout)

    def test_aar_hash_chain_intact(self):
        with tempfile.TemporaryDirectory() as d:
            g = write_goal(d, goal="chain", verify_cmd="true",
                           contract={"proposed_by": "p", "ratified_by": "council", "autonomy_ceiling": 1})
            run_driver(g)
            prev = ""
            for ln in (Path(d) / "aar.jsonl").read_text().splitlines():
                rec = json.loads(ln)
                h = rec.pop("hash")
                calc = hashlib.sha256((prev + json.dumps(rec, sort_keys=True)).encode()).hexdigest()[:16]
                self.assertEqual(rec.get("prev_hash"), prev)
                self.assertEqual(calc, h)
                prev = h


if __name__ == "__main__":
    unittest.main()
