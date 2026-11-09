import json
import math

import numpy as np

from neuro_bridge import BANDS, Bridge, band_powers, minute_records

FS = 256.0


def sine(hz: float, seconds: float = 2.0, channels: int = 4) -> np.ndarray:
    t = np.arange(int(FS * seconds)) / FS
    return np.stack([20 * np.sin(2 * math.pi * hz * t + c) for c in range(channels)], axis=1)


def test_each_band_center_lands_in_its_own_band():
    for name, hz in zip(BANDS, [2.5, 6, 9, 11.5, 15.5, 24, 35, 45]):
        p = band_powers(sine(hz), FS)
        assert BANDS[int(np.argmax(p))] == name, (name, hz, p)


def test_minute_records_average_scores_and_skip_missing():
    scores = [{"focus": 40.0, "rel_alpha": 0.5, "faa": float("nan")}, {"focus": 60.0, "rel_alpha": 0.7}]
    recs = {r["type"]: r for r in minute_records(scores, 1794207000, 1794207060, "Muse S")}
    assert recs["NEUROSKILL_FOCUS"]["value"] == 50.0 and recs["NEUROSKILL_FOCUS"]["unit"] == "score"
    assert recs["NEUROSKILL_RELATIVE_ALPHA"]["value"] == 0.6 and recs["NEUROSKILL_RELATIVE_ALPHA"]["unit"] == "ratio"
    assert "NEUROSKILL_FRONTAL_ALPHA_ASYMMETRY" not in recs  # NaN only -> no record
    assert recs["NEUROSKILL_FOCUS"]["startDate"] == "2026-11-09T06:50:00Z"


def test_tick_emits_one_point_per_second_once_window_is_full(tmp_path):
    tok = tmp_path / "t"
    tok.write_text("x")
    b = Bridge({"NS_TOKEN_FILE": str(tok), "DATA_DIR": str(tmp_path / "d")})
    assert b.tick(1.0) is None
    for row in sine(11.5):
        b.on_event({"type": "EegSample", "payload": {"channels": list(row)}})
    b.on_event({"type": "EegBands", "payload": {"focus": 70.0}})
    pt = b.tick(2.0)
    assert pt is not None
    assert max(pt["rel"], key=pt["rel"].get) == "high_alpha"
    assert pt["scores"]["focus"] == 70.0
    b.persist(pt)
    line = json.loads(next((tmp_path / "d").glob("eeg-field-*.jsonl")).read_text())
    assert set(line["bands"]) == set(BANDS)
