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

  // Agent Inspector & Play Controls
  const agentSelect = document.getElementById('agentSelect');
  const btnModeAuto = document.getElementById('btnModeAuto');
  const btnModeManual = document.getElementById('btnModeManual');
  const btnModeHttp = document.getElementById('btnModeHttp');
  const httpUrlBox = document.getElementById('httpUrlBox');
  const agentUrlInput = document.getElementById('agentUrlInput');
  const btnApplyHttpUrl = document.getElementById('btnApplyHttpUrl');
  const agentHud = document.getElementById('agentHud');
  const hudAgentId = document.getElementById('hudAgentId');
  const btnDeselectAgent = document.getElementById('btnDeselectAgent');
  const hudAgentFacing = document.getElementById('hudAgentFacing');
  const hudVisionList = document.getElementById('hudVisionList');

  const dpadUp = document.getElementById('dpadUp');
  const dpadDown = document.getElementById('dpadDown');
  const dpadLeft = document.getElementById('dpadLeft');
  const dpadRight = document.getElementById('dpadRight');
  const dpadStay = document.getElementById('dpadStay');
  const btnAttack = document.getElementById('btnAttack');
  const btnLunge = document.getElementById('btnLunge');

  // Game Settings Modal Elements
  const btnOpenSettings = document.getElementById('btnOpenSettings');
  const btnCloseSettings = document.getElementById('btnCloseSettings');
  const btnCancelSettings = document.getElementById('btnCancelSettings');
  const btnSaveSettings = document.getElementById('btnSaveSettings');
  const btnResetSettings = document.getElementById('btnResetSettings');
  const settingsModal = document.getElementById('settingsModal');
  const settingsFeedback = document.getElementById('settingsFeedback');

  const settingCatMove = document.getElementById('settingCatMove');
  const settingCatAttackRange = document.getElementById('settingCatAttackRange');
  const settingCatAttackCd = document.getElementById('settingCatAttackCd');
  const settingCatLungeCd = document.getElementById('settingCatLungeCd');
  const settingCatVision = document.getElementById('settingCatVision');
  const settingCatWeight = document.getElementById('settingCatWeight');

  const settingMouseMove = document.getElementById('settingMouseMove');
  const settingMouseVision = document.getElementById('settingMouseVision');
  const settingMouseWeight = document.getElementById('settingMouseWeight');

  let currentSimId = 'active';
  let pollTimeout = null;
  let currentState = null;
  let selectedAgentId = null;
  let selectedAgentMode = 'auto';
  let isFetchingObs = false;


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

    // Render Vision Cones & Detection Fields (underneath sprites)
    const aliveMice = entities.filter(e => e.type === 'mouse' && e.alive);
    const miceToShow = new Set();
    if (selectedAgentId) {
      const selMouse = aliveMice.find(m => m.id === selectedAgentId);
      if (selMouse) miceToShow.add(selMouse.id);
    }
    for (const m of aliveMice) {
      if (miceToShow.size >= 2) break;
      miceToShow.add(m.id);
    }

    const catVision = (state.species && state.species.cat && state.species.cat.vision_radius) ? state.species.cat.vision_radius : 4;
    const mouseVision = (state.species && state.species.mouse && state.species.mouse.vision_radius) ? state.species.mouse.vision_radius : 4;

    for (let i = 0; i < entities.length; i++) {
      const e = entities[i];
      if (!e.alive) continue;

      const cx = e.position.x * cellW + cellW / 2;
      const cy = e.position.y * cellH + cellH / 2;

      if (e.type === 'cat') {
        const angle = getFacingAngle(e.facing || 'north');
        const radius = catVision * cellW;
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, radius, angle - Math.PI / 4, angle + Math.PI / 4);
        ctx.closePath();
        ctx.fillStyle = 'rgba(239, 68, 68, 0.12)';
        ctx.fill();
        ctx.strokeStyle = 'rgba(220, 38, 38, 0.35)';
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.stroke();
        ctx.restore();
      } else if (e.type === 'mouse' && miceToShow.has(e.id)) {
        const radius = mouseVision * cellW;
        ctx.save();
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(2, 132, 199, 0.08)';
        ctx.fill();
        ctx.strokeStyle = 'rgba(2, 132, 199, 0.25)';
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 3]);
        ctx.stroke();
        ctx.restore();
      }
    }

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

      // Focus ring for selected agent
      if (e.id === selectedAgentId) {
        ctx.save();
        ctx.strokeStyle = '#2563eb';
        ctx.lineWidth = 2.5;
        ctx.setLineDash([3, 2]);
        ctx.beginPath();
        ctx.arc(0, 0, bgRadius + 4, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }

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

    // Sync Agent Inspector
    syncAgentSelect(state.entities || []);
    if (selectedAgentId) {
      refreshAgentHUD();
    }
  }

  // Agent Inspector Logic
  function syncAgentSelect(entities) {
    if (!agentSelect) return;
    const living = entities.filter(e => e.alive);
    const existingVal = agentSelect.value || selectedAgentId;

    // Check if options match
    const currentOptions = Array.from(agentSelect.options).map(o => o.value).filter(Boolean);
    const livingIds = living.map(e => e.id);
    const isSame = (currentOptions.length === livingIds.length) && currentOptions.every((id, idx) => id === livingIds[idx]);

    if (!isSame) {
      agentSelect.replaceChildren();
      const defaultOpt = document.createElement('option');
      defaultOpt.value = '';
      defaultOpt.textContent = living.length > 0 ? '-- Choose Agent to Play / Inspect --' : '-- No Living Animals --';
      agentSelect.appendChild(defaultOpt);

      for (const ent of living) {
        const opt = document.createElement('option');
        opt.value = ent.id;
        opt.textContent = `${ent.id} (${ent.type.toUpperCase()}) at (${ent.position.x}, ${ent.position.y})`;
        agentSelect.appendChild(opt);
      }

      if (existingVal && livingIds.includes(existingVal)) {
        agentSelect.value = existingVal;
      }
    }
  }

  function updateModeButtons(mode) {
    selectedAgentMode = mode;
    btnModeAuto.className = mode === 'auto' ? 'btn btn-primary' : 'btn btn-outline';
    btnModeManual.className = mode === 'manual' ? 'btn btn-primary' : 'btn btn-outline';
    btnModeHttp.className = mode === 'http' ? 'btn btn-primary' : 'btn btn-outline';
    httpUrlBox.style.display = mode === 'http' ? 'flex' : 'none';
  }

  async function setAgentMode(mode, url = null) {
    if (!selectedAgentId) return;
    const body = { mode: mode, agent_url: url };
    const res = await apiCall(`/api/simulations/${currentSimId}/agents/${selectedAgentId}/register`, 'POST', body);
    if (res) {
      updateModeButtons(mode);
      refreshAgentHUD();
    }
  }

  async function selectAgent(agentId) {
    const prevAgentId = selectedAgentId;
    const prevMode = selectedAgentMode;

    // If there was a previously selected animal and it was in manual mode, revert it to auto
    if (prevAgentId && prevAgentId !== agentId && prevMode === 'manual') {
      await apiCall(`/api/simulations/${currentSimId}/agents/${prevAgentId}/register`, 'POST', { mode: 'auto' });
    }

    selectedAgentId = agentId || null;

    if (!selectedAgentId) {
      selectedAgentMode = 'auto';
      if (agentSelect) agentSelect.value = '';
      if (agentHud) agentHud.style.display = 'none';
      if (currentState) renderBoard(currentState);
      return;
    }

    if (agentSelect) agentSelect.value = selectedAgentId;
    if (agentHud) agentHud.style.display = 'block';

    // Fetch the agent's current mode from backend so we reflect reality without clobbering
    try {
      const agents = await apiCall(`/api/simulations/${currentSimId}/agents`, 'GET');
      if (agents && Array.isArray(agents)) {
        const found = agents.find(a => a.id === selectedAgentId);
        const mode = (found && found.mode) ? found.mode : 'auto';
        updateModeButtons(mode);
      } else {
        updateModeButtons('auto');
      }
    } catch {
      updateModeButtons('auto');
    }

    refreshAgentHUD();
    if (currentState) renderBoard(currentState);
  }


  async function refreshAgentHUD() {
    if (!selectedAgentId || isFetchingObs) return;
    isFetchingObs = true;
    const obs = await apiCall(`/api/simulations/${currentSimId}/agents/${selectedAgentId}/observation`, 'GET');
    isFetchingObs = false;

    if (!obs || !obs.self) {
      hudAgentId.textContent = `${selectedAgentId}`;
      hudAgentFacing.textContent = '💀 CAPTURED / INACTIVE';
      hudVisionList.replaceChildren();
      const deadItem = document.createElement('div');
      deadItem.className = 'vision-item';
      deadItem.textContent = 'Agent is no longer alive in simulation.';
      hudVisionList.appendChild(deadItem);
      btnAttack.disabled = true;
      return;
    }

    const self = obs.self;
    const isLocked = Boolean(self.action_cooldown && self.action_cooldown > 0);
    hudAgentId.textContent = `${self.id} (${self.type.toUpperCase()}) @ (${self.position.x}, ${self.position.y})`;
    if (isLocked) {
      hudAgentFacing.textContent = `Facing: ⬆ ${self.facing.toUpperCase()} | ⏳ LOCKED (${self.action_cooldown}t: ${self.locked_action || 'moving'})`;
    } else {
      hudAgentFacing.textContent = `Facing: ⬆ ${self.facing.toUpperCase()} | ⚡ READY`;
    }

    // Vision list
    hudVisionList.replaceChildren();
    const visible = obs.visible_entities || [];
    if (visible.length === 0) {
      const item = document.createElement('div');
      item.className = 'vision-item';
      item.textContent = 'No entities in field of view.';
      hudVisionList.appendChild(item);
    } else {
      for (const ent of visible) {
        const item = document.createElement('div');
        const isCat = (ent.type === 'cat');
        item.className = `vision-item ${isCat ? 'threat-cat' : 'target-mouse'}`;

        const dx = ent.position.x - self.position.x;
        const dy = ent.position.y - self.position.y;
        const dist = Math.sqrt(dx * dx + dy * dy).toFixed(1);

        item.textContent = `${isCat ? '🐱 CAT' : '🐭 MOUSE'}: ${ent.id} @ (${ent.position.x}, ${ent.position.y}) [Dist: ${dist}]`;
        hudVisionList.appendChild(item);
      }
    }

    // Directional Dpad buttons: disable when animation locked
    if (dpadUp) dpadUp.disabled = isLocked;
    if (dpadDown) dpadDown.disabled = isLocked;
    if (dpadLeft) dpadLeft.disabled = isLocked;
    if (dpadRight) dpadRight.disabled = isLocked;
    if (dpadStay) dpadStay.disabled = isLocked;

    // Slash Attack & Lunge Buttons
    const legal = obs.legal_actions || [];
    if (self.type === 'cat') {
      btnAttack.style.display = 'block';
      if (isLocked) {
        btnAttack.disabled = true;
        btnAttack.textContent = `⚔️ Slash Attack (Locked: ${self.action_cooldown}t)`;
      } else if (legal.includes('attack')) {
        btnAttack.disabled = false;
        btnAttack.textContent = '⚔️ Slash Attack (Ready!)';
      } else {
        btnAttack.disabled = true;
        btnAttack.textContent = '⚔️ Slash Attack (No target ahead)';
      }

      if (btnLunge) {
        btnLunge.style.display = 'block';
        if (isLocked) {
          btnLunge.disabled = true;
          btnLunge.textContent = `⚡ Forward Lunge (Locked: ${self.action_cooldown}t)`;
        } else if (legal.includes('lunge')) {
          btnLunge.disabled = false;
          btnLunge.textContent = '⚡ Forward Lunge (Ready!)';
        } else {
          btnLunge.disabled = true;
          btnLunge.textContent = '⚡ Forward Lunge (Blocked)';
        }
      }
    } else {
      btnAttack.style.display = 'none';
      if (btnLunge) btnLunge.style.display = 'none';
    }
  }

  async function sendAgentAction(action) {
    if (!selectedAgentId) return;
    const res = await apiCall(`/api/simulations/${currentSimId}/agents/${selectedAgentId}/actions?step=true`, 'POST', { action: action });
    if (res && res.accepted) {
      // Step simulation visually or refresh state
      const newState = await apiCall(`/api/simulations/${currentSimId}/state`, 'GET');
      if (newState) {
        updateUI(newState);
      }
      refreshAgentHUD();
    }
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

  // Canvas Click to Select Entity
  canvas.addEventListener('click', (ev) => {
    if (!currentState || !currentState.entities) return;
    const rect = canvas.getBoundingClientRect();
    const clickX = ev.clientX - rect.left;
    const clickY = ev.clientY - rect.top;
    const cellW = canvas.width / (currentState.width || 100);
    const cellH = canvas.height / (currentState.height || 100);
    const gridX = Math.floor(clickX / cellW);
    const gridY = Math.floor(clickY / cellH);

    let closest = null;
    let minD = Infinity;
    for (const ent of currentState.entities) {
      if (!ent.alive) continue;
      const d = Math.abs(ent.position.x - gridX) + Math.abs(ent.position.y - gridY);
      if (d < minD && d <= 4) {
        closest = ent;
        minD = d;
      }
    }
    if (closest) {
      selectAgent(closest.id);
    } else if (selectedAgentId) {
      // Clicked on empty space: deselect current animal and release to auto
      selectAgent(null);
    }
  });

  // Agent Inspector Listeners
  if (agentSelect) {
    agentSelect.addEventListener('change', () => selectAgent(agentSelect.value));
  }
  if (btnDeselectAgent) {
    btnDeselectAgent.addEventListener('click', () => selectAgent(null));
  }
  if (btnModeAuto) {
    btnModeAuto.addEventListener('click', async () => {
      await setAgentMode('auto');
      if (currentState && currentState.status === 'paused') {
        const startRes = await apiCall(`/api/simulations/${currentSimId}/start`, 'POST');
        if (startRes) updateUI(startRes);
      }
    });
  }
  if (btnModeManual) {
    btnModeManual.addEventListener('click', () => setAgentMode('manual'));
  }
  if (btnModeHttp) {
    btnModeHttp.addEventListener('click', () => {
      updateModeButtons('http');
      if (agentUrlInput) {
        const isCat = selectedAgentId && selectedAgentId.startsWith('cat');
        agentUrlInput.value = isCat ? 'http://127.0.0.1:8001/act' : 'http://127.0.0.1:8002/act';
      }
    });
  }

  if (btnApplyHttpUrl) {
    btnApplyHttpUrl.addEventListener('click', () => {
      const url = agentUrlInput ? agentUrlInput.value.trim() : '';
      if (url) setAgentMode('http', url);
    });
  }

  // D-Pad and Attack Buttons
  if (dpadUp) dpadUp.addEventListener('click', () => sendAgentAction('north'));
  if (dpadDown) dpadDown.addEventListener('click', () => sendAgentAction('south'));
  if (dpadLeft) dpadLeft.addEventListener('click', () => sendAgentAction('west'));
  if (dpadRight) dpadRight.addEventListener('click', () => sendAgentAction('east'));
  if (dpadStay) dpadStay.addEventListener('click', () => sendAgentAction('stay'));
  if (btnAttack) btnAttack.addEventListener('click', () => sendAgentAction('attack'));
  if (btnLunge) btnLunge.addEventListener('click', () => sendAgentAction('lunge'));

  // Game Settings Pop-out Modal Logic
  function showSettingsFeedback(message, isError = false) {
    if (!settingsFeedback) return;
    settingsFeedback.textContent = message;
    settingsFeedback.className = isError ? 'settings-feedback feedback-error' : 'settings-feedback feedback-success';
    settingsFeedback.style.display = 'block';
  }

  function hideSettingsFeedback() {
    if (!settingsFeedback) return;
    settingsFeedback.textContent = '';
    settingsFeedback.className = 'settings-feedback';
    settingsFeedback.style.display = 'none';
  }

  function parseIntDefault(val, def, minVal = 0) {
    const parsed = parseInt(val, 10);
    if (isNaN(parsed)) return def;
    return Math.max(minVal, parsed);
  }

  async function openSettingsModal() {
    if (!settingsModal) return;
    hideSettingsFeedback();
    settingsModal.style.display = 'flex';

    try {
      const data = await apiCall(`/api/simulations/${currentSimId}/species`, 'GET');
      if (!data) {
        showSettingsFeedback('Failed to load current species settings from server.', true);
        return;
      }
      if (data.cat) {
        if (settingCatMove) settingCatMove.value = data.cat.move_cooldown ?? 3;
        if (settingCatAttackRange) settingCatAttackRange.value = data.cat.attack_range ?? 2;
        if (settingCatAttackCd) settingCatAttackCd.value = data.cat.attack_cooldown ?? 3;
        if (settingCatLungeCd) settingCatLungeCd.value = data.cat.lunge_cooldown ?? 2;
        if (settingCatVision) settingCatVision.value = data.cat.vision_radius ?? 4;
        if (settingCatWeight) settingCatWeight.value = data.cat.weight ?? 10;
      }
      if (data.mouse) {
        if (settingMouseMove) settingMouseMove.value = data.mouse.move_cooldown ?? 2;
        if (settingMouseVision) settingMouseVision.value = data.mouse.vision_radius ?? 4;
        if (settingMouseWeight) settingMouseWeight.value = data.mouse.weight ?? 1;
      }
    } catch (err) {
      console.error('Failed to load species settings:', err);
      showSettingsFeedback('Failed to load species settings.', true);
    }
  }

  function closeSettingsModal() {
    if (!settingsModal) return;
    settingsModal.style.display = 'none';
    hideSettingsFeedback();
  }

  function resetSettingsToDefaults() {
    if (settingCatMove) settingCatMove.value = 3;
    if (settingCatAttackRange) settingCatAttackRange.value = 2;
    if (settingCatAttackCd) settingCatAttackCd.value = 3;
    if (settingCatLungeCd) settingCatLungeCd.value = 2;
    if (settingCatVision) settingCatVision.value = 4;
    if (settingCatWeight) settingCatWeight.value = 10;

    if (settingMouseMove) settingMouseMove.value = 2;
    if (settingMouseVision) settingMouseVision.value = 4;
    if (settingMouseWeight) settingMouseWeight.value = 1;

    showSettingsFeedback('Reset to default values. Click "Apply Settings" to save.', false);
  }

  async function saveSettings() {
    hideSettingsFeedback();

    const catPayload = {
      move_cooldown: parseIntDefault(settingCatMove?.value, 3, 1),
      attack_range: parseIntDefault(settingCatAttackRange?.value, 2, 0),
      attack_cooldown: parseIntDefault(settingCatAttackCd?.value, 3, 0),
      lunge_cooldown: parseIntDefault(settingCatLungeCd?.value, 2, 0),
      vision_radius: parseIntDefault(settingCatVision?.value, 4, 1),
      weight: parseIntDefault(settingCatWeight?.value, 10, 1)
    };

    const mousePayload = {
      move_cooldown: parseIntDefault(settingMouseMove?.value, 2, 1),
      vision_radius: parseIntDefault(settingMouseVision?.value, 4, 1),
      weight: parseIntDefault(settingMouseWeight?.value, 1, 1)
    };

    try {
      const [catRes, mouseRes] = await Promise.all([
        apiCall(`/api/simulations/${currentSimId}/species/cat`, 'PATCH', catPayload),
        apiCall(`/api/simulations/${currentSimId}/species/mouse`, 'PATCH', mousePayload)
      ]);

      if (!catRes || !mouseRes) {
        showSettingsFeedback('Failed to update species parameters on server.', true);
        return;
      }

      showSettingsFeedback('✓ Settings applied live! Active on next simulation tick.', false);
      const stateData = await apiCall(`/api/simulations/${currentSimId}/state`, 'GET');
      if (stateData) updateUI(stateData);
    } catch (err) {
      console.error('Error saving settings:', err);
      showSettingsFeedback('Error saving settings: ' + err.message, true);
    }
  }

  const btnPopoutWindow = document.getElementById('btnPopoutWindow');

  function openSettingsWindow() {
    const width = 560;
    const height = 780;
    const screenW = window.screen.availWidth || window.innerWidth;
    const screenH = window.screen.availHeight || window.innerHeight;
    const left = Math.max(10, Math.min(screenW - width - 20, (window.screenX || 0) + (window.outerWidth || 1000) - width));
    const top = Math.max(20, (window.screenY || 0) + 30);
    const features = `width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes,status=no,toolbar=no,menubar=no,location=no`;

    const popup = window.open('/settings', 'CMGameSettings', features);
    if (popup && !popup.closed) {
      popup.focus();
      closeSettingsModal();
    } else {
      // Fallback to in-page pop-out if browser blocks popup windows
      openSettingsModal();
    }
  }

  // Settings Pop-Out Window Listeners
  if (btnOpenSettings) btnOpenSettings.addEventListener('click', openSettingsWindow);
  if (btnPopoutWindow) btnPopoutWindow.addEventListener('click', openSettingsWindow);
  if (btnCloseSettings) btnCloseSettings.addEventListener('click', closeSettingsModal);
  if (btnCancelSettings) btnCancelSettings.addEventListener('click', closeSettingsModal);
  if (btnSaveSettings) btnSaveSettings.addEventListener('click', saveSettings);
  if (btnResetSettings) btnResetSettings.addEventListener('click', resetSettingsToDefaults);

  // Sync settings when modified from external standalone settings window
  window.addEventListener('message', async (event) => {
    if (event.data && event.data.type === 'settings_updated') {
      try {
        const stateData = await apiCall(`/api/simulations/${currentSimId}/state`, 'GET');
        if (stateData) updateUI(stateData);
      } catch (err) {
        console.error('Error refreshing state from settings update:', err);
      }
    }
  });

  // Draggable Game Settings Pop-Out Window
  const modalDialog = settingsModal ? settingsModal.querySelector('.modal-dialog') : null;
  const modalHeader = settingsModal ? settingsModal.querySelector('.modal-header') : null;

  if (modalDialog && modalHeader) {
    let isDragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    let initialLeft = 0;
    let initialTop = 0;

    modalHeader.addEventListener('mousedown', (e) => {
      if (e.target.closest('button')) return;
      isDragging = true;
      dragStartX = e.clientX;
      dragStartY = e.clientY;

      const rect = modalDialog.getBoundingClientRect();
      initialLeft = rect.left;
      initialTop = rect.top;

      modalDialog.style.position = 'fixed';
      modalDialog.style.left = `${initialLeft}px`;
      modalDialog.style.top = `${initialTop}px`;
      modalDialog.style.right = 'auto';
      modalDialog.style.margin = '0';

      document.body.style.userSelect = 'none';
    });

    window.addEventListener('mousemove', (e) => {
      if (!isDragging) return;
      const dx = e.clientX - dragStartX;
      const dy = e.clientY - dragStartY;

      const newLeft = Math.max(10, Math.min(window.innerWidth - modalDialog.offsetWidth - 10, initialLeft + dx));
      const newTop = Math.max(10, Math.min(window.innerHeight - 80, initialTop + dy));

      modalDialog.style.left = `${newLeft}px`;
      modalDialog.style.top = `${newTop}px`;
    });

    window.addEventListener('mouseup', () => {
      if (isDragging) {
        isDragging = false;
        document.body.style.userSelect = '';
      }
    });
  }

  // Keyboard Controls (WASD / Arrows / Spacebar / L for lunge)
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (settingsModal && settingsModal.style.display !== 'none') {
        closeSettingsModal();
        return;
      }
    }
    if (!selectedAgentId || selectedAgentMode !== 'manual') return;
    if (document.activeElement && document.activeElement.tagName === 'INPUT') return;

    let action = null;
    if (e.key === 'ArrowUp' || e.key === 'w' || e.key === 'W') action = 'north';
    else if (e.key === 'ArrowDown' || e.key === 's' || e.key === 'S') action = 'south';
    else if (e.key === 'ArrowLeft' || e.key === 'a' || e.key === 'A') action = 'west';
    else if (e.key === 'ArrowRight' || e.key === 'd' || e.key === 'D') action = 'east';
    else if (e.key === ' ') action = 'attack';
    else if (e.key === 'l' || e.key === 'L') action = 'lunge';
    else if (e.key === '.' || e.key === 'x') action = 'stay';

    if (action) {
      e.preventDefault();
      sendAgentAction(action);
    }
  });

  // Event Listeners for Simulation Administration
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

