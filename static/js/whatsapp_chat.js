/**
 * QwickCall AI — WhatsApp AI Chatbot Live Simulator & Testing Studio
 * Connects directly to /api/v1/chat endpoints with real-time RAG grounding,
 * brochure dispatch triggers, escalation routing, and telemetry HUD.
 */

let waSessionId = null;
let waTurnIndex = 0;
let waLastLatencyMs = 0;
let waIsSending = false;

// Default initial state
function getWaActiveTenantId() {
  const selectEl = document.getElementById('wa-tenant-select');
  if (selectEl && selectEl.value) return selectEl.value;
  if (typeof activeTenantId !== 'undefined' && activeTenantId) return activeTenantId;
  return 'idigitalstudies';
}

function getWaActiveLanguage() {
  const langEl = document.getElementById('wa-language-select');
  return langEl ? langEl.value : 'hi-IN';
}

function getWaCustomerName() {
  const nameEl = document.getElementById('wa-cust-name-input');
  return (nameEl && nameEl.value.trim()) ? nameEl.value.trim() : 'Prospective Student';
}

function getWaCustomerPhone() {
  const phoneEl = document.getElementById('wa-cust-phone-input');
  return (phoneEl && phoneEl.value.trim()) ? phoneEl.value.trim() : '+91 98765 43210';
}

/**
 * Format timestamp like WhatsApp: e.g. "16:25"
 */
function getWhatsAppTimeString() {
  const now = new Date();
  let hours = now.getHours();
  let minutes = now.getMinutes();
  const ampm = hours >= 12 ? 'PM' : 'AM';
  hours = hours % 12;
  hours = hours ? hours : 12;
  const minsStr = minutes < 10 ? '0' + minutes : minutes;
  return `${hours}:${minsStr} ${ampm}`;
}

/**
 * Initialize WhatsApp Simulator on page load
 */
document.addEventListener('DOMContentLoaded', () => {
  initWhatsAppChat();
});

function initWhatsAppChat() {
  const inputEl = document.getElementById('wa-chat-input');
  const sendBtn = document.getElementById('wa-send-btn');

  if (inputEl) {
    inputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendWhatsAppMessage();
      }
    });
    // Auto-update send button icon based on text
    inputEl.addEventListener('input', () => {
      const sendIcon = document.getElementById('wa-send-btn-icon');
      if (sendIcon) {
        sendIcon.innerText = inputEl.value.trim() ? '➤' : '🎤';
      }
    });
  }

  if (sendBtn) {
    sendBtn.addEventListener('click', () => {
      sendWhatsAppMessage();
    });
  }

  // Populate tenant selector if available
  syncWhatsAppTenantDropdown();

  // Start initial conversation session
  startNewWhatsAppSession();
}

/**
 * Sync WhatsApp tenant dropdown with available tenants
 */
async function syncWhatsAppTenantDropdown() {
  const waSelect = document.getElementById('wa-tenant-select');
  if (!waSelect) return;

  const currentVal = waSelect.value || (typeof activeTenantId !== 'undefined' ? activeTenantId : 'idigitalstudies');
  const clientSwitcher = document.getElementById('client-switcher');

  if (clientSwitcher && clientSwitcher.options.length > 0) {
    waSelect.innerHTML = '';
    const options = Array.from(clientSwitcher.options);
    options.forEach(opt => {
      if (opt.value) {
        const newOpt = document.createElement('option');
        newOpt.value = opt.value;
        newOpt.innerText = opt.innerText;
        if (opt.value === currentVal) newOpt.selected = true;
        waSelect.appendChild(newOpt);
      }
    });
  } else {
    try {
      const res = await fetch('/api/sim/tenants');
      if (res.ok) {
        const data = await res.json();
        waSelect.innerHTML = '';
        (data.tenants || []).forEach(t => {
          const newOpt = document.createElement('option');
          newOpt.value = t.tenant_id;
          newOpt.innerText = `${t.business_name} (${t.tenant_id})`;
          if (t.tenant_id === currentVal) newOpt.selected = true;
          waSelect.appendChild(newOpt);
        });
      }
    } catch (e) {
      console.warn('Error fetching tenants list:', e);
    }
  }

  if (waSelect.options.length === 0) {
    const defaultOpt = document.createElement('option');
    defaultOpt.value = 'idigitalstudies';
    defaultOpt.innerText = 'iDigitalStudies (idigitalstudies)';
    defaultOpt.selected = true;
    waSelect.appendChild(defaultOpt);
  }
}

/**
 * Called when tenant changes from global workspace or local dropdown
 */
function onWhatsAppTenantChange(tenantId) {
  const tid = tenantId || getWaActiveTenantId();
  updateWhatsAppHeaderForTenant(tid);
  startNewWhatsAppSession(tid);
}

/**
 * Updates the WhatsApp phone header with dynamic tenant info
 */
function updateWhatsAppHeaderForTenant(tenantId) {
  const tid = tenantId || getWaActiveTenantId();
  const headerName = document.getElementById('wa-header-title');
  const headerSub = document.getElementById('wa-header-sub');
  const headerAvatar = document.getElementById('wa-header-avatar');

  let bizName = 'iDigitalStudies';
  let advisorName = 'Aanya';

  if (typeof currentTenantProfile !== 'undefined' && currentTenantProfile && currentTenantProfile.tenant_id === tid) {
    bizName = currentTenantProfile.business_name || tid;
    advisorName = currentTenantProfile.advisor_name || 'AI Agent';
  } else if (tid.includes('phono')) {
    bizName = 'Phono Tel Support';
    advisorName = 'Vikram';
  }

  if (headerName) headerName.innerText = `${bizName} AI`;
  if (headerSub) headerSub.innerText = `${advisorName} • Online`;
  if (headerAvatar) headerAvatar.innerText = bizName.substring(0, 2).toUpperCase();

  const hudTenant = document.getElementById('wa-hud-tenant');
  if (hudTenant) hudTenant.innerText = tid;
}

/**
 * Start or reset WhatsApp session with welcome greeting
 */
async function startNewWhatsAppSession(explicitTenant = null) {
  const tenantId = explicitTenant || getWaActiveTenantId();
  const lang = getWaActiveLanguage();
  const custName = getWaCustomerName();

  updateWhatsAppHeaderForTenant(tenantId);

  const container = document.getElementById('wa-messages-container');
  if (container) {
    container.innerHTML = `
      <div class="wa-date-divider"><span>TODAY</span></div>
      <div class="wa-encryption-pill">
        🔒 Messages and calls are end-to-end encrypted with QwickCall AI multi-tenant isolation. No one outside of this chat can read them.
      </div>
    `;
  }

  showWhatsAppTyping();

  try {
    const res = await fetch('/api/v1/chat/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tenant_id: tenantId,
        language: lang,
        customer_name: custName,
        channel: 'whatsapp'
      })
    });

    hideWhatsAppTyping();

    if (res.ok) {
      const data = await res.json();
      waSessionId = data.session_id || `chat_${Math.random().toString(36).substring(2, 10)}`;
      waTurnIndex = 1;

      const greeting = data.greeting || data.reply || `Namaste! ${data.business_name || 'We'} mein aapka swagat hai. Main aapki kya madad kar sakti hoon?`;
      appendWhatsAppBubble('assistant', greeting, {
        timestamp: getWhatsAppTimeString(),
        advisor: data.agent_name || 'AI Agent'
      });

      updateWhatsAppTelemetryHUD({
        session_id: waSessionId,
        turn_index: waTurnIndex,
        latency_ms: 24,
        state: 'GREETING',
        escalate: false
      });

      updateWhatsAppJsonInspector({
        endpoint: '/api/v1/chat/start',
        request: { tenant_id: tenantId, language: lang, customer_name: custName, channel: 'whatsapp' },
        response: data
      });
    } else {
      fallbackInitialGreeting();
    }
  } catch (err) {
    console.warn('Failed to fetch /api/v1/chat/start, using fallback greeting:', err);
    hideWhatsAppTyping();
    fallbackInitialGreeting();
  }
}

function fallbackInitialGreeting() {
  waSessionId = `chat_${Math.random().toString(36).substring(2, 10)}`;
  waTurnIndex = 1;
  appendWhatsAppBubble(
    'assistant',
    'Namaste! iDigitalStudies Admissions Desk mein aapka swagat hai. Main Aanya hoon. Main courses, fees, syllabus aur batch timings ke baare mein aapki kya madad kar sakti hoon?',
    { timestamp: getWhatsAppTimeString(), advisor: 'Aanya' }
  );
  updateWhatsAppTelemetryHUD({
    session_id: waSessionId,
    turn_index: waTurnIndex,
    latency_ms: 12,
    state: 'GREETING',
    escalate: false
  });
}

/**
 * Send WhatsApp User Message
 */
async function sendWhatsAppMessage(overrideText = null) {
  if (waIsSending) return;

  const inputEl = document.getElementById('wa-chat-input');
  const text = overrideText !== null ? overrideText : (inputEl ? inputEl.value.trim() : '');

  if (!text) return;

  if (inputEl && overrideText === null) {
    inputEl.value = '';
    const sendIcon = document.getElementById('wa-send-btn-icon');
    if (sendIcon) sendIcon.innerText = '🎤';
  }

  // Render user turn
  appendWhatsAppBubble('user', text, {
    timestamp: getWhatsAppTimeString()
  });

  const tenantId = getWaActiveTenantId();
  const lang = getWaActiveLanguage();
  const custName = getWaCustomerName();

  waIsSending = true;
  showWhatsAppTyping();

  const payload = {
    tenant_id: tenantId,
    session_id: waSessionId,
    message: text,
    language: lang,
    channel: 'whatsapp',
    customer_name: custName
  };

  const tStart = performance.now();

  try {
    const res = await fetch('/api/v1/chat/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const tEnd = performance.now();
    waLastLatencyMs = Math.round(tEnd - tStart);
    hideWhatsAppTyping();
    waIsSending = false;

    if (res.ok) {
      const data = await res.json();
      if (data.session_id) waSessionId = data.session_id;
      waTurnIndex = data.turn_index || (waTurnIndex + 1);

      const reply = data.reply || data.response || data.agent_response || 'Ji, main aapki baat samajh rahi hoon.';
      
      appendWhatsAppBubble('assistant', reply, {
        timestamp: getWhatsAppTimeString(),
        escalate: data.escalate,
        escalation_contact: data.escalation_contact,
        state: data.state
      });

      updateWhatsAppTelemetryHUD({
        session_id: waSessionId,
        turn_index: waTurnIndex,
        latency_ms: data.latency_ms !== undefined ? data.latency_ms : waLastLatencyMs,
        state: data.state || 'MAIN_LOOP',
        escalate: data.escalate || false,
        escalation_contact: data.escalation_contact
      });

      updateWhatsAppJsonInspector({
        endpoint: '/api/v1/chat/message',
        request: payload,
        response: data
      });
    } else {
      const errData = await res.json().catch(() => ({}));
      appendWhatsAppBubble('assistant', `⚠️ Server returned ${res.status}: ${errData.detail || 'Unable to process message.'}`, {
        timestamp: getWhatsAppTimeString(),
        isError: true
      });
    }
  } catch (e) {
    hideWhatsAppTyping();
    waIsSending = false;
    appendWhatsAppBubble('assistant', `⚠️ Network error: Could not reach chat server at /api/v1/chat/message. (${e.message})`, {
      timestamp: getWhatsAppTimeString(),
      isError: true
    });
  }
}

/**
 * Append message bubble to WhatsApp chat container
 */
function appendWhatsAppBubble(role, text, meta = {}) {
  const container = document.getElementById('wa-messages-container');
  if (!container) return;

  const bubbleWrapper = document.createElement('div');
  bubbleWrapper.className = `wa-bubble-row ${role === 'user' ? 'wa-row-user' : 'wa-row-assistant'}`;

  const bubble = document.createElement('div');
  bubble.className = `wa-bubble ${role === 'user' ? 'wa-bubble-user' : 'wa-bubble-assistant'} ${meta.isError ? 'wa-bubble-error' : ''}`;

  // Process text for links or brochure patterns
  let formattedText = escapeHtml(text);

  // Auto-link URLs
  formattedText = formattedText.replace(
    /(https?:\/\/[^\s]+)/g,
    '<a href="$1" target="_blank" rel="noopener noreferrer" class="wa-chat-link">$1</a>'
  );

  // Preserve line breaks
  formattedText = formattedText.replace(/\n/g, '<br/>');

  let extraHtml = '';

  // 1. Brochure Card if brochure link or mention is present
  if (text.includes('brochure.pdf') || text.toLowerCase().includes('brochure') && (text.includes('http') || text.includes('bhej diya'))) {
    extraHtml += `
      <div class="wa-attachment-card">
        <div class="wa-pdf-icon">📄</div>
        <div style="flex: 1; min-width: 0;">
          <div style="font-weight: 700; font-size: 0.82rem; color: #FFFFFF; text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">
            Course_Brochure_2026.pdf
          </div>
          <div style="font-size: 0.7rem; color: #9CA3AF;">12 Pages • 2.4 MB • Official Syllabus</div>
        </div>
        <a href="https://idigitalstudies.com/assets/brochure.pdf" target="_blank" class="btn btn-sm btn-outline" style="font-size: 0.7rem; padding: 4px 8px; border-color: #25D366; color: #25D366; text-decoration: none;">
          📥 Download
        </a>
      </div>
    `;
  }

  // 2. Escalation Card if supervisor escalation triggered
  if (meta.escalate || (meta.state === 'ESCALATION') || text.includes('senior counselor') && meta.escalation_contact) {
    const contact = meta.escalation_contact || '+917022721098';
    const cleanNum = contact.replace(/[^0-9]/g, '');
    extraHtml += `
      <div class="wa-escalation-alert-card">
        <div style="display: flex; align-items: center; gap: 8px; font-weight: 700; color: #F59E0B; font-size: 0.8rem;">
          <span>🚨</span> Human Supervisor Escalation Triggered
        </div>
        <div style="font-size: 0.74rem; color: #D1D5DB; margin-top: 4px;">
          Direct connection ready for Senior Admissions Team: <strong>${contact}</strong>
        </div>
        <div style="display: flex; gap: 8px; margin-top: 8px;">
          <a href="tel:${contact}" class="btn btn-sm" style="background: #10B981; color: #FFFFFF; font-size: 0.72rem; padding: 4px 10px; text-decoration: none; font-weight: 700;">
            📞 Call Now
          </a>
          <a href="https://wa.me/${cleanNum}?text=Hello%2C%20I%20was%20chatting%20with%20your%20AI%20and%20would%20like%20to%20speak%20with%20an%20advisor." target="_blank" class="btn btn-sm" style="background: #25D366; color: #0b141a; font-size: 0.72rem; padding: 4px 10px; text-decoration: none; font-weight: 700;">
            💬 Open in WhatsApp
          </a>
        </div>
      </div>
    `;
  }

  bubble.innerHTML = `
    <div class="wa-bubble-content">${formattedText}</div>
    ${extraHtml}
    <div class="wa-bubble-meta">
      <span class="wa-bubble-time">${meta.timestamp || getWhatsAppTimeString()}</span>
      ${role === 'user' ? '<span class="wa-check-icon">✓✓</span>' : ''}
    </div>
  `;

  bubbleWrapper.appendChild(bubble);
  container.appendChild(bubbleWrapper);

  // Auto-scroll to bottom smoothly
  container.scrollTop = container.scrollHeight;
}

/**
 * Typing Indicator Controls
 */
function showWhatsAppTyping() {
  const container = document.getElementById('wa-messages-container');
  let typingBubble = document.getElementById('wa-typing-bubble');

  if (!typingBubble && container) {
    typingBubble = document.createElement('div');
    typingBubble.id = 'wa-typing-bubble';
    typingBubble.className = 'wa-bubble-row wa-row-assistant';
    typingBubble.innerHTML = `
      <div class="wa-bubble wa-bubble-assistant wa-bubble-typing">
        <span class="wa-dot"></span>
        <span class="wa-dot"></span>
        <span class="wa-dot"></span>
      </div>
    `;
    container.appendChild(typingBubble);
  } else if (typingBubble) {
    typingBubble.style.display = 'flex';
  }

  if (container) container.scrollTop = container.scrollHeight;
}

function hideWhatsAppTyping() {
  const typingBubble = document.getElementById('wa-typing-bubble');
  if (typingBubble) {
    typingBubble.style.display = 'none';
  }
}

/**
 * Quick Suggestion Click Handler
 */
function sendWhatsAppQuickPrompt(promptText) {
  const inputEl = document.getElementById('wa-chat-input');
  if (inputEl) inputEl.value = promptText;
  sendWhatsAppMessage(promptText);
}

/**
 * Scenario Test Runners
 */
function runWhatsAppScenario(type) {
  const phone = getWaCustomerPhone();
  switch (type) {
    case 'courses':
      sendWhatsAppQuickPrompt('Hello, what courses and certifications do you offer?');
      break;
    case 'fees':
      sendWhatsAppQuickPrompt('Can you tell me the fee structure and if EMI payment is available?');
      break;
    case 'brochure':
      sendWhatsAppQuickPrompt(`Please send me the full syllabus brochure to my WhatsApp: ${phone}`);
      break;
    case 'escalate':
      sendWhatsAppQuickPrompt('I want to speak directly with the senior admissions counselor or manager right now.');
      break;
    case 'offtopic':
      sendWhatsAppQuickPrompt('Who won the cricket match yesterday and what is the score?');
      break;
    default:
      sendWhatsAppQuickPrompt('Namaste! Mujhe digital marketing course ki details chahiye.');
  }
}

/**
 * Update Telemetry HUD
 */
function updateWhatsAppTelemetryHUD(meta = {}) {
  const elSid = document.getElementById('wa-hud-sid');
  const elTurns = document.getElementById('wa-hud-turns');
  const elLatency = document.getElementById('wa-hud-latency');
  const elState = document.getElementById('wa-hud-state');
  const elEsc = document.getElementById('wa-hud-escalation');

  if (elSid) elSid.innerText = meta.session_id ? meta.session_id.substring(0, 16) + '...' : 'None';
  if (elTurns) elTurns.innerText = meta.turn_index || waTurnIndex;
  
  if (elLatency) {
    const lat = meta.latency_ms !== undefined ? meta.latency_ms : waLastLatencyMs;
    elLatency.innerText = `${lat} ms`;
    elLatency.style.color = lat < 800 ? 'var(--emerald)' : (lat < 2000 ? 'var(--cyan)' : 'var(--amber)');
  }

  if (elState) {
    elState.innerText = meta.state || 'MAIN_LOOP';
    elState.className = meta.escalate ? 'badge badge-rose' : 'badge badge-emerald';
  }

  if (elEsc) {
    if (meta.escalate) {
      elEsc.innerText = meta.escalation_contact || 'Triggered';
      elEsc.style.color = 'var(--rose)';
    } else {
      elEsc.innerText = 'Standard RAG Containment';
      elEsc.style.color = 'var(--emerald)';
    }
  }
}

/**
 * Update JSON Inspector
 */
function updateWhatsAppJsonInspector(data) {
  const inspector = document.getElementById('wa-json-inspector-content');
  if (inspector) {
    inspector.innerText = JSON.stringify(data, null, 2);
  }
}

function toggleWhatsAppJsonInspector() {
  const content = document.getElementById('wa-json-inspector-wrapper');
  const toggleBtn = document.getElementById('wa-json-toggle-btn');
  if (content) {
    const isHidden = content.style.display === 'none';
    content.style.display = isHidden ? 'block' : 'none';
    if (toggleBtn) {
      toggleBtn.innerText = isHidden ? '▲ Hide Raw Payload JSON' : '▼ Inspect Raw Payload JSON';
    }
  }
}

/**
 * Utility HTML escaper
 */
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
