"""求解器测试：转向数学、典型场景、以及与暴力枚举的全量对拍。"""

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
    _axis_seconds,
    ring_positions,
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
    """校验结果计划的内部一致性（时间链、窗口、必观、统计量）。

    电缆包络模式下额外校验：展开位置同余且落在区间内、转向不越界、
    有符号转角/方向与耗时一致。
    """
    by_id = {t.id: t for t in targets}
    env = cfg.cable_envelope
    if res.status == "infeasible":
        assert res.observations == ()
        return
    seen: set[str] = set()
    t_now = cfg.initial_time
    if env is None:
        az, el = cfg.initial_azimuth, cfg.initial_elevation
    else:
        az, el = env.reference_azimuth, cfg.initial_elevation
    total_priority = 0
    for obs in res.observations:
        t = by_id[obs.target_id]
        assert obs.target_id not in seen
        seen.add(obs.target_id)
        if env is None:
            d = abs(az - t.azimuth) % 360
            d = min(d, 360 - d)
            assert obs.azimuth_start is None and obs.azimuth_end is None
            assert obs.direction is None
        else:
            # 展开位置必须与目标方位同余且落在软限位区间内。
            assert obs.azimuth_start == az
            az_end = obs.azimuth_end
            assert az_end is not None
            assert env.lower_limit <= az <= env.upper_limit
            assert env.lower_limit <= az_end <= env.upper_limit
            assert az_end % 360 == t.azimuth % 360
            d = abs(az_end - az)
            delta = az_end - az
            expect_dir = "cw" if delta > 0 else ("ccw" if delta < 0 else "none")
            assert obs.direction == expect_dir
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
        if env is None:
            az = t.azimuth
        else:
            az = obs.azimuth_end
        el = t.elevation
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


# ---------------------------------------------------------------- 电缆包络


def env_cfg(
    reference: int = 0,
    lower: int = -360,
    upper: int = 360,
    initial_azimuth: int = 0,
    az_speed: float = 1.0,
) -> ObservatoryConfig:
    return ObservatoryConfig(
        initial_time=0,
        initial_azimuth=initial_azimuth,
        initial_elevation=0,
        azimuth_speed=az_speed,
        elevation_speed=1.0,
        cable_envelope=CableEnvelope(
            reference_azimuth=reference, lower_limit=lower, upper_limit=upper
        ),
    )


def test_ring_positions_lists_all_congruent_points():
    env = CableEnvelope(reference_azimuth=0, lower_limit=-370, upper_limit=350)
    assert ring_positions(0, env) == [-360, 0]
    assert ring_positions(350, env) == [-370, -10, 350]
    assert ring_positions(10, env) == [-350, 10]
    # 区间内不存在同余位置。
    env2 = CableEnvelope(reference_azimuth=0, lower_limit=0, upper_limit=10)
    assert ring_positions(20, env2) == []
    # 端点恰好可达。
    env3 = CableEnvelope(reference_azimuth=0, lower_limit=-360, upper_limit=360)
    assert ring_positions(0, env3) == [-360, 0, 360]


def test_envelope_near_congruent_unwrapped_slew_is_signed_linear():
    # 0° -> 350°：旧模式圆周最短为 10°（逆时针）；
    # 区间 [0, 350] 内 350 仅有展开位置 350，必须顺时针走 350°。
    # 用窗口强制顺序 A(350) -> B(10)：B 需从 350 逆时针走到 10（340°）。
    cfg = env_cfg(lower=0, upper=350)
    targets = [
        make_target("A", azimuth=350, duration=10, window_end=1000),
        make_target("B", azimuth=10, duration=10, window_start=400, window_end=2000),
    ]
    res = solve(cfg, targets)
    assert res.status == "ok"
    obs_a, obs_b = res.observations
    assert obs_a.target_id == "A"
    assert (obs_a.azimuth_start, obs_a.azimuth_end) == (0, 350)
    assert obs_a.direction == "cw"
    assert obs_a.slew.azimuth_seconds == 350
    assert obs_a.arrival_time == 350
    assert (obs_b.azimuth_start, obs_b.azimuth_end) == (350, 10)
    assert obs_b.direction == "ccw"
    assert obs_b.slew.azimuth_seconds == 340
    assert_plan_valid(cfg, targets, res)


def test_envelope_shortest_ring_fails_but_other_ring_works():
    # 区间 [-10, 350]：目标 A(350) 有 -10 与 350 两个圈位；B(349) 仅有 349。
    # 按"最短转向"先取 A=-10：到 B(349) 需 359°，B 在 t=360 才结束，
    # 再回 A(350) 到 361，超过 B 窗口 352 -> A 无法再观测 -> 必观组合失败。
    # 全局求解改取 A=350：B->A 仅 1°，顺序 B(结束350)->A(结束351) 可行。
    cfg = env_cfg(lower=-10, upper=350)
    targets = [
        make_target("A", azimuth=350, duration=1, window_end=360, must_observe=True),
        make_target("B", azimuth=349, duration=1, window_end=352, must_observe=True),
    ]
    res = solve(cfg, targets)
    assert res.status == "ok"
    assert [o.target_id for o in res.observations] == ["B", "A"]
    b, a = res.observations
    assert (b.azimuth_start, b.azimuth_end) == (0, 349)
    assert b.direction == "cw"
    assert (a.azimuth_start, a.azimuth_end) == (349, 350)
    assert (b.end, a.end) == (350, 352)
    assert_plan_valid(cfg, targets, res)


def test_envelope_no_common_ring_order_is_infeasible():
    # 若贪心选最近圈位则可行于一时，但两个必观目标没有共同可行的
    # 顺序与圈位组合：A(-10/350)、B(349) 窗口都只给 351 秒，
    # 任一圈位下都无法先到 -10（10s）再赶到 349（359s）。
    cfg = env_cfg(lower=-10, upper=350)
    targets = [
        make_target("A", azimuth=350, duration=1, window_end=351, must_observe=True),
        make_target("B", azimuth=349, duration=1, window_end=351, must_observe=True),
    ]
    res = solve(cfg, targets)
    assert res.status == "infeasible"
    assert res.message and "圈位" in res.message
    assert res.observations == ()
    assert set(res.unscheduled) == {"A", "B"}


def test_envelope_target_without_congruent_position_is_infeasible():
    # 目标方位 20 在区间 [0, 10] 内没有任何同余展开位置。
    cfg = env_cfg(lower=0, upper=10)
    targets = [
        make_target("M", azimuth=20, duration=1, window_end=100, must_observe=True),
        make_target("X", azimuth=0, duration=1, window_end=100),
    ]
    res = solve(cfg, targets)
    assert res.status == "infeasible"
    assert res.message and "M" in res.message
    assert "同余" in res.message


def test_envelope_soft_limit_boundaries_are_inclusive():
    # 下界 -90、上界 90：目标 270 的同余位置恰为 -90；目标 90 恰为上界。
    cfg = env_cfg(lower=-90, upper=90)
    targets = [
        make_target("L", azimuth=270, duration=10, window_end=200, must_observe=True),
        make_target("U", azimuth=90, duration=10, window_end=400, must_observe=True),
    ]
    res = solve(cfg, targets)
    assert res.status == "ok"
    l, u = res.observations
    assert (l.azimuth_start, l.azimuth_end) == (0, -90)
    assert l.direction == "ccw"
    assert (u.azimuth_start, u.azimuth_end) == (-90, 90)
    assert u.direction == "cw"
    assert u.slew.azimuth_seconds == 180
    assert_plan_valid(cfg, targets, res)


def test_envelope_boundary_crossing_is_not_allowed():
    # 区间 [-89, 89]：270 的最近同余位置 -90 落在界外，唯一位置 270 也在界外，
    # 即区间内无同余位置 -> 必观不可行，不得越过软限位转向。
    cfg = env_cfg(lower=-89, upper=89)
    targets = [
        make_target("M", azimuth=270, duration=1, window_end=1000, must_observe=True),
        make_target("X", azimuth=0, duration=1, window_end=1000),
    ]
    res = solve(cfg, targets)
    assert res.status == "infeasible"
    assert "M" in (res.message or "")


def test_envelope_unwrapped_azimuth_lexicographic_tie_break():
    # 同一编号序列 [A, ...] 下 A 有两个圈位，若结束时刻相同则取展开方位小者。
    # 区间 [-360, 360]、目标 A(10) 圈位 -350/10：从初始 0 出发分别于 350s、
    # 10s 到达，但窗口 500 才开启，观测都在 600 结束 -> 并列，取 -350。
    cfg = env_cfg(lower=-360, upper=360)
    targets = [
        make_target("A", azimuth=10, duration=100, window_start=500, window_end=700,
                    must_observe=True),
        make_target("B", azimuth=0, duration=100, window_start=700, window_end=4000,
                    must_observe=True),
    ]
    res = solve(cfg, targets)
    assert res.status == "ok"
    assert [o.target_id for o in res.observations] == ["A", "B"]
    assert res.observations[0].azimuth_end == -350
    # B 从 -350 出发到圈位 -360 仅 10°，可等待至 700，结束最早。
    assert res.observations[1].azimuth_end == -360
    assert_plan_valid(cfg, targets, res)


def test_envelope_reference_unwrapped_nonzero():
    # 初始方位 350，电缆零位给展开值 710（=350+360），区间 [350, 720]。
    # A(10) 在区间内只有展开位置 370；用窗口强制 A 先观测。
    cfg = env_cfg(reference=710, lower=350, upper=720, initial_azimuth=350)
    targets = [
        make_target("A", azimuth=10, duration=10, window_end=2000, must_observe=True),
        make_target("B", azimuth=350, duration=10, window_start=500, window_end=4000),
    ]
    res = solve(cfg, targets)
    assert res.status == "ok"
    assert [o.target_id for o in res.observations] == ["A", "B"]
    assert res.observations[0].azimuth_start == 710
    assert res.observations[0].azimuth_end == 370
    assert_plan_valid(cfg, targets, res)


def test_envelope_validate_helper_rejects_bad_inputs():
    base = env_cfg()
    with pytest.raises(ValueError):
        solve(
            ObservatoryConfig(
                0, 0, 0, 1.0, 1.0,
                cable_envelope=CableEnvelope(0, 10, 0),  # 下界大于上界
            ),
            [make_target("A"), make_target("B")],
        )
    with pytest.raises(ValueError):
        solve(
            ObservatoryConfig(
                0, 0, 0, 1.0, 1.0,
                cable_envelope=CableEnvelope(0, -2000, 2000),  # 跨度过大
            ),
            [make_target("A"), make_target("B")],
        )
    with pytest.raises(ValueError):
        solve(
            ObservatoryConfig(
                0, 10, 0, 1.0, 1.0,
                cable_envelope=CableEnvelope(0, -360, 360),  # 零位不与初始方位同余
            ),
            [make_target("A"), make_target("B")],
        )
    with pytest.raises(ValueError):
        solve(
            ObservatoryConfig(
                0, 0, 0, 1.0, 1.0,
                cable_envelope=CableEnvelope(400, 0, 360),  # 零位不在区间内
            ),
            [make_target("A"), make_target("B")],
        )


# ------------------------------------------------- 电缆包络暴力对拍


def brute_force_best_envelope(cfg: ObservatoryConfig, targets: list[Target]):
    """枚举子集 × 排列 × 每目标圈位，按六级字典序取最优（独立实现）。"""
    assert cfg.cable_envelope is not None
    env = cfg.cable_envelope
    n = len(targets)
    must = {i for i, t in enumerate(targets) if t.must_observe}
    choices = [ring_positions(t.azimuth, env) for t in targets]
    init_az = env.reference_azimuth

    def slew_sec(a0: int, el0: int, a1: int, el1: int) -> int:
        a = math.ceil(round(abs(a1 - a0) / cfg.azimuth_speed, 9))
        e = math.ceil(round(abs(el1 - el0) / cfg.elevation_speed, 9))
        return max(a, e)

    best = None
    for r in range(n + 1):
        for combo in itertools.combinations(range(n), r):
            if not must.issubset(combo):
                continue
            if any(choices[c] == [] for c in combo):
                continue
            for perm in itertools.permutations(combo):
                ring_options = [range(len(choices[c])) for c in perm]
                for rings in itertools.product(*ring_options):
                    t_now = cfg.initial_time
                    az, el = init_az, cfg.initial_elevation
                    ok = True
                    for c, k in zip(perm, rings):
                        tgt = targets[c]
                        az1 = choices[c][k]
                        sec = slew_sec(az, el, az1, tgt.elevation)
                        arr = t_now + sec
                        s = max(arr, tgt.window_start)
                        e = s + tgt.duration
                        if e > tgt.window_end:
                            ok = False
                            break
                        t_now = e
                        az, el = az1, tgt.elevation
                    if not ok:
                        continue
                    key = (
                        -sum(targets[c].priority for c in perm),
                        -len(perm),
                        t_now,
                        tuple(targets[c].id for c in perm),
                        tuple(choices[c][k] for c, k in zip(perm, rings)),
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
        tuple(o.azimuth_end for o in res.observations),
    )


def random_envelope_case(rng: random.Random, n: int):
    initial_azimuth = rng.randint(0, 359)
    # 跨度覆盖 1~3 整圈，零位取初始方位附近的某个同余展开值。
    span = rng.choice([180, 359, 360, 540, 720, 900, 1080])
    center = initial_azimuth + 360 * rng.randint(-1, 1)
    lower = center - span // 2
    upper = lower + span
    if not (lower <= center <= upper):
        center = lower
    env = CableEnvelope(reference_azimuth=center, lower_limit=lower, upper_limit=upper)
    cfg = ObservatoryConfig(
        initial_time=rng.randint(0, 40000),
        initial_azimuth=initial_azimuth,
        initial_elevation=rng.randint(0, 90),
        azimuth_speed=rng.choice([0.5, 1, 1.5, 2, 3, 5]),
        elevation_speed=rng.choice([0.5, 1, 1.5, 2, 3]),
        cable_envelope=env,
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
    return cfg, targets


@pytest.mark.parametrize("seed", range(40))
def test_envelope_against_brute_force_small(seed: int):
    rng = random.Random(9000 + seed)
    cfg, targets = random_envelope_case(rng, rng.randint(2, 6))
    res = solve(cfg, targets)
    assert_plan_valid(cfg, targets, res)
    assert result_key_envelope(res) == brute_force_best_envelope(cfg, targets)


def test_envelope_sixteen_targets_within_time_budget():
    rng = random.Random(11)
    cfg, targets = random_envelope_case(rng, 16)
    started = time.monotonic()
    res = solve(cfg, targets)
    elapsed = time.monotonic() - started
    assert res.status in ("ok", "infeasible")
    assert_plan_valid(cfg, targets, res)
    assert elapsed < 30, f"电缆包络 16 目标求解耗时 {elapsed:.1f}s，超出预算"
