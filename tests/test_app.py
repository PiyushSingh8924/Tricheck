"""Run from the project folder:  python3 tests/test_app.py   (every line should say PASS)"""
import os, sys, random, importlib
import tempfile
os.environ["TEACHER_CODE"] = "muj-secret"; os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")   # never touches your real database
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import app as A
import re, time as _t
sent = {}
def fake_mail(to, subject, body): sent[to] = re.search(r"code is (\d{6})", body).group(1)
A.send_mail = fake_mail
def cl(ip="10.1.1.1"):
    x = A.app.test_client(); x.environ_base["REMOTE_ADDR"] = ip; return x
def db(sql, *a):
    with A.app.app_context():
        r = A.db().execute(sql, a); A.db().commit()
        try: return r.fetchall()
        except Exception: return None
c = cl("10.0.0.1")
def E(r): return f"{r}@muj.manipal.edu"
def reg(client, r, name, pw, seed, sec="B", dept=None, cls="Semester 3"):
    out = J(client.post("/api/student/register", json=dict(reg_no=r, name=name, email=E(r.lower()), password=pw, descriptor=vec(seed), department=dept or A.DEPARTMENTS[0], class_name=cls, section=sec)))
    if not out["ok"]: return out
    return J(client.post("/api/student/verify", json=dict(email=E(r.lower()), code=sent[E(r.lower())])))
def login(client, r, pw): return J(client.post("/api/student/login", json=dict(email=E(r.lower()), password=pw)))
def J(r): return r.get_json()
def check(name, cond, extra=""): print(("PASS " if cond else "FAIL ") + name, extra if not cond else ""); 
vec = lambda s: [random.Random(s).uniform(-.2,.2) for _ in range(128)]
D, CL, S = A.DEPARTMENTS[0], "Semester 3", "B"
g = dict(department=D, class_name=CL, section=S)

# pages render
for p in ["/", "/teacher/login", "/teacher/register", "/teacher/forgot", "/student/login", "/student/register", "/student/forgot"]:
    r = c.get(p); check("page " + p, r.status_code == 200)
check("teacher pages redirect when logged out", c.get("/teacher/dashboard").status_code == 302)
check("register page has dropdowns", b'id="department"' in c.get("/student/register").data and b"Section B" in c.get("/student/register").data)

# students (each from its own IP so the per-IP code limit is not hit)
check("S1 register+verify", reg(cl("10.0.1.1"), "S1", "Asha", "pass1", 1)["ok"])
check("S2 register+verify", reg(cl("10.0.1.2"), "S2", "Ravi", "pass2", 2)["ok"])
check("S3 other section", reg(cl("10.0.1.3"), "S3", "Meena", "pass3", 3, sec="C")["ok"])
check("bad dept rejected", not J(cl("10.0.1.4").post("/api/student/register", json=dict(reg_no="S9", name="X", email=E("s9"), password="pass1", descriptor=vec(9), department="Hacking", class_name=CL, section=S)))["ok"])

# teacher register / login
check("teacher register wrong code", not J(c.post("/api/teacher/register", json=dict(name="T", email="t@muj.in", password="tpass12", code="no")))["ok"])
check("teacher register", J(c.post("/api/teacher/register", json=dict(name="Prof T", email="T@muj.in", password="tpass12", code="muj-secret")))["ok"])

# ---- teacher forgot password ----
r = J(c.post("/api/teacher/reset", json=dict(email="t@muj.in", code="wrong", password="newpass1"))); check("reset wrong code rejected", not r["ok"])
r = J(c.post("/api/teacher/reset", json=dict(email="t@muj.in", code="muj-secret", password="123"))); check("reset short pw rejected", not r["ok"])
check("old pw still works after failed reset", J(c.post("/api/teacher/login", json=dict(email="t@muj.in", password="tpass12")))["ok"])
c.get("/logout")
r = J(c.post("/api/teacher/reset", json=dict(email="t@muj.in", code="muj-secret", password="newpass1"))); check("reset with code ok", r["ok"])
check("old pw dead", not J(c.post("/api/teacher/login", json=dict(email="t@muj.in", password="tpass12")))["ok"])
check("new pw works", J(c.post("/api/teacher/login", json=dict(email="t@muj.in", password="newpass1")))["ok"])
c.get("/logout")
check("reset unknown email rejected", not J(c.post("/api/teacher/reset", json=dict(email="x@y.z", code="muj-secret", password="newpass1")))["ok"])
for i in range(8): last = c.post("/api/teacher/reset", json=dict(email="t@muj.in", code="bad%d"%i, password="newpass1"))
check("reset brute force throttled (429)", last.status_code == 429, last.status_code)

# ---- student forgot password (face) ----
r = J(c.post("/api/student/reset", json=dict(reg_no="S1", password="fresh1", descriptor=vec(2)))); check("student reset wrong face rejected", not r["ok"], r)
r = J(c.post("/api/student/reset", json=dict(reg_no="S1", password="fresh1", descriptor=vec(1)))); check("student reset right face ok", r["ok"], r)
check("student old pw dead", not J(c.post("/api/student/login", json=dict(email=E("s1"), password="pass1")))["ok"])
check("student new pw works", J(c.post("/api/student/login", json=dict(email=E("s1"), password="fresh1")))["ok"])
c.get("/logout")

# ---- teacher dashboard ----
c.post("/api/teacher/login", json=dict(email="t@muj.in", password="newpass1"))
for p in ["/teacher/dashboard", "/teacher/session/new", "/teacher/live", "/teacher/history", "/teacher/students"]:
    check("teacher page " + p, c.get(p).status_code == 200)
check("start needs dropdowns", not J(c.post("/api/session/start", json=dict(word="x", need_ble=False)))["ok"])
check("start ok", J(c.post("/api/session/start", json=dict(word="Tiger", subject="Data Structures", need_ble=False, window=300, **g)))["ok"])
l = J(c.get("/api/live")); check("live: expected=2 (same section only)", l["expected"] == 2 and len(l["absent"]) == 2 and l["live"], l)

# students: S1 (same class) sees it, S3 (other section) does not
s1 = app_c = A.app.test_client(); s1.post("/api/student/login", json=dict(email=E("s1"), password="fresh1"))
s3 = A.app.test_client(); s3.post("/api/student/login", json=dict(email=E("s3"), password="pass3"))
check("S1 sees session", len(J(s1.get("/api/session"))["sessions"]) == 1)
check("S3 sees none", len(J(s3.get("/api/session"))["sessions"]) == 0)
r = J(s3.post("/api/attend", json=dict(word="tiger", descriptor=vec(3)))); check("S3 cannot attend", not r["ok"], r)
check("S1 wrong word", not J(s1.post("/api/attend", json=dict(word="lion", descriptor=vec(1))))["ok"])
check("S1 wrong face", not J(s1.post("/api/attend", json=dict(word="tiger", descriptor=vec(2))))["ok"])
r = J(s1.post("/api/attend", json=dict(word="TIGER", descriptor=vec(1)))); check("S1 attends", r["ok"], r)
check("S1 duplicate rejected", not J(s1.post("/api/attend", json=dict(word="tiger", descriptor=vec(1))))["ok"])
check("attend page renders w/ group", b"Section B" in s1.get("/student/attend").data)

l = J(c.get("/api/live")); check("live: 1 present 1 absent", len(l["rows"]) == 1 and [a["reg_no"] for a in l["absent"]] == ["S2"], l)
o = J(c.get("/api/teacher/overview")); check("overview", o["held"] == 1 and o["checkins"] == 1 and o["students"] == 2 and o["average"] == 50 and o["today"] == 1, o)
h = J(c.get("/api/teacher/history")); check("history row", len(h["sessions"]) == 1 and h["sessions"][0]["present"] == 1 and h["sessions"][0]["expected"] == 2, h)
check("history filter match", len(J(c.get("/api/teacher/history?section=B&q=Data"))["sessions"]) == 1)
check("history filter no match", len(J(c.get("/api/teacher/history?section=C"))["sessions"]) == 0)
sid = h["sessions"][0]["id"]
d = J(c.get(f"/api/teacher/session/{sid}")); check("session detail", len(d["rows"]) == 1 and len(d["absent"]) == 1)
check("session page renders", c.get(f"/teacher/session/{sid}").status_code == 200)
csvt = c.get(f"/api/csv/{sid}").data.decode(); check("csv has Present+Absent", "S1,Asha,Present" in csvt and "S2,Ravi,Absent" in csvt, csvt)
st = J(c.get("/api/teacher/students"))["students"]; m = {s["reg_no"]: s for s in st}
check("students list", m["S1"]["attended"] == 1 and m["S1"]["total"] == 1 and m["S2"]["attended"] == 0 and m["S3"]["total"] == 0, m)
check("students filter by section", [s["reg_no"] for s in J(c.get("/api/teacher/students?section=C"))["students"]] == ["S3"])
check("students search", [s["reg_no"] for s in J(c.get("/api/teacher/students?q=rav"))["students"]] == ["S2"])
# other teacher can't see my session
c2 = A.app.test_client(); c2.post("/api/teacher/register", json=dict(name="U", email="u@muj.in", password="upass123", code="muj-secret")); c2.post("/api/teacher/login", json=dict(email="u@muj.in", password="upass123"))
check("other teacher blocked from csv", c2.get(f"/api/csv/{sid}").status_code == 403)
check("other teacher blocked from detail", c2.get(f"/api/teacher/session/{sid}").status_code == 403)
check("other teacher history empty", len(J(c2.get("/api/teacher/history"))["sessions"]) == 0)
check("end session", J(c.post("/api/session/end"))["ok"])
check("S1 sees none after end", len(J(s1.get("/api/session"))["sessions"]) == 0)


print("---- Task B: college email + one-time code ----")
ok_mail = ["a.b@muj.manipal.edu", "A.B@MUJ.Manipal.EDU", "x_y+z-1@muj.manipal.edu"]
bad_mail = ["name@muj.manipal.edu.evil.com", "name@evil.com", "a@b@muj.manipal.edu", "name@muj.manipal.edu@evil.com", "name@xmuj.manipal.edu",
            "name@sub.muj.manipal.edu", "@muj.manipal.edu", "name @muj.manipal.edu", "name@muj.manipal.edu ,x@y.com",
            "a"*65 + "@muj.manipal.edu", "name@muj-manipal.edu", "name@mujXmanipal.edu", "\u212a@muj.manipal.edu", ""]
check("valid emails accepted (case-insensitive)", all(A.muj_email(x) == x.lower() for x in ok_mail))
check("lookalike emails rejected", not any(A.muj_email(x) for x in bad_mail), [x for x in bad_mail if A.muj_email(x)])
check("surrounding whitespace is trimmed, not kept", A.muj_email("  name@muj.manipal.edu\n") == "name@muj.manipal.edu")
check("non-string email rejected", A.muj_email(None) is None and A.muj_email(["a@muj.manipal.edu"]) is None)
r = J(cl("10.0.2.1").post("/api/student/register", json=dict(reg_no="L1", name="L", email="l1@muj.manipal.edu.evil.com", password="pass1", descriptor=vec(11), **g)))
check("register rejects lookalike email", not r["ok"] and "college email" in r["error"], r)

x = cl("10.0.2.2"); r = J(x.post("/api/student/register", json=dict(reg_no="N1", name="New", email="N1@MUJ.Manipal.edu", password="pass1", descriptor=vec(21), **g)))
check("register sends code, email normalised", r["ok"] and r["email"] == "n1@muj.manipal.edu" and len(sent["n1@muj.manipal.edu"]) == 6, r)
row = db("SELECT * FROM email_otps WHERE email=?", "n1@muj.manipal.edu")[0]
check("code stored hashed (not the code itself)", sent["n1@muj.manipal.edu"] not in str(dict(row).values()) and len(row["code_hash"]) == 64)
r = login(x, "N1", "pass1"); check("login blocked until verified", not r["ok"] and r.get("need_verify"), r)
check("unverified student cannot open attend page", x.get("/student/attend").status_code == 302)
r = J(x.post("/api/student/verify", json=dict(email="n1@muj.manipal.edu", code="000000"))); check("wrong code rejected", not r["ok"], r)
good = sent["n1@muj.manipal.edu"]
r = J(x.post("/api/student/verify", json=dict(email="n1@muj.manipal.edu", code="12345"))); check("5-digit code rejected", not r["ok"])
for i in range(3): x.post("/api/student/verify", json=dict(email="n1@muj.manipal.edu", code="111111"))   # attempts 3,4,5 used (wrong ones)
r = J(x.post("/api/student/verify", json=dict(email="n1@muj.manipal.edu", code=good))); check("6th try blocked even with right code", not r["ok"] and "Too many" in r["error"], r)

# new code (age the cooldown), then expiry
db("UPDATE email_otps SET created=created-120 WHERE email=?", "n1@muj.manipal.edu")
r = J(x.post("/api/student/resend", json=dict(email="n1@muj.manipal.edu"))); check("resend works after cooldown", r["ok"], r)
r = J(x.post("/api/student/resend", json=dict(email="n1@muj.manipal.edu"))); check("resend within 60s blocked", not r["ok"], r)
new = sent["n1@muj.manipal.edu"]
db("UPDATE email_otps SET expires=? WHERE email=? AND expires>0", _t.time() - 1, "n1@muj.manipal.edu")
r = J(x.post("/api/student/verify", json=dict(email="n1@muj.manipal.edu", code=new))); check("expired code rejected", not r["ok"] and "expired" in r["error"], r)
db("UPDATE email_otps SET created=created-120 WHERE email=?", "n1@muj.manipal.edu")
J(x.post("/api/student/resend", json=dict(email="n1@muj.manipal.edu"))); new = sent["n1@muj.manipal.edu"]
old_ok = J(x.post("/api/student/verify", json=dict(email="n1@muj.manipal.edu", code=good)))["ok"]
check("older code no longer works", not old_ok)
r = J(x.post("/api/student/verify", json=dict(email="n1@muj.manipal.edu", code=new))); check("right code verifies", r["ok"], r)
r = J(x.post("/api/student/verify", json=dict(email="n1@muj.manipal.edu", code=new))); check("code is single-use", not r["ok"])
check("login works after verify", login(x, "N1", "pass1")["ok"])
check("attend page opens when verified", x.get("/student/attend").status_code == 200)
check("wrong password still rejected", not login(x, "N1", "nope")["ok"])
check("login with non-college email rejected", not J(x.post("/api/student/login", json=dict(email="n1@gmail.com", password="pass1")))["ok"])

# duplicates / takeover protection
r = J(cl("10.0.2.3").post("/api/student/register", json=dict(reg_no="N1", name="Thief", email="other@muj.manipal.edu", password="x1234", descriptor=vec(5), **g))); check("verified reg_no cannot be re-registered", not r["ok"])
r = J(cl("10.0.2.3").post("/api/student/register", json=dict(reg_no="N2", name="Thief", email="n1@muj.manipal.edu", password="x1234", descriptor=vec(5), **g))); check("verified email cannot be re-registered", not r["ok"])
# half-finished sign-up can be redone with the same email
y = cl("10.0.2.4"); J(y.post("/api/student/register", json=dict(reg_no="P1", name="Pen", email="p1@muj.manipal.edu", password="pass1", descriptor=vec(31), **g)))
r = J(cl("10.0.2.5").post("/api/student/register", json=dict(reg_no="P1", name="Pen2", email="p1@muj.manipal.edu", password="pass2", descriptor=vec(32), **g))); check("pending sign-up can be redone", r["ok"], r)
check("only one row for P1", len(db("SELECT * FROM students WHERE reg_no=?", "P1")) == 1)

# older accounts (made before email existed) are protected, and can link an email
db("INSERT INTO students(reg_no,name,pw,descriptor,department,class_name,section) VALUES(?,?,?,?,?,?,?)", "OLD1", "Old", A.gen("oldpw1"), "[]", D, CL, "B")
r = J(cl("10.0.2.6").post("/api/student/register", json=dict(reg_no="OLD1", name="Thief", email="thief@muj.manipal.edu", password="x1234", descriptor=vec(5), **g))); check("pre-email account cannot be taken over", not r["ok"])
z = cl("10.0.2.7")
check("link: wrong password rejected", not J(z.post("/api/student/link", json=dict(reg_no="OLD1", password="bad", email="old1@muj.manipal.edu")))["ok"])
check("link: bad email rejected", not J(z.post("/api/student/link", json=dict(reg_no="OLD1", password="oldpw1", email="old1@gmail.com")))["ok"])
r = J(z.post("/api/student/link", json=dict(reg_no="OLD1", password="oldpw1", email="old1@muj.manipal.edu"))); check("link sends code", r["ok"], r)
r = J(z.post("/api/student/verify", json=dict(email="old1@muj.manipal.edu", code=sent["old1@muj.manipal.edu"]))); check("link verifies", r["ok"])
check("old account logs in with email", login(z, "OLD1", "oldpw1")["ok"])
check("link on verified account refused", not J(z.post("/api/student/link", json=dict(reg_no="OLD1", password="oldpw1", email="old2@muj.manipal.edu")))["ok"])

# rate limits
w = cl("10.0.3.1"); res = []
for i in range(5):
    db("UPDATE email_otps SET created=created-61 WHERE email=?", "p1@muj.manipal.edu")
    res.append(J(w.post("/api/student/resend", json=dict(email="p1@muj.manipal.edu")))["ok"])
check("max 3 codes per email per 10 min", res.count(True) <= 2 and res[-1] is False, res)
v = cl("10.0.3.2"); n = 0
for i in range(12):
    r = J(v.post("/api/student/register", json=dict(reg_no=f"R{i}", name="R", email=f"r{i}@muj.manipal.edu", password="pass1", descriptor=vec(40+i), **g)))
    if r["ok"]: n += 1
check("max 10 codes per IP per hour", n == 10, n)
u = cl("10.0.3.3"); codes = [u.post("/api/student/login", json=dict(email="n1@muj.manipal.edu", password="bad%d" % i)).status_code for i in range(12)]
check("login attempts throttled (429)", 429 in codes, codes)

# SMTP failure path
A.send_mail = lambda *a: (_ for _ in ()).throw(OSError("smtp down password=SECRET123"))
A.app.logger.handlers.clear(); import io as _io, logging; buf = _io.StringIO(); h = logging.StreamHandler(buf); A.app.logger.addHandler(h)
r = cl("10.0.4.1").post("/api/student/register", json=dict(reg_no="M1", name="M", email="m1@muj.manipal.edu", password="pass1", descriptor=vec(77), **g))
check("mail failure -> clean error, no secrets logged", r.status_code == 502 and "SECRET123" not in buf.getvalue() and "SECRET123" not in r.get_data(as_text=True), buf.getvalue())
A.send_mail = fake_mail

# migration is idempotent and keeps data
A.init(); A.init()
check("init() twice keeps data", len(db("SELECT * FROM students WHERE reg_no=?", "S1")) == 1)
check("unique email index exists", len(db("SELECT name FROM sqlite_master WHERE name=?", "students_email_uq")) == 1)
