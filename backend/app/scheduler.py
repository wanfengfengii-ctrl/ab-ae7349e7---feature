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

电缆包络模式（可选，``ObservatoryConfig.envelope`` 非空时启用）
--------------------------------------------------------------
连续跟踪受电缆缠绕限制：方位轴按"展开方位"（可以落在 0..359 之外，
同一物理方位对应相差 360 整数倍的多个展开位置）单向计量，并设有整数
软限位区间 [min_azimuth, max_azimuth]，转向路径不得越界。值班员给出
与初始方位同余（mod 360）的展开初始方位；目标仍以 0..359 方位表示，
每次到达须选择区间内与目标方位同余的某个展开位置。此时：

- 方位距离 = 展开位置之差的绝对值（不再取圆周最短距离）；
- 圈位选择与目标观测顺序必须**联合全局求解**；
- 决胜规则在原有"编号序列字典序"之后，再取展开方位序列字典序最小。

目标（按字典序依次优化，不用贪心，全局求解）
------------------------------------------
1. 所有必观目标必须入选（硬约束，无可行序列时整体报告 infeasible）；
2. 最大化总优先级；
3. 最大化入选目标数；
4. 最小化结束时刻（最后一次观测的结束秒）；
5. 以观测顺序的编号序列字典序决胜；
6. 电缆包络模式下，编号序列相同时再取展开方位序列字典序最小。

算法
----
n <= 16，使用子集动态规划：
- 旧模式：f[mask][last] = 观测恰好为 mask 且最后观测 last 的最早结束时刻；
- 电缆包络模式：每个目标展开为区间内全部同余位置（圈位），
  f[mask][position] 同时刻画"观测了哪些目标"与"停在哪个圈位"；
- 按 (-总优先级, -目标数, 结束时刻) 选出最优子集集合；
- 反向 DP（最晚开始时刻表 LS）支撑逐位贪心重建，得到字典序最小的
  编号序列（电缆包络模式下并列时再取展开方位最小）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

INF = 1 << 60  # 远大于任何合法时刻（<= 86400）的"无穷大"


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
class CableEnvelope:
    """电缆包络：展开方位的软限位区间。

    initial_azimuth 与请求中的初始方位相差 360 的整数倍（同余），
    且必须落在 [min_azimuth, max_azimuth] 内；所有转向路径不得越界。
    """

    initial_azimuth: int  # 展开初始方位（与 0..359 的初始方位同余）
    min_azimuth: int  # 软限位下界（含）
    max_azimuth: int  # 软限位上界（含）


@dataclass(frozen=True)
class ObservatoryConfig:
    initial_time: int  # 初始时刻（当天秒）
    initial_azimuth: int  # 初始方位角
    initial_elevation: int  # 初始俯仰角
    azimuth_speed: float  # 方位转速（度/秒），正数
    elevation_speed: float  # 俯仰转速（度/秒），正数
    envelope: CableEnvelope | None = None  # 电缆包络；None 为旧模式


@dataclass(frozen=True)
class SlewStep:
    azimuth_seconds: int  # 方位轴所需秒数（向上取整）
    elevation_seconds: int  # 俯仰轴所需秒数（向上取整）
    total_seconds: int  # 两轴同时运行，取最大值
    # 以下三项仅电缆包络模式给出；旧模式为 None：
    from_azimuth: int | None = None  # 转向起点的展开方位
    to_azimuth: int | None = None  # 转向终点的展开方位（与目标方位同余）
    direction: Literal["cw", "ccw"] | None = None  # cw=展开方位增大（顺转）


@dataclass(frozen=True)
class Observation:
    target_id: str
    slew: SlewStep  # 从上一姿态转向本目标的耗时分解
    arrival_time: int  # 转向完成、可以开始等待的时刻
    wait_seconds: int  # 等待可见窗开启的秒数
    start: int  # 观测开始时刻
    end: int  # 观测结束时刻（<= window_end）
    arrival_azimuth: int | None = None  # 到达时的展开方位（仅电缆包络模式）


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
    """两轴各自所需秒数：方位按圆周最短距离，俯仰按绝对差。"""
    azimuth_distance = abs(from_azimuth - to_azimuth) % 360
    azimuth_distance = min(azimuth_distance, 360 - azimuth_distance)
    elevation_distance = abs(from_elevation - to_elevation)
    return (
        _ceil_div(azimuth_distance, cfg.azimuth_speed),
        _ceil_div(elevation_distance, cfg.elevation_speed),
    )


def solve(cfg: ObservatoryConfig, targets: list[Target]) -> ScheduleResult:
    """全局精确求解。目标数 2..16 时可在秒级内完成。

    未启用电缆包络时走旧的圆周最短转向模型；启用后展开圈位与顺序联合求解。
    """
    if cfg.envelope is None:
        return _solve_legacy(cfg, targets)
    return _solve_envelope(cfg, targets, cfg.envelope)


def _infeasible_result(ids: list[str], message: str) -> ScheduleResult:
    return ScheduleResult(
        status="infeasible",
        message=message,
        observations=(),
        unscheduled=tuple(ids),
        total_priority=0,
        target_count=0,
        end_time=None,
    )


def _axis_seconds_unwrapped(
    cfg: ObservatoryConfig,
    azimuth_distance: int,
    from_elevation: int,
    to_elevation: int,
) -> tuple[int, int]:
    """给定展开方位路径长度（带符号，内部取绝对值）时两轴各自所需秒数。"""
    return (
        _ceil_div(abs(azimuth_distance), cfg.azimuth_speed),
        _ceil_div(abs(from_elevation - to_elevation), cfg.elevation_speed),
    )


def _solve_legacy(cfg: ObservatoryConfig, targets: list[Target]) -> ScheduleResult:
    """旧模式：方位按圆周最短距离，f[mask][last] 子集 DP。"""
    n = len(targets)
    ids = [t.id for t in targets]
    ws = [t.window_start for t in targets]
    we = [t.window_end for t in targets]
    dur = [t.duration for t in targets]
    prio = [t.priority for t in targets]
    bit_of = [1 << i for i in range(n)]

    # 姿态下标：0..n-1 为各目标，n 为初始姿态。
    az_of = [t.azimuth for t in targets] + [cfg.initial_azimuth]
    el_of = [t.elevation for t in targets] + [cfg.initial_elevation]

    # slew[src][dst]：从姿态 src 转向目标 dst 的秒数（两轴取最大）。
    slew = [[0] * n for _ in range(n + 1)]
    for src in range(n + 1):
        for dst in range(n):
            az_sec, el_sec = _axis_seconds(
                cfg, az_of[src], el_of[src], az_of[dst], el_of[dst]
            )
            slew[src][dst] = max(az_sec, el_sec)
    # slew_col[dst][src]：按目的地方便取列。
    slew_col = [[slew[src][dst] for src in range(n + 1)] for dst in range(n)]

    size = 1 << n

    # ---- 前向 DP：f[mask][last] = 观测集合恰为 mask、最后观测 last 的最早结束时刻 ----
    f: list[list[int] | None] = [None] * size
    for mask in range(1, size):
        row = [INF] * n
        m = mask
        while m:
            lb = m & -m
            last = lb.bit_length() - 1
            m ^= lb
            prev = mask ^ lb
            wsl = ws[last]
            durl = dur[last]
            wel = we[last]
            best = INF
            if prev == 0:
                # 从初始姿态直接转向第一个目标。
                end = cfg.initial_time + slew[n][last]
                if end < wsl:
                    end = wsl
                end += durl
                if end <= wel:
                    best = end
            else:
                prow = f[prev]
                col = slew_col[last]
                q = prev
                while q:
                    qb = q & -q
                    p = qb.bit_length() - 1
                    q ^= qb
                    e = prow[p]
                    if e == INF:
                        continue
                    e += col[p]
                    if e < wsl:
                        e = wsl
                    e += durl
                    if e <= wel and e < best:
                        best = e
            row[last] = best
        f[mask] = row

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
            end = min(f[mask])  # type: ignore[arg-type]
            if end >= INF:
                continue
        key = (-prio_sum[mask], -popcount[mask], end)
        if best_key is None or key < best_key:
            best_key = key
            tied = [mask]
        elif key == best_key:
            tied.append(mask)

    if best_key is None:
        # 必观目标无法全部纳入任何可行序列。
        return _infeasible_result(ids, "必观目标无法全部纳入任何可行序列")

    deadline = best_key[2]

    def latest_start_table(mask_star: int) -> dict[int, list[int]]:
        """LS[sub][src] = 在姿态 src、时刻 t 出发仍能完成 sub 全部观测
        （且不超过 deadline）的最晚时刻 t；不可行为 -INF。"""
        table: dict[int, list[int]] = {0: [deadline] * (n + 1)}
        submasks = [0]
        s = mask_star
        while s:
            submasks.append(s)
            s = (s - 1) & mask_star
        submasks.sort()
        for sub in submasks:
            if sub == 0:
                continue
            row = [-INF] * (n + 1)
            q = sub
            while q:
                qb = q & -q
                c = qb.bit_length() - 1
                q ^= qb
                rem = sub ^ qb
                # 先观测 c：end_c <= min(we[c], LS[rem][c 的姿态])
                bound = min(we[c], table[rem][c]) - dur[c]
                if bound < ws[c]:
                    continue
                col = slew_col[c]
                cand = [bound - col[src] for src in range(n + 1)]
                row = [r if r > v else v for r, v in zip(row, cand)]
            table[sub] = row
        return table

    def lex_min_sequence(mask_star: int) -> list[int]:
        """在 mask_star 内、结束时刻不超过 deadline 的字典序最小编号序列。"""
        table = latest_start_table(mask_star)
        seq: list[int] = []
        t_now = cfg.initial_time
        src = n
        rem = mask_star
        while rem:
            candidates = []
            q = rem
            while q:
                qb = q & -q
                c = qb.bit_length() - 1
                q ^= qb
                candidates.append(c)
            candidates.sort(key=lambda i: ids[i])
            for c in candidates:
                arrival = t_now + slew[src][c]
                start = arrival if arrival > ws[c] else ws[c]
                end = start + dur[c]
                if end > we[c]:
                    continue
                nxt = rem ^ bit_of[c]
                if end <= table[nxt][c]:
                    seq.append(c)
                    t_now = end
                    src = c
                    rem = nxt
                    break
            else:  # pragma: no cover - 理论不变式保证不会发生
                raise RuntimeError("无法重建最优序列")
        return seq

    # ---- 字典序决胜：在并列最优的子集中取编号序列最小者 ----
    best_seq_ids: tuple[str, ...] | None = None
    best_seq_idx: list[int] = []
    best_mask = 0
    for mask in tied:
        seq_idx = lex_min_sequence(mask)
        seq_ids = tuple(ids[i] for i in seq_idx)
        if best_seq_ids is None or seq_ids < best_seq_ids:
            best_seq_ids = seq_ids
            best_seq_idx = seq_idx
            best_mask = mask

    # ---- 还原完整观测计划（含转向分解与等待） ----
    observations: list[Observation] = []
    t_now = cfg.initial_time
    src = n
    for idx in best_seq_idx:
        az_sec, el_sec = _axis_seconds(
            cfg, az_of[src], el_of[src], az_of[idx], el_of[idx]
        )
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
            )
        )
        t_now = end
        src = idx

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


def _solve_envelope(
    cfg: ObservatoryConfig, targets: list[Target], env: CableEnvelope
) -> ScheduleResult:
    """电缆包络模式：圈位（展开位置）与目标顺序联合子集 DP。

    每个目标展开为软限位区间内全部与其方位同余的位置；DP 状态
    f[mask][p] 同时记录"已观测集合 mask"与"当前停在展开位置 p"的最早
    结束时刻，因此圈位选择是全局解的一部分，而非逐段最短转向贪心。
    """
    n = len(targets)
    ids = [t.id for t in targets]
    ws = [t.window_start for t in targets]
    we = [t.window_end for t in targets]
    dur = [t.duration for t in targets]
    prio = [t.priority for t in targets]
    bit_of = [1 << i for i in range(n)]

    # ---- 展开位置：pos_* 下标 0..P-1 为各目标的同余圈位，P 为初始位置 ----
    pos_target: list[int] = []
    pos_az: list[int] = []
    pos_el: list[int] = []
    pos_lo = [0] * n  # 每个目标在位置表中的区间 [pos_lo, pos_hi)
    pos_hi = [0] * n
    unreachable_must: list[str] = []
    for i, t in enumerate(targets):
        pos_lo[i] = len(pos_az)
        k_lo = math.ceil((env.min_azimuth - t.azimuth) / 360)
        k_hi = math.floor((env.max_azimuth - t.azimuth) / 360)
        for k in range(k_lo, k_hi + 1):
            pos_target.append(i)
            pos_az.append(t.azimuth + 360 * k)
            pos_el.append(t.elevation)
        pos_hi[i] = len(pos_az)
        if pos_lo[i] == pos_hi[i] and t.must_observe:
            unreachable_must.append(t.id)

    if unreachable_must:
        return _infeasible_result(
            ids,
            "必观目标在软限位区间内没有与目标方位同余的展开位置（电缆不可达）: "
            + ", ".join(unreachable_must),
        )

    pos_az.append(env.initial_azimuth)
    pos_el.append(cfg.initial_elevation)
    init_p = len(pos_az) - 1
    p_count = init_p + 1

    # slew_sec[src][dst]：展开方位按绝对差（恒速不越界），俯仰按绝对差。
    slew_sec: list[list[int]] = [[0] * p_count for _ in range(p_count)]
    for src in range(p_count):
        for dst in range(p_count):
            az_sec = _ceil_div(abs(pos_az[src] - pos_az[dst]), cfg.azimuth_speed)
            el_sec = _ceil_div(abs(pos_el[src] - pos_el[dst]), cfg.elevation_speed)
            slew_sec[src][dst] = max(az_sec, el_sec)
    slew_col = [[slew_sec[src][dst] for src in range(p_count)] for dst in range(p_count)]

    # 每个目标的位置列表（转移动作只在"某目标的圈位"上发生）。
    positions_of = [list(range(pos_lo[i], pos_hi[i])) for i in range(n)]

    size = 1 << n

    # ---- 前向 DP：f[mask][p] = 集合恰为 mask、停在位置 p 的最早结束时刻 ----
    f: list[list[int]] = [[INF] * p_count for _ in range(size)]
    for mask in range(1, size):
        row = f[mask]
        m = mask
        while m:
            lb = m & -m
            c = lb.bit_length() - 1
            m ^= lb
            prev = mask ^ lb
            wsl, wel, durl = ws[c], we[c], dur[c]
            for p in positions_of[c]:
                best = INF
                if prev == 0:
                    end = cfg.initial_time + slew_sec[init_p][p]
                    if end < wsl:
                        end = wsl
                    end += durl
                    if end <= wel:
                        best = end
                else:
                    prow = f[prev]
                    qmask = prev
                    while qmask:
                        qb = qmask & -qmask
                        qt = qb.bit_length() - 1
                        qmask ^= qb
                        for q in range(pos_lo[qt], pos_hi[qt]):
                            e = prow[q]
                            if e == INF:
                                continue
                            e += slew_sec[q][p]
                            if e < wsl:
                                e = wsl
                            e += durl
                            if e <= wel and e < best:
                                best = e
                row[p] = best

    must_mask = 0
    for i, t in enumerate(targets):
        if t.must_observe:
            must_mask |= bit_of[i]

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

    if best_key is None:
        return _infeasible_result(
            ids,
            "必观目标无法全部纳入任何同时满足电缆软限位与可见窗的可行序列",
        )

    deadline = best_key[2]
    total_prio0 = -best_key[0]
    count0 = -best_key[1]

    # ---- 反向最晚开始时刻表（对全部目标一次性构建，与最终子集无关）----
    # LS[sub][p] = 在位置 p、时刻 t 出发，仍能观测 sub 全部目标且不晚于
    # deadline 结束的最晚 t；不可行为 -INF。
    neg_inf = -INF
    ls: list[list[int]] = [[neg_inf] * p_count for _ in range(size)]
    ls[0] = [deadline] * p_count
    for sub in range(1, size):
        row = [neg_inf] * p_count
        sm = sub
        while sm:
            sb = sm & -sm
            c = sb.bit_length() - 1
            sm ^= sb
            rem = sub ^ sb
            lrem = ls[rem]
            durl, wsl, wel = dur[c], ws[c], we[c]
            for q in positions_of[c]:
                bound = min(wel, lrem[q]) - durl
                if bound < wsl:
                    continue
                col = slew_col[q]
                # 先到 q 观测 c、再完成 rem：从各出发位置 p 的最晚出发时刻。
                for p in range(p_count):
                    cand = bound - col[p]
                    if cand > row[p]:
                        row[p] = cand
        ls[sub] = row

    # ---- 重建第一阶段：字典序最小的编号序列（圈位无关）----
    # 维护 frontier：固定已选前缀后，{停在位置 p: 该前缀能达到的最早结束时刻}。
    # 每一步选择编号最小、且存在某个圈位 + 可补齐最优后缀的目标。
    chosen_targets: list[int] = []
    chosen_mask = 0
    frontier: dict[int, int] = {init_p: cfg.initial_time}
    for step in range(count0):
        rem_count = count0 - step - 1
        rem_prio = total_prio0 - prio_sum[chosen_mask]
        avail = (size - 1) ^ chosen_mask
        feasible_targets: list[int] = []
        am = avail
        while am:
            ab = am & -am
            c = ab.bit_length() - 1
            am ^= ab
            if prio[c] > rem_prio:
                continue
            need_prio = rem_prio - prio[c]
            rest = avail ^ bit_of[c]
            for q in positions_of[c]:
                # 任一前缀状态停在 q 能赶上窗口且后缀可补齐即可。
                found = False
                for p, t_prev in frontier.items():
                    arr = t_prev + slew_sec[p][q]
                    start = arr if arr > ws[c] else ws[c]
                    end = start + dur[c]
                    if end > we[c] or end > deadline:
                        continue
                    r = rest
                    while True:
                        if popcount[r] == rem_count and prio_sum[r] == need_prio:
                            if ls[r][q] >= end:
                                found = True
                                break
                        if r == 0:
                            break
                        r = (r - 1) & rest
                    if found:
                        break
                if found:
                    feasible_targets.append(c)
                    break
        feasible_targets.sort(key=lambda i: ids[i])
        if not feasible_targets:  # pragma: no cover - 理论不变式保证不会发生
            raise RuntimeError("无法重建最优序列（电缆包络模式）")
        c = feasible_targets[0]
        # 推进 frontier：保留所有可行圈位，每个位置只留最早结束时刻。
        need_prio = rem_prio - prio[c]
        rest = avail ^ bit_of[c]
        next_frontier: dict[int, int] = {}
        for q in positions_of[c]:
            best_end = INF
            for p, t_prev in frontier.items():
                arr = t_prev + slew_sec[p][q]
                start = arr if arr > ws[c] else ws[c]
                end = start + dur[c]
                if end > we[c] or end > deadline:
                    continue
                r = rest
                completable = False
                while True:
                    if popcount[r] == rem_count and prio_sum[r] == need_prio:
                        if ls[r][q] >= end:
                            completable = True
                            break
                    if r == 0:
                        break
                    r = (r - 1) & rest
                if completable and end < best_end:
                    best_end = end
            if best_end < INF:
                next_frontier[q] = best_end
        chosen_targets.append(c)
        chosen_mask |= bit_of[c]
        frontier = next_frontier

    # ---- 重建第二阶段：编号序列固定，取展开方位序列字典序最小 ----
    # 沿序列反向构建最晚出发时刻表 L[j][p]：在位置 p 出发仍能完成
    # 第 j..末尾观测（且 <= deadline 结束）的最晚时刻。
    k = len(chosen_targets)
    latest: list[list[int]] = [[neg_inf] * p_count for _ in range(k + 1)]
    latest[k] = [deadline] * p_count
    for j in range(k - 1, -1, -1):
        c = chosen_targets[j]
        row = [neg_inf] * p_count
        nxt = latest[j + 1]
        for q in positions_of[c]:
            bound = min(we[c], nxt[q]) - dur[c]
            if bound < ws[c]:
                continue
            col = slew_col[q]
            for p in range(p_count):
                cand = bound - col[p]
                if cand > row[p]:
                    row[p] = cand
        latest[j] = row

    chosen: list[tuple[int, int]] = []
    t_now = cfg.initial_time
    src = init_p
    for j, c in enumerate(chosen_targets):
        options = sorted(positions_of[c], key=lambda q: pos_az[q])
        for q in options:
            arr = t_now + slew_sec[src][q]
            start = arr if arr > ws[c] else ws[c]
            end = start + dur[c]
            if end <= we[c] and end <= latest[j + 1][q]:
                chosen.append((c, q))
                t_now = end
                src = q
                break
        else:  # pragma: no cover - 第一阶段已保证存在
            raise RuntimeError("无法重建展开方位序列（电缆包络模式）")

    # ---- 还原完整观测计划（含展开方位、顺逆方向与转向分解）----
    observations: list[Observation] = []
    t_now = cfg.initial_time
    src = init_p
    prev_az = env.initial_azimuth
    for c, q in chosen:
        to_az = pos_az[q]
        az_sec, el_sec = _axis_seconds_unwrapped(
            cfg, to_az - prev_az, pos_el[src], pos_el[q]
        )
        total_slew = max(az_sec, el_sec)
        arrival = t_now + total_slew
        start = arrival if arrival > ws[c] else ws[c]
        wait = start - arrival
        end = start + dur[c]
        observations.append(
            Observation(
                target_id=ids[c],
                slew=SlewStep(
                    azimuth_seconds=az_sec,
                    elevation_seconds=el_sec,
                    total_seconds=total_slew,
                    from_azimuth=prev_az,
                    to_azimuth=to_az,
                    direction="cw" if to_az >= prev_az else "ccw",
                ),
                arrival_time=arrival,
                wait_seconds=wait,
                start=start,
                end=end,
                arrival_azimuth=to_az,
            )
        )
        t_now = end
        src = q
        prev_az = to_az

    scheduled = {c for c, _ in chosen}
    unscheduled = tuple(ids[i] for i in range(n) if i not in scheduled)
    return ScheduleResult(
        status="ok",
        message=None,
        observations=tuple(observations),
        unscheduled=unscheduled,
        total_priority=total_prio0,
        target_count=count0,
        end_time=t_now,
    )
