import json
import sqlite3

from noop_to_ow import collect, rmssd


def make_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
    create table pairedDevice(id, brand, model);
    create table hrSample(deviceId, ts, bpm);
    create table rrInterval(deviceId, ts, rrMs, ord, tsSuspect);
    create table metricSeries(deviceId, day, key, value);
    create table appleDaily(deviceId, day, steps, activeKcal);
    create table sleepSession(deviceId, startTs, endTs, stagesJSON);
    insert into pairedDevice values ('oura-1','Oura','Oura Ring 4'),('whoop-5','WHOOP','5.0 MG');
    """)
    con.executemany("insert into hrSample values (?,?,?)",
                    [("whoop-5", 1794199980 + i, 60 + i % 2) for i in range(120)] + [("whoop-5", 1794200500, 5)])
    con.executemany("insert into rrInterval values (?,?,?,?,0)",
                    [("oura-1", 1794200100 + i, 1000 + (20 if i % 2 else 0), i) for i in range(60)])
    con.execute("insert into metricSeries values ('whoop-5-noop','2026-11-08','rhr_primary_session',56.0)")
    con.execute("insert into appleDaily values ('health-connect','2026-11-08',5718,517.5)")
    stages = json.dumps([{"start": 1794190000, "end": 1794191000, "stage": "light"},
                         {"start": 1794191000, "end": 1794192000, "stage": "wake"}])
    con.execute("insert into sleepSession values ('oura-1',1794190000,1794192000,?)", (stages,))
    con.execute("insert into sleepSession values ('oura-1-noop',1794190000,1794192000,?)", (stages,))
    con.commit()


def test_rmssd_needs_30_clean_intervals():
    assert rmssd([1000, 1020] * 10) is None
    v = rmssd([1000, 1020] * 20)
    assert v is not None and round(v, 1) == 20.0


def test_collect_maps_noop_tables_to_ow_records(tmp_path):
    db = tmp_path / "noop.db"
    make_db(db)
    records, sleeps, newest = collect(db, 0)
    by = {}
    for r in records:
        by.setdefault(r["type"], []).append(r)
    assert [round(r["value"], 1) for r in by["HEART_RATE"]] == [60.5, 60.5]  # 2 minutes; bpm=5 dropped
    assert by["HEART_RATE"][0]["source"]["deviceName"] == "5.0 MG"
    assert len(by["HEART_RATE_VARIABILITY"]) == 1 and by["HEART_RATE_VARIABILITY"][0]["unit"] == "ms"
    assert by["RESTING_HEART_RATE"][0]["source"]["deviceManufacturer"] == "WHOOP"
    assert by["STEP_COUNT"][0]["value"] == 5718 and by["STEP_COUNT"][0]["zoneOffset"] == "-06:00"
    assert [s["stage"] for s in sleeps] == ["light", "awake"]  # recomputed duplicate night skipped
    assert newest == 1794200500
