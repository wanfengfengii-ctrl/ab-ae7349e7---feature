import { formatHMS } from "../time"
import type { CableEnvelopeInput, SchedulerConfig } from "../types"

type NumericConfigKey =
  | "initial_time"
  | "initial_azimuth"
  | "initial_elevation"
  | "azimuth_speed"
  | "elevation_speed"

interface ConfigFormProps {
  values: SchedulerConfig
  onChange: (field: NumericConfigKey, value: number) => void
  onEnvelopeChange: (patch: Partial<CableEnvelopeInput> | null) => void
}

interface FieldDef {
  key: NumericConfigKey
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

interface EnvFieldDef {
  key: keyof CableEnvelopeInput
  label: string
}

const ENVELOPE_FIELDS: EnvFieldDef[] = [
  { key: "initial_azimuth_unwrapped", label: "展开初始方位（°，与初始方位同余）" },
  { key: "min_azimuth", label: "软限位下限（°）" },
  { key: "max_azimuth", label: "软限位上限（°）" },
]

function defaultEnvelope(initialAzimuth: number): CableEnvelopeInput {
  return {
    initial_azimuth_unwrapped: initialAzimuth,
    min_azimuth: initialAzimuth - 360,
    max_azimuth: initialAzimuth + 360,
  }
}

export function ConfigForm({ values, onChange, onEnvelopeChange }: ConfigFormProps) {
  const envelope = values.cable_envelope
  return (
    <div>
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

      <div className="envelope">
        <label className="envelope-toggle">
          <input
            type="checkbox"
            checked={envelope !== null}
            onChange={(e) => {
              if (e.target.checked) {
                onEnvelopeChange(defaultEnvelope(values.initial_azimuth))
              } else {
                onEnvelopeChange(null)
              }
            }}
          />
          <span>启用电缆包络模式（展开方位 + 整数软限位，联合求解圈位与顺序）</span>
        </label>
        {envelope !== null && (
          <>
            <div className="config-grid">
              {ENVELOPE_FIELDS.map((f) => (
                <label key={f.key} className="field">
                  <span>{f.label}</span>
                  <input
                    type="number"
                    value={envelope[f.key]}
                    step={1}
                    onChange={(e) => {
                      const v = e.target.valueAsNumber
                      if (!Number.isNaN(v)) onEnvelopeChange({ [f.key]: v })
                    }}
                  />
                </label>
              ))}
            </div>
            <p className="hint envelope-hint">
              展开初始方位须与初始方位相差 360 的整数倍；区间宽度不超过 720°（2 整圈）。
              目标仍按 0–359° 填写，每次到达在区间内选择同余的展开位置，转向不得越过软限位。
            </p>
          </>
        )}
      </div>
    </div>
  )
}
