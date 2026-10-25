# ATLAS — Field intelligence, builder's node (v2.0)

A personal field AI for Mitchell Crowther (Perth WA), built from the design law of *The Atlas Expedition*.
Capable, proactive and anticipatory, precise and loyal, offline-first and sandboxed.
ATLAS is a system, not a person. It does not claim feelings, flags its inferences, and says "Mitchell" only when something matters.

Runs fully offline: Python server, SQLite, plain HTML/CSS/JS with no CDN. Ollama is optional.

## Run
**Windows:** install Python 3.10+ from python.org (tick "Add to PATH"), then double-click `START.bat`.
**macOS / Linux:** `./start.sh`
Both scripts create `.venv`, install `requirements.txt`, start the server and open http://127.0.0.1:8765/.
Add `--no-browser` if you don't want a browser window to open. Manual start: `pip install -r requirements.txt && python app.py`.

**First run:** `config.json` is created from `config.example.json`, and `data/` is created with the SQLite DB, workspace, quarantine and vision folders. ATLAS seeds a starter notebook, pre-departure checks, reserves and tasks. Its first line is "Platform online. Pre-departure sequence available."
To reset, delete `data/`.

## How it maps to the book
| Book | App |
|---|---|
| Notebook quadrants Known / Unknown / Risk / Opportunity | Notebook board; chat `known: …`, `risk: …`, `log: …` |
| Pre-departure checks, reserves (fuel/water/time), walk-out number | Protocol tab. A failed critical check or a reserve below minimum gives "Mitchell. Recommend withdrawal." |
| Check-in schedule (1900) | Time awareness, check-in countdown, missed check-in prediction, `checked in` in chat |
| Sandbox / quarantine for untrusted bursts | Quarantine tab. Content stays inert text and is never executed. Promotion needs explicit confirmation |
| Event log, pattern/log review | Live event log (SSE) and Recall (TF-IDF search over everything) |
| Ranks options by risk, confidence in words | Council: Risk / Opportunity / Evidence. Parallel shows all three; Council gives one recommendation, e.g. "sixty-five percent" |
| Silent unless something matters | Ambient loop speaks only for priority needs, once per day per item |

## Capability layer
- **World model** (`atlas/world_model.py`): stores observations of people, places, objects, routines, preferences, events and scenes. It is fed by chat facts ("I usually…", "my … is …"), notebook entries, check-ins and vision frames. Inferred entries are flagged as inferred, and you can delete any entry in the World tab.
- **Predicted needs** (`atlas/predict_needs.py`): ranked using time of day, check-in, FIFO swing, protocol and reserves, quarantine and learned routines ("You usually…"). They show in the strip under the header. When a prediction is above 80%, ATLAS adds "Recommend: …" to its replies.
- **Ambient loop**: runs every `ambient.interval_s` (default 20 s). Each pass updates time awareness, refreshes predictions and emits events. Priority items are spoken into the transcript.
- **Time awareness**: Australia/Perth by default, day phase, check-in countdown, and an optional FIFO roster (`fifo.enabled`, `roster_start`, 14 on / 7 off).

## Vision: "ATLAS sees only when you allow it."
**Hyperlapse, not video.** ATLAS samples your point of view as one still every `vision.interval_s`. The default is 120 s, and the UI offers 30 s, 1 min, 2 min, 5 min or 15 min. Over a day that gives a few hundred captioned stills instead of a 30 fps firehose. That is enough to learn routines, places and needs: where you are at 0600, what's on the bench, when the ute leaves. It costs far less battery, disk and compute, and records much less of the people around you. The ambient prediction loop runs on its own timer and does not depend on the camera.
- Vision is **off by default**. Hyperlapse is **off by default**. Turning vision off also turns hyperlapse off.
- Frames are stored only under `data/vision/`, capped at `keep_frames`. They are never uploaded anywhere, and there is a Clear frames button.
- Sources:
  - `upload`: Vision tab or `POST /api/vision/frame`
  - `webcam`: needs `pip install opencv-python`. Hyperlapse opens the camera, takes one snap, then releases it each interval
  - `meta_bridge`: see below
- Captions come from an Ollama vision model (`vision.model`, e.g. `llava`) when Ollama is enabled. Without one, each frame is logged as "Frame logged, vision model offline."
- The status light shows IDLE / SEEING / LEARNING.

### Meta glasses hyperlapse (honest status)
Ray-Ban Meta / Oakley Meta glasses **do not stream directly to a PC**. Their camera is available only through Meta's **Wearables Device Access Toolkit (DAT)** inside an **iOS/Android companion app**. That requires Meta developer registration (https://wearables.developer.meta.com/) and CAMERA permission granted via the Meta AI app.

ATLAS on the desktop is the brain that receives the frames. `bridges/meta_glasses/` contains:
- the OpenAPI contract for `POST /api/vision/ingest`: one JPEG still per hyperlapse interval, with device_id, timestamp and optional GPS
- a test sender that simulates the phone

**The phone app itself is not included.** You still need to build it. For LAN use, set `"host": "0.0.0.0"` and firewall the port.

## Ollama (optional)
Install from https://ollama.com, run `ollama pull llama3.1:8b` (and `ollama pull llava` for vision), then set `"ollama": {"enabled": true}` in `config.json`.
Commands and safety-critical topics (status, reserves, withdrawal, quarantine) always use the deterministic rule engine. Ollama handles open conversation under a system prompt that enforces the voice, and its replies are filtered for claimed feelings.

## Chat commands
`status` · `reserves` / `walk-out` · `time` · `log: …` · `known|unknown|risk|opportunity: …` · `task: …` · `recall: …` · `council: …` · `checked in` · `what do you know` · `help`

## API (all JSON under /api)
| Method | Path | Purpose |
|---|---|---|
| GET | /health, /status, /time, /config · PUT /config | system |
| GET | /stream | **SSE** live events |
| GET/POST | /events | event log / emit |
| GET/POST · PATCH/DELETE | /notebook · /notebook/{id} | notebook |
| POST · GET/DELETE | /chat · /transcript | conversation |
| GET · PATCH · POST | /protocol · /protocol/checks/{id}, /protocol/reserves/{key} · /protocol/checks, /protocol/reset | protocol |
| GET/POST · PATCH/DELETE | /tasks · /tasks/{id} | tasks |
| GET/PUT · GET/DELETE | /workspace · /workspace/{name} | workspace files |
| GET/POST · GET/DELETE · POST | /quarantine · /quarantine/{name} · /quarantine/{name}/promote?confirm=true | sandbox |
| GET | /recall?q= | search |
| POST | /council `{question, mode: council\|parallel}` | council |
| GET/POST · DELETE | /world · /world/{id} | world model |
| GET · POST | /predictions · /checkin | predicted needs, check-in |
| GET · PUT · DELETE | /vision · /vision/config · /vision (clear) | vision state and consent |
| POST · GET | /vision/frame, /vision/ingest · /vision/last.jpg | frames |

## Test
`python tests/smoke_test.py` starts a throwaway server with a temp data directory and Ollama disabled, then runs the full flow. It should print SMOKE: GREEN.

## Layout
`app.py` (server and routes) · `atlas/core.py` (config, event bus, time, SQLite, recall) · `atlas/voice.py` (voice, rules, Ollama, council) · `atlas/world_model.py` · `atlas/predict_needs.py` · `atlas/vision/` · `static/` (UI) · `bridges/meta_glasses/` · `data/` (local only).
