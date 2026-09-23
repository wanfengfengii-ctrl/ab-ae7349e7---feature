import { formatHMS } from "../time"
import type { Observation } from "../types"

interface TimelineProps {
  observations: Observation[]
  initialTime: number
  endTime: number
  windows: Record<string, { start: number; end: number }>
}

const WIDTH = 980
const LABEL_W = 72
const PAD_R = 12
const ROW_H = 40
const AXIS_H = 30

function niceTicks(t0: number, t1: number, maxTicks = 8): number[] {
  const span = t1 - t0
  const candidates = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800]
  const step = candidates.find((c) => span / c <= maxTicks) ?? 14400
  const ticks: number[] = []
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) ticks.push(t)
  return ticks
}

/** 时间线：每个目标一行，依次绘制可见窗、转向、等待、观测四段。 */
export function Timeline({ observations, initialTime, endTime, windows }: TimelineProps) {
  const t0 = initialTime
  const t1 = Math.max(endTime, t0 + 1)
  const chartW = WIDTH - LABEL_W - PAD_R
  const x = (t: number) => LABEL_W + ((t - t0) / (t1 - t0)) * chartW
  const height = observations.length * ROW_H + AXIS_H

  return (
    <svg viewBox={`0 0 ${WIDTH} ${height}`} className="timeline" role="img" aria-label="排程时间线">
      {observations.map((o, i) => {
        const w = windows[o.target_id]
        if (!w) return null
        return (
          <rect
            key={`w-${o.target_id}`}
            className="tl-window"
            x={x(w.start)}
            y={i * ROW_H + 4}
            width={Math.max(1, x(w.end) - x(w.start))}
            height={ROW_H - 8}
            rx={3}
          />
        )
      })}

      {observations.map((o, i) => {
        const prevEnd = i === 0 ? t0 : observations[i - 1].end
        const y = i * ROW_H + ROW_H / 2 - 6
        return (
          <g key={`s-${o.target_id}`}>
            <text className="tl-label" x={8} y={y + 10}>
              {o.target_id}
            </text>
            <rect
              className="tl-slew"
              x={x(prevEnd)}
              y={y}
              width={Math.max(0, x(o.arrival_time) - x(prevEnd))}
              height={12}
            >
              <title>{`转向 ${o.slew.total_seconds}s（方位 ${o.slew.azimuth_seconds}s / 俯仰 ${o.slew.elevation_seconds}s）`}</title>
            </rect>
            <rect
              className="tl-wait"
              x={x(o.arrival_time)}
              y={y}
              width={Math.max(0, x(o.start) - x(o.arrival_time))}
              height={12}
            >
              <title>{`等待 ${o.wait_seconds}s`}</title>
            </rect>
            <rect
              className="tl-observe"
              x={x(o.start)}
              y={y}
              width={Math.max(2, x(o.end) - x(o.start))}
              height={12}
              rx={2}
            >
              <title>{`观测 ${o.target_id}：${o.start}–${o.end}`}</title>
            </rect>
          </g>
        )
      })}

      {niceTicks(t0, t1).map((t) => (
        <g key={`tick-${t}`}>
          <line
            className="tl-tick"
            x1={x(t)}
            y1={height - AXIS_H}
            x2={x(t)}
            y2={height - AXIS_H + 5}
          />
          <text className="tl-tick-label" x={x(t)} y={height - 8} textAnchor="middle">
            {formatHMS(t)}
          </text>
        </g>
      ))}
    </svg>
  )
}
