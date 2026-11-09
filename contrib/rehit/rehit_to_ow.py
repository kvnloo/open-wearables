"""REHIT (CAROL bike app) -> Open Wearables: rides become indoor-cycling workouts plus bike HR.

The REHIT app writes one CSV per ride (t_ms,stage,kind,level,rpm,watts,hr; 1 Hz) and a one-line
summary to its external files dir on the bike tablet. This pulls them over adb (read-only), and
posts each ride to OW's SDK sync API as a CYCLING_STATIONARY workout with power/cadence/HR stats
and per-5-s HEART_RATE records. Rides shorter than MIN_RIDE_S (sim/test runs) are skipped.
OW upserts by id, so re-posting a ride is harmless; `imported.json` only avoids the work.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("TZ_NAME", "America/Chicago"))
REMOTE = "/sdcard/Android/data/com.kvnloo.rehit/files/sessions"
MIN_RIDE_S = int(os.environ.get("MIN_RIDE_S", "300"))
SOURCE = {"deviceName": "CAROL bike", "deviceManufacturer": "CAROL", "deviceType": "unknown", "appId": "com.kvnloo.rehit"}


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def tablet_serial(adb: str) -> str | None:
    out = subprocess.run([adb, "devices", "-l"], capture_output=True, text=True, timeout=20).stdout
    hits = [ln.split()[0] for ln in out.splitlines() if "model:TB350FU" in ln and " device " in ln]
    return sorted(hits, key=lambda s: ":5555" not in s)[0] if hits else None  # prefer the tailnet address


def pull(adb: str, serial: str, mirror: Path) -> None:
    mirror.mkdir(parents=True, exist_ok=True, mode=0o700)
    names = subprocess.run([adb, "-s", serial, "shell", f"ls {REMOTE}"], capture_output=True, text=True, timeout=30).stdout.split()
    for n in names:
        dst = mirror / n
        if n.endswith((".csv", ".txt")) and not dst.exists():
            data = subprocess.run([adb, "-s", serial, "exec-out", "cat", f"{REMOTE}/{n}"], capture_output=True, timeout=60).stdout
            dst.write_bytes(data)


def ride(csv_path: Path) -> dict | None:
    """One ride -> {workout, records}; None when too short to be a real ride."""
    stem = csv_path.stem  # 20261109-005541[-REHIT]
    mode = stem.split("-", 2)[2] if stem.count("-") >= 2 else "ride"
    start = datetime.strptime(stem[:15], "%Y%m%d-%H%M%S").replace(tzinfo=TZ).timestamp()
    rows: list[dict[str, int]] = [{k: int(v) for k, v in r.items() if k != "kind"} for r in csv.DictReader(csv_path.open())]
    if not rows or rows[-1]["t_ms"] / 1000 < MIN_RIDE_S:
        return None
    dur = rows[-1]["t_ms"] / 1000 + 1
    hr = [r["hr"] for r in rows if r["hr"] > 0]
    pedal = [r for r in rows if r["rpm"] > 0]
    stats = [("duration", "s", round(dur)), ("maxPower", "W", max(r["watts"] for r in rows))]
    if pedal:
        stats += [("meanPower", "W", round(sum(r["watts"] for r in pedal) / len(pedal))),
                  ("meanRpm", "rpm", round(sum(r["rpm"] for r in pedal) / len(pedal)))]
    if hr:
        stats += [("averageHeartRate", "bpm", round(sum(hr) / len(hr))), ("maxHeartRate", "bpm", max(hr)), ("minHeartRate", "bpm", min(hr))]
    summary = csv_path.with_suffix(".txt")
    wid = f"rehit-{stem}"
    workout = {"id": wid, "type": "CYCLING_STATIONARY", "startDate": iso(start), "endDate": iso(start + dur),
               "title": f"CAROL {mode}", "notes": summary.read_text().strip() if summary.exists() else None,
               "source": SOURCE, "values": [{"type": t, "unit": u, "value": v} for t, u, v in stats]}
    records = []
    for i in range(0, len(rows), 5):  # 5-s HR, enough for HR-recovery curves without flooding the store
        win = [r["hr"] for r in rows[i:i + 5] if r["hr"] > 0]
        if win:
            t = start + rows[i]["t_ms"] / 1000
            records.append({"id": f"{wid}-hr-{i}", "parentId": wid, "type": "HEART_RATE", "startDate": iso(t),
                            "endDate": iso(t + 5), "value": round(sum(win) / len(win), 1), "unit": "bpm", "source": SOURCE})
    return {"workout": workout, "records": records}


def post(env: dict, r: dict) -> int:
    key = Path(env["OW_API_KEY_FILE"]).read_text().strip()
    body = {"provider": "health_connect", "sdkVersion": "rehit-import-1", "syncTimestamp": iso(datetime.now(timezone.utc).timestamp()),
            "data": {"workouts": [r["workout"]], "records": r["records"]}}
    req = urllib.request.Request(f"{env.get('OW_URL', 'http://127.0.0.1:8000')}/api/v1/sdk/users/{env['OW_USER_ID']}/sync",
                                 json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Open-Wearables-API-Key": key})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status


def main(env: dict) -> int:
    mirror = Path(env.get("REHIT_MIRROR", Path(env["OW_API_KEY_FILE"]).parent / "rehit-sessions"))
    serial = tablet_serial(env.get("ADB", "adb"))
    if serial:
        pull(env.get("ADB", "adb"), serial, mirror)
    done_f = mirror / "imported.json"
    done = set(json.loads(done_f.read_text())) if done_f.exists() else set()
    posted, skipped = [], []
    for c in sorted(mirror.glob("*.csv")):
        if c.stem in done:
            continue
        r = ride(c)
        if r is None:
            skipped.append(c.stem)
            continue
        if post(env, r) in (200, 202):
            done.add(c.stem)
            posted.append(c.stem)
    done_f.write_text(json.dumps(sorted(done)))
    print(json.dumps({"tablet": serial or "offline", "posted": posted, "skipped_short": skipped}))
    return 0


if __name__ == "__main__":
    sys.exit(main(dict(os.environ)))
