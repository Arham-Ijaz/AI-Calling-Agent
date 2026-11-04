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
  speechQueue: null,
  activeStreamController: null,
  currentBotBubble: null,
  currentStreamText: "",
  activeUserText: "",
  isShowingFiller: false,
  isSpeakingFiller: false,
};

const ARABIC_SCRIPT_REGEX = /[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]/;

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
  if (!state.speechQueue && state.audioPlayer) {
    state.speechQueue = new SentenceSpeechQueue(state.audioPlayer);
  }
  loadFillerPhrases();
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
  if (!state.speechQueue && state.audioPlayer) {
    state.speechQueue = new SentenceSpeechQueue(state.audioPlayer);
  }
  loadFillerPhrases();

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

  stopFillerAudio();
  if (state.speechQueue) {
    state.speechQueue.reset();
  }
  if (speechSilenceTimer) {
    clearTimeout(speechSilenceTimer);
    speechSilenceTimer = null;
  }

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
  const isRtl = ARABIC_SCRIPT_REGEX.test(text);
  const rtlStyle = isRtl ? 'dir="rtl" style="text-align:right;font-family:\'Noto Nastaliq Urdu\',\'Jameel Noori Nastaleeq\',\'Segoe UI\',sans-serif;"' : '';
  row.innerHTML = `
    <div class="message-avatar">🤖</div>
    <div>
      <div class="message-bubble" ${rtlStyle}>${escapeHtml(text)}</div>
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

function updateStreamingBotMessage(row, text, isDone = false) {
  if (!row) return;
  const bubble = row.querySelector(".message-bubble");
  if (bubble) {
    if (ARABIC_SCRIPT_REGEX.test(text)) {
      bubble.setAttribute("dir", "rtl");
      bubble.style.textAlign = "right";
      bubble.style.fontFamily = "'Noto Nastaliq Urdu', 'Jameel Noori Nastaleeq', 'Segoe UI', Tahoma, sans-serif";
    }
    bubble.innerHTML = isDone
      ? escapeHtml(text)
      : `${escapeHtml(text)}<span class="streaming-cursor">▌</span>`;
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
        cancelInstantFiller(); // Immediately halt filler when real neural audio begins!
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

// =====================================================================
// Sentence Speech Queue with Prefetching & Gapless Web Audio Scheduling
// =====================================================================
class SentenceSpeechQueue {
  constructor(audioPlayer) {
    this.audioPlayer = audioPlayer;
    this.queue = [];
    this.isProcessing = false;
    this.prefetchCache = new Map();
    this.onQueueEmpty = null;
    this.localEnVoice = null;
    this.localUrVoice = null;
    this.initVoices();
  }

  initVoices() {
    if (typeof window !== "undefined" && window.speechSynthesis) {
      const updateVoices = () => {
        const voices = window.speechSynthesis.getVoices() || [];
        this.localEnVoice = voices.find((v) => v.lang && (v.lang.startsWith("en") || v.lang.includes("US") || v.lang.includes("UK"))) || null;
        this.localUrVoice = voices.find((v) => v.lang && (v.lang.startsWith("ur") || v.lang.includes("PK"))) || null;
      };
      updateVoices();
      if (window.speechSynthesis.onvoiceschanged !== undefined) {
        window.speechSynthesis.onvoiceschanged = updateVoices;
      }
    }
  }

  reset() {
    this.queue = [];
    this.isProcessing = false;
    this.prefetchCache.clear();
    if (typeof window !== "undefined" && window.speechSynthesis) {
      try {
        window.speechSynthesis.cancel();
      } catch (_) {}
    }
  }

  async enqueue(sentence, idx) {
    const clean = (sentence || "").trim();
    if (!clean) return;
    this.queue.push({ text: clean, idx: idx });
    // Immediately prefetch TTS audio for this sentence from /api/tts
    this.prefetch(clean);
    if (!this.isProcessing) {
      this.processNext();
    }
  }

  async prefetch(text) {
    if (!text || this.prefetchCache.has(text)) return;
    try {
      const res = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (res.ok) {
        const buf = await res.arrayBuffer();
        this.prefetchCache.set(text, buf);
      }
    } catch (e) {
      console.warn("TTS prefetch error:", e);
    }
  }

  async processNext() {
    if (this.queue.length === 0) {
      this.isProcessing = false;
      if (typeof this.onQueueEmpty === "function") {
        this.onQueueEmpty();
      }
      return;
    }

    this.isProcessing = true;
    const item = this.queue.shift();
    const isFirst = (item.idx === 0);
    const isUrdu = ARABIC_SCRIPT_REGEX.test(item.text);

    // Halt any active filler cleanly the instant the first real sentence arrives
    stopFillerAudio();

    // Browser speechSynthesis for first sentence if voice for that language exists
    const canUseBrowserSpeech = isFirst && (
      (!isUrdu && this.localEnVoice) || (isUrdu && this.localUrVoice)
    );

    if (canUseBrowserSpeech && window.speechSynthesis) {
      try {
        if (this.queue.length > 0) {
          this.prefetch(this.queue[0].text);
        }
        await new Promise((resolve) => {
          const utter = new SpeechSynthesisUtterance(item.text);
          utter.voice = isUrdu ? this.localUrVoice : this.localEnVoice;
          utter.lang = isUrdu ? "ur-PK" : "en-US";
          utter.rate = 1.05;
          utter.onend = resolve;
          utter.onerror = resolve;
          window.speechSynthesis.speak(utter);
        });
        this.processNext();
        return;
      } catch (err) {
        console.warn("speechSynthesis error, using edge-tts:", err);
      }
    }

    // Neural audio via edge-tts & Web Audio API
    let buf = this.prefetchCache.get(item.text);
    if (!buf) {
      try {
        const res = await fetch("/api/tts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: item.text }),
        });
        if (res.ok) {
          buf = await res.arrayBuffer();
        }
      } catch (err) {
        console.warn("TTS fetch error:", err);
      }
    }

    if (this.queue.length > 0) {
      this.prefetch(this.queue[0].text);
    }

    if (buf && this.audioPlayer) {
      await this.audioPlayer.queueAudio(buf);
      this.processNext();
    } else {
      this.processNext();
    }
  }
}

// =====================================================================
// Phase 9: Filler / Handler Phrases System
// =====================================================================
const fillerState = {
  data: null,
  lastFiller: "",
  timer700ms: null,
  timer4s: null,
  timer10s: null,
  firstSentenceArrived: false,
};

async function loadFillerPhrases() {
  if (fillerState.data) return;
  try {
    const res = await fetch("/api/fillers");
    if (res.ok) {
      fillerState.data = await res.json();
    }
  } catch (e) {
    console.warn("Could not load /api/fillers:", e);
  }
}

function detectInputLanguage(text) {
  if (ARABIC_SCRIPT_REGEX.test(text)) return "ur";
  const romanUrduWords = ["mera", "meri", "karo", "kya", "hai", "hain", "kahan", "mujhe", "batao", "chahiye", "theek", "shukriya", "salam", "ap", "aap", "kitna", "kitne"];
  const lowerWords = (text || "").toLowerCase().split(/\s+/);
  if (lowerWords.some((w) => romanUrduWords.includes(w))) return "roman_ur";
  return "en";
}

function pickFillerPhrase(userText, isSecond = false) {
  const lang = detectInputLanguage(userText);
  const fillers = (fillerState.data && fillerState.data[lang]) || (fillerState.data && fillerState.data.en) || {
    short_ack: ["Sure, one moment.", "Okay, let me check.", "Just a second."],
    order_status: "Let me check your order status.",
    faq_products: "Let me look that up for you.",
    place_order: "Okay, let me note that down.",
    second_filler: "Thanks for waiting, almost there.",
    timeout: "Sorry, I am having trouble right now. Let me connect you with an agent.",
  };

  if (isSecond) {
    return fillers.second_filler || "Thanks for waiting, almost there.";
  }

  const clean = (userText || "").toLowerCase();
  if (clean.includes("order") || clean.includes("ord-") || clean.includes("track") || clean.includes("kahan")) {
    return fillers.order_status;
  }
  if (clean.includes("buy") || clean.includes("book") || clean.includes("piece") || clean.includes("quantity")) {
    return fillers.place_order;
  }
  if (clean.includes("price") || clean.includes("cost") || clean.includes("specs") || clean.includes("what is") || clean.includes("kya")) {
    return fillers.faq_products;
  }

  const list = Array.isArray(fillers.short_ack) ? fillers.short_ack : [fillers.short_ack];
  const candidates = list.filter((f) => f !== fillerState.lastFiller);
  const picked = candidates.length > 0 ? candidates[Math.floor(Math.random() * candidates.length)] : list[0];
  fillerState.lastFiller = picked;
  return picked;
}

function clearFillerTimers() {
  if (fillerState.timer700ms) { clearTimeout(fillerState.timer700ms); fillerState.timer700ms = null; }
  if (fillerState.timer4s) { clearTimeout(fillerState.timer4s); fillerState.timer4s = null; }
  if (fillerState.timer10s) { clearTimeout(fillerState.timer10s); fillerState.timer10s = null; }
}

function stopFillerAudio() {
  clearFillerTimers();
  cancelInstantFiller();
  if (state.currentAudio && state.fillerPlaying) {
    try { state.currentAudio.pause(); } catch (_) {}
    state.currentAudio = null;
    state.fillerPlaying = false;
  }
}

function speakInstantFiller(phrase) {
  if (!state.isVoiceEnabled || !phrase) return;
  if (typeof window !== "undefined" && window.speechSynthesis) {
    try {
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(phrase);
      const isUrdu = ARABIC_SCRIPT_REGEX.test(phrase);
      if (isUrdu && state.speechQueue && state.speechQueue.localUrVoice) {
        utter.voice = state.speechQueue.localUrVoice;
      } else if (!isUrdu && state.speechQueue && state.speechQueue.localEnVoice) {
        utter.voice = state.speechQueue.localEnVoice;
      }
      utter.lang = isUrdu ? "ur-PK" : "en-US";
      utter.rate = 1.05;
      utter.pitch = 1.0;
      state.isSpeakingFiller = true;
      state.fillerPlaying = true;
      utter.onend = () => { state.isSpeakingFiller = false; state.fillerPlaying = false; };
      utter.onerror = () => { state.isSpeakingFiller = false; state.fillerPlaying = false; };
      window.speechSynthesis.speak(utter);
    } catch (e) {
      console.warn("SpeechSynthesis filler error:", e);
    }
  }
}

function cancelInstantFiller() {
  if (typeof window !== "undefined" && window.speechSynthesis && state.isSpeakingFiller) {
    try {
      window.speechSynthesis.cancel();
    } catch (_) {}
    state.isSpeakingFiller = false;
    state.fillerPlaying = false;
  }
}

function bargeIn() {
  stopAudio();
  stopFillerAudio();
  if (state.speechQueue) {
    state.speechQueue.reset();
  }
  if (state.activeStreamController) {
    try {
      state.activeStreamController.abort();
    } catch (_) {}
    state.activeStreamController = null;
  }
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({ type: "interrupt" }));
  }
  setBusy(false);
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
  cancelInstantFiller();
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

async function ensureAiWebSocket() {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    return state.ws;
  }
  const ws = initAiWebSocket();
  if (!ws) return null;
  if (ws.readyState === WebSocket.OPEN) return ws;

  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(ws.readyState === WebSocket.OPEN ? ws : null), 1200);
    ws.addEventListener("open", () => {
      clearTimeout(timer);
      resolve(ws);
    }, { once: true });
    ws.addEventListener("error", () => {
      clearTimeout(timer);
      resolve(null);
    }, { once: true });
  });
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
  bargeIn();

  // Check if caller wants to finish the call via voice command
  if (isEndCallPhrase(text)) {
    setBusy(true);
    const farewell = "Thank you for calling DigiBuzz Support. Have a wonderful day! Goodbye.";
    addBotMessage(farewell);
    
    if (state.isVoiceEnabled) {
      await playAudio(farewell);
    }
    setBusy(false);
    endCall();
    return;
  }

  setBusy(true);
  setMicLocked(true);

  // Setup Phase 9 filler timers (700ms short ack, 4s waiting, 10s timeout)
  fillerState.firstSentenceArrived = false;
  clearFillerTimers();

  fillerState.timer700ms = setTimeout(() => {
    if (!fillerState.firstSentenceArrived && state.isOnCall && state.isBusy) {
      const phrase = pickFillerPhrase(text, false);
      speakInstantFiller(phrase);
    }
  }, 700);

  fillerState.timer4s = setTimeout(() => {
    if (!fillerState.firstSentenceArrived && state.isOnCall && state.isBusy) {
      const phrase = pickFillerPhrase(text, true);
      speakInstantFiller(phrase);
    }
  }, 4000);

  fillerState.timer10s = setTimeout(async () => {
    if (!fillerState.firstSentenceArrived && state.isOnCall && state.isBusy) {
      stopFillerAudio();
      if (state.activeStreamController) {
        state.activeStreamController.abort();
      }
      const apology = "Sorry, I am having trouble right now. Let me connect you with an agent.";
      addBotMessage(apology);
      if (state.isVoiceEnabled) {
        await playAudio(apology);
      }
      setBusy(false);
      setMicLocked(false);
      autoStartListening();

      // Register handoff request
      fetch("/api/handoff", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: state.sessionId,
          trigger_type: "low_confidence",
          reason: "10s latency timeout on voice stream",
        }),
      }).catch(() => {});
    }
  }, 10000);

  // Create streaming bot message bubble
  state.currentBotBubble = createStreamingBotMessage();

  // Stream via SSE /api/chat/stream
  await sendStreamingMessage(text);
}

async function sendStreamingMessage(text) {
  const controller = new AbortController();
  state.activeStreamController = controller;

  if (state.speechQueue) {
    state.speechQueue.reset();
  }

  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ session_id: state.sessionId, message: text }),
      signal: controller.signal,
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let fullTextAccumulator = "";
    let metaData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop(); // keep partial trailing line

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const jsonStr = trimmed.slice(5).trim();
        if (!jsonStr) continue;

        try {
          const event = JSON.parse(jsonStr);
          if (event.type === "sentence") {
            // First sentence arrived! Halt all fillers cleanly
            if (!fillerState.firstSentenceArrived) {
              fillerState.firstSentenceArrived = true;
              clearFillerTimers();
              stopFillerAudio();
            }

            fullTextAccumulator += (fullTextAccumulator ? " " : "") + event.text;
            updateStreamingBotMessage(state.currentBotBubble, fullTextAccumulator, false);

            if (state.isVoiceEnabled && state.speechQueue) {
              state.speechQueue.enqueue(event.text, event.idx);
            }
          } else if (event.type === "meta") {
            metaData = event;
          } else if (event.type === "done") {
            updateStreamingBotMessage(state.currentBotBubble, fullTextAccumulator, true);
          }
        } catch (parseErr) {
          console.warn("SSE parse error:", parseErr, trimmed);
        }
      }
    }

    clearFillerTimers();
    stopFillerAudio();

    if (metaData) {
      recordClientCallTurn(
        state.sessionId,
        text,
        fullTextAccumulator,
        metaData.intent || "general_inquiry",
        metaData.outcome || "Resolved"
      );
    }

    // Wait until speech queue finishes before allowing mic input
    if (state.speechQueue && state.speechQueue.isProcessing) {
      state.speechQueue.onQueueEmpty = () => {
        setBusy(false);
        setMicLocked(false);
        autoStartListening();
      };
    } else {
      setBusy(false);
      setMicLocked(false);
      autoStartListening();
    }
  } catch (err) {
    if (err.name === "AbortError") {
      console.log("Streaming aborted by barge-in or caller action.");
      return;
    }
    console.warn("SSE stream failed, falling back to /api/chat:", err);
    await fallbackHttpChat(text);
  }
}

async function fallbackHttpChat(text) {
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ session_id: state.sessionId, message: text }),
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    clearFillerTimers();
    stopFillerAudio();
    if (state.currentBotBubble) {
      updateStreamingBotMessage(state.currentBotBubble, data.reply, true);
    } else {
      addBotMessage(data.reply);
    }
    recordClientCallTurn(state.sessionId, text, data.reply, data.intent, data.outcome);
    if (state.isVoiceEnabled) {
      await playAudio(data.speech || data.reply);
    } else {
      setBusy(false);
      setMicLocked(false);
      autoStartListening();
    }
  } catch (e) {
    setBusy(false);
    setMicLocked(false);
    clearFillerTimers();
    stopFillerAudio();
    addBotMessage("Sorry, I encountered an error. Please try again.");
    autoStartListening();
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
// Speech Recognition (Mic Button) & Half-Duplex Lock with Barge-In
// =====================================================================
let speechSilenceTimer = null;
let currentSpeechTranscript = "";

function initSpeechRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    $("btnMic").title = "Speech recognition not supported in this browser";
    return;
  }

  const recog = new SpeechRecognition();
  recog.lang = "en-US";
  recog.continuous = true;
  recog.interimResults = true;
  recog.maxAlternatives = 1;

  recog.onresult = (e) => {
    let interim = "";
    let finalTranscript = "";

    for (let i = e.resultIndex; i < e.results.length; ++i) {
      if (e.results[i].isFinal) {
        finalTranscript += e.results[i][0].transcript;
      } else {
        interim += e.results[i][0].transcript;
      }
    }

    const spokenText = (finalTranscript || interim).trim();
    if (!spokenText) return;

    // Phase 9 Barge-in: if AI agent is speaking or streaming, immediately halt all audio!
    if (state.isBusy || (state.audioPlayer && state.audioPlayer.isPlaying) || state.currentAudio || state.fillerPlaying || state.isSpeakingFiller) {
      if (spokenText.length >= 2) {
        bargeIn();
      }
    }

    $("chatInput").value = spokenText;
    currentSpeechTranscript = spokenText;

    // Turn-taking: wait ~800ms of silence before sending the finalized transcript
    if (speechSilenceTimer) {
      clearTimeout(speechSilenceTimer);
      speechSilenceTimer = null;
    }

    speechSilenceTimer = setTimeout(() => {
      const textToSend = currentSpeechTranscript.trim();
      currentSpeechTranscript = "";
      if (textToSend && state.isOnCall && !state.isBusy) {
        sendMessage(textToSend);
      }
    }, 800);
  };

  recog.onstart = () => {
    state.isListening = true;
    $("btnMic").classList.add("listening");
    $("btnMic").title = "Listening to you live…";
  };

  recog.onend = () => {
    state.isListening = false;
    $("btnMic").classList.remove("listening");
    $("btnMic").title = "Click to speak";
    // Keep continuous listening alive while on active call if AI is not speaking
    if (state.isOnCall && !state.isBusy && !state.currentAudio && !(state.audioPlayer && state.audioPlayer.isPlaying)) {
      setTimeout(() => {
        if (state.isOnCall && !state.isBusy && !state.isListening) {
          try { recog.start(); } catch (_) {}
        }
      }, 200);
    }
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
    if (!state.isOnCall) return;
    
    // Ensure permission is granted
    if (!state.micPermissionRequested) {
      await requestMicPermission();
    }

    if (state.isListening) {
      try { recog.stop(); } catch (_) {}
    } else {
      bargeIn();
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
    mic.title = "Agent is speaking... Speak to barge-in";
  } else {
    mic.disabled = !state.isOnCall || state.isBusy;
    mic.title = state.isListening ? "Listening to you live…" : "Click to speak";
  }
}

// Automatically turn ON microphone when the AI agent completes speaking
function autoStartListening() {
  if (!state.isOnCall || state.isBusy || !state.recognition) return;
  if (state.audioPlayer && state.audioPlayer.isPlaying) return;
  if (state.speechQueue && state.speechQueue.isProcessing) return;

  setTimeout(() => {
    if (!state.isOnCall || state.isBusy || state.currentAudio || state.isListening) return;
    if (state.audioPlayer && state.audioPlayer.isPlaying) return;
    try {
      state.recognition.start();
    } catch (_) {}
  }, 200);
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
  initAiWebSocket(); // Pre-connect WebSocket so it is OPEN and waiting before the first call!

  // Resume / Unlock AudioContext on the very first user gesture anywhere on page
  window.addEventListener("click", () => {
    if (state.audioPlayer) {
      state.audioPlayer.ensureContext();
    }
  }, { once: true });

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
