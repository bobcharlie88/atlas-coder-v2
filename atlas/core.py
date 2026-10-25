"""Core subsystems: config, event bus, time awareness, storage, recall."""
import asyncio, json, math, os, re, sqlite3, threading, time, uuid
from collections import Counter
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("ATLAS_DATA", ROOT / "data"))
WORKSPACE = DATA / "workspace"
QUARANTINE = DATA / "quarantine"
CONFIG_PATH = ROOT / "config.json"
EXAMPLE_PATH = ROOT / "config.example.json"

# ---------------- config ----------------
def load_config() -> dict:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

# ---------------- event bus ----------------
class EventBus:
    """In-process pub/sub. Every action emits. Events persisted to the log."""
    def __init__(self):
        self.subscribers: set[asyncio.Queue] = set()
        self.local: list = []          # sync callbacks
        self.loop = None
        self.db = None
        self.count = 0

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=500); self.subscribers.add(q); return q

    def unsubscribe(self, q): self.subscribers.discard(q)

    def emit(self, kind: str, message: str, level: str = "info", data: dict | None = None) -> dict:
        ev = {"id": uuid.uuid4().hex[:12], "ts": now_iso(), "kind": kind,
              "level": level, "message": message, "data": data or {}}
        self.count += 1
        if self.db: self.db.insert_event(ev)
        for cb in self.local:
            try: cb(ev)
            except Exception: pass
        for q in list(self.subscribers):
            try:
                if self.loop and self.loop.is_running():
                    self.loop.call_soon_threadsafe(q.put_nowait, ev)
                else: q.put_nowait(ev)
            except Exception: pass
        return ev

bus = EventBus()

# ---------------- time awareness ----------------
_cfg_cache = {}
def tz() -> ZoneInfo:
    return ZoneInfo(_cfg_cache.get("timezone", "Australia/Perth"))

def now() -> datetime: return datetime.now(tz())
def now_iso() -> str: return now().isoformat(timespec="seconds")

PHASES = [(0, "night"), (5, "first light"), (7, "morning"), (12, "midday"),
          (14, "afternoon"), (17, "last light"), (19, "evening"), (22, "night")]

def day_phase(dt: datetime) -> str:
    p = "night"
    for h, name in PHASES:
        if dt.hour >= h: p = name
    return p

def time_context(cfg: dict) -> dict:
    dt = now()
    ctx = {"iso": dt.isoformat(timespec="seconds"), "time": dt.strftime("%H%M"),
           "date": dt.strftime("%a %d %b %Y"), "tz": cfg.get("timezone"),
           "phase": day_phase(dt)}
    ci = cfg.get("checkin_time", "1900")
    try:
        ch, cm = int(ci[:2]), int(ci[2:])
        mins = (ch * 60 + cm) - (dt.hour * 60 + dt.minute)
        ctx["checkin"] = ci
        ctx["minutes_to_checkin"] = mins if mins >= 0 else mins + 1440
    except Exception: pass
    fifo = cfg.get("fifo", {})
    if fifo.get("enabled"):
        try:
            start = date.fromisoformat(fifo["roster_start"])
            on, off = int(fifo.get("days_on", 14)), int(fifo.get("days_off", 7))
            cyc = (dt.date() - start).days % (on + off)
            if cyc < on:
                ctx["fifo"] = f"on swing, day {cyc+1} of {on}"
            else:
                ctx["fifo"] = f"off swing, day {cyc-on+1} of {off}"
        except Exception as e:
            ctx["fifo"] = "roster indeterminate"
    return ctx

# ---------------- storage ----------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS notebook(id INTEGER PRIMARY KEY, quadrant TEXT, title TEXT, body TEXT, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS transcript(id INTEGER PRIMARY KEY, role TEXT, text TEXT, ts TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, ts TEXT, kind TEXT, level TEXT, message TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, title TEXT, status TEXT, origin TEXT, created TEXT, done_at TEXT);
CREATE TABLE IF NOT EXISTS checks(id INTEGER PRIMARY KEY, key TEXT UNIQUE, label TEXT, critical INTEGER, status TEXT, note TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS reserves(key TEXT PRIMARY KEY, label TEXT, value REAL, unit TEXT, minimum REAL, updated TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
QUADRANTS = ("known", "unknown", "risk", "opportunity")

class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock: self.conn.executescript(SCHEMA); self.conn.commit()

    def q(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args); self.conn.commit()
            return cur

    def all(self, sql, args=()): return [dict(r) for r in self.q(sql, args).fetchall()]
    def one(self, sql, args=()):
        r = self.q(sql, args).fetchone(); return dict(r) if r else None

    def insert_event(self, ev):
        self.q("INSERT INTO events VALUES(?,?,?,?,?,?)", (ev["id"], ev["ts"], ev["kind"], ev["level"], ev["message"], json.dumps(ev["data"])))

    def meta(self, key, value=None):
        if value is None:
            r = self.one("SELECT value FROM meta WHERE key=?", (key,)); return r["value"] if r else None
        self.q("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, value))

# ---------------- workspace / quarantine files ----------------
SAFE = re.compile(r"[^A-Za-z0-9._ -]")
def safe_name(name: str) -> str:
    n = SAFE.sub("_", Path(name).name).strip(" .")
    if not n: raise ValueError("invalid name")
    return n if "." in n else n + ".md"

def list_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(p.iterdir()):
        if f.is_file() and not f.name.endswith(".meta.json"):
            st = f.stat()
            out.append({"name": f.name, "size": st.st_size,
                        "modified": datetime.fromtimestamp(st.st_mtime, tz()).isoformat(timespec="seconds")})
    return out

# ---------------- recall (TF-IDF, embedding-free) ----------------
STOP = set("the a an and or of to in on at for is are was were be it this that with as by from what did we log about any have has do does how why when where who our my your i you me".split())
def tokens(t: str): return [w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in STOP and len(w) > 1]

def recall(db: DB, query: str, limit: int = 8):
    docs = []
    for r in db.all("SELECT id,quadrant,title,body,updated FROM notebook"):
        docs.append(("notebook", f"{r['quadrant']}:{r['id']}", r["title"], f"{r['title']} {r['body']}", r["updated"]))
    for r in db.all("SELECT id,role,text,ts FROM transcript"):
        docs.append(("transcript", f"{r['role']}:{r['id']}", r["text"][:60], r["text"], r["ts"]))
    for r in db.all("SELECT id,title,status,created FROM tasks"):
        docs.append(("task", str(r["id"]), r["title"], r["title"], r["created"]))
    for f in list_dir(WORKSPACE):
        try: txt = (WORKSPACE / f["name"]).read_text(encoding="utf-8", errors="ignore")
        except Exception: continue
        docs.append(("workspace", f["name"], f["name"], f["name"] + " " + txt, f["modified"]))
    qt = tokens(query)
    if not qt or not docs: return []
    toks = [Counter(tokens(d[3])) for d in docs]
    N = len(docs)
    df = Counter(w for t in toks for w in set(t))
    res = []
    for d, tf in zip(docs, toks):
        if not tf: continue
        score = 0.0
        for w in qt:
            # allow prefix match for simple stemming
            hits = tf.get(w, 0) or sum(c for k, c in tf.items() if k.startswith(w[:5]) and len(w) >= 5)
            if hits:
                score += (1 + math.log(hits)) * math.log(1 + N / (1 + df.get(w, 1)))
        if score > 0:
            score /= math.sqrt(sum(tf.values())) ** 0.5
            text = d[3]
            i = max(0, min((text.lower().find(w) for w in qt if w in text.lower()), default=0) - 40)
            res.append({"source": d[0], "ref": d[1], "title": d[2], "snippet": text[i:i+180], "ts": d[4], "score": round(score, 3)})
    res.sort(key=lambda r: -r["score"])
    return res[:limit]
