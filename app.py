import csv, hmac, hashlib, io, json, math, os, re, secrets, sqlite3, time
from email.message import EmailMessage
from datetime import timedelta
from functools import wraps
from flask import Flask, g, jsonify, redirect, render_template, request, session, Response
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash as gen, check_password_hash as chk

# ---------- configuration (all secrets come from environment variables) ----------
ON_HOST = bool(os.environ.get("RENDER"))                      # Render sets this automatically
SECRET = os.environ.get("SECRET_KEY")
if not SECRET:
    if ON_HOST: raise RuntimeError("Set the SECRET_KEY environment variable")
    SECRET = secrets.token_hex(32)                            # local run only: sessions reset on restart
TEACHER_CODE = os.environ.get("TEACHER_CODE", "")             # optional: required to register as a teacher
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"): DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
PG = DATABASE_URL.startswith("postgresql")                    # hosted Postgres if set, otherwise local SQLite file
DB = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "attendance.db"))
MATCH = 0.5  # max face distance to accept

# ---------- college email (exact domains only; edit here to change) ----------
STUDENT_DOMAINS, TEACHER_DOMAINS = ("muj.manipal.edu",), ("muj.manipal.edu", "jaipur.manipal.edu")

# ---------- dropdown choices (edit these lists to change what students and teachers can pick) ----------
DEPARTMENTS = [
    "Computer Science & Engineering", "Information Technology", "Artificial Intelligence & Data Science",
    "Electronics & Communication Engineering", "Electrical & Electronics Engineering", "Mechanical Engineering",
    "Civil Engineering", "Chemical Engineering", "Automobile Engineering", "Mechatronics Engineering",
    "Architecture & Planning", "Biotechnology", "Basic Sciences", "Computer Applications (BCA / MCA)",
    "Management (BBA / MBA)", "Commerce", "Law", "Design", "Journalism & Mass Communication", "Humanities & Social Sciences"]
CLASSES = [f"Semester {i}" for i in range(1, 11)]
SECTIONS = list("ABCDEFGHIJKL")

app = Flask(__name__)
app.secret_key = SECRET
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # correct https/host behind Render, ngrok, cloudflared
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                  SESSION_COOKIE_SECURE=ON_HOST or bool(os.environ.get("HTTPS")),
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=12))

if PG:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=int(os.environ.get("DB_POOL", 6)), open=True,
                          check=ConnectionPool.check_connection, kwargs={"autocommit": True, "row_factory": dict_row})
INTEGRITY = (sqlite3.IntegrityError,) + ((psycopg.IntegrityError,) if PG else ())

class Conn:
    """Same db().execute(...) calls for SQLite and Postgres (queries are written with ? placeholders)."""
    def __init__(self, raw): self.raw = raw
    def execute(self, sql, args=()):
        return self.raw.execute(sql.replace("?", "%s") if PG else sql, args)
    def commit(self):
        if not PG: self.raw.commit()

def db():
    if "db" not in g:
        if PG: raw = pool.getconn()
        else: raw = sqlite3.connect(DB, timeout=15); raw.row_factory = sqlite3.Row
        g.db = Conn(raw)
    return g.db

@app.teardown_appcontext
def close(_):
    d = g.pop("db", None)
    if d:
        if PG: pool.putconn(d.raw)
        else: d.raw.close()

NEW_COLS = [("students", "department", "TEXT"), ("students", "class_name", "TEXT"), ("students", "section", "TEXT"),
            ("sessions", "need_ble", "INTEGER DEFAULT 1"), ("sessions", "department", "TEXT"),
            ("sessions", "section", "TEXT"), ("sessions", "subject", "TEXT"),
            ("students", "email", "TEXT"), ("students", "email_verified", "INTEGER DEFAULT 0")]

def init():
    pk, real = ("SERIAL PRIMARY KEY", "DOUBLE PRECISION") if PG else ("INTEGER PRIMARY KEY AUTOINCREMENT", "REAL")
    stmts = [
        "CREATE TABLE IF NOT EXISTS students(reg_no TEXT PRIMARY KEY, name TEXT, pw TEXT, descriptor TEXT)",
        f"CREATE TABLE IF NOT EXISTS teachers(id {pk}, name TEXT, email TEXT UNIQUE, pw TEXT)",
        f'CREATE TABLE IF NOT EXISTS sessions(id {pk}, teacher_id INTEGER, class_name TEXT, word TEXT, ble_name TEXT, start {real}, "window" INTEGER, active INTEGER DEFAULT 1)',
        f"CREATE TABLE IF NOT EXISTS attendance(session_id INTEGER, reg_no TEXT, marked_at {real}, UNIQUE(session_id, reg_no))"]
    after = ["CREATE UNIQUE INDEX IF NOT EXISTS students_email_uq ON students(email)"]
    if PG:
        with pool.connection() as c:
            c.execute("SELECT pg_advisory_lock(42)")          # several workers may start together
            try:
                for s in stmts: c.execute(s)
                for t, col, ty in NEW_COLS: c.execute(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {col} {ty}")
                for q in after: c.execute(q)
            finally: c.execute("SELECT pg_advisory_unlock(42)")
    else:
        with sqlite3.connect(DB, timeout=15) as c:
            c.execute("PRAGMA journal_mode=WAL")               # readers and the one writer no longer block each other
            for s in stmts: c.execute(s)
            for t, col, ty in NEW_COLS:
                if col not in [r[1] for r in c.execute(f"PRAGMA table_info({t})")]:
                    c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {ty}")
            for q in after: c.execute(q)

def err(m, code=400): return jsonify(ok=False, error=m), code
def dist(a, b): return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
def body():
    d = request.get_json(silent=True); return d if isinstance(d, dict) else {}
def text(v, n=100): return str(v).strip()[:n] if isinstance(v, (str, int, float)) else ""
def face_ok(v):
    """A face signature is exactly 128 finite numbers; anything else is rejected."""
    if not isinstance(v, list) or len(v) != 128: return None
    try: v = [float(x) for x in v]
    except (TypeError, ValueError): return None
    return v if all(math.isfinite(x) and abs(x) < 10 for x in v) else None

def actives():  # read-only: a session is live while active=1 and its time window has not passed
    return db().execute('SELECT * FROM sessions WHERE active=1 AND start+"window">?', (time.time(),)).fetchall()

@app.errorhandler(500)
def oops(_):
    if request.path.startswith("/api/"): return err("Server error - please try again", 500)
    return "Server error", 500

@app.get("/healthz")
def healthz(): return "ok"

# ---------- small helpers ----------
def group(d):
    """(department, class, section) picked from the dropdowns; None if any value is not in the allowed lists."""
    g = (text(d.get("department")), text(d.get("class_name")), text(d.get("section")))
    return g if g[0] in DEPARTMENTS and g[1] in CLASSES and g[2] in SECTIONS else None

_tries = {}
def throttle(key, limit=5, per=600):
    """Allow `limit` attempts per `per` seconds for a key (kept in memory, per worker). Stops password guessing."""
    now = time.time(); hits = [t for t in _tries.get(key, []) if now - t < per]
    ok = len(hits) < limit
    if ok: hits.append(now)
    _tries[key] = hits
    if len(_tries) > 5000: _tries.clear()
    return ok
TOO_MANY = "Too many attempts - wait 10 minutes and try again"

def teacher_page(f):
    @wraps(f)
    def w(*a, **k):
        if "tid" not in session: return redirect("/teacher/login")
        return f(*a, **k)
    return w

def teacher_api(f):
    @wraps(f)
    def w(*a, **k):
        if "tid" not in session: return err("Please log in", 401)
        return f(*a, **k)
    return w

@app.context_processor
def choices(): return dict(DEPARTMENTS=DEPARTMENTS, CLASSES=CLASSES, SECTIONS=SECTIONS)

def muj_email(v, domains=STUDENT_DOMAINS):
    """Normalised college address, or None. Exact domain only: lookalikes and extra @ signs fail."""
    v = v.strip() if isinstance(v, str) else ""
    if len(v) > 100 or not v.isascii(): return None          # check before lower(): some odd Unicode letters lowercase into plain ASCII
    v = v.lower()
    return v if re.fullmatch(r"[a-z0-9._%+-]{1,64}@(?:" + "|".join(map(re.escape, domains)) + ")", v, re.ASCII) else None

def me():
    """The logged-in student."""
    if "reg_no" not in session: return None
    st = db().execute("SELECT * FROM students WHERE reg_no=?", (session["reg_no"],)).fetchone()
    return st

# ---------- pages ----------
AUTH = {
    "teacher_register": dict(title="Teacher Registration", api="/api/teacher/register", nxt="/teacher/login", done="Account created. Redirecting to login...",
        blurb="Create your teacher account to start sessions and see who attended.",
        fields=[("name", "Full name", "text"), ("email", "College email (@muj.manipal.edu)", "email"), ("password", "Password", "password"), ("code", "Teacher code", "password")],
        links=[("/teacher/login", "Already have an account? Log in")]),
    "teacher_login": dict(title="Teacher Login", api="/api/teacher/login", nxt="/teacher/dashboard", done="Success",
        blurb="Face, Bluetooth and a word said out loud. That's all it takes.",
        fields=[("email", "Email", "email"), ("password", "Password", "password")],
        links=[("/teacher/forgot", "Forgot password?"), ("/teacher/register", "Create an account")]),
    "teacher_forgot": dict(title="Reset Teacher Password", api="/api/teacher/reset", nxt="/teacher/login", done="Password changed. Redirecting to login...",
        blurb="Enter your email and the teacher access code to choose a new password.",
        fields=[("email", "Email", "email"), ("code", "Teacher access code", "password"), ("password", "New password (6+ characters)", "password")],
        links=[("/teacher/login", "Back to login")]),
    "student_login": dict(title="Student Login", api="/api/student/login", nxt="/student/attend", done="Success",
        blurb="Sign in with your college email. Face, Bluetooth and a word said out loud do the rest.",
        fields=[("email", "College email (@muj.manipal.edu)", "email"), ("password", "Password", "password")],
        links=[("/student/forgot", "Forgot password?"), ("/student/register", "Register for the first time")]),
}
def auth_page(k): return render_template("auth.html", **AUTH[k])
for k in AUTH:
    app.add_url_rule("/" + k.replace("_", "/"), k, lambda k=k: auth_page(k))

@app.get("/")
def home(): return render_template("home.html")

@app.get("/student/register")
def student_register(): return render_template("student_register.html")

@app.get("/student/forgot")
def student_forgot(): return render_template("student_forgot.html")

@app.get("/student/attend")
def student_attend():
    st = me()
    if not st: session.clear(); return redirect("/student/login")
    return render_template("attend.html", name=st["name"], st=st)

# teacher pages: each one is its own screen, all behind login
TPAGES = {"/teacher/dashboard": ("teacher_overview.html", "overview"), "/teacher/session/new": ("teacher_new.html", "new"),
          "/teacher/live": ("teacher_live.html", "live"), "/teacher/history": ("teacher_history.html", "history"),
          "/teacher/students": ("teacher_students.html", "students")}
for path, (tpl, key) in TPAGES.items():
    app.add_url_rule(path, "page_" + key, teacher_page(lambda tpl=tpl, key=key: render_template(tpl, name=session["tname"], page=key)))

@app.get("/teacher/session/<int:sid>")
@teacher_page
def t_session_page(sid):
    if not owned(sid): return redirect("/teacher/history")
    return render_template("teacher_session.html", name=session["tname"], page="history", sid=sid)

@app.get("/logout")
def logout():
    session.clear(); return redirect("/")

# ---------- teacher account ----------
@app.post("/api/teacher/register")
def t_register():
    d = body()
    email = muj_email(d.get("email"), TEACHER_DOMAINS)
    if not throttle(("t-reg", request.remote_addr), 10): return err(TOO_MANY, 429)
    if not (d.get("name") and d.get("password")): return err("All fields are required")
    if not email: return err("Use your college email (@muj.manipal.edu or @jaipur.manipal.edu)")
    if not TEACHER_CODE: return err("Teacher sign-up is closed until the admin sets TEACHER_CODE", 503)
    if not hmac.compare_digest(text(d.get("code")).encode(), TEACHER_CODE.encode()): return err("Wrong teacher code", 403)
    try:
        db().execute("INSERT INTO teachers(name,email,pw) VALUES(?,?,?)", (text(d["name"]), email, gen(text(d["password"], 200))))
        db().commit()
    except INTEGRITY:
        return err("Email already registered")
    return jsonify(ok=True)

@app.post("/api/teacher/login")
def t_login():
    d = body()
    t = db().execute("SELECT * FROM teachers WHERE email=?", (text(d.get("email")).lower(),)).fetchone()
    if not t or not chk(t["pw"], text(d.get("password"), 200)): return err("Wrong email or password", 401)
    session.clear(); session.permanent = True; session.update(tid=t["id"], tname=t["name"])
    return jsonify(ok=True)

@app.post("/api/teacher/reset")
def t_reset():
    """Forgot password: the private teacher access code proves the person is a teacher."""
    d = body(); email, pw = text(d.get("email")).lower(), text(d.get("password"), 200)
    if not TEACHER_CODE: return err("Password reset is not set up. Ask the admin to set TEACHER_CODE.", 503)
    if not (throttle(("t-ip", request.remote_addr), 15) and throttle(("t-acct", email))): return err(TOO_MANY, 429)
    if len(pw) < 6: return err("New password must be at least 6 characters")
    t = db().execute("SELECT id FROM teachers WHERE email=?", (email,)).fetchone()
    if not t or not hmac.compare_digest(text(d.get("code")).encode(), TEACHER_CODE.encode()): return err("Email or teacher access code is wrong", 403)
    db().execute("UPDATE teachers SET pw=? WHERE id=?", (gen(pw), t["id"])); db().commit()
    return jsonify(ok=True)

# ---------- teacher sessions ----------
def owned(sid):
    return db().execute("SELECT * FROM sessions WHERE id=? AND teacher_id=?", (sid, session.get("tid"))).fetchone()

def roster(s):
    """Registered students who belong to the session's department / class / section."""
    if not s["department"]: return []
    return db().execute("SELECT reg_no, name FROM students WHERE department=? AND class_name=? AND section=? ORDER BY reg_no",
                        (s["department"], s["class_name"], s["section"])).fetchall()

def rows(sid):
    return db().execute("""SELECT a.reg_no, s.name, a.marked_at FROM attendance a
        JOIN students s ON s.reg_no=a.reg_no WHERE a.session_id=? ORDER BY a.marked_at""", (sid,)).fetchall()

def absent(s, present):
    here = {r["reg_no"] for r in present}
    return [dict(r) for r in roster(s) if r["reg_no"] not in here]

def info(s):
    left = max(0, int(s["start"] + s["window"] - time.time()))
    return dict(id=s["id"], subject=s["subject"] or "", department=s["department"] or "", class_name=s["class_name"] or "",
                section=s["section"] or "", start=s["start"], live=bool(s["active"]) and left > 0, remaining=left)

@app.post("/api/session/start")
@teacher_api
def start():
    d = body(); g = group(d)
    word, ble, subject = text(d.get("word")).lower(), text(d.get("ble_name")), text(d.get("subject"))
    need = 0 if d.get("need_ble") in (False, 0, "0", "false") else 1
    try: win = max(30, min(int(d.get("window") or 300), 3600))
    except (TypeError, ValueError): return err("Time window must be a number of seconds")
    if not g: return err("Choose the department, class and section")
    if not word: return err("Enter the word or number to say in class")
    if need and not ble: return err("Enter the Bluetooth name, or untick 'Require Bluetooth'")
    db().execute("UPDATE sessions SET active=0 WHERE teacher_id=?", (session["tid"],))
    db().execute('INSERT INTO sessions(teacher_id,department,class_name,section,subject,word,ble_name,start,"window",need_ble) VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (session["tid"], g[0], g[1], g[2], subject, word, ble, time.time(), win, need))
    db().commit()
    return jsonify(ok=True)

@app.post("/api/session/end")
@teacher_api
def end():
    db().execute("UPDATE sessions SET active=0 WHERE teacher_id=?", (session["tid"],)); db().commit()
    return jsonify(ok=True)

@app.get("/api/live")
@teacher_api
def live():
    s = db().execute("SELECT * FROM sessions WHERE teacher_id=? ORDER BY id DESC LIMIT 1", (session["tid"],)).fetchone()
    if not s: return jsonify(id=None)
    present = rows(s["id"]); out = info(s)
    return jsonify(**out, rows=[dict(r) for r in present], absent=absent(s, present), expected=len(roster(s)))

@app.get("/api/teacher/session/<int:sid>")
@teacher_api
def t_session(sid):
    s = owned(sid)
    if not s: return err("Not allowed", 403)
    present = rows(sid)
    return jsonify(**info(s), rows=[dict(r) for r in present], absent=absent(s, present), expected=len(roster(s)))

COUNTS = """(SELECT COUNT(*) FROM attendance a WHERE a.session_id=se.id) AS present,
 (SELECT COUNT(*) FROM students st WHERE st.department=se.department AND st.class_name=se.class_name AND st.section=se.section) AS expected"""

def listing(limit, d):
    """This teacher's sessions, newest first, optionally narrowed by department / class / section / subject text."""
    sql, args = f"SELECT se.*, {COUNTS} FROM sessions se WHERE se.teacher_id=?", [session["tid"]]
    for col in ("department", "class_name", "section"):
        if text(d.get(col)): sql += f" AND se.{col}=?"; args.append(text(d.get(col)))
    if text(d.get("q")): sql += " AND se.subject LIKE ?"; args.append("%" + text(d.get("q")) + "%")
    return [dict(info(s), present=s["present"], expected=s["expected"])
            for s in db().execute(sql + f" ORDER BY se.id DESC LIMIT {int(limit)}", args).fetchall()]

@app.get("/api/teacher/history")
@teacher_api
def t_history(): return jsonify(sessions=listing(200, request.args))

@app.get("/api/teacher/overview")
@teacher_api
def t_overview():
    tid = session["tid"]; one = lambda sql, *a: db().execute(sql, a).fetchone()["n"]
    midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    mine = "FROM sessions se JOIN students st ON st.department=se.department AND st.class_name=se.class_name AND st.section=se.section WHERE se.teacher_id=?"
    held = one("SELECT COUNT(*) AS n FROM sessions WHERE teacher_id=?", tid)
    checkins = one("SELECT COUNT(*) AS n FROM attendance a JOIN sessions se ON se.id=a.session_id WHERE se.teacher_id=?", tid)
    expected = one(f"SELECT COUNT(*) AS n {mine}", tid)
    return jsonify(held=held, today=one("SELECT COUNT(*) AS n FROM sessions WHERE teacher_id=? AND start>=?", tid, midnight),
                   checkins=checkins, students=one(f"SELECT COUNT(DISTINCT st.reg_no) AS n {mine}", tid),
                   average=min(100, round(100 * checkins / expected)) if expected else None, recent=listing(6, {}))

@app.get("/api/teacher/students")
@teacher_api
def t_students():
    """Registered students with how many of this teacher's sessions (for their class) they attended."""
    same = "se.teacher_id=? AND se.department=st.department AND se.class_name=st.class_name AND se.section=st.section"
    sql, args = f"""SELECT st.reg_no, st.name, st.department, st.class_name, st.section,
        (SELECT COUNT(*) FROM sessions se WHERE {same}) AS total,
        (SELECT COUNT(*) FROM attendance a JOIN sessions se ON se.id=a.session_id WHERE a.reg_no=st.reg_no AND {same}) AS attended
        FROM students st WHERE 1=1""", [session["tid"], session["tid"]]
    for col in ("department", "class_name", "section"):
        if text(request.args.get(col)): sql += f" AND st.{col}=?"; args.append(text(request.args.get(col)))
    q = text(request.args.get("q"))
    if q: sql += " AND (st.name LIKE ? OR st.reg_no LIKE ?)"; args += ["%" + q + "%"] * 2
    return jsonify(students=[dict(r) for r in db().execute(sql + " ORDER BY st.reg_no LIMIT 500", args).fetchall()])

def safe(v):  # stop spreadsheet programs treating a name as a formula
    v = str(v); return "'" + v if v[:1] in "=+-@" else v

@app.get("/api/csv/<int:sid>")
def export(sid):
    s = owned(sid)
    if not s: return err("Not allowed", 403)
    present = rows(sid)
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["Registration No", "Name", "Status", "Time"])
    for r in present:
        w.writerow([safe(r["reg_no"]), safe(r["name"]), "Present", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["marked_at"]))])
    for r in absent(s, present): w.writerow([safe(r["reg_no"]), safe(r["name"]), "Absent", ""])
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=attendance_{sid}.csv"})

# ---------- student API ----------
@app.post("/api/student/register")
def s_register():
    d = body(); ip = request.remote_addr
    reg, name, pw, desc, g = text(d.get("reg_no"), 30), text(d.get("name")), text(d.get("password"), 200), face_ok(d.get("descriptor")), group(d)
    email = muj_email(d.get("email"))
    if not throttle(("s-reg", ip), 20): return err(TOO_MANY, 429)
    if not (reg and name and pw): return err("All fields and a face scan are required")
    if not email: return err("Use your college email, ending in @muj.manipal.edu")
    if not g: return err("Choose your department, class and section")
    if desc is None: return err("Face scan was not valid - try again")
    if len(pw) < 4: return err("Password must be at least 4 characters")
    if db().execute("SELECT 1 AS x FROM students WHERE reg_no=? OR email=?", (reg, email)).fetchone(): return err("Already registered. Log in with your college email.")
    try:
        db().execute("INSERT INTO students(reg_no,name,pw,descriptor,department,class_name,section,email,email_verified) VALUES(?,?,?,?,?,?,?,?,1)",
                     (reg, name, gen(pw), json.dumps(desc), *g, email))
        db().commit()
    except INTEGRITY:
        return err("Already registered (one-time registration)")
    return jsonify(ok=True)

@app.post("/api/student/login")
def s_login():
    d = body(); email = muj_email(d.get("email"))
    if not email: return err("Use your college email, ending in @muj.manipal.edu", 401)
    if not (throttle(("s-login", request.remote_addr, email), 10) and throttle(("s-login-ip", request.remote_addr), 60)): return err(TOO_MANY, 429)
    s = db().execute("SELECT * FROM students WHERE email=?", (email,)).fetchone()
    if not s or not chk(s["pw"], text(d.get("password"), 200)): return err("Wrong email or password", 401)
    session.clear(); session.permanent = True; session.update(reg_no=s["reg_no"], name=s["name"])
    return jsonify(ok=True)

@app.post("/api/student/reset")
def s_reset():
    """Forgot password: the student's own face (matched against the one saved at registration) proves who they are."""
    d = body(); reg, pw, desc = text(d.get("reg_no"), 30), text(d.get("password"), 200), face_ok(d.get("descriptor"))
    if not (throttle(("s-ip", request.remote_addr), 15) and throttle(("s-acct", reg))): return err(TOO_MANY, 429)
    if not reg or len(pw) < 4: return err("Enter your registration no and a new password of at least 4 characters")
    if desc is None: return err("Face scan was not valid - try again")
    st = db().execute("SELECT * FROM students WHERE reg_no=?", (reg,)).fetchone()
    if not st or dist(json.loads(st["descriptor"]), desc) > MATCH: return err("Registration no not found or face did not match", 403)
    db().execute("UPDATE students SET pw=? WHERE reg_no=?", (gen(pw), reg)); db().commit()
    return jsonify(ok=True)

def mine_only(st, act):
    """A student only sees sessions for their own department / class / section."""
    if not st["department"]: return list(act)           # accounts made before these dropdowns existed
    return [s for s in act if (s["department"], s["class_name"], s["section"]) == (st["department"], st["class_name"], st["section"])]

@app.get("/api/session")
def current():
    st = me()
    if not st: return jsonify(sessions=[])
    return jsonify(sessions=[dict(class_name=s["class_name"], subject=s["subject"] or "", ble_name=s["ble_name"], need_ble=s["need_ble"] != 0,
                   remaining=int(s["start"] + s["window"] - time.time())) for s in mine_only(st, actives())])

@app.post("/api/attend")
def attend():
    d = body(); st = me()
    if not st: session.clear(); return err("Please log in", 401)
    act = mine_only(st, actives())
    if not act: return err("No active session for your class")
    ble = text(d.get("ble_name"))
    s = next((x for x in act if x["need_ble"] == 0 or (ble and ble.startswith(x["ble_name"]))), None)
    if not s: return err("Teacher's Bluetooth not in range")
    desc = face_ok(d.get("descriptor"))
    if desc is None: return err("Face scan was not valid - try again")
    if dist(json.loads(st["descriptor"]), desc) > MATCH: return err("Face did not match")
    if not hmac.compare_digest(text(d.get("word")).lower().encode(), s["word"].encode()): return err("Wrong word/number")
    try:
        db().execute("INSERT INTO attendance VALUES(?,?,?)", (s["id"], st["reg_no"], time.time())); db().commit()
    except INTEGRITY:
        return err("Attendance already marked")
    return jsonify(ok=True, name=st["name"])

init()  # create tables when the module loads, so gunicorn works as well as `python app.py`

if __name__ == "__main__":
    # local use only. Camera + Web Bluetooth need localhost or HTTPS.
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
