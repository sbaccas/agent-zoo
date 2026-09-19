/**
 * Game Settings — Independent Standalone Window Controller
 * Zero-innerHTML compliant. Interacts with /api/simulations/active/species in real time.
 */
document.addEventListener('DOMContentLoaded', () => {
  // DOM Elements
  const syncStatus = document.getElementById('syncStatus');
  const settingsFeedback = document.getElementById('settingsFeedback');

  // Cat inputs
  const settingCatMove = document.getElementById('settingCatMove');
  const settingCatAttackRange = document.getElementById('settingCatAttackRange');
  const settingCatAttackCd = document.getElementById('settingCatAttackCd');
  const settingCatLungeCd = document.getElementById('settingCatLungeCd');
  const settingCatVision = document.getElementById('settingCatVision');
  const settingCatVisionRear = document.getElementById('settingCatVisionRear');
  const settingCatVisionHalfWidth = document.getElementById('settingCatVisionHalfWidth');
  const settingCatWeight = document.getElementById('settingCatWeight');

  // Mouse inputs
  const settingMouseMove = document.getElementById('settingMouseMove');
  const settingMouseVision = document.getElementById('settingMouseVision');
  const settingMouseWeight = document.getElementById('settingMouseWeight');

  // Buttons
  const btnSaveSettings = document.getElementById('btnSaveSettings');
  const btnResetSettings = document.getElementById('btnResetSettings');
  const btnCloseWindow = document.getElementById('btnCloseWindow');

  function showFeedback(text, isError = false) {
    if (!settingsFeedback) return;
    settingsFeedback.textContent = text;
    settingsFeedback.className = isError ? 'settings-feedback feedback-error' : 'settings-feedback feedback-success';
    settingsFeedback.style.display = 'block';
  }

  function hideFeedback() {
    if (!settingsFeedback) return;
    settingsFeedback.style.display = 'none';
  }

  function parseIntDefault(val, defaultVal, minVal = 0) {
    const parsed = parseInt(val, 10);
    if (isNaN(parsed)) return defaultVal;
    return Math.max(minVal, parsed);
  }

  async function loadCurrentSpecies() {
    try {
      const resp = await fetch('/api/simulations/active/species');
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = await resp.json();
      if (syncStatus) {
        syncStatus.textContent = 'Active (Live)';
        syncStatus.style.background = '#dcfce7';
        syncStatus.style.color = '#166534';
      }

      if (data.cat) {
        if (settingCatMove) settingCatMove.value = data.cat.move_cooldown ?? 3;
        if (settingCatAttackRange) settingCatAttackRange.value = data.cat.attack_range ?? 4;
        if (settingCatAttackCd) settingCatAttackCd.value = data.cat.attack_cooldown ?? 3;
        if (settingCatLungeCd) settingCatLungeCd.value = data.cat.lunge_cooldown ?? 2;
        if (settingCatVision) settingCatVision.value = data.cat.vision_radius ?? 10;
        if (settingCatVisionRear) settingCatVisionRear.value = data.cat.vision_rear ?? 2;
        if (settingCatVisionHalfWidth) settingCatVisionHalfWidth.value = data.cat.vision_half_width ?? 3;
        if (settingCatWeight) settingCatWeight.value = data.cat.weight ?? 10;
        updateCatVisionBoxReadout();
      }

      if (data.mouse) {
        if (settingMouseMove) settingMouseMove.value = data.mouse.move_cooldown ?? 2;
        if (settingMouseVision) settingMouseVision.value = data.mouse.vision_radius ?? 4;
        if (settingMouseWeight) settingMouseWeight.value = data.mouse.weight ?? 1;
      }
    } catch (err) {
      console.error('Failed to load species data:', err);
      if (syncStatus) {
        syncStatus.textContent = 'Sync Error';
        syncStatus.style.background = '#fee2e2';
        syncStatus.style.color = '#991b1b';
      }
      showFeedback('Could not connect to active game simulation. Is server running?', true);
    }
  }

  async function saveSettings() {
    hideFeedback();

    const catPayload = {
      move_cooldown: parseIntDefault(settingCatMove?.value, 3, 1),
      attack_range: parseIntDefault(settingCatAttackRange?.value, 4, 0),
      attack_cooldown: parseIntDefault(settingCatAttackCd?.value, 3, 0),
      lunge_cooldown: parseIntDefault(settingCatLungeCd?.value, 2, 0),
      vision_radius: parseIntDefault(settingCatVision?.value, 10, 1),
      vision_rear: parseIntDefault(settingCatVisionRear?.value, 2, 0),
      vision_half_width: parseIntDefault(settingCatVisionHalfWidth?.value, 3, 0),
      weight: parseIntDefault(settingCatWeight?.value, 10, 1),
    };

    const mousePayload = {
      move_cooldown: parseIntDefault(settingMouseMove?.value, 2, 1),
      vision_radius: parseIntDefault(settingMouseVision?.value, 4, 1),
      weight: parseIntDefault(settingMouseWeight?.value, 1, 1),
    };

    try {
      const [resCat, resMouse] = await Promise.all([
        fetch('/api/simulations/active/species/cat', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(catPayload),
        }),
        fetch('/api/simulations/active/species/mouse', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(mousePayload),
        }),
      ]);

      if (!resCat.ok || !resMouse.ok) {
        throw new Error('Failed to update species parameters');
      }

      showFeedback('✓ Settings applied live! Active on next simulation tick.', false);

      // Notify parent game board window to refresh its state & view cones immediately
      if (window.opener && !window.opener.closed) {
        window.opener.postMessage({ type: 'settings_updated' }, '*');
      }
    } catch (err) {
      console.error('Error saving settings:', err);
      showFeedback('Error saving settings: ' + err.message, true);
    }
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

  function resetToDefaults() {
    if (settingCatMove) settingCatMove.value = 3;
    if (settingCatAttackRange) settingCatAttackRange.value = 2;
    if (settingCatAttackCd) settingCatAttackCd.value = 3;
    if (settingCatLungeCd) settingCatLungeCd.value = 2;
    if (settingCatVision) settingCatVision.value = 10;
    if (settingCatVisionRear) settingCatVisionRear.value = 2;
    if (settingCatVisionHalfWidth) settingCatVisionHalfWidth.value = 3;
    if (settingCatWeight) settingCatWeight.value = 10;
    updateCatVisionBoxReadout();

    if (settingMouseMove) settingMouseMove.value = 2;
    if (settingMouseVision) settingMouseVision.value = 4;
    if (settingMouseWeight) settingMouseWeight.value = 1;

    showFeedback('Reset values to default. Click "Apply Settings" to activate on server.', false);
  }

  // Event Listeners
  if (btnSaveSettings) btnSaveSettings.addEventListener('click', saveSettings);
  if (btnResetSettings) btnResetSettings.addEventListener('click', resetToDefaults);
  if (btnCloseWindow) btnCloseWindow.addEventListener('click', () => window.close());

  // Keep the derived-shape readout honest while the operator is still typing,
  // not just after a save round-trips.
  [settingCatVision, settingCatVisionRear, settingCatVisionHalfWidth].forEach((el) => {
    if (el) el.addEventListener('input', updateCatVisionBoxReadout);
  });

  // Initial load
  loadCurrentSpecies();
});
