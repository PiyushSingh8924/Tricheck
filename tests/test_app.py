"""Run from the project folder:  python3 tests/test_app.py   (every line should say PASS)"""
import os, sys, random, importlib
import tempfile
os.environ["TEACHER_CODE"] = "muj-secret"; os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")   # never touches your real database
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import app as A
import re, time as _t
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
    return out
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
check("teacher register wrong code", not J(c.post("/api/teacher/register", json=dict(name="T", email="t@muj.manipal.edu", password="tpass12", code="no")))["ok"])
check("teacher register", J(c.post("/api/teacher/register", json=dict(name="Prof T", email="T@muj.manipal.edu", password="tpass12", code="muj-secret")))["ok"])

# ---- teacher forgot password ----
r = J(c.post("/api/teacher/reset", json=dict(email="t@muj.manipal.edu", code="wrong", password="newpass1"))); check("reset wrong code rejected", not r["ok"])
r = J(c.post("/api/teacher/reset", json=dict(email="t@muj.manipal.edu", code="muj-secret", password="123"))); check("reset short pw rejected", not r["ok"])
check("old pw still works after failed reset", J(c.post("/api/teacher/login", json=dict(email="t@muj.manipal.edu", password="tpass12")))["ok"])
c.get("/logout")
r = J(c.post("/api/teacher/reset", json=dict(email="t@muj.manipal.edu", code="muj-secret", password="newpass1"))); check("reset with code ok", r["ok"])
check("old pw dead", not J(c.post("/api/teacher/login", json=dict(email="t@muj.manipal.edu", password="tpass12")))["ok"])
check("new pw works", J(c.post("/api/teacher/login", json=dict(email="t@muj.manipal.edu", password="newpass1")))["ok"])
c.get("/logout")
check("reset unknown email rejected", not J(c.post("/api/teacher/reset", json=dict(email="x@y.z", code="muj-secret", password="newpass1")))["ok"])
for i in range(8): last = c.post("/api/teacher/reset", json=dict(email="t@muj.manipal.edu", code="bad%d"%i, password="newpass1"))
check("reset brute force throttled (429)", last.status_code == 429, last.status_code)

# ---- student forgot password (face) ----
r = J(c.post("/api/student/reset", json=dict(reg_no="S1", password="fresh1", descriptor=vec(2)))); check("student reset wrong face rejected", not r["ok"], r)
r = J(c.post("/api/student/reset", json=dict(reg_no="S1", password="fresh1", descriptor=vec(1)))); check("student reset right face ok", r["ok"], r)
check("student old pw dead", not J(c.post("/api/student/login", json=dict(email=E("s1"), password="pass1")))["ok"])
check("student new pw works", J(c.post("/api/student/login", json=dict(email=E("s1"), password="fresh1")))["ok"])
c.get("/logout")

# ---- teacher dashboard ----
c.post("/api/teacher/login", json=dict(email="t@muj.manipal.edu", password="newpass1"))
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
c2 = A.app.test_client(); c2.post("/api/teacher/register", json=dict(name="U", email="u@muj.manipal.edu", password="upass123", code="muj-secret")); c2.post("/api/teacher/login", json=dict(email="u@muj.manipal.edu", password="upass123"))
check("other teacher blocked from csv", c2.get(f"/api/csv/{sid}").status_code == 403)
check("other teacher blocked from detail", c2.get(f"/api/teacher/session/{sid}").status_code == 403)
check("other teacher history empty", len(J(c2.get("/api/teacher/history"))["sessions"]) == 0)
check("end session", J(c.post("/api/session/end"))["ok"])
check("S1 sees none after end", len(J(s1.get("/api/session"))["sessions"]) == 0)


print("---- college email (no email codes) ----")
def S(email, r="N1"): return J(cl("10.9.9.9").post("/api/student/register", json=dict(reg_no=r, name="N", email=email, password="pass1234", descriptor=vec(7), department=A.DEPARTMENTS[0], class_name="Semester 3", section="B")))
check("student gmail rejected", not S("n1@gmail.com")["ok"])
check("student lookalike rejected", not S("n1@muj.manipal.edu.evil.com")["ok"] and not S("a@b@muj.manipal.edu")["ok"])
check("student register is instant, email lower-cased", S("N1@MUJ.manipal.edu")["ok"])
check("student can log in straight away", login(cl("10.9.9.8"), "n1", "pass1234")["ok"])
check("same email cannot register twice", not S("n1@muj.manipal.edu", "N2")["ok"])
t = lambda e, code="muj-secret": J(cl("10.9.9.7").post("/api/teacher/register", json=dict(name="T", email=e, password="tpass12", code=code)))
check("teacher gmail rejected", not t("x@gmail.com")["ok"])
check("teacher needs the teacher code", not t("tt@muj.manipal.edu", "")["ok"] and not t("tt@muj.manipal.edu", "bad")["ok"])
check("teacher with college email + code ok", t("tt@muj.manipal.edu")["ok"] and t("tj@jaipur.manipal.edu")["ok"])
print("done")
