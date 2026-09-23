import { formatHMS } from "../time"
import type { CableEnvelope, SchedulerConfig } from "../types"

interface ConfigFormProps {
  values: SchedulerConfig
  onChange: (field: NumericConfigKey, value: number) => void
  onEnvelopeChange: (envelope: CableEnvelope | null) => void
}

type NumericConfigKey =
  | "initial_time"
  | "initial_azimuth"
  | "initial_elevation"
  | "azimuth_speed"
  | "elevation_speed"

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

const ENVELOPE_MAX_SPAN = 1440

function congruent(reference: number, initialAzimuth: number): boolean {
  return ((reference - initialAzimuth) % 360 + 360) % 360 === 0
}

export function ConfigForm({ values, onChange, onEnvelopeChange }: ConfigFormProps) {
  const env = values.cable_envelope ?? null
  const enabled = env !== null

  function updateEnv(patch: Partial<CableEnvelope>) {
    if (env === null) return
    onEnvelopeChange({ ...env, ...patch })
  }

  const span = enabled ? env.upper_limit - env.lower_limit : 0
  const envValid =
    !enabled ||
    (env.lower_limit <= env.upper_limit &&
      span <= ENVELOPE_MAX_SPAN &&
      env.lower_limit <= env.reference_azimuth &&
      env.reference_azimuth <= env.upper_limit &&
      congruent(env.reference_azimuth, values.initial_azimuth))

  return (
    <div className="config-stack">
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
            checked={enabled}
            onChange={(e) =>
              onEnvelopeChange(
                e.target.checked
                  ? {
                      // 默认：零位取当前初始方位，区间向两侧各放约半圈。
                      reference_azimuth: values.initial_azimuth,
                      lower_limit: values.initial_azimuth - 180,
                      upper_limit: values.initial_azimuth + 180,
                    }
                  : null,
              )
            }
          />
          <span>启用电缆包络模式（展开方位 + 整数软限位，联合求解圈位）</span>
        </label>

        {enabled && (
          <>
            <div className="config-grid envelope-grid">
              <label className="field">
                <span>电缆零位（展开方位°）</span>
                <input
                  type="number"
                  value={env.reference_azimuth}
                  step={1}
                  onChange={(e) => {
                    const v = e.target.valueAsNumber
                    if (!Number.isNaN(v)) updateEnv({ reference_azimuth: Math.trunc(v) })
                  }}
                />
                <em>须与初始方位 {values.initial_azimuth}° 相差 360 的整数倍</em>
              </label>
              <label className="field">
                <span>软限位下界（含，°）</span>
                <input
                  type="number"
                  value={env.lower_limit}
                  step={1}
                  onChange={(e) => {
                    const v = e.target.valueAsNumber
                    if (!Number.isNaN(v)) updateEnv({ lower_limit: Math.trunc(v) })
                  }}
                />
              </label>
              <label className="field">
                <span>软限位上界（含，°）</span>
                <input
                  type="number"
                  value={env.upper_limit}
                  step={1}
                  onChange={(e) => {
                    const v = e.target.valueAsNumber
                    if (!Number.isNaN(v)) updateEnv({ upper_limit: Math.trunc(v) })
                  }}
                />
              </label>
            </div>
            <p className={`hint envelope-hint${envValid ? "" : " invalid"}`}>
              {envValid
                ? `区间跨度 ${span}°；目标仍用 0–359° 方位，后端在区间内选择同余展开位置（圈位）并与观测顺序联合求解。`
                : `包络参数无效：需 lower ≤ reference ≤ upper、跨度 ≤ ${ENVELOPE_MAX_SPAN}°、零位与初始方位同余。`}
            </p>
          </>
        )}
      </div>
    </div>
  )
}
