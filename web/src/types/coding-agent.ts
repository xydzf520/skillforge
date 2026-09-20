export interface CodingRuntimeProfile {
  vendor_version?: string
  provider?: string
  permission_strategy?: string
  bare_mode?: boolean
  mcp_servers_count?: number
}

export interface CodingSessionReadyPayload {
  session_id: string
  model: string
  tools: string[]
  work_dir: string
  prompt_hash: string
  config_dir: string
  runtime_profile: CodingRuntimeProfile | null
}

export interface CodingToolCallPayload {
  id: string
  tool: string
  input: Record<string, unknown>
  decision?: string
  decision_reason?: string
}

export interface CodingToolResultPayload {
  id: string
  is_error: boolean
  content: unknown
}

export interface CodingFileChangePayload {
  path: string
  operation: string
  old_string?: string
  new_string?: string
  snippet_preview?: string
  edit_count?: number
  edits_preview?: unknown[]
}

export interface CodingPermissionRequestPayload {
  request_id: string
  tool: string
  input: Record<string, unknown>
  policy?: string
  auto_approved?: boolean
}

export interface CodingUsagePayload {
  input_tokens?: number
  output_tokens?: number
  cache_creation_input_tokens?: number
  cache_read_input_tokens?: number
  total_tokens?: number
}

export interface CodingDonePayload {
  subtype?: string
  is_error?: boolean
  duration_ms?: number
  total_cost_usd?: number
  usage?: CodingUsagePayload
  tool_call_count?: number
  stop_reason?: string
  git_commit?: string
  git_commit_full?: string
  git_commit_error?: string
  changed_files?: string[]
  diff_summary?: Record<string, unknown>
}
