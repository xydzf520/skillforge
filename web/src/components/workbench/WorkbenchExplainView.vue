<template>
  <div class="explain-view">
    <section class="ev-hero">
      <div class="ev-headline">
        <div class="ev-kicker">讲解视图</div>
        <h2>{{ meta.name || skillId }}</h2>
        <p>{{ goal || meta.description || '当前 Skill 暂无明确目标描述。' }}</p>
      </div>
      <div class="ev-badges">
        <a-tag v-if="meta.department" size="small">{{ meta.department }}</a-tag>
        <a-tag v-if="meta.trigger_type" size="small" color="arcoblue">{{ meta.trigger_type }}</a-tag>
        <a-tag v-if="meta.risk_level" size="small" :color="meta.risk_level === 'R4' ? 'red' : meta.risk_level === 'R3' ? 'orange' : 'green'">
          {{ meta.risk_level }}
        </a-tag>
        <a-tag v-if="readiness" size="small" :color="readiness.can_publish ? 'green' : 'orange'">
          {{ readiness.can_publish ? '当前可发布' : `${readiness.blocker_count || 0} 个发布阻断` }}
        </a-tag>
      </div>
    </section>

    <section class="ev-grid">
      <article class="ev-card">
        <div class="ev-card-title">执行摘要</div>
        <p class="ev-card-copy">{{ explainPack.executive_summary }}</p>
      </article>

      <article class="ev-card">
        <div class="ev-card-title">触发方式</div>
        <p class="ev-card-copy">{{ explainPack.trigger_summary }}</p>
      </article>
    </section>

    <section class="ev-card">
      <div class="ev-card-title">决策阶梯</div>
      <div v-if="explainPack.decision_ladder.length" class="ev-list">
        <div v-for="item in explainPack.decision_ladder" :key="item.id" class="ev-list-item">
          <div class="ev-list-name">{{ item.name }}</div>
          <div class="ev-list-copy">{{ item.summary }}</div>
        </div>
      </div>
      <div v-else class="ev-empty">暂无规则，当前更像一个空白骨架。</div>
    </section>

    <section class="ev-grid">
      <article class="ev-card">
        <div class="ev-card-title">参数影响</div>
        <div v-if="explainPack.parameter_impacts.length" class="ev-list">
          <div v-for="item in explainPack.parameter_impacts" :key="item.name" class="ev-list-item">
            <div class="ev-list-name">{{ item.name }} = {{ item.value }}</div>
            <div class="ev-list-copy">{{ item.impact }}</div>
          </div>
        </div>
        <div v-else class="ev-empty">暂无参数。</div>
      </article>

      <article class="ev-card">
        <div class="ev-card-title">输出契约</div>
        <div v-if="explainPack.output_contract.length" class="ev-list">
          <div v-for="item in explainPack.output_contract" :key="item.name" class="ev-list-item">
            <div class="ev-list-name">{{ item.name }}</div>
            <div class="ev-list-copy">{{ item.format }} → {{ item.recipient }}</div>
          </div>
        </div>
        <div v-else class="ev-empty">暂无输出定义。</div>
      </article>
    </section>

    <section class="ev-card">
      <div class="ev-card-title">测试信心</div>
      <div class="ev-grid ev-grid-tight">
        <article class="ev-card ev-subcard">
          <div class="ev-card-title">覆盖概览</div>
          <ul class="ev-stats">
            <li>样例总数：{{ explainPack.test_confidence.total_cases }}</li>
            <li>已覆盖规则：{{ explainPack.test_confidence.covered_rules }}</li>
            <li>未覆盖规则：{{ explainPack.test_confidence.uncovered_rules }}</li>
          </ul>
        </article>
        <article class="ev-card ev-subcard">
          <div class="ev-card-title">结论</div>
          <p class="ev-card-copy">{{ explainPack.test_confidence.summary }}</p>
        </article>
      </div>
    </section>

    <section v-if="canStartReview" class="ev-next-action">
      <div class="ev-next-action-header">
        <icon-check-circle :size="16" />
        <span>理解完毕？</span>
      </div>
      <p class="ev-card-copy">确认 Skill 逻辑无误后，可以发起审核流程。</p>
      <a-button type="primary" size="small" @click="$emit('start-review')">
        <template #icon><icon-send /></template>
        发起审核
      </a-button>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, type PropType } from 'vue'
import { IconCheckCircle, IconSend } from '@arco-design/web-vue/es/icon'

const props = defineProps({
  doc: { type: Object as PropType<any>, default: () => ({}) },
  skillId: { type: String, default: '' },
  readiness: { type: Object as PropType<any>, default: null },
  explainPack: { type: Object as PropType<any>, default: null },
  canStartReview: { type: Boolean, default: false },
})

defineEmits(['start-review'])

const meta = computed(() => props.doc?.meta || {})
const goal = computed(() => props.doc?.goal || '')
const rules = computed(() => props.doc?.rules || [])
const params = computed(() => props.doc?.params || [])
const outputs = computed(() => props.doc?.output_table || [])
const tests = computed(() => props.doc?.test_cases || [])

const localExplainPack = computed(() => {
  const firstRule = rules.value[0]
  const firstCondition = firstRule?.branches?.[0]?.condition || '业务条件'
  const executiveSummary = rules.value.length
    ? `这个 Skill 的核心目标是「${goal.value || meta.value.description || '未命名目标'}」。它会先判断「${firstCondition}」，再根据不同分支给出动作建议。整体上更适合被业务同学当作“规则说明书”和“判断依据”来理解。`
    : '这个 Skill 目前主要定义了基础信息和目标，但还没有足够的决策规则来支撑自动判断。'
  const decisionLadder = rules.value.map((rule: any) => {
    const branches = Array.isArray(rule.branches) ? rule.branches : []
    const branchSummary = branches.length
      ? branches
          .slice(0, 3)
          .map((branch: any) => `${branch.condition || '条件'}时，${branch.conclusion || branch.action || '给出处理结果'}`)
          .join('；')
      : '当前还没有配置分支'
    return {
      id: rule.id || rule.name,
      name: rule.name || rule.id || '未命名规则',
      summary: branchSummary,
    }
  })
  const parameterImpacts = params.value.map((item: any) => ({
    name: item.name || '未命名参数',
    value: item.default_value ?? item.value ?? '',
    impact: `${item.name || '该参数'} 会影响相关阈值判断与分支命中结果。`,
  }))
  const outputContract = outputs.value.map((item: any) => ({
    name: item.name || item.field || '未命名输出',
    recipient: item.recipient || '待确认接收方',
    format: item.format || 'text',
  }))
  const coveredRules = Math.min(rules.value.length, tests.value.length)
  const uncoveredRules = Math.max(0, rules.value.length - coveredRules)
  return {
    executive_summary: executiveSummary,
    trigger_summary: meta.value.trigger_type ? `当前通过「${meta.value.trigger_type}」触发。` : '当前没有显式触发方式，默认按手动执行理解。',
    decision_ladder: decisionLadder,
    parameter_impacts: parameterImpacts,
    output_contract: outputContract,
    test_confidence: {
      total_cases: tests.value.length,
      covered_rules: coveredRules,
      uncovered_rules: uncoveredRules,
      summary: tests.value.length
        ? (uncoveredRules > 0 ? `当前已有 ${tests.value.length} 个测试样例，但仍有 ${uncoveredRules} 条规则缺少对应覆盖。` : `当前测试样例已基本覆盖主要规则，适合做业务讲解和审批参考。`)
        : '当前还没有测试样例，讲解时需要明确说明这套规则尚未经过足够验证。',
    },
  }
})

const explainPack = computed(() => props.explainPack || localExplainPack.value)

function formatObject(value: unknown): string {
  if (!value) return '{}'
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}
</script>

<style scoped>
.explain-view {
  height: 100%;
  overflow-y: auto;
  padding: 28px 32px 36px;
  background: var(--ai-surface);
}
.ev-hero, .ev-card {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--sf-panel-radius);
  box-shadow: var(--ai-shadow-2);
}
.ev-hero {
  display: flex;
  justify-content: space-between;
  gap: 24px;
  padding: 24px 26px;
  margin-bottom: 18px;
}
.ev-kicker {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ai-warn);
  margin-bottom: 10px;
}
.ev-headline h2 {
  margin: 0 0 8px;
  font-size: 24px;
  line-height: 1.1;
  color: var(--ai-ink-1);
}
.ev-headline p {
  margin: 0;
  max-width: 720px;
  color: var(--ai-ink-2);
  line-height: 1.7;
}
.ev-badges {
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 6px;
  max-width: 260px;
}
.ev-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  margin-bottom: 18px;
}
.ev-card {
  padding: 18px 20px;
  margin-bottom: 18px;
}
.ev-card-title {
  font-size: 13px;
  font-weight: 800;
  color: var(--ai-ink-1);
  margin-bottom: 12px;
}
.ev-card-copy {
  margin: 0;
  color: var(--ai-ink-2);
  line-height: 1.75;
}
.ev-stats {
  margin: 0;
  padding-left: 18px;
  color: var(--ai-ink-2);
  line-height: 1.8;
}
.ev-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.ev-list-item {
  padding: 12px 14px;
  border-radius: var(--sf-panel-radius);
  background: var(--ai-surface-2);
}
.ev-list-name {
  font-size: 13px;
  font-weight: 800;
  color: var(--ai-ink-1);
  margin-bottom: 6px;
}
.ev-list-copy {
  font-size: 13px;
  line-height: 1.65;
  color: var(--ai-ink-3);
}
.ev-chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.ev-chip {
  display: inline-flex;
  align-items: center;
  padding: 6px 10px;
  border-radius: 999px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 700;
}
.ev-empty {
  color: var(--ai-ink-4);
  font-size: 13px;
}
.ev-next-action {
  margin-top: 16px;
  padding: 18px 20px;
  background: var(--ai-surface);
  border: 1px dashed rgba(49,86,163,0.35);
  border-radius: var(--sf-panel-radius);
  box-shadow: var(--ai-shadow-2);
}
.ev-next-action-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 800;
  color: var(--ai-ink-1);
  margin-bottom: 10px;
}
.ev-next-action .ev-card-copy {
  margin-bottom: 14px;
}
@media (max-width: 960px) {
  .ev-hero, .ev-grid {
    grid-template-columns: 1fr;
    display: grid;
  }
  .ev-hero {
    display: grid;
  }
}
</style>
