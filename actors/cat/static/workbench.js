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

  // DOM Elements - Auth Alert Banner
  const authAlertBanner = document.getElementById('authAlertBanner');
  const authAlertTitle = document.getElementById('authAlertTitle');
  const authAlertDetail = document.getElementById('authAlertDetail');
  const authAlertCommand = document.getElementById('authAlertCommand');
  const btnCopyAuthCommand = document.getElementById('btnCopyAuthCommand');
  const btnVerifyAuth = document.getElementById('btnVerifyAuth');
  const btnBannerApiKey = document.getElementById('btnBannerApiKey');

  // DOM Elements - Past Inspection Banner
  const inspectionBanner = document.getElementById('inspectionBanner');
  const inspectingTickNumber = document.getElementById('inspectingTickNumber');
  const btnReturnLive = document.getElementById('btnReturnLive');

  // DOM Elements - Box 1: Observation
  const chipPos = document.getElementById('chipPos');
  const chipFacing = document.getElementById('chipFacing');
  const chipRound = document.getElementById('chipRound');
  const chipReach = document.getElementById('chipReach');
  const chipSpeed = document.getElementById('chipSpeed');
  const chipVision = document.getElementById('chipVision');
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

  // DOM Elements - N-Tick History Window Hyperparameter
  const sliderHistoryWindow = document.getElementById('sliderHistoryWindow');
  const historyWindowBadge = document.getElementById('historyWindowBadge');
  const tokenEstBadge = document.getElementById('tokenEstBadge');
  let isDraggingHistorySlider = false;

  function estimateTokens(n) {
    return Math.round(320 + n * 45);
  }

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

  // DOM Elements - Box 4: Commander Radio & Direct Agent Interrogation
  const cardCommanderRadio = document.getElementById('cardCommanderRadio');
  const btnRadioInterrogate = document.getElementById('btnRadioInterrogate');
  const radioStatusBadge = document.getElementById('radioStatusBadge');
  const btnClearChat = document.getElementById('btnClearChat');
  const chatMessagesContainer = document.getElementById('chatMessages');
  const chatPromptForm = document.getElementById('chatPromptForm');
  const inputUserPrompt = document.getElementById('inputUserPrompt');
  const btnSendPrompt = document.getElementById('btnSendPrompt');
  const sendPromptIcon = document.getElementById('sendPromptIcon');
  const sendPromptText = document.getElementById('sendPromptText');
  const checkSteerDirective = document.getElementById('checkSteerDirective');
  const chatTypingIndicator = document.getElementById('chatTypingIndicator');
  const presetChips = document.querySelectorAll('.btn-preset-chip');
  let isSendingPrompt = false;
  let lastRenderedChatLen = -1;

  // DOM Elements - Box 5: History
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
      if (chipRound) chipRound.textContent = '--';
      if (chipReach) chipReach.textContent = '--';
      if (chipSpeed) chipSpeed.textContent = '--';
      if (chipVision) chipVision.textContent = '--';
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
    if (chipRound) chipRound.textContent = obs.round !== undefined ? `#${obs.round}` : '--';

    const caps = obs.capabilities || {};
    if (chipReach) {
      const atkR = caps.attack_range !== undefined ? caps.attack_range : 2;
      chipReach.textContent = `${atkR} tile${atkR === 1 ? '' : 's'}`;
    }
    if (chipSpeed) {
      const moveCd = caps.move_cooldown !== undefined ? caps.move_cooldown : 3;
      chipSpeed.textContent = `1 tile / ${moveCd}t`;
    }
    if (chipVision) {
      const visR = caps.vision_radius !== undefined ? caps.vision_radius : 4;
      chipVision.textContent = `${visR} tiles`;
    }

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

    const hasAuth = Boolean(state.has_auth && state.auth_valid);
    const isVertex = state.auth_mode === 'vertex_ai';
    const isAuthExpired = !hasAuth;
    const engineErr = isAuthExpired ? (state.last_engine_error || state.auth_error) : null;

    // 1. Update Top Alert Banner
    if (authAlertBanner) {
      if (isAuthExpired) {
        authAlertBanner.style.display = 'flex';
        if (authAlertTitle) {
          authAlertTitle.textContent = isVertex
            ? 'VERTEX AI AUTHENTICATION EXPIRED — LLM IS OFFLINE'
            : 'GEMINI AUTHENTICATION FAILED — LLM IS OFFLINE';
        }
        if (authAlertDetail) {
          authAlertDetail.textContent = engineErr
            ? `Decisions are currently being generated by local reflex heuristics because authentication failed: "${engineErr}".`
            : 'Decisions are currently being generated by local reflex heuristics because your Vertex AI credentials need reauthentication.';
        }
        if (authAlertCommand) {
          authAlertCommand.textContent = state.reauth_command || 'gcloud auth application-default login';
        }
      } else {
        authAlertBanner.style.display = 'none';
      }
    }

    // 2. Engine Badge in Box 2 Header
    if (engineBadge) {
      if (isAuthExpired) {
        engineBadge.textContent = '⚠️ ADC Expired (Fallback)';
        engineBadge.className = 'engine-badge error';
        engineBadge.title = `Vertex AI Token Expired: ${engineErr || 'Run gcloud auth application-default login'}`;
      } else if (state.is_deliberating) {
        engineBadge.textContent = '⏳ Deliberating...';
        engineBadge.className = 'engine-badge deliberating';
        engineBadge.title = 'Gemini LLM is deliberating in background';
      } else if (hasAuth) {
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
        // Report the engine that ACTUALLY produced this decision. Using live auth
        // here credited the model for every reflex tick.
        const engDesc = describeSource(meta.engine, meta.fallback_reason);
        tacticalEngine.textContent = (meta.engine === 'adk_gemini' && isVertex)
          ? 'ADK Gemini (Vertex AI)'
          : engDesc.label;
        tacticalEngine.title = engDesc.title;
        tacticalEngine.style.color = meta.engine === 'adk_gemini' ? '#7e22ce' : '#b45309';
        tacticalEngine.style.fontWeight = '700';
      }
    } else {
      autonomousThought.textContent = 'Standing by for tick...';
      autoActionPill.textContent = '--';
      autoActionPill.className = 'action-pill-lg';
      badgeBrainState.textContent = 'IDLE';
      badgeBrainState.className = 'badge-state badge-roaming';
      if (tacticalStrategy) tacticalStrategy.textContent = '--';
      if (tacticalTarget) tacticalTarget.textContent = 'None';
      if (tacticalEngine) {
        // No decision has been made yet, so this is an availability statement,
        // not a claim about how any tick was decided.
        if (hasAuth) {
          tacticalEngine.textContent = isVertex
            ? 'Awaiting tick — ADK Gemini (Vertex AI) ready'
            : 'Awaiting tick — ADK Gemini ready';
          tacticalEngine.style.color = '#7e22ce';
        } else {
          tacticalEngine.textContent = 'Awaiting tick — heuristics only (auth expired)';
          tacticalEngine.style.color = '#dc2626';
        }
        tacticalEngine.title = '';
        tacticalEngine.style.fontWeight = '700';
      }
    }

    // Synchronize N-tick history window hyperparameter
    if (sliderHistoryWindow && !isDraggingHistorySlider) {
      const windowSize = state.history_window_size || 3;
      sliderHistoryWindow.value = windowSize;
      if (historyWindowBadge) {
        historyWindowBadge.textContent = `${windowSize} ticks`;
      }
      if (tokenEstBadge) {
        tokenEstBadge.textContent = `~${estimateTokens(windowSize)} tokens`;
      }
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

  // Maps a RECORDED decision source to its badge presentation.
  //
  // This deliberately takes no account of current auth status. Both badge sites
  // used to read `(source === 'adk_gemini' || hasAuth)`, and because `hasAuth` is
  // recomputed from live state on every render, a healthy credential retroactively
  // relabelled every past heuristic tick as "ADK Gemini". A tick's source is a
  // historical fact; it is decided once, server-side, and never re-derived here.
  // `cls`  -> modifier for `.history-source-badge`
  // `chip` -> modifier for `.chip-window-tag` ('' means the base style)
  function describeSource(source, fallbackReason) {
    switch (source) {
      case 'manual_override':
        return { label: 'Override', cls: 'override', tag: 'MANUAL', chip: '',
                 title: 'You issued this action manually' };
      case 'adk_gemini':
        return { label: 'ADK Gemini', cls: 'adk-gemini', tag: 'LLM', chip: '',
                 title: 'Decision generated by the Gemini model' };
      case 'heuristic_reflex':
        return { label: 'Reflex (System 1)', cls: 'reflex', tag: 'REFLEX', chip: 'reflex',
                 title: fallbackReason
                   || 'Fast local reflex; Gemini had not returned a decision within the tick budget' };
      case 'heuristic':
        return { label: 'Heuristic', cls: 'heuristic-plain', tag: 'HEURISTIC', chip: 'heuristic-plain',
                 title: fallbackReason || 'No Gemini credentials configured' };
      case 'heuristic_fallback':
        return { label: 'Fallback', cls: 'fallback-error', tag: 'FALLBACK', chip: 'fallback',
                 title: fallbackReason || 'Gemini call did not produce a usable decision' };
      default:
        return { label: source ? String(source) : 'Unknown', cls: 'heuristic-plain',
                 tag: 'OTHER', chip: 'heuristic-plain',
                 title: fallbackReason || 'Unrecognised decision source' };
    }
  }

  // Pulls the reason recorded for THIS tick. Older rows (and the autonomous
  // decision that preceded a manual override) carry it under meta instead.
  function itemFallbackReason(item) {
    if (!item) return null;
    if (item.response && item.response.fallback_reason) return item.response.fallback_reason;
    if (item.autonomous_decision && item.autonomous_decision.meta
        && item.autonomous_decision.meta.fallback_reason) {
      return item.autonomous_decision.meta.fallback_reason;
    }
    return null;
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
    const desc = describeSource(resp.source, resp.fallback_reason);

    responseSourceBadge.textContent = desc.label;
    responseSourceBadge.className = `history-source-badge ${desc.cls}`;
    responseSourceBadge.title = desc.title;
    if (isOverride) {
      actionSummaryCard.classList.add('override-sent');
    } else {
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
    const windowSize = (sliderHistoryWindow ? parseInt(sliderHistoryWindow.value, 10) : (cachedState && cachedState.history_window_size)) || 3;
    const windowCutoffIndex = Math.max(0, history ? (history.length - windowSize) : 0);
    const inWindowCount = history ? Math.min(history.length, windowSize) : 0;
    historyCountPill.textContent = `${(history && history.length) || 0} ticks (${inWindowCount} in LLM context)`;

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

      const isInWindow = (i >= windowCutoffIndex);
      if (isInWindow) {
        tr.classList.add('in-llm-window');
      }

      // 1. Tick
      const tdTick = document.createElement('td');
      const strongTick = document.createElement('strong');
      strongTick.textContent = `#${item.tick}`;
      tdTick.appendChild(strongTick);

      // Truthful badge: the source recorded on THIS row decides the label.
      // Never consult live auth state here -- see describeSource().
      const itemDesc = describeSource(item.source, itemFallbackReason(item));

      const tag = document.createElement('span');
      tag.className = itemDesc.chip
        ? `chip-window-tag ${itemDesc.chip}`
        : 'chip-window-tag';
      tag.title = itemDesc.title;
      tag.textContent = itemDesc.tag;
      tdTick.appendChild(tag);

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

      sourceBadge.className = `history-source-badge ${itemDesc.cls}`;
      sourceBadge.textContent = itemDesc.label;
      sourceBadge.title = itemDesc.title;
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

  // Render Box 4: Commander Radio & Direct Agent Interrogation (Zero innerHTML)
  function renderChatHistory(chatList) {
    if (!chatMessagesContainer) return;

    if (!chatList || chatList.length === 0) {
      const bubble = document.createElement('div');
      bubble.className = 'chat-bubble cat-bubble';

      const hdr = document.createElement('div');
      hdr.className = 'chat-bubble-header';
      const sender = document.createElement('span');
      sender.className = 'chat-sender';
      sender.textContent = '🐱 Cat Agent [cat-1]';
      const meta = document.createElement('span');
      meta.className = 'chat-meta';
      meta.textContent = (cachedState && cachedState.active_tick) ? `Tick #${cachedState.active_tick}` : 'Standby';
      hdr.appendChild(sender);
      hdr.appendChild(meta);

      const body = document.createElement('div');
      body.className = 'chat-bubble-body';
      body.textContent = "Commander, radio link established! You can ask me what I'm thinking, why I took certain moves, why I'm circling or not exploring parts of the board, or order me to pivot tactics.";

      bubble.appendChild(hdr);
      bubble.appendChild(body);
      chatMessagesContainer.replaceChildren(bubble);
      lastRenderedChatLen = 0;
      return;
    }

    const bubbles = chatList.map((msg) => {
      const bubble = document.createElement('div');
      const isUser = msg.sender === 'commander' || msg.source === 'user';
      bubble.className = isUser ? 'chat-bubble user-bubble' : 'chat-bubble cat-bubble';

      const hdr = document.createElement('div');
      hdr.className = 'chat-bubble-header';

      const sender = document.createElement('span');
      sender.className = 'chat-sender';
      sender.textContent = isUser ? '👨‍✈️ Commander' : '🐱 Cat Agent [cat-1]';

      const meta = document.createElement('span');
      meta.className = 'chat-meta';
      const tickText = (msg.tick !== undefined && msg.tick !== null && msg.tick > 0) ? `Tick #${msg.tick}` : '';
      const timeText = msg.timestamp || '';
      meta.textContent = [tickText, timeText].filter(Boolean).join(' • ');

      hdr.appendChild(sender);
      hdr.appendChild(meta);
      bubble.appendChild(hdr);

      const body = document.createElement('div');
      body.className = 'chat-bubble-body';
      body.textContent = msg.message || '';
      bubble.appendChild(body);

      if (msg.adopted_directive) {
        const badge = document.createElement('span');
        badge.className = 'chat-directive-badge';
        badge.textContent = `🎯 Adopted as Active Directive`;
        bubble.appendChild(badge);
      }

      return bubble;
    });

    chatMessagesContainer.replaceChildren(...bubbles);
    lastRenderedChatLen = chatList.length;
    chatMessagesContainer.scrollTop = chatMessagesContainer.scrollHeight;
  }

  async function handleSendPrompt(e) {
    if (e) e.preventDefault();
    if (isSendingPrompt) return;
    const promptText = (inputUserPrompt.value || '').trim();
    if (!promptText) {
      inputUserPrompt.focus();
      return;
    }

    const steer = checkSteerDirective ? checkSteerDirective.checked : true;
    isSendingPrompt = true;
    btnSendPrompt.disabled = true;
    inputUserPrompt.disabled = true;
    if (chatTypingIndicator) chatTypingIndicator.style.display = 'inline-flex';
    if (sendPromptIcon) sendPromptIcon.textContent = '⏳';
    if (sendPromptText) sendPromptText.textContent = 'Analyzing...';

    // Optimistic local bubble append
    const userBubble = document.createElement('div');
    userBubble.className = 'chat-bubble user-bubble';
    const hdr = document.createElement('div');
    hdr.className = 'chat-bubble-header';
    const sender = document.createElement('span');
    sender.className = 'chat-sender';
    sender.textContent = '👨‍✈️ Commander';
    const meta = document.createElement('span');
    meta.className = 'chat-meta';
    meta.textContent = (cachedState && cachedState.active_tick) ? `Tick #${cachedState.active_tick}` : 'Now';
    hdr.appendChild(sender);
    hdr.appendChild(meta);
    const body = document.createElement('div');
    body.className = 'chat-bubble-body';
    body.textContent = promptText;
    userBubble.appendChild(hdr);
    userBubble.appendChild(body);
    chatMessagesContainer.appendChild(userBubble);
    chatMessagesContainer.scrollTop = chatMessagesContainer.scrollHeight;

    try {
      const res = await api('/api/agent/prompt', 'POST', {
        prompt: promptText,
        steer_directive: steer,
      });

      if (res) {
        inputUserPrompt.value = '';
        await pollAgentState();
      }
    } catch (err) {
      console.error('Failed to interrogate agent:', err);
      const errBubble = document.createElement('div');
      errBubble.className = 'chat-bubble cat-bubble';
      const errBody = document.createElement('div');
      errBody.className = 'chat-bubble-body';
      errBody.style.color = 'var(--danger-color)';
      errBody.textContent = `⚠️ Connection error: ${err.message || err}`;
      errBubble.appendChild(errBody);
      chatMessagesContainer.appendChild(errBubble);
      chatMessagesContainer.scrollTop = chatMessagesContainer.scrollHeight;
    } finally {
      isSendingPrompt = false;
      btnSendPrompt.disabled = false;
      inputUserPrompt.disabled = false;
      if (chatTypingIndicator) chatTypingIndicator.style.display = 'none';
      if (sendPromptIcon) sendPromptIcon.textContent = '📡';
      if (sendPromptText) sendPromptText.textContent = 'Send to Agent';
      inputUserPrompt.focus();
    }
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
  // DOM Elements - Box 6: Strategic Evaluator (Gem2)
  const evalStateBadge = document.getElementById('evalStateBadge');
  const evalTranscriptTicks = document.getElementById('evalTranscriptTicks');
  const evalLastRun = document.getElementById('evalLastRun');
  const evalNextTick = document.getElementById('evalNextTick');
  const evalSource = document.getElementById('evalSource');
  const evalIntervalBadge = document.getElementById('evalIntervalBadge');
  const sliderEvalInterval = document.getElementById('sliderEvalInterval');
  const checkEvalEnabled = document.getElementById('checkEvalEnabled');
  const evalNotesContent = document.getElementById('evalNotesContent');
  const btnRunEvalNow = document.getElementById('btnRunEvalNow');
  const btnCopyEvalNotes = document.getElementById('btnCopyEvalNotes');
  const btnViewEvalSkills = document.getElementById('btnViewEvalSkills');
  const skillsModalTitle = document.getElementById('skillsModalTitle');

  // True while the user is dragging the interval slider, so incoming poll data
  // does not yank the handle out from under them.
  let isDraggingEvalSlider = false;
  let rawEvalNotes = '';

  // Splits the evaluator's markdown into directives. Continuation lines (any line
  // that does not start a new bullet) are folded into the preceding directive.
  function parseDirectives(markdown) {
    const out = [];
    const lines = String(markdown || '').split('\n');
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const bullet = trimmed.match(/^[-*]\s+(.*)$/);
      if (bullet) {
        out.push(bullet[1]);
      } else if (out.length) {
        out[out.length - 1] += ' ' + trimmed;
      } else {
        out.push(trimmed);
      }
    }
    return out;
  }

  // Renders directives as discrete DOM nodes. Model output is untrusted text and
  // is never assigned as markup.
  function renderEvalNotes(notes) {
    rawEvalNotes = notes || '';
    while (evalNotesContent.firstChild) {
      evalNotesContent.removeChild(evalNotesContent.firstChild);
    }

    const directives = parseDirectives(notes);
    if (!directives.length) {
      evalNotesContent.classList.add('eval-notes-empty');
      evalNotesContent.textContent =
        'No coaching notes yet. The evaluator publishes its first set of standing orders once the interval above elapses.';
      return;
    }

    evalNotesContent.classList.remove('eval-notes-empty');
    for (const directive of directives) {
      const row = document.createElement('div');
      row.className = 'eval-directive';

      const dot = document.createElement('span');
      dot.className = 'eval-directive-bullet';
      dot.textContent = '\u25b8';
      row.appendChild(dot);

      const body = document.createElement('span');
      body.className = 'eval-directive-text';

      // Pull out a leading "**Label**:" so it can be emphasised separately.
      const labelled = directive.match(/^\*\*(.+?)\*\*:?\s*(.*)$/);
      if (labelled) {
        const label = document.createElement('span');
        label.className = 'eval-directive-label';
        label.textContent = labelled[1] + ': ';
        body.appendChild(label);
        body.appendChild(document.createTextNode(labelled[2].replace(/\*\*/g, '')));
      } else {
        body.textContent = directive.replace(/\*\*/g, '');
      }

      row.appendChild(body);
      evalNotesContent.appendChild(row);
    }
  }

  function renderEvaluator(ev) {
    if (!ev || !evalStateBadge) return;

    const interval = ev.interval || 100;
    const enabled = ev.enabled !== false;

    if (!isDraggingEvalSlider && sliderEvalInterval) {
      sliderEvalInterval.value = String(Math.min(500, Math.max(10, interval)));
    }
    if (evalIntervalBadge) evalIntervalBadge.textContent = interval + ' ticks';
    if (checkEvalEnabled) checkEvalEnabled.checked = enabled;

    const ticks = ev.transcript_ticks || 0;
    evalTranscriptTicks.textContent = ticks + (ticks === 1 ? ' tick' : ' ticks');

    const last = ev.last_result;
    if (last) {
      evalLastRun.textContent = last.tick_range ? 'ticks ' + last.tick_range : last.ticks_evaluated + ' ticks';
      evalSource.textContent = last.source === 'adk_gemini' ? 'ADK Gemini' : 'Offline Fallback';
    } else {
      evalLastRun.textContent = 'Never';
      evalSource.textContent = '--';
    }

    const nextTick = (ev.last_dispatch_tick || 0) + interval;
    evalNextTick.textContent = enabled ? String(nextTick) : 'Disabled';

    if (ev.is_evaluating) {
      evalStateBadge.className = 'engine-badge eval-running';
      evalStateBadge.textContent = '\u23f3 Evaluating...';
    } else if (!enabled) {
      evalStateBadge.className = 'engine-badge eval-offline';
      evalStateBadge.textContent = '\u23f8 Disabled';
    } else if (last && last.source === 'adk_gemini') {
      evalStateBadge.className = 'engine-badge eval-fresh';
      evalStateBadge.textContent = '\u2728 Advice Published';
    } else if (last) {
      evalStateBadge.className = 'engine-badge eval-offline';
      evalStateBadge.textContent = '\ud83e\udd16 Offline Fallback';
    } else {
      evalStateBadge.className = 'engine-badge';
      evalStateBadge.textContent = 'Idle';
    }

    // Only re-render the notes when they actually change, so the user can scroll
    // and select text without the 500ms poll wiping it out.
    const notes = ev.coaching_notes || '';
    if (notes !== rawEvalNotes) {
      renderEvalNotes(notes);
    }
  }

  function renderLiveState(state) {
    cachedState = state;

    // Header info
    if (state.active_tick !== undefined && state.active_tick !== null && state.active_tick > 0) {
      headerTick.textContent = `#${state.active_tick}`;
    } else {
      headerTick.textContent = '--';
    }
    headerCycles.textContent = state.total_ticks_processed || '0';

    renderEvaluator(state.evaluator);

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

    // Always update Box 5 history
    renderHistory(state.history || []);

    // Update Box 4 Commander Radio
    if (radioStatusBadge) {
      const hasAuth = Boolean(state.has_auth && state.auth_valid);
      if (hasAuth) {
        radioStatusBadge.textContent = '✨ Neural Link Active';
        radioStatusBadge.className = 'engine-badge adk';
      } else {
        radioStatusBadge.textContent = '🤖 Reflex Only';
        radioStatusBadge.className = 'engine-badge fallback';
      }
    }

    if (!isSendingPrompt && state.chat_history) {
      if (state.chat_history.length !== lastRenderedChatLen) {
        renderChatHistory(state.chat_history);
      }
    }
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

  // Event Listeners - Working Memory / N-Tick History Window Slider
  if (sliderHistoryWindow) {
    sliderHistoryWindow.addEventListener('input', () => {
      isDraggingHistorySlider = true;
      const n = parseInt(sliderHistoryWindow.value, 10);
      if (historyWindowBadge) {
        historyWindowBadge.textContent = `${n} ticks`;
      }
      if (tokenEstBadge) {
        tokenEstBadge.textContent = `~${estimateTokens(n)} tokens`;
      }
      if (cachedState && cachedState.history) {
        renderHistory(cachedState.history);
      }
    });

    sliderHistoryWindow.addEventListener('change', async () => {
      const n = parseInt(sliderHistoryWindow.value, 10);
      isDraggingHistorySlider = false;
      if (cachedState) {
        cachedState.history_window_size = n;
      }
      try {
        await api('/api/agent/history-window', 'POST', { history_window_size: n });
      } catch (err) {
        console.error('Failed to update history window size:', err);
      }
      if (cachedState && cachedState.history) {
        renderHistory(cachedState.history);
      }
    });
  }

  // Event Listeners - Commander Radio & Direct Agent Interrogation
  if (chatPromptForm) {
    chatPromptForm.addEventListener('submit', handleSendPrompt);
  }

  if (presetChips && presetChips.length > 0) {
    presetChips.forEach((chip) => {
      chip.addEventListener('click', () => {
        const text = chip.getAttribute('data-prompt');
        if (text && inputUserPrompt) {
          inputUserPrompt.value = text;
          handleSendPrompt();
        }
      });
    });
  }

  if (btnRadioInterrogate) {
    btnRadioInterrogate.addEventListener('click', () => {
      if (cardCommanderRadio) {
        cardCommanderRadio.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      if (inputUserPrompt) inputUserPrompt.focus();
    });
  }

  if (btnClearChat) {
    btnClearChat.addEventListener('click', async () => {
      await api('/api/agent/clear-chat', 'POST');
      renderChatHistory([]);
    });
  }

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
      if (skillsModalTitle) {
        skillsModalTitle.textContent = '\ud83d\udcd6 Cat Agent Skills & Tactical Hunting Manual (actor_skills.md)';
      }
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

  // Event Listeners - Auth Alert Banner
  if (btnCopyAuthCommand) {
    btnCopyAuthCommand.addEventListener('click', async () => {
      const cmd = (authAlertCommand && authAlertCommand.textContent) || 'gcloud auth application-default login';
      try {
        await navigator.clipboard.writeText(cmd.trim());
        flashCopied(btnCopyAuthCommand, '✓ Copied!');
      } catch (e) {
        prompt('Copy command:', cmd);
      }
    });
  }

  if (btnVerifyAuth) {
    btnVerifyAuth.addEventListener('click', async () => {
      btnVerifyAuth.textContent = 'Checking...';
      btnVerifyAuth.disabled = true;
      try {
        const resp = await fetch('/api/agent/auth/verify', { method: 'POST' });
        const data = await resp.json();
        if (data.authenticated) {
          flashCopied(btnVerifyAuth, '✓ Authenticated!');
        } else {
          flashCopied(btnVerifyAuth, '⚠️ Still Expired');
        }
        await pollAgentState();
      } catch (err) {
        flashCopied(btnVerifyAuth, '✕ Check Failed');
      } finally {
        setTimeout(() => {
          btnVerifyAuth.textContent = '🔄 Re-check Auth';
          btnVerifyAuth.disabled = false;
        }, 1500);
      }
    });
  }

  if (btnBannerApiKey) {
    btnBannerApiKey.addEventListener('click', () => {
      if (apiKeyModal) apiKeyModal.style.display = 'flex';
      if (inputApiKey) inputApiKey.focus();
    });
  }

  // Event Listeners - API Key Modal
  if (btnConfigKey && apiKeyModal) {
    btnConfigKey.addEventListener('click', () => {
      apiKeyModal.style.display = 'flex';
      const isAuthValid = Boolean(cachedState && cachedState.auth_valid);
      const isVertex = cachedState && cachedState.auth_mode === 'vertex_ai';
      const proj = (cachedState && cachedState.gcp_project) ? ` (Project: ${cachedState.gcp_project})` : '';
      const err = cachedState && (cachedState.last_engine_error || cachedState.auth_error);

      if (isVertex && isAuthValid) {
        apiKeyStatus.textContent = `✓ Active Vertex AI ADC credentials detected${proj}.`;
        apiKeyStatus.style.color = 'var(--success-color)';
      } else if (isVertex && !isAuthValid) {
        apiKeyStatus.textContent = `🚨 Vertex AI ADC credentials expired: ${err || 'Please run gcloud auth application-default login'}`;
        apiKeyStatus.style.color = 'var(--danger-color)';
      } else if (isAuthValid) {
        apiKeyStatus.textContent = '✓ Active Gemini API Key detected on agent server.';
        apiKeyStatus.style.color = 'var(--success-color)';
      } else {
        apiKeyStatus.textContent = '⚠️ No valid Gemini credentials. Operating on local Heuristic Fallback.';
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
  // Event Listeners - Box 6: Strategic Evaluator
  if (sliderEvalInterval) {
    sliderEvalInterval.addEventListener('input', () => {
      isDraggingEvalSlider = true;
      evalIntervalBadge.textContent = sliderEvalInterval.value + ' ticks';
    });
    sliderEvalInterval.addEventListener('change', async () => {
      await api('/api/agent/evaluator/interval', 'POST', {
        interval: parseInt(sliderEvalInterval.value, 10),
        enabled: checkEvalEnabled ? checkEvalEnabled.checked : true,
      });
      isDraggingEvalSlider = false;
      await pollAgentState();
    });
  }

  if (checkEvalEnabled) {
    checkEvalEnabled.addEventListener('change', async () => {
      await api('/api/agent/evaluator/interval', 'POST', {
        interval: parseInt(sliderEvalInterval.value, 10),
        enabled: checkEvalEnabled.checked,
      });
      await pollAgentState();
    });
  }

  if (btnRunEvalNow) {
    btnRunEvalNow.addEventListener('click', async () => {
      const original = btnRunEvalNow.textContent;
      btnRunEvalNow.disabled = true;
      btnRunEvalNow.textContent = '\u23f3 Evaluating...';
      try {
        await api('/api/agent/evaluator/run', 'POST', {});
        await pollAgentState();
      } finally {
        btnRunEvalNow.disabled = false;
        btnRunEvalNow.textContent = original;
      }
    });
  }

  if (btnCopyEvalNotes) {
    btnCopyEvalNotes.addEventListener('click', () => {
      navigator.clipboard.writeText(rawEvalNotes || '').then(() => {
        flashCopied(btnCopyEvalNotes, '\u2713 Copied!');
      });
    });
  }

  if (btnViewEvalSkills && skillsModal) {
    btnViewEvalSkills.addEventListener('click', async () => {
      skillsModal.style.display = 'flex';
      if (skillsModalTitle) {
        skillsModalTitle.textContent = '\ud83e\udde0 Cat Evaluator \u2014 Long-Horizon Advisory Manual (eval_skills.md)';
      }
      skillsManualContent.textContent = 'Loading evaluator manual from server...';
      const res = await api('/api/agent/evaluator/skills');
      if (res && res.content) {
        skillsManualContent.textContent = res.content;
      } else {
        skillsManualContent.textContent = 'Failed to load evaluator manual from server.';
      }
    });
  }

  pollIntervalId = setInterval(pollAgentState, 500);
  pollAgentState();
})();
