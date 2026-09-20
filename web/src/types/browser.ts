export interface BrowserPlatformApiItem {
  id: number
  domain: string
  page_path: string
  page_title?: string
  api_count?: number
  updated_at?: string
}

export interface BrowserFetchJsonResult {
  url: string
  status: number
  ok: boolean
  content_type?: string
  data?: unknown
  parse_error?: string
  text_preview?: string
}
