"""API 请求/响应模型。所有校验错误都带有可定位的 loc 信息。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

SECONDS_PER_DAY = 86400

# 展开方位软限位区间的允许跨度（度），与求解器 MAX_ENVELOPE_SPAN 保持一致。
MAX_ENVELOPE_SPAN = 1440


class TargetIn(BaseModel):
    """单个观测目标。"""

    id: str = Field(min_length=1, max_length=64, description="唯一编号")
    azimuth: int = Field(ge=0, le=359, description="方位角，整数度")
    elevation: int = Field(ge=0, le=90, description="俯仰角，整数度")
    duration: int = Field(ge=1, le=SECONDS_PER_DAY, description="观测持续秒数")
    window_start: int = Field(ge=0, le=SECONDS_PER_DAY - 1, description="可见窗起点（当天秒）")
    window_end: int = Field(ge=1, le=SECONDS_PER_DAY, description="可见窗终点（当天秒）")
    priority: int = Field(ge=1, description="正整数优先级")
    must_observe: bool = Field(default=False, description="必观标记")

    @field_validator("window_end")
    @classmethod
    def _window_end_after_start(cls, v: int, info) -> int:
        start = info.data.get("window_start")
        if start is not None and v <= start:
            raise ValueError("window_end 必须大于 window_start")
        return v


class CableEnvelopeIn(BaseModel):
    """电缆包络模式：展开方位的软限位区间与电缆零位。

    目标方位仍用 0..359 表示；后端为每个目标枚举区间内与其方位同余的
    展开位置（圈位），并将圈位选择与观测顺序联合全局求解。

    字段按 lower_limit、upper_limit、reference_azimuth 的顺序声明，
    以便跨字段校验错误能定位到具体字段（与 window_end 的处理一致）。
    """

    lower_limit: int = Field(description="软限位区间下界（含端点），整数度")
    upper_limit: int = Field(description="软限位区间上界（含端点），整数度")
    reference_azimuth: int = Field(
        description="与初始方位同余（mod 360 相等）的展开方位（电缆零位），整数度"
    )

    @field_validator("upper_limit")
    @classmethod
    def _upper_limit_checks(cls, v: int, info) -> int:
        lower = info.data.get("lower_limit")
        if lower is not None and v < lower:
            raise ValueError("upper_limit 不得小于 lower_limit")
        if lower is not None and v - lower > MAX_ENVELOPE_SPAN:
            raise ValueError(f"软限位区间跨度不得超过 {MAX_ENVELOPE_SPAN} 度")
        return v

    @field_validator("reference_azimuth")
    @classmethod
    def _reference_inside_interval(cls, v: int, info) -> int:
        lower = info.data.get("lower_limit")
        upper = info.data.get("upper_limit")
        if lower is not None and upper is not None and not (lower <= v <= upper):
            raise ValueError("reference_azimuth 必须落在 [lower_limit, upper_limit] 内")
        return v


class ScheduleRequest(BaseModel):
    """排程请求：初始时刻与姿态、两轴转速、2 至 16 个目标。"""

    initial_time: int = Field(ge=0, le=SECONDS_PER_DAY - 1, description="初始时刻（当天秒）")
    initial_azimuth: int = Field(ge=0, le=359, description="初始方位角")
    initial_elevation: int = Field(ge=0, le=90, description="初始俯仰角")
    azimuth_speed: float = Field(gt=0, le=360, description="方位转速（度/秒）")
    elevation_speed: float = Field(gt=0, le=360, description="俯仰转速（度/秒）")
    targets: list[TargetIn] = Field(min_length=2, max_length=16, description="2 至 16 个目标")
    cable_envelope: CableEnvelopeIn | None = Field(
        default=None,
        description="可选电缆包络；缺省或为 null 时沿用圆周最短转向的旧语义",
    )

    @field_validator("targets")
    @classmethod
    def _unique_target_ids(cls, v: list[TargetIn]) -> list[TargetIn]:
        seen: dict[str, int] = {}
        for i, t in enumerate(v):
            if t.id in seen:
                raise ValueError(
                    f"目标编号重复: '{t.id}'（第 {seen[t.id]} 与第 {i} 个目标）"
                )
            seen[t.id] = i
        return v

    @field_validator("cable_envelope")
    @classmethod
    def _check_reference_congruent(cls, v: "CableEnvelopeIn | None", info) -> "CableEnvelopeIn | None":
        if v is not None:
            initial_azimuth = info.data.get("initial_azimuth")
            if (
                initial_azimuth is not None
                and (v.reference_azimuth - initial_azimuth) % 360 != 0
            ):
                raise ValueError(
                    "cable_envelope.reference_azimuth 必须与 initial_azimuth 同余"
                    "（二者之差须为 360 的整数倍）"
                )
        return v


class SlewOut(BaseModel):
    azimuth_seconds: int
    elevation_seconds: int
    total_seconds: int


class ObservationOut(BaseModel):
    target_id: str
    slew: SlewOut
    arrival_time: int
    wait_seconds: int
    start: int
    end: int
    # 仅电缆包络模式给出：起止展开方位与顺逆方向；旧模式为 null。
    azimuth_start: int | None = None
    azimuth_end: int | None = None
    direction: Literal["cw", "ccw", "none"] | None = None


class CableEnvelopeOut(BaseModel):
    reference_azimuth: int
    lower_limit: int
    upper_limit: int


class ScheduleResponse(BaseModel):
    status: Literal["ok", "infeasible"]
    message: str | None
    observations: list[ObservationOut]
    unscheduled: list[str]
    total_priority: int
    target_count: int
    end_time: int | None
    # 回显实际生效的电缆包络；未启用时为 null。
    cable_envelope: CableEnvelopeOut | None = None


class HealthResponse(BaseModel):
    status: str
    service: str
