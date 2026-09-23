/** 当天秒数格式化为 HH:MM:SS。 */
export function formatHMS(total: number): string {
  const s = Math.max(0, Math.floor(total))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const pad = (v: number) => String(v).padStart(2, "0")
  return `${pad(h)}:${pad(m)}:${pad(sec)}`
}
