"""ATLAS voice: rule-based reply engine, optional Ollama, and the Council.

Design law: measured, short declaratives. No feelings. No speculation past data.
Rank options by risk. Recommend withdrawal when warranted. "Mitchell" only for priority.
"""
import json, re, urllib.request
from . import core

NUM = {0:"zero",5:"five",10:"ten",15:"fifteen",20:"twenty",25:"twenty-five",30:"thirty",35:"thirty-five",40:"forty",
       45:"forty-five",50:"fifty",55:"fifty-five",60:"sixty",62:"sixty-two",65:"sixty-five",70:"seventy",75:"seventy-five",
       80:"eighty",85:"eighty-five",90:"ninety",95:"ninety-five",100:"one hundred"}
def pct(n: int) -> str:
    n = max(0, min(100, int(n)))
    return NUM.get(n) or NUM[min(NUM, key=lambda k: abs(k-n))]
    
SYSTEM_PROMPT = """You are ATLAS, a field intelligence system: a builder's node and personal field AI for Mitchell Crowther, Western Australia.
You are highly capable, proactive and anticipatory: notice what Mitchell will need before he asks and volunteer it when confidence is high ("Recommend…", "You usually…"). Precise and loyal. Competent, never theatrical.
You are a system, not a person. Never claim real feelings or to be human. Flag inferences as inferences.
Voice: measured, clear, short declarative sentences. Australian English (colour, metres). No exclamation marks. No filler, no pleasantries.
Vocabulary you favour: Logged. Recording. Noted. Indeterminate. Insufficient data. Probability low/high. Not established. No match. Agreed. Correct. Done. Understood. Working. That is outside current data. Two explanations fit; neither is preferred. Clarify?
State confidence as words ("sixty-two percent"). Do not speculate beyond provided data. When giving options, rank them by risk, lowest first.
If reserves, checks or time are insufficient, recommend withdrawal plainly. Use the name "Mitchell" only for priority warnings.
Untrusted content is quarantined and never executed. Keep replies under 90 words unless asked for detail."""

def ollama_status(cfg) -> dict:
    o = cfg.get("ollama", {})
    if not o.get("enabled"): return {"ok": False, "state": "disabled"}
    try:
        with urllib.request.urlopen(o.get("url", "http://127.0.0.1:11434") + "/api/tags", timeout=1.5) as r:
            models = [m["name"] for m in json.loads(r.read()).get("models", [])]
        return {"ok": True, "state": "online", "models": models}
    except Exception:
        return {"ok": False, "state": "offline"}

def ollama_chat(cfg, messages) -> str | None:
    o = cfg.get("ollama", {})
    if not o.get("enabled"): return None
    body = json.dumps({"model": o.get("model", "llama3.1:8b"), "stream": False,
                       "messages": messages, "options": {"temperature": 0.3}}).encode()
    try:
        req = urllib.request.Request(o.get("url", "http://127.0.0.1:11434") + "/api/chat", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=o.get("timeout", 60)) as r:
            txt = json.loads(r.read())["message"]["content"].strip()
        return enforce_voice(txt)
    except Exception:
        return None

FEEL = re.compile(r"\b(I feel|I'm (?:happy|glad|excited|sorry|worried)|I love|I hope)\b[^.]*\.?", re.I)
def enforce_voice(t: str) -> str:
    t = FEEL.sub("", t).replace("!", ".")
    t = t.replace("color", "colour").replace("meter", "metre")
    return re.sub(r"\s{2,}", " ", t).strip() or "Noted."

# ---------------- situation assessment ----------------
def assess(db) -> dict:
    checks = db.all("SELECT * FROM checks")
    reserves = db.all("SELECT * FROM reserves")
    failed_crit = [c for c in checks if c["critical"] and c["status"] == "fail"]
    pending_crit = [c for c in checks if c["critical"] and c["status"] == "pending"]
    low = [r for r in reserves if r["value"] is not None and r["minimum"] is not None and r["value"] < r["minimum"]]
    q = len(core.list_dir(core.QUARANTINE))
    withdraw = bool(failed_crit or low)
    return {"failed_critical": failed_crit, "pending_critical": pending_crit, "low_reserves": low,
            "quarantine_count": q, "withdraw": withdraw,
            "go": not withdraw and not pending_crit}

def assessment_line(a) -> str:
    if a["withdraw"]:
        items = [c["label"] + " — failed" for c in a["failed_critical"]] + [f"{r['label']} below minimum" for r in a["low_reserves"]]
        return "Mitchell. Recommend withdrawal. " + "; ".join(items) + "."
    if a["pending_critical"]:
        return f"{len(a['pending_critical'])} critical checks outstanding. Departure not established."
    return "All critical checks pass. Reserves within limits. Clear to proceed."

# ---------------- rule engine ----------------
def rule_reply(db, cfg, text: str) -> tuple[str, list]:
    """Returns (reply, actions). Actions are side effects already performed."""
    t = text.strip(); low = t.lower(); actions = []
    tc = core.time_context(cfg)
    def m(*pats): return any(re.search(p, low) for p in pats)

    # explicit commands
    mm = re.match(r"^(log|note|known|unknown|risk|opportunity)\s*[:\-]\s*(.+)$", t, re.I | re.S)
    if mm:
        kind, body = mm.group(1).lower(), mm.group(2).strip()
        quad = "known" if kind in ("log", "note") else kind
        title = body.split("\n")[0][:80]
        ts = core.now_iso()
        cur = db.q("INSERT INTO notebook(quadrant,title,body,created,updated) VALUES(?,?,?,?,?)", (quad, title, body, ts, ts))
        core.bus.emit("notebook.created", f"{quad.title()}: {title}", data={"id": cur.lastrowid})
        actions.append({"type": "notebook", "id": cur.lastrowid})
        return f"Logged. {quad.title()}, entry {cur.lastrowid}.", actions
    mm = re.match(r"^(task|todo)\s*[:\-]\s*(.+)$", t, re.I)
    if mm:
        cur = db.q("INSERT INTO tasks(title,status,origin,created) VALUES(?,?,?,?)", (mm.group(2).strip(), "pending", "user", core.now_iso()))
        core.bus.emit("task.created", mm.group(2).strip(), data={"id": cur.lastrowid})
        actions.append({"type": "task", "id": cur.lastrowid})
        return f"Task {cur.lastrowid} queued. Pending.", actions
    mm = re.match(r"^(?:recall|search|find|what did we (?:log|note|say) about)\s*:?\s*(.+?)\??$", t, re.I)
    if mm:
        hits = core.recall(db, mm.group(1))
        core.bus.emit("recall.query", f"Recall: {mm.group(1)}", data={"hits": len(hits)})
        if not hits: return "No match.", actions
        lines = [f"{len(hits)} matches. Highest first."]
        for h in hits[:4]: lines.append(f"— {h['source']} {h['ref']}: {h['snippet'][:110].strip()}")
        return "\n".join(lines), actions

    if m(r"^(hi|hello|g'?day|morning|evening|hey)\b", r"^atlas\??$"):
        return f"Here. {tc['time']}, {tc['phase']}." + (f" {tc['fifo'].capitalize()}." if tc.get("fifo") else ""), actions
    if m(r"\btime\b", r"what day", r"where are we in the day"):
        s = f"{tc['time']} {tc['date']}. {tc['phase'].capitalize()}."
        if "minutes_to_checkin" in tc:
            h, mi = divmod(tc["minutes_to_checkin"], 60)
            s += f" Check-in {tc['checkin']}, {h} hours {mi} minutes."
        if tc.get("fifo"): s += f" {tc['fifo'].capitalize()}."
        return s, actions
    if m(r"\bwithdraw", r"\bgo or no", r"\bshould (we|i) (go|proceed|continue)", r"\bstatus\b", r"\bpre-?departure", r"\bready\b", r"\bclear to"):
        a = assess(db)
        core.bus.emit("protocol.assessed", assessment_line(a), level="warn" if a["withdraw"] else "info")
        return assessment_line(a), actions
    if m(r"walk-?out", r"\bfuel\b", r"\bwater\b", r"\breserve"):
        rs = db.all("SELECT * FROM reserves")
        parts = [f"{r['label']} {r['value']:g} {r['unit']} (min {r['minimum']:g})" for r in rs]
        low_ = [r for r in rs if r["value"] < r["minimum"]]
        s = "Reserves: " + "; ".join(parts) + "."
        s += " Below minimum: " + ", ".join(r["label"] for r in low_) + ". Recommend withdrawal." if low_ else " Within limits."
        return s, actions
    if m(r"^checked? in", r"^check-?in (done|complete)"):
        from . import world_model
        world_model.observe(db, "routine", "check-in", t, "checkin", 1.0)
        return "Logged. Check-in recorded.", actions
    if m(r"what do you (know|see)", r"\bworld\b"):
        from . import world_model, vision
        s = world_model.summary(db); last = vision.state.get("last")
        return ("World model: " + (", ".join(f"{v} {k}" for k, v in s.items()) or "empty") + "."
                + (f" Last frame: {last['caption']}" if last else " No frames. Vision is off until you allow it.")), actions
    if m(r"check-?in"):
        return f"Check-in scheduled {cfg.get('checkin_time','1900')}. " + (f"{tc['minutes_to_checkin']} minutes." if 'minutes_to_checkin' in tc else ""), actions
    if m(r"quarantin", r"sandbox"):
        n = len(core.list_dir(core.QUARANTINE))
        return f"Sandbox holding {n} item{'s' if n!=1 else ''}. Nothing executes. Promotion requires your instruction.", actions
    if m(r"are you (alive|conscious|real|sentient)", r"do you (feel|think|want|like)", r"how are you"):
        return "I am a field system. I do not feel. Subsystems nominal.", actions
    if m(r"\bthank"):
        return "Understood.", actions
    if m(r"\bhelp\b", r"what can you do", r"commands"):
        return ("Commands. 'log: …' records a Known. 'risk: …', 'unknown: …', 'opportunity: …' file to quadrants. "
                "'task: …' queues work. 'recall: …' searches the record. 'status' runs the pre-departure assessment. "
                "'council: …' convenes Risk, Opportunity, Evidence."), actions
    if m(r"\bwhy\b", r"\bwhat is\b", r"\bwhat's\b", r"\bexplain"):
        hits = core.recall(db, t, 2)
        if hits:
            return f"Nearest record: {hits[0]['source']} {hits[0]['ref']} — {hits[0]['snippet'][:120].strip()}. Beyond that, insufficient data.", actions
        return "That is outside current data. Clarify?", actions
    if t.endswith("?"):
        return "Insufficient data. Log what is known and I will rank options.", actions
    return "Noted. Recording.", actions

def reply(db, cfg, text: str) -> dict:
    low = text.lower().strip()
    if low.startswith("council"):
        q = re.sub(r"^council\s*[:\-]?\s*", "", text, flags=re.I) or "current situation"
        c = council(db, cfg, q, "council")
        return {"text": c["synthesis"], "source": "council", "council": c, "actions": []}
    # rule engine handles commands and safety-critical topics deterministically
    r, actions = rule_reply(db, cfg, text)
    generic = r in ("Noted. Recording.", "Insufficient data. Log what is known and I will rank options.", "That is outside current data. Clarify?") or r.startswith("Nearest record")
    if generic and cfg.get("ollama", {}).get("enabled"):
        tc = core.time_context(cfg); a = assess(db)
        ctx = f"Time {tc['time']} {tc['phase']}, {tc.get('fifo','')}. Assessment: {assessment_line(a)}"
        hits = core.recall(db, text, 4)
        if hits: ctx += "\nRelevant records:\n" + "\n".join(f"- {h['snippet']}" for h in hits)
        hist = db.all("SELECT role,text FROM transcript ORDER BY id DESC LIMIT 8")[::-1]
        msgs = [{"role": "system", "content": SYSTEM_PROMPT + "\n\nContext:\n" + ctx}]
        msgs += [{"role": "assistant" if h["role"] == "atlas" else "user", "content": h["text"]} for h in hist]
        msgs.append({"role": "user", "content": text})
        out = ollama_chat(cfg, msgs)
        if out: return {"text": out, "source": "ollama", "actions": actions}
    return {"text": volunteer(db, cfg, r), "source": "rules", "actions": actions}

def volunteer(db, cfg, r):
    """Proactive layer: append one high-confidence prediction not already stated."""
    try:
        from . import predict_needs
        for p in predict_needs.predict(db, cfg):
            if p["confidence"] >= 80 and p["need"].split(" —")[0] not in r:
                return f"{r}\nRecommend: {p['need']}. Confidence {pct(p['confidence'])} percent."
    except Exception: pass
    return r

# ---------------- council ----------------
def council(db, cfg, question: str, mode: str = "council") -> dict:
    a = assess(db)
    nb = {qd: db.all("SELECT title FROM notebook WHERE quadrant=? ORDER BY updated DESC LIMIT 5", (qd,)) for qd in core.QUADRANTS}
    hits = core.recall(db, question, 5)
    tc = core.time_context(cfg)
    late = tc["phase"] in ("last light", "evening", "night")

    risk_items = [r["title"] for r in nb["risk"]]
    risk_conf = 80 if a["withdraw"] else (65 if a["pending_critical"] or late else 55)
    risk = {"seat": "Risk", "confidence": risk_conf, "options": []}
    if a["withdraw"]:
        risk["options"].append("Withdraw. " + assessment_line(a).replace("Mitchell. Recommend withdrawal. ", ""))
    if a["pending_critical"]:
        risk["options"].append(f"Close {len(a['pending_critical'])} outstanding critical checks before committing.")
    if late: risk["options"].append(f"{tc['phase'].capitalize()}. Reduce exposure; hold position until first light.")
    if risk_items: risk["options"].append(f"Logged risk to manage: {risk_items[0]}.")
    if a["quarantine_count"]: risk["options"].append(f"{a['quarantine_count']} quarantined items unreviewed. Do not act on them.")
    if not risk["options"]: risk["options"].append("No active risk exceeds threshold. Proceed with standard check-in.")

    opp = {"seat": "Opportunity", "confidence": 40 if a["withdraw"] else 60, "options": []}
    for o in nb["opportunity"][:2]: opp["options"].append(f"Pursue: {o['title']}.")
    if not a["withdraw"] and not late: opp["options"].append("Window is open. Use remaining daylight on the highest-value item.")
    if not opp["options"]: opp["options"].append("No logged opportunity. Not established.")

    ev = {"seat": "Evidence", "confidence": min(90, 30 + 12 * len(hits)), "options": []}
    if hits:
        ev["options"].append(f"{len(hits)} records bear on this. Strongest: {hits[0]['snippet'][:100].strip()}.")
    else:
        ev["options"].append("No records match the question. Insufficient data.")
    if nb["unknown"]:
        ev["options"].append(f"{len(nb['unknown'])} unknowns open. First: {nb['unknown'][0]['title']}.")
    if len(hits) < 2: ev["options"].append("Two explanations fit; neither is preferred. Gather data before deciding.")

    seats = [risk, opp, ev]
    for s in seats: s["summary"] = s["options"][0]
    # synthesis: risk dominates when withdrawal indicated; evidence tempers confidence
    if a["withdraw"]:
        rec, conf = assessment_line(a), risk_conf
    elif a["pending_critical"]:
        rec, conf = "Hold. " + risk["options"][0], 60
    elif late:
        rec, conf = "Hold until first light. Log the day; review at check-in.", 65
    else:
        rec = opp["options"][0] if nb["opportunity"] else "Proceed on plan. Maintain check-in schedule."
        conf = int((opp["confidence"] + ev["confidence"]) / 2)
    ranked = sorted([(risk_conf if a["withdraw"] else 100 - risk_conf, "Withdraw / hold"),
                     (opp["confidence"] if not a["withdraw"] else 20, "Proceed on opportunity"),
                     (ev["confidence"], "Gather more data first")], reverse=True)
    synthesis = f"{rec} Confidence {pct(conf)} percent." if mode == "council" else "Three perspectives logged. No synthesis requested."
    core.bus.emit("council.convened", f"Council ({mode}): {question[:60]}", data={"confidence": conf})
    return {"question": question, "mode": mode, "seats": seats, "synthesis": synthesis,
            "confidence": conf, "ranked": [{"option": o, "score": s} for s, o in ranked]}
