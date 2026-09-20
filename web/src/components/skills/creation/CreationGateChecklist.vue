<template>
  <!-- Skill 创建向导 — AI 合成完成后的「发布前检查」面板 -->
  <div class="creation-gate-card">
    <!-- 顶部 title 行：整体通过状态图标 + 文案 + 右侧进度计数 -->
    <div class="gate-title">
      <span class="title-main">
        <IconCheckCircleFill v-if="canPublish" class="title-icon icon-pass" />
        <IconExclamationCircleFill v-else class="title-icon icon-warn" />
        <span class="title-text">发布前检查</span>
      </span>
      <span class="title-progress">{{ passedCount }}/{{ items.length }} 已通过</span>
    </div>

    <!-- 中间 checklist：每项一行，三列 grid -->
    <div class="gate-list">
      <div
        v-for="item in items"
        :key="item.key"
        class="gate-row"
        :class="{ 'row-pending': !item.passed }"
      >
        <!-- 第 1 列：状态图标 -->
        <span class="row-icon">
          <!-- manual_confirmation 且仅差这一项时，用警示橙图标提示「待你确认」 -->
          <IconExclamationCircleFill
            v-if="isPendingManualConfirm(item)"
            class="icon-warn"
          />
          <IconCheckCircleFill
            v-else-if="item.passed"
            class="icon-pass"
          />
          <IconStop v-else class="icon-stop" />
        </span>

        <!-- 第 2 列：label -->
        <span class="row-label" :class="{ 'label-dim': !item.passed }">
          {{ item.label }}
        </span>

        <!-- 第 3 列：detail（可选） -->
        <span v-if="item.detail" class="row-detail">{{ item.detail }}</span>
      </div>
    </div>

    <!-- 分隔线 -->
    <div class="gate-divider" />

    <!-- 底部 action 区 -->
    <div class="gate-actions">
      <!-- 左：确认预演结果（仅在 needsManualConfirm=true 时亮起） -->
      <a-button
        type="outline"
        status="warning"
        :disabled="!needsManualConfirm"
        class="btn-approve"
        @click="onApprovePreview"
      >
        ✓ 确认预演结果
      </a-button>

      <!-- 右：发布 Skill。不可发布时套 tooltip 解释差几项 -->
      <a-tooltip
        v-if="!canPublish"
        :content="`尚有 ${failedCount} 项未通过`"
        position="top"
      >
        <a-button
          type="primary"
          status="success"
          :disabled="true"
          :loading="publishing"
          class="btn-publish"
        >
          发布 Skill
        </a-button>
      </a-tooltip>
      <a-button
        v-else
        type="primary"
        status="success"
        :loading="publishing"
        class="btn-publish"
        @click="onPublish"
      >
        发布 Skill
      </a-button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * CreationGateChecklist
 * ---------------------
 * Skill 创建向导 AI 合成完成后，取代原本「直接发布」按钮。
 *
 * 显示后端 `gate_status` 返回的 6 项发布前检查：
 *   intent_clear / schema_valid / contract_consistency /
 *   sandbox_preview_passed / regression_ready / manual_confirmation
 *
 * 其中 `manual_confirmation`（人工确认沙箱预演）显性化为
 * 一个独立按钮 —— 用户点「确认预演结果」后父组件去调
 * reviewTaskContract({checkpoint:'preview', decision:'approved'})
 * 使这一项通过，然后才能发布。
 */
import { computed } from 'vue'
import {
  IconCheckCircleFill,
  IconExclamationCircleFill,
  IconStop,
} from '@arco-design/web-vue/es/icon'

interface GateItem {
  key: string
  label: string
  passed: boolean
  detail?: string
}

const props = withDefaults(
  defineProps<{
    /** 6 项检查。父组件从 skill_ready_enriched frame 的 gate.items 直接透传 */
    items: GateItem[]
    /** 是否可发布 = items.every(passed) && !publishing（由父组件计算好传入） */
    canPublish: boolean
    /** 父组件正在调 finalize，发布按钮 loading */
    publishing?: boolean
    /** 是否仅差 manual_confirmation 这一项（其他全过，唯独 manual_confirmation 未 pass） */
    needsManualConfirm?: boolean
  }>(),
  {
    publishing: false,
    needsManualConfirm: false,
  },
)

const emit = defineEmits<{
  /** 点「确认预演结果」按钮 */
  (e: 'approve-preview'): void
  /** 点「发布 Skill」按钮 */
  (e: 'publish'): void
}>()

// 通过 / 未通过数量
const passedCount = computed(() => props.items.filter((i) => i.passed).length)
const failedCount = computed(() => props.items.length - passedCount.value)

/**
 * 判断某一项是不是「待用户手动确认预演」的特殊状态：
 * 当 key === manual_confirmation 且未通过，且父组件标注 needsManualConfirm=true 时，
 * 用警示橙图标，而不是灰色 Stop 图标。
 */
function isPendingManualConfirm(item: GateItem): boolean {
  return (
    item.key === 'manual_confirmation' &&
    !item.passed &&
    !!props.needsManualConfirm
  )
}

function onApprovePreview() {
  emit('approve-preview')
}

function onPublish() {
  emit('publish')
}
</script>

<style scoped>
/* ===== 外层卡片 ===== */
.creation-gate-card {
  padding: 16px;
  border-radius: 8px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}

/* ===== 顶部 title 行 ===== */
.gate-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.title-main {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 16px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.title-icon {
  font-size: 16px;
}
.title-text {
  line-height: 1;
}
.title-progress {
  font-size: 12px;
  color: var(--ai-ink-3);
}

/* ===== checklist ===== */
.gate-list {
  margin-top: 8px;
}
.gate-row {
  display: grid;
  grid-template-columns: 20px 1fr auto;
  gap: 10px;
  align-items: center;
  padding: 8px 0;
}
.row-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  font-size: 14px;
}
.row-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
  line-height: 1.4;
}
/* 未通过时 label 降一档色阶 */
.row-label.label-dim {
  color: var(--ai-ink-2);
}
.row-detail {
  font-size: 11px;
  color: var(--ai-ink-4);
  line-height: 1.4;
  text-align: right;
}

/* ===== 图标语义色 ===== */
.icon-pass {
  color: var(--ai-ok);
}
.icon-warn {
  color: var(--ai-warn);
}
.icon-stop {
  color: var(--ai-ink-4);
}

/* ===== 分隔线 ===== */
.gate-divider {
  height: 1px;
  background: var(--ai-border);
  margin: 16px 0;
}

/* ===== action 区：左右两端对齐 ===== */
.gate-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.btn-approve {
  flex: 0 0 auto;
}
.btn-publish {
  flex: 0 0 auto;
}

/* ===== 响应式：窄屏改为纵向堆叠，「发布 Skill」在上 ===== */
@media (max-width: 480px) {
  .gate-actions {
    flex-direction: column-reverse;
    align-items: stretch;
  }
  .btn-approve,
  .btn-publish {
    width: 100%;
  }
}
</style>
