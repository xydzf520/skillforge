export function truthyAutoAnalyze(value: unknown, fallback = true): boolean {
  if (value === undefined || value === null || value === '') return fallback
  if (value === false || value === 0) return false
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (['false', '0', 'no', 'off'].includes(normalized)) return false
    if (['true', '1', 'yes', 'on'].includes(normalized)) return true
  }
  return Boolean(value)
}

export function normalizeGatewayIngestPayload(
  payload: Record<string, unknown>,
  requestId: unknown,
): Record<string, unknown> {
  const normalized = { ...payload }
  const explicitAutoAnalyze = Object.prototype.hasOwnProperty.call(normalized, 'auto_analyze')
    ? normalized.auto_analyze
    : normalized.autoAnalyze
  delete normalized.autoAnalyze
  normalized.request_id = requestId
  normalized.auto_analyze = truthyAutoAnalyze(explicitAutoAnalyze, true)
  return normalized
}
