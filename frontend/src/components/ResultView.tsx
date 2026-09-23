import { formatHMS } from "../time"
import type { ScheduleResponse, TargetInput } from "../types"
import { Timeline } from "./Timeline"

interface ResultViewProps {
  result: ScheduleResponse
  targets: TargetInput[]
  initialTime: number
}

export function ResultView({ result, targets, initialTime }: ResultViewProps) {
  if (result.status === "infeasible") {
    return (
      <section className="card result-card infeasible">
        <h2>无可行必观序列</h2>
        <p>{result.message ?? "必观目标无法全部纳入任何可行序列。"}</p>
        <p className="hint">请放宽可见窗、缩短持续时长、调整初始条件或提高转速后重试。</p>
      </section>
    )
  }

  const windows = Object.fromEntries(
    targets.map((t) => [t.id, { start: t.window_start, end: t.window_end }]),
  )

  return (
    <section className="card result-card">
      <h2>排程结果</h2>

      <div className="stats">
        <div>
          <span>总优先级</span>
          <strong>{result.total_priority}</strong>
        </div>
        <div>
          <span>入选目标</span>
          <strong>{result.target_count}</strong>
        </div>
        <div>
          <span>结束时刻</span>
          <strong>
            {result.end_time !== null ? `${result.end_time}（${formatHMS(result.end_time)}）` : "—"}
          </strong>
        </div>
        <div>
          <span>未选目标</span>
          <strong>{result.unscheduled.length}</strong>
        </div>
      </div>

      {result.observations.length === 0 ? (
        <p className="hint">当前条件下没有任何目标可以排入。</p>
      ) : (
        <>
          {result.end_time !== null && (
            <>
              <Timeline
                observations={result.observations}
                initialTime={initialTime}
                endTime={result.end_time}
                windows={windows}
              />
              <div className="legend">
                <span><i className="swatch slew" />转向</span>
                <span><i className="swatch wait" />等待</span>
                <span><i className="swatch observe" />观测</span>
                <span><i className="swatch window" />可见窗</span>
              </div>
            </>
          )}

          <table className="result-table">
            <thead>
              <tr>
                <th>#</th>
                <th>编号</th>
                <th>转向（方位/俯仰/合计）</th>
                <th>到达</th>
                <th>等待</th>
                <th>开始</th>
                <th>结束</th>
              </tr>
            </thead>
            <tbody>
              {result.observations.map((o, i) => (
                <tr key={o.target_id}>
                  <td>{i + 1}</td>
                  <td>{o.target_id}</td>
                  <td>
                    {o.slew.azimuth_seconds}s / {o.slew.elevation_seconds}s /{" "}
                    <strong>{o.slew.total_seconds}s</strong>
                  </td>
                  <td>
                    {o.arrival_time}
                    <span className="hms">{formatHMS(o.arrival_time)}</span>
                  </td>
                  <td>{o.wait_seconds}s</td>
                  <td>
                    {o.start}
                    <span className="hms">{formatHMS(o.start)}</span>
                  </td>
                  <td>
                    {o.end}
                    <span className="hms">{formatHMS(o.end)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {result.unscheduled.length > 0 && (
        <div className="unscheduled">
          <h3>未选目标</h3>
          <div className="chips">
            {result.unscheduled.map((id) => (
              <span key={id} className="chip">
                {id}
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
