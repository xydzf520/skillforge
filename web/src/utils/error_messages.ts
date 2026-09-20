export const ERROR_MESSAGES: Record<string, string> = {
  NO_COOKIE: '当前店铺没有可用授权 cookie，请先推送',
  CREDENTIAL_UNAVAILABLE: '授权账号都不可用，请通知 owner 重新登录',
  CONNECTOR_KEY_REQUIRED: '请重新绑定 API key（旧 key 已不再支持）',
  CONNECTOR_KEY_REVOKED: 'key 已被撤销，请联系管理员或重新申请',
  MISSING_SHOP_CONTEXT: '请填写店铺 ID',
  VERIFY_FAILED: '服务端验证未通过',
  RATE_LIMIT_HOLD: '平台访问频率限制，已自动等待',
  CIRCUIT_OPEN: '该接口暂时不通，已冷却保护',
  SYCM_WARNING_HOLD: '生意参谋警告，已冻结该数据块',
  BUDGET_EXCEEDED: '今日 AI 分析预算已用完',
  MODEL_NOT_ALLOWED: '模型未在白名单',
  SDK_FIELDS_REQUIRED: 'Skill SDK 已升级，请补充店铺字段',
  TOOLMETA_NOT_REGISTERED: 'MCP 工具未注册采集范围，请联系平台 admin',
  MCP_ENV_MISSING: 'MCP 运行环境异常，请联系平台 admin',
  RUN_TOKEN_INVALID: '运行授权已过期，请重新启动 run',
  STATIC_CHECK_BLOCKED: '检测到 LLM 客户端 import，需 reviewer override',
  REVIEW_OVERRIDE_NO_REASON: '请填写 override 理由 ≥ 20 字',
  COOKIE_PERMISSION_DENIED: '您没有权限操作他人的 cookie',
  COOKIE_REASON_REQUIRED: '请填写操作理由',
  LEGACY_BLOCKED: '兼容期已结束，请改用个人 API key',
  FIXTURE_SENSITIVE: '检测到敏感字段，请脱敏',
  MCP_RUNTIME_DRIFT: '当前 MCP 运行时不是平台版本，请升级节点运行时',
  MCP_SCOPE_UNCLASSIFIED: '当前 MCP 工具未登记采集范围',
  PROMPT_VERSION_NOT_FOUND: 'Prompt 版本未找到，请检查 Skill 发布状态',
  MODEL_PARAM_NOT_ACCEPTED: '请求字段不被接受（首期模型由平台决定）',
  CONNECTOR_SCOPE_DENIED: '无权将 cookie 推送到该店铺，请联系管理员核对绑定关系',
  CONNECTOR_SOURCE_MISMATCH: 'source_id 与 platform/shop_id 不匹配，请检查插件配置',
  MCP_DIRECT_COOKIE_BLOCKED: 'MCP 直接读取 cookie 已被平台阻断',
}

export function errorMessage(code?: string, fallback = '操作失败'): string {
  if (!code) return fallback
  return ERROR_MESSAGES[code] || fallback
}
