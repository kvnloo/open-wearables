"""NOOP -> Open Wearables: import Oura / WHOOP data NOOP captured on the phone.

limitless-sync copies NOOP's Room DB to groot every 15 min; this reads that copy (read-only) and
posts Health-Connect-format records to OW's SDK sync API. OW upserts on
(source, series, timestamp), so re-sending is harmless; a watermark file only keeps batches small.

  heart rate      hrSample      -> HEART_RATE, per-minute mean
  HRV             rrInterval    -> HEART_RATE_VARIABILITY (RMSSD over 5-min windows)
  resting HR      metricSeries  -> RESTING_HEART_RATE (NOOP's primary-sleep RHR)
  sleep stages    sleepSession  -> sleep phases (one copy per night; NOOP keeps raw + recomputed)
  steps, kcal     appleDaily    -> STEP_COUNT / ACTIVE_CALORIES_BURNED daily totals (Health Connect)
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("TZ_NAME", "America/Chicago"))
STAGE = {"light": "light", "deep": "deep", "rem": "rem", "wake": "awake"}
BATCH = 2000


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def zone(t: float) -> str:
    off = datetime.fromtimestamp(t, TZ).utcoffset() or timedelta()
    m = int(off.total_seconds() // 60)
    return f"{'+' if m >= 0 else '-'}{abs(m) // 60:02d}:{abs(m) % 60:02d}"


def base_device(dev: str) -> str:
    return dev[:-5] if dev.endswith("-noop") else dev  # "-noop" rows are NOOP's recomputation


def sources(con: sqlite3.Connection) -> dict[str, dict]:
    out = {"health-connect": {"deviceName": "Health Connect", "deviceManufacturer": "Android", "deviceType": "phone"}}
    for pid, brand, model in con.execute("select id, brand, model from pairedDevice"):
        kind = "ring" if (brand or "").lower() == "oura" else "fitness_band"
        out[pid] = {"deviceId": pid, "deviceName": model, "deviceManufacturer": brand, "deviceType": kind, "appId": "com.noop.whoop"}
    return out


def rec(kind: str, start: float, end: float, value: float, unit: str, src: dict, rid: str) -> dict:
    return {"id": rid, "type": kind, "startDate": iso(start), "endDate": iso(end), "zoneOffset": zone(start),
            "value": round(value, 3), "unit": unit, "source": src}


def heart_rate(con, srcs, since: float) -> list[dict]:
    buckets: dict[tuple[str, int], list[float]] = defaultdict(list)
    for dev, ts, bpm in con.execute("select deviceId, ts, bpm from hrSample where ts > ? and bpm between 25 and 230", (since,)):
        buckets[(dev, int(ts // 60))].append(bpm)
    return [rec("HEART_RATE", m * 60, m * 60 + 60, sum(v) / len(v), "bpm", srcs.get(dev, {}), f"noop-hr-{dev}-{m}")
            for (dev, m), v in sorted(buckets.items(), key=lambda kv: kv[0][1])]


def rmssd(rr: list[float]) -> float | None:
    rr = [x for x in rr if 300 <= x <= 2000]
    d = [b - a for a, b in zip(rr, rr[1:]) if abs(b - a) < 200]
    return math.sqrt(sum(x * x for x in d) / len(d)) if len(d) >= 30 else None


def hrv(con, srcs, since: float) -> list[dict]:
    wins: dict[tuple[str, int], list[float]] = defaultdict(list)
    for dev, ts, rr in con.execute(
            "select deviceId, ts, rrMs from rrInterval where ts > ? and coalesce(tsSuspect,0)=0 order by deviceId, ts, ord", (since,)):
        wins[(dev, int(ts // 300))].append(rr)
    out = []
    for (dev, w), rr in sorted(wins.items(), key=lambda kv: kv[0][1]):
        v = rmssd(rr)
        if v is not None:
            out.append(rec("HEART_RATE_VARIABILITY", w * 300, w * 300 + 300, v, "ms", srcs.get(dev, {}), f"noop-hrv-{dev}-{w}"))
    return out


def resting_hr(con, srcs) -> list[dict]:
    out = []
    for dev, day, v in con.execute("select deviceId, day, value from metricSeries where key='rhr_primary_session'"):
        t = datetime.fromisoformat(day).replace(tzinfo=TZ).timestamp()
        out.append(rec("RESTING_HEART_RATE", t, t + 86400, v, "bpm", srcs.get(base_device(dev), {}), f"noop-rhr-{dev}-{day}"))
    return out


def daily_activity(con, srcs) -> list[dict]:
    out = []
    for dev, day, steps, kcal in con.execute("select deviceId, day, steps, activeKcal from appleDaily"):
        t = datetime.fromisoformat(day).replace(tzinfo=TZ).timestamp()
        if steps is not None:
            out.append(rec("STEP_COUNT", t, t + 86400, steps, "count", srcs.get(dev, {}), f"noop-steps-{dev}-{day}"))
        if kcal is not None:
            out.append(rec("ACTIVE_CALORIES_BURNED", t, t + 86400, kcal, "kcal", srcs.get(dev, {}), f"noop-kcal-{dev}-{day}"))
    return out


def sleep(con, srcs) -> list[dict]:
    nights: dict[tuple[int, int], tuple[str, str]] = {}
    for dev, s, e, stages in con.execute(
            "select deviceId, startTs, endTs, stagesJSON from sleepSession where endTs is not null order by deviceId like '%-noop'"):
        nights.setdefault((s, e), (dev, stages))  # raw device first; skip NOOP's recomputed duplicate
    out = []
    for (s, _e), (dev, stages) in sorted(nights.items()):
        parent = f"noop-sleep-{base_device(dev)}-{s}"
        for i, st in enumerate(json.loads(stages or "[]")):
            if st.get("stage") in STAGE:
                out.append({"id": f"{parent}-{i}", "parentId": parent, "stage": STAGE[st["stage"]],
                            "startDate": iso(st["start"]), "endDate": iso(st["end"]), "zoneOffset": zone(st["start"]),
                            "source": srcs.get(base_device(dev), {})})
    return out


def post(env: dict, records: list[dict], sleeps: list[dict]) -> list[int]:
    key = Path(env["OW_API_KEY_FILE"]).read_text().strip()
    url = f"{env.get('OW_URL', 'http://127.0.0.1:8000')}/api/v1/sdk/users/{env['OW_USER_ID']}/sync"
    codes = []
    chunks = [{"records": records[i:i + BATCH]} for i in range(0, len(records), BATCH)] + ([{"sleep": sleeps}] if sleeps else [])
    for data in chunks:
        body = {"provider": "health_connect", "sdkVersion": "noop-import-1", "syncType": "historical",
                "syncTimestamp": iso(datetime.now(timezone.utc).timestamp()), "data": data}
        req = urllib.request.Request(url, json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", "X-Open-Wearables-API-Key": key})
        with urllib.request.urlopen(req, timeout=60) as r:
            codes.append(r.status)
    return codes


def collect(db: Path, since: float) -> tuple[list[dict], list[dict], float]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    srcs = sources(con)
    newest = con.execute("select max(m) from (select max(ts) m from hrSample union all select max(ts) from rrInterval)").fetchone()[0] or since
    # Re-read the last 10 min so a window split across two copies is completed, not truncated.
    lookback = max(0.0, since - 600)
    records = heart_rate(con, srcs, lookback) + hrv(con, srcs, lookback) + resting_hr(con, srcs) + daily_activity(con, srcs)
    return records, sleep(con, srcs), float(newest)


def main(env: dict) -> int:
    db = Path(env.get("NOOP_DB", "/mnt/zer0models/sft-svlm/data/limitless/state/noop/noop_whoop.db"))
    wm = Path(env.get("WATERMARK", Path(env["OW_API_KEY_FILE"]).parent / "noop_import.json"))
    since = json.loads(wm.read_text()).get("since", 0.0) if wm.exists() else 0.0
    records, sleeps, newest = collect(db, since)
    codes = post(env, records, sleeps) if records or sleeps else []
    if all(c in (200, 202) for c in codes):
        wm.write_text(json.dumps({"since": newest, "at": iso(datetime.now(timezone.utc).timestamp())}))
    kinds: dict[str, int] = defaultdict(int)
    for r in records:
        kinds[r["type"]] += 1
    print(json.dumps({"records": dict(kinds), "sleep_phases": len(sleeps), "posts": codes, "since": since, "newest": newest}))
    return 0 if all(c in (200, 202) for c in codes) else 1


if __name__ == "__main__":
    sys.exit(main(dict(os.environ)))
