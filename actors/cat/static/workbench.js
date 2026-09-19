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

  // DOM Elements - Thinking Budget Hyperparameter
  const sliderThinkingBudget = document.getElementById('sliderThinkingBudget');
  const thinkingBudgetBadge = document.getElementById('thinkingBudgetBadge');
  const thinkingBudgetMaxLabel = document.getElementById('thinkingBudgetMaxLabel');
  const thinkingSpentBadge = document.getElementById('thinkingSpentBadge');
  const btnThinkingAuto = document.getElementById('btnThinkingAuto');
  const inputDeliberationTimeout = document.getElementById('inputDeliberationTimeout');
  const affordableTokens = document.getElementById('affordableTokens');
  const thinkingWarning = document.getElementById('thinkingWarning');
  let isDraggingThinkingSlider = false;
  // Bounds as reported by the agent. Defaults are only what is shown for the
  // instant before the first poll lands; the server is the authority, because
  // the legal range depends on which model the actor is running.
  let thinkingBounds = { off: 0, auto: -1, min: 1, max: 24576, canDisable: true };
  let thinkingLadder = [0];

  // Steps the slider moves through. Log-scaled rather than linear: the
  // interesting differences are between 0, 512 and 4,096, and a linear slider
  // over 0-24,576 would spend nine tenths of its travel on values that all
  // behave identically.
  const THINKING_LADDER_STEPS = [
    0, 128, 256, 512, 1024, 2048, 4096, 8192, 12288, 16384, 24576,
  ];

  function buildThinkingLadder(bounds) {
    const rungs = THINKING_LADDER_STEPS.filter(
      (v) => v >= bounds.min && v <= bounds.max
    );
    if (bounds.canDisable) rungs.unshift(bounds.off);
    // The model's exact ceiling is always reachable even if it is not a rung,
    // so "as much as it will take" is never an unrepresentable setting.
    if (rungs[rungs.length - 1] !== bounds.max) rungs.push(bounds.max);
    return rungs;
  }

  function nearestIndexIn(ladder, budget) {
    let best = 0;
    let bestGap = Infinity;
    ladder.forEach((v, i) => {
      const gap = Math.abs(v - budget);
      if (gap < bestGap) {
        bestGap = gap;
        best = i;
      }
    });
    return best;
  }

  function nearestLadderIndex(budget) {
    return nearestIndexIn(thinkingLadder, budget);
  }

  // Both agents share the OFF (0) and AUTO (-1) sentinels, so one formatter
  // serves both controls; only the min/max differ, and those never reach here.
  function formatBudget(budget) {
    if (budget === thinkingBounds.auto) return 'auto';
    if (budget === thinkingBounds.off) return 'off';
    return `${budget.toLocaleString()} tokens`;
  }


  // DOM Elements - Modals
  const skillsModal = document.getElementById('skillsModal');
  const promptModal = document.getElementById('promptModal');
  const promptModalTitle = document.getElementById('promptModalTitle');
  const promptModalMeta = document.getElementById('promptModalMeta');
  const promptModalSections = document.getElementById('promptModalSections');
  const btnClosePrompt = document.getElementById('btnClosePrompt');
  const btnClosePromptFooter = document.getElementById('btnClosePromptFooter');
  const btnCopyPrompt = document.getElementById('btnCopyPrompt');
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

    // Synchronize the thinking budget control.
    //
    // Rendered from the polled state rather than from whatever this tab last
    // sent, so a second workbench tab, a curl, or an env-seeded startup value
    // all show up here instead of the control quietly lying about what the
    // actor is doing.
    if (state.thinking) {
      renderThinkingControl(state.thinking);
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

  // Was the cat locked on this tick? A locked tick still produces a full
  // decision, but the engine discards it, so the row must not read as a move.
  //
  // `tick_type` is the recorded answer. The `can_act` fallback covers rows
  // captured before that field existed; note it is checked ahead of
  // `action_cooldown` because the server decrements the counter before it
  // builds the payload, leaving the final locked tick reading zero.
  function isLockedTick(item) {
    if (!item) return false;
    if (item.tick_type) return item.tick_type === 'cooldown';
    if (typeof item.locked === 'boolean') return item.locked;
    const self = (item.observation && item.observation.self) || {};
    if (typeof self.can_act === 'boolean') return !self.can_act;
    return (self.action_cooldown || 0) > 0;
  }

  // Remaining lock ticks, for the tooltip. May legitimately be 0 on the last
  // locked tick.
  function lockDetail(item) {
    const self = (item && item.observation && item.observation.self) || {};
    const recovering = item.locked_action || self.locked_action;
    const remaining = (typeof item.action_cooldown === 'number')
      ? item.action_cooldown
      : (self.action_cooldown || 0);
    const from = recovering ? `recovering from '${recovering}'` : 'recovering';
    return remaining > 0
      ? `Cat was LOCKED this tick (${from}; free in ${remaining} more tick(s)). `
        + 'The engine discarded this action -- it was never executed.'
      : `Cat was LOCKED this tick (${from}; final tick of the lock). `
        + 'The engine discarded this action -- it was never executed.';
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
    const lockedCount = history ? history.filter(isLockedTick).length : 0;
    const playedCount = (history ? history.length : 0) - lockedCount;
    // Splitting the count matters: at the cat's default cooldowns only about a
    // third of ticks are playable, so a raw tick total overstates how much the
    // cat actually got to do.
    historyCountPill.textContent =
      `${(history && history.length) || 0} ticks (${inWindowCount} in LLM context) `
      + `· ${playedCount} played / ${lockedCount} locked`;

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

      // Locked ticks are observation-only: the cat reasoned, but the engine was
      // still counting down and threw the action away. They used to render
      // identically to real moves, which made a normal 1-move-per-3-ticks cadence
      // look like the cat spamming the same command.
      const locked = isLockedTick(item);
      const lockTitle = locked ? lockDetail(item) : '';
      if (locked) {
        tr.classList.add('cooldown-tick');
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

      // The engine label above answers "who decided this". This one answers
      // "did it count". Both are shown because a locked tick is still a real
      // Gemini call, and hiding that would misreport model usage.
      const lifecycleBadge = document.createElement('span');
      lifecycleBadge.className = locked
        ? 'history-lifecycle-badge locked'
        : 'history-lifecycle-badge played';
      lifecycleBadge.textContent = locked ? '🔒 LOCKED' : '▶ PLAY';
      lifecycleBadge.title = locked
        ? lockTitle
        : 'Cat was free this tick; the engine accepted this action.';
      tdSource.appendChild(lifecycleBadge);
      tr.appendChild(tdSource);

      // 4. Action
      const tdAction = document.createElement('td');
      const actionBadge = document.createElement('span');
      const act = (item.response && item.response.action) ? item.response.action : 'stay';
      actionBadge.className = locked
        ? `history-action-badge ${act} discarded`
        : `history-action-badge ${act}`;
      actionBadge.textContent = act.toUpperCase();
      actionBadge.title = locked
        ? lockTitle
        : 'Executed by the engine on this tick.';
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

      // Only Gemini ticks have a payload. The reflex never calls the model, so
      // offering the button there would promise something that cannot exist.
      if (item.source === 'adk_gemini') {
        const btnPayload = document.createElement('button');
        btnPayload.className = 'btn btn-sm btn-payload';
        btnPayload.textContent = 'Payload';
        btnPayload.title = 'Show the exact prompt Gemini received for this decision';
        btnPayload.addEventListener('click', () => openPromptModal(item.tick));
        tdInspect.appendChild(btnPayload);
      }

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

  // ---- Box 7: General Radio ----------------------------------------------
  // The General's channel to the Coach. Structurally a twin of Box 4 above,
  // but every endpoint, every DOM node and every piece of state is separate.
  // The one thing deliberately NOT shared is the steer checkbox default: the
  // actor's is checked, because an order to the cat is cheap and expires with
  // the situation. This one is unchecked, because an order to the coach is
  // injected into every future evaluation until it is explicitly rescinded,
  // and adopting one by accident would quietly rewrite the cat's whole dossier.
  const generalChatMessages = document.getElementById('generalChatMessages');
  const generalPromptForm = document.getElementById('generalPromptForm');
  const inputGeneralPrompt = document.getElementById('inputGeneralPrompt');
  const btnSendGeneralPrompt = document.getElementById('btnSendGeneralPrompt');
  const sendGeneralIcon = document.getElementById('sendGeneralIcon');
  const sendGeneralText = document.getElementById('sendGeneralText');
  const checkSteerCoach = document.getElementById('checkSteerCoach');
  const generalTypingIndicator = document.getElementById('generalTypingIndicator');
  const generalStandingOrder = document.getElementById('generalStandingOrder');
  const generalStandingOrderText = document.getElementById('generalStandingOrderText');
  const btnClearGeneralChat = document.getElementById('btnClearGeneralChat');
  const btnClearGeneralDirective = document.getElementById('btnClearGeneralDirective');
  const generalPresetChips = document.querySelectorAll('.general-preset');
  let isSendingGeneralPrompt = false;

  function renderGeneralStandingOrder(payload) {
    if (!generalStandingOrder || !generalStandingOrderText) return;
    const active = payload && payload.active && payload.directive;
    if (!active) {
      generalStandingOrder.classList.add('is-empty');
      generalStandingOrderText.textContent =
        'No standing order. The coach is writing its dossier unconstrained.';
      return;
    }
    generalStandingOrder.classList.remove('is-empty');
    const tick = payload.directive.tick;
    const when = (tick !== undefined && tick !== null) ? ` (issued tick #${tick})` : '';
    generalStandingOrderText.textContent =
      `STANDING ORDER${when}: ${payload.directive.directive}`;
  }

  async function refreshGeneralDirective() {
    try {
      const res = await api('/api/agent/general/directive');
      renderGeneralStandingOrder(res);
    } catch (err) {
      // Non-fatal: the banner is informational, the channel still works.
      console.error('Failed to read coach standing order:', err);
    }
  }

  function renderGeneralChat(chatList) {
    if (!generalChatMessages) return;

    if (!chatList || chatList.length === 0) {
      const bubble = document.createElement('div');
      bubble.className = 'chat-bubble general-bubble';
      const hdr = document.createElement('div');
      hdr.className = 'chat-bubble-header';
      const sender = document.createElement('span');
      sender.className = 'chat-sender';
      sender.textContent = '🎓 Coach [cat_evaluator]';
      const meta = document.createElement('span');
      meta.className = 'chat-meta';
      meta.textContent = (cachedState && cachedState.active_tick) ? `Tick #${cachedState.active_tick}` : 'Standby';
      hdr.appendChild(sender);
      hdr.appendChild(meta);
      const body = document.createElement('div');
      body.className = 'chat-bubble-body';
      body.textContent = 'General, the Coach is on the channel. Ask me why any directive is in the Cat\u2019s dossier, what evidence I based it on, or whether I am coaching toward the right thing. I can pull the live box score if you want numbers.';
      bubble.appendChild(hdr);
      bubble.appendChild(body);
      generalChatMessages.replaceChildren(bubble);
      return;
    }

    const bubbles = chatList.map((msg) => {
      const bubble = document.createElement('div');
      // Keyed on `source`, not `sender`. On this channel the human IS the
      // General, so a `sender` of 'general' is the operator -- but an older
      // log written before the roles were corrected used 'general' for the
      // replying agent. `source === 'user'` has always meant the operator
      // and never meant anything else, so it survives that rename.
      const isUser = msg.source === 'user';
      bubble.className = isUser ? 'chat-bubble user-bubble' : 'chat-bubble general-bubble';

      const hdr = document.createElement('div');
      hdr.className = 'chat-bubble-header';
      const sender = document.createElement('span');
      sender.className = 'chat-sender';
      sender.textContent = isUser ? '🎖️ General' : '🎓 Coach [cat_evaluator]';
      const meta = document.createElement('span');
      meta.className = 'chat-meta';
      const tickText = (msg.tick !== undefined && msg.tick !== null && msg.tick > 0) ? `Tick #${msg.tick}` : '';
      // The offline path is surfaced rather than hidden. A reply that came from
      // the fallback contains no reasoning about the dossier at all, and reading
      // it as the coach's actual position would be a real misunderstanding.
      const engineText = (!isUser && msg.source === 'offline_fallback') ? '⚠️ offline' : '';
      meta.textContent = [tickText, msg.timestamp || '', engineText].filter(Boolean).join(' • ');
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
        badge.textContent = '📌 Adopted as Standing Order';
        bubble.appendChild(badge);
      }
      return bubble;
    });

    generalChatMessages.replaceChildren(...bubbles);
    generalChatMessages.scrollTop = generalChatMessages.scrollHeight;
  }

  async function refreshGeneralChat() {
    try {
      const res = await api('/api/agent/general/chat-history');
      renderGeneralChat(res);
    } catch (err) {
      console.error('Failed to read general radio log:', err);
    }
  }

  async function handleSendGeneralPrompt(e) {
    if (e) e.preventDefault();
    if (isSendingGeneralPrompt) return;
    const promptText = (inputGeneralPrompt.value || '').trim();
    if (!promptText) {
      inputGeneralPrompt.focus();
      return;
    }

    const steer = checkSteerCoach ? checkSteerCoach.checked : false;
    isSendingGeneralPrompt = true;
    btnSendGeneralPrompt.disabled = true;
    inputGeneralPrompt.disabled = true;
    if (generalTypingIndicator) generalTypingIndicator.style.display = 'inline-flex';
    if (sendGeneralIcon) sendGeneralIcon.textContent = '⏳';
    if (sendGeneralText) sendGeneralText.textContent = 'On the air...';

    // Optimistic local bubble so the channel feels live. The coach can take a
    // while: it may call get_boxscore before it answers.
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
    generalChatMessages.appendChild(userBubble);
    generalChatMessages.scrollTop = generalChatMessages.scrollHeight;

    try {
      // Raw fetch rather than api(): the shared helper discards the response
      // body on a non-2xx, and the coach's error detail is the whole point of
      // this channel.
      const resp = await fetch('/api/agent/general/prompt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: promptText, steer_directive: steer }),
      });
      const payload = await resp.json().catch(() => null);
      if (!resp.ok) {
        throw new Error((payload && (payload.detail || payload.error)) || `HTTP ${resp.status}`);
      }
      inputGeneralPrompt.value = '';
      if (checkSteerCoach) checkSteerCoach.checked = false;
      await refreshGeneralChat();
      await refreshGeneralDirective();
    } catch (err) {
      console.error('Failed to reach the General:', err);
      const errBubble = document.createElement('div');
      errBubble.className = 'chat-bubble general-bubble';
      const errBody = document.createElement('div');
      errBody.className = 'chat-bubble-body';
      errBody.style.color = 'var(--danger-color)';
      errBody.textContent = `⚠️ Radio failure: ${err.message || err}`;
      errBubble.appendChild(errBody);
      generalChatMessages.appendChild(errBubble);
      generalChatMessages.scrollTop = generalChatMessages.scrollHeight;
    } finally {
      isSendingGeneralPrompt = false;
      btnSendGeneralPrompt.disabled = false;
      inputGeneralPrompt.disabled = false;
      if (generalTypingIndicator) generalTypingIndicator.style.display = 'none';
      if (sendGeneralIcon) sendGeneralIcon.textContent = '📻';
      if (sendGeneralText) sendGeneralText.textContent = 'Send to General';
      inputGeneralPrompt.focus();
    }
  }

  // Inspect a specific past tick
  // ---- Gemini payload inspector -----------------------------------------
  // Answers "what exactly did the model see when it decided this tick?".
  // Sections are kept separate rather than shown as one blob so it is obvious
  // which source contributed what -- and, just as importantly, which source
  // contributed nothing.
  let lastPromptRecord = null;

  function openPromptModal(tick) {
    if (!promptModal) return;
    promptModalTitle.textContent = `🔬 Gemini Payload — decision applied at tick #${tick}`;
    promptModalMeta.textContent = 'Loading…';
    promptModalSections.replaceChildren();
    promptModal.style.display = 'flex';

    fetch(`/api/agent/prompt/${tick}`)
      .then((r) => r.json())
      .then((data) => {
        if (data.status !== 'ok' || !data.record) {
          lastPromptRecord = null;
          promptModalMeta.textContent = data.detail || 'No prompt recorded for this tick.';
          return;
        }
        lastPromptRecord = data.record;
        renderPromptRecord(data.record);
        // Updated here rather than inside the renderer: the badge sits outside
        // the modal, on the thinking-budget control, and a renderer that only
        // touches the modal it was asked to fill is the easier one to trust.
        renderThinkingSpend(data.record);
      })
      .catch((err) => {
        lastPromptRecord = null;
        promptModalMeta.textContent = `Failed to load prompt: ${err}`;
      });
  }

  function renderPromptRecord(rec) {
    const built = rec.built_at_tick;
    const applied = rec.applied_at_tick;
    const lagNote = (built !== applied)
      // Worth calling out explicitly: the model reasoned about an older world
      // than the one its decision was applied to.
      ? ` — built from tick ${built}'s observation, applied ${applied - built} tick(s) later`
      : ' — built and applied on the same tick';

    promptModalMeta.replaceChildren();
    const meta = document.createElement('div');
    const line1 = document.createElement('div');
    line1.textContent = `${rec.model} · ${rec.latency_ms ?? '?'}ms${lagNote}`;
    meta.appendChild(line1);

    // What this particular decision was allowed to think, and what it actually
    // spent. Per-record rather than read off the live control, because the
    // budget is adjustable mid-game and the control shows only what is set
    // now -- which may not be what produced the tick being inspected.
    if (rec.thinking_budget !== undefined && rec.thinking_budget !== null) {
      const spent = rec.thoughts_token_count;
      const spentText = (spent === null || spent === undefined)
        ? 'not reported by the API'
        : `${spent.toLocaleString()} spent`;
      const line = document.createElement('div');
      line.textContent = `Thinking budget: ${formatBudget(rec.thinking_budget)} · ${spentText}`;
      meta.appendChild(line);
    }

    if (rec.decision) {
      const line2 = document.createElement('div');
      line2.textContent = `Returned: ${(rec.decision.action || '').toUpperCase()} — ${rec.decision.thought || ''}`;
      meta.appendChild(line2);
    }
    promptModalMeta.appendChild(meta);

    const order = [
      'system_instruction',
      'coaching_notes',
      'commander_directive',
      'history_window',
      'current_observation',
    ];
    const sections = rec.sections || {};
    const blocks = [];

    order.forEach((key) => {
      const sec = sections[key];
      if (!sec) return;

      const details = document.createElement('details');
      details.className = sec.delivered ? 'prompt-section' : 'prompt-section not-delivered';
      // The skills manual is ~7KB and identical every tick; collapsing it keeps
      // the parts that actually vary in view.
      details.open = sec.delivered && key !== 'system_instruction';

      const summary = document.createElement('summary');
      const status = sec.delivered ? '✓' : '✗ NOT SENT';
      summary.textContent = `${status}  ${sec.label}  (${sec.text.length} chars)`;
      details.appendChild(summary);

      const pre = document.createElement('pre');
      pre.className = 'prompt-section-body';
      pre.textContent = sec.text;
      details.appendChild(pre);

      blocks.push(details);
    });

    promptModalSections.replaceChildren(...blocks);
  }

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

  // Box 6: the coach's own thinking budget. Mirrors the actor's control in Box
  // 2 but keeps its own bounds and ladder -- the two agents may not be on the
  // same model, and their abandon ceilings differ by an order of magnitude.
  const sliderEvalThinking = document.getElementById('sliderEvalThinking');
  const evalThinkingBadge = document.getElementById('evalThinkingBadge');
  const evalThinkingMaxLabel = document.getElementById('evalThinkingMaxLabel');
  const evalThinkingSpentBadge = document.getElementById('evalThinkingSpentBadge');
  const btnEvalThinkingAuto = document.getElementById('btnEvalThinkingAuto');
  const inputEvalTimeout = document.getElementById('inputEvalTimeout');
  const evalAffordableTokens = document.getElementById('evalAffordableTokens');
  const evalThinkingWarning = document.getElementById('evalThinkingWarning');
  let isDraggingEvalThinkingSlider = false;
  let evalThinkingBounds = { off: 0, auto: -1, min: 1, max: 24576, canDisable: true };
  let evalThinkingLadder = [0];
  let cachedEvalThinking = null;

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

    // Driven from the poll rather than from whatever this tab last sent, so a
    // change made from another tab or from curl shows up here.
    if (ev.thinking) {
      renderEvalThinkingControl(ev.thinking);
    }
  }

  // ---- Coach thinking budget ----------------------------------------------

  function renderEvalThinkingControl(thinking) {
    cachedEvalThinking = thinking;
    evalThinkingBounds = {
      off: thinking.off,
      auto: thinking.auto,
      min: thinking.min,
      max: thinking.max,
      canDisable: thinking.can_disable,
    };
    evalThinkingLadder = buildThinkingLadder(evalThinkingBounds);

    if (sliderEvalThinking) {
      sliderEvalThinking.max = String(evalThinkingLadder.length - 1);
    }
    if (evalThinkingMaxLabel) {
      evalThinkingMaxLabel.textContent = evalThinkingBounds.max.toLocaleString();
    }

    const budget = thinking.budget;
    const isAuto = budget === evalThinkingBounds.auto;

    if (btnEvalThinkingAuto) {
      btnEvalThinkingAuto.classList.toggle('active', isAuto);
    }
    // AUTO is a mode, not a quantity, so the slider is disabled rather than
    // left parked on a number that is not in force.
    if (sliderEvalThinking) {
      sliderEvalThinking.disabled = isAuto;
      if (!isDraggingEvalThinkingSlider && !isAuto) {
        sliderEvalThinking.value = String(nearestIndexIn(evalThinkingLadder, budget));
      }
    }
    if (evalThinkingBadge) {
      evalThinkingBadge.textContent = formatBudget(budget);
    }
    if (inputEvalTimeout && document.activeElement !== inputEvalTimeout) {
      inputEvalTimeout.value = String(thinking.timeout_sec);
      inputEvalTimeout.min = String(thinking.timeout_min_sec);
      inputEvalTimeout.max = String(thinking.timeout_max_sec);
    }
    if (evalAffordableTokens) {
      evalAffordableTokens.textContent = (thinking.affordable_tokens || 0).toLocaleString();
    }
    if (evalThinkingWarning) {
      if (thinking.warning) {
        evalThinkingWarning.textContent = `⚠️ ${thinking.warning}`;
        evalThinkingWarning.style.display = 'block';
      } else {
        evalThinkingWarning.style.display = 'none';
      }
    }

    renderEvalThinkingSpend(thinking.last_run);
  }

  function renderEvalThinkingSpend(lastRun) {
    // What the last evaluation actually spent. The coach runs once per ~100
    // ticks, so unlike the actor there is no per-decision log to read this from
    // after the fact -- if it is not shown here it is not observable at all.
    if (!evalThinkingSpentBadge) return;
    if (!lastRun || lastRun.latency_ms === null || lastRun.latency_ms === undefined) {
      evalThinkingSpentBadge.textContent = 'last: —';
      return;
    }
    if (lastRun.timed_out) {
      // Worth calling out rather than folding into the token figure: a timeout
      // means the published standing orders came from offline filler.
      evalThinkingSpentBadge.textContent = 'last: ABANDONED (timed out)';
      return;
    }
    const spent = lastRun.thoughts_token_count;
    const secs = (lastRun.latency_ms / 1000).toFixed(1);
    let text = spent === null || spent === undefined
      ? `last: ${secs}s · thinking not reported`
      : `last: ${secs}s · ${spent.toLocaleString()} thought tokens`;

    if (Array.isArray(lastRun.tool_calls) && lastRun.tool_calls.length > 0) {
      const callsStr = lastRun.tool_calls.map(tc => tc.ok ? tc.name : `${tc.name}(err)`).join(', ');
      text += ` · tools: ${callsStr}`;
    }
    evalThinkingSpentBadge.textContent = text;
  }

  // FastAPI reports a schema violation as a 422 whose `detail` is an array of
  // objects, not a string. Interpolating that directly yields "[object Object]",
  // which is how a real bug here stayed invisible: the slider was posting an
  // undefined budget, the server was rejecting it correctly, and the UI was
  // showing a message that told nobody anything.
  function describeApiError(data, res) {
    const detail = data && data.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      const parts = detail
        .map((d) => {
          const where = Array.isArray(d.loc) ? d.loc.filter((p) => p !== 'body').join('.') : '';
          return where ? `${where}: ${d.msg}` : d.msg;
        })
        .filter(Boolean);
      if (parts.length) return parts.join('; ');
    }
    return `HTTP ${res.status}`;
  }

  // A status payload always carries a budget. An error payload may not, and
  // feeding one to the renderer would hand `buildThinkingLadder` undefined
  // bounds -- replacing a visible failure with a corrupted control.
  function isThinkingStatus(data) {
    return data && typeof data.budget === 'number' && typeof data.max === 'number';
  }

  async function applyEvalThinkingSettings(budget, timeoutSec) {
    // Guards against posting a budget the ladder could not supply. Reaching
    // here with undefined means the control never received its bounds, and the
    // useful thing to do is say so rather than let the request 422.
    if (typeof budget !== 'number' || !Number.isFinite(budget)) {
      if (evalThinkingWarning) {
        evalThinkingWarning.textContent =
          '⛔ Coach thinking control has not loaded its limits yet — waiting for the next poll.';
        evalThinkingWarning.style.display = 'block';
      }
      return;
    }
    // Raw fetch rather than `api()`, which discards the body on a non-2xx. A
    // 400 here carries the reason the value was refused, which is the single
    // most useful thing to show someone who just moved the dial.
    try {
      const res = await fetch('/api/agent/evaluator/thinking-budget', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          thinking_budget: budget,
          eval_timeout_sec: timeoutSec,
        }),
      });
      const data = await res.json();
      if (!res.ok && evalThinkingWarning) {
        evalThinkingWarning.textContent = `⛔ ${describeApiError(data, res)}`;
        evalThinkingWarning.style.display = 'block';
      }
      // Rendered on both paths: on a rejection the payload still carries the
      // settings actually in force, so the control snaps back to the truth.
      if (isThinkingStatus(data)) {
        renderEvalThinkingControl(data);
      }
    } catch (err) {
      console.error('Failed to update evaluator thinking budget:', err);
    }
  }

  function currentEvalTimeoutInput() {
    const parsed = parseFloat(inputEvalTimeout && inputEvalTimeout.value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function currentEvalBudget() {
    return cachedEvalThinking ? cachedEvalThinking.budget : evalThinkingBounds.off;
  }

  if (sliderEvalThinking) {
    sliderEvalThinking.addEventListener('input', () => {
      isDraggingEvalThinkingSlider = true;
      const budget = evalThinkingLadder[parseInt(sliderEvalThinking.value, 10)];
      if (evalThinkingBadge) {
        evalThinkingBadge.textContent = formatBudget(budget);
      }
    });

    sliderEvalThinking.addEventListener('change', async () => {
      const budget = evalThinkingLadder[parseInt(sliderEvalThinking.value, 10)];
      isDraggingEvalThinkingSlider = false;
      await applyEvalThinkingSettings(budget, currentEvalTimeoutInput());
    });
  }

  if (btnEvalThinkingAuto) {
    btnEvalThinkingAuto.addEventListener('click', async () => {
      // A toggle, not a one-way door: clicking Auto while it is already on
      // returns to whichever rung the slider is parked on.
      const goingAuto = currentEvalBudget() !== evalThinkingBounds.auto;
      const budget = goingAuto
        ? evalThinkingBounds.auto
        : evalThinkingLadder[parseInt(sliderEvalThinking.value, 10)];
      await applyEvalThinkingSettings(budget, currentEvalTimeoutInput());
    });
  }

  if (inputEvalTimeout) {
    inputEvalTimeout.addEventListener('change', async () => {
      await applyEvalThinkingSettings(currentEvalBudget(), currentEvalTimeoutInput());
    });
  }

  // DOM Elements - Box 7: Deliberation Timing
  const delibStateBadge = document.getElementById('delibStateBadge');
  const delibAnswered = document.getElementById('delibAnswered');
  const delibAbandoned = document.getElementById('delibAbandoned');
  const delibSuperseded = document.getElementById('delibSuperseded');
  const delibErrors = document.getElementById('delibErrors');
  const delibAvgMs = document.getElementById('delibAvgMs');
  const delibMaxMs = document.getElementById('delibMaxMs');
  const delibSamples = document.getElementById('delibSamples');
  const delibRecentList = document.getElementById('delibRecentList');

  // Signature of the recent window currently on screen, so an unchanged poll
  // does not rebuild the list. Mirrors how the evaluator notes are handled.
  let lastDelibSignature = '';

  // How each recorded outcome is presented. Only 'answered' reached the board;
  // every other outcome is a tick the built-in heuristic played instead, so they
  // are all styled as misses rather than as neutral bookkeeping.
  // `cls` -> modifier for `.delib-outcome-badge`
  function describeOutcome(outcome) {
    switch (outcome) {
      case 'answered':
        return { label: '\u2713 ANSWERED', cls: 'answered', miss: false,
                 title: 'The model answered in time and its action was played' };
      case 'abandoned':
        return { label: '\u2715 ABANDONED', cls: 'abandoned', miss: true,
                 title: 'The model blew its time budget; the heuristic played this turn' };
      case 'superseded':
        return { label: '\u2715 SUPERSEDED', cls: 'superseded', miss: true,
                 title: 'A newer observation arrived while the model was still thinking, '
                   + 'so this answer could no longer be used; the heuristic played this turn' };
      case 'error':
        return { label: '\u2715 ERROR', cls: 'error', miss: true,
                 title: 'The deliberation raised an error; the heuristic played this turn' };
      case 'send_failed':
        return { label: '\u2715 SEND FAILED', cls: 'error', miss: true,
                 title: 'The answer was produced but could not be sent; the heuristic played this turn' };
      default:
        return { label: outcome ? String(outcome).toUpperCase() : 'UNKNOWN', cls: 'error', miss: true,
                 title: 'Unrecognised deliberation outcome' };
    }
  }

  // Latency is null until something has actually been answered. A zero there
  // would read as an instant model rather than as an absent one.
  function fmtMs(val) {
    if (typeof val !== 'number' || isNaN(val)) return '--';
    return `${Math.round(val)} ms`;
  }

  function fmtClockTime(iso) {
    if (!iso) return '--';
    const when = new Date(iso);
    if (isNaN(when.getTime())) return '--';
    return when.toLocaleTimeString();
  }

  // Render Box 7: Deliberation Timing (Zero innerHTML)
  function renderDeliberation(delib) {
    if (!delibRecentList) return;

    const d = delib || {};
    const samples = typeof d.samples === 'number' ? d.samples : 0;
    const answered = typeof d.answered === 'number' ? d.answered : 0;
    const abandoned = typeof d.abandoned === 'number' ? d.abandoned : 0;
    const superseded = typeof d.superseded === 'number' ? d.superseded : 0;
    const errors = typeof d.errors === 'number' ? d.errors : 0;

    if (delibAnswered) delibAnswered.textContent = String(answered);
    if (delibAbandoned) delibAbandoned.textContent = String(abandoned);
    if (delibSuperseded) delibSuperseded.textContent = String(superseded);
    if (delibErrors) delibErrors.textContent = String(errors);
    if (delibAvgMs) delibAvgMs.textContent = fmtMs(d.avg_answered_ms);
    if (delibMaxMs) delibMaxMs.textContent = fmtMs(d.max_answered_ms);
    if (delibSamples) {
      delibSamples.textContent = `${samples} sample${samples === 1 ? '' : 's'}`;
    }

    // Headline: what share of the window the model actually drove. Stated as a
    // miss count as well, because "80% answered" and "1 in 5 turns played by the
    // heuristic" are the same fact and only the second one reads as a problem.
    if (delibStateBadge) {
      if (samples === 0) {
        delibStateBadge.className = 'engine-badge';
        delibStateBadge.textContent = 'No samples';
        delibStateBadge.title = 'No deliberations recorded in the recent window yet';
      } else {
        const misses = samples - answered;
        const pct = Math.round((answered / samples) * 100);
        delibStateBadge.textContent = `${pct}% reached the board`;
        delibStateBadge.title = misses === 0
          ? `All ${samples} recent deliberations were played`
          : `${misses} of ${samples} recent deliberations missed; the heuristic played those turns`;
        if (misses === 0) {
          delibStateBadge.className = 'engine-badge adk';
        } else if (pct >= 50) {
          delibStateBadge.className = 'engine-badge fallback';
        } else {
          delibStateBadge.className = 'engine-badge error';
        }
      }
    }

    const recent = Array.isArray(d.recent) ? d.recent : [];
    if (recent.length === 0) {
      if (lastDelibSignature !== '') {
        lastDelibSignature = '';
        delibRecentList.classList.add('delib-recent-empty');
        delibRecentList.textContent =
          'No deliberations recorded yet. Connect to the Game Server and advance ticks.';
      }
      return;
    }

    // Only rebuild when the window actually changed, so the 500ms poll cannot
    // wipe a scroll position or a text selection out from under the user.
    const signature = recent
      .map((e) => (e ? `${e.tick}|${e.outcome}|${e.elapsed_ms}|${e.at}` : '-'))
      .join('\n');
    if (signature === lastDelibSignature) return;
    lastDelibSignature = signature;

    delibRecentList.classList.remove('delib-recent-empty');

    // Newest first, to match the trajectory history table.
    const rows = [];
    for (let i = recent.length - 1; i >= 0; i--) {
      const entry = recent[i] || {};
      const desc = describeOutcome(entry.outcome);

      const row = document.createElement('div');
      row.className = desc.miss ? 'delib-row delib-row-miss' : 'delib-row';
      row.title = desc.title;

      const tick = document.createElement('span');
      tick.className = 'delib-row-tick';
      tick.textContent = (entry.tick === undefined || entry.tick === null)
        ? '#--'
        : `#${entry.tick}`;
      row.appendChild(tick);

      const badge = document.createElement('span');
      badge.className = `delib-outcome-badge ${desc.cls}`;
      badge.textContent = desc.label;
      row.appendChild(badge);

      const elapsed = document.createElement('span');
      elapsed.className = 'delib-row-elapsed';
      elapsed.textContent = fmtMs(entry.elapsed_ms);
      row.appendChild(elapsed);

      const at = document.createElement('span');
      at.className = 'delib-row-at';
      at.textContent = fmtClockTime(entry.at);
      row.appendChild(at);

      rows.push(row);
    }

    delibRecentList.replaceChildren(...rows);
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

    // Deliberation timing rides along on the connection status payload.
    renderDeliberation(conn.deliberation);

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

  // ---- Thinking budget ----------------------------------------------------
  // How much private scratchpad the actor gets before it has to commit to an
  // action, paired with the ceiling that decides whether a thought-out answer
  // arrives in time to be used at all.

  function renderThinkingControl(thinking) {
    thinkingBounds = {
      off: thinking.off,
      auto: thinking.auto,
      min: thinking.min,
      max: thinking.max,
      canDisable: thinking.can_disable,
    };
    thinkingLadder = buildThinkingLadder(thinkingBounds);

    if (sliderThinkingBudget) {
      sliderThinkingBudget.max = String(thinkingLadder.length - 1);
    }
    if (thinkingBudgetMaxLabel) {
      thinkingBudgetMaxLabel.textContent = thinkingBounds.max.toLocaleString();
    }

    const budget = thinking.budget;
    const isAuto = budget === thinkingBounds.auto;

    if (btnThinkingAuto) {
      btnThinkingAuto.classList.toggle('active', isAuto);
    }
    // The slider cannot express AUTO -- it is a mode, not a quantity -- so it
    // is greyed out rather than left parked on a number that is not in force.
    if (sliderThinkingBudget) {
      sliderThinkingBudget.disabled = isAuto;
      if (!isDraggingThinkingSlider && !isAuto) {
        sliderThinkingBudget.value = String(nearestLadderIndex(budget));
      }
    }
    if (thinkingBudgetBadge) {
      thinkingBudgetBadge.textContent = formatBudget(budget);
    }
    if (inputDeliberationTimeout && document.activeElement !== inputDeliberationTimeout) {
      inputDeliberationTimeout.value = String(thinking.deliberation_timeout_sec);
      inputDeliberationTimeout.min = String(thinking.deliberation_timeout_min_sec);
      inputDeliberationTimeout.max = String(thinking.deliberation_timeout_max_sec);
    }
    if (affordableTokens) {
      affordableTokens.textContent = (thinking.affordable_tokens || 0).toLocaleString();
    }
    if (thinkingWarning) {
      if (thinking.warning) {
        thinkingWarning.textContent = `⚠️ ${thinking.warning}`;
        thinkingWarning.style.display = 'block';
      } else {
        thinkingWarning.style.display = 'none';
      }
    }
  }

  function renderThinkingSpend(record) {
    // What the last inspected Gemini decision actually spent. A budget is a
    // ceiling, not a spend, and at AUTO the two are rarely the same number, so
    // the dial alone cannot tell you whether the cat is thinking.
    if (!thinkingSpentBadge) return;
    if (!record) {
      thinkingSpentBadge.textContent = 'last: —';
      return;
    }
    const spent = record.thoughts_token_count;
    if (spent === null || spent === undefined) {
      // Not the same as zero: the response carried no usage metadata. Saying
      // "0 thought tokens" here would be inventing a measurement.
      thinkingSpentBadge.textContent = 'last: not reported';
      return;
    }
    thinkingSpentBadge.textContent = `last: ${spent.toLocaleString()} thought tokens`;
  }

  async function applyThinkingSettings(budget, timeoutSec) {
    // Deliberately not routed through `api()`: that helper throws away the
    // response body on a non-2xx, and a 400 from this endpoint carries the
    // explanation of why the value was refused -- the single most useful thing
    // to put in front of someone who just moved the dial.
    try {
      const res = await fetch('/api/agent/thinking-budget', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          thinking_budget: budget,
          deliberation_timeout_sec: timeoutSec,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        if (thinkingWarning) {
          thinkingWarning.textContent = `⛔ ${describeApiError(data, res)}`;
          thinkingWarning.style.display = 'block';
        }
      }
      // Rendered on both paths. On a rejection the payload still carries the
      // settings actually in force, so the control snaps back to the truth
      // instead of sitting on a value the agent refused. Guarded because a 422
      // body is a validation report, not a status -- rendering that would give
      // the ladder undefined bounds and break the control outright.
      if (isThinkingStatus(data)) {
        renderThinkingControl(data);
        if (cachedState) cachedState.thinking = data;
      }
    } catch (err) {
      console.error('Failed to update thinking budget:', err);
    }
  }

  function currentTimeoutInput() {
    const parsed = parseFloat(inputDeliberationTimeout && inputDeliberationTimeout.value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function currentBudget() {
    const thinking = cachedState && cachedState.thinking;
    return thinking ? thinking.budget : thinkingBounds.off;
  }

  if (sliderThinkingBudget) {
    sliderThinkingBudget.addEventListener('input', () => {
      isDraggingThinkingSlider = true;
      const budget = thinkingLadder[parseInt(sliderThinkingBudget.value, 10)];
      if (thinkingBudgetBadge) {
        thinkingBudgetBadge.textContent = formatBudget(budget);
      }
    });

    sliderThinkingBudget.addEventListener('change', async () => {
      const budget = thinkingLadder[parseInt(sliderThinkingBudget.value, 10)];
      isDraggingThinkingSlider = false;
      await applyThinkingSettings(budget, currentTimeoutInput());
    });
  }

  if (btnThinkingAuto) {
    btnThinkingAuto.addEventListener('click', async () => {
      // A toggle, not a one-way door: clicking Auto while it is already on
      // returns to whichever rung the slider is parked on.
      const goingAuto = currentBudget() !== thinkingBounds.auto;
      const budget = goingAuto
        ? thinkingBounds.auto
        : thinkingLadder[parseInt(sliderThinkingBudget.value, 10)];
      await applyThinkingSettings(budget, currentTimeoutInput());
    });
  }

  if (inputDeliberationTimeout) {
    inputDeliberationTimeout.addEventListener('change', async () => {
      await applyThinkingSettings(currentBudget(), currentTimeoutInput());
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

  // Event Listeners - Box 7: General Radio
  if (generalPromptForm) {
    generalPromptForm.addEventListener('submit', handleSendGeneralPrompt);
  }

  if (generalPresetChips && generalPresetChips.length > 0) {
    generalPresetChips.forEach((chip) => {
      chip.addEventListener('click', () => {
        const text = chip.getAttribute('data-prompt');
        if (text && inputGeneralPrompt) {
          inputGeneralPrompt.value = text;
          // Sends immediately, like the actor's presets. Safe to do here only
          // because the steer checkbox defaults to OFF -- a one-click preset
          // that silently rewrote the coach's standing order would be a trap.
          handleSendGeneralPrompt();
        }
      });
    });
  }

  if (btnClearGeneralChat) {
    btnClearGeneralChat.addEventListener('click', async () => {
      await api('/api/agent/general/clear-chat', 'POST');
      renderGeneralChat([]);
    });
  }

  if (btnClearGeneralDirective) {
    btnClearGeneralDirective.addEventListener('click', async () => {
      // Confirmed, unlike clearing the log. Rescinding is the only irreversible
      // action in this box: the order text is not recoverable from the log if
      // the log has since been cleared.
      if (!window.confirm('Rescind the standing order? The next evaluation will run without it.')) {
        return;
      }
      await api('/api/agent/general/clear-directive', 'POST');
      await refreshGeneralDirective();
    });
  }

  // Seed the channel on load so a reload does not appear to have lost the
  // conversation or the order that is still very much in force.
  refreshGeneralChat();
  refreshGeneralDirective();

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

  // Event Listeners - Gemini Payload Inspector Modal
  function closePromptModal() {
    if (promptModal) promptModal.style.display = 'none';
  }
  if (btnClosePrompt) btnClosePrompt.addEventListener('click', closePromptModal);
  if (btnClosePromptFooter) btnClosePromptFooter.addEventListener('click', closePromptModal);
  if (btnCopyPrompt) {
    btnCopyPrompt.addEventListener('click', () => {
      // Copy the verbatim concatenated prompt, not a re-join of the rendered
      // sections -- the whole point is to reproduce exactly what the model saw.
      if (!lastPromptRecord || !lastPromptRecord.full_prompt) {
        flashCopied(btnCopyPrompt, '✕ Nothing to copy');
        return;
      }
      navigator.clipboard.writeText(lastPromptRecord.full_prompt).then(() => {
        flashCopied(btnCopyPrompt, '✓ Copied Prompt!');
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
    if (e.target === promptModal) closePromptModal();
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
