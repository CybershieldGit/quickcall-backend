/**
 * Main App Controller — State Manager, View Switcher, Showcase Benchmarks & Init
 */

let currentActivePanel = 'client';
let activeTtsEngine = 'deepgram';

let toastDismissTimer = null;
let popupConfirmCallback = null;
let popupCancelCallback = null;

function dismissToast() {
  const el = document.getElementById('toast');
  if (!el) return;
  el.classList.remove('show');
  setTimeout(() => {
    el.style.display = 'none';
  }, 220);
  if (toastDismissTimer) {
    clearTimeout(toastDismissTimer);
    toastDismissTimer = null;
  }
}

function escapeToastHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function showToast(msg, typeOrSuccess = true, customTitle = null) {
  let el = document.getElementById('toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    document.body.appendChild(el);
  }

  // Determine type: 'success' | 'error' | 'warning' | 'info'
  let type = 'success';
  if (typeOrSuccess === false || typeOrSuccess === 'error' || typeOrSuccess === 'danger') {
    type = 'error';
  } else if (typeOrSuccess === 'warning') {
    type = 'warning';
  } else if (typeOrSuccess === 'info') {
    type = 'info';
  } else if (typeOrSuccess === true || typeOrSuccess === 'success') {
    type = 'success';
  }

  let cleanMsg = String(msg ?? '');

  // Detect explicit unicode indicator prefixes in message strings
  if (cleanMsg.startsWith('✓ ') || cleanMsg.startsWith('✔ ')) {
    cleanMsg = cleanMsg.substring(2).trim();
    type = 'success';
  } else if (cleanMsg.startsWith('✕ ') || cleanMsg.startsWith('✖ ')) {
    cleanMsg = cleanMsg.substring(2).trim();
    type = 'error';
  } else if (cleanMsg.startsWith('⚠️ ')) {
    cleanMsg = cleanMsg.substring(2).trim();
    type = 'warning';
  } else if (cleanMsg.startsWith('ℹ️ ') || cleanMsg.startsWith('ℹ ')) {
    cleanMsg = cleanMsg.substring(2).trim();
    type = 'info';
  } else if (typeOrSuccess === true && /^(error|failed|invalid|please provide|cannot|exception)/i.test(cleanMsg)) {
    type = 'error';
  }

  const defaultTitles = {
    success: 'Action Successful',
    error: 'Alert / Attention Needed',
    warning: 'System Warning',
    info: 'System Notification'
  };

  const icons = {
    success: '✓',
    error: '✕',
    warning: '⚠️',
    info: 'ℹ'
  };

  const title = customTitle || defaultTitles[type] || 'Notification';

  // Apply classes and accessibility attributes
  el.className = `enterprise-alert-popup toast-${type}`;
  el.setAttribute('role', 'alert');
  el.setAttribute('aria-live', 'assertive');

  el.innerHTML = `
    <div class="toast-icon-badge ${type}">
      ${icons[type] || 'ℹ'}
    </div>
    <div class="toast-body">
      <div class="toast-title">${escapeToastHtml(title)}</div>
      <div class="toast-msg">${escapeToastHtml(cleanMsg)}</div>
    </div>
    <button type="button" class="toast-close-btn" onclick="dismissToast()" title="Dismiss notification" aria-label="Close notification">✕</button>
    <div class="toast-progress-bar ${type}"></div>
  `;

  // Pause timer on hover, resume on leave
  el.onmouseenter = () => {
    if (toastDismissTimer) {
      clearTimeout(toastDismissTimer);
      toastDismissTimer = null;
    }
    const bar = el.querySelector('.toast-progress-bar');
    if (bar) bar.style.animationPlayState = 'paused';
  };

  el.onmouseleave = () => {
    const bar = el.querySelector('.toast-progress-bar');
    if (bar) bar.style.animationPlayState = 'running';
    if (toastDismissTimer) clearTimeout(toastDismissTimer);
    toastDismissTimer = setTimeout(dismissToast, 2200);
  };

  if (toastDismissTimer) clearTimeout(toastDismissTimer);

  el.style.display = 'flex';
  void el.offsetWidth; // Force DOM reflow
  el.classList.add('show');

  toastDismissTimer = setTimeout(() => {
    dismissToast();
  }, 4500);
}

// Intercept window.alert so all alert() calls automatically pop up in this styled HUD
window.alert = function(msg) {
  showToast(msg, 'info', 'System Notice');
};

// Enterprise Interactive Modal Dialogs
function showAlertPopup(title, message, type = 'info', onOk = null) {
  const modal = document.getElementById('enterprise-dialog-modal');
  if (!modal) {
    showToast(message, type, title);
    if (typeof onOk === 'function') onOk();
    return;
  }
  const iconEl = document.getElementById('dialog-modal-icon');
  const titleEl = document.getElementById('dialog-modal-title');
  const msgEl = document.getElementById('dialog-modal-msg');
  const cancelBtn = document.getElementById('dialog-modal-cancel-btn');

  const icons = { success: '✓', error: '✕', warning: '⚠️', info: 'ℹ' };
  if (iconEl) {
    iconEl.className = `toast-icon-badge ${type}`;
    iconEl.innerText = icons[type] || 'ℹ';
  }
  if (titleEl) titleEl.innerText = title || 'System Notice';
  if (msgEl) msgEl.innerText = message || '';
  if (cancelBtn) cancelBtn.style.display = 'none';

  popupConfirmCallback = onOk;
  popupCancelCallback = null;

  modal.classList.add('active');
}

function showConfirmPopup(title, message, onConfirm = null, onCancel = null) {
  const modal = document.getElementById('enterprise-dialog-modal');
  if (!modal) {
    if (confirm(message)) {
      if (typeof onConfirm === 'function') onConfirm();
    } else {
      if (typeof onCancel === 'function') onCancel();
    }
    return;
  }
  const iconEl = document.getElementById('dialog-modal-icon');
  const titleEl = document.getElementById('dialog-modal-title');
  const msgEl = document.getElementById('dialog-modal-msg');
  const cancelBtn = document.getElementById('dialog-modal-cancel-btn');

  if (iconEl) {
    iconEl.className = 'toast-icon-badge warning';
    iconEl.innerText = '❓';
  }
  if (titleEl) titleEl.innerText = title || 'Confirmation Required';
  if (msgEl) msgEl.innerText = message || 'Are you sure you want to proceed?';
  if (cancelBtn) cancelBtn.style.display = 'inline-block';

  popupConfirmCallback = onConfirm;
  popupCancelCallback = onCancel;

  modal.classList.add('active');
}

function handleDialogModalOk() {
  closeDialogModal();
  if (typeof popupConfirmCallback === 'function') {
    popupConfirmCallback();
  }
}

function handleDialogModalCancel() {
  closeDialogModal();
  if (typeof popupCancelCallback === 'function') {
    popupCancelCallback();
  }
}

function closeDialogModal() {
  const modal = document.getElementById('enterprise-dialog-modal');
  if (modal) modal.classList.remove('active');
}

// =============================================================================
// ENTERPRISE INPUT POP-UP MODAL CONTROLLER (Rates, Credits, Values)
// =============================================================================
let inputModalCallback = null;

function openInputModal({
  title = 'Edit Value',
  subtitle = '',
  label = 'Value',
  value = '',
  prefix = '₹',
  suffix = '',
  step = 'any',
  min = '0',
  inputType = 'number',
  placeholder = '',
  presets = [],
  hint = '',
  confirmText = 'Save & Apply',
  onConfirm = null
}) {
  const modal = document.getElementById('enterprise-input-modal');
  if (!modal) return;

  const titleEl = document.getElementById('input-modal-title');
  const subEl = document.getElementById('input-modal-subtitle');
  const labelEl = document.getElementById('input-modal-label');
  if (titleEl) titleEl.innerText = title;
  if (subEl) subEl.innerText = subtitle;
  if (labelEl) labelEl.innerText = label;

  const prefixEl = document.getElementById('input-modal-prefix');
  const suffixEl = document.getElementById('input-modal-suffix');
  if (prefixEl) {
    prefixEl.innerText = prefix;
    prefixEl.style.display = prefix ? 'flex' : 'none';
  }
  if (suffixEl) {
    suffixEl.innerText = suffix;
    suffixEl.style.display = suffix ? 'flex' : 'none';
  }

  const field = document.getElementById('input-modal-field');
  if (field) {
    field.type = inputType || 'number';
    field.value = value;
    if (inputType === 'number') {
      field.step = step;
      if (min !== null && min !== undefined) field.min = min;
    } else {
      field.removeAttribute('step');
      field.removeAttribute('min');
    }
    if (placeholder) field.placeholder = placeholder;
  }

  const presetsRow = document.getElementById('input-modal-presets');
  if (presetsRow) {
    presetsRow.innerHTML = '';
    if (presets && presets.length > 0) {
      presets.forEach(p => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'input-preset-chip';
        btn.innerText = p.label || p;
        btn.onclick = () => {
          if (field) {
            field.value = p.value !== undefined ? p.value : p;
            field.focus();
          }
        };
        presetsRow.appendChild(btn);
      });
      presetsRow.style.display = 'flex';
    } else {
      presetsRow.style.display = 'none';
    }
  }

  const hintEl = document.getElementById('input-modal-hint');
  if (hintEl) {
    hintEl.innerHTML = hint || '';
    hintEl.style.display = hint ? 'block' : 'none';
  }

  const confirmBtn = document.getElementById('input-modal-confirm-btn');
  if (confirmBtn) confirmBtn.innerText = confirmText;

  inputModalCallback = onConfirm;

  modal.classList.add('active');
  setTimeout(() => {
    if (field) {
      field.focus();
      field.select();
    }
  }, 100);
}

function closeInputModal() {
  const modal = document.getElementById('enterprise-input-modal');
  if (modal) modal.classList.remove('active');
  inputModalCallback = null;
}

function handleInputModalSubmit(e) {
  if (e) e.preventDefault();
  const field = document.getElementById('input-modal-field');
  const val = field ? field.value : '';
  const cb = inputModalCallback;
  closeInputModal();
  if (typeof cb === 'function') {
    cb(val);
  }
}

// =============================================================================
// ENTERPRISE CONFIRMATION POP-UP MODAL (Destructive & Warning actions)
// =============================================================================
let confirmModalOkCallback = null;
let confirmModalCancelCallback = null;

function openConfirmModal({
  title = 'Confirmation Required',
  message = 'Are you sure you want to proceed?',
  iconType = 'warning',
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  isDestructive = false,
  onConfirm = null,
  onCancel = null
}) {
  const modal = document.getElementById('enterprise-confirm-modal');
  if (!modal) {
    if (confirm(message)) {
      if (typeof onConfirm === 'function') onConfirm();
    } else {
      if (typeof onCancel === 'function') onCancel();
    }
    return;
  }

  const iconEl = document.getElementById('confirm-modal-icon');
  const titleEl = document.getElementById('confirm-modal-title');
  const msgEl = document.getElementById('confirm-modal-msg');
  const okBtn = document.getElementById('confirm-modal-ok-btn');
  const cancelBtn = document.getElementById('confirm-modal-cancel-btn');

  const icons = {
    warning: '⚠️',
    danger: '⛔',
    info: 'ℹ',
    success: '✓'
  };

  if (iconEl) {
    iconEl.className = `toast-icon-badge ${iconType}`;
    iconEl.innerText = icons[iconType] || '⚠️';
  }
  if (titleEl) titleEl.innerText = title;
  if (msgEl) msgEl.innerText = message;
  if (okBtn) {
    okBtn.innerText = confirmText;
    okBtn.className = isDestructive ? 'btn btn-danger' : 'btn btn-primary';
  }
  if (cancelBtn) {
    cancelBtn.innerText = cancelText;
  }

  confirmModalOkCallback = onConfirm;
  confirmModalCancelCallback = onCancel;

  modal.classList.add('active');
}

function closeConfirmModal() {
  const modal = document.getElementById('enterprise-confirm-modal');
  if (modal) modal.classList.remove('active');
  confirmModalOkCallback = null;
  confirmModalCancelCallback = null;
}

function handleConfirmModalOk() {
  const cb = confirmModalOkCallback;
  closeConfirmModal();
  if (typeof cb === 'function') cb();
}

function handleConfirmModalCancel() {
  const cb = confirmModalCancelCallback;
  closeConfirmModal();
  if (typeof cb === 'function') cb();
}

const ROUTE_MAP = {
  'client': '/tenant',
  'reseller': '/reseller',
  'admin': '/admin',
  'showcase': '/simulator',
  'whatsapp': '/chats',
  'docs': '/docs-portal'
};

const PANEL_BY_PATH = {
  '/admin': 'admin',
  '/reseller': 'reseller',
  '/tenant': 'client',
  '/client': 'client',
  '/simulator': 'showcase',
  '/docs-portal': 'docs',
  '/docs': 'docs'
};

function switchPanel(panelId, updateHistory = true) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.panel-tab-btn').forEach(b => {
    b.classList.remove('active');
    const badge = b.querySelector('.nav-item-badge');
    if (badge) badge.remove();
  });

  const targetPanel = document.getElementById(`panel-${panelId}`);
  const targetBtn = document.getElementById(`tab-btn-${panelId}`);

  if (targetPanel) targetPanel.classList.add('active');
  if (targetBtn) {
    targetBtn.classList.add('active');
    if (!targetBtn.querySelector('.nav-item-badge')) {
      const badge = document.createElement('span');
      badge.className = 'nav-item-badge';
      badge.textContent = 'Active';
      targetBtn.appendChild(badge);
    }
  }

  currentActivePanel = panelId;

  // Sync browser URL bar with dedicated route
  if (updateHistory && typeof window !== 'undefined' && window.history) {
    const targetPath = ROUTE_MAP[panelId] || '/tenant';
    const currentPath = window.location.pathname.replace(/\/+$/, '') || '/';
    if (currentPath !== targetPath) {
      window.history.pushState({ panel: panelId }, '', targetPath);
    }
  }

  // Always reset scroll to top immediately when switching panels
  window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
  document.documentElement.scrollTop = 0;
  document.body.scrollTop = 0;
  const appMain = document.querySelector('.app-main');
  if (appMain) appMain.scrollTop = 0;
  const appViewport = document.querySelector('.app-viewport');
  if (appViewport) appViewport.scrollTop = 0;
  if (targetPanel) targetPanel.scrollTop = 0;

  const titleMap = {
    'client': 'Client Workspace & Live Voicebot',
    'reseller': 'Reseller Agency Profit Hub',
    'admin': 'Platform SuperAdmin Oversight',
    'showcase': 'Voicebot Simulator & Latency Pitch',
    'whatsapp': 'WhatsApp AI Conversational Studio',
    'docs': 'Platform Documentation & Architecture'
  };
  const topbarTitle = document.getElementById('topbar-section-title');
  if (topbarTitle && titleMap[panelId]) {
    topbarTitle.textContent = titleMap[panelId];
  }

  if (panelId === 'admin') {
    if (typeof loadAdminData === 'function') loadAdminData();
  } else if (panelId === 'reseller') {
    if (typeof loadResellerData === 'function') loadResellerData();
  } else if (panelId === 'client') {
    if (typeof loadClientDashboard === 'function') loadClientDashboard(activeTenantId);
  } else if (panelId === 'showcase') {
    initShowcaseBenchmarks();
  } else if (panelId === 'whatsapp') {
    if (typeof syncWhatsAppTenantDropdown === 'function') syncWhatsAppTenantDropdown();
    // Guarantee WhatsApp Studio opens cleanly from the very top without viewport jump
    requestAnimationFrame(() => {
      window.scrollTo(0, 0);
      document.documentElement.scrollTop = 0;
      document.body.scrollTop = 0;
    });
  }
}

/**
 * Enterprise Sub-Navigation for Tenant Dashboard
 * Allows one-click switching between Overview, Leads CRM, Call Logs, Lab, and RAG Studio
 */
function switchClientSubTab(tabName) {
  // Ensure the client workspace panel is active whenever a workspace subtab is clicked
  const activePanel = document.querySelector('.panel.active');
  if (!activePanel || activePanel.id !== 'panel-client') {
    if (typeof switchPanel === 'function') {
      switchPanel('client', true);
    }
  }

  // Sync hash in browser URL bar
  if (typeof window !== 'undefined' && window.history) {
    const targetHash = tabName === 'all' ? 'overview' : tabName;
    try {
      window.history.replaceState(null, '', `#${targetHash}`);
    } catch (e) {}
  }

  document.querySelectorAll('.subnav-btn').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-subtab') === tabName);
  });
  document.querySelectorAll('.sidebar-nav-item[data-subtab]').forEach(btn => {
    const isActive = btn.getAttribute('data-subtab') === tabName;
    btn.classList.toggle('active', isActive);
    const badge = btn.querySelector('.nav-item-badge');
    if (isActive) {
      if (!badge) {
        const b = document.createElement('span');
        b.className = 'nav-item-badge';
        b.textContent = 'Active';
        btn.appendChild(b);
      }
    } else if (badge) {
      badge.remove();
    }
  });

  const allSections = document.querySelectorAll('.client-section');
  if (tabName === 'all') {
    allSections.forEach(s => s.classList.remove('hidden-section'));
  } else {
    allSections.forEach(s => s.classList.add('hidden-section'));
    const target = document.getElementById(`client-section-${tabName}`);
    if (target) {
      target.classList.remove('hidden-section');
      target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }

  // Trigger waveform canvas resize if switching to calls or all
  if (tabName === 'all' || tabName === 'calls') {
    if (typeof clientWaveform !== 'undefined' && clientWaveform && clientWaveform.resize) {
      setTimeout(() => clientWaveform.resize(), 80);
    }
  }

  // Refresh Leads CRM immediately when switching to leads tab
  if (tabName === 'all' || tabName === 'leads') {
    if (typeof loadClientLeads === 'function' && typeof activeTenantId !== 'undefined' && activeTenantId) {
      loadClientLeads(activeTenantId);
    }
  }

  // Sync WhatsApp tenant dropdown if switching to whatsapp tab without jumping scroll
  if (tabName === 'all' || tabName === 'whatsapp') {
    if (typeof syncWhatsAppTenantDropdown === 'function') {
      syncWhatsAppTenantDropdown();
    }
  }

  // Ensure active tab button scrolls into view if partially hidden
  const activeBtn = document.querySelector(`.subnav-btn[data-subtab="${tabName}"]`);
  if (activeBtn) {
    activeBtn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
  }
}

function openWhatsAppChatTab() {
  switchPanel('whatsapp');
}

/**
 * Documentation Hub Sub-Navigation Tab Switcher
 */
function switchDocsTab(tabId) {
  document.querySelectorAll('.docs-tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-doctab') === tabId);
  });
  document.querySelectorAll('.docs-section').forEach(sec => {
    sec.classList.toggle('active', sec.id === `docs-section-${tabId}`);
  });
  const container = document.querySelector('.app-viewport') || window;
  if (container.scrollTo) container.scrollTo({ top: 0, behavior: 'smooth' });
}

/**
 * Enterprise Sub-Navigation Horizontal Scroll Engine
 * Enables natural horizontal mouse wheel and drag-to-scroll on .subnav-tabs while keeping .subnav-quick-actions fixed
 */
function initSubnavScroll() {
  const container = document.getElementById('subnav-bar') || document.querySelector('.subnav-container');
  const tabs = document.querySelector('.subnav-tabs');
  if (!container || !tabs) return;

  // 1. Natural mouse wheel to horizontal scroll conversion on tabs
  container.addEventListener('wheel', (e) => {
    if (e.deltaY !== 0) {
      if (tabs.scrollWidth > tabs.clientWidth) {
        e.preventDefault();
        tabs.scrollLeft += e.deltaY;
      }
    }
  }, { passive: false });

  // 2. Click-and-drag gesture (Grab & Swipe) on tabs
  let isDown = false;
  let startX = 0;
  let scrollLeftStart = 0;

  tabs.addEventListener('mousedown', (e) => {
    if (e.target.closest('button, a, input, select')) return; // allow clicking tab buttons
    isDown = true;
    tabs.style.cursor = 'grabbing';
    startX = e.pageX - tabs.offsetLeft;
    scrollLeftStart = tabs.scrollLeft;
  });

  window.addEventListener('mouseup', () => {
    isDown = false;
    if (tabs) tabs.style.cursor = 'default';
  });

  tabs.addEventListener('mouseleave', () => {
    isDown = false;
    if (tabs) tabs.style.cursor = 'default';
  });

  tabs.addEventListener('mousemove', (e) => {
    if (!isDown) return;
    e.preventDefault();
    const x = e.pageX - tabs.offsetLeft;
    const walk = (x - startX) * 1.5;
    tabs.scrollLeft = scrollLeftStart - walk;
  });
}

// Initialize on script load
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initSubnavScroll);
} else {
  initSubnavScroll();
}

// TTS Engine Controller
async function loadTtsStatus() {
  try {
    const res = await fetch('/api/sim/system-status');
    if (res.ok) {
      const data = await res.json();
      activeTtsEngine = (data.tts_engine || 'deepgram').toLowerCase();
      updateTtsUi(activeTtsEngine);
    }
  } catch (e) {
    console.warn('TTS status fetch error:', e);
  }
}

function updateTtsUi(engine) {
  const label = document.getElementById('nav-tts-label');
  if (!label) return;
  if (engine === 'deepgram') {
    label.innerText = '⚡ Deepgram Aura (Testing)';
    label.style.color = 'var(--cyan)';
  } else {
    label.innerText = '🎙️ Sarvam AI (Production)';
    label.style.color = '#A5B4FC';
  }
}

async function toggleTtsEngine() {
  const target = activeTtsEngine === 'deepgram' ? 'sarvam' : 'deepgram';
  try {
    const res = await fetch(`/api/sim/set-tts?engine=${target}`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      activeTtsEngine = data.active_tts_engine;
      updateTtsUi(activeTtsEngine);
      showToast(`Switched TTS to ${activeTtsEngine === 'deepgram' ? 'Deepgram Aura' : 'Sarvam AI'}`);
    }
  } catch (e) {
    showToast('Error switching TTS', false);
  }
}

// Executive Showcase Benchmarks
function initShowcaseBenchmarks() {
  const stt = document.getElementById('sc-metric-stt');
  const llm = document.getElementById('sc-metric-llm');
  const tts = document.getElementById('sc-metric-tts');
  const total = document.getElementById('sc-metric-total');

  if (stt) stt.innerText = '210ms';
  if (llm) llm.innerText = '165ms';
  if (tts) tts.innerText = '190ms';
  if (total) total.innerText = '565ms';
}

async function runShowcaseQuery(testPhrase) {
  const input = document.getElementById('sc-query-input');
  if (input) input.value = testPhrase;

  const box = document.getElementById('sc-result-box');
  if (box) {
    box.style.display = 'block';
    box.innerHTML = `
      <div style="color:var(--brown); font-weight:600; margin-bottom:6px;">⚡ Querying Groq Llama3 + In-Memory Vector Cache...</div>
      <div style="font-size:0.85rem; color:var(--text-primary);">"${escapeHtml(testPhrase)}"</div>
    `;
  }

  const t0 = performance.now();
  try {
    const res = await fetch('/api/sim/turn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_speech: testPhrase,
        tenant_id: activeTenantId || 'idigitalstudies'
      })
    });
    const data = await res.json();
    const elapsed = Math.round(performance.now() - t0);

    const totalEl = document.getElementById('sc-metric-total');
    if (totalEl) totalEl.innerText = `${elapsed}ms`;

    if (box) {
      box.innerHTML = `
        <div style="display:flex; justify-content:space-between; margin-bottom:8px; font-size:0.75rem; color:var(--text-muted);">
          <span>⏱️ Total Turnaround: <strong style="color:var(--emerald);">${elapsed}ms</strong></span>
          <span>State: <strong style="color:var(--brown);">${escapeHtml(data.state || 'MAIN_LOOP')}</strong></span>
        </div>
        <div style="background:#FAF8F5; border:1px solid var(--border); padding:12px; border-radius:8px; font-size:0.88rem; line-height:1.45; color:var(--text-primary);">
          <strong style="color:var(--accent);">AI Advisor:</strong> ${escapeHtml(data.response || data.agent_response || '')}
        </div>
      `;
    }
  } catch (e) {
    if (box) box.innerHTML = `<div style="color:var(--rose);">Error: ${e.message}</div>`;
  }
}

// Global Escape Key Listener for Modals
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (typeof closeCallTranscriptModal === 'function') closeCallTranscriptModal();
    if (typeof closeManageNumbersModal === 'function') closeManageNumbersModal();
    if (typeof closeOnboardingWizard === 'function') closeOnboardingWizard();
    if (typeof closeOnboardResellerModal === 'function') closeOnboardResellerModal();
    if (typeof closeOnboardTenantModal === 'function') closeOnboardTenantModal();
    if (typeof closeSellAgentModal === 'function') closeSellAgentModal();
    if (typeof closeAddLeadModal === 'function') closeAddLeadModal();
  }
});

function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Browser Back/Forward navigation support
window.addEventListener('popstate', (e) => {
  const path = window.location.pathname.replace(/\/+$/, '') || '/';
  if (e.state && e.state.panel) {
    switchPanel(e.state.panel, false);
  } else if (PANEL_BY_PATH[path]) {
    switchPanel(PANEL_BY_PATH[path], false);
  } else if (path === '/') {
    switchPanel('client', false);
  }
});

// Auto-init on page load based on pathname or hash
window.addEventListener('DOMContentLoaded', () => {
  loadTtsStatus();
  if (typeof loadAvailableNumbers === 'function') loadAvailableNumbers();
  if (typeof loadTenantsList === 'function') loadTenantsList();
  if (typeof loadResellerData === 'function') loadResellerData();

  const path = window.location.pathname.replace(/\/+$/, '') || '/';
  const hash = window.location.hash.replace('#', '');
  const clientSubtabs = ['overview', 'all', 'agents', 'telemetry', 'leads', 'calls', 'lab', 'knowledge'];

  if (hash && clientSubtabs.includes(hash)) {
    switchPanel('client', false);
    setTimeout(() => {
      if (typeof switchClientSubTab === 'function') {
        switchClientSubTab(hash === 'overview' ? 'all' : hash);
      }
    }, 120);
  } else if (PANEL_BY_PATH[path]) {
    switchPanel(PANEL_BY_PATH[path], false);
  } else {
    if (hash && ['client', 'reseller', 'admin', 'showcase', 'whatsapp', 'chats', 'chat', 'docs'].includes(hash)) {
      if (hash === 'chats' || hash === 'chat') {
        switchPanel('whatsapp', true);
      } else {
        switchPanel(hash, true);
      }
    } else {
      switchPanel('client', false);
    }
  }
});

function togglePasswordVisibility(inputId, btn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    if (btn) btn.innerText = '🙈';
  } else {
    input.type = 'password';
    if (btn) btn.innerText = '👁️';
  }
}
