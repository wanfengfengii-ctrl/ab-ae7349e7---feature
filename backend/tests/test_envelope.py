"""电缆包络模式求解器测试。

覆盖：
- 圈位与顺序联合求解（最短转向越界 / 另一圈位可行）；
- 软限位边界（端点可达、圈位计数）；
- 编号序列相同时展开方位序列字典序决胜；
- 不可行原因（区间内无同余位置 / 无可行顺序与圈位组合）；
- 与暴力枚举（子集 × 排列 × 圈位）全量对拍。
"""

from __future__ import annotations

import itertools
import math
import random
import string
import time

import pytest

from app.scheduler import (
    CableEnvelope,
    ObservatoryConfig,
    ScheduleResult,
    Target,
    solve,
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


def cfg(min_az: int, max_az: int, initial_unwrapped: int = 0, initial_mod: int = 0):
    return ObservatoryConfig(
        initial_time=0,
        initial_azimuth=initial_mod,
        initial_elevation=0,
        azimuth_speed=1.0,
        elevation_speed=1.0,
        envelope=CableEnvelope(initial_unwrapped, min_az, max_az),
    )


def assert_envelope_plan_valid(
    c: ObservatoryConfig, targets: list[Target], res: ScheduleResult
):
    """校验电缆包络计划的内部一致性（区间、同余、顺逆、时间链）。"""
    env = c.envelope
    assert env is not None
    by_id = {t.id: t for t in targets}
    assert res.status == "ok"
    t_now = c.initial_time
    cur_az = env.initial_azimuth
    cur_el = c.initial_elevation
    total_priority = 0
    seen: set[str] = set()
    for obs in res.observations:
        t = by_id[obs.target_id]
        s = obs.slew
        assert s.from_azimuth == cur_az
        assert s.to_azimuth == obs.arrival_azimuth
        # 终点与目标方位同余，且起终点都在软限位区间内（转向路径不越界）。
        assert s.to_azimuth % 360 == t.azimuth % 360
        assert env.min_azimuth <= s.from_azimuth <= env.max_azimuth
        assert env.min_azimuth <= s.to_azimuth <= env.max_azimuth
        delta = s.to_azimuth - s.from_azimuth
        assert s.direction == ("cw" if delta >= 0 else "ccw")
        az_sec = math.ceil(round(abs(delta) / c.azimuth_speed, 9))
        el_sec = math.ceil(round(abs(cur_el - t.elevation) / c.elevation_speed, 9))
        assert s.azimuth_seconds == az_sec
        assert s.elevation_seconds == el_sec
        assert s.total_seconds == max(az_sec, el_sec)
        assert obs.arrival_time == t_now + s.total_seconds
        assert obs.start == max(obs.arrival_time, t.window_start)
        assert obs.wait_seconds == obs.start - obs.arrival_time
        assert obs.end == obs.start + t.duration <= t.window_end
        total_priority += t.priority
        t_now = obs.end
        cur_az = s.to_azimuth
        cur_el = t.elevation
        seen.add(t.id)
    for t in targets:
        if t.must_observe:
            assert t.id in seen
    assert res.total_priority == total_priority
    assert res.target_count == len(res.observations)
    assert res.end_time == t_now


# ------------------------------------------------ 最短转向失败、另一圈位可行


def test_shortest_slew_crosses_soft_limit_so_long_way_chosen():
    # 区间 [0,360]，初始 0：目标 350° 的最短转向 (-10) 越下界，必须顺转 350。
    targets = [
        make_target("M1", 350, must_observe=True),
        make_target("M2", 10, must_observe=True),
    ]
    res = solve(cfg(0, 360), targets)
    assert res.status == "ok"
    steps = [(o.target_id, o.slew.to_azimuth, o.slew.direction) for o in res.observations]
    assert steps == [("M2", 10, "cw"), ("M1", 350, "cw")]
    assert res.observations[1].slew.azimuth_seconds == 340
    assert_envelope_plan_valid(cfg(0, 360), targets, res)


def test_wrap_choice_must_consider_future_targets():
    # 区间 [-180,180]，目标 180° 可取 -180 或 +180（边界）。
    # 贪心取近者（-180 或 +180 等距）不影响关键：后续 170° 窗口很紧，
    # 只有先停在 +180（距 170 为 10）才能赶上；停 -180 需 350s，必败。
    targets = [
        make_target("M1", 180, duration=10, must_observe=True),
        make_target("M2", 170, duration=60, window_start=180, window_end=260, must_observe=True),
    ]
    res = solve(cfg(-180, 180), targets)
    assert res.status == "ok"
    assert [o.target_id for o in res.observations] == ["M1", "M2"]
    first, second = res.observations
    assert first.arrival_azimuth == 180
    assert first.slew.direction == "cw"
    assert (second.slew.from_azimuth, second.slew.to_azimuth) == (180, 170)
    assert second.slew.direction == "ccw"
    assert (second.arrival_time, second.end) == (200, 260)
    assert_envelope_plan_valid(cfg(-180, 180), targets, res)


def test_future_reachability_forces_wrap_with_three_targets():
    # 三目标均必观，区间 [-270,270]，转速 1。
    # N(270) 唯一近圈位是 -90（距离 90）；若先停在 +270（距离 270）
    # 会错过 N 的紧窗口。全局求解须在顺序与圈位间找到可行组合。
    targets = [
        make_target("M", 180, duration=10, must_observe=True),
        make_target("N", 270, duration=10, window_start=0, window_end=100, must_observe=True),
        make_target("O", 90, duration=10, must_observe=True),
    ]
    c = cfg(-270, 270)
    res = solve(c, targets)
    assert res.status == "ok"
    assert {o.target_id for o in res.observations} == {"M", "N", "O"}
    assert_envelope_plan_valid(c, targets, res)
    n_obs = next(o for o in res.observations if o.target_id == "N")
    assert n_obs.arrival_azimuth == -90
    assert n_obs.end <= 100


# ---------------------------------------------------------------- 边界


def test_soft_limit_endpoints_are_reachable():
    # 区间 [-360, 0]，初始展开 -360（与 0 同余）：端点 -360 与 0 都可用。
    c = cfg(-360, 0, initial_unwrapped=-360, initial_mod=0)
    targets = [
        make_target("M1", 0, must_observe=True),  # 圈位 -360/0
        make_target("M2", 180, duration=100, must_observe=True),  # 圈位 -180
    ]
    res = solve(c, targets)
    assert res.status == "ok"
    assert [o.arrival_azimuth for o in res.observations] == [-360, -180]
    assert_envelope_plan_valid(c, targets, res)


def test_arrival_may_rest_at_both_interval_endpoints():
    c = cfg(0, 360)
    targets = [make_target("X", 0, must_observe=True), make_target("Y", 0, must_observe=True)]
    # 编号不同但方位相同：字典序展开方位最小优先（停 0 而非 360）。
    res = solve(c, targets)
    assert res.status == "ok"
    assert [o.arrival_azimuth for o in res.observations] == [0, 0]


def test_unwrapped_initial_offset_does_not_change_congruent_geometry():
    # 区间 [0,360]/初始 0 整体平移 +360：初始展开 360（物理 0），
    # 区间 [360,720]；计划几何应完全一致（方位全部 +360）。
    c = cfg(360, 720, initial_unwrapped=360, initial_mod=0)
    targets = [
        make_target("M1", 350, must_observe=True),
        make_target("M2", 10, must_observe=True),
    ]
    res = solve(c, targets)
    assert res.status == "ok"
    assert [o.target_id for o in res.observations] == ["M2", "M1"]
    assert [o.arrival_azimuth for o in res.observations] == [370, 710]


# ---------------------------------------------------------------- 决胜规则


def test_lex_tie_break_on_unwrapped_azimuth_sequence():
    # 区间 [-180,180]：180° 目标可取 -180/180；编号序列相同时取展开方位更小。
    c = cfg(-180, 180)
    targets = [
        make_target("M1", 180, duration=10, must_observe=True),
        make_target("M2", 0, duration=10, must_observe=True),
    ]
    res = solve(c, targets)
    assert res.status == "ok"
    assert [o.target_id for o in res.observations] == ["M2", "M1"]
    assert res.observations[1].arrival_azimuth == -180


# ---------------------------------------------------------------- 不可行


def test_infeasible_when_must_target_has_no_congruent_position():
    c = cfg(0, 179)
    targets = [make_target("M1", 180, must_observe=True), make_target("M2", 0)]
    res = solve(c, targets)
    assert res.status == "infeasible"
    assert res.message is not None and "M1" in res.message
    assert res.observations == ()
    assert res.end_time is None


def test_infeasible_when_no_order_and_wrap_combination_fits_windows():
    # 区间 [0,720]：两必观目标相距 340 度，方位转速 1，窗口都只开 200s，
    # 无论选哪个圈位、何种顺序都无法在窗口内完成两个观测。
    c = cfg(0, 720)
    targets = [
        make_target("M1", 10, duration=10, window_start=0, window_end=200, must_observe=True),
        make_target("M2", 350, duration=10, window_start=0, window_end=200, must_observe=True),
    ]
    res = solve(c, targets)
    assert res.status == "infeasible"
    assert res.message


# ---------------------------------------------------------------- 旧语义回归


def test_legacy_identical_with_without_envelope_when_shortest_fits():
    # 区间宽度足够大且初始在 0 时，包络解应包含旧的最短转向选择。
    targets = [
        make_target("A", 10, duration=100, priority=5),
        make_target("B", 350, duration=100, priority=1),
    ]
    legacy = solve(
        ObservatoryConfig(0, 0, 0, 1.0, 1.0),
        targets,
    )
    # 旧模式：350 按圆周最短 10s 到达。
    assert legacy.observations[0].slew.azimuth_seconds == 10
    c = cfg(-720, 720)
    res = solve(c, targets)
    assert res.status == "ok"
    assert_envelope_plan_valid(c, targets, res)
    assert res.end_time == legacy.end_time


# ---------------------------------------------------------------- 暴力对拍


def brute_force_envelope(c: ObservatoryConfig, targets: list[Target]):
    """枚举子集 × 排列 × 每个观测的圈位，按完整决胜键取最优。"""
    env = c.envelope
    assert env is not None
    n = len(targets)
    must = {i for i, t in enumerate(targets) if t.must_observe}

    def wraps(i: int) -> list[int]:
        k_lo = math.ceil((env.min_azimuth - targets[i].azimuth) / 360)
        k_hi = math.floor((env.max_azimuth - targets[i].azimuth) / 360)
        return [targets[i].azimuth + 360 * k for k in range(k_lo, k_hi + 1)]

    wrap_opts = [wraps(i) for i in range(n)]
    if any(not wrap_opts[i] and i in must for i in range(n)):
        return None

    best = None
    for r in range(n + 1):
        for combo in itertools.combinations(range(n), r):
            if not must.issubset(combo):
                continue
            for perm in itertools.permutations(combo):
                choices = [wrap_opts[i] for i in perm]
                if any(not opts for opts in choices):
                    continue
                for azs in itertools.product(*choices):
                    t = c.initial_time
                    cur_az = env.initial_azimuth
                    cur_el = c.initial_elevation
                    ok = True
                    for i, az in zip(perm, azs):
                        sec_az = math.ceil(round(abs(az - cur_az) / c.azimuth_speed, 9))
                        sec_el = math.ceil(
                            round(abs(cur_el - targets[i].elevation) / c.elevation_speed, 9)
                        )
                        arr = t + max(sec_az, sec_el)
                        s = max(arr, targets[i].window_start)
                        e = s + targets[i].duration
                        if e > targets[i].window_end:
                            ok = False
                            break
                        t, cur_az, cur_el = e, az, targets[i].elevation
                    if not ok:
                        continue
                    key = (
                        -sum(targets[i].priority for i in perm),
                        -len(perm),
                        t,
                        tuple(targets[i].id for i in perm),
                        azs,
                    )
                    if best is None or key < best:
                        best = key
    return best


def result_key_envelope(res: ScheduleResult):
    if res.status == "infeasible":
        return None
    return (
        -res.total_priority,
        -res.target_count,
        res.end_time,
        tuple(o.target_id for o in res.observations),
        tuple(o.arrival_azimuth for o in res.observations),
    )


def random_envelope_case(rng: random.Random, n: int):
    initial_mod = rng.randint(0, 359)
    # 区间宽度不超过 API 上限 720：每个目标至多展开出 3 个同余圈位。
    half = rng.randint(160, 340)
    slack = 720 - 2 * half
    left_pad = rng.randint(0, slack)
    right_pad = slack - left_pad
    # 初始展开位置取初始方位 + 360*k0，区间围绕它张开。
    k0 = rng.choice([-1, 0, 1])
    initial_unwrapped = initial_mod + 360 * k0
    min_az = initial_unwrapped - half - left_pad
    max_az = initial_unwrapped + half + right_pad
    assert max_az - min_az <= 720
    c = ObservatoryConfig(
        initial_time=rng.randint(0, 40000),
        initial_azimuth=initial_mod,
        initial_elevation=rng.randint(0, 90),
        azimuth_speed=rng.choice([0.5, 1, 2, 3]),
        elevation_speed=rng.choice([0.5, 1, 2]),
        envelope=CableEnvelope(initial_unwrapped, min_az, max_az),
    )
    ids = rng.sample(string.ascii_uppercase, n)
    targets = []
    for i in range(n):
        ws = rng.randint(0, 84000)
        targets.append(
            Target(
                id=ids[i],
                azimuth=rng.randint(0, 359),
                elevation=rng.randint(0, 90),
                duration=rng.randint(1, 1200),
                window_start=ws,
                window_end=ws + rng.randint(1, 86400 - ws),
                priority=rng.randint(1, 10),
                must_observe=rng.random() < 0.3,
            )
        )
    return c, targets


def test_random_envelope_case_generator_valid():
    c, targets = random_envelope_case(random.Random(1), 5)
    env = c.envelope
    assert env.initial_azimuth % 360 == c.initial_azimuth % 360
    assert env.min_azimuth <= env.initial_azimuth <= env.max_azimuth
    assert len(targets) == 5


@pytest.mark.parametrize("seed", range(40))
def test_against_brute_force_envelope_small(seed: int):
    rng = random.Random(9000 + seed)
    c, targets = random_envelope_case(rng, rng.randint(2, 6))
    res = solve(c, targets)
    if res.status == "ok":
        assert_envelope_plan_valid(c, targets, res)
    assert result_key_envelope(res) == brute_force_envelope(c, targets)


@pytest.mark.parametrize("seed", range(6))
def test_against_brute_force_envelope_seven(seed: int):
    rng = random.Random(9500 + seed)
    c, targets = random_envelope_case(rng, 7)
    res = solve(c, targets)
    if res.status == "ok":
        assert_envelope_plan_valid(c, targets, res)
    assert result_key_envelope(res) == brute_force_envelope(c, targets)


def test_envelope_sixteen_targets_within_time_budget():
    rng = random.Random(11)
    c, targets = random_envelope_case(rng, 16)
    started = time.monotonic()
    res = solve(c, targets)
    elapsed = time.monotonic() - started
    assert res.status in ("ok", "infeasible")
    if res.status == "ok":
        assert_envelope_plan_valid(c, targets, res)
    assert elapsed < 30, f"16 目标电缆包络求解耗时 {elapsed:.1f}s"
