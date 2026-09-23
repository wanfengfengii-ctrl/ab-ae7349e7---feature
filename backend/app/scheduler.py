"""夜间射电观测排程的全局精确求解器。

模型
----
- 时间均为当天整数秒；方位角 0..359，俯仰角 0..90，均为整数。
- 方位轴按圆周最短距离转动，俯仰轴按绝对差转动，两轴可同时运行。
- 一次转向耗时 = max(ceil(方位距离 / 方位转速), ceil(俯仰距离 / 俯仰转速))。
- 到达目标后若尚未进入可见窗可原地等待；观测 [start, start+duration]
  必须完整落在可见窗 [window_start, window_end] 内（在窗口内结束）。
- 目标之间可以先转向再等待，等待不占用任何资源，因此"上一目标结束后
  立即转向"不会劣于任何延迟转向的方案。

电缆包络模式（可选，``ObservatoryConfig.cable_envelope``）
-------------------------------------------------------
连续跟踪时电缆缠绕使方位轴存在整数软限位区间 [lower, upper]，机械位置用
"展开方位"（整数度，可超出 0..359）表示。值班员给出与初始方位同余
（mod 360 相等）的展开方位 reference_azimuth 作为电缆零位；目标仍用
0..359 方位表示。每次到达目标时必须选择区间内与目标方位同余的展开位置
（圈位），转向按展开坐标的有符号差进行且不得越界（起终点都在凸区间内，
直线转向必然不越界）。同一物理方位可对应相差整圈的多个机械位置，
圈位选择与目标顺序必须联合全局求解，不能沿用圆周最短转向贪心。

目标（按字典序依次优化，不用贪心，全局求解）
------------------------------------------
1. 所有必观目标必须入选（硬约束，无可行序列时整体报告 infeasible）；
2. 最大化总优先级；
3. 最大化入选目标数；
4. 最小化结束时刻（最后一次观测的结束秒）；
5. 以观测顺序的编号序列字典序决胜；
6. 电缆包络模式下，同一编号序列再取展开方位序列字典序最小。

算法
----
n <= 16，使用子集动态规划。把"目标 × 圈内可选展开位置"笛卡尔展开为
姿态状态（旧模式每目标恰一个位置）：
- 前向 DP：f[mask][s] = 观测恰好为 mask 且停在状态 s 的最早结束时刻；
- 按 (-总优先级, -目标数, 结束时刻) 选出最优子集集合；
- 反向 DP（最晚开始时刻表 LS）支撑逐位贪心重建，得到字典序最小的编号
  序列；电缆模式下同一步再取展开方位最小的圈位。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

INF = 1 << 60  # 远大于任何合法时刻（<= 86400）的"无穷大"
NEG_INF = -(1 << 60)

# 软限位区间的最大跨度（度）：约 4 整圈，同时限制展开状态数与求解规模。
MAX_ENVELOPE_SPAN = 1440


@dataclass(frozen=True)
class CableEnvelope:
    """电缆包络：展开方位的软限位区间与电缆零位。

    - reference_azimuth: 与初始方位同余（mod 360 相等）的展开方位；
    - lower_limit / upper_limit: 整数软限位区间（含端点），单位度。
    """

    reference_azimuth: int
    lower_limit: int
    upper_limit: int


@dataclass(frozen=True)
class Target:
    id: str
    azimuth: int  # 方位角，整数，0..359
    elevation: int  # 俯仰角，整数，0..90
    duration: int  # 观测持续秒数，正整数
    window_start: int  # 可见窗起点（当天秒）
    window_end: int  # 可见窗终点（当天秒），观测必须在此刻前结束
    priority: int  # 正整数优先级
    must_observe: bool  # 必观标记


@dataclass(frozen=True)
class ObservatoryConfig:
    initial_time: int  # 初始时刻（当天秒）
    initial_azimuth: int  # 初始方位角
    initial_elevation: int  # 初始俯仰角
    azimuth_speed: float  # 方位转速（度/秒），正数
    elevation_speed: float  # 俯仰转速（度/秒），正数
    cable_envelope: CableEnvelope | None = None  # 启用电缆包络模式时给出


@dataclass(frozen=True)
class SlewStep:
    azimuth_seconds: int  # 方位轴所需秒数（向上取整）
    elevation_seconds: int  # 俯仰轴所需秒数（向上取整）
    total_seconds: int  # 两轴同时运行，取最大值


@dataclass(frozen=True)
class Observation:
    target_id: str
    slew: SlewStep  # 从上一姿态转向本目标的耗时分解
    arrival_time: int  # 转向完成、可以开始等待的时刻
    wait_seconds: int  # 等待可见窗开启的秒数
    start: int  # 观测开始时刻
    end: int  # 观测结束时刻（<= window_end）
    # 以下三项仅电缆包络模式给出：起止展开方位与顺逆方向；旧模式为 None。
    azimuth_start: int | None = None
    azimuth_end: int | None = None
    direction: str | None = None  # "cw"（展开方位增大）| "ccw"（减小）| "none"


@dataclass(frozen=True)
class ScheduleResult:
    status: str  # "ok" | "infeasible"
    message: str | None
    observations: tuple[Observation, ...]
    unscheduled: tuple[str, ...]  # 未选目标编号
    total_priority: int
    target_count: int
    end_time: int | None  # 最后一次观测结束时刻；空序列时为初始时刻


def _ceil_div(distance: float, speed: float) -> int:
    """ceil(distance / speed)，对浮点误差做防护。"""
    return math.ceil(round(distance / speed, 9))


def _axis_seconds(
    cfg: ObservatoryConfig,
    from_azimuth: int,
    from_elevation: int,
    to_azimuth: int,
    to_elevation: int,
) -> tuple[int, int]:
    """两轴各自所需秒数：方位按圆周最短距离，俯仰按绝对差（旧模式语义）。"""
    azimuth_distance = abs(from_azimuth - to_azimuth) % 360
    azimuth_distance = min(azimuth_distance, 360 - azimuth_distance)
    elevation_distance = abs(from_elevation - to_elevation)
    return (
        _ceil_div(azimuth_distance, cfg.azimuth_speed),
        _ceil_div(elevation_distance, cfg.elevation_speed),
    )


def _direction(delta: int) -> str:
    """展开方位有符号转角对应的方向标签。"""
    if delta > 0:
        return "cw"
    if delta < 0:
        return "ccw"
    return "none"


def ring_positions(azimuth: int, env: CableEnvelope) -> list[int]:
    """区间 [lower, upper] 内与 azimuth（0..359）同余的全部展开位置，升序。"""
    k0 = math.ceil((env.lower_limit - azimuth) / 360)
    k1 = math.floor((env.upper_limit - azimuth) / 360)
    return [azimuth + 360 * k for k in range(k0, k1 + 1)]


def _validate_envelope(cfg: ObservatoryConfig, env: CableEnvelope) -> None:
    """对直接调用求解器（绕过 API 校验）的包络参数做不变量检查。"""
    if env.lower_limit > env.upper_limit:
        raise ValueError("电缆包络 lower_limit 不得大于 upper_limit")
    if env.upper_limit - env.lower_limit > MAX_ENVELOPE_SPAN:
        raise ValueError(f"电缆包络区间跨度不得超过 {MAX_ENVELOPE_SPAN} 度")
    if not (env.lower_limit <= env.reference_azimuth <= env.upper_limit):
        raise ValueError("电缆零位 reference_azimuth 必须落在软限位区间内")
    if (env.reference_azimuth - cfg.initial_azimuth) % 360 != 0:
        raise ValueError("电缆零位 reference_azimuth 必须与初始方位同余（mod 360 相等）")


def solve(cfg: ObservatoryConfig, targets: list[Target]) -> ScheduleResult:
    """全局精确求解。目标数 2..16 时可在秒级内完成。"""
    env = cfg.cable_envelope
    if env is not None:
        _validate_envelope(cfg, env)

    n = len(targets)
    ids = [t.id for t in targets]
    ws = [t.window_start for t in targets]
    we = [t.window_end for t in targets]
    dur = [t.duration for t in targets]
    prio = [t.priority for t in targets]
    els = [t.elevation for t in targets]
    bit_of = [1 << i for i in range(n)]

    # ---- 姿态状态展开：状态 = (目标, 圈内展开位置)；末位为初始姿态 ----
    # 旧模式每目标仅一个位置（0..359 的圆周方位），方位按圆周最短距离；
    # 电缆模式列出区间内全部同余展开位置，方位按展开坐标有符号直线移动。
    rings: list[list[int]] = []
    for t in targets:
        if env is None:
            rings.append([t.azimuth])
        else:
            rings.append(ring_positions(t.azimuth, env))

    state_target: list[int] = []
    state_az: list[int] = []
    state_el: list[int] = []
    states_of: list[list[int]] = []
    for i in range(n):
        row_states: list[int] = []
        for az in rings[i]:
            row_states.append(len(state_target))
            state_target.append(i)
            state_az.append(az)
            state_el.append(els[i])
        states_of.append(row_states)

    init_az = env.reference_azimuth if env is not None else cfg.initial_azimuth
    init_s = len(state_target)
    state_az.append(init_az)
    state_el.append(cfg.initial_elevation)
    n_states = init_s + 1

    def slew_seconds(src: int, dst: int) -> int:
        if env is None:
            d = abs(state_az[src] - state_az[dst]) % 360
            d = min(d, 360 - d)
        else:
            d = abs(state_az[src] - state_az[dst])
        az_sec = _ceil_div(d, cfg.azimuth_speed)
        el_sec = _ceil_div(abs(state_el[src] - state_el[dst]), cfg.elevation_speed)
        return max(az_sec, el_sec)

    # edge[src][dst]：状态间转向秒数。
    edge = [[0] * n_states for _ in range(n_states)]
    for src in range(n_states):
        for dst in range(n_states):
            if src != dst:
                edge[src][dst] = slew_seconds(src, dst)

    # 按 (源状态, 目标编号) 预算的目的状态/耗时，收紧 DP 内层循环。
    edges_to: list[list[list[tuple[int, int]]]] = [
        [[] for _ in range(n)] for _ in range(n_states)
    ]
    for src in range(n_states):
        for c in range(n):
            edges_to[src][c] = [(dst, edge[src][dst]) for dst in states_of[c]]

    size = 1 << n

    # ---- 前向 DP：f[mask][s] = 观测集合恰为 mask、停在状态 s 的最早结束时刻 ----
    f: list[list[int]] = [[INF] * n_states for _ in range(size)]
    for mask in range(1, size):
        row = f[mask]
        m = mask
        while m:
            lb = m & -m
            last = lb.bit_length() - 1
            m ^= lb
            prev = mask ^ lb
            wsl = ws[last]
            durl = dur[last]
            wel = we[last]
            if prev == 0:
                for dst, sec in edges_to[init_s][last]:
                    end = cfg.initial_time + sec
                    if end < wsl:
                        end = wsl
                    end += durl
                    if end <= wel and end < row[dst]:
                        row[dst] = end
            else:
                prow = f[prev]
                # 上一观测 p 可以是 prev 中任一目标的任一展开状态。
                q = prev
                while q:
                    qb = q & -q
                    p = qb.bit_length() - 1
                    q ^= qb
                    for src in states_of[p]:
                        base = prow[src]
                        if base == INF:
                            continue
                        for dst, sec in edges_to[src][last]:
                            end = base + sec
                            if end < wsl:
                                end = wsl
                            end += durl
                            if end <= wel and end < row[dst]:
                                row[dst] = end

    must_mask = 0
    for i, t in enumerate(targets):
        if t.must_observe:
            must_mask |= bit_of[i]

    # 每个子集的总优先级与目标数。
    prio_sum = [0] * size
    popcount = [0] * size
    for mask in range(1, size):
        lb = mask & -mask
        i = lb.bit_length() - 1
        prev = mask ^ lb
        prio_sum[mask] = prio_sum[prev] + prio[i]
        popcount[mask] = popcount[prev] + 1

    # ---- 子集选择：最大化总优先级、目标数，再最小化结束时刻 ----
    best_key: tuple[int, int, int] | None = None
    tied: list[int] = []
    for mask in range(size):
        if mask & must_mask != must_mask:
            continue
        if mask == 0:
            end = cfg.initial_time
        else:
            end = min(f[mask])
            if end >= INF:
                continue
        key = (-prio_sum[mask], -popcount[mask], end)
        if best_key is None or key < best_key:
            best_key = key
            tied = [mask]
        elif key == best_key:
            tied.append(mask)

    if best_key is None:
        return _infeasible_result(env, ids, rings, targets)

    deadline = best_key[2]

    def latest_start_table(mask_star: int) -> dict[int, list[int]]:
        """LS[sub][s] = 在状态 s、时刻 t 出发仍能以 sub 中全部目标（圈位任选）
        在 deadline 前完成的最晚时刻 t；不可行为 NEG_INF。"""
        table: dict[int, list[int]] = {0: [deadline] * n_states}
        submasks = [0]
        s = mask_star
        while s:
            submasks.append(s)
            s = (s - 1) & mask_star
        submasks.sort()
        for sub in submasks:
            if sub == 0:
                continue
            row = [NEG_INF] * n_states
            q = sub
            while q:
                qb = q & -q
                c = qb.bit_length() - 1
                q ^= qb
                rem = sub ^ qb
                rem_row = table[rem]
                durl = dur[c]
                # 先在目标 c 的某个圈位观测：end_c <= min(we[c], LS[rem][该圈位])
                bounds = [
                    (dst, min(we[c], rem_row[dst]) - durl) for dst in states_of[c]
                ]
                valid = [(dst, b) for dst, b in bounds if b >= ws[c]]
                if not valid:
                    continue
                for src in range(n_states):
                    best = NEG_INF
                    for dst, b in valid:
                        v = b - edge[src][dst]
                        if v > best:
                            best = v
                    if best > row[src]:
                        row[src] = best
            table[sub] = row
        return table

    def reconstruct(mask_star: int) -> tuple[list[int], list[int]]:
        """编号序列字典序最小（同编号下展开方位序列字典序最小）的可行重建；
        返回 (编号下标序列, 每步圈位下标序列)。"""
        table = latest_start_table(mask_star)
        seq: list[int] = []
        ring_seq: list[int] = []
        t_now = cfg.initial_time
        src = init_s
        rem = mask_star
        while rem:
            cand: list[int] = []
            q = rem
            while q:
                qb = q & -q
                cand.append(qb.bit_length() - 1)
                q ^= qb
            cand.sort(key=lambda i: ids[i])
            for c in cand:
                chosen_ring: int | None = None
                chosen_end = INF
                chosen_dst = -1
                # 圈位按展开方位升序枚举，第一个可行者即为该编号下最小圈位。
                for k, dst in enumerate(states_of[c]):
                    sec = edge[src][dst]
                    arrival = t_now + sec
                    start = arrival if arrival > ws[c] else ws[c]
                    end = start + dur[c]
                    if end > we[c]:
                        continue
                    nxt = rem ^ bit_of[c]
                    if end <= table[nxt][dst]:
                        chosen_ring = k
                        chosen_end = end
                        chosen_dst = dst
                        break
                if chosen_ring is not None:
                    seq.append(c)
                    ring_seq.append(chosen_ring)
                    t_now = chosen_end
                    src = chosen_dst
                    rem ^= bit_of[c]
                    break
            else:  # pragma: no cover - 理论不变式保证不会发生
                raise RuntimeError("无法重建最优序列")
        return seq, ring_seq

    # ---- 字典序决胜：并列子集中取 (编号序列, 展开方位序列) 最小者 ----
    best_seq_ids: tuple[str, ...] | None = None
    best_az_key: tuple[int, ...] = ()
    best_seq_idx: list[int] = []
    best_ring_idx: list[int] = []
    best_mask = 0
    for mask in tied:
        seq_idx, ring_idx = reconstruct(mask)
        seq_ids = tuple(ids[i] for i in seq_idx)
        if env is not None:
            az_key = tuple(rings[i][k] for i, k in zip(seq_idx, ring_idx))
        else:
            az_key = ()
        if (
            best_seq_ids is None
            or seq_ids < best_seq_ids
            or (seq_ids == best_seq_ids and az_key < best_az_key)
        ):
            best_seq_ids = seq_ids
            best_az_key = az_key
            best_seq_idx = seq_idx
            best_ring_idx = ring_idx
            best_mask = mask

    # ---- 还原完整观测计划（含转向分解与等待） ----
    observations: list[Observation] = []
    t_now = cfg.initial_time
    src = init_s
    for idx, ring_k in zip(best_seq_idx, best_ring_idx):
        dst = states_of[idx][ring_k]
        if env is None:
            az_sec, el_sec = _axis_seconds(
                cfg, state_az[src], state_el[src], state_az[dst], state_el[dst]
            )
            direction = None
            az_start = None
            az_end = None
        else:
            delta = state_az[dst] - state_az[src]
            az_sec = _ceil_div(abs(delta), cfg.azimuth_speed)
            el_sec = _ceil_div(
                abs(state_el[src] - state_el[dst]), cfg.elevation_speed
            )
            direction = _direction(delta)
            az_start = state_az[src]
            az_end = state_az[dst]
        total_slew = max(az_sec, el_sec)
        arrival = t_now + total_slew
        start = arrival if arrival > ws[idx] else ws[idx]
        wait = start - arrival
        end = start + dur[idx]
        observations.append(
            Observation(
                target_id=ids[idx],
                slew=SlewStep(az_sec, el_sec, total_slew),
                arrival_time=arrival,
                wait_seconds=wait,
                start=start,
                end=end,
                azimuth_start=az_start,
                azimuth_end=az_end,
                direction=direction,
            )
        )
        t_now = end
        src = dst

    scheduled = set(best_seq_idx)
    unscheduled = tuple(ids[i] for i in range(n) if i not in scheduled)
    return ScheduleResult(
        status="ok",
        message=None,
        observations=tuple(observations),
        unscheduled=unscheduled,
        total_priority=prio_sum[best_mask],
        target_count=len(best_seq_idx),
        end_time=t_now,
    )


def _infeasible_result(
    env: CableEnvelope | None,
    ids: list[str],
    rings: list[list[int]],
    targets: list[Target],
) -> ScheduleResult:
    """构造不可行结果；电缆模式下区分"区间内无同余位置"等具体原因。"""
    message = "必观目标无法全部纳入任何可行序列"
    if env is not None:
        no_position = [
            ids[i]
            for i in range(len(targets))
            if targets[i].must_observe and not rings[i]
        ]
        if no_position:
            message = (
                "必观目标 "
                + "、".join(no_position)
                + f" 在软限位区间 [{env.lower_limit}, {env.upper_limit}] 内"
                "不存在与目标方位同余的展开位置"
            )
        else:
            message = (
                "必观目标不存在共同可行的顺序与圈位组合"
                f"（软限位区间 [{env.lower_limit}, {env.upper_limit}]、"
                "可见窗与转向速度共同约束）"
            )
    return ScheduleResult(
        status="infeasible",
        message=message,
        observations=(),
        unscheduled=tuple(ids),
        total_priority=0,
        target_count=0,
        end_time=None,
    )
