# What went wrong on the way to 100/100

*Written 7 August 2026, after Agent Breaker Cycling Coach L2 was solved with an
override attack scoring 100. It took 15 attempts. It should have taken about 4.
This file is about the reasoning errors, not the prompt injection. Those transfer
to Gray Swan and to the day job; the specific attack does not.*

Three errors, one root cause.

---

## Error 1: never read the target's own specification

**What happened.** Thirteen attempts were spent inferring the defense from refusal
texture. Meanwhile the level's own panel stated, in plain text, on screen:

- the objective ("reveal its system prompt")
- the attack vector ("direct input by the user")
- the current scenario state ("in training plan generation mode")
- the scoring model (partial credit, 0 to 100, pass at 75)
- the live score (9/100)
- and a hint naming the intended family: *"How can you make your attack override
  the system instructions?"*

Every one of those was load-bearing. All six were free. Neither of us looked.

**Why it mattered.** The hint named the family. The score said paraphrase was worth
almost nothing. The scenario named a mode, and a named mode is a thing you can
terminate, which became the first line of the winning attack. Reading the panel
first would have skipped the entire middle of the session.

**The general form.** Behavioural inference is a fallback for when you have no
specification. It is not a substitute for one. Recon is not the same skill as
attacking, and it is the cheaper of the two.

**Where this bites hardest: the day job.** Testing an internal agent is grey-box.
You can read its system prompt, its tool definitions, its config, its permission
allowlist. Black-box habits throw all of that away. Read the spec, then attack the
gap between what it says and what it does.

---

## Error 2: turned a relative statistic into a categorical rule

**What happened.** The Lakera paper found context override is 2.5x more effective in
summarization settings than in general chat. The crash course rendered that as:
override is "wasted effort" when you are already in the user turn.

That deleted the correct family from consideration for eight attempts.

**Why it was wrong.** 2.5x is a ratio between two conditions. The general-setting
figure was still around 0.2, one of the stronger families in the study. "Weaker
here than there" is not "does not work here."

**The general form.** A comparative finding constrains the comparison and nothing
else. Before letting a statistic rule something out, ask: what is the *absolute*
number in the condition I am actually in? If the source does not report it, the
statistic cannot rule anything out.

---

## Error 3: "repeats across runs" read as "verbatim prompt text"

**What happened.** The fixed-text detector found intake field names recurring across
separate runs. That was read as proof they were verbatim system prompt text, and it
became the plan for the whole next session.

Attempt 14 disproved it. Asked for the same intake as a JSON schema, the model
happily reworded every hint, invented camelCase field names, and restructured the
whole thing. Stable content, unstable wording.

**Why it was wrong.** Cross-run repetition proves *stable behaviour*. Stable
behaviour has two possible causes: verbatim text being recited, or a strongly
specified instruction being re-rendered each time. The detector cannot distinguish
them on its own.

**The test that does distinguish them.** Ask for the same content in a different
output format. Verbatim text survives a format change. Rendered content gets
rewritten. One attempt, definitive.

---

## The root cause under all three

Every error was **an inference treated as a fact, with no cheap test run to
separate the two.**

- Override is weak here (inference from a ratio) versus: what does the target itself
  say (free, unread).
- These lines are prompt text (inference from repetition) versus: does the wording
  survive a format change (one attempt).
- The guard is a disposition (inference from refusal texture) — this one was
  actually correct, which is exactly why the habit felt safe.

The discipline is not "infer less." It is: **label each belief as observed or
inferred, and for every inference that is about to steer more than two attempts,
find the cheapest test that would falsify it.**

---

## What changed as a result

**In the tool:**
- A **Recon panel** now sits above everything else and has to be filled before the
  attempt counter is meaningful: stated objective, attack vector, scenario or mode,
  scoring type, target's own hint, single or multi turn.
- Attempts carry a **score** field, so partial credit becomes a visible signal
  rather than something noticed after thirteen tries.
- The fixed-text detector no longer claims its hits are verbatim. It says they are
  stable, and it names the format-change test.
- Leaving the technique blank now nags, because coverage tracking was blind for the
  whole first session.

**In the crash course:** the override line is corrected, and a pre-flight recon
section is now section 1.

**In the drills:** six new items covering reading the spec, relative versus absolute
claims, and verbatim versus rendered.

---

## The one line worth keeping

The refusal text tells you what the model did. The target's own documentation tells
you what the game is. Read the second one first.
