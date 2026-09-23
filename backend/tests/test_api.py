"""API 测试：健康检查、正常排程、必观不可行、字段校验错误的可定位性。"""

from __future__ import annotations

import copy

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def valid_payload() -> dict:
    return {
        "initial_time": 0,
        "initial_azimuth": 0,
        "initial_elevation": 0,
        "azimuth_speed": 1,
        "elevation_speed": 1,
        "targets": [
            {"id": "A", "azimuth": 10, "elevation": 0, "duration": 100,
             "window_start": 0, "window_end": 1000, "priority": 5, "must_observe": False},
            {"id": "B", "azimuth": 0, "elevation": 10, "duration": 50,
             "window_start": 0, "window_end": 200, "priority": 1, "must_observe": False},
            {"id": "C", "azimuth": 0, "elevation": 0, "duration": 10,
             "window_start": 500, "window_end": 600, "priority": 1, "must_observe": False},
            {"id": "D", "azimuth": 180, "elevation": 80, "duration": 500,
             "window_start": 0, "window_end": 100, "priority": 3, "must_observe": False},
        ],
    }


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_schedule_ok_full_response():
    resp = client.post("/api/schedule", json=valid_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert [o["target_id"] for o in body["observations"]] == ["A", "B", "C"]
    assert body["total_priority"] == 7
    assert body["target_count"] == 3
    assert body["end_time"] == 510
    assert body["unscheduled"] == ["D"]

    a, b, c = body["observations"]
    assert a["slew"] == {"azimuth_seconds": 10, "elevation_seconds": 0, "total_seconds": 10}
    assert (a["arrival_time"], a["wait_seconds"], a["start"], a["end"]) == (10, 0, 10, 110)
    assert b["slew"]["total_seconds"] == 10
    assert (b["arrival_time"], b["wait_seconds"], b["start"], b["end"]) == (120, 0, 120, 170)
    # C 到达过早，等待至窗口开启。
    assert (c["arrival_time"], c["wait_seconds"], c["start"], c["end"]) == (180, 320, 500, 510)


def test_schedule_infeasible_must_observe():
    payload = valid_payload()
    payload["targets"] = [
        {"id": "M1", "azimuth": 0, "elevation": 0, "duration": 100,
         "window_start": 0, "window_end": 150, "priority": 5, "must_observe": True},
        {"id": "M2", "azimuth": 90, "elevation": 0, "duration": 100,
         "window_start": 0, "window_end": 150, "priority": 5, "must_observe": True},
    ]
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "infeasible"
    assert body["message"]
    assert body["observations"] == []
    assert body["end_time"] is None


def _locs(body: dict) -> list[list]:
    return [item.get("loc", []) for item in body.get("detail", [])]


def test_validation_bad_azimuth_is_locatable():
    payload = valid_payload()
    payload["targets"][0]["azimuth"] = 360
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422
    locs = _locs(resp.json())
    assert any("azimuth" in [str(x) for x in loc] for loc in locs)
    assert any("targets" in [str(x) for x in loc] for loc in locs)


def test_validation_duplicate_ids():
    payload = valid_payload()
    payload["targets"][1]["id"] = "A"
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("A" in item["msg"] for item in detail)


def test_validation_window_end_before_start():
    payload = valid_payload()
    payload["targets"][0]["window_start"] = 500
    payload["targets"][0]["window_end"] = 500
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422
    locs = _locs(resp.json())
    assert any("window_end" in [str(x) for x in loc] for loc in locs)


def test_validation_target_count_bounds():
    payload = valid_payload()
    payload["targets"] = payload["targets"][:1]
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422

    payload = valid_payload()
    base = payload["targets"][0]
    payload["targets"] = [
        dict(base, id=f"T{i:02d}") for i in range(17)
    ]
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422


def test_validation_priority_and_speed_positive():
    payload = valid_payload()
    payload["targets"][0]["priority"] = 0
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422
    locs = _locs(resp.json())
    assert any("priority" in [str(x) for x in loc] for loc in locs)

    payload = valid_payload()
    payload["azimuth_speed"] = 0
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422
    locs = _locs(resp.json())
    assert any("azimuth_speed" in [str(x) for x in loc] for loc in locs)


def test_validation_missing_field():
    payload = valid_payload()
    del payload["targets"][0]["duration"]
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422
    locs = _locs(resp.json())
    assert any("duration" in [str(x) for x in loc] for loc in locs)


def test_import_roundtrip_payload_shape():
    # 导入/导出使用同一 JSON 结构，保证页面导入样例可直接重放。
    payload = valid_payload()
    resp = client.post("/api/schedule", json=copy.deepcopy(payload))
    assert resp.status_code == 200


# ------------------------------------------------ 电缆包络模式


def envelope_payload() -> dict:
    return {
        "initial_time": 0,
        "initial_azimuth": 0,
        "initial_elevation": 0,
        "azimuth_speed": 1,
        "elevation_speed": 1,
        "cable_envelope": {
            "initial_azimuth_unwrapped": 0,
            "min_azimuth": 0,
            "max_azimuth": 360,
        },
        "targets": [
            {"id": "M1", "azimuth": 350, "elevation": 0, "duration": 10,
             "window_start": 0, "window_end": 1000, "priority": 5, "must_observe": True},
            {"id": "M2", "azimuth": 10, "elevation": 0, "duration": 10,
             "window_start": 0, "window_end": 1000, "priority": 5, "must_observe": True},
        ],
    }


def test_envelope_response_shape_and_global_wrap_choice():
    resp = client.post("/api/schedule", json=envelope_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # 350 不能走 -10（越下界），全局解：先 10°(顺转 10) 再 350°(顺转 340)。
    m2, m1 = body["observations"]
    assert [o["target_id"] for o in body["observations"]] == ["M2", "M1"]
    assert m2["arrival_azimuth"] == 10
    assert m2["slew"] == {
        "azimuth_seconds": 10,
        "elevation_seconds": 0,
        "total_seconds": 10,
        "from_azimuth": 0,
        "to_azimuth": 10,
        "azimuth_direction": "cw",
    }
    assert m1["slew"]["from_azimuth"] == 10
    assert m1["slew"]["to_azimuth"] == 350
    assert m1["slew"]["azimuth_direction"] == "cw"
    assert m1["arrival_azimuth"] == 350


def test_legacy_response_has_no_envelope_fields():
    # 旧请求（不带 cable_envelope）响应形状保持原样，无包络附加字段。
    resp = client.post("/api/schedule", json=valid_payload())
    slew = resp.json()["observations"][0]["slew"]
    assert set(slew.keys()) == {"azimuth_seconds", "elevation_seconds", "total_seconds"}
    obs = resp.json()["observations"][0]
    assert "arrival_azimuth" not in obs


def test_envelope_null_keeps_legacy_semantics():
    payload = valid_payload()
    payload["cable_envelope"] = None
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert [o["target_id"] for o in body["observations"]] == ["A", "B", "C"]
    assert "arrival_azimuth" not in body["observations"][0]


def test_envelope_infeasible_unreachable_must_target():
    payload = envelope_payload()
    payload["cable_envelope"]["max_azimuth"] = 179  # 350° 与 10° 均无同余位置
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "infeasible"
    assert body["message"]
    assert body["observations"] == []
    assert set(body["unscheduled"]) == {"M1", "M2"}


def test_envelope_validation_non_congruent_initial_azimuth():
    payload = envelope_payload()
    payload["cable_envelope"]["initial_azimuth_unwrapped"] = 10  # 与初始 0 不同余
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422
    locs = _locs(resp.json())
    assert any("cable_envelope" in [str(x) for x in loc] for loc in locs)


def test_envelope_validation_initial_outside_interval():
    payload = envelope_payload()
    payload["cable_envelope"]["initial_azimuth_unwrapped"] = 360
    # 360 与初始 0 同余，但区间 [0,359] 不含 360。
    payload["cable_envelope"]["max_azimuth"] = 359
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422


def test_envelope_validation_reversed_and_too_wide_interval():
    payload = envelope_payload()
    payload["cable_envelope"] = {
        "initial_azimuth_unwrapped": 0,
        "min_azimuth": 360,
        "max_azimuth": 0,
    }
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422

    payload = envelope_payload()
    payload["cable_envelope"]["max_azimuth"] = 721
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("720" in item["msg"] for item in detail)


def test_envelope_negative_unwrapped_azimuth_accepted():
    payload = envelope_payload()
    payload["cable_envelope"] = {
        "initial_azimuth_unwrapped": 0,
        "min_azimuth": -180,
        "max_azimuth": 180,
    }
    payload["targets"] = [
        {"id": "M1", "azimuth": 180, "elevation": 0, "duration": 10,
         "window_start": 0, "window_end": 1000, "priority": 5, "must_observe": True},
        {"id": "M2", "azimuth": 0, "elevation": 0, "duration": 10,
         "window_start": 0, "window_end": 1000, "priority": 5, "must_observe": True},
    ]
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # 180° 的两个圈位 ±180 完全并列，取展开方位更小的 -180。
    assert [o["target_id"] for o in body["observations"]] == ["M2", "M1"]
    m1 = body["observations"][1]
    assert m1["arrival_azimuth"] == -180
    assert m1["slew"]["azimuth_direction"] == "ccw"
