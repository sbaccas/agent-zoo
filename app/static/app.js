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
    switch ((facing || 'north').toLowerCase()) {
      case 'north': return -Math.PI / 2;
      case 'south': return Math.PI / 2;
      case 'east': return 0;
      case 'west': return Math.PI;
      default: return 0;
    }
  }

  // Strike zone, mirroring world.py get_slash_aoe_tiles: a rectangle
  // `rangeTiles` deep and (2 * SLASH_HALF_WIDTH + 1) tiles wide.
  //
  // This math is duplicated from Python. If the two drift, the canvas draws a
  // strike zone in a different place from where damage actually lands, which
  // is worse than not drawing it at all. tests/test_attack_zone.py compares
  // this function against the engine tile-for-tile.
  const SLASH_HALF_WIDTH = 1;

  function getSlashAoeTiles(pos, facing, rangeTiles = 2, width = 100, height = 100, topology = 'torus') {
    if (!pos) return [];
    let dx = 0, dy = 0;
    switch ((facing || 'north').toLowerCase()) {
      case 'north': dy = -1; break;
      case 'south': dy = 1; break;
      case 'east': dx = 1; break;
      case 'west': dx = -1; break;
      default: dy = -1; break;
    }
    const px = -dy;
    const py = dx;

    function wrapOrBound(x, y) {
      if (topology === 'torus') {
        return {
          x: ((x % width) + width) % width,
          y: ((y % height) + height) % height,
        };
      } else {
        if (x >= 0 && x < width && y >= 0 && y < height) {
          return { x, y };
        }
        return null;
      }
    }

    const tiles = [];
    const seen = new Set();
    const maxR = Math.min(rangeTiles, Math.max(width, height));

    for (let dist = 1; dist <= maxR; dist++) {
      for (let lat = -SLASH_HALF_WIDTH; lat <= SLASH_HALF_WIDTH; lat++) {
        const pt = wrapOrBound(pos.x + dx * dist + px * lat, pos.y + dy * dist + py * lat);
        if (pt) {
          const key = `${pt.x},${pt.y}`;
          if (!seen.has(key)) {
            seen.add(key);
            tiles.push({
              x: pt.x,
              y: pt.y,
              isFrontalArc: dist === 1,
              isCorridor: dist > 1,
              distance: dist,
            });
          }
        }
      }
    }

    return tiles;
  }

  // Turn a server hazard zone into tiles the slash renderers understand.
  //
  // The server's tile list is authoritative (it already accounts for bounds and
  // torus wrapping), but the renderers want the isFrontalArc / isCorridor /
  // distance metadata that only the local geometry helper produces. So we
  // recompute the geometry purely to borrow that metadata, and fall back to
  // plain corridor tiles for anything the recomputation misses.
  function hydrateZoneTiles(zone, state) {
    const owner = (state.entities || []).find(e => e.id === zone.owner_id);
    const range = state.species?.cat?.attack_range ?? 2;
    let meta = [];
    if (owner) {
      meta = getSlashAoeTiles(owner.position, zone.facing, range, state.width, state.height, state.topology);
    }
    const byKey = new Map(meta.map(t => [`${t.x},${t.y}`, t]));
    return (zone.tiles || []).map(t => {
      const m = byKey.get(`${t.x},${t.y}`);
      return m ? { ...m, x: t.x, y: t.y }
               : { x: t.x, y: t.y, isFrontalArc: false, isCorridor: true, distance: range };
    });
  }

  // Animation Engine for Slash, Special Effects & Multi-Frame Death
  let activeAnimations = [];
  let animFrameId = null;
  const processedSlashEvents = new Set();
  const dyingEntities = new Map(); // victimId -> { id, type, position, facing, startTime, duration }
  const deadEntitiesSet = new Set(); // Permanently dead mice in current simulation run

  function startAnimationLoop() {
    if (!animFrameId) {
      animFrameId = requestAnimationFrame(animationLoop);
    }
  }

  function animationLoop(timestamp) {
    animFrameId = null;
    const now = performance.now();
    activeAnimations = activeAnimations.filter(anim => (now - anim.startTime) < anim.duration);

    // Clean up expired dying entities
    for (const [id, dying] of dyingEntities.entries()) {
      if ((now - dying.startTime) >= dying.duration) {
        dyingEntities.delete(id);
      }
    }

    if (currentState) {
      renderBoard(currentState, now);
    }

    if (activeAnimations.length > 0 || dyingEntities.size > 0) {
      animFrameId = requestAnimationFrame(animationLoop);
    }
  }

  function enqueueSlashAnimation(anim) {
    anim.startTime = performance.now();
    anim.duration = anim.duration || 600;
    const duplicate = activeAnimations.some(a =>
      a.actorId === anim.actorId && Math.abs(a.startTime - anim.startTime) < 220
    );
    if (duplicate) return;

    // ALWAYS ensure aoeTiles is computed with proper metadata (isFrontalArc, isCorridor, distance)
    const width = currentState?.width || 100;
    const height = currentState?.height || 100;
    const topology = currentState?.topology || 'torus';
    anim.aoeTiles = getSlashAoeTiles(anim.catPos, anim.facing, anim.rangeTiles, width, height, topology);

    activeAnimations.push(anim);
    if (activeAnimations.length > 20) {
      activeAnimations.shift();
    }
    startAnimationLoop();
  }

  function renderAoePreview(state, cat, cellW, cellH) {
    if (!cat || !cat.alive) return;
    const catRange = (settingCatAttackRange && settingCatAttackRange.value)
      ? parseInt(settingCatAttackRange.value, 10)
      : (state.species?.cat?.attack_range ?? 2);
    const aoeTiles = getSlashAoeTiles(cat.position, cat.facing, catRange, state.width, state.height, state.topology);

    const catPx = (cat.position.x + 0.5) * cellW;
    const catPy = (cat.position.y + 0.5) * cellH;
    const angle = getFacingAngle(cat.facing || 'north');
    const coneReach = Math.max(cellW * 1.5, catRange * cellW);
    const coneHalfAngle = 35 * Math.PI / 180;

    // Projected 60-70 degree attack cone sector (matching FOV cone style)
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(catPx, catPy);
    ctx.arc(catPx, catPy, coneReach, angle - coneHalfAngle, angle + coneHalfAngle);
    ctx.closePath();
    ctx.fillStyle = 'rgba(239, 68, 68, 0.15)';
    ctx.fill();
    ctx.strokeStyle = 'rgba(250, 204, 21, 0.75)';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.restore();

    ctx.save();
    for (const t of aoeTiles) {
      const rx = t.x * cellW;
      const ry = t.y * cellH;
      const cx = rx + cellW / 2;
      const cy = ry + cellH / 2;

      // Vibrant crimson tint (stronger on frontal 3-tile arc)
      ctx.fillStyle = t.isFrontalArc ? 'rgba(239, 68, 68, 0.35)' : 'rgba(239, 68, 68, 0.22)';
      ctx.fillRect(rx + 1, ry + 1, cellW - 2, cellH - 2);

      // High-contrast gold / red boundary
      ctx.strokeStyle = t.isFrontalArc ? 'rgba(250, 204, 21, 0.95)' : 'rgba(239, 68, 68, 0.75)';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([3, 2]);
      ctx.strokeRect(rx + 1, ry + 1, cellW - 2, cellH - 2);

      // Subtle targeting reticle / crosshair in center
      ctx.strokeStyle = 'rgba(250, 204, 21, 0.55)';
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      const crossSize = Math.max(2, Math.min(4, cellW * 0.2));
      ctx.beginPath();
      ctx.moveTo(cx - crossSize, cy);
      ctx.lineTo(cx + crossSize, cy);
      ctx.moveTo(cx, cy - crossSize);
      ctx.lineTo(cx, cy + crossSize);
      ctx.stroke();
    }

    // Small icon indicator on primary frontal strike tile
    let dx = 0, dy = 0;
    switch (cat.facing) {
      case 'north': dy = -1; break;
      case 'south': dy = 1; break;
      case 'east': dx = 1; break;
      case 'west': dx = -1; break;
    }
    const frontX = ((cat.position.x + dx) % state.width + state.width) % state.width;
    const frontY = ((cat.position.y + dy) % state.height + state.height) % state.height;
    const tagX = frontX * cellW + cellW / 2;
    const tagY = frontY * cellH + cellH / 2;

    if (cellW >= 14) {
      ctx.fillStyle = 'rgba(220, 38, 38, 0.85)';
      ctx.font = `bold ${Math.max(8, Math.floor(cellW * 0.45))}px system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('⚔️', tagX, tagY);
    }

    ctx.restore();
  }

  // 1. Ground AoE Damage Squares Fill & Delineation (beneath sprites)
  function renderSlashGroundFill(slashes, cellW, cellH) {
    if (!slashes || slashes.length === 0) return;

    for (const slash of slashes) {
      const p = slash.progress;
      const fade = slash.isDynamic ? Math.max(0.4, 1.0 - p * 0.6) : 1.0;

      ctx.save();
      for (const t of slash.aoeTiles) {
        const rx = t.x * cellW;
        const ry = t.y * cellH;

        // A) High-visibility vibrant red/crimson damage fill for the square
        ctx.fillStyle = `rgba(239, 68, 68, ${0.75 * fade})`;
        ctx.fillRect(rx, ry, cellW, cellH);

        // B) Bold, sharp electric yellow / gold border outlining the exact square
        ctx.strokeStyle = `rgba(250, 204, 21, ${0.98 * fade})`;
        ctx.lineWidth = Math.max(2, Math.min(3.5, cellW * 0.18));
        ctx.strokeRect(rx + 0.5, ry + 0.5, cellW - 1, cellH - 1);

        // C) Tactical hazard cross inside the tile square
        ctx.strokeStyle = `rgba(255, 255, 255, ${0.45 * fade})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(rx + 1, ry + 1);
        ctx.lineTo(rx + cellW - 1, ry + cellH - 1);
        ctx.moveTo(rx + cellW - 1, ry + 1);
        ctx.lineTo(rx + 1, ry + cellH - 1);
        ctx.stroke();
      }
      ctx.restore();
    }
  }

  // 1b. Prominent "!" Danger Badges on each active attack tile
  function renderSlashDamageSquareMarkers(slashes, state, cellW, cellH) {
    if (!slashes || slashes.length === 0) return;

    for (const slash of slashes) {
      const p = slash.progress;
      const fade = slash.isDynamic ? Math.max(0.5, 1.0 - p * 0.5) : 1.0;

      ctx.save();
      for (const t of slash.aoeTiles) {
        // User rule: "you dont have to put one on the mouse if it gets hit but i do wnana be able to see where the fucking slash tiles are"
        const hasAliveMouse = (state.entities || []).some(
          e => e.type === 'mouse' && e.alive && e.position.x === t.x && e.position.y === t.y
        );
        if (hasAliveMouse) {
          continue; // Skip putting "!" on top of a living mouse
        }

        const cx = (t.x + 0.5) * cellW;
        const cy = (t.y + 0.5) * cellH;

        // Ensure badge is ALWAYS human-readable (minimum radius 7.5px = 15px diameter)
        const badgeRadius = Math.max(7.5, Math.min(cellW * 0.65, 14));

        // Circular danger badge backing
        ctx.beginPath();
        ctx.arc(cx, cy, badgeRadius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(220, 38, 38, ${fade * 0.98})`;
        ctx.fill();
        ctx.strokeStyle = `rgba(250, 204, 21, ${fade * 1.0})`;
        ctx.lineWidth = 1.8;
        ctx.stroke();

        // The bold "!" character (minimum 11px font so it is sharp & crisp)
        const fontSize = Math.max(11, Math.min(Math.floor(cellW * 0.8), 16));
        ctx.font = `900 ${fontSize}px -apple-system, system-ui, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';

        // Drop shadow for crispness against any background
        ctx.shadowColor = 'rgba(0, 0, 0, 0.9)';
        ctx.shadowBlur = 3;
        ctx.fillStyle = '#ffffff';
        ctx.fillText('!', cx, cy + 0.5);
        ctx.shadowBlur = 0;
      }
      ctx.restore();
    }
  }

  // 2. Multi-Frame Mouse Death Sequence (40–50 frames across 850ms, plays ONCE per death)
  function renderDyingEntities(state, timestamp, cellW, cellH) {
    if (dyingEntities.size === 0) return;

    const fontSize = Math.max(10, Math.floor(cellW * 1.3));

    for (const [victimId, dying] of dyingEntities.entries()) {
      const elapsed = timestamp - dying.startTime;
      if (elapsed >= dying.duration) {
        dyingEntities.delete(victimId);
        continue;
      }

      const dp = Math.min(1.0, elapsed / dying.duration);
      const cx = dying.position.x * cellW + cellW / 2;
      const cy = dying.position.y * cellH + cellH / 2;

      ctx.save();

      // Phase 1: Flinch recoil (first 300ms)
      const flinchOffset = (dp < 0.35)
        ? Math.sin(dp * Math.PI * 6) * Math.max(2, cellW * 0.2) * (1.0 - dp / 0.35)
        : 0;

      ctx.translate(cx + flinchOffset, cy);

      // Phase 2: Tumble rotation & collapse (300ms to 850ms)
      const tumbleAngle = (dp > 0.15) ? Math.min(Math.PI * 0.5, (dp - 0.15) * Math.PI * 0.8) : 0;
      ctx.rotate(tumbleAngle);

      // Squashing down onto the ground as it collapses
      const squashX = 1.0 + dp * 0.25;
      const squashY = Math.max(0.2, 1.0 - dp * 0.65);
      ctx.scale(squashX, squashY);

      // Flash effect: alternating red/white damage flash in first 350ms, then fading ash slate
      let bodyColor = COLOR_MOUSE;
      let bgGlowColor = COLOR_MOUSE_BG;
      const fadeAlpha = Math.max(0, 1.0 - (dp > 0.5 ? (dp - 0.5) / 0.5 : 0));

      if (dp < 0.35) {
        const flashPhase = Math.floor(elapsed / 55) % 2;
        bodyColor = (flashPhase === 0) ? '#ffffff' : '#ef4444';
        bgGlowColor = (flashPhase === 0) ? 'rgba(255, 255, 255, 0.9)' : 'rgba(239, 68, 68, 0.6)';
      } else {
        bodyColor = `rgba(148, 163, 184, ${fadeAlpha})`; // Ash slate
        bgGlowColor = `rgba(100, 116, 139, ${fadeAlpha * 0.3})`;
      }

      // Background disc
      ctx.fillStyle = bgGlowColor;
      const bgRadius = Math.max(5, cellW * 0.7);
      ctx.beginPath();
      ctx.arc(0, 0, bgRadius, 0, Math.PI * 2);
      ctx.fill();

      // Mouse Dead Eyes '✕'
      ctx.fillStyle = bodyColor;
      ctx.font = `bold ${fontSize}px monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      if (dp < 0.25) {
        ctx.fillText('M', 0, 1);
      } else {
        ctx.fillText('✕', 0, 1);
      }

      ctx.restore();

      // Phase 3: Soul ghost rising upward (from 250ms to 850ms)
      if (dp > 0.25) {
        const ghostProg = (dp - 0.25) / 0.75;
        const ghostY = cy - ghostProg * (cellH * 1.8);
        const ghostAlpha = (1.0 - ghostProg) * 0.9;
        const ghostSize = Math.max(10, Math.floor(cellW * 0.8));

        ctx.save();
        ctx.font = `${ghostSize}px system-ui, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = `rgba(224, 242, 254, ${ghostAlpha})`;
        ctx.fillText('👻', cx, ghostY);
        ctx.restore();
      }
    }
  }

  // 3. Prominent Slash Blades: Orange Sweeping Arc & (///) Frontal Claws (drawn ABOVE entities)
  function renderSlashBladesAndClaws(slashes, cellW, cellH) {
    if (!slashes || slashes.length === 0) return;

    for (const slash of slashes) {
      const p = slash.progress;
      const fade = slash.isDynamic ? Math.max(0.35, 1.0 - p * 0.65) : 0.9;
      const angle = getFacingAngle(slash.facing || 'north');

      if (!slash.catPos) continue;

      const catPx = (slash.catPos.x + 0.5) * cellW;
      const catPy = (slash.catPos.y + 0.5) * cellH;

      // ----------------------------------------------------
      // A0) SWEEPING FIERY CONE SECTOR (Shaped like the FOV cone, matching user's request)
      // ----------------------------------------------------
      const coneReach = Math.max(cellW * 1.5, (slash.rangeTiles || 2) * cellW);
      const coneHalfAngle = 35 * Math.PI / 180; // 35° each side (~70° sweeping cone)
      const sweepFactor = slash.isDynamic ? Math.min(1.0, p / 0.45) : 1.0;

      ctx.save();
      ctx.translate(catPx, catPy);
      ctx.rotate(angle);

      // Translucent fiery cone fill matching the FOV cone geometry
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.arc(0, 0, coneReach, -coneHalfAngle * sweepFactor, coneHalfAngle * sweepFactor);
      ctx.closePath();

      const coneGrad = ctx.createRadialGradient(0, 0, cellW * 0.3, 0, 0, coneReach);
      coneGrad.addColorStop(0, `rgba(255, 69, 0, ${0.45 * fade})`);
      coneGrad.addColorStop(0.65, `rgba(255, 140, 0, ${0.22 * fade})`);
      coneGrad.addColorStop(1.0, `rgba(255, 235, 59, ${0.05 * fade})`);
      ctx.fillStyle = coneGrad;
      ctx.fill();

      // Glowing outer rim arc
      ctx.strokeStyle = `rgba(255, 200, 0, ${0.85 * fade})`;
      ctx.lineWidth = Math.max(2, cellW * 0.12);
      ctx.setLineDash([4, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();

      // ----------------------------------------------------
      // A) THE ORANGE ARC: 3 Sweeping Concentric Blade Crescents across Frontal Cone
      // ----------------------------------------------------
      ctx.save();
      ctx.translate(catPx, catPy);
      ctx.rotate(angle); // +X points forward into the frontal cone

      const swipeProg = slash.isDynamic ? Math.min(1.0, p / 0.45) : 1.0;
      const easedSwipe = slash.isDynamic ? (1.0 - Math.pow(1.0 - swipeProg, 2.5)) : 1.0;
      const startAngle = -Math.PI * 0.42; // -75° (left flank)
      const maxSweepSpan = Math.PI * 0.84; // 150° span sweeping across frontal tiles
      const currentEndAngle = startAngle + maxSweepSpan * easedSwipe;

      // 3 Concentric Claw Blade Arcs (Outer, Middle, Inner) scaling with attack reach
      const clawRadii = [
        Math.max(14, cellW * 1.3),  // Inner claw
        Math.max(18, Math.min(cellW * 1.8, coneReach * 0.65)),  // Middle claw
        Math.max(22, Math.min(cellW * 2.4, coneReach * 0.98)),  // Outer claw
      ];

      for (let i = 0; i < clawRadii.length; i++) {
        const r = clawRadii[i];
        const rStart = startAngle - (i * 0.04);
        const rEnd = Math.max(rStart + 0.08, currentEndAngle - (i * 0.03));

        ctx.beginPath();
        ctx.arc(0, 0, r, rStart, rEnd);

        // Outer Blazing Neon Orange Aura
        ctx.strokeStyle = `rgba(255, 85, 0, ${fade * 0.95})`;
        ctx.lineWidth = Math.max(5, cellW * 0.45);
        ctx.lineCap = 'round';
        ctx.stroke();

        // Mid Electric Amber Blade Edge
        ctx.strokeStyle = `rgba(255, 200, 0, ${fade * 0.98})`;
        ctx.lineWidth = Math.max(3, cellW * 0.25);
        ctx.stroke();

        // Inner White-Hot Cutting Laser Core
        ctx.strokeStyle = `rgba(255, 255, 255, ${fade * 1.0})`;
        ctx.lineWidth = Math.max(1.5, cellW * 0.12);
        ctx.stroke();

        // Fiery Cutting Tip Spark Head
        if (slash.isDynamic && swipeProg < 0.98) {
          const tipX = Math.cos(rEnd) * r;
          const tipY = Math.sin(rEnd) * r;
          ctx.fillStyle = '#ffffff';
          ctx.beginPath();
          ctx.arc(tipX, tipY, Math.max(2, cellW * 0.15), 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.restore();

      // ----------------------------------------------------
      // B) THE (///) CLAW GOUGES ACROSS ALL 3 FRONTAL TILES
      // ----------------------------------------------------
      const frontalTiles = slash.aoeTiles.filter(t => t.isFrontalArc);
      for (const t of frontalTiles) {
        const cx = t.x * cellW + cellW / 2;
        const cy = t.y * cellH + cellH / 2;
        const clawSweep = slash.isDynamic ? Math.min(1.0, p / 0.4) : 1.0;

        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(angle);

        // Draw 3 sharp diagonal claw gouges (///)
        const scratchLen = Math.max(12, cellW * 0.9) * clawSweep;
        const clawOffsets = [-0.3 * cellW, 0, 0.3 * cellW];

        for (const cOff of clawOffsets) {
          ctx.beginPath();
          ctx.moveTo(-scratchLen * 0.5 + cOff * 0.5, -scratchLen * 0.45);
          ctx.lineTo(scratchLen * 0.5 + cOff * 0.5, scratchLen * 0.45);

          // Fiery Orange Glow
          ctx.strokeStyle = `rgba(255, 85, 0, ${fade * 0.95})`;
          ctx.lineWidth = Math.max(3.5, cellW * 0.25);
          ctx.lineCap = 'round';
          ctx.stroke();

          // Electric Yellow Midline
          ctx.strokeStyle = `rgba(255, 235, 59, ${fade * 0.98})`;
          ctx.lineWidth = Math.max(2, cellW * 0.15);
          ctx.stroke();

          // White Hot Core
          ctx.strokeStyle = `rgba(255, 255, 255, ${fade * 1.0})`;
          ctx.lineWidth = Math.max(1, cellW * 0.08);
          ctx.stroke();
        }
        ctx.restore();
      }

      // ----------------------------------------------------
      // C) FORWARD PIERCING CORRIDOR BEAM & CHEVRONS (dist >= 2)
      // ----------------------------------------------------
      const corridorTiles = slash.aoeTiles.filter(t => t.isCorridor);
      for (const t of corridorTiles) {
        const cx = t.x * cellW + cellW / 2;
        const cy = t.y * cellH + cellH / 2;
        const dist = t.distance || 2;
        const delay = (dist - 1) * 0.03;
        const beamProg = slash.isDynamic ? Math.min(1.0, Math.max(0, (p - delay) / 0.35)) : 1.0;
        const bAlpha = fade * (beamProg > 0 ? 1 : 0);

        if (bAlpha > 0.01) {
          ctx.save();
          ctx.translate(cx, cy);
          ctx.rotate(angle);

          // Piercing Laser Beam
          ctx.beginPath();
          ctx.moveTo(-cellW * 0.5, 0);
          ctx.lineTo(cellW * 0.5 * beamProg, 0);
          ctx.strokeStyle = `rgba(239, 68, 68, ${bAlpha * 0.95})`;
          ctx.lineWidth = Math.max(3.5, cellW * 0.28);
          ctx.lineCap = 'round';
          ctx.stroke();

          ctx.strokeStyle = `rgba(255, 255, 255, ${bAlpha * 1.0})`;
          ctx.lineWidth = Math.max(1.5, cellW * 0.1);
          ctx.stroke();

          // Directional Chevrons >>>
          const chevSize = Math.max(4, cellW * 0.3);
          ctx.beginPath();
          ctx.moveTo(-chevSize * 0.4, -chevSize * 0.7);
          ctx.lineTo(chevSize * 0.5, 0);
          ctx.lineTo(-chevSize * 0.4, chevSize * 0.7);
          ctx.strokeStyle = `rgba(255, 237, 74, ${bAlpha * 1.0})`;
          ctx.lineWidth = Math.max(2, cellW * 0.12);
          ctx.stroke();

          ctx.restore();
        }
      }
    }
  }

  // 4. Hit Sparks, Impact Shockwave Rings & Floating Combat Text
  function renderSlashImpactAndText(slashes, state, cellW, cellH) {
    if (!slashes || slashes.length === 0) return;

    for (const slash of slashes) {
      const p = slash.progress;
      const fade = slash.isDynamic ? Math.max(0, 1.0 - p * 1.5) : 0.85;

      // Hit sparks & shockwave rings
      if (slash.hits && slash.hits.length > 0) {
        for (const hitId of slash.hits) {
          const victim = (state.entities || []).find(e => e.id === hitId);
          const vPos = victim ? victim.position : null;
          if (!vPos) continue;

          const vx = vPos.x * cellW + cellW / 2;
          const vy = vPos.y * cellH + cellH / 2;

          const impactProgress = slash.isDynamic ? Math.min(1.0, p * 2.0) : 1.0;
          const ringRadius = (cellW * 0.3) + impactProgress * (cellW * 0.8);
          const ringAlpha = slash.isDynamic ? (1.0 - impactProgress) : 0.7;

          ctx.save();
          ctx.strokeStyle = `rgba(239, 68, 68, ${ringAlpha})`;
          ctx.lineWidth = Math.max(2, cellW * 0.12);
          ctx.beginPath();
          ctx.arc(vx, vy, ringRadius, 0, Math.PI * 2);
          ctx.stroke();

          const sparkDist = impactProgress * cellW * 0.85;
          for (let s = 0; s < 8; s++) {
            const sparkAngle = (s * Math.PI) / 4 + 0.25;
            const sx = vx + Math.cos(sparkAngle) * sparkDist;
            const sy = vy + Math.sin(sparkAngle) * sparkDist;
            ctx.fillStyle = (s % 2 === 0) ? `rgba(255, 237, 74, ${ringAlpha})` : `rgba(255, 69, 0, ${ringAlpha})`;
            ctx.beginPath();
            ctx.arc(sx, sy, Math.max(1.5, cellW * 0.08), 0, Math.PI * 2);
            ctx.fill();
          }
          ctx.restore();
        }
      }

      // Floating text "SLASH!"
      if (slash.catPos && fade > 0) {
        const catPx = (slash.catPos.x + 0.5) * cellW;
        const catPy = (slash.catPos.y + 0.5) * cellH;
        const textY = slash.isDynamic
          ? (catPy - cellH * 0.85 - (p * cellH * 0.6))
          : (catPy - cellH * 1.2);
        const text = (slash.hits && slash.hits.length > 0) ? `⚔️ SLASH! (${slash.hits.length})` : '⚔️ SLASH!';
        const fontSize = Math.max(10, Math.floor(cellW * 0.65));

        ctx.save();
        ctx.font = `900 ${fontSize}px system-ui, -apple-system, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';

        ctx.strokeStyle = `rgba(0, 0, 0, ${fade * 0.85})`;
        ctx.lineWidth = 3;
        ctx.strokeText(text, catPx, textY);

        ctx.fillStyle = (slash.hits && slash.hits.length > 0)
          ? `rgba(255, 237, 74, ${fade})`
          : `rgba(255, 255, 255, ${fade})`;
        ctx.fillText(text, catPx, textY);
        ctx.restore();
      }
    }
  }

  function renderBoard(state, timestamp = performance.now()) {
    const width = state.width || 100;
    const height = state.height || 100;
    const cellW = canvas.width / width;
    const cellH = canvas.height / height;

    // 1. Clear background
    ctx.fillStyle = COLOR_BG;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // 2. Grid lines
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

    // 3. Render AoE Strike-Zone Preview for selected cat
    const entities = state.entities || [];
    if (selectedAgentId) {
      const selCat = entities.find(e => e.id === selectedAgentId && e.type === 'cat' && e.alive);
      if (selCat) {
        renderAoePreview(state, selCat, cellW, cellH);
      }
    }

    // 4. Collect all slashes for current tick (stays drawn while on this tick!)
    const currentTickSlashes = [];
    const processedCatIds = new Set();

    if (state.recent_events) {
      for (const ev of state.recent_events) {
        if (ev.event_type === 'slash' && ev.tick === state.tick && ev.position) {
          const active = activeAnimations.find(a => a.actorId === ev.actor_id);
          const details = ev.details || {};
          const facing = details.facing || 'north';
          const rangeTiles = details.range_tiles || 2;
          let aoeTiles = getSlashAoeTiles(ev.position, facing, rangeTiles, state.width, state.height, state.topology);
          if (details.aoe_tiles && Array.isArray(details.aoe_tiles)) {
            const seen = new Set(aoeTiles.map(t => `${t.x},${t.y}`));
            for (const st of details.aoe_tiles) {
              if (!seen.has(`${st.x},${st.y}`)) {
                aoeTiles.push({
                  x: st.x,
                  y: st.y,
                  isFrontalArc: false,
                  isCorridor: true,
                  distance: rangeTiles,
                });
              }
            }
          }

          let progress = 1.0;
          let isDynamic = false;
          if (active) {
            const elapsed = timestamp - active.startTime;
            progress = Math.min(1.0, elapsed / active.duration);
            isDynamic = true;
          }

          currentTickSlashes.push({
            actorId: ev.actor_id,
            catPos: ev.position,
            facing: facing,
            rangeTiles: rangeTiles,
            aoeTiles: aoeTiles,
            hits: details.hit_entities || [],
            progress: progress,
            isDynamic: isDynamic,
          });
          processedCatIds.add(ev.actor_id);
        }
      }
    }

    // Also include any local active dynamic animations not yet reflected in state.recent_events
    for (const anim of activeAnimations) {
      if (!processedCatIds.has(anim.actorId)) {
        const elapsed = timestamp - anim.startTime;
        const progress = Math.min(1.0, elapsed / anim.duration);
        currentTickSlashes.push({
          actorId: anim.actorId,
          catPos: anim.catPos,
          facing: anim.facing,
          rangeTiles: anim.rangeTiles,
          aoeTiles: anim.aoeTiles,
          hits: anim.hits || [],
          progress: progress,
          isDynamic: true,
        });
        processedCatIds.add(anim.actorId);
      }
    }

    // Lingering lethal zones, straight from server state.
    //
    // The event pass above is gated on `ev.tick === state.tick`, so a slash
    // vanished from the board the moment the tick advanced even though the
    // zone stays lethal afterwards. Anything still dangerous is drawn here so
    // the danger markers last exactly as long as the danger does. Actors
    // already drawn by the event or animation passes are skipped, so the swing
    // keeps its animation on the tick it lands.
    for (const zone of (state.hazard_zones || [])) {
      if (processedCatIds.has(zone.owner_id)) continue;
      const owner = (state.entities || []).find(e => e.id === zone.owner_id);
      currentTickSlashes.push({
        actorId: zone.owner_id,
        catPos: owner ? owner.position : (zone.tiles && zone.tiles[0]) || { x: 0, y: 0 },
        facing: zone.facing,
        rangeTiles: state.species?.cat?.attack_range ?? 2,
        aoeTiles: hydrateZoneTiles(zone, state),
        hits: [],
        progress: 1.0,
        isDynamic: false,
        lingering: true,
        ticksRemaining: Math.max(0, zone.lethal_until_tick - state.tick + 1),
      });
      processedCatIds.add(zone.owner_id);
    }

    // 5. Render Slash Ground AoE Glow Fill (beneath sprites)
    renderSlashGroundFill(currentTickSlashes, cellW, cellH);

    // 6. Render Recent Capture Marker (only on the exact tick it occurs)
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

    // 7. Render Vision Cones & Detection Fields (underneath sprites)
    const aliveMice = entities.filter(e => e.type === 'mouse' && e.alive && !dyingEntities.has(e.id));
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
      if (!e.alive || dyingEntities.has(e.id)) continue;

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

    // 8. Render Living Entities
    const fontSize = Math.max(10, Math.floor(cellW * 1.3));

    for (let i = 0; i < entities.length; i++) {
      const e = entities[i];
      if (!e.alive || dyingEntities.has(e.id)) continue;

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

    // 9. Render Dying Entities (Multi-Frame Death Animation across ~50 frames)
    renderDyingEntities(state, timestamp, cellW, cellH);

    // 10. Render Slash Blade Arcs (Orange Arc & (///) Frontal Claws) ABOVE entities
    renderSlashBladesAndClaws(currentTickSlashes, cellW, cellH);

    // 11. Render Active Damage Square "!" Badges (ABOVE CLAWS & SPRITES, skipping living mice)
    renderSlashDamageSquareMarkers(currentTickSlashes, state, cellW, cellH);

    // 12. Render Floating Combat Text ("SLASH!", "CAPTURE!") and Hit Sparks
    renderSlashImpactAndText(currentTickSlashes, state, cellW, cellH);
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

  /**
   * Point the tick-rate dropdown at the rate the simulation is actually using.
   *
   * The dropdown used to be write-only: app.js read speedSelect.value when the
   * user changed it and never wrote it back, so the control displayed whichever
   * <option> carried the hardcoded `selected` attribute regardless of the real
   * rate. On a server already running at 5 ticks/sec the UI read "10 ticks /
   * sec", and reloading the page did not correct it.
   *
   * Exposed on `window` so it can be tested in isolation.
   */
  function syncSpeedSelect(selectEl, rate) {
    if (!selectEl) return;
    if (typeof rate !== 'number' || !isFinite(rate) || rate <= 0) return;

    // Never yank the value out from under someone who has the menu open.
    if (typeof document !== 'undefined' && document.activeElement === selectEl) return;

    const wanted = String(rate);
    if (selectEl.value === wanted) return;

    const options = Array.from(selectEl.options);
    const match = options.find((o) => o.value === wanted);

    if (!match) {
      // The rate was set out of band (direct API call, another browser tab, a
      // config default). Showing the nearest preset would be a lie, so surface
      // the real value instead.
      const opt = document.createElement('option');
      opt.value = wanted;
      opt.textContent = rate + ' ticks / sec (custom)';
      opt.dataset.customRate = 'true';
      selectEl.appendChild(opt);
    } else {
      // Drop any stale custom option now that a preset is in effect again.
      options
        .filter((o) => o.dataset && o.dataset.customRate === 'true')
        .forEach((o) => o.remove());
    }

    selectEl.value = wanted;
  }
  window.syncSpeedSelect = syncSpeedSelect;

  function updateUI(state) {
    const prevState = currentState;
    currentState = state;

    // Keep the tick-rate control honest: the server is the source of truth.
    syncSpeedSelect(speedSelect, state.tick_rate);

    // Detect new slash events to animate
    if (state.recent_events) {
      for (const ev of state.recent_events) {
        if (ev.event_type === 'slash') {
          const evKey = `${ev.tick}_${ev.actor_id}_slash`;
          if (!processedSlashEvents.has(evKey)) {
            processedSlashEvents.add(evKey);
            if (processedSlashEvents.size > 200) {
              const first = processedSlashEvents.values().next().value;
              processedSlashEvents.delete(first);
            }
            const details = ev.details || {};
            const facing = details.facing || 'north';
            const rangeTiles = details.range_tiles || 2;
            const aoeTiles = getSlashAoeTiles(ev.position, facing, rangeTiles, state.width, state.height, state.topology);
            enqueueSlashAnimation({
              id: evKey,
              type: 'slash',
              actorId: ev.actor_id,
              catPos: ev.position,
              facing: facing,
              rangeTiles: rangeTiles,
              aoeTiles: aoeTiles,
              hits: details.hit_entities || [],
              duration: 600,
            });
          }
        }
      }
    }

    // Detect mouse captures & deaths to trigger multi-frame death animation (~50 frames across 850ms)
    // Plays strictly ONCE per mouse death; ignores already dead mice or repeated polls when stopped
    if (state.recent_events) {
      for (const ev of state.recent_events) {
        if (ev.event_type === 'capture' && ev.target_id) {
          if (!deadEntitiesSet.has(ev.target_id) && !dyingEntities.has(ev.target_id) && ev.tick === state.tick) {
            deadEntitiesSet.add(ev.target_id);
            const victimPos = ev.position || { x: 0, y: 0 };
            dyingEntities.set(ev.target_id, {
              id: ev.target_id,
              type: 'mouse',
              position: { x: victimPos.x, y: victimPos.y },
              facing: (ev.details?.facing || 'east').toLowerCase(),
              startTime: performance.now(),
              duration: 850,
            });
            startAnimationLoop();
          }
        }
      }
    }

    // Also detect if any living mouse from previous state died
    if (prevState && prevState.entities) {
      for (const oldEnt of prevState.entities) {
        if (oldEnt.type === 'mouse' && oldEnt.alive) {
          const newEnt = (state.entities || []).find(e => e.id === oldEnt.id);
          if (!newEnt || !newEnt.alive) {
            if (!deadEntitiesSet.has(oldEnt.id) && !dyingEntities.has(oldEnt.id)) {
              deadEntitiesSet.add(oldEnt.id);
              dyingEntities.set(oldEnt.id, {
                id: oldEnt.id,
                type: 'mouse',
                position: { x: oldEnt.position.x, y: oldEnt.position.y },
                facing: (oldEnt.facing || 'east').toLowerCase(),
                startTime: performance.now(),
                duration: 850,
              });
              startAnimationLoop();
            }
          }
        }
      }
    }

    // Step Back / Rewind support: If an entity is alive again, un-mark it so it can die again if stepped forward
    if (state.entities) {
      for (const ent of state.entities) {
        if (ent.type === 'mouse' && ent.alive) {
          deadEntitiesSet.delete(ent.id);
          dyingEntities.delete(ent.id);
        }
      }
    }

    // If simulation reset (tick 0), clear all animation tracking sets
    if (state.tick === 0) {
      deadEntitiesSet.clear();
      dyingEntities.clear();
      activeAnimations = [];
      processedSlashEvents.clear();
    }

    // When rewinding ticks, clean up processedSlashEvents for ticks in the future
    for (const key of processedSlashEvents) {
      const t = parseInt(key.split('_')[0], 10);
      if (!isNaN(t) && t > state.tick) {
        processedSlashEvents.delete(key);
      }
    }

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

    // Instant client-side slash animation for zero-latency manual feedback
    if (action === 'attack' && currentState) {
      const cat = (currentState.entities || []).find(e => e.id === selectedAgentId && e.type === 'cat' && e.alive);
      if (cat) {
        const range = (settingCatAttackRange && settingCatAttackRange.value)
          ? parseInt(settingCatAttackRange.value, 10)
          : (currentState.species?.cat?.attack_range ?? 2);
        const aoe = getSlashAoeTiles(cat.position, cat.facing, range, currentState.width, currentState.height, currentState.topology);
        enqueueSlashAnimation({
          id: `manual_${Date.now()}`,
          type: 'slash',
          actorId: cat.id,
          catPos: { x: cat.position.x, y: cat.position.y },
          facing: cat.facing,
          rangeTiles: range,
          aoeTiles: aoe,
          hits: [],
          duration: 600,
        });
      }
    }

    const res = await apiCall(`/api/simulations/${currentSimId}/agents/${selectedAgentId}/actions?step=true`, 'POST', { action: action });
    if (res && res.accepted) {
      // If action resulted in captures, register victims for death animation immediately
      if (res.affected_entities && res.affected_entities.length > 0) {
        for (const victimId of res.affected_entities) {
          const victim = (currentState?.entities || []).find(e => e.id === victimId);
          if (victim && !deadEntitiesSet.has(victimId) && !dyingEntities.has(victimId)) {
            deadEntitiesSet.add(victimId);
            dyingEntities.set(victimId, {
              id: victimId,
              type: 'mouse',
              position: { x: victim.position.x, y: victim.position.y },
              facing: (victim.facing || 'east').toLowerCase(),
              startTime: performance.now(),
              duration: 850,
            });
            startAnimationLoop();
          }
        }
      }

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
        if (settingCatVision) settingCatVision.value = data.cat.vision_radius ?? 10;
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
    if (settingCatAttackRange) settingCatAttackRange.value = 8;
    if (settingCatAttackCd) settingCatAttackCd.value = 3;
    if (settingCatLungeCd) settingCatLungeCd.value = 2;
    if (settingCatVision) settingCatVision.value = 10;
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
      vision_radius: parseIntDefault(settingCatVision?.value, 10, 1),
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

  // Real-time canvas AoE preview update when user edits attack range in settings
  if (settingCatAttackRange) {
    settingCatAttackRange.addEventListener('input', () => {
      if (currentState && selectedAgentId) {
        renderBoard(currentState);
      }
    });
  }

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
  btnReset.addEventListener('click', () => {
    deadEntitiesSet.clear();
    dyingEntities.clear();
    activeAnimations = [];
    processedSlashEvents.clear();
    triggerAction(`/api/simulations/${currentSimId}/reset`);
  });

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

