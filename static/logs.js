"use strict";

// =====================================================================
// AI Calling Agent — Call Logs (Role-Based Access Guarded)
// =====================================================================

const logsState = {
  calls: [],
};

// =====================================================================
// Load & Render with Strict Guest Route Guard
// =====================================================================
async function loadCallLogs() {
  const tbody = document.getElementById("logsTableBody");

  // 1. ROUTE GUARD: Ensure unauthenticated guests have strictly NO access to view call logs
  if (!window.authState || !window.authState.user) {
    if (tbody) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" style="text-align:center;padding:3.5rem 1rem;">
            <div class="auth-gate-card" role="alert" style="max-width:460px;margin:0 auto;background:var(--surface-card);border:2px dashed var(--border-strong);border-radius:var(--radius-lg);padding:2.5rem 1.5rem;box-shadow:var(--shadow);">
              <div style="font-size:3rem;margin-bottom:0.75rem;" aria-hidden="true">🔒</div>
              <h3 style="font-size:1.3rem;margin-bottom:0.5rem;color:var(--text);font-weight:700;">Call Logs Protected</h3>
              <p style="color:var(--text-muted);font-size:0.92rem;line-height:1.5;margin-bottom:1.5rem;">
                Unauthenticated guest users have strictly no access to view, edit, or delete call logs. Please sign in or create an account to view your conversation history and call metrics.
              </p>
              <div style="display:flex;gap:0.75rem;justify-content:center;flex-wrap:wrap;">
                <button class="btn btn-brand" onclick="if(typeof openAuthModal==='function')openAuthModal('login');">🔐 Sign In</button>
                <button class="btn btn-outline" onclick="if(typeof openAuthModal==='function')openAuthModal('register');">✨ Create Account</button>
              </div>
            </div>
          </td>
        </tr>`;
    }
    renderLogsStats([]);
    return;
  }

  if (tbody) {
    tbody.innerHTML = '<tr><td colspan="7" class="loading-row">Loading call logs…</td></tr>';
  }

  try {
    let serverCalls = [];
    const res = await fetch("/api/calls?limit=50", { credentials: "same-origin" });
    if (res.status === 401) {
      // Session expired or unauthenticated
      if (tbody) {
        tbody.innerHTML = `
          <tr>
            <td colspan="7" style="text-align:center;padding:3rem 1rem;">
              <div class="auth-gate-card" style="max-width:440px;margin:0 auto;">
                <div style="font-size:2.5rem;margin-bottom:0.5rem;">🔒</div>
                <h3>Authentication Required</h3>
                <p style="color:var(--text-muted);margin-bottom:1rem;">Your session has expired. Please sign in to view call logs.</p>
                <button class="btn btn-brand" onclick="if(typeof openAuthModal==='function')openAuthModal('login');">Sign In</button>
              </div>
            </td>
          </tr>`;
      }
      renderLogsStats([]);
      return;
    }

    if (res.ok) {
      serverCalls = await res.json();
    }

    // Load client localStorage calls only for authenticated user
    let clientCalls = {};
    try {
      const raw = localStorage.getItem("calling_agent_logs");
      if (raw) clientCalls = JSON.parse(raw);
    } catch (e) {}

    // Merge server calls and client calls by session_id
    const mergedMap = new Map();

    // 1. Add server calls first
    for (const c of serverCalls) {
      mergedMap.set(c.session_id, {
        id: c.id,
        session_id: c.session_id,
        started_at: c.started_at,
        last_activity: c.last_activity,
        status: c.status,
        outcome: c.outcome,
        total_messages: c.total_messages ?? 0,
        caller_name: c.caller_name || null,
        caller_email: c.caller_email || null,
        user_id: c.user_id || null,
      });
    }

    // 2. Overlay client calls
    for (const [sid, c] of Object.entries(clientCalls)) {
      if (mergedMap.has(sid)) {
        const existing = mergedMap.get(sid);
        existing.total_messages = Math.max(existing.total_messages || 0, c.total_messages || (c.messages ? c.messages.length : 0));
        existing.last_activity = c.last_activity || existing.last_activity;
        existing.outcome = c.outcome || existing.outcome;
      } else {
        mergedMap.set(sid, {
          id: c.id || null,
          session_id: sid,
          started_at: c.started_at || "—",
          last_activity: c.last_activity || "—",
          status: c.status || "active",
          outcome: c.outcome || "Resolved",
          total_messages: c.total_messages || (c.messages ? c.messages.length : 0),
        });
      }
    }

    const mergedList = Array.from(mergedMap.values());
    mergedList.sort((a, b) => (b.last_activity || "").localeCompare(a.last_activity || ""));

    logsState.calls = mergedList;
    renderLogsTable(logsState.calls);
    renderLogsStats(logsState.calls);
  } catch (err) {
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="7" class="loading-row" style="color:var(--red)">Failed to load logs: ${escLog(err.message)}</td></tr>`;
    }
  }
}

function renderLogsTable(calls) {
  const tbody = document.getElementById("logsTableBody");
  if (!tbody) return;

  if (!calls || calls.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="loading-row">No call sessions recorded yet.</td></tr>';
    return;
  }

  tbody.innerHTML = calls
    .map(
      (c) => `
      <tr>
        <td>
          <span class="mono" style="font-size:.82rem;">${escLog(c.session_id)}</span>
          ${c.caller_name ? `<div style="font-size:.74rem;color:var(--brand);font-weight:600;">👤 ${escLog(c.caller_name)}</div>` : ""}
        </td>
        <td>${escLog(c.started_at || "—")}</td>
        <td>${escLog(c.last_activity || "—")}</td>
        <td>${statusBadgeLog(c.status)}</td>
        <td>${outcomeBadge(c.outcome)}</td>
        <td><strong>${c.total_messages ?? 0}</strong></td>
        <td>
          <div style="display:flex;gap:0.4rem;align-items:center;">
            <button class="btn btn-sm btn-edit-sm" onclick="viewSession('${escLog(c.session_id)}')" aria-label="View transcript for session ${escLog(c.session_id)}">📄 View</button>
            ${c.id ? `<button class="btn btn-sm btn-outline-danger" onclick="deleteSessionLog(${c.id}, '${escLog(c.session_id)}')" aria-label="Delete call log ${c.id}" title="Delete Call Log">🗑️</button>` : ""}
          </div>
        </td>
      </tr>`
    )
    .join("");
}

function renderLogsStats(calls) {
  const total = calls ? calls.length : 0;
  const resolved = calls ? calls.filter((c) => c.outcome === "Resolved").length : 0;
  const escalated = calls ? calls.filter((c) => c.outcome === "Escalated").length : 0;
  const totalMessages = calls ? calls.reduce((sum, c) => sum + (c.total_messages || 0), 0) : 0;

  const statTotal = document.getElementById("statTotal");
  const statResolved = document.getElementById("statResolved");
  const statEscalated = document.getElementById("statEscalated");
  const statMessages = document.getElementById("statMessages");

  if (statTotal) statTotal.textContent = total;
  if (statResolved) statResolved.textContent = resolved;
  if (statEscalated) statEscalated.textContent = escalated;
  if (statMessages) statMessages.textContent = totalMessages;
}

function statusBadgeLog(status) {
  if (status === "active") return '<span class="badge active" role="status">Active</span>';
  return `<span class="badge" style="background:var(--surface-card);color:var(--text-muted);">${escLog(status || "—")}</span>`;
}

function outcomeBadge(outcome) {
  if (outcome === "Resolved") return '<span class="badge resolved" role="status">Resolved</span>';
  if (outcome === "Escalated") return '<span class="badge escalated" role="status">Escalated</span>';
  return `<span class="badge" role="status">${escLog(outcome || "—")}</span>`;
}

// =====================================================================
// View Session Transcript
// =====================================================================
async function viewSession(sessionId) {
  if (!window.authState || !window.authState.user) {
    alert("Authentication required. Please sign in to view transcripts.");
    return;
  }

  const modal = document.getElementById("transcriptModal");
  const title = document.getElementById("transcriptModalTitle");
  const body = document.getElementById("transcriptModalBody");

  if (!modal || !body) return;

  if (title) title.textContent = `Call Transcript — ${sessionId}`;
  body.innerHTML = '<div class="loading-row">Loading transcript…</div>';
  modal.classList.remove("hidden");

  let messages = [];
  try {
    const res = await fetch(`/api/history?session_id=${encodeURIComponent(sessionId)}`, { credentials: "same-origin" });
    if (res.ok) {
      messages = await res.json();
    }
  } catch (err) {
    console.warn("Server history fetch failed:", err);
  }

  // Fallback to client storage
  if (!messages || messages.length === 0) {
    try {
      const raw = localStorage.getItem("calling_agent_logs");
      if (raw) {
        const clientCalls = JSON.parse(raw);
        if (clientCalls[sessionId] && clientCalls[sessionId].messages) {
          messages = clientCalls[sessionId].messages;
        }
      }
    } catch (e) {}
  }

  renderTranscript(body, messages);
}

// =====================================================================
// Delete Session Log
// =====================================================================
async function deleteSessionLog(callId, sessionId) {
  if (!callId) return;
  if (!confirm(`Are you sure you want to delete call log #${callId}? This action cannot be undone.`)) {
    return;
  }

  try {
    const res = await fetch(`/api/calls/${callId}`, {
      method: "DELETE",
      credentials: "same-origin",
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    // Clean up local cache
    try {
      const raw = localStorage.getItem("calling_agent_logs");
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed[sessionId]) {
          delete parsed[sessionId];
          localStorage.setItem("calling_agent_logs", JSON.stringify(parsed));
        }
      }
    } catch (_) {}

    await loadCallLogs();
  } catch (err) {
    alert(`Failed to delete call log: ${err.message}`);
  }
}

function renderTranscript(container, messages) {
  if (!container) return;
  if (!messages || messages.length === 0) {
    container.innerHTML = '<div class="loading-row">No messages recorded in this session.</div>';
    return;
  }

  container.innerHTML = messages
    .map(
      (m) => `
      <div class="transcript-turn">
        <div class="transcript-meta">
          <span>${escLog(m.timestamp || "")}</span>
          ${m.detected_intent ? `<span class="badge" style="background:var(--brand-light);color:var(--brand);font-size:.7rem;">${escLog(m.detected_intent)}</span>` : ""}
          ${outcomeBadge(m.outcome)}
        </div>
        ${m.user_transcript ? `<div class="transcript-user">👤 <strong>User:</strong> ${escLog(m.user_transcript)}</div>` : ""}
        ${m.ai_response ? `<div class="transcript-bot">🤖 <strong>AI:</strong> ${escLog(m.ai_response)}</div>` : ""}
      </div>`
    )
    .join("");
}

// =====================================================================
// Escape helper
// =====================================================================
function escLog(str) {
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
  const refreshBtn = document.getElementById("btnRefreshLogs");
  if (refreshBtn) refreshBtn.addEventListener("click", loadCallLogs);

  // Transcript modal close buttons
  const modalClose = document.getElementById("transcriptModalClose");
  if (modalClose) {
    modalClose.addEventListener("click", () => {
      const modal = document.getElementById("transcriptModal");
      if (modal) modal.classList.add("hidden");
    });
  }
  const modalDone = document.getElementById("transcriptModalDone");
  if (modalDone) {
    modalDone.addEventListener("click", () => {
      const modal = document.getElementById("transcriptModal");
      if (modal) modal.classList.add("hidden");
    });
  }
  const transcriptModal = document.getElementById("transcriptModal");
  if (transcriptModal) {
    transcriptModal.addEventListener("click", (e) => {
      if (e.target === transcriptModal) {
        transcriptModal.classList.add("hidden");
      }
    });
  }

  // Auto-load if logs page is already active
  const pageLogs = document.getElementById("page-logs");
  if (pageLogs && pageLogs.classList.contains("active")) {
    loadCallLogs();
  }
});
