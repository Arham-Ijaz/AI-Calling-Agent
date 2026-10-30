"use strict";

// =====================================================================
// DigiBuzz AI Calling Agent — Call Logs
// =====================================================================

const logsState = {
  calls: [],
};

// =====================================================================
// Load & Render
// =====================================================================
async function loadCallLogs() {
  const tbody = document.getElementById("logsTableBody");
  if (tbody) {
    tbody.innerHTML = '<tr><td colspan="7" class="loading-row">Loading call logs…</td></tr>';
  }

  try {
    let serverCalls = [];
    try {
      const res = await fetch("/api/calls?limit=50", { credentials: "same-origin" });
      if (res.ok) {
        serverCalls = await res.json();
      }
    } catch (e) {
      console.warn("Could not fetch server calls:", e);
    }

    // Load client localStorage calls
    let clientCalls = {};
    try {
      const raw = localStorage.getItem("digibuzz_call_logs");
      if (raw) clientCalls = JSON.parse(raw);
    } catch (e) {}

    // Merge server calls and client calls by session_id
    const mergedMap = new Map();

    // 1. Add server calls first
    for (const c of serverCalls) {
      mergedMap.set(c.session_id, {
        session_id: c.session_id,
        started_at: c.started_at,
        last_activity: c.last_activity,
        status: c.status,
        outcome: c.outcome,
        total_messages: c.total_messages ?? 0
      });
    }

    // 2. Overlay client calls (ensuring calls on serverless Vercel are never lost)
    for (const [sid, c] of Object.entries(clientCalls)) {
      if (mergedMap.has(sid)) {
        const existing = mergedMap.get(sid);
        existing.total_messages = Math.max(existing.total_messages || 0, c.total_messages || (c.messages ? c.messages.length : 0));
        existing.last_activity = c.last_activity || existing.last_activity;
        existing.outcome = c.outcome || existing.outcome;
      } else {
        mergedMap.set(sid, {
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
    // Sort by last_activity descending
    mergedList.sort((a, b) => (b.last_activity || "").localeCompare(a.last_activity || ""));

    logsState.calls = mergedList;
    renderLogsTable(logsState.calls);
    renderLogsStats(logsState.calls);
  } catch (err) {
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="7" class="loading-row" style="color:var(--red)">Failed to load logs: ${err.message}</td></tr>`;
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
          <span class="mono" style="font-size:.8rem;">${escLog(c.session_id)}</span>
          ${c.caller_name ? `<div style="font-size:.72rem;color:var(--brand);font-weight:600;">👤 ${escLog(c.caller_name)}</div>` : ""}
        </td>
        <td>${escLog(c.started_at || "—")}</td>
        <td>${escLog(c.last_activity || "—")}</td>
        <td>${statusBadgeLog(c.status)}</td>
        <td>${outcomeBadge(c.outcome)}</td>
        <td>${c.total_messages ?? 0}</td>
        <td>
          <button class="btn btn-sm btn-edit-sm" onclick="viewSession('${escLog(c.session_id)}')">📄 View</button>
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
  if (status === "active") return '<span class="badge active">Active</span>';
  return `<span class="badge" style="background:var(--surface-card);color:var(--text-muted);">${escLog(status || "—")}</span>`;
}

function outcomeBadge(outcome) {
  if (outcome === "Resolved") return '<span class="badge resolved">Resolved</span>';
  if (outcome === "Escalated") return '<span class="badge escalated">Escalated</span>';
  return `<span class="badge">${escLog(outcome || "—")}</span>`;
}

// =====================================================================
// View Session Transcript
// =====================================================================
async function viewSession(sessionId) {
  const modal = document.getElementById("transcriptModal");
  const title = document.getElementById("transcriptModalTitle");
  const body = document.getElementById("transcriptModalBody");

  if (!modal || !body) return;

  if (title) title.textContent = `Transcript — ${sessionId}`;
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

  // Fallback to client localStorage if server has no messages for this session
  if (!messages || messages.length === 0) {
    try {
      const raw = localStorage.getItem("digibuzz_call_logs");
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

function renderTranscript(container, messages) {
  if (!container) return;
  if (!messages || messages.length === 0) {
    container.innerHTML = '<div class="loading-row">No messages in this session.</div>';
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
// Escape helper (local scope)
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
