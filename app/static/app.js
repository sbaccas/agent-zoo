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
  const settingCatVisionRear = document.getElementById('settingCatVisionRear');
  const settingCatVisionHalfWidth = document.getElementById('settingCatVisionHalfWidth');
  const settingCatAimAssist = document.getElementById('settingCatAimAssist');
  const settingCatWeight = document.getElementById('settingCatWeight');

  const settingMouseMove = document.getElementById('settingMouseMove');
  const settingMouseVision = document.getElementById('settingMouseVision');
  const settingMouseWeight = document.getElementById('settingMouseWeight');

  const settingCoachPopulation = document.getElementById('settingCoachPopulation');
  const settingCoachCoverageMap = document.getElementById('settingCoachCoverageMap');
  const settingActorOwnTrail = document.getElementById('settingActorOwnTrail');

  let currentSimId = 'active';
  let pollTimeout = null;
  let currentState = null;
  let selectedAgentId = null;
  let selectedAgentMode = 'auto';
  let isFetchingObs = false;


  // Light mode colors
  const COLOR_BG = '#ffffff';
  const COLOR_GRID_LINE = 'rgba(203, 213, 225, 0.4)';
  const COLOR_CAT = '#dc2626';       // Crimson Red (NPC Cats)
  const COLOR_AGENT_CAT = '#9333ea'; // Electric Royal Purple (Agent Cats)
  const COLOR_MOUSE = '#0284c7';     // Sky Blue
  const COLOR_CAT_BG = 'rgba(220, 38, 38, 0.12)';
  const COLOR_AGENT_CAT_BG = 'rgba(147, 51, 234, 0.22)';
  const COLOR_MOUSE_BG = 'rgba(2, 132, 199, 0.12)';
  const COLOR_POINTER_CAT = '#b91c1c';
  const COLOR_POINTER_AGENT_CAT = '#a855f7';
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
    const range = state.species?.cat?.attack_range ?? 4;
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
  const catHpAnim = new Map(); // catId -> { currentHp, targetHp }

  function hasActiveHpAnimations() {
    for (const anim of catHpAnim.values()) {
      if (Math.abs(anim.currentHp - anim.targetHp) > 0.5) return true;
    }
    return false;
  }

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

    if (activeAnimations.length > 0 || dyingEntities.size > 0 || hasActiveHpAnimations()) {
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
      : (state.species?.cat?.attack_range ?? 4);
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
        // User rule: Render "!" indicator on empty slash tiles so the slash coverage remains visible
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

  function renderMapFeatures(state, cellW, cellH) {
    const mf = state.map_features;
    if (!mf) return;

    // 1. Buildings (Floor, Walls, Doors, Name)
    if (Array.isArray(mf.buildings)) {
      for (const b of mf.buildings) {
        const bx = b.x * cellW;
        const by = b.y * cellH;
        const bw = b.w * cellW;
        const bh = b.h * cellH;

        // Interior floor fill
        ctx.fillStyle = '#f8fafc';
        ctx.fillRect(bx, by, bw, bh);

        // Walls
        if (Array.isArray(b.walls)) {
          ctx.fillStyle = '#334155';
          for (const w of b.walls) {
            ctx.fillRect(w.x * cellW, w.y * cellH, cellW, cellH);
            ctx.strokeStyle = '#1e293b';
            ctx.lineWidth = 0.5;
            ctx.strokeRect(w.x * cellW, w.y * cellH, cellW, cellH);
          }
        }

        // Doors (open pathways)
        if (Array.isArray(b.doors)) {
          for (const d of b.doors) {
            ctx.fillStyle = '#fef3c7';
            ctx.fillRect(d.x * cellW, d.y * cellH, cellW, cellH);
            ctx.strokeStyle = '#d97706';
            ctx.lineWidth = 1;
            ctx.setLineDash([2, 2]);
            ctx.strokeRect(d.x * cellW + 1, d.y * cellH + 1, cellW - 2, cellH - 2);
            ctx.setLineDash([]);
          }
        }

        // Building Name label
        if (b.name) {
          ctx.save();
          ctx.fillStyle = '#475569';
          ctx.font = `bold ${Math.max(9, Math.floor(cellW * 0.9))}px -apple-system, BlinkMacSystemFont, sans-serif`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          const labelX = bx + bw / 2;
          const labelY = by + Math.max(12, cellH * 1.5);
          ctx.fillText(b.name, labelX, labelY);
          ctx.restore();
        }
      }
    }

    // 2. Trees (each tree is 2x2 tiles)
    if (Array.isArray(mf.trees)) {
      for (const t of mf.trees) {
        const tw = (t.w || 2) * cellW;
        const th = (t.h || 2) * cellH;
        const cx = t.x * cellW + tw / 2;
        const cy = t.y * cellH + th / 2;
        const radius = Math.min(tw, th) * 0.46;

        ctx.save();
        // Drop shadow
        ctx.beginPath();
        ctx.arc(cx + 1, cy + 1, radius, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(0, 0, 0, 0.12)';
        ctx.fill();

        // Canopy base
        ctx.beginPath();
        ctx.arc(cx, cy, radius, 0, Math.PI * 2);
        ctx.fillStyle = '#166534';
        ctx.fill();
        ctx.strokeStyle = '#14532d';
        ctx.lineWidth = 1;
        ctx.stroke();

        // Canopy highlight
        ctx.beginPath();
        ctx.arc(cx - radius * 0.18, cy - radius * 0.18, radius * 0.62, 0, Math.PI * 2);
        ctx.fillStyle = '#22c55e';
        ctx.fill();

        // Center trunk
        ctx.beginPath();
        ctx.arc(cx, cy, Math.max(2, radius * 0.2), 0, Math.PI * 2);
        ctx.fillStyle = '#78350f';
        ctx.fill();

        ctx.restore();
      }
    }

    // 3. Cheese
    if (Array.isArray(mf.cheese)) {
      for (const ch of mf.cheese) {
        const cx = ch.x * cellW + cellW / 2;
        const cy = ch.y * cellH + cellH / 2;
        const r = Math.max(4, cellW * 0.45);

        ctx.save();
        // Warm cheese glow
        ctx.beginPath();
        ctx.arc(cx, cy, r * 1.4, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(250, 204, 21, 0.35)';
        ctx.fill();

        // Cheese wedge triangle
        ctx.beginPath();
        ctx.moveTo(cx - r * 0.8, cy + r * 0.7);
        ctx.lineTo(cx + r * 0.8, cy + r * 0.7);
        ctx.lineTo(cx, cy - r * 0.85);
        ctx.closePath();
        ctx.fillStyle = '#eab308';
        ctx.fill();
        ctx.strokeStyle = '#ca8a04';
        ctx.lineWidth = 1;
        ctx.stroke();

        // Cheese holes
        ctx.fillStyle = '#a16207';
        ctx.beginPath();
        ctx.arc(cx - r * 0.25, cy + r * 0.2, Math.max(1, r * 0.16), 0, Math.PI * 2);
        ctx.arc(cx + r * 0.28, cy + r * 0.28, Math.max(1, r * 0.13), 0, Math.PI * 2);
        ctx.arc(cx, cy - r * 0.2, Math.max(1, r * 0.14), 0, Math.PI * 2);
        ctx.fill();

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

    // 2.5 Render Map Features (Buildings, Walls, Doors, Trees, Cheese)
    renderMapFeatures(state, cellW, cellH);

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
        rangeTiles: state.species?.cat?.attack_range ?? 4,
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
        // Draw the viewport as the engine actually computes it: a box *around*
        // the cat -- forward, rear and lateral reach all read live from the
        // species config, the same three numbers `World.get_fov_tiles` is
        // called with. They are deliberately NOT hardcoded here: all three are
        // editable from the settings tab at runtime, and an overlay that kept
        // its own copy would quietly disagree with the engine the moment
        // anyone touched a dial.
        //
        // This started life as a `ctx.arc` pie sector spanning angle +/- PI/4,
        // which matched nothing in the engine -- it was a decorative 90 degree
        // wedge. Watching the cat ignore a mouse sitting plainly inside the red
        // wedge was misleading: the mouse was outside the real tile set. The
        // fallbacks below mirror World.FOV_* only for the case where species
        // config has not arrived yet.
        //
        // A tapered version was tried and reverted, which is why this is a
        // single rect: the shape is uniform with distance, so one rect is exact.
        const HALF_WIDTH = state.species?.cat?.vision_half_width ?? 3;
        const REAR_DEPTH = state.species?.cat?.vision_rear ?? 2;
        const [fdx, fdy] = { north: [0, -1], south: [0, 1], east: [1, 0], west: [-1, 0] }[e.facing || 'north'];
        const pdx = -fdy, pdy = fdx;
        const corners = [
          [-REAR_DEPTH, -HALF_WIDTH], [-REAR_DEPTH, HALF_WIDTH],
          [catVision, -HALF_WIDTH], [catVision, HALF_WIDTH],
        ].map(([f, l]) => [e.position.x + fdx * f + pdx * l, e.position.y + fdy * f + pdy * l]);
        const minX = Math.min(...corners.map(c => c[0]));
        const maxX = Math.max(...corners.map(c => c[0]));
        const minY = Math.min(...corners.map(c => c[1]));
        const maxY = Math.max(...corners.map(c => c[1]));
        ctx.save();
        ctx.beginPath();
        ctx.rect(minX * cellW, minY * cellH, (maxX - minX + 1) * cellW, (maxY - minY + 1) * cellH);
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
      const isAgentCat = isCat && Boolean(e.is_agent || e.id === 'cat-1' || e.id === 'cat-2' || (e.agent_name && e.agent_name.length > 0));
      const angle = getFacingAngle(e.facing || 'north');

      ctx.save();
      ctx.translate(cx, cy);

      // Subtle background glow disc
      ctx.fillStyle = isCat ? (isAgentCat ? COLOR_AGENT_CAT_BG : COLOR_CAT_BG) : COLOR_MOUSE_BG;
      const bgRadius = isCat ? Math.max(7, cellW * 0.9) : Math.max(5, cellW * 0.7);
      ctx.beginPath();
      ctx.arc(0, 0, bgRadius, 0, Math.PI * 2);
      ctx.fill();

      // Directional pointer / nose indicator
      ctx.save();
      ctx.rotate(angle);
      ctx.fillStyle = isCat ? (isAgentCat ? COLOR_POINTER_AGENT_CAT : COLOR_POINTER_CAT) : COLOR_POINTER_MOUSE;
      const pointerDist = bgRadius + 2;
      ctx.beginPath();
      ctx.moveTo(pointerDist + 3, 0);
      ctx.lineTo(pointerDist - 3, -3);
      ctx.lineTo(pointerDist - 3, 3);
      ctx.closePath();
      ctx.fill();
      ctx.restore();

      // Draw ASCII Character 'C' or 'M'
      ctx.fillStyle = isCat ? (isAgentCat ? COLOR_AGENT_CAT : COLOR_CAT) : COLOR_MOUSE;
      ctx.font = `bold ${fontSize}px monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(isCat ? 'C' : 'M', 0, 1);

      // Render HP indicator for Cats (ratio out of 100, 3 slashes to eliminate)
      if (isCat && (e.max_hp > 1 || e.hp !== undefined)) {
        const hp = (e.hp !== undefined) ? e.hp : 100;
        const maxHp = e.max_hp || 100;

        let anim = catHpAnim.get(e.id);
        if (!anim) {
          anim = { currentHp: hp, targetHp: hp };
          catHpAnim.set(e.id, anim);
        }
        anim.targetHp = hp;
        if (Math.abs(anim.currentHp - anim.targetHp) > 0.5) {
          anim.currentHp += (anim.targetHp - anim.currentHp) * 0.2;
          startAnimationLoop();
        } else {
          anim.currentHp = anim.targetHp;
        }

        const displayRatio = Math.max(0, Math.min(1, anim.currentHp / maxHp));
        const actualRatio = Math.max(0, Math.min(1, hp / maxHp));

        // Scaled cleanly to fit comfortably above the 'C' within its own cell
        const barW = Math.max(6, Math.min(Math.round(cellW * 0.85), Math.round(bgRadius * 1.25)));
        const barH = Math.max(1.5, Math.min(2.5, Math.round(cellH * 0.18)));
        const barX = -barW / 2;
        const barY = -bgRadius - barH - 1;

        // Dark background trough
        ctx.fillStyle = 'rgba(15, 23, 42, 0.75)';
        ctx.fillRect(barX, barY, barW, barH);

        // Trailing damage fill (shows drained HP smoothly catching up)
        if (displayRatio > actualRatio) {
          ctx.fillStyle = 'rgba(239, 68, 68, 0.7)';
          ctx.fillRect(barX + barW * actualRatio, barY, barW * (displayRatio - actualRatio), barH);
        }

        // Main health fill: Green (3 hits left) > Amber (2 hits left) > Red (1 hit left)
        const fillRatio = Math.min(displayRatio, actualRatio);
        ctx.fillStyle = fillRatio >= 0.85 ? '#22c55e' : (fillRatio >= 0.45 ? '#eab308' : '#ef4444');
        ctx.fillRect(barX, barY, barW * fillRatio, barH);

        // Crisp thin border
        ctx.strokeStyle = 'rgba(0, 0, 0, 0.6)';
        ctx.lineWidth = 0.5;
        ctx.strokeRect(barX, barY, barW, barH);

        // Segment divider lines for 33/100 slash marks (drawn when bar is wide enough)
        if (barW >= 9) {
          ctx.strokeStyle = 'rgba(0, 0, 0, 0.45)';
          ctx.lineWidth = 0.5;
          ctx.beginPath();
          const d1 = Math.round(barX + barW * 0.33);
          const d2 = Math.round(barX + barW * 0.66);
          ctx.moveTo(d1, barY);
          ctx.lineTo(d1, barY + barH);
          ctx.moveTo(d2, barY);
          ctx.lineTo(d2, barY + barH);
          ctx.stroke();
        }
      }

      // Overhead Name Label for Agent Cats (e.g. "cat-1", "cat-2")
      if (isAgentCat) {
        const labelText = e.agent_name || e.id;
        const labelFontSize = Math.max(8, Math.min(11, Math.floor(cellW * 0.38)));
        ctx.save();
        ctx.font = `bold ${labelFontSize}px system-ui, -apple-system, sans-serif`;
        const textMetrics = ctx.measureText(labelText);
        const textW = textMetrics.width;
        const padX = 3.5;
        const padY = 1.5;
        const pillW = textW + padX * 2;
        const pillH = labelFontSize + padY * 2;

        const hpOffset = (isCat && (e.max_hp > 1 || e.hp !== undefined)) ? (Math.max(1.5, Math.min(2.5, Math.round(cellH * 0.18))) + 3) : 0;
        const pillY = -bgRadius - hpOffset - pillH - 2;
        const pillX = -pillW / 2;

        ctx.beginPath();
        if (ctx.roundRect) {
          ctx.roundRect(pillX, pillY, pillW, pillH, 2.5);
        } else {
          ctx.rect(pillX, pillY, pillW, pillH);
        }
        ctx.fillStyle = 'rgba(15, 23, 42, 0.88)';
        ctx.fill();
        ctx.strokeStyle = COLOR_AGENT_CAT;
        ctx.lineWidth = 1;
        ctx.stroke();

        ctx.fillStyle = '#ffffff';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(labelText, 0, pillY + pillH / 2 + 0.5);
        ctx.restore();
      }

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

    // 13. Render Pending Repositions (scheduled for next tick)
    if (state.pending_repositions) {
      for (const [entId, pos] of Object.entries(state.pending_repositions)) {
        const ent = (state.entities || []).find(e => e.id === entId);
        if (!ent || !ent.alive) continue;
        const fromX = (ent.position.x + 0.5) * cellW;
        const fromY = (ent.position.y + 0.5) * cellH;
        const toX = (pos.x + 0.5) * cellW;
        const toY = (pos.y + 0.5) * cellH;
        const isCat = ent.type === 'cat';

        ctx.save();
        // Dashed trajectory vector
        ctx.beginPath();
        ctx.moveTo(fromX, fromY);
        ctx.lineTo(toX, toY);
        ctx.strokeStyle = isCat ? 'rgba(220, 38, 38, 0.75)' : 'rgba(2, 132, 199, 0.75)';
        ctx.lineWidth = 2;
        ctx.setLineDash([4, 4]);
        ctx.stroke();

        // Staged destination tile highlight
        ctx.fillStyle = isCat ? 'rgba(220, 38, 38, 0.2)' : 'rgba(2, 132, 199, 0.2)';
        ctx.fillRect(pos.x * cellW, pos.y * cellH, cellW, cellH);
        ctx.strokeStyle = isCat ? '#dc2626' : '#0284c7';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([]);
        ctx.strokeRect(pos.x * cellW, pos.y * cellH, cellW, cellH);

        // Ghost entity disc
        const ghostR = Math.max(6, cellW * 0.75);
        ctx.beginPath();
        ctx.arc(toX, toY, ghostR, 0, Math.PI * 2);
        ctx.fillStyle = isCat ? 'rgba(220, 38, 38, 0.35)' : 'rgba(2, 132, 199, 0.35)';
        ctx.fill();
        ctx.fillStyle = '#ffffff';
        ctx.font = `bold ${Math.max(9, Math.floor(ghostR * 1.2))}px monospace`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(isCat ? 'C' : 'M', toX, toY);

        // Pin badge
        ctx.fillStyle = '#0f172a';
        ctx.font = 'bold 9px -apple-system, BlinkMacSystemFont, sans-serif';
        ctx.fillText('📌 Next Tick', toX, toY - ghostR - 4);
        ctx.restore();
      }
    }

    // 14. Render Active Drag Overlay (in-flight cursor preview & tile target)
    if (isDraggingAsset && dragCandidate) {
      const fromX = (dragCandidate.position.x + 0.5) * cellW;
      const fromY = (dragCandidate.position.y + 0.5) * cellH;
      const targetGridX = dragCurrentGrid.x;
      const targetGridY = dragCurrentGrid.y;
      const toCellX = targetGridX * cellW;
      const toCellY = targetGridY * cellH;
      const blocked = isCellBlocked(state, targetGridX, targetGridY);

      ctx.save();
      // Dashed connector line from original position to target cell
      ctx.beginPath();
      ctx.moveTo(fromX, fromY);
      ctx.lineTo(toCellX + cellW / 2, toCellY + cellH / 2);
      ctx.strokeStyle = blocked ? 'rgba(239, 68, 68, 0.85)' : 'rgba(34, 197, 94, 0.85)';
      ctx.lineWidth = 2.5;
      ctx.setLineDash([5, 4]);
      ctx.stroke();

      // Destination tile highlight
      ctx.fillStyle = blocked ? 'rgba(239, 68, 68, 0.3)' : 'rgba(34, 197, 94, 0.25)';
      ctx.fillRect(toCellX, toCellY, cellW, cellH);
      ctx.strokeStyle = blocked ? '#ef4444' : '#22c55e';
      ctx.lineWidth = 2;
      ctx.setLineDash([]);
      ctx.strokeRect(toCellX, toCellY, cellW, cellH);

      // Floating cursor avatar following mouse
      const curX = dragCurrentPixel.x;
      const curY = dragCurrentPixel.y;
      const isCat = dragCandidate.type === 'cat';
      const avatarR = Math.max(12, Math.floor(cellW * 1.25));

      // Drop shadow
      ctx.shadowColor = 'rgba(0, 0, 0, 0.4)';
      ctx.shadowBlur = 10;
      ctx.shadowOffsetY = 4;

      // Disc
      ctx.beginPath();
      ctx.arc(curX, curY - 16, avatarR, 0, Math.PI * 2);
      ctx.fillStyle = isCat ? '#dc2626' : '#0284c7';
      ctx.fill();

      // Letter
      ctx.shadowColor = 'transparent';
      ctx.fillStyle = '#ffffff';
      ctx.font = `bold ${Math.max(12, Math.floor(avatarR * 1.1))}px monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(isCat ? 'C' : 'M', curX, curY - 16);

      // Tooltip badge
      const labelText = blocked
        ? `⛔ Blocked (${targetGridX}, ${targetGridY})`
        : `📍 Drop at (${targetGridX}, ${targetGridY}) → Next Tick`;
      ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, sans-serif';
      const textWidth = ctx.measureText(labelText).width;
      const padX = 8, badgeH = 20;
      const badgeX = curX - textWidth / 2 - padX;
      const badgeY = curY + 10;

      ctx.fillStyle = blocked ? 'rgba(185, 28, 28, 0.94)' : 'rgba(15, 23, 42, 0.9)';
      ctx.beginPath();
      if (typeof ctx.roundRect === 'function') {
        ctx.roundRect(badgeX, badgeY, textWidth + padX * 2, badgeH, 6);
      } else {
        ctx.rect(badgeX, badgeY, textWidth + padX * 2, badgeH);
      }
      ctx.fill();

      ctx.fillStyle = '#ffffff';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(labelText, curX, badgeY + badgeH / 2);

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
      catHpAnim.clear();
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
    const hpStr = (self.hp !== undefined) ? ` | ❤️ HP: ${self.hp}/${self.max_hp || 3}` : '';
    hudAgentId.textContent = `${self.id} (${self.type.toUpperCase()}) @ (${self.position.x}, ${self.position.y})`;
    if (isLocked) {
      hudAgentFacing.textContent = `Facing: ⬆ ${self.facing.toUpperCase()}${hpStr} | ⏳ LOCKED (${self.action_cooldown}t: ${self.locked_action || 'moving'})`;
    } else {
      hudAgentFacing.textContent = `Facing: ⬆ ${self.facing.toUpperCase()}${hpStr} | ⚡ READY`;
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
        const hpTag = (ent.hp !== undefined) ? ` [HP: ${ent.hp}/${ent.max_hp || 3}]` : '';

        item.textContent = `${isCat ? '🐱 CAT' : '🐭 MOUSE'}: ${ent.id} @ (${ent.position.x}, ${ent.position.y}) [Dist: ${dist}]${hpTag}`;
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
          : (currentState.species?.cat?.attack_range ?? 4);
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

  // Helper to check if a tile is blocked by obstacles or out of bounds
  function isCellBlocked(state, x, y) {
    if (!state) return false;
    const w = state.width || 100;
    const h = state.height || 100;
    if (x < 0 || x >= w || y < 0 || y >= h) {
      return true;
    }
    const mf = state.map_features;
    if (!mf) return false;
    if (Array.isArray(mf.buildings)) {
      for (const b of mf.buildings) {
        if (Array.isArray(b.walls)) {
          for (const wall of b.walls) {
            if (wall.x === x && wall.y === y) return true;
          }
        }
      }
    }
    if (Array.isArray(mf.trees)) {
      for (const t of mf.trees) {
        const tw = t.w || 2;
        const th = t.h || 2;
        if (x >= t.x && x < t.x + tw && y >= t.y && y < t.y + th) {
          return true;
        }
      }
    }
    return false;
  }

  // Interactive Asset Drag & Drop Repositioning
  let dragCandidate = null;
  let isDraggingAsset = false;
  let dragStartPos = { x: 0, y: 0 };
  let dragCurrentGrid = { x: 0, y: 0 };
  let dragCurrentPixel = { x: 0, y: 0 };

  canvas.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0 || !currentState || !currentState.entities) return;
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
      if (d < minD && d <= 3) {
        closest = ent;
        minD = d;
      }
    }

    if (closest) {
      dragCandidate = closest;
      isDraggingAsset = false;
      dragStartPos = { x: ev.clientX, y: ev.clientY };
      dragCurrentGrid = { x: closest.position.x, y: closest.position.y };
      dragCurrentPixel = { x: clickX, y: clickY };
      try { canvas.setPointerCapture(ev.pointerId); } catch (_) {}
    } else {
      dragCandidate = null;
      isDraggingAsset = false;
    }
  });

  canvas.addEventListener('pointermove', (ev) => {
    if (!currentState) return;
    const rect = canvas.getBoundingClientRect();
    const mx = ev.clientX - rect.left;
    const my = ev.clientY - rect.top;
    const cellW = canvas.width / (currentState.width || 100);
    const cellH = canvas.height / (currentState.height || 100);
    const gridX = Math.floor(mx / cellW);
    const gridY = Math.floor(my / cellH);

    if (dragCandidate) {
      const dist = Math.hypot(ev.clientX - dragStartPos.x, ev.clientY - dragStartPos.y);
      if (dist > 5) {
        isDraggingAsset = true;
        canvas.style.cursor = 'grabbing';
        const targetX = Math.max(0, Math.min((currentState.width || 100) - 1, gridX));
        const targetY = Math.max(0, Math.min((currentState.height || 100) - 1, gridY));
        dragCurrentGrid = { x: targetX, y: targetY };
        dragCurrentPixel = { x: mx, y: my };
        renderBoard(currentState);
      }
    } else {
      // Hover feedback when mouse is near an animal
      let isNearEntity = false;
      for (const ent of (currentState.entities || [])) {
        if (!ent.alive) continue;
        if (Math.abs(ent.position.x - gridX) + Math.abs(ent.position.y - gridY) <= 1) {
          isNearEntity = true;
          break;
        }
      }
      canvas.style.cursor = isNearEntity ? 'grab' : 'default';
    }
  });

  canvas.addEventListener('pointerup', async (ev) => {
    if (ev.button !== 0) return;
    try { canvas.releasePointerCapture(ev.pointerId); } catch (_) {}

    if (dragCandidate) {
      const candidate = dragCandidate;
      const wasDragging = isDraggingAsset;
      const targetGrid = { ...dragCurrentGrid };

      dragCandidate = null;
      isDraggingAsset = false;
      canvas.style.cursor = 'default';

      if (wasDragging) {
        // Drag completed!
        const blocked = isCellBlocked(currentState, targetGrid.x, targetGrid.y);
        if (!blocked && (targetGrid.x !== candidate.position.x || targetGrid.y !== candidate.position.y)) {
          // Send reposition request to stage for next tick
          const res = await apiCall(`/api/simulations/${currentSimId}/entities/${candidate.id}/reposition`, 'POST', {
            x: targetGrid.x,
            y: targetGrid.y,
          });
          if (res && res.state) {
            updateUI(res.state);
          }
        } else if (currentState) {
          renderBoard(currentState);
        }
      } else {
        // Plain click: select agent
        selectAgent(candidate.id);
      }
    } else {
      // Clicked on empty space: deselect current animal and release to auto
      if (selectedAgentId) {
        selectAgent(null);
      }
    }
  });

  canvas.addEventListener('pointercancel', (ev) => {
    try { canvas.releasePointerCapture(ev.pointerId); } catch (_) {}
    dragCandidate = null;
    isDraggingAsset = false;
    canvas.style.cursor = 'default';
    if (currentState) renderBoard(currentState);
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
        if (settingCatAttackRange) settingCatAttackRange.value = data.cat.attack_range ?? 4;
        if (settingCatAttackCd) settingCatAttackCd.value = data.cat.attack_cooldown ?? 3;
        if (settingCatLungeCd) settingCatLungeCd.value = data.cat.lunge_cooldown ?? 2;
        if (settingCatVision) settingCatVision.value = data.cat.vision_radius ?? 10;
        if (settingCatVisionRear) settingCatVisionRear.value = data.cat.vision_rear ?? 2;
        if (settingCatVisionHalfWidth) settingCatVisionHalfWidth.value = data.cat.vision_half_width ?? 3;
        // `checked`, not `value`: assigning to `.value` on a checkbox sets the
        // submitted string and leaves the tick state alone, so the control would
        // render unticked regardless of server state and the next save would
        // then switch the feature off behind the operator's back.
        if (settingCatAimAssist) settingCatAimAssist.checked = !!data.cat.aim_assist;
        updateAimAssistReadout();
        if (settingCatWeight) settingCatWeight.value = data.cat.weight ?? 10;
        updateCatVisionBoxReadout();
      }
      if (data.mouse) {
        if (settingMouseMove) settingMouseMove.value = data.mouse.move_cooldown ?? 2;
        if (settingMouseVision) settingMouseVision.value = data.mouse.vision_radius ?? 4;
        if (settingMouseWeight) settingMouseWeight.value = data.mouse.weight ?? 1;
      }

      // Load disclosure settings
      try {
        const disc = await apiCall(`/api/simulations/${currentSimId}/disclosure`, 'GET');
        if (disc) {
          if (settingCoachPopulation) settingCoachPopulation.checked = disc.coach_sees_population ?? true;
          if (settingCoachCoverageMap) settingCoachCoverageMap.checked = disc.coach_sees_coverage_map ?? true;
          if (settingActorOwnTrail) settingActorOwnTrail.checked = disc.actor_sees_own_trail ?? false;
        }
      } catch (discErr) {
        console.warn('Failed to load disclosure settings:', discErr);
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


  // Renders the box the three vision numbers actually describe. The stored
  // field is a *half*-width, so without this you have to do 2n+1 in your head
  // to know what you just built -- and the whole reason the viewport is a box
  // rather than a taper is to keep that arithmetic off the operator as well as
  // off the actor.
  function updateCatVisionBoxReadout() {
    const el = document.getElementById('catVisionBoxReadout');
    if (!el) return;
    const fwd = parseIntDefault(settingCatVision?.value, 10, 1);
    const rear = parseIntDefault(settingCatVisionRear?.value, 2, 0);
    const half = parseIntDefault(settingCatVisionHalfWidth?.value, 3, 0);
    const depth = fwd + rear + 1;       // +1 for the cat's own row
    const width = 2 * half + 1;         // always odd, by construction
    let msg = `Box: ${depth} deep \u00d7 ${width} wide = ${depth * width} tiles`;
    if (rear === 0) msg += ' \u2014 blind behind';
    if (half === 0) msg += ' \u2014 single-file lane';
    el.textContent = msg;
  }

  function resetSettingsToDefaults() {
    if (settingCatMove) settingCatMove.value = 3;
    if (settingCatAttackRange) settingCatAttackRange.value = 8;
    if (settingCatAttackCd) settingCatAttackCd.value = 3;
    if (settingCatLungeCd) settingCatLungeCd.value = 2;
    if (settingCatVision) settingCatVision.value = 10;
    if (settingCatVisionRear) settingCatVisionRear.value = 2;
    if (settingCatVisionHalfWidth) settingCatVisionHalfWidth.value = 3;
    // Mirrors SpeciesConfig.aim_assist's default -- reset means the shipped
    // baseline, and the shipped baseline has aim assist enabled.
    if (settingCatAimAssist) settingCatAimAssist.checked = true;
    updateAimAssistReadout();
    if (settingCatWeight) settingCatWeight.value = 10;
    updateCatVisionBoxReadout();

    if (settingMouseMove) settingMouseMove.value = 2;
    if (settingMouseVision) settingMouseVision.value = 4;
    if (settingMouseWeight) settingMouseWeight.value = 1;

    // Matches DisclosureConfig defaults
    if (settingCoachPopulation) settingCoachPopulation.checked = true;
    if (settingCoachCoverageMap) settingCoachCoverageMap.checked = true;
    if (settingActorOwnTrail) settingActorOwnTrail.checked = false;

    showSettingsFeedback('Reset to default values. Click "Apply Settings" to save.', false);
  }

  async function saveSettings() {
    hideSettingsFeedback();

    const catPayload = {
      move_cooldown: parseIntDefault(settingCatMove?.value, 3, 1),
      attack_range: parseIntDefault(settingCatAttackRange?.value, 4, 0),
      attack_cooldown: parseIntDefault(settingCatAttackCd?.value, 3, 0),
      lunge_cooldown: parseIntDefault(settingCatLungeCd?.value, 2, 0),
      vision_radius: parseIntDefault(settingCatVision?.value, 10, 1),
      vision_rear: parseIntDefault(settingCatVisionRear?.value, 2, 0),
      vision_half_width: parseIntDefault(settingCatVisionHalfWidth?.value, 3, 0),
      // A real boolean: the endpoint's field is Optional[bool] and pydantic
      // would reject the "on" string a form submission produces.
      aim_assist: !!settingCatAimAssist?.checked,
      weight: parseIntDefault(settingCatWeight?.value, 10, 1)
    };

    const mousePayload = {
      move_cooldown: parseIntDefault(settingMouseMove?.value, 2, 1),
      vision_radius: parseIntDefault(settingMouseVision?.value, 4, 1),
      weight: parseIntDefault(settingMouseWeight?.value, 1, 1)
    };

    const disclosurePayload = {
      coach_sees_population: !!settingCoachPopulation?.checked,
      coach_sees_coverage_map: !!settingCoachCoverageMap?.checked,
      actor_sees_own_trail: !!settingActorOwnTrail?.checked
    };

    try {
      const [catRes, mouseRes, discRes] = await Promise.all([
        apiCall(`/api/simulations/${currentSimId}/species/cat`, 'PATCH', catPayload),
        apiCall(`/api/simulations/${currentSimId}/species/mouse`, 'PATCH', mousePayload),
        apiCall(`/api/simulations/${currentSimId}/disclosure`, 'PATCH', disclosurePayload)
      ]);

      if (!catRes || !mouseRes || !discRes) {
        showSettingsFeedback('Failed to update parameters on server.', true);
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

  // Both pop-outs are opened with a *fixed* window name so that clicking the
  // button twice focuses the existing window instead of littering the desktop
  // with duplicates. That is the behaviour we want, but it has a nasty side
  // effect: when a window with the given name already exists AND the URL is
  // byte-identical to the one it is already showing, the browser treats the
  // call as a pure focus request. It does not navigate, so it never re-fetches
  // the HTML, so the popup keeps showing whatever markup it loaded when it was
  // first opened -- potentially hours earlier.
  //
  // This bit us for real: the Cat vision controls were split into Forward /
  // Rear / Side Reach, the server was verifiably serving the new markup, and
  // the settings popup still showed the old single "Vision Radius" dial. The
  // page was not cached in the ordinary sense and a reload of the *parent*
  // window did nothing, because the popup is an independent browsing context
  // that was simply never told to load anything again.
  //
  // Appending a throwaway timestamp makes each open a genuinely different URL,
  // which forces the navigation and therefore a fresh fetch. The server routes
  // /settings and /boxscore with FileResponse and ignores the query string, and
  // neither page reads its own query params, so the parameter is inert beyond
  // defeating the no-op-focus path.
  function popoutUrl(path, simId = null) {
    const targetSim = simId || currentSimId || 'active';
    return `${path}?sim=${encodeURIComponent(targetSim)}&v=${Date.now()}`;
  }

  function openSettingsWindow() {
    const width = 560;
    const height = 780;
    const screenW = window.screen.availWidth || window.innerWidth;
    const screenH = window.screen.availHeight || window.innerHeight;
    const left = Math.max(10, Math.min(screenW - width - 20, (window.screenX || 0) + (window.outerWidth || 1000) - width));
    const top = Math.max(20, (window.screenY || 0) + 30);
    const features = `width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes,status=no,toolbar=no,menubar=no,location=no`;

    const popup = window.open(popoutUrl('/settings'), 'CMGameSettings', features);
    if (popup && !popup.closed) {
      popup.focus();
      closeSettingsModal();
    } else {
      // Fallback to in-page pop-out if browser blocks popup windows
      openSettingsModal();
    }
  }

  const btnOpenBoxscore = document.getElementById('btnOpenBoxscore');

  function openBoxscoreWindow() {
    const width = 900;
    const height = 820;
    const screenW = window.screen.availWidth || window.innerWidth;
    const screenH = window.screen.availHeight || window.innerHeight;
    const left = Math.max(10, Math.min(screenW - width - 20, (window.screenX || 0) + (window.outerWidth || 1000) - width));
    const top = Math.max(20, (window.screenY || 0) + 30);
    const features = `width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes,status=no,toolbar=no,menubar=no,location=no`;

    const popup = window.open(popoutUrl('/boxscore'), 'CMBoxScore', features);
    if (popup && !popup.closed) {
      popup.focus();
    } else {
      console.warn('Box Score pop-out was blocked by the browser. Allow pop-ups for this site to view it.');
    }
  }

  const btnOpenArchive = document.getElementById('btnOpenArchive');

  function openArchiveWindow() {
    const screenW = window.screen.availWidth || window.innerWidth || 1440;
    const screenH = window.screen.availHeight || window.innerHeight || 900;
    const width = Math.min(1600, Math.max(1150, Math.floor(screenW * 0.92)));
    const height = Math.min(1050, Math.max(800, Math.floor(screenH * 0.90)));
    const left = Math.max(10, Math.floor((screenW - width) / 2));
    const top = Math.max(20, Math.floor((screenH - height) / 2));
    const features = `width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes,status=no,toolbar=no,menubar=no,location=no`;

    const popup = window.open(popoutUrl('/archive'), 'CMArchive', features);
    if (popup && !popup.closed) {
      popup.focus();
    } else {
      console.warn('Archive pop-out was blocked by the browser. Allow pop-ups for this site to view it.');
    }
  }

  // Settings Pop-Out Window Listeners
  if (btnOpenSettings) btnOpenSettings.addEventListener('click', openSettingsWindow);
  if (btnPopoutWindow) btnPopoutWindow.addEventListener('click', openSettingsWindow);
  if (btnOpenBoxscore) btnOpenBoxscore.addEventListener('click', openBoxscoreWindow);
  if (btnOpenArchive) btnOpenArchive.addEventListener('click', openArchiveWindow);
  if (btnCloseSettings) btnCloseSettings.addEventListener('click', closeSettingsModal);
  if (btnCancelSettings) btnCancelSettings.addEventListener('click', closeSettingsModal);
  if (btnSaveSettings) btnSaveSettings.addEventListener('click', saveSettings);
  if (btnResetSettings) btnResetSettings.addEventListener('click', resetSettingsToDefaults);
  // Keep the derived-shape readout honest while the operator is still typing,
  // not just after a save round-trips.
  // Says what the switch does, because the label cannot. "Aim Assist" reads
  // like a difficulty setting; the point to convey is the opposite. It changes
  // what the agent is told, never what the cat can do, so any kill-rate delta
  // across this switch is evidence about the agent rather than the animal.
  function updateAimAssistReadout() {
    const el = document.getElementById('catAimAssistReadout');
    if (!el) return;
    // textContent, never innerHTML.
    el.textContent = settingCatAimAssist?.checked
      ? 'ON \u2014 agent is told forward/offaxis/in_strike_zone per target. Same powers, less arithmetic.'
      : 'OFF \u2014 agent gets absolute coordinates only and must derive the strike zone itself.';
  }
  // 'change' rather than 'input': it is the event specified to fire on a
  // checked-state transition.
  if (settingCatAimAssist) settingCatAimAssist.addEventListener('change', updateAimAssistReadout);

  [settingCatVision, settingCatVisionRear, settingCatVisionHalfWidth].forEach((el) => {
    if (el) el.addEventListener('input', updateCatVisionBoxReadout);
  });

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
    catHpAnim.clear();
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

