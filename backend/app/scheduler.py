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

目标（按字典序依次优化，不用贪心，全局求解）
------------------------------------------
1. 所有必观目标必须入选（硬约束，无可行序列时整体报告 infeasible）；
2. 最大化总优先级；
3. 最大化入选目标数；
4. 最小化结束时刻（最后一次观测的结束秒）；
5. 以观测顺序的编号序列字典序决胜。

算法
----
n <= 16，使用子集动态规划：
- 前向 DP：f[mask][last] = 观测恰好为 mask 且最后观测 last 的最早结束时刻；
- 按 (-总优先级, -目标数, 结束时刻) 选出最优子集集合；
- 反向 DP（最晚开始时刻表 LS）支撑逐位贪心重建，得到字典序最小的编号序列。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

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
class ObservatoryConfig:
    initial_time: int  # 初始时刻（当天秒）
    initial_azimuth: int  # 初始方位角
    initial_elevation: int  # 初始俯仰角
    azimuth_speed: float  # 方位转速（度/秒），正数
    elevation_speed: float  # 俯仰转速（度/秒），正数


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
    """全局精确求解。目标数 2..16 时可在秒级内完成。"""
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
        return ScheduleResult(
            status="infeasible",
            message="必观目标无法全部纳入任何可行序列",
            observations=(),
            unscheduled=tuple(ids),
            total_priority=0,
            target_count=0,
            end_time=None,
        )

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
