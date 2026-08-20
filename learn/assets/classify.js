/* Reusable reply-classifier drill.
   Shows a captured reply, learner picks the outcome CLASS from memory,
   then gets the class meaning and the paired next move as feedback.
   Retrieval direction: reply text -> class -> next move (the effortful direction).

   Usage from a lesson:
     initClassifier('drillEl', ITEMS, CLASSES)
   where CLASSES is an ordered array of {key,label,means,next}
   and ITEMS is [{reply, key, why}]. */

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function initClassifier(containerId, items, classes) {
  const root = document.getElementById(containerId);
  const byKey = Object.fromEntries(classes.map(c => [c.key, c]));
  let i = 0, correct = 0, answered = 0;

  function render() {
    const it = items[i];
    root.innerHTML = `
      <div class="clf-progress">Reply ${i + 1} of ${items.length} · ${correct}/${answered} right</div>
      <div class="clf-reply">${esc(it.reply)}</div>
      <div class="clf-ask">What class is this, and what is your next move?</div>
      <div class="clf-btns">
        ${classes.map(c => `<button class="clf-opt" data-k="${c.key}">${c.label}</button>`).join('')}
      </div>
      <div class="clf-fb" id="clf-fb"></div>`;

    root.querySelectorAll('.clf-opt').forEach(btn => {
      btn.addEventListener('click', () => grade(btn.dataset.k, it));
    });
  }

  function grade(picked, it) {
    if (root.dataset.locked === '1') return;
    root.dataset.locked = '1';
    answered++;
    const right = picked === it.key;
    if (right) correct++;
    const truth = byKey[it.key];
    root.querySelectorAll('.clf-opt').forEach(b => {
      if (b.dataset.k === it.key) b.classList.add('clf-correct');
      else if (b.dataset.k === picked) b.classList.add('clf-wrong');
      b.disabled = true;
    });
    const fb = document.getElementById('clf-fb');
    fb.innerHTML = `
      <div class="${right ? 'hit' : 'miss'}">${right ? 'Right.' : 'Not quite.'} This is <strong>${truth.label}</strong>.</div>
      <div class="clf-means"><em>Signal:</em> ${it.why}</div>
      <div class="clf-means"><em>Means:</em> ${truth.means}</div>
      <div class="clf-next"><em>Next move:</em> ${truth.next}</div>
      <button class="clf-next-btn" id="clf-adv">${i + 1 < items.length ? 'Next reply' : 'Done - see score'}</button>`;
    document.getElementById('clf-adv').addEventListener('click', advance);
  }

  function advance() {
    root.dataset.locked = '0';
    if (i + 1 < items.length) { i++; render(); }
    else {
      root.innerHTML = `<div class="clf-final ${correct === items.length ? 'hit' : 'miss'}">
        ${correct} / ${items.length} classified cold.
        ${correct === items.length
          ? 'You can read a reply without the table open. That is the skill.'
          : 'Re-run it. The class you missed is the one costing you attempts at a live target.'}</div>
        <button class="clf-next-btn" id="clf-again">Run again</button>`;
      document.getElementById('clf-again').addEventListener('click', () => {
        i = 0; correct = 0; answered = 0; root.dataset.locked = '0'; render();
      });
    }
  }

  root.dataset.locked = '0';
  render();
}
