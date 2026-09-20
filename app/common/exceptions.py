"""
统一错误处理：AppError + 错误码体系 + FastAPI全局异常处理器。
所有业务错误用 raise AppError("CODE", status) 抛出。
"""

from fastapi import Request
from fastapi.responses import JSONResponse

# ===== 错误码体系 =====
ERROR_CODES: dict[str, str] = {
    "CODING_AGENT_RUNTIME_UNAVAILABLE": "旧编程运行时已移除，替代 Harness 尚未接入验收",
    # 认证 (401/403)
    "AUTH_REQUIRED":            "未登录，请先登录",
    "AUTH_INVALID_CREDENTIALS": "用户名或密码错误",
    "AUTH_ACCOUNT_DISABLED":    "账号已禁用，请联系管理员",
    "AUTH_SESSION_EXPIRED":     "登录已过期，请重新登录",
    "AUTH_PERMISSION_DENIED":   "权限不足，无法执行此操作",
    "AUTH_DEPARTMENT_DENIED":   "无权访问其他部门的数据",
    "TOKEN_EXPIRED":            "CLI 登录已过期，请重新登录",
    "TOKEN_REVOKED":            "CLI 登录已吊销，请重新登录",
    "USER_DISABLED":            "账号已禁用，请联系管理员",
    "PERMISSION_REV_CHANGED":   "权限已变更，请重新登录",
    "AUTH_TOO_MANY_ATTEMPTS":   "登录失败次数过多，请15分钟后重试",
    "AUTH_USER_NOT_FOUND":      "用户不存在",
    "AUTH_USER_EXISTS":         "用户名已存在",
    "AUTH_ACCOUNT_NOT_ACTIVE":  "账号尚未激活或已禁用",
    "AUTH_SAME_PASSWORD":       "新密码不能与旧密码相同",
    "PASSWORD_TOO_SHORT":       "密码长度不足",
    "PASSWORD_TOO_WEAK":        "密码复杂度不够",
    # role-matrix-v2 §18 — 用户生命周期新增错误码
    "USER_NOT_PENDING":         "用户状态不是 pending，无法激活",
    "USER_NOT_FOUND":           "用户不存在",
    "SUCCESSOR_INVALID":        "接手人不存在或已禁用",
    "LAST_SYSTEM_ADMIN":        "不能禁用或删除最后一个系统管理员",
    "ORG_UNIT_NOT_FOUND":       "部门不存在",
    "ORG_MERGE_SELF":           "不能合并部门到自己",
    "PERMISSIONS_REV_MISMATCH": "会话已过期（权限已变更），请重新登录",
    "DEPT_ADMIN_CANNOT_PROMOTE": "部门管理员不能提拔他人到部门管理员",
    "PARAM_INVALID":            "参数无效",
    "INVALID_GROUP_BY":         "分组维度不在允许列表内",

    # Skill操作 (400/404/409)
    "SKILL_NOT_FOUND":          "Skill不存在",
    "SKILL_ID_INVALID":         "Skill ID格式非法（只允许字母、数字、连字符）",
    "SKILL_ALREADY_EXISTS":     "Skill ID已存在",
    "SKILL_ACCESS_DENIED":      "无权访问该 Skill",
    "SKILL_LOCKED":             "Skill正在被其他用户编辑，请稍后再试",
    "SKILL_VERSION_CONFLICT":   "Skill内容已被其他人修改，请刷新后重试",
    "UI_PREF_CONFLICT":         "个人界面已被其他请求更新，请刷新后重试",
    "SKILL_FRONTMATTER_INVALID": "Skill frontmatter 字段不合法",
    "SKILL_VALIDATION_FAILED":  "SKILL.md 校验失败",
    "STATIC_CHECK_BLOCKED":     "检测到 LLM 客户端 import，需 reviewer override",
    "REVIEW_OVERRIDE_NO_REASON": "请填写 override 理由 ≥ 20 字",
    "REGRESSION_CASE_INVALID":  "回归用例格式不合法",

    # 审核 (400/404)
    "REVIEW_NOT_FOUND":         "审核请求不存在",
    "REVIEW_ALREADY_DECIDED":   "审核请求已处理",
    "REVIEW_SELF_APPROVE":      "不能审核自己提交的变更",

    # 数据源 (400/404/422)
    "DATASOURCE_NOT_FOUND":     "数据源不存在",
    "DATASOURCE_UPLOAD_EMPTY":  "上传文件为空",
    "DATASOURCE_PARSE_ERROR":   "文件解析失败（编码/格式不正确）",
    "DATASOURCE_QUALITY_FAIL":  "数据质量校验失败",
    "MISSING_SHOP_CONTEXT":     "缺少平台或店铺上下文",
    "CONNECTOR_SOURCE_MISMATCH": "Connector 推送来源与数据源不匹配",
    "CONNECTOR_SCOPE_DENIED":   "Connector Key 无权写入该店铺",
    "CONNECTOR_KEY_REVOKED":    "Connector Key 已吊销或不可用",
    "CONNECTOR_KEY_REQUIRED":   "当前 API key 已过期，请重新绑定",
    "LEGACY_BLOCKED":           "兼容期已结束，请改用个人 API key",
    "NO_COOKIE":                "当前店铺没有可用授权 cookie，请先推送",
    "NO_VALID_COOKIE":          "当前店铺没有已验证有效的 cookie，请先验证登录态",
    "CREDENTIAL_UNAVAILABLE":   "授权账号都不可用，请通知 owner 重新登录",
    "COOKIE_PERMISSION_DENIED": "您没有权限操作他人的 cookie",
    "COOKIE_REASON_REQUIRED":   "请填写操作理由",
    "VERIFY_FAILED":            "服务端验证未通过",
    "BROWSER_NOT_RUNNING":      "浏览器未运行，请先启动 Docker Chrome",
    "COOKIE_INJECT_FAILED":     "Cookie 注入浏览器失败",
    "LOGIN_PROBE_UNSUPPORTED":  "该平台暂不支持登态验证",
    "RATE_LIMIT_HOLD":          "平台访问频率限制，已自动等待",
    "CIRCUIT_OPEN":             "该接口暂时不通，已冷却保护",
    "SYCM_WARNING_HOLD":        "生意参谋警告，已冻结该数据块",

    # 执行 (400/500/504)
    "EXECUTION_TIMEOUT":        "Skill执行超时",
    "EXECUTION_SCRIPT_ERROR":   "Skill脚本执行出错",
    "GATEWAY_AUTH_FAILED":      "执行网关认证失败",
    "GATEWAY_EXECUTION_FAILED": "执行网关调用失败",
    "EXECUTION_DATA_MISSING":   "Skill依赖的数据源尚未上传",
    "AICLAW_ERROR":             "AIClaw 调用失败",
    "AICLAW_INSTANCE_NOT_FOUND": "AIClaw 实例不存在",
    "AICLAW_INSTANCE_EXISTS":   "AIClaw 实例 ID 已存在",
    "AICLAW_BRIDGE_AUTH_FAILED": "AIClaw Bridge 认证失败",
    "AICLAW_ENROLLMENT_INVALID": "AIClaw enrollment token 无效或已过期",
    "AICLAW_SIGNATURE_INVALID": "AIClaw 设备签名校验失败",
    "BRIDGE_BUSY":              "AIClaw bridge 处于忙碌状态",
    "BRIDGE_DISCONNECTED":      "AIClaw bridge 连接已断开",
    "BRIDGE_REGISTRY_FULL":     "AIClaw bridge 连接数已达上限",
    "FINGERPRINT_MISMATCH":     "AIClaw bridge 设备指纹不匹配",
    "EPOCH_MISMATCH":           "AIClaw bridge 连接 epoch 已过期",
    "REQUEST_TIMEOUT":          "请求超时",
    "RATE_LIMITED":             "请求过于频繁，请稍后再试",
    "AICLAW_RATE_LIMITED":      "AIClaw 请求过于频繁",
    "BRIDGE_OFFLINE":           "AIClaw Bridge 未连接",
    "BRIDGE_OP_ERROR":          "Bridge 设备端命令执行失败",
    "BRIDGE_PLACEMENT_CONFLICT": "业务节点 Bridge 不能安装在平台主节点机器上",

    # 钉钉 (502/503)
    "DINGTALK_API_ERROR":       "钉钉API调用失败",
    "DINGTALK_RATE_LIMITED":    "钉钉消息发送限流，已加入队列稍后重试",
    "DINGTALK_CALLBACK_INVALID": "钉钉回调签名验证失败",
    "DINGTALK_NOT_CONFIGURED": "钉钉未配置，请联系管理员",
    "DINGTALK_ERROR":           "钉钉API调用失败",

    # LLM (502/504)
    "LLM_API_ERROR":            "模型API调用失败",
    "LLM_TIMEOUT":              "模型响应超时",
    "LLM_QUOTA_EXCEEDED":       "今日对话额度已用完",
    "RUN_TOKEN_INVALID":        "运行授权无效或已过期",
    "RUNTIME_ACTOR_NOT_FOUND":  "无法确定 Skill 运行身份",
    "TOOLMETA_NOT_REGISTERED":  "MCP 工具未登记采集范围",
    "MCP_ENV_MISSING":          "MCP 运行环境异常",
    "MCP_RUNTIME_DRIFT":        "当前 MCP 运行时不是平台版本",
    "MCP_DIRECT_COOKIE_BLOCKED": "MCP 直接读取 cookie 已被平台阻断",
    "MCP_SCOPE_UNCLASSIFIED":   "当前 MCP 工具未登记采集范围",
    "MCP_SCOPE_DENIED":         "无权调用该 MCP 工具",
    "SHOP_SCOPE_DENIED":        "无权访问该店铺",
    "DEBUG_RUN_EXPIRED":        "本地调试授权已过期",
    "IDEMPOTENCY_KEY_REQUIRED": "写操作需要幂等键",
    "PACKAGE_CONFLICT":         "提交包基线落后于平台最新版本",
    "PACKAGE_INVALID":          "提交包不合法",
    "OUTPUT_VALIDATE_FAILED":   "输出结果校验失败",
    "OUTPUT_SCHEMA_INVALID":    "输出 schema 不合法",
    "CATALOG_VERSION_UNSUPPORTED": "CLI 或 Skill 版本过低",
    "CATALOG_SIGNATURE_INVALID": "能力目录签名校验失败",
    "MCP_CALL_FAILED":          "平台侧 MCP 调用失败",
    "MODEL_PARAM_NOT_ACCEPTED": "运行期分析不接受模型或 raw prompt 参数",
    "MODEL_NOT_ALLOWED":        "平台模型配置未通过白名单或定价校验",
    "BUDGET_EXCEEDED":          "本次分析超过预算限制",
    "PROMPT_VERSION_NOT_FOUND": "指定版本的 Skill prompt 不存在",

    # 上下文压缩 (413/503)
    "CONTEXT_OVERFLOW":         "对话上下文过长且无法压缩，请开启新会话",
    "COMPACT_CIRCUIT_BREAKER":  "AI 服务暂时不可用（压缩失败次数过多），请稍后重试",

    # Prompt Registry (404)
    "PROMPT_NOT_FOUND":         "Prompt 不存在或未注册",

    # Agent Core (v7 LangGraph runtime)
    "AGENT_THREAD_FORBIDDEN":   "无权访问该 agent 会话（thread_id 不属于当前用户）",
    "AGENT_EXECUTION_FAILED":   "Agent 执行失败，请稍后重试",

    # 组织架构 (400/404)
    "ORG_HAS_CHILDREN":         "该部门下有子部门，请先处理子部门或使用强制删除",

    # 审批人 (404)
    "APPROVER_NOT_FOUND":       "无法确定审批人",

    # 模板 / 资产 (404/409)
    "TEMPLATE_NOT_FOUND":       "模板不存在",
    "TEMPLATE_ALREADY_EXISTS":  "模板已存在",

    # ABAC (403)
    "ABAC_DENIED":              "ABAC 策略拒绝此操作",

    # 市场认证
    "MARKET_NOT_LISTED":        "Skill 尚未上架市场，无法提交认证",
    "MARKET_CERT_PENDING":      "该 Skill 已有待审核的认证申请",
    "MARKET_CERT_NOT_FOUND":    "认证记录不存在",
    "MARKET_CERT_DECIDED":      "该认证申请已处理",

    # Git (500)
    "GIT_CONFLICT":             "Git操作冲突，请刷新后重试",
    "GIT_OPERATION_FAILED":     "Git操作失败",

    # Skill文件
    "SKILL_FILE_NOT_FOUND":     "Skill文件不存在",

    # Playbook
    "PLAYBOOK_NOT_FOUND":       "Playbook不存在",
    "PLAYBOOK_INVALID":         "Playbook配置不合法",
    "PLAYBOOK_EXECUTION_FAILED": "Playbook执行失败",

    # Project Host
    "PROJECT_NOT_FOUND":       "项目不存在",
    "PROJECT_INVALID":         "项目配置不合法",
    "PROJECT_ALREADY_EXISTS":  "项目 ID 已存在",
    "PROJECT_ACCESS_DENIED":   "无权访问该项目",
    "PROJECT_RUN_NOT_FOUND":   "项目运行记录不存在",
    "PROJECT_CAPABILITY_DENIED": "无权调用该项目能力",
    "PROJECT_PACKAGE_TOO_LARGE": "项目包超过平台限制",
    "PROJECT_PAYLOAD_TOO_LARGE": "项目网关载荷超过平台限制",
    "PROJECT_CAPACITY_EXCEEDED": "项目运行容量已满",
    "PROJECT_SDK_AUTH_REQUIRED": "Project SDK token 缺失",
    "PROJECT_SDK_AUTH_INVALID": "Project SDK token 无效或已过期",
    "PROJECT_SDK_PROJECT_DENIED": "Project SDK token 无权访问该项目",
    "PROJECT_SDK_SCOPE_INVALID": "Project SDK scope 不合法",
    "PROJECT_SDK_SCOPE_DENIED": "Project SDK token 缺少所需 scope",
    "PROJECT_SDK_TOKEN_INVALID": "Project SDK token 参数不合法",
    "PROJECT_SDK_TOKEN_NOT_FOUND": "Project SDK token 不存在",
    "PROJECT_SDK_RATE_LIMITED": "Project SDK 调用过于频繁",
    "PROJECT_SDK_CONCURRENCY_LIMITED": "Project SDK 236 模型并发已达上限",
    "PROJECT_OPENAI_236_INVALID_REQUEST": "236 OpenAI 兼容请求不合法",
    "PROJECT_RESIDENT_MODEL_GATEWAY_NOT_FOUND": "236 常驻模型网关不存在",
    "PROJECT_RESIDENT_MODEL_GATEWAY_OFFLINE": "236 常驻模型网关未在线",
    "PROJECT_RESIDENT_MODEL_GATEWAY_UNSUPPORTED": "236 常驻模型网关不受支持",
    "PROJECT_RESIDENT_MODEL_NOT_FOUND": "236 常驻模型不存在或未加载",
    "PROJECT_RESIDENT_MODEL_REQUIRED": "236 常驻模型 ID 缺失",
    "PROJECT_RESIDENT_MODEL_CHAT_FAILED": "236 常驻模型调用失败",
    "PROJECT_ASSET_INVALID":    "项目运行资产不合法",
    "PROJECT_ASSET_TOO_LARGE":  "项目运行资产超过平台限制",
    "PROJECT_ASSET_UNAVAILABLE": "项目运行资产不可用",
    "PROJECT_ASSET_LINK_EXPIRED": "项目运行资产下载链接已过期",
    "PROJECT_ASSET_LINK_INVALID": "项目运行资产下载链接无效",
    "PROJECT_ASSET_NOT_FOUND":  "项目运行资产不存在",
    "CODING_AGENT_LOCAL_PATH_UNAVAILABLE": "当前执行环境无法访问请求中的本地路径",

    # 素材工作台 / H3 生产与审片
    "MEDIA_ASSET_GROUP_INVALID": "素材组配置不合法",
    "MEDIA_ASSET_GROUP_NOT_FOUND": "素材组不存在或已归档",
    "MEDIA_BATCH_CANCEL_INVALID": "批量取消参数不合法",
    "MEDIA_BATCH_INVALID": "生产批次参数不合法",
    "MEDIA_BATCH_NOT_FOUND": "生产批次不存在",
    "MEDIA_BATCH_STATUS_INVALID": "当前生产批次状态不支持此操作",
    "MEDIA_BRAND_REFERENCE_RECOMMENDED": "涉及包装、Logo 或文字真实性时，请上传正确商品图或确认失真风险",
    "MEDIA_BRIEF_REQUIRED": "请填写产品、文案或生成要求",
    "MEDIA_CONTINUATION_DOWNSTREAM_CONFIRM_REQUIRED": "重做该片段会使后续片段失效，请先确认",
    "MEDIA_CONTINUATION_DURATION_INVALID": "自动续写目标时长或分段时长不合法",
    "MEDIA_CONTINUATION_NOT_FOUND": "自动续写任务不存在",
    "MEDIA_CONTINUATION_SOURCE_INVALID": "自动续写需要有效的 2–15 秒源视频",
    "MEDIA_CREATIVE_OPTION_INVALID": "创作模板不受支持",
    "MEDIA_DURATION_INVALID": "镜头时长只支持 5 秒或 8 秒",
    "MEDIA_IDEMPOTENCY_KEY_REQUIRED": "生产提交缺少幂等键，请刷新后重试",
    "MEDIA_JOB_FILTER_INVALID": "任务筛选或分页参数不合法",
    "MEDIA_JOB_NOT_FOUND": "视频任务不存在或无权访问",
    "MEDIA_JOB_NOT_RETRYABLE": "当前视频任务状态不能重试",
    "MEDIA_LIBRARY_ASSET_NOT_FOUND": "部门素材不存在或已归档",
    "MEDIA_MODE_INVALID": "当前 H3 生成模式不受支持",
    "MEDIA_OUTPUT_PRESET_INVALID": "输出清晰度档位不存在",
    "MEDIA_OUTPUT_PRESET_NOT_READY": "该清晰度尚未通过双节点稳定性基准",
    "MEDIA_PARAM_INVALID": "视频生成参数不合法",
    "MEDIA_PLAN_INVALID": "H3 分镜或编译提示词不合法",
    "MEDIA_PLAN_JSON_INVALID": "AI 分镜 JSON 不合法，请修改后重试",
    "MEDIA_SOURCE_UNDERSTANDING_FAILED": "素材视觉分析失败；严格复刻不会在看不懂素材时继续猜测生成",
    "MEDIA_PLAN_NOT_FOUND": "AI 分镜不属于当前运行，请重新生成分镜",
    "MEDIA_PROMPT_OVERRIDE_INVALID": "手工修改的 H3 提示词不合法",
    "MEDIA_PROMPT_OVERRIDE_BATCH_UNSUPPORTED": "手工修改的最终提示词暂不支持批量提交",
    "MEDIA_CLOUD_VIDEO_NOT_FOUND": "没有找到这条云视频",
    "MEDIA_CLOUD_VIDEO_UNAVAILABLE": "这条云视频当前无法导入",
    "MEDIA_CLOUD_VIDEO_TOO_LARGE": "这条云视频超过平台单素材大小限制",
    "MEDIA_CLOUD_VIDEO_IMPORT_FAILED": "云视频导入失败，请稍后重试",
    "MEDIA_MATERIAL_REQUEST_NOT_FOUND": "素材需求不存在或已归档",
    "MEDIA_MATERIAL_REQUEST_FORBIDDEN": "无权查看或修改该素材需求",
    "MEDIA_MATERIAL_REQUEST_INVALID": "素材需求内容或参数不合法",
    "MEDIA_MATERIAL_REQUEST_LOCKED": "该素材需求已进入生产，不能直接修改",
    "MEDIA_MATERIAL_REQUEST_NOT_SUBMITTED": "请先提交素材需求再生成候选",
    "MEDIA_MATERIAL_REQUEST_FILTER_INVALID": "素材需求筛选条件不合法",
    "MEDIA_CANDIDATE_NOT_FOUND": "素材候选不存在或无权访问",
    "MEDIA_CANDIDATE_FILTER_INVALID": "素材候选筛选条件不合法",
    "MEDIA_CANDIDATE_SORT_INVALID": "素材候选排序方式不受支持",
    "MEDIA_CANDIDATE_IDS_REQUIRED": "请选择要处理的素材候选",
    "MEDIA_CANDIDATE_TAGS_REQUIRED": "请填写要添加的标签",
    "MEDIA_CANDIDATE_TECHNICAL_GATE": "素材尚未通过技术检查，不能进入编导决策",
    "MEDIA_EDITORIAL_DECISION_INVALID": "编导决策不合法",
    "MEDIA_EDITORIAL_DECISION_FORBIDDEN": "当前账号可以查看素材，但没有编导选片权限",
    "MEDIA_EDITORIAL_REJECTION_REASON_REQUIRED": "驳回时请填写问题类型或时间点标注",
    "MEDIA_EDITORIAL_BATCH_INVALID": "批量选片参数不合法",
    "MEDIA_EDITORIAL_BATCH_SELECT_FORBIDDEN": "为防止误选，系统不支持批量选用",
    "MEDIA_DELIVERY_SELECTION_REQUIRED": "只有编导明确选用的素材才能入库",
    "MEDIA_PERFORMANCE_ROW_INVALID": "投流效果数据格式不合法",
    "MEDIA_PERFORMANCE_EXACT_LINK_REQUIRED": "效果归因必须同时提供远端视频ID和广告素材ID",
    "MEDIA_PERFORMANCE_EXACT_LINK_NOT_FOUND": "未找到唯一的远端视频入库记录，不能归因",
    "MEDIA_PERFORMANCE_AD_ASSET_CONFLICT": "广告素材ID与已绑定记录冲突",
    "MEDIA_PERFORMANCE_IDEMPOTENCY_CONFLICT": "同一广告素材日期的数据归属冲突",
    "MEDIA_PERFORMANCE_DATE_INVALID": "效果数据日期范围不合法",
    "MEDIA_PLAN_REQUIRED": "请先生成或填写 H3 分镜",
    "MEDIA_PLAN_SELECTION_INVALID": "分镜方案来源不合法",
    "MEDIA_QUALITY_ANALYSIS_REQUEST_INVALID": "AI 质检任务参数不合法",
    "MEDIA_QUALITY_ANALYSIS_STATUS_INVALID": "当前任务状态不能进行 AI 质检",
    "MEDIA_REFERENCE_DUPLICATE": "参考素材重复",
    "MEDIA_REFERENCE_DURATION_INVALID": "参考视频或音频时长不合法",
    "MEDIA_REFERENCE_DURATION_LIMIT": "参考媒体总时长超过 H3 限制",
    "MEDIA_REFERENCE_INVALID": "参考素材组合不合法",
    "MEDIA_REFERENCE_LIMIT": "参考素材数量超过 H3 限制",
    "MEDIA_REFERENCE_MODE_CONFLICT": "商品首帧与参考复刻素材不能混用",
    "MEDIA_REFERENCE_NOT_ALLOWED": "当前生成模式不允许参考素材",
    "MEDIA_REFERENCE_PREPROCESS_FAILED": "源素材自动处理失败，请检查视频编码或稍后重试",
    "MEDIA_REFERENCE_PROBE_REQUIRED": "参考媒体尚未完成时长和编码检查",
    "MEDIA_REFERENCE_REQUIRED": "当前创作模板需要指定参考素材",
    "MEDIA_REFERENCE_ROLE_INVALID": "参考素材角色不合法",
    "MEDIA_REFERENCE_TOO_LARGE": "参考素材文件过大",
    "MEDIA_REFERENCE_TYPE_INVALID": "参考素材格式不受支持",
    "MEDIA_RESULT_HASH_MISMATCH": "生成结果哈希校验失败",
    "MEDIA_RESULT_INVALID": "生成结果格式、大小或媒体流不合法",
    "MEDIA_RESULT_NOT_READY": "当前视频尚未生成完成，暂时不能创建高清版",
    "MEDIA_ENHANCEMENT_TARGET_INVALID": "当前只支持受控的 1080p 高清化目标",
    "MEDIA_REFERENCE_IDENTITY_POLICY_INVALID": "复刻人物选项无效，请选择保留原人物或换成新演员",
    "MEDIA_RESULT_REQUIRED": "视频结果尚未收集完成",
    "MEDIA_REVIEW_ANNOTATION_INVALID": "审片问题标注不合法",
    "MEDIA_REVIEW_ANNOTATION_NOT_FOUND": "审片问题标注不存在",
    "MEDIA_REVIEW_BATCH_INVALID": "批量审片操作不合法；不支持批量通过",
    "MEDIA_REVIEW_FILTER_INVALID": "审片筛选或分页参数不合法",
    "MEDIA_REVIEW_HARD_GATE_BLOCKED": "视频未通过硬合规门禁，不能审核通过",
    "MEDIA_REVIEW_INVALID": "审核决定不合法",
    "MEDIA_REVIEW_STATUS_INVALID": "当前视频状态不能审核",
    "MEDIA_SCHEDULE_INVALID": "生产开始或截止时间不合法",
    "MEDIA_STATUS_INVALID": "视频任务状态不合法",
    "MEDIA_STATUS_TRANSITION_INVALID": "视频任务不能进行该状态流转",
    "MEDIA_SYNC_STATUS_INVALID": "当前视频状态不能同步云视频",
    "MEDIA_THEME_DIVERGE_INVALID": "主题发散方向数必须为 3–12 个",
    "MEDIA_THEME_DIVERGE_JSON_INVALID": "主题发散结果格式不合法，请修改后重试",
    "MEDIA_WORKFLOW_INVALID": "创作模板配置不合法",
    "MEDIA_WORKFLOW_NOT_FOUND": "创作模板不存在或无权访问",
    "MEDIA_WORKFLOW_PUBLISH_FORBIDDEN": "只有部门管理员可以发布共享创作模板",
    "MEDIA_WORKFLOW_REFERENCE_REQUIRED": "创作模板缺少指定角色的参考素材",

    # AI 待办
    "TODO_NOT_FOUND":            "待办不存在",
    "TODO_ALREADY_DECIDED":      "待办已处理",
    "TODO_NOT_ASSIGNED_TO_YOU":  "该待办不属于你",
    "TODO_INVALID_DECISION":     "无效的待办决策动作",
    "TODO_EXPIRED":              "待办已过期",
    "DECISION_REQUEST_NOT_FOUND":"决策请求不存在",
    "INVALID_REVIEWER_CONFIG":   "Skill 的 reviewer 配置无效",
    "TODO_SPEC_INVALID":         "Skill 输出的待办结构不合法",
    "TODO_DISPATCH_EXECUTOR_REQUIRED": "请先选择当前部门执行人后再通过",

    # 合规规则
    "COMPLIANCE_RULE_NOT_FOUND": "合规规则不存在",
    "COMPLIANCE_RULE_EXISTS":    "合规规则ID已存在",
    "COMPLIANCE_INVALID_REGEX":  "正则表达式格式无效",
    "COMPLIANCE_IMPORT_ENCODING_ERROR": "合规规则导入文件编码错误",
    "COMPLIANCE_IMPORT_EMPTY_FILE": "合规规则导入文件为空",

    # 优化器 / 评测
    "EMPTY_PACK":               "评测包无用例，无法运行评测",
    "OPTIMIZER_NO_CASES":       "该 Skill 还没有任何测试样例或历史决策记录，无法启动优化。请先在「测试」面板添加用例后再试。",
    "INVALID_STATUS":           "当前状态不允许此操作",
    "NOT_FOUND":                "资源不存在",

    # 审批 (400)
    "APPROVER_NOT_FOUND":       "无法确定审批人",
}


class AppError(Exception):
    """统一业务异常，所有模块使用此类抛出错误"""

    def __init__(self, code: str, status: int = 400, detail: dict | None = None):
        self.code = code
        self.status = status
        self.message = ERROR_CODES.get(code, "未知错误")
        self.detail = detail
        super().__init__(self.message)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """FastAPI全局异常处理器，注册到 app.add_exception_handler"""
    import uuid

    body: dict = {
        "code": exc.code,
        "message": exc.message,
        "detail": exc.detail or {},
        "request_id": str(uuid.uuid4())[:8],
    }

    return JSONResponse(
        status_code=exc.status,
        content={"error": body},
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """捕获未处理的异常，返回统一格式而非FastAPI默认的HTML/纯文本"""
    import uuid
    from loguru import logger

    request_id = str(uuid.uuid4())[:8]
    logger.error("未处理异常 [{}]: {} - {}", request_id, type(exc).__name__, exc)

    return JSONResponse(
        status_code=500,
        content={"error": {
            "code": "INTERNAL_ERROR",
            "message": "服务器内部错误，请联系管理员",
            "detail": {},
            "request_id": request_id,
        }},
    )


def _humanize_validation_error(err: dict) -> str:
    """把单条 Pydantic 错误翻译成业务方能看懂的中文提示。"""
    loc = err.get("loc") or ()
    field = ".".join(str(x) for x in loc if x not in ("body", "query", "path"))
    err_type = err.get("type", "")
    ctx = err.get("ctx") or {}
    if err_type in ("string_too_long", "value_error.any_str.max_length"):
        max_len = ctx.get("max_length") or ctx.get("limit_value")
        return f"字段 {field or '内容'} 超过长度上限（最多 {max_len} 字符）"
    if err_type in ("string_too_short", "value_error.any_str.min_length"):
        min_len = ctx.get("min_length") or ctx.get("limit_value") or 1
        return f"字段 {field or '内容'} 不能为空（至少 {min_len} 字符）"
    if err_type in ("missing", "value_error.missing"):
        return f"缺少必填字段 {field or '?'}"
    if (
        err_type.startswith("type_error")
        or err_type.endswith("_type")
        or err_type.endswith("_parsing")
    ):
        return f"字段 {field or '?'} 类型不正确"
    return err.get("msg") or "参数无效"


async def validation_exception_handler(request: Request, exc) -> JSONResponse:
    """FastAPI请求验证错误，返回统一格式"""
    import uuid
    from loguru import logger

    errors = exc.errors() if hasattr(exc, "errors") else []
    # 422 排查辅助：把字段路径 + 错误类型 + 消息打到日志，方便定位前端实际发了什么
    try:
        logger.warning(
            "[422] {} {} → errors={}",
            request.method,
            request.url.path,
            errors,
        )
    except Exception as log_err:
        logger.debug("422 日志记录自身失败: {}", log_err)

    # 取第一条错误生成友好提示，让前端能直接 Message.error 出来
    friendly = _humanize_validation_error(errors[0]) if errors else "请求参数校验失败"

    return JSONResponse(
        status_code=422,
        content={"error": {
            "code": "PARAM_INVALID",
            "message": friendly,
            "detail": {"errors": errors},
            "request_id": str(uuid.uuid4())[:8],
        }},
    )
