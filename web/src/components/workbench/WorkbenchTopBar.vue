<template>
  <div class="wb-hdr">
    <div class="hdr-left">
      <button class="hdr-back" @click="$router.back()" title="返回">
        <icon-left :size="14" />
      </button>
      <span class="hdr-sep" />
      <span class="hdr-crumb" @click="$router.push('/')">Skills</span>
      <span class="hdr-slash">/</span>
      <span class="hdr-title">{{ title }}</span>
      <a-tag v-if="!isEditPerspective" size="small" :color="modeTagColor">{{ modeLabel }}</a-tag>
      <a-tag v-if="status" size="small" :color="statusColor">{{ statusLabel }}</a-tag>
      <a-tag v-if="riskLevel" size="small" :color="riskTagColor">{{ riskLevel }}</a-tag>
      <a-tag v-if="versionLabel" size="small" color="gray">{{ versionLabel }}</a-tag>
      <a-tag v-if="dirty" size="small" color="orangered">● 未保存</a-tag>
      <a-tag v-else-if="editMode && !isCreate" size="small" color="black" class="editing-tag">编辑中</a-tag>
      <a-tag v-else-if="!isCreate && isEditPerspective" size="small" color="gray">只读</a-tag>
      <!-- 编辑锁占用提示：他人正在编辑时显示 -->
      <a-tooltip v-if="lockOwner" :content="`${lockOwner} 正在编辑，你只能查看`" position="bottom">
        <a-tag size="small" color="orange">
          <icon-lock :size="11" style="margin-right:3px" />{{ lockOwner }} 编辑中
        </a-tag>
      </a-tooltip>
      <!-- 健康评分徽章：5维健康评分 -->
      <a-popover
        v-if="healthScore"
        trigger="click"
        position="bl"
        :content-style="{ padding: 0 }"
      >
        <a-tag size="small" :color="healthColor" class="health-tag">
          <icon-heart :size="11" style="margin-right:3px" />健康 {{ Math.round(healthScore.score || 0) }}
        </a-tag>
        <template #content>
          <div class="health-popover">
            <div class="hp-header">
              <span class="hp-title">Skill 健康评分</span>
              <a-button size="mini" type="text" :loading="healthLoading" @click="$emit('refresh-health')">刷新</a-button>
            </div>
            <div class="hp-body">
              <div class="hp-total">
                <div class="hp-total-num" :style="{ color: healthHexColor }">{{ Math.round(healthScore.score || 0) }}</div>
                <div class="hp-total-label">
                  总分（满分 100）<span v-if="healthScore.grade" class="hp-grade">· {{ healthScore.grade }}</span>
                </div>
              </div>
              <div class="hp-grid">
                <div v-for="dim in healthDimensions" :key="dim.key" class="hp-dim">
                  <span class="hp-dim-label">{{ dim.label }}</span>
                  <a-progress :percent="dim.value / 100" :stroke-width="6" :show-text="false" :color="dimColor(dim.value)" />
                  <span class="hp-dim-value">{{ Math.round(dim.value) }}</span>
                </div>
              </div>
              <div v-if="healthScore.suggestions?.length" class="hp-suggestions">
                <div class="hp-sugg-title">改进建议</div>
                <div v-for="(s, i) in healthScore.suggestions.slice(0, 3)" :key="i" class="hp-sugg-item">
                  · {{ typeof s === 'string' ? s : (s.message || s.suggestion) }}
                </div>
              </div>
            </div>
          </div>
        </template>
      </a-popover>
      <!-- Guardian 健康徽章：有异常/冲突时显示 -->
      <a-popover
        v-if="guardianCount > 0"
        v-model:popup-visible="guardianPopVisible"
        trigger="click"
        position="bl"
        :content-style="{ padding: 0 }"
      >
        <a-tag size="small" :color="guardianSeverity === 'high' ? 'red' : 'orange'" class="guardian-tag">
          <icon-exclamation-circle-fill :size="11" style="margin-right:3px" />运行告警 {{ guardianCount }}
        </a-tag>
        <template #content>
          <div class="guardian-popover">
            <div class="gp-header">
              <span class="gp-title">Runtime Guardian</span>
              <a-button size="small" type="text" :loading="guardianLoading" @click="$emit('guardian-refresh')">刷新</a-button>
            </div>
            <div v-if="!guardianItems.length" class="gp-empty">暂无告警</div>
            <div v-else class="gp-list">
              <div v-for="(a, i) in guardianItems" :key="i" class="gp-item">
                <a-tag size="small" :color="a.severity === 'high' || a.severity === 'critical' ? 'red' : 'orange'">{{ a.severity || 'info' }}</a-tag>
                <div class="gp-item-body">
                  <div class="gp-item-msg">{{ a.description || a.message || a.metric }}</div>
                  <div class="gp-item-meta">{{ a.dimension || a.category }} · {{ a.segment || '' }}</div>
                </div>
              </div>
            </div>
            <div v-if="guardianItems.length" class="gp-actions">
              <a-button size="small" type="text" @click="$emit('guardian-root-cause')">分析根因</a-button>
            </div>
          </div>
        </template>
      </a-popover>
    </div>

    <div class="hdr-center">
      <!-- 编辑表现切换：设计稿主操作区从 view mode segmented 开始 -->
      <a-tooltip v-if="isEditPerspective" content="结构化编辑各模块 / 九宫格总览 / 源码直接改 SKILL.md，修改会互相同步" position="bottom">
        <a-radio-group v-model="localView" type="button" size="small" @change="$emit('toggle-view', $event)">
          <a-radio value="block">
            <icon-apps :size="13" style="margin-right:3px" />模块
          </a-radio>
          <a-radio value="grid">
            <IconMosaic :size="13" style="margin-right:3px" />九宫格
          </a-radio>
          <a-radio value="code">
            <icon-code :size="13" style="margin-right:3px" />源码
          </a-radio>
        </a-radio-group>
      </a-tooltip>
      <a-button
        v-if="!isCreate"
        class="submit-review-btn"
        size="small"
        type="outline"
        :loading="submittingReview"
        @click="$emit('submit-review')"
      >
        <icon-check-circle :size="13" style="margin-right:3px" />提交审核
      </a-button>
      <a-button
        v-if="!isCreate"
        class="personal-ui-btn"
        size="small"
        type="outline"
        @click="$emit('open-portal-ui')"
      >
        <icon-robot :size="13" style="margin-right:3px" />AI 调整界面
      </a-button>
    </div>

    <div class="hdr-right">
      <a-dropdown v-if="!isCreate" trigger="click" position="br">
        <a-button size="small" type="primary" :disabled="!hasContent">
          <icon-play-arrow :size="14" style="margin-right:3px" />运行
          <icon-down :size="11" style="margin-left:4px" />
        </a-button>
        <template #content>
          <a-doption @click="$emit('open-tab', 'sandbox')">
            <icon-thunderbolt :size="14" style="margin-right:6px" />沙箱试运行
          </a-doption>
          <a-doption @click="$emit('run-aiclaw')">
            <icon-robot :size="14" style="margin-right:6px" />本机 AIClaw 试运行
          </a-doption>
          <a-doption @click="$emit('open-deploy')">
            <icon-upload :size="14" style="margin-right:6px" />补发到 Agent 终端
          </a-doption>
        </template>
      </a-dropdown>

      <a-button
        v-if="!isCreate && !editMode && isEditPerspective"
        data-testid="enter-edit-btn"
        size="small"
        type="outline"
        @click="$emit('enter-edit')"
      >
        <icon-edit :size="13" style="margin-right:3px" />编辑
      </a-button>
      <a-button
        v-else-if="!isCreate && editMode && isEditPerspective"
        data-testid="save-menu-btn"
        size="small"
        :type="dirty ? 'primary' : 'outline'"
        :loading="saving"
        :disabled="!dirty"
        @click="$emit('save')"
      >
        <icon-save :size="13" style="margin-right:3px" />{{ dirty ? '保存' : '已保存' }}
      </a-button>
      <a-button
        v-else-if="isCreate"
        size="small"
        type="primary"
        :loading="saving"
        :disabled="!hasContent"
        @click="$emit('create')"
      >
        创建 Skill
      </a-button>

      <a-dropdown trigger="click" position="br">
        <a-button class="studio-more-btn" size="small" type="outline">
          更多
          <icon-down :size="11" style="margin-left:4px" />
        </a-button>
        <template #content>
          <a-dsubmenu v-if="availablePerspectiveValues.length > 1" trigger="click">
            <template #default>模式：{{ perspectiveLabelMap[studioMode] || studioMode }}</template>
            <template #content>
              <a-doption
                v-for="perspective in availablePerspectiveValues"
                :key="perspective"
                @click="$emit('change-perspective', perspective)"
              >
                {{ perspectiveLabelMap[perspective] || perspective }}
              </a-doption>
            </template>
          </a-dsubmenu>
          <a-doption v-if="!isCreate" :disabled="trainingCandidateLoading" @click="$emit('generate-training-candidate')">
            <icon-trophy :size="14" style="margin-right:6px" />训练候选
          </a-doption>
          <a-doption v-if="!isCreate && editMode && isEditPerspective" @click="$emit('cancel-edit')">取消编辑</a-doption>
          <a-doption v-if="!isCreate && editMode && isEditPerspective" @click="$emit('publish')">发布</a-doption>
          <a-doption v-if="!isCreate && editMode && isEditPerspective" style="color: var(--ai-bad)" @click="$emit('deprecate')">停用</a-doption>
          <a-doption v-if="!isCreate" @click="$emit('open-permissions')">
            <icon-user-group :size="14" style="margin-right:6px" />成员权限
          </a-doption>
          <a-doption @click="$emit('toggle-navigator')">
            <icon-menu-fold :size="14" style="margin-right:6px" />{{ navigatorOpen ? '关闭' : '打开' }}模块导航
          </a-doption>
          <a-doption @click="$emit('toggle-assistant')">
            <icon-robot :size="14" style="margin-right:6px" />{{ assistantOpen ? '关闭' : '打开' }}AI 助手
          </a-doption>
          <a-dsubmenu trigger="click">
            <template #default>底部面板</template>
            <template #content>
              <a-doption @click="$emit('open-tab', 'validation')">验证</a-doption>
              <a-doption @click="$emit('open-tab', 'test')">测试</a-doption>
              <a-doption @click="$emit('open-tab', 'sandbox')">沙箱</a-doption>
              <a-doption @click="$emit('open-tab', 'history')">历史</a-doption>
              <a-doption @click="$emit('open-tab', 'shadow')">灰度对比</a-doption>
              <a-doption @click="$emit('open-tab', 'optimizer')">自动调参</a-doption>
            </template>
          </a-dsubmenu>
          <a-doption @click="$emit('command-palette')">
            <icon-search :size="14" style="margin-right:6px" />命令面板
          </a-doption>
        </template>
      </a-dropdown>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, computed, type PropType } from 'vue'
import {
  IconLeft, IconSave, IconApps, IconCode, IconCheckCircle, IconEdit,
  IconRobot, IconMenuFold, IconSearch, IconMosaic,
  IconPlayArrow, IconThunderbolt, IconTrophy,
  IconExclamationCircleFill, IconDown, IconLock, IconHeart,
  IconUpload, IconUserGroup,
} from '@arco-design/web-vue/es/icon'

const props = defineProps({
  title: { type: String, default: '' },
  status: { type: String, default: '' },
  riskLevel: { type: String, default: '' },
  versionLabel: { type: String, default: '' },
  dirty: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
  isCreate: { type: Boolean, default: false },
  hasContent: { type: Boolean, default: false },
  submittingReview: { type: Boolean, default: false },
  viewMode: { type: String, default: 'block' },
  studioMode: { type: String, default: 'edit' },
  availablePerspectives: { type: Array as PropType<string[]>, default: () => ['edit', 'explain', 'review'] },
  editMode: { type: Boolean, default: false },
  lockHeld: { type: Boolean, default: false },
  // 编辑锁被他人占用时的持有人显示名
  lockOwner: { type: String, default: '' },
  navigatorOpen: { type: Boolean, default: true },
  assistantOpen: { type: Boolean, default: true },
  bottomOpen: { type: Boolean, default: false },
  // Guardian 运行时告警
  guardianItems: { type: Array as PropType<any[]>, default: () => [] },
  guardianLoading: { type: Boolean, default: false },
  // 5维健康评分（{score, dimensions: {code_quality, test_coverage, stability, adoption, freshness}, suggestions}）
  healthScore: { type: Object as PropType<any>, default: null },
  healthLoading: { type: Boolean, default: false },
  trainingCandidateLoading: { type: Boolean, default: false },
})

defineEmits([
  'change-perspective',
  'toggle-view', 'validate', 'save', 'create',
  'submit-review', 'publish', 'deprecate',
  'enter-edit', 'cancel-edit',
  'run-aiclaw', 'open-deploy',
  'toggle-navigator', 'toggle-assistant', 'toggle-bottom', 'open-tab',
  'open-permissions',
  'command-palette',
  'guardian-refresh', 'guardian-root-cause',
  'refresh-health',
  'generate-training-candidate',
  'open-portal-ui',
])

const guardianCount = computed(() => props.guardianItems.length)
const availablePerspectiveValues = computed(() => props.availablePerspectives || [])
const isEditPerspective = computed(() => props.studioMode === 'edit' || props.studioMode === 'create')
const modeLabel = computed(() => ({
  create: '创建模式',
  edit: '编辑模式',
  explain: '讲解模式',
  review: '审批模式',
}[props.studioMode] || '编辑模式'))
const modeTagColor = computed(() => ({
  create: 'arcoblue',
  edit: 'green',
  explain: 'purple',
  review: 'orange',
}[props.studioMode] || 'green'))
const perspectiveLabelMap: Record<string, string> = {
  edit: '编辑',
  explain: '讲解',
  review: '审批',
}
const guardianSeverity = computed(() => {
  return props.guardianItems.some(a => a.severity === 'high' || a.severity === 'critical') ? 'high' : 'medium'
})

// 健康评分颜色：>=80 绿，>=60 橙，<60 红
const healthColor = computed(() => {
  const s = props.healthScore?.score || 0
  if (s >= 80) return 'green'
  if (s >= 60) return 'orange'
  return 'red'
})
const healthHexColor = computed(() => {
  const s = props.healthScore?.score || 0
  if (s >= 80) return 'var(--ai-ok)'
  if (s >= 60) return 'var(--ai-warn)'
  return 'var(--ai-bad)'
})
function dimColor(v: number) {
  if (v >= 80) return 'rgb(var(--green-6))'
  if (v >= 60) return 'rgb(var(--orange-6))'
  return 'rgb(var(--red-6))'
}
// 后端 dashboard.service.get_skill_health_score 返回：
//   {skill_id, score, grade, breakdown: {adoption_rate, success_rate, test_coverage, freshness, data_health}}
// 公式：采纳率30% + 成功率25% + 测试覆盖20% + 新鲜度15% + 数据健康10%
const healthDimensions = computed(() => {
  // 兼容 breakdown / dimensions（万一 schema 演进）
  const dim = props.healthScore?.breakdown || props.healthScore?.dimensions || {}
  return [
    { key: 'adoption_rate', label: '采纳率', value: dim.adoption_rate ?? dim.adoption ?? 0 },
    { key: 'success_rate', label: '成功率', value: dim.success_rate ?? dim.stability ?? 0 },
    { key: 'test_coverage', label: '测试覆盖', value: dim.test_coverage ?? dim.coverage ?? 0 },
    { key: 'freshness', label: '新鲜度', value: dim.freshness ?? 0 },
    { key: 'data_health', label: '数据健康', value: dim.data_health ?? 0 },
  ]
})

// Guardian popover：0 → >0 时自动展开一次；用户手动关过就不再自动弹
const guardianPopVisible = ref(false)
const hasAutoOpened = ref(false)
watch(
  () => props.guardianItems.length,
  (n, old) => {
    if (!hasAutoOpened.value && (old ?? 0) === 0 && n > 0) {
      guardianPopVisible.value = true
      hasAutoOpened.value = true
    }
    // 告警清空后重置 auto flag，下次仍可再弹一次
    if (n === 0) {
      guardianPopVisible.value = false
      hasAutoOpened.value = false
    }
  }
)

const localView = ref<string>(props.viewMode)
watch(() => props.viewMode, (v: string) => { localView.value = v })

const statusColor = computed(() => {
  const m: Record<string, string> = { active: 'green', draft: 'blue', shadow: 'purple', deprecated: 'gray' }
  return m[props.status] || 'blue'
})
const riskTagColor = computed(() => {
  const level = String(props.riskLevel || '').toUpperCase()
  if (level === 'R4') return 'red'
  if (level === 'R3') return 'orange'
  return 'gray'
})
const statusLabel = computed(() => {
  const m: Record<string, string> = { active: '运行中', draft: '草稿', shadow: '影子', deprecated: '已停用' }
  return m[props.status] || props.status
})
</script>

<style scoped>
.wb-hdr {
  display: flex; align-items: center; justify-content: space-between;
  height: 44px; padding: 0 16px; flex-shrink: 0;
  background: var(--ai-surface);
  border-bottom: 1px solid var(--ai-border);
  font-family: var(--ai-font-sans);
}
.hdr-left, .hdr-center, .hdr-right { display: flex; align-items: center; gap: 8px; }
/* 统一 TopBar 内 radio-group 按钮字体 — Arco 默认 14px 偏大，对齐为 12.5px */
.hdr-center :deep(.arco-radio-button) {
  font-size: 12.5px; padding: 0 10px; height: 26px; line-height: 26px;
  font-weight: 500; color: var(--ai-ink-3);
  border-color: var(--ai-border);
  background: var(--ai-surface);
}
.hdr-center :deep(.arco-radio-button.arco-radio-checked) {
  background: var(--ai-surface-2); color: var(--ai-ink-1);
}
.hdr-center :deep(.arco-radio-group) {
  border: 1px solid var(--ai-border); border-radius: 6px; padding: 0;
  background: var(--ai-surface);
}
.submit-review-btn,
.personal-ui-btn,
.studio-more-btn { font-size: 12.5px; height: 30px; padding: 0 12px; font-weight: 500; }
.hdr-right :deep(.arco-btn-size-small) { font-size: 12.5px; height: 30px; padding: 0 12px; font-weight: 500; }
.hdr-left { flex: 1; min-width: 0; }
.hdr-center { flex: 0 0 auto; }
.hdr-right { flex-shrink: 0; }
.hdr-back {
  width: 30px; height: 30px; border-radius: 6px; border: 0;
  background: transparent; color: var(--ai-ink-3);
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  transition: background .15s, color .15s;
}
.hdr-back:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }
.hdr-sep { width: 1px; height: 18px; background: var(--ai-border); }
.hdr-crumb { font-size: 12px; color: var(--ai-ink-4); cursor: pointer; font-weight: 450; transition: color .15s; }
.hdr-crumb:hover { color: var(--ai-ink-2); }
.hdr-slash { font-size: 12px; color: var(--ai-ink-4); margin: 0 2px; }
.hdr-title {
  font-size: 13px; font-weight: 500; color: var(--ai-ink-1);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 240px;
  letter-spacing: -0.005em;
}
.hdr-icon-btn {
  width: 30px; height: 30px; border-radius: 6px; border: 0;
  background: transparent; color: var(--ai-ink-3);
  display: flex; align-items: center; justify-content: center; cursor: pointer;
  transition: background .15s, color .15s;
}
.hdr-icon-btn:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }
.hdr-icon-btn.active { background: var(--ai-surface-2); color: var(--ai-ink-1); }
/* 面板菜单 */
.panel-menu { display: flex; flex-direction: column; gap: 2px; width: 200px; }
.pm-item {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px; border: 0; border-radius: 6px;
  background: transparent; cursor: pointer; text-align: left;
  transition: background .15s;
}
.pm-item:hover { background: var(--ai-surface-2); }
.pm-icon { flex-shrink: 0; color: var(--ai-ink-3); }
.pm-text { flex: 1; min-width: 0; }
.pm-label { font-size: 13px; font-weight: 500; color: var(--ai-ink-1); line-height: 1.3; }
.pm-desc { font-size: 11px; color: var(--ai-ink-4); line-height: 1.3; margin-top: 1px; font-weight: 450; }
.pm-item.pm-ai .pm-label { color: var(--ai-ink-1); }
.pm-item.pm-ai .pm-icon { color: var(--ai-ink-3); }

.hdr-icon-btn.ai-toggle { background: transparent; }
.hdr-icon-btn.ai-toggle .arco-icon { color: var(--ai-ink-3); }
.hdr-icon-btn.ai-toggle:hover { background: var(--ai-surface-2); }
.hdr-icon-btn.ai-toggle:hover .arco-icon { color: var(--ai-ink-1); }
.hdr-icon-btn.ai-toggle.active { background: var(--ai-ink-1); color: var(--ai-surface); border-color: var(--ai-ink-1); }
.hdr-icon-btn.ai-toggle.active .arco-icon { color: var(--ai-surface); }

/* Guardian 健康徽章 */
.guardian-tag { cursor: pointer; }
.guardian-popover { width: 320px; max-height: 360px; display: flex; flex-direction: column; }
.gp-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 12px; border-bottom: 1px solid var(--ai-border);
}
.gp-title { font-size: 13px; font-weight: 500; color: var(--ai-ink-1); }
.gp-empty { padding: 24px; text-align: center; font-size: 12px; color: var(--ai-ink-4); font-weight: 450; }
.gp-list { overflow-y: auto; max-height: 260px; padding: 4px; }
.gp-item {
  display: flex; gap: 8px; padding: 8px 10px; border-radius: 6px;
  transition: background .15s;
}
.gp-item:hover { background: var(--ai-surface-2); }
.gp-item-body { flex: 1; min-width: 0; }
.gp-item-msg { font-size: 12px; color: var(--ai-ink-1); line-height: 1.4; font-weight: 500; }
.gp-item-meta { font-size: 11px; color: var(--ai-ink-4); margin-top: 2px; font-weight: 450; }
.gp-actions { padding: 6px 10px; border-top: 1px solid var(--ai-border); display: flex; justify-content: flex-end; }

/* 健康评分徽章 */
.health-tag { cursor: pointer; }
.health-popover { width: 320px; }
.hp-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 12px; border-bottom: 1px solid var(--ai-border);
}
.hp-title { font-size: 13px; font-weight: 500; color: var(--ai-ink-1); }
.hp-body { padding: 12px; }
.hp-total { text-align: center; margin-bottom: 12px; }
.hp-total-num { font-size: 36px; font-weight: 600; line-height: 1; letter-spacing: -0.02em; }
.hp-total-label { font-size: 11px; color: var(--ai-ink-4); margin-top: 4px; font-weight: 450; }
.hp-grade { color: var(--ai-ink-2); font-weight: 500; margin-left: 4px; }
.hp-grid { display: flex; flex-direction: column; gap: 6px; }
.hp-dim { display: grid; grid-template-columns: 60px 1fr 30px; align-items: center; gap: 8px; font-size: 11px; }
.hp-dim-label { color: var(--ai-ink-3); font-weight: 500; }
.hp-dim-value { text-align: right; color: var(--ai-ink-1); font-weight: 500; font-variant-numeric: tabular-nums; }
.hp-suggestions { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--ai-border); }
.hp-sugg-title { font-size: 11px; color: var(--ai-ink-4); font-weight: 500; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.06em; }
.hp-sugg-item { font-size: 11px; color: var(--ai-ink-2); line-height: 1.5; font-weight: 450; margin-bottom: 2px; }

/* a-tag → ai-pill 映射（编辑模式 / 状态 / 锁等） */
.wb-hdr :deep(.arco-tag) {
  height: 20px; padding: 0 7px; border-radius: 4px; font-size: 11px;
  font-weight: 500; line-height: 18px;
}
.wb-hdr :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft); color: var(--ai-ok); border-color: transparent;
}
.wb-hdr :deep(.arco-tag-color-arcoblue),
.wb-hdr :deep(.arco-tag-color-blue) {
  background: var(--ai-info-soft); color: var(--ai-info); border-color: transparent;
}
.wb-hdr :deep(.arco-tag-color-orange),
.wb-hdr :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft); color: var(--ai-warn); border-color: transparent;
}
.wb-hdr :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft); color: var(--ai-bad); border-color: transparent;
}
.wb-hdr :deep(.arco-tag-color-purple),
.wb-hdr :deep(.arco-tag-color-magenta) {
  background: var(--ai-accent-soft); color: var(--ai-accent-ink); border-color: transparent;
}
.wb-hdr :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2); color: var(--ai-ink-3); border-color: transparent;
}
.wb-hdr :deep(.editing-tag) {
  background: var(--ai-ink-1); color: var(--ai-surface); border-color: var(--ai-ink-1);
}

/* 按钮 → ai-btn 视感 */
.wb-hdr :deep(.arco-btn) {
  height: 30px; border-radius: 6px; font-size: 12.5px; font-weight: 500;
  box-shadow: none;
}
.wb-hdr :deep(.arco-btn-outline),
.wb-hdr :deep(.arco-btn-secondary) {
  border-color: var(--ai-border); background: var(--ai-surface); color: var(--ai-ink-1);
}
.wb-hdr :deep(.arco-btn-outline:hover),
.wb-hdr :deep(.arco-btn-secondary:hover) {
  background: var(--ai-surface-2); border-color: var(--ai-border-2);
}
.wb-hdr :deep(.arco-btn-primary) {
  background: var(--ai-ink-1); border-color: var(--ai-ink-1); color: var(--ai-surface);
  box-shadow: none;
}
.wb-hdr :deep(.arco-btn-primary:hover) {
  background: #000; border-color: #000;
}
</style>
