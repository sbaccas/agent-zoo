// Cat & Mouse Simulation Frontend Controller (Light Mode + Orientation + Step Back + Event Feed)

(function () {
  'use strict';

  // Elements
  const statusBadge = document.getElementById('statusBadge');
  const metricTick = document.getElementById('metricTick');
  const metricRound = document.getElementById('metricRound');
  const metricCats = document.getElementById('metricCats');
  const metricMice = document.getElementById('metricMice');
  const metricCaptures = document.getElementById('metricCaptures');
  const metricDimensions = document.getElementById('metricDimensions');
  const speedSelect = document.getElementById('speedSelect');

  const btnStart = document.getElementById('btnStart');
  const btnPause = document.getElementById('btnPause');
  const btnStepBack = document.getElementById('btnStepBack');
  const btnStep = document.getElementById('btnStep');
  const btnStop = document.getElementById('btnStop');
  const btnReset = document.getElementById('btnReset');

  const btnAddCat1 = document.getElementById('btnAddCat1');
  const btnAddCat10 = document.getElementById('btnAddCat10');
  const btnRemCat1 = document.getElementById('btnRemCat1');
  const btnAddMouse10 = document.getElementById('btnAddMouse10');
  const btnAddMouse50 = document.getElementById('btnAddMouse50');
  const btnRemMouse10 = document.getElementById('btnRemMouse10');

  const eventFeedList = document.getElementById('eventFeedList');
  const eventCount = document.getElementById('eventCount');

  const canvas = document.getElementById('boardCanvas');
  const ctx = canvas.getContext('2d');
  const canvasWrapper = document.getElementById('canvasWrapper');

  let currentSimId = 'active';
  let pollTimeout = null;
  let currentState = null;

  // Light mode colors
  const COLOR_BG = '#ffffff';
  const COLOR_GRID_LINE = 'rgba(203, 213, 225, 0.4)';
  const COLOR_CAT = '#dc2626';       // Crimson Red
  const COLOR_MOUSE = '#0284c7';     // Sky Blue
  const COLOR_CAT_BG = 'rgba(220, 38, 38, 0.12)';
  const COLOR_MOUSE_BG = 'rgba(2, 132, 199, 0.12)';
  const COLOR_POINTER_CAT = '#b91c1c';
  const COLOR_POINTER_MOUSE = '#0369a1';

  function resizeCanvas() {
    if (!canvasWrapper) return;
    const rect = canvasWrapper.getBoundingClientRect();
    const size = Math.min(rect.width - 24, rect.height - 24, 1000);
    if (size > 100) {
      canvas.width = Math.floor(size);
      canvas.height = Math.floor(size);
      if (currentState) {
        renderBoard(currentState);
      }
    }
  }

  window.addEventListener('resize', resizeCanvas);

  function getFacingAngle(facing) {
    switch (facing) {
      case 'north': return -Math.PI / 2;
      case 'south': return Math.PI / 2;
      case 'east': return 0;
      case 'west': return Math.PI;
      default: return 0;
    }
  }

  function renderBoard(state) {
    const width = state.width || 100;
    const height = state.height || 100;
    const cellW = canvas.width / width;
    const cellH = canvas.height / height;

    // Clear background
    ctx.fillStyle = COLOR_BG;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Grid lines
    ctx.strokeStyle = COLOR_GRID_LINE;
    ctx.lineWidth = 1;
    const step = width <= 20 ? 1 : 10;
    for (let x = 0; x <= width; x += step) {
      ctx.beginPath();
      ctx.moveTo(x * cellW, 0);
      ctx.lineTo(x * cellW, canvas.height);
      ctx.stroke();
    }
    for (let y = 0; y <= height; y += step) {
      ctx.beginPath();
      ctx.moveTo(0, y * cellH);
      ctx.lineTo(canvas.width, y * cellH);
      ctx.stroke();
    }

    // Render Recent Capture Marker (only on the exact tick it occurs)
    if (state.recent_events) {
      for (const ev of state.recent_events) {
        if (ev.event_type === 'capture' && ev.position && ev.tick === state.tick) {
          const cx = ev.position.x * cellW + cellW / 2;
          const cy = ev.position.y * cellH + cellH / 2;
          ctx.save();
          ctx.strokeStyle = '#dc2626';
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(cx, cy, Math.max(8, cellW * 1.2), 0, Math.PI * 2);
          ctx.stroke();
          ctx.restore();
        }
      }
    }

    // Render Entities
    const entities = state.entities || [];
    const fontSize = Math.max(10, Math.floor(cellW * 1.3));

    for (let i = 0; i < entities.length; i++) {
      const e = entities[i];
      if (!e.alive) continue;

      const cx = e.position.x * cellW + cellW / 2;
      const cy = e.position.y * cellH + cellH / 2;
      const isCat = (e.type === 'cat');
      const angle = getFacingAngle(e.facing || 'north');

      ctx.save();
      ctx.translate(cx, cy);

      // Subtle background glow disc
      ctx.fillStyle = isCat ? COLOR_CAT_BG : COLOR_MOUSE_BG;
      const bgRadius = isCat ? Math.max(7, cellW * 0.9) : Math.max(5, cellW * 0.7);
      ctx.beginPath();
      ctx.arc(0, 0, bgRadius, 0, Math.PI * 2);
      ctx.fill();

      // Directional pointer / nose indicator
      ctx.save();
      ctx.rotate(angle);
      ctx.fillStyle = isCat ? COLOR_POINTER_CAT : COLOR_POINTER_MOUSE;
      const pointerDist = bgRadius + 2;
      ctx.beginPath();
      ctx.moveTo(pointerDist + 3, 0);
      ctx.lineTo(pointerDist - 3, -3);
      ctx.lineTo(pointerDist - 3, 3);
      ctx.closePath();
      ctx.fill();
      ctx.restore();

      // Draw ASCII Character 'C' or 'M'
      ctx.fillStyle = isCat ? COLOR_CAT : COLOR_MOUSE;
      ctx.font = `bold ${fontSize}px monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(isCat ? 'C' : 'M', 0, 1);

      ctx.restore();
    }
  }

  let lastEventSignature = '';
  function renderEventFeed(events) {
    if (!eventFeedList) return;

    if (!events || events.length === 0) {
      if (lastEventSignature === 'empty') return;
      lastEventSignature = 'empty';
      eventFeedList.replaceChildren();
      const empty = document.createElement('div');
      empty.className = 'event-empty';
      empty.textContent = 'No events yet. Click Start or Step!';
      eventFeedList.appendChild(empty);
      if (eventCount) eventCount.textContent = '0 events';
      return;
    }

    const sig = events.map(e => `${e.tick}:${e.actor_id}:${e.event_type}`).join('|');
    if (sig === lastEventSignature) {
      return; // Do not touch DOM if events have not changed
    }
    lastEventSignature = sig;

    eventFeedList.replaceChildren();
    if (eventCount) {
      eventCount.textContent = `${events.length} events`;
    }

    // Show newest first
    const reversed = [...events].reverse();
    for (const ev of reversed) {
      const item = document.createElement('div');
      item.className = `event-item event-${ev.event_type}`;

      const pill = document.createElement('span');
      pill.className = `event-pill pill-${ev.event_type}`;
      pill.textContent = ev.event_type.toUpperCase();

      const text = document.createElement('span');
      text.className = 'event-text';
      text.textContent = ev.message;

      item.appendChild(pill);
      item.appendChild(text);
      eventFeedList.appendChild(item);
    }
  }

  function updateUI(state) {
    currentState = state;

    // Update Status Badge
    const status = (state.status || 'CREATED').toUpperCase();
    statusBadge.textContent = status;
    statusBadge.className = 'badge';
    if (status === 'RUNNING') {
      statusBadge.classList.add('status-running');
    } else if (status === 'PAUSED') {
      statusBadge.classList.add('status-paused');
    } else if (status === 'STOPPED') {
      statusBadge.classList.add('status-stopped');
    }

    // Update Step Back button state
    if (btnStepBack) {
      btnStepBack.disabled = !state.can_step_back;
      btnStepBack.style.opacity = state.can_step_back ? '1' : '0.5';
    }

    // Update Metrics safely with textContent
    metricTick.textContent = String(state.tick || 0);
    metricRound.textContent = String(state.round || 0);
    metricCats.textContent = String(state.cat_count || 0);
    metricMice.textContent = String(state.mouse_count || 0);
    metricCaptures.textContent = String(state.captures || 0);
    metricDimensions.textContent = `${state.width || 100} × ${state.height || 100}`;

    renderBoard(state);
    renderEventFeed(state.recent_events);
  }

  async function apiCall(endpoint, method = 'POST', body = null) {
    try {
      const options = {
        method: method,
        headers: { 'Content-Type': 'application/json' },
      };
      if (body) {
        options.body = JSON.stringify(body);
      }
      const res = await fetch(endpoint, options);
      if (!res.ok) return null;
      return await res.json();
    } catch (err) {
      console.error('API error:', err);
      return null;
    }
  }

  // Smart adaptive polling
  async function pollLoop() {
    const data = await apiCall(`/api/simulations/${currentSimId}/state`, 'GET');
    if (data) {
      updateUI(data);
    }

    const delay = (currentState && currentState.status === 'running') ? 150 : 2000;
    pollTimeout = setTimeout(pollLoop, delay);
  }

  async function triggerAction(endpoint, body = null) {
    const data = await apiCall(endpoint, 'POST', body);
    if (data) {
      updateUI(data);
    }
    if (pollTimeout) clearTimeout(pollTimeout);
    pollLoop();
  }

  // Event Listeners
  btnStart.addEventListener('click', () => triggerAction(`/api/simulations/${currentSimId}/start`));
  btnPause.addEventListener('click', () => triggerAction(`/api/simulations/${currentSimId}/pause`));
  if (btnStepBack) {
    btnStepBack.addEventListener('click', () => triggerAction(`/api/simulations/${currentSimId}/step_back`));
  }
  btnStep.addEventListener('click', () => triggerAction(`/api/simulations/${currentSimId}/step`));
  btnStop.addEventListener('click', () => triggerAction(`/api/simulations/${currentSimId}/stop`));
  btnReset.addEventListener('click', () => triggerAction(`/api/simulations/${currentSimId}/reset`));

  speedSelect.addEventListener('change', async () => {
    const rate = parseFloat(speedSelect.value);
    await triggerAction(`/api/simulations/${currentSimId}/tick_rate`, { tick_rate: rate });
  });

  btnAddCat1.addEventListener('click', () => triggerAction(`/api/simulations/${currentSimId}/entities`, { species: 'cat', delta: 1 }));
  btnAddCat10.addEventListener('click', () => triggerAction(`/api/simulations/${currentSimId}/entities`, { species: 'cat', delta: 10 }));
  btnRemCat1.addEventListener('click', () => triggerAction(`/api/simulations/${currentSimId}/entities`, { species: 'cat', delta: -1 }));

  btnAddMouse10.addEventListener('click', () => triggerAction(`/api/simulations/${currentSimId}/entities`, { species: 'mouse', delta: 10 }));
  btnAddMouse50.addEventListener('click', () => triggerAction(`/api/simulations/${currentSimId}/entities`, { species: 'mouse', delta: 50 }));
  btnRemMouse10.addEventListener('click', () => triggerAction(`/api/simulations/${currentSimId}/entities`, { species: 'mouse', delta: -10 }));

  // Init
  resizeCanvas();
  pollLoop();
})();
