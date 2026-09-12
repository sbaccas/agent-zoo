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

  // DOM Elements - Server Connection & Selection
  const selectCatEntity = document.getElementById('selectCatEntity');
  const modalSelectCatEntity = document.getElementById('modalSelectCatEntity');
  const btnConnectServer = document.getElementById('btnConnectServer');
  const connectBtnIcon = document.getElementById('connectBtnIcon');
  const connectBtnText = document.getElementById('connectBtnText');
  const btnChooseServer = document.getElementById('btnChooseServer');
  const currentServerLabel = document.getElementById('currentServerLabel');
  const serverLink = document.getElementById('serverLink');

  const serverModal = document.getElementById('serverModal');
  const btnCloseServer = document.getElementById('btnCloseServer');
  const btnPresetLocal = document.getElementById('btnPresetLocal');
  const btnPresetLocalhost = document.getElementById('btnPresetLocalhost');
  const inputServerUrl = document.getElementById('inputServerUrl');
  const modalConnStatus = document.getElementById('modalConnStatus');
  const modalConnEntity = document.getElementById('modalConnEntity');
  const btnModalDisconnect = document.getElementById('btnModalDisconnect');
  const btnModalConnect = document.getElementById('btnModalConnect');

  // DOM Elements - Past Inspection Banner
  const inspectionBanner = document.getElementById('inspectionBanner');
  const inspectingTickNumber = document.getElementById('inspectingTickNumber');
  const btnReturnLive = document.getElementById('btnReturnLive');

  // DOM Elements - Box 1: Observation
  const chipPos = document.getElementById('chipPos');
  const chipFacing = document.getElementById('chipFacing');
  const chipRound = document.getElementById('chipRound');
  const chipCooldown = document.getElementById('chipCooldown');
  const chipVisible = document.getElementById('chipVisible');
  const obsJsonViewer = document.getElementById('obsJsonViewer');
  const btnCopyObs = document.getElementById('btnCopyObs');
  const obsSubtitleChip = document.getElementById('obsSubtitleChip');

  // DOM Elements - Box 2: Brain & Override
  const badgeBrainState = document.getElementById('badgeBrainState');
  const engineBadge = document.getElementById('engineBadge');
  const btnViewSkills = document.getElementById('btnViewSkills');
  const btnConfigKey = document.getElementById('btnConfigKey');
  const tacticalStrategy = document.getElementById('tacticalStrategy');
  const tacticalTarget = document.getElementById('tacticalTarget');
  const tacticalEngine = document.getElementById('tacticalEngine');
  const autonomousThought = document.getElementById('autonomousThought');
  const autoActionPill = document.getElementById('autoActionPill');
  const autoDecisionLabel = document.querySelector('.autonomous-decision-label');
  const overrideTitle = document.querySelector('.override-title');
  const btnModeOnce = document.getElementById('btnModeOnce');
  const btnModeSticky = document.getElementById('btnModeSticky');
  const dpadButtons = document.querySelectorAll('.dpad-btn, .btn-attack-override, .btn-lunge-override');
  const overrideStatusBox = document.getElementById('overrideStatusBox');
  const overrideStatusText = document.getElementById('overrideStatusText');
  const btnClearOverride = document.getElementById('btnClearOverride');

  // DOM Elements - Modals
  const skillsModal = document.getElementById('skillsModal');
  const btnCloseSkills = document.getElementById('btnCloseSkills');
  const btnCloseSkillsFooter = document.getElementById('btnCloseSkillsFooter');
  const btnCopySkills = document.getElementById('btnCopySkills');
  const skillsManualContent = document.getElementById('skillsManualContent');

  const apiKeyModal = document.getElementById('apiKeyModal');
  const btnCloseApiKey = document.getElementById('btnCloseApiKey');
  const inputApiKey = document.getElementById('inputApiKey');
  const apiKeyStatus = document.getElementById('apiKeyStatus');
  const btnClearApiKey = document.getElementById('btnClearApiKey');
  const btnSaveApiKey = document.getElementById('btnSaveApiKey');

  // DOM Elements - Box 3: Action Response
  const actionSummaryCard = document.getElementById('actionSummaryCard');
  const responseActionPill = document.getElementById('responseActionPill');
  const responseSourceBadge = document.getElementById('responseSourceBadge');
  const responseThought = document.getElementById('responseThought');
  const respJsonViewer = document.getElementById('respJsonViewer');
  const btnCopyResp = document.getElementById('btnCopyResp');
  const respSubtitleChip = document.getElementById('respSubtitleChip');

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

  let wasServerReachable = true;

  // API Call helper
  async function api(path, method = 'GET', body = null) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 2500);
      const opts = {
        method,
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
      };
      if (body) {
        opts.body = JSON.stringify(body);
      }
      const res = await fetch(path, opts);
      clearTimeout(timer);
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      if (!wasServerReachable) {
        console.log('[API] Connection restored to server');
        wasServerReachable = true;
      }
      return await res.json();
    } catch (err) {
      if (wasServerReachable) {
        console.warn(`[API] Server unreachable on ${path}:`, err.message || err);
        wasServerReachable = false;
      }
      return null;
    }
  }

  // Render Box 1: Observation
  function renderObservation(obs, isHistorical = false) {
    if (!obs) {
      chipPos.textContent = '--';
      chipFacing.textContent = '--';
      chipRound.textContent = '--';
      if (chipCooldown) {
        chipCooldown.textContent = '--';
        chipCooldown.style.color = '';
      }
      chipVisible.textContent = '--';
      obsJsonViewer.textContent = '// Waiting for observations from simulation server...';
      if (obsSubtitleChip) obsSubtitleChip.textContent = 'Server ➔ Cat';
      return;
    }

    const selfInfo = obs.self || {};
    const pos = selfInfo.position || { x: '?', y: '?' };
    chipPos.textContent = `(${pos.x}, ${pos.y})`;
    chipFacing.textContent = (selfInfo.facing || 'north').toUpperCase();
    chipRound.textContent = obs.round !== undefined ? `#${obs.round}` : '--';

    if (chipCooldown) {
      const cd = selfInfo.action_cooldown;
      if (cd && cd > 0) {
        chipCooldown.textContent = `⏳ Locked (${cd}t: ${selfInfo.locked_action || 'acting'})`;
        chipCooldown.style.color = '#ea580c';
      } else {
        chipCooldown.textContent = '⚡ Ready';
        chipCooldown.style.color = '#10b981';
      }
    }

    const visible = obs.visible_entities || [];
    const mice = visible.filter((e) => e.type === 'mouse');
    if (mice.length > 0) {
      chipVisible.textContent = `${mice.length} Mouse (${mice.map((m) => m.id).join(', ')})`;
    } else {
      chipVisible.textContent = `${visible.length} entities`;
    }

    if (obsSubtitleChip) {
      if (isHistorical) {
        obsSubtitleChip.textContent = `Historical Tick #${obs.tick}`;
      } else if (obs.tick !== undefined) {
        obsSubtitleChip.textContent = `Incoming for Tick #${obs.tick}`;
      } else {
        obsSubtitleChip.textContent = 'Server ➔ Cat';
      }
    }

    obsJsonViewer.textContent = JSON.stringify(obs, null, 2);
  }

  // Render Box 2: Brain Decision & Override
  function renderBrain(state) {
    const currentTick = (state.active_tick !== undefined && state.active_tick !== null && state.active_tick > 0) ? state.active_tick : 1;
    if (overrideTitle) {
      overrideTitle.textContent = `Manual Override for Tick #${currentTick}`;
    }
    if (autoDecisionLabel) {
      autoDecisionLabel.textContent = `Proposed Auto Action for Tick #${currentTick}:`;
    }

    const hasAuth = Boolean(state.has_auth || state.has_api_key);
    const isVertex = state.auth_mode === 'vertex_ai';
    const lastSource = state.last_engine_source;
    if (engineBadge) {
      if (lastSource === 'adk_gemini' || hasAuth) {
        engineBadge.textContent = isVertex ? '✨ ADK Gemini (Vertex AI)' : '✨ ADK Gemini';
        engineBadge.className = 'engine-badge adk';
        engineBadge.title = isVertex
          ? `Google ADK Active via Vertex AI ADC (${state.brain_engine || 'gemini-2.5-flash'})`
          : `Google ADK Active (${state.brain_engine || 'gemini-2.5-flash'})`;
      } else {
        engineBadge.textContent = '🤖 Heuristic';
        engineBadge.className = 'engine-badge fallback';
        engineBadge.title = 'Autonomous Heuristic Brain (Awaiting Gemini Credentials)';
      }
    }

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
      } else if (phase === 'LUNGE') {
        badgeBrainState.classList.add('badge-lunge');
        autoActionPill.className = 'action-pill-lg';
      } else if (phase === 'STALKING') {
        badgeBrainState.classList.add('badge-stalking');
        autoActionPill.className = 'action-pill-lg';
      } else {
        badgeBrainState.classList.add('badge-roaming');
        autoActionPill.className = 'action-pill-lg';
      }

      if (tacticalStrategy) tacticalStrategy.textContent = phase;
      if (tacticalTarget) tacticalTarget.textContent = meta.target_id || 'None';
      if (tacticalEngine) {
        if (meta.engine === 'adk_gemini' || (hasAuth && meta.engine !== 'heuristic_fallback')) {
          tacticalEngine.textContent = isVertex ? 'ADK Gemini (Vertex AI)' : 'ADK Gemini';
        } else if (hasAuth && lastSource === 'adk_gemini') {
          tacticalEngine.textContent = isVertex ? 'ADK Gemini (Vertex AI)' : 'ADK Gemini';
        } else {
          tacticalEngine.textContent = 'Heuristic';
        }
      }
    } else {
      autonomousThought.textContent = 'Standing by for tick...';
      autoActionPill.textContent = '--';
      autoActionPill.className = 'action-pill-lg';
      badgeBrainState.textContent = 'IDLE';
      badgeBrainState.className = 'badge-state badge-roaming';
      if (tacticalStrategy) tacticalStrategy.textContent = '--';
      if (tacticalTarget) tacticalTarget.textContent = 'None';
      if (tacticalEngine) tacticalEngine.textContent = hasAuth ? (isVertex ? 'ADK Gemini (Vertex AI)' : 'ADK Gemini') : 'Heuristic';
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
      if (respSubtitleChip) respSubtitleChip.textContent = 'Cat ➔ Server';
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
    const isAdk = resp.source === 'adk_gemini';

    if (isOverride) {
      responseSourceBadge.textContent = 'Override';
      responseSourceBadge.className = 'history-source-badge override';
      actionSummaryCard.classList.add('override-sent');
    } else if (isAdk) {
      responseSourceBadge.textContent = 'ADK Gemini';
      responseSourceBadge.className = 'history-source-badge adk-gemini';
      actionSummaryCard.classList.remove('override-sent');
    } else {
      responseSourceBadge.textContent = 'Heuristic';
      responseSourceBadge.className = 'history-source-badge auto';
      actionSummaryCard.classList.remove('override-sent');
    }

    if (respSubtitleChip) {
      const tickNum = (cachedState && cachedState.active_tick !== undefined && cachedState.active_tick !== null) ? `Tick #${cachedState.active_tick} ` : '';
      respSubtitleChip.textContent = `${tickNum}Executed & Recorded`;
    }

    if (resp.resulting_posture) {
      const rp = resp.resulting_posture;
      const rPos = rp.position || {};
      responseThought.textContent = `${resp.thought || ''} • Resulting: (${rPos.x}, ${rPos.y}) facing ${(rp.facing || '').toUpperCase()}`;
    } else {
      responseThought.textContent = resp.thought || '';
    }

    const payloadObj = {
      action: resp.action,
      thought: resp.thought,
    };
    if (resp.resulting_posture) {
      payloadObj.resulting_posture = resp.resulting_posture;
    }

    respJsonViewer.textContent = JSON.stringify(payloadObj, null, 2);
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
      const isAdk = item.source === 'adk_gemini';
      if (isOverride) {
        sourceBadge.className = 'history-source-badge override';
        sourceBadge.textContent = 'Override';
      } else if (isAdk) {
        sourceBadge.className = 'history-source-badge adk-gemini';
        sourceBadge.textContent = 'ADK Gemini';
      } else {
        sourceBadge.className = 'history-source-badge auto';
        sourceBadge.textContent = 'Heuristic';
      }
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
      const selfObj = obs.self || {};
      const pos = selfObj.position ? `(${selfObj.position.x}, ${selfObj.position.y})` : '';
      const facing = (selfObj.facing || '').toUpperCase();
      const visible = obs.visible_entities || [];
      const mice = visible.filter((e) => e.type === 'mouse');

      const rp = (item.response && item.response.resulting_posture) ? item.response.resulting_posture : null;
      let postureTransition = `At ${pos} [${facing}]`;
      if (rp && rp.position) {
        postureTransition = `${pos} [${facing}] ➔ (${rp.position.x}, ${rp.position.y}) [${(rp.facing || '').toUpperCase()}]`;
      }

      const senseRow = document.createElement('span');
      senseRow.style.fontSize = '0.72rem';
      senseRow.style.color = 'var(--text-muted)';
      senseRow.textContent = `${postureTransition} | ${mice.length} mouse seen`;
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
    renderObservation(item.observation, true);

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
    if (state.active_tick !== undefined && state.active_tick !== null && state.active_tick > 0) {
      headerTick.textContent = `#${state.active_tick}`;
    } else {
      headerTick.textContent = '--';
    }
    headerCycles.textContent = state.total_ticks_processed || '0';

    const conn = state.server_connection || {};
    const isWsConnected = Boolean(state.server_connected || conn.connected);
    const isConnecting = Boolean(conn.connecting);

    if (isWsConnected) {
      statusBadge.className = 'wb-status-badge online';
      statusText.textContent = `WS Connected • ${conn.assigned_id || state.agent_id || 'cat-1'}`;
      if (btnConnectServer) {
        btnConnectServer.className = 'btn btn-sm btn-danger-outline';
        if (connectBtnIcon) connectBtnIcon.textContent = '✕';
        if (connectBtnText) connectBtnText.textContent = 'Disconnect';
        btnConnectServer.title = 'Disconnect from Game Server WebSocket';
      }
    } else if (isConnecting) {
      statusBadge.className = 'wb-status-badge waiting';
      statusText.textContent = 'Connecting...';
      if (btnConnectServer) {
        btnConnectServer.className = 'btn btn-sm btn-primary';
        if (connectBtnIcon) connectBtnIcon.textContent = '◌';
        if (connectBtnText) connectBtnText.textContent = 'Connecting...';
        btnConnectServer.title = 'Attempting connection...';
      }
    } else {
      statusBadge.className = 'wb-status-badge disconnected';
      statusText.textContent = 'Disconnected';
      if (btnConnectServer) {
        btnConnectServer.className = 'btn btn-sm btn-primary';
        if (connectBtnIcon) connectBtnIcon.textContent = '🔌';
        if (connectBtnText) connectBtnText.textContent = 'Connect';
        btnConnectServer.title = 'Connect to Game Server WebSocket';
      }
    }

    if (conn.server_url) {
      let host = conn.server_url;
      try {
        const parsed = new URL(conn.server_url);
        host = parsed.host;
      } catch (e) {
        host = conn.server_url.replace(/^(ws|wss|http|https):\/\//, '').split('/')[0];
      }
      if (currentServerLabel) currentServerLabel.textContent = host;
      if (serverLink) serverLink.href = `http://${host}`;
      if (inputServerUrl && !inputServerUrl.dataset.userEdited) {
        inputServerUrl.value = conn.server_url;
      }
    }

    if (modalConnStatus) {
      modalConnStatus.textContent = isWsConnected ? 'Connected' : (isConnecting ? 'Connecting...' : 'Disconnected');
    }
    if (modalConnEntity) {
      modalConnEntity.textContent = conn.assigned_id || state.agent_id || '--';
    }

    const activeCatId = conn.assigned_id || state.agent_id;
    if (activeCatId) {
      if (selectCatEntity && document.activeElement !== selectCatEntity && selectCatEntity.value !== activeCatId) {
        selectCatEntity.value = activeCatId;
      }
      if (modalSelectCatEntity && document.activeElement !== modalSelectCatEntity && modalSelectCatEntity.value !== activeCatId) {
        modalSelectCatEntity.value = activeCatId;
      }
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

  let pollCount = 0;
  let knownCats = ['cat-1', 'cat-2'];

  async function fetchAvailableCats() {
    const res = await api('/api/agent/server/cats');
    if (res && Array.isArray(res.cats) && res.cats.length > 0) {
      const catIds = res.cats.map((c) => c.id);
      knownCats = catIds;
      updateCatDropdowns(catIds, res.current);
    }
  }

  function updateCatDropdowns(catIds, currentSelected) {
    [selectCatEntity, modalSelectCatEntity].forEach((selectElem) => {
      if (!selectElem) return;
      const prevVal = currentSelected || selectElem.value || catIds[0];
      const options = catIds.map((cid) => {
        const opt = document.createElement('option');
        opt.value = cid;
        opt.textContent = cid;
        if (cid === prevVal) opt.selected = true;
        return opt;
      });
      selectElem.replaceChildren(...options);
      selectElem.value = prevVal;
    });
  }

  // Fetch agent state with live board synchronization
  async function pollAgentState() {
    pollCount++;
    if (pollCount % 10 === 1) {
      fetchAvailableCats();
    }
    const data = await api('/api/agent/state?sync=1');
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
    } else if (key === 'l') {
      e.preventDefault();
      setOverride('lunge');
    } else if (key === 'escape') {
      e.preventDefault();
      clearOverride();
    }
  });

  // Event Listeners - Skills Manual Modal
  if (btnViewSkills && skillsModal) {
    btnViewSkills.addEventListener('click', async () => {
      skillsModal.style.display = 'flex';
      skillsManualContent.textContent = 'Loading skills manual from server...';
      const res = await api('/api/agent/skills');
      if (res && res.content) {
        skillsManualContent.textContent = res.content;
      } else {
        skillsManualContent.textContent = 'Failed to load skills manual from server.';
      }
    });
  }

  function closeSkillsModal() {
    if (skillsModal) skillsModal.style.display = 'none';
  }
  if (btnCloseSkills) btnCloseSkills.addEventListener('click', closeSkillsModal);
  if (btnCloseSkillsFooter) btnCloseSkillsFooter.addEventListener('click', closeSkillsModal);
  if (btnCopySkills) {
    btnCopySkills.addEventListener('click', () => {
      navigator.clipboard.writeText(skillsManualContent.textContent).then(() => {
        flashCopied(btnCopySkills, '✓ Copied Manual!');
      });
    });
  }

  // Event Listeners - API Key Modal
  if (btnConfigKey && apiKeyModal) {
    btnConfigKey.addEventListener('click', () => {
      apiKeyModal.style.display = 'flex';
      const hasAuth = Boolean(cachedState && (cachedState.has_auth || cachedState.has_api_key));
      const isVertex = cachedState && cachedState.auth_mode === 'vertex_ai';
      const proj = (cachedState && cachedState.gcp_project) ? ` (Project: ${cachedState.gcp_project})` : '';

      if (isVertex) {
        apiKeyStatus.textContent = `✓ Active Vertex AI ADC credentials detected${proj}.`;
        apiKeyStatus.style.color = 'var(--success-color)';
      } else if (hasAuth) {
        apiKeyStatus.textContent = '✓ Active Gemini API Key detected on agent server.';
        apiKeyStatus.style.color = 'var(--success-color)';
      } else {
        apiKeyStatus.textContent = '⚠️ No Gemini credentials set. Operating with Heuristic Tactical Fallback.';
        apiKeyStatus.style.color = 'var(--warning-color)';
      }
    });
  }

  function closeApiKeyModal() {
    if (apiKeyModal) apiKeyModal.style.display = 'none';
    if (inputApiKey) inputApiKey.value = '';
  }
  if (btnCloseApiKey) btnCloseApiKey.addEventListener('click', closeApiKeyModal);

  if (btnSaveApiKey) {
    btnSaveApiKey.addEventListener('click', async () => {
      const keyVal = inputApiKey ? inputApiKey.value.trim() : '';
      if (!keyVal) {
        apiKeyStatus.textContent = 'Please enter a valid Gemini API Key.';
        apiKeyStatus.style.color = 'var(--danger-color)';
        return;
      }
      btnSaveApiKey.disabled = true;
      btnSaveApiKey.textContent = 'Saving...';
      const res = await api('/api/agent/set-key', 'POST', { api_key: keyVal });
      btnSaveApiKey.disabled = false;
      btnSaveApiKey.textContent = 'Save Key';
      if (res && res.status === 'ok') {
        apiKeyStatus.textContent = '✓ Gemini API Key saved successfully!';
        apiKeyStatus.style.color = 'var(--success-color)';
        setTimeout(closeApiKeyModal, 800);
        await pollAgentState();
      } else {
        apiKeyStatus.textContent = 'Failed to save API key on server.';
        apiKeyStatus.style.color = 'var(--danger-color)';
      }
    });
  }

  if (btnClearApiKey) {
    btnClearApiKey.addEventListener('click', async () => {
      btnClearApiKey.disabled = true;
      const res = await api('/api/agent/set-key', 'POST', { api_key: '' });
      btnClearApiKey.disabled = false;
      if (res && res.status === 'cleared') {
        apiKeyStatus.textContent = 'API Key cleared. Running on Heuristic Fallback.';
        apiKeyStatus.style.color = 'var(--text-muted)';
        setTimeout(closeApiKeyModal, 800);
        await pollAgentState();
      }
    });
  }

  // Event Listeners - Server Selection & Connection
  function openServerModal() {
    if (serverModal) {
      serverModal.style.display = 'flex';
      fetchAvailableCats();
      const conn = (cachedState && cachedState.server_connection) || {};
      if (inputServerUrl && conn.server_url) {
        inputServerUrl.value = conn.server_url;
      }
      if (modalConnStatus) {
        modalConnStatus.textContent = (cachedState && (cachedState.server_connected || conn.connected))
          ? 'Connected'
          : (conn.connecting ? 'Connecting...' : 'Disconnected');
      }
      const activeCat = conn.assigned_id || (cachedState && cachedState.agent_id) || 'cat-1';
      if (modalConnEntity) {
        modalConnEntity.textContent = activeCat;
      }
      if (modalSelectCatEntity) {
        modalSelectCatEntity.value = activeCat;
      }
    }
  }

  function closeServerModal() {
    if (serverModal) serverModal.style.display = 'none';
  }

  if (btnChooseServer) btnChooseServer.addEventListener('click', openServerModal);
  if (btnCloseServer) btnCloseServer.addEventListener('click', closeServerModal);

  if (selectCatEntity) {
    selectCatEntity.addEventListener('change', async () => {
      const chosen = selectCatEntity.value;
      if (modalSelectCatEntity) modalSelectCatEntity.value = chosen;
      const isConnected = Boolean(cachedState && (cachedState.server_connected || (cachedState.server_connection && cachedState.server_connection.connected)));
      if (isConnected) {
        const url = (inputServerUrl && inputServerUrl.value.trim()) || 'ws://127.0.0.1:8000/ws/agents';
        await api('/api/agent/server/connect', 'POST', { server_url: url, cat_id: chosen });
        await pollAgentState();
      }
    });
  }

  if (modalSelectCatEntity) {
    modalSelectCatEntity.addEventListener('change', () => {
      if (selectCatEntity) selectCatEntity.value = modalSelectCatEntity.value;
    });
  }

  if (inputServerUrl) {
    inputServerUrl.addEventListener('input', () => {
      inputServerUrl.dataset.userEdited = 'true';
    });
  }

  if (btnPresetLocal && inputServerUrl) {
    btnPresetLocal.addEventListener('click', () => {
      inputServerUrl.value = 'ws://127.0.0.1:8000/ws/agents';
      inputServerUrl.dataset.userEdited = 'true';
    });
  }

  if (btnPresetLocalhost && inputServerUrl) {
    btnPresetLocalhost.addEventListener('click', () => {
      inputServerUrl.value = 'ws://localhost:8000/ws/agents';
      inputServerUrl.dataset.userEdited = 'true';
    });
  }

  if (btnModalConnect) {
    btnModalConnect.addEventListener('click', async () => {
      const url = inputServerUrl ? inputServerUrl.value.trim() : '';
      const catId = (modalSelectCatEntity && modalSelectCatEntity.value) || (selectCatEntity && selectCatEntity.value) || 'cat-1';
      btnModalConnect.disabled = true;
      btnModalConnect.textContent = 'Connecting...';
      await api('/api/agent/server/connect', 'POST', { server_url: url, cat_id: catId });
      btnModalConnect.disabled = false;
      btnModalConnect.textContent = 'Connect Now';
      closeServerModal();
      await pollAgentState();
    });
  }

  if (btnModalDisconnect) {
    btnModalDisconnect.addEventListener('click', async () => {
      btnModalDisconnect.disabled = true;
      await api('/api/agent/server/disconnect', 'POST');
      btnModalDisconnect.disabled = false;
      closeServerModal();
      await pollAgentState();
    });
  }

  if (btnConnectServer) {
    btnConnectServer.addEventListener('click', async () => {
      const isConnected = Boolean(cachedState && (cachedState.server_connected || (cachedState.server_connection && cachedState.server_connection.connected)));
      btnConnectServer.disabled = true;
      if (isConnected) {
        await api('/api/agent/server/disconnect', 'POST');
      } else {
        const url = (inputServerUrl && inputServerUrl.value.trim()) || 'ws://127.0.0.1:8000/ws/agents';
        const catId = (selectCatEntity && selectCatEntity.value) || (modalSelectCatEntity && modalSelectCatEntity.value) || 'cat-1';
        await api('/api/agent/server/connect', 'POST', { server_url: url, cat_id: catId });
      }
      btnConnectServer.disabled = false;
      await pollAgentState();
    });
  }

  // Close modals on overlay background click
  window.addEventListener('click', (e) => {
    if (e.target === skillsModal) closeSkillsModal();
    if (e.target === apiKeyModal) closeApiKeyModal();
    if (e.target === serverModal) closeServerModal();
  });

  // Start polling on boot
  pollIntervalId = setInterval(pollAgentState, 500);
  pollAgentState();
})();
