"""NeuroSkill -> Open Wearables bridge and live EEG feed.

NeuroSkill (GPL-3.0, separate process) does capture and metrics. This bridge only talks to its
local WebSocket API, so it is not a derivative work. It does three things:

1. Turns the raw 256 Hz samples into one 8-band point per second (the "brainwave field" encoding:
   delta, theta, low/high alpha, low/high beta, low/high gamma) and keeps them in daily JSONL files.
2. Broadcasts those points plus NeuroSkill's scores to live clients (REHIT flow UI, EEG field page).
3. Posts per-minute score averages to Open Wearables as NEUROSKILL_* metric records (SDK sync API).

Raw EEG never goes to Open Wearables; NeuroSkill keeps its own raw CSV.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from urllib import request as urlrequest

import numpy as np

BANDS = ["delta", "theta", "low_alpha", "high_alpha", "low_beta", "high_beta", "low_gamma", "high_gamma"]
BAND_EDGES_HZ = [(1, 4), (4, 8), (8, 10), (10, 13), (13, 18), (18, 30), (30, 40), (40, 50)]
# NeuroSkill EegBands key -> Open Wearables SDK metric type (see series_types.py IDs 600-612).
SCORE_METRICS = {
    "focus": "NEUROSKILL_FOCUS",
    "relaxation": "NEUROSKILL_RELAXATION",
    "engagement": "NEUROSKILL_ENGAGEMENT",
    "meditation": "NEUROSKILL_MEDITATION",
    "cognitive_load": "NEUROSKILL_COGNITIVE_LOAD",
    "drowsiness": "NEUROSKILL_DROWSINESS",
    "mood": "NEUROSKILL_MOOD",
    "rel_delta": "NEUROSKILL_RELATIVE_DELTA",
    "rel_theta": "NEUROSKILL_RELATIVE_THETA",
    "rel_alpha": "NEUROSKILL_RELATIVE_ALPHA",
    "rel_beta": "NEUROSKILL_RELATIVE_BETA",
    "rel_gamma": "NEUROSKILL_RELATIVE_GAMMA",
    "faa": "NEUROSKILL_FRONTAL_ALPHA_ASYMMETRY",
}
UNITS = {k: ("ratio" if k.startswith("rel_") or k == "faa" else "score") for k in SCORE_METRICS}


def band_powers(window: np.ndarray, fs: float) -> list[float]:
    """Mean absolute power per band (uV^2) across channels; window is (samples, channels)."""
    x = window - window.mean(axis=0)
    x = x * np.hanning(len(x))[:, None]
    psd = (np.abs(np.fft.rfft(x, axis=0)) ** 2).mean(axis=1)
    freqs = np.fft.rfftfreq(len(x), 1 / fs)
    return [float(psd[(freqs >= lo) & (freqs < hi)].sum()) for lo, hi in BAND_EDGES_HZ]


def minute_records(scores: list[dict], start: float, end: float, device: str) -> list[dict]:
    """Average a minute of EegBands payloads into Open Wearables SDK MetricRecords."""
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")  # noqa: E731
    source = {"deviceName": device, "deviceManufacturer": "NeuroSkill", "deviceType": "eeg_headband"}
    out = []
    for key, metric in SCORE_METRICS.items():
        vals = [s[key] for s in scores if isinstance(s.get(key), (int, float)) and np.isfinite(s[key])]
        if vals:
            out.append({
                "id": f"neuroskill-{metric.lower()}-{int(start)}",
                "type": metric, "startDate": iso(start), "endDate": iso(end),
                "value": round(float(np.mean(vals)), 4), "unit": UNITS[key], "source": source,
            })
    return out


class Bridge:
    def __init__(self, env: dict[str, str]):
        self.ns_url = env.get("NS_URL", "ws://127.0.0.1:8765/v1/events")
        self.ns_token = Path(env["NS_TOKEN_FILE"]).read_text().strip()
        self.ow_url = env.get("OW_URL", "http://127.0.0.1:8000")
        self.ow_user = env.get("OW_USER_ID", "")
        key_file = env.get("OW_API_KEY_FILE", "")
        self.ow_key = Path(key_file).read_text().strip() if key_file and Path(key_file).exists() else ""
        self.data_dir = Path(env.get("DATA_DIR", "./data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.fs = 256.0
        self.samples: deque = deque(maxlen=512)  # 2 s window
        self.latest: dict = {}
        self.minute: list[dict] = []
        self.minute_start = time.time()
        self.device = "unknown"
        self.state = "disconnected"
        self.history: deque = deque(maxlen=3 * 3600)  # last 3 h of 1 Hz points
        self.clients: set = set()
        self.ow_status = {"posted_minutes": 0, "last_error": None}

    # ---- NeuroSkill side -------------------------------------------------
    async def run_neuroskill(self) -> None:
        import websockets

        while True:
            try:
                async with websockets.connect(f"{self.ns_url}?token={self.ns_token}", max_size=None) as ws:
                    await ws.send(json.dumps({"command": "subscribe", "events": ["EegBands", "EegSample"]}))
                    async for msg in ws:
                        self.on_event(json.loads(msg))
            except Exception as e:  # reconnect forever; NeuroSkill restarts are normal
                self.state = f"neuroskill unavailable: {type(e).__name__}"
                await asyncio.sleep(5)

    def on_event(self, ev: dict) -> None:
        kind, p = ev.get("type"), ev.get("payload") or {}
        if kind == "EegSample":
            self.samples.append(p["channels"])
        elif kind == "EegBands":
            self.latest = p
            self.minute.append(p)
        elif kind == "status":
            self.state = p.get("state", self.state)
            self.device = p.get("device_name") or self.device
            self.fs = p.get("eeg_sample_rate_hz") or self.fs
            self.quality = p.get("channel_quality")

    def tick(self, now: float) -> dict | None:
        """One 1 Hz point, or None until there is a full 1 s of samples."""
        if len(self.samples) < int(self.fs):
            return None
        powers = band_powers(np.asarray(self.samples, dtype=float), self.fs)
        total = sum(powers) or 1.0
        s = self.latest
        return {
            "t": round(now, 3), "device": self.device, "state": self.state,
            "bands": dict(zip(BANDS, [round(p, 3) for p in powers])),
            "rel": dict(zip(BANDS, [round(p / total, 4) for p in powers])),
            "scores": {k: s.get(k) for k in SCORE_METRICS},
            "quality": getattr(self, "quality", None),
        }

    # ---- outputs ---------------------------------------------------------
    def persist(self, point: dict) -> None:
        day = datetime.fromtimestamp(point["t"]).strftime("%Y-%m-%d")
        with open(self.data_dir / f"eeg-field-{day}.jsonl", "a") as f:
            f.write(json.dumps({"t": point["t"], "bands": point["bands"], "scores": point["scores"]}) + "\n")

    def post_minute(self, now: float) -> None:
        batch, start = self.minute, self.minute_start
        self.minute, self.minute_start = [], now
        if not (batch and self.ow_user and self.ow_key):
            return
        body = {
            "provider": "health_connect", "sdkVersion": "neuroskill-bridge/0.1",
            "syncTimestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": {"records": minute_records(batch, start, now, self.device)},
        }
        req = urlrequest.Request(
            f"{self.ow_url}/api/v1/sdk/users/{self.ow_user}/sync", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-Open-Wearables-API-Key": self.ow_key}, method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=10) as r:
                self.ow_status = {"posted_minutes": self.ow_status["posted_minutes"] + 1, "last_error": None,
                                  "last_status": r.status, "last_post": now}
        except Exception as e:
            self.ow_status["last_error"] = f"{type(e).__name__}: {e}"[:300]

    async def run_clock(self) -> None:
        while True:
            await asyncio.sleep(1 - (time.time() % 1))
            now = time.time()
            point = self.tick(now)
            if point:
                self.history.append(point)
                self.persist(point)
                msg = json.dumps({"type": "point", **point})
                for ws in list(self.clients):
                    try:
                        await ws.send(msg)
                    except Exception:
                        self.clients.discard(ws)
            if now - self.minute_start >= 60:
                await asyncio.to_thread(self.post_minute, now)

    # ---- live server -----------------------------------------------------
    async def handle_ws(self, ws) -> None:
        self.clients.add(ws)
        try:
            await ws.send(json.dumps({"type": "history", "points": list(self.history)[-3600:]}))
            await ws.wait_closed()
        finally:
            self.clients.discard(ws)

    def http(self, connection, req):
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        path = req.path.split("?")[0]
        if req.headers.get("Upgrade", "").lower() == "websocket" and path == "/live":
            return None
        if path in ("/", "/index.html"):
            body, ctype = (Path(__file__).parent / "web" / "index.html").read_bytes(), "text/html; charset=utf-8"
        elif path == "/status":
            body, ctype = json.dumps({"state": self.state, "device": self.device, "clients": len(self.clients),
                                      "points": len(self.history), "open_wearables": self.ow_status}).encode(), "application/json"
        elif path == "/history":
            day = (req.path.split("day=")[1][:10] if "day=" in req.path else datetime.now().strftime("%Y-%m-%d"))
            f = self.data_dir / f"eeg-field-{day}.jsonl"
            body, ctype = (f.read_bytes() if f.exists() and day.replace("-", "").isdigit() else b""), "application/x-ndjson"
        else:
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        return Response(200, "OK", Headers([("Content-Type", ctype), ("Content-Length", str(len(body))),
                                            ("Cache-Control", "no-store")]), body)


async def main() -> None:
    from websockets.asyncio.server import serve

    b = Bridge(dict(os.environ))
    servers = []
    for addr in os.environ.get("BRIDGE_BIND", "127.0.0.1:8376").split(","):
        host, port = addr.rsplit(":", 1)
        servers.append(await serve(b.handle_ws, host, int(port), process_request=b.http, max_size=2**20))
    await asyncio.gather(b.run_neuroskill(), b.run_clock())


if __name__ == "__main__":
    asyncio.run(main())
