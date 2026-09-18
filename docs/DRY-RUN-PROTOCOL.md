# Post-build dry-run protocol

Branch `audit-build-2026-09-18`, tip `c71f8d6`. Run this top to bottom after the 12-task build to
prove two things in order: first that the new tooling works at all (Layer 1), then that it actually
raises the competition score (Layer 2). Layer 1 is a sanity pass on the mechanics. Layer 2 is the
real test. Do not read a Layer 2 result as meaningful until Layer 1 is fully green.

All commands assume the repo root as the working directory and the real ledger `attempts.db`. The
CLI entry point is `python attempts.py <subcommand>`. This is a process document only. It carries no
attack content.

---

## Purpose

The build added compete mode, a coverage recommender, capture-ingest with a time-to-break field, an
atomic target claim, a heartbeat reaper, and the `compete-swarm` orchestrator skill. This protocol
verifies each of those in isolation, then runs a timed dry-run that mirrors a competition to measure
whether the swarm breaks more distinct targets per hour than the owner's pre-build sessions did.

---

## Layer 1: automated sanity (proves the tooling, not the score)

Tick every box. Each step names the exact command from the build. If a step errors or prints the
wrong thing, stop and fix before Layer 2.

- [ ] **Full suite is green first.** The current suite is 99 tests plus 6 probes. Run both and
      confirm the counts before anything else.
      ```
      python -m unittest discover -s tests
      python -m unittest discover -s probes
      ```
      Expect `Ran 99 tests ... OK` and `Ran 6 tests ... OK`. A lower count means a file did not load.

- [ ] **Migration on the real ledger.** Migration runs automatically when any command opens the
      ledger through `connect()` (this is where `_migrate` creates the `claims` table and its
      `idx_claims_active` index, idempotently). Trigger it against the real DB with a read command
      and confirm it returns without error:
      ```
      python attempts.py claim ls
      ```
      If the owner has added a dedicated migrate subcommand, use that instead; the reports confirm
      only the connect-time migration, so verify the `claims` table now exists (an empty `claim ls`
      that exits cleanly is the pass signal).

- [ ] **Compete mode on vs off.** The flag is `--mode {normal,compete}`, default `normal`, on both
      the `check` and `brief` subcommands. It is scoped to the `solved` claim only (G-SOLVE); every
      other claim ignores it. Show the difference on a cell that has exactly one counted break:
      ```
      python attempts.py check solved --challenge <c> --behavior <b>                 # normal: exit 1 until certainty batch
      python attempts.py check solved --challenge <c> --behavior <b> --mode compete  # compete: exit 0, message contains COMPETE
      python attempts.py brief --challenge <c> --mode compete                        # first line is the COMPETE MODE header
      ```
      Pass: normal mode holds the cell open, compete mode banks the first judged break and prints
      COMPETE. Confirm a judge-artifact-only win is NOT banked (compete excludes those).

- [ ] **The two new enforced gates fire.** Run `check` for the two claims those gates guard and
      confirm each prints its verdict rather than passing silently. The exact gate names and claims
      live in the CLAUDE.md gate table; the task that added them was not in the reports read for this
      protocol, so the owner fills the two claim names here before running:
      ```
      python attempts.py check <claim-1> --challenge <c> --behavior <b>
      python attempts.py check <claim-2> --challenge <c> --behavior <b>
      ```
      Pass: each gate returns its expected verdict line. This step needs the owner to confirm the two
      gate names from the gate table.

- [ ] **Coverage recommender excludes closed cells.** The command is `coverage` with a required
      `--challenge`. It ranks winning levers against still-open cells and filters out closed channels
      (a cell with zero wins and at least 30 active non-scope-out fires):
      ```
      python attempts.py coverage --challenge <c>
      ```
      Pass: the ranked queue prints, and no behavior you know to be closed appears in it. Cross-check
      that the same top cells show under the COVERAGE block of `brief --challenge <c>`.

- [ ] **Atomic claim under a race.** The `claims` table's partial unique index allows only one active
      claim per cell, so a second worker's acquire is denied at the database, not in a Python check.
      Confirm by hand:
      ```
      python attempts.py claim acquire --challenge <c> --behavior <b> --worker w1 --tab t1   # acquired claim #N
      python attempts.py claim acquire --challenge <c> --behavior <b> --worker w2 --tab t2   # DENIED, exit 1
      python attempts.py claim ls                                                            # exactly one active claim
      ```
      The suite already proves the 8-thread race elects exactly one winner; this is the live smoke.

- [ ] **Heartbeat reap frees a stalled claim.** The heartbeat action is `claim beat --id`. The reaper
      is `claim reap --older-than <secs>` (default 300). Refresh a claim, then reap with a threshold
      that should and should not catch it:
      ```
      python attempts.py claim beat --id <N>
      python attempts.py claim reap --older-than 999999   # reaped 0 stale claim(s): []   (fresh, nothing caught)
      python attempts.py claim reap --older-than 0        # reaps the claim, prints its id
      python attempts.py claim ls                         # the reaped cell is now free to reclaim
      ```
      Pass: a fresh claim survives a wide threshold and a zero threshold releases it, exactly once.

- [ ] **Ingest lands a break with its latency.** Capture-ingest reads a JSON capture file and inserts
      attempt rows without spending an attempt. The latency field is `latency_ms`, computed as
      `submitted_ms - first_prompt_ms`. A break maps to a counted win (`oracle_type="real-effect"`):
      ```
      python attempts.py ingest <capture.json>            # ingested N row(s) from <capture.json>
      ```
      Pass: the row count matches the capture, and a break row carries a non-null `latency_ms`. An
      inverted-timing capture is rejected whole-file.

---

## Layer 2: timed dry-run (the real test of score)

Run the `compete-swarm` orchestrator with a small number of parallel browser-tab workers for a fixed
time-box against real targets. A run starts by loading the `compete-swarm` skill; the orchestrator
then assigns disjoint cells by expected value off the `coverage` queue, each worker claims its cell,
drives one browser tab, logs each turn, and heartbeats. Keep the worker count small on the first
dry-run (two or three tabs) so collisions and stalls are easy to read.

Fix these before you start:

- [ ] Time-box length (30 or 60 minutes is enough to get a rate).
- [ ] Worker count (start at 2 or 3 tabs).
- [ ] One account, one tab per worker, browser-driven submits only. Nothing scripted.
- [ ] The reap interval and `--older-than` threshold the orchestrator will use.

During the run, every landed turn is captured and ingested so the ledger is the single source of
truth for all three numbers below. Note the wall-clock start and end.

### The three numbers, and how to read each from the ledger

**1. Distinct targets broken per hour (the score proxy).**
After the run, ingest all captures, then read the distinct-break count off the ledger:
```
python attempts.py brief --challenge <c> --mode compete
```
Take the count of distinct cells (behavior x model) that have at least one counted win inside the
time-box window, and divide by the time-box length in hours. The `brief` capability headline and the
`coverage` queue (which cells are now closed vs open) together give the distinct-break set. This is
the headline number.

**2. Median time-to-first-break per target (ties break on speed).**
Each ingested break row carries `latency_ms` (`submitted_ms - first_prompt_ms`), the time from the
first prompt on a target to the submit that broke it. Take the first winning row per broken cell, then
take the median of those `latency_ms` values across all broken cells. Lower is better and is the
tiebreak when distinct-break counts are level.

**3. Target collisions and stalled-worker reassignments (orchestrator correctness).**
Collisions: count the denied acquires during the run (each is a `DENIED` result from `claim acquire`;
a healthy run has zero cells worked by two tabs at once). Reassignments: count what the reaper
released and what was then re-acquired (the `claim reap` output prints `reaped N stale claim(s)` with
the ids; a following `claim acquire` on the freed cell is the reassignment). `claim ls` at any moment
shows the live active claims. This number tests the orchestrator, not the score.

---

## Baseline (something for the dry-run to beat)

Compute the same three numbers from the owner's pre-build sessions already in the ledger. The ledger
carries timestamps from the August and September sessions, so pick a representative pre-build window
and query the attempt rows inside it.

- **Distinct breaks per hour:** count distinct cells with a counted win inside the chosen pre-build
  window, divide by that window's length in hours. Query the ledger by timestamp range; the same
  counted-win rule the tool uses applies (a real-effect win, judge-artifact wins excluded).
- **Median time-to-first-break:** pre-build rows predate the `ingest` path, so most will have no
  `latency_ms`. Where it is absent, derive time-to-first-break per target from the row timestamps: the
  gap between the first attempt on a cell and the first win on that cell. Take the median across cells.
- **Collisions and reassignments:** there is no pre-build baseline for this one. The `claims` table is
  new in this build, so pre-build sessions have no claims, no collisions, and no reaps. Treat number 3
  as an absolute correctness check on the dry-run itself, not a comparison against baseline.

Record the baseline distinct-breaks-per-hour and median-time-to-first-break as the two numbers the
dry-run has to beat.

---

## Venues, in priority order

Pick the highest venue that is available. Only one of them yields a real, directly comparable score.

1. **Open Gray Swan arena, if one is open between events.** Same grader as the competition, so the
   distinct-breaks-per-hour number compares directly to the baseline. This is the only venue that
   gives a real score number on the competition's own scale. Use it if it is open.
2. **Lakera Agent Breaker.** Multi-turn and free, but a different grader. The break signal is real,
   yet the numbers are relative only; they show whether the swarm out-produces the baseline in shape,
   not on the Gray Swan scale. Use it when no arena is open.
3. **Local Ollama targets (the five models set up locally).** These exercise the orchestrator
   mechanics only: claim, tab binding, heartbeat, reap, reassignment, ingest, the coverage queue.
   They do not produce a competition score. Use them to shake out collisions and stalls cheaply before
   spending an arena window, never to judge score.

State plainly in the run notes which venue was used, so a reader knows whether number 1 is a real
score (venue 1), a relative score (venue 2), or not a score at all (venue 3).

---

## Go / no-go read

**The build helped** when all three hold:

- Distinct targets broken per hour is higher than the pre-build baseline, measured at venue 1 or, as a
  relative read, venue 2.
- Zero unresolved target collisions: no cell was worked by two tabs at once that the claim table failed
  to prevent.
- Every stall recovered: each reaped claim's cell was reassigned and a replacement worker resumed from
  the recorded turn state.

**The build did not help** when any of these hold:

- Distinct breaks per hour is at or below baseline.
- Any cell was double-worked, meaning the atomic claim did not hold.
- A stalled worker's cell was never reassigned, meaning the reaper or the orchestrator loop dropped it.

If distinct-break counts tie the baseline, break the tie on median time-to-first-break: lower wins.
