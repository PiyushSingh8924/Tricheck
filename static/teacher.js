// Teacher dashboard pages. Each page has its own set of element ids; only the matching block below runs.
const get = u => fetch(u).then(r => { if (r.status === 401) { location = "/teacher/login"; throw 0; } return r.json(); });
const fdate = t => new Date(t * 1000).toLocaleString([], {day: "numeric", month: "short", hour: "numeric", minute: "2-digit"});
const ftime = t => new Date(t * 1000).toLocaleTimeString([], {hour: "numeric", minute: "2-digit", second: "2-digit"});
const mmss = s => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const title = s => esc(s.subject || s.class_name);
const where = s => s.department ? `${esc(s.department)}, ${esc(s.class_name)}, Section ${esc(s.section)}` : esc(s.class_name);
const none = (cols, t) => `<tr><td colspan="${cols}" class="empty">${t}</td></tr>`;
const tag = s => `<span class="tag ${s.live ? "live" : "done"}">${s.live ? "Live" : "Ended"}</span>`;
const sessionRow = s => `<tr><td>${fdate(s.start)} ${tag(s)}</td><td>${title(s)}</td><td>${where(s)}</td>
  <td><b>${s.present}</b>${s.expected ? " / " + s.expected : ""}</td><td class="act"><a href="/teacher/session/${s.id}">View</a> <a href="/api/csv/${s.id}">CSV</a></td></tr>`;
const people = (list, time) => list.map(r => `<tr><td>${esc(r.reg_no)}</td><td>${esc(r.name)}</td>${time ? `<td>${ftime(r.marked_at)}</td>` : ""}</tr>`).join("");
const debounce = (fn, ms = 250) => { let t; return () => { clearTimeout(t); t = setTimeout(fn, ms); }; };
const filters = () => new URLSearchParams(["department", "class_name", "section", "q"].map(k => [k, $("f_" + k) ? $("f_" + k).value : ""]).filter(p => p[1])).toString();
const watchFilters = fn => document.querySelectorAll("[id^=f_]").forEach(el => el.addEventListener(el.tagName === "INPUT" ? "input" : "change", el.tagName === "INPUT" ? debounce(fn) : fn));

// ---- start session ----
async function startSession() {
  const v = id => $(id).value;
  const r = await post("/api/session/start", {department: v("department"), class_name: v("class_name"), section: v("section"), subject: v("subject"),
    word: v("word"), window: v("win"), ble_name: v("ble_name"), need_ble: $("need_ble").checked});
  say(r.ok ? "Session started. Opening live view..." : r.error, r.ok);
  if (r.ok) setTimeout(() => location = "/teacher/live", 700);
}
if ($("need_ble")) $("need_ble").onchange = () => { $("ble_name").style.display = $("need_ble").checked ? "" : "none"; };

// ---- live session ----
let liveId = null, left = 0, tick;
async function endSession() { const r = await post("/api/session/end"); say(r.ok ? "Session ended" : r.error, r.ok); pollLive(); }
function csv() { if (liveId) location = "/api/csv/" + liveId; }
function showSession(l) {   // shared by the live page and the past-session page
  $("status").textContent = l.live ? "Live now" : "Ended"; $("status").dataset.live = l.live ? 1 : 0;
  $("title").textContent = l.subject || l.class_name;
  $("meta").textContent = `${l.department}, ${l.class_name}, Section ${l.section}` + (l.start ? " - " + fdate(l.start) : "");
  $("count").textContent = l.rows.length; $("left").textContent = l.absent.length;
  $("live").innerHTML = l.rows.length ? people(l.rows, true) : none(3, "Nobody has marked attendance yet.");
  $("absent").innerHTML = l.absent.length ? people(l.absent) : none(2, l.expected ? "Everyone is present." : "No registered students for this class yet.");
}
async function pollLive() {
  let l; try { l = await get("/api/live"); } catch (e) { return; }
  liveId = l.id; $("csvbtn").hidden = !l.id; $("endbtn").hidden = !l.live;
  if (!l.id) {
    $("status").textContent = "No session yet"; $("status").dataset.live = 0; $("title").textContent = "Nothing running";
    $("meta").innerHTML = 'Go to <a href="/teacher/session/new">Start session</a> to begin.'; return;
  }
  showSession(l); left = l.remaining; $("timer").textContent = l.live ? mmss(left) : "-";
}
if (document.body.dataset.page === "live") {
  pollLive(); setInterval(pollLive, 3000);
  tick = setInterval(() => { if (left > 0 && $("endbtn") && !$("endbtn").hidden) $("timer").textContent = mmss(--left); }, 1000);
}

// ---- one past session ----
if (document.body.dataset.sid) get("/api/teacher/session/" + document.body.dataset.sid).then(l => {
  showSession(l); const tot = l.rows.length + l.absent.length;
  $("pct").textContent = tot ? Math.round(100 * l.rows.length / tot) + "%" : "-";
});

// ---- overview ----
if (document.body.dataset.page === "overview") get("/api/teacher/overview").then(o => {
  $("s_held").textContent = o.held; $("s_today").textContent = o.today; $("s_students").textContent = o.students;
  $("s_avg").textContent = o.average === null ? "-" : o.average + "%";
  $("recent").innerHTML = o.recent.length ? o.recent.map(sessionRow).join("") : none(5, "No sessions yet. Start one to see it here.");
});

// ---- history ----
async function loadHistory() {
  const d = await get("/api/teacher/history?" + filters());
  $("hist").innerHTML = d.sessions.length ? d.sessions.map(sessionRow).join("") : none(5, "No sessions match.");
}
if (document.body.dataset.page === "history" && $("hist")) { loadHistory(); watchFilters(loadHistory); }

// ---- students ----
async function loadStudents() {
  const d = await get("/api/teacher/students?" + filters());
  $("stu").innerHTML = d.students.length ? d.students.map(s => {
    const pct = s.total ? Math.round(100 * s.attended / s.total) : null, low = pct !== null && pct < 75;
    return `<tr><td>${esc(s.reg_no)}</td><td>${esc(s.name)}</td><td>${s.department ? esc(s.class_name) + ", Section " + esc(s.section) : "-"}</td>
      <td>${s.attended} / ${s.total}</td><td>${pct === null ? "-" : `<div class="pct ${low ? "low" : ""}"><span class="bar"><i style="width:${pct}%"></i></span><b>${pct}%</b>${low ? " Low" : ""}</div>`}</td></tr>`;
  }).join("") : none(5, "No students match.");
}
if (document.body.dataset.page === "students") { loadStudents(); watchFilters(loadStudents); }
