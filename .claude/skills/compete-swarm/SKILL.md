---
name: compete-swarm
description: Orchestrate several browser-tab workers during a live competition. Use when running the sanctioned browser-controlled bot mode under one account, assigning open cells by expected value, coordinating atomic target claims, per-turn logging, heartbeats, and stall reassignment across parallel workers off the shared ledger.
---

# Compete-swarm: parallel browser-worker orchestrator

This skill coordinates several workers during a live competition run. Each worker drives its own
browser tab on the arena. The shared SQLite ledger (`attempts.py` over `attempts.db`) is the only
shared state. The orchestrator reads that ledger to assign work, and every worker writes back to it
as it goes. Nothing here describes an attack. It describes coordination only: who works which cell,
how each worker records what it did, and how a stalled worker is detected and replaced.

## Rules of the mode (read first)

- The mode is the sanctioned browser-controlled bot mode. The owner confirmed with the Gray Swan
  mods that a browser-controlled bot is allowed and a script is not. A worker acts by driving a
  real browser tab and clicking submit. Nothing bypasses the browser, and no submission is scripted.
- All workers run under one account. Do not open a second account to gain parallelism.
- One tab per worker. A worker drives exactly one browser tab for the life of its claim and never
  touches another worker's tab.

If any of these cannot hold, stop and raise it before starting a run.

## Roles

- Orchestrator: one session that assigns cells, runs the stall reaper on an interval, and keeps the
  expected-value queue current. It does not drive a tab itself.
- Worker: one session bound to one browser tab. It claims a cell, runs the lab flow in that tab,
  logs each turn, and refreshes its heartbeat.

## Assignment by expected value

1. Read the ranked queue of still-open cells from the ledger:
   - `python attempts.py coverage --challenge <c>` for the winning-lever-to-open-cell propagation
     rows, ranked by lever wins then fewest prior fires, with closed channels already excluded.
   - `python attempts.py brief --challenge <c> --mode compete` for the compete-mode session state,
     which prints the compete header and folds in the top coverage rows.
2. Cut the ranked list into disjoint slices, one slice per worker, so no two workers start on the
   same cell. The claim table below is the authority that keeps them disjoint even if a slice
   overlaps by mistake.
3. Hand each worker its slice plus the mode flag (`--mode compete`) and the per-turn plan step it
   should follow (plan backward from the last scored turn, one goal per turn).

## Claiming a cell (worker)

A worker takes a cell only by winning an atomic claim. It never starts work on an unclaimed cell.

1. Open a browser tab and record its tab id.
2. Run `python attempts.py claim acquire --challenge <c> --behavior <b> --worker <id> --tab <tab_id>`.
3. If the acquire returns a claim id, the worker owns the cell and binds this claim to its one tab.
   Proceed.
4. If the acquire is denied (another worker holds the cell), the worker does not work it. It picks
   the next cell in its slice and tries again.

The claim is bound to the tab id from step 1. The worker drives only that tab for as long as it
holds the claim.

## One tab per worker

- Each worker opens exactly one tab, records its id, and binds it to the claim.
- The worker never drives, reads, or submits in another tab.
- When the claim closes, that tab is done. A new cell means a new claim and, if the worker wants a
  clean context, a new tab whose id is recorded on the new claim.

## Per-turn logging and heartbeat (worker)

Log as it happens, never batched at the end, so a crash mid-conversation loses nothing.

After every turn:
1. Capture the result of that single turn.
2. Write it immediately with either:
   - `python attempts.py add --challenge <c> --behavior <b> ... --conversation-id <id> --turn-index <n> --turn-goal "<what this turn establishes>" --latency-ms <ms>`, or
   - `python attempts.py ingest <capture.json>` when the turn result was captured to a JSON file.
   The turn goal is what the turn proves or sets up for the next one, in plain process language,
   never the turn text itself.
3. Refresh the heartbeat: `python attempts.py claim beat --id <claim_id>`.

Because each turn is written the moment it lands and the heartbeat is refreshed right after, a
worker that crashes leaves a complete record up to its last turn and a heartbeat old enough for the
reaper to notice.

## Compete mode

- Workers run the lab flow with `--mode compete`.
- Compete mode banks a cell on the first judged break and defers the certainty batches to a later
  pass, so speed rank is protected. The deferral is scoped to the solved claim only; every other
  gate runs its normal logic.
- Minimize turns to break. Plan backward from the last scored turn and set one goal per turn before
  the first turn.

## Stall handling and reassignment (orchestrator)

A worker can crash, hang, or lose its tab. Its claim would otherwise hold the cell forever. The
orchestrator reaps stalled claims on an interval.

1. On a fixed interval, run `python attempts.py claim reap --older-than <secs>`. This releases every
   active claim whose last activity is older than the threshold and reports the released ids. Pick a
   threshold longer than a normal turn so a slow-but-live worker is not reaped.
2. A reaped claim's cell is now free. Put it back at the front of the expected-value queue.
3. Assign the freed cell to a worker that has capacity. That worker acquires a fresh claim on it.
4. The replacement resumes from the recorded turn state: it reads the cell's logged turns and turn
   goals from the ledger and continues from the last turn that was written, rather than starting the
   conversation over.

## Collision safety

- The atomic claim is the only assignment authority. A cell is worked only by the worker that holds
  its active claim.
- Never assign a cell to two workers by any side channel. If two workers both target a cell, the
  claim acquire lets exactly one win and denies the other; the denied worker moves on.
- The orchestrator's queue slices are a convenience to spread work. The claim table is the guarantee.

## Close-out

- On a break: run `python attempts.py claim release --id <claim_id>`, mark the cell result in the
  ledger, then pull the next expected-value cell and acquire a claim on it.
- On a denied or exhausted slice: request the next slice from the orchestrator's queue.
- At session end: every worker releases all claims it still holds with `claim release --id <id>`, so
  no cell is left locked. The orchestrator confirms with `python attempts.py claim ls` that no active
  claim remains for a worker that has stopped.

## Command reference

- `attempts.py coverage --challenge <c>` - ranked open-cell propagation queue.
- `attempts.py brief --challenge <c> --mode compete` - compete-mode session state.
- `attempts.py claim acquire --challenge <c> --behavior <b> --worker <id> --tab <tab_id>` - win a cell.
- `attempts.py claim beat --id <claim_id>` - refresh the heartbeat.
- `attempts.py claim reap --older-than <secs>` - release stalled claims, returns freed ids.
- `attempts.py claim release --id <claim_id>` - close a claim.
- `attempts.py claim ls` - list active claims.
- `attempts.py add ... --conversation-id <id> --turn-index <n> --turn-goal "<...>" --latency-ms <ms>` - log one turn.
- `attempts.py ingest <capture.json>` - log a captured turn result.
