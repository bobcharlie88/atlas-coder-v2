"""World model: observations about Mitchell's environment (people, places, objects,
routines, preferences, events). Fed by chat facts, notebook, check-ins and vision.
Everything stays in local SQLite. Inferences are flagged (inferred=1)."""
import re
from collections import Counter
from . import core

KINDS = ("person", "place", "object", "routine", "preference", "event", "scene")
SCHEMA = """CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, kind TEXT, subject TEXT, detail TEXT,
 source TEXT, confidence REAL, inferred INTEGER, ts TEXT, hour INTEGER, weekday INTEGER);"""

def init(db): db.q(SCHEMA)

def observe(db, kind, subject, detail="", source="chat", confidence=0.7, inferred=False):
    kind = kind if kind in KINDS else "event"
    dt = core.now()
    cur = db.q("INSERT INTO observations(kind,subject,detail,source,confidence,inferred,ts,hour,weekday) VALUES(?,?,?,?,?,?,?,?,?)",
               (kind, subject[:120], detail[:1000], source, confidence, int(inferred), dt.isoformat(timespec="seconds"), dt.hour, dt.weekday()))
    core.bus.emit("world.observed", f"{kind}: {subject[:80]}" + (" (inferred)" if inferred else ""), data={"id": cur.lastrowid, "source": source})
    return cur.lastrowid

# lightweight fact extraction from chat (no model needed)
PATTERNS = [
    (r"\bi (?:usually|always|normally) (.+)", "routine", 0.75),
    (r"\bi (?:like|prefer|love) (.+)", "preference", 0.7),
    (r"\bmy ([a-z ]{2,30}?) is (.+)", "object", 0.7),
    (r"\b(?:i'm|i am) (?:at|in|heading to|going to) (.+)", "place", 0.65),
    (r"\b([A-Z][a-z]+) is (?:my|the) ([a-z ]+)", "person", 0.65),
    (r"\bchecked? in\b", "routine", 0.8),
]
def learn_from_text(db, text, source="chat"):
    ids = []
    for pat, kind, conf in PATTERNS:
        m = re.search(pat, text, re.I if kind != "person" else 0)
        if m:
            subj = " ".join(g for g in m.groups() if g) if m.groups() else text
            ids.append(observe(db, kind, subj.strip(" .?!"), text, source, conf, inferred=True))
    return ids

def summary(db):
    rows = db.all("SELECT kind, COUNT(*) n FROM observations GROUP BY kind")
    return {r["kind"]: r["n"] for r in rows}

def routines(db):
    """Infer habits: subjects recurring at similar hours. Flagged as inference."""
    rows = db.all("SELECT subject, hour FROM observations WHERE kind IN ('routine','event','scene')")
    c = Counter((r["subject"].lower()[:40], r["hour"]) for r in rows)
    return [{"subject": s, "hour": h, "count": n} for (s, h), n in c.most_common(10) if n >= 2]
