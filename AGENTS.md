# AGENTS.md — for AI agents & contributors

You're working on **machine-driver** — the deliberately-dumb, tireless loop that drives
long-running *code* work to a verified definition of done without a human in the loop. It is
**Box 2 (the Dumb Driver)** of [The Machine](https://github.com/frontier-infra), and the
*code-work* sibling of [Conductor](https://github.com/frontier-infra/conductor) (the ops
driver). This file gets you productive fast.

## Read in this order
1. `README.md` — the loop, how to run it, and the planned iteration-2 (signed AAR).
2. `CONFORMANCE.md` — how each of the 6 boxes maps to the code, and the current honest level.
3. `driver.py` — the whole driver (~160 lines, stdlib-only, Python ≥ 3.7).
4. `goal.example.json` — the contract + budget + tasks schema you point it at.
5. `tests/test_driver.py` — the conformance smoke tests.

## Verify it works (30 seconds)
```bash
python3 -m unittest discover -s tests -v
# happy-path emits AAR + completes · governor halts (exit 2) · no-verifier ⇒ propose · hash-chain intact
```

## The one rule: keep the driver dumb
The driver spends **zero model tokens**. The only judgment in the whole system lives on the
far side of the `worker_cmd` shell-out. If you find yourself wanting the *driver* to reason —
to "decide" the next task, to "judge" whether a diff is good — stop. That judgment belongs in
the worker (do the work) or the verifier (`verify_cmd`, ground truth), never the control plane.
The thing that runs for weeks cannot be the thing that occasionally decides a bug isn't a big deal.

## How to contribute
- **Every change keeps `tests/` green** before you commit. New behavior ships a new test.
- **No model in the loop.** Task *selection* stays deterministic (`next_pending`, or a rules
  table) — never an LLM call inside `main()`.
- **Fail closed.** No verifier ⇒ propose-only. Unratified contract ⇒ propose-only. A budget
  breach halts; it never "tries once more."
- **Verifier ≠ worker, always.** The thing that grades the work is never the thing that did it.
- **Be honest about limits.** This is a reference skeleton; the README/CONFORMANCE say plainly
  what's enforced vs. planned. Don't oversell it.

## What this is NOT
Not a coding agent (that's the worker you plug in). Not a verifier (that's `verify_cmd` / your
tests / AVL). Not an orchestration framework — it's ~160 lines of boring, resumable control flow.
The point is that it's *boring*.

MIT.
