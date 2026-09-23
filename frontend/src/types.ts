export interface CableEnvelope {
  reference_azimuth: number
  lower_limit: number
  upper_limit: number
}

export interface SchedulerConfig {
  initial_time: number
  initial_azimuth: number
  initial_elevation: number
  azimuth_speed: number
  elevation_speed: number
  cable_envelope?: CableEnvelope | null
}

export interface TargetInput {
  id: string
  azimuth: number
  elevation: number
  duration: number
  window_start: number
  window_end: number
  priority: number
  must_observe: boolean
}

export interface ScheduleRequest extends SchedulerConfig {
  targets: TargetInput[]
}

export interface SlewInfo {
  azimuth_seconds: number
  elevation_seconds: number
  total_seconds: number
}

export type SlewDirection = "cw" | "ccw" | "none"

export interface Observation {
  target_id: string
  slew: SlewInfo
  arrival_time: number
  wait_seconds: number
  start: number
  end: number
  /** 仅电缆包络模式给出：起止展开方位与顺逆方向。 */
  azimuth_start: number | null
  azimuth_end: number | null
  direction: SlewDirection | null
}

export interface ScheduleResponse {
  status: "ok" | "infeasible"
  message: string | null
  observations: Observation[]
  unscheduled: string[]
  total_priority: number
  target_count: number
  end_time: number | null
  cable_envelope: CableEnvelope | null
}
