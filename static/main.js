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
  ws: null,
  audioPlayer: null,
  currentBotBubble: null,
  currentStreamText: "",
  activeUserText: "",
  isShowingFiller: false,
};

// ---- Filler phrases spoken immediately while Gemini processes ----
const FILLER_PHRASES = [
  "Let me check that for you.",
  "One moment please.",
  "Sure, let me look into that.",
  "Please hold on a moment.",
  "Got it, checking for you now.",
];

// ---- DOM Helpers (Null-Safe) ----
const $ = (id) => document.getElementById(id);
const setText = (id, val) => { const el = $(id); if (el) el.textContent = val; };
const setHtml = (id, val) => { const el = $(id); if (el) el.innerHTML = val; };
const on = (id, event, handler) => { const el = $(id); if (el) el.addEventListener(event, handler); };

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

  const burger = $("navHamburger");
  const navLinks = $("navLinks");

  if (burger) {
    burger.addEventListener("click", (e) => {
      e.stopPropagation();
      if (navLinks) navLinks.classList.toggle("open");
    });
  }

  // Close mobile drawer when any link inside it is clicked
  if (navLinks) {
    navLinks.addEventListener("click", (e) => {
      if (e.target.closest("a") || e.target.closest("button")) {
        navLinks.classList.remove("open");
      }
    });
  }

  // Close drawer on outside tap/click
  document.addEventListener("click", (e) => {
    if (navLinks && navLinks.classList.contains("open")) {
      if (!navLinks.contains(e.target) && burger && !burger.contains(e.target)) {
        navLinks.classList.remove("open");
      }
    }
  });

  // Close drawer and open modals on Escape key
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (navLinks && navLinks.classList.contains("open")) {
        navLinks.classList.remove("open");
      }
      ["authModal", "faqModal", "productModal", "transcriptModal"].forEach((id) => {
        const modal = $(id);
        if (modal && !modal.classList.contains("hidden")) {
          modal.classList.add("hidden");
        }
      });
    }
  });

  // Key status badge -> open settings
  const keyBadge = $("keyStatusBadge");
  if (keyBadge) {
    keyBadge.addEventListener("click", () => navigateTo("settings"));
  }
}

function navigateTo(page) {
  const navLinks = $("navLinks");
  if (navLinks) navLinks.classList.remove("open");

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
  if (page === "admin" && typeof loadAdminData === "function") loadAdminData();
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
  if (!state.audioPlayer) {
    state.audioPlayer = new AudioStreamPlayer();
    state.audioPlayer.onPlaybackStart = () => {
      setMicLocked(true);
    };
    state.audioPlayer.onPlaybackComplete = () => {
      setMicLocked(false);
      setBusy(false);
      autoStartListening();
    };
  }
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
      credentials: "same-origin",
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

  // Ensure AudioContext is initialized and unlocked during user gesture
  if (state.audioPlayer) {
    state.audioPlayer.ensureContext();
  }

  setStatus("active", "Connected — DigiBuzz AI Calling Agent");
  setInputEnabled(true);

  const btnStart = $("btnStartCall");
  const btnEnd = $("btnEndCall");
  if (btnStart) btnStart.classList.add("hidden");
  if (btnEnd) btnEnd.classList.remove("hidden");
  const hint = $("callHintBanner");
  if (hint) hint.style.display = "inline-flex";

  setText("sideStatus", "Active");
  setText("settingsCallStatus", "Active");

  // Establish low-latency WebSocket connection
  initAiWebSocket();

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

  // Gracefully close WebSocket and interrupt any ongoing backend stream
  if (state.ws) {
    try {
      if (state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: "interrupt" }));
      }
      state.ws.close();
    } catch (_) {}
    state.ws = null;
  }

  setStatus("idle", "Call ended");
  setInputEnabled(false);

  const btnStart = $("btnStartCall");
  const btnEnd = $("btnEndCall");
  if (btnStart) btnStart.classList.remove("hidden");
  if (btnEnd) btnEnd.classList.add("hidden");
  const hint = $("callHintBanner");
  if (hint) hint.style.display = "none";

  setText("sideStatus", "Ended");
  setText("settingsCallStatus", "Ended");

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
  const win = $("chatWindow");
  if (win) win.appendChild(row);
  scrollChatToBottom();
  state.messageCount++;
  setText("sideTurnCount", state.messageCount);
  setText("sideMsgCount", state.messageCount);
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

function createStreamingBotMessage() {
  hidePlaceholder();
  const row = document.createElement("div");
  row.className = "message-row bot streaming";
  row.innerHTML = `
    <div class="message-avatar">🤖</div>
    <div>
      <div class="message-bubble"><span class="streaming-cursor">▌</span></div>
      <div class="message-meta">${formatTime()}</div>
    </div>`;
  const win = $("chatWindow");
  if (win) win.appendChild(row);
  scrollChatToBottom();
  return row;
}

function updateStreamingBotMessage(row, text) {
  if (!row) return;
  const bubble = row.querySelector(".message-bubble");
  if (bubble) {
    bubble.innerHTML = `${escapeHtml(text)}<span class="streaming-cursor">▌</span>`;
  }
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
// Web Audio API — Real-time Gapless Stream Queue Player
// =====================================================================
class AudioStreamPlayer {
  constructor() {
    this.audioCtx = null;
    this.activeSources = [];
    this.nextStartTime = 0;
    this.totalQueued = 0;
    this.totalFinished = 0;
    this.isPlaying = false;
    this.onPlaybackStart = null;
    this.onPlaybackComplete = null;
  }

  ensureContext() {
    if (!this.audioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        this.audioCtx = new AudioContextClass();
      }
    }
    if (this.audioCtx && this.audioCtx.state === "suspended") {
      this.audioCtx.resume().catch(() => {});
    }
    return this.audioCtx;
  }

  async queueAudio(arrayBuffer) {
    const ctx = this.ensureContext();
    if (!ctx || !arrayBuffer || arrayBuffer.byteLength === 0) return;
    try {
      const bufferCopy = arrayBuffer.slice(0);
      const audioBuffer = await ctx.decodeAudioData(bufferCopy);
      this.totalQueued++;

      if (!this.isPlaying) {
        this.isPlaying = true;
        if (typeof this.onPlaybackStart === "function") {
          this.onPlaybackStart();
        }
      }

      const source = ctx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(ctx.destination);

      const now = ctx.currentTime;
      if (this.nextStartTime < now) {
        this.nextStartTime = now + 0.03;
      }
      const playAt = this.nextStartTime;
      this.nextStartTime += audioBuffer.duration;

      source.start(playAt);
      this.activeSources.push(source);

      source.onended = () => {
        this.totalFinished++;
        const idx = this.activeSources.indexOf(source);
        if (idx !== -1) this.activeSources.splice(idx, 1);
        if (this.totalFinished >= this.totalQueued) {
          this.isPlaying = false;
          this.nextStartTime = 0;
          if (typeof this.onPlaybackComplete === "function") {
            this.onPlaybackComplete();
          }
        }
      };
    } catch (err) {
      console.warn("AudioBuffer decode error:", err);
    }
  }

  stop() {
    this.activeSources.forEach((src) => {
      try {
        src.stop();
        src.disconnect();
      } catch (_) {}
    });
    this.activeSources = [];
    this.nextStartTime = 0;
    this.totalQueued = 0;
    this.totalFinished = 0;
    this.isPlaying = false;
  }
}

// Global audio playback & streaming methods
async function playAudio(text) {
  if (!state.isVoiceEnabled || !text) return;
  stopAudio();
  setMicLocked(true);

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

    const arrayBuffer = await res.arrayBuffer();
    if (state.audioPlayer) {
      await state.audioPlayer.queueAudio(arrayBuffer);
    } else {
      const blob = new Blob([arrayBuffer], { type: "audio/mpeg" });
      const url = URL.createObjectURL(blob);
      state.currentAudio = new Audio(url);
      const cleanup = () => {
        URL.revokeObjectURL(url);
        if (state.currentAudio) state.currentAudio = null;
        setMicLocked(false);
        autoStartListening();
      };
      state.currentAudio.onended = cleanup;
      state.currentAudio.onerror = cleanup;
      await state.currentAudio.play();
    }
  } catch (err) {
    console.warn("playAudio error:", err);
    setMicLocked(false);
    autoStartListening();
  }
}

function stopAudio() {
  if (state.audioPlayer) {
    state.audioPlayer.stop();
  }
  if (state.currentAudio) {
    try {
      state.currentAudio.pause();
    } catch (_) {}
    state.currentAudio = null;
  }
  state.fillerPlaying = false;
  setMicLocked(false);
}

// =====================================================================
// WebSocket Ultra-Low Latency Streaming Connection
// =====================================================================
function initAiWebSocket() {
  if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) {
    return state.ws;
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws/ai-call`;

  try {
    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      console.log("WebSocket connected to ultra-low latency endpoint /ws/ai-call");
    };

    ws.onmessage = async (event) => {
      // 1. Incoming binary audio stream chunks from edge-tts
      if (event.data instanceof ArrayBuffer) {
        if (state.isVoiceEnabled && state.audioPlayer) {
          setMicLocked(true);
          await state.audioPlayer.queueAudio(event.data);
        }
        return;
      }

      // 2. Incoming JSON text events & stream progress
      try {
        const data = JSON.parse(event.data);
        switch (data.type) {
          case "start":
            setBusy(true);
            setMicLocked(true);
            if (!state.currentBotBubble) {
              state.isShowingFiller = true;
              state.currentStreamText = "Let me check that for you...";
              state.currentBotBubble = createStreamingBotMessage();
              updateStreamingBotMessage(state.currentBotBubble, state.currentStreamText);
            }
            break;

          case "filler":
            state.isShowingFiller = true;
            state.currentStreamText = data.text || "Let me check that for you...";
            if (!state.currentBotBubble) {
              state.currentBotBubble = createStreamingBotMessage();
            }
            updateStreamingBotMessage(state.currentBotBubble, state.currentStreamText);
            break;

          case "audio_start":
            setMicLocked(true);
            break;

          case "text_delta":
            if (data.delta) {
              if (state.isShowingFiller) {
                state.isShowingFiller = false;
                state.currentStreamText = "";
              }
              state.currentStreamText += data.delta;
              updateStreamingBotMessage(state.currentBotBubble, state.currentStreamText);
            }
            break;

          case "audio_end":
            // Server completed audio chunk generation
            if (!state.isVoiceEnabled || (state.audioPlayer && !state.audioPlayer.isPlaying)) {
              setBusy(false);
              setMicLocked(false);
              autoStartListening();
            }
            break;

          case "done":
            state.isShowingFiller = false;
            const finalReply = data.reply || state.currentStreamText;
            if (state.currentBotBubble) {
              updateStreamingBotMessage(state.currentBotBubble, finalReply);
              state.currentBotBubble.classList.remove("streaming");
              const cursor = state.currentBotBubble.querySelector(".streaming-cursor");
              if (cursor) cursor.remove();
            } else {
              addBotMessage(finalReply);
            }
            recordClientCallTurn(
              state.sessionId,
              data.user_text || state.activeUserText,
              finalReply,
              data.intent,
              data.outcome
            );
            state.currentBotBubble = null;
            state.currentStreamText = "";
            state.activeUserText = "";

            if (!state.isVoiceEnabled || (state.audioPlayer && !state.audioPlayer.isPlaying)) {
              setBusy(false);
              setMicLocked(false);
              autoStartListening();
            }
            break;

          case "interrupted":
            stopAudio();
            setBusy(false);
            setMicLocked(false);
            break;

          case "error":
            setBusy(false);
            setMicLocked(false);
            addBotMessage(data.message || "An error occurred during audio generation.");
            autoStartListening();
            break;
        }
      } catch (err) {
        console.error("WebSocket message parse error:", err);
      }
    };

    ws.onclose = () => {
      console.log("WebSocket connection closed.");
      state.ws = null;
    };

    ws.onerror = (err) => {
      console.warn("WebSocket error:", err);
    };

    state.ws = ws;
    return ws;
  } catch (e) {
    console.warn("Could not initialize WebSocket:", e);
    return null;
  }
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
  state.activeUserText = text;

  // Interrupt any existing audio playback immediately
  stopAudio();
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({ type: "interrupt" }));
  }

  // Check if caller wants to finish the call via voice command
  if (isEndCallPhrase(text)) {
    setBusy(true);
    const farewell = "Thank you for calling DigiBuzz Support. Have a wonderful day! Goodbye.";
    addBotMessage(farewell);
    
    if (state.isVoiceEnabled) {
      if (state.audioPlayer) {
        state.audioPlayer.onPlaybackComplete = () => {
          setBusy(false);
          endCall();
        };
      }
      await playAudio(farewell);
      return;
    }
    setBusy(false);
    endCall();
    return;
  }

  setBusy(true);
  setMicLocked(true);

  // Instantly render filler in chat bubble with 0ms delay
  state.isShowingFiller = true;
  state.currentStreamText = "Let me check that for you...";
  state.currentBotBubble = createStreamingBotMessage();
  updateStreamingBotMessage(state.currentBotBubble, state.currentStreamText);

  // 1. Ultra-low latency streaming over WebSocket
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    if (state.audioPlayer) {
      state.audioPlayer.ensureContext();
    }
    state.ws.send(JSON.stringify({
      type: "message",
      session_id: state.sessionId,
      text: text,
    }));
    return;
  }

  // 2. Fallback to HTTP POST if WebSocket connection is not open
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
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
    await playAudio(data.speech || data.reply);
  } catch (err) {
    setBusy(false);
    setMicLocked(false);
    addBotMessage("Sorry, I encountered an error. Please try again.");
    console.error("Chat error:", err);
  }
}

// =====================================================================
// Quick FAQ Chips Carousel / Slide Bar with Drag & Mobile Touch Support
// =====================================================================
let chipsListenersAttached = false;
let isDown = false;
let startX = 0;
let scrollLeft = 0;
let hasDragged = false;

async function loadChips() {
  const section = $("chipsSection");
  if (!section) return;

  try {
    let questions = [];
    try {
      const res = await fetch("/api/faqs?limit=30", { credentials: "same-origin" });
      if (res.ok) {
        const data = await res.json();
        questions = data.questions || [];
      }
    } catch (_) {}

    // Fallback default suggestions if empty or offline
    if (!questions || questions.length === 0) {
      questions = [
        "What are your wholesale minimums?",
        "What are your delivery times across Pakistan?",
        "Do you offer bulk purchase discounts?",
        "Can I track my order status?",
        "What payment methods do you accept?",
        "How do I return a damaged shipment?",
      ];
    }

    section.innerHTML = "";
    questions.forEach((item) => {
      const qText = typeof item === "string" ? item : (item.question || "");
      if (!qText) return;
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.innerHTML = `<span>💬</span> <span>${escapeHtml(qText)}</span>`;
      chip.title = `Click to ask: "${qText}"`;

      chip.addEventListener("click", () => {
        if (hasDragged) {
          hasDragged = false;
          return;
        }
        if (!state.isOnCall) {
          startCall();
          setTimeout(() => sendMessage(qText), 600);
        } else {
          sendMessage(qText);
        }
      });
      section.appendChild(chip);
    });

    // Wire arrow slider scroll buttons
    const leftBtn = $("chipScrollLeft");
    const rightBtn = $("chipScrollRight");
    if (leftBtn) {
      leftBtn.onclick = (e) => {
        e.preventDefault();
        section.scrollBy({ left: -260, behavior: "smooth" });
      };
    }
    if (rightBtn) {
      rightBtn.onclick = (e) => {
        e.preventDefault();
        section.scrollBy({ left: 260, behavior: "smooth" });
      };
    }

    // Attach drag and wheel listeners once
    if (!chipsListenersAttached) {
      chipsListenersAttached = true;

      section.addEventListener("wheel", (e) => {
        if (e.deltaY !== 0) {
          e.preventDefault();
          section.scrollBy({ left: e.deltaY * 2, behavior: "smooth" });
        }
      }, { passive: false });

      section.addEventListener("mousedown", (e) => {
        isDown = true;
        hasDragged = false;
        startX = e.pageX - section.offsetLeft;
        scrollLeft = section.scrollLeft;
        section.style.cursor = "grabbing";
      });

      window.addEventListener("mouseup", () => {
        if (isDown) {
          isDown = false;
          section.style.cursor = "pointer";
          setTimeout(() => { hasDragged = false; }, 50);
        }
      });

      section.addEventListener("mousemove", (e) => {
        if (!isDown) return;
        const x = e.pageX - section.offsetLeft;
        const walk = (x - startX) * 1.5;
        if (Math.abs(walk) > 5) {
          hasDragged = true;
        }
        section.scrollLeft = scrollLeft - walk;
      });
    }

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
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: "interrupt" }));
    }
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
      stopAudio();
      if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: "interrupt" }));
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
  if (state.audioPlayer && state.audioPlayer.isPlaying) return;
  
  // Brief delay (300ms) to ensure speakers have completely released the audio buffer
  setTimeout(() => {
    if (!state.isOnCall || state.isBusy || state.currentAudio || state.isListening) return;
    if (state.audioPlayer && state.audioPlayer.isPlaying) return;
    try {
      state.recognition.start();
    } catch (e) {
      console.log("Auto-mic start attempt:", e.message || e);
    }
  }, 300);
}

// =====================================================================
// Voice Toggle
// =====================================================================
function toggleVoice() {
  state.isVoiceEnabled = !state.isVoiceEnabled;
  const label = state.isVoiceEnabled ? "🔇 Disable Voice Output" : "🔊 Enable Voice Output";
  const status = state.isVoiceEnabled ? "Enabled 🔊" : "Disabled 🔇";
  ["btnToggleVoice", "settingsBtnToggleVoice"].forEach((id) => {
    const el = $(id);
    if (el) el.textContent = label;
  });
  setText("sideVoiceStatus", status);
  setText("settingsVoiceStatus", state.isVoiceEnabled ? "Enabled 🔊" : "Disabled 🔇");
  if (!state.isVoiceEnabled) stopAudio();
}

// =====================================================================
// Settings Page
// =====================================================================
async function refreshSettingsPage() {
  updateSessionDisplay();
  setText("settingsCallStatus", state.isOnCall ? "Active" : "Idle");
  setText("settingsVoiceStatus", state.isVoiceEnabled ? "Enabled 🔊" : "Disabled 🔇");
  const toggleBtn = $("settingsBtnToggleVoice");
  if (toggleBtn) {
    toggleBtn.textContent = state.isVoiceEnabled ? "🔇 Disable Voice Output" : "🔊 Enable Voice Output";
  }

  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    setText("settingsModel", data.model || "Gemini 2.5 Flash");
    setText("settingsFaqs", data.faqs != null ? `${data.faqs} active items` : "—");
    setText("settingsProducts", data.products != null ? `${data.products} items` : "—");
  } catch (_) {
    setText("settingsModel", "Unavailable");
  }

  await refreshKeyStatus();
}

async function refreshKeyStatus() {
  // Only query key status if current user is an admin
  const isAdmin = typeof authState !== "undefined" && authState && authState.isAdmin;
  const adminSec = $("settingsAdminSection");
  const badge = $("keyStatusBadge");

  if (!isAdmin) {
    if (adminSec) adminSec.style.display = "none";
    if (badge) badge.style.display = "none";
    return;
  }

  if (adminSec) adminSec.style.display = "block";
  if (badge) badge.style.display = "inline-flex";

  try {
    const res = await fetch("/api/key-status", { credentials: "same-origin" });
    if (!res.ok) {
      if (adminSec) adminSec.style.display = "none";
      if (badge) badge.style.display = "none";
      return;
    }
    const d = await res.json();
    setText("keyActiveLabel", d.active_key || "Primary");
    setHtml("keyPrimaryStatus", statusBadge(d.primary_status));
    setHtml("keySecondaryStatus", statusBadge(d.secondary_status));
    setText("keyFallback", d.failover_triggered ? "Active (Failover engaged)" : (d.has_fallback ? "Configured & Ready" : "Not Configured"));
    setText("keyLastRefreshed", "Last checked: " + new Date().toLocaleTimeString());

    if (badge) {
      if (d.failover_triggered) {
        badge.textContent = "⚠️";
        badge.title = "Gemini Key: Switched to Failover Key";
      } else {
        badge.textContent = "🟢";
        badge.title = `Gemini Key: ${d.active_key} Active`;
      }
    }
  } catch (_) {
    if (badge) badge.textContent = "⚪";
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
    themeLabel.textContent = theme === "dark" ? "Dark Mode 🌙" : "Light Mode ☀️";
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
  loadChips();

  // Call controls (with null-safe 'on' helper)
  on("btnStartCall", "click", startCall);
  on("btnEndCall", "click", endCall);
  on("btnNewSession", "click", startNewSession);
  on("btnToggleVoice", "click", toggleVoice);
  on("settingsBtnToggleVoice", "click", toggleVoice);
  on("settingsBtnNewSession", "click", startNewSession);
  on("btnRefreshKeyStatus", "click", refreshKeyStatus);

  // Chat input
  const chatInput = $("chatInput");
  if (chatInput) {
    chatInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
  }
  on("btnSend", "click", () => sendMessage());

  // Check key status only if admin
  if (typeof authState !== "undefined" && authState && authState.isAdmin) {
    refreshKeyStatus();
  }
});
