// Cat & Mouse Simulation Frontend Controller

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
  const btnStep = document.getElementById('btnStep');
  const btnStop = document.getElementById('btnStop');
  const btnReset = document.getElementById('btnReset');

  const btnAddCat1 = document.getElementById('btnAddCat1');
  const btnAddCat10 = document.getElementById('btnAddCat10');
  const btnRemCat1 = document.getElementById('btnRemCat1');
  const btnAddMouse10 = document.getElementById('btnAddMouse10');
  const btnAddMouse50 = document.getElementById('btnAddMouse50');
  const btnRemMouse10 = document.getElementById('btnRemMouse10');

  const canvas = document.getElementById('boardCanvas');
  const ctx = canvas.getContext('2d');
  const canvasWrapper = document.getElementById('canvasWrapper');

  let currentSimId = 'active';
  let pollInterval = null;
  let currentState = null;

  // Render colors
  const COLOR_BG = '#0c0e12';
  const COLOR_GRID_LINE = 'rgba(255, 255, 255, 0.04)';
  const COLOR_CAT = '#ff6b4a';
  const COLOR_MOUSE = '#00d2ff';

  // Resize canvas to match wrapper bounds maintaining aspect ratio
  function resizeCanvas() {
    if (!canvasWrapper) return;
    const rect = canvasWrapper.getBoundingClientRect();
    const size = Math.min(rect.width - 20, rect.height - 20, 1000);
    if (size > 100) {
      canvas.width = size;
      canvas.height = size;
      if (currentState) {
        renderBoard(currentState);
      }
    }
  }

  window.addEventListener('resize', resizeCanvas);

  function renderBoard(state) {
    const width = state.width || 100;
    const height = state.height || 100;
    const cellW = canvas.width / width;
    const cellH = canvas.height / height;

    // Clear background
    ctx.fillStyle = COLOR_BG;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Subtle grid lines (every 10 tiles for 100x100)
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

    // Render Entities
    const entities = state.entities || [];
    for (let i = 0; i < entities.length; i++) {
      const e = entities[i];
      if (!e.alive) continue;

      const px = e.position.x * cellW;
      const py = e.position.y * cellH;

      if (e.type === 'cat') {
        ctx.fillStyle = COLOR_CAT;
        // Draw slightly larger marker for Cat
        const radius = Math.max(2.5, cellW * 0.7);
        ctx.beginPath();
        ctx.arc(px + cellW / 2, py + cellH / 2, radius, 0, Math.PI * 2);
        ctx.fill();
      } else {
        ctx.fillStyle = COLOR_MOUSE;
        // Draw marker for Mouse
        const radius = Math.max(1.5, cellW * 0.45);
        ctx.beginPath();
        ctx.arc(px + cellW / 2, py + cellH / 2, radius, 0, Math.PI * 2);
        ctx.fill();
      }
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

    // Update Metrics safely with textContent
    metricTick.textContent = String(state.tick || 0);
    metricRound.textContent = String(state.round || 0);
    metricCats.textContent = String(state.cat_count || 0);
    metricMice.textContent = String(state.mouse_count || 0);
    metricCaptures.textContent = String(state.captures || 0);
    metricDimensions.textContent = `${state.width || 100} × ${state.height || 100}`;

    renderBoard(state);
  }

  async function apiCall(endpoint, method = 'POST', body = null) {
    try {
      const options = {
        method: method,
        headers: {
          'Content-Type': 'application/json',
        },
      };
      if (body) {
        options.body = JSON.stringify(body);
      }
      const res = await fetch(endpoint, options);
      if (!res.ok) {
        console.error('API call failed', res.status, res.statusText);
        return null;
      }
      return await res.json();
    } catch (err) {
      console.error('API error:', err);
      return null;
    }
  }

  async function fetchState() {
    const data = await apiCall(`/api/simulations/${currentSimId}/state`, 'GET');
    if (data) {
      updateUI(data);
    }
  }

  // Event Listeners
  btnStart.addEventListener('click', async () => {
    const data = await apiCall(`/api/simulations/${currentSimId}/start`);
    if (data) updateUI(data);
  });

  btnPause.addEventListener('click', async () => {
    const data = await apiCall(`/api/simulations/${currentSimId}/pause`);
    if (data) updateUI(data);
  });

  btnStep.addEventListener('click', async () => {
    const data = await apiCall(`/api/simulations/${currentSimId}/step`);
    if (data) updateUI(data);
  });

  btnStop.addEventListener('click', async () => {
    const data = await apiCall(`/api/simulations/${currentSimId}/stop`);
    if (data) updateUI(data);
  });

  btnReset.addEventListener('click', async () => {
    const data = await apiCall(`/api/simulations/${currentSimId}/reset`);
    if (data) updateUI(data);
  });

  speedSelect.addEventListener('change', async () => {
    const rate = parseFloat(speedSelect.value);
    await apiCall(`/api/simulations/${currentSimId}/tick_rate`, 'POST', { tick_rate: rate });
  });

  btnAddCat1.addEventListener('click', async () => {
    await apiCall(`/api/simulations/${currentSimId}/entities`, 'POST', { species: 'cat', delta: 1 });
    await fetchState();
  });

  btnAddCat10.addEventListener('click', async () => {
    await apiCall(`/api/simulations/${currentSimId}/entities`, 'POST', { species: 'cat', delta: 10 });
    await fetchState();
  });

  btnRemCat1.addEventListener('click', async () => {
    await apiCall(`/api/simulations/${currentSimId}/entities`, 'POST', { species: 'cat', delta: -1 });
    await fetchState();
  });

  btnAddMouse10.addEventListener('click', async () => {
    await apiCall(`/api/simulations/${currentSimId}/entities`, 'POST', { species: 'mouse', delta: 10 });
    await fetchState();
  });

  btnAddMouse50.addEventListener('click', async () => {
    await apiCall(`/api/simulations/${currentSimId}/entities`, 'POST', { species: 'mouse', delta: 50 });
    await fetchState();
  });

  btnRemMouse10.addEventListener('click', async () => {
    await apiCall(`/api/simulations/${currentSimId}/entities`, 'POST', { species: 'mouse', delta: -10 });
    await fetchState();
  });

  // Init
  resizeCanvas();
  fetchState();
  // Poll state every 150ms for responsive updates
  pollInterval = setInterval(fetchState, 150);
})();
