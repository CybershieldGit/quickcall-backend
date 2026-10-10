async function getResellerToken() {
  let token = localStorage.getItem('qwickcall_reseller_token');
  if (!token || token.startsWith('reseller_token_')) {
    try {
      const res = await fetch('/api/v1/reseller/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: 'partner@apexsolutions.in', password: 'partner123' })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.token) {
          token = data.token;
          localStorage.setItem('qwickcall_reseller_token', token);
        }
      }
    } catch (e) {
      console.warn('Reseller token error:', e);
    }
  }
  return token || '';
}

async function resellerFetch(url, options = {}) {
  const token = await getResellerToken();
  const headers = Object.assign({}, options.headers || {}, {
    'Authorization': `Bearer ${token}`
  });
  return fetch(url, Object.assign({}, options, { headers }));
}

/**
 * Reseller Portal Controller — Partner Agency View (#panel-reseller)
 * Reseller Margins, Sub-Clients CRUD, Wholesale-to-Retail Profit Engine
 */

let currentResellerId = 'partner_apex';
let activeWholesaleRate = 3.00;

async function loadResellerData() {
  const resellerId = currentResellerId;
  try {
    // 1. Fetch Reseller Financial Overview & Margin Economics
    const oRes = await resellerFetch(`/api/v1/reseller/overview?reseller_id=${encodeURIComponent(resellerId)}`);
    if (oRes.ok) {
      const oData = await oRes.json();
      const metrics = oData.metrics || {};
      activeWholesaleRate = Number(metrics.wholesale_rate_per_min || 3.00);

      const elName = document.getElementById('reseller-name');
      if (elName) elName.innerText = oData.company_name || 'Apex AI Voice';

      const elSub = document.getElementById('reseller-id-sub');
      if (elSub) elSub.innerText = `Partner UID: ${resellerId}`;

      const elWallet = document.getElementById('reseller-wallet');
      if (elWallet) elWallet.innerText = `₹${Number(metrics.reseller_wallet_balance || 0).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;

      const elWholesale = document.getElementById('reseller-wholesale-rate');
      if (elWholesale) elWholesale.innerText = `₹${activeWholesaleRate.toFixed(2)}`;

      const elRetail = document.getElementById('reseller-avg-retail');
      if (elRetail) elRetail.innerText = `₹${Number(metrics.avg_retail_rate_per_min || 5.00).toFixed(2)}`;

      const elMargin = document.getElementById('reseller-avg-margin');
      if (elMargin) elMargin.innerText = `+₹${Number(metrics.avg_margin_per_min || 2.00).toFixed(2)}/min`;

      const elNetProfit = document.getElementById('reseller-net-profit');
      if (elNetProfit) elNetProfit.innerText = `₹${Number(metrics.net_profit_earned_inr || 0).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;

      const elCount = document.getElementById('reseller-client-count');
      if (elCount) elCount.innerText = metrics.total_sub_clients ?? 0;

      const elAirtime = document.getElementById('reseller-total-airtime');
      if (elAirtime) elAirtime.innerText = `${Math.round(metrics.total_client_minutes || 0)}m`;
    }

    // 2. Fetch Sub-Clients Portfolio Table
    const cRes = await resellerFetch(`/api/v1/reseller/clients?reseller_id=${encodeURIComponent(resellerId)}`);
    const tbody = document.getElementById('reseller-clients-tbody');
    if (cRes.ok && tbody) {
      const cData = await cRes.json();
      const clients = cData.clients || [];

      if (clients.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 28px;">No sub-clients onboarded yet. Click "+ Sell AI Agent to Tenant" to launch your first customer!</td></tr>';
        return;
      }

      tbody.innerHTML = '';
      clients.forEach(cl => {
        const retail = Number(cl.retail_rate_per_min || 5.00);
        const wholesale = Number(cl.wholesale_rate_per_min || activeWholesaleRate);
        const margin = Number(cl.margin_per_min || Math.max(0, retail - wholesale));
        const airtimeMins = Number(cl.total_airtime_minutes || 0);
        const clientProfit = (airtimeMins * margin).toFixed(2);
        const wallet = Number(cl.wallet_balance || 0).toFixed(2);

        tbody.innerHTML += `
          <tr>
            <td>
              <strong>${escapeHtml(cl.business_name)}</strong><br/>
              <span style="font-size:0.75rem; color:var(--text-muted);">${escapeHtml(cl.email || '')}</span>
            </td>
            <td><code style="color: var(--cyan); font-family: var(--font-mono);">${escapeHtml(cl.tenant_id)}</code></td>
            <td><code style="font-family: var(--font-mono); color: #A5B4FC;">${escapeHtml(cl.assigned_numbers || 'Auto-Allocated')}</code></td>
            <td><span class="badge badge-indigo">${escapeHtml(cl.plan)}</span></td>
            <td><strong>₹${retail.toFixed(2)}/min</strong></td>
            <td><strong style="color: var(--emerald);">+₹${margin.toFixed(2)}/min</strong></td>
            <td>${Math.round(airtimeMins)}m</td>
            <td style="color: var(--emerald); font-weight: 800;">₹${Number(clientProfit).toLocaleString('en-IN', {minimumFractionDigits: 2})}</td>
            <td style="display:flex; gap:6px; flex-wrap:wrap;">
              <button class="btn btn-outline btn-sm" onclick="promptEditClientRate('${escapeHtml(cl.tenant_id)}', ${retail})" style="font-size:0.74rem; padding:4px 8px;" title="Adjust retail price for this tenant">✏️ Price</button>
              <button class="btn btn-outline btn-sm" onclick="promptTopupClient('${escapeHtml(cl.tenant_id)}')" style="font-size:0.74rem; padding:4px 8px;" title="Credit client wallet">+ Credit</button>
              <button class="btn btn-primary btn-sm" onclick="openClientDashboard('${escapeHtml(cl.tenant_id)}')" style="font-size:0.74rem; padding:4px 10px;">Workspace →</button>
            </td>
          </tr>
        `;
      });
    }
  } catch (e) {
    console.error('Error loading reseller data:', e);
  }
}

// -----------------------------------------------------------------------------
// Sell AI Agent to Tenant Modal
// -----------------------------------------------------------------------------
function openSellAgentModal() {
  const modal = document.getElementById('sell-agent-modal');
  if (modal) {
    const wholesaleEl = document.getElementById('sell-wholesale-val');
    if (wholesaleEl) wholesaleEl.innerText = `₹${activeWholesaleRate.toFixed(2)}/min`;

    const retailInput = document.getElementById('sell-retail-rate');
    if (retailInput && !retailInput.value) retailInput.value = (activeWholesaleRate + 2.00).toFixed(2);

    updateSellAgentProfitPreview();
    modal.classList.add('active');
    document.body.style.overflow = 'hidden';
  }
}

function closeSellAgentModal() {
  const modal = document.getElementById('sell-agent-modal');
  if (modal) {
    modal.classList.remove('active');
    document.body.style.overflow = '';
  }
}

function updateSellAgentProfitPreview() {
  const retailVal = parseFloat(document.getElementById('sell-retail-rate')?.value || '5.00');
  const previewEl = document.getElementById('sell-profit-preview');
  if (!previewEl) return;

  const margin = Math.max(0, retailVal - activeWholesaleRate);
  const marginPct = retailVal > 0 ? ((margin / retailVal) * 100).toFixed(1) : 0;

  previewEl.innerHTML = `
    <span>Fixed Wholesale Cost: <strong>₹${activeWholesaleRate.toFixed(2)}/min</strong></span>
    <span>Selling Retail Price: <strong>₹${retailVal.toFixed(2)}/min</strong></span>
    <span style="color:var(--emerald); font-weight:800; font-size:1.05rem;">
      ✨ Your Pure Profit: +₹${margin.toFixed(2)}/min (${marginPct}% margin)
    </span>
  `;
}

async function submitSellAgent(e) {
  e.preventDefault();
  const bizName = document.getElementById('sell-biz-name')?.value.trim();
  const email = document.getElementById('sell-email')?.value.trim();
  const plan = document.getElementById('sell-plan')?.value || 'growth';
  const retailRate = parseFloat(document.getElementById('sell-retail-rate')?.value || '5.00');
  const initialWallet = parseFloat(document.getElementById('sell-initial-wallet')?.value || '500.00');
  const advisor = document.getElementById('sell-advisor-name')?.value.trim() || 'Customer Advisor';
  const escNumber = document.getElementById('sell-esc-number')?.value.trim() || '+917022721098';

  if (!bizName || !email) {
    showToast('Company name and email are required', false);
    return;
  }

  try {
    const res = await resellerFetch('/api/v1/reseller/clients', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        reseller_id: currentResellerId,
        business_name: bizName,
        email: email,
        plan: plan,
        retail_rate_per_min: retailRate,
        initial_wallet_balance: initialWallet,
        advisor_name: advisor,
        escalation_number: escNumber
      })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast(`✓ AI Agent provisioned for '${bizName}' at ₹${retailRate.toFixed(2)}/min!`);
      closeSellAgentModal();
      document.getElementById('sell-agent-form')?.reset();
      loadResellerData();
      if (typeof loadTenantsList === 'function') loadTenantsList();
    } else {
      showToast(data.detail || 'Failed to sell agent', false);
    }
  } catch (err) {
    showToast(err.message, false);
  }
}

// -----------------------------------------------------------------------------
// Reseller Client Rate & Credit Controls (Enterprise Pop-Up Modals)
// -----------------------------------------------------------------------------
function promptEditClientRate(tenantId, currentRate) {
  openInputModal({
    title: 'Update Client Retail Rate',
    subtitle: `Configure per-minute retail billing rate for ${tenantId}`,
    label: 'Retail Selling Rate (INR per minute)',
    value: Number(currentRate || 5.0).toFixed(2),
    prefix: '₹',
    suffix: '/min',
    step: '0.25',
    min: '0.50',
    presets: [
      { label: '₹3.50/m', value: 3.50 },
      { label: '₹4.50/m', value: 4.50 },
      { label: '₹5.00/m', value: 5.00 },
      { label: '₹6.00/m', value: 6.00 },
      { label: '₹8.00/m', value: 8.00 }
    ],
    hint: `Your agency wholesale cost is <strong style="color:var(--cyan);">₹${activeWholesaleRate.toFixed(2)}/min</strong>. Everything above this is your 100% agency net margin.`,
    confirmText: 'Save Retail Rate 🚀',
    onConfirm: async (rateStr) => {
      const rate = parseFloat(rateStr);
      if (isNaN(rate) || rate <= 0) {
        showToast('Invalid rate entered', false);
        return;
      }

      const applyRate = async () => {
        try {
          const res = await resellerFetch(`/api/v1/reseller/clients/${encodeURIComponent(tenantId)}/rate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ retail_rate_per_min: rate })
          });
          if (res.ok) {
            showToast(`✓ Retail price updated to ₹${rate.toFixed(2)}/min!`);
            loadResellerData();
          } else {
            const err = await res.json();
            showToast(err.detail || 'Failed to update rate', false);
          }
        } catch (e) {
          showToast(e.message, false);
        }
      };

      if (rate < activeWholesaleRate) {
        openConfirmModal({
          title: 'Below Wholesale Cost Warning',
          message: `Notice: Retail rate (₹${rate.toFixed(2)}) is lower than your wholesale cost (₹${activeWholesaleRate.toFixed(2)}). You will subsidize ₹${(activeWholesaleRate - rate).toFixed(2)}/min. Continue?`,
          iconType: 'warning',
          confirmText: 'Yes, Apply Rate',
          isDestructive: true,
          onConfirm: applyRate
        });
      } else {
        await applyRate();
      }
    }
  });
}

function promptTopupClient(tenantId) {
  openInputModal({
    title: 'Top Up Talktime Credit',
    subtitle: `Allocate prepaid airtime funds to client tenant: ${tenantId}`,
    label: 'Credit Amount (INR)',
    value: '500',
    prefix: '₹',
    suffix: 'INR',
    step: '50',
    min: '10',
    presets: [
      { label: '+₹250', value: 250 },
      { label: '+₹500', value: 500 },
      { label: '+₹1,000', value: 1000 },
      { label: '+₹2,500', value: 2500 },
      { label: '+₹5,000', value: 5000 }
    ],
    hint: 'Credits are instantly deposited into client wallet balance for automated AI calling.',
    confirmText: 'Allocate Credits 💳',
    onConfirm: async (amtStr) => {
      const amt = parseFloat(amtStr);
      if (isNaN(amt) || amt <= 0) {
        showToast('Invalid credit amount entered', false);
        return;
      }
      try {
        const res = await resellerFetch(`/api/v1/reseller/clients/${encodeURIComponent(tenantId)}/topup`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ amount: amt, notes: "Reseller Credit Allocation" })
        });
        if (res.ok) {
          showToast(`✓ Added ₹${amt} credits to ${tenantId}!`);
          loadResellerData();
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
