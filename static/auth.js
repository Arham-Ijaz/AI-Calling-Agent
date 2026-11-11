"use strict";

// =====================================================================
// AI Calling Agent — Authentication & RBAC Module
// =====================================================================

const authState = {
  user: null,
  isLoggedIn: false,
  isAdmin: false,
};

// =====================================================================
// Session Check on Application Start
// =====================================================================
async function checkAuthStatus() {
  try {
    const res = await fetch("/api/auth/me", { credentials: "same-origin" });
    if (res.ok) {
      const data = await res.json();
      authState.user = data.user;
      authState.isLoggedIn = true;
      authState.isAdmin = data.user.role === "admin";
    } else {
      authState.user = null;
      authState.isLoggedIn = false;
      authState.isAdmin = false;
    }
  } catch (err) {
    console.warn("Auth check failed:", err);
    authState.user = null;
    authState.isLoggedIn = false;
    authState.isAdmin = false;
  }
  updateAuthUI();
}

// =====================================================================
// Update UI based on User Role & Auth State
// =====================================================================
function updateAuthUI() {
  const guestBlock = document.getElementById("authNavGuest");
  const userBlock = document.getElementById("authNavUser");
  const adminNav = document.getElementById("navAdminLink");
  const mobileAdminNav = document.getElementById("mobileNavAdminLink");
  const userNameEl = document.getElementById("authUserName");
  const userRoleEl = document.getElementById("authUserRole");
  const userAvatarEl = document.getElementById("authUserAvatar");

  // Mobile elements
  const mobileGuest = document.getElementById("mobileAuthGuest");
  const mobileUser = document.getElementById("mobileAuthUser");
  const mobileName = document.getElementById("mobileAuthName");
  const mobileRole = document.getElementById("mobileAuthRole");

  if (authState.isLoggedIn && authState.user) {
    if (guestBlock) guestBlock.classList.add("hidden");
    if (userBlock) userBlock.classList.remove("hidden");
    if (mobileGuest) mobileGuest.classList.add("hidden");
    if (mobileUser) mobileUser.classList.remove("hidden");

    const initials = (authState.user.full_name || "User")
      .split(" ")
      .map((n) => n[0])
      .join("")
      .toUpperCase()
      .slice(0, 2);

    if (userNameEl) userNameEl.textContent = authState.user.full_name;
    if (mobileName) mobileName.textContent = authState.user.full_name;
    if (userAvatarEl) userAvatarEl.textContent = initials;

    if (userRoleEl) {
      userRoleEl.textContent = authState.isAdmin ? "Admin" : "Member";
      userRoleEl.className = authState.isAdmin ? "role-badge admin" : "role-badge member";
    }
    if (mobileRole) {
      mobileRole.textContent = authState.isAdmin ? "Admin" : "Member";
      mobileRole.className = authState.isAdmin ? "role-badge admin" : "role-badge member";
    }

    // Admin Navigation Tab
    if (adminNav) adminNav.style.display = authState.isAdmin ? "inline-flex" : "none";
    if (mobileAdminNav) mobileAdminNav.style.display = authState.isAdmin ? "block" : "none";
  } else {
    if (guestBlock) guestBlock.classList.remove("hidden");
    if (userBlock) userBlock.classList.add("hidden");
    if (mobileGuest) mobileGuest.classList.remove("hidden");
    if (mobileUser) mobileUser.classList.add("hidden");

    if (adminNav) adminNav.style.display = "none";
    if (mobileAdminNav) mobileAdminNav.style.display = "none";

    // If on admin page and not admin, redirect to home
    const pageAdmin = document.getElementById("page-admin");
    if (pageAdmin && pageAdmin.classList.contains("active")) {
      if (typeof navigateTo === "function") navigateTo("home");
    }
  }

  // FAQs page visibility: ONLY Admin can see / access FAQs page (regular users only ask questions)
  const navFaqs = document.getElementById("navFaqsLink");
  const sidebarFaqs = document.getElementById("sidebarManageFaqs");
  const isAdm = Boolean(authState.isLoggedIn && authState.isAdmin);
  if (navFaqs) navFaqs.style.display = isAdm ? "inline-flex" : "none";
  if (sidebarFaqs) sidebarFaqs.style.display = isAdm ? "block" : "none";

  // Orders navigation tab customization: for Admin, show "Orders" + live pending/in-process count badge
  updateAdminOrdersNavBadge();

  const pageFaq = document.getElementById("page-faqs");
  if (pageFaq && pageFaq.classList.contains("active") && !isAdm) {
    if (typeof navigateTo === "function") navigateTo("home");
  }

  // Refresh current view permissions
  if (isAdm && typeof loadFAQs === "function") {
    if (pageFaq && pageFaq.classList.contains("active")) loadFAQs();
  }
  if (typeof loadProducts === "function") {
    const pageProd = document.getElementById("page-products");
    if (pageProd && pageProd.classList.contains("active")) loadProducts();
  }
  if (typeof loadCallLogs === "function") {
    const pageLogs = document.getElementById("page-logs");
    if (pageLogs && pageLogs.classList.contains("active")) loadCallLogs();
  }

  // Update Settings Page View for active role
  updateSettingsView();
}

async function updateAdminOrdersNavBadge() {
  const navOrdersLink = document.getElementById("navOrdersLink");
  if (!navOrdersLink) return;

  if (!authState || !authState.isLoggedIn) {
    navOrdersLink.innerHTML = "🛍️ My Orders";
    navOrdersLink.setAttribute("data-page", "orders");
    return;
  }

  if (!authState.isAdmin) {
    navOrdersLink.innerHTML = "🛍️ My Orders";
    navOrdersLink.setAttribute("data-page", "orders");
    return;
  }

  // Admin view: change label to "Orders" and show live status badge (pending / in process)
  navOrdersLink.setAttribute("data-page", "admin-orders");
  try {
    const res = await fetch("/api/admin/orders/summary", { credentials: "same-origin" });
    if (!res.ok) {
      navOrdersLink.innerHTML = "📦 Orders";
      return;
    }
    const data = await res.json();
    const pending = data.pending_confirmation_count || 0;
    const cancelReq = data.cancel_requested_count || 0;
    const confirmed = data.confirmed_count || 0;

    let badge = "";
    if (pending > 0) {
      badge = ` <span style="background:#eab308;color:#000;font-size:0.75rem;padding:2px 7px;border-radius:12px;font-weight:700;margin-left:4px;">${pending} Pending</span>`;
    } else if (cancelReq > 0) {
      badge = ` <span style="background:#ef4444;color:#fff;font-size:0.75rem;padding:2px 7px;border-radius:12px;font-weight:700;margin-left:4px;">${cancelReq} Cancel</span>`;
    } else if (confirmed > 0) {
      badge = ` <span style="background:#3b82f6;color:#fff;font-size:0.75rem;padding:2px 7px;border-radius:12px;font-weight:700;margin-left:4px;">${confirmed} In Process</span>`;
    }

    navOrdersLink.innerHTML = `📦 Orders${badge}`;
  } catch (_) {
    navOrdersLink.innerHTML = "📦 Orders";
  }
}

function updateSettingsView() {
  const user = authState.user;
  const isLogged = authState.isLoggedIn;
  const isAdmin = authState.isAdmin;

  const roleBadge = document.getElementById("settingsUserRoleBadge");
  const nameEl = document.getElementById("settingsUserName");
  const emailEl = document.getElementById("settingsUserEmail");
  const phoneEl = document.getElementById("settingsUserPhone");
  const adminSec = document.getElementById("settingsAdminSection");
  const keyBadge = document.getElementById("keyStatusBadge");

  if (isLogged && user) {
    if (roleBadge) {
      roleBadge.textContent = isAdmin ? "Administrator" : "Member";
      roleBadge.className = `role-badge ${isAdmin ? "admin" : "member"}`;
    }
    if (nameEl) nameEl.textContent = user.full_name || "—";
    if (emailEl) emailEl.textContent = user.email || "—";
    if (phoneEl) phoneEl.textContent = user.phone || "—";
  } else {
    if (roleBadge) {
      roleBadge.textContent = "Guest";
      roleBadge.className = "role-badge member";
    }
    if (nameEl) nameEl.textContent = "Guest Caller";
    if (emailEl) emailEl.textContent = "Not signed in";
    if (phoneEl) phoneEl.textContent = "—";
  }

  // Admin Diagnostics Section
  if (adminSec) {
    adminSec.style.display = isAdmin ? "block" : "none";
  }
  if (keyBadge) {
    keyBadge.style.display = isAdmin ? "inline-flex" : "none";
  }
  if (isAdmin && typeof refreshKeyStatus === "function") {
    refreshKeyStatus();
  }

  // Self Change Password Card (visible for any logged-in user or admin)
  const passCard = document.getElementById("settingsChangePasswordCard");
  if (passCard) {
    passCard.style.display = isLogged ? "block" : "none";
  }
}

async function handleSettingsPasswordChange() {
  const btn = document.getElementById("btnSettingsChangePass");
  const errEl = document.getElementById("settingsPassError");
  const succEl = document.getElementById("settingsPassSuccess");
  const currentPass = document.getElementById("settingsCurrentPass").value;
  const newPass = document.getElementById("settingsNewPass").value;
  const confirmPass = document.getElementById("settingsConfirmPass").value;

  if (errEl) { errEl.textContent = ""; errEl.classList.add("hidden"); }
  if (succEl) { succEl.textContent = ""; succEl.classList.add("hidden"); }

  if (!currentPass || !newPass || !confirmPass) {
    if (errEl) { errEl.textContent = "All password fields are required."; errEl.classList.remove("hidden"); }
    return;
  }

  if (newPass !== confirmPass) {
    if (errEl) { errEl.textContent = "New passwords do not match."; errEl.classList.remove("hidden"); }
    return;
  }

  if (newPass.length < 8 || !/[A-Za-z]/.test(newPass) || !/[0-9]/.test(newPass)) {
    if (errEl) {
      errEl.textContent = "New password must be at least 8 characters and contain both letters and numbers.";
      errEl.classList.remove("hidden");
    }
    return;
  }

  btn.disabled = true;
  btn.textContent = "Updating Password…";

  try {
    const res = await fetch("/api/auth/password", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: currentPass, new_password: newPass }),
      credentials: "same-origin",
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to update password.");

    document.getElementById("settingsCurrentPass").value = "";
    document.getElementById("settingsNewPass").value = "";
    document.getElementById("settingsConfirmPass").value = "";

    if (succEl) {
      succEl.textContent = "✅ " + data.message;
      succEl.classList.remove("hidden");
    }
  } catch (err) {
    if (errEl) {
      errEl.textContent = err.message;
      errEl.classList.remove("hidden");
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Update Password";
  }
}

// =====================================================================
// Auth Modal & Form Controls
// =====================================================================
function openAuthModal(defaultTab = "login") {
  const navLinks = document.getElementById("navLinks");
  if (navLinks) navLinks.classList.remove("open");
  const modal = document.getElementById("authModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  switchAuthTab(defaultTab);
  clearAuthErrors();
}

function closeAuthModal() {
  const modal = document.getElementById("authModal");
  if (modal) modal.classList.add("hidden");
  clearAuthErrors();
}

function switchAuthTab(tab) {
  const tabLogin = document.getElementById("authTabLogin");
  const tabRegister = document.getElementById("authTabRegister");
  const formLogin = document.getElementById("authFormLogin");
  const formRegister = document.getElementById("authFormRegister");
  const modalTitle = document.getElementById("authModalTitle");

  clearAuthErrors();

  if (tab === "register") {
    if (tabLogin) tabLogin.classList.remove("active");
    if (tabRegister) tabRegister.classList.add("active");
    if (formLogin) formLogin.classList.add("hidden");
    if (formRegister) formRegister.classList.remove("hidden");
    if (modalTitle) modalTitle.textContent = "Create Account";
  } else {
    if (tabLogin) tabLogin.classList.add("active");
    if (tabRegister) tabRegister.classList.remove("active");
    if (formLogin) formLogin.classList.remove("hidden");
    if (formRegister) formRegister.classList.add("hidden");
    if (modalTitle) modalTitle.textContent = "Sign In";
  }
}

function showAuthError(msg) {
  const errBox = document.getElementById("authErrorBox");
  if (errBox) {
    errBox.textContent = msg;
    errBox.classList.remove("hidden");
  }
}

function clearAuthErrors() {
  const errBox = document.getElementById("authErrorBox");
  if (errBox) {
    errBox.textContent = "";
    errBox.classList.add("hidden");
  }
}

// =====================================================================
// Login & Register Handlers
// =====================================================================
async function handleLogin(e) {
  e.preventDefault();
  clearAuthErrors();

  const ident = document.getElementById("loginIdentifier").value.trim();
  const pass = document.getElementById("loginPassword").value;
  const btn = document.getElementById("btnLoginSubmit");

  if (!ident || !pass) {
    showAuthError("Please provide your email/phone and password.");
    return;
  }

  btn.disabled = true;
  btn.textContent = "Signing in…";

  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identifier: ident, password: pass }),
      credentials: "same-origin",
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Authentication failed.");
    }

    authState.user = data.user;
    authState.isLoggedIn = true;
    authState.isAdmin = data.user.role === "admin";

    // Clean purge of any leftover client logs and unlock guest quota limits
    try { 
      localStorage.removeItem("calling_agent_logs"); 
      localStorage.removeItem("digibuzz_call_logs"); 
      localStorage.removeItem("guest_turn_count");
    } catch (_) {}
    if (window.state) window.state.guestLimitReached = false;
    const guestBanner = document.getElementById("authGuestLimitBanner");
    if (guestBanner) guestBanner.classList.add("hidden");
    if (typeof startNewSession === "function") startNewSession();
    if (typeof clearChat === "function") clearChat();
    if (window.logsState) logsState.calls = [];

    updateAuthUI();
    closeAuthModal();

    // Reset form
    document.getElementById("authFormLogin").reset();
  } catch (err) {
    showAuthError(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Sign In";
  }
}

async function handleRegister(e) {
  e.preventDefault();
  clearAuthErrors();

  const fullName = document.getElementById("regFullName").value.trim();
  const email = document.getElementById("regEmail").value.trim();
  const phone = document.getElementById("regPhone").value.trim();
  const pass = document.getElementById("regPassword").value;
  const passConfirm = document.getElementById("regPasswordConfirm").value;
  const btn = document.getElementById("btnRegisterSubmit");

  if (!fullName || !email || !phone || !pass) {
    showAuthError("All fields are required.");
    return;
  }

  if (pass !== passConfirm) {
    showAuthError("Passwords do not match.");
    return;
  }

  if (pass.length < 8 || !/[A-Za-z]/.test(pass) || !/[0-9]/.test(pass)) {
    showAuthError("Password must be at least 8 characters with letters and numbers.");
    return;
  }

  btn.disabled = true;
  btn.textContent = "Creating account…";

  try {
    const res = await fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ full_name: fullName, email, phone, password: pass }),
      credentials: "same-origin",
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Registration failed.");
    }

    authState.user = data.user;
    authState.isLoggedIn = true;
    authState.isAdmin = data.user.role === "admin";

    // Clean purge of any leftover client logs and unlock guest quota limits
    try { 
      localStorage.removeItem("calling_agent_logs"); 
      localStorage.removeItem("digibuzz_call_logs"); 
      localStorage.removeItem("guest_turn_count");
    } catch (_) {}
    if (window.state) window.state.guestLimitReached = false;
    const guestBanner = document.getElementById("authGuestLimitBanner");
    if (guestBanner) guestBanner.classList.add("hidden");
    if (typeof startNewSession === "function") startNewSession();
    if (typeof clearChat === "function") clearChat();
    if (window.logsState) logsState.calls = [];

    updateAuthUI();
    closeAuthModal();

    document.getElementById("authFormRegister").reset();
  } catch (err) {
    showAuthError(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Create Account";
  }
}

async function handleLogout() {
  const navLinks = document.getElementById("navLinks");
  if (navLinks) navLinks.classList.remove("open");
  try {
    await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  } catch (err) {
    console.warn("Logout error:", err);
  }
  authState.user = null;
  authState.isLoggedIn = false;
  authState.isAdmin = false;

  // Complete session wipe so no user data bleeds across accounts
  try { 
    localStorage.removeItem("calling_agent_logs"); 
    localStorage.removeItem("digibuzz_call_logs"); 
  } catch (_) {}
  if (typeof endCall === "function") endCall();
  if (typeof startNewSession === "function") startNewSession();
  if (typeof clearChat === "function") clearChat();
  if (window.logsState) logsState.calls = [];
  if (window.adminState) adminState.users = [];

  updateAuthUI();
  if (typeof navigateTo === "function") navigateTo("home");
}

// =====================================================================
// Setup Event Listeners
// =====================================================================
document.addEventListener("DOMContentLoaded", () => {
  // Check auth immediately
  checkAuthStatus();

  // Open modal buttons
  const btnOpenLogin = document.getElementById("btnOpenLogin");
  if (btnOpenLogin) btnOpenLogin.addEventListener("click", () => openAuthModal("login"));

  const mobileBtnLogin = document.getElementById("mobileBtnLogin");
  if (mobileBtnLogin) mobileBtnLogin.addEventListener("click", () => openAuthModal("login"));

  // Logout buttons
  const btnLogout = document.getElementById("btnLogout");
  if (btnLogout) btnLogout.addEventListener("click", handleLogout);

  const mobileBtnLogout = document.getElementById("mobileBtnLogout");
  if (mobileBtnLogout) mobileBtnLogout.addEventListener("click", handleLogout);

  // Modal close buttons
  const modalClose = document.getElementById("authModalClose");
  if (modalClose) modalClose.addEventListener("click", closeAuthModal);

  const modal = document.getElementById("authModal");
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeAuthModal();
    });
  }

  // Tabs
  const tabLogin = document.getElementById("authTabLogin");
  if (tabLogin) tabLogin.addEventListener("click", () => switchAuthTab("login"));

  const tabRegister = document.getElementById("authTabRegister");
  if (tabRegister) tabRegister.addEventListener("click", () => switchAuthTab("register"));

  // Form submits
  const formLogin = document.getElementById("authFormLogin");
  if (formLogin) formLogin.addEventListener("submit", handleLogin);

  const formRegister = document.getElementById("authFormRegister");
  if (formRegister) formRegister.addEventListener("submit", handleRegister);

  // Settings change password
  const btnSettingsChangePass = document.getElementById("btnSettingsChangePass");
  if (btnSettingsChangePass) btnSettingsChangePass.addEventListener("click", handleSettingsPasswordChange);

  // Password visibility toggles
  document.querySelectorAll(".password-toggle-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = btn.dataset.target;
      const input = document.getElementById(targetId);
      if (input) {
        if (input.type === "password") {
          input.type = "text";
          btn.textContent = "👁️";
        } else {
          input.type = "password";
          btn.textContent = "🔒";
        }
      }
    });
  });
});
