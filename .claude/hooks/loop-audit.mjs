#!/usr/bin/env node
/**
 * loop-audit.mjs - PostToolUse backstop for the red-team learning loop.
 * Advisory (never blocks real work). Fires on Edit/Write to the OPERATIONAL logs where
 * wall-claims actually get written: learn/harvest/*.md, PROGRESS.md, and the attempts
 * snapshot. Deliberately NOT the methodology docs (CLAUDE.md, LEARNING-LOOP.md, CRASH-COURSE.md,
 * SEAMWRIGHT.md) - those legitimately quote the give-up vocabulary as the thing to avoid, so
 * scanning them would flag the guidance itself.
 * Warns when a live log shows a gate violation the operator tends to miss:
 *   (a) a conclusion/stop word on a line with no bound/hedge token nearby  -> PRE-CONCLUSION checklist (0d)
 *   (b) a SEND row with no result= within 2 lines                          -> complete it or mark ABORTED (0b)
 *   (c) STATE header "FIRES since last review" >= 8                        -> run the adversarial review (0e)
 * See learn/LEARNING-LOOP.md section 0.
 */
import { readFileSync } from 'node:fs';

function readStdin() {
  try { return readFileSync(0, 'utf8'); } catch { return ''; }
}

// Stop-claim detection, hoisted to module scope so --selftest exercises the exact patterns the hook
// uses. CONCLUSION = a give-up phrase (the actual over-claim risk, NOT common descriptors like
// "hardened"/"robust"). The technique-exhaustion / unbreakable / resists-everything forms were added
// 2026-08-13 after a premature "technique space exhausted" call on the Gray Swan admin guard that a
// fresh brainstorm then broke (G-PERSIST).
const CONCLUSION = /(wall holds|no path exists|no clean path|no clean note-only path|no note-only path|research-grade wall|note-only[- ]unbeatable|\bunbeatable\b|\bunbreakable\b|structurally immune to (all|any|every)|cannot be beaten|no working (technique|path) (exists|remains)|technique[ -]space (is |was )?exhausted|exhausted (the |all |every |my )?(technique|lever|catalog)|resists (all|any|every|everything)|nothing (else )?(works|left to try))/i;
// BOUND = a genuine quantitative/epistemic hedge that makes a "wall" line legitimate. Bare negations
// (do not / don't / doesn't / would need) were REMOVED 2026-08-20: they are the exact give-up idiom
// CLAUDE.md itself uses, so including them let any give-up line ending in "do not continue" mask its
// own stop-claim (audit item 7). Keep only real bounds and epistemic verbs.
const BOUND = /(<=|>=|≤|≥|\bn\s*[=>]\s*\d|\d+\s*\/\s*\d+|\bCI\b|pass_rate|95%|upper bound|\bbound\b|per-fire|opening rate|UNMEASURED|UNPROVEN|RETRACTED|gate-legal|hypothesis|open branch|consistent-with|found \+ open|falsif|refut|retract|unsound|stop.?claim|5-lens|mandated|not prove|disprove|over-claim)/i;

// Positive-control: `node loop-audit.mjs --selftest` must print "selftest OK" and exit 0.
if (process.argv.includes('--selftest')) {
  const flagged = (ln) => CONCLUSION.test(ln) && !BOUND.test(ln);
  const cases = [
    ['no path exists, do not continue', true],    // bare negation must NOT mask the stop-claim
    ['technique space exhausted here', true],
    ['note-only unbeatable, giving up', true],
    ['wall holds at n=8/8, pass_rate 0', false],   // a genuine bound legitimately suppresses
    ['this is consistent-with a hardened guard', false],
    ['rerolled 3x, still blocked, changing surface', false], // no stop-claim phrase at all
  ];
  let bad = 0;
  for (const [ln, want] of cases) {
    if (flagged(ln) !== want) { bad++; process.stderr.write(`selftest FAIL: "${ln}" expected flagged=${want}\n`); }
  }
  process.stderr.write(bad ? `selftest: ${bad} failure(s)\n` : 'selftest OK\n');
  process.exit(bad ? 1 : 0);
}

try {
  let data = {};
  try { data = JSON.parse(readStdin() || '{}'); } catch { data = {}; }
  const fp = (data.tool_input && (data.tool_input.file_path || data.tool_input.path)) || '';

  // Audit LIVE operational logs (harvest + PROGRESS.md, where wall-claims get written),
  // AND the attempts snapshot (for the G-READ reroll-before-rewrite check).
  const isHarvest = /[\\/]learn[\\/]harvest[\\/][^\\/]+\.md$/i.test(fp);
  const isSnapshot = /[\\/]learn[\\/]attempts-snapshot\.md$/i.test(fp);
  const isProgress = /(^|[\\/])PROGRESS\.md$/i.test(fp);
  if (!isHarvest && !isSnapshot && !isProgress) process.exit(0);

  let text = '';
  try { text = readFileSync(fp, 'utf8'); } catch { process.exit(0); }
  const lines = text.split(/\r?\n/);

  const warnings = [];

  lines.forEach((ln, i) => {
    // Same-line only: a stop-claim must carry its bound/hedge in the same breath. A bound on an
    // unrelated adjacent line must NOT suppress it (that masking was the bug the positive-control caught).
    if (CONCLUSION.test(ln) && !BOUND.test(ln)) {
      warnings.push(`L${i + 1}: "${ln.trim().slice(0, 70)}" - stop-claim phrase, no bound. Run PRE-CONCLUSION checklist (0d).`);
    }
    // Proper send-row = "SEND <id> | ..." ; must carry result= within 2 lines.
    if (/^\s*[-*]?\s*SEND\s+[\w.-]+\s*\|/i.test(ln) && !/result=/i.test(ln)) {
      const near = (lines[i + 1] || '') + ' ' + (lines[i + 2] || '');
      if (!/result=/i.test(near) && !/ABORTED/i.test(ln + near)) warnings.push(`L${i + 1}: SEND row with no result= - complete it or mark ABORTED (0b).`);
    }
  });

  // G-READ (CRASH-COURSE 4 + 10): a near-boundary result (soft-refusal / adjacent / structure-no-payload)
  // is the highest-value state and OWES a reroll or one-clause edit BEFORE any rewrite. Flag rows in the
  // attempts snapshot where such a class was followed by a rewrite/new-family move instead. This is the exact
  // drift Omri flagged 2026-08-20 (rewrote blindly, never rerolled).
  if (isSnapshot) {
    const NEAR = new Set(['soft-refusal', 'adjacent', 'structure-no-payload']);
    const OK_MOVE = new Set(['reroll', 'edit-one-clause', 'extract-detail', 'done', '']);
    lines.forEach((ln, i) => {
      if (!/^\s*\|\s*\d+\s*\|/.test(ln)) return; // markdown data rows only
      const cells = ln.split('|').map((s) => s.trim()); // trailing '|' -> empty last cell
      const move = cells[cells.length - 2] || '';
      const cls = cells[cells.length - 3] || '';
      if (NEAR.has(cls) && !OK_MOVE.has(move)) {
        warnings.push(`L${i + 1}: class=${cls} -> move=${move || '(none)'} - a near-boundary hit owed a REROLL / one-clause edit before any rewrite (G-READ, CRASH-COURSE 4+10).`);
      }
    });
  }

  const m = text.match(/FIRES since last (?:adversarial )?review:\s*(\d+)\s*\/\s*8/i);
  if (m && Number(m[1]) >= 8) warnings.push(`STATE header: FIRES since last review = ${m[1]}/8 - run the adversarial review (0e), then reset to 0.`);

  if (warnings.length) {
    const top = warnings.slice(0, 8);
    process.stderr.write('LOOP-AUDIT (advisory - learn/harvest gate check):\n- ' + top.join('\n- ') +
      (warnings.length > 8 ? `\n(+${warnings.length - 8} more)` : '') + '\n');
    process.exit(2); // PostToolUse: surface the advisory back to Claude
  }
  process.exit(0);
} catch {
  process.exit(0); // never break real work
}
