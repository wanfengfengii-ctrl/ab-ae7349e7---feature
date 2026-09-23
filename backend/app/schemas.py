"""API 请求/响应模型。所有校验错误都带有可定位的 loc 信息。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

SECONDS_PER_DAY = 86400


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


class ScheduleRequest(BaseModel):
    """排程请求：初始时刻与姿态、两轴转速、2 至 16 个目标。"""

    initial_time: int = Field(ge=0, le=SECONDS_PER_DAY - 1, description="初始时刻（当天秒）")
    initial_azimuth: int = Field(ge=0, le=359, description="初始方位角")
    initial_elevation: int = Field(ge=0, le=90, description="初始俯仰角")
    azimuth_speed: float = Field(gt=0, le=360, description="方位转速（度/秒）")
    elevation_speed: float = Field(gt=0, le=360, description="俯仰转速（度/秒）")
    targets: list[TargetIn] = Field(min_length=2, max_length=16, description="2 至 16 个目标")

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


class ScheduleResponse(BaseModel):
    status: Literal["ok", "infeasible"]
    message: str | None
    observations: list[ObservationOut]
    unscheduled: list[str]
    total_priority: int
    target_count: int
    end_time: int | None


class HealthResponse(BaseModel):
    status: str
    service: str
