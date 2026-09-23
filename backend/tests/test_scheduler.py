"""求解器测试：转向数学、典型场景、以及与暴力枚举的全量对拍。"""

from __future__ import annotations

import itertools
import math
import random
import string
import time

import pytest

from app.scheduler import (
    ObservatoryConfig,
    ScheduleResult,
    Target,
    _axis_seconds,
    solve,
)

CFG = ObservatoryConfig(
    initial_time=0,
    initial_azimuth=0,
    initial_elevation=0,
    azimuth_speed=1.0,
    elevation_speed=1.0,
)


def make_target(
    id: str,
    azimuth: int = 0,
    elevation: int = 0,
    duration: int = 100,
    window_start: int = 0,
    window_end: int = 86400,
    priority: int = 1,
    must_observe: bool = False,
) -> Target:
    return Target(
        id=id,
        azimuth=azimuth,
        elevation=elevation,
        duration=duration,
        window_start=window_start,
        window_end=window_end,
        priority=priority,
        must_observe=must_observe,
    )


def assert_plan_valid(cfg: ObservatoryConfig, targets: list[Target], res: ScheduleResult):
    """校验结果计划的内部一致性（时间链、窗口、必观、统计量）。"""
    by_id = {t.id: t for t in targets}
    if res.status == "infeasible":
        assert res.observations == ()
        return
    seen: set[str] = set()
    t_now = cfg.initial_time
    az, el = cfg.initial_azimuth, cfg.initial_elevation
    total_priority = 0
    for obs in res.observations:
        t = by_id[obs.target_id]
        assert obs.target_id not in seen
        seen.add(obs.target_id)
        d = abs(az - t.azimuth) % 360
        d = min(d, 360 - d)
        az_sec = math.ceil(round(d / cfg.azimuth_speed, 9))
        el_sec = math.ceil(round(abs(el - t.elevation) / cfg.elevation_speed, 9))
        assert obs.slew.azimuth_seconds == az_sec
        assert obs.slew.elevation_seconds == el_sec
        assert obs.slew.total_seconds == max(az_sec, el_sec)
        assert obs.arrival_time == t_now + obs.slew.total_seconds
        assert obs.start == max(obs.arrival_time, t.window_start)
        assert obs.wait_seconds == obs.start - obs.arrival_time
        assert obs.end == obs.start + t.duration
        assert obs.end <= t.window_end
        total_priority += t.priority
        t_now = obs.end
        az, el = t.azimuth, t.elevation
    for t in targets:
        if t.must_observe:
            assert t.id in seen, f"必观目标 {t.id} 未入选"
    assert res.total_priority == total_priority
    assert res.target_count == len(res.observations)
    assert res.end_time == t_now
    assert set(res.unscheduled) == set(by_id) - seen


# ---------------------------------------------------------------- 转向数学


def test_azimuth_uses_shortest_circular_distance():
    cfg = ObservatoryConfig(0, 350, 0, 2.0, 1.0)
    az_sec, el_sec = _axis_seconds(cfg, 350, 0, 10, 0)
    assert az_sec == 10  # 圆周最短距离 20 度 / 2 = 10 秒
    assert el_sec == 0


def test_slew_rounds_up_per_axis_and_takes_max():
    cfg = ObservatoryConfig(0, 0, 0, 2.0, 4.0)
    az_sec, el_sec = _axis_seconds(cfg, 0, 0, 5, 10)
    assert az_sec == 3  # ceil(5/2)
    assert el_sec == 3  # ceil(10/4)
    cfg2 = ObservatoryConfig(0, 0, 0, 2.0, 1.0)
    az_sec2, el_sec2 = _axis_seconds(cfg2, 0, 0, 4, 25)
    assert max(az_sec2, el_sec2) == 25  # 两轴同时运行，取最大


# ---------------------------------------------------------------- 典型场景


def test_wait_until_window_opens():
    targets = [
        make_target("W", duration=10, window_start=100, window_end=200, priority=5),
        make_target("X", azimuth=180, elevation=80, duration=500, window_start=0, window_end=100),
    ]
    res = solve(CFG, targets)
    assert res.status == "ok"
    assert [o.target_id for o in res.observations] == ["W"]
    obs = res.observations[0]
    assert (obs.arrival_time, obs.wait_seconds, obs.start, obs.end) == (0, 100, 100, 110)
    assert res.unscheduled == ("X",)
    assert_plan_valid(CFG, targets, res)


def test_infeasible_must_observe():
    targets = [
        make_target("M1", duration=100, window_start=0, window_end=150, must_observe=True),
        make_target("M2", azimuth=90, duration=100, window_start=0, window_end=150, must_observe=True),
    ]
    res = solve(CFG, targets)
    assert res.status == "infeasible"
    assert res.observations == ()
    assert set(res.unscheduled) == {"M1", "M2"}


def test_total_priority_beats_count():
    # {A}=10 分 1 个目标；{B,C}=12 分 2 个目标；三者不可兼得。
    targets = [
        make_target("A", duration=300, window_start=0, window_end=300, priority=10),
        make_target("B", duration=100, window_start=0, window_end=300, priority=6),
        make_target("C", duration=100, window_start=200, window_end=350, priority=6),
    ]
    res = solve(CFG, targets)
    assert [o.target_id for o in res.observations] == ["B", "C"]
    assert res.total_priority == 12
    assert_plan_valid(CFG, targets, res)


def test_count_breaks_priority_tie():
    # {X,Y} 与 {Z} 同为 8 分，目标数多者胜。
    targets = [
        make_target("Z", duration=200, window_start=0, window_end=200, priority=8),
        make_target("X", duration=100, window_start=0, window_end=200, priority=4),
        make_target("Y", duration=100, window_start=100, window_end=250, priority=4),
    ]
    res = solve(CFG, targets)
    assert [o.target_id for o in res.observations] == ["X", "Y"]
    assert res.target_count == 2
    assert_plan_valid(CFG, targets, res)


def test_earlier_end_breaks_tie():
    # 同一子集两种顺序：P->Q 结束于 600，Q->P 结束于 700。
    targets = [
        make_target("P", duration=100, window_start=0, window_end=1000, priority=5),
        make_target("Q", duration=100, window_start=500, window_end=1000, priority=5),
    ]
    res = solve(CFG, targets)
    assert [o.target_id for o in res.observations] == ["P", "Q"]
    assert res.end_time == 600
    assert_plan_valid(CFG, targets, res)


def test_lexicographic_sequence_breaks_tie():
    # A->B 与 B->A 都结束于 170，取编号序列字典序较小者。
    targets = [
        make_target("A", azimuth=10, duration=100, window_start=0, window_end=1000, priority=5),
        make_target("B", elevation=10, duration=50, window_start=0, window_end=200, priority=1),
    ]
    res = solve(CFG, targets)
    assert [o.target_id for o in res.observations] == ["A", "B"]
    assert res.end_time == 170
    assert_plan_valid(CFG, targets, res)


def test_lexicographic_across_subsets():
    # R1 与 R2 互斥且各项指标相同，取编号较小者。
    targets = [
        make_target("R2", duration=100, window_start=0, window_end=100, priority=5),
        make_target("R1", duration=100, window_start=0, window_end=100, priority=5),
    ]
    res = solve(CFG, targets)
    assert [o.target_id for o in res.observations] == ["R1"]
    assert res.unscheduled == ("R2",)


def test_must_observe_is_included_despite_low_priority():
    targets = [
        make_target("M", duration=50, window_start=0, window_end=50, priority=1, must_observe=True),
        make_target("H", duration=100, window_start=0, window_end=1000, priority=100),
    ]
    res = solve(CFG, targets)
    assert [o.target_id for o in res.observations] == ["M", "H"]
    assert_plan_valid(CFG, targets, res)


def test_empty_schedule_when_nothing_feasible():
    targets = [
        make_target("X", duration=100, window_start=0, window_end=50),
        make_target("Y", duration=100, window_start=0, window_end=50),
    ]
    res = solve(CFG, targets)
    assert res.status == "ok"
    assert res.observations == ()
    assert res.total_priority == 0
    assert res.end_time == CFG.initial_time
    assert set(res.unscheduled) == {"X", "Y"}


# ---------------------------------------------------------------- 暴力对拍


def brute_force_best(cfg: ObservatoryConfig, targets: list[Target]):
    """独立实现：枚举所有子集的所有排列，取同一目标元组的最优。"""
    n = len(targets)
    must = {i for i, t in enumerate(targets) if t.must_observe}
    azs = [t.azimuth for t in targets] + [cfg.initial_azimuth]
    els = [t.elevation for t in targets] + [cfg.initial_elevation]

    def slew(src: int, dst: int) -> int:
        d = abs(azs[src] - azs[dst]) % 360
        d = min(d, 360 - d)
        a = math.ceil(round(d / cfg.azimuth_speed, 9))
        e = math.ceil(round(abs(els[src] - els[dst]) / cfg.elevation_speed, 9))
        return max(a, e)

    best = None
    for r in range(n + 1):
        for combo in itertools.combinations(range(n), r):
            if not must.issubset(combo):
                continue
            for perm in itertools.permutations(combo):
                t = cfg.initial_time
                src = n
                ok = True
                for c in perm:
                    arr = t + slew(src, c)
                    s = max(arr, targets[c].window_start)
                    e = s + targets[c].duration
                    if e > targets[c].window_end:
                        ok = False
                        break
                    t = e
                    src = c
                if not ok:
                    continue
                key = (
                    -sum(targets[c].priority for c in perm),
                    -len(perm),
                    t,
                    tuple(targets[c].id for c in perm),
                )
                if best is None or key < best:
                    best = key
    return best


def result_key(res: ScheduleResult):
    if res.status == "infeasible":
        return None
    return (
        -res.total_priority,
        -res.target_count,
        res.end_time,
        tuple(o.target_id for o in res.observations),
    )


def random_case(rng: random.Random, n: int):
    cfg = ObservatoryConfig(
        initial_time=rng.randint(0, 40000),
        initial_azimuth=rng.randint(0, 359),
        initial_elevation=rng.randint(0, 90),
        azimuth_speed=rng.choice([0.5, 1, 1.5, 2, 3, 5]),
        elevation_speed=rng.choice([0.5, 1, 1.5, 2, 3]),
    )
    ids = rng.sample(string.ascii_uppercase, n)
    targets = []
    for i in range(n):
        ws = rng.randint(0, 84000)
        we = ws + rng.randint(1, 86400 - ws)
        targets.append(
            Target(
                id=ids[i],
                azimuth=rng.randint(0, 359),
                elevation=rng.randint(0, 90),
                duration=rng.randint(1, 1200),
                window_start=ws,
                window_end=we,
                priority=rng.randint(1, 10),
                must_observe=rng.random() < 0.3,
            )
        )
    return cfg, targets


@pytest.mark.parametrize("seed", range(60))
def test_against_brute_force_small(seed: int):
    rng = random.Random(1000 + seed)
    cfg, targets = random_case(rng, rng.randint(2, 7))
    res = solve(cfg, targets)
    assert_plan_valid(cfg, targets, res)
    assert result_key(res) == brute_force_best(cfg, targets)


@pytest.mark.parametrize("seed", range(8))
def test_against_brute_force_eight(seed: int):
    rng = random.Random(5000 + seed)
    cfg, targets = random_case(rng, 8)
    res = solve(cfg, targets)
    assert_plan_valid(cfg, targets, res)
    assert result_key(res) == brute_force_best(cfg, targets)


def test_solve_sixteen_targets_within_time_budget():
    rng = random.Random(7)
    cfg, targets = random_case(rng, 16)
    started = time.monotonic()
    res = solve(cfg, targets)
    elapsed = time.monotonic() - started
    assert res.status in ("ok", "infeasible")
    assert_plan_valid(cfg, targets, res)
    assert elapsed < 30, f"16 目标求解耗时 {elapsed:.1f}s，超出预算"
