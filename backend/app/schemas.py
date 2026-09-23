"""API 请求/响应模型。所有校验错误都带有可定位的 loc 信息。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SECONDS_PER_DAY = 86400

# 软限位区间最大宽度（度）：宽度内每个目标至多展开出 3 个同余圈位，
# 保证 n <= 16 时圈位×顺序联合 DP 仍可在秒级完成。
MAX_ENVELOPE_WIDTH = 720
# 展开方位的绝对量防护（实际取值由区间宽度与同余关系约束）。
MAX_ENVELOPE_ABS = 100_000


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
    """可选的电缆包络：展开方位与整数软限位区间。

    启用后目标仍以 0..359 方位表示，每次到达选择区间内与目标方位
    同余的展开位置，转向按展开方位的绝对差计量且不得越过软限位。
    """

    initial_azimuth_unwrapped: int = Field(
        ge=-MAX_ENVELOPE_ABS,
        le=MAX_ENVELOPE_ABS,
        description="与初始方位同余（mod 360）的展开初始方位",
    )
    min_azimuth: int = Field(
        ge=-MAX_ENVELOPE_ABS, le=MAX_ENVELOPE_ABS, description="软限位下界（含，整数度）"
    )
    max_azimuth: int = Field(
        ge=-MAX_ENVELOPE_ABS, le=MAX_ENVELOPE_ABS, description="软限位上界（含，整数度）"
    )

    @model_validator(mode="after")
    def _check_interval(self) -> "CableEnvelopeIn":
        if self.min_azimuth > self.max_azimuth:
            raise ValueError("min_azimuth 不能大于 max_azimuth")
        if self.max_azimuth - self.min_azimuth > MAX_ENVELOPE_WIDTH:
            raise ValueError(
                f"软限位区间宽度不能超过 {MAX_ENVELOPE_WIDTH} 度（{MAX_ENVELOPE_WIDTH // 360} 整圈）"
            )
        if not (self.min_azimuth <= self.initial_azimuth_unwrapped <= self.max_azimuth):
            raise ValueError("展开初始方位必须落在软限位区间内")
        return self


class ScheduleRequest(BaseModel):
    """排程请求：初始时刻与姿态、两轴转速、2 至 16 个目标。"""

    initial_time: int = Field(ge=0, le=SECONDS_PER_DAY - 1, description="初始时刻（当天秒）")
    initial_azimuth: int = Field(ge=0, le=359, description="初始方位角")
    initial_elevation: int = Field(ge=0, le=90, description="初始俯仰角")
    azimuth_speed: float = Field(gt=0, le=360, description="方位转速（度/秒）")
    elevation_speed: float = Field(gt=0, le=360, description="俯仰转速（度/秒）")
    targets: list[TargetIn] = Field(min_length=2, max_length=16, description="2 至 16 个目标")
    cable_envelope: CableEnvelopeIn | None = Field(
        default=None, description="电缆包络；省略或为 null 时沿用圆周最短转向旧语义"
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
    def _check_envelope_congruence(
        cls, v: "CableEnvelopeIn | None", info
    ) -> "CableEnvelopeIn | None":
        if v is not None:
            initial_azimuth = info.data.get("initial_azimuth")
            if (
                initial_azimuth is not None
                and v.initial_azimuth_unwrapped % 360 != initial_azimuth % 360
            ):
                raise ValueError(
                    "initial_azimuth_unwrapped 必须与 initial_azimuth 同余（相差 360 的整数倍）"
                )
        return v


class SlewOut(BaseModel):
    azimuth_seconds: int
    elevation_seconds: int
    total_seconds: int


class EnvelopeSlewOut(SlewOut):
    """电缆包络模式的转向分解：额外给出起止展开方位和顺逆方向。"""

    from_azimuth: int
    to_azimuth: int
    azimuth_direction: Literal["cw", "ccw"]  # cw=展开方位增大（顺转）


class ObservationOut(BaseModel):
    target_id: str
    slew: SlewOut
    arrival_time: int
    wait_seconds: int
    start: int
    end: int


class EnvelopeObservationOut(BaseModel):
    target_id: str
    slew: EnvelopeSlewOut
    arrival_time: int
    wait_seconds: int
    start: int
    end: int
    arrival_azimuth: int  # 到达时的展开方位


class ScheduleResponse(BaseModel):
    # 旧模式只含 ObservationOut；电缆包络模式为 EnvelopeObservationOut。
    # 旧响应的 JSON 形状因此保持逐字段不变。
    status: Literal["ok", "infeasible"]
    message: str | None
    observations: list[ObservationOut | EnvelopeObservationOut]
    unscheduled: list[str]
    total_priority: int
    target_count: int
    end_time: int | None


class HealthResponse(BaseModel):
    status: str
    service: str
