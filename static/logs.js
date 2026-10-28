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
  tbody.innerHTML = '<tr><td colspan="7" class="loading-row">Loading call logs…</td></tr>';

  try {
    const res = await fetch("/api/calls?limit=50");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    logsState.calls = await res.json();
    renderLogsTable(logsState.calls);
    renderLogsStats(logsState.calls);
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" class="loading-row" style="color:var(--red)">Failed to load logs: ${err.message}</td></tr>`;
  }
}

function renderLogsTable(calls) {
  const tbody = document.getElementById("logsTableBody");

  if (calls.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="loading-row">No call sessions recorded yet.</td></tr>';
    return;
  }

  tbody.innerHTML = calls
    .map(
      (c) => `
      <tr>
        <td><span class="mono" style="font-size:.8rem;">${escLog(c.session_id)}</span></td>
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
  const total = calls.length;
  const resolved = calls.filter((c) => c.outcome === "Resolved").length;
  const escalated = calls.filter((c) => c.outcome === "Escalated").length;
  const totalMessages = calls.reduce((sum, c) => sum + (c.total_messages || 0), 0);

  document.getElementById("statTotal").textContent = total;
  document.getElementById("statResolved").textContent = resolved;
  document.getElementById("statEscalated").textContent = escalated;
  document.getElementById("statMessages").textContent = totalMessages;
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

  title.textContent = `Transcript — ${sessionId}`;
  body.innerHTML = '<div class="loading-row">Loading transcript…</div>';
  modal.classList.remove("hidden");

  try {
    const res = await fetch(`/api/history?session_id=${encodeURIComponent(sessionId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const messages = await res.json();
    renderTranscript(body, messages);
  } catch (err) {
    body.innerHTML = `<div class="loading-row" style="color:var(--red)">Failed to load transcript: ${err.message}</div>`;
  }
}

function renderTranscript(container, messages) {
  if (messages.length === 0) {
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
  document.getElementById("transcriptModalClose").addEventListener("click", () => {
    document.getElementById("transcriptModal").classList.add("hidden");
  });
  document.getElementById("transcriptModalDone").addEventListener("click", () => {
    document.getElementById("transcriptModal").classList.add("hidden");
  });
  document.getElementById("transcriptModal").addEventListener("click", (e) => {
    if (e.target === document.getElementById("transcriptModal")) {
      document.getElementById("transcriptModal").classList.add("hidden");
    }
  });

  // Auto-load if logs page is already active
  if (document.getElementById("page-logs").classList.contains("active")) {
    loadCallLogs();
  }
});
