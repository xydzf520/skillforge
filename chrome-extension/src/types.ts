export type PlatformKey = 'taobao' | 'sycm' | 'alimama' | 'douyin' | 'jd' | 'pdd'

export interface PlatformConfig {
  name: string
  domains: string[]
  sourceId: string
}

export interface CookieDetail {
  name: string
  value: string
  domain: string
  path: string
  secure?: boolean
  httpOnly?: boolean
  sameSite?: string
  expirationDate?: number
}

export interface StorageConfig {
  serverUrl: string
  apiKey: string
  autoSync: boolean
}

export interface PlatformStatus {
  connected: boolean
  pushedAt?: string
  message?: string
  autoSync?: boolean
}

export interface ApiLogEntry {
  type: string
  method: string
  url: string
  timestamp: number
  status?: number
  duration?: number
  size?: number
  contentType?: string
  responseKeys?: unknown
}

export type RuntimeMessage =
  | { type: 'sync-all' }
  | { type: 'sync-platform'; platform: PlatformKey }
  | { type: 'get-status' }
  | { type: 'report-apis' }
  | { type: 'get-api-log' }

export interface SyncResult {
  ok: boolean
  error?: string
  data?: unknown
}

export type SyncAllResult = Record<PlatformKey, SyncResult>
export type StatusResponse = Record<PlatformKey, PlatformStatus>

export interface ApiLogResponse {
  apis: ApiLogEntry[]
  pageUrl: string
  pageTitle: string
}

export interface ReportApisResult extends SyncResult {
  apiCount?: number
}
