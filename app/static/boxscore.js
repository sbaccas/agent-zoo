/**
 * Box Score — Independent Standalone Window Controller
 * Zero raw-HTML-injection compliant: every node is built with createElement + textContent.
 * Polls /api/simulations/active/boxscore in real time.
 */
document.addEventListener('DOMContentLoaded', () => {
  // DOM Elements
  const syncStatus = document.getElementById('syncStatus');
  const boxscoreMeta = document.getElementById('boxscoreMeta');
  const boxscoreCaveat = document.getElementById('boxscoreCaveat');
  const boxscoreHint = document.getElementById('boxscoreHint');
  const boxscoreState = document.getElementById('boxscoreState');
  const boxscoreTableWrap = document.getElementById('boxscoreTableWrap');
  const boxscoreUpdated = document.getElementById('boxscoreUpdated');

  // Buttons
  const btnRefreshBoxscore = document.getElementById('btnRefreshBoxscore');
  const btnCloseWindow = document.getElementById('btnCloseWindow');
  const btnOpenHeatmap = document.getElementById('btnOpenHeatmap');

  const REFRESH_MS = 2000;

  // Agent mode -> human label. "ws" is the LLM agent, "auto" is the built-in heuristic.
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


  const DASH = '—';

  function fmtInt(val) {
    if (val === null || val === undefined || isNaN(val)) return DASH;
    return Number(val).toLocaleString();
  }

  function fmtFloat(val, digits) {
    if (val === null || val === undefined || isNaN(val)) return DASH;
    return Number(val).toFixed(digits);
  }

  // Contract sends rates/ratios as fractions 0.0-1.0; render them as percentages.
  function fmtPct(val, digits = 1) {
    if (val === null || val === undefined || isNaN(val)) return DASH;
    return (Number(val) * 100).toFixed(digits) + '%';
  }

  /**
   * Decides whether a cat is even a candidate for a row's leader highlight.
   *
   * A cat with no agent attached never had a model to miss with, so its empty
   * decision counters are not an achievement. Without this, a heuristic-only
   * cat sitting at 0 fallbacks would outrank an LLM cat's 5 and the Control
   * section would decorate the exact opposite of what it measures -- the
   * agentless cat did not win that contest, it never entered it.
   *
   * `model_control_pct` is the marker because the server emits it as null
   * precisely when the decision denominator is empty (see _rate in
   * app/game/stats.py). Testing `decisions_* === 0` instead would lock out a
   * genuine agent that has simply never missed a tick, which is the one cat
   * that most deserves to win the row.
   */
  function hasAttachedAgent(cat) {
    return isNum(cat.model_control_pct);
  }

  /**
   * KPI row definitions grouped into box-score sections.
   * `dir` encodes which direction wins the row highlight:
   *   'high' = higher value is better, 'low' = lower value is better,
   *   'none' = raw context with no better direction, so nothing is highlighted.
   * NOTE: the 'low' rows are Tiles per kill, Whiffs, Declined, Wall bonks,
   * Locked, Ticks per tile, Fallback decisions and Stale answers.
   *
   * `eligible` is an optional per-row opt-in: a predicate on a cat that gates
   * whether that cat may win or be highlighted for the row. Rows without one
   * put every cat in contention, which is the behaviour of every section other
   * than Control.
   *
   * Tooltip formulas are kept verbatim in step with app/game/stats.py so the
   * team shares one unambiguous definition per KPI.
   */
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
      title: 'Slashing',
      rows: [
        { label: 'Slash attempts', key: 'slash_attempts', dir: 'high', format: (v) => fmtInt(v), tip: 'slash actions executed' },
        { label: 'Slash accuracy', key: 'slash_accuracy', dir: 'high', format: (v) => fmtPct(v), tip: 'landed slashes / slash attempts. A slash counts as landed if it killed at any point in its lethal window, including the tick after impact' },
        { label: 'Whiffs', key: 'whiffs', dir: 'low', format: (v) => fmtInt(v), tip: 'slash attempts - landed slashes' },
        { label: 'Multi-kills', key: 'multi_kills', dir: 'high', format: (v) => fmtInt(v), tip: 'slashes that captured 2 or more mice at once' },
      ],
    },
    {
      title: 'Conversion',
      rows: [
        { label: 'Strike-zone chances', key: 'zone_chances', dir: 'high', format: (v) => fmtInt(v), tip: 'ticks with a mouse inside this cat\u2019s slash AoE range' },
        { label: 'Conversion', key: 'zone_conversion', dir: 'high', format: (v) => fmtPct(v), tip: 'strike-zone slash attempts / strike-zone chances' },
        { label: 'Declined', key: 'zone_declined', dir: 'low', format: (v) => fmtInt(v), tip: 'strike-zone chances - strike-zone slash attempts' },
      ],
    },
    {
      title: 'Movement',
      rows: [
        { label: 'Tiles run', key: 'tiles_run', dir: 'high', format: (v) => fmtInt(v), tip: 'total tiles of distance travelled' },
        { label: 'Ground covered', key: 'ground_covered', dir: 'high', format: (v) => fmtPct(v), tip: 'unique tiles visited / total arena tiles' },
        { label: 'Unique tiles', key: 'unique_tiles', dir: 'high', format: (v) => fmtInt(v), tip: 'distinct arena tiles this cat has stood on' },
        { label: 'Speed (tiles/tick)', key: 'speed_tiles_per_tick', dir: 'high', format: (v) => fmtFloat(v, 3), tip: 'tiles run / ticks elapsed. A mouse moves at 0.5; walking gives 0.33 and cannot win a chase, lunging gives 1.0' },
        { label: 'Moves', key: 'moves', dir: 'none', format: (v) => fmtInt(v), tip: 'single-tile steps taken (1 tile per move_cooldown ticks)' },
        { label: 'Lunges', key: 'lunges', dir: 'none', format: (v) => fmtInt(v), tip: 'two-tile dashes taken (2 tiles per lunge_cooldown ticks)' },
        { label: 'Lunge share', key: 'lunge_share', dir: 'high', format: (v) => fmtPct(v), tip: 'lunges / (moves + lunges)' },
        { label: 'Wall bonks', key: 'wall_bonks', dir: 'low', format: (v) => fmtInt(v), tip: 'lunges that covered less than 2 tiles because they clipped an arena boundary' },
      ],
    },
    {
      title: 'Positioning',
      rows: [
        { label: 'Prey in sight', key: 'prey_in_sight_pct', dir: 'high', format: (v) => fmtPct(v), tip: 'ticks with a mouse in the vision box / ticks free (locked ticks excluded, since visibility is only sampled when the cat acts)' },
        { label: 'Locked', key: 'locked_pct', dir: 'low', format: (v) => fmtPct(v), tip: 'ticks locked in attack/lunge recovery / (ticks free + ticks locked)' },
        { label: 'Ticks per tile', key: 'ticks_per_tile', dir: 'low', format: (v) => fmtFloat(v, 2), tip: '(ticks free + ticks locked) / unique tiles — how long the cat loiters per tile it occupies. Higher means camping' },
      ],
    },
    {
      title: 'Control',
      // Every row here is gated on the cat having had an agent at all: these
      // counters only move for a cat a model was attached to, so an agentless
      // cat's zeroes are an absence of data rather than a good score.
      rows: [
        { label: 'Model control', key: 'model_control_pct', dir: 'high', eligible: hasAttachedAgent, format: (v) => fmtPct(v), tip: 'decisions by model / (decisions by model + fallbacks) — dash means no agent was ever attached to this cat, which is not the same as 0%' },
        { label: 'Decisions by model', key: 'decisions_by_model', dir: 'high', eligible: hasAttachedAgent, format: (v) => fmtInt(v), tip: 'ticks where the attached agent answered in time and its action was played' },
        { label: 'Fallback decisions', key: 'decisions_by_fallback', dir: 'low', eligible: hasAttachedAgent, format: (v) => fmtInt(v), tip: 'ticks where no usable answer arrived and the built-in heuristic played instead' },
        { label: 'Stale answers', key: 'decisions_stale', dir: 'low', eligible: hasAttachedAgent, format: (v) => fmtInt(v), tip: 'of the fallbacks, answers that did arrive but described a tick the board had already moved past' },
      ],
    },
  ];


  function clearChildren(el) {
    if (!el) return;
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function setSyncLive() {
    if (!syncStatus) return;
    syncStatus.textContent = 'Active (Live)';
    syncStatus.style.background = '#dcfce7';
    syncStatus.style.color = '#166534';
  }

  function setSyncError() {
    if (!syncStatus) return;
    syncStatus.textContent = 'Sync Error';
    syncStatus.style.background = '#fee2e2';
    syncStatus.style.color = '#991b1b';
  }

  function setSyncIdle(text) {
    if (!syncStatus) return;
    syncStatus.textContent = text;
    syncStatus.style.background = '#e2e8f0';
    syncStatus.style.color = '#475569';
  }

  function showState(text, isError = false) {
    if (!boxscoreState) return;
    boxscoreState.textContent = text;
    boxscoreState.className = isError ? 'boxscore-state boxscore-state-error' : 'boxscore-state';
    boxscoreState.style.display = 'block';
  }

  function hideState() {
    if (!boxscoreState) return;
    boxscoreState.style.display = 'none';
  }

  function isNum(val) {
    return typeof val === 'number' && !isNaN(val);
  }

  /** Returns the winning value for a row, or null when there is nothing to highlight. */
  function leadingValue(cats, row) {
    // Raw counts like Moves and Lunges have no better direction on their own;
    // they are context for Lunge share rather than a contest. Without this a
    // higher count would be decorated as if it were an achievement.
    if (row.dir !== 'low' && row.dir !== 'high') return null;
    // Rows may exclude cats that were never in the running -- see `eligible`
    // on the row definitions.
    const contenders = row.eligible ? cats.filter(row.eligible) : cats;
    // One contender is not a contest. Naming it "best in row" would restate the
    // same false comparison the filter just removed, in a one-LLM-cat game.
    if (contenders.length < 2) return null;
    const values = contenders.map((cat) => cat[row.key]).filter(isNum);
    if (values.length === 0) return null;
    const distinct = new Set(values);
    if (distinct.size < 2) return null; // single cat or a tie: highlight nothing
    return row.dir === 'low' ? Math.min.apply(null, values) : Math.max.apply(null, values);
  }

  function appendMetaItem(label, value) {
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

  function renderMeta(data) {
    clearChildren(boxscoreMeta);
    if (!boxscoreMeta) return;

    appendMetaItem('Simulation', data.simulation_id ? String(data.simulation_id) : DASH);
    appendMetaItem('Status', data.status ? String(data.status).toUpperCase() : DASH);
    appendMetaItem('Tick', isNum(data.tick) ? fmtInt(data.tick) : DASH);
    appendMetaItem('Round', isNum(data.round) ? fmtInt(data.round) : DASH);

    if (data.arena) {
      const w = isNum(data.arena.width) ? data.arena.width : DASH;
      const h = isNum(data.arena.height) ? data.arena.height : DASH;
      appendMetaItem('Arena', `${w} x ${h}`);
    }

    // `leader` is null both on a genuine tie and on an untouched board; only
    // call it a tie once at least one kill has actually been recorded.
    if (data.leader) {
      appendMetaItem('Leader', String(data.leader));
    } else {
      const cats = Array.isArray(data.cats) ? data.cats : [];
      const anyKills = cats.some((cat) => isNum(cat.kills) && cat.kills > 0);
      appendMetaItem('Leader', anyKills ? 'Tied' : DASH);
    }
  }

  function renderCaveat(data) {
    if (!boxscoreCaveat) return;
    const since = data.tracking_since_tick;
    if (isNum(since) && since > 0) {
      boxscoreCaveat.textContent =
        `⚠️ Tracking since tick ${fmtInt(since)} — this game was already in flight, so earlier ` +
        'activity is missing. Reset the game for a full box score.';
      boxscoreCaveat.style.display = 'block';
    } else {
      boxscoreCaveat.textContent = '';
      boxscoreCaveat.style.display = 'none';
    }
  }

  /** Explains an all-zero table on a game that has not ticked yet. */
  function renderHint(data) {
    if (!boxscoreHint) return;
    if (isNum(data.tick) && data.tick === 0) {
      boxscoreHint.textContent =
        'No data yet — start the simulation and these numbers will fill in from tick 1. ' +
        'Rates stay blank (—) until their denominator is non-zero.';
      boxscoreHint.style.display = 'block';
    } else {
      boxscoreHint.textContent = '';
      boxscoreHint.style.display = 'none';
    }
  }


  /** Builds the <th> for one cat column: id, mode badge, alive state, position. */
  function buildCatHeaderCell(cat, leaderId) {
    const th = document.createElement('th');
    th.className = 'boxscore-cat-col';
    th.setAttribute('scope', 'col');

    const nameRow = document.createElement('div');
    nameRow.className = 'boxscore-cat-name';
    nameRow.textContent = cat.id ? String(cat.id) : 'unknown cat';
    th.appendChild(nameRow);

    const badgeRow = document.createElement('div');
    badgeRow.className = 'boxscore-badge-row';

    const modeBadge = document.createElement('span');
    const modeKey = typeof cat.mode === 'string' ? cat.mode : '';
    modeBadge.className = 'boxscore-badge boxscore-badge-mode';
    if (modeKey === 'ws' || modeKey === 'auto') {
      modeBadge.classList.add('boxscore-badge-mode-' + modeKey);
    }
    modeBadge.textContent = MODE_LABELS[modeKey] || (modeKey || 'unknown');
    modeBadge.title = MODE_TOOLTIPS[modeKey] || `Controller mode: ${modeKey || 'unknown'}`;
    badgeRow.appendChild(modeBadge);

    const aliveBadge = document.createElement('span');
    aliveBadge.className = cat.alive ? 'boxscore-badge boxscore-badge-alive' : 'boxscore-badge boxscore-badge-dead';
    aliveBadge.textContent = cat.alive ? 'Alive' : 'Out';
    badgeRow.appendChild(aliveBadge);

    if (leaderId && cat.id === leaderId) {
      const leaderBadge = document.createElement('span');
      leaderBadge.className = 'boxscore-badge boxscore-badge-leader';
      leaderBadge.textContent = '★ Leader';
      leaderBadge.title = 'Currently leading the box score';
      badgeRow.appendChild(leaderBadge);
    }

    th.appendChild(badgeRow);

    const pos = document.createElement('div');
    pos.className = 'boxscore-cat-pos';
    if (cat.position && isNum(cat.position.x) && isNum(cat.position.y)) {
      pos.textContent = `Position (${cat.position.x}, ${cat.position.y})`;
    } else {
      pos.textContent = `Position ${DASH}`;
    }
    th.appendChild(pos);

    return th;
  }

  function buildTable(cats, leaderId) {
    const table = document.createElement('table');
    table.className = 'boxscore-table';

    // Header
    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');

    const kpiHead = document.createElement('th');
    kpiHead.className = 'boxscore-kpi-col';
    kpiHead.setAttribute('scope', 'col');
    kpiHead.textContent = 'KPI';
    headRow.appendChild(kpiHead);

    cats.forEach((cat) => headRow.appendChild(buildCatHeaderCell(cat, leaderId)));
    thead.appendChild(headRow);
    table.appendChild(thead);

    // Body: one group of rows per section
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
          // The eligibility check is repeated here on purpose: an excluded cat
          // can still happen to hold the winning number (an agentless cat's 0
          // fallbacks ties an LLM cat that has never missed), and matching on
          // value alone would decorate it anyway.
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

  function renderBoxScore(data) {
    const cats = Array.isArray(data.cats) ? data.cats : [];

    renderMeta(data);
    renderCaveat(data);
    renderHint(data);
    clearChildren(boxscoreTableWrap);

    if (cats.length === 0) {
      showState('No cats are registered in this simulation yet. Start or reset the game to populate the box score.');
      return;
    }

    hideState();
    if (boxscoreTableWrap) {
      boxscoreTableWrap.appendChild(buildTable(cats, data.leader || null));
    }
  }

  function renderNoSimulation() {
    clearChildren(boxscoreMeta);
    clearChildren(boxscoreTableWrap);
    if (boxscoreCaveat) {
      boxscoreCaveat.textContent = '';
      boxscoreCaveat.style.display = 'none';
    }
    if (boxscoreHint) {
      boxscoreHint.textContent = '';
      boxscoreHint.style.display = 'none';
    }
    showState('No active simulation. Start a game in the main window and the box score will fill in automatically.');
  }

  function renderFetchError() {
    clearChildren(boxscoreTableWrap);
    showState('Could not connect to active game simulation. Is server running?', true);
  }

  function stampUpdated(text) {
    if (!boxscoreUpdated) return;
    boxscoreUpdated.textContent = text;
  }

  async function loadBoxScore() {
    try {
      const resp = await fetch('/api/simulations/active/boxscore');

      if (resp.status === 404) {
        setSyncIdle('No Active Game');
        renderNoSimulation();
        stampUpdated(`Checked ${new Date().toLocaleTimeString()} · auto-refreshing every 2s`);
        return;
      }

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }

      const data = await resp.json();
      setSyncLive();
      renderBoxScore(data);
      stampUpdated(`Updated ${new Date().toLocaleTimeString()} · auto-refreshing every 2s`);
    } catch (err) {
      console.error('Failed to load box score data:', err);
      setSyncError();
      renderFetchError();
      stampUpdated(`Last attempt failed at ${new Date().toLocaleTimeString()}`);
    }
  }

  /**
   * Opens the movement map in its own window.
   *
   * The `?v=` stamp is the same trick app.js documents for this pair of
   * popouts: re-opening a named window that already exists is a no-op focus,
   * so an already-open map would never pick up a new build. A throwaway
   * timestamp makes each open a genuinely different URL and forces the
   * navigation. The server routes /heatmap with FileResponse and ignores the
   * query string, and the page does not read its own params, so it is inert
   * beyond that.
   */
  function openHeatmapWindow() {
    const width = 1000;
    const height = 860;
    const screenW = window.screen.availWidth || window.innerWidth;
    const left = Math.max(10, Math.min(screenW - width - 20, (window.screenX || 0) + 40));
    const top = Math.max(20, (window.screenY || 0) + 30);
    const features = `width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes,status=no,toolbar=no,menubar=no,location=no`;

    const popup = window.open(`/heatmap?v=${Date.now()}`, 'CMMovementMap', features);
    if (popup && !popup.closed) {
      popup.focus();
    } else {
      console.warn('Movement Map pop-out was blocked by the browser. Allow pop-ups for this site to view it.');
    }
  }

  // Event Listeners
  if (btnRefreshBoxscore) btnRefreshBoxscore.addEventListener('click', loadBoxScore);
  if (btnCloseWindow) btnCloseWindow.addEventListener('click', () => window.close());
  if (btnOpenHeatmap) btnOpenHeatmap.addEventListener('click', openHeatmapWindow);

  // Initial load + polling
  loadBoxScore();
  setInterval(loadBoxScore, REFRESH_MS);
});
