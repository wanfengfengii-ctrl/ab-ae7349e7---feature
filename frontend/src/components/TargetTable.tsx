import { formatHMS } from "../time"
import type { TargetInput } from "../types"

interface TargetTableProps {
  targets: TargetInput[]
  onChange: (targets: TargetInput[]) => void
}

const MIN_TARGETS = 2
const MAX_TARGETS = 16

function intOr(value: number, fallback: number): number {
  return Number.isNaN(value) ? fallback : Math.trunc(value)
}

export function TargetTable({ targets, onChange }: TargetTableProps) {
  function update(index: number, patch: Partial<TargetInput>) {
    onChange(targets.map((t, i) => (i === index ? { ...t, ...patch } : t)))
  }

  function remove(index: number) {
    onChange(targets.filter((_, i) => i !== index))
  }

  function add() {
    const used = new Set(targets.map((t) => t.id))
    let n = targets.length + 1
    while (used.has(`T${n}`)) n += 1
    onChange([
      ...targets,
      {
        id: `T${n}`,
        azimuth: 0,
        elevation: 45,
        duration: 300,
        window_start: 72000,
        window_end: 86400,
        priority: 5,
        must_observe: false,
      },
    ])
  }

  return (
    <div className="table-wrap">
      <table className="target-table">
        <thead>
          <tr>
            <th>编号</th>
            <th>方位角°</th>
            <th>俯仰角°</th>
            <th>持续秒</th>
            <th>窗口开始</th>
            <th>窗口结束</th>
            <th>优先级</th>
            <th>必观</th>
            <th aria-label="操作" />
          </tr>
        </thead>
        <tbody>
          {targets.map((t, i) => (
            <tr key={i}>
              <td>
                <input
                  className="id-input"
                  value={t.id}
                  onChange={(e) => update(i, { id: e.target.value })}
                />
              </td>
              <td>
                <input
                  type="number"
                  min={0}
                  max={359}
                  value={t.azimuth}
                  onChange={(e) => update(i, { azimuth: intOr(e.target.valueAsNumber, t.azimuth) })}
                />
              </td>
              <td>
                <input
                  type="number"
                  min={0}
                  max={90}
                  value={t.elevation}
                  onChange={(e) => update(i, { elevation: intOr(e.target.valueAsNumber, t.elevation) })}
                />
              </td>
              <td>
                <input
                  type="number"
                  min={1}
                  value={t.duration}
                  onChange={(e) => update(i, { duration: intOr(e.target.valueAsNumber, t.duration) })}
                />
              </td>
              <td>
                <input
                  type="number"
                  min={0}
                  max={86399}
                  title={formatHMS(t.window_start)}
                  value={t.window_start}
                  onChange={(e) => update(i, { window_start: intOr(e.target.valueAsNumber, t.window_start) })}
                />
                <span className="hms">{formatHMS(t.window_start)}</span>
              </td>
              <td>
                <input
                  type="number"
                  min={1}
                  max={86400}
                  title={formatHMS(t.window_end)}
                  value={t.window_end}
                  onChange={(e) => update(i, { window_end: intOr(e.target.valueAsNumber, t.window_end) })}
                />
                <span className="hms">{formatHMS(t.window_end)}</span>
              </td>
              <td>
                <input
                  type="number"
                  min={1}
                  value={t.priority}
                  onChange={(e) => update(i, { priority: intOr(e.target.valueAsNumber, t.priority) })}
                />
              </td>
              <td className="center">
                <input
                  type="checkbox"
                  checked={t.must_observe}
                  onChange={(e) => update(i, { must_observe: e.target.checked })}
                />
              </td>
              <td>
                <button
                  type="button"
                  className="danger"
                  disabled={targets.length <= MIN_TARGETS}
                  onClick={() => remove(i)}
                >
                  删
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="table-footer">
        <button type="button" disabled={targets.length >= MAX_TARGETS} onClick={add}>
          + 添加目标
        </button>
        <span className="hint">
          目标数 2–16；时间输入当天秒，右侧显示对应时刻（如 72000 = {formatHMS(72000)}）
        </span>
      </div>
    </div>
  )
}
