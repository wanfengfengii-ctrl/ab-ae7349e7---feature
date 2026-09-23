#!/usr/bin/env python3
"""API 冒烟测试：健康检查、前端托管、正常排程、可定位校验错误、必观不可行。

用法: python smoke_test.py [base_url]
以退出码报告结果（0 通过，非 0 失败）。
"""

from __future__ import annotations

import copy
import json
import sys
import time
import urllib.error
import urllib.request

FAILURES: list[str] = []


def request(method: str, url: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, raw, _parse_json(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, raw, _parse_json(raw)


def _parse_json(raw: str):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {extra}")
        FAILURES.append(name)


def wait_for_health(base: str, attempts: int = 60, delay: float = 0.5) -> None:
    for _ in range(attempts):
        try:
            status, _, body = request("GET", f"{base}/api/health")
            if status == 200 and body and body.get("status") == "ok":
                print("  PASS /api/health 就绪")
                return
        except Exception:
            pass
        time.sleep(delay)
    print("  FAIL /api/health 未就绪")
    raise SystemExit(1)


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


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    print(f"[smoke] 目标服务: {base}")

    wait_for_health(base)

    print("[smoke] 前端静态托管")
    status, raw, _ = request("GET", f"{base}/")
    check("GET / 返回前端页面", status == 200 and 'id="root"' in raw, status)

    print("[smoke] 正常排程")
    status, _, body = request("POST", f"{base}/api/schedule", valid_payload())
    check("HTTP 200", status == 200, status)
    check("status == ok", bool(body) and body.get("status") == "ok", body)
    if body and body.get("status") == "ok":
        ids = [o["target_id"] for o in body["observations"]]
        check("观测序列 == [A, B, C]", ids == ["A", "B", "C"], ids)
        check("结束时刻 == 510", body["end_time"] == 510, body["end_time"])
        check("总优先级 == 7", body["total_priority"] == 7, body["total_priority"])
        check("未选目标 == [D]", body["unscheduled"] == ["D"], body["unscheduled"])
        a, _, c = body["observations"]
        check(
            "A 转向分解 == 10/0/10",
            a["slew"] == {"azimuth_seconds": 10, "elevation_seconds": 0, "total_seconds": 10},
            a["slew"],
        )
        check(
            "C 等待 320s 后于窗口内观测",
            (c["arrival_time"], c["wait_seconds"], c["start"], c["end"]) == (180, 320, 500, 510),
            c,
        )

    print("[smoke] 非法字段返回可定位错误")
    bad = copy.deepcopy(valid_payload())
    bad["targets"][0]["azimuth"] = 360
    status, _, body = request("POST", f"{base}/api/schedule", bad)
    check("HTTP 422", status == 422, status)
    locs = [item.get("loc", []) for item in (body or {}).get("detail", [])]
    check(
        "错误定位包含 targets/0/azimuth",
        any("azimuth" in [str(x) for x in loc] for loc in locs)
        and any("targets" in [str(x) for x in loc] for loc in locs),
        locs,
    )

    print("[smoke] 必观不可行返回明确状态")
    infeasible = valid_payload()
    infeasible["targets"] = [
        {"id": "M1", "azimuth": 0, "elevation": 0, "duration": 100,
         "window_start": 0, "window_end": 150, "priority": 5, "must_observe": True},
        {"id": "M2", "azimuth": 90, "elevation": 0, "duration": 100,
         "window_start": 0, "window_end": 150, "priority": 5, "must_observe": True},
    ]
    status, _, body = request("POST", f"{base}/api/schedule", infeasible)
    check("HTTP 200", status == 200, status)
    check("status == infeasible", bool(body) and body.get("status") == "infeasible", body)

    if FAILURES:
        print(f"[smoke] 失败 {len(FAILURES)} 项: {FAILURES}")
        return 1
    print("[smoke] 全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (urllib.error.URLError, OSError) as exc:
        print(f"[smoke] 连接失败: {exc}")
        sys.exit(1)
