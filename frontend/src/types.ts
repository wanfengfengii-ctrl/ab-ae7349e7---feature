export interface SchedulerConfig {
  initial_time: number
  initial_azimuth: number
  initial_elevation: number
  azimuth_speed: number
  elevation_speed: number
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

export interface Observation {
  target_id: string
  slew: SlewInfo
  arrival_time: number
  wait_seconds: number
  start: number
  end: number
}

export interface ScheduleResponse {
  status: "ok" | "infeasible"
  message: string | null
  observations: Observation[]
  unscheduled: string[]
  total_priority: number
  target_count: number
  end_time: number | null
}
