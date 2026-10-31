const MODELS = "https://cdn.jsdelivr.net/npm/@vladmandic/face-api@1.7.12/model/";
const $ = id => document.getElementById(id);
const post = (u, b = {}) => fetch(u, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(b)}).then(r => r.json());
const esc = t => String(t).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
let msgTimer;
const say = (t, ok) => {
  const m = $("msg"); if (!m) return;
  m.textContent = t && t.message ? t.message : t; m.className = ok ? "ok" : "bad";
  clearTimeout(msgTimer);   // on the attendance page the message floats over the page, so let it go away by itself
  if (document.body.classList.contains("toast") && t) msgTimer = setTimeout(() => { m.textContent = ""; }, 8000);
};
const chip = t => { const c = $("camchip"); if (c) c.textContent = t; };
let sessions = [], bleName = "";

// generic login / registration / reset form
const form = $("auth");
if (form) form.onsubmit = async e => {
  e.preventDefault();
  const r = await post(form.dataset.api, Object.fromEntries(new FormData(form)));
  if (r.ok) { say(form.dataset.done || "Success", true); setTimeout(() => location = form.dataset.next, 900); } else say(r.error, false);
};

// camera + face (student register / forgot password / attend pages)
async function face() {
  const box = $("cam").parentElement; box.classList.add("scanning"); chip("Scanning... hold still");
  try {
    const r = await faceapi.detectSingleFace($("cam"), new faceapi.TinyFaceDetectorOptions()).withFaceLandmarks().withFaceDescriptor();
    if (!r) throw "No face detected - look at the camera";
    return Array.from(r.descriptor);
  } finally { box.classList.remove("scanning"); chip("Camera ready"); }
}
async function initCam() {
  chip("Loading face models...");
  await Promise.all([faceapi.nets.tinyFaceDetector, faceapi.nets.faceLandmark68Net, faceapi.nets.faceRecognitionNet].map(n => n.loadFromUri(MODELS)));
  chip("Starting camera...");
  // facingMode "user" = the front (selfie) camera; without it many phones open the rear camera
  const v = $("cam");
  v.srcObject = await navigator.mediaDevices.getUserMedia({video: {facingMode: "user", width: {ideal: 640}, height: {ideal: 480}}, audio: false});
  try { await v.play(); } catch (e) {}
  chip("Camera ready");
  if ($("status")) { pollSession(); setInterval(pollSession, 5000); }
}
if ($("cam")) {
  initCam().catch(e => { chip("Camera unavailable"); say("Camera/model error: " + (e && e.message || e), false); });
  addEventListener("pagehide", () => { const s = $("cam").srcObject; if (s) s.getTracks().forEach(t => t.stop()); });
}

// run a face-scan action with the button locked, and the keyboard closed so the camera stays in view
async function scanning(fn) {
  const b = $("go"); if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  if (b) b.disabled = true;
  try { await fn(); } catch (e) { say(e, false); } finally { if (b) b.disabled = false; }
}
const registerStudent = () => scanning(async () => {
  const pick = id => $(id).value;
  const r = await post("/api/student/register", {reg_no: pick("reg_no"), name: pick("name"), email: pick("email"), department: pick("department"),
    class_name: pick("class_name"), section: pick("section"), password: pick("password"), descriptor: await face()});
  if (r.ok) { say("Registered! Redirecting to login...", true); setTimeout(() => location = "/student/login", 1200); } else say(r.error, false);
});
const resetStudent = () => scanning(async () => {
  const r = await post("/api/student/reset", {reg_no: $("reg_no").value, password: $("password").value, descriptor: await face()});
  if (r.ok) { say("Password changed! Redirecting to login...", true); setTimeout(() => location = "/student/login", 1200); } else say(r.error, false);
});

// attend page
async function pollSession() {
  try { sessions = (await fetch("/api/session").then(r => r.json())).sessions; } catch (e) { return; }
  if ($("ble-step")) $("ble-step").style.display = sessions.length && !sessions[0].need_ble ? "none" : "";
  const s = sessions[0];
  $("status").textContent = s ? `${s.subject || s.class_name} - ${Math.max(0, s.remaining)}s left` : "No active session for your class";
  $("status").dataset.live = s ? 1 : 0;
}
async function pairBle() {
  if (!sessions.length) return say("No active session", false);
  try {  // chooser lists only devices actually in Bluetooth range
    const d = await navigator.bluetooth.requestDevice({filters: sessions.map(s => ({namePrefix: s.ble_name}))});
    bleName = d.name; $("ble").textContent = "In range: " + d.name;
  } catch (e) { say("Teacher's Bluetooth not found nearby", false); }
}
const mark = () => scanning(async () => {
  const r = await post("/api/attend", {word: $("word").value, ble_name: bleName, descriptor: await face()});
  say(r.ok ? `Attendance marked for ${r.name}` : r.error, r.ok);
});
