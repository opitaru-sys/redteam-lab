# Resources

Ordered by trust. Omri's own lab material comes first because it is already
sourced and matches the vocabulary the lessons use. Never introduce a competing
taxonomy.

## Primary (his own, canonical for this workspace)
- `../CRASH-COURSE.md` — the content. Section 1 (two root causes), section 2 (the
  9 families with real numbers), section 3 (the 8-level defense ladder), section 4
  (the refusal-reading table). **The glossary and every lesson defer to this.**
- `../LESSONS.md` — post-mortem on the three reasoning errors: read the spec first,
  relative-vs-absolute statistics, verbatim-vs-rendered.
- `../index.html` — 22 technique cards, 50-item drill bank, the attempt logbook.

## External, high-trust
- **Gandalf the Red: Adaptive Security for LLMs**, arXiv:2501.07927 — the controlled
  trial behind the 9 families and the win-rate numbers. 279,675 prompts. The closest
  thing the field has to a data-backed tier list.
- **Jailbroken: How Does LLM Safety Training Fail?**, Wei, Haghtalab, Steinhardt —
  the "mismatched generalization" lens behind root cause two.
- PortSwigger Web Security Academy, LLM attacks — free, lab-graded, ties LLM attacks
  to real appsec. High value for the day job.

## Beating an input classifier (the HIDE bucket, grounded)
Added 2026-08-07 while stuck on Cycling Coach L4's added input-guard layer. These
ground the input-obfuscation / non-English families with real data. Core idea: the
classifier and the LLM read the same bytes differently. Make the trigger unreadable
to the classifier, still readable to the model.
- **Bypassing LLM Guardrails: An Empirical Analysis of Evasion Attacks**, arXiv:2504.11168
  (ACL 2025 LLMSec). Character injection (homoglyphs, zero-width chars, Unicode tags,
  emoji smuggling) and AML evasion hit up to 100% against Azure Prompt Shield, Meta
  Prompt Guard, Protect AI. The evidence that input-obfuscation is a top class, not dead.
- **Controlled-Release Prompting: Bypassing Prompt Guards in Production**, arXiv:2510.01529.
  Splits the payload across turns so each passes the stateless classifier alone. Powerful,
  and OUT for Agent Breaker on purpose: it is single-shot, one input then reset. See the
  memory note agent-breaker-single-shot.
- Mindgard, "Outsmarting AI Guardrails with Invisible Characters and Adversarial Prompts"
  — practitioner write-up of the zero-width / homoglyph mechanics.
- Lakera Guard docs, platform.lakera.ai/docs/prompt_injection — what the L4 layer IS.
  Trained on Gandalf adversarial data, claims to catch obfuscation. Treat character
  injection as the next class to test here, not a guaranteed key.

## Walkthroughs (harvest a working solution, per CRASH-COURSE section 5)
- bot-tricks.com/challenges — community walkthroughs for Lakera challenges incl. Cycling Coach.
- Lakera's own Agent Breaker walkthrough series on YouTube (search "Lakera Agent Breaker walkthrough").

## Practice targets (where reps happen)
- Gandalf: Agent Breaker, play.lakera.ai/agent-breaker — the primary. Agentic.
- Classic 8-level ladder via gandalf-api.lakera.ai — the cleanest defense ladder.
- trial-summarization-* on the same API — indirect prompt injection, the thing that
  matters most for the day job.

## Community (wisdom, still to source)
- Not yet chosen. Network hours are an open item in the plan. Candidate: HackAPrompt
  Discord, the Gray Swan community. To be picked deliberately.
