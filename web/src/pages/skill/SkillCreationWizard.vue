<template>
  <div class="page-container wizard-container skill-creation-wizard-page">
    <div class="page-header wizard-header">
      <div>
        <div class="page-kicker">Skills · 新建 Skill</div>
        <h2 class="page-title">新建 Skill</h2>
        <p class="page-subtitle">描述场景后，系统会选择直接生成或采访补全。</p>
      </div>
      <a-space class="wizard-nav">
        <span class="wizard-step-badge">{{ stepProgressText }}</span>
        <a-button type="text" @click="cancelWizard">
          <template #icon><icon-close /></template>取消
        </a-button>
      </a-space>
    </div>

    <div class="wizard-progress-shell">
      <div class="wizard-progress-copy">
        <span class="progress-kicker">当前阶段</span>
        <strong>{{ currentStepTitle }}</strong>
        <span>{{ currentStepHint }}</span>
      </div>

      <!-- 顶部步骤条：采访路径 4 步 / AI 合成路径 3 步，两套分开避免步数抖动 -->
      <a-steps v-if="step === 'agent'" :current="2" small class="wizard-steps">
        <a-step>描述</a-step>
        <a-step>AI 合成</a-step>
        <a-step>发布</a-step>
      </a-steps>
      <a-steps v-else :current="stepIndex + 1" small class="wizard-steps">
        <a-step>描述</a-step>
        <a-step>采访</a-step>
        <a-step>预览</a-step>
        <a-step>命名</a-step>
      </a-steps>
    </div>

    <!-- ══════════════ Step 1: 业务描述 ══════════════ -->
    <a-card v-if="step === 'describe'" class="wizard-panel page-list-card" :bordered="false">
      <template #title>
        <div class="panel-title-block">
          <span class="panel-kicker">业务描述</span>
          <span>描述要解决的业务问题</span>
        </div>
      </template>
      <template #extra>
        <a-tooltip content="例：每天早上 9 点监控电商店铺 top5 下滑链接，按流量来源分析原因并推给运营">
          <icon-info-circle />
        </a-tooltip>
      </template>

      <div class="describe-grid">
        <div class="describe-main">
          <a-textarea
            v-model="description"
            class="description-input"
            placeholder="写清楚触发时机、判断条件、输出对象；完整 SOP 可直接粘贴。"
            :auto-size="{ minRows: 6, maxRows: 14 }"
            show-word-limit
            :max-length="50000"
          />

          <div class="output-capability-panel">
            <div class="capability-head">
              <div>
                <div class="capability-title">通知能力</div>
                <div class="capability-subtitle">选择运行结果要进入哪里，生成时会自动写入代码和契约。</div>
              </div>
              <a-tag v-if="!outputChannels.length" size="small" color="gray">仅业务数据</a-tag>
              <a-tag v-else size="small" color="arcoblue">{{ outputCapabilitySummary }}</a-tag>
            </div>
            <div class="capability-grid">
              <button
                type="button"
                class="capability-card"
                :class="{ active: true, locked: true }"
                aria-disabled="true"
              >
                <strong>收件 - 报告</strong>
                <span>默认输出运行报告，生成日报、诊断结果、只读通知</span>
              </button>
              <button
                type="button"
                class="capability-card"
                :class="{ active: hasOutputChannel('todos') }"
                @click="toggleOutputChannel('todos')"
              >
                <strong>收件 - 待办</strong>
                <span>需要人审批、整改、派发执行</span>
              </button>
            </div>
            <div v-if="hasOutputChannel('todos')" class="todo-capability-config">
              <a-radio-group v-model="todoOutputKind" type="button" size="mini">
                <a-radio value="dispatch">派发任务</a-radio>
                <a-radio value="review">审核确认</a-radio>
              </a-radio-group>
              <a-select v-model="todoReviewerRole" size="small" class="todo-role-select">
                <a-option value="operator">运营</a-option>
                <a-option value="biz_owner">业务负责人</a-option>
                <a-option value="dept_admin">部门管理员</a-option>
                <a-option value="aibp">AIBP</a-option>
                <a-option value="ai_engineer">AI 工程师</a-option>
                <a-option value="admin">管理员</a-option>
              </a-select>
              <span class="todo-config-hint">
                {{ todoOutputKind === 'dispatch' ? '异常项会生成 tasks，进入待办后再分配执行。' : '运行结果会等待接收人确认。' }}
              </span>
            </div>
          </div>

          <!-- v2.11.3: LLM 评估描述完整度 -->
          <div v-if="assessing" class="assess-panel assess-loading">
            <icon-loading /> AI 正在评估描述完整度...
          </div>
          <div v-else-if="assessError" class="assess-panel assess-error">
            <div class="assess-title">
              <icon-exclamation-circle-fill /> AI 评估失败
            </div>
            <div class="assess-body">{{ assessError }}</div>
            <div class="assess-body">手动选择下一步：</div>
          </div>
          <div v-else-if="assessment" class="assess-panel" :class="assessTierClass">
            <div class="assess-title">
              <icon-check-circle-fill v-if="assessment.tier === 'complete'" />
              <icon-info-circle-fill v-else-if="assessment.tier === 'partial'" />
              <icon-question-circle-fill v-else />
              <span>{{ assessTierLabel }}</span>
              <a-tag size="small" :color="assessComplexityColor">{{ assessComplexityLabel }}</a-tag>
              <span class="assess-confidence">置信度 {{ Math.round(assessment.confidence * 100) }}%</span>
            </div>
            <div class="assess-body">{{ assessment.user_hint || assessment.rationale }}</div>
            <div v-if="assessment.covered_dimensions.length || assessment.missing_dimensions.length" class="assess-chips">
              <a-tag
                v-for="d in assessment.covered_dimensions"
                :key="`c-${d}`"
                size="small"
                color="green"
              >✓ {{ d }}</a-tag>
              <a-tag
                v-for="d in assessment.missing_dimensions"
                :key="`m-${d}`"
                size="small"
                color="orange"
              >缺 {{ d }}</a-tag>
            </div>
          </div>

          <div v-if="similarSkills.length" class="similar-row">
            <div class="similar-label">相近的已有 Skill：</div>
            <a-tag
              v-for="s in similarSkills"
              :key="s.id"
              size="small"
              color="arcoblue"
              class="similar-chip"
              @click="useExistingSkill(s)"
            >{{ s.name || s.id }}</a-tag>
          </div>
        </div>

        <aside class="describe-aside">
          <div class="aside-stat">
            <span>描述长度</span>
            <strong>{{ descriptionStatsText }}</strong>
          </div>
          <div class="aside-divider" />
          <div class="path-panel">
            <div class="path-panel-title">建议路径</div>
            <div class="path-choice" :class="{ active: preferDirectSynthesize }">
              <div>
                <strong>直接生成</strong>
                <span>{{ directPathHint }}</span>
              </div>
              <a-tag size="small" :color="preferDirectSynthesize ? 'green' : 'gray'">
                {{ preferDirectSynthesize ? '推荐' : '可选' }}
              </a-tag>
            </div>
            <div class="path-choice" :class="{ active: !preferDirectSynthesize }">
              <div>
                <strong>采访补全</strong>
                <span>{{ interviewPathHint }}</span>
              </div>
              <a-tag size="small" :color="!preferDirectSynthesize ? 'arcoblue' : 'gray'">
                {{ !preferDirectSynthesize ? '推荐' : '可选' }}
              </a-tag>
            </div>
          </div>
          <div class="aside-divider" />
          <div class="aside-note">
            <span class="aside-note-label">状态</span>
            <strong>{{ descriptionStateText }}</strong>
          </div>
        </aside>
      </div>

      <div class="wizard-actions">
        <a-button type="text" @click="findSimilar" :loading="similarLoading">
          <template #icon><icon-search /></template>找找相似 Skill
        </a-button>
        <a-space>
          <!-- tier=complete 或 error：直接生成为主按钮，采访为次 -->
          <template v-if="preferDirectSynthesize">
            <a-button
              :disabled="!canProceed || synthesizing"
              :loading="loading"
              @click="startInterview"
            >
              开始采访
            </a-button>
            <a-button
              type="primary"
              status="success"
              :disabled="!canProceed"
              :loading="synthesizing"
              @click="startDirectSynthesize"
            >
              直接生成 <icon-arrow-right />
            </a-button>
          </template>
          <!-- tier=partial：两按钮并列 -->
          <template v-else-if="assessment?.tier === 'partial'">
            <a-button
              :disabled="!canProceed"
              :loading="synthesizing"
              @click="startDirectSynthesize"
            >
              直接生成
            </a-button>
            <a-button
              type="primary"
              :disabled="!canProceed"
              :loading="loading"
              @click="startInterview"
            >
              开始采访 <icon-arrow-right />
            </a-button>
          </template>
          <!-- tier=insufficient 或评估未跑：采访为主（默认） -->
          <template v-else>
            <a-button
              type="primary"
              :disabled="!canProceed"
              :loading="loading"
              @click="startInterview"
            >
              {{ assessmentPending ? '等 AI 评估...' : '开始采访' }} <icon-arrow-right v-if="!assessmentPending" />
            </a-button>
          </template>
        </a-space>
      </div>
    </a-card>

    <!-- ══════════════ Step 2: 采访 ══════════════ -->
    <a-card v-else-if="step === 'interview'" class="wizard-panel page-list-card" :bordered="false">
      <template #title>
        <div class="panel-title-block">
          <span class="panel-kicker">采访补全</span>
          <span>采访（{{ currentRoundNum }} / 4）· {{ currentRound?.title || '加载中...' }}</span>
        </div>
      </template>

      <SfLoadingState v-if="!currentRound && loading" tip="生成采访问题..." />

      <div v-else-if="currentRound" class="interview-body">
        <div class="round-intro" v-if="currentRound.description">{{ currentRound.description }}</div>

        <div class="question-list">
          <div
            v-for="(q, qi) in currentRound.questions"
            :key="q.id"
            class="question-block"
          >
            <div class="q-label">
              <span class="q-num">Q{{ Number(qi) + 1 }}</span>
              <span class="q-text">{{ q.prompt }}</span>
              <a-tag v-if="getQuestionHint(q)" size="small" color="gray">{{ getQuestionHint(q) }}</a-tag>
            </div>
            <a-radio-group
              v-if="q.kind === 'single'"
              type="button"
              size="small"
              class="question-choice-group"
              :model-value="getSingleAnswer(q.id)"
              @update:model-value="setSingleAnswer(q.id, $event)"
            >
              <a-radio v-for="candidate in q.candidates" :key="candidate" :value="candidate">
                {{ candidate }}
              </a-radio>
            </a-radio-group>
            <a-checkbox-group
              v-else-if="q.kind === 'multi'"
              class="question-choice-group"
              :model-value="getMultiAnswer(q.id)"
              @update:model-value="setMultiAnswer(q.id, $event)"
            >
              <a-checkbox v-for="candidate in q.candidates" :key="candidate" :value="candidate">
                {{ candidate }}
              </a-checkbox>
            </a-checkbox-group>
            <a-select
              v-else-if="q.kind === 'tags'"
              class="question-tag-select"
              :model-value="getMultiAnswer(q.id)"
              multiple
              allow-create
              allow-search
              :placeholder="buildQuestionPlaceholder(q)"
              @update:model-value="setMultiAnswer(q.id, $event)"
            >
              <a-option v-for="candidate in q.candidates" :key="candidate" :value="candidate">
                {{ candidate }}
              </a-option>
            </a-select>
            <a-textarea
              v-else
              :model-value="getTextAnswer(q.id)"
              :placeholder="buildQuestionPlaceholder(q)"
              :auto-size="{ minRows: 2, maxRows: 6 }"
              @update:model-value="setTextAnswer(q.id, $event)"
            />
          </div>
        </div>
      </div>

      <div class="wizard-actions">
        <a-button @click="goBackRound" :disabled="loading">
          <template #icon><icon-left /></template>上一步
        </a-button>
        <a-space>
          <a-button type="text" :loading="loading" @click="skipRound">跳过这一步</a-button>
          <a-button type="primary" :loading="loading" @click="submitRound">
            下一步 <icon-arrow-right />
          </a-button>
        </a-space>
      </div>
    </a-card>

    <!-- ══════════════ Step 3: 预览 ══════════════ -->
    <a-card v-else-if="step === 'preview'" class="wizard-panel page-list-card" :bordered="false">
      <template #title>
        <div class="panel-title-block">
          <span class="panel-kicker">骨架预览</span>
          <span>AI 合成的 Skill 骨架</span>
        </div>
      </template>
      <template #extra>
        <a-space>
          <a-button
            size="small"
            :loading="synthesizing"
            @click="runSynthesize(true)"
          >
            <template #icon><icon-refresh /></template>重新生成
          </a-button>
          <a-radio-group v-model="synthMode" type="button" size="mini">
            <a-radio value="basic">基础</a-radio>
            <a-radio value="swarm">Swarm 并行</a-radio>
          </a-radio-group>
        </a-space>
      </template>

      <SfLoadingState v-if="synthesizing" :tip="streamMessage || (synthMode === 'swarm' ? 'Swarm 4-Agent 并行生成中...' : '合成骨架...')">
        <template v-if="streamPhase" #extra>
          <a-tag size="small" color="arcoblue">阶段：{{ streamPhase }}</a-tag>
        </template>
      </SfLoadingState>

      <div v-else-if="pipelineError" class="synth-error">
        <a-alert type="error" show-icon>
          <template #title>骨架生成失败</template>
          {{ pipelineError }}
        </a-alert>
        <div class="error-actions">
          <a-button type="primary" @click="runSynthesize(true)">重试这一步</a-button>
          <a-button @click="step = 'interview'">回到采访修改</a-button>
          <a-collapse :bordered="false">
            <a-collapse-item header="查看原始响应（调试用）" key="raw">
              <pre class="raw-response">{{ JSON.stringify(rawPipelineResult, null, 2) }}</pre>
            </a-collapse-item>
          </a-collapse>
        </div>
      </div>

      <div v-else-if="generatedSkill" class="preview-body">
        <div class="preview-section">
          <div class="section-label">元数据</div>
          <div class="meta-row">
            <span><b>名称：</b>{{ generatedSkill.meta?.name || '—' }}</span>
            <span><b>部门：</b>{{ generatedSkill.meta?.department || '—' }}</span>
            <a-tag size="small">{{ generatedSkill.meta?.trigger_type || 'manual' }}</a-tag>
            <a-tag size="small" :color="getRiskTagColor(generatedSkill.meta?.risk_level)">
              {{ generatedSkill.meta?.risk_level || 'R2' }}
            </a-tag>
          </div>
        </div>

        <div class="preview-section">
          <div class="section-label">目标</div>
          <div class="section-text">{{ generatedSkill.goal || '—' }}</div>
        </div>

        <div class="preview-section">
          <div class="section-label">决策步骤（{{ generatedSkill.rules?.length || 0 }}）</div>
          <div
            v-for="(r, ri) in (generatedSkill.rules || [])"
            :key="r.id || ri"
            class="rule-item"
          >
            <strong>{{ r.id }} · {{ r.name }}</strong>
            <ul class="branch-list">
              <li v-for="(b, bi) in (r.branches || [])" :key="bi">
                <code>{{ b.condition }}</code> → <b>{{ b.conclusion }}</b>
                <span v-if="b.action" class="branch-action"> → {{ b.action }}</span>
              </li>
            </ul>
          </div>
        </div>

        <div class="preview-section" v-if="generatedSkill.test_cases?.length">
          <div class="section-label">测试用例（{{ generatedSkill.test_cases.length }}）</div>
          <ul class="simple-list">
            <li v-for="(t, ti) in generatedSkill.test_cases" :key="ti">{{ t.name }}</li>
          </ul>
        </div>

        <div class="preview-section" v-if="generatedSkill.antipatterns?.length">
          <div class="section-label">反例（{{ generatedSkill.antipatterns.length }}）</div>
          <ul class="simple-list">
            <li v-for="(a, ai) in generatedSkill.antipatterns" :key="ai">{{ a.scenario }}</li>
          </ul>
        </div>

        <a-alert
          v-if="lintReport && (lintReport.error_count ?? 0) > 0"
          type="warning"
          :content="`自动 lint 检出 ${lintReport.error_count ?? 0} 个问题，创建后可继续在编辑器里修复`"
          show-icon
        />
      </div>

      <div v-else class="empty-preview">
        <a-empty description="还没生成骨架，请点击上方'重新生成'">
          <a-button type="primary" @click="runSynthesize(false)">
            开始合成
          </a-button>
        </a-empty>
      </div>

      <div class="wizard-actions">
        <a-button @click="step = 'interview'" :disabled="synthesizing">
          <template #icon><icon-left /></template>改答案
        </a-button>
        <a-button
          type="primary"
          :disabled="!generatedSkill || synthesizing || !!pipelineError"
          @click="step = 'commit'"
        >
          下一步 <icon-arrow-right />
        </a-button>
      </div>
    </a-card>

    <!-- ══════════════ Step Agent: AI Coding Agent 直接生成 ══════════════ -->
    <a-card v-else-if="step === 'agent'" class="wizard-panel wizard-agent page-list-card" :bordered="false">
      <template #title>
        <div class="agent-card-title">
          <span>AI 正在合成 Skill</span>
          <a-tag v-if="agentSkillId" size="small" class="agent-skill-tag">{{ agentSkillId }}</a-tag>
        </div>
      </template>
      <template #extra>
        <div class="agent-card-status">
          <a-tag v-if="agentRunning" size="small" color="arcoblue">运行中</a-tag>
          <a-tag v-else-if="agentDone" size="small" color="green">已完成</a-tag>
          <a-tag v-if="agentError" size="small" color="red">错误</a-tag>
        </div>
      </template>

      <!-- 顶部 milestone 进度条 -->
      <CreationMilestoneStepper
        class="agent-stepper"
        :milestones="agentMilestones"
        :current-attempt="agentAttempt"
        :max-attempts="agentMaxAttempts"
        :has-error="!!agentError"
      />

      <!-- 主体：左栏文件树 + 右栏 Monaco -->
      <div class="agent-layout">
        <div class="agent-files-pane">
          <CreationFileTree
            :files="agentFiles"
            :selected="selectedAgentFile || ''"
            :recently-added="recentlyAddedFiles"
            @update:selected="selectedAgentFile = $event"
          />
        </div>
        <div class="agent-preview">
          <div v-if="selectedAgentFile" class="agent-preview-header">
            <span class="agent-preview-path">{{ selectedAgentFile }}</span>
          </div>
          <div class="agent-preview-body">
            <MonacoEditor
              v-if="selectedAgentFile"
              :key="selectedAgentFile"
              :filename="selectedAgentFile"
              :model-value="agentFiles[selectedAgentFile] || ''"
              :language="getFileLanguage(selectedAgentFile)"
              height="100%"
              :read-only="true"
              :options="{ readOnly: true, minimap: { enabled: false }, fontSize: 12 }"
            />
            <div v-else class="agent-preview-empty">
              左栏点击任意文件查看
            </div>
          </div>
        </div>
      </div>

      <div v-if="agentRuntimePreviewVisible" class="agent-runtime-card">
        <div class="agent-runtime-head">
          <div>
            <div class="agent-runtime-title">运行结果流向</div>
            <div class="agent-runtime-subtitle">基于 sample_input 沙箱试跑，展示执行后会进入收件中心的内容。</div>
          </div>
          <div class="agent-runtime-counts">
            <a-tag size="small" :color="agentPreviewSuccess === false ? 'red' : 'green'">
              {{ agentPreviewSuccess === false ? '预演异常' : '沙箱预演' }}
            </a-tag>
            <a-tag size="small" color="arcoblue">待办 {{ agentTodoCount }}</a-tag>
            <a-tag size="small" color="purple">报告 {{ agentReportCount }}</a-tag>
          </div>
        </div>

        <div v-if="agentRuntimeSummary" class="agent-runtime-summary">{{ agentRuntimeSummary }}</div>

        <div class="agent-runtime-grid">
          <div class="agent-runtime-section">
            <div class="agent-runtime-section-title">收件 - 待办</div>
            <template v-if="agentRuntimeTodoItems.length">
              <div v-for="(todo, idx) in agentRuntimeTodoItems" :key="idx" class="agent-runtime-item">
                <div class="agent-runtime-item-head">
                  <a-tag size="small" :color="todoKindColor(todo.kind)">{{ todoKindLabel(todo.kind) }}</a-tag>
                  <strong>{{ todo.title || `待办 ${idx + 1}` }}</strong>
                </div>
                <div v-if="todo.summary" class="agent-runtime-muted">{{ todo.summary }}</div>
                <div class="agent-runtime-meta">
                  <span v-if="todo.reviewer_role">角色 {{ todo.reviewer_role }}</span>
                  <span v-if="todo.reviewers?.length">处理人 {{ todo.reviewers.join(', ') }}</span>
                  <span v-if="todo.sla_hours">SLA {{ todo.sla_hours }}h</span>
                  <span v-if="todo.tasks?.length">任务 {{ todo.tasks.length }}</span>
                </div>
                <div v-if="todo.tasks?.length" class="agent-runtime-tasks">
                  <div v-for="(task, taskIdx) in todo.tasks.slice(0, 3)" :key="taskIdx" class="agent-runtime-task">
                    <span>{{ taskIdx + 1 }}</span>
                    <em>{{ task.content || '-' }}</em>
                  </div>
                </div>
              </div>
            </template>
            <div v-else class="agent-runtime-empty">
              {{ agentOutputCapabilities.todos ? '已声明 todos，本次样例没有生成待办。' : '未声明 todos，不会进入收件-待办。' }}
            </div>
          </div>

          <div class="agent-runtime-section">
            <div class="agent-runtime-section-title">收件 - 报告</div>
            <template v-if="agentRuntimeReportItems.length">
              <div v-for="(report, idx) in agentRuntimeReportItems" :key="idx" class="agent-runtime-item">
                <div class="agent-runtime-item-head">
                  <strong>{{ report.title || `报告 ${idx + 1}` }}</strong>
                </div>
                <div v-if="report.channel || report.summary" class="agent-runtime-muted">
                  {{ [report.channel, report.summary].filter(Boolean).join(' · ') }}
                </div>
              </div>
            </template>
            <div v-else class="agent-runtime-empty">
              {{ agentOutputCapabilities.reports ? '已声明 reports，本次样例没有生成报告。' : '未声明 reports，不会进入收件-报告。' }}
            </div>
          </div>
        </div>
      </div>

      <!-- 错误条 -->
      <div v-if="agentError" class="agent-error-banner">
        <icon-exclamation-circle-fill />
        <span>{{ agentError }}</span>
      </div>

      <!-- 实时事件 Timeline -->
      <CreationEventTimeline
        class="agent-timeline"
        :events="timelineEvents"
        v-model:collapsed="timelineCollapsed"
      />

      <!-- 发布前 Gate Checklist —— 只在 DONE 且拿到 gate 数据时出现 -->
      <CreationGateChecklist
        v-if="agentDone && gateItems.length"
        class="agent-gate"
        :items="gateItems"
        :can-publish="gateCanPublish"
        :publishing="creating"
        :needs-manual-confirm="gateNeedsManualConfirm"
        @approve-preview="handleApprovePreview"
        @publish="goToAgentCommit"
      />

      <!-- 次要 actions：中断 / 返回。发布按钮在 GateChecklist 里 -->
      <div class="wizard-actions wizard-actions-secondary">
        <a-button @click="cancelAgent" :disabled="!agentRunning && !agentDone">
          <template #icon><icon-close /></template>
          {{ agentRunning ? '中断合成' : '重新开始' }}
        </a-button>
        <!-- DONE 但还没拿到 gate 数据（容错：后端 enriched frame 缺失）仍然允许发布 -->
        <a-button
          v-if="agentDone && !gateItems.length"
          type="primary"
          status="success"
          :loading="creating"
          :disabled="!agentFiles['SKILL.md'] || !agentSkillId"
          @click="goToAgentCommit"
        >
          <template #icon><icon-check /></template>
          发布 Skill
        </a-button>
        <span v-else></span>
      </div>
    </a-card>

    <!-- ══════════════ Step 4: 命名确认 ══════════════ -->
    <a-card v-else-if="step === 'commit'" class="wizard-panel page-list-card" :bordered="false">
      <template #title>
        <div class="panel-title-block">
          <span class="panel-kicker">命名确认</span>
          <span>最后一步：命名和确认</span>
        </div>
      </template>

      <div class="commit-layout">
        <a-form :model="finalForm" layout="vertical" class="commit-form">
          <a-form-item label="Skill 名称" required>
            <a-input v-model="finalForm.name" placeholder="例如：店铺下滑链接分析" :max-length="50" show-word-limit />
          </a-form-item>
          <a-form-item label="部门" required>
            <a-select
              v-model="finalForm.department"
              placeholder="选择部门"
              :allow-create="false"
              :disabled="!creationCanChooseDepartment"
            >
              <a-option v-for="d in availableDepts" :key="d" :value="d">{{ d }}</a-option>
            </a-select>
            <div class="field-hint">{{ departmentSourceHint }}</div>
          </a-form-item>
          <a-form-item label="Skill ID（自动生成，可覆盖）">
            <a-input v-model="finalForm.skill_id" :placeholder="generatedSkillIdPreview" />
            <div class="field-hint">{{ namingPolicyHint }}</div>
          </a-form-item>
          <a-form-item label="风险等级">
            <a-radio-group v-model="finalForm.risk_level" type="button" size="small">
              <a-radio value="R1">R1 低风险</a-radio>
              <a-radio value="R2">R2 中风险</a-radio>
              <a-radio value="R3">R3 高风险</a-radio>
              <a-radio value="R4">R4 敏感</a-radio>
            </a-radio-group>
          </a-form-item>
        </a-form>

        <div class="commit-summary">
          <div class="commit-summary-title">即将创建</div>
          <dl>
            <div>
              <dt>名称</dt>
              <dd>{{ finalForm.name || generatedSkill?.meta?.name || '待填写' }}</dd>
            </div>
            <div>
              <dt>部门</dt>
              <dd>{{ finalForm.department || generatedSkill?.meta?.department || '待选择' }}</dd>
            </div>
            <div>
              <dt>风险</dt>
              <dd>
                <a-tag size="small" :color="getRiskTagColor(finalForm.risk_level)">
                  {{ finalForm.risk_level }}
                </a-tag>
              </dd>
            </div>
          </dl>
        </div>
      </div>

      <div class="wizard-actions">
        <a-button @click="step = agentSkillId ? 'agent' : 'preview'" :disabled="creating">
          <template #icon><icon-left /></template>上一步
        </a-button>
        <a-button
          type="primary"
          status="success"
          :loading="creating"
          :disabled="!finalForm.name || !finalForm.department"
          @click="commit"
        >
          <template #icon><icon-check /></template>创建 Skill
        </a-button>
      </div>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  IconArrowRight,
  IconCheck,
  IconCheckCircleFill,
  IconClose,
  IconExclamationCircleFill,
  IconInfoCircle,
  IconInfoCircleFill,
  IconLeft,
  IconLoading,
  IconQuestionCircleFill,
  IconRefresh,
  IconSearch,
} from '@arco-design/web-vue/es/icon'
import { Message } from '@arco-design/web-vue'
import { skillApi, workbenchApi } from '@/api'
import type {
  ArchitectAnswer,
  ArchitectAnswerMap,
  ArchitectAssessResponse,
  ArchitectFindSimilarResponse,
  ArchitectGenerationMode,
  ArchitectGeneratedSkill,
  ArchitectLintReport,
  ArchitectNextResponse,
  ArchitectPipelineResponse,
  ArchitectQuestion,
  ArchitectRound,
  ArchitectSynthesizePayload,
  SkillCreationContext,
} from '@/api'
import { riskColor } from '@/utils/constants'
import { SfLoadingState } from '@/components/common'
import MonacoEditor from '@/components/editor/MonacoEditor.vue'
import CreationMilestoneStepper from '@/components/skills/creation/CreationMilestoneStepper.vue'
import CreationFileTree from '@/components/skills/creation/CreationFileTree.vue'
import CreationEventTimeline from '@/components/skills/creation/CreationEventTimeline.vue'
import CreationGateChecklist from '@/components/skills/creation/CreationGateChecklist.vue'
import { useUserStore } from '@/stores/user'
import { getErrorMessage } from '@/types/skillstudio'
import { confirmAction } from '@/utils/confirmDelete'
import { inferOutputCapabilitiesFromContract } from '@/utils/schemaMapper'
import {
  normalizeRuntimeOutputPreview,
  type RuntimeReportPreview,
  type RuntimeTodoPreview,
} from '@/utils/runtimeOutputPreview'
import { formatTime, toDate } from '@/utils/format'

defineOptions({ name: 'SkillCreationWizard' })

const router = useRouter()
const userStore = useUserStore()

function routeParamText(value: unknown): string {
  if (Array.isArray(value)) return value.length ? String(value[0] ?? '') : ''
  return value == null ? '' : String(value)
}

async function navigateToCreatedSkill(skillId: string): Promise<void> {
  const targetPath = `/skills/${encodeURIComponent(skillId)}`
  try {
    await router.replace({ name: 'SkillStudio', params: { id: skillId } })
    await nextTick()
    const route = router.currentRoute.value
    if (route.name === 'SkillStudio' && routeParamText(route.params.id) === skillId) {
      return
    }
    console.warn('[wizard] created skill navigation did not reach target route', {
      skillId,
      currentName: route.name,
      currentId: routeParamText(route.params.id),
    })
  } catch (error) {
    console.warn('[wizard] failed to navigate to created skill route', error)
  }
  window.location.assign(targetPath)
}

type Step = 'describe' | 'interview' | 'agent' | 'preview' | 'commit'

type FinalRiskLevel = 'R1' | 'R2' | 'R3' | 'R4'
type OutputChannel = 'reports' | 'todos'
type TodoOutputKind = 'dispatch' | 'review'

interface FinalFormState {
  name: string
  department: string
  skill_id: string
  risk_level: FinalRiskLevel
}

interface SimilarSkillOption {
  id: string
  name: string
  department?: string
  status?: string
  reason?: string
}

interface WizardAutosaveState {
  description: string
  outputChannels: OutputChannel[]
  todoOutputKind: TodoOutputKind
  todoReviewerRole: string
  rounds: ArchitectRound[]
  currentRoundIdx: number
  interviewAnswers: Record<string, ArchitectAnswerMap>
  roundAnswers: ArchitectAnswerMap
  generatedSkill: ArchitectGeneratedSkill | null
  finalForm: FinalFormState
  step: Step
  savedAt: number
}

interface StreamPhaseEvent {
  type: 'phase'
  phase?: string
  message?: string
  similar_skills?: ArchitectFindSimilarResponse['items']
  skill?: ArchitectGeneratedSkill
}

interface StreamDoneEvent {
  type: 'done'
  result?: ArchitectPipelineResponse
}

interface StreamErrorEvent {
  type: 'error'
  message?: string
}

type StreamEvent = StreamPhaseEvent | StreamDoneEvent | StreamErrorEvent

const DEFAULT_FINAL_FORM: FinalFormState = {
  name: '',
  department: '',
  skill_id: '',
  risk_level: 'R2',
}

function createDefaultFinalForm(): FinalFormState {
  return { ...DEFAULT_FINAL_FORM }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object'
}

function isStep(value: unknown): value is Step {
  return value === 'describe' || value === 'interview' || value === 'agent' || value === 'preview' || value === 'commit'
}

function isFinalRiskLevel(value: unknown): value is FinalRiskLevel {
  return value === 'R1' || value === 'R2' || value === 'R3' || value === 'R4'
}

function normalizeStringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.filter((item): item is string => typeof item === 'string' && item.length > 0)
  }
  if (typeof value === 'string' && value) return [value]
  return []
}

function normalizeAnswerMap(value: unknown): ArchitectAnswerMap {
  if (!isRecord(value)) return {}
  const next: ArchitectAnswerMap = {}
  for (const [key, raw] of Object.entries(value)) {
    if (typeof raw === 'string') {
      next[key] = raw
      continue
    }
    const list = normalizeStringList(raw)
    if (list.length) next[key] = list
  }
  return next
}

function normalizeInterviewAnswers(value: unknown): Record<string, ArchitectAnswerMap> {
  if (!isRecord(value)) return {}
  const next: Record<string, ArchitectAnswerMap> = {}
  for (const [key, raw] of Object.entries(value)) {
    next[key] = normalizeAnswerMap(raw)
  }
  return next
}

function normalizeFinalForm(value: unknown): FinalFormState {
  if (!isRecord(value)) return createDefaultFinalForm()
  return {
    name: typeof value.name === 'string' ? value.name : '',
    department: typeof value.department === 'string' ? value.department : '',
    skill_id: typeof value.skill_id === 'string' ? value.skill_id : '',
    risk_level: isFinalRiskLevel(value.risk_level) ? value.risk_level : 'R2',
  }
}

function cloneAnswers(value: ArchitectAnswerMap): ArchitectAnswerMap {
  const next: ArchitectAnswerMap = {}
  for (const [key, raw] of Object.entries(value)) {
    next[key] = Array.isArray(raw) ? [...raw] : raw
  }
  return next
}

function normalizeOutputChannels(value: unknown): OutputChannel[] {
  const channels = Array.isArray(value)
    ? value.filter((item): item is OutputChannel => item === 'reports' || item === 'todos')
    : []
  return channels.includes('reports') ? channels : ['reports', ...channels]
}

function normalizeTodoOutputKind(value: unknown): TodoOutputKind {
  return value === 'review' ? 'review' : 'dispatch'
}

function buildRoundAnswers(round: ArchitectRound | null): ArchitectAnswerMap {
  if (!round) return {}
  const next: ArchitectAnswerMap = {}
  for (const question of round.questions || []) {
    if (question.kind === 'multi' || question.kind === 'tags') {
      next[question.id] = Array.isArray(question.default)
        ? [...question.default]
        : question.default
          ? [question.default]
          : []
      continue
    }
    next[question.id] = Array.isArray(question.default)
      ? question.default[0] || ''
      : question.default || ''
  }
  return next
}

function getRoundStorageKey(round: Pick<ArchitectRound, 'id'> | null, fallbackIndex: number): string {
  return round?.id || `round_${fallbackIndex}`
}

function normalizeSimilarSkills(items: ArchitectFindSimilarResponse['items'] | undefined): SimilarSkillOption[] {
  return (items || []).flatMap((item) => {
    if (!item?.skill_id) return []
    return [{
      id: item.skill_id,
      name: item.name || item.skill_id,
      department: item.department,
      status: item.status,
      reason: item.reason,
    }]
  })
}

function isArchitectDoneResponse(response: ArchitectNextResponse): response is Extract<ArchitectNextResponse, { done: true }> {
  return 'done' in response && response.done === true
}

function parseStreamEvent(value: unknown): StreamEvent | null {
  if (!isRecord(value) || typeof value.type !== 'string') return null
  if (value.type === 'phase') {
    return {
      type: 'phase',
      phase: typeof value.phase === 'string' ? value.phase : '',
      message: typeof value.message === 'string' ? value.message : '',
      similar_skills: Array.isArray(value.similar_skills) ? value.similar_skills as ArchitectFindSimilarResponse['items'] : undefined,
      skill: isRecord(value.skill) ? value.skill as ArchitectGeneratedSkill : undefined,
    }
  }
  if (value.type === 'done') {
    return {
      type: 'done',
      result: isRecord(value.result) ? value.result as ArchitectPipelineResponse : undefined,
    }
  }
  if (value.type === 'error') {
    return {
      type: 'error',
      message: typeof value.message === 'string' ? value.message : '',
    }
  }
  return null
}

function formatPipelineIssue(error: ArchitectGeneratedSkill['_error'] | undefined): string {
  if (!error) return 'Unknown: 未知错误'
  return `${error.type || 'Unknown'}: ${error.message || '未知错误'}`
}

function getQuestionHint(question: ArchitectQuestion): string {
  if (question.hint) return question.hint
  if (question.kind === 'multi') return '可多选'
  if (question.kind === 'tags') return '可新增标签'
  return ''
}

function getRiskTagColor(level: string | undefined): string {
  if (!level) return 'gray'
  return riskColor[level as keyof typeof riskColor] || 'gray'
}

// v2.8.2 C3：向导本地 autosave（description + 采访答案），
// 刷新 / 关掉 tab 再进来能续上
const AUTOSAVE_KEY = 'sf.skill.wizard.autosave'
function getWizardStorageKey(): string {
  const uid = userStore.userInfo?.user_id || userStore.userInfo?.username || 'anon'
  return `${AUTOSAVE_KEY}.${uid}`
}

function saveWizardState() {
  try {
    const snapshot: WizardAutosaveState = {
      description: description.value,
      outputChannels: outputChannels.value,
      todoOutputKind: todoOutputKind.value,
      todoReviewerRole: todoReviewerRole.value,
      rounds: rounds.value,
      currentRoundIdx: currentRoundIdx.value,
      interviewAnswers: interviewAnswers.value,
      roundAnswers: roundAnswers.value,
      generatedSkill: generatedSkill.value,
      finalForm: finalForm.value,
      step: step.value,
      savedAt: Date.now(),
    }
    window.localStorage.setItem(getWizardStorageKey(), JSON.stringify(snapshot))
  } catch {
    // ignore
  }
}

function loadWizardState(): WizardAutosaveState | null {
  try {
    const raw = window.localStorage.getItem(getWizardStorageKey())
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<WizardAutosaveState>
    return {
      description: typeof parsed.description === 'string' ? parsed.description : '',
      outputChannels: normalizeOutputChannels(parsed.outputChannels),
      todoOutputKind: normalizeTodoOutputKind(parsed.todoOutputKind),
      todoReviewerRole: typeof parsed.todoReviewerRole === 'string' && parsed.todoReviewerRole
        ? parsed.todoReviewerRole
        : 'operator',
      rounds: Array.isArray(parsed.rounds) ? parsed.rounds : [],
      currentRoundIdx: typeof parsed.currentRoundIdx === 'number' ? parsed.currentRoundIdx : 0,
      interviewAnswers: normalizeInterviewAnswers(parsed.interviewAnswers),
      roundAnswers: normalizeAnswerMap(parsed.roundAnswers),
      generatedSkill: isRecord(parsed.generatedSkill) ? parsed.generatedSkill as ArchitectGeneratedSkill : null,
      finalForm: normalizeFinalForm(parsed.finalForm),
      step: isStep(parsed.step) ? parsed.step : 'describe',
      savedAt: typeof parsed.savedAt === 'number' ? parsed.savedAt : 0,
    }
  } catch {
    return null
  }
}
function clearWizardState() {
  try {
    window.localStorage.removeItem(getWizardStorageKey())
  } catch {
    // ignore
  }
}

// ────────── 状态机 ──────────
const step = ref<Step>('describe')
const stepIndex = computed(() => {
  // agent 流只有 3 步视图：描述 / AI 合成 / 命名
  if (step.value === 'agent') return 1
  return ['describe', 'interview', 'preview', 'commit'].indexOf(step.value)
})

const loading = ref(false)
const synthesizing = ref(false)
const creating = ref(false)

// ────────── Step 1: 描述 ──────────
const description = ref('')
const similarSkills = ref<SimilarSkillOption[]>([])
const similarLoading = ref(false)
const outputChannels = ref<OutputChannel[]>(['reports'])
const todoOutputKind = ref<TodoOutputKind>('dispatch')
const todoReviewerRole = ref('operator')

const outputCapabilitySummary = computed(() => {
  const labels: string[] = []
  if (hasOutputChannel('reports')) labels.push('报告')
  if (hasOutputChannel('todos')) labels.push(todoOutputKind.value === 'dispatch' ? '派发待办' : '审核待办')
  return labels.join(' + ')
})

function hasOutputChannel(channel: OutputChannel): boolean {
  return outputChannels.value.includes(channel)
}

function toggleOutputChannel(channel: OutputChannel): void {
  if (channel === 'reports') return
  outputChannels.value = hasOutputChannel(channel)
    ? outputChannels.value.filter((item) => item !== channel)
    : [...outputChannels.value, channel]
}

function buildOutputCapabilityInstruction(): string {
  const wantsReports = true
  const wantsTodos = hasOutputChannel('todos')

  const lines = [
    '【SkillForge 输出能力要求】',
    '- 这是平台能力开关，不是普通说明文字；生成时必须同时落实到 contract.json 的 output_schema、scripts/main.py 的 return dict、fixtures/sample_input.json。',
  ]
  if (wantsReports) {
    lines.push(
      '- 必须返回 reports 数组，用于「收件 - 报告」。只读诊断、日报、通知结论放 reports；如果没有报告内容也返回 reports: []。',
    )
  }
  if (wantsTodos) {
    lines.push(
      `- 必须返回 todos 数组，用于「收件 - 待办」。待办 kind 固定为 ${todoOutputKind.value}，reviewer_role 固定为 ${todoReviewerRole.value}；如果没有异常项也返回 todos: []。`,
    )
    if (todoOutputKind.value === 'dispatch') {
      lines.push('- dispatch 待办必须带 tasks，每条异常/整改动作对应一条 task，task.content 写清楚要处理什么。')
    } else {
      lines.push('- review 待办用于确认运行结果，不要把执行任务塞进 review 的 tasks。')
    }
  }
  lines.push('- output_schema.properties 和 required 必须包含上述 reports/todos 字段，字段名一字不差。')
  return lines.join('\n')
}

function buildCreationDescription(): string {
  const base = description.value.trim()
  const instruction = buildOutputCapabilityInstruction()
  return [base, instruction].filter(Boolean).join('\n\n')
}

// v2.11.4: Agent 创建流状态
const agentSkillId = ref<string | null>(null)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const agentClient = ref<any>(null)
const agentFiles = ref<Record<string, string>>({})
const agentText = ref('')
const agentError = ref<string | null>(null)
const agentDone = ref(false)
const agentRunning = ref(false)
const selectedAgentFile = ref<string | null>(null)
const agentPreview = ref<Record<string, unknown> | null>(null)

const agentFileList = computed<string[]>(() => Object.keys(agentFiles.value).sort())
const agentContract = computed<Record<string, unknown> | null>(() => {
  const raw = agentFiles.value['contract.json']
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw)
    return isRecord(parsed) ? parsed : null
  } catch {
    return null
  }
})
const agentOutputCapabilities = computed(() => inferOutputCapabilitiesFromContract(agentContract.value))
const agentRuntimePreview = computed(() =>
  normalizeRuntimeOutputPreview(agentPreview.value?.sandbox_output || agentPreview.value || {}),
)
const agentRuntimeTodoItems = computed<RuntimeTodoPreview[]>(() => agentRuntimePreview.value.todos)
const agentRuntimeReportItems = computed<RuntimeReportPreview[]>(() => agentRuntimePreview.value.reports)
const agentSandboxCounts = computed(() => {
  const counts = agentPreview.value?.sandbox_counts
  if (!isRecord(counts)) return { todos: 0, reports: 0 }
  const todos = Number(counts.todos)
  const reports = Number(counts.reports)
  return {
    todos: Number.isFinite(todos) ? todos : 0,
    reports: Number.isFinite(reports) ? reports : 0,
  }
})
const agentTodoCount = computed(() => agentRuntimeTodoItems.value.length || agentSandboxCounts.value.todos)
const agentReportCount = computed(() => agentRuntimeReportItems.value.length || agentSandboxCounts.value.reports)
const agentRuntimeSummary = computed(() => String(agentPreview.value?.sandbox_summary || '').trim())
const agentPreviewSuccess = computed(() => {
  if (!agentPreview.value || typeof agentPreview.value.success !== 'boolean') return null
  return agentPreview.value.success
})
const agentRuntimePreviewVisible = computed(() =>
  agentDone.value && (
    !!agentPreview.value
    || agentOutputCapabilities.value.todos
    || agentOutputCapabilities.value.reports
    || agentTodoCount.value > 0
    || agentReportCount.value > 0
  ),
)

function todoKindLabel(kind?: string): string {
  if (kind === 'dispatch') return '派发'
  if (kind === 'review') return '审核'
  return kind || '待办'
}

function todoKindColor(kind?: string): string {
  if (kind === 'dispatch') return 'orange'
  if (kind === 'review') return 'arcoblue'
  return 'gray'
}

// v2.12.0: 7 个固定 milestone 状态机
type MilestoneStatus = 'pending' | 'running' | 'done' | 'failed'
interface AgentMilestoneEntry {
  key: string
  label: string
  status: MilestoneStatus
  elapsed_s?: number
}
const MILESTONE_DEFS: { key: string; label: string }[] = [
  { key: 'identify_intent', label: '识别意图' },
  { key: 'draft_contract', label: '起草任务合同' },
  { key: 'write_skill_md', label: '写 SKILL.md' },
  { key: 'write_main_py', label: '写 scripts/main.py' },
  { key: 'write_tests', label: '写 tests/test_main.py' },
  { key: 'verify_import', label: '跑测试自验证' },
  { key: 'verify_schema', label: '跑契约 schema 自检' },
]
function defaultMilestones(): AgentMilestoneEntry[] {
  return MILESTONE_DEFS.map((m) => ({ ...m, status: 'pending' as MilestoneStatus }))
}
const agentMilestones = ref<AgentMilestoneEntry[]>(defaultMilestones())
const agentAttempt = ref(1)
const agentMaxAttempts = ref(3)

// v2.12.0: 实时日志时间线
type TimelineEventKind = 'milestone' | 'tool_call' | 'tool_error' | 'thinking' | 'retry' | 'info' | 'contract_ready' | 'skill_ready'
interface TimelineEventEntry {
  id: number
  kind: TimelineEventKind
  text: string
  detail?: string
  time: number
}
const timelineEvents = ref<TimelineEventEntry[]>([])
const timelineCollapsed = ref(false)
let timelineSeq = 0
function pushTimelineEvent(kind: TimelineEventKind, text: string, detail?: string) {
  timelineSeq += 1
  timelineEvents.value.push({ id: timelineSeq, kind, text, detail, time: Date.now() })
}

// v2.12.0: gate 4 必感知点
interface GateItemEntry {
  key: string
  label: string
  passed: boolean
  detail?: string
}
const gateItems = ref<GateItemEntry[]>([])
const gateUserApprovedPreview = ref(false)

const gateCanPublish = computed(() => {
  if (!gateItems.value.length) return false
  return gateItems.value.every((g) => g.passed || (g.key === 'manual_confirmation' && gateUserApprovedPreview.value))
})
const gateNeedsManualConfirm = computed(() => {
  const manual = gateItems.value.find((g) => g.key === 'manual_confirmation')
  if (!manual) return false
  if (gateUserApprovedPreview.value || manual.passed) return false
  // 只有其他 5 项都通过时才引导用户手动确认
  return gateItems.value.filter((g) => g.key !== 'manual_confirmation').every((g) => g.passed)
})

// v2.12.0: 新文件脉冲动画
const recentlyAddedFiles = ref<string[]>([])
const pulseTimers = new Map<string, ReturnType<typeof setTimeout>>()
function markFileRecentlyAdded(fp: string) {
  if (!fp) return
  if (!recentlyAddedFiles.value.includes(fp)) {
    recentlyAddedFiles.value = [...recentlyAddedFiles.value, fp]
  }
  const old = pulseTimers.get(fp)
  if (old) clearTimeout(old)
  pulseTimers.set(fp, setTimeout(() => {
    recentlyAddedFiles.value = recentlyAddedFiles.value.filter((p) => p !== fp)
    pulseTimers.delete(fp)
  }, 1400))
}

function setMilestoneDone(key: string, elapsed: number) {
  let advanced = false
  agentMilestones.value = agentMilestones.value.map((m) => {
    if (m.key === key && m.status !== 'done') {
      advanced = true
      return { ...m, status: 'done' as MilestoneStatus, elapsed_s: elapsed || m.elapsed_s }
    }
    return m
  })
  if (!advanced) return
  // 把下一个 pending milestone 置为 running（串行推进）
  const nextIdx = agentMilestones.value.findIndex((m) => m.status === 'pending')
  if (nextIdx >= 0) {
    agentMilestones.value = agentMilestones.value.map((m, i) =>
      i === nextIdx ? { ...m, status: 'running' as MilestoneStatus } : m,
    )
  }
}

function failRunningMilestone() {
  agentMilestones.value = agentMilestones.value.map((m) =>
    m.status === 'running' ? { ...m, status: 'failed' as MilestoneStatus } : m,
  )
}

function resetAgentRunState() {
  agentFiles.value = {}
  agentMilestones.value = defaultMilestones()
  timelineEvents.value = []
  timelineSeq = 0
  recentlyAddedFiles.value = []
  pulseTimers.forEach((t) => clearTimeout(t))
  pulseTimers.clear()
  agentAttempt.value = 1
  agentMaxAttempts.value = 3
  gateItems.value = []
  gateUserApprovedPreview.value = false
  selectedAgentFile.value = null
  agentPreview.value = null
  agentText.value = ''
  agentError.value = null
  agentDone.value = false
}

// v2.11.3: LLM 评估描述完整度，决定直接合成还是采访
const assessment = ref<ArchitectAssessResponse | null>(null)
const assessing = ref(false)
const assessError = ref<string | null>(null)
let assessTimer: ReturnType<typeof setTimeout> | null = null
const assessSeq = ref(0)

const preferDirectSynthesize = computed(
  () => assessment.value?.recommended_path === 'direct_synthesize'
    || assessment.value?.tier === 'complete'
    || !!assessError.value,
)

// v2.11.3: 描述 ≥10 字会触发评估；评估未出或进行中时按钮锁住
const assessmentPending = computed(() => {
  const text = description.value.trim()
  if (text.length < 10) return false
  if (assessing.value) return true
  return !assessment.value && !assessError.value
})

const canProceed = computed(() => {
  const text = description.value.trim()
  if (text.length < 5) return false
  if (assessmentPending.value) return false
  if (synthesizing.value || loading.value) return false
  return true
})

const assessTierClass = computed(() => {
  if (!assessment.value) return ''
  return `assess-${assessment.value.tier}`
})

const assessTierLabel = computed(() => {
  if (!assessment.value) return ''
  const map: Record<string, string> = {
    complete: '描述已足够完整，可直接生成',
    partial: '描述基本够用，少量关键项缺失',
    insufficient: '描述信息不足，建议进入采访',
  }
  return map[assessment.value.tier] || ''
})

const assessComplexityLabel = computed(() => {
  if (!assessment.value) return ''
  const map: Record<string, string> = {
    simple: '简单 Skill',
    moderate: '中等 Skill',
    complex: '复杂 Skill',
  }
  return map[assessment.value.skill_complexity] || ''
})

const assessComplexityColor = computed(() => {
  if (!assessment.value) return 'gray'
  const map: Record<string, string> = {
    simple: 'green',
    moderate: 'arcoblue',
    complex: 'orangered',
  }
  return map[assessment.value.skill_complexity] || 'gray'
})

const stepProgressText = computed(() => {
  if (step.value === 'agent') return '第 2/3 步'
  const index = Math.max(stepIndex.value, 0) + 1
  return `第 ${index}/4 步`
})

const currentStepTitle = computed(() => {
  const map: Record<Step, string> = {
    describe: '业务描述',
    interview: '采访补全',
    preview: '骨架预览',
    agent: 'AI 合成',
    commit: '命名发布',
  }
  return map[step.value]
})

const currentStepHint = computed(() => {
  const map: Record<Step, string> = {
    describe: '写清触发时机、判断条件和输出对象。',
    interview: '按当前轮次补齐关键变量，答案会合并进骨架。',
    preview: '确认规则、测试用例和风险标记。',
    agent: '实时查看生成进度、文件和发布前 Gate。',
    commit: '确认名称、部门和风险等级后创建到 Skill 仓库。',
  }
  return map[step.value]
})

const descriptionStatsText = computed(() => `${description.value.trim().length}/50000`)

const descriptionStateText = computed(() => {
  if (description.value.trim().length < 5) return '等待描述'
  if (assessmentPending.value) return 'AI 评估中'
  if (assessError.value) return '评估失败，可手动选择'
  if (assessment.value) return assessTierLabel.value
  return '可进入采访'
})

const directPathHint = computed(() => {
  if (assessment.value?.tier === 'complete') return '信息完整，适合一次成稿'
  if (assessment.value?.tier === 'partial') return '先出草稿，再编辑补充'
  if (assessError.value) return '评估失败时仍可生成'
  return '描述充分后可跳过采访'
})

const interviewPathHint = computed(() => {
  if (assessment.value?.tier === 'complete') return '需要人工确认时再使用'
  if (assessment.value?.tier === 'partial') return '补齐缺失项后更稳'
  return '当前默认路径'
})

async function runAssess() {
  const text = description.value.trim()
  if (text.length < 10) {
    assessment.value = null
    assessError.value = null
    return
  }
  const seq = ++assessSeq.value
  assessing.value = true
  assessError.value = null
  try {
    const res = await workbenchApi.architectAssess(text)
    if (seq !== assessSeq.value) return
    assessment.value = res
  } catch (error) {
    if (seq !== assessSeq.value) return
    assessment.value = null
    assessError.value = getErrorMessage(error, 'AI 评估服务失败')
  } finally {
    if (seq === assessSeq.value) assessing.value = false
  }
}

watch(description, () => {
  // 每次描述变化都作废旧评估，防止用户基于上一版结果按按钮
  assessError.value = null
  assessment.value = null
  if (assessTimer) clearTimeout(assessTimer)
  if (description.value.trim().length < 10) {
    return
  }
  assessTimer = setTimeout(() => { void runAssess() }, 800)
})

async function findSimilar() {
  if (!description.value.trim()) return
  similarLoading.value = true
  try {
    const response = await workbenchApi.architectFindSimilar(description.value.trim())
    similarSkills.value = normalizeSimilarSkills(response.items)
  } catch {
    similarSkills.value = []
  } finally {
    similarLoading.value = false
  }
}

function useExistingSkill(skill: SimilarSkillOption) {
  confirmAction({
    title: '基于已有 Skill 创建？',
    content: `将直接 Fork ${skill.name || skill.id}，并跳转到新 Skill 的编辑页。`,
    async onOk() {
      try {
        const response = await skillApi.createUnified({
          source: {
            type: 'fork',
            payload: {
              parent_skill_id: skill.id,
            },
          },
          department: currentUserDepartment(),
        })
        const createdId = response.skill_id
        if (!createdId) throw new Error('后端未返回 skill_id')
        clearWizardState()
        Message.success('已基于现有 Skill 创建副本')
        await navigateToCreatedSkill(createdId)
      } catch (error) {
        Message.error(getErrorMessage(error, 'Fork 失败，请重试'))
        throw error
      }
    },
  })
}

// ────────── Step 2: 采访 ──────────
const rounds = ref<ArchitectRound[]>([])
const currentRoundIdx = ref(0)
const currentRound = computed<ArchitectRound | null>(() => rounds.value[currentRoundIdx.value] || null)
const currentRoundNum = computed(() => currentRound.value ? currentRoundIdx.value + 1 : 1)
const interviewAnswers = ref<Record<string, ArchitectAnswerMap>>({})
const roundAnswers = ref<ArchitectAnswerMap>({})

function buildQuestionPlaceholder(question: ArchitectQuestion): string {
  if (question.kind === 'tags') return '输入标签后回车，可继续添加'
  if (question.kind === 'text') return '你的回答...'
  if (question.candidates.length) return `候选：${question.candidates.slice(0, 3).join(' / ')}`
  return '请选择'
}

function getSingleAnswer(questionId: string): string {
  const value = roundAnswers.value[questionId]
  return typeof value === 'string' ? value : ''
}

function setSingleAnswer(questionId: string, value: unknown): void {
  roundAnswers.value[questionId] = typeof value === 'string' ? value : ''
}

function getTextAnswer(questionId: string): string {
  const value = roundAnswers.value[questionId]
  return typeof value === 'string' ? value : ''
}

function setTextAnswer(questionId: string, value: unknown): void {
  roundAnswers.value[questionId] = typeof value === 'string' ? value : ''
}

function getMultiAnswer(questionId: string): string[] {
  return normalizeStringList(roundAnswers.value[questionId])
}

function setMultiAnswer(questionId: string, value: unknown): void {
  roundAnswers.value[questionId] = normalizeStringList(value)
}

async function startInterview() {
  loading.value = true
  try {
    const round = await workbenchApi.architectStart(buildCreationDescription())
    rounds.value = [round]
    currentRoundIdx.value = 0
    roundAnswers.value = buildRoundAnswers(round)
    step.value = 'interview'
  } catch (error) {
    Message.error(getErrorMessage(error, '启动采访失败，请检查 AI 服务可用性'))
  } finally {
    loading.value = false
  }
}

// v2.11.5：默认走老的稳定路径 /workbench/create-skill/stream（skill_creation_runner
// 后台任务 + 内建 3 次重试 + scratch 隔离），prompt 只 8k 不炸 GLM 额度。
// v2.11.4 新的 /architect/prepare-workspace + coding/stream 路径保留但不默认（prompt 25k 会炸 429）。
async function startDirectSynthesize() {
  const text = description.value.trim()
  if (text.length < 10) {
    Message.warning('描述至少 10 个字才能直接生成')
    return
  }
  synthesizing.value = true
  resetAgentRunState()
  agentRunning.value = false
  agentSkillId.value = null  // 老路径首帧后从 draft_created 事件拿
  agentRetryCount = 0
  if (agentRetryTimer) { clearTimeout(agentRetryTimer); agentRetryTimer = null }
  step.value = 'agent'
  openCreationStream(buildCreationDescription())
}

// 老路径事件协议：
//   { type: 'draft_created', draft_id }
//   { type: 'started', attempt, max_attempts }
//   { type: 'milestone', milestone, label, status, elapsed_s }
//   { type: 'thinking', content }
//   { type: 'tool_call', tool, input }
//   { type: 'tool_result', is_error, ... }
//   { type: 'contract_ready', contract }
//   { type: 'skill_ready', contract, files, elapsed_s }
//   { type: 'queued', position, max_concurrent_tasks }
//   { type: 'retry', reason, code, next_attempt, max_attempts }
//   { type: 'error', code, detail }
//   { type: 'done', status }
//   { type: 'heartbeat' | 'pong' }
function openCreationStream(firstMessage: string, resumeDraftId = '') {
  closeAgentStream()
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${proto}//${window.location.host}/api/skills/workbench/create-skill/stream`
  const ws = new WebSocket(url)
  agentClient.value = { close: () => ws.close() } as unknown as Record<string, unknown>
  agentRunning.value = true

  ws.onopen = () => {
    ws.send(JSON.stringify(
      resumeDraftId
        ? { type: 'resume', draft_id: resumeDraftId }
        : { type: 'start', message: firstMessage },
    ))
  }
  ws.onmessage = (evt) => {
    let msg: Record<string, unknown>
    try { msg = JSON.parse(evt.data) } catch { return }
    handleCreationEvent(msg, firstMessage)
  }
  ws.onerror = () => {
    if (agentRunning.value && !agentError.value) {
      agentError.value = 'WebSocket 连接错误'
    }
  }
  ws.onclose = () => {
    agentRunning.value = false
  }
}

function mergeAgentFiles(files: Record<string, unknown>) {
  const normalized: Record<string, string> = {}
  for (const [path, content] of Object.entries(files || {})) {
    if (typeof content === 'string') normalized[path] = content
  }
  if (!Object.keys(normalized).length) return

  const newKeys = Object.keys(normalized).filter((k) => !(k in agentFiles.value))
  agentFiles.value = { ...agentFiles.value, ...normalized }
  if (!selectedAgentFile.value) {
    selectedAgentFile.value = normalized['contract.json']
      ? 'contract.json'
      : Object.keys(normalized)[0] || null
  }
  newKeys.forEach(markFileRecentlyAdded)
}

function handleCreationEvent(msg: Record<string, unknown>, firstMessage: string) {
  const type = String(msg.type || '')
  switch (type) {
    case 'draft_created': {
      const draftId = String(msg.draft_id || '')
      agentSkillId.value = draftId
      pushTimelineEvent('info', `Draft 已创建：${draftId}`)
      break
    }
    case 'started': {
      const attempt = Number(msg.attempt || 1)
      const maxAttempts = Number(msg.max_attempts || 3)
      agentAttempt.value = attempt
      agentMaxAttempts.value = maxAttempts
      // 新 attempt 启动：把所有失败/已完成的重置回 pending，重新跑
      if (attempt > 1) {
        agentMilestones.value = defaultMilestones()
      }
      // 第一项（识别意图）进入 running
      if (agentMilestones.value[0] && agentMilestones.value[0].status === 'pending') {
        agentMilestones.value = agentMilestones.value.map((m, i) =>
          i === 0 ? { ...m, status: 'running' as MilestoneStatus } : m,
        )
      }
      pushTimelineEvent('info', `开始第 ${attempt}/${maxAttempts} 次尝试`)
      break
    }
    case 'queued': {
      const position = Number(msg.position || 0)
      const maxConcurrent = Number(msg.max_concurrent_tasks || 0)
      const positionText = position > 0 ? `第 ${position} 位` : '等待中'
      const limitText = maxConcurrent > 0 ? `，最大并发 ${maxConcurrent}` : ''
      pushTimelineEvent('info', `排队中（${positionText}${limitText}）`)
      break
    }
    case 'milestone': {
      const ms = String(msg.milestone || '')
      const label = String(msg.label || ms)
      const elapsed = Number(msg.elapsed_s || 0)
      setMilestoneDone(ms, elapsed)
      const elapsedPart = elapsed ? `（${elapsed}s）` : ''
      pushTimelineEvent('milestone', `${label}${elapsedPart}`)
      break
    }
    case 'thinking': {
      const content = String(msg.content || msg.text || '').trim()
      if (!content) break
      const head = content.length > 160 ? `${content.slice(0, 160)}…` : content
      pushTimelineEvent('thinking', head, content.length > 160 ? content : undefined)
      break
    }
    case 'tool_call': {
      const tool = String(msg.tool || msg.name || '')
      const input = (msg.input || {}) as Record<string, unknown>
      if (tool === 'Write' && input) {
        const fp = stripWorkDirLoose(String(input.file_path || ''))
        const content = String(input.content ?? '')
        if (fp) {
          const isNew = !(fp in agentFiles.value)
          agentFiles.value = { ...agentFiles.value, [fp]: content }
          if (!selectedAgentFile.value) selectedAgentFile.value = fp
          if (isNew) markFileRecentlyAdded(fp)
          pushTimelineEvent('tool_call', `写 ${fp}`)
        }
      } else if (tool) {
        // 展示其他工具调用（Read/Bash/Grep…）
        const rawInput = input as { command?: unknown; file_path?: unknown; pattern?: unknown }
        const hint = typeof rawInput.command === 'string'
          ? ` ${rawInput.command.slice(0, 60)}`
          : typeof rawInput.file_path === 'string'
            ? ` ${String(rawInput.file_path)}`
            : typeof rawInput.pattern === 'string'
              ? ` ${String(rawInput.pattern)}`
              : ''
        pushTimelineEvent('tool_call', `调用 ${tool}${hint}`)
      }
      break
    }
    case 'tool_result': {
      if (msg.is_error) {
        const preview = String(msg.content_preview || msg.content || '').trim()
        const shortPreview = preview.length > 140 ? `${preview.slice(0, 140)}…` : preview
        pushTimelineEvent(
          'tool_error',
          shortPreview || '工具返回错误（无详细信息）',
          preview.length > 140 ? preview : undefined,
        )
      }
      break
    }
    case 'contract_ready': {
      pushTimelineEvent('contract_ready', 'contract.json 已生成，可在左栏预览')
      break
    }
    case 'skill_ready': {
      mergeAgentFiles((msg.files || {}) as Record<string, unknown>)
      // 兜底：把还没 done 的 milestone 全部标 done
      agentMilestones.value = agentMilestones.value.map((m) =>
        m.status === 'done' || m.status === 'failed' ? m : { ...m, status: 'done' as MilestoneStatus },
      )
      agentDone.value = true
      pushTimelineEvent('skill_ready', '全部文件已生成')
      break
    }
    case 'skill_ready_enriched': {
      // creation_task_manager 附带 gate 信息，给用户渲染发布前检查 checklist
      mergeAgentFiles((msg.files || {}) as Record<string, unknown>)
      agentPreview.value = isRecord(msg.preview) ? msg.preview : null
      const gate = (msg.gate || {}) as { items?: Array<Record<string, unknown>> }
      if (Array.isArray(gate.items)) {
        gateItems.value = gate.items.map((raw) => ({
          key: String(raw.key || ''),
          label: String(raw.label || raw.key || ''),
          passed: !!raw.passed,
          detail: raw.detail ? String(raw.detail) : undefined,
        }))
      }
      break
    }
    case 'retry': {
      const reason = String(msg.reason || '')
      const next = Number(msg.next_attempt || 0)
      const max = Number(msg.max_attempts || 3)
      const retryIn = Number(msg.retry_in_s || 0)
      const retryDelay = retryIn > 0 ? `，${retryIn}s 后` : ''
      failRunningMilestone()
      pushTimelineEvent('retry', `内建重试 ${next}/${max}${retryDelay}：${reason}`)
      break
    }
    case 'error': {
      const code = String(msg.code || '')
      const detail = String(msg.detail || '')
      const combined = `${code}: ${detail}`.trim()
      if (/(429|1302|rate.?limit|速率限制|quota)/i.test(combined)) {
        scheduleAgentRetry(firstMessage, `速率限制 ${code}`)
        pushTimelineEvent('retry', `遇到速率限制 ${code}，准备重试`, detail || undefined)
      } else {
        agentError.value = combined || 'AI 合成出错'
        failRunningMilestone()
        pushTimelineEvent('tool_error', combined || 'AI 合成出错', detail || undefined)
      }
      break
    }
    case 'done': {
      const status = String(msg.status || '')
      agentRunning.value = false
      if (status !== 'ok' && !agentDone.value && !agentError.value) {
        agentError.value = '合成未完成'
        failRunningMilestone()
      }
      break
    }
    case 'heartbeat':
    case 'pong':
      break
    default:
      pushTimelineEvent('info', `未知事件 ${type}`)
  }
}

function stripWorkDirLoose(fullPath: string): string {
  // 老路径工作目录 /tmp/sf_skill_creation/<draft_id>/，取相对路径
  const m = fullPath.match(/sf_skill_creation\/[^/]+\/(.+)$/)
  if (m) return m[1]
  return fullPath.replace(/^\/+/, '').split('/').slice(-3).join('/')
}

let agentRetryTimer: ReturnType<typeof setTimeout> | null = null
let agentRetryCount = 0
const AGENT_MAX_RETRIES = 3

function scheduleAgentRetry(firstMessage: string, reason: string) {
  if (agentRetryTimer) return  // 已排队
  if (agentRetryCount >= AGENT_MAX_RETRIES) {
    agentError.value = `${reason}：已重试 ${AGENT_MAX_RETRIES} 次仍失败，请稍后手动重试`
    agentRunning.value = false
    return
  }
  agentRetryCount += 1
  // 指数退避 15 / 30 / 60s
  const delay = 15000 * agentRetryCount
  agentText.value += `\n[${reason}，${Math.round(delay / 1000)}s 后自动重试（${agentRetryCount}/${AGENT_MAX_RETRIES}）]\n`
  agentRetryTimer = setTimeout(() => {
    agentRetryTimer = null
    if (agentDone.value) return
    try {
      // 重新发起老路径创建流（新 WS + 新 draft_id）
      openCreationStream(firstMessage)
    } catch (e) {
      agentError.value = getErrorMessage(e, '重试失败')
      agentRunning.value = false
    }
  }, delay)
}

function closeAgentStream() {
  if (agentRetryTimer) {
    clearTimeout(agentRetryTimer)
    agentRetryTimer = null
  }
  if (agentClient.value) {
    try { agentClient.value.close?.() } catch { /* noop */ }
    agentClient.value = null
  }
}

function cancelAgent() {
  if (agentDone.value) {
    closeAgentStream()
    agentRunning.value = false
    const draftId = agentSkillId.value || ''
    confirmAction({
      title: '放弃这个已生成草稿？',
      content: '放弃后会回到新建页，从头生成不会再带上一次内容。',
      okText: '放弃并重新开始',
      cancelText: '继续查看',
      okStatus: 'danger',
      onOk() {
        void dismissRestoredDraft(draftId)
      },
    })
    return
  }
  if (agentRunning.value) {
    confirmAction({
      title: '取消将丢失当前生成进度，确定取消？',
      content: '中断后需要从头开始合成，已生成的内容将不会保留。',
      okText: '确认中断',
      cancelText: '继续合成',
      okStatus: 'danger',
      onOk() {
        closeAgentStream()
        agentRunning.value = false
        step.value = 'describe'
        agentSkillId.value = null
        synthesizing.value = false
      },
    })
    return
  }
  closeAgentStream()
  agentRunning.value = false
  step.value = 'describe'
  agentSkillId.value = null
  synthesizing.value = false
}

async function goToAgentCommit() {
  if (!agentSkillId.value) return
  closeAgentStream()
  autoFillFinalForm()
  step.value = 'commit'
}

// v2.12.0: 用户在 gate checklist 里点"确认预演结果"
async function handleApprovePreview() {
  if (!agentSkillId.value) return
  try {
    await workbenchApi.reviewTaskContract(agentSkillId.value, {
      checkpoint: 'preview',
      decision: 'approved',
      detail: { auto_by: 'creation_wizard_manual_confirm' },
    })
    gateUserApprovedPreview.value = true
    gateItems.value = gateItems.value.map((g) =>
      g.key === 'manual_confirmation' ? { ...g, passed: true } : g,
    )
    Message.success('已确认预演结果，可以发布')
  } catch (error) {
    Message.error(getErrorMessage(error, '确认失败'))
  }
}

function stripWorkDir(fullPath: string, skillId: string): string {
  // 后端 path 可能是绝对路径 skills-repo/{id}/scripts/main.py 或相对 scripts/main.py
  const marker = `/${skillId}/`
  const idx = fullPath.indexOf(marker)
  return idx >= 0 ? fullPath.slice(idx + marker.length) : fullPath
}

function formatFileSize(chars: number): string {
  if (chars < 1000) return `${chars}B`
  return `${(chars / 1024).toFixed(1)}KB`
}

function getFileLanguage(path: string): string {
  if (path.endsWith('.py')) return 'python'
  if (path.endsWith('.md')) return 'markdown'
  if (path.endsWith('.json')) return 'json'
  if (path.endsWith('.yaml') || path.endsWith('.yml')) return 'yaml'
  return 'plaintext'
}

function parseSkillMdFrontmatter(content: string): Record<string, unknown> {
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n---/)
  if (!match) return {}
  const meta: Record<string, unknown> = {}
  for (const rawLine of match[1].split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const idx = line.indexOf(':')
    if (idx <= 0) continue
    const key = line.slice(0, idx).trim()
    const value = line.slice(idx + 1).trim().replace(/^['"]|['"]$/g, '')
    if (key) meta[key] = value
  }
  return meta
}

async function submitRound() {
  const round = currentRound.value
  if (!round) return
  const answersKey = getRoundStorageKey(round, currentRoundIdx.value + 1)
  interviewAnswers.value = {
    ...interviewAnswers.value,
    [answersKey]: cloneAnswers(roundAnswers.value),
  }

  loading.value = true
  try {
    const response = await workbenchApi.architectNext({
      current_round: getRoundStorageKey(round, currentRoundIdx.value + 1),
      description: description.value.trim(),
      answers: interviewAnswers.value,
      skill_draft: {},
    })
    if (isArchitectDoneResponse(response)) {
      await runSynthesize(false)
      return
    }
    rounds.value.push(response)
    currentRoundIdx.value += 1
    roundAnswers.value = buildRoundAnswers(response)
  } catch (error) {
    Message.error(getErrorMessage(error, '采访下一步失败'))
  } finally {
    loading.value = false
  }
}

function skipRound() {
  void submitRound()
}

function goBackRound() {
  if (currentRoundIdx.value > 0) {
    currentRoundIdx.value -= 1
    const prevRound = currentRound.value
    const key = getRoundStorageKey(prevRound, currentRoundIdx.value + 1)
    roundAnswers.value = cloneAnswers(interviewAnswers.value[key] || buildRoundAnswers(prevRound))
  } else {
    step.value = 'describe'
  }
}

// ────────── Step 3: 合成 ──────────
const generatedSkill = ref<ArchitectGeneratedSkill | null>(null)
const lintReport = ref<ArchitectLintReport | null>(null)
const pipelineError = ref('')
const rawPipelineResult = ref<ArchitectPipelineResponse | null>(null)
const synthMode = ref<ArchitectGenerationMode>('basic')
// v2.10.0 V210-2：流式进度（WebSocket push 的阶段事件）
const streamPhase = ref('')
const streamMessage = ref('')

function _buildWsUrl(path: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}/api${path}`
}

function applyPipelineResult(result: ArchitectPipelineResponse, invalidMetaMessage: string): void {
  rawPipelineResult.value = result
  if (result.similar_skills?.length) {
    similarSkills.value = normalizeSimilarSkills(result.similar_skills)
  }
  lintReport.value = result.lint_report || null

  const skill = result.skill
  if (skill?._error) {
    pipelineError.value = formatPipelineIssue(skill._error)
    generatedSkill.value = null
    return
  }
  if (!skill?.meta) {
    pipelineError.value = invalidMetaMessage
    generatedSkill.value = null
    return
  }
  generatedSkill.value = skill
}

function synthesizeViaStream(payload: ArchitectSynthesizePayload): Promise<ArchitectPipelineResponse> {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(_buildWsUrl('/skills/architect/stream'))
    let doneResult: ArchitectPipelineResponse | null = null
    const timeoutId = window.setTimeout(() => {
      try { ws.close() } catch { /* ignore */ }
      reject(new Error('WebSocket 合成超时（120s）'))
    }, 120000)

    ws.onopen = () => {
      ws.send(JSON.stringify(payload))
    }
    ws.onmessage = (ev) => {
      let parsed: unknown
      try {
        parsed = JSON.parse(ev.data) as unknown
      } catch {
        return
      }
      const evt = parseStreamEvent(parsed)
      if (!evt) return
      if (evt.type === 'phase') {
        streamPhase.value = evt.phase || ''
        streamMessage.value = evt.message || ''
        if (evt.similar_skills) {
          similarSkills.value = normalizeSimilarSkills(evt.similar_skills)
        }
        if (evt.phase === 'synthesize_ready' && evt.skill) {
          generatedSkill.value = evt.skill
        }
      } else if (evt.type === 'done') {
        doneResult = evt.result || {}
        try { ws.close() } catch { /* ignore */ }
      } else if (evt.type === 'error') {
        window.clearTimeout(timeoutId)
        try { ws.close() } catch { /* ignore */ }
        reject(new Error(evt.message || '流式合成失败'))
      }
    }
    ws.onclose = () => {
      window.clearTimeout(timeoutId)
      if (doneResult) resolve(doneResult)
      else reject(new Error('WebSocket 关闭前未收到 done 事件'))
    }
    ws.onerror = () => {
      window.clearTimeout(timeoutId)
      reject(new Error('WebSocket 连接失败'))
    }
  })
}

async function runSynthesize(regenerate: boolean) {
  synthesizing.value = true
  pipelineError.value = ''
  streamPhase.value = ''
  streamMessage.value = ''
  if (regenerate) generatedSkill.value = null
  step.value = 'preview'
  const payload: ArchitectSynthesizePayload = {
    description: buildCreationDescription(),
    answers: interviewAnswers.value,
    mode: synthMode.value,
  }

  // v2.10.0 V210-2：优先走 WebSocket 流式；失败降级到 HTTP
  try {
    const result = await synthesizeViaStream(payload)
    applyPipelineResult(result, 'AI 返回格式异常：缺少 meta 字段')
    synthesizing.value = false
    return
  } catch (wsError) {
    // WS 失败 → 走 HTTP fallback（老路径兼容）
    streamMessage.value = `WS 不可用（${getErrorMessage(wsError, '未知错误')}），走 HTTP 回退...`
  }

  try {
    const call = synthMode.value === 'swarm'
      ? workbenchApi.swarmGenerate
      : workbenchApi.architectSynthesize
    const response = await call(payload)
    applyPipelineResult(response, 'AI 返回格式异常：缺少 meta 字段（可能是 LLM 响应被截断）')
  } catch (error) {
    pipelineError.value = getErrorMessage(error, '合成失败')
    generatedSkill.value = null
  } finally {
    synthesizing.value = false
  }
}

// ────────── Step 4: 命名确认 ──────────
const finalForm = ref<FinalFormState>(createDefaultFinalForm())
const availableDepts = ref<string[]>([])
const creationContext = ref<SkillCreationContext | null>(null)
const skillIdPreview = ref('')
const PLACEHOLDER_TEXTS = new Set(['', '未指定', '待选择', '待填写', '-', '—', 'unknown', 'none', 'null'])

const generatedSkillIdPreview = computed(() => {
  if (finalForm.value.skill_id) return finalForm.value.skill_id
  return skillIdPreview.value || '自动生成（留空即可）'
})

const creationCanChooseDepartment = computed(() => creationContext.value?.can_choose_department !== false)
const namingPolicyHint = computed(() =>
  creationContext.value?.naming_policy?.description || '留空则后端按 Skill 名称生成拼音 slug + 随机后缀',
)
const departmentSourceHint = computed(() => {
  const source = creationContext.value?.department_source || 'none'
  const map: Record<string, string> = {
    org_units: '部门来自后端组织架构。',
    org_membership: '部门来自你的组织归属。',
    user_profile: '部门来自你的用户资料。',
    none: '未找到部门归属。',
  }
  return map[source] || '部门由后端创建上下文决定。'
})

function cleanWizardText(value: unknown, maxLen?: number) {
  const text = typeof value === 'string' ? value.trim() : ''
  if (!text || PLACEHOLDER_TEXTS.has(text) || PLACEHOLDER_TEXTS.has(text.toLowerCase())) return ''
  return typeof maxLen === 'number' ? text.slice(0, maxLen).trim() : text
}

function currentUserDepartment() {
  return cleanWizardText(userStore.userInfo?.department, 50)
}

function deriveSkillNameFallback() {
  const fromGoal = cleanWizardText(generatedSkill.value?.goal, 30)
  if (fromGoal) return fromGoal
  const text = description.value.replace(/\s+/g, ' ').trim()
  if (!text) return ''
  return text.split(/[。.!！？?，,；;]/)[0]?.slice(0, 30).trim() || text.slice(0, 30).trim()
}

async function loadDepts() {
  try {
    const context = await workbenchApi.getCreationContext()
    creationContext.value = context
    availableDepts.value = Array.isArray(context.allowed_departments) ? context.allowed_departments : []
    const current = cleanWizardText(finalForm.value.department, 50)
    if (!current) {
      finalForm.value.department = cleanWizardText(context.default_department, 50) || availableDepts.value[0] || ''
    } else if (!availableDepts.value.includes(current)) {
      finalForm.value.department = cleanWizardText(context.default_department, 50) || availableDepts.value[0] || current
    }
  } catch {
    if (!userStore.userInfo) {
      try {
        await userStore.fetchUser()
      } catch {
        // ignore
      }
    }
    const userDepartment = currentUserDepartment()
    availableDepts.value = userDepartment ? [userDepartment] : []
    if (!cleanWizardText(finalForm.value.department)) finalForm.value.department = userDepartment
  }
}

let idPreviewTimer: ReturnType<typeof setTimeout> | null = null

async function refreshSkillIdPreview() {
  const name = cleanWizardText(finalForm.value.name, 80)
  if (!name || finalForm.value.skill_id) {
    skillIdPreview.value = ''
    return
  }
  try {
    const preview = await workbenchApi.previewCreationId({
      name,
      department: finalForm.value.department,
    })
    skillIdPreview.value = preview.skill_id || ''
  } catch {
    skillIdPreview.value = ''
  }
}

watch(
  () => [finalForm.value.name, finalForm.value.department, finalForm.value.skill_id],
  () => {
    if (idPreviewTimer) clearTimeout(idPreviewTimer)
    idPreviewTimer = setTimeout(() => { void refreshSkillIdPreview() }, 350)
  },
)

async function commit() {
  // v2.11.4：agent 路径走 finalize-creation（草稿已经在后端）
  if (agentSkillId.value) {
    await commitAgentDraft()
    return
  }
  if (!generatedSkill.value) return
  creating.value = true
  try {
    // 把最后确认的 meta 合并进 generated skill
    const finalSkill = {
      ...generatedSkill.value,
      meta: {
        ...(generatedSkill.value.meta || {}),
        name: finalForm.value.name,
        department: finalForm.value.department,
        risk_level: finalForm.value.risk_level,
      },
    }
    const response = await skillApi.createUnified({
      source: {
        type: 'architect',
        payload: {
          skill_dict: finalSkill,
        },
      },
      name: finalForm.value.name,
      department: finalForm.value.department,
      risk_level: finalForm.value.risk_level,
      skill_id: finalForm.value.skill_id || undefined,
    })
    const createdId = response.skill_id
    if (!createdId) throw new Error('后端未返回 skill_id')
    Message.success('Skill 已创建，跳转到编辑页')
    // v2.8.2 C3：创建成功清除草稿
    clearWizardState()
    // v2.8.2 B1：创建完成后跳编辑模式（不再同页切换 isCreate）
    await navigateToCreatedSkill(createdId)
  } catch (error) {
    Message.error(getErrorMessage(error, '创建失败，请重试'))
  } finally {
    creating.value = false
  }
}

async function commitAgentDraft() {
  if (!agentSkillId.value) return
  creating.value = true
  try {
    // 老路径简化 UX：7 个文件已在用户面前，点"发布"即视为人工确认沙箱预演
    // 把 4 必感知点中的 preview 置 approved，后端 gate 才能放行（否则 finalize 抛 400 "尚未通过 4 必感知点确认"）
    try {
      await workbenchApi.reviewTaskContract(agentSkillId.value, {
        checkpoint: 'preview',
        decision: 'approved',
        detail: { auto_by: 'creation_wizard_finalize' },
      })
    } catch (preApproveErr) {
      // 非阻断：若已是 approved / 端点瞬时错误，仍走 finalize，让后端的真正错误来决定
      console.warn('[wizard] pre-approve preview failed, proceeding to finalize', preApproveErr)
    }
    // v2.11.5：老路径 finalize —— cp scratch → skill_repo + git commit
    const resp = await workbenchApi.createSkillFinalize(agentSkillId.value, {
      name: finalForm.value.name,
      department: finalForm.value.department,
      risk_level: finalForm.value.risk_level,
      skill_id: finalForm.value.skill_id || undefined,
    })
    const createdId = resp.skill_id
    if (!createdId) throw new Error('后端未返回 skill_id')
    Message.success(`Skill 已创建：${createdId}`)
    clearWizardState()
    await navigateToCreatedSkill(createdId)
  } catch (error) {
    Message.error(getErrorMessage(error, '发布失败，请重试'))
  } finally {
    creating.value = false
  }
}

function cancelWizard() {
  confirmAction({
    title: '放弃创建？',
    content: '已填写的采访答案和草稿将清除（本地留存直到创建或 7 天失效）。',
    okText: '放弃并清除',
    okStatus: 'danger',
    onOk() {
      clearWizardState()
      void router.push('/skills')
    },
  })
}

// ────────── Auto-prefill ──────────
// 观察 description 变化时，预填最终表单（让命名步骤省事）
function autoFillFinalForm() {
  const skillMdMeta = parseSkillMdFrontmatter(agentFiles.value['SKILL.md'] || '')
  const contract = agentContract.value || {}
  const risks = isRecord(contract['risks']) ? contract['risks'] : {}
  const contractMeta = isRecord(contract['meta']) ? contract['meta'] : {}
  const generatedMeta = generatedSkill.value?.meta || {}

  if (!cleanWizardText(finalForm.value.name)) {
    finalForm.value.name = cleanWizardText(skillMdMeta.name, 80)
      || cleanWizardText(contractMeta.name, 80)
      || cleanWizardText(generatedMeta.name, 80)
      || deriveSkillNameFallback()
  }
  if (!cleanWizardText(finalForm.value.department)) {
    finalForm.value.department = cleanWizardText(skillMdMeta.department, 50)
      || cleanWizardText(risks.department, 50)
      || cleanWizardText(generatedMeta.department, 50)
      || cleanWizardText(creationContext.value?.default_department, 50)
      || currentUserDepartment()
  }
  const risk = skillMdMeta.risk_level || risks.level || generatedMeta.risk_level
  if (isFinalRiskLevel(risk)) {
    finalForm.value.risk_level = risk
  }
}

watch(step, (s) => {
  if (s === 'commit') autoFillFinalForm()
})

function applyMilestoneSnapshot(milestones: unknown) {
  if (!Array.isArray(milestones)) return
  const doneKeys = new Set(
    milestones
      .map((item) => (isRecord(item) ? String(item.key || '') : ''))
      .filter(Boolean),
  )
  if (!doneKeys.size) return

  agentMilestones.value = agentMilestones.value.map((m) =>
    doneKeys.has(m.key) ? { ...m, status: 'done' as MilestoneStatus } : m,
  )
  const nextIdx = agentMilestones.value.findIndex((m) => m.status === 'pending')
  if (nextIdx >= 0) {
    agentMilestones.value = agentMilestones.value.map((m, i) =>
      i === nextIdx ? { ...m, status: 'running' as MilestoneStatus } : m,
    )
  }
}

function isReadyDraftReopenable(active: Record<string, unknown>): boolean {
  const raw = typeof active.reopen_expires_at === 'string' ? active.reopen_expires_at : ''
  if (!raw) return false
  const expiresAt = toDate(raw)?.getTime() ?? Number.NaN
  return Number.isFinite(expiresAt) && expiresAt > Date.now()
}

function applyActiveCreationDraft(active: Record<string, unknown>): void {
  const draftId = String(active.draft_id || '')
  resetAgentRunState()
  agentSkillId.value = draftId
  description.value = String(active.source_message || description.value || '')
  step.value = 'agent'

  mergeAgentFiles((active.files || {}) as Record<string, unknown>)
  agentPreview.value = isRecord(active.preview) ? active.preview : null
  applyMilestoneSnapshot(active.milestones)

  const status = String(active.generation_status || '')
  const taskAlive = !!active.task_alive
  agentDone.value = status === 'ready'
  agentRunning.value = taskAlive && status !== 'ready'
  synthesizing.value = status !== 'ready'

  if (agentDone.value) {
    agentMilestones.value = agentMilestones.value.map((m) =>
      m.status === 'failed' ? m : { ...m, status: 'done' as MilestoneStatus },
    )
  }

  const gate = (active.gate || {}) as { items?: Array<Record<string, unknown>> }
  if (Array.isArray(gate.items)) {
    gateItems.value = gate.items.map((raw) => ({
      key: String(raw.key || ''),
      label: String(raw.label || raw.key || ''),
      passed: !!raw.passed,
      detail: raw.detail ? String(raw.detail) : undefined,
    }))
  }

  pushTimelineEvent(
    agentDone.value ? 'skill_ready' : 'info',
    agentDone.value ? '已恢复完成的 Skill 草稿' : `已恢复后台生成任务：${draftId}`,
  )

  if (taskAlive && status !== 'ready') {
    openCreationStream(description.value, draftId)
  }
}

async function dismissRestoredDraft(draftId: string): Promise<void> {
  if (draftId) {
    try {
      await workbenchApi.dismissDraft(draftId)
    } catch {
      // ignore: 从头开始不应被旧草稿清理失败阻断
    }
  }
  resetAgentRunState()
  agentSkillId.value = null
  description.value = ''
  step.value = 'describe'
  synthesizing.value = false
  clearWizardState()
}

async function restoreActiveCreationDraft(): Promise<boolean> {
  let active: Record<string, unknown> | null = null
  try {
    const response = await workbenchApi.getMyActiveDraft()
    active = isRecord(response) ? response : null
  } catch {
    return false
  }
  if (!active) return false
  const draftId = String(active.draft_id || '')
  if (!draftId) return false
  const activeDraft: Record<string, unknown> = active

  const status = String(activeDraft.generation_status || '')
  const taskAlive = !!activeDraft.task_alive

  if (status === 'ready') {
    if (!isReadyDraftReopenable(activeDraft)) {
      void dismissRestoredDraft(draftId)
      return false
    }
    confirmAction({
      title: '发现已生成的草稿',
      content: '上一次 Skill 已生成完成。继续会打开该草稿；从头开始会放弃它并清空当前新建页。',
      okText: '继续查看',
      cancelText: '从头开始',
      onOk() {
        applyActiveCreationDraft(activeDraft)
      },
      onCancel() {
        void dismissRestoredDraft(draftId)
      },
    })
    return true
  }

  if (taskAlive && (status === 'pending' || status === 'running')) {
    applyActiveCreationDraft(activeDraft)
    return true
  }

  return false
}

onMounted(async () => {
  await loadDepts()
  if (await restoreActiveCreationDraft()) return
  // v2.8.2 C3：发现已有草稿 → 问是否恢复
  const saved = loadWizardState()
  if (saved && saved.description && (Date.now() - (saved.savedAt || 0) < 7 * 86400_000)) {
    confirmAction({
      title: '发现未完成的草稿',
      content: `上次（${formatTime(saved.savedAt)}）正在创建 Skill，是否继续？`,
      okText: '恢复',
      cancelText: '从头开始',
      onOk() {
        description.value = saved.description || ''
        outputChannels.value = normalizeOutputChannels(saved.outputChannels)
        todoOutputKind.value = saved.todoOutputKind || 'dispatch'
        todoReviewerRole.value = saved.todoReviewerRole || 'operator'
        rounds.value = saved.rounds || []
        currentRoundIdx.value = saved.currentRoundIdx || 0
        interviewAnswers.value = saved.interviewAnswers || {}
        roundAnswers.value = saved.roundAnswers || buildRoundAnswers(saved.rounds[saved.currentRoundIdx] || null)
        generatedSkill.value = saved.generatedSkill
        finalForm.value = normalizeFinalForm(saved.finalForm)
        step.value = saved.step || 'describe'
      },
      onCancel() {
        clearWizardState()
      },
    })
  }
})

// 主要状态变更时自动写 localStorage（debounce 500ms）
let autosaveTimer: ReturnType<typeof setTimeout> | null = null
watch(
  () => [
    description.value,
    outputChannels.value,
    todoOutputKind.value,
    todoReviewerRole.value,
    step.value,
    currentRoundIdx.value,
    roundAnswers.value,
    generatedSkill.value,
  ],
  () => {
    if (autosaveTimer) clearTimeout(autosaveTimer)
    autosaveTimer = setTimeout(saveWizardState, 500)
  },
  { deep: true },
)

onUnmounted(() => {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  if (idPreviewTimer) clearTimeout(idPreviewTimer)
  // 组件卸载时关闭仍活跃的 AI agent WS，避免后台任务继续跑 + 内存泄漏
  closeAgentStream()
})
</script>

<style scoped>
/* 设计稿 page chrome 覆盖 —— 与 SkillList / AdminUsers 同模式 */
.skill-creation-wizard-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.skill-creation-wizard-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.skill-creation-wizard-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.skill-creation-wizard-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}

/* 顶部按钮统一外形 —— 30px / 6px 圆角 / 12.5px 字号 */
.skill-creation-wizard-page :deep(.arco-btn:not(.arco-btn-primary):not(.arco-btn-text)) {
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
.skill-creation-wizard-page :deep(.arco-btn:not(.arco-btn-primary):not(.arco-btn-text):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.skill-creation-wizard-page :deep(.arco-btn-primary) {
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
.skill-creation-wizard-page :deep(.arco-btn-primary:hover) {
  background: #000;
  border-color: #000;
}

/* a-tag → ai-pill：20px / 4px 方角 / 11px / 500 */
.skill-creation-wizard-page :deep(.arco-tag.arco-tag-size-small),
.skill-creation-wizard-page :deep(.arco-tag.arco-tag-size-mini),
.skill-creation-wizard-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}
.skill-creation-wizard-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.skill-creation-wizard-page :deep(.arco-tag-color-blue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.skill-creation-wizard-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.skill-creation-wizard-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.skill-creation-wizard-page :deep(.arco-tag-color-orange),
.skill-creation-wizard-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.skill-creation-wizard-page :deep(.arco-tag-color-purple),
.skill-creation-wizard-page :deep(.arco-tag-color-purpleblue) {
  background: var(--ai-accent-soft) !important;
  color: var(--ai-accent-ink) !important;
  border-color: transparent !important;
}
.skill-creation-wizard-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

.wizard-container {
  max-width: 1160px;
  margin: 0 auto;
  padding-bottom: 40px;
}
.wizard-header {
  margin-bottom: 14px;
}
.wizard-steps {
  margin: 0;
  min-width: 0;
}
.wizard-steps :deep(.arco-steps-item-title) {
  color: var(--ai-ink-2);
  font-weight: 500;
}
.wizard-steps :deep(.arco-steps-item-active .arco-steps-item-title),
.wizard-steps :deep(.arco-steps-item-process .arco-steps-item-title) {
  color: var(--ai-ink-1);
  font-weight: 600;
}
.wizard-steps :deep(.arco-steps-item-finish .arco-steps-item-icon),
.wizard-steps :deep(.arco-steps-item-process .arco-steps-item-icon),
.wizard-steps :deep(.arco-steps-item-active .arco-steps-item-icon) {
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
  font-family: var(--ai-font-mono);
  font-weight: 500;
}
.wizard-steps :deep(.arco-steps-item-wait .arco-steps-item-icon) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border);
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
  font-weight: 500;
}
.wizard-steps :deep(.arco-steps-item-tail::after) {
  background: var(--ai-border);
}
.wizard-steps :deep(.arco-steps-item-finish .arco-steps-item-tail::after) {
  background: var(--ai-ink-1);
}
.wizard-progress-shell {
  display: grid;
  grid-template-columns: minmax(190px, 240px) minmax(0, 1fr);
  gap: 18px;
  align-items: center;
  margin-bottom: 18px;
  padding: 16px 18px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  box-shadow: none;
}
.wizard-progress-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  padding-right: 18px;
  border-right: 1px solid var(--ai-border);
}
.wizard-progress-copy strong {
  color: var(--ai-ink-1);
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.005em;
  line-height: 22px;
}
.wizard-progress-copy span:last-child {
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 18px;
}
.progress-kicker,
.panel-kicker {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  line-height: 16px;
}
.panel-title-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  line-height: 1.3;
}
.wizard-panel {
  border-radius: var(--ai-radius);
  overflow: hidden;
}
.wizard-panel:hover {
  transform: none !important;
}
.wizard-panel :deep(.arco-card-body) {
  padding: 24px !important;
  overflow: visible;
}
.wizard-panel :deep(.arco-card-header),
.wizard-panel :deep(.arco-card-head) {
  height: auto !important;
  min-height: 56px !important;
  padding: 14px 24px !important;
  border-bottom: 1px solid var(--ai-border) !important;
  background: var(--ai-surface);
  align-items: center !important;
}
.wizard-panel :deep(.arco-card-header-title),
.wizard-panel :deep(.arco-card-head-title) {
  min-width: 0;
  color: var(--ai-ink-1);
  font-weight: 600 !important;
  font-size: 13px !important;
  line-height: 1.4 !important;
  white-space: normal !important;
}
.wizard-panel :deep(.arco-card-header-extra),
.wizard-panel :deep(.arco-card-head-extra) {
  align-self: center;
}
.wizard-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-top: 24px;
  padding-top: 18px;
  border-top: 1px solid var(--ai-border);
}
.wizard-nav {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.wizard-step-badge {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 11.5px;
  font-weight: 500;
  font-family: var(--ai-font-mono);
  border: 1px solid var(--ai-border);
}

/* 描述 */
.describe-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 18px;
  align-items: start;
}
.describe-main {
  min-width: 0;
}
.description-input :deep(.arco-textarea-wrapper) {
  min-height: 190px;
  border-color: var(--ai-border);
  background: var(--ai-surface);
  border-radius: 6px;
}
.description-input :deep(.arco-textarea-wrapper:focus-within),
.description-input :deep(.arco-textarea-wrapper.arco-textarea-focus) {
  border-color: var(--ai-ink-3);
}
.description-input :deep(textarea) {
  line-height: 1.7;
}
.output-capability-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 14px;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}
.capability-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.capability-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.capability-subtitle {
  margin-top: 2px;
  font-size: 12px;
  font-weight: 400;
  color: var(--ai-ink-3);
}
.capability-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.capability-card {
  min-height: 70px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 5px;
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.capability-card strong {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.capability-card span {
  font-size: 12px;
  color: var(--ai-ink-3);
  line-height: 1.45;
  font-weight: 400;
}
.capability-card:hover {
  border-color: var(--ai-border-2);
  background: var(--ai-surface-3);
}
.capability-card.active {
  border-color: var(--ai-ink-3);
  background: var(--ai-surface);
  box-shadow: inset 0 0 0 1px var(--ai-ink-3);
}
.capability-card.locked {
  cursor: default;
}
.todo-capability-config {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding-top: 2px;
}
.todo-role-select {
  width: 150px;
}
.todo-config-hint {
  min-width: 220px;
  flex: 1;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ai-ink-3);
  font-weight: 400;
}
.describe-aside {
  position: sticky;
  top: 72px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}
.aside-stat,
.aside-note {
  display: flex;
  gap: 6px;
}
.aside-stat {
  align-items: baseline;
  justify-content: space-between;
}
.aside-note {
  flex-direction: column;
  align-items: flex-start;
}
.aside-stat span,
.aside-note-label,
.path-panel-title {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.aside-stat strong,
.aside-note strong {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 500;
  line-height: 20px;
}
.aside-divider {
  height: 1px;
  background: var(--ai-border);
}
.path-panel {
  display: grid;
  gap: 8px;
}
.path-choice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
}
.path-choice.active {
  border-color: var(--ai-ink-3);
  background: var(--ai-surface);
}
.path-choice div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.path-choice strong {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 500;
  line-height: 18px;
}
.path-choice span {
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 18px;
}

/* 采访 */
.interview-body {
  display: grid;
  gap: 14px;
}
.round-intro {
  font-size: 13px;
  color: var(--ai-ink-2);
  padding: 12px 14px;
  border-left: 2px solid var(--ai-info);
  background: var(--ai-info-soft);
  border-radius: 4px;
}
.question-list {
  display: grid;
  gap: 12px;
}
.question-block {
  margin: 0;
  padding: 14px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}
.question-choice-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.question-tag-select {
  width: 100%;
}
.q-label {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 10px;
}
.q-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 22px;
  border-radius: 4px;
  background: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  font-weight: 500;
  color: var(--ai-surface);
  flex-shrink: 0;
}
.q-text {
  flex: 1;
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
  line-height: 22px;
}

/* 相似 Skill */
.similar-row {
  margin-top: 14px;
}
.similar-label {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 6px;
}
.similar-chip {
  cursor: pointer;
  margin-right: 6px;
}

/* v2.12.0: Agent 合成工作台 */
.wizard-agent {
  min-height: 520px;
}
.agent-card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.agent-card-status {
  display: flex;
  gap: 6px;
  align-items: center;
}
.agent-skill-tag {
  font-family: var(--ai-font-mono);
}
.agent-stepper {
  margin-bottom: 18px;
}
.agent-layout {
  display: grid;
  grid-template-columns: 270px minmax(0, 1fr);
  gap: 0;
  height: 460px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  overflow: hidden;
}
.agent-files-pane {
  border-right: 1px solid var(--ai-border);
  overflow: hidden;
  display: flex;
  min-width: 0;
}
.agent-files-pane > * { flex: 1; min-width: 0; }
.agent-preview {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.agent-preview-header {
  padding: 8px 12px;
  border-bottom: 1px solid var(--ai-border);
  font-size: 12px;
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
}
.agent-preview-path {
  font-family: var(--ai-font-mono);
}
.agent-preview-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.agent-preview-body :deep(.monaco-editor-container) {
  height: 100%;
  border: 0;
  border-radius: 0;
}
.agent-preview-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--ai-ink-4);
  font-size: 13px;
}
.agent-runtime-card {
  margin-top: 12px;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}
.agent-runtime-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}
.agent-runtime-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.agent-runtime-subtitle,
.agent-runtime-muted,
.agent-runtime-empty {
  font-size: 12px;
  line-height: 1.6;
  color: var(--ai-ink-3);
}
.agent-runtime-counts,
.agent-runtime-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.agent-runtime-summary {
  margin-top: 8px;
  white-space: pre-line;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
  border-radius: 4px;
  padding: 8px 10px;
}
.agent-runtime-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 10px;
}
.agent-runtime-section {
  min-width: 0;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  background: var(--ai-surface-2);
  padding: 10px;
}
.agent-runtime-section-title {
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 8px;
}
.agent-runtime-item {
  padding: 8px;
  border-radius: 4px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}
.agent-runtime-item + .agent-runtime-item {
  margin-top: 8px;
}
.agent-runtime-item-head {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: 13px;
  color: var(--ai-ink-1);
}
.agent-runtime-item-head strong {
  font-weight: 500;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.agent-runtime-meta {
  margin-top: 6px;
  font-size: 11px;
  color: var(--ai-ink-3);
}
.agent-runtime-tasks {
  margin-top: 6px;
  display: grid;
  gap: 4px;
}
.agent-runtime-task {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 6px;
  align-items: start;
  font-size: 12px;
  color: var(--ai-ink-2);
}
.agent-runtime-task span {
  width: 18px;
  height: 18px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-style: normal;
  font-weight: 500;
  font-family: var(--ai-font-mono);
  border: 1px solid var(--ai-border);
}
.agent-runtime-task em {
  font-style: normal;
  min-width: 0;
  overflow-wrap: anywhere;
}
.agent-error-banner {
  margin-top: 12px;
  padding: 10px 14px;
  border-radius: var(--ai-radius);
  border: 1px solid transparent;
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.agent-timeline {
  margin-top: 12px;
}
.agent-gate {
  margin-top: 16px;
}
.wizard-actions-secondary {
  justify-content: space-between;
}

/* v2.11.3: 描述完整度评估面板 */
.assess-panel {
  margin-top: 12px;
  padding: 12px 14px;
  border-radius: var(--ai-radius);
  border: 1px solid var(--ai-border);
  font-size: 13px;
  line-height: 1.6;
}
.assess-loading {
  color: var(--ai-ink-3);
  display: flex;
  align-items: center;
  gap: 6px;
}
.assess-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
  margin-bottom: 4px;
  min-width: 0;
  flex-wrap: wrap;
}
.assess-confidence {
  margin-left: auto;
  font-weight: 400;
  font-size: 12px;
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
}
.assess-body {
  color: var(--ai-ink-2);
  margin-top: 4px;
}
.assess-chips {
  margin-top: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.assess-complete {
  background: var(--ai-ok-soft);
  border-color: transparent;
}
.assess-complete .assess-title { color: var(--ai-ok); }
.assess-partial {
  background: var(--ai-info-soft);
  border-color: transparent;
}
.assess-partial .assess-title { color: var(--ai-info); }
.assess-insufficient {
  background: var(--ai-surface-2);
  border-color: var(--ai-border);
}
.assess-insufficient .assess-title { color: var(--ai-ink-2); }
.assess-error {
  background: var(--ai-bad-soft);
  border-color: transparent;
}
.assess-error .assess-title { color: var(--ai-bad); }

/* 预览 */
.preview-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.preview-section {
  padding: 14px;
  border: 1px solid var(--ai-border);
  border-left: 2px solid var(--ai-ink-3);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}
.section-label {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 8px;
}
.section-text {
  font-size: 13px;
  color: var(--ai-ink-1);
  line-height: 1.6;
}
.meta-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  align-items: center;
  font-size: 13px;
  color: var(--ai-ink-1);
}
.rule-item {
  margin-bottom: 10px;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  background: var(--ai-surface-2);
}
.rule-item strong {
  display: block;
  margin-bottom: 4px;
  color: var(--ai-ink-1);
  font-weight: 500;
  font-family: var(--ai-font-mono);
  font-size: 12.5px;
}
.branch-list {
  margin: 6px 0 0 14px;
  padding: 0;
  list-style: disc;
}
.branch-list li {
  font-size: 12px;
  color: var(--ai-ink-2);
  margin-bottom: 4px;
  line-height: 20px;
}
.branch-list code {
  background: var(--ai-surface-3);
  padding: 1px 5px;
  border-radius: 3px;
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-1);
  border: 1px solid var(--ai-border);
}
.branch-action {
  color: var(--ai-ink-3);
}
.simple-list {
  margin: 0 0 0 16px;
  padding: 0;
  font-size: 13px;
  color: var(--ai-ink-2);
}

/* 错误 */
.synth-error {
  padding: 12px 0;
}
.error-actions {
  margin-top: 12px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.raw-response {
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  padding: 12px;
  border-radius: 4px;
  font-size: 11px;
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-2);
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

/* 命名 */
.commit-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 18px;
  align-items: start;
}
.commit-form {
  min-width: 0;
}
.commit-form :deep(.arco-radio-group) {
  display: flex;
  flex-wrap: wrap;
}
.commit-form :deep(.arco-input-wrapper),
.commit-form :deep(.arco-select-view) {
  border-radius: 6px;
  border-color: var(--ai-border);
  background: var(--ai-surface);
}
.commit-form :deep(.arco-input-wrapper:focus-within),
.commit-form :deep(.arco-select-view:focus-within) {
  border-color: var(--ai-ink-3);
}
.commit-form :deep(.arco-form-item-label) {
  color: var(--ai-ink-2);
  font-size: 12.5px;
  font-weight: 500;
}
.commit-summary {
  padding: 14px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}
.commit-summary-title {
  margin-bottom: 12px;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.commit-summary dl {
  display: grid;
  gap: 12px;
  margin: 0;
}
.commit-summary dl > div {
  display: grid;
  gap: 3px;
}
.commit-summary dt {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.commit-summary dd {
  margin: 0;
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 500;
  word-break: break-word;
}
.field-hint {
  font-size: 12px;
  color: var(--ai-ink-3);
  margin-top: 4px;
}

.empty-preview {
  padding: 32px 0;
  text-align: center;
}

/* Skill ID 字段 mono 字体 */
.commit-form :deep(.arco-input-wrapper input[placeholder*="ID"]),
.commit-form :deep(.arco-input) {
  font-family: var(--ai-font-sans);
}

:global(body[arco-theme='dark']) .wizard-progress-shell,
:global(body[arco-theme='dark']) .describe-aside,
:global(body[arco-theme='dark']) .output-capability-panel,
:global(body[arco-theme='dark']) .capability-card,
:global(body[arco-theme='dark']) .question-block,
:global(body[arco-theme='dark']) .preview-section,
:global(body[arco-theme='dark']) .commit-summary,
:global(body[arco-theme='dark']) .agent-layout {
  background: var(--ai-surface-2);
  border-color: var(--ai-border);
}

@media (max-width: 920px) {
  .wizard-progress-shell {
    grid-template-columns: 1fr;
    gap: 12px;
  }
  .wizard-progress-copy {
    padding-right: 0;
    padding-bottom: 12px;
    border-right: none;
    border-bottom: 1px solid var(--ai-border);
  }
  .describe-grid,
  .commit-layout {
    grid-template-columns: 1fr;
  }
  .describe-aside {
    position: static;
  }
  .capability-grid {
    grid-template-columns: 1fr;
  }
  .agent-layout {
    grid-template-columns: 230px minmax(0, 1fr);
  }
}

@media (max-width: 768px) {
  .wizard-steps :deep(.arco-steps-item-title) {
    font-size: 12px;
  }
  .question-block {
    padding: 10px;
  }
  .q-text {
    font-size: 13px;
  }
  .interview-body {
    gap: 10px;
  }
  .question-list {
    gap: 8px;
  }
}

/* v2.9.1 M3：移动端 / 窄屏适配 */
@media (max-width: 640px) {
  .wizard-container {
    padding: 12px 12px 80px;
  }
  .wizard-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
  .wizard-nav {
    align-self: stretch;
    justify-content: space-between;
  }
  .wizard-progress-shell {
    padding: 12px;
  }
  .wizard-progress-copy strong {
    font-size: 15px;
  }
  .wizard-panel :deep(.arco-card-body) {
    padding: 16px !important;
  }
  .wizard-panel :deep(.arco-card-header),
  .wizard-panel :deep(.arco-card-head) {
    min-height: 62px !important;
    padding: 14px 16px !important;
  }
  .wizard-actions {
    flex-direction: column-reverse;
    gap: 10px;
    align-items: stretch;
  }
  .wizard-actions > * {
    width: 100%;
  }
  .wizard-actions :deep(.arco-space) {
    display: flex;
    flex-direction: column;
    width: 100%;
  }
  .wizard-actions :deep(.arco-space-item),
  .wizard-actions :deep(.arco-btn) {
    width: 100%;
  }
  .path-choice {
    align-items: flex-start;
  }
  .meta-row {
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
  }
  .agent-layout {
    grid-template-columns: 1fr;
    height: auto;
  }
  .agent-files-pane {
    min-height: 180px;
    max-height: 220px;
    border-right: none;
    border-bottom: 1px solid var(--ai-border);
  }
  .agent-preview {
    height: 340px;
  }
  .agent-runtime-grid {
    grid-template-columns: 1fr;
  }
  .agent-runtime-head {
    flex-direction: column;
  }
  .error-actions {
    flex-direction: column;
  }
  .error-actions > * {
    width: 100%;
  }
  /* step 组件默认横向排 4 格，窄屏改 2x2 */
  .wizard-steps {
    flex-wrap: wrap;
    row-gap: 8px;
  }
  .raw-response {
    font-size: 10px;
    max-height: 180px;
  }
}
</style>
