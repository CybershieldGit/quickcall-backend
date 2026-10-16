function getTenantToken() {
  return localStorage.getItem('qwickcall_tenant_token') || localStorage.getItem('qwickcall_tenant_token_' + activeTenantId) || localStorage.getItem('qwickcall_admin_token') || '';
}

function tenantFetch(url, options = {}) {
  const token = getTenantToken();
  const headers = Object.assign({}, options.headers || {}, {
    'X-Tenant-ID': activeTenantId || 'idigitalstudies'
  });
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return fetch(url, Object.assign({}, options, { headers }));
}

function checkTenantAuth() {
  const overlay = document.getElementById('tenant-login-overlay');
  const token = localStorage.getItem('qwickcall_tenant_token');
  const savedTid = localStorage.getItem('qwickcall_tenant_id');

  if (!token || !savedTid || savedTid === 'None' || savedTid === 'none') {
    if (overlay) {
      overlay.style.display = 'flex';
      overlay.classList.add('active');
    }
    window.location.href = '/tenant/login';
    return false;
  }

  activeTenantId = savedTid;
  if (overlay) {
    overlay.style.display = 'none';
    overlay.classList.remove('active');
  }
  return true;
}

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

function autofillTenantDemo() {
  const u = document.getElementById('tenant-login-user');
  const p = document.getElementById('tenant-login-pass');
  if (u) u.value = 'idigitalstudies';
  if (p) p.value = 'tenant123';
}

async function submitTenantLogin(e) {
  if (e) e.preventDefault();
  const userEl = document.getElementById('tenant-login-user');
  const passEl = document.getElementById('tenant-login-pass');
  const errEl = document.getElementById('tenant-login-error');
  const btn = document.getElementById('tenant-login-btn');

  const username = userEl ? userEl.value.trim() : '';
  const password = passEl ? passEl.value : '';

  if (!username || !password) {
    if (errEl) {
      errEl.innerText = 'Please provide both Tenant ID / Email and password.';
      errEl.style.display = 'block';
    }
    return;
  }

  if (errEl) errEl.style.display = 'none';
  if (btn) {
    btn.disabled = true;
    btn.innerText = 'Verifying credentials... ⏳';
  }

  try {
    const res = await fetch('/api/v1/tenant/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    const data = await res.json();

    if (res.ok && data.status === 'success' && data.token) {
      localStorage.setItem('qwickcall_tenant_token', data.token);
      localStorage.setItem('qwickcall_tenant_id', data.tenant.tenant_id);
      localStorage.setItem('qwickcall_tenant_profile', JSON.stringify(data.tenant));

      activeTenantId = data.tenant.tenant_id;

      const overlay = document.getElementById('tenant-login-overlay');
      if (overlay) {
        overlay.style.display = 'none';
        overlay.classList.remove('active');
      }

      showToast(`✓ Welcome, ${data.tenant.business_name}!`);
      loadClientDashboard(activeTenantId);
    } else {
      const msg = data.detail || data.message || 'Invalid credentials. Password is managed by SuperAdmin.';
      if (errEl) {
        errEl.innerText = msg;
        errEl.style.display = 'block';
      }
      showToast(msg, false);
    }
  } catch (err) {
    if (errEl) {
      errEl.innerText = 'Network connection error: ' + err.message;
      errEl.style.display = 'block';
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = 'Sign In to Workspace 🚀';
    }
  }
}

function logoutTenant() {
  try {
    localStorage.removeItem('qwickcall_tenant_token');
    localStorage.removeItem('qwickcall_tenant_id');
    localStorage.removeItem('qwickcall_tenant_profile');
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k && (k.startsWith('qwickcall_tenant') || k.includes('tenant_token'))) {
        localStorage.removeItem(k);
      }
    }
  } catch (e) {
    console.error('Logout cleanup error:', e);
  }
  window.location.replace('/tenant/login');
}

/**
 * Client Workspace Controller — Single Tenant Day-to-Day Hub (#panel-client)
 * Telephony Hub, Dual-PDF RAG, Website Scraper, Live Call Waveform, Leads CRM Pipeline & Transcripts
 */

let activeTenantId = '';
let currentTenantProfile = null;
let clientWaveform = null;
let availablePoolNumbers = [];
let clientCachedLeads = [];
let currentLeadFilter = 'all';

// Initialize Canvas Waveform
function initClientWaveform() {
  if (!clientWaveform && document.getElementById('client-waveform-canvas')) {
    clientWaveform = new AudioWaveformVisualizer('client-waveform-canvas');
  }
}

async function loadTenantsList() {
  // If not authenticated, ensure login gateway is shown and stop
  if (!checkTenantAuth()) {
    return;
  }

  try {
    const res = await fetch('/api/sim/tenants');
    if (res.ok) {
      const data = await res.json();
      const availableTenants = data.tenants || [];
      const switcher = document.getElementById('client-switcher');
      if (switcher) {
        switcher.innerHTML = '';
        if (availableTenants.length > 0) {
          availableTenants.forEach(t => {
            const opt = document.createElement('option');
            opt.value = t.tenant_id;
            opt.innerText = `${t.business_name} (${t.tenant_id})`;
            if (t.tenant_id === activeTenantId) opt.selected = true;
            switcher.appendChild(opt);
          });
          if (!availableTenants.some(t => t.tenant_id === activeTenantId)) {
            // Keep activeTenantId as logged in
            const opt = document.createElement('option');
            opt.value = activeTenantId;
            opt.innerText = activeTenantId;
            opt.selected = true;
            switcher.appendChild(opt);
          }
          switcher.value = activeTenantId;
          loadClientDashboard(activeTenantId);
        } else {
          loadClientDashboard(activeTenantId);
        }
      } else {
        loadClientDashboard(activeTenantId);
      }
    }
  } catch (e) {
    console.warn('Error loading tenants list:', e);
    if (activeTenantId) loadClientDashboard(activeTenantId);
  }
}

// Auto-check on script load
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', checkTenantAuth);
  } else {
    checkTenantAuth();
  }
}

function showEmptyClientDashboard() {
  currentTenantProfile = null;
  const elBiz = document.getElementById('client-biz-name');
  if (elBiz) elBiz.innerText = 'No Client Selected';
  const elTid = document.getElementById('client-tid');
  if (elTid) elTid.innerText = 'None';
  const elAvatar = document.getElementById('client-avatar');
  if (elAvatar) elAvatar.innerText = '—';
  const elAdvisor = document.getElementById('client-advisor');
  if (elAdvisor) elAdvisor.innerText = '—';
  const elEsc = document.getElementById('client-esc');
  if (elEsc) elEsc.innerText = '—';
  const elDid = document.getElementById('client-did');
  if (elDid) elDid.innerHTML = '<span style="color:var(--text-muted); font-size:0.85rem;">None Assigned</span>';
  const elDidSub = document.getElementById('client-did-sub');
  if (elDidSub) elDidSub.innerText = 'Onboard a client to allocate a DID';
  const elKCount = document.getElementById('client-k-count');
  if (elKCount) elKCount.innerText = '0 Chunks';

  const callsTable = document.getElementById('client-calls-tbody');
  if (callsTable) {
    callsTable.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 24px;">No client selected. Onboard a client via the "+ Onboard New Client" button.</td></tr>';
  }
  const leadsTable = document.getElementById('client-leads-tbody');
  if (leadsTable) {
    leadsTable.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 24px;">No client selected.</td></tr>';
  }
  const chunksGrid = document.getElementById('client-chunks-container');
  if (chunksGrid) {
    chunksGrid.innerHTML = '<div style="color: var(--text-muted); padding: 16px;">No knowledge base ingested.</div>';
  }
}

function changeActiveTenant(tenantId) {
  activeTenantId = tenantId;
  loadClientDashboard(tenantId);
}

function openClientDashboard(tenantId) {
  activeTenantId = tenantId;
  if (typeof switchPanel === 'function') {
    switchPanel('client');
  }
}

async function loadClientDashboard(tenantId) {
  if (!tenantId) {
    showEmptyClientDashboard();
    return;
  }
  activeTenantId = tenantId;

  const switcher = document.getElementById('client-switcher');
  if (switcher) switcher.value = tenantId;

  try {
    const res = await tenantFetch(`/api/v1/tenant/details/${encodeURIComponent(tenantId)}`);
    if (res.ok) {
      const data = await res.json();
      currentTenantProfile = data;

      const elBiz = document.getElementById('client-biz-name');
      if (elBiz) elBiz.innerText = data.business_name || 'Business Workspace';

      const elTid = document.getElementById('client-tid');
      if (elTid) elTid.innerText = data.tenant_id;

      const elAvatar = document.getElementById('client-avatar');
      if (elAvatar) elAvatar.innerText = (data.business_name || 'QWK').substring(0, 3).toUpperCase();

      const elAdvisor = document.getElementById('client-advisor');
      if (elAdvisor) elAdvisor.innerText = data.advisor_name || 'Customer Advisor';

      const elEsc = document.getElementById('client-esc');
      if (elEsc) elEsc.innerText = data.escalation_number || '+917022721098';

      const didEl = document.getElementById('client-did');
      const subEl = document.getElementById('client-did-sub');
      if (data.assigned_numbers && data.assigned_numbers.length > 0) {
        if (didEl) didEl.innerHTML = data.assigned_numbers.map(n => `<span style="display:inline-block; margin-right:8px; font-weight:700; color:var(--text-primary);">${escapeHtml(n.number)}</span>`).join('');
        if (subEl) subEl.innerText = `${data.assigned_numbers.length} inbound carrier DID(s) active`;
      } else {
        if (didEl) didEl.innerHTML = '<span style="color:var(--text-muted); font-size:0.85rem;">None Assigned</span>';
        if (subEl) subEl.innerHTML = '<a href="javascript:void(0)" onclick="openManageNumbersModal()" style="color:var(--cyan); text-decoration:underline;">Click to assign a DID →</a>';
      }

      const mnTitle = document.getElementById('mn-tenant-name');
      if (mnTitle) mnTitle.innerText = `${data.business_name} (${data.tenant_id})`;

      // Render Talktime Recharge Balance
      const elWalletVal = document.getElementById('client-wallet-val');
      const elTalktimeEst = document.getElementById('client-talktime-est');
      const elWalletStatus = document.getElementById('client-wallet-status');
      const elRechargeBadge = document.getElementById('client-recharge-badge');

      const wallet = Number(data.wallet_balance != null ? data.wallet_balance : 500);
      const rate = Number(data.rate_per_min != null ? data.rate_per_min : 5.0);
      const minsLeft = rate > 0 ? (wallet / rate).toFixed(0) : "0";

      if (elWalletVal) elWalletVal.innerText = `₹${wallet.toFixed(2)}`;
      if (elTalktimeEst) elTalktimeEst.innerText = `(~${minsLeft} mins @ ₹${rate.toFixed(2)}/min)`;

      if (wallet < 20) {
        if (elRechargeBadge) {
          elRechargeBadge.style.background = 'rgba(239, 68, 68, 0.12)';
          elRechargeBadge.style.borderColor = 'rgba(239, 68, 68, 0.35)';
          elRechargeBadge.style.color = '#991b1b';
        }
        if (elWalletStatus) {
          elWalletStatus.innerHTML = '<span class="badge badge-rose" style="font-size:0.75rem; padding:3px 8px;">⚠️ Low Balance — Top-up Needed</span>';
        }
      } else {
        if (elRechargeBadge) {
          elRechargeBadge.style.background = 'rgba(16, 185, 129, 0.12)';
          elRechargeBadge.style.borderColor = 'rgba(16, 185, 129, 0.35)';
          elRechargeBadge.style.color = '#065f46';
        }
        if (elWalletStatus) {
          elWalletStatus.innerHTML = '<span class="badge badge-emerald" style="font-size:0.75rem; padding:3px 8px;">✓ Active Recharge</span>';
        }
      }

      renderClientAgents(data);
    }
  } catch (e) {
    console.warn('Error loading tenant details:', e);
  }

  loadClientAnalytics(tenantId);
  loadClientLeads(tenantId);
  loadClientRecentCalls(tenantId);
  loadClientKnowledgeChunks();
  initClientWaveform();
  startLeadsRealtimePolling();
}

// -----------------------------------------------------------------------------
// Executive Analytics KPIs
// -----------------------------------------------------------------------------
async function loadClientAnalytics(tenantId) {
  const tid = tenantId || activeTenantId;
  if (!tid) return;

  try {
    const res = await fetch(`/api/analytics/overview?tenant_id=${encodeURIComponent(tid)}`);
    if (res.ok) {
      const data = await res.json();
      const m = data.metrics || {};

      const elCalls = document.getElementById('client-kpi-calls');
      if (elCalls) elCalls.innerText = m.total_calls ?? 0;

      const elAirtime = document.getElementById('client-kpi-airtime');
      if (elAirtime) elAirtime.innerText = m.total_airtime_formatted || '0m 0s';

      const elContain = document.getElementById('client-kpi-containment');
      if (elContain) elContain.innerText = `${m.containment_rate ?? 100}%`;

      const elLeads = document.getElementById('client-kpi-leads');
      if (elLeads) elLeads.innerText = m.total_leads ?? 0;

      const elConv = document.getElementById('client-kpi-conversion');
      if (elConv) elConv.innerText = `${m.lead_capture_rate ?? 0}%`;
    }
  } catch (e) {
    console.warn('Error loading client analytics:', e);
  }
}

// -----------------------------------------------------------------------------
// Leads CRM & Customer Status Board (User Request: "see all calls conversation summary status of the lead")
// -----------------------------------------------------------------------------
async function loadClientLeads(tenantId) {
  const tid = tenantId || activeTenantId;
  const tbody = document.getElementById('client-leads-tbody');
  if (!tbody) return;

  if (!tid) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 24px;">No client selected.</td></tr>';
    return;
  }

  try {
    const res = await tenantFetch(`/api/v1/tenant/leads?tenant_id=${encodeURIComponent(tid)}`);
    if (res.ok) {
      const data = await res.json();
      const leads = data.leads || [];
      clientCachedLeads = leads;

      // Update count badges on filter buttons
      const summary = data.status_summary || {};
      const allCount = leads.length;
      updateLeadFilterBadge('all', allCount);
      updateLeadFilterBadge('hot', summary.hot || 0);
      updateLeadFilterBadge('warm', summary.warm || 0);
      updateLeadFilterBadge('cold', summary.cold || 0);
      updateLeadFilterBadge('callback', summary.callback || 0);
      updateLeadFilterBadge('converted', summary.converted || 0);

      renderLeadsTable();
    }
  } catch (e) {
    console.warn('Error loading client leads:', e);
  }
}

// Background poller: keeps the Leads CRM Pipeline continuously updated in real-time
let leadsAutoRefreshInterval = null;
function startLeadsRealtimePolling() {
  if (leadsAutoRefreshInterval) clearInterval(leadsAutoRefreshInterval);
  leadsAutoRefreshInterval = setInterval(() => {
    if (!document.hidden && typeof activeTenantId !== 'undefined' && activeTenantId) {
      const modalOpen = document.querySelector('.modal-overlay.active, .modal.active');
      if (!modalOpen) {
        loadClientLeads(activeTenantId);
      }
    }
  }, 8000);
}

function updateLeadFilterBadge(statusKey, count) {
  const el = document.getElementById(`lead-cnt-${statusKey}`);
  if (el) el.innerText = count;
}

let leadsCurrentPage = 1;
let leadsPerPage = 10;

function filterLeadsByStatus(status) {
  currentLeadFilter = status;
  leadsCurrentPage = 1;
  document.querySelectorAll('.lead-filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-status') === status);
  });
  renderLeadsTable();
}

function changeLeadsPage(newPage) {
  leadsCurrentPage = Math.max(1, newPage);
  renderLeadsTable();
  const scrollWrapper = document.getElementById('leads-table-scroll-wrapper');
  if (scrollWrapper) {
    scrollWrapper.scrollTo({ top: 0, behavior: 'smooth' });
  }
}

function changeLeadsPerPage(newLimit) {
  leadsPerPage = parseInt(newLimit, 10) || 10;
  leadsCurrentPage = 1;
  renderLeadsTable();
  const scrollWrapper = document.getElementById('leads-table-scroll-wrapper');
  if (scrollWrapper) {
    scrollWrapper.scrollTo({ top: 0, behavior: 'smooth' });
  }
}

function getPaginationPageList(current, total) {
  if (total <= 7) {
    const list = [];
    for (let i = 1; i <= total; i++) list.push(i);
    return list;
  }
  if (current <= 4) {
    return [1, 2, 3, 4, 5, '...', total];
  }
  if (current >= total - 3) {
    return [1, '...', total - 4, total - 3, total - 2, total - 1, total];
  }
  return [1, '...', current - 1, current, current + 1, '...', total];
}

function renderLeadsTable() {
  const tbody = document.getElementById('client-leads-tbody');
  const paginationInfoEl = document.getElementById('leads-pagination-info');
  const paginationNavEl = document.getElementById('leads-pagination-nav');
  if (!tbody) return;

  let filtered = clientCachedLeads || [];
  if (currentLeadFilter !== 'all') {
    filtered = clientCachedLeads.filter(l => (l.status || 'hot').toLowerCase() === currentLeadFilter);
  }

  const totalLeads = filtered.length;

  if (totalLeads === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align: center; color: var(--text-muted); padding: 28px;">
          No leads found in '${currentLeadFilter.toUpperCase()}' pipeline stage.<br/>
          Inbound calls from interested callers automatically appear here as qualified sales leads!
        </td>
      </tr>
    `;
    if (paginationInfoEl) paginationInfoEl.innerHTML = 'Showing <strong>0</strong> of <strong>0</strong> leads';
    if (paginationNavEl) paginationNavEl.innerHTML = '';
    return;
  }

  const totalPages = Math.max(1, Math.ceil(totalLeads / leadsPerPage));
  if (leadsCurrentPage > totalPages) leadsCurrentPage = totalPages;
  if (leadsCurrentPage < 1) leadsCurrentPage = 1;

  const startIndex = (leadsCurrentPage - 1) * leadsPerPage;
  const endIndex = Math.min(startIndex + leadsPerPage, totalLeads);
  const pagedLeads = filtered.slice(startIndex, endIndex);

  // Update pagination info & controls
  if (paginationInfoEl) {
    paginationInfoEl.innerHTML = `Showing <strong>${startIndex + 1}</strong>–<strong>${endIndex}</strong> of <strong>${totalLeads}</strong> leads`;
  }

  if (paginationNavEl) {
    if (totalPages <= 1) {
      paginationNavEl.innerHTML = '';
    } else {
      let navHtml = `
        <button class="pagination-btn" onclick="changeLeadsPage(${leadsCurrentPage - 1})" ${leadsCurrentPage === 1 ? 'disabled' : ''} title="Previous page">
          ← Prev
        </button>
      `;

      const pages = getPaginationPageList(leadsCurrentPage, totalPages);
      pages.forEach(p => {
        if (p === '...') {
          navHtml += '<span class="pagination-ellipsis">…</span>';
        } else {
          navHtml += `
            <button class="pagination-btn ${p === leadsCurrentPage ? 'active' : ''}" onclick="changeLeadsPage(${p})">
              ${p}
            </button>
          `;
        }
      });

      navHtml += `
        <button class="pagination-btn" onclick="changeLeadsPage(${leadsCurrentPage + 1})" ${leadsCurrentPage === totalPages ? 'disabled' : ''} title="Next page">
          Next →
        </button>
      `;
      paginationNavEl.innerHTML = navHtml;
    }
  }

  // Sync per-page selector value if present
  const perPageSelect = document.getElementById('leads-per-page-select');
  if (perPageSelect && perPageSelect.value != leadsPerPage) {
    perPageSelect.value = String(leadsPerPage);
  }

  // Render rows
  tbody.innerHTML = '';
  pagedLeads.forEach(lead => {
    const st = (lead.status || 'hot').toLowerCase();
    const stClass = `status-${st}`;
    const cleanPhone = escapeHtml(lead.phone || 'Unknown');
    const safeLid = escapeHtml(lead.lead_id);
    const safeCid = lead.call_id ? escapeHtml(lead.call_id) : '';

    tbody.innerHTML += `
      <tr>
        <td>
          <strong>${escapeHtml(lead.name || 'Inbound Lead')}</strong><br/>
          <span style="font-size:0.75rem; color:var(--text-muted);">${escapeHtml(lead.captured_at || 'Recent')}</span>
        </td>
        <td>
          <a href="tel:${cleanPhone}" style="color:var(--brown); font-family:var(--font-mono); font-weight:700; text-decoration:none;">
            📞 ${cleanPhone}
          </a>
        </td>
        <td style="font-size:0.84rem; color:var(--text-primary); max-width:320px; white-space:normal; line-height:1.45; font-weight:500;">
          ${escapeHtml(lead.reason || 'General inquiry regarding services')}
          ${lead.is_urgent ? '<span class="badge badge-rose" style="margin-left:6px; font-size:0.68rem;">🚨 URGENT</span>' : ''}
        </td>
        <td>
          ${safeCid 
            ? `<button class="btn btn-outline btn-sm" onclick="openCallTranscriptModal('${safeCid}')" style="font-size:0.72rem; padding:3px 8px; font-family:var(--font-mono);" title="Open Conversation Dialogue">🎙️ ${safeCid.substring(0, 10)}...</button>` 
            : '<span style="color:var(--text-muted); font-size:0.75rem;">Manual Entry</span>'}
        </td>
        <td>
          <select class="lead-status-select ${stClass}" onchange="changeLeadStatus('${safeLid}', this.value)" title="Change customer status in pipeline">
            <option value="hot" ${st === 'hot' ? 'selected' : ''}>🔥 Hot Lead</option>
            <option value="warm" ${st === 'warm' ? 'selected' : ''}>⚡ Warm / Follow-up</option>
            <option value="cold" ${st === 'cold' ? 'selected' : ''}>❄️ Cold</option>
            <option value="callback" ${st === 'callback' ? 'selected' : ''}>📞 Callback Pending</option>
            <option value="converted" ${st === 'converted' ? 'selected' : ''}>✅ Converted / Won</option>
          </select>
        </td>
        <td style="display:flex; gap:6px;">
          ${st !== 'converted' ? `
            <button class="btn btn-outline btn-sm" onclick="changeLeadStatus('${safeLid}', 'converted')" style="font-size:0.72rem; padding:4px 8px; border-color:rgba(16,185,129,0.4); color:var(--emerald);" title="Mark Deal as Won">
              ✓ Convert
            </button>
          ` : '<span class="badge badge-emerald">Won 🎉</span>'}
          ${safeCid ? `
            <button class="btn btn-primary btn-sm" onclick="openCallTranscriptModal('${safeCid}')" style="font-size:0.72rem; padding:4px 8px;">
              Transcript →
            </button>
          ` : ''}
        </td>
      </tr>
    `;
  });
}

async function changeLeadStatus(leadId, newStatus) {
  try {
    const res = await tenantFetch(`/api/v1/tenant/leads/${encodeURIComponent(leadId)}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus })
    });
    if (res.ok) {
      showToast(`✓ Lead status updated to '${newStatus.toUpperCase()}'!`);
      // Update local cache
      const item = clientCachedLeads.find(l => l.lead_id === leadId);
      if (item) item.status = newStatus;
      loadClientLeads(activeTenantId);
    } else {
      const err = await res.json();
      showToast(err.detail || 'Failed to update status', false);
    }
  } catch (e) {
    showToast(e.message, false);
  }
}

// Manual Lead Modal Handlers
function openAddLeadModal() {
  const modal = document.getElementById('add-lead-modal');
  if (modal) {
    modal.classList.add('active');
    document.body.style.overflow = 'hidden';
  }
}

function closeAddLeadModal() {
  const modal = document.getElementById('add-lead-modal');
  if (modal) {
    modal.classList.remove('active');
    document.body.style.overflow = '';
  }
}

async function submitAddLead(e) {
  e.preventDefault();
  const name = document.getElementById('lead-name-input')?.value.trim();
  const phone = document.getElementById('lead-phone-input')?.value.trim();
  const reason = document.getElementById('lead-reason-input')?.value.trim();
  const status = document.getElementById('lead-status-input')?.value || 'hot';

  if (!phone) {
    showToast('Customer phone number is required', false);
    return;
  }

  try {
    const res = await tenantFetch('/api/v1/tenant/leads', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tenant_id: activeTenantId,
        name: name || 'Inbound Lead',
        phone: phone,
        reason: reason || 'Manual CRM entry',
        status: status
      })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast(`✓ Lead for '${phone}' added to pipeline!`);
      closeAddLeadModal();
      document.getElementById('add-lead-form')?.reset();
      loadClientLeads(activeTenantId);
    } else {
      showToast(data.detail || 'Failed to add lead', false);
    }
  } catch (err) {
    showToast(err.message, false);
  }
}

let clientCachedCalls = [];
let callsCurrentPage = 1;
let callsPerPage = 10;

function changeCallsPage(newPage) {
  callsCurrentPage = Math.max(1, newPage);
  renderRecentCallsTable();
  const scrollWrapper = document.getElementById('calls-table-scroll-wrapper');
  if (scrollWrapper) {
    scrollWrapper.scrollTo({ top: 0, behavior: 'smooth' });
  }
}

function changeCallsPerPage(newLimit) {
  callsPerPage = parseInt(newLimit, 10) || 10;
  callsCurrentPage = 1;
  renderRecentCallsTable();
  const scrollWrapper = document.getElementById('calls-table-scroll-wrapper');
  if (scrollWrapper) {
    scrollWrapper.scrollTo({ top: 0, behavior: 'smooth' });
  }
}

function renderRecentCallsTable() {
  const tbody = document.getElementById('client-calls-tbody');
  const paginationInfoEl = document.getElementById('calls-pagination-info');
  const paginationNavEl = document.getElementById('calls-pagination-nav');
  if (!tbody) return;

  const totalCalls = (clientCachedCalls || []).length;

  if (totalCalls === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 24px;">No calls logged for this client yet. Click "Call My Mobile Phone" to test live!</td></tr>`;
    if (paginationInfoEl) paginationInfoEl.innerHTML = 'Showing <strong>0</strong> of <strong>0</strong> calls';
    if (paginationNavEl) paginationNavEl.innerHTML = '';
    return;
  }

  const totalPages = Math.max(1, Math.ceil(totalCalls / callsPerPage));
  if (callsCurrentPage > totalPages) callsCurrentPage = totalPages;
  if (callsCurrentPage < 1) callsCurrentPage = 1;

  const startIndex = (callsCurrentPage - 1) * callsPerPage;
  const endIndex = Math.min(startIndex + callsPerPage, totalCalls);
  const pagedCalls = clientCachedCalls.slice(startIndex, endIndex);

  // Update pagination info & controls
  if (paginationInfoEl) {
    paginationInfoEl.innerHTML = `Showing <strong>${startIndex + 1}</strong>–<strong>${endIndex}</strong> of <strong>${totalCalls}</strong> calls`;
  }

  if (paginationNavEl) {
    if (totalPages <= 1) {
      paginationNavEl.innerHTML = '';
    } else {
      let navHtml = `
        <button class="pagination-btn" onclick="changeCallsPage(${callsCurrentPage - 1})" ${callsCurrentPage === 1 ? 'disabled' : ''} title="Previous page">
          ← Prev
        </button>
      `;

      const pages = getPaginationPageList(callsCurrentPage, totalPages);
      pages.forEach(p => {
        if (p === '...') {
          navHtml += '<span class="pagination-ellipsis">…</span>';
        } else {
          navHtml += `
            <button class="pagination-btn ${p === callsCurrentPage ? 'active' : ''}" onclick="changeCallsPage(${p})">
              ${p}
            </button>
          `;
        }
      });

      navHtml += `
        <button class="pagination-btn" onclick="changeCallsPage(${callsCurrentPage + 1})" ${callsCurrentPage === totalPages ? 'disabled' : ''} title="Next page">
          Next →
        </button>
      `;
      paginationNavEl.innerHTML = navHtml;
    }
  }

  // Sync per-page selector value if present
  const perPageSelect = document.getElementById('calls-per-page-select');
  if (perPageSelect && perPageSelect.value != callsPerPage) {
    perPageSelect.value = String(callsPerPage);
  }

  tbody.innerHTML = '';
  pagedCalls.forEach(c => {
    const sentLower = (c.caller_sentiment || 'neutral').toLowerCase();
    const sentColor = sentLower.includes('positive') || sentLower.includes('interest') 
      ? 'var(--emerald)' 
      : (sentLower.includes('confused') || sentLower.includes('escalat') ? 'var(--amber)' : 'var(--cyan)');
    
    const durSec = Math.round(c.duration || 0);
    const safeCid = escapeHtml(c.call_id || '');
    const summaryText = c.summary || c.executive_notes || 'Call completed normally.';
    const concernText = c.primary_concern ? `<div style="margin-top:4px; font-size:0.75rem; color:#A5B4FC;"><strong>Inquiry:</strong> ${escapeHtml(c.primary_concern)}</div>` : '';
    const actionText = c.action_required ? `<div style="margin-top:4px; font-size:0.74rem; color:var(--emerald);"><strong>Next Action:</strong> ${escapeHtml(c.action_required)}</div>` : '';

    tbody.innerHTML += `
      <tr class="call-row-clickable" onclick="openCallTranscriptModal('${safeCid}')" title="Click to view full recorded conversation transcript">
        <td>
          <code style="color: var(--brown); font-size: 0.82rem; font-family:var(--font-mono); font-weight:600;">${c.call_id ? c.call_id.substring(0, 15) : '-'}</code><br/>
          <span style="font-size:0.72rem; color:var(--text-muted);">${c.start_time ? c.start_time.substring(0, 16).replace('T', ' ') : ''}</span>
        </td>
        <td>
          <strong style="color:var(--text-primary);">${escapeHtml(c.caller_number || 'Unknown')}</strong>
        </td>
        <td><strong style="color:var(--text-primary);">${durSec}s</strong></td>
        <td><span class="badge ${c.transferred ? 'badge-indigo' : 'badge-emerald'}">${escapeHtml(c.outcome)}</span></td>
        <td style="color: ${sentColor}; font-weight: 700;">${escapeHtml(c.caller_sentiment || 'neutral')}</td>
        <td style="font-size: 0.84rem; color: var(--text-primary); max-width: 320px; white-space: normal; line-height: 1.45; font-weight: 500;">
          ${escapeHtml(summaryText)}
          ${concernText}
          ${actionText}
        </td>
        <td>
          <button class="btn btn-outline btn-sm" onclick="event.stopPropagation(); openCallTranscriptModal('${safeCid}')" style="font-size: 0.74rem;">
            🎙️ View Transcript
          </button>
        </td>
      </tr>
    `;
  });
}

// -----------------------------------------------------------------------------
// Recent Calls & AI Executive Summaries
// -----------------------------------------------------------------------------
async function loadClientRecentCalls(tenantId) {
  const tid = tenantId || activeTenantId;
  const tbody = document.getElementById('client-calls-tbody');
  if (!tbody) return;
  if (!tid) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 24px;">No client selected.</td></tr>';
    return;
  }

  try {
    const res = await fetch(`/api/call/recent?tenant_id=${encodeURIComponent(tid)}`);
    if (res.ok) {
      const data = await res.json();
      clientCachedCalls = data.calls || [];
      renderRecentCallsTable();
    }
  } catch (e) {
    console.warn('Error loading calls:', e);
  }
}

// -----------------------------------------------------------------------------
// Interactive Call Transcript Modal
// -----------------------------------------------------------------------------
async function openCallTranscriptModal(callId) {
  if (!callId) return;
  const modal = document.getElementById('call-transcript-modal');
  const container = document.getElementById('transcript-modal-turns-container');
  if (!modal || !container) return;

  document.getElementById('t-modal-sid').innerText = callId;
  document.getElementById('t-modal-caller').innerText = '...';
  document.getElementById('t-modal-duration').innerText = '...';
  document.getElementById('t-modal-outcome').innerText = '...';
  document.getElementById('t-modal-sentiment').innerText = '...';
  document.getElementById('t-modal-turns').innerText = 'Loading...';

  container.innerHTML = `
    <div style="text-align: center; color: var(--text-muted); padding: 50px;">
      <div style="font-size: 1.8rem; margin-bottom: 12px;">⏳</div>
      <div>Loading full conversation dialogue for <code style="color: #A5B4FC;">${escapeHtml(callId)}</code>...</div>
    </div>
  `;

  modal.classList.add('active');
  document.body.style.overflow = 'hidden';

  try {
    const res = await fetch(`/api/call/${encodeURIComponent(callId)}/transcript`);
    if (!res.ok) throw new Error(`HTTP ${res.status}: Failed to load transcript`);
    const data = await res.json();
    const meta = data.metadata || {};
    const turns = data.transcripts || [];

    document.getElementById('t-modal-sid').innerHTML = `
      <span title="${escapeHtml(callId)}">${escapeHtml(callId.substring(0, 16))}...</span>
      <button onclick="navigator.clipboard.writeText('${escapeHtml(callId)}'); showToast('Call SID copied!');" style="background: none; border: none; cursor: pointer; color: var(--accent); font-size: 0.75rem;" title="Copy Call SID">📋</button>
    `;
    document.getElementById('t-modal-caller').innerText = meta.caller_number || 'Unknown';
    document.getElementById('t-modal-duration').innerText = `${Math.round(meta.duration || 0)}s`;

    const outcomeEl = document.getElementById('t-modal-outcome');
    outcomeEl.innerText = meta.outcome || 'completed';
    outcomeEl.className = `badge ${meta.transferred ? 'badge-indigo' : 'badge-emerald'}`;

    const sentEl = document.getElementById('t-modal-sentiment');
    sentEl.innerText = meta.caller_sentiment || 'neutral';
    sentEl.style.color = (meta.caller_sentiment || '').toLowerCase().includes('positive') ? 'var(--emerald)' : 'var(--cyan)';

    document.getElementById('t-modal-turns').innerText = `${turns.length} Recorded Dialogue Turns`;

    if (turns.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; color: var(--text-muted); padding: 50px; background: rgba(255,255,255,0.02); border-radius: 12px; border: 1px dashed var(--border);">
          <div style="font-size: 2rem; margin-bottom: 10px;">🔇</div>
          <h4 style="color: var(--text-primary); margin-bottom: 6px;">No Speech Dialogue Recorded</h4>
          <p style="font-size: 0.85rem;">This call disconnected before bidirectional audio turns were processed.</p>
        </div>
      `;
    } else {
      let turnsHtml = '';
      turns.forEach(t => {
        const isAgent = (t.speaker || '').toLowerCase() === 'agent';
        const speakerName = isAgent ? '🤖 AI Voice Agent' : `👤 Caller (${escapeHtml(meta.caller_number || 'Caller')})`;
        const wrapperClass = isAgent ? 'turn-agent' : 'turn-caller';

        turnsHtml += `
          <div class="turn-bubble-wrapper ${wrapperClass}">
            <div class="turn-meta">
              <span class="badge ${isAgent ? 'badge-indigo' : 'badge-cyan'}">${speakerName}</span>
              <span>Turn #${t.turn_index}</span>
            </div>
            <div class="turn-bubble">${escapeHtml(t.text)}</div>
          </div>
        `;
      });

      if (meta.summary || meta.executive_notes || meta.primary_concern) {
        turnsHtml += `
          <div style="margin-top: 18px; background: var(--bg-surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px;">
            <div style="font-size: 0.88rem; font-weight: 700; color: var(--brown); margin-bottom: 8px;">
              📊 AI Executive Call Brief & Lead Notes
            </div>
            <div style="font-size: 0.84rem; color: var(--text-primary); line-height: 1.5; font-weight: 500;">
              ${meta.summary ? `<div style="margin-bottom: 8px;"><strong>Summary:</strong> ${escapeHtml(meta.summary)}</div>` : ''}
              ${meta.primary_concern ? `<div style="margin-bottom: 8px;"><strong>Primary Inquiry:</strong> ${escapeHtml(meta.primary_concern)}</div>` : ''}
              ${meta.action_required ? `<div style="color:var(--emerald);"><strong>Action Required:</strong> ${escapeHtml(meta.action_required)}</div>` : ''}
            </div>
          </div>
        `;
      }

      container.innerHTML = turnsHtml;
    }
  } catch (err) {
    container.innerHTML = `
      <div style="text-align: center; color: var(--rose); padding: 40px;">
        <div style="font-size: 1.8rem; margin-bottom: 8px;">⚠️</div>
        <div>Failed to load call transcript: ${escapeHtml(err.message)}</div>
      </div>
    `;
  }
}

function closeCallTranscriptModal() {
  const modal = document.getElementById('call-transcript-modal');
  if (modal) {
    modal.classList.remove('active');
    document.body.style.overflow = '';
  }
}

function handleTranscriptModalBackdrop(event) {
  if (event.target && event.target.id === 'call-transcript-modal') {
    closeCallTranscriptModal();
  }
}

// -----------------------------------------------------------------------------
// Telephony Phone Call Dispatcher & Fast Reasoning Chat
// -----------------------------------------------------------------------------
async function triggerClientPhoneCall() {
  const phoneInput = document.getElementById('client-phone-input');
  const toNum = phoneInput ? phoneInput.value.trim() : '';
  if (!toNum) {
    showToast('Enter your phone number', false);
    return;
  }

  const statusEl = document.getElementById('client-call-status');
  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.style.background = 'rgba(99,102,241,0.15)';
    statusEl.style.color = '#A5B4FC';
    statusEl.innerText = `Dispatching AI call to ${toNum}...`;
  }

  try {
    const res = await tenantFetch('/api/v1/outbound/make-call', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone_number: toNum, tenant_id: activeTenantId })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`✓ Call dispatched to ${toNum}!`);
      if (statusEl) {
        statusEl.style.background = 'rgba(16,185,129,0.15)';
        statusEl.style.color = 'var(--emerald)';
        statusEl.innerHTML = `✓ Call initiated! Call SID: <code>${escapeHtml(data.call_sid || 'sip_call')}</code>. Your phone will ring in 3 seconds.`;
      }
      setTimeout(() => loadClientRecentCalls(activeTenantId), 6000);
    } else {
      const errMsg = data.detail || data.message || 'Call dispatch failed';
      showToast(errMsg, false);
      if (statusEl) {
        statusEl.style.background = 'rgba(239,68,68,0.15)';
        statusEl.style.color = 'var(--rose)';
        statusEl.innerText = `✗ Failed: ${errMsg}`;
      }
    }
  } catch (e) {
    showToast(e.message, false);
    if (statusEl) {
      statusEl.style.background = 'rgba(239,68,68,0.15)';
      statusEl.style.color = 'var(--rose)';
      statusEl.innerText = `✗ Error: ${e.message}`;
    }
  }
}

// -----------------------------------------------------------------------------
// Quick Test Call Modal Controller
// -----------------------------------------------------------------------------
function openTestCallModal() {
  const modal = document.getElementById('test-call-modal');
  if (modal) {
    const tenantName = document.getElementById('client-biz-name')?.innerText || 'Active Tenant';
    const numDisplay = document.getElementById('modal-test-call-tenant-name');
    if (numDisplay) numDisplay.innerText = tenantName;

    const labPhone = document.getElementById('client-phone-input')?.value;
    const modalInput = document.getElementById('modal-test-phone-input');
    if (modalInput && labPhone) modalInput.value = labPhone;

    const statusEl = document.getElementById('modal-test-call-status');
    if (statusEl) statusEl.style.display = 'none';

    modal.classList.add('active');
    document.body.style.overflow = 'hidden';
  }
}

function closeTestCallModal() {
  const modal = document.getElementById('test-call-modal');
  if (modal) {
    modal.classList.remove('active');
    document.body.style.overflow = '';
  }
}

async function triggerModalTestCall() {
  const phoneInput = document.getElementById('modal-test-phone-input');
  const toNum = phoneInput ? phoneInput.value.trim() : '';
  if (!toNum) {
    showToast('Please enter your mobile phone number', false);
    return;
  }

  const labPhone = document.getElementById('client-phone-input');
  if (labPhone) labPhone.value = toNum;

  const btn = document.getElementById('modal-test-call-submit-btn');
  const origBtnText = btn ? btn.innerHTML : '📞 Call My Phone Now';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '⏳ Dialing Mobile Gateway...';
  }

  const statusEl = document.getElementById('modal-test-call-status');
  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.style.background = 'rgba(99,102,241,0.15)';
    statusEl.style.color = '#A5B4FC';
    statusEl.innerText = `Dispatching AI telephone call to ${toNum}...`;
  }

  try {
    const res = await tenantFetch('/api/v1/outbound/make-call', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone_number: toNum, tenant_id: activeTenantId })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`✓ Call dispatched to ${toNum}!`);
      if (statusEl) {
        statusEl.style.background = 'rgba(16,185,129,0.15)';
        statusEl.style.color = 'var(--emerald)';
        statusEl.innerHTML = `✓ <strong>Call Initiated!</strong> Call SID: <code>${escapeHtml(data.call_sid || 'sip_call')}</code><br/>Your phone will ring in 3 seconds. Pick up to speak with the AI voicebot live!`;
      }
      setTimeout(() => loadClientRecentCalls(activeTenantId), 6000);
    } else {
      const errMsg = data.detail || data.message || 'Call dispatch failed';
      showToast(errMsg, false);
      if (statusEl) {
        statusEl.style.background = 'rgba(239,68,68,0.15)';
        statusEl.style.color = 'var(--rose)';
        statusEl.innerText = `✗ Failed: ${errMsg}`;
      }
    }
  } catch (e) {
    showToast(e.message, false);
    if (statusEl) {
      statusEl.style.background = 'rgba(239,68,68,0.15)';
      statusEl.style.color = 'var(--rose)';
      statusEl.innerText = `✗ Error: ${e.message}`;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origBtnText;
    }
  }
}

window.openTestCallModal = openTestCallModal;
window.closeTestCallModal = closeTestCallModal;
window.triggerModalTestCall = triggerModalTestCall;

let currentChatSessionId = null;

async function sendClientChat() {
  const input = document.getElementById('client-chat-input');
  const query = input ? input.value.trim() : '';
  if (!query) return;

  const chatBox = document.getElementById('client-chat-box');
  const latencyEl = document.getElementById('chat-latency');
  const stateEl = document.getElementById('chat-state');

  // Look up active agent assigned to selected DID or switcher
  let activeAgentId = null;
  const didSelect = document.getElementById('quick-switch-did-select');
  if (didSelect && didSelect.value && currentTenantProfile && currentTenantProfile.assigned_numbers) {
    const numObj = currentTenantProfile.assigned_numbers.find(n => n.number === didSelect.value);
    if (numObj && numObj.assigned_agent_id) activeAgentId = numObj.assigned_agent_id;
  }
  if (!activeAgentId && cachedClientAgents && cachedClientAgents.length > 0) {
    const def = cachedClientAgents.find(a => a.is_default);
    activeAgentId = def ? def.agent_id : cachedClientAgents[0].agent_id;
  }

  // Append user message with high contrast readable styling
  if (chatBox) {
    chatBox.innerHTML += `
      <div style="align-self: flex-end; background: #0284C7; color: #FFFFFF; padding: 9px 14px; border-radius: 12px 12px 3px 12px; max-width: 85%; font-size: 0.85rem; font-weight: 500; box-shadow: 0 2px 8px rgba(2, 132, 199, 0.25);">
        <strong style="color: #E0F2FE;">You:</strong> ${escapeHtml(query)}
      </div>
    `;
    chatBox.scrollTop = chatBox.scrollHeight;
  }
  if (input) input.value = '';
  if (stateEl) stateEl.innerText = 'REASONING...';

  const t0 = performance.now();
  try {
    const res = await fetch('/api/v1/chat/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: query,
        query: query,
        tenant_id: activeTenantId,
        agent_id: activeAgentId,
        session_id: currentChatSessionId
      })
    });
    const dur = Math.round(performance.now() - t0);
    if (latencyEl) latencyEl.innerText = `${dur}ms`;
    if (stateEl) stateEl.innerText = 'IDLE';

    if (res.ok) {
      const data = await res.json();
      if (data.session_id) currentChatSessionId = data.session_id;
      const reply = data.reply || data.response || data.message || 'Understood.';
      const isEscalate = Boolean(data.escalate);
      const badge = isEscalate ? `<span style="background: rgba(239, 68, 68, 0.2); color: #DC2626; border: 1px solid rgba(239, 68, 68, 0.4); padding: 1px 6px; border-radius: 4px; font-size: 0.68rem; margin-left: 6px; font-weight: 700;">Transfer to Human</span>` : '';

      // Determine active agent name and role
      let activeAgName = 'AI Agent';
      let activeAgRole = 'AI Agent';
      let activeAgEmoji = '🤖';
      if (activeAgentId && cachedClientAgents && cachedClientAgents.length > 0) {
        const ag = cachedClientAgents.find(a => a.agent_id === activeAgentId);
        if (ag) {
          activeAgName = ag.name;
          activeAgRole = ag.role_title;
          activeAgEmoji = getRoleEmoji(ag.role_title);
        }
      }

      if (chatBox) {
        chatBox.innerHTML += `
          <div style="align-self: flex-start; background: #F8FAFC; border: 1.5px solid #CBD5E1; padding: 10px 14px; border-radius: 12px 12px 12px 3px; max-width: 88%; font-size: 0.85rem; color: #0F172A; box-shadow: 0 1px 4px rgba(0,0,0,0.05);">
            <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px;">
              <span>${activeAgEmoji}</span>
              <strong style="color: var(--indigo); font-size: 0.88rem;">${escapeHtml(activeAgName)}</strong>
              <span style="font-size: 0.72rem; color: var(--text-muted); font-weight: 600;">(${escapeHtml(activeAgRole)})</span>
              ${badge}
            </div>
            <div style="line-height: 1.45; color: #1E293B; font-weight: 400;">${escapeHtml(reply)}</div>
          </div>
        `;
        chatBox.scrollTop = chatBox.scrollHeight;
      }
    } else {
      throw new Error(`HTTP ${res.status}`);
    }
  } catch (e) {
    if (stateEl) stateEl.innerText = 'ERROR';
    if (chatBox) {
      chatBox.innerHTML += `<div style="align-self: flex-start; color: var(--rose); font-size: 0.8rem;">Error: ${escapeHtml(e.message)}</div>`;
    }
  }
}

async function clearClientChat() {
  const box = document.getElementById('client-chat-box');
  if (currentChatSessionId) {
    try {
      await fetch('/api/v1/chat/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: currentChatSessionId })
      });
    } catch (_) {}
    currentChatSessionId = null;
  }
  if (box) {
    box.innerHTML = `
      <div style="align-self: flex-start; background: rgba(99, 102, 241, 0.15); border: 1px solid rgba(99, 102, 241, 0.3); padding: 8px 12px; border-radius: 10px 10px 10px 2px; max-width: 90%; font-size: 0.82rem;">
        <strong>AI Agent:</strong> Ready. Ask any question to test conversational accuracy.
      </div>
    `;
  }
}

// -----------------------------------------------------------------------------
// Dual-PDF Knowledge Base & Website Crawler
// -----------------------------------------------------------------------------
async function uploadSlotPdf(slotName) {
  const fileInput = document.getElementById(`upload-${slotName}-input`);
  const statusEl = document.getElementById(`status-${slotName}`);
  const btn = document.getElementById(`btn-upload-${slotName}`);

  if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
    showToast('Please select a PDF file first', false);
    return;
  }

  const file = fileInput.files[0];
  const formData = new FormData();
  formData.append('file', file);
  formData.append('tenant_id', activeTenantId);
  formData.append('slot_type', slotName === 'slot1' ? 'company_overview' : 'product_catalog');

  if (btn) {
    btn.disabled = true;
    btn.innerText = '⏳ Vectorizing...';
  }
  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.innerText = 'Extracting text and computing embeddings...';
  }

  try {
    const res = await tenantFetch('/api/v1/tenant/upload-pdf', {
      method: 'POST',
      body: formData
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast(`✓ Ingested ${data.chunks_indexed || 0} chunks from ${file.name}!`);
      if (statusEl) statusEl.innerHTML = `<span style="color:var(--emerald);">✓ ${data.chunks_indexed || 0} chunks indexed</span>`;
      loadClientKnowledgeChunks();
    } else {
      showToast(data.detail || 'Upload failed', false);
      if (statusEl) statusEl.innerHTML = `<span style="color:var(--rose);">✗ ${escapeHtml(data.detail || 'Upload error')}</span>`;
    }
  } catch (e) {
    showToast(e.message, false);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = 'Upload & Vectorize';
    }
  }
}

async function submitWebsiteScrape() {
  const urlInput = document.getElementById('client-scrape-url');
  const url = urlInput ? urlInput.value.trim() : '';
  const btn = document.getElementById('btn-scrape-url');
  const statusEl = document.getElementById('client-scrape-status');

  if (!url) {
    showToast('Enter client website URL', false);
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.innerText = '🌐 Crawling & Ingesting...';
  }
  if (statusEl) {
    statusEl.style.display = 'block';
    statusEl.innerText = 'Fetching page text and computing vector embeddings...';
  }

  try {
    const res = await tenantFetch('/api/v1/tenant/scrape-website', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tenant_id: activeTenantId, website_url: url })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast(`✓ Ingested ${data.chunks_indexed} website chunks!`);
      if (statusEl) statusEl.innerHTML = `<span style="color:var(--emerald);">✓ Ingested ${data.chunks_indexed} chunks from live site!</span>`;
      loadClientKnowledgeChunks();
    } else {
      showToast(data.detail || 'Scraping failed', false);
      if (statusEl) statusEl.innerHTML = `<span style="color:var(--rose);">✗ ${escapeHtml(data.detail || 'Scrape error')}</span>`;
    }
  } catch (e) {
    showToast(e.message, false);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = 'Crawl & Index Website';
    }
  }
}

async function loadClientKnowledgeChunks() {
  const container = document.getElementById('client-chunks-container');
  const badge = document.getElementById('rag-chunk-badge');
  const countStat = document.getElementById('client-k-count');
  if (!container) return;

  if (!activeTenantId) {
    container.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 18px;">No client selected.</div>';
    if (badge) badge.innerText = '0 Chunks Indexed';
    if (countStat) countStat.innerText = '0 Chunks';
    return;
  }

  try {
    const res = await tenantFetch(`/api/v1/tenant/knowledge?tenant_id=${encodeURIComponent(activeTenantId)}`);
    if (res.ok) {
      const data = await res.json();
      const chunks = data.chunks || [];
      if (badge) badge.innerText = `${chunks.length} Chunks Indexed`;
      if (countStat) countStat.innerText = `${chunks.length} Chunks`;

      if (chunks.length === 0) {
        container.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 18px;">No chunks indexed yet.<br/>Upload Slot 1 / Slot 2 PDF or crawl a website to enable AI voice RAG answers.</div>';
        return;
      }

      container.innerHTML = '';
      chunks.forEach((c, idx) => {
        container.innerHTML += `
          <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px;">
            <div style="display: flex; justify-content: space-between; font-size: 0.72rem; color: var(--cyan); margin-bottom: 4px;">
              <span>Chunk #${idx + 1}</span>
              <span style="color: var(--text-muted); font-family:var(--font-mono);">${escapeHtml(c.source_doc || '')}</span>
            </div>
            <div style="line-height: 1.4;">${escapeHtml(c.chunk_text)}</div>
          </div>
        `;
      });
    }
  } catch (e) {
    container.innerHTML = `<div style="color: var(--rose); padding: 10px;">Error loading chunks: ${e.message}</div>`;
  }
}

// -----------------------------------------------------------------------------
// Telephony Numbers Modal
// -----------------------------------------------------------------------------
async function loadAvailableNumbers() {
  try {
    const res = await tenantFetch('/api/v1/tenant/available-numbers');
    if (res.ok) {
      const data = await res.json();
      availablePoolNumbers = data.numbers || [];

      // Populate onboarding wizard selector
      const wzSelect = document.getElementById('wz-did-select');
      if (wzSelect) {
        const prev = wzSelect.value;
        wzSelect.innerHTML = `
          <option value="">⚡ Auto-Allocate from Available Pool</option>
          ${availablePoolNumbers.map(n => `<option value="${escapeHtml(n.number)}">${escapeHtml(n.number)} (${escapeHtml((n.vendor || 'carrier').toUpperCase())} DID - Available)</option>`).join('')}
          <option value="custom">✏️ Enter Custom Phone Number / Extension</option>
        `;
        if (prev) wzSelect.value = prev;
      }

      // Populate manage numbers modal selector
      const mnSelect = document.getElementById('mn-available-select');
      if (mnSelect) {
        mnSelect.innerHTML = `
          <option value="">-- Choose Available Number --</option>
          ${availablePoolNumbers.map(n => `<option value="${escapeHtml(n.number)}">${escapeHtml(n.number)} (${escapeHtml((n.vendor || 'carrier').toUpperCase())})</option>`).join('')}
          <option value="custom">✏️ Enter Custom Phone Number / Extension</option>
        `;
      }
    }
  } catch (e) {
    console.warn('Error loading pool numbers:', e);
  }
}

function openManageNumbersModal() {
  loadAvailableNumbers();
  const modal = document.getElementById('manage-numbers-modal');
  if (!modal) return;

  const mnTitle = document.getElementById('mn-tenant-name');
  if (mnTitle && currentTenantProfile) {
    mnTitle.innerText = `${currentTenantProfile.business_name} (${currentTenantProfile.tenant_id})`;
  }

  const container = document.getElementById('mn-assigned-list');
  if (container && currentTenantProfile) {
    const assigned = currentTenantProfile.assigned_numbers || [];
    if (assigned.length === 0) {
      container.innerHTML = `
        <div style="color: var(--text-muted); font-size: 0.85rem; padding: 14px; background: rgba(255,255,255,0.02); border-radius: 8px; border: 1px dashed var(--border); text-align: center;">
          No phone numbers currently assigned to this client.<br/>Select an available number below to bind one.
        </div>
      `;
    } else {
      container.innerHTML = '';
      assigned.forEach(n => {
        container.innerHTML += `
          <div style="display: flex; align-items: center; justify-content: space-between; background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px;">
            <div>
              <strong style="font-family: var(--font-mono); font-size: 0.95rem; color: var(--text-primary);">${escapeHtml(n.number)}</strong>
              <span class="badge badge-indigo" style="margin-left: 8px; font-size: 0.72rem;">${escapeHtml((n.vendor || 'carrier').toUpperCase())}</span>
            </div>
            <button class="btn btn-outline btn-sm" onclick="unassignNumberFromTenant('${escapeHtml(n.number)}')" style="border-color: rgba(239,68,68,0.4); color: var(--rose); font-size: 0.75rem; padding: 4px 10px;">
              Unassign ✕
            </button>
          </div>
        `;
      });
    }
  }

  modal.classList.add('active');
  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

function closeManageNumbersModal() {
  const modal = document.getElementById('manage-numbers-modal');
  if (modal) {
    modal.classList.remove('active');
    modal.style.display = 'none';
    document.body.style.overflow = '';
  }
}

function handleManageNumbersBackdrop(e) {
  if (e.target && e.target.id === 'manage-numbers-modal') {
    closeManageNumbersModal();
  }
}

function toggleCustomModalNumber(val) {
  const custom = document.getElementById('mn-custom-input');
  if (custom) custom.style.display = (val === 'custom') ? 'block' : 'none';
}

async function submitModalAssignNumber() {
  let selNum = document.getElementById('mn-available-select')?.value;
  if (selNum === 'custom') {
    selNum = document.getElementById('mn-custom-input')?.value.trim();
  }
  if (!selNum) {
    showToast('Please select or enter a phone number', false);
    return;
  }

  const btn = document.getElementById('btn-assign-modal');
  if (btn) {
    btn.disabled = true;
    btn.innerText = '⏳ Assigning...';
  }

  try {
    const res = await tenantFetch('/api/v1/tenant/assign-number', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tenant_id: activeTenantId, phone_number: selNum })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast(`✓ Assigned ${selNum} to client!`);
      const customInput = document.getElementById('mn-custom-input');
      if (customInput) { customInput.value = ''; customInput.style.display = 'none'; }
      await loadAvailableNumbers();
      await loadClientDashboard(activeTenantId);
      openManageNumbersModal();
    } else {
      showToast(data.detail || 'Failed to assign number', false);
    }
  } catch (e) {
    showToast(e.message, false);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = 'Assign Number to Client';
    }
  }
}

function unassignNumberFromTenant(number) {
  openConfirmModal({
    title: 'Unassign Telephony DID Number',
    message: `Are you sure you want to unassign ${number} from this client? The number will be instantly returned to the central pool and available to other tenants.`,
    iconType: 'warning',
    confirmText: 'Unassign Number 🗑️',
    isDestructive: true,
    onConfirm: async () => {
      try {
        const res = await tenantFetch('/api/v1/tenant/unassign-number', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tenant_id: activeTenantId, phone_number: number })
        });
        const data = await res.json();
        if (res.ok && data.status === 'success') {
          showToast(`✓ ${number} unassigned and returned to inventory.`);
          await loadAvailableNumbers();
          await loadClientDashboard(activeTenantId);
          openManageNumbersModal();
        } else {
          showToast(data.detail || 'Failed to unassign number', false);
        }
      } catch (e) {
        showToast(e.message, false);
      }
    }
  });
}

// =============================================================================
// AI AGENTS & MULTI-AGENT DID ROUTING
// =============================================================================

let cachedClientAgents = [];

function getRoleEmoji(roleTitle) {
  const r = (roleTitle || '').toLowerCase();
  if (r.includes('placement') || r.includes('career') || r.includes('job')) return '💼';
  if (r.includes('admission') || r.includes('enroll')) return '🎓';
  if (r.includes('fee') || r.includes('finance') || r.includes('account')) return '💰';
  if (r.includes('support') || r.includes('help') || r.includes('tech')) return '🛠️';
  if (r.includes('counselor')) return '🗣️';
  return '🤖';
}

function populateQuickDIDSwitcher(data) {
  const didSelect = document.getElementById('quick-switch-did-select');
  const counselorSelect = document.getElementById('quick-switch-counselor-select');
  const bar = document.getElementById('quick-did-switcher-bar');
  if (!bar) return;

  const assignedNumbers = data.assigned_numbers || [];
  const agents = data.agents || [];

  if (assignedNumbers.length === 0) {
    if (didSelect) {
      didSelect.innerHTML = '<option value="">No Inbound DID Assigned</option>';
      didSelect.disabled = true;
    }
    const badge = document.getElementById('quick-switch-current-badge');
    if (badge) {
      badge.innerHTML = '<span>⚠️</span> <span style="color:var(--text-muted); font-size:0.85rem;">No DID line assigned</span>';
    }
    if (counselorSelect) counselorSelect.disabled = true;
    const btn = document.getElementById('btn-quick-switch');
    if (btn) btn.disabled = true;
    return;
  }

  // Populate DIDs
  if (didSelect) {
    didSelect.disabled = false;
    const prevVal = didSelect.value;
    didSelect.innerHTML = assignedNumbers.map(n => {
      const isSel = (prevVal && prevVal === n.number) ? 'selected' : '';
      return `<option value="${escapeHtml(n.number)}" ${isSel}>${escapeHtml(n.number)}</option>`;
    }).join('');
    if (!didSelect.value && assignedNumbers.length > 0) {
      didSelect.value = assignedNumbers[0].number;
    }
  }

  // Populate Agents
  if (counselorSelect) {
    counselorSelect.disabled = false;
    counselorSelect.innerHTML = agents.map(ag => {
      const emoji = getRoleEmoji(ag.role_title);
      return `<option value="${escapeHtml(ag.agent_id)}">${emoji} ${escapeHtml(ag.name)} (${escapeHtml(ag.role_title)})${ag.is_default ? ' ⭐' : ''}</option>`;
    }).join('');
  }

  const btn = document.getElementById('btn-quick-switch');
  if (btn) btn.disabled = false;

  // Update current badge based on selected DID
  if (didSelect && didSelect.value) {
    onQuickSwitchDIDChange(didSelect.value);
  }
}

function onQuickSwitchDIDChange(selectedPhone) {
  if (!currentTenantProfile || !currentTenantProfile.assigned_numbers) return;
  const numObj = currentTenantProfile.assigned_numbers.find(n => n.number === selectedPhone);
  const badge = document.getElementById('quick-switch-current-badge');
  const counselorSelect = document.getElementById('quick-switch-counselor-select');

  if (numObj) {
    const aid = numObj.assigned_agent_id;
    let agent = cachedClientAgents.find(a => a.agent_id === aid);
    if (!agent && cachedClientAgents.length > 0) {
      agent = cachedClientAgents.find(a => a.is_default) || cachedClientAgents[0];
    }

    if (agent && badge) {
      const emoji = getRoleEmoji(agent.role_title);
      badge.innerHTML = `<span>${emoji}</span> <strong id="quick-current-name">${escapeHtml(agent.name)}</strong> <span style="font-size: 0.78rem; opacity: 0.85;" id="quick-current-role">(${escapeHtml(agent.role_title)})</span>`;
      
      // Auto-select the OTHER agent in the switch dropdown for 1-click convenience
      if (counselorSelect && cachedClientAgents.length > 1) {
        const otherAgent = cachedClientAgents.find(a => a.agent_id !== agent.agent_id);
        if (otherAgent) counselorSelect.value = otherAgent.agent_id;
      }
    }
  }
}

async function executeQuickDIDSwitch() {
  const didSelect = document.getElementById('quick-switch-did-select');
  const counselorSelect = document.getElementById('quick-switch-counselor-select');
  if (!didSelect || !counselorSelect) return;

  const phone = didSelect.value;
  const agentId = counselorSelect.value;

  if (!phone) {
    showToast('Please select a DID phone line', false);
    return;
  }
  if (!agentId) {
    showToast('Please select an agent to switch to', false);
    return;
  }

  const btn = document.getElementById('btn-quick-switch');
  if (btn) {
    btn.disabled = true;
    btn.innerText = '⚡ Switching...';
  }

  try {
    await switchAgentForDID(phone, agentId);
    if (didSelect) didSelect.value = phone;
    onQuickSwitchDIDChange(phone);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = '⚡ Switch Agent Now';
    }
  }
}

function renderClientAgents(data) {
  cachedClientAgents = data.agents || [];
  const assignedNumbers = data.assigned_numbers || [];

  // Update prominent DID switcher banner at the top of workspace
  populateQuickDIDSwitcher(data);

  // 1. Update count badge
  const badge = document.getElementById('agents-count-badge');
  if (badge) {
    badge.innerText = `${cachedClientAgents.length} Active Agent${cachedClientAgents.length === 1 ? '' : 's'}`;
  }

  // 2. Render Inbound DID Routing Table
  const didContainer = document.getElementById('agents-did-routing-container');
  if (didContainer) {
    if (assignedNumbers.length === 0) {
      didContainer.innerHTML = `
        <div style="text-align: center; padding: 28px 20px; background: rgba(255, 255, 255, 0.02); border: 1px dashed var(--border); border-radius: 10px;">
          <div style="font-size: 2rem; margin-bottom: 8px;">📞</div>
          <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 4px;">No Inbound Phone Lines Configured</div>
          <div style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 14px;">Assign a telephony DID line to this client to enable live agent voice routing.</div>
          <button class="btn btn-outline btn-sm" onclick="openManageNumbersModal()">+ Assign DID Line</button>
        </div>
      `;
    } else {
      let rowsHtml = '';
      assignedNumbers.forEach((n, idx) => {
        const assignedAgentId = n.assigned_agent_id;
        let activeAgent = cachedClientAgents.find(a => a.agent_id === assignedAgentId);
        if (!activeAgent && cachedClientAgents.length > 0) {
          activeAgent = cachedClientAgents.find(a => a.is_default) || cachedClientAgents[0];
        }

        const agentOptions = cachedClientAgents.map(ag => {
          const isSelected = activeAgent && activeAgent.agent_id === ag.agent_id ? 'selected' : '';
          return `<option value="${escapeHtml(ag.agent_id)}" ${isSelected}>${escapeHtml(ag.name)} (${escapeHtml(ag.role_title)})</option>`;
        }).join('');

        const agentBadge = activeAgent ? `
          <div style="display: flex; align-items: center; gap: 8px;">
            <span style="font-size: 1.15rem;">${getRoleEmoji(activeAgent.role_title)}</span>
            <div>
              <strong style="color: var(--text-primary); font-size: 0.88rem;">${escapeHtml(activeAgent.name)}</strong>
              <div style="font-size: 0.74rem; color: var(--accent); font-weight: 600;">${escapeHtml(activeAgent.role_title)}</div>
            </div>
          </div>
        ` : `<span style="color: var(--text-muted); font-size: 0.82rem;">Unassigned</span>`;

        rowsHtml += `
          <tr style="border-bottom: 1px solid rgba(255,255,255,0.06);">
            <td style="padding: 12px 14px;">
              <strong style="font-family: var(--font-mono); font-size: 0.95rem; color: var(--text-primary);">${escapeHtml(n.number)}</strong>
              <span class="badge badge-indigo" style="margin-left: 6px; font-size: 0.68rem;">${escapeHtml((n.provider || 'CARRIER').toUpperCase())}</span>
            </td>
            <td style="padding: 12px 14px;">
              ${agentBadge}
            </td>
            <td style="padding: 12px 14px;">
              <div style="display: flex; gap: 8px; align-items: center;">
                <select id="did-agent-select-${idx}" class="form-control form-control-sm" style="min-width: 200px; font-size: 0.82rem; padding: 5px 8px; background: rgba(0,0,0,0.3); border-color: rgba(255,255,255,0.15);">
                  ${agentOptions}
                </select>
                <button class="btn btn-primary btn-sm" onclick="handleSwitchAgentBtn('${escapeHtml(n.number)}', 'did-agent-select-${idx}')" style="font-size: 0.78rem; padding: 6px 12px; font-weight: 700; white-space: nowrap;">
                  ⚡ Switch
                </button>
              </div>
            </td>
            <td style="padding: 12px 14px; text-align: right;">
              <span class="badge badge-emerald" style="font-size: 0.72rem;">● Live & Ready</span>
            </td>
          </tr>
        `;
      });

      didContainer.innerHTML = `
        <div style="overflow-x: auto;">
          <table style="width: 100%; border-collapse: collapse; text-align: left;">
            <thead>
              <tr style="border-bottom: 1px solid var(--border); font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase;">
                <th style="padding: 8px 14px;">Inbound DID Phone Line</th>
                <th style="padding: 8px 14px;">Currently Answering Agent</th>
                <th style="padding: 8px 14px;">Hot-Switch Agent</th>
                <th style="padding: 8px 14px; text-align: right;">Telephony Status</th>
              </tr>
            </thead>
            <tbody>
              ${rowsHtml}
            </tbody>
          </table>
        </div>
      `;
    }
  }

  // 3. Render Registered Agents Cards Grid
  const cardsGrid = document.getElementById('agents-cards-grid');
  if (cardsGrid) {
    if (cachedClientAgents.length === 0) {
      cardsGrid.innerHTML = `
        <div style="grid-column: 1 / -1; text-align: center; padding: 32px; background: rgba(255, 255, 255, 0.02); border: 1px dashed var(--border); border-radius: 12px;">
          <div style="font-size: 2rem; margin-bottom: 8px;">🤖</div>
          <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 4px;">No AI Agents Registered</div>
          <div style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 14px;">Create your first specialized agent for admissions, placements, or customer service.</div>
          <button class="btn btn-primary btn-sm" onclick="openCreateAgentModal()">+ Create Agent</button>
        </div>
      `;
    } else {
      let cardsHtml = '';
      cachedClientAgents.forEach(ag => {
        const didsList = ag.assigned_dids && ag.assigned_dids.length > 0 
          ? ag.assigned_dids.map(d => `<span class="badge badge-emerald" style="font-family: var(--font-mono); font-size: 0.72rem; margin-right: 4px; margin-bottom: 4px;">📞 ${escapeHtml(d)}</span>`).join('')
          : `<span class="badge" style="background: rgba(255,255,255,0.06); color: var(--text-muted); font-size: 0.72rem;">Standby (No Inbound DID)</span>`;

        cardsHtml += `
          <div class="card" style="background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 18px; display: flex; flex-direction: column; justify-content: space-between;">
            <div>
              <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                  <div style="width: 42px; height: 42px; border-radius: 10px; background: rgba(99, 102, 241, 0.15); border: 1px solid rgba(99, 102, 241, 0.35); display: flex; align-items: center; justify-content: center; font-size: 1.4rem;">
                    ${getRoleEmoji(ag.role_title)}
                  </div>
                  <div>
                    <h4 style="margin: 0; font-size: 1.05rem; font-weight: 700; color: var(--text-primary);">${escapeHtml(ag.name)}</h4>
                    <div style="color: var(--accent); font-size: 0.8rem; font-weight: 600; margin-top: 2px;">${escapeHtml(ag.role_title)}</div>
                  </div>
                </div>
                <div>
                  ${ag.is_default ? '<span class="badge badge-indigo" style="font-size: 0.68rem; padding: 3px 8px;">⭐ Default</span>' : '<span class="badge" style="background: rgba(255,255,255,0.06); color: var(--text-muted); font-size: 0.68rem; padding: 3px 8px;">Specialized</span>'}
                </div>
              </div>

              <div style="margin-bottom: 12px;">
                <div style="font-size: 0.7rem; color: var(--text-muted); text-transform: uppercase; font-weight: 700; margin-bottom: 6px; letter-spacing: 0.03em;">Active Routing:</div>
                <div style="display: flex; flex-wrap: wrap;">
                  ${didsList}
                </div>
              </div>

              <div style="background: rgba(0,0,0,0.25); border-left: 3px solid var(--accent); padding: 8px 12px; border-radius: 4px; font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 12px; font-style: italic; line-height: 1.4;">
                “${escapeHtml(ag.greeting || 'Hello, how can I assist you today?')}”
              </div>

              <div style="font-size: 0.78rem; color: var(--text-muted); line-height: 1.45; margin-bottom: 14px; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;" title="${escapeHtml(ag.persona || '')}">
                ${escapeHtml(ag.persona || '')}
              </div>

              <div style="display: flex; flex-direction: column; gap: 4px; font-size: 0.74rem; color: var(--text-muted); margin-bottom: 16px; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 8px;">
                <div>📚 <strong style="color: var(--indigo);">Shared Knowledge Base</strong> (Auto-synced)</div>
                ${ag.escalation_number ? `<div>📞 Escalation: <strong style="color: var(--text-primary); font-family: var(--font-mono);">${escapeHtml(ag.escalation_number)}</strong></div>` : ''}
              </div>
            </div>

            <div style="display: flex; gap: 8px; justify-content: flex-end; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 12px;">
              <button class="btn btn-outline btn-sm" onclick="openCreateAgentModal('${escapeHtml(ag.agent_id)}')" style="font-size: 0.76rem; padding: 5px 12px;">
                ✏️ Edit Agent
              </button>
              ${!ag.is_default ? `
                <button class="btn btn-outline btn-sm" onclick="deleteCounselorAgent('${escapeHtml(ag.agent_id)}', '${escapeHtml(ag.name)}')" style="color: var(--rose); border-color: rgba(244,63,94,0.3); font-size: 0.76rem; padding: 5px 10px;" title="Delete Agent">
                  🗑️
                </button>
              ` : ''}
            </div>
          </div>
        `;
      });
      cardsGrid.innerHTML = cardsHtml;
    }
  }
}

async function handleSwitchAgentBtn(phone, selectId) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  const agentId = sel.value;
  await switchAgentForDID(phone, agentId);
}

async function switchAgentForDID(phone, agentId) {
  if (!activeTenantId || !phone || !agentId) return;
  try {
    const res = await tenantFetch('/api/v1/tenant/assign-agent-to-did', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tenant_id: activeTenantId,
        phone_number: phone,
        agent_id: agentId
      })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast(`✓ Inbound calls to ${phone} now routed to ${data.agent_name} (${data.role_title})!`);

      // Reset chat session so Conversational Reasoning Tester switches immediately to the new agent
      currentChatSessionId = null;
      const chatBox = document.getElementById('client-chat-box');
      if (chatBox) {
        const emoji = getRoleEmoji(data.role_title);
        chatBox.innerHTML = `
          <div style="align-self: flex-start; background: #F8FAFC; border: 1.5px solid #CBD5E1; padding: 10px 14px; border-radius: 12px 12px 12px 3px; max-width: 90%; font-size: 0.85rem; color: #0F172A; box-shadow: 0 1px 4px rgba(0,0,0,0.05);">
            <div style="display:flex; align-items:center; gap:6px; margin-bottom:4px;">
              <span>${emoji}</span>
              <strong style="color: var(--indigo); font-size: 0.88rem;">${escapeHtml(data.agent_name)}</strong>
              <span style="font-size:0.72rem; color:var(--text-muted); font-weight:600;">(${escapeHtml(data.role_title)})</span>
              <span class="badge badge-emerald" style="font-size:0.65rem; padding:1px 6px;">Live on ${escapeHtml(phone)}</span>
            </div>
            <div style="line-height:1.45; color: #334155; font-style: italic;">“${escapeHtml(data.greeting || 'Hello, how can I assist you today?')}”</div>
          </div>
        `;
      }

      await loadClientDashboard(activeTenantId);
    } else {
      showToast(data.detail || 'Failed to switch agent', false);
    }
  } catch (e) {
    showToast(e.message, false);
  }
}

function openCreateAgentModal(editAgentId = null) {
  const modal = document.getElementById('create-agent-modal');
  if (!modal) {
    console.error('Modal #create-agent-modal not found');
    return;
  }

  // Ensure activeTenantId is resolved
  if (!activeTenantId) {
    const sw = document.getElementById('client-switcher');
    if (sw && sw.value) activeTenantId = sw.value;
    else activeTenantId = 'cl_idigitalstudie_82d5';
  }

  const tenantNameEl = document.getElementById('agent-modal-tenant-name');
  if (tenantNameEl) {
    if (currentTenantProfile) {
      tenantNameEl.innerText = `${currentTenantProfile.business_name} (${currentTenantProfile.tenant_id})`;
    } else {
      tenantNameEl.innerText = activeTenantId;
    }
  }

  // Populate DID select
  const didSelect = document.getElementById('agent-did-select');
  if (didSelect) {
    didSelect.innerHTML = '<option value="">-- Do Not Assign Now --</option>';
    if (currentTenantProfile && currentTenantProfile.assigned_numbers) {
      currentTenantProfile.assigned_numbers.forEach(n => {
        didSelect.innerHTML += `<option value="${escapeHtml(n.number)}">${escapeHtml(n.number)} (${escapeHtml((n.provider || 'carrier').toUpperCase())})</option>`;
      });
    }
  }

  const editIdEl = document.getElementById('agent-edit-id');
  const titleEl = document.getElementById('agent-modal-title');
  const saveBtn = document.getElementById('btn-save-agent');
  const nameInput = document.getElementById('agent-name-input');
  const roleInput = document.getElementById('agent-role-input');
  const greetingInput = document.getElementById('agent-greeting-input');
  const personaInput = document.getElementById('agent-persona-input');
  const escInput = document.getElementById('agent-esc-input');

  if (editAgentId && cachedClientAgents && cachedClientAgents.length > 0) {
    const ag = cachedClientAgents.find(a => a.agent_id === editAgentId);
    if (ag) {
      if (editIdEl) editIdEl.value = ag.agent_id;
      if (titleEl) titleEl.innerText = `✏️ Edit Agent: ${ag.name}`;
      if (saveBtn) saveBtn.innerText = 'Save Agent Updates 💾';
      if (nameInput) nameInput.value = ag.name || '';
      if (roleInput) roleInput.value = ag.role_title || '';
      if (greetingInput) greetingInput.value = ag.greeting || '';
      if (personaInput) personaInput.value = ag.persona || '';
      if (escInput) escInput.value = ag.escalation_number || '';
      if (didSelect && ag.assigned_dids && ag.assigned_dids.length > 0) {
        didSelect.value = ag.assigned_dids[0];
      }
      modal.classList.add('active');
      modal.style.display = 'flex';
      document.body.style.overflow = 'hidden';
      return;
    }
  }

  // Reset for new agent
  if (editIdEl) editIdEl.value = '';
  if (titleEl) titleEl.innerText = '🤖 Create Specialized AI Agent';
  if (saveBtn) saveBtn.innerText = 'Deploy Agent 🚀';
  if (nameInput) nameInput.value = '';
  if (roleInput) roleInput.value = '';
  if (greetingInput) greetingInput.value = '';
  if (personaInput) personaInput.value = '';
  if (escInput) escInput.value = (currentTenantProfile && currentTenantProfile.fallback_number) || '+919560732274';

  modal.classList.add('active');
  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';
}

function closeCreateAgentModal() {
  const modal = document.getElementById('create-agent-modal');
  if (modal) {
    modal.classList.remove('active');
    modal.style.display = 'none';
    document.body.style.overflow = '';
  }
}

function handleCreateAgentBackdrop(e) {
  if (e.target && e.target.id === 'create-agent-modal') {
    closeCreateAgentModal();
  }
}

const AGENT_ROLE_TEMPLATES = {
  placement: {
    name: 'Rohan',
    role: 'Placement & Career Counselor',
    greeting: 'Hello, I am Rohan, Placement and Career Counselor at iDigital Studies. Are you calling regarding our placement assistance, salary packages, or campus hiring partners?',
    persona: 'You are Rohan, the Senior Placement & Career Counselor at iDigital Studies. Your primary responsibility is addressing student queries about career pathways, 100% placement assistance, resume preparation, mock interviews, corporate tie-ups (such as TCS, Infosys, Wipro, Accenture, Amazon, HCL), and average salary packages. Ground all answers strictly on approved iDigital Studies placement information. Maintain a confident, encouraging, and professional demeanor.'
  },
  admissions: {
    name: 'Aanya',
    role: 'Senior Admissions Counselor',
    greeting: 'Hello! I am Aanya, Admissions Counselor at iDigital Studies. Which course or certification program are you interested in exploring today?',
    persona: 'You are Aanya, the Lead Admissions Counselor at iDigital Studies. Your role is to guide prospective students through course modules, curriculum depth, batch timings (weekday/weekend), certification credentials, and admission formalities. Be polite, warm, and structured.'
  },
  support: {
    name: 'Priya',
    role: 'Student Support & Helpdesk',
    greeting: 'Hello, this is Priya from iDigital Studies Student Support desk. How can I assist you with your LMS, classes, or attendance today?',
    persona: 'You are Priya from Student Support at iDigital Studies. You assist enrolled students with class schedules, student portal login, batch transfers, leave requests, LMS access, and mentor feedback. Be empathetic, concise, and problem-solving focused.'
  },
  fees: {
    name: 'Vikram',
    role: 'Fee & Financial Aid Advisor',
    greeting: 'Hello, I am Vikram, Financial Aid and Fee Advisor at iDigital Studies. Are you inquiring about course fees, installment options, or scholarships?',
    persona: 'You are Vikram, the Fee and Accounts Advisor at iDigital Studies. You explain course fee structures, flexible EMI/installment plans, zero-cost EMI options, scholarship criteria, and payment receipt confirmations accurately and transparently.'
  }
};

function applyAgentTemplate(templateKey) {
  const tpl = AGENT_ROLE_TEMPLATES[templateKey];
  if (!tpl) return;

  const nameInput = document.getElementById('agent-name-input');
  const roleInput = document.getElementById('agent-role-input');
  const greetingInput = document.getElementById('agent-greeting-input');
  const personaInput = document.getElementById('agent-persona-input');

  if (nameInput) nameInput.value = tpl.name;
  if (roleInput) roleInput.value = tpl.role;
  if (greetingInput) greetingInput.value = tpl.greeting;
  if (personaInput) personaInput.value = tpl.persona;
  showToast(`✓ Loaded '${tpl.role}' template!`);
}

async function submitCreateOrUpdateAgent() {
  const editIdEl = document.getElementById('agent-edit-id');
  const isEdit = editIdEl && editIdEl.value.trim().length > 0;
  const agentId = isEdit ? editIdEl.value.trim() : null;

  const name = (document.getElementById('agent-name-input')?.value || '').trim();
  const role = (document.getElementById('agent-role-input')?.value || '').trim();
  const greeting = (document.getElementById('agent-greeting-input')?.value || '').trim();
  const persona = (document.getElementById('agent-persona-input')?.value || '').trim();
  const esc = (document.getElementById('agent-esc-input')?.value || '').trim();
  const assignDid = (document.getElementById('agent-did-select')?.value || '').trim();

  if (!name) {
    showToast('Please enter an Agent Name', false);
    return;
  }
  if (!role) {
    showToast('Please enter a Role / Department Title', false);
    return;
  }
  if (!greeting) {
    showToast('Please enter the First Spoken Greeting', false);
    return;
  }
  if (!persona) {
    showToast('Please provide System Persona & Guidance', false);
    return;
  }

  const btn = document.getElementById('btn-save-agent');
  if (btn) {
    btn.disabled = true;
    btn.innerText = isEdit ? 'Updating Agent...' : 'Deploying Agent...';
  }

  try {
    let url, method, payload;
    if (isEdit) {
      url = `/api/v1/tenant/${activeTenantId}/agents/${agentId}`;
      method = 'PUT';
      payload = {
        name,
        role_title: role,
        greeting,
        persona,
        escalation_number: esc || null
      };
    } else {
      url = `/api/v1/tenant/${activeTenantId}/agents`;
      method = 'POST';
      payload = {
        name,
        role_title: role,
        greeting,
        persona,
        escalation_number: esc || null,
        assign_to_did: assignDid || null
      };
    }

    const res = await tenantFetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();

    if (res.ok && data.status === 'success') {
      showToast(`✓ Agent '${name}' (${role}) ${isEdit ? 'updated' : 'deployed'}!`);
      closeCreateAgentModal();
      await loadClientDashboard(activeTenantId);
    } else {
      showToast(data.detail || 'Failed to save agent', false);
    }
  } catch (e) {
    showToast(e.message, false);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = isEdit ? 'Save Agent Updates 💾' : 'Deploy Agent 🚀';
    }
  }
}

function deleteCounselorAgent(agentId, agentName) {
  openConfirmModal({
    title: 'Delete AI Agent',
    message: `Are you sure you want to delete agent '${agentName}'? This action cannot be undone. Any calls routed to this agent will fall back to your default agent.`,
    iconType: 'warning',
    confirmText: 'Delete Agent 🗑️',
    isDestructive: true,
    onConfirm: async () => {
      try {
        const res = await tenantFetch(`/api/v1/tenant/${activeTenantId}/agents/${agentId}`, {
          method: 'DELETE'
        });
        const data = await res.json();
        if (res.ok && data.status === 'success') {
          showToast(`✓ Agent '${agentName}' deleted.`);
          await loadClientDashboard(activeTenantId);
        } else {
          showToast(data.detail || 'Failed to delete agent', false);
        }
      } catch (e) {
        showToast(e.message, false);
      }
    }
  });
}

// Global window bindings for inline onclick handlers
window.renderClientAgents = renderClientAgents;
window.handleSwitchAgentBtn = handleSwitchAgentBtn;
window.switchAgentForDID = switchAgentForDID;
window.openCreateAgentModal = openCreateAgentModal;
window.closeCreateAgentModal = closeCreateAgentModal;
window.handleCreateAgentBackdrop = handleCreateAgentBackdrop;
window.applyAgentTemplate = applyAgentTemplate;
window.submitCreateOrUpdateAgent = submitCreateOrUpdateAgent;
window.deleteCounselorAgent = deleteCounselorAgent;
window.onQuickSwitchDIDChange = onQuickSwitchDIDChange;
window.executeQuickDIDSwitch = executeQuickDIDSwitch;

// Escape key to dismiss modal
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    const modal = document.getElementById('create-agent-modal');
    if (modal && (modal.classList.contains('active') || modal.style.display === 'flex')) {
      closeCreateAgentModal();
    }
  }
});


