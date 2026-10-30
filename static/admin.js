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
      const isPrimary = Boolean(u.is_primary) || u.id === 1 || (u.email && u.email.toLowerCase() === "admin@digibuzz.com");
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
});
