/**
 * Game Stats — Comprehensive Multi-Tab Dashboard Controller
 * 1) Box Score — Per-cat KPIs, efficiency ratios, control & fallbacks
 * 2) Cat Path  — Movement map, arena trajectory tracing, coach digest
 * 3) Game Events & Replay — Live feed of match events with interactive tick replay
 *
 * Zero raw-HTML-injection compliant: every node is built with createElement + textContent.
 */
document.addEventListener('DOMContentLoaded', () => {
  // Navigation & Shell Elements
  const syncStatus = document.getElementById('syncStatus');
  const syncUpdated = document.getElementById('syncUpdated');
  const btnCloseWindow = document.getElementById('btnCloseWindow');
  const btnRefreshActiveTab = document.getElementById('btnRefreshActiveTab');
  const btnCopyDigest = document.getElementById('btnCopyDigest');

  // Tab Buttons & Panels
  const tabBtnBoxscore = document.getElementById('tabBtnBoxscore');
  const tabBtnCatpath = document.getElementById('tabBtnCatpath');
  const tabBtnEvents = document.getElementById('tabBtnEvents');
  const eventCountBadge = document.getElementById('eventCountBadge');

  const tabBoxscore = document.getElementById('tabBoxscore');
  const tabCatpath = document.getElementById('tabCatpath');
  const tabEvents = document.getElementById('tabEvents');

  // Tab 1 (Box Score) Elements
  const boxscoreMeta = document.getElementById('boxscoreMeta');
  const boxscoreCaveat = document.getElementById('boxscoreCaveat');
  const boxscoreHint = document.getElementById('boxscoreHint');
  const boxscoreState = document.getElementById('boxscoreState');
  const boxscoreTableWrap = document.getElementById('boxscoreTableWrap');

  // Tab 2 (Cat Path / Heatmap) Elements
  const heatmapMeta = document.getElementById('heatmapMeta');
  const heatmapCaveat = document.getElementById('heatmapCaveat');
  const heatmapState = document.getElementById('heatmapState');
  const heatmapCats = document.getElementById('heatmapCats');
  const gridSizeEl = document.getElementById('gridSize');
  const windowTicksEl = document.getElementById('windowTicks');
  const showPathEl = document.getElementById('showPath');

  // Tab 3 (Game Events & Replay) Elements
  const replayCurrentTick = document.getElementById('replayCurrentTick');
  const replayFeedback = document.getElementById('replayFeedback');
  const btnReplayStepBack = document.getElementById('btnReplayStepBack');
  const btnReplayStepForward = document.getElementById('btnReplayStepForward');
  const inputSeekTick = document.getElementById('inputSeekTick');
  const btnSeekTick = document.getElementById('btnSeekTick');

  const filterChips = document.querySelectorAll('.event-filter-chip');
  const eventActorFilter = document.getElementById('eventActorFilter');
  const eventSearchInput = document.getElementById('eventSearchInput');
  const eventsState = document.getElementById('eventsState');
  const eventsTableBody = document.getElementById('eventsTableBody');

  const REFRESH_MS = 2000;
  const CANVAS_PX = 360;
  const DASH = '—';

  const urlParams = new URLSearchParams(window.location.search);
  const activeSimId = urlParams.get('sim') || urlParams.get('simulation_id') || 'active';

  let currentTab = 'tabBoxscore';
  let activeEventFilter = 'all';
  let cachedEventsData = null;
  let cachedHeatmapData = null;

  // Agent mode -> human label
  const MODE_LABELS = {
    ws: 'LLM',
    auto: 'Heuristic',
    http: 'HTTP',
    manual: 'Manual',
  };

  const MODE_TOOLTIPS = {
    ws: 'LLM agent driving this cat over the websocket',
    auto: 'Built-in heuristic driving this cat',
    http: 'External agent driving this cat over HTTP',
    manual: 'Human-controlled from the main window',
  };

  function clearChildren(el) {
    if (!el) return;
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function isNum(val) {
    return typeof val === 'number' && !isNaN(val);
  }

  function fmtInt(val) {
    if (val === null || val === undefined || isNaN(val)) return DASH;
    return Number(val).toLocaleString();
  }

  function fmtFloat(val, digits) {
    if (val === null || val === undefined || isNaN(val)) return DASH;
    return Number(val).toFixed(digits);
  }

  function fmtPct(val, digits = 1) {
    if (val === null || val === undefined || isNaN(val)) return DASH;
    return (Number(val) * 100).toFixed(digits) + '%';
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

  function stampUpdated(text) {
    if (!syncUpdated) return;
    syncUpdated.textContent = text;
  }

  function showFeedback(text, isError = false) {
    if (!replayFeedback) return;
    replayFeedback.textContent = text;
    replayFeedback.style.backgroundColor = isError ? '#fee2e2' : '#dcfce7';
    replayFeedback.style.color = isError ? '#991b1b' : '#166534';
    replayFeedback.style.borderColor = isError ? '#fca5a5' : '#86efac';
    replayFeedback.style.display = 'inline-block';
    setTimeout(() => {
      if (replayFeedback) replayFeedback.style.display = 'none';
    }, 4500);
  }

  /* ============================================================
     TAB SWITCHING LOGIC
     ============================================================ */
  function switchTab(targetTab) {
    currentTab = targetTab;

    [tabBtnBoxscore, tabBtnCatpath, tabBtnEvents].forEach((btn) => {
      if (btn) btn.classList.toggle('active', btn.dataset.tab === targetTab);
    });

    if (tabBoxscore) tabBoxscore.style.display = targetTab === 'tabBoxscore' ? 'flex' : 'none';
    if (tabCatpath) tabCatpath.style.display = targetTab === 'tabCatpath' ? 'flex' : 'none';
    if (tabEvents) tabEvents.style.display = targetTab === 'tabEvents' ? 'flex' : 'none';

    if (btnCopyDigest) {
      btnCopyDigest.style.display = targetTab === 'tabCatpath' ? 'inline-block' : 'none';
    }

    refreshActiveTab();
  }

  if (tabBtnBoxscore) tabBtnBoxscore.addEventListener('click', () => switchTab('tabBoxscore'));
  if (tabBtnCatpath) tabBtnCatpath.addEventListener('click', () => switchTab('tabCatpath'));
  if (tabBtnEvents) tabBtnEvents.addEventListener('click', () => switchTab('tabEvents'));

  /* ============================================================
     TAB 1: BOX SCORE LOGIC
     ============================================================ */
  function hasAttachedAgent(cat) {
    if (!cat) return false;
    const mode = cat.mode || cat.agent_mode;
    if (['ws', 'http', 'adk_gemini'].includes(mode)) return true;
    return isNum(cat.model_control_pct) || isNum(cat.decisions_by_model);
  }

  const SECTIONS = [
    {
      title: 'Scoring',
      rows: [
        { label: 'Kills', key: 'kills', dir: 'high', format: (v) => fmtInt(v), tip: 'mice captured by this cat since tracking began' },
        { label: 'Kills / 100 ticks', key: 'kills_per_100_ticks', dir: 'high', format: (v) => fmtFloat(v, 2), tip: 'kills x 100 / ticks elapsed since tracking began' },
        { label: 'Tiles per kill', key: 'tiles_per_kill', dir: 'low', format: (v) => fmtFloat(v, 1), tip: 'tiles run / kills — fewer tiles per kill is more efficient (dash while kills = 0)' },
      ],
    },
    {
      title: 'Slashing & Combat',
      rows: [
        { label: 'Slash attempts', key: 'slash_attempts', dir: 'high', format: (v) => fmtInt(v), tip: 'total attack actions executed by this cat' },
        { label: 'Hits (landed)', key: 'landed_slashes', dir: 'high', format: (v) => fmtInt(v), tip: 'attack actions that caught at least one prey in the blade rectangle or lingering zone' },
        { label: 'Slash accuracy', key: 'slash_accuracy', dir: 'high', format: (v) => fmtPct(v), tip: 'landed slashes / slash attempts. A slash counts as landed if it killed at any point in its lethal window' },
        { label: 'Whiffs', key: 'whiffs', dir: 'low', format: (v) => fmtInt(v), tip: 'slash attempts - landed slashes' },
        { label: 'Multi-kills', key: 'multi_kills', dir: 'high', format: (v) => fmtInt(v), tip: 'swings that captured 2 or more mice at once' },
        { label: 'Strike zone chances', key: 'zone_chances', dir: 'high', format: (v) => fmtInt(v), tip: 'ticks with prey inside this cat\'s slash AoE range' },
        { label: 'Strike zone conversion', key: 'zone_conversion', dir: 'high', format: (v) => fmtPct(v), tip: 'strike-zone slash attempts / strike-zone chances' },
        { label: 'Zone chances declined', key: 'zone_declined', dir: 'low', format: (v) => fmtInt(v), tip: 'strike-zone chances - strike-zone slash attempts' },
      ],
    },
    {
      title: 'Movement & Positioning',
      rows: [
        { label: 'Tiles run', key: 'tiles_run', dir: 'high', format: (v) => fmtInt(v), tip: 'total tiles of distance travelled' },
        { label: 'Ground covered', key: 'ground_covered', dir: 'high', format: (v) => fmtPct(v), tip: 'unique tiles visited / total arena tiles' },
        { label: 'Unique tiles visited', key: 'unique_tiles', dir: 'high', format: (v) => fmtInt(v), tip: 'distinct arena coordinates this cat has set foot on' },
        { label: 'Speed (tiles/tick)', key: 'speed_tiles_per_tick', dir: 'high', format: (v) => fmtFloat(v, 3), tip: 'tiles run / ticks elapsed' },
        { label: 'Ticks per tile', key: 'ticks_per_tile', dir: 'low', format: (v) => fmtFloat(v, 2), tip: '(ticks free + ticks locked) / unique tiles — how long the cat loiters per tile' },
        { label: 'Moves (1-tile)', key: 'moves', dir: 'none', format: (v) => fmtInt(v), tip: 'single-tile steps taken' },
        { label: 'Lunges (2-tile)', key: 'lunges', dir: 'none', format: (v) => fmtInt(v), tip: 'two-tile dashes taken' },
        { label: 'Lunge share', key: 'lunge_share', dir: 'high', format: (v) => fmtPct(v), tip: 'lunges / (moves + lunges)' },
        { label: 'Wall bonks', key: 'wall_bonks', dir: 'low', format: (v) => fmtInt(v), tip: 'moves or lunges that clipped an arena boundary' },
        { label: 'Prey in sight', key: 'prey_in_sight_pct', dir: 'high', format: (v) => fmtPct(v), tip: 'ticks with prey in the vision box / ticks free' },
        { label: 'Locked recovery', key: 'locked_pct', dir: 'low', format: (v) => fmtPct(v), tip: 'ticks locked in attack/lunge recovery / (ticks free + ticks locked)' },
      ],
    },
    {
      title: 'Control',
      rows: [
        { label: 'Model control %', key: 'model_control_pct', dir: 'high', eligible: hasAttachedAgent, format: (v) => fmtPct(v), tip: 'decisions by model / (decisions by model + fallbacks) — share of ticks truly driven by the model' },
        { label: 'Decisions by model', key: 'decisions_by_model', dir: 'high', eligible: hasAttachedAgent, format: (v) => fmtInt(v), tip: 'ticks played by the cat\'s configured LLM agent' },
        { label: 'Reflex decisions', key: 'decisions_by_agent_reflex', dir: 'none', eligible: hasAttachedAgent, format: (v) => fmtInt(v), tip: 'ticks where agent reflex fast-path answered' },
        { label: 'Heuristic fallbacks', key: 'decisions_by_fallback', dir: 'low', eligible: hasAttachedAgent, format: (v) => fmtInt(v), tip: 'ticks where no usable answer arrived and the reflex heuristic played instead' },
        { label: 'Stale answers', key: 'decisions_stale', dir: 'low', eligible: hasAttachedAgent, format: (v) => fmtInt(v), tip: 'answers that arrived after the simulation had already advanced' },
      ],
    },
  ];

  function leadingValue(cats, row) {
    if (row.dir === 'none') return null;
    const eligible = row.eligible ? cats.filter(row.eligible) : cats;
    const nums = eligible.map((c) => c[row.key]).filter(isNum);
    if (nums.length === 0) return null;
    return row.dir === 'low' ? Math.min(...nums) : Math.max(...nums);
  }

  function appendBoxscoreMeta(label, value) {
    if (!boxscoreMeta) return;
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
    boxscoreMeta.appendChild(item);
  }

  function renderBoxScoreMeta(data) {
    clearChildren(boxscoreMeta);
    appendBoxscoreMeta('Simulation', data.simulation_id ? String(data.simulation_id) : DASH);
    appendBoxscoreMeta('Tick', isNum(data.tick) ? data.tick.toLocaleString() : DASH);
    appendBoxscoreMeta('Round', isNum(data.round) ? data.round.toLocaleString() : DASH);
    appendBoxscoreMeta('Leader', data.leader || DASH);
  }

  function renderBoxscoreCaveat(data) {
    if (!boxscoreCaveat) return;
    if (data.tracking_since_tick && data.tracking_since_tick > 0) {
      boxscoreCaveat.textContent = `⚠️ Box score tracking started at tick ${data.tracking_since_tick}. Earlier ticks are not counted in these totals.`;
      boxscoreCaveat.style.display = 'block';
    } else {
      boxscoreCaveat.textContent = '';
      boxscoreCaveat.style.display = 'none';
    }
  }

  function renderBoxscoreHint(data) {
    if (!boxscoreHint) return;
    if (data.tick === 0) {
      boxscoreHint.textContent = 'ℹ️ Simulation has not started ticking yet. Advance ticks to watch KPIs populate in real time.';
      boxscoreHint.style.display = 'block';
    } else {
      boxscoreHint.textContent = '';
      boxscoreHint.style.display = 'none';
    }
  }

  function buildCatHeaderCell(cat, leaderId) {
    const th = document.createElement('th');
    th.className = 'boxscore-cat-col';
    const name = document.createElement('div');
    name.className = 'boxscore-cat-name';
    name.textContent = cat.id;
    th.appendChild(name);

    const badgeRow = document.createElement('div');
    badgeRow.className = 'boxscore-badge-row';

    // Crucial: check cat.mode FIRST (cat.agent_mode is legacy/fallback)
    const mode = cat.mode || cat.agent_mode || 'auto';
    const modeBadge = document.createElement('span');
    modeBadge.className = `boxscore-badge boxscore-badge-mode boxscore-badge-mode-${mode}`;
    modeBadge.textContent = MODE_LABELS[mode] || mode.toUpperCase();
    modeBadge.title = MODE_TOOLTIPS[mode] || mode;
    badgeRow.appendChild(modeBadge);

    const aliveBadge = document.createElement('span');
    aliveBadge.className = cat.alive ? 'boxscore-badge boxscore-badge-alive' : 'boxscore-badge boxscore-badge-dead';
    aliveBadge.textContent = cat.alive ? 'Alive' : 'Dead';
    badgeRow.appendChild(aliveBadge);

    if (leaderId && cat.id === leaderId) {
      const leaderBadge = document.createElement('span');
      leaderBadge.className = 'boxscore-badge boxscore-badge-leader';
      leaderBadge.textContent = '👑 Leader';
      badgeRow.appendChild(leaderBadge);
    }

    th.appendChild(badgeRow);

    if (cat.position && isNum(cat.position.x) && isNum(cat.position.y)) {
      const pos = document.createElement('div');
      pos.className = 'boxscore-cat-pos';
      const hpVal = (cat.hp !== undefined && cat.hp !== null) ? cat.hp : (cat.alive ? 100 : 0);
      pos.textContent = `(${cat.position.x}, ${cat.position.y}) · ${hpVal} HP`;
      th.appendChild(pos);
    }
    return th;
  }

  function buildBoxscoreTable(cats, leaderId) {
    const table = document.createElement('table');
    table.className = 'boxscore-table';

    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    const kpiHead = document.createElement('th');
    kpiHead.className = 'boxscore-kpi-col';
    kpiHead.textContent = 'KPI';
    headRow.appendChild(kpiHead);

    cats.forEach((cat) => headRow.appendChild(buildCatHeaderCell(cat, leaderId)));
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    SECTIONS.forEach((section) => {
      const sectionRow = document.createElement('tr');
      sectionRow.className = 'boxscore-section-row';
      const sectionCell = document.createElement('td');
      sectionCell.colSpan = cats.length + 1;
      sectionCell.textContent = section.title;
      sectionRow.appendChild(sectionCell);
      tbody.appendChild(sectionRow);

      section.rows.forEach((row) => {
        const tr = document.createElement('tr');
        const labelCell = document.createElement('th');
        labelCell.className = 'boxscore-stat-label';
        labelCell.setAttribute('scope', 'row');
        labelCell.textContent = row.label;
        labelCell.title = `${row.label} = ${row.tip}`;
        tr.appendChild(labelCell);

        const best = leadingValue(cats, row);
        cats.forEach((cat) => {
          const td = document.createElement('td');
          td.className = 'boxscore-stat-value';
          const raw = cat[row.key];
          td.textContent = row.format(raw);
          const eligible = !row.eligible || row.eligible(cat);
          if (best !== null && eligible && isNum(raw) && raw === best) {
            td.classList.add('stat-leader');
            td.title = row.dir === 'low' ? 'Best in row (lower is better)' : 'Best in row (higher is better)';
          }
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    });

    table.appendChild(tbody);
    return table;
  }

  async function loadBoxScore() {
    try {
      const resp = await fetch(`/api/simulations/${encodeURIComponent(activeSimId)}/boxscore`);
      if (resp.status === 404) {
        setSyncIdle('No Active Game');
        clearChildren(boxscoreMeta);
        clearChildren(boxscoreTableWrap);
        if (boxscoreState) {
          boxscoreState.textContent = 'No active simulation. Start a game in the main window to view the box score.';
          boxscoreState.style.display = 'block';
        }
        stampUpdated(`Checked ${new Date().toLocaleTimeString()} · auto-refreshing every 2s`);
        return;
      }
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      const data = await resp.json();
      setSyncLive();
      renderBoxScoreMeta(data);
      renderBoxscoreCaveat(data);
      renderBoxscoreHint(data);
      clearChildren(boxscoreTableWrap);

      const cats = Array.isArray(data.cats) ? data.cats : [];
      if (cats.length === 0) {
        if (boxscoreState) {
          boxscoreState.textContent = 'No cats registered yet.';
          boxscoreState.style.display = 'block';
        }
        return;
      }

      if (boxscoreState) boxscoreState.style.display = 'none';
      if (boxscoreTableWrap) boxscoreTableWrap.appendChild(buildBoxscoreTable(cats, data.leader || null));
      stampUpdated(`Updated ${new Date().toLocaleTimeString()} · auto-refreshing every 2s`);
    } catch (err) {
      console.error('Failed to load box score data:', err);
      setSyncError();
      if (boxscoreState) {
        boxscoreState.textContent = 'Could not load box score data from simulation.';
        boxscoreState.className = 'boxscore-state boxscore-state-error';
        boxscoreState.style.display = 'block';
      }
    }
  }

  /* ============================================================
     TAB 2: CAT PATH (HEATMAP) LOGIC
     Directly matched to heatmap.js contract (cat.path, cat.ascii, cat.mode)
     ============================================================ */
  function appendHeatmapMeta(label, value) {
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

  function drawCanvas(canvas, cat, arena) {
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const w = arena.width || 100;
    const h = arena.height || 100;
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
      const intensity = Math.sqrt(count / peak);
      const hue = 220 - 200 * intensity;
      const light = 25 + 45 * intensity;
      ctx.fillStyle = `hsl(${hue}, 90%, ${light}%)`;
      ctx.fillRect(x * sx, y * sy, Math.max(1, sx), Math.max(1, sy));
    });

    if (showPathEl && showPathEl.checked && path.length > 1) {
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      path.forEach(([x, y], i) => {
        const px = x * sx + sx / 2;
        const py = y * sy + sy / 2;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();

      const [lx, ly] = path[path.length - 1];
      ctx.fillStyle = '#fef08a';
      ctx.beginPath();
      ctx.arc(lx * sx + sx / 2, ly * sy + sy / 2, 4, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function buildCatHeatmapCard(cat, arena) {
    const card = document.createElement('section');
    card.className = 'heatmap-card';

    const head = document.createElement('div');
    head.className = 'heatmap-card-head';

    const name = document.createElement('span');
    name.className = 'boxscore-cat-name';
    name.textContent = cat.id ? String(cat.id) : 'unknown cat';
    head.appendChild(name);

    // Read cat.mode correctly
    const modeKey = typeof cat.mode === 'string' ? cat.mode : (typeof cat.agent_mode === 'string' ? cat.agent_mode : 'auto');
    const modeBadge = document.createElement('span');
    modeBadge.className = 'boxscore-badge boxscore-badge-mode';
    if (modeKey === 'ws' || modeKey === 'auto') {
      modeBadge.classList.add('boxscore-badge-mode-' + modeKey);
    }
    modeBadge.textContent = MODE_LABELS[modeKey] || (modeKey || 'unknown');
    modeBadge.title = MODE_TOOLTIPS[modeKey] || modeKey;
    head.appendChild(modeBadge);

    const windowSpan = document.createElement('span');
    windowSpan.className = 'heatmap-window';
    windowSpan.textContent = `${isNum(cat.window_ticks) ? cat.window_ticks : 0} ticks · ` +
      `${isNum(cat.unique_tiles) ? cat.unique_tiles.toLocaleString() : 0} unique tiles`;
    head.appendChild(windowSpan);
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

    drawCanvas(canvas, cat, arena);
    return card;
  }

  async function loadHeatmap() {
    try {
      const grid = gridSizeEl ? gridSizeEl.value : '5';
      const last = windowTicksEl && windowTicksEl.value ? windowTicksEl.value : '';
      const params = new URLSearchParams({ grid });
      if (last) params.set('last', last);

      const resp = await fetch(`/api/simulations/${encodeURIComponent(activeSimId)}/heatmap?${params.toString()}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      const data = await resp.json();
      cachedHeatmapData = data;
      setSyncLive();

      clearChildren(heatmapMeta);
      appendHeatmapMeta('Simulation', data.simulation_id ? String(data.simulation_id) : DASH);
      appendHeatmapMeta('Tick', isNum(data.tick) ? data.tick.toLocaleString() : DASH);
      if (data.arena) appendHeatmapMeta('Arena', `${data.arena.width} x ${data.arena.height}`);
      if (isNum(data.grid_size)) appendHeatmapMeta('Digest', `${data.grid_size} x ${data.grid_size}`);

      if (heatmapCaveat) {
        if (data.window_capped) {
          heatmapCaveat.textContent = '⚠️ Map covers only the most recent stretch of history snapshots.';
          heatmapCaveat.style.display = 'block';
        } else {
          heatmapCaveat.style.display = 'none';
        }
      }

      clearChildren(heatmapCats);
      const cats = Array.isArray(data.cats) ? data.cats : [];
      if (cats.length === 0) {
        if (heatmapState) {
          heatmapState.textContent = 'No cat paths recorded yet. Advance ticks to trace movement.';
          heatmapState.style.display = 'block';
        }
        return;
      }

      if (heatmapState) heatmapState.style.display = 'none';
      cats.forEach((cat) => {
        heatmapCats.appendChild(buildCatHeatmapCard(cat, data.arena || { width: 100, height: 100 }));
      });
      stampUpdated(`Updated ${new Date().toLocaleTimeString()} · auto-refreshing every 2s`);
    } catch (err) {
      console.error('Failed to load heatmap:', err);
      if (heatmapState) {
        heatmapState.textContent = 'Could not load movement map from active simulation.';
        heatmapState.className = 'boxscore-state boxscore-state-error';
        heatmapState.style.display = 'block';
      }
    }
  }

  if (gridSizeEl) gridSizeEl.addEventListener('change', loadHeatmap);
  if (windowTicksEl) windowTicksEl.addEventListener('change', loadHeatmap);
  if (showPathEl) showPathEl.addEventListener('change', () => {
    if (cachedHeatmapData) {
      clearChildren(heatmapCats);
      const cats = Array.isArray(cachedHeatmapData.cats) ? cachedHeatmapData.cats : [];
      cats.forEach((cat) => {
        heatmapCats.appendChild(buildCatHeatmapCard(cat, cachedHeatmapData.arena || { width: 100, height: 100 }));
      });
    } else {
      loadHeatmap();
    }
  });

  if (btnCopyDigest) {
    btnCopyDigest.addEventListener('click', async () => {
      try {
        const resp = await fetch(`/api/simulations/${encodeURIComponent(activeSimId)}/heatmap.txt`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const text = await resp.text();
        await navigator.clipboard.writeText(text);
        showFeedback('Copied coarse digest to clipboard!');
      } catch (err) {
        console.error('Failed to copy digest:', err);
        showFeedback('Failed to copy digest', true);
      }
    });
  }

  /* ============================================================
     TAB 3: GAME EVENTS & REPLAY LOGIC
     Single unified event vocabulary: "Elimination" (no duplicate "capture")
     ============================================================ */
  function normalizeEvents(data) {
    const list = [];

    // Authoritative event source: data.events
    if (Array.isArray(data.events) && data.events.length > 0) {
      for (const ev of data.events) {
        let evType = (ev.event_type || 'event').toLowerCase();
        // Unify all kills / captures / eliminations under "elimination"
        if (evType === 'capture' || evType === 'cat_eliminated' || evType === 'eliminated') {
          evType = 'elimination';
        }

        const source = (ev.details && ev.details.source) || (ev.details && ev.details.kind) || '--';
        let detailText = ev.message || '';
        if (evType === 'elimination') {
          detailText = `${ev.target_id || 'victim'} eliminated by ${ev.actor_id} via ${source}`;
        }

        list.push({
          tick: ev.tick,
          round: ev.round,
          type: evType,
          actor: ev.actor_id || '--',
          target: ev.target_id || '',
          position: ev.position || null,
          source: source,
          details: detailText,
        });
      }
    } else if (Array.isArray(data.deaths) && data.deaths.length > 0) {
      // Fallback if data.events not populated
      for (const d of data.deaths) {
        list.push({
          tick: d.tick,
          round: d.round,
          type: 'elimination',
          actor: d.killed_by || '--',
          target: d.id || '',
          position: d.position || null,
          source: d.source || 'slash impact',
          details: `${d.id} (${d.type}) eliminated by ${d.killed_by} via ${d.source}`,
        });
      }
    }

    // Deduplicate by signature
    const seen = new Set();
    const unique = [];
    for (const item of list) {
      const sig = `${item.tick}:${item.type}:${item.actor}:${item.target}:${item.source}`;
      if (!seen.has(sig)) {
        seen.add(sig);
        unique.push(item);
      }
    }
    // Newest events first
    unique.sort((a, b) => b.tick - a.tick);
    return unique;
  }

  function getEventBadgeClass(type) {
    const t = (type || '').toLowerCase();
    if (t.includes('elimination')) return 'event-badge-elimination';
    if (t.includes('damage') || t.includes('combat')) return 'event-badge-damage-dealt';
    if (t.includes('zone') || t.includes('hazard')) return 'event-badge-hazard';
    return 'event-badge-default';
  }

  function getEventIcon(type) {
    const t = (type || '').toLowerCase();
    if (t.includes('elimination')) return '💀';
    if (t.includes('damage') || t.includes('combat')) return '⚔️';
    if (t.includes('zone') || t.includes('hazard')) return '🔥';
    return '⚡';
  }

  async function replayToTick(tick) {
    try {
      showFeedback(`Rewinding board to tick ${tick}...`);
      const resp = await fetch(`/api/simulations/${encodeURIComponent(activeSimId)}/seek`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tick: Number(tick) }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const state = await resp.json();
      if (replayCurrentTick) replayCurrentTick.textContent = String(state.tick);
      showFeedback(`Board successfully rewound to tick ${state.tick}!`);
      refreshActiveTab();
    } catch (err) {
      console.error('Failed to seek tick:', err);
      showFeedback(`Seek failed: ${err.message}`, true);
    }
  }

  function renderEventsTable(events) {
    clearChildren(eventsTableBody);
    if (!eventsTableBody) return;

    const actorVal = eventActorFilter ? eventActorFilter.value.trim().toLowerCase() : '';
    const searchVal = eventSearchInput ? eventSearchInput.value.trim().toLowerCase() : '';

    const filtered = events.filter((ev) => {
      // Category filter: all, elimination, combat, hazard
      if (activeEventFilter !== 'all') {
        const t = ev.type.toLowerCase();
        if (activeEventFilter === 'elimination' && !t.includes('elimination')) return false;
        if (activeEventFilter === 'combat' && !t.includes('damage') && !t.includes('combat')) return false;
        if (activeEventFilter === 'hazard' && !t.includes('hazard') && !t.includes('zone')) return false;
      }
      // Actor filter
      if (actorVal) {
        const a = (ev.actor || '').toLowerCase();
        const tg = (ev.target || '').toLowerCase();
        if (a !== actorVal && tg !== actorVal) return false;
      }
      // Search text filter
      if (searchVal) {
        const haystack = `${ev.tick} ${ev.type} ${ev.actor} ${ev.target} ${ev.source} ${ev.details}`.toLowerCase();
        if (!haystack.includes(searchVal)) return false;
      }
      return true;
    });

    if (filtered.length === 0) {
      if (eventsState) {
        eventsState.textContent = events.length === 0 ? 'No events recorded in simulation yet.' : 'No events match your current filter.';
        eventsState.style.display = 'block';
      }
      return;
    }

    if (eventsState) eventsState.style.display = 'none';

    filtered.forEach((ev) => {
      const tr = document.createElement('tr');

      // Col 1: Tick
      const tdTick = document.createElement('td');
      tdTick.style.fontWeight = '700';
      tdTick.textContent = String(ev.tick);
      tr.appendChild(tdTick);

      // Col 2: Badge
      const tdBadge = document.createElement('td');
      const badge = document.createElement('span');
      badge.className = `event-badge ${getEventBadgeClass(ev.type)}`;
      const label = ev.type === 'elimination' ? 'Elimination' : ev.type.replace('_', ' ');
      badge.textContent = `${getEventIcon(ev.type)} ${label}`;
      tdBadge.appendChild(badge);
      tr.appendChild(tdBadge);

      // Col 3: Actor / Target
      const tdActors = document.createElement('td');
      const actorSpan = document.createElement('strong');
      actorSpan.textContent = ev.actor;
      tdActors.appendChild(actorSpan);
      if (ev.target) {
        const arrow = document.createTextNode(' ➔ ');
        const targetSpan = document.createElement('span');
        targetSpan.textContent = ev.target;
        tdActors.appendChild(arrow);
        tdActors.appendChild(targetSpan);
      }
      tr.appendChild(tdActors);

      // Col 4: Coordinates
      const tdCoords = document.createElement('td');
      if (ev.position && isNum(ev.position.x) && isNum(ev.position.y)) {
        tdCoords.textContent = `(${ev.position.x}, ${ev.position.y})`;
      } else {
        tdCoords.textContent = DASH;
      }
      tr.appendChild(tdCoords);

      // Col 5: Source weapon
      const tdSource = document.createElement('td');
      tdSource.textContent = ev.source || DASH;
      tr.appendChild(tdSource);

      // Col 6: Details
      const tdDetails = document.createElement('td');
      tdDetails.textContent = ev.details || DASH;
      tr.appendChild(tdDetails);

      // Col 7: Replay action button
      const tdReplay = document.createElement('td');
      tdReplay.style.textAlign = 'center';
      const btnReplay = document.createElement('button');
      btnReplay.className = 'btn-replay-event';
      btnReplay.textContent = '⏪ Replay';
      btnReplay.title = `Rewind simulation board to tick ${ev.tick}`;
      btnReplay.addEventListener('click', () => replayToTick(ev.tick));
      tdReplay.appendChild(btnReplay);
      tr.appendChild(tdReplay);

      eventsTableBody.appendChild(tr);
    });
  }

  function updateActorFilterOptions(events) {
    if (!eventActorFilter) return;
    const currentSelected = eventActorFilter.value;
    const actors = new Set();
    events.forEach((ev) => {
      if (ev.actor && ev.actor !== '--') actors.add(ev.actor);
      if (ev.target && ev.target !== '--') actors.add(ev.target);
    });

    clearChildren(eventActorFilter);
    const allOpt = document.createElement('option');
    allOpt.value = '';
    allOpt.textContent = 'All Animals';
    eventActorFilter.appendChild(allOpt);

    Array.from(actors).sort().forEach((act) => {
      const opt = document.createElement('option');
      opt.value = act;
      opt.textContent = act;
      if (act === currentSelected) opt.selected = true;
      eventActorFilter.appendChild(opt);
    });
  }

  async function loadEvents() {
    try {
      const resp = await fetch(`/api/simulations/${encodeURIComponent(activeSimId)}/events`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      cachedEventsData = data;

      if (replayCurrentTick && isNum(data.tick)) {
        replayCurrentTick.textContent = String(data.tick);
      }

      const events = normalizeEvents(data);
      if (eventCountBadge) {
        eventCountBadge.textContent = String(events.length);
      }

      updateActorFilterOptions(events);
      renderEventsTable(events);
      stampUpdated(`Updated ${new Date().toLocaleTimeString()} · auto-refreshing every 2s`);
    } catch (err) {
      console.error('Failed to load game events:', err);
      if (eventsState) {
        eventsState.textContent = 'Could not load events from active simulation.';
        eventsState.className = 'boxscore-state boxscore-state-error';
        eventsState.style.display = 'block';
      }
    }
  }

  // Filter chips listeners
  filterChips.forEach((chip) => {
    chip.addEventListener('click', () => {
      filterChips.forEach((c) => c.classList.remove('active'));
      chip.classList.add('active');
      activeEventFilter = chip.dataset.filter || 'all';
      if (cachedEventsData) {
        renderEventsTable(normalizeEvents(cachedEventsData));
      } else {
        loadEvents();
      }
    });
  });

  if (eventActorFilter) {
    eventActorFilter.addEventListener('change', () => {
      if (cachedEventsData) renderEventsTable(normalizeEvents(cachedEventsData));
    });
  }

  if (eventSearchInput) {
    eventSearchInput.addEventListener('input', () => {
      if (cachedEventsData) renderEventsTable(normalizeEvents(cachedEventsData));
    });
  }

  // Replay controls listeners
  if (btnReplayStepBack) {
    btnReplayStepBack.addEventListener('click', async () => {
      try {
        const resp = await fetch('/api/simulations/active/step_back', { method: 'POST' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const state = await resp.json();
        showFeedback(`Stepped back to tick ${state.tick}`);
        refreshActiveTab();
      } catch (err) {
        showFeedback(`Step back failed: ${err.message}`, true);
      }
    });
  }

  if (btnReplayStepForward) {
    btnReplayStepForward.addEventListener('click', async () => {
      try {
        const resp = await fetch('/api/simulations/active/step', { method: 'POST' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const state = await resp.json();
        showFeedback(`Advanced to tick ${state.tick}`);
        refreshActiveTab();
      } catch (err) {
        showFeedback(`Step forward failed: ${err.message}`, true);
      }
    });
  }

  if (btnSeekTick && inputSeekTick) {
    btnSeekTick.addEventListener('click', () => {
      const val = parseInt(inputSeekTick.value, 10);
      if (isNaN(val) || val < 0) {
        showFeedback('Please enter a valid tick number', true);
        return;
      }
      replayToTick(val);
    });
  }

  /* ============================================================
     GLOBAL LIFECYCLE & POLLING
     ============================================================ */
  function refreshActiveTab() {
    if (currentTab === 'tabBoxscore') {
      loadBoxScore();
    } else if (currentTab === 'tabCatpath') {
      loadHeatmap();
    } else if (currentTab === 'tabEvents') {
      loadEvents();
    }
  }

  if (btnRefreshActiveTab) {
    btnRefreshActiveTab.addEventListener('click', refreshActiveTab);
  }

  if (btnCloseWindow) {
    btnCloseWindow.addEventListener('click', () => window.close());
  }

  // Initial load
  loadBoxScore();
  // Background polling: refreshes active tab + keeps event badge updated
  setInterval(() => {
    refreshActiveTab();
    if (currentTab !== 'tabEvents') {
      fetch(`/api/simulations/${encodeURIComponent(activeSimId)}/events`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (d && eventCountBadge) {
            const evs = normalizeEvents(d);
            eventCountBadge.textContent = String(evs.length);
          }
        })
        .catch(() => {});
    }
  }, REFRESH_MS);
});
