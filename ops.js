// Renders the "Now" rail from data/ops.json. Only finished incidents and
// problems are published; changes and releases show in flight and done.
(function () {
  const rail = document.querySelector('aside.rail');
  if (!rail) return;

  const STATE = { in_progress: 'In progress', scheduled: 'Scheduled', planned: 'Planned', completed: 'Done' };
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const day = iso => new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

  const item = (s, title, meta, extra = '') =>
    `<li data-s="${s}"><span class="now-title">${esc(title)}</span>${extra}${meta ? `<span class="now-meta">${meta}</span>` : ''}</li>`;
  const block = (label, items) => (items.length ? `<h3>${label}</h3><ul class="now-list">${items.join('')}</ul>` : '');

  function render(d) {
    const changes = (d.changes || []).slice(0, 6).map(c => item(
      c.state === 'completed' ? 'done' : c.state, c.title,
      c.state === 'completed' ? `Done ${day(c.date)}` : (STATE[c.state] || '') + (c.state === 'scheduled' && c.start ? ` · ${day(c.start)}` : '')));

    const releases = (d.releases || []).map(r => {
      if (r.done) return item('done', r.title, 'Shipped');
      const pct = r.changes_total ? Math.round((r.changes_done / r.changes_total) * 100) : 0;
      return item('in_progress', r.title,
        r.changes_total ? `${r.changes_done} of ${plural(r.changes_total, 'change')} done` : 'Planning',
        r.changes_total ? `<span class="now-bar" role="img" aria-label="${pct}% done"><i style="width:${pct}%"></i></span>` : '');
    });

    const problems = (d.problems || []).slice(0, 4).map(p => item('done', p.title,
      `Solved ${day(p.date)}${p.incidents ? ` · ${plural(p.incidents, 'linked incident')}` : ''}`));

    const inc = d.incidents || {};
    const today = new Date().toISOString().slice(0, 10);
    const incLabel = 'Incidents resolved';
    const incidents = (inc.log || []).slice(0, 3).map(b =>
      `<li data-s="done"><span class="now-title">${esc(b.headline)}</span><span class="now-detail">${esc(b.detail)}</span>${b.date ? `<span class="now-meta">${b.date === today ? 'Today' : day(b.date)}</span>` : ''}</li>`);

    const solutions = (d.solutions || []).map(s => item('done', s.title, day(s.date)));

    const body = block('Changes', changes) + block('Releases', releases) + block('Problems solved', problems)
      + block(incLabel, incidents) + block('Solutions published', solutions)
      + '<p class="now-link"><a href="live.html">See how it all connects →</a></p>';

    rail.innerHTML = `<section class="today now" aria-labelledby="now-h">
      <div class="today-head">
        <h2 id="now-h"><span class="pulse" aria-hidden="true"></span>Now</h2>
        <time datetime="${esc(d.updated)}">updated ${day(d.updated.slice(0, 10))}</time>
      </div>${body}
    </section>`;
  }

  fetch('data/ops.json', { cache: 'no-cache' })
    .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
    .then(render)
    .catch(() => {});
})();
