<template>
  <div
    class="bp"
    :class="{ 'bp-result': blueprint || creationActive }"
    style="display: flex; flex-direction: column; align-items: center; justify-content: flex-start; width: 100%; height: 100%; overflow-y: auto;"
  >
    <!-- v7 一步到位：创建中显示流式进度页 -->
    <SkillCreationProgress
      v-if="creationActive"
      ref="creationProgressRef"
      :initial-message="creationMessage"
      :resume-draft-id="resumeDraftId"
      :resume-data="resumeData"
      @cancel="onCreationCancel"
      @created="onCreationCreated"
    />

    <!-- 第一阶段：输入业务问题 -->
    <div v-if="!blueprint && !interviewRound && !taskContract && !creationActive" class="bp-input-stage">
      <div class="bp-hero">
        <div class="bp-hero-icon"><icon-bulb :size="28" /></div>
        <h2>创建新 Skill</h2>
        <p>描述你想让 Skill 替你做什么判断或流程</p>
      </div>

      <div class="bp-main-input">
        <a-textarea
          v-model="userInput"
          :auto-size="{ minRows: 3, maxRows: 8 }"
          placeholder="例如：根据 ROI 和预算消耗速度，判断是否加预算/降价/暂停&#10;&#10;支持粘贴 SOP、会议纪要、现有规则、接口文档..."
          class="bp-textarea"
          :max-length="50000"
          show-word-limit
        />

        <!-- 主 CTA：一句话 → 任务合同 -->
        <a-button
          class="bp-primary-cta"
          type="primary"
          size="large"
          :loading="generating"
          :disabled="!userInput.trim()"
          @click="generateTaskContract"
        >
          <icon-message :size="16" style="margin-right:6px" />生成任务合同
        </a-button>

        <!-- 首次使用引导 -->
        <div v-if="!hasSeenBlueprint" class="bp-first-hint">
          💡 先确认任务目标、权限和预演结果，再进入 Skill 骨架编辑
        </div>

        <!-- O5: 原"4 轮采访 / 直接生成"两条次级 CTA 已收拢到下方"更多创建方式"折叠区，层级从 3 层降到 2 层 -->
      </div>

      <!-- Phase 2 4-轮采访模式 -->
    </div>

    <!-- 采访模式：逐轮问答 -->
    <div v-else-if="interviewRound && !blueprint" class="bp-interview-stage">
      <div class="bp-interview-header">
        <div class="bp-interview-progress">
          <div v-for="(r, i) in ROUND_ORDER" :key="r"
               class="step-dot"
               :class="{ active: ROUND_ORDER.indexOf(interviewRound.id) === i, done: ROUND_ORDER.indexOf(interviewRound.id) > i }">
            {{ Number(i) + 1 }}
          </div>
        </div>
        <a-button size="small" type="text" @click="cancelInterview">取消</a-button>
      </div>
      <div class="bp-interview-title">
        <h3>{{ interviewRound.title }}</h3>
        <p>{{ interviewRound.description }}</p>
      </div>
      <div v-if="interviewRound.inferred && Object.keys(interviewRound.inferred).length" class="bp-inferred">
        <div class="bp-inferred-head">从你的描述推断</div>
        <div v-for="(v, k) in interviewRound.inferred" :key="k" class="bp-inferred-row">
          <span class="bp-inferred-k">{{ k }}</span>
          <span class="bp-inferred-v">{{ v }}</span>
        </div>
      </div>
      <div class="bp-questions">
        <div v-for="q in interviewRound.questions" :key="q.id" class="bp-question-card">
          <div class="bp-q-prompt">{{ q.prompt }}</div>
          <div v-if="q.hint" class="bp-q-hint">{{ q.hint }}</div>
          <!-- 单选 -->
          <div v-if="q.kind === 'single'" class="bp-choices">
            <button v-for="c in q.candidates" :key="c"
                    class="bp-choice"
                    :class="{ active: currentAnswers[q.id] === c }"
                    @click="currentAnswers[q.id] = c">
              {{ c }}
            </button>
          </div>
          <!-- 多选 -->
          <div v-else-if="q.kind === 'multi'" class="bp-choices">
            <button v-for="c in q.candidates" :key="c"
                    class="bp-choice"
                    :class="{ active: (currentAnswers[q.id] || []).includes(c) }"
                    @click="toggleMulti(q.id, c)">
              {{ c }}
            </button>
          </div>
          <!-- 标签 -->
          <div v-else-if="q.kind === 'tags'" class="bp-tags-input">
            <a-tag v-for="(t, i) in (currentAnswers[q.id] || [])" :key="i" closable @close="removeTag(q.id, Number(i))">{{ t }}</a-tag>
            <a-input size="small" v-model="tagDraft[q.id]" placeholder="+ 标签 回车添加" style="width:140px"
                     @keydown.enter="addTag(q.id)" />
          </div>
          <!-- 文本 -->
          <a-textarea v-else v-model="currentAnswers[q.id]" :auto-size="{ minRows: 1, maxRows: 3 }" placeholder="简短回答..." />
        </div>
      </div>
      <div class="bp-interview-footer">
        <a-button :loading="generating" @click="skipRound">跳过本轮</a-button>
        <a-button type="primary" class="ai-btn" :loading="generating" @click="advanceInterview">
          <template v-if="interviewRound.id === 'round4_deployment'">完成采访，合成 Skill</template>
          <template v-else>下一轮</template>
        </a-button>
      </div>
    </div>

    <!-- v7 F2: 生成中显示业务语言进度条 -->
    <div v-if="generating && streamMessage && !taskContract && !creationActive" class="bp-progress-stage">
      <AgentProgressStream
        :message="streamMessage"
        mode="save"
        :auto-start="true"
      />
    </div>

    <div v-else-if="taskContract && !blueprint" class="bp-result-stage bp-contract-stage">
      <div class="bp-result-header">
        <h3>任务合同确认</h3>
        <a-space>
          <a-tag :color="taskContract.contract?.risks?.level === 'R3' ? 'red' : 'orange'">
            {{ taskContract.contract?.risks?.level || 'R1' }}
          </a-tag>
          <a-button size="small" @click="resetAll">重新描述</a-button>
          <a-button
            size="small"
            type="primary"
            class="ai-btn"
            :disabled="!taskContract.gate?.can_generate_skill"
            @click="advanceFromTaskContract"
          >
            继续生成骨架
          </a-button>
        </a-space>
      </div>

      <div class="bp-cards">
        <div class="bp-card bp-card-wide">
          <div class="bp-card-head"><icon-bulb :size="15" style="color:var(--ai-warn)" /> 平台理解</div>
          <div class="bp-contract-summary">
            <div class="bp-contract-row"><span>目标</span><strong>{{ taskContract.contract?.goal }}</strong></div>
            <div class="bp-contract-row"><span>触发</span><strong>{{ taskContract.contract?.trigger?.description || taskContract.contract?.trigger?.type }}</strong></div>
            <div class="bp-contract-row"><span>输出</span><strong>{{ taskContract.contract?.output?.adapter }} → {{ taskContract.contract?.output?.recipient || '待确认' }}</strong></div>
          </div>
          <div class="bp-contract-actions">
            <a-button
              v-if="canApproveCheckpoint('target')"
              size="small"
              type="primary"
              :loading="reviewingCheckpoint === 'target'"
              @click="submitCheckpointReview('target')"
            >
              确认目标
            </a-button>
            <a-tag v-else :color="checkpointState('target')?.approved ? 'green' : 'orange'">
              {{ checkpointState('target')?.approved ? '已确认' : '待确认' }}
            </a-tag>
          </div>
        </div>

        <div class="bp-card">
          <div class="bp-card-head"><icon-import :size="15" style="color:var(--ai-info)" /> 输入依赖</div>
          <div class="bp-contract-list">
            <div v-for="(inp, i) in (taskContract.contract?.input || [])" :key="i" class="bp-contract-item">
              <strong>{{ inp.name }}</strong>
              <span>{{ inp.source }} / {{ inp.type }}</span>
            </div>
          </div>
        </div>

        <div class="bp-card">
          <div class="bp-card-head"><icon-export :size="15" style="color:var(--ai-ok)" /> 权限与风险</div>
          <div class="bp-contract-list">
            <div v-for="(perm, i) in (taskContract.contract?.permissions || [])" :key="i" class="bp-contract-item">
              <strong>{{ perm.action }}</strong>
              <span>{{ perm.target }}{{ perm.reversible ? '' : ' / 不可逆' }}</span>
            </div>
          </div>
          <div class="bp-contract-actions">
            <a-button
              v-if="canApproveCheckpoint('permission')"
              size="small"
              type="primary"
              :loading="reviewingCheckpoint === 'permission'"
              @click="submitCheckpointReview('permission')"
            >
              授予权限
            </a-button>
            <a-tag v-else :color="checkpointState('permission')?.approved ? 'green' : 'orange'">
              {{ checkpointState('permission')?.decision === 'not_required' ? '无需确认' : (checkpointState('permission')?.approved ? '已确认' : '待确认') }}
            </a-tag>
          </div>
        </div>

        <div class="bp-card bp-card-wide">
          <div class="bp-card-head"><icon-message :size="15" style="color:rgb(var(--cyan-6))" /> 真实预演</div>
          <pre class="bp-contract-preview">{{ taskContract.preview?.rendered_output }}</pre>
          <PreviewCardActions :preview="taskContract.preview" />
          <div class="bp-contract-actions">
            <a-button
              v-if="canApproveCheckpoint('preview')"
              size="small"
              type="primary"
              :loading="reviewingCheckpoint === 'preview'"
              @click="submitCheckpointReview('preview')"
            >
              预演符合预期
            </a-button>
            <a-tag v-else :color="checkpointState('preview')?.approved ? 'green' : 'orange'">
              {{ checkpointState('preview')?.decision === 'not_required' ? '无需确认' : (checkpointState('preview')?.approved ? '已确认' : '待确认') }}
            </a-tag>
          </div>
        </div>

        <div class="bp-card">
          <div class="bp-card-head"><icon-settings :size="15" style="color:rgb(var(--purple-6))" /> 责任说明</div>
          <div class="bp-contract-note">
            失败会通知责任人，连续 3 次失败自动停用，并可回滚到最近发布版本。
          </div>
          <div class="bp-contract-actions">
            <a-button
              v-if="canApproveCheckpoint('responsibility')"
              size="small"
              type="primary"
              :loading="reviewingCheckpoint === 'responsibility'"
              @click="submitCheckpointReview('responsibility')"
            >
              我已知晓
            </a-button>
            <a-tag v-else :color="checkpointState('responsibility')?.approved ? 'green' : 'orange'">
              {{ checkpointState('responsibility')?.approved ? '已确认' : '待确认' }}
            </a-tag>
          </div>
        </div>

        <div class="bp-card">
          <div class="bp-card-head"><icon-list :size="15" style="color:var(--ai-warn)" /> 60 分门禁</div>
          <div class="bp-gate-list">
            <div v-for="item in (taskContract.gate?.items || [])" :key="item.key" class="bp-gate-item">
              <a-tag size="small" :color="item.passed ? 'green' : 'orange'">{{ item.passed ? '通过' : '待完成' }}</a-tag>
              <div class="bp-gate-copy">
                <strong>{{ item.label }}</strong>
                <span>{{ item.detail }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 更多创建方式：折叠区 -->
    <div v-if="!blueprint && !interviewRound && !taskContract && !creationActive" class="bp-input-stage bp-input-templates">
      <button class="bp-more-toggle" @click="showMore = !showMore">
        更多创建方式
        <icon-down v-if="!showMore" :size="12" />
        <icon-up v-else :size="12" />
      </button>

      <div v-if="showMore" class="bp-more-panel">
        <!-- O5: 把原本跟主 CTA 并列的 4 轮采访 / 直接生成 移到这里；高频场景仍走主 CTA -->
        <div class="bp-or">其他 AI 生成方式</div>
        <div class="bp-more-actions">
          <button class="bp-more-action" :disabled="!userInput.trim() || generating" @click="startInterview">
            <icon-message :size="16" />
            <div class="bp-more-action-body">
              <div class="bp-more-action-title">4 轮采访式创建</div>
              <div class="bp-more-action-desc">逐轮回答问题 → 沉淀结构化 Blueprint</div>
            </div>
          </button>
          <button class="bp-more-action" :disabled="!userInput.trim() || generating" @click="generateBlueprint">
            <icon-robot :size="16" />
            <div class="bp-more-action-body">
              <div class="bp-more-action-title">直接生成</div>
              <div class="bp-more-action-desc">跳过采访，AI 一次性生成 Blueprint</div>
            </div>
          </button>
        </div>

        <div class="bp-or">从模板开始</div>
        <div class="bp-templates">
          <button v-for="t in templates" :key="t.type" class="bp-tpl" @click="applyTemplate(t)">
            <component :is="t.icon" :size="20" class="bp-tpl-icon" />
            <div class="bp-tpl-name">{{ t.name }}</div>
            <div class="bp-tpl-desc">{{ t.desc }}</div>
          </button>
        </div>

        <!-- Phase 4 Motif 库：从现有 Skill 提取的可复用模式 -->
        <div v-if="motifs.length" class="bp-or">从 Motif 库复用（从现有 Skill 自动学习）</div>
        <div v-if="motifs.length" class="bp-motifs">
          <button v-for="m in motifs" :key="m.id" class="bp-motif" :title="m.description" @click="applyMotif(m)">
            <div class="bp-motif-head">
              <span class="bp-motif-name">{{ m.name }}</span>
              <a-tag size="small" :color="(motifCategoryColor as Record<string, string>)[m.category] || 'blue'">{{ m.category }}</a-tag>
            </div>
            <div class="bp-motif-desc">{{ m.description }}</div>
            <div v-if="m.example_skill_ids?.length" class="bp-motif-examples">
              示例：{{ m.example_skill_ids.slice(0, 3).join(', ') }}
            </div>
          </button>
        </div>

        <div class="bp-import">
          <label class="bp-import-btn">
            <icon-upload :size="13" /> 导入 SKILL.md
            <input type="file" accept=".md" style="display:none" @change="handleImport" />
          </label>
        </div>
      </div>
    </div>

    <!-- 第二阶段：Blueprint 结构卡 -->
    <div v-if="blueprint" class="bp-result-stage">
      <div class="bp-result-header">
        <h3>
          Skill 方案预览
          <a-tag v-if="blueprint._fromSwarm" color="purple" size="small" style="margin-left:8px">Swarm 4-Agent</a-tag>
          <a-tag v-else-if="blueprint._fromInterview" color="arcoblue" size="small" style="margin-left:8px">4 轮采访</a-tag>
          <a-tag v-else-if="blueprint._fromContract" color="green" size="small" style="margin-left:8px">任务合同</a-tag>
        </h3>
        <a-space>
          <a-button size="small" @click="resetAll">重新描述</a-button>
          <a-button size="small" type="primary" class="ai-btn" @click="confirmBlueprint">确认并生成骨架</a-button>
        </a-space>
      </div>

      <!-- §8.4 Swarm 发现面板 -->
      <div v-if="blueprint._fromSwarm" class="bp-swarm-card">
        <div class="bp-swarm-head">
          <icon-apps :size="14" /> Swarm 协作产出
          <a-tag v-if="blueprint._swarmCanPublish" color="green" size="small">质检通过</a-tag>
          <a-tag v-else color="orange" size="small">需要修复</a-tag>
        </div>
        <div v-if="blueprint._swarmSummary" class="bp-swarm-summary">{{ blueprint._swarmSummary }}</div>
        <div v-if="blueprint._swarmReferences?.length" class="bp-swarm-row">
          <span class="bp-swarm-label">借鉴自</span>
          <div class="bp-swarm-refs">
            <a-tag v-for="r in blueprint._swarmReferences" :key="r" size="small">{{ r }}</a-tag>
          </div>
        </div>
        <div v-if="blueprint._swarmTestsNotes?.length" class="bp-swarm-row">
          <span class="bp-swarm-label">Tests Agent</span>
          <ul class="bp-swarm-list">
            <li v-for="(n, i) in blueprint._swarmTestsNotes" :key="i">{{ n }}</li>
          </ul>
        </div>
        <div v-if="blueprint._swarmVerifierIssues?.length" class="bp-swarm-row">
          <span class="bp-swarm-label">Verifier Agent</span>
          <div class="bp-swarm-issues">
            <div v-for="(is, i) in blueprint._swarmVerifierIssues.slice(0, 5)" :key="i" class="bp-swarm-issue">
              <a-tag :color="is.severity === 'critical' || is.severity === 'high' ? 'red' : 'orange'" size="small">{{ is.severity }}</a-tag>
              <span class="bp-swarm-issue-title">{{ is.title }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="bp-cards">
        <!-- 目标 -->
        <div class="bp-card">
          <div class="bp-card-head"><icon-bulb :size="15" style="color:var(--ai-warn)" /> 目标</div>
          <a-textarea v-model="blueprint.goal" :auto-size="{ minRows: 2, maxRows: 4 }" placeholder="一句话描述..." />
        </div>

        <!-- 建议输入 -->
        <div class="bp-card">
          <div class="bp-card-head"><icon-import :size="15" style="color:var(--ai-info)" /> 建议输入</div>
          <div class="bp-chips">
            <a-tag v-for="(inp, i) in blueprint.inputs" :key="i" closable @close="blueprint.inputs.splice(i,1)">{{ inp }}</a-tag>
            <a-input size="small" v-model="newInput" placeholder="+ 添加" style="width:100px" @keydown.enter="addInput" />
          </div>
        </div>

        <!-- 建议输出 -->
        <div class="bp-card">
          <div class="bp-card-head"><icon-export :size="15" style="color:var(--ai-ok)" /> 建议输出</div>
          <div class="bp-chips">
            <a-tag v-for="(out, i) in blueprint.outputs" :key="i" closable @close="blueprint.outputs.splice(i,1)">{{ out }}</a-tag>
            <a-input size="small" v-model="newOutput" placeholder="+ 添加" style="width:100px" @keydown.enter="addOutput" />
          </div>
        </div>

        <!-- 规则骨架 -->
        <div class="bp-card bp-card-wide">
          <div class="bp-card-head"><icon-list :size="15" style="color:rgb(var(--purple-6))" /> 规则骨架</div>
          <div v-for="(step, i) in blueprint.steps" :key="i" class="bp-step">
            <div class="bp-step-head">
              <span class="bp-step-num">Step {{ Number(i) + 1 }}</span>
              <a-input v-model="step.name" size="small" placeholder="步骤名称" style="flex:1" />
            </div>
            <div v-for="(br, j) in step.branches" :key="j" class="bp-branch">
              <span class="bp-branch-line">{{ j === step.branches.length - 1 ? '└─' : '├─' }}</span>
              <a-input v-model="br.condition" size="small" placeholder="条件" style="flex:2" />
              <span class="bp-branch-arrow">→</span>
              <a-input v-model="br.conclusion" size="small" placeholder="结论" style="flex:1" />
            </div>
          </div>
        </div>

        <!-- 候选参数 -->
        <div class="bp-card">
          <div class="bp-card-head"><icon-settings :size="15" style="color:rgb(var(--cyan-6))" /> 候选参数</div>
          <div v-for="(p, i) in blueprint.params" :key="i" class="bp-param-row">
            <a-input v-model="p.name" size="small" placeholder="参数名" style="flex:1" />
            <span class="bp-param-eq">=</span>
            <a-input v-model="p.value" size="small" placeholder="默认值" style="width:80px" />
          </div>
        </div>

        <!-- 待确认问题 -->
        <div class="bp-card" v-if="blueprint.questions?.length">
          <div class="bp-card-head" style="color:var(--ai-warn)"><icon-question-circle :size="15" /> 待确认</div>
          <div v-for="(q, i) in blueprint.questions" :key="i" class="bp-question">
            <span class="bp-q-text">{{ q }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { Message } from '@arco-design/web-vue'
import {
  IconBulb, IconRobot, IconUpload, IconList, IconSettings, IconImport, IconExport,
  IconQuestionCircle, IconThunderbolt, IconBranch, IconShareAlt, IconFilter,
  IconMessage, IconApps, IconDown, IconUp,
} from '@arco-design/web-vue/es/icon'
import { workbenchApi as rawWorkbenchApi, skillApi as rawSkillApi } from '@/api'
import AgentProgressStream from '@/components/chat/AgentProgressStream.vue'
import SkillCreationProgress from '@/components/workbench/SkillCreationProgress.vue'
import PreviewCardActions from '@/components/workbench/PreviewCardActions.vue'
import { useRouter } from 'vue-router'

const workbenchApi: any = rawWorkbenchApi
const skillApi: any = rawSkillApi
const router: any = useRouter()
const emit = defineEmits(['confirm', 'startManual'])

const userInput = ref('')
const generating = ref(false)
const blueprint = ref<any>(null)
const taskContract = ref<any>(null)
const reviewingCheckpoint = ref('')
const newInput = ref('')
const newOutput = ref('')
const motifs = ref<any[]>([])  // Phase 4 Motif 库：从后端加载
const showMore = ref(false)  // 折叠：更多创建方式
const hasSeenBlueprint = ref(true)  // 首次使用引导开关（onMounted 时根据 localStorage 决定）
// v7 F2: 业务语言进度条（旧采访式流程仍在用）
const streamMessage = ref('')

// v7 一步到位：aiclaw 流式 skill 创建
const creationActive = ref(false)
const creationMessage = ref('')
const creationProgressRef = ref<any>(null)

// Phase 2 §3.3 4-轮采访状态
const ROUND_ORDER = ['round1_goal', 'round2_signals', 'round3_boundaries', 'round4_deployment']
const interviewRound = ref<any>(null)            // 当前轮 InterviewRound
const interviewAnswers = ref<Record<string, any>>({})            // 历史所有轮次答案 {round_id: {q_id: value}}
const currentAnswers = reactive<Record<string, any>>({})         // 本轮答案 {q_id: value}
const tagDraft = reactive<Record<string, string>>({})               // tags kind 的临时输入

const templates = [
  { type: 'threshold', name: '阈值决策', desc: '根据数值判断绿/黄/红灯', icon: IconFilter },
  { type: 'pipeline', name: '多阶段流程', desc: '按顺序执行多个步骤', icon: IconShareAlt },
  { type: 'routing', name: '分类路由', desc: '根据类型分发到不同处理', icon: IconBranch },
  { type: 'api_workflow', name: 'API 工作流', desc: '调用外部接口完成任务', icon: IconThunderbolt },
]

const motifCategoryColor = {
  threshold: 'blue',
  safety: 'red',
  flow: 'cyan',
  resolution: 'purple',
  fallback: 'orange',
}

// v7: 刷新恢复需要传给 SkillCreationProgress 的 resume 数据
const resumeDraftId = ref('')
const resumeData = ref<any>(null)

onMounted(async () => {
  // 首次使用引导：localStorage 判断，未看过时显示提示
  try {
    const seen = localStorage.getItem('sf-blueprint-seen')
    hasSeenBlueprint.value = seen === '1'
    if (!seen) {
      localStorage.setItem('sf-blueprint-seen', '1')
    }
  } catch { /* localStorage 不可用时保持默认已看过 */ }

  try {
    const resp = await skillApi.motifLibrary()
    motifs.value = (resp.motifs || []).filter((m: Record<string, unknown>) => (Array.isArray(m.example_skill_ids) ? m.example_skill_ids : []).length > 0)
  } catch { /* 静默：motif 库可选 */ }

  // v7: 检查是否有正在运行/已就绪的创建草稿 → 自动恢复
  try {
    const active = await workbenchApi.getMyActiveDraft()
    if (active && active.draft_id) {
      resumeDraftId.value = active.draft_id
      resumeData.value = active
      creationActive.value = true
      // 等子组件挂载后 start resume
      await new Promise(r => requestAnimationFrame(r))
      if (creationProgressRef.value?.start) {
        creationProgressRef.value.start()
      }
    }
  } catch { /* 静默：没有活跃草稿很正常 */ }
})

function applyMotif(m: any) {
  // 将一个 motif 的 template 映射到 blueprint 结构
  const tpl = m.template || {}
  const branches = (tpl.branches || []).map((b: Record<string, unknown>) => ({
    condition: b.condition || '',
    conclusion: b.conclusion || '',
  }))
  blueprint.value = {
    goal: '', inputs: [], outputs: [],
    steps: [{ name: tpl.name || m.name, branches }],
    params: [],
    questions: [`这是 ${m.name} 模式（来自 ${m.example_skill_ids?.join(', ') || 'Skill 库'}），请补全具体的指标/阈值/动作`],
    _rawSkill: {},
    _motif: m.id,
  }
}

function buildBlueprintFromSkill(skill: any = {}, extras: any = {}) {
  return {
    goal: skill.goal || skill.meta?.description || '',
    inputs: extractInputs(skill),
    outputs: (skill.output_table || []).map((o: Record<string, unknown>) => o.name || o.field || ''),
    steps: (skill.rules || []).map((r: Record<string, unknown>) => ({
      name: r.name || r.id || '',
      branches: (Array.isArray(r.branches) ? r.branches : []).map((b: Record<string, unknown>) => ({ condition: b.condition || '', conclusion: b.conclusion || '' })),
    })),
    params: (skill.params || []).map((p: Record<string, unknown>) => ({ name: p.name || '', value: String(p.default_value ?? p.value ?? '') })),
    questions: generateQuestions(skill),
    _rawSkill: skill,
    ...extras,
  }
}

function checkpointState(key: string) {
  return (taskContract.value?.checkpoints || []).find((c: Record<string, unknown>) => c.key === key) || null
}

function canApproveCheckpoint(key: string) {
  const item = checkpointState(key)
  return !!(item && item.required && !item.approved)
}

async function generateTaskContract() {
  // v7 一步到位：把用户的 SOP 交给 SkillCreationProgress 全屏组件
  // 它会通过 WS /api/skills/workbench/create-skill/stream 跑 aiclaw 一次出 6 文件
  if (!userInput.value.trim()) return
  creationMessage.value = userInput.value
  creationActive.value = true
  // 等子组件挂载后启动 WS
  await new Promise(r => requestAnimationFrame(r))
  if (creationProgressRef.value?.start) {
    creationProgressRef.value.start()
  }
}

function onCreationCancel() {
  creationActive.value = false
  creationMessage.value = ''
}

function onCreationCreated(payload: any) {
  // 后端 finalize 完成 → 跳转到新建 Skill 的 studio 页
  if (payload?.skill_id) {
    Message.success(`Skill ${payload.skill_id} 已创建，正在跳转...`)
    // 先关创建进度页（否则 v-if="creationActive" 会遮住跳转后的 Studio，
    // 两个 route 共用同一个 SkillStudio.vue 组件 Vue Router 默认不 unmount 重建）
    creationActive.value = false
    creationMessage.value = ''
    // 然后跳转到新 skill
    router.replace({ path: `/skills/${encodeURIComponent(payload.skill_id)}`, force: true })
  } else {
    creationActive.value = false
    creationMessage.value = ''
  }
}

async function submitCheckpointReview(checkpoint: string, decision = 'approved') {
  if (!taskContract.value?.draft_id) return
  reviewingCheckpoint.value = checkpoint
  try {
    const resp = await workbenchApi.reviewTaskContract(taskContract.value.draft_id, {
      checkpoint,
      decision,
      detail: { source: 'blueprint' },
    })
    taskContract.value = {
      ...taskContract.value,
      checkpoints: resp.checkpoints || taskContract.value.checkpoints,
      gate: resp.gate || taskContract.value.gate,
    }
  } catch (e: any) {
    Message.error(e._message || '确认失败')
  } finally {
    reviewingCheckpoint.value = ''
  }
}

function advanceFromTaskContract() {
  if (!taskContract.value?.gate?.can_generate_skill) {
    Message.warning('请先完成目标、权限和预演确认')
    return
  }
  blueprint.value = buildBlueprintFromSkill(taskContract.value.skill || {}, {
    _fromContract: true,
    _taskContract: taskContract.value,
  })
  taskContract.value = null
}

// ─── Phase 2 §3.3 4-轮采访 ───
async function startInterview() {
  if (!userInput.value.trim()) return
  generating.value = true
  try {
    const round = await workbenchApi.architectStart(userInput.value)
    interviewRound.value = round
    for (const k of Object.keys(currentAnswers)) delete currentAnswers[k]
    // 预填 default 答案
    for (const q of (round.questions || [])) {
      if (q.default !== undefined && q.default !== '') {
        currentAnswers[q.id] = q.default
      }
    }
  } catch (e: any) {
    Message.error(e._message || '启动采访失败，已切换到直接生成模式')
    interviewRound.value = null
    await generateBlueprint()
  } finally { generating.value = false }
}

async function advanceInterview() {
  if (!interviewRound.value) return
  // 归档当前轮答案
  interviewAnswers.value = {
    ...interviewAnswers.value,
    [interviewRound.value.id]: { ...currentAnswers },
  }

  generating.value = true
  try {
    // 调 next 接口；若已是最后一轮，后端返回 done=true
    const resp = await workbenchApi.architectNext({
      current_round: interviewRound.value.id,
      description: userInput.value,
      answers: interviewAnswers.value,
      skill_draft: {},
    })
    if (resp?.done) {
      await synthesizeFromInterview()
      return
    }
    interviewRound.value = resp
    for (const k of Object.keys(currentAnswers)) delete currentAnswers[k]
    for (const q of (resp.questions || [])) {
      if (q.default !== undefined && q.default !== '') {
        currentAnswers[q.id] = q.default
      }
    }
  } catch (e) {
    Message.error(String((e as Record<string, unknown>)?._message || '切换下一轮失败'))
  } finally { generating.value = false }
}

async function synthesizeFromInterview() {
  generating.value = true
  try {
    const resp = await workbenchApi.architectSynthesize({
      description: userInput.value,
      answers: interviewAnswers.value,
    })
    const skill = resp.skill || {}
    blueprint.value = buildBlueprintFromSkill(skill, { _fromInterview: true })
    interviewRound.value = null
  } catch (e) {
    Message.error(String((e as Record<string, unknown>)?._message || '合成失败'))
  } finally { generating.value = false }
}

function skipRound() {
  // 跳过：不保存本轮答案，直接去下一轮
  if (!interviewRound.value) return
  const idx = ROUND_ORDER.indexOf(interviewRound.value.id)
  if (idx === -1 || idx >= ROUND_ORDER.length - 1) {
    synthesizeFromInterview()
    return
  }
  advanceInterview()
}

function cancelInterview() {
  interviewRound.value = null
  interviewAnswers.value = {}
  for (const k of Object.keys(currentAnswers)) delete currentAnswers[k]
}

function resetAll() {
  blueprint.value = null
  taskContract.value = null
  cancelInterview()
}

function toggleMulti(qid: string, val: string) {
  const cur = currentAnswers[qid] || []
  const i = cur.indexOf(val)
  if (i >= 0) cur.splice(i, 1)
  else cur.push(val)
  currentAnswers[qid] = [...cur]
}

function addTag(qid: string) {
  const v = (tagDraft[qid] || '').trim()
  if (!v) return
  const cur = currentAnswers[qid] || []
  cur.push(v)
  currentAnswers[qid] = [...cur]
  tagDraft[qid] = ''
}

function removeTag(qid: string, i: number) {
  const cur = currentAnswers[qid] || []
  cur.splice(i, 1)
  currentAnswers[qid] = [...cur]
}

// §8.4 Swarm 4-Agent 协作生成
async function generateBySwarm() {
  if (!userInput.value.trim()) return
  generating.value = true
  try {
    const resp = await workbenchApi.swarmGenerate({
      description: userInput.value,
      answers: {},
    })
    const skill = resp.skill || {}
    if (!skill || !Object.keys(skill).length) {
      Message.error(resp.leader_summary || 'Swarm 未产出骨架')
      return
    }
    blueprint.value = buildBlueprintFromSkill(skill, {
      _fromSwarm: true,
      _swarmReferences: skill.meta?.references || [],
      _swarmAntipatterns: skill.antipatterns || [],
      _swarmTestsNotes: resp.tests_notes || [],
      _swarmVerifierIssues: resp.verifier_issues || [],
      _swarmSummary: resp.leader_summary || '',
      _swarmCanPublish: resp.can_publish || false,
    })
    if (resp.leader_summary) {
      Message.success('Swarm 完成：' + resp.leader_summary.split('\n')[0])
    }
  } catch (e: any) {
    Message.error(e._message || 'Swarm 生成失败')
  } finally { generating.value = false }
}

async function generateBlueprint() {
  if (!userInput.value.trim()) return
  generating.value = true
  try {
    // 尝试用 Architect pipeline（4 轮采访 + 三重检索 + 质检）
    // 简化：一步直接合成（跳过采访 UI，后续可升级为 4 步）
    let resp
    try {
      resp = await workbenchApi.architectSynthesize({
        description: userInput.value,
        answers: {},  // 空答案，让 AI 自主推断
      })
    } catch {
      // 降级到旧的 generateDraft
      resp = await workbenchApi.generateDraft({
        message: userInput.value,
        mode: 'novice',
        references: [],
      })
    }
    const skill = resp.skill || {}
    blueprint.value = buildBlueprintFromSkill(skill)
  } catch (e: any) {
    Message.error(e._message || '生成失败，请重试')
    // Fallback：根据输入生成最小 blueprint
    blueprint.value = {
      goal: userInput.value.slice(0, 120),
      inputs: [], outputs: [],
      steps: [{ name: '待定义', branches: [{ condition: '待定义', conclusion: '待定义' }] }],
      params: [], questions: ['请补充具体的判断条件和阈值'],
      _rawSkill: {},
    }
  } finally { generating.value = false }
}

function extractInputs(skill: any) {
  const inputs: string[] = []
  if (skill.data_inputs?.length) inputs.push(...skill.data_inputs.map((d: Record<string, unknown>) => d.name))
  // 从规则条件中提取引用的数据
  for (const r of (skill.rules || [])) {
    for (const b of (r.branches || [])) {
      const words = (b.condition || '').match(/[\u4e00-\u9fff]+|[A-Z][a-z]+|[a-z]+/g) || []
      for (const w of words) {
        if (w.length > 1 && !inputs.includes(w)) inputs.push(w)
      }
    }
  }
  return inputs.slice(0, 6)
}

function generateQuestions(skill: any) {
  const qs: string[] = []
  if (!skill.goal && !skill.meta?.description) qs.push('目标描述不够具体，能再说明适用场景吗？')
  if (!(skill.params?.length)) qs.push('未检测到可配置参数，是否有需要外部调整的阈值？')
  if (!(skill.test_cases?.length)) qs.push('暂无测试用例，能提供一两个典型输入输出吗？')
  return qs
}

function applyTemplate(t: any) {
  const templateMap = {
    threshold: {
      goal: '', inputs: ['指标数据'], outputs: ['判断结果', '动作建议'],
      steps: [
        { name: '检查指标', branches: [
          { condition: '指标 > 高阈值', conclusion: '绿灯' },
          { condition: '指标在中间区间', conclusion: '黄灯' },
          { condition: '指标 < 低阈值', conclusion: '红灯' },
        ]},
      ],
      params: [{ name: 'high_threshold', value: '' }, { name: 'low_threshold', value: '' }],
      questions: ['具体检查什么指标？', '阈值是多少？'],
    },
    pipeline: {
      goal: '', inputs: ['输入数据'], outputs: ['最终产出'],
      steps: [
        { name: '阶段1：准备', branches: [{ condition: '数据完备', conclusion: '继续' }] },
        { name: '阶段2：处理', branches: [{ condition: '处理成功', conclusion: '继续' }] },
        { name: '阶段3：输出', branches: [{ condition: '质量合格', conclusion: '完成' }] },
      ],
      params: [], questions: ['有几个阶段？', '每个阶段做什么？'],
    },
    routing: {
      goal: '', inputs: ['请求内容'], outputs: ['路由结果'],
      steps: [{ name: '分类判断', branches: [
        { condition: '类型A', conclusion: '走A路径' },
        { condition: '类型B', conclusion: '走B路径' },
        { condition: '其他', conclusion: '走默认路径' },
      ]}],
      params: [], questions: ['有哪些分类？', '每种分类怎么处理？'],
    },
    api_workflow: {
      goal: '', inputs: ['触发条件'], outputs: ['执行结果'],
      steps: [
        { name: '调用API', branches: [{ condition: '调用成功', conclusion: '继续' }, { condition: '调用失败', conclusion: '重试/报错' }] },
        { name: '处理结果', branches: [{ condition: '结果有效', conclusion: '输出' }] },
      ],
      params: [], questions: ['调用什么API？', '输入输出格式是什么？'],
    },
  }
  blueprint.value = { ...((templateMap as Record<string, Record<string, unknown>>)[t.type] || {}), _rawSkill: {} }
}

function addInput() {
  if (newInput.value.trim()) { blueprint.value.inputs.push(newInput.value.trim()); newInput.value = '' }
}
function addOutput() {
  if (newOutput.value.trim()) { blueprint.value.outputs.push(newOutput.value.trim()); newOutput.value = '' }
}

function confirmBlueprint() {
  const contractMeta = blueprint.value?._taskContract || null
  // 将 blueprint 转成 Skill 文档格式
  const skill = {
    meta: { name: '', description: blueprint.value.goal },
    goal: blueprint.value.goal,
    rules: blueprint.value.steps.map((s: Record<string, any>, i: number) => ({
      id: `step_${i + 1}`, name: s.name,
      branches: (Array.isArray(s.branches) ? s.branches : []).map((b: Record<string, unknown>) => ({ condition: b.condition, conclusion: b.conclusion, action: '', next_step: null })),
    })),
    params: blueprint.value.params.map((p: Record<string, unknown>) => ({ name: p.name, default_value: p.value, description: '' })),
    output_table: blueprint.value.outputs.map((o: Record<string, unknown>) => ({ name: o, format: 'text', recipient: '', approval_level: '' })),
    test_cases: [],
    workflow: {},
    custom_sections: contractMeta ? {
      __artifacts: {
        'intent.md': contractMeta.intent_md || '',
        'policy.yaml': contractMeta.policy_yaml || '',
        'task-contract.json': JSON.stringify(contractMeta.contract || {}, null, 2),
        'task-review-state.json': JSON.stringify({
          draft_id: contractMeta.draft_id,
          checkpoints: contractMeta.checkpoints || [],
          gate: contractMeta.gate || {},
        }, null, 2),
      },
      __task_contract_meta: {
        draft_id: contractMeta.draft_id || '',
        checkpoints: contractMeta.checkpoints || [],
        gate: contractMeta.gate || {},
      },
      __task_contract: contractMeta.contract || {},
      __task_gate: contractMeta.gate || {},
    } : {},
  }
  emit('confirm', skill)
}

async function handleImport(e: unknown) {
  const file = ((e as Event).target as HTMLInputElement)?.files?.[0]
  if (!file) return
  const text = await file.text()
  emit('confirm', { _rawMarkdown: text })
}
</script>

<style scoped>
/* .bp 用 flex 强制水平居中所有子阶段，
   避免父级 surface-block 在某些 flex 嵌套下让 margin: 0 auto 失效 */
.bp {
  height: 100%;
  width: 100%;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
}

/* 输入阶段 — 加大 max-width 让大屏体验更舒展，box-sizing 防止挤压 */
.bp-input-stage {
  max-width: 780px;
  width: 100%;
  padding: 42px 28px 20px;
  box-sizing: border-box;
  position: relative;
  z-index: 1;
}
.bp-input-templates { padding-top: 10px; padding-bottom: 26px; margin-top: -4px; }
.bp-hero {
  text-align: center;
  margin-bottom: 20px;
  padding: 18px 20px 6px;
}
.bp-hero-icon {
  width: 64px; height: 64px; border-radius: 18px; margin: 0 auto 18px;
  background: var(--sf-brand-action);
  color: var(--sf-on-action); display: flex; align-items: center; justify-content: center;
  box-shadow: var(--ai-shadow-1);
}
.bp-hero h2 {
  font-size: 34px;
  font-weight: 900;
  letter-spacing: -0.03em;
  margin: 0 0 8px;
  color: var(--ai-ink-1);
}
.bp-hero p {
  font-size: 15px;
  color: var(--ai-ink-2);
  margin: 0;
  font-weight: 700;
}

.bp-main-input {
  margin-bottom: 16px;
  padding: 16px 18px 18px;
  border-radius: 22px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  box-shadow: 0 18px 40px var(--ai-border);
}
.bp-textarea :deep(.arco-textarea) {
  font-size: 15px !important;
  padding: 18px !important;
  border-radius: 14px !important;
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border-2) !important;
  color: var(--ai-ink-1) !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.9), 0 8px 18px var(--ai-surface-2);
  font-weight: 600;
}
.bp-textarea :deep(.arco-textarea-wrapper) {
  background: transparent !important;
}
.bp-textarea :deep(textarea::placeholder) {
  color: var(--ai-ink-3) !important;
  opacity: 1 !important;
  font-weight: 600;
}

.bp-contract-stage {
  width: 100%;
}

.bp-contract-summary {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.bp-contract-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bp-contract-row span,
.bp-contract-item span,
.bp-gate-copy span,
.bp-contract-note {
  font-size: 12px;
  color: var(--ai-ink-3);
  line-height: 1.5;
}

.bp-contract-row strong,
.bp-contract-item strong,
.bp-gate-copy strong {
  font-size: 14px;
  color: var(--ai-ink-1);
}

.bp-contract-list,
.bp-gate-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.bp-contract-item,
.bp-gate-item {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.bp-gate-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.bp-contract-preview {
  margin: 0;
  padding: 14px;
  border-radius: 12px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ai-ink-1);
}

.bp-contract-actions {
  margin-top: 12px;
}


/* 主 CTA：填充式大按钮，占满宽度 */
.bp-primary-cta {
  width: 100%;
  height: 52px;
  margin-top: 14px;
  font-size: 16px;
  font-weight: 800;
  border-radius: 14px;
  box-shadow: 0 16px 28px rgba(45, 97, 255, 0.24);
}
.bp-primary-cta :deep(.arco-btn-content) { display: inline-flex; align-items: center; }

/* 首次使用引导提示 */
.bp-first-hint {
  margin-top: 10px;
  padding: 12px 14px;
  border-radius: 10px;
  background: rgba(45, 97, 255, 0.08);
  border: 1px dashed rgba(45, 97, 255, 0.35);
  font-size: 12px;
  color: var(--ai-accent);
  text-align: center;
  line-height: 1.5;
  font-weight: 700;
}

/* 次要操作：link-like 文字按钮 */
.bp-secondary-actions {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  margin-top: 14px;
  font-size: 12px;
  color: var(--ai-ink-2);
}
.bp-or-inline { color: var(--ai-ink-2); font-weight: 800; }
.bp-link-btn {
  padding: 2px 8px !important;
  height: auto !important;
  font-size: 12px !important;
  color: var(--ai-ink-2) !important;
  font-weight: 800 !important;
}
.bp-link-btn:hover { color: var(--ai-accent) !important; }
.bp-link-swarm:hover { color: rgb(var(--purple-6)) !important; }
.bp-sep { color: var(--ai-ink-4); font-weight: 800; }

/* 更多创建方式：折叠按钮 */
.bp-more-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  width: 100%;
  padding: 10px 14px;
  margin-bottom: 12px;
  border: 1px solid var(--ai-border);
  border-radius: 10px;
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
  transition: all .12s;
}
.bp-more-toggle:hover {
  border-color: var(--ai-accent);
  color: var(--ai-accent);
}
.bp-more-panel {
  padding: 16px 18px 18px;
  border-radius: 20px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  box-shadow: 0 14px 28px var(--ai-surface-2);
}
/* O5: "更多创建方式"里并列的 AI 生成方式卡 */
.bp-more-actions {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  margin-bottom: 12px;
}
.bp-more-action {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: 10px;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s;
}
.bp-more-action:hover:not(:disabled) {
  border-color: var(--ai-accent);
  box-shadow: 0 6px 14px rgba(22, 93, 255, 0.12);
}
.bp-more-action:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.bp-more-action-body { flex: 1; }
.bp-more-action-title { font-weight: 600; font-size: 13px; margin-bottom: 4px; }
.bp-more-action-desc { font-size: 12px; color: var(--ai-ink-3); line-height: 1.5; }

/* Phase 2 采访模式 */
.bp-interview-stage {
  max-width: 760px;
  width: 100%;
  padding: 32px;
  box-sizing: border-box;
}
.bp-interview-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.bp-interview-progress { display: flex; gap: 8px; align-items: center; }
.step-dot {
  width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center;
  justify-content: center; font-size: 11px; font-weight: 800;
  background: var(--ai-surface-2); color: var(--ai-ink-3);
  transition: all var(--sf-transition);
}
.step-dot.active { background: var(--sf-warning-solid); color: #fff; box-shadow: 0 4px 10px rgba(198, 106, 20, 0.32); }
.step-dot.done { background: var(--sf-success-solid); color: #fff; }
.bp-interview-title { margin-bottom: 16px; }
.bp-interview-title h3 { font-size: 20px; font-weight: 800; margin: 0 0 4px; color: var(--ai-ink-1); letter-spacing: -0.01em; }
.bp-interview-title p { font-size: 12px; color: var(--ai-ink-3); margin: 0; font-weight: 600; }
.bp-inferred {
  padding: 10px 12px; border-radius: 8px; margin-bottom: 16px;
  background: rgba(198, 106, 20, 0.06); border: 1px dashed rgba(198, 106, 20, 0.45);
}
.bp-inferred-head { font-size: 11px; font-weight: 800; color: var(--ai-warn); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.4px; }
.bp-inferred-row { display: flex; gap: 10px; font-size: 12px; color: var(--ai-ink-2); margin-bottom: 2px; font-weight: 700; }
.bp-inferred-k { color: var(--ai-ink-3); min-width: 80px; font-weight: 800; }
.bp-inferred-v { color: var(--ai-ink-1); font-weight: 800; }
.bp-questions { display: flex; flex-direction: column; gap: 16px; }
.bp-question-card {
  padding: 14px; border-radius: 8px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}
.bp-q-prompt { font-size: 13px; font-weight: 800; color: var(--ai-ink-1); margin-bottom: 4px; }
.bp-q-hint { font-size: 11px; color: var(--ai-ink-3); margin-bottom: 10px; font-weight: 600; }
.bp-choices { display: flex; flex-wrap: wrap; gap: 6px; }
.bp-choice {
  padding: 6px 12px; border: 1px solid var(--ai-border); border-radius: 999px;
  background: var(--ai-surface); color: var(--ai-ink-2); font-size: 12px;
  cursor: pointer; transition: all var(--sf-transition); font-weight: 700;
}
.bp-choice:hover { border-color: var(--ai-warn); color: var(--ai-warn); }
.bp-choice.active { background: var(--sf-warning-solid); color: #fff; border-color: var(--ai-warn); font-weight: 800; }
.bp-tags-input { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.bp-interview-footer {
  display: flex; justify-content: space-between; margin-top: 24px; padding-top: 16px;
  border-top: 1px solid var(--ai-border);
}

.bp-or { text-align: center; color: var(--ai-ink-3); font-size: 12px; margin: 16px 0 14px; font-weight: 800; }

.bp-templates { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.bp-tpl {
  padding: 14px; border: 1px solid var(--ai-border); border-radius: 8px;
  background: var(--ai-surface);
  cursor: pointer; text-align: left; transition: all var(--sf-transition);
}
.bp-tpl:hover {
  border-color: var(--ai-warn); transform: translateY(-1px);
  box-shadow: 0 10px 24px var(--ai-border);
}
.bp-tpl-icon { color: var(--ai-warn); margin-bottom: 6px; }
.bp-tpl-name { font-size: 14px; font-weight: 800; color: var(--ai-ink-1); }
.bp-tpl-desc { font-size: 11px; color: var(--ai-ink-3); margin-top: 2px; font-weight: 600; }

/* Phase 4 Motif 库 */
.bp-motifs { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.bp-motif {
  padding: 12px; border: 1px solid var(--ai-border); border-radius: 8px;
  background: var(--ai-surface);
  cursor: pointer; text-align: left; transition: all var(--sf-transition);
}
.bp-motif:hover {
  border-color: rgb(var(--purple-6)); transform: translateY(-1px);
  box-shadow: 0 10px 24px var(--ai-border);
}
.bp-motif-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.bp-motif-name { font-size: 13px; font-weight: 800; color: var(--ai-ink-1); }
.bp-motif-desc { font-size: 11px; color: var(--ai-ink-2); line-height: 1.4; font-weight: 600; }
.bp-motif-examples { font-size: 10px; color: var(--ai-ink-3); margin-top: 4px; font-style: italic; }

.bp-import { text-align: center; margin-top: 16px; }
.bp-import-btn {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 6px 14px; border-radius: 4px; font-size: 12px;
  border: 1px dashed var(--ai-border); color: var(--ai-ink-3); cursor: pointer;
  transition: all var(--sf-transition); font-weight: 700;
  background: var(--ai-surface);
}
.bp-import-btn:hover { border-color: var(--ai-warn); color: var(--ai-warn); }

/* 结果阶段 */
.bp-result-stage { max-width: 780px; width: 100%; padding: 24px; box-sizing: border-box; }
.bp-result-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.bp-result-header h3 { font-size: 20px; font-weight: 800; margin: 0; color: var(--ai-ink-1); letter-spacing: -0.01em; }

.bp-cards { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
.bp-card {
  padding: 14px; border: 1px solid var(--ai-border); border-radius: 8px;
  background: var(--ai-surface);
  box-shadow: 0 4px 12px var(--ai-surface-2);
}
.bp-card-wide { grid-column: span 2; }
.bp-card-head { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 800; color: var(--ai-ink-1); margin-bottom: 10px; }

.bp-chips { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }

.bp-step {
  margin-bottom: 10px; padding: 8px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  border-radius: 4px;
}
.bp-step-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.bp-step-num { font-size: 11px; font-weight: 800; color: var(--ai-warn); white-space: nowrap; }
.bp-branch { display: flex; align-items: center; gap: 4px; margin-left: 16px; margin-bottom: 3px; }
.bp-branch-line { font-family: var(--ai-font-mono); color: var(--ai-ink-3); width: 20px; }
.bp-branch-arrow { color: var(--ai-ink-3); font-size: 12px; }

.bp-param-row { display: flex; align-items: center; gap: 4px; margin-bottom: 4px; }
.bp-param-eq { color: var(--ai-ink-3); }

.bp-question { padding: 6px 0; font-size: 12px; color: var(--ai-ink-2); border-bottom: 1px solid var(--ai-border); font-weight: 600; }
.bp-q-text::before { content: '❓ '; }

/* §8.4 Swarm 产出面板 */
.bp-swarm-card {
  margin-bottom: 16px; padding: 14px 16px;
  background: rgba(139, 92, 246, 0.05);
  border: 1px solid rgba(139, 92, 246, 0.30); border-radius: 8px;
}
.bp-swarm-head {
  display: flex; align-items: center; gap: 6px; margin-bottom: 8px;
  font-size: 13px; font-weight: 800; color: rgb(var(--purple-6));
}
.bp-swarm-summary {
  font-size: 12px; color: var(--ai-ink-2); line-height: 1.6;
  white-space: pre-wrap; margin-bottom: 10px; font-weight: 600;
}
.bp-swarm-row { display: flex; gap: 10px; font-size: 12px; margin-bottom: 8px; }
.bp-swarm-label {
  min-width: 80px; color: var(--ai-ink-3);
  font-weight: 800; flex-shrink: 0; text-transform: uppercase; letter-spacing: 0.3px; font-size: 10px;
}
.bp-swarm-refs { display: flex; flex-wrap: wrap; gap: 4px; }
.bp-swarm-list {
  margin: 0; padding: 0 0 0 14px; color: var(--ai-ink-2); font-weight: 600;
}
.bp-swarm-list li { line-height: 1.6; }
.bp-swarm-issues { display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 0; }
.bp-swarm-issue { display: flex; align-items: center; gap: 6px; }
.bp-swarm-issue-title { color: var(--ai-ink-2); font-size: 12px; font-weight: 700; }
</style>
