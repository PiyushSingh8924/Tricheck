const MODELS = "https://cdn.jsdelivr.net/npm/@vladmandic/face-api@1.7.12/model/";
const $ = id => document.getElementById(id);
const post = (u, b = {}) => fetch(u, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(b)}).then(r => r.json());
const esc = t => String(t).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const say = (t, ok) => { const m = $("msg"); m.textContent = t; m.className = ok ? "ok" : "bad"; };
let sessions = [], bleName = "";

// generic login / teacher-registration form
const form = $("auth");
if (form) form.onsubmit = async e => {
  e.preventDefault();
  const r = await post(form.dataset.api, Object.fromEntries(new FormData(form)));
  if (r.ok) { say("Success", true); location = form.dataset.next; } else say(r.error, false);
};

// camera + face (student register / attend pages)
async function face() {
  const box = $("cam").parentElement; box.classList.add("scanning");
  try {
    const r = await faceapi.detectSingleFace($("cam"), new faceapi.TinyFaceDetectorOptions()).withFaceLandmarks().withFaceDescriptor();
    if (!r) throw "No face detected - look at the camera";
    return Array.from(r.descriptor);
  } finally { box.classList.remove("scanning"); }
}
async function initCam() {
  say("Loading face models...", true);
  await Promise.all([faceapi.nets.tinyFaceDetector, faceapi.nets.faceLandmark68Net, faceapi.nets.faceRecognitionNet].map(n => n.loadFromUri(MODELS)));
  $("cam").srcObject = await navigator.mediaDevices.getUserMedia({video: true});
  say("Ready", true);
  if ($("status")) { pollSession(); setInterval(pollSession, 5000); }
}
if ($("cam")) initCam().catch(e => say("Camera/model error: " + e, false));

async function registerStudent() {
  try {
    const r = await post("/api/student/register", {reg_no: $("reg_no").value, name: $("name").value, password: $("password").value, descriptor: await face()});
    if (r.ok) { say("Registered! Redirecting to login...", true); setTimeout(() => location = "/student/login", 1200); } else say(r.error, false);
  } catch (e) { say(e, false); }
}

// attend page
async function pollSession() {
  try { sessions = (await fetch("/api/session").then(r => r.json())).sessions; } catch (e) { return; }
  if ($("ble-step")) $("ble-step").style.display = sessions.length && !sessions[0].need_ble ? "none" : "";
  $("status").textContent = sessions.length ? `${sessions[0].class_name} - ${sessions[0].remaining}s left` : "No active session";
  $("status").dataset.live = sessions.length ? 1 : 0;
}
async function pairBle() {
  if (!sessions.length) return say("No active session", false);
  try {  // chooser lists only devices actually in Bluetooth range
    const d = await navigator.bluetooth.requestDevice({filters: sessions.map(s => ({namePrefix: s.ble_name}))});
    bleName = d.name; $("ble").textContent = "In range: " + d.name;
  } catch (e) { say("Teacher's Bluetooth not found nearby", false); }
}
async function mark() {
  try {
    const r = await post("/api/attend", {word: $("word").value, ble_name: bleName, descriptor: await face()});
    say(r.ok ? `Attendance marked for ${r.name}` : r.error, r.ok);
  } catch (e) { say(e, false); }
}

// teacher dashboard
async function startSession() {
  const r = await post("/api/session/start", {class_name: $("class_name").value, ble_name: $("ble_name").value, word: $("word").value, window: $("win").value, need_ble: $("need_ble").checked});
  say(r.ok ? "Session started" : r.error, r.ok);
}
async function endSession() { const r = await post("/api/session/end"); say(r.ok ? "Session ended" : r.error, r.ok); }
let liveId = null;
async function pollLive() {
  let l; try { l = await fetch("/api/live").then(r => r.json()); } catch (e) { return; }
  if (l.error) return;
  liveId = l.id;
  $("status").textContent = l.id ? `${l.class_name} - ${l.active ? l.remaining + "s left" : "ended"}` : "No session yet";
  $("count").textContent = l.rows.length;
  $("status").dataset.live = l.active ? 1 : 0;
  $("live").innerHTML = !l.rows.length ? '<tr><td colspan="3" class="empty">Nobody has marked attendance yet.</td></tr>' : l.rows.map(r => `<tr><td>${esc(r.reg_no)}</td><td>${esc(r.name)}</td><td>${new Date(r.marked_at * 1000).toLocaleTimeString()}</td></tr>`).join("");
}
function csv() { if (liveId) location = "/api/csv/" + liveId; }
if ($("live")) { pollLive(); setInterval(pollLive, 3000); }
