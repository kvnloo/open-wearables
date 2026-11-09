from rehit_to_ow import ride


def write(tmp, stem, seconds, hr=140):
    p = tmp / f"{stem}.csv"
    lines = ["t_ms,stage,kind,level,rpm,watts,hr"]
    lines += [f"{i * 1000},0,WORK,10,{80 if i % 60 else 0},{200 + i % 3},{hr + i % 2}" for i in range(seconds)]
    p.write_text("\n".join(lines) + "\n")
    (tmp / f"{stem}.txt").write_text("REHIT. Sprint 1: 600 W peak\n")
    return p


def test_short_sim_ride_is_skipped(tmp_path):
    assert ride(write(tmp_path, "20261109-005541-REHIT", 100)) is None


def test_ride_becomes_indoor_cycling_workout_with_5s_hr(tmp_path):
    r = ride(write(tmp_path, "20261109-180000-ZONE2", 600))
    assert r is not None
    w = r["workout"]
    stats = {v["type"]: v["value"] for v in w["values"]}
    assert w["type"] == "CYCLING_STATIONARY" and w["title"] == "CAROL ZONE2"
    assert w["startDate"] == "2026-11-10T00:00:00Z"  # 18:00 CST
    assert stats["duration"] == 600 and stats["maxHeartRate"] == 141 and stats["meanRpm"] == 80
    assert len(r["records"]) == 120 and r["records"][0]["parentId"] == w["id"]
    assert w["notes"].startswith("REHIT.")
