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
  - Box 4  PROOF — TWO artifacts, split cleanly (iteration 2, 2026-06-14):
           driver-log.jsonl = the hash-chained loop AUDIT (dispatch/requeue/halt);
           aar/<task-id>.json = one canonical, Ed25519-SIGNED AAR per resolved task,
           signed via our own agentscontrolplane.org signer. The signed AAR is the
           proof layer (verifier ≠ subject ⇒ AAR L2); the log is telemetry. Runs
           keyless (skips AAR) when no signing identity is configured.
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
import shlex
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

GOAL_PATH = Path(sys.argv[1] if len(sys.argv) > 1 else "goal.json")
DRIVER_LOG_PATH = GOAL_PATH.with_name("driver-log.jsonl")  # hash-chained loop audit (was aar.jsonl)
AAR_DIR = GOAL_PATH.with_name("aar")                       # one canonical SIGNED AAR per resolved task


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


def log_append(record: dict) -> str:
    # Driver-loop AUDIT (driver-log.jsonl): an append-only, hash-chained line per transition.
    # Each line carries the previous line's hash, so a tampered/missing record breaks the
    # chain. This is loop telemetry (dispatch/requeue/halt), NOT the proof layer — the
    # canonical *signed* AAR per resolved task is emit_aar() below (agentscontrolplane.org).
    prev = ""
    if DRIVER_LOG_PATH.exists():
        lines = DRIVER_LOG_PATH.read_text().splitlines()
        if lines:
            prev = json.loads(lines[-1]).get("hash", "")
    record = {"ts": now(), "prev_hash": prev, **record}
    record["hash"] = hashlib.sha256((prev + json.dumps(record, sort_keys=True)).encode()).hexdigest()[:16]
    with DRIVER_LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return record["hash"]


def idem_key(goal_id: str, task: dict) -> str:
    # idempotency_key = hash(goal, task, normalized intent). Recorded on every receipt
    # so a duplicate side-effect is detectable. (Enforcement against an external store
    # — "reject the second ACTIVE row" — lands when the driver integrates with a real
    # board/gateway; for a repo, git already makes re-runs near-idempotent.)
    return hashlib.sha256(f"{goal_id}|{task['id']}|{task['goal']}".encode()).hexdigest()[:16]


def _cfg_path(p) -> Path:
    """aar_tool / aar_priv resolution: '~' expands; a relative path anchors to goal.json's
    directory (NOT the driver's cwd) — the goal file is the config, so it is the anchor.
    Regression: 2026-07-07 stack-demo run, a repo-root-relative key path hit ENOENT because
    resolution silently depended on subprocess cwd."""
    q = Path(p).expanduser()
    return q if q.is_absolute() else (GOAL_PATH.resolve().parent / q).resolve()


def emit_aar(task: dict, vcode: int, vout: str, repo: str, verify_cmd, aar_cfg: dict):
    """Box-4 PROOF: one canonical, Ed25519-signed AAR per RESOLVED task — our own standard
    (agentscontrolplane.org), so the driver MUST emit it. Deterministic plumbing; no model
    token. If the signing identity/key isn't configured the driver runs KEYLESS: skip the AAR
    (driver-log.jsonl still records the loop). The verify step already gives `verifier ≠ subject`.
    """
    tool, priv = aar_cfg.get("aar_tool"), aar_cfg.get("aar_priv")
    subject, principal = aar_cfg.get("subject"), aar_cfg.get("principal")
    if not (tool and priv and subject and principal):
        return None  # keyless — no signing identity; loop audit still written
    tool, priv = _cfg_path(tool), _cfg_path(priv)
    AAR_DIR.mkdir(parents=True, exist_ok=True)
    verified = vcode == 0
    rec = {
        "aar": "0.02",
        "subject": subject,                                  # the worker — did:web:<org>:machine-driver
        "principal": principal,                              # the signing org — did:web:<org> (= sig.by)
        "task": {"id": task["id"], "claim": task["goal"]},
        "verdict": "verified" if verified else "rejected",
        "ground_truth": "confirmed" if verified else "contradicted",
        "reason": f"verify_cmd exited {vcode} against repo HEAD",
        "checks": [{
            "source": repo,
            "query": verify_cmd or "(no verify_cmd)",
            "observed_at": now(),
            "response_sha256": hashlib.sha256((vout or "").encode()).hexdigest(),
            "excerpt": (vout or "")[-300:],
        }],
        # verifier ≠ subject ⇒ AAR L2 (structural independence; same org ⇒ disclosed, not audit-grade)
        "verifier": {"id": aar_cfg.get("verifier") or f"{principal}:verifier", "independence": "same_principal"},
        "issued": now(),
    }
    out = AAR_DIR / f"{task['id']}.json"
    out.write_text(json.dumps(rec, indent=2))
    # Pre-check the resolved paths so a config mistake alerts with the FULL path, not a
    # cryptic ENOENT from node. The unsigned record stays on disk either way (sign later).
    missing = [str(p) for p in (tool, priv) if not p.exists()]
    if missing:
        alert(f"AAR sign SKIPPED for task {task['id']}: missing {', '.join(missing)} — record left unsigned at {out}")
        return str(out)
    # sign with OUR OWN signer (eat our own cooking) — driver stays pure-Python, shells to node.
    rc, sout = run(
        f"node {shlex.quote(str(tool))} sign {shlex.quote(str(out))} --priv {shlex.quote(str(priv))}",
        str(GOAL_PATH.resolve().parent),
    )
    if rc != 0:
        alert(f"AAR sign FAILED for task {task['id']} (rc {rc}): {sout.strip().splitlines()[-1] if sout.strip() else '?'}")
    return str(out)


def next_pending(state: dict) -> dict | None:
    for t in state["tasks"]:
        if t.get("status", "pending") == "pending":
            return t
    return None


def contract_ratified(contract: dict) -> bool:
    # Box 0: a contract is INDEPENDENTLY ratified iff it exists and a THIRD party ratified
    # its target — ratified_by is present and is not the proposer. Missing / self-ratified
    # ⇒ not ratified. This is the predicate the durable-mutation hard-deny gates on.
    if not contract:
        return False
    return bool(contract.get("ratified_by")) and contract.get("ratified_by") != contract.get("proposed_by")


def effective_mode(state: dict, task: dict | None = None) -> tuple[str, str]:
    # Box 5 keystone (deterministic; no model token). The autonomy TIER granted is the
    # contract's autonomy_ceiling; the operator dial (commit/propose) and verifier trust
    # are binary admissions. Commit proceeds iff: operator asked commit AND a verifier is
    # present (trust 1) AND the granted ceiling clears the tier THIS action REQUIRES.
    # NO verifier (no verify_cmd) => trust 0 => propose-only, no matter what's asked.
    # autonomy_ceiling scale: 0=propose · 1+=commit-allowed (full dial is later).
    #
    # Δ1 (vNext Box 5) — reversibility is a TERM INSIDE the evaluated gate, not commentary:
    # a reversible action clears the baseline commit tier (>=1); an IRREVERSIBLE action must
    # clear the higher bar contract.irreversible_min_trust. A missing per-task `reversible`
    # defaults to False (treat as irreversible); a missing irreversible_min_trust defaults
    # to the STRICTEST reading (deny autonomous irreversible commit until the contract opts
    # in by raising the ceiling to meet it).
    asked = state.get("mode", "propose")
    contract = state.get("contract") or {}
    ceiling = contract.get("autonomy_ceiling", 0)
    trust = 1 if state.get("verify_cmd") else 0
    reversible = bool(task.get("reversible", False)) if task is not None else False
    if reversible:
        required = 1                                  # baseline commit tier
    else:
        irr = contract.get("irreversible_min_trust")  # higher bar for irreversible actions
        required = irr if isinstance(irr, int) and not isinstance(irr, bool) and irr >= 1 else float("inf")
    if asked == "commit" and trust >= 1 and ceiling >= required:
        note = "reversible" if reversible else f"irreversible · ceiling {ceiling}>={required}"
        return "commit", f"operator=commit · {note} · verifier present"
    if not trust:
        why = "no verifier -> trust 0"
    elif asked != "commit":
        why = "operator asked propose"
    elif ceiling < 1:
        why = "contract ceiling forbids commit"
    elif not reversible and ceiling < required:
        req = "strictest" if required == float("inf") else required
        why = f"irreversible action needs ceiling>={req} (have {ceiling})"
    else:
        why = "operator asked propose"
    return "propose", why


def apply_mutation(repo: str, task: dict, mode: str, state: dict) -> bool:
    """THE single named path to a durable mutation — every git commit routes through here,
    so "all mutation via the gate" is ONE auditable chokepoint (non-bypassable). It
    RE-CHECKS the Box-5 gate at the mutation site (defense in depth, not just where the
    decision was first taken) and enforces the Box-0 contract before committing. Returns
    True iff it committed; False if it REFUSED (caller leaves the diff in the working tree).

    Acknowledged residue, OUT OF SCOPE here: the pre-gate `worker_cmd` shell-out in main()'s
    WORKER step already edits the working tree BEFORE this gate runs; that pre-gate tree
    mutation is not yet routed through this chokepoint.
    """
    if mode != "commit":
        return False
    contract = state.get("contract") or {}
    goal_id = state.get("goal", "goal")
    # Re-evaluate the Box-5 gate HERE, at the mutation site (non-bypassable choke).
    gate_mode, _ = effective_mode(state, task)
    if gate_mode != "commit":
        alert(f"Box-5 gate re-check at mutation site DENIED commit of {task['id']} on {goal_id!r}.")
        log_append({"event": "commit_denied", "task": task["id"], "reason": "gate_recheck"})
        return False
    # Box 0 HARD-DENY (not a silent downgrade): a durable mutation requires an independently
    # ratified contract. Refuse loudly; do NOT quietly behave as propose.
    if not contract_ratified(contract):
        alert(f"Box-0 HARD-DENY: refused durable mutation (commit) of {task['id']} on {goal_id!r} "
              f"— contract not independently ratified "
              f"(ratified_by={contract.get('ratified_by')!r}, proposed_by={contract.get('proposed_by')!r}).")
        log_append({"event": "commit_denied", "task": task["id"], "reason": "contract_not_ratified"})
        return False
    run(f"git add -A && git commit -m 'driver: {task['id']}'", repo)
    log_append({"event": "commit", "task": task["id"]})
    return True


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
    aar_cfg = {k: state.get(k) for k in ("aar_tool", "aar_priv", "subject", "principal", "verifier")}

    # --- Box 0 gate: a DURABLE MUTATION may not proceed without a contract whose ratifier
    #     is not its proposer. Enforcement is a HARD-DENY at the mutation chokepoint
    #     (apply_mutation), NOT a silent downgrade. We surface the condition early here;
    #     a propose-mode run performs no durable mutation, so it proceeds normally even
    #     with no/unratified contract (the keyless / propose path stays live). ---
    proposed_by, ratified_by = contract.get("proposed_by"), contract.get("ratified_by")
    if not contract_ratified(contract):
        alert(f"Box-0 contract NOT independently ratified on {goal_id!r} "
              f"(ratified_by={ratified_by!r}, proposed_by={proposed_by!r}) — any durable mutation "
              f"(commit) will be HARD-DENIED until a third party ratifies; propose-mode still runs.")

    # Banner reflects the gate for the FIRST pending task (reversibility is per-task, so a
    # single goal-level line would mislead); the loop re-decides per task authoritatively.
    mode, why = effective_mode(state, next_pending(state))
    print(f"[{now()}] driver up — goal: {goal_id!r}  mode={mode} ({why})")
    log_append({"event": "driver_up", "goal": goal_id, "mode": mode,
                "ratified_by": ratified_by, "proposed_by": proposed_by})

    runs = state.get("runs", 0)
    t0 = time.monotonic()

    while True:
        # --- Box 5 governor: the control the 131-duplicate incident lacked. ---
        if budget.get("max_worker_runs") and runs >= budget["max_worker_runs"]:
            msg = f"BUDGET HALT — {runs} worker-runs hit max_worker_runs={budget['max_worker_runs']} on {goal_id!r}."
            log_append({"event": "budget_halt", "runs": runs})
            alert(msg)
            print(f"[{now()}] {msg}")
            return 2
        if budget.get("max_wall_seconds") and (time.monotonic() - t0) >= budget["max_wall_seconds"]:
            msg = f"BUDGET HALT — wall-clock hit max_wall_seconds={budget['max_wall_seconds']} on {goal_id!r}."
            log_append({"event": "budget_halt", "wall_s": round(time.monotonic() - t0)})
            alert(msg)
            print(f"[{now()}] {msg}")
            return 2

        task = next_pending(state)
        if task is None:
            blocked = [t for t in state["tasks"] if t.get("status") == "blocked"]
            print(f"[{now()}] GOAL COMPLETE — {len(state['tasks'])} tasks, {len(blocked)} blocked/surfaced.")
            log_append({"event": "goal_complete", "tasks": len(state["tasks"]), "blocked": len(blocked)})
            return 0

        task["attempts"] = task.get("attempts", 0) + 1
        task["status"] = "running"
        task["started"] = now()
        ikey = idem_key(goal_id, task)
        save(state)
        print(f"[{now()}] -> {task['id']} (attempt {task['attempts']}): {task['goal']}")
        log_append({"event": "dispatch", "task": task["id"], "attempt": task["attempts"], "idempotency_key": ikey})

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
            # Δ1: evaluate the gate PER-TASK — reversibility is a per-task term, so the
            # autonomy decision can differ task to task even within one goal.
            task_mode, task_why = effective_mode(state, task)
            print(f"[{now()}]    ok  verified.")
            log_append({"event": "verified", "task": task["id"], "verify_exit": 0,
                        "verifier_id": verify_cmd, "idempotency_key": ikey, "autonomy_used": task_mode})
            emit_aar(task, vcode, vout, repo, verify_cmd, aar_cfg)   # signed AAR — verified/confirmed
            # 5. AUTONOMY — propose leaves the diff for review; commit proceeds, but ONLY
            #    through the single apply_mutation chokepoint (re-checks gate + Box 0).
            if task_mode == "commit":
                if apply_mutation(repo, task, task_mode, state):
                    print(f"[{now()}]    committed.")
                else:
                    print(f"[{now()}]    commit REFUSED (Box-0 / gate re-check) — diff left in the working tree.")
            else:
                print(f"[{now()}]    (propose mode — {task_why}; diff left for your review)")
        elif task["attempts"] >= max_attempts:
            task["status"] = "blocked"                 # terminal quarantine — surfaced, not re-queued
            task["reason"] = f"verify failed {max_attempts}x"
            log_append({"event": "quarantine", "task": task["id"], "verify_exit": vcode, "idempotency_key": ikey})
            alert(f"task {task['id']} QUARANTINED on {goal_id!r} after {max_attempts} failed verifies — surfaced, not skipped.")
            emit_aar(task, vcode, vout, repo, verify_cmd, aar_cfg)   # signed AAR — rejected/contradicted
            print(f"[{now()}]    BLOCKED after {max_attempts} attempts — surfaced + alerted.")
        else:
            task["status"] = "pending"   # re-queue: a fresh worker, fresh context, tries again
            log_append({"event": "requeue", "task": task["id"], "verify_exit": vcode})
            print(f"[{now()}]    verify failed — re-queueing for a fresh attempt.")

        save(state)
        time.sleep(tick)


if __name__ == "__main__":
    raise SystemExit(main())
