"use strict";

// =====================================================================
// AI Calling Agent — Main JS (Call Page + Navigation)
// =====================================================================

// ---- State ----
const state = {
  sessionId: null,
  isOnCall: false,
  isBusy: false,
  isVoiceEnabled: true, // Default to true so AI agent speaks aloud on call
  messageCount: 0,
  currentAudio: null,
  fillerPlaying: false,
  recognition: null,
  isListening: false,
  micPermissionRequested: false,
};

// ---- Filler phrases spoken immediately while Gemini processes ----
const FILLER_PHRASES = [
  "Let me check that for you.",
  "One moment please.",
  "Sure, let me look into that.",
  "Please hold on a moment.",
  "Got it, checking for you now.",
];

// ---- DOM Elements ----
const $ = (id) => document.getElementById(id);

// =====================================================================
// Navigation
// =====================================================================
function initNavigation() {
  document.querySelectorAll(".nav-link[data-page]").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      navigateTo(link.dataset.page);
    });
  });

  $("navHamburger").addEventListener("click", () => {
    $("navLinks").classList.toggle("open");
  });

  // Close mobile menu when a link is clicked
  $("navLinks").addEventListener("click", () => {
    $("navLinks").classList.remove("open");
  });

  // Key status badge -> open settings
  $("keyStatusBadge").addEventListener("click", () => navigateTo("settings"));
}

function navigateTo(page) {
  document.querySelectorAll(".page").forEach((el) => {
    el.classList.remove("active");
    el.classList.add("hidden");
  });
  document.querySelectorAll(".nav-link").forEach((l) => l.classList.remove("active"));

  const target = $(`page-${page}`);
  if (target) {
    target.classList.remove("hidden");
    target.classList.add("active");
  }

  const link = document.querySelector(`.nav-link[data-page="${page}"]`);
  if (link) link.classList.add("active");

  // Trigger lazy loads for other pages
  if (page === "logs" && typeof loadCallLogs === "function") loadCallLogs();
  if (page === "faqs" && typeof loadFAQs === "function") loadFAQs();
  if (page === "products" && typeof loadProducts === "function") loadProducts();
  if (page === "settings") refreshSettingsPage();
}

// =====================================================================
// Session Management
// =====================================================================
function generateSessionId() {
  return "sess-" + Math.random().toString(36).slice(2, 10) + "-" + Date.now().toString(36);
}

function initSession() {
  state.sessionId = generateSessionId();
  state.messageCount = 0;
  updateSessionDisplay();
}

function startNewSession() {
  if (state.isOnCall) endCall();
  clearChat();
  initSession();
}

function updateSessionDisplay() {
  const short = state.sessionId ? state.sessionId.slice(0, 18) + "…" : "—";
  const el = $("sideSessionId");
  if (el) el.textContent = short;
  if (el) el.title = state.sessionId || "";

  const settEl = $("settingsSessionId");
  if (settEl) { settEl.textContent = short; settEl.title = state.sessionId || ""; }
}

function recordClientCallTurn(sessionId, userText, botReply, intent, outcome) {
  try {
    const raw = localStorage.getItem("digibuzz_call_logs");
    const logs = raw ? JSON.parse(raw) : {};
    if (!logs[sessionId]) {
      logs[sessionId] = {
        session_id: sessionId,
        started_at: new Date().toISOString().replace("T", " ").slice(0, 19),
        last_activity: new Date().toISOString().replace("T", " ").slice(0, 19),
        status: "active",
        outcome: outcome || "Resolved",
        total_messages: 0,
        messages: [],
      };
    }
    const session = logs[sessionId];
    session.last_activity = new Date().toISOString().replace("T", " ").slice(0, 19);
    session.outcome = outcome || session.outcome || "Resolved";
    session.total_messages = (session.total_messages || 0) + 1;
    session.messages.push({
      timestamp: session.last_activity,
      user_transcript: userText,
      ai_response: botReply,
      detected_intent: intent || "general_inquiry",
      outcome: outcome || "Resolved",
    });
    localStorage.setItem("digibuzz_call_logs", JSON.stringify(logs));

    // Non-blocking sync to server database
    fetch("/api/calls/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(session),
    }).catch(() => {});
  } catch (e) {
    console.warn("Could not save to localStorage:", e);
  }
}

// =====================================================================
// Call Start / End
// =====================================================================
async function startCall() {
  if (state.isOnCall) return;
  state.isOnCall = true;

  setStatus("active", "Connected — DigiBuzz AI Calling Agent");
  setInputEnabled(true);

  $("btnStartCall").classList.add("hidden");
  $("btnEndCall").classList.remove("hidden");
  const hint = $("callHintBanner");
  if (hint) hint.style.display = "inline-flex";

  $("sideStatus").textContent = "Active";
  $("settingsCallStatus").textContent = "Active";

  // Request browser microphone permission right on call start if not done yet
  await requestMicPermission();

  hidePlaceholder();
  const greeting = "Hello! I am the DigiBuzz AI Calling Agent. How may I assist you with our products or services today?";
  addBotMessage(greeting);
  loadChips();

  // Speak the initial greeting out loud so the model speaks immediately upon call start
  playAudio(greeting);
}

// Prompt and verify user microphone permission
async function requestMicPermission() {
  if (state.micPermissionRequested) return;
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // Stop stream right after getting permission so browser mic is unlocked
      stream.getTracks().forEach((track) => track.stop());
      state.micPermissionRequested = true;
      $("btnMic").title = "Click to speak";
    } catch (err) {
      console.warn("Microphone access permission denied or dismissed:", err);
      $("btnMic").title = "Mic access blocked. Enable in browser site settings.";
      addSystemMessage("⚠️ Microphone permission required for voice input. Click mic or enable in browser.");
    }
  }
}

function endCall() {
  if (!state.isOnCall) return;
  state.isOnCall = false;
  state.isBusy = false;

  // Turn off mic if active
  if (state.recognition && state.isListening) {
    try { state.recognition.stop(); } catch (_) {}
  }

  setStatus("idle", "Call ended");
  setInputEnabled(false);

  $("btnStartCall").classList.remove("hidden");
  $("btnEndCall").classList.add("hidden");
  const hint = $("callHintBanner");
  if (hint) hint.style.display = "none";

  $("sideStatus").textContent = "Ended";
  $("settingsCallStatus").textContent = "Ended";

  stopAudio();
  addSystemMessage("Call ended. Click Start Call to reconnect.");
}

// =====================================================================
// Chat UI Helpers
// =====================================================================
function hidePlaceholder() {
  const ph = $("chatPlaceholder");
  if (ph) ph.remove();
}

function clearChat() {
  const win = $("chatWindow");
  win.innerHTML = `
    <div class="chat-placeholder" id="chatPlaceholder">
      <div class="placeholder-icon">🎙️</div>
      <p>Click <strong>Start Call</strong> to connect with the DigiBuzz AI Calling Agent.</p>
      <p class="text-muted">You can type or use voice to ask questions about products, pricing, delivery, and more.</p>
    </div>`;
}

function scrollChatToBottom() {
  const win = $("chatWindow");
  win.scrollTop = win.scrollHeight;
}

function addUserMessage(text) {
  hidePlaceholder();
  const row = document.createElement("div");
  row.className = "message-row user";
  row.innerHTML = `
    <div class="message-avatar">🧑</div>
    <div>
      <div class="message-bubble">${escapeHtml(text)}</div>
      <div class="message-meta">${formatTime()}</div>
    </div>`;
  $("chatWindow").appendChild(row);
  scrollChatToBottom();
  state.messageCount++;
  $("sideMsgCount").textContent = state.messageCount;
}

function addBotMessage(text) {
  hidePlaceholder();
  const row = document.createElement("div");
  row.className = "message-row bot";
  row.innerHTML = `
    <div class="message-avatar">🤖</div>
    <div>
      <div class="message-bubble">${escapeHtml(text)}</div>
      <div class="message-meta">${formatTime()}</div>
    </div>`;
  $("chatWindow").appendChild(row);
  scrollChatToBottom();
}

function addSystemMessage(text) {
  const el = document.createElement("div");
  el.style.cssText = "text-align:center;color:var(--text-muted);font-size:.8rem;padding:.5rem 1rem;";
  el.textContent = text;
  $("chatWindow").appendChild(el);
  scrollChatToBottom();
}

// =====================================================================
// Status / UI State Helpers
// =====================================================================
function setStatus(type, text) {
  const dot = $("statusDot");
  const label = $("statusText");
  dot.className = "status-dot " + type;
  label.textContent = text;
}

function setInputEnabled(enabled) {
  const input = $("chatInput");
  const sendBtn = $("btnSend");
  const micBtn = $("btnMic");
  input.disabled = !enabled;
  sendBtn.disabled = !enabled;
  micBtn.disabled = !enabled;
}

function setBusy(busy) {
  state.isBusy = busy;
  $("btnSend").disabled = busy || !state.isOnCall;
  $("chatInput").disabled = busy || !state.isOnCall;
  const typing = $("typingIndicator");
  if (busy) {
    typing.classList.remove("hidden");
  } else {
    typing.classList.add("hidden");
  }
}

// =====================================================================
// Filler Speech + Send Message
// =====================================================================
function pickFiller() {
  return FILLER_PHRASES[Math.floor(Math.random() * FILLER_PHRASES.length)];
}

async function playTTSImmediate(text) {
  if (!state.isVoiceEnabled) return;
  try {
    const res = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    stopAudio();
    state.currentAudio = new Audio(url);
    state.fillerPlaying = true;
    state.currentAudio.onended = () => { state.fillerPlaying = false; URL.revokeObjectURL(url); };
    state.currentAudio.play().catch(() => {});
  } catch (_) { /* TTS is non-critical */ }
}

async function playAudio(text) {
  if (!state.isVoiceEnabled) return;
  // Wait for filler to finish (max 3s)
  if (state.fillerPlaying) {
    await new Promise((resolve) => {
      const check = setInterval(() => {
        if (!state.fillerPlaying) { clearInterval(check); resolve(); }
      }, 100);
      setTimeout(() => { clearInterval(check); resolve(); }, 3000);
    });
  }
  try {
    const res = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    stopAudio();
    state.currentAudio = new Audio(url);
    state.currentAudio.onended = () => URL.revokeObjectURL(url);
    state.currentAudio.play().catch(() => {});
  } catch (_) { /* TTS is non-critical */ }
}

function stopAudio() {
  if (state.currentAudio) {
    state.currentAudio.pause();
    state.currentAudio = null;
  }
  state.fillerPlaying = false;
}

// Detect spoken intent to end the call
function isEndCallPhrase(text) {
  if (!text) return false;
  const clean = text.toLowerCase().trim().replace(/[.,!?;:]/g, "");
  const endPhrases = [
    "end call",
    "end the call",
    "finish call",
    "finish the call",
    "stop call",
    "hang up",
    "disconnect",
    "disconnect call",
    "bye",
    "goodbye",
    "bye bye",
    "call cut",
    "cut the call",
    "close call",
    "end conversation",
    "allah hafiz",
    "khuda hafiz",
  ];
  return endPhrases.some((phrase) => clean === phrase || clean.startsWith(phrase + " ") || clean.endsWith(" " + phrase));
}

async function sendMessage(text) {
  text = (text || $("chatInput").value).trim();
  if (!text || !state.isOnCall || state.isBusy) return;

  $("chatInput").value = "";
  addUserMessage(text);

  // Check if caller wants to finish the call via voice command
  if (isEndCallPhrase(text)) {
    setBusy(true);
    const farewell = "Thank you for calling DigiBuzz Support. Have a wonderful day! Goodbye.";
    addBotMessage(farewell);
    
    // Play polite farewell message and automatically hang up call when audio completes
    if (state.isVoiceEnabled) {
      await playAudio(farewell);
      // Wait for audio to finish then end call
      if (state.currentAudio) {
        state.currentAudio.onended = () => {
          setBusy(false);
          endCall();
        };
        return;
      }
    }
    setBusy(false);
    endCall();
    return;
  }

  setBusy(true);

  // Play filler immediately — do not await (fire-and-forget)
  playTTSImmediate(pickFiller());

  // Small delay so filler starts before API call
  await sleep(800);

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, message: text }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    const data = await res.json();
    setBusy(false);
    addBotMessage(data.reply);
    recordClientCallTurn(state.sessionId, text, data.reply, data.intent, data.outcome);
    playAudio(data.speech || data.reply);
  } catch (err) {
    setBusy(false);
    addBotMessage("Sorry, I encountered an error. Please try again.");
    console.error("Chat error:", err);
  }
}

// =====================================================================
// Quick FAQ Chips Carousel / Slide Bar with Mouse Drag & Wheel
// =====================================================================
async function loadChips() {
  const section = $("chipsSection");
  if (!section) return;
  section.innerHTML = "";
  try {
    const res = await fetch("/api/faqs?limit=30");
    const data = await res.json();
    const questions = data.questions || [];

    questions.forEach((item) => {
      const qText = typeof item === "string" ? item : item.question;
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.innerHTML = `<span>💬</span> <span>${escapeHtml(qText)}</span>`;
      chip.title = `Click to ask: "${qText}"`;
      chip.addEventListener("click", () => {
        if (!state.isOnCall) {
          startCall();
          setTimeout(() => sendMessage(qText), 600);
        } else {
          sendMessage(qText);
        }
      });
      section.appendChild(chip);
    });

    // Wire arrow slider scroll buttons (with larger scroll step)
    const leftBtn = $("chipScrollLeft");
    const rightBtn = $("chipScrollRight");
    if (leftBtn) {
      leftBtn.onclick = () => section.scrollBy({ left: -280, behavior: "smooth" });
    }
    if (rightBtn) {
      rightBtn.onclick = () => section.scrollBy({ left: 280, behavior: "smooth" });
    }

    // Enable mouse wheel horizontal scrolling on the slider
    section.addEventListener("wheel", (e) => {
      if (e.deltaY !== 0) {
        e.preventDefault();
        section.scrollBy({ left: e.deltaY * 2, behavior: "smooth" });
      }
    }, { passive: false });

    // Enable mouse drag-to-scroll
    let isDown = false;
    let startX = 0;
    let scrollLeft = 0;

    section.addEventListener("mousedown", (e) => {
      isDown = true;
      startX = e.pageX - section.offsetLeft;
      scrollLeft = section.scrollLeft;
      section.style.cursor = "grabbing";
    });

    window.addEventListener("mouseup", () => {
      if (isDown) {
        isDown = false;
        section.style.cursor = "pointer";
      }
    });

    section.addEventListener("mousemove", (e) => {
      if (!isDown) return;
      e.preventDefault();
      const x = e.pageX - section.offsetLeft;
      const walk = (x - startX) * 1.5;
      section.scrollLeft = scrollLeft - walk;
    });

  } catch (_) { /* chips are non-critical */ }
}

// =====================================================================
// Speech Recognition (Mic Button) & Half-Duplex Lock
// =====================================================================
function initSpeechRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    $("btnMic").title = "Speech recognition not supported in this browser";
    return;
  }

  const recog = new SpeechRecognition();
  recog.lang = "en-US";
  recog.interimResults = false;
  recog.maxAlternatives = 1;

  recog.onresult = (e) => {
    const transcript = e.results[0][0].transcript;
    $("chatInput").value = transcript;
    sendMessage(transcript);
  };

  recog.onstart = () => {
    state.isListening = true;
    $("btnMic").classList.add("listening");
    $("btnMic").title = "Listening to you…";
    // If agent was playing audio, mute/stop it immediately so mic doesn't catch AI voice
    stopAudio();
  };

  recog.onend = () => {
    state.isListening = false;
    $("btnMic").classList.remove("listening");
    $("btnMic").title = "Click to speak";
  };

  recog.onerror = (e) => {
    state.isListening = false;
    $("btnMic").classList.remove("listening");
    $("btnMic").title = "Click to speak";
    if (e.error === "not-allowed") {
      addSystemMessage("⚠️ Microphone blocked. Please allow mic permissions in your browser bar.");
    }
  };

  state.recognition = recog;

  $("btnMic").addEventListener("click", async () => {
    if (!state.isOnCall || state.isBusy) return;
    
    // Ensure permission is granted
    if (!state.micPermissionRequested) {
      await requestMicPermission();
    }

    if (state.isListening) {
      recog.stop();
    } else {
      // Never allow mic while AI agent is speaking
      if (state.currentAudio) {
        stopAudio();
      }
      try {
        recog.start();
      } catch (err) {
        console.warn("Recog start error:", err);
      }
    }
  });
}

// Disable/enable microphone when AI agent is speaking to prevent echo
function setMicLocked(locked) {
  const mic = $("btnMic");
  if (!mic) return;
  if (locked) {
    if (state.recognition && state.isListening) {
      try { state.recognition.stop(); } catch (_) {}
    }
    mic.disabled = true;
    mic.title = "Agent is speaking... Mic is paused to avoid echo";
  } else {
    mic.disabled = !state.isOnCall || state.isBusy;
    mic.title = state.isListening ? "Listening to you…" : "Click to speak";
  }
}

// Automatically turn ON microphone when the AI agent completes speaking
function autoStartListening() {
  if (!state.isOnCall || state.isBusy || !state.recognition) return;
  
  // Brief delay (350ms) to ensure speakers have completely released the audio buffer
  setTimeout(() => {
    if (!state.isOnCall || state.isBusy || state.currentAudio || state.isListening) return;
    try {
      state.recognition.start();
    } catch (e) {
      // If already started or browser blocked auto-start, ignore cleanly
      console.log("Auto-mic start attempt:", e.message || e);
    }
  }, 350);
}

// =====================================================================
// Filler Speech + Audio Playback Helpers
// =====================================================================
function pickFiller() {
  return FILLER_PHRASES[Math.floor(Math.random() * FILLER_PHRASES.length)];
}

async function playTTSImmediate(text) {
  if (!state.isVoiceEnabled) return;
  try {
    const res = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    stopAudio();

    // Lock mic while filler speaks
    setMicLocked(true);

    state.currentAudio = new Audio(url);
    state.fillerPlaying = true;
    state.currentAudio.onended = () => {
      state.fillerPlaying = false;
      URL.revokeObjectURL(url);
      setMicLocked(false);
      // Don't autoStartListening here because the actual reply will speak right after
    };
    state.currentAudio.onerror = () => {
      state.fillerPlaying = false;
      URL.revokeObjectURL(url);
      setMicLocked(false);
    };
    await state.currentAudio.play();
  } catch (_) {
    setMicLocked(false);
  }
}

async function playAudio(text) {
  if (!state.isVoiceEnabled) return;
  
  // Wait for filler to finish if one is currently playing
  if (state.fillerPlaying) {
    await new Promise((resolve) => {
      const check = setInterval(() => {
        if (!state.fillerPlaying) { clearInterval(check); resolve(); }
      }, 100);
      setTimeout(() => { clearInterval(check); resolve(); }, 3000);
    });
  }

  try {
    const res = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) {
      setMicLocked(false);
      autoStartListening();
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    stopAudio();

    // Lock mic while agent speaks
    setMicLocked(true);

    state.currentAudio = new Audio(url);
    const cleanup = () => {
      URL.revokeObjectURL(url);
      if (state.currentAudio) state.currentAudio = null;
      setMicLocked(false);
      // Auto-turn on mic right when AI finishes speaking its answer or greeting!
      autoStartListening();
    };

    state.currentAudio.onended = cleanup;
    state.currentAudio.onerror = cleanup;
    await state.currentAudio.play();
  } catch (_) {
    setMicLocked(false);
    autoStartListening();
  }
}

function stopAudio() {
  if (state.currentAudio) {
    state.currentAudio.pause();
    state.currentAudio = null;
  }
  state.fillerPlaying = false;
  setMicLocked(false);
}

// =====================================================================
// Voice Toggle
// =====================================================================
function toggleVoice() {
  state.isVoiceEnabled = !state.isVoiceEnabled;
  const label = state.isVoiceEnabled ? "🔇 Disable Voice" : "🔊 Enable Voice";
  const status = state.isVoiceEnabled ? "On" : "Off";
  ["btnToggleVoice", "settingsBtnToggleVoice"].forEach((id) => {
    if ($(id)) $(id).textContent = label;
  });
  $("sideVoiceStatus").textContent = status;
  $("settingsVoiceStatus").textContent = state.isVoiceEnabled ? "Enabled" : "Disabled";
  if (!state.isVoiceEnabled) stopAudio();
}

// =====================================================================
// Settings Page
// =====================================================================
async function refreshSettingsPage() {
  updateSessionDisplay();
  $("settingsCallStatus").textContent = state.isOnCall ? "Active" : "Idle";
  $("settingsVoiceStatus").textContent = state.isVoiceEnabled ? "Enabled" : "Disabled";
  ["settingsBtnToggleVoice"].forEach((id) => {
    if ($(id)) $(id).textContent = state.isVoiceEnabled ? "🔇 Disable Voice" : "🔊 Enable Voice";
  });

  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    $("settingsModel").textContent = data.model || "—";
    $("settingsFaqs").textContent = data.faqs ?? "—";
    $("settingsProducts").textContent = data.products ?? "—";
  } catch (_) {
    $("settingsModel").textContent = "Unavailable";
  }

  await refreshKeyStatus();
}

async function refreshKeyStatus() {
  try {
    const res = await fetch("/api/key-status");
    const d = await res.json();
    $("keyActiveLabel").textContent = d.active_key || "—";
    $("keyPrimaryStatus").innerHTML = statusBadge(d.primary_status);
    $("keySecondaryStatus").innerHTML = statusBadge(d.secondary_status);
    $("keyFallback").textContent = d.has_fallback ? "Available" : "Not configured";
    $("keyLastRefreshed").textContent = "Last refreshed: " + new Date().toLocaleTimeString();

    // Update navbar badge
    const badge = $("keyStatusBadge");
    if (d.primary_status === "active") {
      badge.textContent = "🟢";
      badge.title = "API: Primary key active";
    } else if (d.active_key === "Secondary" && d.secondary_status === "active") {
      badge.textContent = "🟡";
      badge.title = "API: Switched to Secondary key";
    } else {
      badge.textContent = "🔴";
      badge.title = "API: All keys may be exhausted";
    }
  } catch (_) {
    $("keyLastRefreshed").textContent = "Failed to fetch status";
  }
}

function statusBadge(status) {
  const map = {
    active: '<span class="badge in-stock">Active</span>',
    exhausted: '<span class="badge out-of-stock">Exhausted</span>',
    not_configured: '<span class="badge" style="background:var(--surface-card);color:var(--text-muted)">Not configured</span>',
  };
  return map[status] || status;
}

// =====================================================================
// Utility Helpers
// =====================================================================
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// =====================================================================
// Theme Manager (Dark & Light)
// =====================================================================
function initTheme() {
  const saved = localStorage.getItem("digibuzz_theme");
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const theme = saved || (prefersDark ? "dark" : "light");
  applyTheme(theme);

  const toggleBtn = $("themeToggleBtn");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "light";
      const next = current === "dark" ? "light" : "dark";
      applyTheme(next);
    });
  }

  const settingsToggleBtn = $("settingsBtnToggleTheme");
  if (settingsToggleBtn) {
    settingsToggleBtn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "light";
      const next = current === "dark" ? "light" : "dark";
      applyTheme(next);
    });
  }
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("digibuzz_theme", theme);
  
  const icon = $("themeToggleIcon");
  const text = $("themeToggleText");
  const toggleBtn = $("themeToggleBtn");
  
  if (icon && text) {
    icon.textContent = theme === "dark" ? "☀️" : "🌙";
    text.textContent = theme === "dark" ? "Light" : "Dark";
  } else if (toggleBtn) {
    toggleBtn.textContent = theme === "dark" ? "☀️" : "🌙";
  }
  if (toggleBtn) {
    toggleBtn.title = theme === "dark" ? "Switch to Light Theme" : "Switch to Dark Theme";
  }

  const themeLabel = $("currentThemeLabel");
  if (themeLabel) {
    themeLabel.textContent = theme === "dark" ? "Dark Mode" : "Light Mode";
  }
}

// =====================================================================
// Initialization
// =====================================================================
document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initNavigation();
  initSession();
  initSpeechRecognition();
  loadChips(); // Load FAQ suggestion slider immediately on page load!

  // Call controls
  $("btnStartCall").addEventListener("click", startCall);
  $("btnEndCall").addEventListener("click", endCall);
  $("btnNewSession").addEventListener("click", startNewSession);
  $("btnToggleVoice").addEventListener("click", toggleVoice);
  $("settingsBtnToggleVoice").addEventListener("click", toggleVoice);
  $("settingsBtnNewSession").addEventListener("click", startNewSession);
  $("btnRefreshKeyStatus").addEventListener("click", refreshKeyStatus);

  // Chat input
  $("chatInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  $("btnSend").addEventListener("click", () => sendMessage());

  // Load key status on first load
  refreshKeyStatus();
});
