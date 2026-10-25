"""Simulate the phone bridge: post a JPEG to ATLAS /api/vision/ingest."""
import base64, json, sys, urllib.request
from datetime import datetime, timezone
path = sys.argv[1]; url = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8765"
body = {"image_b64": base64.b64encode(open(path, "rb").read()).decode(), "device_id": "simulated-glasses",
        "timestamp": datetime.now(timezone.utc).isoformat()}
req = urllib.request.Request(url + "/api/vision/ingest", json.dumps(body).encode(), {"Content-Type": "application/json"})
print(json.loads(urllib.request.urlopen(req).read())["caption"])
