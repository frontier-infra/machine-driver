"""Iteration-2: the driver emits a canonical, Ed25519-signed AAR per resolved task.

End-to-end, with a THROWAWAY local key (no production identity, no domain): keygen → run the
driver on a trivial verified task → it writes aar/<id>.json and signs it via our own aar.mjs →
verify it OFFLINE (`--did-json`) and assert `conformance: L2`. Skipped cleanly where node or the
sibling signer is absent.
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
AAR_MJS = ROOT.parent / "agentcontrolplane" / "tools" / "aar.mjs"


@unittest.skipUnless(shutil.which("node") and AAR_MJS.exists(),
                     "needs node + ../agentcontrolplane/tools/aar.mjs")
class SignedAAR(unittest.TestCase):
    def _run_driver(self, d: Path, goal: dict) -> subprocess.CompletedProcess:
        gp = d / "goal.json"
        gp.write_text(json.dumps(goal))
        return subprocess.run([sys.executable, str(DRIVER), str(gp)],
                              capture_output=True, text=True, cwd=str(d))

    def test_verified_task_emits_signed_aar_that_verifies_L2(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            repo = d / "repo"; repo.mkdir()
            priv, didj = d / "k.jwk.json", d / "did.json"
            subprocess.run(["node", str(AAR_MJS), "keygen", "--did", "did:web:example.com",
                            "--out-priv", str(priv), "--out-did", str(didj)],
                           check=True, capture_output=True, text=True)
            goal = {
                "goal": "g", "repo": str(repo), "mode": "propose",
                "worker_cmd": "true", "verify_cmd": "true",
                "aar_tool": str(AAR_MJS), "aar_priv": str(priv),
                "subject": "did:web:example.com:machine-driver",
                "principal": "did:web:example.com",
                "tasks": [{"id": "t1", "goal": "do the thing", "status": "pending"}],
            }
            r = self._run_driver(d, goal)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

            rec_path = d / "aar" / "t1.json"
            self.assertTrue(rec_path.exists(), "signed AAR written to aar/<id>.json")
            rec = json.loads(rec_path.read_text())
            self.assertEqual(rec["verdict"], "verified")
            self.assertEqual(rec["ground_truth"], "confirmed")
            self.assertIn("sig", rec)
            self.assertNotEqual(rec["verifier"]["id"], rec["subject"])  # verifier ≠ subject

            v = subprocess.run(["node", str(AAR_MJS), "verify", str(rec_path), "--did-json", str(didj)],
                               capture_output=True, text=True)
            self.assertIn("conformance: L2", v.stdout, v.stdout + v.stderr)

            # artifact split: loop audit renamed; legacy name gone
            self.assertTrue((d / "driver-log.jsonl").exists())
            self.assertFalse((d / "aar.jsonl").exists())

    def test_relative_key_path_anchors_to_goal_dir_not_cwd(self):
        """Regression (2026-07-07 stack-demo run): relative aar_priv resolution silently
        depended on subprocess cwd. Contract now: relative anchors to goal.json's dir —
        signing must succeed even when the driver runs from a completely different cwd."""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as elsewhere:
            d = Path(tmp)
            repo = d / "repo"; repo.mkdir()
            keys = d / "keys"; keys.mkdir()
            priv, didj = keys / "k.jwk.json", d / "did.json"
            subprocess.run(["node", str(AAR_MJS), "keygen", "--did", "did:web:example.com",
                            "--out-priv", str(priv), "--out-did", str(didj)],
                           check=True, capture_output=True, text=True)
            goal = {
                "goal": "g", "repo": str(repo), "mode": "propose",
                "worker_cmd": "true", "verify_cmd": "true",
                "aar_tool": str(AAR_MJS), "aar_priv": "keys/k.jwk.json",  # goal-dir-relative
                "subject": "did:web:example.com:machine-driver",
                "principal": "did:web:example.com",
                "tasks": [{"id": "t1", "goal": "x", "status": "pending"}],
            }
            gp = d / "goal.json"
            gp.write_text(json.dumps(goal))
            # run from an unrelated cwd — resolution must not depend on it
            r = subprocess.run([sys.executable, str(DRIVER), str(gp)],
                               capture_output=True, text=True, cwd=str(elsewhere))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            rec = json.loads((d / "aar" / "t1.json").read_text())
            self.assertIn("sig", rec, "relative key path signed from foreign cwd")
            v = subprocess.run(["node", str(AAR_MJS), "verify", str(d / "aar" / "t1.json"),
                                "--did-json", str(didj)], capture_output=True, text=True)
            self.assertIn("conformance: L2", v.stdout, v.stdout + v.stderr)

    def test_missing_key_alerts_resolved_path_and_leaves_unsigned_record(self):
        """A bad key path must alert with the FULL resolved path (not node's cryptic ENOENT)
        and leave the unsigned record on disk for later signing."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "repo").mkdir()
            goal = {
                "goal": "g", "repo": str(d / "repo"), "mode": "propose",
                "worker_cmd": "true", "verify_cmd": "true",
                "aar_tool": str(AAR_MJS), "aar_priv": "keys/nope.jwk.json",
                "subject": "did:web:example.com:machine-driver",
                "principal": "did:web:example.com",
                "tasks": [{"id": "t1", "goal": "x", "status": "pending"}],
            }
            r = self._run_driver(d, goal)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("AAR sign SKIPPED", r.stdout)
            self.assertIn(str(d / "keys" / "nope.jwk.json"), r.stdout, "alert names resolved path")
            rec = json.loads((d / "aar" / "t1.json").read_text())
            self.assertNotIn("sig", rec, "record left unsigned, intact")

    def test_keyless_skips_aar_but_still_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "repo").mkdir()
            goal = {
                "goal": "g", "repo": str(d / "repo"), "mode": "propose",
                "worker_cmd": "true", "verify_cmd": "true",
                "tasks": [{"id": "t1", "goal": "x", "status": "pending"}],
            }
            r = self._run_driver(d, goal)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse((d / "aar").exists(), "no signing identity ⇒ AAR skipped")
            self.assertTrue((d / "driver-log.jsonl").exists(), "loop audit still written keyless")


if __name__ == "__main__":
    unittest.main()
