#!/usr/bin/env python3
"""
machine-driver — the dumb, tireless driver. Part 2 of "The Machine."
(See: Obsidian Vault/Systems/The Machine — Conformance Spec v1.md)

The driver is the DUMBEST part of the system, on purpose. It spends no model
tokens and makes no judgments. It reads a goal, picks the next pending task,
fires ONE fresh worker (the only place judgment is spent), checks the result
against REALITY (not the worker's say-so), records the outcome, and loops —
surviving restarts via an atomic checkpoint. The thing that runs for days is
THIS loop; the model runs in short bursts, one per task.

Iteration 1 (2026-06-14) hardens the skeleton toward Conformance Spec v1 WITHOUT
adding a single model token to the loop — every addition below is deterministic:
  - Box 0  CONTRACT GATE — a move may not begin unless the goal carries a contract
           whose target was ratified by someone OTHER than its proposer
           (ratified_by != proposed_by). Missing / self-ratified -> forced propose-only.
           (The Council is the natural ratifier here — independent judgment on the
           TARGET. Ground-truth tests stay the Box 4 verifier; a model panel is not.)
  - Box 4  AAR — every transition appends a hash-chained receipt to aar.jsonl: the
           proof layer, not the worker's claim. (Minimal/AAR-shaped for now; to be
           reconciled with the canonical signed AAR spec — agentscontrolplane.org.)
  - Box 5  GOVERNOR — a goal-level budget (worker-runs / wall-clock). Breach -> HALT
           + operator alert. THIS is the control the 131-duplicate incident lacked.
           AUTONOMY — effective = min(operator mode, contract ceiling, verifier trust);
           NO verifier -> trust 0 -> propose-only (the keystone gate).
  - ESCALATION — block / quarantine / budget-halt fire an operator alert (Telegram),
           never a silent stdout line. The incident's defining failure was "no alert."

The loop stays dumb: all of the above is deterministic plumbing. The only judgment
in the whole system is on the far side of the `worker_cmd` shell-out.

Usage:  python3 driver.py goal.json
Env (optional): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  -> operator alerts.
"""

from __future__ import annotations  # keep type hints lazy so this runs on Python 3.7+

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GOAL_PATH = Path(sys.argv[1] if len(sys.argv) > 1 else "goal.json")
AAR_PATH = GOAL_PATH.with_name("aar.jsonl")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load() -> dict:
    return json.loads(GOAL_PATH.read_text())


def save(state: dict) -> None:
    # Atomic write — a crash mid-write must never corrupt the goal. This is what
    # makes the loop resumable: kill it any time, restart, it picks up the
    # pending tasks. (hermes batch_runner checkpoint pattern.)
    tmp = GOAL_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(GOAL_PATH)


def run(cmd: str, cwd: str) -> tuple[int, str]:
    # One fresh process = one fresh context. The driver never reasons here;
    # it just shells out. The intelligence is on the other side of this call.
    p = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr)


def alert(msg: str) -> None:
    # Escalation's outbound half. The incident cooked a GPU because nothing alerted;
    # a block / quarantine / budget-halt must reach a human, not just stdout.
    print(f"[{now()}] ALERT: {msg.splitlines()[0]}")
    bot, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (bot and chat):
        return  # degrade silently when unconfigured — the stdout line still fired
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": f"🤖 machine-driver\n{msg}"}).encode()
        urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{bot}/sendMessage", data=data), timeout=15)
    except Exception as e:
        print(f"[{now()}]    (alert send failed: {e})")


def aar_append(record: dict) -> str:
    # Box 4 proof layer: an append-only, hash-chained receipt per transition. Each
    # line carries the previous line's hash, so a tampered/missing record breaks the
    # chain. Minimal + AAR-SHAPED (prev/hash/idempotency_key/verifier/autonomy) — the
    # canonical *signed* AAR (agentscontrolplane.org) is a later reconciliation pass.
    prev = ""
    if AAR_PATH.exists():
        lines = AAR_PATH.read_text().splitlines()
        if lines:
            prev = json.loads(lines[-1]).get("hash", "")
    record = {"ts": now(), "prev_hash": prev, **record}
    record["hash"] = hashlib.sha256((prev + json.dumps(record, sort_keys=True)).encode()).hexdigest()[:16]
    with AAR_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return record["hash"]


def idem_key(goal_id: str, task: dict) -> str:
    # idempotency_key = hash(goal, task, normalized intent). Recorded on every receipt
    # so a duplicate side-effect is detectable. (Enforcement against an external store
    # — "reject the second ACTIVE row" — lands when the driver integrates with a real
    # board/gateway; for a repo, git already makes re-runs near-idempotent.)
    return hashlib.sha256(f"{goal_id}|{task['id']}|{task['goal']}".encode()).hexdigest()[:16]


def next_pending(state: dict) -> dict | None:
    for t in state["tasks"]:
        if t.get("status", "pending") == "pending":
            return t
    return None


def effective_mode(state: dict) -> tuple[str, str]:
    # Box 5 keystone: effective autonomy = min(operator mode, contract ceiling, verifier
    # trust). NO verifier (no verify_cmd) => trust 0 => propose-only, no matter what's
    # asked. autonomy_ceiling scale: 0=propose · 1+=commit-allowed (full dial is later).
    asked = state.get("mode", "propose")
    contract = state.get("contract") or {}
    ceiling = contract.get("autonomy_ceiling", 0)
    trust = 1 if state.get("verify_cmd") else 0
    if asked == "commit" and ceiling >= 1 and trust >= 1:
        return "commit", "operator=commit · ceiling>=1 · verifier present"
    why = ("no verifier -> trust 0" if not trust
           else "contract ceiling forbids commit" if ceiling < 1
           else "operator asked propose")
    return "propose", why


def main() -> int:
    state = load()
    repo = state["repo"]
    goal_id = state.get("goal", "goal")
    worker_cmd = state["worker_cmd"]
    verify_cmd = state.get("verify_cmd")
    max_attempts = state.get("max_attempts", 3)
    tick = state.get("tick_seconds", 1)
    budget = state.get("budget", {})
    contract = state.get("contract") or {}

    # --- Box 0 gate: a move may not begin without a contract whose ratifier is not its
    #     proposer. Missing / self-ratified -> loud alert + forced propose-only. ---
    proposed_by, ratified_by = contract.get("proposed_by"), contract.get("ratified_by")
    if not contract:
        alert(f"no Box-0 contract on goal {goal_id!r} — running propose-only, unratified.")
    elif not ratified_by or ratified_by == proposed_by:
        alert(f"contract NOT independently ratified (ratified_by={ratified_by!r}, "
              f"proposed_by={proposed_by!r}) on {goal_id!r} — propose-only until a third party ratifies.")

    mode, why = effective_mode(state)
    print(f"[{now()}] driver up — goal: {goal_id!r}  mode={mode} ({why})")
    aar_append({"event": "driver_up", "goal": goal_id, "mode": mode,
                "ratified_by": ratified_by, "proposed_by": proposed_by})

    runs = state.get("runs", 0)
    t0 = time.monotonic()

    while True:
        # --- Box 5 governor: the control the 131-duplicate incident lacked. ---
        if budget.get("max_worker_runs") and runs >= budget["max_worker_runs"]:
            msg = f"BUDGET HALT — {runs} worker-runs hit max_worker_runs={budget['max_worker_runs']} on {goal_id!r}."
            aar_append({"event": "budget_halt", "runs": runs})
            alert(msg)
            print(f"[{now()}] {msg}")
            return 2
        if budget.get("max_wall_seconds") and (time.monotonic() - t0) >= budget["max_wall_seconds"]:
            msg = f"BUDGET HALT — wall-clock hit max_wall_seconds={budget['max_wall_seconds']} on {goal_id!r}."
            aar_append({"event": "budget_halt", "wall_s": round(time.monotonic() - t0)})
            alert(msg)
            print(f"[{now()}] {msg}")
            return 2

        task = next_pending(state)
        if task is None:
            blocked = [t for t in state["tasks"] if t.get("status") == "blocked"]
            print(f"[{now()}] GOAL COMPLETE — {len(state['tasks'])} tasks, {len(blocked)} blocked/surfaced.")
            aar_append({"event": "goal_complete", "tasks": len(state["tasks"]), "blocked": len(blocked)})
            return 0

        task["attempts"] = task.get("attempts", 0) + 1
        task["status"] = "running"
        task["started"] = now()
        ikey = idem_key(goal_id, task)
        save(state)
        print(f"[{now()}] -> {task['id']} (attempt {task['attempts']}): {task['goal']}")
        aar_append({"event": "dispatch", "task": task["id"], "attempt": task["attempts"], "idempotency_key": ikey})

        # 3. WORKER — fresh process; the only judgment in the whole loop.
        run(worker_cmd.format(task=task["goal"]), repo)
        runs += 1
        state["runs"] = runs

        # 4. VERIFY — ground truth. We trust the exit code, not a 'done' claim.
        #    No verifier => Box 4 trust 0 => cannot pass (fail-closed).
        if not verify_cmd:
            vcode, vout = 1, "(no verify_cmd — Box 4 trust 0; cannot verify against reality)"
        else:
            vcode, vout = run(verify_cmd, repo)
        task["verify_tail"] = vout[-600:]

        if vcode == 0:
            task["status"] = "done"
            task["done"] = now()
            state["last_success_at"] = now()           # observability heartbeat
            print(f"[{now()}]    ok  verified.")
            aar_append({"event": "verified", "task": task["id"], "verify_exit": 0,
                        "verifier_id": verify_cmd, "idempotency_key": ikey, "autonomy_used": mode})
            # 5. AUTONOMY — propose leaves the diff for review; commit proceeds.
            if mode == "commit":
                run(f"git add -A && git commit -m 'driver: {task['id']}'", repo)
                print(f"[{now()}]    committed.")
                aar_append({"event": "commit", "task": task["id"]})
            else:
                print(f"[{now()}]    (propose mode — diff left for your review)")
        elif task["attempts"] >= max_attempts:
            task["status"] = "blocked"                 # terminal quarantine — surfaced, not re-queued
            task["reason"] = f"verify failed {max_attempts}x"
            aar_append({"event": "quarantine", "task": task["id"], "verify_exit": vcode, "idempotency_key": ikey})
            alert(f"task {task['id']} QUARANTINED on {goal_id!r} after {max_attempts} failed verifies — surfaced, not skipped.")
            print(f"[{now()}]    BLOCKED after {max_attempts} attempts — surfaced + alerted.")
        else:
            task["status"] = "pending"   # re-queue: a fresh worker, fresh context, tries again
            aar_append({"event": "requeue", "task": task["id"], "verify_exit": vcode})
            print(f"[{now()}]    verify failed — re-queueing for a fresh attempt.")

        save(state)
        time.sleep(tick)


if __name__ == "__main__":
    raise SystemExit(main())
