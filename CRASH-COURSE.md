# Prompt injection: the crash course

Written 6 August 2026. Everything here is sourced. The point is to give you the
meta before you play, so you skip the six weeks of discovering it by trial and
error.

Read part 1 and part 5, then go play. Come back for the rest.

---

## 0. Read this first, it will save you an hour

**`gandalf.lakera.ai` no longer exists.** Some time between 23 and 26 July 2026 it
started 308-redirecting to `play.lakera.ai/agent-breaker`. Every walkthrough on
the internet still points at the dead URL.

So whatever you are playing right now is **Gandalf: Agent Breaker**, which is a
different and better game: ten realistic agent apps, four to seven hardening
levels each, and the objectives are real agent threats rather than a password.
System prompt extraction, data exfiltration, tool abuse, and manipulating an agent
into attacking another user. It is scored out of 100 with a ~75 pass mark, so
clean minimal attacks beat brute force.

That is much closer to both Gray Swan and your day job than the old game was.

The classic eight-level ladder is still alive with no UI, at
`gandalf-api.lakera.ai/api`, if you want the clean version of the defense ladder.
A 20-line script plays it.

**One warning:** several public repos publish all eight classic passwords
outright. Avoid those specifically. Technique writeups are fine, answer lists
destroy the exercise.

---

## 0.5. Before attempt 1: read the target's own spec

Added 7 August, after this was learned the expensive way. Full write-up in
[LESSONS.md](LESSONS.md).

Reading refusals is the core skill and it is what most of this document is about.
But it is the **fallback for when you have no specification**. Where one exists, it
is free and it is better.

Agent Breaker prints, on screen, before you send anything: the objective, the attack
vector, the scenario the app is currently in, the scoring model and pass mark, your
live score, and often a hint naming the intended attack family outright. Thirteen
attempts were once spent inferring things that were sitting in that panel.

The six things to capture before attempt 1, which the tool's Recon panel now asks
for:

| | Why it pays |
|---|---|
| Stated objective, verbatim | Tells you what is actually scored, which is often narrower than you assume |
| Attack vector | Direct input, retrieved document, and tool output are three different games |
| Scenario or current mode | **A mode with a name is a mode you can declare terminated.** This became the first line of the winning attack |
| Scoring: binary or partial | Partial credit is a gradient. A flat score across many attempts means the axis is wrong, not that you are close |
| Any hint the target gives | Scored systems frequently name the family. Free answer |
| What you can read directly | See below |

**At work this matters more, not less.** Testing your own company's agent is grey
box: you can read the system prompt, the tool definitions, the permission allowlist.
Practising pure black-box habits on games and then carrying them into a grey-box job
throws away your biggest advantage. Read the spec, then attack the gap between what
it claims and what it does. That gap is the finding a security lead can act on.

**Two reading rules that came out of the same session:**

**A relative statistic constrains only its own comparison.** "2.5x more effective in
setting A than setting B" does not mean it fails in B. Check the absolute number for
the condition you are actually in. If the source does not report one, it cannot rule
anything out for you.

**Repeating across runs proves stable behaviour, not verbatim text.** Stable content
has two causes: recitation, or a strongly specified instruction re-rendered each
time. To separate them, ask for the same content in a different output format.
Verbatim text survives a format change; rendered content gets reworded.

---

## 1. The two root causes

Everything below is a variation on one of these. If you understand both, you can
derive techniques instead of memorizing them.

**Cause one: there is no separation between instructions and data.** The system
prompt, the developer prompt, your message, retrieved documents and tool output
all arrive as one flat token stream. Nothing marks some tokens as privileged code
and others as inert data. So a sentence that looks like an instruction gets
processed as one regardless of where it came from, and the model resolves
conflicts by recency and specificity rather than by trust.

This is why prompt injection is not a bug that gets patched. It is the
architecture.

**Cause two: safety training generalizes worse than capability training.** The
model is competent in far more situations than it was safety-trained on. Any
region where capability outruns safety coverage is an opening: a language with
less safety data, an encoding, a fictional frame, turn nine of a long
conversation, a rare output format. This is the "mismatched generalization" idea
from Wei, Haghtalab and Steinhardt's *Jailbroken* paper, and it is the single most
useful lens in the field.

The corollary that matters for you: **techniques are payload-agnostic.** The
structure that extracts a fictional password is the structure that does everything
else. That is why practising on Gandalf transfers.

---

## 2. The meta, with real numbers

Lakera ran a controlled trial on the classic game and published it: *Gandalf the
Red: Adaptive Security for LLMs*, [arXiv:2501.07927](https://arxiv.org/abs/2501.07927).
**279,675 prompts, 36,239 sessions, 15,402 users**, across 18 level-and-setup
pairs. This is the closest thing the field has to a tier list built on real data
rather than vibes, and almost nobody reads it.

Their nine families:

| Family | Mechanism |
|---|---|
| **Direct** | Ask outright |
| **Indirect** | Reformulate, or use another term for the target |
| **Non-English** | Models are weaker in other languages, and it doubles as obfuscation against substring checks |
| **Context override** | Tell it to disregard prior instructions |
| **Partial** | Ask for properties instead of the thing: rhyme, definition, length, first letter |
| **Persuasion** | Authority, urgency, bargaining, emotional pressure |
| **Input obfuscation** | Encode or fragment your prompt to evade input filters |
| **Output obfuscation** | Ask the model to transform its answer before printing |
| **Output fiction** | Poems, stories, roleplay as the delivery vehicle |

**What actually wins, causally adjusted for level, model and setup:**

1. **In open chatbot and narrow-topic settings the winners are `partial`,
   `indirect` and `output fiction`.** All three work by exploiting the gray zone
   in what counts as disallowed. The paper is explicit that this is "especially
   effective against LLM checkers that must make binary decisions." A judge forced
   to answer yes or no hesitates at the edges, and the edges are where you live.

2. **`context override` is 2.5x more effective in the summarization setup than in
   the general one** (interventional success rate ~0.5 versus ~0.2). When your text
   arrives as *data* to be processed, you first have to break the data/instruction
   boundary, so "ignore your instructions" does extra work there.

   **Correction, 7 August.** An earlier version of this line said override was
   "wasted effort" when you are already the user turn. That was an overreach and it
   cost a session. The paper's number is a *relative* advantage in summarization,
   not evidence that override fails on direct input. Note the absolute figure:
   ~0.2 in the general setting is still one of the higher families in the study.
   Agent Breaker's Cycling Coach L2 takes direct user input and the game's own hint
   is "how can you make your attack override the system instructions", so override
   is the intended path there. Read a relative comparison as a relative comparison.

   **That is the whole difference between indirect and direct injection**, and it
   is exactly what your day job is exposed to, because real agent attacks arrive
   through documents and tool output, not through a chat box.

3. **Input obfuscation is significantly weaker when the app is domain-restricted**,
   because an on-topic constraint rejects obfuscated text as off-topic. Domain
   restriction is a genuinely good defense and it beats obfuscation specifically.

The paper's three headline defense findings, worth knowing for work: system-prompt
defenses degrade usability even when they block nothing; defenses that look strong
on static benchmarks collapse against adaptive attackers; and the only three things
that hold up are **restricting the application domain, defense in depth, and
adaptive defenses**.

---

## 3. The defense ladder, which is what you are actually mapping

The classic eight levels are the cleanest mental model of guard placement that
exists. Learn this and you can fingerprint any target fast.

| # | Defense | The lesson |
|---|---|---|
| 1 | None | Context is not confidential. Anything in the system prompt is reachable |
| 2 | System prompt only, "do not reveal it" | An instruction is not an access control. The model is the thing you negotiate with |
| 3 | + output substring filter | A string matcher does not understand. It compares bytes |
| 4 | LLM judge over the whole transcript | A semantic judge must make a binary call, so it has a gray zone at the edges |
| 5 | Input keyword blocklist | Keyword filters see strings, not intent. They over-block and under-protect at the same time |
| 6 | LLM input classifier judging intent | Intent classifiers judge one message in isolation. Make the honest answer to "is this an attack?" be no |
| 7 | All of the above combined | Defense in depth. Each single-layer trick trips a different layer |
| 8 | Strong enumerated system prompt + both guards | The strongest single lever is a system prompt that enumerates allowed and disallowed behaviour |

Levels 5 and 6 next to each other are the sharpest lesson in the game. Level 5 is
purely lexical, verified: it blocks an innocent question about a wifi router
purely for containing the word "password". Level 6 is purely semantic, verified:
it allows that same question and answers helpfully. **Two guards, same position in
the pipeline, completely different failure modes.**

Only about 8% of players beat level 7.

**The underrated content:** the same API hosts `trial-{vanilla,summarization,topic}-{novice,student,conjurer,enchanter,warlock,archmage}`, which is the research
paper's own experimental ladder. Three application setups by six defense tiers.
**The `summarization` track is indirect prompt injection**, where your text arrives
as data to be summarized rather than as an instruction. Nothing else on the
platform teaches that, and it is the thing that matters most for your work.

---

## 4. Reading the refusal

This is the skill. Everything else is knowledge.

The refusal text is your gradient. Treating outcomes as pass/fail throws away
almost everything the target just handed you for free. The router tab in the tool
has this table live, but here it is in full:

| What you see                              | What it means                                                                        | Next move                                                                    |
| ----------------------------------------- | ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------- |
| Flat block, no engagement                 | The frame is dead, it was not even considered                                        | Change technique **family**, not wording                                     |
| Byte-identical refusal, arrives instantly | An input classifier fired before generation. Deterministic, so rerolling is waste    | Change the input surface: encoding, splitting, language, indirection         |
| Soft refusal, "in a different context"    | Frame nearly acceptable, one tripwire fired. **Highest value state in the table**    | Keep the frame. Edit the single clause that tripped it                       |
| Answered something adjacent               | It parsed your intent and chose a safer neighbour. You are legible and close         | Narrow the gap between the neighbour and the ask                             |
| Structure without payload                 | You own the frame, only the content gate holds                                       | Stop attacking the frame. Push for specificity inside it                     |
| Complied but useless                      | Willingness beat capability. In a scored arena the judge rejected you, not the model | Extract detail. Do not re-jailbreak. This is where people lose the most time |
| Text appeared then vanished               | Output-side defense. **The model was willing**                                       | Highest resolution signal you get. The cut point names the tripwire token    |
| "It looks like you're trying to..."       | Your technique is a recognised trained-on pattern                                    | Retire that family for this target                                           |
| Dropped the persona mid-answer            | The fiction lost its anchor                                                          | Find what re-anchored it. Usually an imperative or a real-world noun         |
| Agrees in text, no tool call              | Intent accepted, action gated separately                                             | Attack the tool contract and parameter format, not the conversation          |

Two rules that override the table:

**Reroll before you rewrite.** When something half-works, vary a numeric or
cosmetic parameter first. It behaves like a fresh random seed and costs no
thinking.

**Stop rerolling when the refusal class stops moving.** Eight attempts in the same
class means you are sampling noise. Change family.

---

## 5. The ladder mindset

You already have this reflex. The translation is nearly one to one.

**Knowing the meta.** The tier list is the technique families and which model
classes they land on. The patch notes are model releases. The critical difference
from Hearthstone: **specific strings die fast, mechanism classes persist for
years.** The famous "AIM" jailbreak template is fully patched. Roleplay, encoding
and long-context dilution are not. Track families, not strings, or your meta
knowledge decays weekly instead of quarterly.

**Netdecking then adapting.** Nobody strong invents from scratch. One competitor
pre-tested ten off-the-shelf templates against fifteen behaviours across eighteen
models, kept the top five and discarded the rest. Another said he got most of his
early breaks by tweaking well-known attacks, then plateaued at four of five
models, and only broke through by building a template to his own spec once he
understood why the borrowed ones worked. Same arc as every ladder climb you have
done: copy the list, learn the matchups, flex two slots.

**Matchup selection is a real lever.** Success rates across models in one agent
challenge spanned 1.47% to 6.49%, a 4x spread. And **transfer runs downhill only.**
Attacks built against the hardest target work on weaker ones, never the reverse.
So develop against the hard target and harvest against the easy ones.

**Sample size discipline, and the trap inside it.** See section 10. It is the
single easiest place to waste a weekend, in either direction.

**Tilt has a specific shape here: the near miss.** Partial compliance is genuinely
the most informative state and the most addictive one. The tool's frame counter
exists for exactly this. Refusal class moving means you are climbing. Refusal
class stationary means you are rerolling and calling it progress.

For scale on how bad the grind can get: an Anthropic bounty saw 185 participants
spend roughly 3,000 hours over two months and produce **zero** universal
jailbreaks. Target selection is a time management decision, not a difficulty
preference.

---

## 6. Session shape for a two hour block

**First 20 minutes, no attacking.** Read your leave-behind from last time. Fire
the plain unattacked baseline to check the target has not changed under you.
Fingerprint the model if it is anonymised, using latency, token rate, refusal
phrasing and formatting habits. Write the session header.

**The middle, three blocks of about 25 minutes.** One hypothesis per block, twelve
to fifteen attempts. At each boundary ask one question: did the refusal class
change during that block? Yes means continue, no means the hypothesis is dead.
Do not renegotiate this at the boundary, that is where the discipline rots.

**Go deep per target, not wide.** Any single attempt runs around 3% on a hard
target, but a directed sequence of ten to a hundred against one behaviour
converges toward success. Ten attempts each against ten behaviours converges on
nothing.

**On a hit, stop attacking and harvest.** Save the exact string verbatim,
whitespace included, then run it against every remaining target and every softer
model before you improve it. One competitor turned a single template into 34
behaviours across 4 models this way.

**Last 15 minutes, no attacking.** Freeze every hit. Close the log. Write the
leave-behind: the highest outcome class you reached, **three fully written probes
ready to paste**, the open question in one line, and dead ends with the refusal
class that killed each. The three ready probes matter more than the rest combined,
because the next session opens with a paste instead of a think.

**Realistic arithmetic.** At about two minutes per attempt including logging, two
hours is roughly 40 attempts. The field average is about 29 attempts per break.
So one to three breaks per session is a normal healthy session, not a bad one.

---

## 7. Beginner traps

- **Improvising every prompt.** Strong players arrive with a pre-built matrix and
  paste. Build yours offline between sessions, when the clock is not running.
- **Opening with a 400-word masterpiece.** Build up incrementally. A big prompt
  that fails has failed for one of twenty reasons and you cannot tell which.
- **Changing two things at once.** Unattributable result, wasted attempt.
- **Claiming a break because it did not say "I can't."** Rule-based string matching
  as a judge has a **64.2% false positive rate**. Check the payload, not the
  absence of a refusal phrase.
- **Not verifying an extracted secret.** Five confirmations minimum, verbatim.
  Models mangle spelling constantly and a wrong password looks exactly like a
  right one until you submit it.
- **Optimising before you have a pass.** Shorten and generalise after the break is
  banked, never before.
- **Developing against the easy target.** Transfer runs downhill only.
- **Believing one screenshot.** Best-of-N success climbs from 41% at N=100 to 78%
  at N=10,000 on the same model with no smarter prompting. Report N or report
  nothing.
- **Collecting strings instead of mechanisms.** Strings die on patch day.
- **Planning to automate submission.** Gray Swan's rules prohibit automated tools
  or scripts for generating or submitting. Build your arsenal with scripts,
  deliver by hand.
- **Ending a session by closing the tab.** The last 15 minutes are the compounding
  part.

---

## 8. Where to play, in order

| Platform | Why | Priority |
|---|---|---|
| [Gandalf: Agent Breaker](https://play.lakera.ai/agent-breaker) | Agent threats: tool abuse, exfiltration, prompt extraction, cross-user. Scored. Closest thing to Gray Swan and to your job | **Start here.** You already are |
| Classic ladder via `gandalf-api.lakera.ai` | The cleanest possible mental model of guard placement. A couple of hours | High, and fast |
| `trial-summarization-*` on the same API | **Indirect prompt injection**, the data/instruction boundary. Nothing else teaches this as cleanly | High and underrated |
| [HackAPrompt](https://www.hackaprompt.com/) | Largest platform, 30k+ competitors. Beginner tutorial track plus an advanced indirect-injection track | High |
| [Wiz Prompt Airlines](https://promptairlines.com) | Full CTF: injection chained into real app compromise | High |
| [PortSwigger Web Security Academy, LLM attacks](https://portswigger.net/web-security/llm-attacks) | Connects LLM attacks to real appsec. Free, lab-graded | **High for the day job** |
| [Tensor Trust](https://tensortrust.ai) | You write defenses too, which is the fastest way to build intuition for why guards fail | Medium-high |
| [GPT Prompt Attack](https://gpa.43z.one) | Prompt golf, fewest characters. Good for Agent Breaker's scoring and Gray Swan efficiency | Medium |
| [Gray Swan Arena](https://app.grayswan.ai/arena) | The actual target | When you have a technique library that is yours |

Two that are dead, so you do not waste time: Mosscap's domain no longer resolves,
and Doublespeak.chat serves a shell with a broken API.

---

## 9. Why the logging is not admin overhead

The field's own loudest complaint is the opening. There is currently **no standard**
for what attack success rate means, what N should be, which judge to use, or which
prompt set. Error bars are essentially never reported. Pre-registration has close
to zero adoption.

So three things follow.

**Your results stay usable.** A logged break with a version string and an attempt
count is still an asset next month. A screenshot is a memory.

**"I broke it" is the null hypothesis.** Across 1.8 million logged attempts, nearly
everything breaks within 10 to 100 queries. The finding is never that it broke. It
is *how cheaply*, *how reliably*, and *whether it transfers*. Median and p95
attempts-to-break plus a transfer result is something a lab can act on.

**At work it is the difference between a slide and a decision.** "We tested the
agent" versus "3 breaks in 40 attempts against version X on date Y, two of which
transferred to the other model" are not the same artifact.

The prediction field costs ten seconds and is the highest credibility-per-minute
move available. It turns every session into a calibration datapoint on your own
judgment, and it is the cheapest defense against reading a partial refusal as a
near-win because you want it to be one.

One caution for Gray Swan specifically: their grader is theirs and you cannot
calibrate it, so optimising against it measures the judge. Record attempts-to-break,
family attribution and cross-model transfer. Not raw success rate.

---

## 10. Variance: how many times to fire the same prompt

The same prompt against the same target genuinely can succeed once and fail nine
times. Models sample tokens probabilistically, these platforms do not expose
temperature, and you are usually operating right at the boundary where a small
random difference decides the outcome. So a "10% prompt" is a real object, not a
measurement error.

That fact points in two opposite directions depending on which activity you are
doing, and conflating them is how people waste weekends.

### Playing to win: variance is your friend

In Agent Breaker or Gray Swan you need **one** success. You do not need to know
the rate. You are not measuring anything.

A prompt that works 10% of the time:

| Tries | Chance you have broken it at least once |
|---|---|
| 1 | 10% |
| 5 | 41% |
| 10 | 65% |
| 30 | 96% |

At a harsher 3% per attempt, ten tries gets you to 26% and thirty gets you to 60%.
This is why the field average is around 29 attempts per break. Nobody is landing
these first try.

**Practical rule: reroll the same prompt 3 to 5 times, not 100.** Then move on.
Firing the eleventh identical copy is worth far less than firing the first copy of
a prompt that changes one variable.

**When to reroll:** it half-worked. Soft refusal, partial compliance, killed
midstream. Those mean you are near the boundary and a reroll can carry you over.

**When rerolling is worthless:** the refusal is byte-identical and instant every
single time. That is a deterministic input classifier, not the model. There is no
randomness to exploit and a hundred rerolls will return the identical string a
hundred times. Change the input surface instead.

**Reroll honestly.** If you change the wording while rerolling, you are not
rerolling, you are testing a new prompt, and you now know nothing about either
one. Change nothing, or change exactly one thing and call it a new attempt.

### Claiming a rate: variance is your enemy

The moment you write "this technique works" in a report or a writeup, you are
making a statistical claim and the numbers start mattering.

The useful rule of thumb: **if you see zero successes in n attempts, the honest
upper bound is about 3/n.** Zero in 10 means "could still be up to 30%", which is
worth nothing. Zero in 30 means "probably under 10%". That is the whole content of
the line that used to be here, and it applies only to this activity.

For comparing two techniques rather than just bounding one, 20 to 30 attempts per
condition is the floor. Below that you cannot distinguish "works most of the time"
from "got lucky".

This is also why the tool hides success rates below n=20 and always shows the raw
counts. One hit in six displayed as "17%" is noise wearing a lab coat, and it will
talk you out of a good family after two unlucky rolls.

### The ladder version

You already run this split without naming it. When you are climbing you are trying
to win the game in front of you, and you cheerfully take a 30% line because you
only need it to hit once. When you are deciding whether a deck is actually good,
you stop trusting your last five games.

Same brain, two modes. Play in the first mode. Write in the second.

**One consequence worth internalising:** because best-of-N works so well, "I broke
it" is almost never the finding. Reported success climbs from 41% at N=100 to 78%
at N=10,000 on the same model with no smarter prompting. Everything breaks
eventually. The finding is how cheaply, how reliably, and whether it transfers.
Which is why the log records attempt counts and not just wins.

---

**The one line worth keeping:** techniques change, models change, classifiers
change. The mindset is permanent.
