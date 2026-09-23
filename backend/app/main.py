"""FastAPI 入口：提供排程 API、健康检查，并托管前端静态文件（合并部署）。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .scheduler import CableEnvelope, ObservatoryConfig, Target, solve
from .schemas import (
    EnvelopeObservationOut,
    EnvelopeSlewOut,
    HealthResponse,
    ObservationOut,
    ScheduleRequest,
    ScheduleResponse,
    SlewOut,
)

app = FastAPI(
    title="夜间射电观测排程台",
    version="1.1.0",
    description=(
        "按优先级全局求解夜间射电观测序列：必观约束、总优先级、目标数、结束时刻、"
        "编号字典序；可选电缆包络模式联合求解圈位与顺序。"
    ),
)


@app.get("/api/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="radio-scheduler")


@app.post("/api/schedule", response_model=ScheduleResponse, tags=["schedule"])
def schedule(req: ScheduleRequest) -> ScheduleResponse:
    envelope = None
    if req.cable_envelope is not None:
        envelope = CableEnvelope(
            initial_azimuth=req.cable_envelope.initial_azimuth_unwrapped,
            min_azimuth=req.cable_envelope.min_azimuth,
            max_azimuth=req.cable_envelope.max_azimuth,
        )
    cfg = ObservatoryConfig(
        initial_time=req.initial_time,
        initial_azimuth=req.initial_azimuth,
        initial_elevation=req.initial_elevation,
        azimuth_speed=req.azimuth_speed,
        elevation_speed=req.elevation_speed,
        envelope=envelope,
    )
    targets = [
        Target(
            id=t.id,
            azimuth=t.azimuth,
            elevation=t.elevation,
            duration=t.duration,
            window_start=t.window_start,
            window_end=t.window_end,
            priority=t.priority,
            must_observe=t.must_observe,
        )
        for t in req.targets
    ]
    result = solve(cfg, targets)
    if envelope is not None:
        observations_out: list[ObservationOut | EnvelopeObservationOut] = [
            EnvelopeObservationOut(
                target_id=o.target_id,
                slew=EnvelopeSlewOut(
                    azimuth_seconds=o.slew.azimuth_seconds,
                    elevation_seconds=o.slew.elevation_seconds,
                    total_seconds=o.slew.total_seconds,
                    from_azimuth=o.slew.from_azimuth,  # type: ignore[arg-type]
                    to_azimuth=o.slew.to_azimuth,  # type: ignore[arg-type]
                    azimuth_direction=o.slew.direction,  # type: ignore[arg-type]
                ),
                arrival_time=o.arrival_time,
                wait_seconds=o.wait_seconds,
                start=o.start,
                end=o.end,
                arrival_azimuth=o.arrival_azimuth,  # type: ignore[arg-type]
            )
            for o in result.observations
        ]
    else:
        observations_out = [
            ObservationOut(
                target_id=o.target_id,
                slew=SlewOut(
                    azimuth_seconds=o.slew.azimuth_seconds,
                    elevation_seconds=o.slew.elevation_seconds,
                    total_seconds=o.slew.total_seconds,
                ),
                arrival_time=o.arrival_time,
                wait_seconds=o.wait_seconds,
                start=o.start,
                end=o.end,
            )
            for o in result.observations
        ]
    return ScheduleResponse(
        status=result.status,  # type: ignore[arg-type]
        message=result.message,
        observations=observations_out,
        unscheduled=list(result.unscheduled),
        total_priority=result.total_priority,
        target_count=result.target_count,
        end_time=result.end_time,
    )


# 合并部署：后端直接托管前端构建产物（frontend/dist）。
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
