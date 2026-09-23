import type { ScheduleRequest, ScheduleResponse } from "./types"

/** 后端返回的可定位校验错误（HTTP 422）。 */
export class ApiError extends Error {
  constructor(public items: string[]) {
    super(items.join("\n"))
  }
}

interface ValidationItem {
  loc?: (string | number)[]
  msg?: string
}

export async function postSchedule(req: ScheduleRequest): Promise<ScheduleResponse> {
  let resp: Response
  try {
    resp = await fetch("/api/schedule", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    })
  } catch {
    throw new ApiError(["无法连接后端服务，请确认服务已启动"])
  }
  const body: unknown = await resp.json().catch(() => null)
  if (resp.status === 422) {
    const detail = (body as { detail?: ValidationItem[] } | null)?.detail
    const items = Array.isArray(detail)
      ? detail.map((d) => `${(d.loc ?? []).join(" → ") || "请求"}: ${d.msg ?? "非法输入"}`)
      : ["请求校验失败"]
    throw new ApiError(items)
  }
  if (!resp.ok) {
    const detail = (body as { detail?: string } | null)?.detail
    throw new ApiError([`请求失败：${detail ?? `HTTP ${resp.status}`}`])
  }
  return body as ScheduleResponse
}
