"""Vision pipeline. Frames stay on local disk (data/vision/). Never uploaded.
Hyperlapse, not video: one still every interval_s (30 s – 15 min). Sources: upload
(POST /api/vision/frame), webcam (OpenCV snap per interval, optional), meta_bridge (phone DAT app
posts a capturePhoto still per interval to /api/vision/ingest). Captioning via an Ollama vision model if present."""
import base64, json, threading, time, urllib.request
from .. import core, world_model

VISION_DIR = core.DATA / "vision"
state = {"led": "IDLE", "last": None, "frames": 0, "webcam": "not started", "error": None}
_thread = None; _stop = threading.Event()

def cfgv(cfg): return {"enabled": False, "source": "upload", "hyperlapse": False, "interval_s": 120, "model": "llava", "keep_frames": 200, **cfg.get("vision", {})}

def caption(cfg, jpeg: bytes) -> tuple[str, str]:
    o = cfg.get("ollama", {})
    if o.get("enabled"):
        try:
            body = json.dumps({"model": cfgv(cfg)["model"], "stream": False, "images": [base64.b64encode(jpeg).decode()],
                "prompt": "Describe this scene in one or two short factual sentences. List notable people, places and objects. No speculation."}).encode()
            req = urllib.request.Request(o.get("url", "http://127.0.0.1:11434") + "/api/generate", body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r: return json.loads(r.read())["response"].strip(), "ollama"
        except Exception: pass
    return "Frame logged, vision model offline.", "placeholder"

def ingest(db, cfg, jpeg: bytes, source="upload", device_id="local", ts=None, gps=None):
    v = cfgv(cfg)
    if not v["enabled"]: raise PermissionError("Vision disabled. ATLAS sees only when you allow it.")
    VISION_DIR.mkdir(parents=True, exist_ok=True)
    state["led"] = "SEEING"
    name = f"{int(time.time()*1000)}-{core.safe_name(device_id)[:20].replace('.md','')}.jpg"
    (VISION_DIR / name).write_bytes(jpeg)
    files = sorted(VISION_DIR.glob("*.jpg"))
    for f in files[:-int(v["keep_frames"])]: f.unlink()
    state["led"] = "LEARNING"
    text, how = caption(cfg, jpeg)
    meta = {"name": name, "caption": text, "captioner": how, "source": source, "device_id": device_id, "ts": ts or core.now_iso(), "gps": gps}
    (VISION_DIR / (name + ".json")).write_text(json.dumps(meta))
    meta["observation_id"] = world_model.observe(db, "scene", text[:120], json.dumps({k: meta[k] for k in ("name","source","device_id","gps")}), f"vision:{source}", 0.8 if how == "ollama" else 0.3, inferred=how == "ollama")
    state.update(last=meta, frames=state["frames"] + 1, led="IDLE" if not v["hyperlapse"] else "SEEING", next_at=None)
    core.bus.emit("vision.frame", f"Frame from {source}: {text[:80]}", data={"name": name})
    return meta

def clear():
    n = 0
    if VISION_DIR.exists():
        for f in VISION_DIR.iterdir(): f.unlink(); n += 1
    state.update(last=None, frames=0); core.bus.emit("vision.cleared", "Vision frames cleared from disk."); return n

INTERVALS = [30, 60, 120, 300, 900]

def snap_webcam():
    """Grab ONE still: open camera, read, release. No video loop held open."""
    import cv2
    cap = cv2.VideoCapture(0)
    try:
        if not cap.isOpened(): return None
        for _ in range(3): cap.read()  # let exposure settle
        ok, frame = cap.read()
        if not ok: return None
        ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes() if ok2 else None
    finally: cap.release()

def start_hyperlapse(db, cfg):
    """Timer-based still capture from the local webcam. Requires opencv-python (optional).
    For upload / meta_bridge sources, stills arrive via the API on the same interval."""
    global _thread
    try: import cv2  # noqa
    except ImportError:
        state["webcam"] = "opencv not installed (pip install opencv-python)"; return False
    if _thread and _thread.is_alive(): return True
    _stop.clear()
    def run():
        state["webcam"] = "hyperlapse running"
        while not _stop.is_set():
            try:
                jpeg = snap_webcam()
                if jpeg: ingest(db, cfg, jpeg, "webcam", "webcam0")
                else: state["webcam"] = "no camera"
            except Exception as e: state["error"] = str(e)
            iv = int(cfgv(cfg)["interval_s"]); state["next_at"] = time.time() + iv
            _stop.wait(iv)
        state["webcam"] = "stopped"
    _thread = threading.Thread(target=run, daemon=True); _thread.start(); return True

def stop_hyperlapse(): _stop.set(); state["led"] = "IDLE"; state["next_at"] = None
