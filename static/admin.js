"use strict";

// =====================================================================
// DigiBuzz AI Calling Agent — Admin Management Panel
// =====================================================================

const adminState = {
  users: [],
  metrics: null,
  filterSearch: "",
  filterRole: "",
  filterStatus: "",
};

// =====================================================================
// Load & Render Admin Dashboard
// =====================================================================
async function loadAdminData() {
  if (!authState.isAdmin) {
    if (typeof navigateTo === "function") navigateTo("home");
    return;
  }

  await Promise.all([loadAdminMetrics(), loadAdminUsers()]);
}

async function loadAdminMetrics() {
  try {
    const res = await fetch("/api/admin/metrics", { credentials: "same-origin" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const metrics = await res.json();
    adminState.metrics = metrics;

    const elUsers = document.getElementById("statAdminUsers");
    const elActive = document.getElementById("statAdminActive");
    const elCalls = document.getElementById("statAdminCalls");
    const elResolved = document.getElementById("statAdminResolved");
    const elFaqs = document.getElementById("statAdminFaqs");
    const elProducts = document.getElementById("statAdminProducts");

    if (elUsers) elUsers.textContent = metrics.total_users ?? 0;
    if (elActive) elActive.textContent = `${metrics.active_users ?? 0} active`;
    if (elCalls) elCalls.textContent = metrics.total_calls ?? 0;
    if (elResolved) {
      const pct = metrics.total_calls ? Math.round((metrics.resolved_calls / metrics.total_calls) * 100) : 100;
      elResolved.textContent = `${pct}% (${metrics.resolved_calls ?? 0})`;
    }
    if (elFaqs) elFaqs.textContent = metrics.total_faqs ?? 0;
    if (elProducts) elProducts.textContent = metrics.total_products ?? 0;
  } catch (err) {
    console.error("Failed to load admin metrics:", err);
  }
}

async function loadAdminUsers() {
  const tbody = document.getElementById("adminUsersTableBody");
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="8" class="loading-row">Loading user directory…</td></tr>';

  try {
    const res = await fetch("/api/admin/users", { credentials: "same-origin" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    adminState.users = await res.json();
    applyAdminFilters();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="8" class="loading-row" style="color:var(--red)">Failed to load users: ${err.message}</td></tr>`;
  }
}

// =====================================================================
// Filtering & Rendering
// =====================================================================
function applyAdminFilters() {
  let filtered = [...adminState.users];

  if (adminState.filterSearch) {
    const q = adminState.filterSearch.toLowerCase();
    filtered = filtered.filter(
      (u) =>
        (u.full_name && u.full_name.toLowerCase().includes(q)) ||
        (u.email && u.email.toLowerCase().includes(q)) ||
        (u.phone && u.phone.includes(q))
    );
  }

  if (adminState.filterRole) {
    filtered = filtered.filter((u) => u.role === adminState.filterRole);
  }

  if (adminState.filterStatus) {
    const isActive = adminState.filterStatus === "active";
    filtered = filtered.filter((u) => Boolean(u.is_active) === isActive);
  }

  renderAdminUsersTable(filtered);
}

function renderAdminUsersTable(users) {
  const tbody = document.getElementById("adminUsersTableBody");
  const countEl = document.getElementById("adminUsersCount");
  if (!tbody) return;

  if (countEl) countEl.textContent = `${users.length} account${users.length !== 1 ? "s" : ""}`;

  if (!users || users.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="loading-row">No user accounts found matching criteria.</td></tr>';
    return;
  }

  tbody.innerHTML = users
    .map((u) => {
      const initials = (u.full_name || "User")
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2);

      const isCurrentAdmin = authState.user && authState.user.id === u.id;
      const statusClass = u.is_active ? "badge-success" : "badge-danger";
      const statusText = u.is_active ? "Active" : "Suspended";
      const roleBadge = u.role === "admin" ? '<span class="role-badge admin">Admin</span>' : '<span class="role-badge member">User</span>';

      return `
      <tr>
        <td>
          <div class="user-cell">
            <div class="user-avatar-small">${escAdmin(initials)}</div>
            <div class="user-meta">
              <span class="user-name-strong">${escAdmin(u.full_name)}</span>
              ${isCurrentAdmin ? '<span class="you-pill">You</span>' : ""}
            </div>
          </div>
        </td>
        <td><span class="mono">${escAdmin(u.email)}</span></td>
        <td>${escAdmin(u.phone || "—")}</td>
        <td>${roleBadge}</td>
        <td><span class="badge ${statusClass}">${statusText}</span></td>
        <td><strong>${u.call_count ?? 0}</strong> calls</td>
        <td style="font-size:.8rem;color:var(--text-muted);">${escAdmin(u.created_at ? u.created_at.slice(0, 10) : "—")}</td>
        <td>
          <div class="action-btn-group">
            ${
              isCurrentAdmin
                ? '<span style="font-size:.75rem;color:var(--text-muted);">(Self)</span>'
                : `
              <button class="btn btn-xs ${u.is_active ? "btn-outline-danger" : "btn-outline-success"}" 
                onclick="toggleUserStatus(${u.id}, ${Boolean(u.is_active)})" 
                title="${u.is_active ? "Suspend Account" : "Activate Account"}">
                ${u.is_active ? "🛑 Suspend" : "✅ Activate"}
              </button>
              <button class="btn btn-xs btn-outline" 
                onclick="toggleUserRole(${u.id}, '${u.role}')" 
                title="${u.role === "admin" ? "Demote to Member" : "Promote to Admin"}">
                ${u.role === "admin" ? "⬇️ Demote" : "⭐ Make Admin"}
              </button>
            `
            }
          </div>
        </td>
      </tr>`;
    })
    .join("");
}

// =====================================================================
// User Actions (Status & Role Toggle)
// =====================================================================
async function toggleUserStatus(userId, currentStatus) {
  const actionText = currentStatus ? "suspend" : "activate";
  if (!confirm(`Are you sure you want to ${actionText} this user's account?`)) return;

  try {
    const res = await fetch(`/api/admin/users/${userId}/status`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_active: !currentStatus }),
      credentials: "same-origin",
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to update user status.");

    await loadAdminData();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

async function toggleUserRole(userId, currentRole) {
  const newRole = currentRole === "admin" ? "user" : "admin";
  const actionText = newRole === "admin" ? "promote to Administrator" : "demote to Member";
  if (!confirm(`Are you sure you want to ${actionText}?`)) return;

  try {
    const res = await fetch(`/api/admin/users/${userId}/role`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: newRole }),
      credentials: "same-origin",
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to update user role.");

    await loadAdminData();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

function escAdmin(str) {
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
  const searchInput = document.getElementById("adminUserSearch");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      adminState.filterSearch = e.target.value.trim();
      applyAdminFilters();
    });
  }

  const roleFilter = document.getElementById("adminRoleFilter");
  if (roleFilter) {
    roleFilter.addEventListener("change", (e) => {
      adminState.filterRole = e.target.value;
      applyAdminFilters();
    });
  }

  const statusFilter = document.getElementById("adminStatusFilter");
  if (statusFilter) {
    statusFilter.addEventListener("change", (e) => {
      adminState.filterStatus = e.target.value;
      applyAdminFilters();
    });
  }

  const btnRefreshAdmin = document.getElementById("btnRefreshAdmin");
  if (btnRefreshAdmin) {
    btnRefreshAdmin.addEventListener("click", loadAdminData);
  }
});
