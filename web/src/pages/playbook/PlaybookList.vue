<template>
  <div class="page-container playbook-list-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">Skills · Playbook</div>
        <h2 class="page-title">Playbook · 编排</h2>
        <p class="page-subtitle">多 Skill 组合为工作流；按业务场景串联调度、审批、回写。</p>
      </div>
      <a-space>
        <a-button class="ai-btn-like" @click="loadDependencyGraph"><icon-mind-mapping /> 依赖图</a-button>
        <a-button v-if="userStore.isEngineer" class="ai-btn-like" @click="showDesigner = true">
          <icon-robot /> AI 推荐编排
        </a-button>
        <a-button v-if="userStore.isEngineer" type="primary" class="ai-btn-like" @click="showCreate = true">
          <template #icon><icon-plus /></template>新建
        </a-button>
      </a-space>
    </div>

    <a-card class="page-list-card">
      <a-spin :loading="loading" class="pb-spin" style="width: 100%">
        <a-row :gutter="[16, 16]">
          <a-col v-for="pb in playbooks" :key="pb.name" :xs="24" :sm="12" :md="8">
            <a-card hoverable class="pb-card" @click="$router.push(`/playbook/${pb.file_name || pb.name}`)">
              <template #title>
                <div class="pb-title-row">
                  <span class="pb-name">{{ pb.name }}</span>
                  <a-tag size="small" color="arcoblue" class="pb-steps-tag">
                    <span class="pb-steps-num">{{ pb.steps_count || 0 }}</span> 步骤
                  </a-tag>
                </div>
              </template>
              <div class="pb-desc">{{ pb.description || '暂无描述' }}</div>
              <div class="pb-meta">
                <span class="pb-cron">{{ pb.trigger?.schedule || '手动触发' }}</span>
              </div>
              <template #actions>
                <a-button type="text" size="small" @click.stop="$router.push(`/playbook/${pb.file_name || pb.name}/edit`)">编辑</a-button>
                <a-button v-if="userStore.isEngineer" type="text" size="small" @click.stop="runPlaybook(pb.file_name || pb.name)">执行</a-button>
                <a-popconfirm v-if="userStore.isAdmin" content="确定要删除该编排吗？此操作不可恢复。" type="warning" @ok="handleDelete(pb.file_name || pb.name)">
                  <a-button type="text" size="small" status="danger" @click.stop>删除</a-button>
                </a-popconfirm>
              </template>
            </a-card>
          </a-col>
        </a-row>
        <SfEmptyState
          v-if="!loading && playbooks.length === 0"
          title="Playbook · 编排"
          description="暂无编排"
          hint="Playbook 把多个 Skill 按顺序/条件串成工作流。点击右上「新建」或「AI 推荐编排」开始。"
          :action-label="userStore.isEngineer ? '新建 Playbook' : ''"
          @action="showCreate = true"
        />
      </a-spin>
    </a-card>

    <!-- 新建弹窗 -->
    <a-modal v-model:visible="showCreate" title="新建 Playbook" @ok="handleCreate" :ok-loading="creating" width="560px">
      <a-form :model="createForm" layout="vertical">
        <a-form-item label="创建方式">
          <a-radio-group v-model="createForm.mode">
            <a-radio value="blank">空白</a-radio>
            <a-radio value="template">从模板</a-radio>
          </a-radio-group>
        </a-form-item>

        <!-- 模板选择 -->
        <a-form-item v-if="createForm.mode === 'template'" label="选择模板">
          <a-spin :loading="templatesLoading">
            <div class="template-list">
              <div
                v-for="t in templates" :key="t.file_name"
                class="template-item"
                :class="{ selected: createForm.template === t.file_name }"
                @click="selectTemplate(t)"
              >
                <div class="template-item-name">{{ t.name }}</div>
                <div class="template-item-desc">{{ t.description }}</div>
                <a-tag size="small" class="template-item-tag">{{ t.steps_count }} 步骤</a-tag>
              </div>
            </div>
            <a-empty v-if="!templatesLoading && templates.length === 0" description="暂无模板" />
          </a-spin>
        </a-form-item>

        <a-form-item label="名称" required><a-input v-model="createForm.name" placeholder="如：daily-ec-check" /></a-form-item>
        <a-form-item label="描述"><a-input v-model="createForm.description" /></a-form-item>
      </a-form>
    </a-modal>
    <!-- 依赖图弹窗 -->
    <a-modal v-model:visible="showGraph" title="Playbook 依赖图" width="800px" :footer="false">
      <div v-if="graphData" v-html="graphSvg" class="mermaid-container"></div>
      <a-empty v-else description="无依赖关系" />
    </a-modal>

    <!-- §7.3 AI Playbook Designer 弹窗 -->
    <a-modal v-model:visible="showDesigner" title="AI 推荐编排（§7.3）" width="720px"
             :ok-text="designResult && designResult.steps.length ? '应用为新 Playbook' : '生成推荐'"
             :ok-button-props="{ disabled: designResult ? !designResult.steps.length : !designerGoal.trim() }"
             @ok="handleDesignerOk" @cancel="resetDesigner" :ok-loading="designing">
      <!-- 新手引导横幅（首次打开显示，localStorage 记录） -->
      <div v-if="!designerTourSeen" class="designer-tour" data-testid="designer-tour">
        <div class="tour-head">
          <icon-thunderbolt />
          <strong>三步完成 AI 推荐编排</strong>
          <a-button size="mini" type="text" @click="dismissDesignerTour">我知道了</a-button>
        </div>
        <div class="tour-steps">
          <div class="tour-step">
            <span class="tour-num">1</span>
            <span class="tour-text">描述业务目标（越具体越好，如"高 ROI 投放自动筛查 + 审批 + 执行"）</span>
          </div>
          <div class="tour-step">
            <span class="tour-num">2</span>
            <span class="tour-text">AI 从现有 Skill 库里挑合适的步骤，给出依赖关系和失败策略</span>
          </div>
          <div class="tour-step">
            <span class="tour-num">3</span>
            <span class="tour-text">不满意可"重新描述"；满意则"应用为新 Playbook"落盘</span>
          </div>
        </div>
      </div>

      <div v-if="!designResult">
        <div class="designer-hint">描述你的业务目标，AI 会从现有 Skill 库中推荐一个编排流程。</div>
        <a-textarea v-model="designerGoal" :auto-size="{ minRows: 3, maxRows: 6 }"
                    placeholder="例如：我要做高 ROI 投放的自动化，先筛查再审批再执行..." />
      </div>
      <div v-else class="designer-result">
        <div class="designer-goal">
          <span class="dr-label">目标</span><span>{{ designResult.goal }}</span>
        </div>
        <div v-if="designResult.reasoning" class="designer-reasoning">
          <span class="dr-label">编排思路</span>
          <div>{{ designResult.reasoning }}</div>
        </div>
        <div class="designer-steps">
          <div v-for="(s, i) in designResult.steps" :key="i" class="designer-step">
            <div class="ds-num">{{ Number(i) + 1 }}</div>
            <div class="ds-body">
              <div class="ds-head">
                <strong class="ds-skill-name">{{ s.skill_name || s.skill_id }}</strong>
                <a-tag size="small" color="arcoblue" class="ds-skill-id">{{ s.skill_id }}</a-tag>
              </div>
              <div class="ds-purpose">{{ s.purpose }}</div>
              <div class="ds-routes">
                <span v-if="s.depends_on?.length" class="ds-route ds-deps">依赖 {{ s.depends_on.join(', ') }}</span>
                <span class="ds-route" :class="s.on_failure === 'retry' ? 'ds-retry' : 'ds-failure'">失败策略 {{ s.on_failure }}</span>
              </div>
            </div>
          </div>
        </div>
        <div v-if="designResult.warnings?.length" class="designer-warnings">
          <div class="dr-label dr-label-warn">注意</div>
          <ul>
            <li v-for="(w, i) in designResult.warnings" :key="i">{{ w }}</li>
          </ul>
        </div>
        <a-button size="small" type="text" @click="designResult = null">重新描述</a-button>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, watch } from 'vue'
import { Message } from '@arco-design/web-vue'
import { playbookApi as rawPlaybookApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { IconPlus, IconMindMapping, IconRobot, IconThunderbolt } from '@arco-design/web-vue/es/icon'
import mermaid from 'mermaid'
import DOMPurify from 'dompurify'
import request from '@/api/request'
import { SfEmptyState } from '@/components/common'

const playbookApi: any = rawPlaybookApi
const userStore = useUserStore()
const loading = ref(false)
const playbooks = ref<any[]>([])
const showCreate = ref(false)
const creating = ref(false)
const templates = ref<any[]>([])
const templatesLoading = ref(false)
const createForm = reactive({ mode: 'blank', template: '', name: '', description: '', templateSteps: [] })

async function loadPlaybooks() {
  loading.value = true
  try { playbooks.value = await playbookApi.list() } finally { loading.value = false }
}

async function loadTemplates() {
  templatesLoading.value = true
  try {
    templates.value = await request.get('/playbooks/templates')
  } catch { templates.value = [] }
  finally { templatesLoading.value = false }
}

function selectTemplate(t: any) {
  createForm.template = t.file_name
  createForm.templateSteps = t.steps || []
  if (!createForm.name) createForm.name = t.file_name
  if (!createForm.description) createForm.description = t.description
}

// 首次打开创建弹窗时加载模板
watch(showCreate, (val) => {
  if (val && templates.value.length === 0) loadTemplates()
})

async function handleCreate() {
  if (!createForm.name) { Message.warning('请输入名称'); return }
  creating.value = true
  try {
    const steps = createForm.mode === 'template' ? createForm.templateSteps : []
    await playbookApi.create({
      name: createForm.name,
      description: createForm.description,
      steps,
    })
    showCreate.value = false
    Message.success('创建成功')
    createForm.mode = 'blank'
    createForm.name = ''
    createForm.description = ''
    createForm.template = ''
    createForm.templateSteps = []
    await loadPlaybooks()
  } catch (e: any) {
    Message.error(e._message || '创建失败')
  } finally { creating.value = false }
}

async function runPlaybook(name: string) {
  try {
    await playbookApi.run(name, {})
    Message.success('已触发执行')
  } catch (e: any) { Message.error(e._message || '执行失败') }
}

async function handleDelete(name: string) {
  try {
    await playbookApi.delete(name)
    Message.success('已删除')
    await loadPlaybooks()
  } catch (e: any) { Message.error(e._message || '删除失败') }
}

// ── 依赖图 ──
const showGraph = ref(false)
const graphData = ref<any>(null)
const graphSvg = ref('')

// ── §7.3 AI Playbook Designer ──
const showDesigner = ref(false)
const TOUR_KEY = 'sf-ai-designer-tour-seen'
const designerTourSeen = ref(localStorage.getItem(TOUR_KEY) === '1')

function dismissDesignerTour() {
  designerTourSeen.value = true
  localStorage.setItem(TOUR_KEY, '1')
}
const designerGoal = ref('')
const designing = ref(false)
const designResult = ref<any>(null)

async function handleDesignerOk() {
  if (!designResult.value) {
    // 阶段 1：生成推荐
    if (!designerGoal.value.trim()) return
    designing.value = true
    try {
      designResult.value = await playbookApi.design(designerGoal.value)
      if (!designResult.value.steps?.length) {
        Message.warning(designResult.value.warnings?.[0] || '未生成有效编排')
      }
    } catch (e: any) {
      Message.error(e._message || '生成失败')
    } finally { designing.value = false }
    return
  }
  // 阶段 2：应用为新 Playbook（匹配 executor schema）
  if (!designResult.value.steps.length) return
  designing.value = true
  try {
    const pbSteps = designResult.value.steps.map((s: Record<string, unknown>) => ({
      id: s.id,
      skill_id: s.skill_id,
      name: s.skill_name || s.skill_id,
      description: s.purpose,
      depends_on: s.depends_on || [],
      on_failure: s.on_failure || 'terminate',
    }))
    const pbName = `ai-designed-${Date.now()}`
    await playbookApi.create({
      name: pbName,
      description: `AI 推荐编排: ${designResult.value.goal}`,
      steps: pbSteps,
    })
    Message.success('已创建编排：' + pbName)
    resetDesigner()
    await loadPlaybooks()
  } catch (e: any) {
    Message.error(e._message || '创建失败')
  } finally { designing.value = false }
}

function resetDesigner() {
  showDesigner.value = false
  designerGoal.value = ''
  designResult.value = null
}

async function loadDependencyGraph() {
  try {
    graphData.value = await playbookApi.dependencyGraph()
    // 转换为 mermaid 语法
    let md = 'graph LR\n'
    const nodes = graphData.value.nodes || []
    const edges = graphData.value.edges || []
    nodes.forEach((n: Record<string, unknown>) => { md += `  ${n.id}["${n.label || n.id}"]\n` })
    edges.forEach((e: Record<string, unknown>) => { md += `  ${e.source} --> ${e.target}\n` })
    const { svg } = await mermaid.render('dep-graph', md)
    // 与 PlaybookWorkbench.vue 保持一致：限定 svg profile，不允许 <iframe> / 事件属性
    graphSvg.value = DOMPurify.sanitize(svg, {
      USE_PROFILES: { svg: true, svgFilters: true },
      ADD_TAGS: ['style'],
      ADD_ATTR: ['id', 'class', 'style', 'xmlns'],
    })
    showGraph.value = true
  } catch (e) { Message.error(String((e as Record<string, unknown>)?._message || '加载依赖图失败')) }
}

onMounted(loadPlaybooks)
</script>

<style scoped>
/* ── 设计稿 page chrome 覆盖 —— 与 SkillList / AdminUsers 同模式 ── */
.playbook-list-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.playbook-list-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.playbook-list-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.playbook-list-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

/* 顶部按钮统一外形 —— 30px / 6px 圆角 / 12.5px 字号，对应 .ai-btn */
.playbook-list-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary)) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.playbook-list-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.playbook-list-page :deep(.arco-btn-primary.ai-btn-like) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.playbook-list-page :deep(.arco-btn-primary.ai-btn-like:hover) {
  background: #000;
  border-color: #000;
}

/* ── 表头/单元格 —— 11.5px uppercase ink-4；12.5px ink-1；hover surface-2 ── */
.playbook-list-page :deep(.arco-table-th) {
  background: var(--ai-surface) !important;
  color: var(--ai-ink-4) !important;
  font-size: 11.5px !important;
  font-weight: 500 !important;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 8px 12px !important;
}
.playbook-list-page :deep(.arco-table-th .arco-table-th-title) {
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.playbook-list-page :deep(.arco-table-td) {
  font-size: 12.5px !important;
  color: var(--ai-ink-1) !important;
  border-bottom: 1px solid var(--ai-border) !important;
  padding: 10px 12px !important;
  background: var(--ai-surface) !important;
}
.playbook-list-page :deep(.arco-table-tr:hover .arco-table-td),
.playbook-list-page :deep(.arco-table-tr-hover .arco-table-td) {
  background: var(--ai-surface-2) !important;
}

/* ── a-tag → ai-pill (20px / 4px / 11px / 500) —— 5 档配色 ── */
.playbook-list-page :deep(.arco-tag.arco-tag-size-small),
.playbook-list-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}
.playbook-list-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.playbook-list-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.playbook-list-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.playbook-list-page :deep(.arco-tag-color-orange),
.playbook-list-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.playbook-list-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

/* ── Playbook 卡片 ── */
.pb-spin { width: 100%; }
.pb-card {
  cursor: pointer;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  box-shadow: none;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.pb-card :deep(.arco-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.pb-card:hover {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.pb-card:hover :deep(.arco-card) {
  background: var(--ai-surface-2) !important;
  border-color: var(--ai-border-2) !important;
  box-shadow: none !important;
}
.pb-card :deep(.arco-card-header) {
  border-bottom: 1px solid var(--ai-border);
  padding: 10px 14px;
}
.pb-card :deep(.arco-card-body) {
  padding: 12px 14px;
}
.pb-card :deep(.arco-card-actions) {
  border-top: 1px solid var(--ai-border);
  padding: 6px 8px;
}
.pb-title-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.pb-name {
  font-family: var(--ai-font-mono);
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
  font-variant-numeric: tabular-nums;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pb-steps-tag {
  flex-shrink: 0;
}
.pb-steps-num {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  margin-right: 2px;
}
.pb-desc {
  color: var(--ai-ink-3);
  margin-bottom: 10px;
  font-size: 12.5px;
  line-height: 1.6;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.pb-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
  color: var(--ai-ink-4);
}
.pb-cron {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--ai-ink-4);
}

/* ── 模板选择 ── */
.template-list { display: flex; flex-direction: column; gap: 8px; }
.template-item {
  padding: 12px 14px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  cursor: pointer;
  background: var(--ai-surface);
  transition: background 0.15s ease, border-color 0.15s ease;
}
.template-item:hover {
  border-color: var(--ai-border-2);
  background: var(--ai-surface-2);
}
.template-item.selected {
  border-color: var(--ai-ink-1);
  background: var(--ai-surface-2);
}
.template-item-name {
  font-weight: 600;
  font-size: 13px;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
}
.template-item-desc {
  font-size: 12px;
  color: var(--ai-ink-3);
  margin-top: 2px;
}
.template-item-tag {
  margin-top: 6px;
}

.mermaid-container { overflow: auto; text-align: center; }
.mermaid-container :deep(svg) { max-width: 100%; height: auto; }

/* ── §7.3 Playbook Designer modal ── */
.designer-hint {
  font-size: 12px;
  color: var(--ai-ink-3);
  margin-bottom: 8px;
}

.designer-tour {
  margin-bottom: 12px;
  padding: 12px 14px;
  border-radius: var(--ai-radius);
  background: var(--ai-info-soft);
  border: 1px solid transparent;
  border-left: 2px solid var(--ai-info);
}
.tour-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--ai-info);
  margin-bottom: 8px;
}
.tour-head strong { flex: 1; font-weight: 600; }
.tour-steps {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tour-step {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 12px;
  color: var(--ai-ink-2);
  line-height: 1.5;
}
.tour-num {
  flex: 0 0 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--ai-info);
  color: var(--ai-surface);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.designer-result { display: flex; flex-direction: column; gap: 14px; }
.dr-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-right: 8px;
}
.dr-label-warn { color: var(--ai-warn); }
.designer-goal {
  display: flex;
  align-items: baseline;
  padding: 10px 12px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  font-size: 12.5px;
  color: var(--ai-ink-1);
}
.designer-reasoning {
  font-size: 12px;
  color: var(--ai-ink-2);
  line-height: 1.6;
}
.designer-reasoning > div { margin-top: 2px; }
.designer-steps { display: flex; flex-direction: column; gap: 8px; }
.designer-step {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
}
.ds-num {
  width: 24px; height: 24px; border-radius: 50%; flex-shrink: 0;
  background: var(--ai-ink-1); color: var(--ai-surface);
  display: flex; align-items: center; justify-content: center;
  font-size: 11.5px; font-weight: 600;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.ds-body { flex: 1; min-width: 0; }
.ds-head { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.ds-skill-name {
  font-size: 12.5px;
  color: var(--ai-ink-1);
  font-weight: 600;
  font-family: var(--ai-font-mono);
}
.ds-skill-id {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.ds-purpose {
  font-size: 12px;
  color: var(--ai-ink-3);
  line-height: 1.5;
}
.ds-routes {
  display: flex;
  gap: 10px;
  margin-top: 6px;
  font-size: 11px;
}
.ds-route {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.ds-deps { color: var(--ai-ink-4); }
.ds-failure { color: var(--ai-bad); }
.ds-retry { color: var(--ai-warn); }
.designer-warnings {
  padding: 8px 12px;
  background: var(--ai-warn-soft);
  border-left: 2px solid var(--ai-warn);
  border-radius: var(--ai-radius);
}
.designer-warnings ul {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--ai-ink-2);
}
.designer-warnings li { line-height: 1.6; }
</style>
