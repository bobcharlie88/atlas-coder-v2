# Meta glasses → ATLAS bridge (stub + contract)

**Honest status:** ATLAS cannot talk to Ray-Ban Meta / Oakley Meta glasses directly from Windows/Mac/Linux.
Meta exposes the glasses camera only through the **Meta Wearables Device Access Toolkit (DAT)** inside an
**iOS or Android app** paired via the Meta AI app. You need to build (or obtain) that phone app. This folder
gives you the contract and a test sender; it is not a working phone app.

Steps:
1. Register at https://wearables.developer.meta.com/ and get DAT access.
2. Build a phone companion with the DAT SDK; request CAMERA permission (granted through the Meta AI app).
3. **Hyperlapse:** on a timer matching ATLAS `vision.interval_s` (default 120 s), take a single still with DAT's periodic photo capture (capturePhoto) rather than holding open the 24–30 fps camera stream. This saves glasses battery and BLE/Wi-Fi bandwidth. JPEG-encode the still and POST it to `http://<atlas-pc-ip>:8765/api/vision/ingest`
   per `openapi.yaml`.
4. On ATLAS: set `"host": "0.0.0.0"` in config.json (LAN only — firewall it), enable Vision, source `meta_bridge`.

Test without glasses: `python bridges/meta_glasses/send_test_frame.py photo.jpg`
