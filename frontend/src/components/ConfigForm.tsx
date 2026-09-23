import { formatHMS } from "../time"
import type { SchedulerConfig } from "../types"

interface ConfigFormProps {
  values: SchedulerConfig
  onChange: (field: keyof SchedulerConfig, value: number) => void
}

interface FieldDef {
  key: keyof SchedulerConfig
  label: string
  min: number
  max: number
  step?: number
  hint?: (v: number) => string
}

const FIELDS: FieldDef[] = [
  { key: "initial_time", label: "初始时刻（当天秒）", min: 0, max: 86399, hint: (v) => formatHMS(v) },
  { key: "initial_azimuth", label: "初始方位角（°）", min: 0, max: 359 },
  { key: "initial_elevation", label: "初始俯仰角（°）", min: 0, max: 90 },
  { key: "azimuth_speed", label: "方位转速（°/秒）", min: 0.01, max: 360, step: 0.1 },
  { key: "elevation_speed", label: "俯仰转速（°/秒）", min: 0.01, max: 360, step: 0.1 },
]

export function ConfigForm({ values, onChange }: ConfigFormProps) {
  return (
    <div className="config-grid">
      {FIELDS.map((f) => (
        <label key={f.key} className="field">
          <span>{f.label}</span>
          <input
            type="number"
            value={values[f.key]}
            min={f.min}
            max={f.max}
            step={f.step ?? 1}
            onChange={(e) => {
              const v = e.target.valueAsNumber
              if (!Number.isNaN(v)) onChange(f.key, v)
            }}
          />
          {f.hint && <em>{f.hint(values[f.key])}</em>}
        </label>
      ))}
    </div>
  )
}
