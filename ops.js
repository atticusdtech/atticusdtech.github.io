// Renders the "Now" rail from data/ops.json: open changes and published solutions.
(function () {
  const rail = document.querySelector('aside.rail');
  if (!rail) return;

  const STATE = { in_progress: 'In progress', scheduled: 'Scheduled', planned: 'Planned' };
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const day = iso => new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

  function render(d) {
    const changes = d.changes || [];
    const solutions = d.solutions || [];
    const extra = (d.changes_total || changes.length) - changes.length;

    const changeList = changes.length ? `
      <h3>Changes in flight</h3>
      <ul class="now-list">${changes.map(c => `
        <li data-s="${esc(c.state)}"><span class="now-title">${esc(c.title)}</span>
          <span class="now-meta">${STATE[c.state] || ''}${c.state === 'scheduled' && c.start ? ` · ${day(c.start)}` : ''}</span></li>`).join('')}
      </ul>${extra > 0 ? `<p class="now-more">and ${extra} more</p>` : ''}` : '';

    const solutionList = solutions.length ? `
      <h3>Solutions published</h3>
      <ul class="now-list">${solutions.map(s => `
        <li data-s="done"><span class="now-title">${esc(s.title)}</span>
          <span class="now-meta">${day(s.date)}</span></li>`).join('')}
      </ul>` : '';

    if (!changeList && !solutionList) return;
    rail.innerHTML = `<section class="today now" aria-labelledby="now-h">
      <div class="today-head">
        <h2 id="now-h"><span class="pulse" aria-hidden="true"></span>Now</h2>
        <time datetime="${esc(d.updated)}">updated ${day(d.updated.slice(0, 10))}</time>
      </div>
      ${changeList}${solutionList}
    </section>`;
  }

  fetch('data/ops.json', { cache: 'no-cache' })
    .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
    .then(render)
    .catch(() => {});
})();
