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
