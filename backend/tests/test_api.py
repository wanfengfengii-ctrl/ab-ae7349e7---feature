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


# ------------------------------------------------------------ 电缆包络模式


def envelope_payload() -> dict:
    # 区间 [-10, 350]：A(350) 有 -10/350 两个圈位，C(349) 只有 349。
    payload = valid_payload()
    payload["cable_envelope"] = {
        "reference_azimuth": 0,
        "lower_limit": -10,
        "upper_limit": 350,
    }
    payload["targets"] = [
        {"id": "A", "azimuth": 350, "elevation": 0, "duration": 1,
         "window_start": 0, "window_end": 1000, "priority": 5, "must_observe": True},
        {"id": "C", "azimuth": 349, "elevation": 0, "duration": 1,
         "window_start": 0, "window_end": 1000, "priority": 1, "must_observe": True},
    ]
    return payload


def test_envelope_schedule_returns_unwrapped_fields():
    resp = client.post("/api/schedule", json=envelope_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["cable_envelope"] == {
        "reference_azimuth": 0,
        "lower_limit": -10,
        "upper_limit": 350,
    }
    obs = body["observations"]
    assert [o["target_id"] for o in obs] == ["C", "A"]
    c, a = obs
    assert (c["azimuth_start"], c["azimuth_end"], c["direction"]) == (0, 349, "cw")
    assert (a["azimuth_start"], a["azimuth_end"], a["direction"]) == (349, 350, "cw")
    # 展开方位的有符号转角：0->349 需要 349 秒。
    assert c["slew"]["azimuth_seconds"] == 349


def test_envelope_infeasible_returns_clear_reason():
    payload = envelope_payload()
    # 窗口收紧到 351：先到 -10 再赶 349 需要 360 秒，任何顺序/圈位都不可行。
    payload["targets"][1]["window_end"] = 351
    payload["targets"][0]["window_end"] = 351
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "infeasible"
    assert body["message"] and "圈位" in body["message"]
    assert body["observations"] == []


def test_envelope_target_outside_interval_is_infeasible():
    payload = envelope_payload()
    # 区间 [0, 10] 内不存在方位 350 的同余位置。
    payload["cable_envelope"] = {
        "reference_azimuth": 0,
        "lower_limit": 0,
        "upper_limit": 10,
    }
    resp = client.post("/api/schedule", json=payload)
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "infeasible"
    assert "A" in (body["message"] or "")


def test_envelope_reference_not_congruent_is_422():
    payload = envelope_payload()
    payload["cable_envelope"]["reference_azimuth"] = 10  # initial_azimuth=0
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    locs = _locs(resp.json())
    # 跨字段错误定位到 cable_envelope，消息中点明 reference_azimuth。
    assert any("cable_envelope" in [str(x) for x in loc] for loc in locs)
    assert any("reference_azimuth" in item["msg"] for item in detail)


def test_envelope_reference_inside_interval_is_422():
    payload = envelope_payload()
    payload["cable_envelope"]["reference_azimuth"] = 360  # 同余但在区间外
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422
    assert any("reference_azimuth" in [str(x) for x in loc] for loc in _locs(resp.json()))


def test_envelope_lower_greater_than_upper_is_422():
    payload = envelope_payload()
    payload["cable_envelope"] = {
        "reference_azimuth": 0,
        "lower_limit": 350,
        "upper_limit": -10,
    }
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422
    # 错误定位到上界字段（与 window_end 晚于 window_start 的定位方式一致）。
    assert any("upper_limit" in [str(x) for x in loc] for loc in _locs(resp.json()))


def test_envelope_span_too_large_is_422():
    payload = envelope_payload()
    payload["cable_envelope"] = {
        "reference_azimuth": 0,
        "lower_limit": -2000,
        "upper_limit": 2000,
    }
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422
    assert any("upper_limit" in [str(x) for x in loc] for loc in _locs(resp.json()))


def test_envelope_missing_field_is_422():
    payload = envelope_payload()
    del payload["cable_envelope"]["upper_limit"]
    resp = client.post("/api/schedule", json=payload)
    assert resp.status_code == 422
    assert any("upper_limit" in [str(x) for x in loc] for loc in _locs(resp.json()))


def test_legacy_mode_omitted_envelope_keeps_old_semantics():
    # 旧请求（不带 cable_envelope）：响应与逐步结果均无展开字段。
    resp = client.post("/api/schedule", json=valid_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["cable_envelope"] is None
    for o in body["observations"]:
        assert o["azimuth_start"] is None
        assert o["azimuth_end"] is None
        assert o["direction"] is None

    # 显式 null 与缺省等价。
    payload = valid_payload()
    payload["cable_envelope"] = None
    resp2 = client.post("/api/schedule", json=payload)
    assert resp2.status_code == 200
    assert resp2.json()["observations"] == body["observations"]
