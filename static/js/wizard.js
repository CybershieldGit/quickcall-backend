/**
 * 4-Step Executive Onboarding Wizard Modal Controller (#wizard-modal)
 * Step 1: Profile & Ownership
 * Step 2: Telephony Integration (Turnkey Master Pool vs BYOC Exotel vs SIP Trunk)
 * Step 3: AI Persona & Bilingual Voice Tuning
 * Step 4: Dual-PDF & Website Ingestion + Commercial Settlement
 */

let currentWizardStep = 1;
const totalWizardSteps = 4;

function openOnboardingWizard() {
  currentWizardStep = 1;
  updateWizardStepUi();
  const modal = document.getElementById('wizard-modal');
  if (modal) {
    modal.classList.add('active');
    document.body.style.overflow = 'hidden';
  }
  if (typeof loadAvailableNumbers === 'function') {
    loadAvailableNumbers();
  }
}

function closeOnboardingWizard() {
  const modal = document.getElementById('wizard-modal');
  if (modal) {
    modal.classList.remove('active');
    document.body.style.overflow = '';
  }
}

function handleWizardBackdrop(e) {
  if (e.target && e.target.id === 'wizard-modal') {
    closeOnboardingWizard();
  }
}

function goToWizardStep(step) {
  if (step < 1 || step > totalWizardSteps) return;

  // Validation before proceeding forward
  if (step > currentWizardStep) {
    if (currentWizardStep === 1) {
      const bizName = document.getElementById('wz-biz-name')?.value.trim();
      const email = document.getElementById('wz-email')?.value.trim();
      if (!bizName || !email) {
        showToast('Please provide Company Name and Work Email', false);
        return;
      }
    } else if (currentWizardStep === 2) {
      const esc = document.getElementById('wz-esc-number')?.value.trim();
      if (!esc) {
        showToast('Please provide a Human Escalation Phone Number', false);
        return;
      }
    }
  }

  currentWizardStep = step;
  updateWizardStepUi();
}

function updateWizardStepUi() {
  // Update Step Pages
  for (let i = 1; i <= totalWizardSteps; i++) {
    const page = document.getElementById(`wz-page-${i}`);
    if (page) {
      page.style.display = (i === currentWizardStep) ? 'block' : 'none';
    }

    const node = document.getElementById(`wz-node-${i}`);
    if (node) {
      node.classList.remove('active', 'completed');
      if (i === currentWizardStep) {
        node.classList.add('active');
      } else if (i < currentWizardStep) {
        node.classList.add('completed');
      }
    }

    const div = document.getElementById(`wz-div-${i}`);
    if (div) {
      if (i < currentWizardStep) div.classList.add('filled');
      else div.classList.remove('filled');
    }
  }

  // Back Button visibility
  const btnBack = document.getElementById('wz-btn-back');
  if (btnBack) {
    btnBack.style.display = currentWizardStep > 1 ? 'inline-flex' : 'none';
  }

  // Next / Submit Button label
  const btnNext = document.getElementById('wz-btn-next');
  if (btnNext) {
    if (currentWizardStep === totalWizardSteps) {
      btnNext.innerHTML = '🚀 Activate Tenant in 30 Seconds';
      btnNext.classList.remove('btn-outline');
      btnNext.classList.add('btn-primary');
    } else {
      btnNext.innerHTML = 'Next Step →';
    }
  }
}

function toggleWizardTelephonyMode(mode) {
  const byocFields = document.getElementById('wz-byoc-fields');
  const sipFields = document.getElementById('wz-sip-fields');
  const poolFields = document.getElementById('wz-pool-fields');

  if (byocFields) byocFields.style.display = (mode === 'byoc') ? 'block' : 'none';
  if (sipFields) sipFields.style.display = (mode === 'sip_trunk') ? 'block' : 'none';
  if (poolFields) poolFields.style.display = (mode === 'master_pool') ? 'block' : 'none';
}

function toggleWizardCustomDid(val) {
  const custom = document.getElementById('wz-custom-did');
  if (custom) {
    custom.style.display = (val === 'custom') ? 'block' : 'none';
  }
}

async function handleWizardNext() {
  if (currentWizardStep < totalWizardSteps) {
    goToWizardStep(currentWizardStep + 1);
  } else {
    await submitWizardActivation();
  }
}

async function submitWizardActivation() {
  const btn = document.getElementById('wz-btn-next');
  btn.disabled = true;
  btn.innerHTML = '⏳ Provisioning Tenant & Telephony...';

  const bizName = document.getElementById('wz-biz-name')?.value.trim();
  const email = document.getElementById('wz-email')?.value.trim();
  const plan = document.getElementById('wz-plan')?.value || 'growth';
  const partnerId = document.getElementById('wz-reseller-id')?.value || 'partner_apex';
  const retailRate = parseFloat(document.getElementById('wz-retail-rate')?.value || '5.00');

  let didNumber = document.getElementById('wz-did-select')?.value;
  if (didNumber === 'custom') {
    didNumber = document.getElementById('wz-custom-did')?.value.trim();
  }

  const advisorName = document.getElementById('wz-advisor-name')?.value.trim() || 'Aanya';
  const escNumber = document.getElementById('wz-esc-number')?.value.trim() || '+917022721098';
  const promptTemplate = document.getElementById('wz-prompt-template')?.value || '';
  const websiteUrl = document.getElementById('wz-website-url')?.value.trim();

  try {
    // 1. Provision Sub-Client
    const res = await fetch('/api/v1/reseller/clients', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        reseller_id: partnerId,
        business_name: bizName,
        email: email,
        plan: plan,
        retail_rate_per_min: retailRate,
        did_number: didNumber || null,
        advisor_name: advisorName,
        escalation_number: escNumber,
        knowledge_text: promptTemplate
      })
    });

    const data = await res.json();
    if (!res.ok || data.status !== 'success') {
      throw new Error(data.detail || data.message || 'Onboarding failed');
    }

    const newTid = data.client?.tenant_id;

    // 2. Ingest Slot 1 PDF (Company Overview) if provided
    const pdf1Input = document.getElementById('wz-pdf-slot1');
    if (pdf1Input && pdf1Input.files && pdf1Input.files[0]) {
      btn.innerHTML = '📄 Ingesting Slot 1 (Overview) into RAG Vector DB...';
      const form1 = new FormData();
      form1.append('tenant_id', newTid);
      form1.append('file', pdf1Input.files[0]);
      form1.append('slot', 'slot1');
      await fetch('/api/v1/tenant/upload-knowledge-pdf', { method: 'POST', body: form1 });
    }

    // 3. Ingest Slot 2 PDF (Product Catalog) if provided
    const pdf2Input = document.getElementById('wz-pdf-slot2');
    if (pdf2Input && pdf2Input.files && pdf2Input.files[0]) {
      btn.innerHTML = '📦 Ingesting Slot 2 (Catalog) into RAG Vector DB...';
      const form2 = new FormData();
      form2.append('tenant_id', newTid);
      form2.append('file', pdf2Input.files[0]);
      form2.append('slot', 'slot2');
      await fetch('/api/v1/tenant/upload-knowledge-pdf', { method: 'POST', body: form2 });
    }

    // 4. Ingest Website URL if provided
    if (websiteUrl) {
      btn.innerHTML = '🌐 Crawling & Ingesting Website Text...';
      try {
        await fetch('/api/v1/tenant/scrape-website', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tenant_id: newTid, website_url: websiteUrl })
        });
      } catch (e) {
        console.warn('Website scrape non-critical warning:', e);
      }
    }

    showToast(`✓ Onboarded ${bizName} successfully!`);
    btn.innerHTML = '✓ Success!';

    setTimeout(() => {
      closeOnboardingWizard();
      btn.disabled = false;
      btn.innerHTML = 'Next Step →';
      if (typeof loadTenantsList === 'function') loadTenantsList();
      if (typeof openClientDashboard === 'function') openClientDashboard(newTid);
    }, 700);

  } catch (err) {
    btn.disabled = false;
    btn.innerHTML = '🚀 Activate Tenant in 30 Seconds';
    showToast(`Error: ${err.message}`, false);
  }
}

window.openOnboardingWizard = openOnboardingWizard;
window.closeOnboardingWizard = closeOnboardingWizard;
window.handleWizardBackdrop = handleWizardBackdrop;
window.goToWizardStep = goToWizardStep;
window.toggleWizardTelephonyMode = toggleWizardTelephonyMode;
window.toggleWizardCustomDid = toggleWizardCustomDid;
window.handleWizardNext = handleWizardNext;
