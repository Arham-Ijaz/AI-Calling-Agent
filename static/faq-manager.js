"use strict";

// =====================================================================
// DigiBuzz AI Calling Agent — FAQ Manager
// =====================================================================

const faqState = {
  allFaqs: [],
  editingId: null,
};

// =====================================================================
// Load & Render
// =====================================================================
async function loadFAQs() {
  const tbody = document.getElementById("faqTableBody");
  tbody.innerHTML = '<tr><td colspan="5" class="loading-row">Loading FAQs…</td></tr>';

  try {
    const res = await fetch("/api/faqs/all");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    faqState.allFaqs = await res.json();
    populateIntentDropdown();
    renderFAQTable(faqState.allFaqs);
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="loading-row" style="color:var(--red)">Failed to load FAQs: ${err.message}</td></tr>`;
  }
}

function populateIntentDropdown() {
  const sel = document.getElementById("faqIntentFilter");
  if (!sel) return;
  const current = sel.value;
  const intents = [...new Set(faqState.allFaqs.map((f) => f.intent).filter(Boolean))].sort();

  sel.innerHTML = '<option value="">All Intents</option>';
  intents.forEach((intent) => {
    const opt = document.createElement("option");
    opt.value = intent;
    opt.textContent = intent;
    if (intent === current) opt.selected = true;
    sel.appendChild(opt);
  });
}

function renderFAQTable(faqs) {
  const tbody = document.getElementById("faqTableBody");
  const countEl = document.getElementById("faqCount");

  countEl.textContent = `${faqs.length} record${faqs.length !== 1 ? "s" : ""}`;

  if (faqs.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="loading-row">No FAQs match your search criteria.</td></tr>';
    return;
  }

  tbody.innerHTML = faqs
    .map(
      (f) => `
      <tr data-id="${f.id}">
        <td><strong>#${f.id}</strong></td>
        <td><code style="font-size:.8rem;background:var(--surface-card);padding:.2rem .45rem;border-radius:4px;font-weight:600;">${escFaq(f.intent)}</code></td>
        <td class="td-truncate" style="font-weight:500;" title="${escFaq(f.question)}">${escFaq(f.question)}</td>
        <td class="td-answer" title="${escFaq(f.answer)}">${escFaq(f.answer)}</td>
        <td>
          <div class="action-cell">
            <button class="btn btn-sm btn-edit-sm" onclick="openEditFAQModal(${f.id})">✏️ Edit</button>
            <button class="btn btn-sm btn-danger-sm" onclick="deleteFAQ(${f.id})">🗑️ Delete</button>
          </div>
        </td>
      </tr>`
    )
    .join("");
}

// =====================================================================
// Search / Filter
// =====================================================================
function initFaqSearch() {
  const input = document.getElementById("faqSearch");
  const intentFilter = document.getElementById("faqIntentFilter");

  function applyFaqFilter() {
    const q = (input ? input.value : "").toLowerCase().trim();
    const selectedIntent = intentFilter ? intentFilter.value : "";

    const filtered = faqState.allFaqs.filter((f) => {
      const matchesIntent = !selectedIntent || f.intent === selectedIntent;
      const matchesQ =
        !q ||
        f.intent.toLowerCase().includes(q) ||
        f.question.toLowerCase().includes(q) ||
        f.answer.toLowerCase().includes(q);
      return matchesIntent && matchesQ;
    });

    renderFAQTable(filtered);
  }

  if (input) input.addEventListener("input", applyFaqFilter);
  if (intentFilter) intentFilter.addEventListener("change", applyFaqFilter);
}

// =====================================================================
// Modal — Open / Close
// =====================================================================
function openAddFAQModal() {
  faqState.editingId = null;
  document.getElementById("faqModalTitle").textContent = "Add FAQ";
  document.getElementById("faqIntent").value = "";
  document.getElementById("faqQuestion").value = "";
  document.getElementById("faqAnswer").value = "";
  hideFaqError();
  document.getElementById("faqModal").classList.remove("hidden");
  document.getElementById("faqIntent").focus();
}

function openEditFAQModal(id) {
  const faq = faqState.allFaqs.find((f) => f.id === id);
  if (!faq) return;
  faqState.editingId = id;
  document.getElementById("faqModalTitle").textContent = "Edit FAQ";
  document.getElementById("faqIntent").value = faq.intent;
  document.getElementById("faqQuestion").value = faq.question;
  document.getElementById("faqAnswer").value = faq.answer;
  hideFaqError();
  document.getElementById("faqModal").classList.remove("hidden");
  document.getElementById("faqQuestion").focus();
}

function closeFAQModal() {
  document.getElementById("faqModal").classList.add("hidden");
  faqState.editingId = null;
}

function showFaqError(msg) {
  const el = document.getElementById("faqFormError");
  el.textContent = msg;
  el.classList.remove("hidden");
}

function hideFaqError() {
  document.getElementById("faqFormError").classList.add("hidden");
}

// =====================================================================
// Save FAQ (POST or PUT)
// =====================================================================
async function saveFAQ() {
  const intent = document.getElementById("faqIntent").value.trim();
  const question = document.getElementById("faqQuestion").value.trim();
  const answer = document.getElementById("faqAnswer").value.trim();

  if (!intent) { showFaqError("Intent is required."); return; }
  if (!question) { showFaqError("Question is required."); return; }
  if (!answer) { showFaqError("Answer is required."); return; }

  hideFaqError();

  const saveBtn = document.getElementById("faqModalSave");
  saveBtn.disabled = true;
  saveBtn.textContent = "Saving…";

  try {
    const isEdit = faqState.editingId !== null;
    const url = isEdit ? `/api/faqs/${faqState.editingId}` : "/api/faqs";
    const method = isEdit ? "PUT" : "POST";

    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ intent, question, answer }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    closeFAQModal();
    await loadFAQs();
  } catch (err) {
    showFaqError("Save failed: " + err.message);
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = "Save FAQ";
  }
}

// =====================================================================
// Delete FAQ
// =====================================================================
async function deleteFAQ(id) {
  const faq = faqState.allFaqs.find((f) => f.id === id);
  const preview = faq ? `"${faq.question.slice(0, 60)}"` : `#${id}`;
  if (!confirm(`Delete FAQ ${preview}?\n\nThis cannot be undone.`)) return;

  try {
    const res = await fetch(`/api/faqs/${id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 204) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    await loadFAQs();
  } catch (err) {
    alert("Delete failed: " + err.message);
  }
}

// =====================================================================
// Escape helper (local scope)
// =====================================================================
function escFaq(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// =====================================================================
// Initialization
// =====================================================================
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("btnAddFAQ").addEventListener("click", openAddFAQModal);
  document.getElementById("faqModalClose").addEventListener("click", closeFAQModal);
  document.getElementById("faqModalCancel").addEventListener("click", closeFAQModal);
  document.getElementById("faqModalSave").addEventListener("click", saveFAQ);

  // Close modal when clicking overlay background
  document.getElementById("faqModal").addEventListener("click", (e) => {
    if (e.target === document.getElementById("faqModal")) closeFAQModal();
  });

  initFaqSearch();

  // Load FAQs when the FAQ tab is active (may already be active on page load)
  if (document.getElementById("page-faqs").classList.contains("active")) {
    loadFAQs();
  }
});
