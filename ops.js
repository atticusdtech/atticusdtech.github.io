// Renders the ITIL operations rail from data/ops.json into every <aside class="rail">.
(function () {
  const rail = document.querySelector('aside.rail');
  if (!rail) return;

  const PROBLEM = {
    investigating: 'Investigating',
    known_error: 'Known error',
    fix_in_change: 'Fix in progress',
    resolved: 'Resolved',
  };
  const CHANGE = {
    planned: 'Planned',
    scheduled: 'Scheduled',
    in_progress: 'In progress',
    completed: 'Done',
    rolled_back: 'Rolled back',
    cancelled: 'Cancelled',
  };
  const LIMIT = 4;

  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
  const day = iso => new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

  function lane(key, label, summary, rows) {
    if (!rows.length) return '';
    const shown = rows.slice(0, LIMIT).join('');
    const rest = rows.slice(LIMIT);
    const more = rest.length
      ? `<details class="ops-more"><summary>${rest.length} more</summary><ul class="ops-list">${rest.join('')}</ul></details>`
      : '';
    return `<section class="ops-lane ops-${key}" aria-label="${label}">
      <h3><span>${label}</span><span class="ops-sum">${summary}</span></h3>
      <ul class="ops-list">${shown}</ul>${more}
    </section>`;
  }

  function row(id, title, pill, pillKey, meta) {
    return `<li><div class="ops-row"><span class="ops-id">${esc(id)}</span><span class="ops-pill" data-s="${pillKey}">${pill}</span></div>
      <div class="ops-title">${esc(title)}</div>${meta ? `<div class="ops-meta">${meta}</div>` : ''}</li>`;
  }

  function render(d) {
    const inc = d.incidents || { log: [] };
    const problems = d.problems || [];
    const changes = d.changes || [];
    const releases = d.releases || [];
    const updated = new Date(d.updated);

    const incidentLane = `<section class="ops-lane ops-incidents" aria-label="Incidents">
      <h3><span>Incidents</span><span class="ops-sum"><strong>${inc.resolved_today}</strong> resolved today · ${inc.open} open</span></h3>
      ${inc.log && inc.log.length
        ? `<ul class="today-log">${inc.log.map(b => `<li><strong>${esc(b.headline)}.</strong> ${esc(b.detail)}</li>`).join('')}</ul>`
        : '<p class="ops-quiet">Nothing resolved yet today.</p>'}
    </section>`;

    const openP = problems.filter(p => p.state !== 'resolved');
    const problemLane = lane('problems', 'Problems',
      `${openP.length} open · ${problems.reduce((n, p) => n + (p.state === 'resolved' ? 0 : p.incidents), 0)} linked incidents`,
      problems.map(p => row(p.id, p.title, PROBLEM[p.state] || p.state, p.state,
        [p.incidents ? plural(p.incidents, 'incident') : '', p.changes.length ? `fix: ${p.changes.map(esc).join(', ')}` : '']
          .filter(Boolean).join(' · '))));

    const doneC = changes.filter(c => c.state === 'completed');
    const changeLane = lane('changes', 'Changes',
      `${changes.length - doneC.length} open · ${doneC.length} done this week`,
      changes.map(c => row(c.id, c.title, CHANGE[c.state] || c.state, c.state,
        [c.fixes.length ? `fixes ${c.fixes.map(esc).join(', ')}` : '',
         c.state === 'scheduled' && c.start ? `starts ${day(c.start)}` : '',
         c.done ? `done ${day(c.done)}` : '']
          .filter(Boolean).join(' · '))));

    const releaseLane = lane('releases', 'Releases', `${releases.length} in flight`,
      releases.map(r => {
        const pct = r.changes_total ? Math.round((r.changes_done / r.changes_total) * 100) : 0;
        const meta = r.changes_total
          ? `<div class="ops-bar" role="img" aria-label="${r.changes_done} of ${r.changes_total} changes done"><i style="width:${pct}%"></i></div>${r.changes_done} of ${plural(r.changes_total, 'change')} done`
          : 'Planning';
        return row(r.id, r.title, r.changes_total ? `${pct}%` : 'Planning', r.changes_total ? 'in_progress' : 'planned', meta);
      }));

    const dayLabel = updated.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
    const timeLabel = updated.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

    rail.innerHTML = `<section class="today ops" aria-labelledby="ops-h">
      <div class="today-head">
        <h2 id="ops-h"><span class="pulse" aria-hidden="true"></span>Operations</h2>
        <time datetime="${esc(d.updated)}">${dayLabel} · updated ${timeLabel}</time>
      </div>
      ${incidentLane}${problemLane}${changeLane}${releaseLane}
      <details class="ops-key"><summary>How to read this</summary>
        <p><b>Incident</b>: something broke for someone, and I fixed it. <b>Problem</b>: the root cause behind repeat incidents. <b>Change</b>: the planned fix, tested and rolled out. <b>Release</b>: a set of changes shipped together. This is ITIL, the usual way IT teams run a service desk.</p>
      </details>
    </section>`;
  }

  fetch('data/ops.json', { cache: 'no-cache' })
    .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
    .then(render)
    .catch(() => { /* keep the static fallback in the page */ });
})();
