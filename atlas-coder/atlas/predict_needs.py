"""Ranked predicted needs from time, FIFO context, protocol state and observations.
Each prediction carries a confidence and a basis. Inference, not fact."""
from . import core, world_model

def predict(db, cfg):
    tc = core.time_context(cfg); out = []
    def add(need, conf, basis, priority=False): out.append({"need": need, "confidence": conf, "basis": basis, "priority": priority})
    m = tc.get("minutes_to_checkin")
    if m is not None:
        if m <= 30: add(f"Check-in due {tc['checkin']}", 95, f"{m} minutes to scheduled check-in", True)
        elif m <= 120: add(f"Prepare check-in for {tc['checkin']}", 70, f"{m} minutes out")
    last_ci = db.one("SELECT ts FROM observations WHERE subject LIKE '%check%in%' ORDER BY id DESC LIMIT 1")
    if m is not None and m > 1380 and not (last_ci and last_ci["ts"][:10] == tc["iso"][:10]):
        add("Check-in missed — confirm with base", 90, "No check-in observed today", True)
    checks = db.all("SELECT * FROM checks")
    fails = [c for c in checks if c["critical"] and c["status"] == "fail"]
    if fails: add("Withdraw or remedy: " + fails[0]["label"], 92, "critical check failed", True)
    for r in db.all("SELECT * FROM reserves"):
        if r["value"] < r["minimum"]: add(f"{r['label']} below minimum — recommend withdrawal", 90, f"{r['value']:g} < {r['minimum']:g} {r['unit']}", True)
        elif r["value"] < r["minimum"] * 1.3: add(f"{r['label']} audit", 65, "within 30% of minimum")
    if tc["phase"] in ("last light",): add("Daylight closing — plan the return leg", 75, "phase: last light")
    if tc["phase"] == "first light": add("Pre-departure sequence", 70, "phase: first light")
    pend = [c for c in checks if c["critical"] and c["status"] == "pending"]
    if pend and tc["phase"] in ("first light", "morning"): add(f"{len(pend)} critical checks outstanding", 68, "morning departure window")
    f = tc.get("fifo", "")
    if f.startswith("off swing") and f"day {cfg.get('fifo',{}).get('days_off',7)} of" in f: add("Fly-in tomorrow — pack and confirm flight", 80, f)
    if f.startswith("on swing") and tc["phase"] in ("first light",): add("Leave for shift", 72, f)
    q = len(core.list_dir(core.QUARANTINE))
    if q: add(f"Review {q} quarantined item{'s'*(q!=1)}", 55, "sandbox holding")
    for r in world_model.routines(db):
        if abs(r["hour"] - core.now().hour) <= 1: add(f"You usually: {r['subject']}", min(85, 50 + 10 * r["count"]), f"seen {r['count']}× near {r['hour']:02d}00 (inferred)")
    out.sort(key=lambda p: (-p["priority"], -p["confidence"]))
    return out[:8]
