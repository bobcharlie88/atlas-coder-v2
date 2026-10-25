"""Smoke test: boots the server on a spare port with a temp data dir, no Ollama.
Run: python tests/smoke_test.py"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
env = dict(os.environ, ATLAS_DATA=tempfile.mkdtemp(prefix="atlas-smoke-"))
cfgp = ROOT / "config.json"; had = cfgp.exists()
code = f"import app,uvicorn;app.CFG['ollama']['enabled']=False;uvicorn.run(app.app,host='127.0.0.1',port={PORT},log_level='warning')"
proc = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=env)
B = f"http://127.0.0.1:{PORT}"
def call(m, p, body=None):
    req = urllib.request.Request(B + p, json.dumps(body).encode() if body is not None else None, {"Content-Type": "application/json"}, method=m)
    with urllib.request.urlopen(req, timeout=10) as r: return json.loads(r.read())
ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name)
try:
    for _ in range(50):
        try: h = call("GET", "/api/health"); break
        except Exception: time.sleep(0.2)
    check("health", h["ok"])
    check("ui served", b"ATLAS" in urllib.request.urlopen(B + "/").read())
    st = call("GET", "/api/status"); check("status subsystems", all(k in st for k in ("bus","db","ollama","time","sandbox")))
    check("seed notebook", len(call("GET", "/api/notebook")) >= 8)
    n = call("POST", "/api/notebook", {"quadrant": "risk", "title": "Smoke risk", "body": "dust storm on ridge"}); check("notebook create", n["id"])
    r = call("POST", "/api/chat", {"text": "hello"}); check("chat reply (rules) -> " + r["text"], r["source"] == "rules" and r["text"])
    r = call("POST", "/api/chat", {"text": "log: fuel drum cached at km 42"}); check("chat log command -> " + r["text"], r["text"].startswith("Logged"))
    r = call("POST", "/api/chat", {"text": "recall: dust storm"}); check("recall via chat", "match" in r["text"])
    r = call("POST", "/api/chat", {"text": "status"}); check("assessment -> " + r["text"], r["text"])
    e = call("POST", "/api/events", {"message": "smoke mark"}); check("emit event", e["id"])
    check("event persisted", any(x["message"] == "smoke mark" for x in call("GET", "/api/events")))
    p = call("GET", "/api/protocol"); fuel = [c for c in p["checks"] if c["key"] == "fuel"][0]
    p = call("PATCH", f"/api/protocol/checks/{fuel['id']}", {"status": "fail"}); check("withdrawal recommended", p["withdraw"] and "withdrawal" in p["assessment"])
    t = call("POST", "/api/tasks", {"title": "smoke task"}); t = call("PATCH", f"/api/tasks/{t['id']}", {"status": "done"}); check("task done", t["status"] == "done")
    call("PUT", "/api/workspace", {"name": "smoke.md", "content": "hello"}); check("workspace", call("GET", "/api/workspace/smoke.md")["content"] == "hello")
    q = call("POST", "/api/quarantine", {"name": "burst", "content": "<script>alert(1)</script>"}); check("quarantine", q["name"])
    try: call("POST", f"/api/quarantine/{q['name']}/promote"); check("promote requires confirm", False)
    except urllib.error.HTTPError as ex: check("promote requires confirm", ex.code == 400)
    check("promote", call("POST", f"/api/quarantine/{q['name']}/promote?confirm=true")["name"] == "burst.md")
    c = call("POST", "/api/council", {"question": "push on to the creek?", "mode": "council"}); check("council -> " + c["synthesis"], len(c["seats"]) == 3 and "percent" in c["synthesis"])
    c = call("POST", "/api/council", {"question": "x", "mode": "parallel"}); check("council parallel", len(c["seats"]) == 3)
    # vision consent + world model + predictions
    import base64
    jpg = base64.b64decode("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/yQALCAABAAEBAREA/8wABgAQEAX/2gAIAQEAAD8A0s8g/9k=")
    b64 = base64.b64encode(jpg).decode()
    try: call("POST", "/api/vision/frame", {"image_b64": b64}); check("vision blocked when off", False)
    except urllib.error.HTTPError as ex: check("vision blocked when off (403)", ex.code == 403)
    v = call("PUT", "/api/vision/config", {"enabled": True}); check("vision enable, hyperlapse stays off", v["config"]["enabled"] and not v["config"]["hyperlapse"])
    v = call("PUT", "/api/vision/config", {"hyperlapse": True, "interval_s": 300, "source": "upload"}); check("hyperlapse on, 5 min interval", v["config"]["hyperlapse"] and v["config"]["interval_s"] == 300)
    v = call("PUT", "/api/vision/config", {"enabled": False}); check("vision off forces hyperlapse off", not v["config"]["hyperlapse"])
    call("PUT", "/api/vision/config", {"enabled": True})
    before = len(call("GET", "/api/world")["observations"])
    f = call("POST", "/api/vision/frame", {"image_b64": b64}); check("frame -> " + f["caption"], f["observation_id"])
    w = call("GET", "/api/world"); check("world model entry from frame", len(w["observations"]) > before and w["observations"][0]["kind"] == "scene")
    check("predictions refreshed with frame", isinstance(f["predictions"], list))
    m = call("POST", "/api/vision/ingest", {"image_b64": b64, "device_id": "rayban-test", "gps": {"lat": -31.95, "lon": 115.86}}); check("meta bridge ingest", m["source"] == "meta_bridge")
    check("last frame served", urllib.request.urlopen(B + "/api/vision/last.jpg").status == 200)
    r = call("POST", "/api/chat", {"text": "I usually refuel at Leonora"}); check("chat fact learned", any(o["kind"] == "routine" for o in call("GET", "/api/world")["observations"]))
    pr = call("GET", "/api/predictions"); check(f"predictions ({len(pr)}) e.g. {pr[0]['need'] if pr else '-'}", pr and pr[0]["priority"])
    check("clear frames", call("DELETE", "/api/vision")["cleared"] >= 2)
    # SSE: open stream, emit, read
    import threading
    got = []
    def rd():
        with urllib.request.urlopen(B + "/api/stream", timeout=10) as r:
            for line in r:
                if line.startswith(b"data:") and b"sse probe" in line: got.append(1); return
    th = threading.Thread(target=rd, daemon=True); th.start(); time.sleep(0.8)
    call("POST", "/api/events", {"message": "sse probe"}); th.join(5); check("SSE delivery", got)
finally:
    proc.terminate(); proc.wait(5)
    if not had and cfgp.exists(): cfgp.unlink()
print("SMOKE: GREEN" if ok else "SMOKE: RED"); sys.exit(0 if ok else 1)
