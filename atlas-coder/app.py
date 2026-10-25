"""ATLAS — Field intelligence, builder's node. Server entry point.
Run:  python app.py   (or START.bat / start.sh)"""
import asyncio, json, os, sys, threading, time, webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from atlas import core, voice, world_model, predict_needs, vision, VERSION
import base64
from atlas.core import bus

CFG = core.load_config()
core._cfg_cache.update(CFG)
db = core.DB(core.DATA / "atlas.db")
bus.db = db
world_model.init(db)
if "vision" in CFG:  # migrate v2.0 continuous/fps keys to hyperlapse
    CFG["vision"].pop("continuous", None); CFG["vision"].pop("fps", None)
    CFG["vision"].setdefault("hyperlapse", False); CFG["vision"].setdefault("interval_s", 120)
for k, v in {"vision": {"enabled": False, "source": "upload", "hyperlapse": False, "interval_s": 120, "model": "llava", "keep_frames": 200}, "ambient": {"interval_s": 20, "speak_priority": True}}.items():
    CFG.setdefault(k, v)
core.WORKSPACE.mkdir(parents=True, exist_ok=True); core.QUARANTINE.mkdir(parents=True, exist_ok=True)
STARTED = time.time()

DEFAULT_CHECKS = [
    ("water", "Water carried meets plan (litres per person per day)", 1),
    ("fuel", "Fuel loaded; walk-out number calculated", 1),
    ("batteries", "Batteries charged — radio, GPS, beacon, node", 1),
    ("walkout", "Walk-out number recorded and shared", 1),
    ("checkin", "Check-in schedule confirmed with base", 1),
    ("sandbox", "Sandbox armed; external bursts quarantined", 1),
    ("models", "Offline models and maps cached", 0),
    ("firstaid", "First-aid kit checked", 1),
    ("vehicle", "Vehicle: tyres, spare, recovery gear", 0),
]
DEFAULT_RESERVES = [("fuel", "Fuel", 120, "L", 80), ("water", "Water", 40, "L", 20),
                    ("time", "Daylight remaining", 6, "h", 2), ("battery", "Battery", 85, "%", 30)]

def seed():
    if db.meta("seeded"): return
    ts = core.now_iso()
    notes = [
        ("known", "Check-in at 1900 daily", "Base expects radio or sat check-in at 1900 local. Two missed check-ins triggers search protocol."),
        ("known", "Walk-out number", "Walk-out number = fuel needed to return to last safe point, plus 30% margin. Below it, the only correct move is back."),
        ("unknown", "Contour anomaly — survey line 4", "Elevation data disagrees with ground observation by ~6 m. Instrument error or terrain change. Not established."),
        ("unknown", "Track condition past the creek crossing", "No recent reports. Insufficient data."),
        ("risk", "Heat after 1400", "Afternoon heat raises water burn. Plan heavy work before midday."),
        ("risk", "Untrusted data bursts", "Sandbox rule: anything arriving from outside is quarantined. Nothing executes without explicit promotion."),
        ("opportunity", "Re-fly contour anomaly at first light", "Low sun angle improves relief shading. Cheap test that separates the two explanations."),
        ("opportunity", "Cache water at the creek", "Reduces carried load on the return leg."),
    ]
    for q, t, b in notes:
        db.q("INSERT INTO notebook(quadrant,title,body,created,updated) VALUES(?,?,?,?,?)", (q, t, b, ts, ts))
    for k, l, c in DEFAULT_CHECKS:
        db.q("INSERT OR IGNORE INTO checks(key,label,critical,status,note,updated) VALUES(?,?,?,?,?,?)", (k, l, c, "pending", "", ts))
    for k, l, v, u, mn in DEFAULT_RESERVES:
        db.q("INSERT OR IGNORE INTO reserves VALUES(?,?,?,?,?,?)", (k, l, v, u, mn, ts))
    for t in ["Run pre-departure sequence", "Confirm 1900 check-in with base", "Review contour anomaly log"]:
        db.q("INSERT INTO tasks(title,status,origin,created) VALUES(?,?,?,?)", (t, "pending", "atlas", ts))
    (core.WORKSPACE / "field-log.md").write_text("# Field log\n\nPlatform online. First entry.\n", encoding="utf-8")
    (core.WORKSPACE / "walk-out-calc.md").write_text(
        "# Walk-out number\n\nDistance to last safe point (km) × consumption (L/100km) ÷ 100 × 1.3 margin.\n\nExample: 180 km × 16 ÷ 100 × 1.3 = 37.4 L\n", encoding="utf-8")
    db.q("INSERT INTO transcript(role,text,ts,source) VALUES(?,?,?,?)", ("atlas", "Platform online. Pre-departure sequence available.", ts, "system"))
    db.meta("seeded", ts)

seed()

@asynccontextmanager
async def lifespan(app):
    bus.loop = asyncio.get_running_loop()
    bus.emit("system.online", "Platform online.")
    task = asyncio.create_task(ticker())
    yield
    task.cancel()

LAST_PRED = {"items": [], "spoken": set()}
async def ticker():
    """Always-on ambient loop: time awareness -> vision summary -> predictions -> events -> priority line."""
    last_phase, warned = None, None
    while True:
        try: ambient_tick(last_phase); last_phase = core.time_context(CFG)["phase"]
        except Exception as e: bus.emit("ambient.error", str(e), level="warn")
        await asyncio.sleep(int(CFG.get("ambient", {}).get("interval_s", 20)))

def ambient_tick(last_phase=None):
    tc = core.time_context(CFG)
    if last_phase and tc["phase"] != last_phase: bus.emit("time.phase", f"{tc['phase'].capitalize()}.")
    preds = predict_needs.predict(db, CFG)
    if [p["need"] for p in preds] != [p["need"] for p in LAST_PRED["items"]]:
        bus.emit("predict.updated", f"{len(preds)} predicted needs." + (f" Top: {preds[0]['need']}." if preds else ""), data={"top": preds[:3]})
    LAST_PRED["items"] = preds
    if CFG.get("ambient", {}).get("speak_priority", True):
        for p in preds:
            key = (tc["date"], p["need"])
            if p["priority"] and key not in LAST_PRED["spoken"]:
                LAST_PRED["spoken"].add(key)
                line = f"Mitchell. {p['need']}. {p['basis'].capitalize()}."
                db.q("INSERT INTO transcript(role,text,ts,source) VALUES(?,?,?,?)", ("atlas", line, core.now_iso(), "ambient"))
                bus.emit("atlas.speaks", line, level="alert")
    return preds

app = FastAPI(title="ATLAS", version=VERSION, lifespan=lifespan)

# ---------------- models ----------------
class NoteIn(BaseModel): quadrant: str; title: str; body: str = ""
class NotePatch(BaseModel): quadrant: str | None = None; title: str | None = None; body: str | None = None
class ChatIn(BaseModel): text: str
class TaskIn(BaseModel): title: str; origin: str = "user"
class TaskPatch(BaseModel): status: str | None = None; title: str | None = None
class CheckPatch(BaseModel): status: str; note: str | None = None
class CheckIn(BaseModel): label: str; critical: bool = False
class ReservePatch(BaseModel): value: float | None = None; minimum: float | None = None
class FileIn(BaseModel): name: str; content: str = ""
class EventIn(BaseModel): kind: str = "user.mark"; message: str; level: str = "info"
class CouncilIn(BaseModel): question: str = "current situation"; mode: str = "council"
class QuarantineIn(BaseModel): name: str; content: str; origin: str = "manual"

# ---------------- system ----------------
@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION, "uptime_s": int(time.time() - STARTED), "message": "Platform online."}

@app.get("/api/status")
def status():
    try: db.one("SELECT 1 AS x"); dbs = {"ok": True, "state": "online"}
    except Exception as e: dbs = {"ok": False, "state": str(e)}
    q = len(core.list_dir(core.QUARANTINE))
    return {"bus": {"ok": True, "state": f"{bus.count} events, {len(bus.subscribers)} listeners"},
            "db": dbs, "ollama": voice.ollama_status(CFG),
            "time": {"ok": True, **core.time_context(CFG)},
            "vision": {"ok": CFG["vision"]["enabled"], "state": vision.state["led"], "enabled": CFG["vision"]["enabled"], "hyperlapse": CFG["vision"]["hyperlapse"], "interval_s": CFG["vision"]["interval_s"], "source": CFG["vision"]["source"]},
            "world": world_model.summary(db),
            "sandbox": {"ok": True, "state": "armed", "quarantined": q},
            "assessment": {"line": voice.assessment_line(voice.assess(db)), **{k: v for k, v in voice.assess(db).items() if k in ("withdraw", "go")}}}

@app.get("/api/time")
def get_time(): return core.time_context(CFG)

@app.get("/api/config")
def get_config(): return CFG

@app.put("/api/config")
def put_config(cfg: dict):
    CFG.update(cfg); core._cfg_cache.update(CFG); core.save_config(CFG)
    bus.emit("config.updated", "Configuration updated."); return CFG

# ---------------- events ----------------
@app.get("/api/events")
def events(limit: int = 100):
    rows = db.all("SELECT * FROM events ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,))
    for r in rows: r["data"] = json.loads(r["data"] or "{}")
    return rows

@app.post("/api/events")
def post_event(e: EventIn): return bus.emit(e.kind, e.message, e.level)

@app.get("/api/stream")
async def stream(request: Request):
    q = bus.subscribe()
    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected(): break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally: bus.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

# ---------------- notebook ----------------
@app.get("/api/notebook")
def nb_list(): return db.all("SELECT * FROM notebook ORDER BY updated DESC")

@app.post("/api/notebook")
def nb_create(n: NoteIn):
    if n.quadrant not in core.QUADRANTS: raise HTTPException(400, "quadrant must be known/unknown/risk/opportunity")
    ts = core.now_iso()
    cur = db.q("INSERT INTO notebook(quadrant,title,body,created,updated) VALUES(?,?,?,?,?)", (n.quadrant, n.title, n.body, ts, ts))
    bus.emit("notebook.created", f"{n.quadrant.title()}: {n.title}", data={"id": cur.lastrowid})
    world_model.observe(db, "event", n.title, f"notebook/{n.quadrant}: {n.body}", "notebook", 0.9)
    return db.one("SELECT * FROM notebook WHERE id=?", (cur.lastrowid,))

@app.patch("/api/notebook/{nid}")
def nb_update(nid: int, p: NotePatch):
    row = db.one("SELECT * FROM notebook WHERE id=?", (nid,))
    if not row: raise HTTPException(404, "No match.")
    d = {k: v for k, v in p.model_dump().items() if v is not None}
    if "quadrant" in d and d["quadrant"] not in core.QUADRANTS: raise HTTPException(400, "bad quadrant")
    row.update(d); row["updated"] = core.now_iso()
    db.q("UPDATE notebook SET quadrant=?,title=?,body=?,updated=? WHERE id=?", (row["quadrant"], row["title"], row["body"], row["updated"], nid))
    bus.emit("notebook.updated", f"Entry {nid} updated.", data={"id": nid}); return row

@app.delete("/api/notebook/{nid}")
def nb_delete(nid: int):
    db.q("DELETE FROM notebook WHERE id=?", (nid,)); bus.emit("notebook.deleted", f"Entry {nid} removed."); return {"ok": True}

# ---------------- chat ----------------
@app.get("/api/transcript")
def transcript(limit: int = 200):
    return db.all("SELECT * FROM (SELECT * FROM transcript ORDER BY id DESC LIMIT ?) ORDER BY id", (limit,))

@app.post("/api/chat")
def chat(c: ChatIn):
    text = c.text.strip()
    if not text: raise HTTPException(400, "empty")
    db.q("INSERT INTO transcript(role,text,ts,source) VALUES(?,?,?,?)", ("user", text, core.now_iso(), "user"))
    bus.emit("chat.user", text[:120])
    world_model.learn_from_text(db, text)
    r = voice.reply(db, CFG, text)
    cur = db.q("INSERT INTO transcript(role,text,ts,source) VALUES(?,?,?,?)", ("atlas", r["text"], core.now_iso(), r["source"]))
    bus.emit("chat.atlas", r["text"][:120], data={"source": r["source"]})
    return {"id": cur.lastrowid, **r}

@app.delete("/api/transcript")
def clear_transcript():
    db.q("DELETE FROM transcript"); bus.emit("chat.cleared", "Transcript cleared."); return {"ok": True}

# ---------------- protocol ----------------
@app.get("/api/protocol")
def protocol():
    a = voice.assess(db)
    return {"checks": db.all("SELECT * FROM checks ORDER BY critical DESC, id"),
            "reserves": db.all("SELECT * FROM reserves"),
            "assessment": voice.assessment_line(a), "withdraw": a["withdraw"], "go": a["go"]}

@app.patch("/api/protocol/checks/{cid}")
def set_check(cid: int, p: CheckPatch):
    if p.status not in ("pending", "pass", "fail"): raise HTTPException(400, "status pending/pass/fail")
    row = db.one("SELECT * FROM checks WHERE id=?", (cid,))
    if not row: raise HTTPException(404, "No match.")
    db.q("UPDATE checks SET status=?, note=COALESCE(?,note), updated=? WHERE id=?", (p.status, p.note, core.now_iso(), cid))
    lvl = "warn" if p.status == "fail" and row["critical"] else "info"
    bus.emit("protocol.check", f"{row['label']}: {p.status}.", level=lvl)
    a = voice.assess(db)
    if a["withdraw"]: bus.emit("protocol.withdraw", voice.assessment_line(a), level="alert")
    return protocol()

@app.post("/api/protocol/checks")
def add_check(c: CheckIn):
    key = core.safe_name(c.label)[:30] + str(int(time.time()))
    db.q("INSERT INTO checks(key,label,critical,status,note,updated) VALUES(?,?,?,?,?,?)", (key, c.label, int(c.critical), "pending", "", core.now_iso()))
    bus.emit("protocol.check", f"Check added: {c.label}."); return protocol()

@app.post("/api/protocol/reset")
def reset_checks():
    db.q("UPDATE checks SET status='pending', updated=?", (core.now_iso(),))
    bus.emit("protocol.reset", "Pre-departure sequence reset."); return protocol()

@app.patch("/api/protocol/reserves/{key}")
def set_reserve(key: str, p: ReservePatch):
    row = db.one("SELECT * FROM reserves WHERE key=?", (key,))
    if not row: raise HTTPException(404, "No match.")
    v = row["value"] if p.value is None else p.value; mn = row["minimum"] if p.minimum is None else p.minimum
    db.q("UPDATE reserves SET value=?, minimum=?, updated=? WHERE key=?", (v, mn, core.now_iso(), key))
    lvl = "warn" if v < mn else "info"
    bus.emit("protocol.reserve", f"{row['label']} {v:g} {row['unit']}" + (" — below minimum." if v < mn else "."), level=lvl)
    a = voice.assess(db)
    if a["withdraw"]: bus.emit("protocol.withdraw", voice.assessment_line(a), level="alert")
    return protocol()

# ---------------- tasks ----------------
@app.get("/api/tasks")
def tasks(): return db.all("SELECT * FROM tasks ORDER BY status DESC, id DESC")

@app.post("/api/tasks")
def add_task(t: TaskIn):
    cur = db.q("INSERT INTO tasks(title,status,origin,created) VALUES(?,?,?,?)", (t.title, "pending", t.origin, core.now_iso()))
    bus.emit("task.created", t.title, data={"id": cur.lastrowid}); return db.one("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,))

@app.patch("/api/tasks/{tid}")
def upd_task(tid: int, p: TaskPatch):
    row = db.one("SELECT * FROM tasks WHERE id=?", (tid,))
    if not row: raise HTTPException(404, "No match.")
    st = p.status or row["status"]
    if st not in ("pending", "done"): raise HTTPException(400, "status pending/done")
    db.q("UPDATE tasks SET status=?, title=?, done_at=? WHERE id=?", (st, p.title or row["title"], core.now_iso() if st == "done" else None, tid))
    bus.emit("task.updated", f"Task {tid}: {'done' if st=='done' else 'pending'}."); return db.one("SELECT * FROM tasks WHERE id=?", (tid,))

@app.delete("/api/tasks/{tid}")
def del_task(tid: int):
    db.q("DELETE FROM tasks WHERE id=?", (tid,)); bus.emit("task.deleted", f"Task {tid} removed."); return {"ok": True}

# ---------------- workspace ----------------
@app.get("/api/workspace")
def ws_list(): return core.list_dir(core.WORKSPACE)

@app.get("/api/workspace/{name}")
def ws_read(name: str):
    p = core.WORKSPACE / core.safe_name(name)
    if not p.exists(): raise HTTPException(404, "No match.")
    return {"name": p.name, "content": p.read_text(encoding="utf-8", errors="replace")}

@app.put("/api/workspace")
def ws_write(f: FileIn):
    n = core.safe_name(f.name); new = not (core.WORKSPACE / n).exists()
    (core.WORKSPACE / n).write_text(f.content, encoding="utf-8")
    bus.emit("workspace.saved", f"{'Created' if new else 'Saved'} {n}."); return {"name": n}

@app.delete("/api/workspace/{name}")
def ws_delete(name: str):
    p = core.WORKSPACE / core.safe_name(name)
    if p.exists(): p.unlink()
    bus.emit("workspace.deleted", f"Removed {p.name}."); return {"ok": True}

# ---------------- quarantine ----------------
@app.get("/api/quarantine")
def q_list():
    out = []
    for f in core.list_dir(core.QUARANTINE):
        meta = core.QUARANTINE / (f["name"] + ".meta.json")
        f["origin"] = json.loads(meta.read_text())["origin"] if meta.exists() else "unknown"
        out.append(f)
    return out

@app.get("/api/quarantine/{name}")
def q_read(name: str):
    """Returned as inert text only. Never executed or rendered as HTML."""
    p = core.QUARANTINE / core.safe_name(name)
    if not p.exists(): raise HTTPException(404, "No match.")
    return {"name": p.name, "content": p.read_text(encoding="utf-8", errors="replace")[:200000]}

@app.post("/api/quarantine")
def q_add(qi: QuarantineIn):
    n = core.safe_name(qi.name) + ".quarantine"
    (core.QUARANTINE / n).write_text(qi.content, encoding="utf-8")
    (core.QUARANTINE / (n + ".meta.json")).write_text(json.dumps({"origin": qi.origin, "ts": core.now_iso()}))
    bus.emit("sandbox.quarantined", f"Untrusted burst quarantined: {n}.", level="warn"); return {"name": n}

@app.post("/api/quarantine/{name}/promote")
def q_promote(name: str, confirm: bool = False):
    if not confirm: raise HTTPException(400, "Promotion requires confirm=true.")
    p = core.QUARANTINE / core.safe_name(name)
    if not p.exists(): raise HTTPException(404, "No match.")
    target = core.safe_name(p.name.removesuffix(".quarantine"))
    (core.WORKSPACE / target).write_text(p.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    p.unlink(); m = core.QUARANTINE / (p.name + ".meta.json"); m.exists() and m.unlink()
    bus.emit("sandbox.promoted", f"{target} promoted to workspace by operator.", level="warn"); return {"name": target}

@app.delete("/api/quarantine/{name}")
def q_delete(name: str):
    p = core.QUARANTINE / core.safe_name(name)
    for f in (p, core.QUARANTINE / (p.name + ".meta.json")):
        if f.exists(): f.unlink()
    bus.emit("sandbox.purged", f"{p.name} purged."); return {"ok": True}

# ---------------- recall & council ----------------
@app.get("/api/recall")
def recall(q: str, limit: int = 10):
    hits = core.recall(db, q, limit); bus.emit("recall.query", f"Recall: {q}", data={"hits": len(hits)}); return hits

@app.post("/api/council")
def council(c: CouncilIn):
    if c.mode not in ("parallel", "council"): raise HTTPException(400, "mode parallel/council")
    return voice.council(db, CFG, c.question, c.mode)

# ---------------- world model / predictions / check-in ----------------
class ObsIn(BaseModel): kind: str; subject: str; detail: str = ""; confidence: float = 0.9
@app.get("/api/world")
def world(kind: str | None = None, limit: int = 200):
    sql = "SELECT * FROM observations" + (" WHERE kind=?" if kind else "") + " ORDER BY id DESC LIMIT ?"
    return {"summary": world_model.summary(db), "routines": world_model.routines(db),
            "observations": db.all(sql, ((kind, limit) if kind else (limit,)))}
@app.post("/api/world")
def world_add(o: ObsIn): return {"id": world_model.observe(db, o.kind, o.subject, o.detail, "user", o.confidence)}
@app.delete("/api/world/{oid}")
def world_del(oid: int): db.q("DELETE FROM observations WHERE id=?", (oid,)); bus.emit("world.forgot", f"Observation {oid} removed."); return {"ok": True}
@app.get("/api/predictions")
def predictions(): return ambient_tick()
@app.post("/api/checkin")
def checkin(note: str = ""):
    world_model.observe(db, "routine", "check-in", note or "Operator checked in.", "checkin", 1.0)
    bus.emit("time.checkin", "Check-in logged."); return {"ok": True, "message": "Logged. Check-in recorded."}

# ---------------- vision ----------------
class FrameIn(BaseModel):
    image_b64: str; source: str = "upload"; device_id: str = "local"; timestamp: str | None = None; gps: dict | None = None
def _ingest(f: FrameIn):
    try: jpeg = base64.b64decode(f.image_b64.split(",")[-1], validate=False)
    except Exception: raise HTTPException(400, "image_b64 invalid")
    if len(jpeg) > 15_000_000: raise HTTPException(413, "frame too large")
    try: meta = vision.ingest(db, CFG, jpeg, f.source, f.device_id, f.timestamp, f.gps)
    except PermissionError as e: raise HTTPException(403, str(e))
    meta["predictions"] = ambient_tick(); return meta
@app.post("/api/vision/frame")
def vision_frame(f: FrameIn): return _ingest(f)
@app.post("/api/vision/ingest")
def vision_ingest(f: FrameIn):
    """Meta glasses bridge endpoint (phone DAT companion posts here). See bridges/meta_glasses/."""
    f.source = "meta_bridge"; return _ingest(f)
@app.get("/api/vision")
def vision_state(): return {**vision.state, "config": CFG["vision"], "privacy": "ATLAS sees only when you allow it. Frames stay on this machine."}
@app.get("/api/vision/last.jpg")
def vision_last():
    if not vision.state["last"]: raise HTTPException(404, "No frame.")
    return FileResponse(vision.VISION_DIR / vision.state["last"]["name"], headers={"Cache-Control": "no-store"})
class VisionCfg(BaseModel): enabled: bool | None = None; hyperlapse: bool | None = None; source: str | None = None; interval_s: int | None = None
@app.put("/api/vision/config")
def vision_cfg(v: VisionCfg):
    CFG["vision"].update({k: x for k, x in v.model_dump().items() if x is not None})
    CFG["vision"]["interval_s"] = max(10, min(3600, int(CFG["vision"]["interval_s"])))
    if not CFG["vision"]["enabled"]: CFG["vision"]["hyperlapse"] = False
    core.save_config(CFG)
    if CFG["vision"]["enabled"] and CFG["vision"]["hyperlapse"] and CFG["vision"]["source"] == "webcam": vision.start_hyperlapse(db, CFG)
    else: vision.stop_hyperlapse()
    vision.state["led"] = "SEEING" if CFG["vision"]["hyperlapse"] else "IDLE"
    bus.emit("vision.config", f"Vision {'enabled' if CFG['vision']['enabled'] else 'disabled'}; hyperlapse {'on every ' + str(CFG['vision']['interval_s']) + ' s' if CFG['vision']['hyperlapse'] else 'off'}.", level="warn")
    return vision_state()
@app.delete("/api/vision")
def vision_clear(): return {"cleared": vision.clear()}

# ---------------- static UI ----------------
STATIC = core.ROOT / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")
@app.get("/")
def index(): return FileResponse(STATIC / "index.html")

def _open_atlas_ui(url: str) -> None:
    """Prefer a real browser — avoid apps that hijack the http handler (e.g. DeepSeek)."""
    if sys.platform == "win32":
        import shutil, subprocess
        candidates = [
            shutil.which("msedge"),
            shutil.which("chrome"),
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        for exe in candidates:
            if exe and Path(exe).exists():
                try:
                    subprocess.Popen([exe, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except OSError:
                    pass
        # Last resort: explorer (still may hit a hijacked default)
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return
        except OSError:
            pass
    webbrowser.open(url)


if __name__ == "__main__":
    host, port = CFG.get("host", "127.0.0.1"), int(CFG.get("port", 8765))
    url = f"http://{host}:{port}/"
    if "--no-browser" not in sys.argv and CFG.get("open_browser", True):
        threading.Timer(1.5, lambda: _open_atlas_ui(url)).start()
    print(f"ATLAS {VERSION} — {url}  (Ctrl+C to stop)")
    print("If DeepSeek or another app opens: leave this window running, then paste that URL into Edge or Chrome.")
    uvicorn.run(app, host=host, port=port, log_level="warning")
