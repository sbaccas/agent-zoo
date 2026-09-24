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

  // Initial load
  fetchArchives(true);
})();
