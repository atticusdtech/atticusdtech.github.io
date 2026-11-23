// Live help desk page: the ITIL chain (incidents -> problems -> changes -> releases)
// plus one table per record type, from data/ops.json.
(function () {
  const root = document.getElementById('live');
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const day = iso => (iso ? new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '');
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const STATE = { in_progress: 'In progress', scheduled: 'Scheduled', planned: 'Planned', completed: 'Done' };

  const tag = (kind, label) => `<span class="kind kind-${kind}">${label}</span>`;
  const node = (kind, label, title, meta, done) =>
    `<div class="node node-${kind}${done ? ' is-done' : ''}">${tag(kind, label)}<div class="node-title">${esc(title)}</div>${meta ? `<div class="node-meta">${meta}</div>` : ''}</div>`;
  const empty = '<div class="node node-empty" aria-hidden="true"></div>';

  function render(d) {
    const P = Object.fromEntries((d.problems || []).map(p => [p.id, p]));
    const C = Object.fromEntries((d.changes || []).map(c => [c.id, c]));
    const R = Object.fromEntries((d.releases || []).map(r => [r.id, r]));
    const changes = d.changes || [];
    const incLog = (d.incidents && d.incidents.log) || [];

    // --- flow rows: one per change, plus solved problems no published change covers
    const rows = [];
    const usedP = new Set();
    for (const c of changes) {
      const probs = (c.fixes || []).map(id => P[id]).filter(Boolean);
      probs.forEach(p => usedP.add(p.id));
      const incN = probs.reduce((n, p) => n + p.incidents, 0) || c.incidents;
      const rel = (c.releases || []).map(id => R[id]).filter(Boolean)[0];
      rows.push({ c, probs, incN, rel });
    }
    for (const p of d.problems || []) if (!usedP.has(p.id)) rows.push({ c: null, probs: [p], incN: p.incidents, rel: null });
    // one row per release (its changes stacked), then standalone chains
    const merged = [], byRel = {};
    for (const r of rows) {
      if (!r.rel) { merged.push({ ...r, cs: r.c ? [r.c] : [] }); continue; }
      const g = byRel[r.rel.id] || (byRel[r.rel.id] = { rel: r.rel, probs: [], cs: [], incN: 0 });
      g.probs.push(...r.probs); if (r.c) g.cs.push(r.c); g.incN += r.incN;
    }
    merged.push(...Object.values(byRel));
    merged.sort((a, b) => (b.probs.length - a.probs.length) || (b.incN - a.incN) || ((b.rel ? 1 : 0) - (a.rel ? 1 : 0)));

    const stack = items => (items.length > 1 ? `<div class="node-stack">${items.join('')}</div>` : items[0] || empty);
    const flow = merged.map(({ cs, probs, incN, rel }) => `<div class="flow-row">
      ${incN ? node('inc', 'Incidents', plural(incN, 'incident'), 'resolved', true) : empty}
      ${stack(probs.map(p => node('prb', p.id, p.title, `Solved ${day(p.date)}`, true)))}
      ${stack(cs.map(c => node('chg', c.id, c.title, c.state === 'completed' ? `Done ${day(c.date)}` : STATE[c.state], c.state === 'completed')))}
      ${rel ? node('rel', rel.id, rel.title, rel.done ? 'Shipped' : `${rel.changes_done} of ${plural(rel.changes_total, 'change')} done`, rel.done) : empty}
    </div>`).join('');

    // --- summary strip
    const openC = changes.filter(c => c.state !== 'completed').length;
    const strip = `<div class="strip">
      <div class="step">${tag('inc', 'Incidents')}<div class="v">${incLog.length}</div><div class="l">resolved, last few days</div></div>
      <div class="step">${tag('prb', 'Problems')}<div class="v">${(d.problems || []).length}</div><div class="l">solved, last 2 weeks</div></div>
      <div class="step">${tag('chg', 'Changes')}<div class="v">${openC}</div><div class="l">in flight · ${changes.length - openC} done</div></div>
      <div class="step">${tag('rel', 'Releases')}<div class="v">${(d.releases || []).length}</div><div class="l">in flight</div></div>
    </div>`;

    // --- tables
    const table = (head, body) => body.length
      ? `<table class="data live-table"><thead><tr>${head.map(h => `<th${h.num ? ' class="num"' : ''}>${h.t || h}</th>`).join('')}</tr></thead><tbody>${body.join('')}</tbody></table>`
      : '<p class="note">Nothing here right now.</p>';

    const tChanges = table(['Change', 'Status', { t: 'Fixes' }, { t: 'Release' }], changes.map(c =>
      `<tr><td><span class="rid">${c.id}</span> ${esc(c.title)}</td><td>${c.state === 'completed' ? `Done ${day(c.date)}` : STATE[c.state]}</td><td>${(c.fixes || []).join(', ') || '—'}</td><td>${(c.releases || []).join(', ') || '—'}</td></tr>`));
    const tReleases = table(['Release', 'Progress', { t: 'Changes' }], (d.releases || []).map(r => {
      const pct = r.changes_total ? Math.round((r.changes_done / r.changes_total) * 100) : 0;
      return `<tr><td><span class="rid">${r.id}</span> ${esc(r.title)}</td><td>${r.done ? 'Shipped' : r.changes_total ? `<span class="now-bar"><i style="width:${pct}%"></i></span>${r.changes_done} of ${r.changes_total}` : 'Planning'}</td><td>${(r.changes || []).join(', ') || '—'}</td></tr>`;
    }));
    const tProblems = table(['Problem', { t: 'Incidents', num: true }, 'Fixed by', 'Solved'], (d.problems || []).map(p =>
      `<tr><td><span class="rid">${p.id}</span> ${esc(p.title)}</td><td class="num">${p.incidents}</td><td>${(p.changes || []).join(', ') || '—'}</td><td>${day(p.date)}</td></tr>`));
    const tIncidents = table(['Incident', 'Problem', 'Resolved'], incLog.map(b =>
      `<tr><td><b>${esc(b.headline)}.</b> <span class="muted">${esc(b.detail)}</span></td><td>${b.problem || '—'}</td><td>${day(b.date)}</td></tr>`));
    const sol = d.solutions || [];
    const tSolutions = sol.length ? `<h2>Solutions</h2>${table(['Article', 'Published'], sol.map(s => `<tr><td>${esc(s.title)}</td><td>${day(s.date)}</td></tr>`))}` : '';

    root.innerHTML = `${strip}
      <h2>Flow</h2>
      <div class="flow">
        <div class="flow-head"><span>Incidents</span><span>Problem</span><span>Change</span><span>Release</span></div>
        ${flow || '<p class="note">Nothing in flight.</p>'}
      </div>
      <h2>Changes</h2>${tChanges}
      <h2>Releases</h2>${tReleases}
      <h2>Problems solved</h2>${tProblems}
      <h2>Incidents resolved</h2>${tIncidents}
      ${tSolutions}`;

    const u = new Date(d.updated);
    document.getElementById('updated').textContent =
      u.toLocaleDateString('en-US', { month: 'long', day: 'numeric' }) + ', ' + u.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  }

  fetch('data/ops.json', { cache: 'no-cache' })
    .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
    .then(render)
    .catch(() => { root.innerHTML = '<p class="note">Couldn’t load the live data.</p>'; });
})();
