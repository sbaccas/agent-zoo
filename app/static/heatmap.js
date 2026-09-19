/**
 * Movement Map — Independent Standalone Window Controller
 * Zero raw-HTML-injection compliant: every node is built with createElement + textContent.
 * Polls /api/simulations/active/heatmap.
 *
 * Division of labour with the server: the server sends the coarse digest (what
 * the coach will read) and the raw per-tick path. The fine-grained canvas is
 * built here from the path, because a browser has pixels and no token budget
 * while the coach has the opposite problem. Sending a 100x100 grid over the
 * wire just to draw it would be the worst of both.
 */
document.addEventListener('DOMContentLoaded', () => {
  const syncStatus = document.getElementById('syncStatus');
  const heatmapMeta = document.getElementById('heatmapMeta');
  const heatmapCaveat = document.getElementById('heatmapCaveat');
  const heatmapState = document.getElementById('heatmapState');
  const heatmapCats = document.getElementById('heatmapCats');
  const heatmapUpdated = document.getElementById('heatmapUpdated');

  const gridSizeEl = document.getElementById('gridSize');
  const windowTicksEl = document.getElementById('windowTicks');
  const showPathEl = document.getElementById('showPath');

  const btnRefresh = document.getElementById('btnRefreshHeatmap');
  const btnClose = document.getElementById('btnCloseWindow');
  const btnCopy = document.getElementById('btnCopyDigest');

  const REFRESH_MS = 3000;
  const CANVAS_PX = 360;
  const DASH = '—';

  const MODE_LABELS = { ws: 'LLM', auto: 'Heuristic', http: 'HTTP', manual: 'Manual' };

  // Last payload, kept so Copy Digest does not have to refetch.
  let lastPayload = null;

  function clearChildren(el) {
    if (!el) return;
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function isNum(val) {
    return typeof val === 'number' && !isNaN(val);
  }

  function setSync(text, bg, fg) {
    if (!syncStatus) return;
    syncStatus.textContent = text;
    syncStatus.style.background = bg;
    syncStatus.style.color = fg;
  }

  const setSyncLive = () => setSync('Active (Live)', '#dcfce7', '#166534');
  const setSyncError = () => setSync('Sync Error', '#fee2e2', '#991b1b');
  const setSyncIdle = (t) => setSync(t, '#e2e8f0', '#475569');

  function showState(text, isError = false) {
    if (!heatmapState) return;
    heatmapState.textContent = text;
    heatmapState.className = isError ? 'boxscore-state boxscore-state-error' : 'boxscore-state';
    heatmapState.style.display = 'block';
  }

  function hideState() {
    if (heatmapState) heatmapState.style.display = 'none';
  }

  function appendMetaItem(label, value) {
    if (!heatmapMeta) return;
    const item = document.createElement('div');
    item.className = 'boxscore-meta-item';

    const labelEl = document.createElement('span');
    labelEl.className = 'boxscore-meta-label';
    labelEl.textContent = label;
    item.appendChild(labelEl);

    const valueEl = document.createElement('span');
    valueEl.className = 'boxscore-meta-value';
    valueEl.textContent = value;
    item.appendChild(valueEl);

    heatmapMeta.appendChild(item);
  }

  function renderMeta(data) {
    clearChildren(heatmapMeta);
    appendMetaItem('Simulation', data.simulation_id ? String(data.simulation_id) : DASH);
    appendMetaItem('Tick', isNum(data.tick) ? data.tick.toLocaleString() : DASH);
    if (data.arena) {
      appendMetaItem('Arena', `${data.arena.width} x ${data.arena.height}`);
    }
    if (isNum(data.grid_size) && isNum(data.cell_size)) {
      appendMetaItem('Digest', `${data.grid_size} x ${data.grid_size} (${data.cell_size}-tile cells)`);
    }
  }

  function renderCaveat(data) {
    if (!heatmapCaveat) return;
    // The server keeps a bounded number of snapshots, so on a long game the
    // map describes a window rather than the whole run. Saying so matters:
    // a partial map read as a full one understates how much ground was covered.
    if (data.window_capped) {
      heatmapCaveat.textContent =
        '⚠️ The server keeps a limited number of per-tick snapshots, so this map covers only ' +
        'the most recent stretch of the game. The "never visited" line below each map is ' +
        'unaffected — it is tracked separately and covers the whole run.';
      heatmapCaveat.style.display = 'block';
    } else {
      heatmapCaveat.textContent = '';
      heatmapCaveat.style.display = 'none';
    }
  }

  /**
   * Full-resolution heat, computed here from the path.
   *
   * Scaled against the busiest tile rather than an absolute count, because the
   * interesting thing is the contrast between where the cat lives and where it
   * merely passes. A fixed scale would render every map of a camping cat as one
   * bright dot on black.
   */
  function drawCanvas(canvas, cat, arena) {
    const ctx = canvas.getContext('2d');
    const w = arena.width;
    const h = arena.height;
    const path = Array.isArray(cat.path) ? cat.path : [];

    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    if (path.length === 0) return;

    const counts = new Map();
    let peak = 0;
    path.forEach(([x, y]) => {
      const key = y * w + x;
      const next = (counts.get(key) || 0) + 1;
      counts.set(key, next);
      if (next > peak) peak = next;
    });

    const sx = canvas.width / w;
    const sy = canvas.height / h;

    counts.forEach((count, key) => {
      const x = key % w;
      const y = Math.floor(key / w);
      // Square root so a single visit is still visible next to a cell the cat
      // sat in for 200 ticks; a linear ramp hides the wandering entirely.
      const intensity = Math.sqrt(count / peak);
      const hue = 220 - 200 * intensity;
      const light = 25 + 45 * intensity;
      ctx.fillStyle = `hsl(${hue}, 90%, ${light}%)`;
      ctx.fillRect(x * sx, y * sy, Math.max(1, sx), Math.max(1, sy));
    });

    if (showPathEl && showPathEl.checked && path.length > 1) {
      ctx.strokeStyle = 'rgba(255,255,255,0.35)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      path.forEach(([x, y], i) => {
        const px = x * sx + sx / 2;
        const py = y * sy + sy / 2;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();

      // Where it is now, so the trace has a direction the eye can follow.
      const [lx, ly] = path[path.length - 1];
      ctx.fillStyle = '#fef08a';
      ctx.beginPath();
      ctx.arc(lx * sx + sx / 2, ly * sy + sy / 2, 3.5, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function buildCatCard(cat, arena) {
    const card = document.createElement('section');
    card.className = 'heatmap-card';

    const head = document.createElement('div');
    head.className = 'heatmap-card-head';

    const name = document.createElement('span');
    name.className = 'boxscore-cat-name';
    name.textContent = cat.id ? String(cat.id) : 'unknown cat';
    head.appendChild(name);

    const mode = document.createElement('span');
    const modeKey = typeof cat.mode === 'string' ? cat.mode : '';
    mode.className = 'boxscore-badge boxscore-badge-mode';
    if (modeKey === 'ws' || modeKey === 'auto') {
      mode.classList.add('boxscore-badge-mode-' + modeKey);
    }
    mode.textContent = MODE_LABELS[modeKey] || (modeKey || 'unknown');
    head.appendChild(mode);

    const window_ = document.createElement('span');
    window_.className = 'heatmap-window';
    window_.textContent = `${isNum(cat.window_ticks) ? cat.window_ticks : 0} ticks · ` +
      `${isNum(cat.unique_tiles) ? cat.unique_tiles.toLocaleString() : 0} unique tiles`;
    head.appendChild(window_);

    card.appendChild(head);

    const body = document.createElement('div');
    body.className = 'heatmap-card-body';

    const canvas = document.createElement('canvas');
    canvas.width = CANVAS_PX;
    canvas.height = CANVAS_PX;
    canvas.className = 'heatmap-canvas';
    body.appendChild(canvas);

    const digest = document.createElement('pre');
    digest.className = 'heatmap-digest';
    digest.textContent = cat.ascii || '';
    body.appendChild(digest);

    card.appendChild(body);

    // Drawn after the canvas is in the tree so its backing store is sized.
    drawCanvas(canvas, cat, arena);
    return card;
  }

  function renderCats(data) {
    clearChildren(heatmapCats);
    const cats = Array.isArray(data.cats) ? data.cats : [];
    if (cats.length === 0) {
      showState('No cats are registered in this simulation yet. Start or reset the game.');
      return;
    }
    hideState();
    cats.forEach((cat) => heatmapCats.appendChild(buildCatCard(cat, data.arena || {})));
  }

  function renderNoSimulation() {
    clearChildren(heatmapMeta);
    clearChildren(heatmapCats);
    if (heatmapCaveat) {
      heatmapCaveat.textContent = '';
      heatmapCaveat.style.display = 'none';
    }
    showState('No active simulation. Start a game in the main window and the map will fill in.');
  }

  function stampUpdated(text) {
    if (heatmapUpdated) heatmapUpdated.textContent = text;
  }

  function currentQuery() {
    const params = new URLSearchParams();
    params.set('grid', gridSizeEl ? gridSizeEl.value : '5');
    const last = windowTicksEl ? windowTicksEl.value : '';
    if (last) params.set('last', last);
    return params.toString();
  }

  async function loadHeatmap() {
    try {
      const resp = await fetch(`/api/simulations/active/heatmap?${currentQuery()}`);

      if (resp.status === 404) {
        setSyncIdle('No Active Game');
        renderNoSimulation();
        stampUpdated(`Checked ${new Date().toLocaleTimeString()} · auto-refreshing every 3s`);
        return;
      }
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      const data = await resp.json();
      lastPayload = data;
      setSyncLive();
      renderMeta(data);
      renderCaveat(data);
      renderCats(data);
      stampUpdated(`Updated ${new Date().toLocaleTimeString()} · auto-refreshing every 3s`);
    } catch (err) {
      console.error('Failed to load movement map:', err);
      setSyncError();
      clearChildren(heatmapCats);
      showState('Could not connect to active game simulation. Is the server running?', true);
      stampUpdated(`Last attempt failed at ${new Date().toLocaleTimeString()}`);
    }
  }

  async function copyDigest() {
    if (!lastPayload || !lastPayload.ascii) return;
    try {
      await navigator.clipboard.writeText(lastPayload.ascii);
      stampUpdated(`Digest copied at ${new Date().toLocaleTimeString()}`);
    } catch (err) {
      console.error('Clipboard write failed:', err);
      stampUpdated('Clipboard unavailable — select the digest text and copy manually.');
    }
  }

  if (btnRefresh) btnRefresh.addEventListener('click', loadHeatmap);
  if (btnClose) btnClose.addEventListener('click', () => window.close());
  if (btnCopy) btnCopy.addEventListener('click', copyDigest);
  if (gridSizeEl) gridSizeEl.addEventListener('change', loadHeatmap);
  if (windowTicksEl) windowTicksEl.addEventListener('change', loadHeatmap);
  // Redraws from the payload already held rather than refetching: the toggle
  // only affects the canvas overlay, not the data.
  if (showPathEl) showPathEl.addEventListener('change', () => {
    if (lastPayload) renderCats(lastPayload);
  });

  loadHeatmap();
  setInterval(loadHeatmap, REFRESH_MS);
});
