# Redteam Lab

A practice instrument for learning LLM red-teaming, and a logbook that turns
practice into a dataset.

Built for [Gandalf: Agent Breaker](https://play.lakera.ai/agent-breaker) (Lakera's
teaching game), [Gray Swan's Arena](https://app.grayswan.ai/arena) (a sanctioned
competition platform), and internal AI agents you are authorized to test.

## Defensive output

This is a blue-team project: the red-teaming is practice, and every break is
written up as a control a deployer can act on. The findings so far:

- [EVAL-0001](evals/EVAL-0001-mcp-tool-poisoning-assurance.md) - flagship measured assurance report (MCP tool-description poisoning, with reliability).
- [EVAL-0002](evals/EVAL-0002-approval-bypass-prereg.md) - approval-bypass pre-registration (predictions locked before firing).
- [Defense playbook](learn/harvest/grayswan-luckybreak-defense-playbook.md) and [defense synthesis](learn/harvest/grayswan-luckybreak-defense-synthesis.md) - blue-team deliverables from the Gray Swan agent-IPI cells.

**Start with [CRASH-COURSE.md](CRASH-COURSE.md).** The tool is the instrument, the
crash course is the actual content.

**Then read [LESSONS.md](LESSONS.md).** It is the post-mortem on the first session:
three reasoning errors that turned a four-attempt level into a fifteen-attempt one,
what they have in common, and what changed in the tool as a result. It transfers to
Gray Swan and to the day job in a way the technique library does not.

## Status

| | |
|---|---|
| First solve | Agent Breaker, Cycling Coach L2, **100/100**, 7 August 2026 |
| Winning family | Instruction override: mode termination, counterfeit system boundary, precedence claim, prefix injection |
| Attempts | 15, of which roughly 11 were spent in the wrong family |

## Why this exists

Most people learn prompt injection by trial and error, keep nothing, and end up
with a folder of screenshots. The published criticism of the field is exactly
that: attacks reported as one-off anecdotes, no protocol, no failure rate, no
reproducibility.

This is the opposite bet. Learn the technique families and *why* they work, then
log every attempt including the failures, with enough structure that the log is
later usable as evidence rather than memory.

## Running it

Two entry points. The logbook has no build step, no dependencies and no network
calls; open it beside whatever you are attacking by hand.

```bash
start index.html
```

The attacker loop is a single Node file, also with no dependencies, but it does
reach the network: Lakera's API and your local Claude Code seat. Node 22+.

**Double-click `Play Gandalf.bat`.** It opens a menu: play a level by hand, run the
attacker loop, status, report. Everything you type is read by Node rather than by
the shell, so an attack prompt can contain quotes, `%`, `&`, `|` and newlines
without being mangled on the way in. Multi-line prompts are finished with a line
containing only `END`.

The subcommands are there if you prefer a terminal:

```bash
node gandalf.mjs status
```

## Layout

| Tab | What it is |
|---|---|
| **Session** | The whole loop on one screen. Header with a frame budget counter, the attempt form, and the outcome grid that doubles as the router: click what came back and the next move appears underneath, with the technique families to try. Counts and history sit beside it |
| **Drill** | 50 diagnosis items across five skills: classify the outcome, identify the defense layer, pick the next move, spot the invariant, compose the attack. Ten minutes before a session. Items you get wrong come back sooner |
| **Techniques** | 22 technique cards. *Why it works* is the field that matters; defense and sources fold behind a toggle so they do not eat the screen mid-session. Each card is tagged objective, wrapper, modifier, amplifier or channel, because real attacks are a composition |
| **Wins** | Frozen working strings plus a harvest checklist per target |

Keyboard: `1`-`4` switch tabs, `/` focuses search, `Ctrl`+`Enter` saves an attempt. In a drill, `1`-`5` answers and `Enter` advances.

## The attacker loop: `gandalf.mjs`

The logbook is for targets you attack by hand. `gandalf.mjs` is the other half: a
best-of-N attacker loop against [Gandalf](https://gandalf.lakera.ai), Lakera's
eight-level ladder. Zero dependencies, no API key, runs on your Claude Code seat.

```bash
node gandalf.mjs status
node gandalf.mjs manual 3 "your own attack, written by you, first"
node gandalf.mjs auto 3 --n 12 --rounds 3
node gandalf.mjs report
node gandalf.mjs rescore
```

Three parts. The middle one is why it exists.

| | |
|---|---|
| **Target** | `gandalf-api.lakera.ai`, eight defenders. The UI retired in July 2026; the API did not |
| **Judge** | The game's own `guess-password` endpoint. Exact boolean, instant, no human reading required |
| **Attacker** | Your Claude Code seat via `claude -p`, writing batches of candidate prompts and reading the failures before the next batch |

**The judge is the piece a browser target cannot give you.** It turns "did that
work" from a reading exercise into a number, which is the only thing that makes
volume worth anything.

**But a judge is only as good as what you hand it.** The first version scraped
replies for bare uppercase words. On level 3, whose only defense is an output
filter doing literal string matching, a password never arrives as itself: it
arrives dashed, spaced, spelled, phonetic, reversed or rotated. The scraper found
the noise beside the answer (`NATO`, `PHONETIC`, `LENGTH`) and walked past
`W-A-V-E-L-E-N-G-T-H` sitting in the same reply, so a first-attempt win was logged
as `deflection`. Fifteen of thirty-eight replies contained the password and the
harness scored zero.

The extractor now decodes separators, NATO phonetic, single-letter lists, ROT13
and reversal before guessing, and the model-read pass runs on any round without a
win rather than only when the scraper came up empty. `rescore` re-runs the current
extractor over replies already on disk and asks the judge about anything new, so a
leak an older version walked past is still recoverable. That is the general lesson
and it is worth more than the level: **a null result from an instrument you have
not tested is not evidence.**

**`auto` refuses to run on a level until you have attempted it by hand.** Your one
guess against the machine's hundred is the measurement, and it is the part that
teaches. `--skip-gate` exists and you should feel slightly bad using it.

Every attempt lands in `logs/gandalf-<date>.jsonl` alongside the manual log. Rows
carry the level, defender, technique, prompt, verbatim response, extracted
candidates, and whether the judge accepted. `report` prints attempts and wins per
level and per technique, and **hides rates below n=20** for the same reason the
logbook does. Auto rows are marked `auto_classified`, because the outcome label on
them is the machine's guess and never your diagnosis.

The eight levels are a defense ladder you can measure rather than read about: no
defense, then a system-prompt disposition, then an output filter, then a GPT output
guard, then an input blacklist, then a GPT input guard, then all of it, then all of
it hardened. Which technique family survives which layer, with a number next to it,
is the finding this produces and the technique library does not.

Lakera published Gandalf to be attacked. The script caps concurrency at 3 and
staggers requests; read their terms before turning `--rounds` up.

## The drill bank

58 items were drafted, then reviewed by three independent critics working blind to
each other: one hunting items with more than one defensible answer, one judging
whether the bank builds transferable skill or just pattern-matching, one judging
whether the stimuli read like real 2026 model output.

Eight items were cut outright (broken stimuli, or one that taught a habit that
loses at a real table). Twenty were repaired, mostly to normalise a discriminator
that three items had stated three incompatible ways. Fifty shipped.

Known gaps the reviewers named and the bank does not yet cover: reading a
trajectory across turns rather than a single reply, targets that emit a visible
reasoning trace, and "who authored this refusal, the model or a guard", which is
the master discriminator behind half the outcome classes.

## The three design decisions worth knowing

**The outcome field is not pass/fail.** It has eight classes, because the routing
loop keys off the difference between a hard refusal (frame is dead, change family)
and a soft refusal (frame is nearly right, edit one clause), and between the model
refusing and a judge rejecting output the model was happy to produce. Those demand
opposite next moves. Binary logging throws that away.

**Rates stay hidden below n=20.** At a roughly 3% base rate, one hit in six
displayed as "17% success" is noise rendered as insight, and it will talk you out
of a good technique family after two unlucky rolls. Counts are always shown.

**Every row carries a disclosure class.** Gandalf and Agent Breaker are public.
Gray Swan's rules forbid sharing breaks. Employer testing is confidential. Export
defaults to public rows only, and asks before including anything else. One enum
field prevents handing a co-author a file with company-confidential rows in it.

## Your data

`localStorage` under `redteam-lab-v2`. It never leaves the browser, which also
means it is one cleared cache away from gone. The header shows unexported rows and
the page warns on close past 15. **Export regularly.** JSONL is the primary format;
a full JSON backup comes with it.

Attempt schema: `schema`, `id`, `ts` (UTC ISO), `target`, `goal`, `hypothesis`,
`k` (attempt index within the frame), `budget`, `delta` (the one thing you
changed), `technique`, `prompt`, `outcome`, `signature` (the refusal phrase or cut
point), `predicted`, `disclosure`, and `response` on partials and wins.

## Scope

Targets you own or are explicitly authorized to test. Gandalf is published by a
security company to teach this. Gray Swan's challenges are sponsored by the labs
whose models are the targets, and they want the findings. Internal tools are your
employer's call, not yours alone.

Open-source agent runtimes you build and run locally (DeepSeek Harness is the
first, see `evals/EVAL-0002`) are in scope as software you control your own copy
of: assessed on your own hardware against a local model, touching no hosted service.
Known vulnerability classes shown locally are publishable as lessons; a novel
exploitable defect goes to the vendor first under coordinated disclosure, per the
paragraph below.

Gray Swan's rules draw the line at automation, not at assistance. AI help crafting
prompts and developing strategies is **explicitly allowed**; automated scripts or
bots that generate or submit jailbreaks are not, and submissions must be made
manually through their platform. Two more that matter: one account per person, and
submissions stay private until 30 days after the event.

So `index.html` stays a reference library and a logbook, with every prompt written
and submitted by you, because Gray Swan submissions must be manual. That is the
only reason it is manual. `gandalf.mjs` automates the whole loop and is pointed at
a target published to be attacked, where no such rule applies. Read the per-challenge
rules yourself before entering anything.

If you find something real in a commercial product, disclose it properly.
Anthropic acknowledges within 3 business days and targets a 90-day window.
Publishing ahead of a vendor is how you burn the reputation this is meant to build.
