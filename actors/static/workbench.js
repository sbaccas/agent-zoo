// Cat Agent Workbench Client Logic
// Note: Follows strict zero-innerHTML security policy.

(function () {
  'use strict';

  // State
  let isPolling = true;
  let pollIntervalId = null;
  let currentOverrideMode = 'once'; // 'once' | 'sticky'
  let inspectingTick = null;
  let cachedState = null;

  // DOM Elements - Header
  const statusBadge = document.getElementById('statusBadge');
  const statusText = document.getElementById('statusText');
  const headerTick = document.getElementById('headerTick');
  const headerCycles = document.getElementById('headerCycles');
  const btnTogglePolling = document.getElementById('btnTogglePolling');
  const pollingDot = document.getElementById('pollingDot');
  const pollingText = document.getElementById('pollingText');

  // DOM Elements - Past Inspection Banner
  const inspectionBanner = document.getElementById('inspectionBanner');
  const inspectingTickNumber = document.getElementById('inspectingTickNumber');
  const btnReturnLive = document.getElementById('btnReturnLive');

  // DOM Elements - Box 1: Observation
  const chipPos = document.getElementById('chipPos');
  const chipFacing = document.getElementById('chipFacing');
  const chipHp = document.getElementById('chipHp');
  const chipVisible = document.getElementById('chipVisible');
  const obsJsonViewer = document.getElementById('obsJsonViewer');
  const btnCopyObs = document.getElementById('btnCopyObs');

  // DOM Elements - Box 2: Brain & Override
  const badgeBrainState = document.getElementById('badgeBrainState');
  const autonomousThought = document.getElementById('autonomousThought');
  const autoActionPill = document.getElementById('autoActionPill');
  const btnModeOnce = document.getElementById('btnModeOnce');
  const btnModeSticky = document.getElementById('btnModeSticky');
  const dpadButtons = document.querySelectorAll('.dpad-btn, .btn-attack-override');
  const overrideStatusBox = document.getElementById('overrideStatusBox');
  const overrideStatusText = document.getElementById('overrideStatusText');
  const btnClearOverride = document.getElementById('btnClearOverride');

  // DOM Elements - Box 3: Action Response
  const actionSummaryCard = document.getElementById('actionSummaryCard');
  const responseActionPill = document.getElementById('responseActionPill');
  const responseSourceBadge = document.getElementById('responseSourceBadge');
  const responseThought = document.getElementById('responseThought');
  const respJsonViewer = document.getElementById('respJsonViewer');
  const btnCopyResp = document.getElementById('btnCopyResp');

  // DOM Elements - Box 4: History
  const historyCountPill = document.getElementById('historyCountPill');
  const historyTableBody = document.getElementById('historyTableBody');
  const btnClearHistory = document.getElementById('btnClearHistory');
  const btnExportJson = document.getElementById('btnExportJson');

  // Helper: Flash copy feedback on a button
  function flashCopied(btn, text) {
    const originalText = btn.textContent;
    btn.textContent = text || '✓ Copied!';
    btn.classList.add('btn-copied');
    setTimeout(() => {
      btn.textContent = originalText;
      btn.classList.remove('btn-copied');
    }, 1200);
  }

  // API Call helper
  async function api(path, method = 'GET', body = null) {
    try {
      const opts = {
        method,
        headers: { 'Content-Type': 'application/json' },
      };
      if (body) {
        opts.body = JSON.stringify(body);
      }
      const res = await fetch(path, opts);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      return await res.json();
    } catch (err) {
      console.warn(`[API] Error on ${path}:`, err);
      return null;
    }
  }

  // Render Box 1: Observation
  function renderObservation(obs) {
    if (!obs) {
      chipPos.textContent = '--';
      chipFacing.textContent = '--';
      chipHp.textContent = '--';
      chipVisible.textContent = '--';
      obsJsonViewer.textContent = '// Waiting for observations from simulation server...';
      return;
    }

    const selfInfo = obs.self || {};
    const pos = selfInfo.position || { x: '?', y: '?' };
    chipPos.textContent = `(${pos.x}, ${pos.y})`;
    chipFacing.textContent = (selfInfo.facing || 'north').toUpperCase();
    chipHp.textContent = selfInfo.hp !== undefined ? `${selfInfo.hp} / 100` : '--';

    const visible = obs.visible_entities || [];
    const mice = visible.filter((e) => e.type === 'mouse');
    if (mice.length > 0) {
      chipVisible.textContent = `${mice.length} Mouse (${mice.map((m) => m.id).join(', ')})`;
    } else {
      chipVisible.textContent = `${visible.length} entities`;
    }

    obsJsonViewer.textContent = JSON.stringify(obs, null, 2);
  }

  // Render Box 2: Brain Decision & Override
  function renderBrain(state) {
    const latestAuto = state.latest_autonomous;
    if (latestAuto) {
      autonomousThought.textContent = latestAuto.thought || 'Autonomous decision ready';
      autoActionPill.textContent = (latestAuto.action || '--').toUpperCase();

      const meta = latestAuto.meta || {};
      const phase = meta.phase || 'ROAMING';
      badgeBrainState.textContent = phase;
      badgeBrainState.className = 'badge-state';
      if (phase === 'ATTACK') {
        badgeBrainState.classList.add('badge-attack');
        autoActionPill.className = 'action-pill-lg attack';
      } else if (phase === 'STALKING') {
        badgeBrainState.classList.add('badge-stalking');
        autoActionPill.className = 'action-pill-lg';
      } else {
        badgeBrainState.classList.add('badge-roaming');
        autoActionPill.className = 'action-pill-lg';
      }
    } else {
      autonomousThought.textContent = 'Standing by for tick...';
      autoActionPill.textContent = '--';
      autoActionPill.className = 'action-pill-lg';
      badgeBrainState.textContent = 'IDLE';
      badgeBrainState.className = 'badge-state badge-roaming';
    }

    // Override mode toggles
    const mode = state.override_mode || currentOverrideMode;
    currentOverrideMode = mode;
    if (mode === 'once') {
      btnModeOnce.classList.add('active');
      btnModeSticky.classList.remove('active');
    } else {
      btnModeSticky.classList.add('active');
      btnModeOnce.classList.remove('active');
    }

    // Pending override feedback
    const pending = state.pending_override;
    dpadButtons.forEach((btn) => {
      const btnAction = btn.getAttribute('data-action');
      if (pending && btnAction === pending) {
        btn.classList.add('active-override');
      } else {
        btn.classList.remove('active-override');
      }
    });

    if (pending) {
      overrideStatusBox.classList.add('active');
      const modeText = mode === 'once' ? 'Next Tick Only' : 'Sticky';
      overrideStatusText.textContent = `⚡ Override Queued: [${pending.toUpperCase()}] (${modeText})`;
      btnClearOverride.style.display = 'inline-flex';
    } else {
      overrideStatusBox.classList.remove('active');
      overrideStatusText.textContent = 'Autonomous AI in control';
      btnClearOverride.style.display = 'none';
    }
  }

  // Render Box 3: Outgoing Action Response
  function renderResponse(resp) {
    if (!resp) {
      responseActionPill.textContent = '--';
      responseActionPill.className = 'action-pill-lg';
      responseSourceBadge.textContent = 'Auto';
      responseSourceBadge.className = 'history-source-badge auto';
      responseThought.textContent = 'No actions returned yet';
      respJsonViewer.textContent = '// Waiting to send action response...';
      actionSummaryCard.classList.remove('override-sent');
      return;
    }

    const actionName = (resp.action || 'stay').toUpperCase();
    responseActionPill.textContent = actionName;
    if (actionName === 'ATTACK') {
      responseActionPill.className = 'action-pill-lg attack';
    } else {
      responseActionPill.className = 'action-pill-lg';
    }

    const isOverride = resp.source === 'manual_override';
    if (isOverride) {
      responseSourceBadge.textContent = 'Override';
      responseSourceBadge.className = 'history-source-badge override';
      actionSummaryCard.classList.add('override-sent');
    } else {
      responseSourceBadge.textContent = 'Autonomous';
      responseSourceBadge.className = 'history-source-badge auto';
      actionSummaryCard.classList.remove('override-sent');
    }

    responseThought.textContent = resp.thought || '';
    respJsonViewer.textContent = JSON.stringify(
      {
        action: resp.action,
        thought: resp.thought,
      },
      null,
      2
    );
  }

  // Render Box 4: History Table (Zero innerHTML)
  function renderHistory(history) {
    historyCountPill.textContent = `${history.length} ticks`;

    if (!history || history.length === 0) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.setAttribute('colspan', '6');
      td.style.textAlign = 'center';
      td.style.color = 'var(--text-muted)';
      td.style.padding = '24px';
      td.textContent = 'No conversation history recorded yet. Connect cat-1 on the Game Server and advance ticks to stream events.';
      tr.appendChild(td);
      historyTableBody.replaceChildren(tr);
      return;
    }

    // Newest first
    const rows = [];
    for (let i = history.length - 1; i >= 0; i--) {
      const item = history[i];
      const tr = document.createElement('tr');
      if (inspectingTick === item.tick) {
        tr.classList.add('selected-history-row');
      }

      // 1. Tick
      const tdTick = document.createElement('td');
      const strongTick = document.createElement('strong');
      strongTick.textContent = `#${item.tick}`;
      tdTick.appendChild(strongTick);
      tr.appendChild(tdTick);

      // 2. Time
      const tdTime = document.createElement('td');
      tdTime.style.fontFamily = 'var(--font-mono)';
      tdTime.style.fontSize = '0.75rem';
      tdTime.style.color = 'var(--text-muted)';
      tdTime.textContent = item.timestamp || '--';
      tr.appendChild(tdTime);

      // 3. Source
      const tdSource = document.createElement('td');
      const sourceBadge = document.createElement('span');
      const isOverride = item.source === 'manual_override';
      sourceBadge.className = isOverride ? 'history-source-badge override' : 'history-source-badge auto';
      sourceBadge.textContent = isOverride ? 'Override' : 'Auto';
      tdSource.appendChild(sourceBadge);
      tr.appendChild(tdSource);

      // 4. Action
      const tdAction = document.createElement('td');
      const actionBadge = document.createElement('span');
      const act = (item.response && item.response.action) ? item.response.action : 'stay';
      actionBadge.className = `history-action-badge ${act}`;
      actionBadge.textContent = act.toUpperCase();
      tdAction.appendChild(actionBadge);
      tr.appendChild(tdAction);

      // 5. Perception & Rationale
      const tdDesc = document.createElement('td');
      const descContainer = document.createElement('div');
      descContainer.style.display = 'flex';
      descContainer.style.flexDirection = 'column';
      descContainer.style.gap = '2px';

      const obs = item.observation || {};
      const pos = (obs.self && obs.self.position) ? `(${obs.self.position.x}, ${obs.self.position.y})` : '';
      const visible = obs.visible_entities || [];
      const mice = visible.filter((e) => e.type === 'mouse');

      const senseRow = document.createElement('span');
      senseRow.style.fontSize = '0.72rem';
      senseRow.style.color = 'var(--text-muted)';
      senseRow.textContent = `At ${pos} | ${mice.length} mouse seen | Legal: ${(obs.legal_actions || []).length}`;
      descContainer.appendChild(senseRow);

      const thoughtRow = document.createElement('span');
      thoughtRow.style.fontWeight = '500';
      thoughtRow.textContent = (item.response && item.response.thought) || '';
      descContainer.appendChild(thoughtRow);

      tdDesc.appendChild(descContainer);
      tr.appendChild(tdDesc);

      // 6. Inspect Button
      const tdInspect = document.createElement('td');
      tdInspect.style.textAlign = 'center';
      const btnInspect = document.createElement('button');
      btnInspect.className = 'btn btn-sm';
      btnInspect.textContent = inspectingTick === item.tick ? 'Viewing' : 'Inspect';
      btnInspect.addEventListener('click', () => inspectHistoryItem(item));
      tdInspect.appendChild(btnInspect);
      tr.appendChild(tdInspect);

      rows.push(tr);
    }

    historyTableBody.replaceChildren(...rows);
  }

  // Inspect a specific past tick
  function inspectHistoryItem(item) {
    inspectingTick = item.tick;
    inspectingTickNumber.textContent = item.tick;
    inspectionBanner.style.display = 'flex';

    // Show historical payload in Box 1 and Box 3
    renderObservation(item.observation);

    // Box 2: Show what the brain calculated on that tick
    const autoDec = item.autonomous_decision || (item.autonomous || {});
    autonomousThought.textContent = autoDec.thought || 'N/A';
    autoActionPill.textContent = (autoDec.action || '--').toUpperCase();
    const meta = autoDec.meta || (autoDec.details || {});
    const phase = meta.phase || 'RECORDED';
    badgeBrainState.textContent = phase;
    badgeBrainState.className = 'badge-state badge-roaming';

    // Box 3: Show what was actually returned to server
    renderResponse(item.response);

    // Re-render history table to update selected row
    if (cachedState && cachedState.history) {
      renderHistory(cachedState.history);
    }
  }

  // Return to live view
  function returnToLive() {
    inspectingTick = null;
    inspectionBanner.style.display = 'none';
    if (cachedState) {
      renderLiveState(cachedState);
    }
  }

  // Render the full live state received from /api/agent/state
  function renderLiveState(state) {
    cachedState = state;

    // Header info
    headerTick.textContent = state.active_tick !== undefined ? `#${state.active_tick}` : '--';
    headerCycles.textContent = state.total_ticks_processed || '0';

    if (state.connected) {
      statusBadge.className = 'wb-status-badge online';
      statusText.textContent = 'Active • Receiving Observations';
    } else {
      statusBadge.className = 'wb-status-badge waiting';
      statusText.textContent = 'Waiting for Game Server';
    }

    // If currently inspecting a past tick, don't overwrite boxes 1-3 with live data
    if (inspectingTick === null) {
      renderObservation(state.latest_observation);
      renderBrain(state);
      renderResponse(state.latest_response);
    } else {
      // Still update Box 2 override controls so player can queue overrides
      renderBrain(state);
    }

    // Always update Box 4 history
    renderHistory(state.history || []);
  }

  // Fetch agent state
  async function pollAgentState() {
    const data = await api('/api/agent/state');
    if (data) {
      renderLiveState(data);
    }
  }

  // Send Manual Override
  async function setOverride(action) {
    const res = await api('/api/agent/override', 'POST', {
      action: action,
      mode: currentOverrideMode,
    });
    if (res) {
      await pollAgentState();
    }
  }

  // Clear Override
  async function clearOverride() {
    const res = await api('/api/agent/override', 'POST', {
      action: null,
      mode: currentOverrideMode,
    });
    if (res) {
      await pollAgentState();
    }
  }

  // Event Listeners - Polling Toggle
  btnTogglePolling.addEventListener('click', () => {
    isPolling = !isPolling;
    if (isPolling) {
      pollingDot.style.backgroundColor = '#16a34a';
      pollingText.textContent = 'Live Polling';
      pollIntervalId = setInterval(pollAgentState, 500);
      pollAgentState();
    } else {
      pollingDot.style.backgroundColor = '#94a3b8';
      pollingText.textContent = 'Paused';
      if (pollIntervalId) clearInterval(pollIntervalId);
    }
  });

  // Event Listeners - Return from History Inspection
  btnReturnLive.addEventListener('click', returnToLive);

  // Event Listeners - Copy Buttons
  btnCopyObs.addEventListener('click', () => {
    const content = obsJsonViewer.textContent;
    navigator.clipboard.writeText(content).then(() => {
      flashCopied(btnCopyObs, '✓ Copied JSON!');
    });
  });

  btnCopyResp.addEventListener('click', () => {
    const content = respJsonViewer.textContent;
    navigator.clipboard.writeText(content).then(() => {
      flashCopied(btnCopyResp, '✓ Copied JSON!');
    });
  });

  // Event Listeners - Override Mode Toggles
  btnModeOnce.addEventListener('click', () => {
    currentOverrideMode = 'once';
    btnModeOnce.classList.add('active');
    btnModeSticky.classList.remove('active');
    if (cachedState && cachedState.pending_override) {
      setOverride(cachedState.pending_override);
    }
  });

  btnModeSticky.addEventListener('click', () => {
    currentOverrideMode = 'sticky';
    btnModeSticky.classList.add('active');
    btnModeOnce.classList.remove('active');
    if (cachedState && cachedState.pending_override) {
      setOverride(cachedState.pending_override);
    }
  });

  // Event Listeners - D-Pad & Attack Override
  dpadButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      const action = btn.getAttribute('data-action');
      setOverride(action);
    });
  });

  btnClearOverride.addEventListener('click', clearOverride);

  // Event Listeners - Clear History
  btnClearHistory.addEventListener('click', async () => {
    await api('/api/agent/clear-history', 'POST');
    returnToLive();
    await pollAgentState();
  });

  // Event Listeners - Export JSON
  btnExportJson.addEventListener('click', () => {
    if (!cachedState || !cachedState.history || cachedState.history.length === 0) {
      alert('No history recorded to export.');
      return;
    }
    const dataStr = 'data:text/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(cachedState.history, null, 2));
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute('href', dataStr);
    downloadAnchor.setAttribute('download', `cat_agent_trajectory_tick_${cachedState.active_tick || 0}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
  });

  // Keyboard Shortcuts (Arrow keys / WASD for D-Pad, Space for attack, Esc to clear)
  window.addEventListener('keydown', (e) => {
    // Avoid hijacking when typing inside an input or textarea
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
      return;
    }

    const key = e.key.toLowerCase();
    if (key === 'arrowup' || key === 'w') {
      e.preventDefault();
      setOverride('north');
    } else if (key === 'arrowdown' || key === 'z') {
      e.preventDefault();
      setOverride('south');
    } else if (key === 'arrowleft' || key === 'a') {
      e.preventDefault();
      setOverride('west');
    } else if (key === 'arrowright' || key === 'd') {
      e.preventDefault();
      setOverride('east');
    } else if (key === 's') {
      e.preventDefault();
      setOverride('stay');
    } else if (e.code === 'Space') {
      e.preventDefault();
      setOverride('attack');
    } else if (key === 'escape') {
      e.preventDefault();
      clearOverride();
    }
  });

  // Start polling on boot
  pollIntervalId = setInterval(pollAgentState, 500);
  pollAgentState();
})();
