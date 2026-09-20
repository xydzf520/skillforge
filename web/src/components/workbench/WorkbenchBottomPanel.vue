<template>
  <div class="wb-bottom-panel">
    <!-- Tab 栏 -->
    <div class="bp-tabs">
      <button
        v-for="tab in tabs" :key="tab.key"
        class="bp-tab" :class="{ active: activeTab === tab.key, 'bp-tab-ai': tab.ai }"
        @click="$emit('change-tab', tab.key)"
      >
        <component :is="tab.icon" :size="13" />
        <span>{{ tab.label }}</span>
        <span v-if="tab.badge" class="bp-badge" :class="tab.badgeType">{{ tab.badge }}</span>
      </button>
      <span class="bp-spacer" />
      <button class="bp-close" :title="maximized ? '还原' : '最大化'" @click="$emit('toggle-maximized')">
        <icon-fullscreen-exit :size="13" v-if="maximized" />
        <icon-fullscreen :size="13" v-else />
      </button>
      <button class="bp-close" @click="$emit('close')">
        <icon-close :size="13" />
      </button>
    </div>

    <!-- 面板内容 -->
    <div class="bp-content">
      <!-- Validation -->
      <div v-show="activeTab === 'validation'" class="bp-pane">
        <WorkbenchValidationPanel
          v-if="validation"
          :report="validation"
        />
        <div v-else class="bp-empty">
          <icon-check-circle :size="24" />
          <div class="bp-empty-title">暂无校验结果</div>
          <div class="bp-empty-desc">点击顶栏「验证」或 {{ metaKey }}{{ shiftKey }}V 运行校验</div>
        </div>
      </div>

      <!-- Test（含决策链可视化 M5） -->
      <div v-show="activeTab === 'test'" class="bp-pane">
        <div class="bp-toolbar">
          <a-button size="mini" class="ai-btn" :loading="testRunning" @click="$emit('run-test')">
            <icon-play-arrow :size="13" style="margin-right:3px" />运行测试
          </a-button>
        </div>
        <div v-if="testResult" class="bp-result">
          <div v-for="(tc, i) in (testResult.results || testResult.test_results || [])" :key="i" class="test-item">
            <div class="test-header" @click="$emit('highlight-test', tc)" style="cursor:pointer">
              <a-tag :color="tc.passed ? 'green' : 'red'" size="small">{{ tc.passed ? 'PASS' : 'FAIL' }}</a-tag>
              <span class="test-name">{{ getTestName(tc, i) }}</span>
              <span v-if="tc.duration_ms" class="test-msg">{{ tc.duration_ms }}ms</span>
              <span class="test-msg" style="margin-left:auto;opacity:.5">点击高亮路径</span>
            </div>
            <!-- 决策链 -->
            <div v-if="tc.decision_chain?.length || tc.decisions?.length" class="test-chain">
              <span v-for="(d, j) in (tc.decision_chain || tc.decisions || [])" :key="j" class="chain-step">
                <a-tag size="small" color="arcoblue">{{ d.step_id || d.step }}</a-tag>
                <span class="chain-arrow">→</span>
                <span class="chain-cond">{{ d.condition }}</span>
                <span class="chain-arrow">→</span>
                <a-tag size="small" :color="d.conclusion === '绿灯' ? 'green' : d.conclusion === '红灯' ? 'red' : 'orange'">{{ d.conclusion }}</a-tag>
              </span>
            </div>
            <div v-if="tc.output" class="test-output-wrapper" style="margin-top:4px">
              <div class="test-output-tools">
                <a-button size="mini" type="text" @click="copyText(tc.output)">
                  <icon-copy :size="11" style="margin-right:3px" />复制
                </a-button>
                <a-button v-if="isLongOutput(tc.output)" size="mini" type="text" @click="toggleExpand(Number(i))">
                  {{ expandedItems.has(Number(i)) ? '折叠' : '展开全部' }}
                </a-button>
              </div>
              <pre class="bp-json" :class="{ 'bp-json-collapsed': isLongOutput(tc.output) && !expandedItems.has(Number(i)) }">{{ formatOutput(tc.output) }}</pre>
            </div>
          </div>
        </div>
        <div v-else class="bp-empty">
          <icon-experiment :size="24" />
          <div class="bp-empty-title">暂无测试结果</div>
          <div class="bp-empty-desc">点击「运行测试」执行 SKILL.md 中定义的 test_cases</div>
        </div>
      </div>

      <!-- Sandbox -->
      <div v-show="activeTab === 'sandbox'" class="bp-pane">
        <div class="bp-toolbar">
          <a-button size="mini" @click="formatSandbox">格式化</a-button>
          <a-button size="mini" @click="insertTemplate">示例参数</a-button>
          <span v-if="sandboxJsonError" class="sandbox-err">⚠ {{ sandboxJsonError }}</span>
          <span class="bp-spacer" />
          <a-button
            size="mini" class="ai-btn"
            :loading="sandboxRunning"
            :disabled="!!sandboxJsonError"
            @click="$emit('run-sandbox', sandboxParams)"
          >
            <icon-thunderbolt :size="13" style="margin-right:3px" />执行
          </a-button>
        </div>
        <a-textarea
          v-model="sandboxParams"
          :auto-size="{ minRows: 3, maxRows: 10 }"
          placeholder='{"roi": 1.5}'
          style="font-size:12px;font-family:var(--sf-font-mono, monospace)"
        />
        <div v-if="sandboxResult" class="bp-result" style="margin-top:10px">
          <div class="runtime-output-summary">
            <div class="runtime-summary-head">
              <span class="runtime-summary-title">运行输出</span>
              <span class="runtime-summary-count">待办 {{ sandboxTodoItems.length }}</span>
              <span class="runtime-summary-count">报告 {{ sandboxReportItems.length }}</span>
            </div>
            <div v-if="sandboxTodoItems.length" class="runtime-section">
              <div class="runtime-section-title">会进入收件-待办</div>
              <div class="runtime-todo-list">
                <div v-for="(todo, idx) in sandboxTodoItems" :key="idx" class="runtime-todo">
                  <div class="runtime-todo-main">
                    <a-tag size="small" :color="todoKindColor(todo.kind)">{{ todoKindLabel(todo.kind) }}</a-tag>
                    <strong>{{ todo.title || `待办 ${idx + 1}` }}</strong>
                  </div>
                  <div v-if="todo.summary" class="runtime-muted">{{ todo.summary }}</div>
                  <div class="runtime-meta-row">
                    <span v-if="todo.reviewer_role">角色 {{ todo.reviewer_role }}</span>
                    <span v-if="todo.reviewers?.length">处理人 {{ todo.reviewers.join(', ') }}</span>
                    <span v-if="todo.sla_hours">SLA {{ todo.sla_hours }}h</span>
                    <span v-if="todo.tasks?.length">任务 {{ todo.tasks.length }}</span>
                  </div>
                  <div v-if="todo.tasks?.length" class="runtime-task-list">
                    <div v-for="(task, taskIdx) in todo.tasks.slice(0, 3)" :key="taskIdx" class="runtime-task">
                      <span class="runtime-task-index">{{ taskIdx + 1 }}</span>
                      <span class="runtime-task-content">{{ task.content || '-' }}</span>
                      <span v-if="task.executor" class="runtime-task-meta">{{ task.executor }}</span>
                      <span v-if="task.deadline" class="runtime-task-meta">{{ task.deadline }}</span>
                    </div>
                    <div v-if="todo.tasks.length > 3" class="runtime-muted">还有 {{ todo.tasks.length - 3 }} 条任务</div>
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="runtime-empty-line">
              本次输出未包含 <code>todos</code>，不会进入收件-待办。
            </div>
            <div v-if="sandboxReportItems.length" class="runtime-section">
              <div class="runtime-section-title">会进入收件-报告</div>
              <div class="runtime-report-list">
                <div v-for="(report, idx) in sandboxReportItems" :key="idx" class="runtime-report">
                  <strong>{{ report.title || `报告 ${idx + 1}` }}</strong>
                  <span v-if="report.channel" class="runtime-muted">{{ report.channel }}</span>
                  <span v-if="report.summary" class="runtime-muted">{{ report.summary }}</span>
                </div>
              </div>
            </div>
            <div v-else class="runtime-empty-line">
              本次输出未包含 <code>reports</code>，不会进入收件-报告。
            </div>
          </div>
          <div class="test-output-tools">
            <a-button size="mini" type="text" @click="copyText(sandboxResult)">
              <icon-copy :size="11" style="margin-right:3px" />复制
            </a-button>
          </div>
          <pre class="bp-json">{{ JSON.stringify(sandboxResult, null, 2) }}</pre>
        </div>
        <div v-else class="bp-empty" style="margin-top:12px">
          <icon-thunderbolt :size="24" />
          <div class="bp-empty-title">暂无执行结果</div>
          <div class="bp-empty-desc">在上方输入参数 JSON 后点「执行」试跑 Skill</div>
        </div>
      </div>

      <!-- History（M1: Diff查看器 + 回滚） -->
      <div v-show="activeTab === 'history'" class="bp-pane bp-history-pane">
        <div v-if="recentAiTurns.length" class="bp-ai-turns">
          <div class="bp-ai-turns-head">
            <span>AI 修改回合</span>
            <span v-if="loadingAiTurns" class="bp-ai-turns-loading">加载中...</span>
          </div>
          <div v-for="turn in recentAiTurns" :key="turn.turn_id" class="bp-ai-turn">
            <div class="bp-ai-turn-main">
              <a-tag size="small" :color="turn.status === 'error' ? 'red' : turn.status === 'running' ? 'orange' : 'green'">
                {{ turnStatusLabel(turn.status) }}
              </a-tag>
              <span class="bp-ai-turn-prompt">{{ turn.user_prompt }}</span>
            </div>
            <div class="bp-ai-turn-meta">
              <span v-if="turn.quality" :class="['bp-ai-quality', `bp-ai-quality-${turn.quality.level || 'risk'}`]">
                质量 {{ turn.quality.score ?? 0 }}/100
              </span>
              <code v-if="turn.git_commit">{{ turn.git_commit }}</code>
              <span v-if="turn.changed_files.length">{{ turn.changed_files.length }} 个文件</span>
              <span v-if="turn.quality && missingQualityLabels(turn).length" class="bp-ai-turn-error">
                缺少 {{ missingQualityLabels(turn).join('、') }}
              </span>
              <span v-if="turn.created_at">{{ String(turn.created_at).slice(0, 19).replace('T', ' ') }}</span>
              <span v-if="turn.error" class="bp-ai-turn-error">{{ turn.error_reason?.reason || turn.error }}</span>
              <span v-if="turn.error_reason?.suggestion" class="bp-ai-turn-hint">{{ turn.error_reason.suggestion }}</span>
              <button v-if="turn.git_commit_full || turn.git_commit" class="bp-ai-turn-diff" @click="openTurnDiff(turn)">
                查看 diff
              </button>
            </div>
          </div>
        </div>
        <div v-if="commits?.length" class="bp-history-layout">
          <!-- 左：commit列表 -->
          <div class="bp-commit-list">
            <div
              v-for="c in commits" :key="c.hash"
              class="history-item" :class="{ active: selectedCommitHash === c.hash }"
              @click="$emit('select-commit', c.hash || ''); selectedCommitHash = c.hash || ''"
            >
              <div class="history-top">
                <div class="history-msg">{{ c.message }}</div>
                <a-popconfirm content="确定回滚到此版本？Skill 将变为草稿状态。" @ok="$emit('rollback', c.hash)">
                  <a-button size="mini" type="text" status="warning" @click.stop>回滚</a-button>
                </a-popconfirm>
              </div>
              <div class="history-meta">
                <span class="history-hash">{{ c.hash?.slice(0, 7) }}</span>
                <span>{{ c.author || '' }}</span>
                <span>{{ c.date || c.timestamp || '' }}</span>
              </div>
            </div>
          </div>
          <!-- 右：版本对比（最新版 vs 选中版，面向普通人） -->
          <div class="bp-diff-viewer">
            <div v-if="selectedCommitHash && versionSummary" class="version-summary">
              <!-- 最新版 -->
              <template v-if="versionSummary.isLatest">
                <div class="vs-badge vs-badge-ok">
                  <icon-check-circle-fill :size="20" />
                  <span>当前已是最新版本</span>
                </div>
                <div class="vs-hint">这是 Skill 的最新状态，包含所有已完成的功能。</div>
              </template>

              <!-- 旧版本 -->
              <template v-else>
                <div class="vs-badge vs-badge-warn">
                  <icon-exclamation-circle-fill :size="20" />
                  <span>此版本落后最新版 {{ versionSummary.missingCount }} 个更新</span>
                </div>

                <!-- 最新版多了什么 -->
                <div class="vs-section">
                  <div class="vs-section-title">
                    <icon-arrow-up style="color: #27AE3B" />
                    最新版比此版本<b style="color: #27AE3B">多了</b>:
                  </div>
                  <div class="vs-items">
                    <div v-for="(item, idx) in versionSummary.added" :key="'a'+idx" class="vs-item vs-item-add">
                      <span class="vs-icon">+</span>
                      <span class="vs-text">{{ item.text }}</span>
                      <span class="vs-date">{{ item.date }}</span>
                    </div>
                    <div v-if="!versionSummary.added.length" class="vs-empty">无新增</div>
                  </div>
                </div>

                <!-- 此版本会丢什么 -->
                <div class="vs-section" v-if="versionSummary.removed.length">
                  <div class="vs-section-title">
                    <icon-arrow-down style="color: #E63F3F" />
                    回到此版本会<b style="color: #E63F3F">丢失</b>:
                  </div>
                  <div class="vs-items">
                    <div v-for="(item, idx) in versionSummary.removed" :key="'r'+idx" class="vs-item vs-item-remove">
                      <span class="vs-icon">-</span>
                      <span class="vs-text">{{ item.text }}</span>
                      <span class="vs-date">{{ item.date }}</span>
                    </div>
                  </div>
                </div>
              </template>
            </div>
            <div v-else class="bp-empty" style="height:100%">
              <icon-history :size="20" />
              <span>点击左侧任意版本，查看与最新版的区别</span>
            </div>
          </div>
        </div>
        <div v-else class="bp-empty">
          <icon-history :size="24" />
          <div class="bp-empty-title">暂无版本历史</div>
          <div class="bp-empty-desc">Skill 有提交后可查看版本历史</div>
        </div>
      </div>

      <!-- Shadow（M2: 完整管理） -->
      <div v-show="activeTab === 'shadow'" class="bp-pane">
        <div v-if="shadowReport" class="bp-shadow">
          <!-- 操作按钮 -->
          <div class="bp-toolbar" style="margin-bottom:8px">
            <a-button size="mini" status="warning" @click="$emit('stop-shadow')">停止影子</a-button>
            <a-button size="mini" type="primary" @click="$emit('promote-shadow')">推正</a-button>
          </div>
          <!-- 统计卡片 -->
          <a-space style="margin-bottom:12px">
            <a-statistic title="影子天数" :value="shadowReport.shadow_days || 0" />
            <a-statistic title="AI 决策" :value="shadowReport.ai_decisions || 0" />
            <a-statistic title="人工记录" :value="shadowReport.human_records || 0" />
            <a-statistic title="一致率" :value="Number(shadowReport.consistency_rate || 0)" />
            <!-- 分歧率：来自 /skills/{id}/shadow/divergence-rate -->
            <a-tooltip v-if="shadowDivergenceRate" :content="divergenceTooltip" position="bottom">
              <a-statistic
                title="分歧率"
                :value="_normPct(shadowDivergenceRate.divergence_rate) || 0"
                :value-style="{ color: divergenceColor }"
              />
            </a-tooltip>
          </a-space>
          <!-- 每日对比表 -->
          <div v-if="shadowReport.daily?.length" style="margin-top:8px">
            <div style="font-size:12px;font-weight:600;color:var(--ai-ink-3);margin-bottom:4px">每日对比</div>
            <a-table :data="shadowReport.daily" :pagination="false" size="small" :bordered="false">
              <a-table-column title="日期" data-index="date" :width="100" />
              <a-table-column title="AI数" data-index="ai_count" :width="70" />
              <a-table-column title="人工数" data-index="human_count" :width="70" />
              <a-table-column title="匹配" data-index="match_count" :width="70" />
              <a-table-column title="一致率" :width="80">
                <template #cell="{ record }">
                  <a-tag :color="record.consistency_rate >= 80 ? 'green' : 'orange'" size="small">{{ record.consistency_rate }}%</a-tag>
                </template>
              </a-table-column>
            </a-table>
          </div>
          <!-- 逐项对比表 -->
          <div v-if="shadowComparisons?.length" style="margin-top:12px">
            <div style="font-size:12px;font-weight:600;color:var(--ai-ink-3);margin-bottom:4px">逐项对比</div>
            <a-table :data="shadowComparisons" :pagination="{ pageSize: 10 }" size="small" :bordered="false">
              <a-table-column title="时间" data-index="timestamp" :width="140" />
              <a-table-column title="AI输出" :width="200">
                <template #cell="{ record }"><pre class="shadow-json">{{ formatJson(record.ai_output) }}</pre></template>
              </a-table-column>
              <a-table-column title="原始输出" :width="200">
                <template #cell="{ record }"><pre class="shadow-json">{{ formatJson(record.original_output) }}</pre></template>
              </a-table-column>
              <a-table-column title="分歧" :width="60" align="center">
                <template #cell="{ record }">
                  <a-tag v-if="record.diverged" color="red" size="small">是</a-tag>
                  <a-tag v-else color="green" size="small">否</a-tag>
                </template>
              </a-table-column>
              <a-table-column title="人工判定" :width="100">
                <template #cell="{ record }">
                  <a-tag v-if="record.human_action" :color="humanActionColor(record.human_action)" size="small">{{ humanActionLabel(record.human_action) }}</a-tag>
                  <a-button v-else size="mini" @click="openRecordModal(record)">记录</a-button>
                </template>
              </a-table-column>
            </a-table>
          </div>
          <a-button v-if="!shadowComparisons?.length" size="mini" style="margin-top:8px" @click="$emit('load-comparisons')">加载逐项对比</a-button>
        </div>
        <div v-else class="bp-empty">
          <icon-eye :size="24" />
          <div class="bp-empty-title">暂无对比数据</div>
          <div class="bp-empty-desc">启动灰度对比后可追踪 AI vs 人工结果</div>
          <a-button size="mini" class="ai-btn" style="margin-top:4px" @click="$emit('start-shadow')">启动灰度</a-button>
        </div>
      </div>

      <!-- 人工记录弹窗 -->
      <a-modal v-model:visible="recordModalVisible" title="记录人工判定" :footer="false" :width="360">
        <div style="margin-bottom:12px;font-size:12px;color:var(--ai-ink-3)">
          Run ID: {{ recordTarget?.run_id || recordTarget?.id || '-' }}
        </div>
        <a-radio-group v-model="recordAction" direction="vertical" style="width:100%">
          <a-radio value="adopted"><a-tag color="green" size="small">采纳</a-tag> AI 结果正确</a-radio>
          <a-radio value="rejected"><a-tag color="red" size="small">否决</a-tag> AI 结果错误</a-radio>
          <a-radio value="na"><a-tag color="gray" size="small">不适用</a-tag> 无法判断</a-radio>
        </a-radio-group>
        <div style="margin-top:12px;text-align:right">
          <a-button size="small" @click="recordModalVisible = false" style="margin-right:8px">取消</a-button>
          <a-button size="small" type="primary" :disabled="!recordAction" @click="submitRecord">确认</a-button>
        </div>
      </a-modal>

      <!-- Optimizer（完整嵌入） -->
      <div v-show="activeTab === 'optimizer'" class="bp-pane" style="padding:0">
        <WorkbenchOptimizerPanel v-if="skillId" :skill-id="skillId" />
        <div v-else class="bp-empty">
          <icon-trophy :size="24" />
          <div class="bp-empty-title">自动调参不可用</div>
          <div class="bp-empty-desc">保存 Skill 后可使用 AI 自动调参</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type { PropType } from 'vue'
import { metaKey, shiftKey } from '@/utils/shortcut'
import {
  IconClose, IconCheckCircle, IconPlayArrow, IconExperiment,
  IconThunderbolt, IconHistory, IconEye, IconSwap, IconTrophy,
  IconCopy, IconFullscreen, IconFullscreenExit,
} from '@arco-design/web-vue/es/icon'
import { Message } from '@arco-design/web-vue'
import WorkbenchValidationPanel from './WorkbenchValidationPanel.vue'
import WorkbenchOptimizerPanel from './WorkbenchOptimizerPanel.vue'
import { workbenchApi } from '@/api'
import type { SkillHistoryResponse } from '@/types/skillstudio'
import {
  normalizeRuntimeOutputPreview,
  type RuntimeReportPreview,
  type RuntimeTodoPreview,
} from '@/utils/runtimeOutputPreview'

type ValidationLike = {
  errors?: unknown[]
  [key: string]: unknown
}

type TestDecision = {
  step_id?: string
  step?: string
  condition?: string
  conclusion?: string
}

type TestItem = {
  name?: string
  passed?: boolean
  duration_ms?: number
  decision_chain?: TestDecision[]
  decisions?: TestDecision[]
  output?: unknown
}

type TestResultLike = {
  results?: TestItem[]
  test_results?: TestItem[]
}

type CommitLike = NonNullable<SkillHistoryResponse['commits']>[number] & { author?: string }
type ShadowReportLike = {
  shadow_days?: number
  ai_decisions?: number
  human_records?: number
  consistency_rate?: number
  daily?: Array<Record<string, unknown>>
}
type DiffContentLike = Record<string, unknown>
type AiTurnLike = {
  turn_id: string
  user_prompt: string
  changed_files: string[]
  git_commit?: string | null
  git_commit_full?: string | null
  diff_summary?: Record<string, unknown> | null
  status: string
  error?: string | null
  error_reason?: { key?: string; reason?: string; suggestion?: string } | null
  quality?: {
    score?: number
    level?: string
    items?: Array<{ key?: string; label?: string; passed?: boolean; weight?: number }>
    summary?: string
  } | null
  created_at?: string | null
}
type ShadowComparisonLike = {
  run_id?: string | number
  id?: string | number
  ai_output?: unknown
  original_output?: unknown
  diverged?: boolean
  human_action?: string
  timestamp?: string
}
type ShadowDivergenceRateLike = {
  divergence_rate?: number
  total_runs?: number
  total?: number
  sample_size?: number
  divergent_runs?: number
  divergent?: number
  divergence_count?: number
  window_days?: number
  days?: number
}

const props = defineProps({
  activeTab: { type: String, default: 'validation' },
  skillId: { type: String, default: '' },
  validation: { type: Object as PropType<ValidationLike | null>, default: null },
  testResult: { type: Object as PropType<TestResultLike | null>, default: null },
  testRunning: { type: Boolean, default: false },
  sandboxResult: { type: [Object, null] as PropType<Record<string, unknown> | null>, default: null },
  sandboxRunning: { type: Boolean, default: false },
  commits: { type: Array as PropType<CommitLike[]>, default: () => [] },
  shadowReport: { type: Object as PropType<ShadowReportLike | null>, default: null },
  diffContent: { type: Object as PropType<DiffContentLike | null>, default: null },
  shadowComparisons: { type: Array as PropType<ShadowComparisonLike[]>, default: () => [] },
  // 影子分歧率（{divergence_rate, total, divergent, window_days?}）
  shadowDivergenceRate: { type: Object as PropType<ShadowDivergenceRateLike | null>, default: null },
  maximized: { type: Boolean, default: false },
})

// 分歧率显示：后端 /shadow/divergence-rate 返回
//   {skill_id, total_runs, divergent_runs, divergence_rate (0-100)}
function formatPct(v: unknown) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  // 后端固定返回 0~100，但兼容 0~1（万一规范变化）
  const num = Number(v)
  const pct = num <= 1 ? num * 100 : num
  return pct.toFixed(1) + '%'
}
function _normPct(v: unknown) {
  if (v == null || Number.isNaN(Number(v))) return null
  const n = Number(v)
  return n <= 1 ? n * 100 : n
}
const divergenceColor = computed(() => {
  const pct = _normPct(props.shadowDivergenceRate?.divergence_rate)
  if (pct == null) return 'var(--ai-ink-3)'
  if (pct >= 20) return 'var(--ai-bad)'
  if (pct >= 10) return 'var(--ai-warn)'
  return 'var(--ai-ok)'
})
const divergenceTooltip = computed(() => {
  const r = props.shadowDivergenceRate
  if (!r) return ''
  const total = r.total_runs ?? r.total ?? r.sample_size ?? 0
  const div = r.divergent_runs ?? r.divergent ?? r.divergence_count ?? 0
  const days = r.window_days ?? r.days
  const win = days ? `（${days}天窗口）` : ''
  if (!total) return `暂无对比数据${win}。建议 < 10% 转正`
  return `${div} / ${total} 条对比中分歧${win}。建议 < 10% 转正`
})

const sandboxRuntimePreview = computed(() => normalizeRuntimeOutputPreview(props.sandboxResult || {}))
const sandboxTodoItems = computed<RuntimeTodoPreview[]>(() => sandboxRuntimePreview.value.todos)
const sandboxReportItems = computed<RuntimeReportPreview[]>(() => sandboxRuntimePreview.value.reports)

const selectedCommitHash = ref('')
const recordModalVisible = ref(false)
const recentAiTurns = ref<AiTurnLike[]>([])
const loadingAiTurns = ref(false)

// 版本对比：最新版 vs 选中版，"多了什么"和"丢失什么"
const versionSummary = computed(() => {
  if (!selectedCommitHash.value || !props.commits?.length) return null
  const commits = props.commits
  const headHash = commits[0]?.hash
  if (selectedCommitHash.value === headHash) {
    return { isLatest: true, missingCount: 0, added: [], removed: [] }
  }

  const idx = commits.findIndex(c => c.hash === selectedCommitHash.value)
  if (idx <= 0) return null

  // 选中版本 → 最新版之间的所有 commit
  const between = commits.slice(0, idx)

  function cleanMsg(msg: string) {
    let text = (msg || '').trim()
    // 去掉技术前缀
    text = text.replace(/^(feat|fix|refactor|chore|docs|AI|ai|style|test)[\s:：]+/i, '')
    // 去掉 Co-Authored-By
    text = text.replace(/\n?Co-Authored-By:.*/s, '').trim()
    // 去掉 hash/路径等技术细节
    text = text.replace(/\([a-f0-9]{7,}\)/g, '')
    if (text.length > 60) text = text.slice(0, 57) + '...'
    return text || msg.slice(0, 40)
  }

  // "最新版多了什么" = 选中版本之后的所有更新
  const added = between.map(c => ({
    text: cleanMsg(c.message || ''),
    date: (c.date || c.timestamp || '').slice(0, 10),
  }))

  // "丢失什么" = 同样的列表，换个说法（实际是同一组 commits）
  // 但从用户角度：回到旧版 = 丢失这些更新
  const removed = added.map(a => ({ ...a }))

  return { isLatest: false, missingCount: between.length, added, removed }
})
const recordTarget = ref<ShadowComparisonLike | null>(null)
const recordAction = ref('')

function openRecordModal(item: ShadowComparisonLike) {
  recordTarget.value = item
  recordAction.value = ''
  recordModalVisible.value = true
}

function submitRecord() {
  if (!recordAction.value || !recordTarget.value) return
  emit('record-human-decision', {
    run_id: recordTarget.value.run_id || recordTarget.value.id,
    action: recordAction.value,
  })
  recordModalVisible.value = false
}

function normalizeAiTurns(payload: unknown): AiTurnLike[] {
  const items = Array.isArray((payload as any)?.items) ? (payload as any).items : Array.isArray(payload) ? payload : []
  return items
    .filter((item: unknown) => item && typeof item === 'object')
    .map((item: any) => ({
      turn_id: String(item.turn_id || item.id || Math.random()),
      user_prompt: String(item.user_prompt || ''),
      changed_files: Array.isArray(item.changed_files) ? item.changed_files.map(String) : [],
      git_commit: item.git_commit || null,
      git_commit_full: item.git_commit_full || null,
      diff_summary: item.diff_summary || null,
      status: String(item.status || 'running'),
      error: item.error || null,
      error_reason: item.error_reason || null,
      quality: item.quality || null,
      created_at: item.created_at || null,
    }))
}

function missingQualityLabels(turn: AiTurnLike): string[] {
  const items = Array.isArray(turn.quality?.items) ? turn.quality!.items! : []
  return items.filter(item => item?.passed === false).map(item => item.label || item.key || '').filter(Boolean).slice(0, 3)
}

async function loadRecentAiTurns() {
  if (!props.skillId || props.activeTab !== 'history') return
  loadingAiTurns.value = true
  try {
    recentAiTurns.value = normalizeAiTurns(await workbenchApi.codingTurns(props.skillId, { limit: 6 }))
  } catch {
    recentAiTurns.value = []
  } finally {
    loadingAiTurns.value = false
  }
}

function turnStatusLabel(status: string) {
  return { success: '完成', error: '失败', running: '进行中' }[status] || status
}

function openTurnDiff(turn: AiTurnLike) {
  const commit = turn.git_commit_full || turn.git_commit
  if (!props.skillId || !commit) return
  window.open(
    `/api/skills/${encodeURIComponent(props.skillId)}/diff-summary?target=${encodeURIComponent(commit)}`,
    '_blank',
    'noopener,noreferrer',
  )
}

watch(() => [props.activeTab, props.skillId], () => {
  void loadRecentAiTurns()
}, { immediate: true })

function humanActionColor(action: string) {
  return { adopted: 'green', rejected: 'red', na: 'gray' }[action] || 'gray'
}

function humanActionLabel(action: string) {
  return { adopted: '采纳', rejected: '否决', na: '不适用' }[action] || action
}

function formatJson(val: unknown) {
  if (!val) return '-'
  if (typeof val === 'string') return val.slice(0, 100)
  try { return JSON.stringify(val, null, 1).slice(0, 100) } catch { return String(val).slice(0, 100) }
}

function todoKindLabel(kind?: string) {
  return kind === 'dispatch' ? '派发' : '审核'
}

function todoKindColor(kind?: string) {
  return kind === 'dispatch' ? 'orange' : 'blue'
}

function getTestName(tc: TestItem, i: string | number): string {
  return tc?.name || `测试 ${Number(i) + 1}`
}

const emit = defineEmits([
  'change-tab', 'close', 'run-test', 'run-sandbox',
  'select-commit', 'rollback',
  'start-shadow', 'stop-shadow', 'promote-shadow', 'load-comparisons', 'record-human-decision', 'highlight-test',
  'toggle-maximized',
])

const sandboxParams = ref('{}')
const sandboxJsonError = ref('')

function validateSandbox() {
  if (!sandboxParams.value.trim()) { sandboxJsonError.value = ''; return }
  try { JSON.parse(sandboxParams.value); sandboxJsonError.value = '' }
  catch (e) { sandboxJsonError.value = e instanceof Error ? e.message : 'JSON 格式错误' }
}
watch(sandboxParams, validateSandbox)

function formatSandbox() {
  try {
    const obj = JSON.parse(sandboxParams.value || '{}')
    sandboxParams.value = JSON.stringify(obj, null, 2)
    sandboxJsonError.value = ''
  } catch (e) {
    Message.error('JSON 格式错误：' + (e instanceof Error ? e.message : '未知错误'))
  }
}

function insertTemplate() {
  sandboxParams.value = JSON.stringify({ example_field: 'value' }, null, 2)
}

// 测试结果：折叠/展开/复制
const expandedItems = ref<Set<number>>(new Set())
function isLongOutput(output: unknown) {
  const s = typeof output === 'string' ? output : JSON.stringify(output)
  return s.length > 500
}
function formatOutput(output: unknown) {
  return typeof output === 'string' ? output : JSON.stringify(output, null, 2)
}
function toggleExpand(i: number) {
  if (expandedItems.value.has(i)) expandedItems.value.delete(i)
  else expandedItems.value.add(i)
  expandedItems.value = new Set(expandedItems.value)
}
async function copyText(val: unknown) {
  const text = typeof val === 'string' ? val : JSON.stringify(val, null, 2)
  const { copyText: copyToClipboard } = await import('@/utils/clipboard')
  const ok = await copyToClipboard(text)
  if (ok) Message.success('已复制')
  else Message.warning('复制失败')
}

function getTestBadge(tr: TestResultLike | null) {
  const results = tr?.results || tr?.test_results
  if (!results?.length) return null
  const failed = results.filter(r => !r.passed).length
  if (failed > 0) return String(failed)
  return '✓'
}
function getTestBadgeType(tr: TestResultLike | null) {
  const results = tr?.results || tr?.test_results
  if (!results?.length) return 'ok'
  return results.some(r => !r.passed) ? 'error' : 'ok'
}
function getValidationBadge(v: ValidationLike | null) {
  const count = v?.errors?.length || 0
  if (!v) return null
  return count > 0 ? String(count) : '✓'
}
function getValidationBadgeType(v: ValidationLike | null) {
  return v?.errors?.length ? 'error' : 'ok'
}

const tabs = computed(() => [
  {
    key: 'validation', label: '验证', icon: IconCheckCircle,
    badge: getValidationBadge(props.validation),
    badgeType: getValidationBadgeType(props.validation),
  },
  {
    key: 'test', label: '测试', icon: IconExperiment,
    badge: getTestBadge(props.testResult),
    badgeType: getTestBadgeType(props.testResult),
  },
  { key: 'sandbox', label: '沙箱', icon: IconThunderbolt, badge: null },
  { key: 'history', label: '历史', icon: IconHistory, badge: props.commits?.length || null, badgeType: 'ok' },
  { key: 'shadow', label: '灰度对比', icon: IconEye, badge: null },
  { key: 'optimizer', label: '自动调参', icon: IconTrophy, badge: null, ai: true },
])
</script>

<style scoped>
.wb-bottom-panel { display: flex; flex-direction: column; height: 100%; }

/* Tab 栏 */
.bp-tabs {
  display: flex; align-items: center; gap: 2px;
  height: 38px; padding: 0 8px; flex-shrink: 0;
  border-bottom: 1px solid var(--ai-border);
}
.bp-tab {
  display: flex; align-items: center; gap: 4px;
  padding: 6px 12px; border: none; border-radius: 0;
  background: transparent; color: var(--ai-ink-3); font-size: 13px;
  cursor: pointer; transition: all var(--sf-transition);
  position: relative;
  font-weight: 800;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.bp-tab:hover { color: var(--ai-ink-1); }
.bp-tab.active { color: var(--ai-ink-1); border-bottom-color: var(--ai-warn); font-weight: 800; }
.bp-tab.bp-tab-ai > span { background: var(--sf-gradient-ai); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 800; }
.bp-tab.bp-tab-ai .arco-icon { color: #8b5cf6; }
.bp-tab.bp-tab-ai.active { border-bottom-color: #8b5cf6; }
.bp-spacer { flex: 1; }
.bp-close {
  width: 24px; height: 24px; border-radius: var(--sf-radius-xs); border: none;
  background: transparent; color: var(--ai-ink-3); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all var(--sf-transition);
}
.bp-close:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }
.bp-badge {
  font-size: 10px; font-weight: 800; min-width: 16px; text-align: center;
  padding: 0 4px; border-radius: 999px;
}
.bp-badge.error { background: var(--ai-bad-soft); color: var(--ai-bad); }
.bp-badge.ok { background: var(--ai-ok-soft); color: var(--ai-ok); }

/* 内容 */
.bp-content { flex: 1; overflow: hidden; }
.bp-pane { height: 100%; overflow-y: auto; padding: 10px 14px; }
.bp-empty {
  min-height: 100%; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 6px; padding: 40px 16px;
  font-size: 12px; color: var(--ai-ink-3); font-weight: 700;
}
.bp-empty-title { font-size: 13px; font-weight: 800; color: var(--ai-ink-2); }
.bp-empty-desc { font-size: 11px; text-align: center; max-width: 260px; line-height: 1.6; font-weight: 600; }
.bp-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.bp-result { font-size: 12px; color: var(--ai-ink-2); font-weight: 600; }
.bp-json {
  padding: 10px; border-radius: 4px; background: var(--ai-surface-2);
  font-size: 11px; line-height: 1.5; overflow-x: auto; white-space: pre-wrap;
  font-family: var(--ai-font-mono); color: var(--ai-ink-1);
  border: 1px solid var(--ai-border);
}
.bp-json-collapsed { max-height: 120px; overflow: hidden; position: relative; }
.bp-json-collapsed::after {
  content: ''; position: absolute; bottom: 0; left: 0; right: 0; height: 30px;
  background: linear-gradient(180deg, transparent, rgba(255, 255, 255, 0.96));
  pointer-events: none;
}
.test-output-wrapper { margin-top: 4px; }
.test-output-tools { display: flex; gap: 4px; margin-bottom: 4px; }
.sandbox-err {
  font-size: 11px; color: var(--ai-bad); font-weight: 700;
  max-width: 60%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.runtime-output-summary {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.62);
}
.runtime-summary-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 22px;
}
.runtime-summary-title,
.runtime-section-title {
  font-size: 12px;
  font-weight: 800;
  color: var(--ai-ink-1);
}
.runtime-summary-count {
  padding: 2px 6px;
  border-radius: 999px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 11px;
  font-weight: 800;
}
.runtime-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.runtime-todo-list,
.runtime-report-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.runtime-todo,
.runtime-report {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 8px 10px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
}
.runtime-todo-main {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.runtime-todo-main strong,
.runtime-report strong {
  font-size: 12px;
  color: var(--ai-ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.runtime-muted,
.runtime-empty-line {
  font-size: 11px;
  line-height: 1.5;
  color: var(--ai-ink-3);
  font-weight: 600;
}
.runtime-meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  font-size: 11px;
  color: var(--ai-ink-3);
  font-weight: 700;
}
.runtime-task-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.runtime-task {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto auto;
  gap: 6px;
  align-items: start;
  font-size: 11px;
  color: var(--ai-ink-2);
}
.runtime-task-index {
  width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--ai-border);
  color: var(--ai-ink-3);
  font-size: 10px;
  font-weight: 800;
}
.runtime-task-content {
  min-width: 0;
  line-height: 1.5;
  word-break: break-word;
}
.runtime-task-meta {
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
  white-space: nowrap;
}

/* 版本历史 - 双栏布局 */
.bp-history-pane { padding: 0 !important; }
.bp-ai-turns {
  padding: 10px 12px;
  border-bottom: 1px solid var(--ai-border);
  background: rgba(15, 143, 111, 0.04);
}
.bp-ai-turns-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 800;
  color: var(--ai-ink-1);
}
.bp-ai-turns-loading {
  color: var(--ai-ink-3);
  font-size: 11px;
  font-weight: 700;
}
.bp-ai-turn {
  padding: 7px 8px;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.72);
}
.bp-ai-turn + .bp-ai-turn { margin-top: 6px; }
.bp-ai-turn-main,
.bp-ai-turn-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.bp-ai-turn-prompt {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 800;
}
.bp-ai-turn-meta {
  margin-top: 4px;
  color: var(--ai-ink-3);
  font-size: 11px;
  font-weight: 700;
}
.bp-ai-turn-meta code {
  font-family: var(--ai-font-mono);
  color: var(--ai-ok);
}
.bp-ai-quality {
  font-weight: 800;
}
.bp-ai-quality-good { color: var(--ai-ok); }
.bp-ai-quality-warn { color: var(--ai-warn); }
.bp-ai-quality-risk { color: var(--ai-bad); }
.bp-ai-turn-error {
  color: var(--ai-bad);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bp-ai-turn-hint {
  color: var(--ai-ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bp-ai-turn-diff {
  margin-left: auto;
  border: none;
  background: transparent;
  color: var(--ai-accent-ink);
  cursor: pointer;
  font-size: 11px;
  font-weight: 800;
}
.bp-ai-turn-diff:hover { text-decoration: underline; }
.bp-history-layout { display: flex; height: 100%; }
.bp-commit-list { width: 280px; flex-shrink: 0; overflow-y: auto; border-right: 1px solid var(--ai-border); padding: 8px; }
.bp-diff-viewer { flex: 1; min-width: 0; overflow-y: auto; padding: 12px 16px; }

/* 版本对比 */
.version-summary { }
.vs-badge {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 14px; border-radius: 8px;
  font-size: 14px; font-weight: 700; margin-bottom: 12px;
}
.vs-badge-ok { background: rgba(39, 174, 59, 0.08); color: #1B7A2B; }
.vs-badge-warn { background: rgba(230, 119, 0, 0.08); color: #B45A00; }
.vs-hint { font-size: 12px; color: var(--ai-ink-3); line-height: 1.6; }
.vs-section { margin-bottom: 14px; }
.vs-section-title {
  display: flex; align-items: center; gap: 6px;
  font-size: 13px; font-weight: 600; color: var(--ai-ink-1);
  margin-bottom: 8px;
}
.vs-items { display: flex; flex-direction: column; gap: 3px; }
.vs-item {
  display: flex; align-items: center; gap: 8px;
  padding: 5px 10px; border-radius: 6px;
  font-size: 12.5px;
}
.vs-item-add { background: rgba(39, 174, 59, 0.05); }
.vs-item-add:hover { background: rgba(39, 174, 59, 0.1); }
.vs-item-remove { background: rgba(230, 63, 63, 0.05); }
.vs-item-remove:hover { background: rgba(230, 63, 63, 0.1); }
.vs-icon {
  width: 18px; height: 18px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: 13px; flex-shrink: 0;
}
.vs-item-add .vs-icon { background: rgba(39, 174, 59, 0.15); color: #1B7A2B; }
.vs-item-remove .vs-icon { background: rgba(230, 63, 63, 0.15); color: #C0392B; }
.vs-text { flex: 1; color: var(--ai-ink-1); font-weight: 600; line-height: 1.5; }
.vs-date { font-size: 11px; color: var(--ai-ink-3); flex-shrink: 0; font-family: var(--ai-font-mono); }
.vs-empty { font-size: 12px; color: var(--ai-ink-3); padding: 4px 10px; }

/* 版本历史 */
.bp-history { overflow-y: auto; }
.history-item { padding: 8px 0; border-bottom: 1px solid var(--ai-border); cursor: pointer; transition: background var(--sf-transition); }
.history-item:hover { background: var(--ai-surface-2); }
.history-top { display: flex; align-items: center; justify-content: space-between; }
.history-msg { font-size: 12px; font-weight: 800; color: var(--ai-ink-1); flex: 1; }
.history-meta { display: flex; gap: 10px; font-size: 11px; color: var(--ai-ink-3); margin-top: 3px; font-weight: 700; }
.history-hash { font-family: var(--ai-font-mono); }

/* 影子运行 */
.bp-shadow { padding: 10px 0; }

/* 测试结果 */
.test-item { padding: 8px 0; border-bottom: 1px solid var(--ai-border); }
.test-header { display: flex; align-items: center; gap: 8px; }
.test-name { font-weight: 800; font-size: 12px; color: var(--ai-ink-1); }
.test-msg { font-size: 11px; color: var(--ai-ink-3); font-weight: 600; }
.test-chain {
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px;
  margin-top: 6px; padding: 6px 8px; border-radius: 4px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
}
.chain-step { display: inline-flex; align-items: center; gap: 3px; }
.chain-arrow { color: var(--ai-ink-3); font-size: 11px; }
.chain-cond { font-size: 11px; color: var(--ai-ink-2); max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 700; }
</style>
