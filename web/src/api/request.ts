/**
 * Axios 请求封装：统一拦截、错误处理、认证
 */
import axios, { type AxiosError, type AxiosRequestConfig, type AxiosResponse } from 'axios'
import { Message } from '@arco-design/web-vue'
import router from '@/router'
import { useUserStore } from '@/stores/user'
import { installApiCache, invalidateApiCacheForMutation } from './cache'

type ApiErrorData = {
  error?: { code?: string; message?: string; detail?: unknown }
  code?: string
  detail?: unknown
  message?: string
}

type ExtendedAxiosError = AxiosError & {
  _message?: string
  _backendMessage?: string
  _backendCode?: string
  _context?: string
}

type ResourceRule = {
  re: RegExp
  get: string
  action: string
  post?: string
  put?: string
  delete?: string
  patch?: string
  resource?: string
}

const request = axios.create({
  baseURL: '/api',
  timeout: 30000,
  withCredentials: true,
  // 数组参数序列化为 repeated key（FastAPI 约定）：
  // { department: ['a','b'] } → ?department=a&department=b，而不是 department[]=a
  paramsSerializer: { indexes: null },
})

installApiCache(request)

const RESOURCE_RULES: ResourceRule[] = [
  { re: /^\/hall\/ai-chat\//, get: '加载', action: 'AI Chat', post: '发送', put: '更新', patch: '更新' },
  { re: /^\/skills\/[^/]+\/workbench\/chat/, get: '查询', action: 'AI 对话' },
  { re: /^\/skills\/[^/]+\/workbench\/command/, get: '查询', action: '执行命令' },
  { re: /^\/skills\/[^/]+\/workbench\/apply/, get: '查询', action: '应用变更' },
  { re: /^\/skills\/[^/]+\/workbench\//, get: '查询', action: '工作台' },
  { re: /^\/skills\/[^/]+\/validate/, get: '查询', action: '校验' },
  { re: /^\/skills\/[^/]+\/lock/, get: '查询', action: '编辑锁' },
  { re: /^\/skills\/[^/]+\/shadow\//, get: '查询', action: '影子运行' },
  { re: /^\/skills\/[^/]+\/guardian\//, get: '查询', action: 'Guardian 监控' },
  { re: /^\/skills\/[^/]+\/files\//, get: '读取', action: '文件' },
  { re: /^\/skills\/[^/]+\/file-head/, get: '读取', action: '历史文件' },
  { re: /^\/skills\/[^/]+\/generate-tests/, get: '生成', action: '测试用例' },
  { re: /^\/skills\/[^/]+\/mermaid/, get: '生成', action: '决策树图' },
  { re: /^\/skills\/[^/]+\/publish-readiness/, get: '检查', action: '发布门禁' },
  { re: /^\/skills\/batch-publish/, get: '执行', action: '批量发布' },
  { re: /^\/skills\/batch-create/, get: '执行', action: '批量创建' },
  { re: /^\/skills\/departments/, get: '加载', action: '部门列表' },
  { re: /^\/skills\/pinned/, get: '加载', action: '置顶列表' },
  { re: /^\/skills\/[^/]+\/pin/, get: '设置', action: '置顶' },
  { re: /^\/skills\/?$/, get: '加载', action: 'Skill 列表', post: '创建', resource: 'Skill' },
  { re: /^\/skills\/[^/]+$/, get: '加载', action: '', post: '创建', put: '保存', delete: '删除', resource: 'Skill' },
  { re: /^\/reviews\//, get: '加载', action: '审核' },
  { re: /^\/playbooks\//, get: '加载', action: '工作流' },
  { re: /^\/projects\/runs\/[^/]+\/assets\/?$/, get: '读取', post: '上传', action: '运行资产' },
  { re: /^\/projects\/runs\/[^/]+\/assets\/[^/]+\/download/, get: '下载', action: '运行资产' },
  { re: /^\/projects\/runs\/[^/]+\/analyze/, get: 'AI 分析', post: 'AI 分析', action: '项目运行' },
  { re: /^\/projects\/runs\/[^/]+\/capability/, get: '调用', post: '调用', action: '项目能力' },
  { re: /^\/projects\//, get: '加载', action: '项目' },
  { re: /^\/executions\//, get: '查询', action: '执行记录' },
  { re: /^\/aiclaw\/instances\/[^/]+\/agents/, get: '加载', action: 'AIClaw 对话 Agent' },
  { re: /^\/aiclaw\/instances/, get: '加载', action: 'AIClaw 对话实例' },
  { re: /^\/todos\/stats/, get: '加载', action: '待办统计' },
  { re: /^\/todos\/[^/]+\/decide/, get: '执行', action: '待办决策' },
  { re: /^\/todos\//, get: '加载', action: '待办' },
  { re: /^\/data-sources\//, get: '操作', action: '数据源' },
  { re: /^\/knowledge\//, get: '操作', action: '知识库' },
  { re: /^\/dashboard\//, get: '加载', action: '看板' },
  { re: /^\/auth\//, get: '', action: '认证' },
]

function describeRequest(config?: AxiosRequestConfig): string {
  const url = (config?.url || '').replace(/^\/api/, '')
  const method = (config?.method || 'get').toLowerCase()
  const skillMatch = url.match(/^\/skills\/([^/?#]+)/)
  const skillId = skillMatch && !['pinned', 'departments', 'batch-publish', 'batch-create', 'motif-library', 'architect', 'workbench'].includes(skillMatch[1])
    ? skillMatch[1]
    : null

  for (const rule of RESOURCE_RULES) {
    if (!rule.re.test(url)) continue
    const verb =
      method === 'get' ? rule.get :
      method === 'post' ? (rule.post || rule.get || '操作') :
      method === 'put' ? (rule.put || '保存') :
      method === 'delete' ? (rule.delete || '删除') :
      method === 'patch' ? (rule.put || rule.patch || '更新') : '操作'
    const action = rule.action
    const resource = rule.resource || ''
    const parts = [verb, resource || action].filter(Boolean)
    if (skillId) parts.push(skillId)
    return parts.join(' ').trim()
  }
  return method.toUpperCase() + ' ' + url
}

function detailToMessage(detail: unknown): string {
  if (!detail) return ''
  if (typeof detail === 'string') return detail
  if (typeof detail !== 'object') return String(detail)
  const record = detail as Record<string, unknown>
  const direct = record.message || record.detail || record.error
  if (typeof direct === 'string') return direct
  if (direct && typeof direct === 'object') return detailToMessage(direct)
  const json = JSON.stringify(record)
  return json === '{}' ? '' : json
}

function extractBackendMessage(data?: ApiErrorData | string | null): string {
  if (!data) return ''
  if (typeof data === 'string') return data
  const nestedDetail = detailToMessage(data.error?.detail)
  if (nestedDetail) return nestedDetail
  const rootDetail = detailToMessage(data.detail)
  if (rootDetail) return rootDetail
  if (data.error?.message) return data.error.message
  if (data.message) return data.message
  return ''
}

function extractBackendCode(data?: ApiErrorData | null): string {
  if (!data || typeof data !== 'object') return ''
  return data.error?.code || data.code || ''
}

request.interceptors.response.use(
  (response: AxiosResponse) => {
    if (response.config.responseType === 'blob') {
      return response
    }
    invalidateApiCacheForMutation(response.config)
    return response.data
  },
  (error: ExtendedAxiosError) => {
    const status = error.response?.status
    const data = error.response?.data as ApiErrorData | undefined
    const ctx = describeRequest(error.config)
    const backendMsg = extractBackendMessage(data)
    const backendCode = extractBackendCode(data)
    error._backendCode = backendCode
    error._backendMessage = backendMsg
    error._context = ctx

    let friendly = `${ctx} 失败（网络异常）`
    if (status === 404) {
      friendly = `${ctx} 失败 (404 未找到)`
    } else if (status === 403) {
      friendly = backendMsg ? `${ctx} 失败：${backendMsg}` : `${ctx} 失败（权限不足）`
    } else if (status === 401) {
      friendly = `${ctx} 失败（未登录）`
    } else if (status === 423) {
      friendly = backendMsg ? `${ctx} 失败：${backendMsg}` : `${ctx} 失败（已被锁定）`
    } else if (status === 429) {
      friendly = backendMsg ? `${ctx} 失败：${backendMsg}` : `${ctx} 失败（请求过于频繁）`
    } else if (status && status >= 500) {
      friendly = `${ctx} 失败 (${status} 服务器错误)${backendMsg ? '：' + backendMsg : ''}`
    } else if (status) {
      friendly = backendMsg ? `${ctx} 失败：${backendMsg}` : `${ctx} 失败 (${status})`
    }

    if (status === 401) {
      const isCredentialError =
        backendCode === 'AUTH_INVALID_CREDENTIALS' ||
        backendCode === 'AUTH_TOO_MANY_ATTEMPTS' ||
        backendCode === 'AUTH_USER_NOT_FOUND'

      if (isCredentialError) {
        const msg = backendMsg || '用户名或密码错误'
        Message.error(msg)
        error._message = msg
        error._backendCode = backendCode
        error._backendMessage = backendMsg
        error._context = ctx
        return Promise.reject(error)
      }
      const store = useUserStore()
      store.userInfo = null
      Message.warning('会话已过期，请重新登录')
      router.push('/login')
      error._message = friendly
      error._backendCode = backendCode
      return Promise.reject(error)
    }

    if (status === 403) {
      Message.error(friendly)
      error._message = friendly
      return Promise.reject(error)
    }

    if (status === 429) {
      Message.warning(friendly)
      error._message = friendly
      return Promise.reject(error)
    }

    error._message = friendly
    return Promise.reject(error)
  },
)

// v2.6.2: 类型改造 —— response interceptor 里 `return response.data`，运行时 request.get()
// 返回的是 T 而不是 AxiosResponse<T>。给 AxiosInstance 打个 override 类型，让业务代码
// 拿到的直接是 T，而不用再手动 cast 或 `.data`。消除 ContractDrift/InboxCenter/TodosTab
// 等 `AxiosResponse<any>.xxx` 不存在的 pre-existing 类型错误。
type UnwrappedRequest = Omit<typeof request, 'get' | 'post' | 'put' | 'delete' | 'patch' | 'head' | 'options' | 'request'> & {
  get: <T = unknown>(url: string, config?: AxiosRequestConfig) => Promise<T>
  post: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>
  put: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>
  delete: <T = unknown>(url: string, config?: AxiosRequestConfig) => Promise<T>
  patch: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>
  head: <T = unknown>(url: string, config?: AxiosRequestConfig) => Promise<T>
  options: <T = unknown>(url: string, config?: AxiosRequestConfig) => Promise<T>
  request: <T = unknown>(config: AxiosRequestConfig) => Promise<T>
}

export default request as unknown as UnwrappedRequest
