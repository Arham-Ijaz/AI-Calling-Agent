"use strict";

// =====================================================================
// AI Calling Agent — Admin Management Panel
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

  await Promise.all([
    loadAdminMetrics(),
    loadAdminUsers(),
    loadAdminHandoffs(),
    loadAdminPendingOrders(),
    loadAdminCancelRequests(),
    loadAdminAllOrders(),
    loadBusinessSettingsForm(),
  ]);
  startAdminAutoPolling();
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
      const isPrimary = Boolean(u.is_primary) || u.id === 1 || (u.email && (u.email.toLowerCase() === "admin@digibuzz.com" || u.email.toLowerCase().startsWith("admin@")));
      const statusClass = u.is_active ? "badge-success" : "badge-danger";
      const statusText = u.is_active ? "Active" : "Suspended";

      let roleBadge = '<span class="role-badge member">User</span>';
      if (isPrimary) {
        roleBadge = '<span class="role-badge admin">👑 Primary Admin</span>';
      } else if (u.role === "admin") {
        roleBadge = '<span class="role-badge admin">⭐ Admin</span>';
      }

      let actionHtml = "";
      if (isCurrentAdmin) {
        actionHtml = `
          <button class="btn btn-xs btn-outline" 
            onclick="openEditUserModal(${u.id})" 
            title="Edit Profile Details">
            ✏️ Edit
          </button>
          <button class="btn btn-xs btn-outline" 
            onclick="openAdminPasswordModal(${u.id}, '${escAdmin(u.full_name)}', '${escAdmin(u.email)}')" 
            title="Change Your Password">
            🔑 My Password
          </button>
          <span style="font-size:.72rem;color:var(--brand);font-weight:700;">(You)</span>
        `;
      } else if (isPrimary) {
        actionHtml = '<span class="badge" style="background:rgba(234,179,8,0.15);color:#eab308;border:1px solid rgba(234,179,8,0.3);font-size:.75rem;font-weight:600;">🛡️ Protected Primary Admin</span>';
      } else {
        actionHtml = `
          <button class="btn btn-xs btn-outline" 
            onclick="openEditUserModal(${u.id})" 
            title="Edit User Details">
            ✏️ Edit
          </button>
          <button class="btn btn-xs btn-outline" 
            onclick="openAdminPasswordModal(${u.id}, '${escAdmin(u.full_name)}', '${escAdmin(u.email)}')" 
            title="Set / Change User Password">
            🔑 Password
          </button>
          <button class="btn btn-xs ${u.is_active ? "btn-outline-danger" : "btn-outline-success"}" 
            onclick="toggleUserStatus(${u.id}, ${Boolean(u.is_active)})" 
            title="${u.is_active ? "Suspend Account" : "Activate Account"}">
            ${u.is_active ? "🛑 Suspend" : "✅ Activate"}
          </button>
          <button class="btn btn-xs ${u.role === "admin" ? "btn-outline" : "btn-brand"}" 
            onclick="toggleUserRole(${u.id}, '${u.role}')" 
            title="${u.role === "admin" ? "Demote to Member" : "Promote to Administrator"}">
            ${u.role === "admin" ? "⬇️ Demote" : "⭐ Make Admin"}
          </button>
          <button class="btn btn-xs btn-outline-danger" 
            onclick="deleteUser(${u.id}, '${escAdmin(u.full_name)}')" 
            title="Permanently Delete Account">
            🗑️ Delete
          </button>
        `;
      }

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
            ${actionHtml}
          </div>
        </td>
      </tr>`;
    })
    .join("");
}

// =====================================================================
// User Actions (Status, Role, Edit, Delete)
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

    // Clear filter if set so promoted user stays visible
    const roleFilter = document.getElementById("adminRoleFilter");
    if (roleFilter && adminState.filterRole) {
      adminState.filterRole = "";
      roleFilter.value = "";
    }

    alert(data.message || (newRole === "admin" ? "⭐ User promoted to Administrator!" : "⬇️ User demoted to Member."));
    await loadAdminData();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

async function deleteUser(userId, userName) {
  if (!confirm(`Are you sure you want to permanently delete account "${userName}"? All associated call history will be removed.`)) {
    return;
  }

  try {
    const res = await fetch(`/api/admin/users/${userId}`, {
      method: "DELETE",
      credentials: "same-origin",
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to delete user account.");

    await loadAdminData();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

// =====================================================================
// Add User Modal Handling
// =====================================================================
function openAddUserModal() {
  const modal = document.getElementById("adminAddUserModal");
  const errEl = document.getElementById("adminAddError");
  if (!modal) return;

  document.getElementById("adminAddName").value = "";
  document.getElementById("adminAddEmail").value = "";
  document.getElementById("adminAddPhone").value = "";
  document.getElementById("adminAddPassword").value = "";
  document.getElementById("adminAddRole").value = "user";
  if (errEl) {
    errEl.textContent = "";
    errEl.classList.add("hidden");
  }

  modal.classList.remove("hidden");
}

function closeAddUserModal() {
  const modal = document.getElementById("adminAddUserModal");
  if (modal) modal.classList.add("hidden");
}

async function handleSaveNewUser() {
  const btn = document.getElementById("btnSaveNewAdminUser");
  const errEl = document.getElementById("adminAddError");
  const name = document.getElementById("adminAddName").value.trim();
  const email = document.getElementById("adminAddEmail").value.trim().toLowerCase();
  const phone = document.getElementById("adminAddPhone").value.trim().replace(/[\s\-()]/g, "");
  const password = document.getElementById("adminAddPassword").value;
  const role = document.getElementById("adminAddRole").value;

  if (!name || !email || !phone || !password) {
    if (errEl) {
      errEl.textContent = "All fields are required.";
      errEl.classList.remove("hidden");
    }
    return;
  }

  if (password.length < 8 || !/[A-Za-z]/.test(password) || !/[0-9]/.test(password)) {
    if (errEl) {
      errEl.textContent = "Password must be at least 8 characters and contain both letters and numbers.";
      errEl.classList.remove("hidden");
    }
    return;
  }

  btn.disabled = true;
  btn.textContent = "Creating Account…";
  if (errEl) errEl.classList.add("hidden");

  try {
    const res = await fetch("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        full_name: name,
        email: email,
        phone: phone,
        password: password,
        role: role,
        is_active: true,
      }),
      credentials: "same-origin",
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to create user account.");

    closeAddUserModal();
    alert(`✅ Successfully created ${role === "admin" ? "Administrator" : "User"}: ${data.full_name}`);
    await loadAdminData();
  } catch (err) {
    if (errEl) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Create Account";
  }
}

// =====================================================================
// Edit User Modal Handling
// =====================================================================
function openEditUserModal(userId) {
  const u = adminState.users.find((item) => item.id === userId);
  if (!u) return;

  const modal = document.getElementById("adminEditUserModal");
  const errEl = document.getElementById("adminEditError");
  if (!modal) return;

  const isCurrentAdmin = authState.user && authState.user.id === u.id;
  const isPrimary = Boolean(u.is_primary) || u.id === 1;

  document.getElementById("adminEditUserId").value = u.id;
  document.getElementById("adminEditName").value = u.full_name || "";
  document.getElementById("adminEditEmail").value = u.email || "";
  document.getElementById("adminEditPhone").value = u.phone || "";
  document.getElementById("adminEditRole").value = u.role || "user";
  document.getElementById("adminEditStatus").value = u.is_active ? "active" : "suspended";
  document.getElementById("adminEditPassword").value = "";

  // Prevent locking out self
  const roleSelect = document.getElementById("adminEditRole");
  const statusSelect = document.getElementById("adminEditStatus");
  if (roleSelect) roleSelect.disabled = isPrimary || (isCurrentAdmin && u.role === "admin");
  if (statusSelect) statusSelect.disabled = isPrimary || isCurrentAdmin;

  if (errEl) {
    errEl.textContent = "";
    errEl.classList.add("hidden");
  }

  modal.classList.remove("hidden");
}

function closeEditUserModal() {
  const modal = document.getElementById("adminEditUserModal");
  if (modal) modal.classList.add("hidden");
}

async function handleSaveEditUser() {
  const btn = document.getElementById("btnSaveEditAdminUser");
  const errEl = document.getElementById("adminEditError");
  const userId = document.getElementById("adminEditUserId").value;
  const name = document.getElementById("adminEditName").value.trim();
  const email = document.getElementById("adminEditEmail").value.trim().toLowerCase();
  const phone = document.getElementById("adminEditPhone").value.trim().replace(/[\s\-()]/g, "");
  const role = document.getElementById("adminEditRole").value;
  const statusVal = document.getElementById("adminEditStatus").value;
  const password = document.getElementById("adminEditPassword").value;

  if (!name || !email || !phone) {
    if (errEl) {
      errEl.textContent = "Full name, email, and phone number are required.";
      errEl.classList.remove("hidden");
    }
    return;
  }

  if (password && (password.length < 8 || !/[A-Za-z]/.test(password) || !/[0-9]/.test(password))) {
    if (errEl) {
      errEl.textContent = "New password must be at least 8 characters and contain both letters and numbers.";
      errEl.classList.remove("hidden");
    }
    return;
  }

  btn.disabled = true;
  btn.textContent = "Saving Changes…";
  if (errEl) errEl.classList.add("hidden");

  try {
    const payload = {
      full_name: name,
      email: email,
      phone: phone,
      role: role,
      is_active: statusVal === "active",
    };
    if (password) payload.password = password;

    const res = await fetch(`/api/admin/users/${userId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      credentials: "same-origin",
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to update user account.");

    closeEditUserModal();
    alert(`✅ Changes saved successfully for ${data.full_name}!`);
    await loadAdminData();
  } catch (err) {
    if (errEl) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Save Changes";
  }
}

// =====================================================================
// Admin Direct Password Reset Modal Handling
// =====================================================================
function openAdminPasswordModal(userId, userName, userEmail) {
  const modal = document.getElementById("adminPasswordModal");
  const titleEl = document.getElementById("adminPasswordModalTitle");
  const descEl = document.getElementById("adminPasswordUserDesc");
  const idEl = document.getElementById("adminPasswordUserId");
  const inputEl = document.getElementById("adminNewPasswordInput");
  const errEl = document.getElementById("adminPasswordError");
  const succEl = document.getElementById("adminPasswordSuccess");

  if (!modal) return;

  if (idEl) idEl.value = userId;
  if (titleEl) titleEl.textContent = `🔑 Change Password — ${userName}`;
  if (descEl) descEl.textContent = `Set or reset password for account: ${userName} (${userEmail})`;
  if (inputEl) {
    inputEl.value = "";
    inputEl.type = "password";
  }
  if (errEl) { errEl.textContent = ""; errEl.classList.add("hidden"); }
  if (succEl) { succEl.textContent = ""; succEl.classList.add("hidden"); }

  modal.classList.remove("hidden");
  if (inputEl) inputEl.focus();
}

function closeAdminPasswordModal() {
  const modal = document.getElementById("adminPasswordModal");
  if (modal) modal.classList.add("hidden");
}

function generateRandomStrongPassword() {
  const charsUpper = "ABCDEFGHJKLMNPQRSTUVWXYZ";
  const charsLower = "abcdefghijkmnopqrstuvwxyz";
  const charsNum = "23456789";
  const charsSym = "@#$%!&*";
  let pass = "";
  pass += charsUpper[Math.floor(Math.random() * charsUpper.length)];
  pass += charsLower[Math.floor(Math.random() * charsLower.length)];
  pass += charsNum[Math.floor(Math.random() * charsNum.length)];
  pass += charsSym[Math.floor(Math.random() * charsSym.length)];
  const all = charsUpper + charsLower + charsNum + charsSym;
  for (let i = 0; i < 6; i++) {
    pass += all[Math.floor(Math.random() * all.length)];
  }
  return pass;
}

async function handleSaveAdminPassword() {
  const btn = document.getElementById("btnSaveAdminPassword");
  const userId = document.getElementById("adminPasswordUserId").value;
  const newPass = document.getElementById("adminNewPasswordInput").value;
  const errEl = document.getElementById("adminPasswordError");
  const succEl = document.getElementById("adminPasswordSuccess");

  if (errEl) { errEl.textContent = ""; errEl.classList.add("hidden"); }
  if (succEl) { succEl.textContent = ""; succEl.classList.add("hidden"); }

  if (!newPass || newPass.length < 8) {
    if (errEl) {
      errEl.textContent = "Password must be at least 8 characters.";
      errEl.classList.remove("hidden");
    }
    return;
  }

  if (!/[A-Za-z]/.test(newPass) || !/[0-9]/.test(newPass)) {
    if (errEl) {
      errEl.textContent = "Password must contain both letters and numbers.";
      errEl.classList.remove("hidden");
    }
    return;
  }

  btn.disabled = true;
  btn.textContent = "Updating Password…";

  try {
    const res = await fetch(`/api/admin/users/${userId}/password`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: newPass }),
      credentials: "same-origin",
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to update password.");

    if (succEl) {
      succEl.textContent = `✅ Password updated successfully!`;
      succEl.classList.remove("hidden");
    }

    setTimeout(() => {
      closeAdminPasswordModal();
    }, 1200);
  } catch (err) {
    if (errEl) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Save New Password";
  }
}

function escAdmin(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Attach functions to window for onclick safety
window.openAddUserModal = openAddUserModal;
window.closeAddUserModal = closeAddUserModal;
window.openEditUserModal = openEditUserModal;
window.closeEditUserModal = closeEditUserModal;
window.openAdminPasswordModal = openAdminPasswordModal;
window.closeAdminPasswordModal = closeAdminPasswordModal;
window.toggleUserRole = toggleUserRole;
window.toggleUserStatus = toggleUserStatus;
window.deleteUser = deleteUser;
window.loadAdminData = loadAdminData;

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

  // Add User Modal Events
  const btnOpenAdd = document.getElementById("btnOpenAddAdminUser");
  if (btnOpenAdd) btnOpenAdd.addEventListener("click", openAddUserModal);

  const addModalClose = document.getElementById("adminAddUserModalClose");
  if (addModalClose) addModalClose.addEventListener("click", closeAddUserModal);

  const addModalCancel = document.getElementById("adminAddUserCancel");
  if (addModalCancel) addModalCancel.addEventListener("click", closeAddUserModal);

  const btnSaveNew = document.getElementById("btnSaveNewAdminUser");
  if (btnSaveNew) btnSaveNew.addEventListener("click", handleSaveNewUser);

  // Edit User Modal Events
  const editModalClose = document.getElementById("adminEditUserModalClose");
  if (editModalClose) editModalClose.addEventListener("click", closeEditUserModal);

  const editModalCancel = document.getElementById("adminEditUserCancel");
  if (editModalCancel) editModalCancel.addEventListener("click", closeEditUserModal);

  const btnSaveEdit = document.getElementById("btnSaveEditAdminUser");
  if (btnSaveEdit) btnSaveEdit.addEventListener("click", handleSaveEditUser);

  // Admin Password Modal Events
  const passModalClose = document.getElementById("adminPasswordModalClose");
  if (passModalClose) passModalClose.addEventListener("click", closeAdminPasswordModal);

  const passModalCancel = document.getElementById("adminPasswordCancel");
  if (passModalCancel) passModalCancel.addEventListener("click", closeAdminPasswordModal);

  const btnSavePass = document.getElementById("btnSaveAdminPassword");
  if (btnSavePass) btnSavePass.addEventListener("click", handleSaveAdminPassword);

  const btnGenPass = document.getElementById("btnGenPassword");
  if (btnGenPass) {
    btnGenPass.addEventListener("click", () => {
      const p = generateRandomStrongPassword();
      const input = document.getElementById("adminNewPasswordInput");
      if (input) {
        input.type = "text";
        input.value = p;
      }
    });
  }

  const btnToggleEye = document.getElementById("btnToggleNewPasswordEye");
  if (btnToggleEye) {
    btnToggleEye.addEventListener("click", () => {
      const input = document.getElementById("adminNewPasswordInput");
      if (input) {
        input.type = input.type === "password" ? "text" : "password";
      }
    });
  }

  // Phase 4 & 7 Admin controls
  const btnRefreshHandoffs = document.getElementById("btnRefreshHandoffs");
  if (btnRefreshHandoffs) btnRefreshHandoffs.addEventListener("click", loadAdminHandoffs);

  const btnRefreshPendingOrders = document.getElementById("btnRefreshPendingOrders");
  if (btnRefreshPendingOrders) btnRefreshPendingOrders.addEventListener("click", loadAdminPendingOrders);

  const btnRefreshCancelRequests = document.getElementById("btnRefreshCancelRequests");
  if (btnRefreshCancelRequests) btnRefreshCancelRequests.addEventListener("click", loadAdminCancelRequests);

  const orderSearchInput = document.getElementById("adminOrderSearch");
  if (orderSearchInput) {
    orderSearchInput.addEventListener("input", () => loadAdminAllOrders());
  }

  const orderStatusFilter = document.getElementById("adminOrderStatusFilter");
  if (orderStatusFilter) {
    orderStatusFilter.addEventListener("change", () => loadAdminAllOrders());
  }

  const btnSaveBiz = document.getElementById("btnSaveBusinessSettings");
  if (btnSaveBiz) btnSaveBiz.addEventListener("click", handleSaveBusinessSettings);

  // Reject order modal
  const rejectClose = document.getElementById("adminOrderRejectModalClose");
  if (rejectClose) rejectClose.addEventListener("click", closeRejectOrderModal);
  const rejectCancel = document.getElementById("adminOrderRejectModalCancel");
  if (rejectCancel) rejectCancel.addEventListener("click", closeRejectOrderModal);
  const rejectConfirm = document.getElementById("btnConfirmAdminRejectOrder");
  if (rejectConfirm) rejectConfirm.addEventListener("click", handleConfirmRejectOrder);

  // Audit history modal
  const auditClose = document.getElementById("adminOrderAuditModalClose");
  if (auditClose) auditClose.addEventListener("click", () => document.getElementById("adminOrderAuditModal")?.classList.add("hidden"));
  const auditCloseBtn = document.getElementById("adminOrderAuditModalCloseBtn");
  if (auditCloseBtn) auditCloseBtn.addEventListener("click", () => document.getElementById("adminOrderAuditModal")?.classList.add("hidden"));

  // Resolve handoff modal
  const resolveClose = document.getElementById("adminHandoffResolveModalClose");
  if (resolveClose) resolveClose.addEventListener("click", closeResolveHandoffModal);
  const resolveCancel = document.getElementById("adminHandoffResolveModalCancel");
  if (resolveCancel) resolveCancel.addEventListener("click", closeResolveHandoffModal);
  const resolveConfirm = document.getElementById("btnConfirmResolveHandoff");
  if (resolveConfirm) resolveConfirm.addEventListener("click", handleConfirmResolveHandoff);
});

// =====================================================================
// Phase 7: Human Handoff Requests Management
// =====================================================================
async function loadAdminHandoffs() {
  const container = document.getElementById("adminHandoffsList");
  const countBadge = document.getElementById("adminHandoffOpenCount");
  if (!container) return;

  try {
    const [listRes, sumRes] = await Promise.all([
      fetch("/api/admin/handoffs?status=open", { credentials: "same-origin" }),
      fetch("/api/admin/handoffs/summary", { credentials: "same-origin" }),
    ]);

    if (!listRes.ok) throw new Error(`HTTP ${listRes.status}`);
    const handoffs = await listRes.json();
    const summary = sumRes.ok ? await sumRes.json() : { open_count: handoffs.length };

    if (countBadge) countBadge.textContent = `${summary.open_count ?? handoffs.length} Open`;

    if (!handoffs || handoffs.length === 0) {
      container.innerHTML = `<div style="grid-column: 1 / -1; text-align: center; color: var(--text-muted); padding: 1.5rem;">✅ No active open human handoff requests. All caller inquiries resolved!</div>`;
      return;
    }

    container.innerHTML = handoffs.map((h) => {
      const isUrgent = h.priority === "urgent";
      const cardClass = isUrgent ? "handoff-card urgent-highlight" : "handoff-card";
      const prioClass = h.priority || "normal";
      const custName = h.contact_name || h.call_customer_name || "Unknown Caller";
      const custPhone = h.contact_phone || h.call_customer_phone || "No phone";
      const lastMsg = h.last_user_message ? `"${escapeHtml(h.last_user_message)}"` : "No message recorded";
      const summaryText = h.summary ? escapeHtml(h.summary) : escapeHtml(h.reason || "");

      return `
        <div class="${cardClass}">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <strong>👤 ${escapeHtml(custName)}</strong>
            <span class="handoff-priority-pill ${prioClass}">${prioClass}</span>
          </div>
          <div style="font-size:0.8rem; color:var(--text-muted); display:flex; flex-direction:column; gap:0.2rem;">
            <span>📞 <strong>${escapeHtml(custPhone)}</strong></span>
            <span>🕒 Requested: ${escapeHtml(h.created_at || "")}</span>
            <span>🏷️ Trigger: <code>${escapeHtml(h.trigger_type || "manual")}</code></span>
          </div>
          <div style="background:var(--surface); padding:0.5rem; border-radius:6px; font-size:0.82rem; border-left:3px solid var(--brand);">
            <div style="font-size:0.75rem; color:var(--text-muted); font-weight:600;">LAST CALLER STATEMENT:</div>
            <div style="font-style:italic; margin-top:2px;">${lastMsg}</div>
          </div>
          <div style="font-size:0.8rem; color:var(--text-muted);">
            <strong>Summary:</strong> ${summaryText}
          </div>
          <div style="display:flex; gap:0.4rem; margin-top:auto; padding-top:0.5rem;">
            <button class="btn btn-sm btn-brand btn-assign-handoff" data-handoff-id="${h.id}" style="flex:1;">🙋 Assign to Me</button>
            <button class="btn btn-sm btn-success btn-resolve-handoff" data-handoff-id="${h.id}" style="flex:1;">✅ Mark Resolved</button>
          </div>
        </div>
      `;
    }).join("");

    container.querySelectorAll(".btn-assign-handoff").forEach((b) => {
      b.addEventListener("click", () => handleAssignHandoff(b.dataset.handoffId));
    });
    container.querySelectorAll(".btn-resolve-handoff").forEach((b) => {
      b.addEventListener("click", () => openResolveHandoffModal(b.dataset.handoffId));
    });

  } catch (err) {
    console.error("Failed to load handoffs:", err);
    container.innerHTML = `<div style="grid-column: 1 / -1; color: var(--red); padding: 1rem;">Failed to load handoff requests: ${err.message}</div>`;
  }
}

async function handleAssignHandoff(id) {
  try {
    const res = await fetch(`/api/admin/handoffs/${id}/assign`, { method: "POST", credentials: "same-origin" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showAdminToast("Handoff request assigned to your account.");
    loadAdminHandoffs();
  } catch (err) {
    alert("Failed to assign handoff: " + err.message);
  }
}

function openResolveHandoffModal(id) {
  const modal = document.getElementById("adminHandoffResolveModal");
  const hiddenId = document.getElementById("adminResolveHandoffId");
  const noteInput = document.getElementById("adminResolveStaffNote");
  const err = document.getElementById("adminResolveHandoffError");
  if (!modal || !hiddenId) return;

  hiddenId.value = id;
  if (noteInput) noteInput.value = "";
  if (err) { err.textContent = ""; err.classList.add("hidden"); }
  modal.classList.remove("hidden");
}

function closeResolveHandoffModal() {
  const modal = document.getElementById("adminHandoffResolveModal");
  if (modal) modal.classList.add("hidden");
}

async function handleConfirmResolveHandoff() {
  const id = document.getElementById("adminResolveHandoffId")?.value;
  const staff_note = document.getElementById("adminResolveStaffNote")?.value?.trim() || "";
  const err = document.getElementById("adminResolveHandoffError");
  if (!id) return;

  try {
    const res = await fetch(`/api/admin/handoffs/${id}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ staff_note }),
      credentials: "same-origin",
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    closeResolveHandoffModal();
    showAdminToast("Handoff marked as resolved successfully.");
    loadAdminHandoffs();
  } catch (ex) {
    if (err) { err.textContent = ex.message; err.classList.remove("hidden"); }
  }
}

// =====================================================================
// Phase 4: Orders Pending Confirmation Management
// =====================================================================
async function loadAdminPendingOrders() {
  const tbody = document.getElementById("adminPendingOrdersTableBody");
  const countBadge = document.getElementById("adminPendingOrdersCount");
  if (!tbody) return;

  try {
    const res = await fetch("/api/admin/orders?status=pending_admin_confirmation", { credentials: "same-origin" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const orders = await res.json();

    if (countBadge) countBadge.textContent = `${orders.length} Pending`;
    if (typeof updateAdminOrdersNavBadge === "function") updateAdminOrdersNavBadge();

    if (!orders || orders.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" class="loading-row" style="color:var(--text-muted)">✅ No pending order requests awaiting confirmation.</td></tr>`;
      return;
    }

    tbody.innerHTML = orders.map((o) => {
      const custInfo = `${escapeHtml(o.customer_name || 'Customer')} ${o.customer_phone ? `<br><small class="text-muted">${escapeHtml(o.customer_phone)}</small>` : ''}`;
      const schedTime = o.preferred_datetime ? escapeHtml(o.preferred_datetime) : `<span class="text-muted">—</span>`;
      let warningHtml = "";
      if (o.double_booking_warning && o.double_booking_warning.has_conflict) {
        warningHtml = `<div class="conflict-warning-pill">⚠️ ${escapeHtml(o.double_booking_warning.warning)}</div>`;
      }

      return `
        <tr>
          <td><strong class="mono">${escapeHtml(o.order_number)}</strong></td>
          <td>${custInfo}</td>
          <td>
            <strong>${escapeHtml(o.item_name_snapshot || 'Item')}</strong>
            <span class="role-badge ${o.item_type === 'service' ? 'admin' : 'member'}" style="margin-left:4px;">${escapeHtml(o.item_type || 'product')}</span>
          </td>
          <td>${o.quantity}</td>
          <td><strong>PKR ${(o.total_pkr || 0).toLocaleString()}</strong></td>
          <td>${schedTime} ${warningHtml}</td>
          <td style="font-size:0.8rem; color:var(--text-muted);">${escapeHtml(o.created_at || "")}</td>
          <td>
            <div style="display:flex; gap:0.35rem;">
              <button class="btn btn-sm btn-success btn-confirm-order" data-order-id="${o.id}">Confirm</button>
              <button class="btn btn-sm btn-danger btn-reject-order" data-order-id="${o.id}" data-order-num="${escapeHtml(o.order_number)}">Reject</button>
            </div>
          </td>
        </tr>
      `;
    }).join("");

    tbody.querySelectorAll(".btn-confirm-order").forEach((b) => {
      b.addEventListener("click", () => handleConfirmOrder(b.dataset.orderId));
    });
    tbody.querySelectorAll(".btn-reject-order").forEach((b) => {
      b.addEventListener("click", () => openRejectOrderModal(b.dataset.orderId, b.dataset.orderNum));
    });

  } catch (err) {
    console.error("Failed to load pending orders:", err);
    tbody.innerHTML = `<tr><td colspan="8" class="loading-row" style="color:var(--red)">Failed to load: ${err.message}</td></tr>`;
  }
}

async function handleConfirmOrder(id) {
  try {
    const res = await fetch(`/api/admin/orders/${id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "confirm" }),
      credentials: "same-origin",
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `Server error (${res.status})`);
    }
    showAdminToast("Order confirmed successfully.");
    loadAdminPendingOrders();
    loadAdminAllOrders();
  } catch (err) {
    alert("Failed to confirm order: " + err.message);
  }
}

function openRejectOrderModal(id, orderNum) {
  const modal = document.getElementById("adminOrderRejectModal");
  const hiddenId = document.getElementById("adminRejectOrderId");
  const desc = document.getElementById("adminRejectOrderDesc");
  const noteInput = document.getElementById("adminRejectNoteInput");
  const err = document.getElementById("adminRejectError");
  if (!modal || !hiddenId) return;

  hiddenId.value = id;
  if (desc) desc.textContent = `Rejecting Order #${orderNum} will lock it permanently. Please provide an explanatory note for the customer.`;
  if (noteInput) noteInput.value = "";
  if (err) { err.textContent = ""; err.classList.add("hidden"); }
  modal.classList.remove("hidden");
}

function closeRejectOrderModal() {
  const modal = document.getElementById("adminOrderRejectModal");
  if (modal) modal.classList.add("hidden");
}

async function handleConfirmRejectOrder() {
  const id = document.getElementById("adminRejectOrderId")?.value;
  const admin_note = document.getElementById("adminRejectNoteInput")?.value?.trim();
  const err = document.getElementById("adminRejectError");
  if (!id) return;

  if (!admin_note) {
    if (err) { err.textContent = "Rejection note is required."; err.classList.remove("hidden"); }
    return;
  }

  try {
    const res = await fetch(`/api/admin/orders/${id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "reject", admin_note }),
      credentials: "same-origin",
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `Server error (${res.status})`);
    }
    closeRejectOrderModal();
    showAdminToast("Order rejected and locked.");
    loadAdminPendingOrders();
    loadAdminAllOrders();
  } catch (ex) {
    if (err) { err.textContent = ex.message; err.classList.remove("hidden"); }
  }
}

// =====================================================================
// Phase 4: Cancellation Requests Management
// =====================================================================
async function loadAdminCancelRequests() {
  const tbody = document.getElementById("adminCancelRequestsTableBody");
  const countBadge = document.getElementById("adminCancelRequestsCount");
  if (!tbody) return;

  try {
    const res = await fetch("/api/admin/orders?status=cancel_requested", { credentials: "same-origin" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const orders = await res.json();

    if (countBadge) countBadge.textContent = `${orders.length} Requests`;
    if (typeof updateAdminOrdersNavBadge === "function") updateAdminOrdersNavBadge();

    if (!orders || orders.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="loading-row" style="color:var(--text-muted)">✅ No customer cancellation requests pending.</td></tr>`;
      return;
    }

    tbody.innerHTML = orders.map((o) => {
      const custInfo = `${escapeHtml(o.customer_name || 'Customer')} ${o.customer_phone ? `<br><small class="text-muted">${escapeHtml(o.customer_phone)}</small>` : ''}`;
      const reasonText = o.cancel_reason ? escapeHtml(o.cancel_reason) : `<span class="text-muted">No reason given</span>`;

      return `
        <tr>
          <td><strong class="mono">${escapeHtml(o.order_number)}</strong></td>
          <td>${custInfo}</td>
          <td><strong>${escapeHtml(o.item_name_snapshot || 'Item')}</strong></td>
          <td><strong>PKR ${(o.total_pkr || 0).toLocaleString()}</strong></td>
          <td style="font-style:italic;">${reasonText}</td>
          <td style="font-size:0.8rem; color:var(--text-muted);">${escapeHtml(o.cancel_requested_at || o.updated_at || "")}</td>
          <td>
            <div style="display:flex; gap:0.35rem;">
              <button class="btn btn-sm btn-danger btn-approve-cancel" data-order-id="${o.id}">Approve Cancel</button>
              <button class="btn btn-sm btn-outline btn-reject-cancel" data-order-id="${o.id}">Reject Cancel</button>
            </div>
          </td>
        </tr>
      `;
    }).join("");

    tbody.querySelectorAll(".btn-approve-cancel").forEach((b) => {
      b.addEventListener("click", () => handleApproveCancelOrder(b.dataset.orderId));
    });
    tbody.querySelectorAll(".btn-reject-cancel").forEach((b) => {
      b.addEventListener("click", () => handleRejectCancelOrder(b.dataset.orderId));
    });

  } catch (err) {
    console.error("Failed to load cancel requests:", err);
    tbody.innerHTML = `<tr><td colspan="7" class="loading-row" style="color:var(--red)">Failed to load: ${err.message}</td></tr>`;
  }
}

async function handleApproveCancelOrder(id) {
  if (!confirm("Approve cancellation? This will permanently cancel and lock the order.")) return;
  try {
    const res = await fetch(`/api/admin/orders/${id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "approve_cancel", admin_note: "Admin approved cancellation request" }),
      credentials: "same-origin",
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showAdminToast("Order cancellation approved.");
    loadAdminCancelRequests();
    loadAdminAllOrders();
  } catch (err) {
    alert("Failed: " + err.message);
  }
}

async function handleRejectCancelOrder(id) {
  const note = prompt("Enter note explaining why cancellation is rejected:") || "Cancellation rejected by admin";
  try {
    const res = await fetch(`/api/admin/orders/${id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "reject_cancel", admin_note: note }),
      credentials: "same-origin",
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showAdminToast("Cancellation request rejected; order returned to previous status.");
    loadAdminCancelRequests();
    loadAdminAllOrders();
  } catch (err) {
    alert("Failed: " + err.message);
  }
}

// =====================================================================
// Phase 4: Full Orders Table & Audit Trail View
// =====================================================================
async function loadAdminAllOrders() {
  const tbody = document.getElementById("adminAllOrdersTableBody");
  const countSpan = document.getElementById("adminAllOrdersCount");
  const searchInput = document.getElementById("adminOrderSearch");
  const statusFilter = document.getElementById("adminOrderStatusFilter");
  if (!tbody) return;

  const q = searchInput?.value?.trim() || "";
  const st = statusFilter?.value?.trim() || "";

  let url = `/api/admin/orders?`;
  if (q) url += `search=${encodeURIComponent(q)}&`;
  if (st) url += `status=${encodeURIComponent(st)}&`;

  try {
    const res = await fetch(url, { credentials: "same-origin" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const orders = await res.json();

    if (countSpan) countSpan.textContent = `${orders.length} order${orders.length === 1 ? '' : 's'}`;

    if (!orders || orders.length === 0) {
      tbody.innerHTML = `<tr><td colspan="10" class="loading-row" style="color:var(--text-muted)">No orders matching search / filter criteria.</td></tr>`;
      return;
    }

    tbody.innerHTML = orders.map((o) => {
      const custInfo = `${escapeHtml(o.customer_name || 'Customer')} ${o.customer_phone ? `<br><small class="text-muted">${escapeHtml(o.customer_phone)}</small>` : ''}`;
      const schedTime = o.preferred_datetime ? escapeHtml(o.preferred_datetime) : `<span class="text-muted">—</span>`;
      let warningHtml = "";
      if (o.double_booking_warning && o.double_booking_warning.has_conflict) {
        warningHtml = `<div class="conflict-warning-pill">⚠️ Double-booking!</div>`;
      }

      let transcriptBtn = `<span class="text-muted">—</span>`;
      if (o.call_session_id) {
        transcriptBtn = `<button class="btn btn-sm btn-outline btn-view-transcript" data-session-id="${escapeHtml(o.call_session_id)}" title="View Call Transcript">📞 Transcript</button>`;
      }

      let completeBtn = "";
      if (o.status === "confirmed") {
        completeBtn = `<button class="btn btn-sm btn-success btn-complete-order" data-order-id="${o.id}">Mark Completed</button>`;
      }

      return `
        <tr>
          <td><strong class="mono">${escapeHtml(o.order_number)}</strong></td>
          <td>${custInfo}</td>
          <td><strong>${escapeHtml(o.item_name_snapshot || 'Item')}</strong></td>
          <td><span class="role-badge ${o.item_type === 'service' ? 'admin' : 'member'}">${escapeHtml(o.item_type || 'product')}</span></td>
          <td>${o.quantity}</td>
          <td><strong>PKR ${(o.total_pkr || 0).toLocaleString()}</strong></td>
          <td>${schedTime} ${warningHtml}</td>
          <td><span class="order-status-badge status-${escapeHtml(o.status)}">${escapeHtml(o.status.replace(/_/g, ' '))}</span></td>
          <td>${transcriptBtn}</td>
          <td>
            <div style="display:flex; gap:0.3rem; flex-wrap:wrap;">
              ${completeBtn}
              <button class="btn btn-sm btn-outline btn-order-audit" data-order-id="${o.id}" data-order-num="${escapeHtml(o.order_number)}">Audit History</button>
            </div>
          </td>
        </tr>
      `;
    }).join("");

    tbody.querySelectorAll(".btn-complete-order").forEach((b) => {
      b.addEventListener("click", () => handleCompleteOrder(b.dataset.orderId));
    });
    tbody.querySelectorAll(".btn-order-audit").forEach((b) => {
      b.addEventListener("click", () => openOrderAuditModal(b.dataset.orderId, b.dataset.orderNum));
    });
    tbody.querySelectorAll(".btn-view-transcript").forEach((b) => {
      b.addEventListener("click", () => {
        if (typeof openCallTranscriptModal === "function") {
          openCallTranscriptModal(b.dataset.sessionId);
        } else if (typeof navigateTo === "function") {
          navigateTo("logs");
        }
      });
    });

  } catch (err) {
    console.error("Failed to load all orders:", err);
    tbody.innerHTML = `<tr><td colspan="10" class="loading-row" style="color:var(--red)">Failed to load orders: ${err.message}</td></tr>`;
  }
}

async function handleCompleteOrder(id) {
  try {
    const res = await fetch(`/api/admin/orders/${id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "complete", admin_note: "Order fulfilled and completed by admin" }),
      credentials: "same-origin",
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showAdminToast("Order marked completed and locked.");
    loadAdminAllOrders();
  } catch (err) {
    alert("Failed: " + err.message);
  }
}

async function openOrderAuditModal(orderId, orderNum) {
  const modal = document.getElementById("adminOrderAuditModal");
  const title = document.getElementById("adminOrderAuditTitle");
  const timeline = document.getElementById("adminOrderAuditTimeline");
  if (!modal || !timeline) return;

  if (title) title.textContent = `📜 Order #${orderNum} Audit Trail`;
  timeline.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem;">Loading audit trail…</div>`;
  modal.classList.remove("hidden");

  try {
    const res = await fetch(`/api/admin/orders/${orderId}/events`, { credentials: "same-origin" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const events = await res.json();

    if (!events || events.length === 0) {
      timeline.innerHTML = `<div style="color:var(--text-muted); font-size:0.85rem;">No recorded audit events.</div>`;
      return;
    }

    timeline.innerHTML = events.map((e) => {
      const actor = e.actor_email ? `${escapeHtml(e.actor_name || e.actor_email)} (${e.actor_role})` : `System / Caller (${e.actor_role || 'guest'})`;
      const statusChange = e.old_status ? `${escapeHtml(e.old_status)} ➔ <strong>${escapeHtml(e.new_status)}</strong>` : `Created with status <strong>${escapeHtml(e.new_status)}</strong>`;
      const noteHtml = e.note ? `<div style="font-style:italic; margin-top:2px; color:var(--text);">${escapeHtml(e.note)}</div>` : "";

      return `
        <div class="audit-event-item">
          <div style="display:flex; justify-content:space-between; align-items:center; font-size:0.8rem; color:var(--text-muted);">
            <span><strong>${escapeHtml(e.event_type)}</strong> by ${actor}</span>
            <span>${escapeHtml(e.created_at)}</span>
          </div>
          <div>${statusChange}</div>
          ${noteHtml}
        </div>
      `;
    }).join("");

  } catch (err) {
    timeline.innerHTML = `<div style="color:var(--red); font-size:0.85rem;">Failed to load audit events: ${err.message}</div>`;
  }
}

// =====================================================================
// Phase 4: Business Profile & Settings Management
// =====================================================================
async function loadBusinessSettingsForm() {
  try {
    const res = await fetch("/api/business-settings", { credentials: "same-origin" });
    if (!res.ok) return;
    const settings = await res.json();

    const setVal = (id, val) => {
      const el = document.getElementById(id);
      if (el && val !== undefined && val !== null) el.value = val;
    };

    setVal("setBizName", settings.business_name);
    setVal("setBizType", settings.business_type);
    setVal("setBizWorkingHours", settings.working_hours);
    setVal("setMaxOrderQty", settings.max_order_quantity);
    setVal("setBizGreeting", settings.greeting);
    setVal("setBizEscalationMsg", settings.escalation_message);
    setVal("setBizHoursUrdu", settings.business_hours_ur);
    setVal("setBizPhone", settings.support_phone);
    setVal("setBizEmail", settings.support_email);
    setVal("setBizAddress", settings.office_address);
  } catch (err) {
    console.error("Failed to load business settings:", err);
  }
}

async function handleSaveBusinessSettings() {
  const getVal = (id) => document.getElementById(id)?.value?.trim() || "";
  const feedback = document.getElementById("bizSettingsFeedback");

  const payload = {
    business_name: getVal("setBizName"),
    business_type: getVal("setBizType"),
    working_hours: getVal("setBizWorkingHours"),
    max_order_quantity: parseInt(getVal("setMaxOrderQty"), 10) || 10,
    greeting: getVal("setBizGreeting"),
    escalation_message: getVal("setBizEscalationMsg"),
    business_hours_ur: getVal("setBizHoursUrdu"),
    support_phone: getVal("setBizPhone"),
    support_email: getVal("setBizEmail"),
    office_address: getVal("setBizAddress"),
  };

  try {
    const res = await fetch("/api/business-settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      credentials: "same-origin",
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const updated = await res.json().catch(() => payload);
    if (typeof window.applyGlobalBranding === "function") {
      window.applyGlobalBranding(updated);
    }
    if (feedback) {
      feedback.textContent = "✅ Business settings saved. Global branding and voice prompts updated dynamically.";
      feedback.style.color = "var(--emerald)";
      feedback.classList.remove("hidden");
      setTimeout(() => feedback.classList.add("hidden"), 4000);
    }
    showAdminToast("Business settings and branding updated globally.");
  } catch (err) {
    if (feedback) {
      feedback.textContent = `❌ Error saving settings: ${err.message}`;
      feedback.style.color = "var(--red)";
      feedback.classList.remove("hidden");
    }
  }
}

// =====================================================================
// Auto-Polling (10s) with Instant Toast Alerts
// =====================================================================
let adminPollingInterval = null;
let lastPendingCount = null;
let lastOpenHandoffCount = null;

function startAdminAutoPolling() {
  if (adminPollingInterval) return;

  adminPollingInterval = setInterval(async () => {
    if (typeof authState === "undefined" || !authState.isAdmin) return;

    try {
      const [orderSumRes, handoffSumRes] = await Promise.all([
        fetch("/api/admin/orders/summary", { credentials: "same-origin" }),
        fetch("/api/admin/handoffs/summary", { credentials: "same-origin" }),
      ]);

      if (orderSumRes.ok) {
        const oSum = await orderSumRes.json();
        const pending = oSum.pending_confirmation_count ?? 0;
        if (lastPendingCount !== null && pending > lastPendingCount) {
          showAdminToast(`🔔 New order request pending confirmation! (${pending} pending)`, "pending");
          loadAdminPendingOrders();
          loadAdminAllOrders();
        }
        lastPendingCount = pending;
      }

      if (handoffSumRes.ok) {
        const hSum = await handoffSumRes.json();
        const openH = hSum.open_count ?? 0;
        if (lastOpenHandoffCount !== null && openH > lastOpenHandoffCount) {
          showAdminToast(`🚨 New human handoff escalation request received!`, "urgent");
          loadAdminHandoffs();
        }
        lastOpenHandoffCount = openH;
      }
    } catch (e) {
      // Background poll failure silently ignored
    }
  }, 10000);
}

function showAdminToast(msg, type = "normal") {
  const container = document.getElementById("toastNotificationContainer");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `notification-toast ${type === "urgent" ? "urgent" : ""}`;
  toast.innerHTML = `<span>${msg}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transition = "opacity 0.5s ease";
    setTimeout(() => toast.remove(), 500);
  }, 4500);
}
