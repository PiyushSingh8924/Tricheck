import csv, hmac, io, json, math, os, secrets, sqlite3, time
from datetime import timedelta
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

def init():
    pk, real = ("SERIAL PRIMARY KEY", "DOUBLE PRECISION") if PG else ("INTEGER PRIMARY KEY AUTOINCREMENT", "REAL")
    stmts = [
        "CREATE TABLE IF NOT EXISTS students(reg_no TEXT PRIMARY KEY, name TEXT, pw TEXT, descriptor TEXT)",
        f"CREATE TABLE IF NOT EXISTS teachers(id {pk}, name TEXT, email TEXT UNIQUE, pw TEXT)",
        f'CREATE TABLE IF NOT EXISTS sessions(id {pk}, teacher_id INTEGER, class_name TEXT, word TEXT, ble_name TEXT, start {real}, "window" INTEGER, active INTEGER DEFAULT 1)',
        f"CREATE TABLE IF NOT EXISTS attendance(session_id INTEGER, reg_no TEXT, marked_at {real}, UNIQUE(session_id, reg_no))"]
    if PG:
        with pool.connection() as c:
            c.execute("SELECT pg_advisory_lock(42)")          # several workers may start together
            try:
                for s in stmts: c.execute(s)
                c.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS need_ble INTEGER DEFAULT 1")
            finally: c.execute("SELECT pg_advisory_unlock(42)")
    else:
        with sqlite3.connect(DB, timeout=15) as c:
            c.execute("PRAGMA journal_mode=WAL")               # readers and the one writer no longer block each other
            for s in stmts: c.execute(s)
            if "need_ble" not in [r[1] for r in c.execute("PRAGMA table_info(sessions)")]:
                c.execute("ALTER TABLE sessions ADD COLUMN need_ble INTEGER DEFAULT 1")

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

# ---------- pages ----------
AUTH = {  # key: (title, fields, api, next page)
    "teacher_register": ("Teacher Registration", [("name", "Full name", "text"), ("email", "Email", "email"), ("password", "Password", "password")]
                         + ([("code", "Teacher access code", "password")] if TEACHER_CODE else []), "/api/teacher/register", "/teacher/login"),
    "teacher_login": ("Teacher Login", [("email", "Email", "email"), ("password", "Password", "password")], "/api/teacher/login", "/teacher/dashboard"),
    "student_login": ("Student Login", [("reg_no", "Registration no", "text"), ("password", "Password", "password")], "/api/student/login", "/student/attend"),
}
def auth_page(k):
    t, f, api, nxt = AUTH[k]
    return render_template("auth.html", title=t, fields=f, api=api, nxt=nxt)
for k in AUTH:
    app.add_url_rule("/" + k.replace("_", "/"), k, lambda k=k: auth_page(k))

@app.get("/")
def home(): return render_template("home.html")

@app.get("/student/register")
def student_register(): return render_template("student_register.html")

@app.get("/student/attend")
def student_attend():
    if "reg_no" not in session: return redirect("/student/login")
    return render_template("attend.html", name=session["name"])

@app.get("/teacher/dashboard")
def dashboard():
    if "tid" not in session: return redirect("/teacher/login")
    return render_template("dashboard.html", name=session["tname"])

@app.get("/logout")
def logout():
    session.clear(); return redirect("/")

# ---------- teacher API ----------
@app.post("/api/teacher/register")
def t_register():
    d = body()
    if not all(d.get(k) for k in ("name", "email", "password")): return err("All fields are required")
    if TEACHER_CODE and not hmac.compare_digest(text(d.get("code")).encode(), TEACHER_CODE.encode()): return err("Wrong teacher access code", 403)
    try:
        db().execute("INSERT INTO teachers(name,email,pw) VALUES(?,?,?)", (text(d["name"]), text(d["email"]).lower(), gen(text(d["password"], 200))))
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

@app.post("/api/session/start")
def start():
    if "tid" not in session: return err("Please log in", 401)
    d = body()
    cls, word, ble = text(d.get("class_name")), text(d.get("word")).lower(), text(d.get("ble_name"))
    need = 0 if d.get("need_ble") in (False, 0, "0", "false") else 1
    try: win = max(30, min(int(d.get("window") or 300), 3600))
    except (TypeError, ValueError): return err("Time window must be a number of seconds")
    if not cls or not word: return err("Class name and word are required")
    if need and not ble: return err("Enter the Bluetooth name, or untick 'Require Bluetooth'")
    db().execute("UPDATE sessions SET active=0 WHERE teacher_id=?", (session["tid"],))
    db().execute('INSERT INTO sessions(teacher_id,class_name,word,ble_name,start,"window",need_ble) VALUES(?,?,?,?,?,?,?)',
                 (session["tid"], cls, word, ble, time.time(), win, need))
    db().commit()
    return jsonify(ok=True)

@app.post("/api/session/end")
def end():
    if "tid" not in session: return err("Please log in", 401)
    db().execute("UPDATE sessions SET active=0 WHERE teacher_id=?", (session["tid"],)); db().commit()
    return jsonify(ok=True)

def rows(sid):
    return db().execute("""SELECT a.reg_no, s.name, a.marked_at FROM attendance a
        JOIN students s ON s.reg_no=a.reg_no WHERE a.session_id=? ORDER BY a.marked_at""", (sid,)).fetchall()

@app.get("/api/live")
def live():
    if "tid" not in session: return err("Please log in", 401)
    s = db().execute("SELECT * FROM sessions WHERE teacher_id=? ORDER BY id DESC LIMIT 1", (session["tid"],)).fetchone()
    if not s: return jsonify(id=None, rows=[])
    left = max(0, int(s["start"] + s["window"] - time.time()))
    return jsonify(id=s["id"], class_name=s["class_name"], active=bool(s["active"]) and left > 0,
                   remaining=left, rows=[dict(r) for r in rows(s["id"])])

def safe(v):  # stop spreadsheet programs treating a name as a formula
    v = str(v); return "'" + v if v[:1] in "=+-@" else v

@app.get("/api/csv/<int:sid>")
def export(sid):
    s = db().execute("SELECT * FROM sessions WHERE id=? AND teacher_id=?", (sid, session.get("tid"))).fetchone()
    if not s: return err("Not allowed", 403)
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["Registration No", "Name", "Time"])
    for r in rows(sid):
        w.writerow([safe(r["reg_no"]), safe(r["name"]), time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["marked_at"]))])
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=attendance_{sid}.csv"})

# ---------- student API ----------
@app.post("/api/student/register")
def s_register():
    d = body()
    reg, name, pw, desc = text(d.get("reg_no"), 30), text(d.get("name")), text(d.get("password"), 200), face_ok(d.get("descriptor"))
    if not (reg and name and pw): return err("All fields and a face scan are required")
    if desc is None: return err("Face scan was not valid - try again")
    if len(pw) < 4: return err("Password must be at least 4 characters")
    try:
        db().execute("INSERT INTO students VALUES(?,?,?,?)", (reg, name, gen(pw), json.dumps(desc)))
        db().commit()
    except INTEGRITY:
        return err("Already registered (one-time registration)")
    return jsonify(ok=True)

@app.post("/api/student/login")
def s_login():
    d = body()
    s = db().execute("SELECT * FROM students WHERE reg_no=?", (text(d.get("reg_no"), 30),)).fetchone()
    if not s or not chk(s["pw"], text(d.get("password"), 200)): return err("Wrong registration no or password", 401)
    session.clear(); session.permanent = True; session.update(reg_no=s["reg_no"], name=s["name"])
    return jsonify(ok=True)

@app.get("/api/session")
def current():
    return jsonify(sessions=[dict(class_name=s["class_name"], ble_name=s["ble_name"], need_ble=s["need_ble"] != 0,
                   remaining=int(s["start"] + s["window"] - time.time())) for s in actives()])

@app.post("/api/attend")
def attend():
    if "reg_no" not in session: return err("Please log in", 401)
    d = body(); act = actives()
    if not act: return err("No active session")
    ble = text(d.get("ble_name"))
    s = next((x for x in act if x["need_ble"] == 0 or (ble and ble.startswith(x["ble_name"]))), None)
    if not s: return err("Teacher's Bluetooth not in range")
    st = db().execute("SELECT * FROM students WHERE reg_no=?", (session["reg_no"],)).fetchone()
    if not st: session.clear(); return err("Please log in", 401)
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
