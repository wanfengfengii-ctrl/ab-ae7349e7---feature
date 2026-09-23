import { useEffect, useMemo, useRef, useState } from "react"
import { ApiError, postSchedule } from "./api"
import { ConfigForm } from "./components/ConfigForm"
import { ResultView } from "./components/ResultView"
import { TargetTable } from "./components/TargetTable"
import { SAMPLE_REQUEST, parseImported } from "./sample"
import type {
  ScheduleRequest,
  ScheduleResponse,
  SchedulerConfig,
  TargetInput,
} from "./types"

function configOf(req: ScheduleRequest): SchedulerConfig {
  return {
    initial_time: req.initial_time,
    initial_azimuth: req.initial_azimuth,
    initial_elevation: req.initial_elevation,
    azimuth_speed: req.azimuth_speed,
    elevation_speed: req.elevation_speed,
  }
}

export default function App() {
  const [config, setConfig] = useState<SchedulerConfig>(configOf(SAMPLE_REQUEST))
  const [targets, setTargets] = useState<TargetInput[]>(SAMPLE_REQUEST.targets)
  const [result, setResult] = useState<ScheduleResponse | null>(null)
  const [errors, setErrors] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 输入变化后撤下旧结果，避免展示过期计划。
  useEffect(() => {
    setResult(null)
  }, [config, targets])

  const request: ScheduleRequest = useMemo(
    () => ({ ...config, targets }),
    [config, targets],
  )

  function applyRequest(next: ScheduleRequest) {
    setConfig(configOf(next))
    setTargets(next.targets)
    setErrors([])
  }

  async function onImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ""
    if (!file) return
    try {
      const text = await file.text()
      applyRequest(parseImported(JSON.parse(text)))
    } catch (err) {
      setErrors([`导入失败：${err instanceof Error ? err.message : String(err)}`])
    }
  }

  function onExport() {
    const blob = new Blob([JSON.stringify(request, null, 2)], {
      type: "application/json",
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = "schedule-request.json"
    a.click()
    URL.revokeObjectURL(url)
  }

  async function onSubmit() {
    setLoading(true)
    setErrors([])
    try {
      setResult(await postSchedule(request))
    } catch (err) {
      setResult(null)
      setErrors(err instanceof ApiError ? err.items : [String(err)])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1>夜间射电观测排程台</h1>
        <p>全局最优：必观约束 → 总优先级 → 目标数 → 结束时刻 → 编号字典序</p>
      </header>

      <section className="card">
        <div className="card-title">
          <h2>观测条件</h2>
          <div className="actions">
            <button type="button" onClick={() => applyRequest(SAMPLE_REQUEST)}>
              载入示例
            </button>
            <button type="button" onClick={() => fileInputRef.current?.click()}>
              导入 JSON
            </button>
            <button type="button" onClick={onExport}>
              导出 JSON
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="application/json,.json"
              hidden
              onChange={onImportFile}
            />
          </div>
        </div>
        <ConfigForm
          values={config}
          onChange={(field, value) => setConfig((c) => ({ ...c, [field]: value }))}
        />
      </section>

      <section className="card">
        <div className="card-title">
          <h2>
            目标列表（{targets.length} / 16）
          </h2>
        </div>
        <TargetTable targets={targets} onChange={setTargets} />
      </section>

      <div className="submit-row">
        <button
          className="primary"
          type="button"
          disabled={loading || targets.length < 2 || targets.length > 16}
          onClick={onSubmit}
        >
          {loading ? "求解中…" : "开始排程"}
        </button>
      </div>

      {errors.length > 0 && (
        <section className="card error-card">
          <h2>输入有误</h2>
          <ul>
            {errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </section>
      )}

      {result && (
        <ResultView result={result} targets={targets} initialTime={config.initial_time} />
      )}

      <footer className="page-footer">
        时间均为当天整数秒（0–86400）；方位角 0–359°，俯仰角 0–90°；编号字典序按字符串比较
      </footer>
    </div>
  )
}
