"use strict";

// =====================================================================
// DigiBuzz AI Calling Agent — FAQ Manager (Multi-Tenant Scoped)
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
  if (tbody) {
    tbody.innerHTML = '<tr><td colspan="6" class="loading-row">Loading FAQs…</td></tr>';
  }

  try {
    const res = await fetch("/api/faqs/all", { credentials: "same-origin" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    faqState.allFaqs = await res.json();
    populateIntentDropdown();
    renderFAQTable(faqState.allFaqs);
  } catch (err) {
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="6" class="loading-row" style="color:var(--red)">Failed to load FAQs: ${err.message}</td></tr>`;
    }
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
  if (!tbody) return;

  if (countEl) countEl.textContent = `${faqs.length} record${faqs.length !== 1 ? "s" : ""}`;

  if (!faqs || faqs.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="loading-row">No FAQs match your search criteria.</td></tr>';
    return;
  }

  tbody.innerHTML = faqs
    .map((f) => {
      const isOwner = authState.user && f.user_id === authState.user.id;
      const canEdit = authState.isAdmin || isOwner;

      let scopeBadge = '<span class="badge" style="background:var(--surface-card);color:var(--text-muted);font-size:.7rem;">Platform</span>';
      if (f.user_id) {
        const ownerLabel = authState.isAdmin ? (f.owner_name || "Custom") : (isOwner ? "Yours" : "Custom");
        scopeBadge = `<span class="badge" style="background:var(--brand-light);color:var(--brand);font-size:.7rem;">${escFaq(ownerLabel)}</span>`;
      }

      return `
      <tr data-id="${f.id}">
        <td><strong>#${f.id}</strong></td>
        <td><code style="font-size:.8rem;background:var(--surface-card);padding:.2rem .45rem;border-radius:4px;font-weight:600;">${escFaq(f.intent)}</code></td>
        <td class="td-truncate" style="font-weight:500;" title="${escFaq(f.question)}">${escFaq(f.question)}</td>
        <td class="td-answer" title="${escFaq(f.answer)}">${escFaq(f.answer)}</td>
        <td>${scopeBadge}</td>
        <td>
          <div class="action-cell">
            ${
              canEdit
                ? `
              <button class="btn btn-sm btn-edit-sm" onclick="openEditFAQModal(${f.id})">✏️ Edit</button>
              <button class="btn btn-sm btn-danger-sm" onclick="deleteFAQ(${f.id})">🗑️ Delete</button>
            `
                : '<span style="font-size:.75rem;color:var(--text-muted);">Default</span>'
            }
          </div>
        </td>
      </tr>`;
    })
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
  if (!authState.isLoggedIn) {
    if (typeof openAuthModal === "function") {
      openAuthModal("login");
    } else {
      alert("Please sign in to add your custom FAQs.");
    }
    return;
  }

  faqState.editingId = null;
  const title = document.getElementById("faqModalTitle");
  if (title) title.textContent = "Add FAQ";
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

  const isOwner = authState.user && faq.user_id === authState.user.id;
  if (!authState.isAdmin && !isOwner) {
    alert("You do not have permission to modify this FAQ.");
    return;
  }

  faqState.editingId = id;
  const title = document.getElementById("faqModalTitle");
  if (title) title.textContent = "Edit FAQ";
  document.getElementById("faqIntent").value = faq.intent;
  document.getElementById("faqQuestion").value = faq.question;
  document.getElementById("faqAnswer").value = faq.answer;
  hideFaqError();
  document.getElementById("faqModal").classList.remove("hidden");
  document.getElementById("faqQuestion").focus();
}

function closeFAQModal() {
  const modal = document.getElementById("faqModal");
  if (modal) modal.classList.add("hidden");
  faqState.editingId = null;
}

function showFaqError(msg) {
  const el = document.getElementById("faqFormError");
  if (el) {
    el.textContent = msg;
    el.classList.remove("hidden");
  }
}

function hideFaqError() {
  const el = document.getElementById("faqFormError");
  if (el) {
    el.textContent = "";
    el.classList.add("hidden");
  }
}

// =====================================================================
// Save (Create or Update)
// =====================================================================
async function saveFAQ() {
  hideFaqError();
  const intent = document.getElementById("faqIntent").value.trim();
  const question = document.getElementById("faqQuestion").value.trim();
  const answer = document.getElementById("faqAnswer").value.trim();
  const saveBtn = document.getElementById("faqModalSave");

  if (!intent) {
    showFaqError("Intent is required (e.g. delivery_time).");
    return;
  }
  if (!question) {
    showFaqError("Question is required.");
    return;
  }
  if (!answer) {
    showFaqError("Answer is required.");
    return;
  }

  saveBtn.disabled = true;
  saveBtn.textContent = "Saving…";

  const payload = { intent, question, answer };
  const isEdit = faqState.editingId !== null;
  const url = isEdit ? `/api/faqs/${faqState.editingId}` : "/api/faqs";
  const method = isEdit ? "PUT" : "POST";

  try {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      credentials: "same-origin",
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    closeFAQModal();
    await loadFAQs();
    if (typeof loadChips === "function") loadChips();
  } catch (err) {
    showFaqError(err.message || "Failed to save FAQ.");
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = "Save FAQ";
  }
}

// =====================================================================
// Delete
// =====================================================================
async function deleteFAQ(id) {
  const faq = faqState.allFaqs.find((f) => f.id === id);
  const qPreview = faq ? `"${faq.question.slice(0, 40)}…"` : `#${id}`;

  if (!confirm(`Are you sure you want to delete FAQ ${qPreview}?`)) return;

  try {
    const res = await fetch(`/api/faqs/${id}`, { method: "DELETE", credentials: "same-origin" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    await loadFAQs();
    if (typeof loadChips === "function") loadChips();
  } catch (err) {
    alert(`Failed to delete FAQ: ${err.message}`);
  }
}

// =====================================================================
// Helpers
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
  const btnAdd = document.getElementById("btnAddFaq");
  if (btnAdd) btnAdd.addEventListener("click", openAddFAQModal);

  const btnRefresh = document.getElementById("btnRefreshFaqs");
  if (btnRefresh) btnRefresh.addEventListener("click", loadFAQs);

  const btnCancel = document.getElementById("faqModalCancel");
  if (btnCancel) btnCancel.addEventListener("click", closeFAQModal);

  const btnClose = document.getElementById("faqModalClose");
  if (btnClose) btnClose.addEventListener("click", closeFAQModal);

  const btnSave = document.getElementById("faqModalSave");
  if (btnSave) btnSave.addEventListener("click", saveFAQ);

  const modal = document.getElementById("faqModal");
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeFAQModal();
    });
  }

  initFaqSearch();
});
