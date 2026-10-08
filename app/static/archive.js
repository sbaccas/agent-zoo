// Archive Management & Inspection UI
// Strictly enforces framework-native / safe DOM construction: NO innerHTML or outerHTML.

(function () {
  'use strict';

  let archives = [];
  let selectedArchive = null;
  let activeTabId = 'tabOverview';
  let pendingAction = null;

  // DOM Elements
  const statusBadge = document.getElementById('archiveStatusBadge');
  const btnCapture = document.getElementById('btnCaptureCurrent');
  const btnWipe = document.getElementById('btnWipeArchives');
  const btnRefresh = document.getElementById('btnRefreshArchives');
  const btnClose = document.getElementById('btnCloseArchiveWindow');
  const btnToggleSidebar = document.getElementById('btnToggleSidebar');
  const archiveSidebar = document.getElementById('archiveSidebar');
  const searchInput = document.getElementById('archiveSearch');
  const archiveList = document.getElementById('archiveList');
  const countText = document.getElementById('archiveCountText');
  const sizeText = document.getElementById('archiveSizeText');

  const emptyState = document.getElementById('archiveEmptyState');
  const detailPanel = document.getElementById('archiveDetailContent');
  const detailArchiveId = document.getElementById('detailArchiveId');
  const detailLatestPill = document.getElementById('detailLatestPill');
  const detailReasonBadge = document.getElementById('detailReasonBadge');
  const detailMetaRow = document.getElementById('detailMetaRow');
  const btnDeleteSelected = document.getElementById('btnDeleteSelected');

  // Confirmation Modal Elements
  const confirmModal = document.getElementById('archiveConfirmModal');
  const confirmTitle = document.getElementById('confirmModalTitle');
  const confirmMessage = document.getElementById('confirmModalMessage');
  const confirmWarning = document.getElementById('confirmModalWarning');
  const btnCancelConfirm = document.getElementById('btnCancelConfirm');
  const btnDismissConfirm = document.getElementById('btnDismissConfirm');
  const btnExecuteConfirm = document.getElementById('btnExecuteConfirm');

  // Notification Banner
  const notificationBanner = document.getElementById('archiveNotification');

  function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  function formatTimestamp(isoStr) {
    if (!isoStr) return '--';
    try {
      const d = new Date(isoStr);
      if (isNaN(d.getTime())) return isoStr;
      return d.toISOString().replace('T', ' ').replace(/\.\d+Z/, ' UTC');
    } catch {
      return isoStr;
    }
  }

  function showNotification(message, isError = false) {
    if (!notificationBanner) return;
    notificationBanner.textContent = message;
    notificationBanner.className = 'archive-notification' + (isError ? ' error' : ' success');
    notificationBanner.style.display = 'block';
    setTimeout(() => {
      notificationBanner.style.display = 'none';
    }, 4500);
  }

  function openConfirmModal(title, message, warning, onConfirm) {
    pendingAction = onConfirm;
    confirmTitle.textContent = title;
    confirmMessage.textContent = message;
    if (warning) {
      confirmWarning.textContent = warning;
      confirmWarning.style.display = 'block';
    } else {
      confirmWarning.style.display = 'none';
    }
    confirmModal.style.display = 'flex';
  }

  function closeConfirmModal() {
    confirmModal.style.display = 'none';
    pendingAction = null;
  }

  btnCancelConfirm.addEventListener('click', closeConfirmModal);
  btnDismissConfirm.addEventListener('click', closeConfirmModal);
  btnExecuteConfirm.addEventListener('click', async () => {
    if (typeof pendingAction === 'function') {
      const action = pendingAction;
      closeConfirmModal();
      await action();
    } else {
      closeConfirmModal();
    }
  });

  // Tab switching
  const tabButtons = document.querySelectorAll('.archive-tab-btn');
  tabButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetId = btn.getAttribute('data-tab');
      tabButtons.forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.archive-tab-pane').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const pane = document.getElementById(targetId);
      if (pane) pane.classList.add('active');
      activeTabId = targetId;

      if (targetId === 'tabReplay') {
        if (selectedArchive) {
          loadArchiveReplay(selectedArchive.id);
        }
      } else {
        pauseReplay();
      }
    });
  });

  // Fetch archives list
  async function fetchArchives(preserveSelected = true) {
    statusBadge.textContent = 'Syncing…';
    statusBadge.style.backgroundColor = '#fef3c7';
    statusBadge.style.color = '#b45309';

    try {
      const res = await fetch('/api/archives');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      archives = data.archives || [];

      statusBadge.textContent = `${archives.length} Runs`;
      statusBadge.style.backgroundColor = '#dcfce7';
      statusBadge.style.color = '#166534';

      let totalBytes = 0;
      archives.forEach(a => { totalBytes += (a.total_size_bytes || 0); });
      countText.textContent = `${archives.length} run${archives.length === 1 ? '' : 's'}`;
      sizeText.textContent = `${formatBytes(totalBytes)} on disk`;

      renderArchiveList();

      if (archives.length === 0) {
        selectedArchive = null;
        emptyState.style.display = 'flex';
        detailPanel.style.display = 'none';
      } else {
        const currentId = selectedArchive ? selectedArchive.id : null;
        let found = null;
        if (preserveSelected && currentId) {
          found = archives.find(a => a.id === currentId);
        }
        if (!found) {
          found = archives[0];
        }
        selectArchive(found.id);
      }
    } catch (err) {
      statusBadge.textContent = 'Sync Failed';
      statusBadge.style.backgroundColor = '#fee2e2';
      statusBadge.style.color = '#b91c1c';
      showNotification('Failed to read archives from disk: ' + err.message, true);
    }
  }

  function renderArchiveList() {
    archiveList.replaceChildren();

    const query = (searchInput.value || '').trim().toLowerCase();
    const filtered = archives.filter(a => {
      if (!query) return true;
      return (
        a.id.toLowerCase().includes(query) ||
        (a.simulation_id && a.simulation_id.toLowerCase().includes(query)) ||
        (a.reason && a.reason.toLowerCase().includes(query)) ||
        String(a.tick).includes(query)
      );
    });

    if (filtered.length === 0) {
      const emptyItem = document.createElement('div');
      emptyItem.className = 'archive-loading';
      emptyItem.textContent = archives.length === 0 ? 'No archives on disk.' : 'No matching archives.';
      archiveList.appendChild(emptyItem);
      return;
    }

    filtered.forEach(item => {
      const card = document.createElement('div');
      card.className = 'archive-item' + (selectedArchive && selectedArchive.id === item.id ? ' active' : '');
      card.addEventListener('click', (e) => {
        if (e.target.closest('.btn-delete-card')) return;
        selectArchive(item.id);
      });

      const topRow = document.createElement('div');
      topRow.className = 'archive-item-top';

      const stampSpan = document.createElement('span');
      stampSpan.className = 'archive-item-stamp';
      stampSpan.textContent = formatTimestamp(item.archived_at);
      topRow.appendChild(stampSpan);

      const badgesWrap = document.createElement('div');
      badgesWrap.className = 'archive-item-badges';

      if (item.is_latest) {
        const latestPill = document.createElement('span');
        latestPill.className = 'badge-pill badge-latest';
        latestPill.textContent = 'LATEST';
        badgesWrap.appendChild(latestPill);
      }

      const reasonPill = document.createElement('span');
      reasonPill.className = 'badge-pill badge-reason';
      reasonPill.textContent = item.reason || 'saved';
      badgesWrap.appendChild(reasonPill);

      topRow.appendChild(badgesWrap);
      card.appendChild(topRow);

      const midRow = document.createElement('div');
      midRow.className = 'archive-item-mid';

      const tickBadge = document.createElement('span');
      tickBadge.className = 'archive-pill-stat';
      tickBadge.textContent = `Tick ${item.tick}`;
      midRow.appendChild(tickBadge);

      const capBadge = document.createElement('span');
      capBadge.className = 'archive-pill-stat';
      capBadge.textContent = `${item.captures || 0} captures`;
      midRow.appendChild(capBadge);

      const sizeBadge = document.createElement('span');
      sizeBadge.className = 'archive-item-size';
      sizeBadge.textContent = formatBytes(item.total_size_bytes);
      midRow.appendChild(sizeBadge);

      card.appendChild(midRow);

      const btmRow = document.createElement('div');
      btmRow.className = 'archive-item-btm';

      const idSpan = document.createElement('span');
      idSpan.className = 'archive-item-simid';
      idSpan.textContent = item.simulation_id || item.id;
      btmRow.appendChild(idSpan);

      const delBtn = document.createElement('button');
      delBtn.className = 'btn-delete-card';
      delBtn.title = 'Delete this archive run';
      delBtn.textContent = '🗑️';
      delBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        confirmDeleteArchive(item.id);
      });
      btmRow.appendChild(delBtn);

      card.appendChild(btmRow);
      archiveList.appendChild(card);
    });
  }

  async function selectArchive(archiveId) {
    try {
      const res = await fetch(`/api/archives/${encodeURIComponent(archiveId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      selectedArchive = await res.json();

      emptyState.style.display = 'none';
      detailPanel.style.display = 'flex';

      renderDetail();
      renderArchiveList(); // Update selected styling in left panel

      if (activeTabId === 'tabReplay') {
        loadArchiveReplay(selectedArchive.id);
      }
    } catch (err) {
      showNotification('Failed to load archive details: ' + err.message, true);
    }
  }

  function renderDetail() {
    if (!selectedArchive) return;

    const manifest = selectedArchive.manifest || {};
    const finalInfo = manifest.final || {};
    const arena = manifest.arena || {};
    const agents = manifest.agents || {};
    const code = manifest.code || {};

    detailArchiveId.textContent = selectedArchive.id;

    if (selectedArchive.is_latest) {
      detailLatestPill.style.display = 'inline-block';
    } else {
      detailLatestPill.style.display = 'none';
    }

    detailReasonBadge.textContent = manifest.reason || 'saved';

    detailMetaRow.replaceChildren();
    const metaParts = [
      `Simulation: ${manifest.simulation_id || '--'}`,
      `Saved: ${formatTimestamp(manifest.archived_at)}`,
      `Disk footprint: ${formatBytes(selectedArchive.total_size_bytes)}`
    ];
    metaParts.forEach(part => {
      const span = document.createElement('span');
      span.textContent = part;
      detailMetaRow.appendChild(span);
    });

    // Overview KPIs
    document.getElementById('kpiFinalTick').textContent = finalInfo.tick ?? '--';
    document.getElementById('kpiFinalRound').textContent = finalInfo.round ?? '--';
    document.getElementById('kpiFinalCaptures').textContent = finalInfo.captures ?? '--';
    document.getElementById('kpiMiceRemaining').textContent = finalInfo.mice_remaining ?? '--';
    document.getElementById('kpiDimensions').textContent = arena.width ? `${arena.width} × ${arena.height}` : '--';
    document.getElementById('kpiDiskFootprint').textContent = formatBytes(selectedArchive.total_size_bytes);

    // Agents
    const agentsList = document.getElementById('detailAgentsList');
    agentsList.replaceChildren();
    const agentKeys = Object.keys(agents);
    if (agentKeys.length === 0) {
      const noneSpan = document.createElement('span');
      noneSpan.className = 'text-muted';
      noneSpan.textContent = 'No agent records saved.';
      agentsList.appendChild(noneSpan);
    } else {
      agentKeys.forEach(aid => {
        const pill = document.createElement('span');
        pill.className = 'agent-mode-pill';
        pill.textContent = `${aid} (${agents[aid]})`;
        agentsList.appendChild(pill);
      });
    }

    // Provenance
    const provBox = document.getElementById('detailProvenance');
    provBox.replaceChildren();
    const cItem = document.createElement('div');
    cItem.textContent = `Git Commit: ${code.commit || 'unknown'}`;
    provBox.appendChild(cItem);

    const bItem = document.createElement('div');
    bItem.textContent = `Branch: ${code.branch || 'unknown'}`;
    provBox.appendChild(bItem);

    const dItem = document.createElement('div');
    dItem.textContent = `Working Tree: ${code.dirty ? 'Dirty (uncommitted changes)' : 'Clean'}`;
    provBox.appendChild(dItem);

    // Box Score Tab
    const boxWrap = document.getElementById('detailBoxscoreWrap');
    boxWrap.replaceChildren();
    const boxscore = selectedArchive.boxscore;
    if (boxscore && boxscore.cats && boxscore.cats.length > 0) {
      const table = document.createElement('table');
      table.className = 'boxscore-table';

      const thead = document.createElement('thead');
      const trh = document.createElement('tr');
      ['Cat ID', 'Mode', 'Kills', 'Kills / 100t', 'Slash Accuracy', 'Zone Conversion', 'Whiffs', 'Distance Traveled'].forEach(h => {
        const th = document.createElement('th');
        th.textContent = h;
        trh.appendChild(th);
      });
      thead.appendChild(trh);
      table.appendChild(thead);

      const tbody = document.createElement('tbody');
      boxscore.cats.forEach(c => {
        const tr = document.createElement('tr');
        const accPct = c.slash_attempts > 0 ? (c.slash_accuracy * 100).toFixed(1) + '%' : '--';
        const convPct = c.zone_chances > 0 ? (c.zone_conversion * 100).toFixed(1) + '%' : '--';
        const k100 = c.kills_per_100_ticks !== undefined ? c.kills_per_100_ticks.toFixed(2) : '--';

        [
          c.id,
          c.mode || '--',
          c.kills,
          k100,
          accPct,
          convPct,
          c.whiffs ?? 0,
          c.tiles_run ?? c.distance_traveled ?? 0
        ].forEach(val => {
          const td = document.createElement('td');
          td.textContent = String(val);
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      boxWrap.appendChild(table);
    } else {
      const noBox = document.createElement('div');
      noBox.className = 'archive-loading';
      noBox.textContent = 'No box score snapshot recorded for this run.';
      boxWrap.appendChild(noBox);
    }

    // Coaching Notes Tab
    const notesWrap = document.getElementById('detailNotesWrap');
    notesWrap.replaceChildren();
    if (selectedArchive.coaching_notes) {
      const pre = document.createElement('pre');
      pre.textContent = selectedArchive.coaching_notes;
      notesWrap.appendChild(pre);
    } else {
      const noNotes = document.createElement('div');
      noNotes.className = 'archive-loading';
      noNotes.textContent = 'No coaching notes found in this archive.';
      notesWrap.appendChild(noNotes);
    }

    // Files Tab
    const filesTbody = document.getElementById('detailFilesTableBody');
    filesTbody.replaceChildren();
    const files = selectedArchive.files || [];
    if (files.length === 0) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.setAttribute('colspan', '3');
      td.textContent = 'No files found in directory.';
      tr.appendChild(td);
      filesTbody.appendChild(tr);
    } else {
      files.forEach(f => {
        const tr = document.createElement('tr');

        const tdName = document.createElement('td');
        tdName.textContent = f.path;
        tr.appendChild(tdName);

        const tdSize = document.createElement('td');
        tdSize.textContent = formatBytes(f.size_bytes);
        tr.appendChild(tdSize);

        const tdAction = document.createElement('td');
        const dlLink = document.createElement('a');
        dlLink.className = 'btn btn-sm btn-outline';
        dlLink.textContent = '⬇ Download';
        dlLink.href = `/api/archives/${encodeURIComponent(selectedArchive.id)}/files/${encodeURIComponent(f.path)}`;
        dlLink.target = '_blank';
        dlLink.rel = 'noreferrer';
        tdAction.appendChild(dlLink);
        tr.appendChild(tdAction);

        filesTbody.appendChild(tr);
      });
    }

    // Raw Manifest Tab
    const manifestPre = document.getElementById('detailManifestJson');
    manifestPre.textContent = JSON.stringify(manifest, null, 2);
  }

  // Delete single archive action
  function confirmDeleteArchive(archiveId) {
    openConfirmModal(
      'Delete Archive Run',
      `Permanently delete archive '${archiveId}' from disk?`,
      'This action deletes the folder and cannot be undone.',
      async () => {
        try {
          const res = await fetch(`/api/archives/${encodeURIComponent(archiveId)}`, {
            method: 'DELETE',
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          showNotification(`Archive '${archiveId}' deleted successfully.`);
          await fetchArchives(false);
        } catch (err) {
          showNotification('Delete failed: ' + err.message, true);
        }
      }
    );
  }

  btnDeleteSelected.addEventListener('click', () => {
    if (selectedArchive) {
      confirmDeleteArchive(selectedArchive.id);
    }
  });

  // Wipe all archives action
  btnWipe.addEventListener('click', () => {
    openConfirmModal(
      'Wipe All Archives',
      `Permanently wipe all ${archives.length} archive runs on disk?`,
      '⚠️ ALL historical simulations, prompts, and box score records will be permanently removed from disk!',
      async () => {
        try {
          const res = await fetch('/api/archives', {
            method: 'DELETE',
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = await res.json();
          showNotification(`Wiped ${data.deleted_count} archive runs from disk.`);
          await fetchArchives(false);
        } catch (err) {
          showNotification('Wipe failed: ' + err.message, true);
        }
      }
    );
  });

  // Capture active game snapshot action
  btnCapture.addEventListener('click', async () => {
    btnCapture.disabled = true;
    btnCapture.textContent = '📸 Capturing…';
    try {
      const res = await fetch('/api/archives/capture', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'operator_manual_capture', sim_id: 'active' }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      showNotification(`Saved snapshot '${data.archive_id}' (tick ${data.tick})`);
      await fetchArchives(false);
      selectArchive(data.archive_id);
    } catch (err) {
      showNotification('Capture failed: ' + err.message, true);
    } finally {
      btnCapture.disabled = false;
      btnCapture.textContent = '📸 Capture Active Game';
    }
  });

  // Refresh button
  btnRefresh.addEventListener('click', () => {
    fetchArchives(true);
  });

  // Search input filter
  searchInput.addEventListener('input', () => {
    renderArchiveList();
  });

  // Close window button
  if (btnClose) {
    btnClose.addEventListener('click', () => {
      window.close();
    });
  }

  // Toggle runs sidebar button
  if (btnToggleSidebar && archiveSidebar) {
    btnToggleSidebar.addEventListener('click', () => {
      const isCollapsed = archiveSidebar.classList.toggle('collapsed');
      btnToggleSidebar.textContent = isCollapsed ? '▶ Runs' : '◀ Runs';
      btnToggleSidebar.title = isCollapsed ? 'Show runs list sidebar' : 'Hide runs list sidebar';
      setTimeout(() => {
        resizeCanvasIfNeeded();
        renderReplayFrame();
      }, 60);
    });
  }

  window.addEventListener('resize', () => {
    if (activeTabId === 'tabReplay') {
      resizeCanvasIfNeeded();
      renderReplayFrame();
    }
  });

  // =========================================================================
  // Replay Controller: Multi-Agent Motion, POV Switcher, Witness Tracking
  // =========================================================================
  let replayData = null;
  let currentReplayTick = 1;
  let isPlaying = false;
  let playbackSpeed = 1.0;
  let activeAgentPov = 'all';
  let replayTimer = null;
  let framesMap = new Map();
  let sortedFrameTicks = [];
  let deathsMap = new Map();
  let catThoughtCounts = new Map();
  let selectedInspectorCat = null;

  function getReplayFrame(tick) {
    if (framesMap.has(tick)) {
      return framesMap.get(tick);
    }
    if (sortedFrameTicks.length === 0) return { cats: {}, mice: [] };
    // Binary search / lower bound for nearest frame <= tick
    let low = 0;
    let high = sortedFrameTicks.length - 1;
    let best = sortedFrameTicks[0];
    while (low <= high) {
      const mid = Math.floor((low + high) / 2);
      if (sortedFrameTicks[mid] <= tick) {
        best = sortedFrameTicks[mid];
        low = mid + 1;
      } else {
        high = mid - 1;
      }
    }
    return framesMap.get(best) || { cats: {}, mice: [] };
  }

  const replayCanvas = document.getElementById('replayCanvas');
  const replayCtx = replayCanvas ? replayCanvas.getContext('2d') : null;
  const replayScrubSlider = document.getElementById('replayScrubSlider');
  const replayCurrentTickText = document.getElementById('replayCurrentTickText');
  const replayMaxTickText = document.getElementById('replayMaxTickText');
  const replayBtnPlayPause = document.getElementById('replayBtnPlayPause');
  const replayBtnStepBack = document.getElementById('replayBtnStepBack');
  const replayBtnStepForward = document.getElementById('replayBtnStepForward');
  const replaySpeedSelect = document.getElementById('replaySpeedSelect');
  const replayDeathsList = document.getElementById('replayDeathsList');
  const replayPovChips = document.getElementById('replayPovChips');
  const replayActiveAgentBadge = document.getElementById('replayActiveAgentBadge');
  const replayInspectorTargetSelect = document.getElementById('replayInspectorTargetSelect');

  const replayDeathAlertBox = document.getElementById('replayDeathAlertBox');
  const replayDeathAlertTitle = document.getElementById('replayDeathAlertTitle');
  const replayDeathAlertActions = document.getElementById('replayDeathAlertActions');
  const replayAgentPos = document.getElementById('replayAgentPos');
  const replayAgentFacing = document.getElementById('replayAgentFacing');
  const replayAgentAction = document.getElementById('replayAgentAction');
  const replayAgentHp = document.getElementById('replayAgentHp');
  const replayAgentCooldown = document.getElementById('replayAgentCooldown');
  const replayAgentEngine = document.getElementById('replayAgentEngine');
  const replayAgentThought = document.getElementById('replayAgentThought');
  const replayAgentVisionList = document.getElementById('replayAgentVisionList');
  const replayCoachNotes = document.getElementById('replayCoachNotes');

  const CAT_PALETTE = {
    'cat-1': '#3b82f6',
    'cat-2': '#f97316',
    'cat-3': '#10b981',
    'cat-4': '#8b5cf6',
    'cat-5': '#ec4899',
    'cat-6': '#06b6d4',
    'cat-7': '#eab308',
    'cat-8': '#6366f1',
    'cat-9': '#14b8a6',
    'cat-10': '#f43f5e',
  };

  function getCatColor(catId) {
    return CAT_PALETTE[catId] || '#a855f7';
  }

  async function loadArchiveReplay(archiveId) {
    pauseReplay();
    if (replayCurrentTickText) replayCurrentTickText.textContent = 'Loading…';
    try {
      const res = await fetch(`/api/archives/${encodeURIComponent(archiveId)}/replay`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      replayData = await res.json();

      framesMap.clear();
      catThoughtCounts.clear();

      (replayData.frames || []).forEach(f => {
        framesMap.set(f.tick, f);
        const cdict = f.cats || {};
        Object.keys(cdict).forEach(cid => {
          const th = (cdict[cid].thought || '').trim();
          if (th) {
            catThoughtCounts.set(cid, (catThoughtCounts.get(cid) || 0) + 1);
          }
        });
      });
      sortedFrameTicks = Array.from(framesMap.keys()).sort((a, b) => a - b);

      deathsMap.clear();
      (replayData.deaths || []).forEach(d => {
        deathsMap.set(d.tick, d);
      });

      // Populate inspector agent target dropdown
      if (replayInspectorTargetSelect) {
        replayInspectorTargetSelect.replaceChildren();
        const catsList = (replayData.cats || []);
        let bestCat = null;
        let maxThoughts = -1;

        catsList.forEach(cid => {
          const cnt = catThoughtCounts.get(cid) || 0;
          if (cnt > maxThoughts) {
            maxThoughts = cnt;
            bestCat = cid;
          }
          const opt = document.createElement('option');
          opt.value = cid;
          opt.textContent = cnt > 0 ? `${cid} (${cnt} thoughts)` : `${cid} (heuristic)`;
          replayInspectorTargetSelect.appendChild(opt);
        });

        // Default to the cat that actually has generated thoughts, if any
        selectedInspectorCat = bestCat || catsList[0] || 'cat-1';
        replayInspectorTargetSelect.value = selectedInspectorCat;
      }

      const meta = replayData.meta || {};
      const minTick = meta.min_tick || 1;
      const maxTick = meta.max_tick || 100;

      if (replayScrubSlider) {
        replayScrubSlider.min = String(minTick);
        replayScrubSlider.max = String(maxTick);
        replayScrubSlider.value = String(minTick);
      }
      if (replayMaxTickText) {
        replayMaxTickText.textContent = String(maxTick);
      }

      currentReplayTick = minTick;
      renderReplayDeaths();
      renderReplayPovChips();
      renderReplayFrame();
      updateReplayInspector();
    } catch (err) {
      if (replayCurrentTickText) replayCurrentTickText.textContent = 'Load Failed';
      showNotification('Could not load replay: ' + err.message, true);
    }
  }

  function renderReplayDeaths() {
    if (!replayDeathsList) return;
    replayDeathsList.replaceChildren();

    const deaths = (replayData && replayData.deaths) || [];
    if (deaths.length === 0) {
      const emptySpan = document.createElement('span');
      emptySpan.className = 'replay-no-deaths';
      emptySpan.textContent = 'No cat eliminations recorded in this match.';
      replayDeathsList.appendChild(emptySpan);
      return;
    }

    deaths.forEach(d => {
      const chip = document.createElement('button');
      chip.className = 'replay-death-chip';
      if (d.tick === currentReplayTick) chip.classList.add('active');

      const killerName = d.killer_id || 'Rival';
      const victimName = d.victim_id || 'Cat';
      chip.textContent = `⚡ [Tick ${d.tick}] ${killerName} ➔ ${victimName}`;
      chip.title = `Jump to tick ${d.tick} (${killerName} eliminated ${victimName})`;

      chip.addEventListener('click', () => {
        pauseReplay();
        const targetTick = Math.max(1, d.tick - 3);
        seekToTick(targetTick);
        if (d.killer_id && d.killer_id.startsWith('cat-')) {
          setActivePov(d.killer_id);
        }
      });
      replayDeathsList.appendChild(chip);
    });
  }

  function renderReplayPovChips() {
    if (!replayPovChips) return;
    replayPovChips.replaceChildren();

    const allBtn = document.createElement('button');
    allBtn.className = 'btn-pov-chip' + (activeAgentPov === 'all' ? ' active' : '');
    allBtn.textContent = '👁️ All Cats';
    allBtn.addEventListener('click', () => setActivePov('all'));
    replayPovChips.appendChild(allBtn);

    const cats = (replayData && replayData.cats) || [];
    cats.forEach(cid => {
      const btn = document.createElement('button');
      btn.className = 'btn-pov-chip' + (activeAgentPov === cid ? ' active' : '');
      const cnt = catThoughtCounts.get(cid) || 0;
      btn.textContent = cnt > 0 ? `🐱 ${cid} 🧠` : `🐱 ${cid}`;
      btn.title = cnt > 0 ? `${cid}: ${cnt} subjective rationales recorded` : `${cid}: Heuristic / No thoughts recorded`;
      btn.style.borderColor = getCatColor(cid);
      btn.addEventListener('click', () => setActivePov(cid));
      replayPovChips.appendChild(btn);
    });
  }

  function setActivePov(pov) {
    activeAgentPov = pov;
    if (pov !== 'all') {
      selectedInspectorCat = pov;
      if (replayInspectorTargetSelect) {
        replayInspectorTargetSelect.value = pov;
      }
    }
    if (replayActiveAgentBadge) {
      replayActiveAgentBadge.textContent = pov === 'all' ? 'Omniscient (All Cats)' : `POV: ${pov}`;
      replayActiveAgentBadge.style.backgroundColor = pov === 'all' ? '#e2e8f0' : getCatColor(pov);
      replayActiveAgentBadge.style.color = pov === 'all' ? '#0f172a' : '#ffffff';
    }
    renderReplayPovChips();
    renderReplayFrame();
    updateReplayInspector();
  }

  if (replayInspectorTargetSelect) {
    replayInspectorTargetSelect.addEventListener('change', (ev) => {
      selectedInspectorCat = ev.target.value;
      updateReplayInspector();
    });
  }

  function resizeCanvasIfNeeded() {
    if (!replayCanvasWrapper || !replayCanvas) return;
    const containerW = replayCanvasWrapper.clientWidth;
    if (containerW > 50) {
      const targetSize = Math.max(560, Math.min(840, Math.floor(containerW - 20)));
      if (replayCanvas.width !== targetSize) {
        replayCanvas.width = targetSize;
        replayCanvas.height = targetSize;
      }
    }
  }

  function renderReplayFrame() {
    if (!replayCtx || !replayCanvas) return;
    resizeCanvasIfNeeded();

    const meta = (replayData && replayData.meta) || {};
    const arenaW = (meta.arena && meta.arena.width) || 100;
    const arenaH = (meta.arena && meta.arena.height) || 100;

    const canvasW = replayCanvas.width;
    const canvasH = replayCanvas.height;
    const cellW = canvasW / arenaW;
    const cellH = canvasH / arenaH;

    // 1. Clean White Board Background
    replayCtx.fillStyle = '#ffffff';
    replayCtx.fillRect(0, 0, canvasW, canvasH);

    // 2. Arena Grid Lines (subtle) & Outer Border
    replayCtx.strokeStyle = 'rgba(0, 0, 0, 0.06)';
    replayCtx.lineWidth = 1;
    for (let x = 0; x <= arenaW; x += 10) {
      replayCtx.beginPath();
      replayCtx.moveTo(x * cellW, 0);
      replayCtx.lineTo(x * cellW, canvasH);
      replayCtx.stroke();
    }
    for (let y = 0; y <= arenaH; y += 10) {
      replayCtx.beginPath();
      replayCtx.moveTo(0, y * cellH);
      replayCtx.lineTo(canvasW, y * cellH);
      replayCtx.stroke();
    }

    // Outer boundary
    replayCtx.strokeStyle = '#94a3b8';
    replayCtx.lineWidth = 1.5;
    replayCtx.strokeRect(0, 0, canvasW, canvasH);

    // Corner coordinate markers
    replayCtx.font = '9px system-ui, sans-serif';
    replayCtx.fillStyle = '#64748b';
    replayCtx.textAlign = 'left';
    replayCtx.fillText('(0,0)', 4, 11);
    replayCtx.textAlign = 'right';
    replayCtx.fillText(`(${arenaW},0)`, canvasW - 4, 11);
    replayCtx.textAlign = 'left';
    replayCtx.fillText(`(0,${arenaH})`, 4, canvasH - 4);
    replayCtx.textAlign = 'right';
    replayCtx.fillText(`(${arenaW},${arenaH})`, canvasW - 4, canvasH - 4);

    const frame = getReplayFrame(currentReplayTick);

    // 3. Draw Active POV Vision Cone & Strike Reach
    if (activeAgentPov !== 'all' && frame.cats && frame.cats[activeAgentPov]) {
      const activeCat = frame.cats[activeAgentPov];
      if (activeCat.alive && activeCat.x !== null && activeCat.y !== null && !isNaN(activeCat.x) && !isNaN(activeCat.y)) {
        const cx = activeCat.x * cellW + cellW / 2;
        const cy = activeCat.y * cellH + cellH / 2;

        // Reach Box (vision box)
        const reachRadius = cellW * 10;
        replayCtx.fillStyle = 'rgba(59, 130, 246, 0.12)';
        replayCtx.beginPath();
        replayCtx.arc(cx, cy, reachRadius, 0, Math.PI * 2);
        replayCtx.fill();
        replayCtx.strokeStyle = 'rgba(59, 130, 246, 0.35)';
        replayCtx.lineWidth = 1;
        replayCtx.stroke();

        // Forward Strike Arc
        const attackRange = cellW * 4;
        replayCtx.fillStyle = 'rgba(239, 68, 68, 0.20)';
        replayCtx.beginPath();
        let startAngle = -Math.PI / 4;
        let endAngle = Math.PI / 4;
        if (activeCat.facing === 'east') {
          startAngle = -Math.PI / 4;
          endAngle = Math.PI / 4;
        } else if (activeCat.facing === 'south') {
          startAngle = Math.PI / 4;
          endAngle = (3 * Math.PI) / 4;
        } else if (activeCat.facing === 'west') {
          startAngle = (3 * Math.PI) / 4;
          endAngle = (5 * Math.PI) / 4;
        } else {
          // north
          startAngle = (5 * Math.PI) / 4;
          endAngle = (7 * Math.PI) / 4;
        }
        replayCtx.moveTo(cx, cy);
        replayCtx.arc(cx, cy, attackRange, startAngle, endAngle);
        replayCtx.closePath();
        replayCtx.fill();
        replayCtx.strokeStyle = 'rgba(239, 68, 68, 0.50)';
        replayCtx.lineWidth = 1;
        replayCtx.stroke();
      }
    }

    // 4. Render Mice
    const miceList = Array.isArray(frame.mice) ? frame.mice : Object.values(frame.mice || {});
    miceList.forEach(m => {
      if (m.alive === false || m.x === null || m.y === null || isNaN(m.x) || isNaN(m.y)) return;
      const mx = m.x * cellW + cellW / 2;
      const my = m.y * cellH + cellH / 2;
      const mRadius = Math.max(2.8, cellW * 0.55);

      replayCtx.fillStyle = '#64748b';
      replayCtx.beginPath();
      replayCtx.arc(mx, my, mRadius, 0, Math.PI * 2);
      replayCtx.fill();
      replayCtx.strokeStyle = '#334155';
      replayCtx.lineWidth = 0.9;
      replayCtx.stroke();

      // Mouse facing indicator
      const fDist = mRadius + 1.5;
      let mfx = mx, mfy = my - fDist;
      if (m.facing === 'south') { mfx = mx; mfy = my + fDist; }
      else if (m.facing === 'east') { mfx = mx + fDist; mfy = my; }
      else if (m.facing === 'west') { mfx = mx - fDist; mfy = my; }
      replayCtx.fillStyle = '#0f172a';
      replayCtx.beginPath();
      replayCtx.arc(mfx, mfy, 1.3, 0, Math.PI * 2);
      replayCtx.fill();
    });

    // 5. Render Cats
    const catsDict = frame.cats || {};
    Object.keys(catsDict).forEach(cid => {
      const cat = catsDict[cid];
      if (cat.x === null || cat.y === null || isNaN(cat.x) || isNaN(cat.y)) return;

      const cx = cat.x * cellW + cellW / 2;
      const cy = cat.y * cellH + cellH / 2;
      const catColor = getCatColor(cid);
      const isAlive = cat.alive && (cat.hp > 0);

      if (isAlive) {
        const radius = Math.max(5.5, cellW * 0.95);

        // Active POV highlight ring
        if (activeAgentPov === cid) {
          replayCtx.strokeStyle = '#2563eb';
          replayCtx.lineWidth = 3;
          replayCtx.beginPath();
          replayCtx.arc(cx, cy, radius + 4, 0, Math.PI * 2);
          replayCtx.stroke();
        }

        // Cat Body
        replayCtx.fillStyle = catColor;
        replayCtx.beginPath();
        replayCtx.arc(cx, cy, radius, 0, Math.PI * 2);
        replayCtx.fill();
        replayCtx.strokeStyle = '#0f172a';
        replayCtx.lineWidth = activeAgentPov === cid ? 2.2 : 1.4;
        replayCtx.stroke();

        // Facing pointer
        const facingDist = radius + 3;
        let fx = cx, fy = cy - facingDist;
        if (cat.facing === 'south') { fx = cx; fy = cy + facingDist; }
        else if (cat.facing === 'east') { fx = cx + facingDist; fy = cy; }
        else if (cat.facing === 'west') { fx = cx - facingDist; fy = cy; }

        replayCtx.fillStyle = '#0f172a';
        replayCtx.beginPath();
        replayCtx.arc(fx, fy, 2.4, 0, Math.PI * 2);
        replayCtx.fill();
        replayCtx.fillStyle = '#ffffff';
        replayCtx.beginPath();
        replayCtx.arc(fx, fy, 1.1, 0, Math.PI * 2);
        replayCtx.fill();

        // HP Bar
        const barW = Math.max(18, cellW * 2.5);
        const barH = 3.5;
        const barX = cx - barW / 2;
        const barY = cy - radius - 7;
        const hpRatio = Math.max(0, Math.min(1, (cat.hp || 100) / (cat.max_hp || 100)));

        replayCtx.fillStyle = '#e2e8f0';
        replayCtx.fillRect(barX, barY, barW, barH);
        replayCtx.fillStyle = hpRatio >= 0.7 ? '#16a34a' : (hpRatio >= 0.35 ? '#d97706' : '#dc2626');
        replayCtx.fillRect(barX, barY, barW * hpRatio, barH);
        replayCtx.strokeStyle = '#94a3b8';
        replayCtx.lineWidth = 0.5;
        replayCtx.strokeRect(barX, barY, barW, barH);

        // Overhead Name Pill
        const labelText = cid;
        replayCtx.font = 'bold 9px system-ui, sans-serif';
        const textMetrics = replayCtx.measureText(labelText);
        const pillW = textMetrics.width + 8;
        const pillH = 13;
        const pillX = cx - pillW / 2;
        const pillY = barY - pillH - 2;

        replayCtx.fillStyle = '#0f172a';
        replayCtx.fillRect(pillX, pillY, pillW, pillH);
        replayCtx.strokeStyle = catColor;
        replayCtx.lineWidth = 1.2;
        replayCtx.strokeRect(pillX, pillY, pillW, pillH);

        replayCtx.textAlign = 'center';
        replayCtx.fillStyle = '#ffffff';
        replayCtx.fillText(labelText, cx, pillY + 10);

        // Slash Action Animation
        if (cat.action === 'attack') {
          replayCtx.strokeStyle = '#ef4444';
          replayCtx.lineWidth = 3;
          replayCtx.beginPath();
          replayCtx.arc(cx, cy, cellW * 4, -Math.PI / 3, Math.PI / 3);
          replayCtx.stroke();
        } else if (cat.action === 'lunge') {
          replayCtx.strokeStyle = '#0284c7';
          replayCtx.lineWidth = 2.4;
          replayCtx.beginPath();
          replayCtx.moveTo(cx, cy);
          replayCtx.lineTo(
            cx - (cat.facing === 'east' ? 14 : (cat.facing === 'west' ? -14 : 0)),
            cy - (cat.facing === 'south' ? 14 : (cat.facing === 'north' ? -14 : 0))
          );
          replayCtx.stroke();
        }
      } else {
        // Eliminated Corpse Marker
        replayCtx.fillStyle = 'rgba(241, 245, 249, 0.9)';
        replayCtx.beginPath();
        replayCtx.arc(cx, cy, Math.max(4.5, cellW * 0.7), 0, Math.PI * 2);
        replayCtx.fill();
        replayCtx.strokeStyle = '#94a3b8';
        replayCtx.lineWidth = 1;
        replayCtx.stroke();

        replayCtx.strokeStyle = '#dc2626';
        replayCtx.lineWidth = 2;
        replayCtx.beginPath();
        const d = cellW * 0.55;
        replayCtx.moveTo(cx - d, cy - d);
        replayCtx.lineTo(cx + d, cy + d);
        replayCtx.moveTo(cx + d, cy - d);
        replayCtx.lineTo(cx - d, cy + d);
        replayCtx.stroke();

        replayCtx.font = 'bold 8px system-ui, sans-serif';
        replayCtx.fillStyle = '#64748b';
        replayCtx.textAlign = 'center';
        replayCtx.fillText(cid, cx, cy + d + 9);
      }
    });

    // 6. Death Flash on this Tick
    const deathOnTick = deathsMap.get(currentReplayTick);
    if (deathOnTick && deathOnTick.victim_pos && deathOnTick.victim_pos.x !== null) {
      const vx = deathOnTick.victim_pos.x * cellW + cellW / 2;
      const vy = deathOnTick.victim_pos.y * cellH + cellH / 2;
      replayCtx.strokeStyle = '#dc2626';
      replayCtx.lineWidth = 3;
      replayCtx.beginPath();
      replayCtx.arc(vx, vy, cellW * 5, 0, Math.PI * 2);
      replayCtx.stroke();

      replayCtx.font = 'bold 11px system-ui, sans-serif';
      replayCtx.fillStyle = '#b91c1c';
      replayCtx.textAlign = 'center';
      replayCtx.fillText('⚡ ELIMINATED', vx, vy - cellW * 5 - 4);
    }
  }

  function updateReplayInspector() {
    if (replayCurrentTickText) {
      replayCurrentTickText.textContent = `Tick ${currentReplayTick}`;
    }
    if (replayScrubSlider) {
      replayScrubSlider.value = String(currentReplayTick);
    }

    const frame = getReplayFrame(currentReplayTick);
    const catsDict = frame.cats || {};

    // Determine target cat for inspector:
    let inspectCatId = activeAgentPov !== 'all' ? activeAgentPov : selectedInspectorCat;
    if (!inspectCatId || !catsDict[inspectCatId]) {
      inspectCatId = (selectedInspectorCat && catsDict[selectedInspectorCat])
        ? selectedInspectorCat
        : (Object.keys(catsDict)[0] || ((replayData && replayData.cats && replayData.cats[0]) || 'cat-1'));
    }

    if (replayInspectorTargetSelect && replayInspectorTargetSelect.value !== inspectCatId) {
      replayInspectorTargetSelect.value = inspectCatId;
    }

    if (replayActiveAgentBadge) {
      if (activeAgentPov === 'all') {
        replayActiveAgentBadge.textContent = 'Omniscient (All Cats)';
        replayActiveAgentBadge.style.backgroundColor = '#334155';
        replayActiveAgentBadge.style.color = '#ffffff';
      } else {
        replayActiveAgentBadge.textContent = `POV: ${activeAgentPov}`;
        replayActiveAgentBadge.style.backgroundColor = getCatColor(activeAgentPov);
        replayActiveAgentBadge.style.color = '#ffffff';
      }
    }

    const catState = catsDict[inspectCatId] || {};

    // 1. Posture & Stats
    if (replayAgentPos) {
      replayAgentPos.textContent = (catState.x !== null && catState.y !== null) ? `(${catState.x}, ${catState.y})` : '--';
    }
    if (replayAgentFacing) {
      replayAgentFacing.textContent = catState.facing ? catState.facing.toUpperCase() : '--';
    }
    if (replayAgentAction) {
      replayAgentAction.textContent = catState.action ? catState.action.toUpperCase() : '--';
      replayAgentAction.style.color = catState.action === 'attack' ? '#dc2626' : (catState.action === 'lunge' ? '#0284c7' : '#0f172a');
    }
    if (replayAgentHp) {
      const hp = catState.hp ?? '--';
      replayAgentHp.textContent = `${hp} / 100`;
      replayAgentHp.style.color = (catState.hp || 100) < 40 ? '#dc2626' : '#16a34a';
    }
    if (replayAgentCooldown) {
      replayAgentCooldown.textContent = catState.action_cooldown ?? 0;
    }
    if (replayAgentEngine) {
      replayAgentEngine.textContent = catState.source || 'adk_gemini';
    }

    // 2. Inner Thought / Tactical Rationale
    if (replayAgentThought) {
      const thought = (catState.thought || '').trim();
      if (thought) {
        replayAgentThought.textContent = thought;
        replayAgentThought.style.fontStyle = 'normal';
        replayAgentThought.style.color = '#f8fafc';
      } else {
        const totalRecordedThoughts = catThoughtCounts.get(inspectCatId) || 0;
        if (totalRecordedThoughts === 0) {
          replayAgentThought.textContent = `(No inner thought generated by ${inspectCatId} at tick ${currentReplayTick} — this agent ran heuristic / no transcript recorded in this match)`;
        } else {
          replayAgentThought.textContent = `(No inner thought generated by ${inspectCatId} at tick ${currentReplayTick})`;
        }
        replayAgentThought.style.fontStyle = 'italic';
        replayAgentThought.style.color = '#94a3b8';
      }
    }

    // 3. Witness / FOV list
    if (replayAgentVisionList) {
      replayAgentVisionList.replaceChildren();
      const visList = catState.visible || [];
      if (visList.length === 0) {
        const emptyDiv = document.createElement('div');
        emptyDiv.className = 'replay-empty-sub';
        emptyDiv.textContent = 'No mice or rival cats within field of view.';
        replayAgentVisionList.appendChild(emptyDiv);
      } else {
        visList.forEach(v => {
          const row = document.createElement('div');
          row.className = 'vision-chip-row' + (v.in_strike_zone ? ' in-strike-zone' : '');

          const leftSpan = document.createElement('span');
          leftSpan.textContent = `${v.type === 'cat' ? '🐱' : '🐭'} ${v.id} (${v.x}, ${v.y})`;
          row.appendChild(leftSpan);

          const rightSpan = document.createElement('span');
          rightSpan.textContent = v.in_strike_zone ? '⚡ IN STRIKE ZONE' : `dist: ${v.forward ?? '?'}`;
          row.appendChild(rightSpan);

          replayAgentVisionList.appendChild(row);
        });
      }
    }

    // 4. Active Coach Notes for this tick
    if (replayCoachNotes) {
      const coach = (replayData && replayData.coach) || {};
      const notes = (coach.notes && coach.notes[inspectCatId]) || (coach.notes && coach.notes['cat-1']) || '';
      replayCoachNotes.textContent = notes.trim() || 'No active coach guidance for this tick.';
    }

    // 5. Death Alert Box & Quick POV Jump
    const deathOnTick = deathsMap.get(currentReplayTick);
    if (deathOnTick && replayDeathAlertBox && replayDeathAlertTitle && replayDeathAlertActions) {
      replayDeathAlertBox.style.display = 'block';
      replayDeathAlertTitle.textContent = `⚡ [Tick ${deathOnTick.tick}] ${deathOnTick.killer_id} eliminated ${deathOnTick.victim_id}!`;
      replayDeathAlertActions.replaceChildren();

      // Quick Hunter Button
      if (deathOnTick.killer_id && deathOnTick.killer_id.startsWith('cat-')) {
        const btnHunter = document.createElement('button');
        btnHunter.className = 'btn-pov-quick';
        btnHunter.textContent = `🎯 Hunter POV (${deathOnTick.killer_id})`;
        btnHunter.addEventListener('click', () => setActivePov(deathOnTick.killer_id));
        replayDeathAlertActions.appendChild(btnHunter);
      }

      // Quick Victim Button
      if (deathOnTick.victim_id && deathOnTick.victim_id.startsWith('cat-')) {
        const btnVictim = document.createElement('button');
        btnVictim.className = 'btn-pov-quick';
        btnVictim.textContent = `💀 Victim POV (${deathOnTick.victim_id})`;
        btnVictim.addEventListener('click', () => setActivePov(deathOnTick.victim_id));
        replayDeathAlertActions.appendChild(btnVictim);
      }

      // Quick Witness Buttons
      (deathOnTick.witnesses || []).forEach(w => {
        const btnWitness = document.createElement('button');
        btnWitness.className = 'btn-pov-quick';
        btnWitness.textContent = `👀 Witness POV (${w})`;
        btnWitness.addEventListener('click', () => setActivePov(w));
        replayDeathAlertActions.appendChild(btnWitness);
      });
    } else if (replayDeathAlertBox) {
      replayDeathAlertBox.style.display = 'none';
    }
  }

  function seekToTick(tick) {
    if (!replayData) return;
    const meta = replayData.meta || {};
    const minT = meta.min_tick || 1;
    const maxT = meta.max_tick || 100;
    currentReplayTick = Math.max(minT, Math.min(maxT, tick));
    renderReplayFrame();
    updateReplayInspector();
  }

  function stepForward(delta = 1) {
    if (!replayData) return;
    const meta = replayData.meta || {};
    const maxT = meta.max_tick || 100;
    if (currentReplayTick >= maxT) {
      pauseReplay();
      return;
    }
    seekToTick(currentReplayTick + delta);
  }

  function stepBack(delta = 1) {
    if (!replayData) return;
    const meta = replayData.meta || {};
    const minT = meta.min_tick || 1;
    seekToTick(currentReplayTick - delta);
  }

  function playReplay() {
    if (!replayData || isPlaying) return;
    isPlaying = true;
    if (replayBtnPlayPause) {
      replayBtnPlayPause.textContent = '⏸ Pause';
      replayBtnPlayPause.className = 'btn btn-sm btn-warning';
    }

    const baseMs = 250;
    const intervalMs = Math.max(25, Math.floor(baseMs / playbackSpeed));

    replayTimer = setInterval(() => {
      const meta = replayData.meta || {};
      const maxT = meta.max_tick || 100;
      if (currentReplayTick >= maxT) {
        pauseReplay();
      } else {
        stepForward(1);
      }
    }, intervalMs);
  }

  function pauseReplay() {
    isPlaying = false;
    if (replayTimer) {
      clearInterval(replayTimer);
      replayTimer = null;
    }
    if (replayBtnPlayPause) {
      replayBtnPlayPause.textContent = '▶ Play';
      replayBtnPlayPause.className = 'btn btn-sm btn-primary';
    }
  }

  function togglePlayPause() {
    if (isPlaying) pauseReplay();
    else playReplay();
  }

  // Playback Control Event Listeners
  if (replayBtnPlayPause) {
    replayBtnPlayPause.addEventListener('click', togglePlayPause);
  }
  if (replayBtnStepBack) {
    replayBtnStepBack.addEventListener('click', () => {
      pauseReplay();
      stepBack(1);
    });
  }
  if (replayBtnStepForward) {
    replayBtnStepForward.addEventListener('click', () => {
      pauseReplay();
      stepForward(1);
    });
  }
  if (replayScrubSlider) {
    replayScrubSlider.addEventListener('input', (ev) => {
      pauseReplay();
      seekToTick(parseInt(ev.target.value, 10));
    });
  }
  if (replaySpeedSelect) {
    replaySpeedSelect.addEventListener('change', (ev) => {
      playbackSpeed = parseFloat(ev.target.value) || 1.0;
      if (isPlaying) {
        pauseReplay();
        playReplay();
      }
    });
  }

  // Keyboard shortcut listeners (Space, Left/Right arrows)
  window.addEventListener('keydown', (ev) => {
    if (activeTabId !== 'tabReplay') return;
    if (ev.target.tagName === 'INPUT' || ev.target.tagName === 'TEXTAREA' || ev.target.tagName === 'SELECT') {
      return;
    }
    if (ev.code === 'Space') {
      ev.preventDefault();
      togglePlayPause();
    } else if (ev.code === 'ArrowLeft') {
      ev.preventDefault();
      pauseReplay();
      stepBack(1);
    } else if (ev.code === 'ArrowRight') {
      ev.preventDefault();
      pauseReplay();
      stepForward(1);
    }
  });

  // Initial load
  fetchArchives(true);
})();
