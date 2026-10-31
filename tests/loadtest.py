"""Run: python tests/loadtest.py http://127.0.0.1:5000 60 demo   (needs: pip install requests)
Creates fake students named demo000... with random (non-biometric) face data. Run it on a LOCAL copy, not on the live demo site."""
import sys, time, random, statistics, threading, json, concurrent.futures as cf
import os, requests
from collections import Counter

base = sys.argv[1].rstrip("/"); N = int(sys.argv[2]); tag = sys.argv[3] if len(sys.argv) > 3 else "t"
server_pid = int(sys.argv[4]) if len(sys.argv) > 4 else None
DUP = 3  # every 5th student fires DUP identical requests at once (duplicate-submission test)

def cpu():
    if not server_pid: return 0.0
    import psutil
    p = psutil.Process(server_pid); t = p.cpu_times(); s = t.user + t.system
    for c in p.children(recursive=True):
        ct = c.cpu_times(); s += ct.user + ct.system
    return s

def pct(v, q):
    v = sorted(v); return v[min(len(v) - 1, int(q * len(v)))] if v else 0
def stats(name, lat, errs):
    print(f"{name:34s} n={len(lat):4d}  p50={pct(lat,.5)*1000:7.0f}ms  p95={pct(lat,.95)*1000:7.0f}ms  max={max(lat or [0])*1000:7.0f}ms  non-200/failed={errs}")

rnd = random.Random(42)
studs = [dict(reg=f"{tag}{i:03d}", name=f"Student {i}", pw=f"pw{i}x", desc=[rnd.uniform(-.3, .3) for _ in range(128)]) for i in range(N)]
tp = {"name": "Demo Teacher", "email": f"{tag}@demo.test", "password": "teachpass1"}

def call(s, method, path, **kw):
    t = time.time()
    try:
        r = s.request(method, base + path, timeout=float(os.environ.get("TIMEOUT", 60)), **kw)
        try: j = r.json()
        except Exception: j = {"ok": False, "error": f"HTTP {r.status_code} (non-JSON)"}
        return r.status_code, j, time.time() - t
    except Exception as e:
        return 0, {"ok": False, "error": f"EXC {type(e).__name__}"}, time.time() - t

def phase(name, fn, items, workers=None):
    lat, bad, out = [], 0, []
    t0 = time.time()
    with cf.ThreadPoolExecutor(workers or len(items)) as ex:
        for st, j, d in ex.map(fn, items):
            lat.append(d); out.append((st, j))
            if st != 200 and not (st in (400, 401) and j.get("error")): bad += 1; print("   !! unexpected:", st, j)
            if st in (0, 500, 502, 503, 504): pass
    stats(name, lat, bad); print(f"{'':34s} wall={time.time()-t0:.1f}s")
    return out

# --- setup ---
ts = requests.Session()
call(ts, "POST", "/api/teacher/register", json=tp)
st, j, _ = call(ts, "POST", "/api/teacher/login", json={"email": tp["email"], "password": tp["password"]}); assert j.get("ok"), j
print("setup: register students (one at a time, like a pre-class sign-up)")
sess = {}
def reg(s):
    c = requests.Session(); sess[s["reg"]] = c
    return call(c, "POST", "/api/student/register", json=dict(reg_no=s["reg"], name=s["name"], password=s["pw"], descriptor=s["desc"]))
t0 = time.time(); r = [reg(s) for s in studs]; print(f"  registered {sum(1 for x in r if x[1].get('ok'))}/{N} in {time.time()-t0:.1f}s sequentially ({(time.time()-t0)/N*1000:.0f}ms each)")

c0 = cpu()
res = phase(f"A: {N} logins at the same moment", lambda s: call(sess[s['reg']], "POST", "/api/student/login", json=dict(reg_no=s["reg"], password=s["pw"])), studs)
print(f"  logins ok: {sum(1 for st,j in res if j.get('ok'))}/{N}   server CPU used: {cpu()-c0:.1f}s")

call(ts, "POST", "/api/session/start", json=dict(class_name="DEMO", ble_name="TEACHER", word="Demo7", window=600))

stop = threading.Event(); poll_lat = []; poll_bad = [0]; poll_codes = Counter()
def poller(kind, s=None):
    while not stop.is_set():
        hdr = {"Connection": "close"} if os.environ.get("POLL_CLOSE") else {}
        if kind == "s": st, j, d = call(sess[s["reg"]], "GET", "/api/session", headers=hdr)
        else: st, j, d = call(ts, "GET", "/api/live", headers=hdr)
        poll_lat.append(d); poll_codes[(st, j.get("error"))] += 1 if st != 200 else 0
        if st != 200: poll_bad[0] += 1
        stop.wait(2)
pts = [threading.Thread(target=poller, args=("s", s), daemon=True) for s in studs] + [threading.Thread(target=poller, args=("l",), daemon=True)]
for t in pts: t.start(); 
time.sleep(3)

gate = threading.Barrier(sum(DUP if i % 5 == 0 else 1 for i in range(N)))
def attend(job):
    s, n = job; noisy = [x + rnd.uniform(-.004, .004) for x in s["desc"]]
    gate.wait()
    return call(sess[s["reg"]], "POST", "/api/attend", json=dict(word="demo7", ble_name="TEACHER-01", descriptor=noisy))
jobs = [(s, k) for i, s in enumerate(studs) for k in range(DUP if i % 5 == 0 else 1)]
c1 = cpu()
res = phase(f"B: {len(jobs)} attend requests at once", attend, jobs)
print(f"  server CPU used for the burst (incl. pollers): {cpu()-c1:.1f}s")
from collections import Counter
cnt = Counter((j.get("ok"), j.get("error")) for st, j in res); print("  outcomes:", dict(cnt))
time.sleep(2.5)
stop.set()
st, live, _ = call(ts, "GET", "/api/live")
rows = live.get("rows", []); regs = [r["reg_no"] for r in rows]
print(f"C: teacher dashboard shows {len(rows)} students, unique={len(set(regs))}  (expected {N})")
oks = Counter()
for (s, k), (st, j) in zip(jobs, res):
    if j.get("ok"): oks[s["reg"]] += 1
print(f"   students with exactly one success: {sum(1 for v in oks.values() if v==1)}/{N};  with >1 success (duplicates): {sum(1 for v in oks.values() if v>1)}")
stats("   background polling during test", poll_lat, poll_bad[0]); print("   poll failures:", {k:v for k,v in poll_codes.items() if v})
# negative checks
s0 = studs[1]
chk = lambda body: call(requests.Session(), "POST", "/api/attend", json=body)[1].get("error")
print("D: security spot checks")
print("   not logged in      ->", chk(dict(word="demo7", ble_name="TEACHER", descriptor=s0["desc"])))
c = sess[studs[1]["reg"]]
print("   missing descriptor ->", call(c, "POST", "/api/attend", json=dict(word="demo7", ble_name="TEACHER"))[:2])
print("   short descriptor   ->", call(c, "POST", "/api/attend", json=dict(word="demo7", ble_name="TEACHER", descriptor=[0.1]*3))[:2])
print("   wrong face         ->", call(c, "POST", "/api/attend", json=dict(word="demo7", ble_name="TEACHER", descriptor=[rnd.uniform(-.3,.3) for _ in range(128)]))[1].get("error"))
print("   wrong word (new student) ->", end=" "); c2 = sess[studs[2]["reg"]]; print(call(c2, "POST", "/api/attend", json=dict(word="nope", ble_name="TEACHER", descriptor=studs[2]["desc"]))[1].get("error"))
print("   no bluetooth       ->", call(c2, "POST", "/api/attend", json=dict(word="demo7", ble_name="", descriptor=studs[2]["desc"]))[1].get("error"))
st, j, _ = call(requests.Session(), "GET", "/api/csv/1"); print("   CSV w/o login      ->", st, j.get("error"))
print("   students CSV by other teacher ->", end=" "); t2 = requests.Session(); call(t2, "POST", "/api/teacher/register", json={"name": "X", "email": f"x{tag}@d.t", "password": "p"}); call(t2, "POST", "/api/teacher/login", json={"email": f"x{tag}@d.t", "password": "p"}); print(call(t2, "GET", "/api/csv/1")[0])
