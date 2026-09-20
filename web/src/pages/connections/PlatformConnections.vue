<template>
  <div class="platform-section">
    <div class="platform-section-header">
      <div class="platform-section-title">
        <icon-link :size="15" /> 平台连接
        <span class="platform-hint">Chrome 扩展自动同步 · Skill 执行时读取</span>
      </div>
      <a-space>
        <a-button size="small" type="primary" @click="showRemoteSyncModal = true"><icon-sync :size="13" /> 远程同步</a-button>
        <a-button size="small" @click="exportConnections"><icon-download :size="13" /> 导出</a-button>
        <a-button size="small" @click="triggerImport"><icon-upload :size="13" /> 导入</a-button>
        <a-button size="small" @click="showApiKeyModal = true">
          <icon-settings :size="13" /> API Key 管理
        </a-button>
      </a-space>
      <input ref="importFileRef" type="file" accept=".json" style="display:none" @change="handleImportFile" />
    </div>

    <div v-if="!platformSources.length" class="platform-empty">
      <icon-link :size="24" style="color: var(--ai-ink-4)" />
      <p>暂无平台连接。安装 Chrome 扩展 → 登录电商平台 → 自动同步。</p>
      <a-space>
        <a-button size="small" type="primary" @click="showRemoteSyncModal = true">从已有机器同步</a-button>
        <a-button size="small" @click="showApiKeyModal = true">获取 API Key</a-button>
      </a-space>
    </div>

    <template v-else>
      <div class="platform-status-note">
        <div class="note-title">状态说明</div>
        <div class="note-text">“Cookie 已同步”只代表扩展已上报登录凭证；“登态”和“业务接口”才用于判断 Skill 当前能否采集。</div>
      </div>

      <div class="pool-summary-panel">
        <div class="pool-summary-head">
          <div>
            <div class="pool-summary-title">Cookie 池摘要</div>
            <div class="pool-summary-subtitle">按 platform + shop_id 聚合，不展示明文 cookie 或 owner_user_id。</div>
          </div>
          <a-button size="small" :loading="poolLoading" @click="loadCookiePools">刷新</a-button>
        </div>
        <a-alert v-if="poolLoadError" type="warning" class="pool-summary-alert">
          {{ poolLoadError }}
        </a-alert>
        <div v-if="poolSummaries.length" class="pool-summary-grid">
          <button
            v-for="pool in poolSummaries"
            :key="poolKey(pool)"
            class="pool-summary-card"
            type="button"
            :class="{ active: selectedPool?.platform === pool.platform && selectedPool?.shopId === pool.shop_id }"
            @click="openPool(pool)"
          >
            <span class="pool-card-name">{{ poolLabel(pool) }}</span>
            <span class="pool-card-stats">
              Active {{ pool.active_count || 0 }} · Standby {{ pool.standby_count || 0 }} · 健康 {{ formatHealth(pool.avg_health) }}
            </span>
            <span class="pool-card-meta">
              验证 {{ formatRatio(pool.verify_pass_rate) }} · 多账号 {{ formatRatio(pool.mixed_credentials_ratio) }}
            </span>
          </button>
        </div>
        <a-empty v-else-if="!poolLoading" description="暂无 Cookie 池摘要" />
      </div>

      <div class="platform-grid">
        <div
          v-for="ps in platformSources"
          :key="ps.id"
          class="platform-card"
          :class="{
            'platform-connected': ps.config?.connected,
            'platform-expanded': expandedPlatform === ps.id,
          }"
          @click="expandedPlatform = expandedPlatform === ps.id ? null : ps.id"
        >
          <div class="pc-title-row">
            <div class="pc-title-left">
              <div class="pc-dot" :class="ps.config?.connected ? 'pc-dot-on' : 'pc-dot-off'" />
              <div class="pc-name">{{ platformLabel(ps.config?.platform) }}</div>
            </div>
            <icon-down v-if="expandedPlatform === ps.id" :size="12" class="pc-chevron" />
            <icon-right v-else :size="12" class="pc-chevron" />
          </div>

          <div class="pc-tags">
            <a-tag size="small" :color="ps.config?.connected ? 'green' : 'gray'">
              {{ ps.config?.connected ? 'Cookie 已同步' : 'Cookie 未同步' }}
            </a-tag>
            <a-tooltip :content="verifyTooltip(ps.config)">
              <a-tag size="small" :color="verifyDisplayColor(ps)">
                登态：{{ verifyDisplayLabel(ps) }}
              </a-tag>
            </a-tooltip>
            <a-tag size="small" :color="businessAccessColor(ps)">
              业务接口：{{ businessAccessLabel(ps) }}
            </a-tag>
            <a-tag v-if="poolSummaryForPlatform(ps)" size="small" color="purple">
              Cookie 池 {{ poolSummaryForPlatform(ps)?.active_count || 0 }}/{{ poolSummaryForPlatform(ps)?.total_count || 0 }}
            </a-tag>
          </div>

          <div class="pc-summary">{{ connectionSummary(ps) }}</div>

          <div class="pc-meta">
            <span>同步：{{ platformSyncedAt(ps) ? timeAgo(platformSyncedAt(ps)) : '未同步' }}</span>
            <span>验证：{{ isVerifyingSource(ps.id) ? '判断中' : (ps.config?.last_verified_at ? timeAgo(ps.config.last_verified_at) : '未验证') }}</span>
          </div>
        </div>
      </div>

      <div v-if="selectedPlatform" class="pc-detail-panel" @click.stop>
        <div class="pc-detail-head">
          <div>
            <div class="pc-detail-title">{{ platformLabel(selectedPlatform.config?.platform) }}</div>
            <div class="pc-detail-subtitle">连接详情与操作</div>
          </div>
          <a-space>
            <a-button
              v-if="PLATFORM_HOME_URLS[selectedPlatform.id]"
              size="mini"
              type="primary"
              @click.stop="openInBrowser(selectedPlatform.id)"
            >
              <icon-launch :size="12" /> 在浏览器中打开
            </a-button>
            <a-button size="mini" @click.stop="testCookies(selectedPlatform.id)">测试 Cookie</a-button>
            <a-button
              v-if="selectedPlatformPool"
              size="mini"
              type="outline"
              @click.stop="openSelectedPlatformPool"
            >
              查看 Cookie 池
            </a-button>
            <a-button
              size="mini"
              type="outline"
              :loading="verifyingId === selectedPlatform.id"
              @click.stop="runVerifyLogin(selectedPlatform.id)"
            >
              验证登态
            </a-button>
            <a-button size="mini" status="danger" @click.stop="disconnectPlatform(selectedPlatform.id)">断开连接</a-button>
          </a-space>
        </div>

        <div class="pc-detail-grid">
          <div class="pc-detail-item">
            <div class="pc-detail-label">数据源 ID</div>
            <div class="pc-detail-value pc-mono">{{ selectedPlatform.id }}</div>
          </div>
          <div class="pc-detail-item">
            <div class="pc-detail-label">域名</div>
            <div class="pc-detail-value pc-mono">{{ selectedPlatform.config?.domain || '—' }}</div>
          </div>
          <div class="pc-detail-item">
            <div class="pc-detail-label">最后同步</div>
            <div class="pc-detail-value">{{ platformSyncedAt(selectedPlatform) ? formatServerTime(platformSyncedAt(selectedPlatform)) : '—' }}</div>
          </div>
          <div class="pc-detail-item">
            <div class="pc-detail-label">同步来源</div>
            <div class="pc-detail-value">{{ syncByLabel(selectedPlatform.config?.pushed_by) }}</div>
          </div>
          <div class="pc-detail-item">
            <div class="pc-detail-label">Cookie 状态</div>
            <div class="pc-detail-value">
              <a-tag :color="selectedPlatform.config?.connected ? 'green' : 'red'" size="small">
                {{ selectedPlatform.config?.connected ? '已同步（加密存储）' : '未同步或已断开' }}
              </a-tag>
            </div>
          </div>
          <div class="pc-detail-item">
            <div class="pc-detail-label">登态心跳</div>
            <div class="pc-detail-value">
              <a-tag :color="verifyDisplayColor(selectedPlatform)" size="small">
                {{ verifyDisplayLabel(selectedPlatform) }}
              </a-tag>
              <span v-if="selectedPlatform.config?.last_verified_at" class="pc-detail-time">
                {{ timeAgo(selectedPlatform.config.last_verified_at) }}
              </span>
            </div>
          </div>
          <div class="pc-detail-item">
            <div class="pc-detail-label">业务接口</div>
            <div class="pc-detail-value">
              <a-tag :color="businessAccessColor(selectedPlatform)" size="small">
                {{ businessAccessLabel(selectedPlatform) }}
              </a-tag>
            </div>
          </div>
          <div class="pc-detail-item">
            <div class="pc-detail-label">部门</div>
            <div class="pc-detail-value">{{ selectedPlatform.department }}</div>
          </div>
          <div class="pc-detail-item pc-detail-wide">
            <div class="pc-detail-label">心跳详情</div>
            <div class="pc-detail-value pc-muted">{{ selectedPlatform.config?.verify_detail || '暂无。点击“验证登态”后会写入最近一次探测结果。' }}</div>
          </div>
          <div class="pc-detail-item pc-detail-wide">
            <div class="pc-detail-label">浏览器</div>
            <div class="pc-detail-value pc-muted">{{ selectedPlatform.config?.user_agent || '—' }}</div>
          </div>
        </div>
      </div>

      <CookiePoolDetail
        v-if="selectedPool"
        :platform="selectedPool.platform"
        :shop-id="selectedPool.shopId"
        :summary="activePoolSummary"
        @changed="loadCookiePools"
        @close="selectedPool = null"
      />
    </template>

    <a-modal
      v-model:visible="verifyCredentialModalVisible"
      title="选择要验证的 Cookie"
      :footer="false"
      :width="'min(90vw, 680px)'"
      :mask-closable="!verifyCredentialLoading && !verifyingCredentialId"
    >
      <div v-if="verifyCredentialContext" class="verify-modal-head">
        <span>{{ platformLabel(verifyCredentialContext.platform) }}</span>
        <span class="pc-mono">{{ verifyCredentialContext.shopId }}</span>
      </div>
      <a-spin :loading="verifyCredentialLoading">
        <div v-if="verifyCredentialList.length" class="verify-credential-list">
          <div
            v-for="credential in verifyCredentialList"
            :key="credential.id || credential.credential_alias"
            class="verify-credential-item"
            :class="{ disabled: !credentialActive(credential) }"
          >
            <div class="verify-credential-main">
              <div class="verify-credential-name">{{ credentialDisplayName(credential) }}</div>
              <div class="verify-credential-meta">{{ credentialMeta(credential) }}</div>
              <div class="verify-credential-tags">
                <a-tag :color="credentialActive(credential) ? 'green' : 'gray'" size="small">
                  {{ credentialActive(credential) ? '启用' : '停用' }}
                </a-tag>
                <a-tag :color="credentialVerifyColor(credential.verification_status)" size="small">
                  {{ credentialVerifyLabel(credential.verification_status) }}
                </a-tag>
                <span class="verify-credential-time">
                  {{ credential.last_verified_at ? `上次验证 ${timeAgo(credential.last_verified_at)}` : '未验证' }}
                </span>
              </div>
            </div>
            <a-button
              size="small"
              type="primary"
              :loading="verifyingCredentialId === credential.id"
              :disabled="!credential.id || !credentialActive(credential)"
              @click="runVerifyCredential(credential)"
            >
              验证
            </a-button>
          </div>
        </div>
        <a-empty v-else description="该 Cookie 池暂无可验证 credential" />
      </a-spin>
    </a-modal>

    <!-- API Key 管理弹窗 -->
    <a-modal v-model:visible="showApiKeyModal" title="Chrome 扩展 API Key 管理" :footer="false" :width="'min(90vw, 520px)'">
      <a-alert style="margin-bottom: 16px" type="info">
        <template #title>使用步骤</template>
        1. 创建个人 Connector Key 并复制一次性明文<br>
        2. 打开 Chrome 扩展弹窗 → 填写服务器地址和 Connector Key<br>
        3. 登录白名单平台后，扩展按 autoSync 配置同步 cookies
      </a-alert>
      <a-alert v-if="connectorKeyError" style="margin-bottom: 12px" type="warning">{{ connectorKeyError }}</a-alert>
      <div v-if="oneTimeConnectorKey" class="one-time-key">
        <div class="one-time-title">新 Connector Key（仅本次展示）</div>
        <div class="one-time-row">
          <a-input :model-value="oneTimeConnectorKey" readonly class="mono-input" />
          <a-button size="small" type="primary" @click="copyOneTimeConnectorKey">复制</a-button>
        </div>
      </div>
      <div class="connector-create-row">
        <a-input v-model="newDeviceLabel" placeholder="设备备注，例如：张三 Chrome" size="small" />
        <a-button size="small" type="primary" :loading="connectorKeyLoading" @click="createConnectorKey">
          创建个人 Key
        </a-button>
      </div>
      <div v-if="connectorKeys.length" class="connector-key-list">
        <div v-for="key in connectorKeys" :key="key.id || key.key_id || key.hash_prefix" class="connector-key-item">
          <div>
            <div class="connector-key-name">{{ connectorKeyName(key) }}</div>
            <div class="connector-key-meta">
              {{ connectorKeyTail(key) }} · {{ key.last_used_at ? `最近使用 ${timeAgo(key.last_used_at)}` : '未使用' }}
            </div>
          </div>
          <a-space>
            <a-tag :color="connectorKeyActive(key) ? 'green' : 'gray'" size="small">
              {{ connectorKeyActive(key) ? '有效' : '已停用' }}
            </a-tag>
            <a-button size="mini" :disabled="!connectorKeyActive(key)" @click="rotateConnectorKey(key)">轮换</a-button>
            <a-button size="mini" status="danger" :disabled="!connectorKeyActive(key)" @click="revokeConnectorKey(key)">撤销</a-button>
          </a-space>
        </div>
      </div>
      <a-divider style="margin: 12px 0" />
      <div style="margin-bottom: 12px">
        <div style="font-size: 12px; color: var(--ai-ink-3); margin-bottom: 6px">Legacy API Key</div>
        <div style="display: flex; gap: 8px; align-items: center">
          <a-input :model-value="apiKey" readonly class="mono-input" />
          <a-button size="small" type="primary" :disabled="!apiKey" @click="copyApiKey">复制</a-button>
        </div>
      </div>
      <div style="margin-bottom: 12px">
        <div style="font-size: 12px; color: var(--ai-ink-3); margin-bottom: 6px">服务器地址</div>
        <a-input :model-value="serverUrl" readonly class="mono-input" />
      </div>
      <div style="margin-bottom: 12px">
        <div style="font-size: 12px; color: var(--ai-ink-3); margin-bottom: 6px">已连接平台</div>
        <div style="display: flex; gap: 6px; flex-wrap: wrap">
          <a-tag v-for="ps in platformSources" :key="ps.id" :color="ps.config?.connected ? 'green' : 'gray'" size="small">
            {{ platformLabel(ps.config?.platform) }} {{ ps.config?.connected ? '✓' : '✗' }}
          </a-tag>
          <a-tag v-if="!platformSources.length" color="gray" size="small">暂无</a-tag>
        </div>
      </div>
      <a-divider style="margin: 12px 0" />
      <div style="display: flex; justify-content: space-between; align-items: center">
        <a-button size="small" status="danger" @click="regenerateApiKey">重新生成 Legacy Key</a-button>
        <span style="font-size: 11px; color: var(--ai-ink-4)">旧版全局 Key 仅做兼容；新扩展优先使用个人 Connector Key</span>
      </div>
    </a-modal>

    <!-- 远程同步弹窗 -->
    <a-modal
      v-model:visible="showRemoteSyncModal"
      title="从远程 SkillForge 同步"
      :width="'min(90vw, 560px)'"
      :footer="false"
      :mask-closable="!syncing"
    >
      <a-alert style="margin-bottom: 16px" type="info">
        从已部署的 SkillForge 一键拉取平台连接（cookies）、平台 API 注册表、MCP servers 配置。
        同步后 Docker 浏览器可直接打开平台网站，保持登录态。
      </a-alert>
      <a-form :model="syncForm" layout="vertical" size="small">
        <a-form-item label="源服务器地址" required>
          <a-input v-model="syncForm.remote_url" placeholder="http://127.0.0.1:8000" class="mono-input" />
        </a-form-item>
        <a-form-item label="源服务器 API Key" required>
          <a-input-password v-model="syncForm.api_key" placeholder="从源机器「API Key 管理」中复制" class="mono-input" />
        </a-form-item>
      </a-form>
      <a-button
        type="primary"
        long
        :loading="syncing"
        :disabled="!syncForm.remote_url || !syncForm.api_key"
        @click="doRemoteSync"
      >
        一键同步
      </a-button>

      <!-- 同步结果 -->
      <div v-if="syncResult" class="sync-result">
        <a-divider style="margin: 16px 0 12px" />
        <div class="sync-result-title">同步完成</div>
        <div class="sync-stats">
          <a-tag color="arcoblue">平台连接 {{ syncResult.imported?.platform_connections || 0 }}</a-tag>
          <a-tag color="arcoblue">平台 API {{ syncResult.imported?.platform_apis || 0 }}</a-tag>
          <a-tag color="arcoblue">MCP Servers {{ syncResult.imported?.mcp_servers || 0 }}</a-tag>
          <a-tag :color="syncResult.browser_synced ? 'green' : 'orange'">
            {{ syncResult.browser_synced ? 'cookies 已注入浏览器' : syncResult.browser_message }}
          </a-tag>
        </div>

        <div v-if="syncResult.connected_platforms?.length" class="sync-platforms">
          <div class="sync-platforms-title">已同步平台 — 点击在 Docker 浏览器中打开</div>
          <div class="sync-platforms-grid">
            <a-button
              v-for="p in syncResult.connected_platforms"
              :key="p.id"
              type="outline"
              size="small"
              @click="openInBrowser(p.id)"
            >
              <icon-launch :size="12" /> {{ p.name || p.id }}
            </a-button>
          </div>
        </div>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Message, Modal } from '@arco-design/web-vue'
import {
  connectorKeyApi as rawConnectorKeyApi,
  cookiePoolApi as rawCookiePoolApi,
  datasourceApi as rawDatasourceApi,
  browserApi as rawBrowserApi,
  type ConnectorKeyRow,
  type CookiePoolCredentialRow,
  type CookiePoolSummaryRow,
} from '@/api'
import { IconLink, IconSettings, IconDown, IconRight, IconDownload, IconUpload, IconSync, IconLaunch } from '@arco-design/web-vue/es/icon'
import { copyText } from '@/utils/clipboard'
import { bjtDateString, formatTime, relativeTime, toDate } from '@/utils/format'
import request from '@/api/request'
import CookiePoolDetail from './CookiePoolDetail.vue'

type PlatformSource = {
  id: string
  department?: string
  config?: Record<string, any>
}

const datasourceApi: any = rawDatasourceApi
const browserApi: any = rawBrowserApi
const connectorKeyApi: any = rawConnectorKeyApi
const cookiePoolApi: any = rawCookiePoolApi
const router = useRouter()

const sources = ref<PlatformSource[]>([])
const expandedPlatform = ref<string | null>(null)
const showApiKeyModal = ref(false)
const apiKey = ref('')
const serverUrl = ref(window.location.origin)
const verifyingId = ref<string | null>(null)
const autoVerifyingIds = ref<Set<string>>(new Set())
const autoVerifiedIds = new Set<string>()
const connectorKeys = ref<ConnectorKeyRow[]>([])
const connectorKeyLoading = ref(false)
const newDeviceLabel = ref('')
const oneTimeConnectorKey = ref('')
const connectorKeyError = ref('')
const poolSummaries = ref<CookiePoolSummaryRow[]>([])
const poolLoading = ref(false)
const poolLoadError = ref('')
const selectedPool = ref<{ platform: string; shopId: string } | null>(null)
const verifyCredentialModalVisible = ref(false)
const verifyCredentialLoading = ref(false)
const verifyCredentialList = ref<CookiePoolCredentialRow[]>([])
const verifyCredentialContext = ref<{ sourceId: string; platform: string; shopId: string } | null>(null)
const verifyingCredentialId = ref<string | null>(null)

const platformSources = computed(() => sources.value)
const selectedPlatform = computed(() => (
  platformSources.value.find((s) => s.id === expandedPlatform.value) || null
))
const selectedPlatformPool = computed(() => {
  const source = selectedPlatform.value
  if (!source) return null
  const summary = poolSummaryForPlatform(source)
  const platform = String(source.config?.platform || source.id.replace(/^platform-/, '') || '')
  const shopId = String(summary?.shop_id || source.config?.shop_id || source.config?.shopId || source.config?.shop_name || '')
  return platform && shopId ? { platform, shopId } : null
})
const activePoolSummary = computed(() => {
  const target = selectedPool.value
  if (!target) return null
  return poolSummaries.value.find(
    (item) => String(item.platform || '') === target.platform && String(item.shop_id || '') === target.shopId,
  ) || null
})

const VERIFY_LABELS: Record<string, string> = {
  valid: '有效',
  expired: '已失效',
  unknown: '待复核',
  unsupported: '不支持',
  checking: '判断中',
}
const VERIFY_COLORS: Record<string, string> = {
  valid: 'green',
  expired: 'red',
  unknown: 'orange',
  unsupported: 'gray',
  checking: 'arcoblue',
}

const PLATFORM_LABELS: Record<string, string> = {
  taobao: '淘宝/天猫千牛',
  sycm: '生意参谋',
  alimama: '阿里妈妈',
  douyin: '抖店/抖音',
  jd: '京东',
  pdd: '拼多多',
}

const PLATFORM_HOME_URLS: Record<string, string> = {
  'platform-taobao': 'https://myseller.taobao.com',
  'platform-sycm': 'https://sycm.taobao.com',
  'platform-alimama': 'https://one.alimama.com/index.html#!/report/account?rptType=account',
  'platform-douyin': 'https://compass.jinritemai.example.test',
  'platform-jd': 'https://shop.jd.com',
  'platform-pdd': 'https://mms.pinduoduo.com',
}
const LOGIN_PROBE_SOURCE_IDS = new Set(['platform-sycm', 'platform-alimama'])
const AUTO_VERIFY_STALE_MS = 5 * 60 * 1000

function platformLabel(p: string | undefined) {
  return PLATFORM_LABELS[p || ''] || p || '未知'
}
function verifyLabel(status?: string) {
  return VERIFY_LABELS[status || ''] || '未检测'
}
function verifyColor(status?: string) {
  return VERIFY_COLORS[status || ''] || 'gray'
}
function isExplicitExpired(config?: Record<string, any>) {
  if (!config || config.verify_status !== 'expired') return false
  const detail = String(config.verify_detail || '').toLowerCase()
  return (
    detail.includes('401') ||
    detail.includes('403') ||
    detail.includes('not_login') ||
    detail.includes('fail_biz_not_login') ||
    detail.includes('passport') ||
    detail.includes('login.taobao') ||
    detail.includes('未登录')
  )
}
function displayVerifyStatus(config?: Record<string, any>) {
  const status = config?.verify_status
  if (status === 'expired' && !isExplicitExpired(config)) return 'unknown'
  return status
}
function platformVerifyStatus(ps: PlatformSource) {
  if (isVerifyingSource(ps.id)) return 'checking'
  const status = displayVerifyStatus(ps.config)
  if (!status && ps.config?.connected && !LOGIN_PROBE_SOURCE_IDS.has(ps.id)) return 'unsupported'
  return status
}
function verifyDisplayLabel(ps: PlatformSource) {
  return verifyLabel(platformVerifyStatus(ps))
}
function verifyDisplayColor(ps: PlatformSource) {
  return verifyColor(platformVerifyStatus(ps))
}
function verifyTooltip(config?: Record<string, any>) {
  if (!config) return ''
  const parts: string[] = []
  if (config.verify_status === 'expired' && !isExplicitExpired(config)) {
    parts.push('原始心跳失败但未命中登录页/401/403，按待复核展示')
  }
  if (config.last_verified_at) parts.push(`上次检测：${formatServerTime(config.last_verified_at)}`)
  if (config.verify_detail) parts.push(config.verify_detail)
  return parts.join(' · ') || '尚未检测'
}
function businessAccessLabel(ps: PlatformSource) {
  if (!ps.config?.connected) return '不可采集'
  const status = platformVerifyStatus(ps)
  if (status === 'checking') return '判断中'
  if (status === 'valid') return '可采集'
  if (status === 'expired') return '需重登'
  if (status === 'unknown') return '待复核'
  if (status === 'unsupported') return '未接入'
  return '待验证'
}
function businessAccessColor(ps: PlatformSource) {
  if (!ps.config?.connected) return 'gray'
  const status = platformVerifyStatus(ps)
  if (status === 'valid') return 'green'
  if (status === 'expired') return 'red'
  if (status === 'unknown') return 'orange'
  if (status === 'checking') return 'arcoblue'
  return 'arcoblue'
}
function connectionSummary(ps: PlatformSource) {
  if (!ps.config?.connected) return '扩展还没有上报 Cookie，Skill 执行时无法读取平台凭证。'
  const status = platformVerifyStatus(ps)
  if (status === 'checking') return '正在自动判断登录态和业务接口可用性。'
  if (status === 'valid') return 'Cookie 已同步且最近登态验证通过，可以作为采集数据源。'
  if (status === 'expired') return 'Cookie 已同步，但最近登态验证失败，需要重新登录并同步。'
  if (status === 'unknown') return 'Cookie 已同步，但心跳没有明确结论；可先点验证登态复核。'
  if (status === 'unsupported') return 'Cookie 已同步；该平台暂未接入自动登态心跳。'
  return 'Cookie 已同步，尚未执行登态心跳；建议先验证后再跑采集。'
}
function isVerifyingSource(sourceId: string) {
  return verifyingId.value === sourceId || autoVerifyingIds.value.has(sourceId)
}
function shouldAutoVerify(ps: PlatformSource) {
  if (!ps.config?.connected) return false
  if (!LOGIN_PROBE_SOURCE_IDS.has(ps.id)) return false
  if (autoVerifiedIds.has(ps.id)) return false
  if (isVerifyingSource(ps.id)) return false
  const pool = poolSummaryForPlatform(ps)
  if (Number(pool?.active_count || 0) > 1) return false

  const lastMs = ps.config?.last_verified_at ? (toDate(ps.config.last_verified_at)?.getTime() || 0) : 0
  if (!Number.isFinite(lastMs) || lastMs <= 0) return true
  return Date.now() - lastMs > AUTO_VERIFY_STALE_MS
}
function setAutoVerifying(sourceId: string, active: boolean) {
  const next = new Set(autoVerifyingIds.value)
  if (active) next.add(sourceId)
  else next.delete(sourceId)
  autoVerifyingIds.value = next
}
function syncByLabel(pushedBy?: string) {
  if (!pushedBy) return '—'
  if (pushedBy === 'chrome_extension') return 'Chrome 扩展自动同步'
  if (pushedBy.startsWith('remote_sync:')) return '远程同步'
  return pushedBy
}
function timeAgo(isoStr: string | undefined) {
  if (!isoStr) return '—'
  return relativeTime(isoStr)
}
function formatServerTime(isoStr: string | undefined) {
  return toDate(isoStr) ? formatTime(isoStr) : '—'
}

function normalizeArrayResponse<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[]
  if (value && typeof value === 'object' && Array.isArray((value as { items?: unknown }).items)) {
    return (value as { items: T[] }).items
  }
  return []
}

function normalizeCredentialResponse(value: unknown): CookiePoolCredentialRow[] {
  if (Array.isArray(value)) return value as CookiePoolCredentialRow[]
  if (value && typeof value === 'object') {
    const obj = value as { credentials?: unknown; items?: unknown }
    if (Array.isArray(obj.credentials)) return obj.credentials as CookiePoolCredentialRow[]
    if (Array.isArray(obj.items)) return obj.items as CookiePoolCredentialRow[]
  }
  return []
}

function formatRatio(value: unknown) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  const normalized = num > 1 ? num / 100 : num
  return `${Math.round(normalized * 100)}%`
}

function formatHealth(value: unknown) {
  const num = Number(value)
  return Number.isFinite(num) ? Math.round(num) : 0
}

function poolKey(row: CookiePoolSummaryRow) {
  return `${row.platform || 'unknown'}:${row.shop_id || 'unknown'}`
}

function poolLabel(row: CookiePoolSummaryRow) {
  const shop = row.shop_name || row.shop_id || '未命名店铺'
  return `${platformLabel(row.platform)} · ${shop}`
}

function openPool(row: CookiePoolSummaryRow) {
  const platform = String(row.platform || '')
  const shopId = String(row.shop_id || '')
  if (!platform || !shopId) return
  router.push(`/admin/connections/${encodeURIComponent(platform)}/${encodeURIComponent(shopId)}`)
}

function openSelectedPlatformPool() {
  if (!selectedPlatformPool.value) return
  router.push(
    `/admin/connections/${encodeURIComponent(selectedPlatformPool.value.platform)}/${encodeURIComponent(selectedPlatformPool.value.shopId)}`,
  )
}

function poolSummaryForPlatform(source: PlatformSource) {
  const platform = String(source.config?.platform || source.id.replace(/^platform-/, ''))
  const shopId = String(source.config?.shop_id || source.config?.shopId || '')
  const matches = poolSummaries.value.filter((item) => String(item.platform || '') === platform)
  if (!matches.length) return undefined
  if (!shopId) return matches[0]
  return matches.find((item) => String(item.shop_id || '') === shopId) || (matches.length === 1 ? matches[0] : undefined)
}

function platformSyncedAt(source: PlatformSource | null | undefined) {
  if (!source) return ''
  const poolTime = poolSummaryForPlatform(source)?.latest_sync_at
  return String(poolTime || source.config?.pushed_at || '')
}

function connectorKeyName(row: ConnectorKeyRow) {
  return row.device_label || row.key_id || row.id || row.hash_prefix || '未命名 Key'
}

function connectorKeyTail(row: ConnectorKeyRow) {
  return row.hash_prefix || row.key_hash || row.id || row.key_id || '—'
}

function connectorKeyActive(row: ConnectorKeyRow) {
  return row.is_active !== false && !row.revoked_at
}

function connectorPlaintext(row: ConnectorKeyRow | Record<string, any> | undefined) {
  return String(row?.plaintext_key || row?.api_key || '')
}

function credentialActive(row: CookiePoolCredentialRow) {
  return row.is_active !== false && row.status !== 'disabled'
}

function credentialDisplayName(row: CookiePoolCredentialRow) {
  return row.device_label || row.account_login || row.owner_name || row.credential_alias || row.id || '未命名 credential'
}

function credentialMeta(row: CookiePoolCredentialRow) {
  const parts = [
    row.account_login && row.account_login !== row.device_label ? row.account_login : '',
    row.owner_name || row.owner_user_id || '',
    row.last_used_at ? `最近使用 ${timeAgo(row.last_used_at)}` : '',
  ].filter(Boolean)
  return parts.join(' · ') || row.credential_alias || '-'
}

function credentialVerifyLabel(value?: string) {
  const map: Record<string, string> = {
    active: '有效',
    valid: '有效',
    passed: '有效',
    failed: '失败',
    expired: '已失效',
    pending: '待验证',
    unknown: '待复核',
    disabled: '停用',
  }
  return map[value || ''] || value || '未验证'
}

function credentialVerifyColor(value?: string) {
  if (value === 'active' || value === 'valid' || value === 'passed') return 'green'
  if (value === 'failed' || value === 'expired') return 'red'
  if (value === 'pending' || value === 'unknown') return 'orange'
  return 'gray'
}

// ── 远程同步 ──
const showRemoteSyncModal = ref(false)
const syncing = ref(false)
const syncForm = ref({ remote_url: '', api_key: '' })
const syncResult = ref<Record<string, any> | null>(null)

async function doRemoteSync() {
  syncing.value = true
  syncResult.value = null
  try {
    const res = await request.post<Record<string, any>>('/data-sources/pull-remote', {
      remote_url: syncForm.value.remote_url.trim().replace(/\/+$/, ''),
      api_key: syncForm.value.api_key.trim(),
    })
    syncResult.value = res
    Message.success('同步完成')
    await loadPlatforms()
    await loadCookiePools()
  } catch (e: any) {
    Message.error(e?._message || '同步失败，请检查地址和 API Key')
  } finally {
    syncing.value = false
  }
}

// ── 在 Docker 浏览器中打开 ──
async function openInBrowser(sourceId: string) {
  const url = PLATFORM_HOME_URLS[sourceId]
  if (!url) return
  try {
    Message.loading({ content: '正在同步 cookies 到浏览器...', id: 'browser-open' })
    await request.post('/browser/sync-cookies')
    await request.post('/browser/navigate', { url })
    Message.success({ content: '已在 Docker 浏览器中打开', id: 'browser-open' })
  } catch (e: any) {
    const msg = e?._message || ''
    if (msg.includes('未运行') || msg.includes('stopped') || msg.includes('CDP')) {
      Message.warning({ content: '浏览器未运行，请先在「浏览器连接」中启动', id: 'browser-open' })
    } else {
      Message.error({ content: msg || '打开失败', id: 'browser-open' })
    }
  }
}

async function loadPlatforms() {
  try {
    const res = await datasourceApi.list({ source_type: 'platform_cookies' })
    const all = Array.isArray(res) ? res : (res.items || [])
    sources.value = all.filter((s: PlatformSource) => s.config?.platform)
  } catch (e: any) {
    Message.error(e?._message || '加载失败')
  }
}

async function loadCookiePools() {
  poolLoading.value = true
  poolLoadError.value = ''
  try {
    const res = await cookiePoolApi.summary()
    poolSummaries.value = normalizeArrayResponse<CookiePoolSummaryRow>(res)
  } catch (e: any) {
    poolSummaries.value = sources.value
      .filter((source) => source.config?.connected)
      .map((source) => ({
        platform: source.config?.platform || source.id.replace(/^platform-/, ''),
        shop_id: source.config?.shop_id || source.config?.shopId || source.id,
        active_count: 1,
        standby_count: 0,
        total_count: 1,
        verify_pass_rate: displayVerifyStatus(source.config) === 'valid' ? 1 : 0,
        mixed_credentials_ratio: 0,
        avg_health: displayVerifyStatus(source.config) === 'valid' ? 90 : 50,
        latest_sync_at: source.config?.pushed_at,
      }))
    poolLoadError.value = e?._message || 'Cookie 池接口未就绪，已按 legacy 平台连接生成摘要。'
  } finally {
    poolLoading.value = false
  }
}

async function autoVerifyStalePlatforms(platforms: PlatformSource[]) {
  const targets = platforms.filter(shouldAutoVerify)
  if (!targets.length) return

  for (const ps of targets) {
    autoVerifiedIds.add(ps.id)
    setAutoVerifying(ps.id, true)
    try {
      const res = await browserApi.verifyLogin(ps.id)
      applyVerifyResult(ps.id, res)
    } catch {
      // 自动判断静默失败，保留页面现有状态；手动按钮仍会展示错误详情。
    } finally {
      setAutoVerifying(ps.id, false)
    }
  }
  try {
    const res = await datasourceApi.list({ source_type: 'platform_cookies' })
    const all = Array.isArray(res) ? res : (res.items || [])
    sources.value = all.filter((s: PlatformSource) => s.config?.platform)
  } catch {}
}

function applyVerifyResult(sourceId: string, result: Record<string, any> | undefined) {
  if (!result?.status) return
  sources.value = sources.value.map((source) => {
    if (source.id !== sourceId) return source
    return {
      ...source,
      config: {
        ...(source.config || {}),
        verify_status: result.status,
        verify_detail: result.detail || result.current_url || '',
        last_verified_at: new Date().toISOString(),
      },
    }
  })
}

async function loadApiKey() {
  connectorKeyLoading.value = true
  connectorKeyError.value = ''
  try {
    const listRes = await connectorKeyApi.list()
    connectorKeys.value = normalizeArrayResponse<ConnectorKeyRow>(listRes)
  } catch (e: any) {
    connectorKeys.value = []
    connectorKeyError.value = e?._message || 'Connector Key 列表接口未就绪，使用 legacy API Key。'
  }
  try {
    const res = await connectorKeyApi.getLegacyKey()
    apiKey.value = res.api_key || ''
  } catch {
    apiKey.value = ''
  } finally {
    connectorKeyLoading.value = false
  }
}

async function copyApiKey() {
  const ok = await copyText(apiKey.value)
  if (ok) Message.success('已复制')
  else Message.warning('复制失败，请手动选中复制')
}

async function regenerateApiKey() {
  try {
    const res = await connectorKeyApi.rotateLegacyKey()
    apiKey.value = res.api_key
    Message.success('API Key 已重新生成，请更新 Chrome 扩展配置')
  } catch (e: any) {
    Message.error(e?._message || '生成失败')
  }
}

async function createConnectorKey() {
  connectorKeyLoading.value = true
  oneTimeConnectorKey.value = ''
  try {
    const res = await connectorKeyApi.create({
      device_label: newDeviceLabel.value.trim() || 'Chrome 扩展',
    })
    oneTimeConnectorKey.value = connectorPlaintext(res)
    newDeviceLabel.value = ''
    Message.success('Connector Key 已创建')
    await loadApiKey()
  } catch (e: any) {
    Message.error(e?._message || '创建失败')
  } finally {
    connectorKeyLoading.value = false
  }
}

async function rotateConnectorKey(row: ConnectorKeyRow) {
  const keyId = row.id || row.key_id
  if (!keyId) return
  connectorKeyLoading.value = true
  oneTimeConnectorKey.value = ''
  try {
    const res = await connectorKeyApi.rotate(keyId)
    oneTimeConnectorKey.value = connectorPlaintext(res)
    Message.success('已轮换，旧 Key 立即失效')
    await loadApiKey()
  } catch (e: any) {
    Message.error(e?._message || '轮换失败')
  } finally {
    connectorKeyLoading.value = false
  }
}

async function revokeConnectorKey(row: ConnectorKeyRow) {
  const keyId = row.id || row.key_id
  if (!keyId) return
  try {
    await connectorKeyApi.revoke(keyId)
    Message.success('已撤销')
    await loadApiKey()
  } catch (e: any) {
    Message.error(e?._message || '撤销失败')
  }
}

async function copyOneTimeConnectorKey() {
  const ok = await copyText(oneTimeConnectorKey.value)
  if (ok) Message.success('已复制')
  else Message.warning('复制失败，请手动选中复制')
}

async function testCookies(sourceId: string) {
  try {
    const res = await datasourceApi.getCookies(sourceId)
    if (res.cookies) {
      const count = res.cookies.split(';').length
      Message.success(`连接有效，共 ${count} 个 cookies`)
    } else {
      Message.warning('cookies 为空或已过期')
    }
  } catch (e: any) {
    Message.error(e?._message || '连接测试失败')
  }
}

async function runVerifyLogin(sourceId: string) {
  const source = platformSources.value.find((item) => item.id === sourceId)
  const pool = source ? poolSummaryForPlatform(source) : null
  if (source && pool) {
    await openVerifyCredentialModal(source, pool)
    return
  }

  verifyingId.value = sourceId
  try {
    const res = await browserApi.verifyLogin(sourceId)
    if (res?.status === 'unsupported') {
      Message.warning('该平台暂不支持登态心跳')
      return
    }
    if (res?.verified) {
      Message.success(`登态有效：${res.detail || res.current_url || ''}`)
    } else if (res?.status === 'expired') {
      Message.error(`登态已失效：${res.detail || '请重新推送 cookies'}`)
    } else {
      Message.warning(`登态未知：${res?.detail || '浏览器可能未运行'}`)
    }
    await loadPlatforms()
    await loadCookiePools()
  } catch (e: any) {
    Message.error(e?._message || '验证失败')
  } finally {
    verifyingId.value = null
  }
}

async function openVerifyCredentialModal(source: PlatformSource, pool: CookiePoolSummaryRow) {
  const platform = String(pool.platform || source.config?.platform || source.id.replace(/^platform-/, '') || '')
  const shopId = String(pool.shop_id || source.config?.shop_id || source.config?.shopId || '')
  if (!platform || !shopId) {
    Message.warning('没有可验证的 Cookie 池')
    return
  }
  verifyCredentialContext.value = { sourceId: source.id, platform, shopId }
  verifyCredentialModalVisible.value = true
  await loadVerifyCredentialOptions()
}

async function loadVerifyCredentialOptions() {
  const context = verifyCredentialContext.value
  if (!context) return
  verifyCredentialLoading.value = true
  try {
    const res = await cookiePoolApi.detail(context.platform, context.shopId, { include_audit: 0 })
    verifyCredentialList.value = normalizeCredentialResponse(res).sort((a, b) => {
      const activeDelta = Number(credentialActive(b)) - Number(credentialActive(a))
      if (activeDelta) return activeDelta
      return (toDate(b.last_verified_at || b.last_used_at || '')?.getTime() || 0) -
        (toDate(a.last_verified_at || a.last_used_at || '')?.getTime() || 0)
    })
  } catch (e: any) {
    verifyCredentialList.value = []
    Message.error(e?._message || '加载 Cookie 凭证失败')
  } finally {
    verifyCredentialLoading.value = false
  }
}

async function runVerifyCredential(record: CookiePoolCredentialRow) {
  const credentialId = record.id
  const context = verifyCredentialContext.value
  if (!credentialId || !context) return
  verifyingCredentialId.value = credentialId
  verifyingId.value = context.sourceId
  try {
    const res = await cookiePoolApi.verifyCredential(credentialId)
    if (res?.verified) {
      Message.success(`登态有效：${res.detail || credentialDisplayName(record)}`)
    } else if (res?.status === 'expired') {
      Message.error(`登态已失效：${res.detail || '请重新推送 cookies'}`)
    } else if (res?.status === 'disabled') {
      Message.warning('该 Cookie 已停用')
    } else {
      Message.warning(`登态未知：${res?.detail || '浏览器可能未运行'}`)
    }
    await loadPlatforms()
    await loadCookiePools()
    await loadVerifyCredentialOptions()
  } catch (e: any) {
    Message.error(e?._message || '验证失败')
  } finally {
    verifyingCredentialId.value = null
    verifyingId.value = null
  }
}

function disconnectPlatform(sourceId: string) {
  Modal.confirm({
    title: '确认断开',
    content: '断开后需要重新授权连接',
    onOk: async () => {
      try {
        await datasourceApi.update(sourceId, { config: { connected: false } })
        Message.success('已断开')
        expandedPlatform.value = null
        await loadPlatforms()
        await loadCookiePools()
      } catch (e: any) {
        Message.error(e?._message || '操作失败')
      }
    },
  })
}

const importFileRef = ref<HTMLInputElement>()

function triggerImport() {
  importFileRef.value?.click()
}

async function exportConnections() {
  try {
    const res = await request.get<{ export_type: string; exported_at: string; total: number; data: unknown[] }>(
      '/data-sources/platform-connections/export',
    )
    const blob = new Blob([JSON.stringify(res, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `platform-connections-${bjtDateString(Date.now())}.json`
    a.click()
    URL.revokeObjectURL(url)
    Message.success(`已导出 ${res.total} 个连接`)
  } catch (e) {
    Message.error(String((e as { _message?: string })?._message || '导出失败'))
  }
}

async function handleImportFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    const text = await file.text()
    const json = JSON.parse(text)
    if (json.export_type !== 'platform_connections') {
      Message.error('文件格式不匹配，需要 platform_connections 导出文件')
      return
    }
    const res = await request.post<{ message: string; total: number }>(
      '/data-sources/platform-connections/import', json,
    )
    Message.success(res.message)
    await loadPlatforms()
    await loadCookiePools()
  } catch (e) {
    Message.error(String((e as { _message?: string })?._message || '导入失败'))
  } finally {
    if (importFileRef.value) importFileRef.value.value = ''
  }
}

onMounted(() => {
  loadPlatforms().then(async () => {
    await loadCookiePools()
    void autoVerifyStalePlatforms(sources.value)
  })
  loadApiKey()
})
</script>

<style scoped>
.platform-section {
  margin-bottom: 16px;
}
.platform-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.platform-section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.platform-hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--ai-ink-3);
  margin-left: 8px;
}
.platform-empty {
  text-align: center;
  padding: 32px;
  background: var(--ai-surface-2);
  border-radius: 8px;
  border: 1px dashed var(--ai-border);
}
.platform-empty p {
  font-size: 12px;
  color: var(--ai-ink-3);
  margin: 8px 0 12px;
}
.platform-status-note {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 10px 12px;
  margin-bottom: 12px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
}
.note-title {
  flex: 0 0 auto;
  font-size: 12px;
  font-weight: 700;
  color: var(--ai-ink-1);
}
.note-text {
  min-width: 0;
  font-size: 12px;
  color: var(--ai-ink-2);
}
.pool-summary-panel {
  margin-bottom: 12px;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
}
.pool-summary-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 10px;
}
.pool-summary-title {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 800;
}
.pool-summary-subtitle {
  margin-top: 2px;
  color: var(--ai-ink-3);
  font-size: 12px;
}
.pool-summary-alert {
  margin-bottom: 10px;
}
.pool-summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 8px;
}
.pool-summary-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
  color: inherit;
  cursor: pointer;
  text-align: left;
  transition: border-color 0.12s, box-shadow 0.12s;
}
.pool-summary-card:hover,
.pool-summary-card.active {
  border-color: rgb(var(--arcoblue-5));
  box-shadow: 0 2px 8px rgba(22, 93, 255, 0.08);
}
.pool-card-name {
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 800;
}
.pool-card-stats,
.pool-card-meta {
  color: var(--ai-ink-3);
  font-size: 11px;
}
.platform-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  align-items: start;
}
.platform-card {
  min-height: 168px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
  padding: 14px 16px;
  cursor: pointer;
  transition: all 0.15s;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: hidden;
}
.platform-card:hover {
  border-color: rgb(var(--arcoblue-4));
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
}
.platform-connected {
  border-left: 3px solid rgb(var(--green-6));
}
.platform-expanded {
  border-color: rgb(var(--arcoblue-5));
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}
.pc-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.pc-title-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.pc-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.pc-dot-on {
  background: rgb(var(--green-6));
  box-shadow: 0 0 4px rgba(var(--green-6), 0.3);
}
.pc-dot-off {
  background: var(--ai-ink-4);
}
.pc-name {
  font-weight: 600;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.pc-summary {
  color: var(--ai-ink-2);
  font-size: 12px;
  line-height: 1.5;
  min-height: 36px;
}
.pc-meta {
  margin-top: auto;
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: var(--ai-ink-3);
  font-size: 11px;
}
.pc-time {
  font-size: 11px;
  color: var(--ai-ink-3);
  margin-left: auto;
}
.pc-chevron {
  color: var(--ai-ink-4);
  flex-shrink: 0;
}
.pc-mono {
  font-family: var(--ai-font-mono);
  font-size: 11px;
}
.mono-input {
  font-family: var(--ai-font-mono);
  font-size: 12px;
}
.one-time-key {
  margin-bottom: 12px;
  padding: 10px 12px;
  border: 1px solid rgba(var(--green-6), 0.25);
  border-radius: 8px;
  background: rgba(var(--green-6), 0.06);
}
.one-time-title {
  margin-bottom: 6px;
  color: rgb(var(--green-7));
  font-size: 12px;
  font-weight: 800;
}
.one-time-row,
.connector-create-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.connector-create-row {
  margin-bottom: 12px;
}
.connector-key-list {
  display: grid;
  gap: 8px;
  margin-bottom: 12px;
}
.connector-key-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
}
.connector-key-name {
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 800;
}
.connector-key-meta {
  margin-top: 2px;
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
  font-size: 11px;
}

.verify-modal-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  color: var(--ai-ink-2);
  font-size: 12px;
}

.verify-credential-list {
  display: grid;
  gap: 8px;
}

.verify-credential-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
}

.verify-credential-item.disabled {
  opacity: 0.62;
}

.verify-credential-main {
  min-width: 0;
}

.verify-credential-name {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 800;
}

.verify-credential-meta {
  margin-top: 2px;
  color: var(--ai-ink-3);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.verify-credential-tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 7px;
}

.verify-credential-time {
  color: var(--ai-ink-3);
  font-size: 11px;
}

.pc-detail-panel {
  margin-top: 12px;
  padding: 14px 16px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface-2);
  cursor: default;
}
.pc-detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--ai-border);
  margin-bottom: 12px;
}
.pc-detail-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--ai-ink-1);
}
.pc-detail-subtitle {
  margin-top: 2px;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.pc-detail-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px 16px;
}
.pc-detail-label {
  font-size: 11px;
  color: var(--ai-ink-3);
  margin-bottom: 2px;
}
.pc-detail-value {
  font-size: 12px;
  color: var(--ai-ink-1);
  word-break: break-word;
}
.pc-detail-wide {
  grid-column: span 2;
}
.pc-detail-time {
  margin-left: 6px;
  font-size: 11px;
  color: var(--ai-ink-3);
}
.pc-muted {
  font-size: 11px;
  color: var(--ai-ink-3);
}
.pc-detail-actions {
  margin-top: 12px;
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  padding-top: 10px;
  /* webkit 1px dashed 渲染成 solid，用 background gradient pattern 代替 */
  background-image: linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%);
  background-position: top;
  background-size: 6px 1px;
  background-repeat: repeat-x;
  flex-wrap: wrap;
}

@media (max-width: 720px) {
  .platform-section-header,
  .platform-status-note,
  .pc-detail-head {
    align-items: stretch;
    flex-direction: column;
  }
  .platform-grid {
    grid-template-columns: 1fr;
  }
  .pc-detail-grid {
    grid-template-columns: 1fr;
  }
  .pc-detail-wide {
    grid-column: span 1;
  }
}

/* 同步结果 */
.sync-result-title {
  font-weight: 700;
  font-size: 14px;
  margin-bottom: 8px;
  color: rgb(var(--green-6));
}
.sync-stats {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.sync-platforms-title {
  font-size: 12px;
  color: var(--ai-ink-2);
  margin-bottom: 8px;
  font-weight: 600;
}
.sync-platforms-grid {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
