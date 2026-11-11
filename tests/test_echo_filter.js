// Unit test for Phase 9B Self-Transcript Filter & Echo Overlap Detection
const assert = require("assert");

function normalizeTextForEcho(text) {
  if (!text) return "";
  let s = text.toLowerCase();
  s = s.replace(/[\.,\/#!$%\^&\*;:{}=\-_`~()؟۔،!"'‘’?]/g, " ");
  s = s.replace(/[\u0640]/g, "");
  s = s.replace(/[يى]/g, "ی");
  s = s.replace(/[ك]/g, "ک");
  s = s.replace(/[ؤ]/g, "و");
  s = s.replace(/[آإأ]/g, "ا");
  s = s.replace(/\s+/g, " ").trim();
  return s;
}

function computeTokenJaccard(s1, s2) {
  const tokens1 = new Set(s1.split(/\s+/).filter(Boolean));
  const tokens2 = new Set(s2.split(/\s+/).filter(Boolean));
  if (tokens1.size === 0 || tokens2.size === 0) return 0;
  let intersection = 0;
  for (const t of tokens1) {
    if (tokens2.has(t)) intersection++;
  }
  const union = new Set([...tokens1, ...tokens2]).size;
  return union > 0 ? intersection / union : 0;
}

function computeTokenOverlap(userText, agentText) {
  const normUser = normalizeTextForEcho(userText);
  const normAgent = normalizeTextForEcho(agentText);
  if (!normUser || !normAgent) return 0;

  if (normAgent.includes(normUser) && normUser.length >= 6) return 1.0;
  if (normUser.includes(normAgent) && normAgent.length >= 6) return 1.0;

  const userTokens = normUser.split(/\s+/).filter(Boolean);
  const agentTokens = new Set(normAgent.split(/\s+/).filter(Boolean));
  if (userTokens.length === 0 || agentTokens.size === 0) return 0;

  let common = 0;
  for (const t of userTokens) {
    if (agentTokens.has(t)) common++;
  }
  const userOverlapRatio = common / userTokens.length;
  const jaccard = computeTokenJaccard(normUser, normAgent);
  return Math.max(userOverlapRatio, jaccard);
}

const KNOWN_FILLER_PHRASES = [
  "let me check",
  "let me check that for you",
  "one moment",
  "one moment please",
  "sure one moment",
  "just a second",
  "thanks for waiting",
  "almost there",
  "hold on",
  "please hold",
  "okay let me check",
  "جی ایک سیکنڈ",
  "جی میں دیکھتی ہوں",
  "ایک لمحہ دیجیے",
  "بس تھوڑا سا انتظار",
  "jee aik second",
  "main check karti hoon",
  "aik second hold kijiye",
];

function isEcho(userText, agentText) {
  const normUser = normalizeTextForEcho(userText);
  for (const filler of KNOWN_FILLER_PHRASES) {
    const normFiller = normalizeTextForEcho(filler);
    if (normUser === normFiller || normUser.includes(normFiller) || normFiller.includes(normUser)) {
      return true;
    }
  }
  return computeTokenOverlap(userText, agentText) >= 0.60;
}

// 1. English self-echo tests
console.log("--- Running Echo Filter Unit Tests ---");

const en1 = isEcho("let me check that for you", "Sure, one moment. Let me check that for you.");
assert.strictEqual(en1, true, "English filler phrase should be detected as echo");
console.log("[PASS] English filler phrase detected as echo");

const en2 = isEcho("order ORD 1001 is currently shipped", "Your order ORD-1001 is currently Shipped. Total amount is PKR 4,500.");
assert.strictEqual(en2, true, "English agent sentence echo should be detected");
console.log("[PASS] English agent sentence echo detected");

// 2. Urdu self-echo tests
const ur1 = isEcho("ایک سیکنڈ میں معلومات دیکھتی ہوں", "جی ایک سیکنڈ، میں معلومات دیکھتی ہوں۔");
assert.strictEqual(ur1, true, "Urdu agent sentence echo should be detected");
console.log("[PASS] Urdu agent sentence echo detected");

const urFiller = isEcho("جی ایک سیکنڈ", "کچھ بھی");
assert.strictEqual(urFiller, true, "Urdu filler should be detected");
console.log("[PASS] Urdu filler detected");

// 3. Roman Urdu self-echo tests
const ro1 = isEcho("main check karti hoon", "Jee main check karti hoon.");
assert.strictEqual(ro1, true, "Roman Urdu echo should be detected");
console.log("[PASS] Roman Urdu echo detected");

// 4. Legitimate user requests (must NOT be discarded!)
const leg1 = isEcho("Can you tell me about the Dell XPS laptop specifications?", "Hello! Welcome to our AI calling service. How may I assist you?");
assert.strictEqual(leg1, false, "Legitimate inquiry should NOT be detected as echo");
console.log("[PASS] Legitimate product inquiry NOT discarded");

const leg2 = isEcho("I want to buy 10 Lenovo tablets", "We have multiple Lenovo tablets in stock.");
assert.strictEqual(leg2, false, "Legitimate order intent should NOT be detected as echo");
console.log("[PASS] Legitimate order intent NOT discarded");

console.log("\nALL ECHO FILTER UNIT TESTS PASSED SUCCESSFULLY!");
