import type { ScheduleRequest, SchedulerConfig, TargetInput } from "./types"

/** 内置示例：傍晚 20:00 开场，6 个目标，含必观与窗口冲突。 */
export const SAMPLE_REQUEST: ScheduleRequest = {
  initial_time: 72000,
  initial_azimuth: 0,
  initial_elevation: 45,
  azimuth_speed: 2,
  elevation_speed: 1,
  targets: [
    { id: "T1", azimuth: 60, elevation: 60, duration: 600, window_start: 72000, window_end: 78000, priority: 10, must_observe: true },
    { id: "T2", azimuth: 120, elevation: 30, duration: 450, window_start: 73500, window_end: 81000, priority: 8, must_observe: false },
    { id: "T3", azimuth: 180, elevation: 45, duration: 600, window_start: 75000, window_end: 82800, priority: 7, must_observe: false },
    { id: "T4", azimuth: 350, elevation: 60, duration: 300, window_start: 72000, window_end: 76500, priority: 9, must_observe: false },
    { id: "T5", azimuth: 30, elevation: 20, duration: 900, window_start: 78000, window_end: 84600, priority: 5, must_observe: false },
    { id: "T6", azimuth: 200, elevation: 70, duration: 300, window_start: 81000, window_end: 84000, priority: 6, must_observe: false },
  ],
}

const CONFIG_KEYS: (keyof SchedulerConfig)[] = [
  "initial_time",
  "initial_azimuth",
  "initial_elevation",
  "azimuth_speed",
  "elevation_speed",
]

function readNumber(obj: Record<string, unknown>, key: string): number {
  const v = obj[key]
  if (typeof v !== "number" || !Number.isFinite(v)) {
    throw new Error(`字段 ${key} 缺失或不是数字`)
  }
  return v
}

function readTarget(raw: unknown, index: number): TargetInput {
  if (typeof raw !== "object" || raw === null) {
    throw new Error(`第 ${index + 1} 个目标不是对象`)
  }
  const obj = raw as Record<string, unknown>
  const id = obj.id
  if (typeof id !== "string" || id.length === 0) {
    throw new Error(`第 ${index + 1} 个目标缺少编号 id`)
  }
  return {
    id,
    azimuth: readNumber(obj, "azimuth"),
    elevation: readNumber(obj, "elevation"),
    duration: readNumber(obj, "duration"),
    window_start: readNumber(obj, "window_start"),
    window_end: readNumber(obj, "window_end"),
    priority: readNumber(obj, "priority"),
    must_observe: obj.must_observe === true,
  }
}

/** 解析导入的 JSON（轻量预检，权威校验由后端完成并返回可定位错误）。 */
export function parseImported(data: unknown): ScheduleRequest {
  if (typeof data !== "object" || data === null) {
    throw new Error("顶层必须是 JSON 对象")
  }
  const obj = data as Record<string, unknown>
  const config = {} as SchedulerConfig
  for (const key of CONFIG_KEYS) {
    config[key] = readNumber(obj, key)
  }
  if (!Array.isArray(obj.targets)) {
    throw new Error("缺少 targets 数组")
  }
  if (obj.targets.length < 2 || obj.targets.length > 16) {
    throw new Error("目标数量须在 2 至 16 之间")
  }
  const targets = obj.targets.map((raw, i) => readTarget(raw, i))
  return { ...config, targets }
}
