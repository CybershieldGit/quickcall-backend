async function getAdminToken() {
  let token = localStorage.getItem('qwickcall_admin_token');
  if (!token || token.startsWith('admin_token_')) {
    try {
      const res = await fetch('/api/v1/admin/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: 'admin@qwickcall.ai', password: 'admin123' })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.token) {
          token = data.token;
          localStorage.setItem('qwickcall_admin_token', token);
        }
      }
    } catch (e) {
      console.warn('Admin token error:', e);
    }
  }
  return token || '';
}

async function adminFetch(url, options = {}) {
  const token = await getAdminToken();
  const headers = Object.assign({}, options.headers || {}, {
    'Authorization': `Bearer ${token}`
  });
  return fetch(url, Object.assign({}, options, { headers }));
}

/**
 * Admin Controller — SuperAdmin & Platform Owner View (#panel-admin)
 * Telemetry, Global Financial Margins, Reseller Oversight, Tenant Master Control, DID Master Pool
 */

let adminCachedResellers = [];

async function loadAdminData() {
  await Promise.all([
    loadAdminOverview(),
    loadAdminResellers(),
    loadAdminTenants(),
    loadAdminPool()
  ]);
}

async function loadAdminOverview() {
  try {
    const res = await adminFetch('/api/v1/admin/overview');
    if (!res.ok) return;
    const data = await res.json();
    const metrics = data.metrics || {};

    const elTenants = document.getElementById('adm-total-tenants');
    if (elTenants) elTenants.innerText = metrics.active_tenants_count ?? metrics.total_tenants ?? 0;

    const elProfit = document.getElementById('adm-net-profit');
    if (elProfit) elProfit.innerText = `₹${Number(metrics.owner_net_profit_inr || metrics.platform_net_profit || 0).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;

    const elAirtime = document.getElementById('adm-airtime');
    if (elAirtime) elAirtime.innerText = `${Math.round(metrics.total_airtime_minutes || 0)}m`;

    const elPool = document.getElementById('adm-pool-count');
    if (elPool) elPool.innerText = `${metrics.available_dids ?? 0} Avail / ${metrics.total_dids ?? 0} Total`;

    const elBaseCost = document.getElementById('adm-base-cost');
    if (elBaseCost) elBaseCost.innerText = `₹${Number(metrics.base_cost_per_min || 2.00).toFixed(2)}/min`;
  } catch (e) {
    console.warn('Admin overview fetch warning:', e);
  }
}

async function loadAdminResellers() {
  const tbody = document.getElementById('adm-resellers-tbody');
  if (!tbody) return;
  try {
    const res = await adminFetch('/api/v1/admin/resellers');
    if (!res.ok) return;
    const data = await res.json();
    const resellers = data.resellers || [];
    adminCachedResellers = resellers;

    populateAdminResellerDropdown(resellers);

    if (resellers.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:24px;">No partner agencies registered yet. Click "+ Onboard Partner Agency" to provision one!</td></tr>';
      return;
    }

    tbody.innerHTML = '';
    resellers.forEach(r => {
      const wholesale = Number(r.wholesale_rate_per_min || 3).toFixed(2);
      const wallet = Number(r.wallet_balance || 0).toFixed(2);
      tbody.innerHTML += `
        <tr>
          <td>
            <strong>${escapeHtml(r.company_name)}</strong><br/>
            <span style="font-size:0.75rem; color:var(--text-muted);">${escapeHtml(r.name)} • ${escapeHtml(r.email)}</span>
          </td>
          <td><code style="color:var(--cyan); font-family:var(--font-mono);">${escapeHtml(r.reseller_id)}</code></td>
          <td><strong style="color:#A5B4FC;">₹${wholesale}/min</strong></td>
          <td style="color:var(--emerald); font-weight:700;">₹${wallet}</td>
          <td><span class="badge badge-indigo">${r.sub_clients_count || 0} Clients</span></td>
          <td><span class="badge ${r.status === 'active' ? 'badge-emerald' : 'badge-amber'}">${escapeHtml(r.status)}</span></td>
          <td style="display:flex; gap:6px; flex-wrap:wrap;">
            <button class="btn btn-outline btn-sm" onclick="promptTopupReseller('${escapeHtml(r.reseller_id)}')" style="font-size:0.72rem; padding:4px 8px;">+ Credit</button>
            <button class="btn btn-outline btn-sm" onclick="promptEditResellerRate('${escapeHtml(r.reseller_id)}', ${r.wholesale_rate_per_min || 3})" style="font-size:0.72rem; padding:4px 8px;">✏️ Rate</button>
            <button class="btn btn-primary btn-sm" onclick="switchToResellerView('${escapeHtml(r.reseller_id)}')" style="font-size:0.72rem; padding:4px 8px;">View Portal →</button>
          </td>
        </tr>
      `;
    });
  } catch (e) {
    console.warn('Admin resellers fetch error:', e);
  }
}

async function loadAdminTenants() {
  const tbody = document.getElementById('adm-tenants-tbody');
  if (!tbody) return;
  try {
    const res = await adminFetch('/api/v1/admin/tenants');
    if (!res.ok) return;
    const data = await res.json();
    const tenants = data.tenants || [];

    if (tenants.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; color:var(--text-muted); padding:24px;">No enterprise tenants provisioned yet. Click "+ Onboard New Tenant" to launch one!</td></tr>';
      return;
    }

    tbody.innerHTML = '';
    tenants.forEach(t => {
      const retail = Number(t.rate_per_min || 5).toFixed(2);
      const wallet = Number(t.wallet_balance || 0).toFixed(2);
      const isDirect = !t.reseller_id;
      tbody.innerHTML += `
        <tr>
          <td>
            <strong>${escapeHtml(t.business_name)}</strong><br/>
            <span style="font-size:0.75rem; color:var(--text-muted);">${escapeHtml(t.email || '')}</span>
          </td>
          <td><code style="color:var(--cyan); font-family:var(--font-mono);">${escapeHtml(t.tenant_id)}</code></td>
          <td>
            ${isDirect 
              ? '<span class="badge badge-cyan">Platform Direct</span>' 
              : `<span class="badge badge-indigo" title="UID: ${escapeHtml(t.reseller_id)}">${escapeHtml(t.reseller_name || t.reseller_id)}</span>`}
          </td>
          <td><code style="color:#A5B4FC; font-family:var(--font-mono);">${escapeHtml(t.assigned_numbers || 'Auto-Allocated')}</code></td>
          <td>₹${retail}/min</td>
          <td style="color:${t.wallet_balance > 100 ? 'var(--emerald)' : 'var(--amber)'}; font-weight:700;">₹${wallet}</td>
          <td>${t.total_calls || 0} (${t.total_airtime_minutes || 0}m)</td>
          <td><span class="badge ${t.status === 'active' ? 'badge-emerald' : 'badge-amber'}">${escapeHtml(t.status)}</span></td>
          <td style="display:flex; gap:6px; flex-wrap:wrap;">
            <button class="btn btn-outline btn-sm" onclick="promptTopupTenantAdmin('${escapeHtml(t.tenant_id)}')" style="font-size:0.72rem; padding:4px 8px;">+ Credit</button>
            <button class="btn btn-outline btn-sm" onclick="promptEditTenantRateAdmin('${escapeHtml(t.tenant_id)}', ${t.rate_per_min || 5})" style="font-size:0.72rem; padding:4px 8px;">✏️ Rate</button>
            <button class="btn btn-primary btn-sm" onclick="openClientDashboard('${escapeHtml(t.tenant_id)}')" style="font-size:0.72rem; padding:4px 8px;">Workspace →</button>
          </td>
        </tr>
      `;
    });
  } catch (e) {
    console.warn('Admin tenants fetch error:', e);
  }
}

function promptTopupReseller(resellerId) {
  openInputModal({
    title: 'Top Up Reseller Partner Credit',
    subtitle: `Allocate wholesale prepaid talktime to agency partner: ${resellerId}`,
    label: 'Topup Amount (INR)',
    value: '1000',
    prefix: '₹',
    suffix: 'INR',
    step: '100',
    min: '50',
    presets: [
      { label: '+₹500', value: 500 },
      { label: '+₹1,000', value: 1000 },
      { label: '+₹2,500', value: 2500 },
      { label: '+₹5,000', value: 5000 },
      { label: '+₹10,000', value: 10000 }
    ],
    hint: 'Funds the agency prepaid wallet pool from which all sub-client live telephony traffic is billed.',
    confirmText: 'Top Up Reseller 💳',
    onConfirm: async (amtStr) => {
      const amt = parseFloat(amtStr);
      if (isNaN(amt) || amt <= 0) {
        showToast('Invalid amount entered', false);
        return;
      }
      try {
        const res = await adminFetch(`/api/v1/admin/resellers/${encodeURIComponent(resellerId)}/topup`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ amount: amt, notes: "Admin Platform Topup" })
        });
        if (res.ok) {
          showToast(`✓ Added ₹${amt} to ${resellerId}!`);
          loadAdminData();
        } else {
          const err = await res.json();
          showToast(err.detail || 'Topup failed', false);
        }
      } catch (e) {
        showToast(e.message, false);
      }
    }
  });
}

function promptEditResellerRate(resellerId, currentRate) {
  openInputModal({
    title: 'Update Wholesale Base Rate',
    subtitle: `Configure platform base cost charged to partner: ${resellerId}`,
    label: 'Wholesale Base Rate (INR per minute)',
    value: Number(currentRate || 3.0).toFixed(2),
    prefix: '₹',
    suffix: '/min',
    step: '0.25',
    min: '0.50',
    presets: [
      { label: '₹2.50/m', value: 2.50 },
      { label: '₹3.00/m', value: 3.00 },
      { label: '₹3.50/m', value: 3.50 },
      { label: '₹4.00/m', value: 4.00 }
    ],
    hint: 'The fixed wholesale price charged by the platform for each connected telephony minute.',
    confirmText: 'Save Wholesale Rate 🚀',
    onConfirm: async (rateStr) => {
      const rate = parseFloat(rateStr);
      if (isNaN(rate) || rate <= 0) {
        showToast('Invalid rate entered', false);
        return;
      }
      try {
        const res = await adminFetch(`/api/v1/admin/resellers/${encodeURIComponent(resellerId)}/rate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ wholesale_rate_per_min: rate })
        });
        if (res.ok) {
          showToast(`✓ Wholesale rate updated to ₹${rate.toFixed(2)}/min!`);
          loadAdminData();
        } else {
          const err = await res.json();
          showToast(err.detail || 'Failed to update rate', false);
        }
      } catch (e) {
        showToast(e.message, false);
      }
    }
  });
}

function promptTopupTenantAdmin(tenantId) {
  openInputModal({
    title: 'Top Up Tenant Wallet Credit',
    subtitle: `Manual platform credit adjustment for tenant: ${tenantId}`,
    label: 'Prepaid Wallet Amount (INR)',
    value: '500',
    prefix: '₹',
    suffix: 'INR',
    step: '50',
    min: '10',
    presets: [
      { label: '+₹250', value: 250 },
      { label: '+₹500', value: 500 },
      { label: '+₹1,000', value: 1000 },
      { label: '+₹2,500', value: 2500 }
    ],
    hint: 'Injects airtime credits directly into the tenant organization wallet.',
    confirmText: 'Add Tenant Credit 💳',
    onConfirm: async (amtStr) => {
      const amt = parseFloat(amtStr);
      if (isNaN(amt) || amt <= 0) {
        showToast('Invalid amount entered', false);
        return;
      }
      try {
        const res = await adminFetch(`/api/v1/admin/tenants/${encodeURIComponent(tenantId)}/wallet`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ amount: amt, notes: "Admin Credit Adjustment" })
        });
        if (res.ok) {
          showToast(`✓ Added ₹${amt} to tenant ${tenantId}!`);
          loadAdminTenants();
        } else {
          const err = await res.json();
          showToast(err.detail || 'Topup failed', false);
        }
      } catch (e) {
        showToast(e.message, false);
      }
    }
  });
}

function promptEditTenantRateAdmin(tenantId, currentRate) {
  openInputModal({
    title: 'Update Direct Tenant Retail Rate',
    subtitle: `Configure per-minute telephony rate charged to tenant: ${tenantId}`,
    label: 'Retail Rate (INR per minute)',
    value: Number(currentRate || 5.0).toFixed(2),
    prefix: '₹',
    suffix: '/min',
    step: '0.25',
    min: '0.50',
    presets: [
      { label: '₹3.50/m', value: 3.50 },
      { label: '₹4.50/m', value: 4.50 },
      { label: '₹5.00/m', value: 5.00 },
      { label: '₹6.00/m', value: 6.00 }
    ],
    hint: 'Rate deducted per connected minute from this direct tenant account balance.',
    confirmText: 'Save Retail Rate 🚀',
    onConfirm: async (rateStr) => {
      const rate = parseFloat(rateStr);
      if (isNaN(rate) || rate <= 0) {
        showToast('Invalid rate entered', false);
        return;
      }
      try {
        const res = await adminFetch(`/api/v1/admin/tenants/${encodeURIComponent(tenantId)}/rate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rate_per_min: rate })
        });
        if (res.ok) {
          showToast(`✓ Retail rate updated to ₹${rate.toFixed(2)}/min!`);
          loadAdminTenants();
        } else {
          const err = await res.json();
          showToast(err.detail || 'Failed to update rate', false);
        }
      } catch (e) {
        showToast(e.message, false);
      }
    }
  });
}

function switchToResellerView(resellerId) {
  if (typeof currentResellerId !== 'undefined') {
    currentResellerId = resellerId;
  }
  if (typeof switchPanel === 'function') {
    switchPanel('reseller');
  }
}

async function loadAdminPool() {
  const tbody = document.getElementById('adm-pool-tbody');
  if (!tbody) return;
  try {
    const res = await adminFetch('/api/v1/admin/pool');
    if (!res.ok) return;
    const data = await res.json();
    const numbers = data.pool || [];

    if (numbers.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:20px;">No numbers in master inventory pool.</td></tr>';
      return;
    }

    tbody.innerHTML = '';
    numbers.forEach(n => {
      const isAvail = n.status === 'available';
      tbody.innerHTML += `
        <tr>
          <td><strong style="font-family:var(--font-mono); color:#fff;">${escapeHtml(n.number)}</strong></td>
          <td><span class="badge badge-cyan">${escapeHtml((n.vendor || 'carrier').toUpperCase())}</span></td>
          <td><span class="badge ${isAvail ? 'badge-emerald' : 'badge-indigo'}">${escapeHtml(n.status)}</span></td>
          <td>${n.assigned_tenant_id ? `<code style="color:#A5B4FC;">${escapeHtml(n.assigned_tenant_id)}</code> (${escapeHtml(n.business_name || '')})` : '<span style="color:var(--text-muted);">Unassigned</span>'}</td>
          <td>
            ${isAvail ? '<span style="color:var(--emerald); font-size:0.75rem;">Ready to Allocate</span>' : '<span style="color:var(--cyan); font-size:0.75rem;">Live Carrier Traffic</span>'}
          </td>
        </tr>
      `;
    });
  } catch (e) {
    console.warn('Admin pool fetch error:', e);
  }
}

async function submitAddNewNumberToPool(event) {
  event.preventDefault();
  const numInput = document.getElementById('adm-new-number');
  const vendorInput = document.getElementById('adm-new-vendor');
  const num = numInput ? numInput.value.trim() : '';
  const vendor = vendorInput ? vendorInput.value : 'rpdigital';

  if (!num) {
    showToast('Enter phone number or extension', false);
    return;
  }

  try {
    const res = await adminFetch('/api/v1/admin/pool', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ number: num, vendor: vendor })
    });
    if (res.ok) {
      showToast(`✓ Added ${num} to master inventory pool!`);
      if (numInput) numInput.value = '';
      loadAdminPool();
      loadAdminOverview();
      if (typeof loadAvailableNumbers === 'function') loadAvailableNumbers();
    } else {
      const err = await res.json();
      showToast(err.detail || 'Failed to add number', false);
    }
  } catch (e) {
    showToast(e.message, false);
  }
}

// -----------------------------------------------------------------------------
// Admin Onboard Reseller Modal
// -----------------------------------------------------------------------------
function openOnboardResellerModal() {
  const modal = document.getElementById('onboard-reseller-modal');
  if (modal) {
    modal.classList.add('active');
    document.body.style.overflow = 'hidden';
  }
}

function closeOnboardResellerModal() {
  const modal = document.getElementById('onboard-reseller-modal');
  if (modal) {
    modal.classList.remove('active');
    document.body.style.overflow = '';
  }
}

async function submitOnboardReseller(e) {
  e.preventDefault();
  const company = document.getElementById('adm-res-company')?.value.trim();
  const name = document.getElementById('adm-res-name')?.value.trim();
  const email = document.getElementById('adm-res-email')?.value.trim();
  const password = document.getElementById('adm-res-password')?.value.trim() || 'partner123';
  const wholesaleRate = parseFloat(document.getElementById('adm-res-wholesale')?.value || '3.00');
  const initialCredit = parseFloat(document.getElementById('adm-res-credit')?.value || '1000.00');

  if (!company || !name || !email) {
    showToast('Please fill out all required fields', false);
    return;
  }

  try {
    const res = await adminFetch('/api/v1/admin/resellers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        company_name: company,
        name: name,
        email: email,
        password: password,
        wholesale_rate_per_min: wholesaleRate,
        initial_wallet_balance: initialCredit
      })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast(`✓ Reseller Partner '${company}' onboarded successfully!`);
      closeOnboardResellerModal();
      document.getElementById('onboard-reseller-form')?.reset();
      loadAdminData();
    } else {
      showToast(data.detail || 'Failed to onboard reseller', false);
    }
  } catch (err) {
    showToast(err.message, false);
  }
}

// -----------------------------------------------------------------------------
// Admin Onboard Tenant Modal
// -----------------------------------------------------------------------------
function openOnboardTenantModal() {
  const modal = document.getElementById('onboard-tenant-modal');
  if (modal) {
    populateAdminResellerDropdown(adminCachedResellers);
    modal.classList.add('active');
    document.body.style.overflow = 'hidden';
  }
}

function closeOnboardTenantModal() {
  const modal = document.getElementById('onboard-tenant-modal');
  if (modal) {
    modal.classList.remove('active');
    document.body.style.overflow = '';
  }
}

function populateAdminResellerDropdown(resellers) {
  const sel = document.getElementById('adm-tnt-reseller');
  if (!sel) return;
  const currVal = sel.value;
  sel.innerHTML = '<option value="direct">Direct Platform Client (No Reseller)</option>';
  (resellers || []).forEach(r => {
    sel.innerHTML += `<option value="${escapeHtml(r.reseller_id)}">${escapeHtml(r.company_name)} (${escapeHtml(r.reseller_id)})</option>`;
  });
  if (currVal) sel.value = currVal;
}

async function submitOnboardTenantAdmin(e) {
  e.preventDefault();
  const bizName = document.getElementById('adm-tnt-name')?.value.trim();
  const email = document.getElementById('adm-tnt-email')?.value.trim();
  const resellerId = document.getElementById('adm-tnt-reseller')?.value;
  const plan = document.getElementById('adm-tnt-plan')?.value || 'growth';
  const retailRate = parseFloat(document.getElementById('adm-tnt-rate')?.value || '5.00');
  const initialCredit = parseFloat(document.getElementById('adm-tnt-credit')?.value || '500.00');
  const advisor = document.getElementById('adm-tnt-advisor')?.value.trim() || 'Customer Advisor';
  const esc = document.getElementById('adm-tnt-esc')?.value.trim() || '+917022721098';

  if (!bizName || !email) {
    showToast('Company name and work email are required', false);
    return;
  }

  try {
    const res = await adminFetch('/api/v1/admin/tenants', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        business_name: bizName,
        email: email,
        reseller_id: resellerId === 'direct' ? null : resellerId,
        plan: plan,
        rate_per_min: retailRate,
        initial_wallet_balance: initialCredit,
        advisor_name: advisor,
        escalation_number: esc
      })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast(`✓ Tenant '${bizName}' successfully onboarded!`);
      closeOnboardTenantModal();
      document.getElementById('onboard-tenant-form')?.reset();
      loadAdminData();
      if (typeof loadTenantsList === 'function') loadTenantsList();
    } else {
      showToast(data.detail || 'Failed to onboard tenant', false);
    }
  } catch (err) {
    showToast(err.message, false);
  }
}
