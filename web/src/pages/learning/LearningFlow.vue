<template>
  <div class="learning-page ai-main" :class="pageMotionClass" :style="pageMotionStyle">
    <header class="learning-head ai-pagehead">
      <div>
        <div class="ai-crumbs">SkillForge · AI Workflow / 脉动 / Agent / Skills / 知识库 / 训练</div>
        <h1 class="ai-title">脉动</h1>
        <p class="ai-sub">集中查看自动处理状态、待办阻塞、知识生成、训练样本和审核流程；默认只展示可执行结论，明细可切换详细视图。</p>
      </div>
      <div class="learning-actions">
        <span class="ai-pill" :class="summary.scope === 'global' ? 'accent' : 'info'">{{ scopeText }}</span>
        <button class="ai-btn" type="button" :disabled="loading" @click="reloadFromUi">
          <SfShellIcon name="refresh" />刷新数据
        </button>
        <button class="ai-btn primary" type="button" :disabled="backfilling" @click="backfill(false)">
          <SfShellIcon name="flow" />{{ backfilling ? '同步中' : '同步最新记录' }}
        </button>
        <button class="ai-btn advanced-action" type="button" :disabled="backfilling" @click="backfill(true)">
          <SfShellIcon name="database" />同步并生成知识
        </button>
        <button class="ai-btn" type="button" :disabled="automationRunning" @click="runAutomationNow">
          <SfShellIcon name="spark" />{{ automationRunning ? '处理中' : '执行一次自动处理' }}
        </button>
        <button class="ai-btn advanced-action" type="button" :disabled="agentizing" @click="runAgentizationNow">
          <SfShellIcon name="bot" />{{ agentizing ? '判断中' : 'AI判断是否Agent化' }}
        </button>
        <button class="ai-btn advanced-action" type="button" :disabled="selfAuditing" @click="runSelfAuditNow">
          <SfShellIcon name="shield" />{{ selfAuditing ? '自审中' : 'AI自审缺口' }}
        </button>
        <button class="ai-btn" type="button" @click="togglePulseLayoutMode">
          <SfShellIcon :name="pulseLayoutMode === 'simple' ? 'flow' : 'eye'" />{{ pulseLayoutMode === 'simple' ? '详细视图' : '返回概览' }}
        </button>
      </div>
    </header>

    <div class="learning-workspace">
      <aside
        ref="aiSideMenuRef"
        class="ai-side-menu ai-card"
        :class="{ 'tools-expanded': aiSideToolsExpanded }"
        :style="aiSideMenuStyle"
        aria-label="自动化工作台侧栏"
        @scroll="requestAiSideSectionSync"
      >
        <span class="ai-side-scroll-progress" aria-hidden="true"></span>
        <div class="ai-side-head">
          <span class="ai-pill" :class="pulseHealth.tone === 'good' ? 'accent' : 'info'">自动化工作台</span>
          <strong>{{ pulseHealth.title }}</strong>
          <em>{{ pulseHealth.detail }}</em>
        </div>

        <div class="ai-side-quick-controls" aria-label="显示控制">
          <button
            v-for="mode in motionModeOptions"
            :key="`quick-motion-${mode.key}`"
            type="button"
            :class="{ active: motionMode === mode.key }"
            :aria-pressed="motionMode === mode.key"
            @click="motionMode = mode.key"
          >
            <SfShellIcon :name="mode.icon" />
            <span>{{ mode.label }}</span>
          </button>
          <button
            type="button"
            class="layout-toggle"
            :aria-pressed="pulseLayoutMode === 'full'"
            @click="togglePulseLayoutMode"
          >
            <SfShellIcon :name="pulseLayoutMode === 'simple' ? 'flow' : 'eye'" />
            <span>{{ pulseLayoutMode === 'simple' ? '详细视图' : '返回概览' }}</span>
          </button>
        </div>

        <section
          class="ai-operation-center"
          :class="[`tone-${aiStickyCommand.tone}`, designToneClass(aiStickyCommand.tone)]"
          :style="aiOperationCenterStyle"
          aria-label="自动处理建议"
        >
          <div class="ai-operation-head">
            <span>
              <small>自动处理建议</small>
              <strong>{{ aiStickyCommand.label }}</strong>
              <em>{{ aiOperationHeaderMeta }}</em>
            </span>
            <b>
              <i>{{ aiStickyCommandConfidence.score }}%</i>
              <small>{{ aiStickyCommandConfidence.label }}</small>
            </b>
          </div>
          <div class="ai-operation-status" aria-label="自动处理状态">
            <button
              v-for="item in aiOperationStatusItems"
              :key="`operation-status-${item.key}`"
              type="button"
              class="ai-operation-status-item"
              :class="[`tone-${item.tone}`, designToneClass(item.tone)]"
              @click="openPulseDecisionItem(item)"
            >
              <i aria-hidden="true"></i>
              <span>
                <strong>{{ item.label }}</strong>
                <em>{{ item.value }}</em>
              </span>
              <b>{{ item.meta }}</b>
            </button>
          </div>
          <div class="ai-operation-decision" aria-label="当前可执行信息">
            <button
              v-for="fact in aiOperationDecisionCards"
              :key="`operation-decision-${fact.key}`"
              type="button"
              class="ai-operation-decision-card"
              :class="[`tone-${fact.tone}`, designToneClass(fact.tone)]"
              @click="openPulseDecisionItem(fact)"
            >
              <i><SfShellIcon :name="fact.icon" /></i>
              <span>
                <strong>{{ fact.label }}</strong>
                <em>{{ fact.meta }}</em>
              </span>
              <b>{{ fact.value }}</b>
            </button>
          </div>
          <div class="ai-operation-queue" aria-label="自动队列快照">
            <button
              v-for="chip in aiOperationQueueChips"
              :key="`operation-queue-${chip.key}`"
              type="button"
              class="ai-operation-queue-chip"
              :class="[`tone-${chip.tone}`, designToneClass(chip.tone)]"
              :style="{ '--operation-queue-level': `${chip.level}%` }"
              @click="openPulseDecisionItem(chip)"
            >
              <i><SfShellIcon :name="chip.icon" /></i>
              <span>
                <strong>{{ chip.label }}</strong>
                <em>{{ chip.meta }}</em>
              </span>
              <b>{{ chip.value }}</b>
            </button>
          </div>
          <div class="ai-operation-actions">
            <button type="button" :disabled="aiStickyCommandDisabled" @click="runAiStickyCommand">
              <SfShellIcon :name="aiStickyCommand.icon" />{{ aiStickyCommand.cta }}
            </button>
            <button type="button" @click="openAiMissionBrief">
              <SfShellIcon name="search" />处理依据
            </button>
            <button type="button" @click="openPrimaryGuardrail">
              <SfShellIcon name="shield" />权限与安全
            </button>
          </div>
        </section>

        <section class="ai-simple-priority" aria-label="处理清单">
          <div class="ai-simple-priority-title">
            <span>处理清单</span>
            <em>{{ aiSimplePrioritySummary }}</em>
          </div>
          <div class="ai-simple-priority-list">
            <button
              v-for="(card, index) in aiSimplePriorityItems"
              :key="`priority-${card.key}`"
              type="button"
              class="ai-simple-priority-item"
              :class="[`tone-${card.tone}`, designToneClass(card.tone), { primary: index === 0 }]"
              @click="openPulseOverviewCard(card)"
            >
              <i><SfShellIcon :name="card.icon" /></i>
              <span>
                <strong>{{ card.label }}</strong>
                <em>{{ card.meta }}</em>
                <small>{{ card.reason }}</small>
              </span>
              <b>{{ card.value }}</b>
            </button>
          </div>
        </section>

        <section class="ai-side-hud" aria-label="当前任务">
          <button
            v-for="card in aiSideHudCards"
            :key="card.key"
            type="button"
            class="ai-side-hud-card"
            :class="`tone-${card.tone}`"
            @click="openAiSideHudCard(card)"
          >
            <i><SfShellIcon :name="card.icon" /></i>
            <span>
              <strong>{{ card.label }}</strong>
              <em>{{ card.meta }}</em>
            </span>
            <b>{{ card.value }}</b>
          </button>
        </section>

        <section class="ai-mission-brief" :class="[`tone-${aiMissionBrief.tone}`, designToneClass(aiMissionBrief.tone)]" :style="aiMissionBriefStyle" aria-label="当前建议简报">
          <button type="button" class="ai-mission-summary" @click="openAiMissionBrief">
            <span class="ai-mission-kicker">
              <i><SfShellIcon :name="aiMissionBrief.icon" /></i>
              <small>{{ aiMissionBrief.kicker }}</small>
              <b>{{ aiMissionBrief.confidence }}%</b>
            </span>
            <strong>{{ aiMissionBrief.title }}</strong>
            <em>{{ aiMissionBrief.meta }}</em>
            <span class="ai-mission-output">{{ aiMissionBrief.output }}</span>
          </button>
          <div class="ai-mission-steps" aria-label="四阶段建议">
            <button
              v-for="(step, index) in aiMissionSteps"
              :key="step.key"
              type="button"
              class="ai-mission-step"
              :class="[`tone-${step.tone}`, designToneClass(step.tone), { active: step.key === aiMissionActiveStep }]"
              :style="{ '--mission-step-progress': `${step.progress}%`, '--mission-step-delay': `${index * 0.13}s` }"
              @click="openAiMissionStep(step)"
            >
              <i><SfShellIcon :name="step.icon" /></i>
              <span>
                <strong>{{ step.label }}</strong>
                <em>{{ step.meta }}</em>
              </span>
              <b>{{ step.value }}</b>
            </button>
          </div>
        </section>

        <button
          type="button"
          class="ai-side-orbit"
          :class="`health-${pulseHealth.tone}`"
          @click="showPulseCore"
        >
          <span class="orbit-core">
            <b>{{ pulseHealth.score }}</b>
            <i>健康度</i>
          </span>
          <span
            v-for="(node, index) in pulseNodes.slice(0, 6)"
            :key="`side-orbit-${node.key}`"
            class="orbit-signal"
            :class="`tone-${node.tone}`"
            :style="sideOrbitSignalStyle(index, Math.min(6, pulseNodes.length))"
          >
            <SfShellIcon :name="node.icon" />
          </span>
        </button>

        <section class="ai-side-index" aria-label="侧栏索引">
          <button
            v-for="item in aiSideSectionAnchors"
            :key="item.id"
            type="button"
            class="ai-side-index-button"
            :class="[`tone-${item.tone}`, { active: activeAiSideSection === item.id }]"
            @click="scrollToAiSideSection(item.id)"
          >
            <i><SfShellIcon :name="item.icon" /></i>
            <span>{{ item.label }}</span>
            <b>{{ item.value }}</b>
          </button>
        </section>

        <section id="ai-side-heartbeat" class="ai-side-section ai-heartbeat-panel">
          <div class="ai-side-section-title">
            <span>系统状态</span>
            <em>{{ aiHeartbeatSummary }}</em>
          </div>
          <button
            type="button"
            class="ai-heartbeat-core"
            :class="`tone-${aiHeartbeat.tone}`"
            :style="aiHeartbeatStyle"
            @click="openAiHeartbeatCore"
          >
            <span class="ai-heartbeat-ring">
              <b>{{ aiHeartbeat.score }}</b>
              <i>节奏</i>
            </span>
            <span>
              <strong>{{ aiHeartbeat.title }}</strong>
              <em>{{ aiHeartbeat.meta }}</em>
            </span>
            <small>{{ aiHeartbeat.cta }}</small>
          </button>
          <div class="ai-heartbeat-steps" aria-label="系统状态步骤">
            <button
              v-for="(step, index) in aiHeartbeatSteps"
              :key="step.key"
              type="button"
              class="ai-heartbeat-step"
              :class="`tone-${step.tone}`"
              :style="{ '--heartbeat-step-delay': `${index * 0.14}s` }"
              @click="openAiHeartbeatStep(step)"
            >
              <i><SfShellIcon :name="step.icon" /></i>
              <span>
                <strong>{{ step.label }}</strong>
                <em>{{ step.meta }}</em>
              </span>
              <b>{{ step.value }}</b>
            </button>
          </div>
        </section>

        <section id="ai-side-decision" class="ai-side-section ai-decision-queue">
          <div class="ai-side-section-title">
            <span>建议优先级</span>
            <em>{{ aiDecisionQueueSummary }}</em>
          </div>
          <div class="ai-decision-stack" aria-label="建议优先级">
            <button
              v-for="(item, index) in aiDecisionQueue"
              :key="item.key"
              type="button"
              class="ai-decision-item"
              :class="`tone-${item.tone}`"
              :style="{ '--decision-score': `${item.score}%`, '--decision-delay': `${index * 0.12}s` }"
              @click="openAiDecisionQueueItem(item)"
            >
              <i><SfShellIcon :name="item.icon" /></i>
              <span>
                <strong>{{ item.label }}</strong>
                <em>{{ item.meta }}</em>
              </span>
              <b>{{ item.value }}</b>
              <small>{{ item.cta }}</small>
            </button>
          </div>
        </section>

        <section id="ai-side-orchestrator" class="ai-side-section ai-command-plan-section">
          <div class="ai-side-section-title">
            <span>执行步骤</span>
            <em>{{ aiCommandPlanSummary }}</em>
          </div>
          <div class="ai-command-plan" :style="aiCommandPlanStyle" aria-label="执行步骤">
            <span class="ai-command-plan-track"></span>
            <button
              v-for="(step, index) in aiCommandPlanSteps"
              :key="step.key"
              type="button"
              class="ai-command-step"
              :class="`tone-${step.tone}`"
              :style="{ '--command-step-score': `${step.score}%`, '--command-step-delay': `${index * 0.11}s` }"
              @click="openAiCommandPlanStep(step)"
            >
              <small>{{ index + 1 }} · {{ step.phase }}</small>
              <i><SfShellIcon :name="step.icon" /></i>
              <span>
                <strong>{{ step.label }}</strong>
                <em>{{ step.meta }}</em>
              </span>
              <b>{{ step.value }}</b>
              <u>{{ step.cta }}</u>
            </button>
          </div>
        </section>

        <section id="ai-side-guardrail" class="ai-side-section ai-execution-guardrail" :style="aiExecutionGuardrailStyle">
          <div class="ai-side-section-title">
            <span>执行前说明</span>
            <em>{{ aiExecutionGuardrailSummary }}</em>
          </div>
          <div class="ai-guardrail-board" aria-label="执行前说明">
            <span class="ai-guardrail-scan" aria-hidden="true"></span>
            <button
              v-for="(guard, index) in aiExecutionGuardrails"
              :key="guard.key"
              type="button"
              class="ai-guardrail-card"
              :class="`tone-${guard.tone}`"
              :style="{ '--guardrail-delay': `${index * 0.12}s` }"
              @click="openAiExecutionGuardrail(guard)"
            >
              <i><SfShellIcon :name="guard.icon" /></i>
              <span>
                <strong>{{ guard.label }}</strong>
                <em>{{ guard.meta }}</em>
              </span>
              <b>{{ guard.value }}</b>
            </button>
          </div>
        </section>

        <section v-if="selected" id="ai-side-context" class="ai-side-section ai-side-context">
          <div class="ai-side-section-title">
            <span>当前观察对象</span>
            <em>{{ selected.type }}</em>
          </div>
          <div class="ai-side-context-card">
            <span class="ai-side-context-type">{{ selected.type }}</span>
            <strong>{{ detailTitle }}</strong>
            <em>{{ detailEntity ? `${entityTypeText(detailEntity.type)} · ${shortId(detailEntity.id)}` : (detailHighlights[0]?.value || '临时详情') }}</em>
            <div class="ai-side-context-actions">
              <button v-if="detailDeepLink" type="button" @click="openDetailDeepLink">打开原页面</button>
              <button v-if="detailEntity" type="button" :disabled="lineageLoading" @click="loadDetailLineage">
                {{ lineageLoading ? '加载中' : detailLineage.length ? `血缘 ${detailLineage.length}` : '查看血缘' }}
              </button>
              <button type="button" @click="selected = null">清除</button>
            </div>
          </div>
        </section>

        <section id="ai-side-autopilot" class="ai-side-section">
          <div class="ai-side-section-title">
            <span>自动处理建议</span>
            <em>{{ aiAutopilotSummary }}</em>
          </div>
          <div class="ai-autopilot" :class="`tone-${aiAutopilotPrimary.tone}`" :style="aiAutopilotStyle">
            <div class="ai-autopilot-head">
              <span><SfShellIcon :name="aiAutopilotPrimary.icon" /></span>
              <strong>{{ aiAutopilotPrimary.label }}</strong>
              <em>{{ aiAutopilotPrimary.meta }}</em>
              <button type="button" class="ai-autopilot-primary" :disabled="backfilling || automationRunning || loading" @click="runAutopilotPrimary">
                {{ aiAutopilotPrimary.cta }}
              </button>
            </div>
            <div class="ai-autopilot-rail" aria-label="自动处理建议步骤">
              <i class="ai-autopilot-energy"></i>
              <button
                v-for="(stage, index) in aiAutopilotStages"
                :key="stage.key"
                type="button"
                class="ai-autopilot-stage"
                :class="`tone-${stage.tone}`"
                :style="{ '--autopilot-stage-progress': `${stage.progress}%`, '--autopilot-stage-delay': `${index * 0.14}s` }"
                @click="openAutopilotStage(stage)"
              >
                <span><SfShellIcon :name="stage.icon" /></span>
                <strong>{{ stage.label }}</strong>
                <em>{{ stage.meta }}</em>
                <b>{{ stage.status }}</b>
              </button>
            </div>
          </div>
        </section>

        <section id="ai-side-evidence" class="ai-side-section">
          <div class="ai-side-section-title">
            <span>风险判断证据</span>
            <em>{{ aiEvidenceSummary }}</em>
          </div>
          <div class="ai-evidence-stream" aria-label="风险判断证据流">
            <button
              v-for="(signal, index) in aiEvidenceSignals"
              :key="signal.key"
              type="button"
              class="ai-evidence-card"
              :class="`tone-${signal.tone}`"
              :style="{ '--evidence-strength': `${signal.strength}%`, '--evidence-delay': `${index * 0.11}s` }"
              @click="openAiEvidence(signal)"
            >
              <i><SfShellIcon :name="signal.icon" /></i>
              <span>
                <strong>{{ signal.label }}</strong>
                <em>{{ signal.meta }}</em>
              </span>
              <b>{{ signal.value }}</b>
            </button>
          </div>
        </section>

        <section id="ai-side-rootcause" class="ai-side-section">
          <div class="ai-side-section-title">
            <span>原因分析</span>
            <em>{{ aiRootCauseSummary }}</em>
          </div>
          <div class="ai-rootcause-panel" aria-label="原因分析">
            <button
              v-for="(cause, index) in aiRootCauses"
              :key="cause.key"
              type="button"
              class="ai-rootcause-card"
              :class="`tone-${cause.tone}`"
              :style="{ '--rootcause-score': `${cause.score}%`, '--rootcause-delay': `${index * 0.12}s` }"
              @click="openAiRootCause(cause)"
            >
              <i><SfShellIcon :name="cause.icon" /></i>
              <span>
                <strong>{{ cause.label }}</strong>
                <em>{{ cause.reason }}</em>
              </span>
              <b>{{ cause.score }}%</b>
              <small>{{ cause.action }}</small>
            </button>
          </div>
        </section>

        <section id="ai-side-impact" class="ai-side-section">
          <div class="ai-side-section-title">
            <span>影响评估</span>
            <em>{{ aiImpactSummary }}</em>
          </div>
          <div class="ai-impact-grid" aria-label="影响评估">
            <button
              v-for="(impact, index) in aiImpactForecasts"
              :key="impact.key"
              type="button"
              class="ai-impact-card"
              :class="`tone-${impact.tone}`"
              :style="{ '--impact-score': `${impact.score}%`, '--impact-delay': `${index * 0.12}s` }"
              @click="openAiImpactForecast(impact)"
            >
              <i><SfShellIcon :name="impact.icon" /></i>
              <span>
                <strong>{{ impact.label }}</strong>
                <em>{{ impact.meta }}</em>
              </span>
              <b>{{ impact.value }}</b>
              <small>{{ impact.change }}</small>
            </button>
          </div>
        </section>

        <section id="ai-side-heat" class="ai-side-section">
          <div class="ai-side-section-title">
            <span>优先级排序</span>
            <em>{{ aiHeatSummary }}</em>
          </div>
          <div class="ai-heat-stack" aria-label="优先级排序">
            <button
              v-for="(item, index) in aiHeatSignals"
              :key="item.key"
              type="button"
              class="ai-heat-card"
              :class="`tone-${item.tone}`"
              :style="{ '--heat-score': `${item.score}%`, '--heat-delay': `${index * 0.12}s` }"
              @click="openAiHeatSignal(item)"
            >
              <span><SfShellIcon :name="item.icon" /></span>
              <strong>{{ item.label }}</strong>
              <em>{{ item.meta }}</em>
              <b>{{ item.value }}</b>
            </button>
          </div>
        </section>

        <section id="ai-side-playbook" class="ai-side-section">
          <div class="ai-side-section-title">
            <span>快捷处理方案</span>
            <em>{{ aiPlaybookSummary }}</em>
          </div>
          <div class="ai-playbook-grid" aria-label="快捷处理方案">
            <button
              v-for="playbook in aiPlaybooks"
              :key="playbook.key"
              type="button"
              class="ai-playbook"
              :class="`tone-${playbook.tone}`"
              :style="{ '--playbook-score': `${playbook.score}%` }"
              @click="applyAiPlaybook(playbook)"
            >
              <i><SfShellIcon :name="playbook.icon" /></i>
              <strong>{{ playbook.label }}</strong>
              <em>{{ playbook.meta }}</em>
              <b>{{ playbook.cta }}</b>
            </button>
          </div>
        </section>

        <section class="ai-side-more-tools" aria-label="更多侧栏工具">
          <button
            type="button"
            :aria-expanded="aiSideToolsExpanded"
            @click="toggleAiSideTools"
          >
            <i><SfShellIcon name="tool" /></i>
            <span>
              <strong>{{ aiSideToolsExpanded ? '收起辅助工具' : '展开更多工具' }}</strong>
              <em>{{ aiSideMoreToolsSummary }}</em>
            </span>
            <b>{{ aiSideToolsExpanded ? '收起' : '展开' }}</b>
          </button>
        </section>

        <section v-if="aiActionTrail.length" id="ai-side-trail" class="ai-side-section">
          <div class="ai-side-section-title">
            <span>操作记录</span>
            <em>{{ aiActionTrailSummary }}</em>
          </div>
          <div class="ai-action-trail" aria-label="操作记录">
            <button
              v-for="item in aiActionTrail"
              :key="item.id"
              type="button"
              class="ai-trail-item"
              :class="`tone-${item.tone}`"
              @click="replayAiActionTrail(item)"
            >
              <i><SfShellIcon :name="item.icon" /></i>
              <span>
                <strong>{{ item.label }}</strong>
                <em>{{ item.meta }}</em>
              </span>
              <b>查看记录</b>
            </button>
            <button type="button" class="ai-trail-clear" @click="clearAiActionTrail">清空记录</button>
          </div>
        </section>

        <section id="ai-side-motion" class="ai-side-section">
          <div class="ai-side-section-title">
            <span>显示模式</span>
            <em>{{ activeMotionModeLabel }}</em>
          </div>
          <div class="ai-motion-mode" aria-label="显示模式切换">
            <button
              v-for="mode in motionModeOptions"
              :key="mode.key"
              type="button"
              :class="[`tone-${mode.tone}`, { active: motionMode === mode.key }]"
              :aria-pressed="motionMode === mode.key"
              @click="motionMode = mode.key"
            >
              <i><SfShellIcon :name="mode.icon" /></i>
              <strong>{{ mode.label }}</strong>
              <em>{{ mode.meta }}</em>
            </button>
          </div>
        </section>

        <section id="ai-side-perspective" class="ai-side-section">
          <div class="ai-side-section-title">
            <span>查看视角</span>
            <em>{{ activeAiPerspectiveLabel }}</em>
          </div>
          <div class="ai-perspective-grid" aria-label="查看视角切换">
            <button
              v-for="view in aiPerspectives"
              :key="view.key"
              type="button"
              class="ai-perspective"
              :class="[`tone-${view.tone}`, { active: isAiPerspectiveActive(view) }]"
              @click="applyAiPerspective(view)"
            >
              <i><SfShellIcon :name="view.icon" /></i>
              <strong>{{ view.label }}</strong>
              <em>{{ view.meta }}</em>
            </button>
          </div>
        </section>

        <section id="ai-side-focus" class="ai-side-section ai-focus-control">
          <div class="ai-side-section-title">
            <span>聚焦控制</span>
            <em>{{ aiFocusCommandSummary }}</em>
          </div>
          <button
            type="button"
            class="ai-focus-radar"
            :class="{ active: activeFilterChips.length }"
            @click="traceCurrentFocus"
          >
            <i></i>
            <span>
              <strong>{{ activeAiPerspectiveLabel }}</strong>
              <em>{{ aiFocusNarrative }}</em>
            </span>
            <b>查看</b>
          </button>
          <div class="ai-focus-shortcuts" aria-label="聚焦快捷入口">
            <button
              v-for="focus in sideQuickFocusFilters"
              :key="`side-focus-${focus.key}`"
              type="button"
              class="ai-focus-shortcut"
              :class="[`tone-${focus.tone}`, { active: isQuickFocusActive(focus) }]"
              @click="applySideQuickFocus(focus)"
            >
              <i><SfShellIcon :name="focus.icon" /></i>
              <span>
                <strong>{{ focus.label }}</strong>
                <em>{{ focus.meta }}</em>
              </span>
            </button>
          </div>
          <div v-if="activeFilterChips.length" class="ai-focus-chips" aria-label="当前聚焦筛选">
            <button
              v-for="chip in activeFilterChips"
              :key="`focus-chip-${chip.key}`"
              type="button"
              @click="clearPulseFilter(chip.key)"
            >
              <span>{{ chip.label }}</span>
              <strong>{{ chip.value }}</strong>
              <i>×</i>
            </button>
          </div>
          <div class="ai-focus-actions">
            <button type="button" @click="openAdvancedFilters">高级筛选</button>
            <button v-if="activeFilterChips.length" type="button" @click="resetFilters()">清空聚焦</button>
          </div>
        </section>

        <section id="ai-side-meter" class="ai-side-section">
          <div class="ai-side-section-title">
            <span>显示状态</span>
            <em>动画速度来自真实队列</em>
          </div>
          <button
            v-for="meter in aiMotionMeters"
            :key="meter.key"
            type="button"
            class="ai-motion-meter"
            :class="`tone-${meter.tone}`"
            @click="openMotionMeter(meter)"
          >
            <span>
              <strong>{{ meter.label }}</strong>
              <em>{{ meter.meta }}</em>
            </span>
            <b>{{ meter.value }}</b>
            <i :style="{ width: `${meter.percent}%` }"></i>
          </button>
        </section>

        <section id="ai-side-actions" class="ai-side-section">
          <div class="ai-side-section-title">
            <span>下一步操作</span>
            <em>{{ aiSideNextActions.length }} 条建议</em>
          </div>
          <button
            v-for="action in aiSideNextActions"
            :key="action.key"
            type="button"
            class="ai-side-action"
            :class="`tone-${action.tone}`"
            @click="runSideAction(action)"
          >
            <span><SfShellIcon :name="action.icon" /></span>
            <strong>{{ action.label }}</strong>
            <em>{{ action.meta }}</em>
            <b>{{ action.cta }}</b>
          </button>
        </section>

        <section id="ai-side-panels" class="ai-side-section">
          <div class="ai-side-section-title">
            <span>面板跳转</span>
            <em>按处理路径定位</em>
          </div>
          <nav class="ai-side-nav" aria-label="学习页面面板跳转">
            <button
              v-for="item in aiSideNavItems"
              :key="item.id"
              type="button"
              :class="{ active: activeSidePanel === item.id }"
              :aria-current="activeSidePanel === item.id ? 'location' : undefined"
              @click="scrollToPanel(item.id)"
            >
              <i><SfShellIcon :name="item.icon" /></i>
              <span>{{ item.label }}</span>
              <em>{{ item.meta }}</em>
            </button>
          </nav>
        </section>

        <section id="ai-side-route" class="ai-side-section">
          <div class="ai-side-section-title">
            <span>处理路径</span>
            <em>{{ aiSideRouteSummary }}</em>
          </div>
          <div class="ai-side-route" :style="aiSideRouteStyle" aria-label="自动处理路径">
            <span class="ai-side-route-track"></span>
            <span class="ai-side-route-energy"></span>
            <button
              v-for="(step, index) in aiSideRouteSteps"
              :key="step.key"
              type="button"
              class="ai-side-route-step"
              :class="[`tone-${step.tone}`, { active: activeSidePanel === step.target }]"
              :style="{ '--route-step-delay': `${index * 0.13}s` }"
              :aria-current="activeSidePanel === step.target ? 'step' : undefined"
              @click="openSideRouteStep(step)"
            >
              <i><SfShellIcon :name="step.icon" /></i>
              <strong>{{ step.label }}</strong>
              <em>{{ step.value }}</em>
              <b>{{ step.status }}</b>
            </button>
          </div>
        </section>

        <section id="ai-side-queue" class="ai-side-section">
          <div class="ai-side-section-title">
            <span>自动处理队列</span>
            <em>{{ automationTitle }}</em>
          </div>
          <button
            v-for="metric in automationMetrics.slice(0, 3)"
            :key="`side-${metric.key}`"
            type="button"
            class="ai-side-metric"
            :class="`tone-${metric.tone}`"
            @click="setSelected({ type: 'automation_metric', data: metric.data })"
          >
            <span>{{ metric.label }}</span>
            <strong>{{ metric.value }}</strong>
            <em>{{ metric.meta }}</em>
          </button>
        </section>

        <button
          type="button"
          class="ai-sticky-command"
          :class="[`tone-${aiStickyCommand.tone}`, designToneClass(aiStickyCommand.tone)]"
          :disabled="aiStickyCommandDisabled"
          aria-label="建议操作"
          @click="runAiStickyCommand"
        >
          <i><SfShellIcon :name="aiStickyCommand.icon" /></i>
          <span>
            <small>{{ aiStickyCommand.kicker }}</small>
            <strong>{{ aiStickyCommand.label }}</strong>
            <em>{{ aiStickyCommand.meta }}</em>
          </span>
          <b>{{ aiStickyCommand.cta }}</b>
          <span
            class="ai-sticky-command-confidence"
            :class="`tone-${aiStickyCommandConfidence.tone}`"
            :style="{ '--sticky-confidence': `${aiStickyCommandConfidence.score}%` }"
            aria-label="建议可信度"
          >
            <small>可信度</small>
            <strong>{{ aiStickyCommandConfidence.score }}%</strong>
            <em>{{ aiStickyCommandConfidence.label }} · {{ aiStickyCommandConfidence.meta }}</em>
          </span>
          <span class="ai-sticky-command-preview" aria-label="操作预览">
            <small>操作预览</small>
            <em v-for="chip in aiStickyCommandChips" :key="chip.key" :class="`tone-${chip.tone}`">
              <SfShellIcon :name="chip.icon" />
              <span>{{ chip.label }}</span>
              <strong>{{ chip.value }}</strong>
            </em>
          </span>
          <span class="ai-sticky-command-reason" aria-label="建议依据">
            <small>建议依据</small>
            <em v-for="reason in aiStickyCommandReasons" :key="reason.key" :class="`tone-${reason.tone}`">
              <SfShellIcon :name="reason.icon" />
              <span>{{ reason.label }}</span>
              <strong>{{ reason.value }}</strong>
            </em>
          </span>
        </button>
      </aside>

      <main class="learning-content">

    <section id="panel-pulse-chain" class="pulse-chain-board ai-card" aria-label="五段脉动">
      <div class="pulse-chain-head">
        <div>
          <span class="ai-pill accent">默认流程</span>
          <h2>原始数据、数据清洗、训练、模型测试、部署输出</h2>
          <p>框架先展示，真实数据加载后自动填充；点击任一模块或明细可查看详细数据。</p>
        </div>
        <div class="pulse-chain-meta">
          <span class="pulse-live-status" :class="{ 'is-loading': pulseLoading || pulseRefreshing, 'is-error': pulseError }">{{ pulseGeneratedText }}</span>
          <button class="ai-btn" type="button" @click="pulseLayoutMode = 'full'">
            <SfShellIcon name="flow" />详细视图
          </button>
        </div>
      </div>
      <div class="pulse-chain-steps">
        <article
          v-for="(module, index) in pulseModules"
          :key="module.key"
          class="pulse-chain-card"
          :class="[`tone-${module.tone || 'info'}`, designToneClass(module.tone), { 'is-loading': pulseLoading && module.loading }]"
          :style="{ '--pulse-chain-delay': `${index * 0.08}s` }"
        >
	          <button type="button" class="pulse-chain-main" @click="openPulseModule(module)">
	            <span class="pulse-chain-index">{{ index + 1 }}</span>
	            <span class="pulse-chain-copy">
	              <small>{{ pulseModuleStatusText(module) }}</small>
	              <strong>{{ module.title }}</strong>
	              <em>{{ module.subtitle }}</em>
	              <b class="pulse-chain-count">{{ pulseModuleCountText(module) }}</b>
	            </span>
	            <i><SfShellIcon :name="pulseModuleIcon(module.key)" /></i>
	          </button>
          <div class="pulse-chain-metrics">
            <button
              v-for="metric in pulseModuleMetrics(module)"
              :key="`${module.key}-${metric.key}`"
              type="button"
              :class="{ 'is-loading': module.loading }"
              @click="openPulseMetric(module, metric)"
            >
              <em>{{ metric.label }}</em>
              <strong>{{ pulseMetricValue(metric) }}</strong>
            </button>
          </div>
          <div class="pulse-chain-items">
            <button
              v-for="item in pulseModuleItems(module)"
              :key="`${module.key}-${item.id}`"
              type="button"
              :class="{ 'is-loading': Boolean(item.payload?.loading) }"
              @click="openPulseItem(module, item)"
            >
              <span :title="pulseItemTitle(item)">{{ pulseItemTitle(item) }}</span>
              <em :title="pulseItemMeta(item)">{{ pulseItemMeta(item) }}</em>
            </button>
            <span v-if="pulseError && module.loading" class="pulse-chain-empty error">数据暂未返回，保留流程框架</span>
            <span v-else-if="!(module.items || []).length && !module.loading" class="pulse-chain-empty">暂无明细数据</span>
          </div>
        </article>
      </div>
    </section>

    <section id="panel-pulse-overview" class="pulse-overview-board ai-card" aria-label="脉动概览">
      <div class="pulse-overview-head">
        <div>
          <span class="ai-pill accent">概览</span>
          <h2>{{ pulseHealth.title }}</h2>
          <p>{{ pulseHealth.detail }}；默认展示当前结论、建议操作和必要路径；需要查看明细时再切换到详细视图。</p>
        </div>
        <div class="pulse-overview-actions">
          <button class="ai-btn primary" type="button" :disabled="aiStickyCommandDisabled" @click="runAiStickyCommand">
            <SfShellIcon :name="aiStickyCommand.icon" />{{ aiStickyCommand.cta }}
          </button>
          <button class="ai-btn" type="button" @click="pulseLayoutMode = pulseLayoutMode === 'simple' ? 'full' : 'simple'">
            <SfShellIcon :name="pulseLayoutMode === 'simple' ? 'flow' : 'eye'" />{{ pulseLayoutMode === 'simple' ? '查看详细视图' : '返回概览' }}
          </button>
        </div>
      </div>
      <div class="pulse-decision-strip" :class="[`tone-${aiStickyCommand.tone}`, designToneClass(aiStickyCommand.tone)]" aria-label="当前建议摘要">
        <button
          v-for="item in pulseDecisionItems"
          :key="item.key"
          type="button"
          class="pulse-decision-item"
          :class="[`tone-${item.tone}`, designToneClass(item.tone)]"
          @click="openPulseDecisionItem(item)"
        >
          <i><SfShellIcon :name="item.icon" /></i>
          <span>
            <strong>{{ item.label }}</strong>
            <em>{{ item.meta }}</em>
          </span>
          <b>{{ item.value }}</b>
        </button>
      </div>
      <section
        class="pulse-automation-strip"
        :class="designToneClass(pulseAutomationTone)"
        aria-label="自动化运行态"
      >
        <div class="pulse-automation-head">
          <span>自动化运行态</span>
          <em>{{ pulseAutomationSummary }}</em>
        </div>
        <div class="pulse-automation-grid">
          <button
            v-for="card in pulseAutomationCards"
            :key="card.key"
            type="button"
            class="pulse-automation-card"
            :class="[`tone-${card.tone}`, designToneClass(card.tone)]"
            :style="{ '--automation-card-level': `${card.level}%` }"
            @click="openPulseDecisionItem(card)"
          >
            <i><SfShellIcon :name="card.icon" /></i>
            <span>
              <strong>{{ card.label }}</strong>
              <em>{{ card.meta }}</em>
            </span>
            <b>{{ card.value }}</b>
          </button>
        </div>
      </section>
      <section class="pulse-value-panel" aria-label="关键有效信息">
        <div class="pulse-value-head">
          <span>关键有效信息</span>
          <em>{{ pulseValueSummary }}</em>
        </div>
        <div class="pulse-overview-grid">
          <button
            v-for="card in pulseOverviewCards"
            :key="card.key"
            type="button"
            class="pulse-overview-card"
            :class="[`tone-${card.tone}`, designToneClass(card.tone)]"
            @click="openPulseOverviewCard(card)"
          >
            <i><SfShellIcon :name="card.icon" /></i>
            <span>
              <small>价值评估</small>
              <strong>{{ card.label }}</strong>
              <em>{{ card.meta }}</em>
              <u>{{ card.reason }}</u>
            </span>
            <b>{{ card.value }}</b>
          </button>
        </div>
      </section>
      <div class="pulse-overview-path" aria-label="简要处理路径">
        <button
          v-for="(step, index) in pulseOverviewPath"
          :key="step.key"
          type="button"
          class="pulse-overview-step"
          :class="[`tone-${step.tone}`, designToneClass(step.tone), { active: step.key === aiMissionActiveStep }]"
          :style="{ '--overview-step-delay': `${index * 0.13}s`, '--overview-step-progress': `${step.progress}%` }"
          @click="openAiMissionStep(step)"
        >
          <i><SfShellIcon :name="step.icon" /></i>
          <span>{{ step.label }}</span>
          <strong>{{ step.value }}</strong>
        </button>
      </div>
    </section>

    <section v-show="pulseLayoutMode === 'full'" id="panel-global-map" class="global-flow-board ai-card" aria-label="全局处理视图">
      <div class="card-title board-title">
        <div>
          <h2>全局处理视图</h2>
          <p>先查看系统处理路径：节点代表业务模块，连线代表实际数据路径；每条连线都标出新增和更新，点击节点可查看详情。</p>
        </div>
        <div class="flow-live">
          <span></span>
          有效处理
        </div>
      </div>
      <div class="motion-key light">
        <span><i class="key-dot"></i>动态标记=存在真实记录</span>
        <span><b>+N</b>=本期新增</span>
        <span>更新=N=已生成、引用、审核流程或改进建议变化</span>
      </div>
      <div class="flow-lens-strip" aria-label="快速筛选">
        <span class="flow-lens-label">快速筛选</span>
        <button
          v-for="(focus, index) in quickFocusFilters"
          :key="focus.key"
          type="button"
          class="flow-lens-node"
          :class="[`tone-${focus.tone}`, { active: isQuickFocusActive(focus) }]"
          :style="lensNodeStyle(index)"
          @click="applyQuickFilter(focus)"
        >
          <i><SfShellIcon :name="focus.icon" /></i>
          <strong>{{ focus.label }}</strong>
          <em>{{ focus.meta }}</em>
          <b v-if="isQuickFocusActive(focus)">正在看</b>
        </button>
        <button v-if="activeFilterChips.length" class="flow-lens-clear" type="button" @click="resetFilters()">
          清空聚焦
        </button>
      </div>
      <div v-if="activeFilterChips.length" class="flow-lens-active">
        <span v-for="chip in activeFilterChips" :key="chip.key">{{ chip.label }}={{ chip.value }}</span>
      </div>
      <div class="global-flow-layout">
        <div class="global-flow-canvas">
          <svg class="global-flow-svg" viewBox="0 0 920 520" aria-hidden="true">
            <defs>
              <linearGradient id="global-flow-gradient" x1="90" y1="260" x2="760" y2="260" gradientUnits="userSpaceOnUse">
                <stop offset="0%" stop-color="#2563eb" />
                <stop offset="45%" stop-color="#14b8a6" />
                <stop offset="100%" stop-color="#f59e0b" />
              </linearGradient>
            </defs>
            <path
              v-for="edge in globalFlowEdges"
              :key="`edge-track-${edge.key}`"
              class="global-edge-track"
              :class="{ active: activeGlobalEdge?.key === edge.key }"
              :d="edge.path"
            />
            <path
              v-for="edge in globalFlowEdges"
              :key="`edge-energy-${edge.key}`"
              class="global-edge-energy"
              :class="[`tone-${edge.tone}`, { active: activeGlobalEdge?.key === edge.key }]"
              :d="edge.path"
            />
            <circle
              v-for="edge in globalFlowEdges.filter((item) => item.count > 0).slice(0, 5)"
              :key="`edge-dot-${edge.key}`"
              class="global-edge-dot"
              r="5"
            >
              <animateMotion :dur="globalFlowDotDuration" repeatCount="indefinite" :begin="`${globalFlowEdges.indexOf(edge) * 0.55}s`" :path="edge.path" />
            </circle>
          </svg>

          <button
            type="button"
            class="global-flow-core"
            :class="`health-${pulseHealth.tone}`"
            @click="showPulseCore"
          >
            <span>整体状态</span>
            <strong>{{ pulseHealth.main }}</strong>
            <em>{{ pulseHealth.detail }}</em>
            <i>{{ numberText(summary.events?.total || events.length) }} 记录 · {{ numberText(bottlenecks.summary?.total) }} 待处理事项</i>
          </button>

          <button
            v-for="edge in globalFlowEdges"
            :key="`edge-label-${edge.key}`"
            type="button"
            class="global-edge-label"
            :class="[`tone-${edge.tone}`, { active: activeGlobalEdge?.key === edge.key }]"
            :style="globalEdgeLabelStyle(edge)"
            @click="openGlobalFlowEdge(edge)"
          >
            {{ edge.label }} · +{{ numberText(edge.newCount) }} / 更新 {{ numberText(edge.updateCount) }}
          </button>

          <button
            v-for="(node, index) in globalFlowNodes"
            :key="node.key"
            type="button"
            class="global-flow-node"
            :class="[`tone-${node.tone}`, { active: activeGlobalNode?.key === node.key, 'has-flow': node.total || node.newCount || node.updateCount }]"
            :style="globalNodeStyle(node, index)"
            @click="openGlobalFlowNode(node)"
          >
            <span class="global-node-icon"><SfShellIcon :name="node.icon" /></span>
            <span class="global-node-copy">
              <strong>{{ node.label }}</strong>
              <em>{{ node.description }}</em>
            </span>
            <span class="global-node-metrics">
              <b>{{ numberText(node.total) }}</b>
              <i>新增 {{ numberText(node.newCount) }} · 更新 {{ numberText(node.updateCount) }}</i>
            </span>
            <span class="global-node-meter" :style="globalNodeMeterStyle(node)"><i></i></span>
          </button>
        </div>

        <aside v-if="activeGlobalNode" class="global-node-detail">
          <span class="ai-pill info">节点详情</span>
          <h3>{{ activeGlobalNode.label }}</h3>
          <p>{{ activeGlobalNode.description }}</p>
          <div class="global-detail-narrative">
            <span></span>
            <strong>{{ globalDetailNarrative }}</strong>
          </div>
          <div v-if="activeGlobalEdge" class="global-edge-focus">
            <span>正在查看路径</span>
            <strong>{{ globalNodeLabel(activeGlobalEdge.from) }} → {{ globalNodeLabel(activeGlobalEdge.to) }}</strong>
            <em>{{ activeGlobalEdge.label }} · +{{ numberText(activeGlobalEdge.newCount) }} / 更新 {{ numberText(activeGlobalEdge.updateCount) }}</em>
            <div>
              <button type="button" @click="openGlobalFlowNodeByKey(activeGlobalEdge.from)">看上游</button>
              <button type="button" @click="openGlobalFlowNodeByKey(activeGlobalEdge.to)">看下游</button>
            </div>
          </div>
          <div class="global-detail-metrics">
            <button
              v-for="metric in globalDetailMetrics"
              :key="metric.key"
              type="button"
              class="global-detail-metric"
              :class="`tone-${metric.tone}`"
              @click="openGlobalDetailMetric(metric)"
            >
              <i></i>
              <em>{{ metric.label }}</em>
              <strong>{{ metric.value }}</strong>
              <span>{{ metric.meta }}</span>
            </button>
          </div>
          <div class="global-detail-flow">
            <strong>流入</strong>
            <button v-for="edge in activeGlobalInbound" :key="`in-${edge.key}`" type="button" @click="openGlobalFlowEdge(edge)">
              {{ edge.label }} · {{ globalNodeLabel(edge.from) }} → {{ globalNodeLabel(edge.to) }} · +{{ numberText(edge.newCount) }}
            </button>
            <em v-if="!activeGlobalInbound.length">这是当前入口</em>
            <strong>流出</strong>
            <button v-for="edge in activeGlobalOutbound" :key="`out-${edge.key}`" type="button" @click="openGlobalFlowEdge(edge)">
              {{ edge.label }} · {{ globalNodeLabel(edge.from) }} → {{ globalNodeLabel(edge.to) }} · 更新 {{ numberText(edge.updateCount) }}
            </button>
          </div>
          <div class="global-detail-changes">
            <section>
              <strong>新增明细</strong>
              <button
                v-for="item in activeGlobalAdditions"
                :key="item.key"
                type="button"
                :class="`tone-${item.tone}`"
                @click="setSelected({ type: 'global_node_addition', data: item.data })"
              >
                <span>{{ item.title }}</span>
                <em>{{ item.meta }}</em>
              </button>
              <em v-if="!activeGlobalAdditions.length">暂无新增明细</em>
            </section>
            <section>
              <strong>更新明细</strong>
              <button
                v-for="item in activeGlobalUpdates"
                :key="item.key"
                type="button"
                :class="`tone-${item.tone}`"
                @click="setSelected({ type: 'global_node_update', data: item.data })"
              >
                <span>{{ item.title }}</span>
                <em>{{ item.meta }}</em>
              </button>
              <em v-if="!activeGlobalUpdates.length">暂无更新明细</em>
            </section>
          </div>
          <button class="ai-btn primary" type="button" @click="focusGlobalFlowNode(activeGlobalNode)">
            <SfShellIcon name="search" />聚焦这个节点
          </button>
        </aside>
      </div>
    </section>

    <section id="panel-run-brief" class="run-brief ai-card" aria-label="系统运行评估">
      <div class="run-brief-head">
        <div>
          <span class="ai-pill accent">运行评估</span>
          <h2>系统处理路径：从状态到操作</h2>
          <p>从左到右是当前处理顺序；动画只标记可执行路径，点击任一动态标记可聚焦对应数据。</p>
        </div>
        <button class="ai-btn" type="button" :disabled="loading" @click="reloadFromUi">
          <SfShellIcon name="refresh" />重新评估
        </button>
      </div>
      <div class="run-brief-flow">
        <span class="run-brief-rail"></span>
        <span
          v-for="packet in 2"
          :key="`brief-packet-${packet}`"
          class="run-brief-packet"
          :style="{ '--brief-packet-delay': `${packet * 0.86}s` }"
        ></span>
        <button
          v-for="(item, index) in systemBriefItems"
          :key="item.key"
          type="button"
          class="run-brief-node"
          :class="`tone-${item.tone}`"
          :style="briefNodeStyle(index)"
          @click="openBriefItem(item)"
        >
          <span class="brief-step">{{ index + 1 }}</span>
          <span class="brief-icon"><SfShellIcon :name="item.icon" /></span>
          <span class="brief-copy">
            <em>{{ item.label }}</em>
            <strong>{{ item.value }}</strong>
            <i>{{ item.meta }}</i>
          </span>
          <b>{{ item.action }}</b>
        </button>
      </div>
    </section>

    <section id="panel-diagnostics" class="diagnostic-switch ai-card">
      <div>
        <span class="ai-pill info">详细面板</span>
        <strong>默认先看概览；需要排查具体来源、记录和血缘时再展开详细面板。</strong>
      </div>
      <button class="ai-btn" type="button" @click="showDiagnosticSections = !showDiagnosticSections">
        <SfShellIcon name="flow" />{{ showDiagnosticSections ? '收起细节面板' : '展开细节面板' }}
      </button>
    </section>

    <div v-show="showDiagnosticSections" class="diagnostic-sections">

    <section id="panel-filters" class="learning-toolbar filter-dock ai-card">
      <div class="filter-dock-head">
        <div>
          <span class="ai-pill info">筛选条件</span>
          <strong>按业务记录筛选</strong>
          <em>{{ activeFilterChips.length ? `${activeFilterChips.length} 个筛选正在生效` : '默认查看全部系统数据' }}</em>
        </div>
        <div class="filter-dock-actions">
          <button class="filter-toggle" type="button" @click="filtersExpanded = !filtersExpanded">
            {{ filtersExpanded ? '收起高级筛选' : '展开高级筛选' }}
          </button>
          <button v-if="activeFilterChips.length" class="filter-toggle ghost" type="button" @click="resetFilters()">
            清空聚焦
          </button>
        </div>
      </div>

      <div class="quick-filter-flow" aria-label="快速聚焦记录">
        <button
          v-for="focus in quickFocusFilters"
          :key="focus.key"
          type="button"
          class="quick-filter-node"
          :class="`tone-${focus.tone}`"
          @click="applyQuickFilter(focus)"
        >
          <span><SfShellIcon :name="focus.icon" /></span>
          <strong>{{ focus.label }}</strong>
          <em>{{ focus.meta }}</em>
        </button>
      </div>

      <div v-if="activeFilterChips.length" class="active-filter-pulse">
        <span v-for="chip in activeFilterChips" :key="chip.key">{{ chip.label }}={{ chip.value }}</span>
      </div>

      <div v-show="filtersExpanded" class="advanced-filter-grid">
      <label>
        <span>时间</span>
        <a-select v-model="filters.days" size="small" @change="reloadAll">
          <a-option :value="7">近 7 天</a-option>
          <a-option :value="30">近 30 天</a-option>
          <a-option :value="90">近 90 天</a-option>
          <a-option :value="180">近 180 天</a-option>
        </a-select>
      </label>
      <label>
        <span>Skill</span>
        <a-input v-model="filters.skill_id" size="small" allow-clear placeholder="按 Skill ID 过滤" @press-enter="reloadAll" />
      </label>
      <label>
        <span>部门</span>
        <a-input v-model="filters.department" size="small" allow-clear placeholder="全局账号可按部门过滤" @press-enter="reloadAll" />
      </label>
      <label>
        <span>SF 工具</span>
        <a-input v-model="filters.sf_tool" size="small" allow-clear placeholder="按 MCP / SF 工具过滤" @press-enter="reloadAll" />
      </label>
      <label>
        <span>来源</span>
        <a-select v-model="filters.source_type" size="small" allow-clear @change="reloadAll">
          <a-option value="execution_run">Skill 运行</a-option>
          <a-option value="execution_artifact">执行数据资产</a-option>
          <a-option value="decision_log">决策反馈</a-option>
          <a-option value="sf_mcp_call">SF 调用</a-option>
          <a-option value="agent_thread">Agent 会话</a-option>
          <a-option value="knowledge_query">知识检索</a-option>
          <a-option value="training_job">训练任务</a-option>
          <a-option value="model_deployment">模型部署</a-option>
          <a-option value="todo_dispatch_task">派发待办</a-option>
        </a-select>
      </label>
      <label>
        <span>事件</span>
        <a-select v-model="filters.event_type" size="small" allow-clear @change="reloadAll">
          <a-option value="skill.run.completed">Skill 完成</a-option>
          <a-option value="skill.run.failed">Skill 失败</a-option>
          <a-option value="decision.created">决策生成</a-option>
          <a-option value="decision.feedback">用户反馈</a-option>
          <a-option value="sf.mcp.called">SF 调用</a-option>
          <a-option value="agent.thread.completed">Agent 完成</a-option>
          <a-option value="knowledge.query.used">知识引用</a-option>
          <a-option value="training.job.completed">训练完成</a-option>
          <a-option value="model.deployed">模型部署</a-option>
        </a-select>
      </label>
      <label>
        <span>资产类型</span>
        <a-select v-model="filters.artifact_kind" size="small" allow-clear @change="reloadAll">
          <a-option value="knowledge_note">知识片段</a-option>
          <a-option value="report_summary">报告摘要</a-option>
          <a-option value="training_sample">训练样本</a-option>
          <a-option value="eval_case">评测样本</a-option>
          <a-option value="agent_memory">Agent 记忆</a-option>
          <a-option value="skill_improvement_candidate">Skill 建议</a-option>
          <a-option value="sf_iteration_candidate">SF 建议</a-option>
          <a-option value="agent_policy_candidate">Agent 建议</a-option>
          <a-option value="ai_system_gap_candidate">AI 系统缺口</a-option>
          <a-option value="knowledge_review_candidate">知识缺口</a-option>
          <a-option value="training_improvement_candidate">训练建议</a-option>
        </a-select>
      </label>
      <label>
        <span>建议类型</span>
        <a-select v-model="filters.target_type" size="small" allow-clear @change="reloadAll">
          <a-option value="skill">Skill</a-option>
          <a-option value="sf">SF</a-option>
          <a-option value="agent">Agent</a-option>
          <a-option value="ai_system">AI 系统</a-option>
          <a-option value="knowledge">知识库</a-option>
          <a-option value="training">训练</a-option>
        </a-select>
      </label>
      <label>
        <span>关键词</span>
        <a-input-search v-model="filters.q" size="small" allow-clear placeholder="事件 / 资产 / 建议" @search="reloadAll" @press-enter="reloadAll" />
      </label>
      <label>
        <span>资产状态</span>
        <a-select v-model="filters.artifact_status" size="small" allow-clear @change="reloadArtifacts">
          <a-option value="ready">待处理</a-option>
          <a-option value="needs_review">待审核</a-option>
          <a-option value="materialized">已生成</a-option>
          <a-option value="ignored">已忽略</a-option>
        </a-select>
      </label>
      <label>
        <span>建议状态</span>
        <a-select v-model="filters.candidate_status" size="small" allow-clear @change="reloadAll">
          <a-option value="open">开放</a-option>
          <a-option value="reviewing">评审中</a-option>
          <a-option value="accepted">已采纳</a-option>
          <a-option value="rejected">已驳回</a-option>
          <a-option value="implemented">已落地</a-option>
        </a-select>
      </label>
      <label>
        <span>Run ID</span>
        <a-input v-model="filters.run_id" size="small" allow-clear placeholder="按运行链路查看" @press-enter="reloadAll" />
      </label>
      </div>
    </section>

    <section class="learning-kpis" aria-label="学习指标">
      <article v-for="item in kpis" :key="item.key" class="learning-kpi">
        <span>{{ item.label }}</span>
        <strong>{{ item.value }}</strong>
        <em>{{ item.meta }}</em>
      </article>
    </section>



    <section id="panel-automation" class="automation-panel ai-card">
      <div>
        <span class="ai-pill" :class="automation.automation?.enabled ? 'accent' : 'info'">自动处理引擎</span>
        <h2>{{ automationTitle }}</h2>
        <p>系统按规则同步最新记录，并生成知识、样本、记忆和改进建议；需要审核的内容只进入审核队列，不会自动发布或变更上线状态。</p>
      </div>
      <div class="automation-service-grid" aria-label="自动处理服务概况">
        <button
          v-for="metric in automationServiceCards"
          :key="metric.key"
          type="button"
          class="automation-service-card"
          :class="`tone-${metric.tone}`"
          @click="setSelected({ type: 'automation_service', data: metric.data })"
        >
          <span><SfShellIcon :name="metric.icon" /></span>
          <em>{{ metric.label }}</em>
          <strong>{{ metric.value }}</strong>
          <i>{{ metric.meta }}</i>
        </button>
      </div>
      <div class="automation-stats">
        <button
          v-for="metric in automationMetrics"
          :key="metric.key"
          type="button"
          class="automation-signal"
          :class="`tone-${metric.tone}`"
          @click="setSelected({ type: 'automation_metric', data: metric.data })"
        >
          <span><SfShellIcon :name="metric.icon" /></span>
          <em>{{ metric.label }}</em>
          <strong>{{ metric.value }}</strong>
          <i>{{ metric.meta }}</i>
        </button>
      </div>
      <div class="automation-runs">
        <strong>最近自动处理</strong>
        <button
          v-for="run in automationRuns"
          :key="String(run.id || run.started_at)"
          type="button"
          class="automation-run"
          @click="setSelected({ type: 'automation_run', data: run as Record<string, unknown> })"
        >
          <span :class="statusClass(run.status)">{{ statusText(run.status) }}</span>
          <em>{{ run.trigger_type || '-' }} · {{ formatTime(run.finished_at || run.started_at) }}</em>
          <b>{{ numberText(run.result?.materialized as number) }} 生成知识</b>
        </button>
      </div>
    </section>

    <section class="learning-manifest ai-card">
      <div>
        <span class="ai-pill info">训练样本清单</span>
        <h2>受控样本清单</h2>
        <p>训练只引用样本血缘和质量指标，不在页面返回原始业务数据明文。</p>
      </div>
      <div class="manifest-beam">
        <button class="manifest-core" type="button" @click="showManifest">
          <span>样本池</span>
          <strong>+{{ numberText(manifest.sample_total) }}</strong>
          <em>{{ manifest.skills?.length || 0 }} 个 Skill</em>
        </button>
        <div class="manifest-stream" aria-label="训练样本流向">
          <span class="manifest-track"></span>
          <span class="manifest-energy"></span>
          <button
            v-for="(skill, index) in (manifest.skills || []).slice(0, 4)"
            :key="String(skill.skill_id || index)"
            type="button"
            class="manifest-skill"
            :style="manifestSkillStyle(index, Math.min(4, manifest.skills?.length || 0))"
            @click="setSelected({ type: 'manifest_skill', data: skill as unknown as Record<string, unknown> })"
          >
            <strong>+{{ numberText(skill.sample_count) }}</strong>
            <em>{{ skill.skill_id || 'Skill' }}</em>
          </button>
        </div>
        <span class="manifest-ref">{{ manifest.dataset_ref || 'learning-artifacts://visible/training/latest' }}</span>
      </div>
      <button class="ai-btn" type="button" @click="showManifest">
        <SfShellIcon name="database" />查看清单
      </button>
    </section>

    <section id="panel-bottleneck" class="bottleneck-board ai-card">
      <div class="card-title">
        <div>
          <h2>待处理问题</h2>
          <p>自动识别待生成知识、待审核建议、失败任务和未提取事件；红 / 橙标记越靠外，越需要优先处理。</p>
        </div>
        <span class="mini-stat">{{ numberText(bottlenecks.summary?.total) }} 个待处理事项</span>
      </div>
      <div class="motion-key light">
        <span><i class="key-dot warn"></i>扫描=定时检测阻塞</span>
        <span><b>+N</b>=该类待处理事项数量</span>
        <span>操作按钮只处理该类待办中的首条真实记录</span>
      </div>
      <div class="bottleneck-radar" :class="{ empty: !bottleneckSections.length }">
        <svg class="bottleneck-radar-svg" viewBox="0 0 720 360" aria-hidden="true">
          <circle class="radar-ring ring-1" cx="360" cy="180" r="72" />
          <circle class="radar-ring ring-2" cx="360" cy="180" r="124" />
          <circle class="radar-ring ring-3" cx="360" cy="180" r="164" />
          <line class="radar-axis" x1="360" y1="24" x2="360" y2="336" />
          <line class="radar-axis" x1="86" y1="180" x2="634" y2="180" />
          <path class="radar-sweep" d="M360 180 L360 32 A148 148 0 0 1 494 116 Z" />
        </svg>

        <button type="button" class="bottleneck-core" @click="setSelected({ type: 'bottlenecks', data: bottlenecks as unknown as Record<string, unknown> })">
          <span>待处理总量</span>
          <strong>+{{ numberText(bottlenecks.summary?.total) }}</strong>
          <em>{{ bottlenecks.summary?.total ? '点击查看全部阻塞' : '当前正常' }}</em>
        </button>

        <article
          v-for="(section, index) in bottleneckSections"
          :key="section.key"
          class="bottleneck-radar-node"
          :class="`severity-${section.severity || 'ok'}`"
          :style="bottleneckNodeStyle(index, bottleneckSections.length)"
        >
          <button type="button" class="bottleneck-beacon" @click="openBottleneckSection(section)">
            <span>{{ section.title || section.key }}</span>
            <strong>+{{ numberText(section.count) }}</strong>
            <em>{{ section.items?.length ? bottleneckTitle(section.key, section.items[0]) : '当前无阻塞' }}</em>
            <i>{{ section.items?.length ? bottleneckMeta(section.key, section.items[0]) : '无阻塞' }}</i>
          </button>
          <div v-if="section.items?.length" class="bottleneck-quick-actions">
            <button
              v-if="['ready_artifacts', 'review_artifacts'].includes(section.key)"
              type="button"
              class="bottleneck-action"
              @click.stop="materializeBottleneck(section.items[0])"
            >
              生成知识
            </button>
            <button
              v-if="section.key === 'open_candidates'"
              type="button"
              class="bottleneck-action"
              @click.stop="createReviewBottleneck(section.items[0])"
            >
              进待办
            </button>
            <button
              v-if="section.key === 'failed_ingestions'"
              type="button"
              class="bottleneck-action"
              @click.stop="retryIngestionBottleneck(section.items[0])"
            >
              重试
            </button>
            <button
              v-if="section.key === 'stalled_events'"
              type="button"
              class="bottleneck-action"
              @click.stop="backfill(false)"
            >
              重新同步
            </button>
          </div>
        </article>
        <div v-if="!bottleneckSections.length" class="bottleneck-empty">暂无待处理问题，系统运行正常</div>
      </div>
    </section>

    <section id="panel-flow-stage" class="flow-stage-board ai-card">
      <div class="card-title board-title">
        <div>
          <h2>数据处理流程</h2>
          <p>从数据源、标准记录、学习资产到知识库、训练、Agent 和改进建议，按真实血缘持续处理。</p>
        </div>
        <div class="flow-live">
          <span></span>
          实时处理
        </div>
      </div>
      <div class="motion-key light">
        <span><i class="key-dot"></i>轨道方向=来源 → 事件 → 资产 → 生成知识 → 建议 → 审核流程</span>
        <span><b>+N</b>=阶段 / 连接真实数量</span>
        <span>只让数据点移动，阶段卡保持稳定可读</span>
      </div>
      <div class="flow-canvas" :class="{ empty: !hasFlowData }">
        <svg class="flow-loop-svg" viewBox="0 0 1160 420" aria-hidden="true">
          <defs>
            <linearGradient id="flow-loop-gradient" x1="90" y1="210" x2="1070" y2="210" gradientUnits="userSpaceOnUse">
              <stop offset="0%" stop-color="#60a5fa" />
              <stop offset="35%" stop-color="#22d3ee" />
              <stop offset="68%" stop-color="#34d399" />
              <stop offset="100%" stop-color="#f59e0b" />
            </linearGradient>
          </defs>
          <path class="flow-loop-track" :d="flowLoopPath" />
          <path class="flow-loop-energy" :d="flowLoopPath" />
          <circle
            v-for="packet in 2"
            :key="`flow-loop-${packet}`"
            class="flow-loop-packet"
            r="5"
          >
            <animateMotion :dur="flowLoopDuration" repeatCount="indefinite" :begin="`${packet * 0.9}s`" :path="flowLoopPath" />
          </circle>
          <text
            v-for="signal in flowLoopSignals"
            :key="`flow-signal-${signal.key}`"
            class="flow-loop-count"
            :class="`tone-${signal.tone}`"
          >
            +{{ numberText(signal.count) }}
            <animateMotion :dur="flowLoopDuration" repeatCount="indefinite" :begin="signal.begin" :path="flowLoopPath" />
          </text>
        </svg>

        <button
          v-for="(_, index) in flowStages.slice(0, Math.max(0, flowStages.length - 1))"
          :key="`connector-label-${index}`"
          type="button"
          class="flow-loop-label"
          :style="flowConnectorLabelStyle(index, Math.max(1, flowStages.length - 1))"
          @click="openFlowConnector(index)"
        >
          {{ connectorLabel(index) }} · {{ numberText(connectorCount(index)) }}
        </button>

        <article
          v-for="(stage, index) in flowStages"
          :key="stage.key"
          class="flow-stage"
          :class="`tone-${stage.tone}`"
          :style="flowStageStyle(index, flowStages.length)"
        >
            <div class="flow-stage-head">
              <span class="stage-index">{{ index + 1 }}</span>
              <div>
                <h3>{{ stage.title }}</h3>
                <p>{{ stage.caption }}</p>
              </div>
              <strong>+{{ numberText(stage.count) }}</strong>
            </div>
            <div class="flow-node-list">
              <button
                v-for="node in stage.nodes.slice(0, 2)"
                :key="node.id"
                type="button"
                class="flow-node"
                :class="[`node-${node.tone || stage.tone}`, { active: selectedNodeId === node.id }]"
                @click="selectFlowNode(node)"
              >
                <span class="node-icon"><SfShellIcon :name="node.icon || 'flow'" /></span>
                <span class="node-main">
                  <strong>{{ node.label }}</strong>
                  <em>{{ node.meta }}</em>
                </span>
                <span class="node-heat" :style="{ width: `${Math.max(18, Math.min(100, Math.round((node.heat || 0.45) * 100)))}%` }"></span>
              </button>
              <button
                v-if="stage.nodes.length > 2"
                type="button"
                class="flow-stage-more"
                @click="openFlowStage(stage)"
              >
                +{{ numberText(stage.nodes.length - 2) }} 个节点
              </button>
              <div v-if="!stage.nodes.length" class="flow-node empty-node">暂无数据</div>
            </div>
          </article>
        <div v-if="!flowStages.length" class="flow-loop-empty">暂无处理阶段，等待记录同步</div>
      </div>
      <div class="flow-legend">
        <span><i class="legend-dot source"></i> 来源</span>
        <span><i class="legend-dot event"></i> 标准记录</span>
        <span><i class="legend-dot artifact"></i> 学习资产</span>
        <span><i class="legend-dot sink"></i> 自动生成知识</span>
        <span><i class="legend-dot candidate"></i> 迭代建议</span>
      </div>
    </section>

    <section id="panel-journey" class="journey-board ai-card">
      <div class="card-title board-title">
        <div>
          <h2>处理记录</h2>
          <p>按单条来源记录展示“来源 → 事件 → 资产 → 生成知识 → 建议 → 审核流程”的真实处理路径。</p>
        </div>
        <div class="journey-stats">
          <span>完成 {{ numberText(journeyFlow.state_counts?.completed) }}</span>
          <span>处理中 {{ numberText(journeyFlow.state_counts?.flowing) }}</span>
          <span>阻塞 {{ numberText((journeyFlow.state_counts?.blocked || 0) + (journeyFlow.state_counts?.failed || 0)) }}</span>
        </div>
      </div>
      <div class="motion-key light">
        <span><i class="key-dot"></i>进度光带=这条事实已走到哪一步</span>
        <span><b>当前位置</b>=当前停留位置</span>
        <span>节点可点开原始链路详情和血缘</span>
      </div>
      <div v-if="journeys.length" class="journey-flow-map">
        <article v-for="journey in journeys" :key="journey.id" class="journey-lane" :class="`state-${journey.state || 'flowing'}`">
          <button type="button" class="journey-summary" @click="setSelected({ type: 'flow_journey', data: journey as unknown as Record<string, unknown> })">
            <span class="journey-state" :class="statusClass(journey.state)">{{ flowStateText(journey.state) }}</span>
            <span class="journey-summary-copy">
              <strong>{{ journey.title || journey.event_id }}</strong>
              <em>{{ sourceTypeText(journey.source_type) }} · {{ journey.skill_id || journey.run_id || journey.source_id || '-' }} · {{ formatTime(journey.last_at) }}</em>
            </span>
            <b>{{ journeyProgressPercent(journey) }}%</b>
          </button>
          <div class="journey-rail" :style="journeyRailStyle(journey)">
            <span class="journey-rail-base"></span>
            <span class="journey-rail-energy"></span>
            <span class="journey-cursor"></span>
            <button
              v-for="(step, index) in journey.steps || []"
              :key="`${journey.id}-${index}-${step.stage}`"
              type="button"
              class="journey-step-node"
              :class="[`step-${step.stage}`, statusClass(step.status)]"
              :style="journeyStepStyle(index, journey.steps?.length || 0)"
              @click="setSelected({ type: 'flow_step', data: step as unknown as Record<string, unknown> })"
            >
              <span>{{ journeyStageText(step.stage) }}</span>
              <strong>{{ step.label }}</strong>
              <em>{{ step.meta || step.status_text || step.status }}</em>
            </button>
          </div>
          <div v-if="journey.blockers?.length" class="journey-blockers">
            <span v-for="blocker in journey.blockers" :key="blocker">{{ blockerText(blocker) }}</span>
          </div>
        </article>
      </div>
      <a-empty v-else description="暂无链路；点击同步最新记录或等待自动处理引擎产生数据" />
    </section>

    <section id="panel-river" class="operation-stream ai-card">
      <div class="card-title board-title">
        <div>
          <h2>运行记录</h2>
          <p>将事件、血缘、资产和改进建议集中到可点击的记录视图；动态标记越密，表示系统越活跃。</p>
        </div>
        <div class="flow-live">
          <span></span>
          记录模式
        </div>
      </div>

      <div class="operation-map">
        <div class="stream-rail" aria-label="事件运行记录">
          <svg class="operation-svg" viewBox="0 0 920 260" aria-hidden="true">
            <path class="stream-backbone" :d="runtimeRiverPath" />
            <path class="stream-energy" :d="runtimeRiverPath" />
            <circle
            v-for="packet in 3"
              :key="`river-${packet}`"
              class="river-packet"
              r="5"
            >
              <animateMotion :dur="runtimeRiverDuration" repeatCount="indefinite" :begin="`${packet * 0.38}s`" :path="runtimeRiverPath" />
            </circle>
          </svg>

          <button
            v-for="(item, index) in eventStreamItems"
            :key="item.id"
            type="button"
            class="stream-signal"
            :class="statusClass(item.status)"
            :style="streamSignalStyle(index, eventStreamItems.length)"
            @click="openEventStream(item)"
          >
            <span class="signal-orb"><SfShellIcon :name="iconForSource(item.source_type)" /></span>
            <strong>{{ item.title }}</strong>
            <em>{{ item.meta }}</em>
          </button>

          <div v-if="!eventStreamItems.length" class="stream-empty">
            <span class="signal-orb"><SfShellIcon name="flow" /></span>
            暂无事件动态标记，点击同步最新记录后会沿记录视图处理
          </div>
        </div>

        <aside class="stream-console" aria-label="血缘边指示器">
          <div class="console-title">
            <span>血缘指示</span>
            <strong>{{ graph.edges?.length || 0 }} 边</strong>
          </div>
          <div class="lineage-compass" :class="{ empty: !lineageFlowEdges.length }">
            <span class="lineage-core">
              <strong>{{ graph.edges?.length || 0 }}</strong>
              <em>血缘关系</em>
            </span>
            <button
              v-for="(edge, index) in lineageFlowEdges"
              :key="String(edge.id || `${edge.source || ''}${edge.target || ''}`)"
              type="button"
              class="lineage-node"
              :style="lineageNodeStyle(index, lineageFlowEdges.length)"
              @click="selected = { type: 'lineage_edge', data: edge as unknown as Record<string, unknown> }"
            >
              <span>{{ edgeLabel(edge.source) }}</span>
              <i>{{ relationText(edge.relation) }}</i>
              <span>{{ edgeLabel(edge.target) }}</span>
            </button>
            <a-empty v-if="!lineageFlowEdges.length" description="暂无血缘边" />
          </div>
        </aside>
      </div>

      <div class="material-orbit" aria-label="资产与建议自动处理环">
        <div class="material-core">
          <span>自动生成知识</span>
          <strong>{{ numberText(materialFlowItems.length) }}</strong>
          <em>资产 / 建议</em>
        </div>
        <svg class="material-orbit-svg" viewBox="0 0 1040 220" aria-hidden="true">
          <path class="material-track" d="M60 110 C220 30 350 30 520 110 C690 190 820 190 980 110" />
          <path class="material-track-hot" d="M60 110 C220 30 350 30 520 110 C690 190 820 190 980 110" />
          <circle v-for="packet in 2" :key="`material-${packet}`" class="material-packet" r="4">
            <animateMotion dur="5.8s" repeatCount="indefinite" :begin="`${packet * 0.62}s`" path="M60 110 C220 30 350 30 520 110 C690 190 820 190 980 110" />
          </circle>
        </svg>
        <button
          v-for="(item, index) in materialFlowItems"
          :key="`${item.type}-${item.id}`"
          type="button"
          class="material-token"
          :class="[`token-${item.type}`, statusClass(item.status)]"
          :style="materialTokenStyle(index, materialFlowItems.length)"
          @click="openMaterialFlow(item)"
        >
          <span>{{ item.kind }}</span>
          <strong>{{ item.title }}</strong>
          <em>{{ item.meta }}</em>
        </button>
        <div v-if="!materialFlowItems.length" class="material-empty">暂无资产 / 建议处理</div>
      </div>
    </section>

    </div>

      </main>
    </div>

    <a-drawer :visible="Boolean(selected)" width="720px" title="处理详情" @cancel="selected = null" @ok="selected = null">
      <template v-if="selected">
        <div class="detail-head">
          <span class="ai-pill info">{{ selected.type }}</span>
          <strong>{{ detailTitle }}</strong>
          <em
            v-if="selectedIsPulseDetail"
            class="detail-live-status"
            :class="{ 'is-loading': pulseLoading || pulseRefreshing, 'is-error': pulseError }"
          >
            {{ selectedDetailLiveText }}
          </em>
        </div>
        <div v-if="detailHighlights.length" class="detail-kv">
          <span v-for="item in detailHighlights" :key="item.label">
            <em>{{ item.label }}</em>
            <strong>{{ item.value }}</strong>
          </span>
        </div>
        <div v-if="selectedIsPulseDetail" class="detail-refresh-grid" aria-label="实时刷新状态">
          <span v-for="item in selectedPulseRefreshCards" :key="item.key" :class="`tone-${item.tone}`">
            <em>{{ item.label }}</em>
            <strong>{{ item.value }}</strong>
            <b>{{ item.meta }}</b>
          </span>
        </div>
        <div class="detail-actions">
          <button
            v-if="selectedIsDeploymentOutput || selectedIsModelTest"
            class="tiny-btn primary"
            type="button"
            @click="openDeploymentChatModal"
          >
            对话模型
          </button>
          <button
            v-if="selectedIsModelTest"
            class="tiny-btn"
            type="button"
            :disabled="!selectedModelTrainingJobId || trainingDatasetLoading"
            @click="openSelectedTrainingDataset"
          >
            {{ trainingDatasetLoading ? '加载训练数据' : '训练数据' }}
          </button>
          <button v-if="detailDeepLink" class="tiny-btn primary" type="button" @click="openDetailDeepLink">
            打开原页面
          </button>
          <button v-if="detailEntity" class="tiny-btn" type="button" :disabled="lineageLoading" @click="loadDetailLineage">
            {{ lineageLoading ? '加载血缘中' : '查看血缘' }}
          </button>
        </div>
        <div v-if="detailLineage.length" class="detail-lineage">
          <strong>相关血缘</strong>
          <button v-for="edge in detailLineage" :key="String(edge.id || `${edge.source || ''}-${edge.target || ''}`)" type="button" class="lineage-edge" @click="selected = { type: 'lineage_edge', data: edge as Record<string, unknown> }">
            <span>{{ edgeLabel(lineageEndpoint(edge, 'source')) }}</span>
            <em>{{ relationText(String(edge.relation || '')) }}</em>
            <span>{{ edgeLabel(lineageEndpoint(edge, 'target')) }}</span>
          </button>
        </div>
        <section v-if="selectedPulseFlowDetail" class="detail-flow">
          <header class="detail-flow-head">
            <span class="ai-pill accent">{{ selectedPulseFlowDetail.title || '流转明细' }}</span>
            <p>{{ selectedPulseFlowDetail.summary || '展示当前阶段的真实数据流转、处理依据和输入输出。' }}</p>
          </header>
          <section class="detail-production-flow" aria-label="生产过程">
            <div class="detail-production-head">
              <span>生产过程</span>
              <strong>{{ selectedPulseLifecycleTitle }}</strong>
              <p>{{ selectedPulseLifecycleMeta }}</p>
            </div>
            <div class="detail-production-track">
              <button
                v-for="(stage, index) in detailProductionStages"
                :key="stage.key"
                type="button"
                class="detail-production-stage"
                :class="[`tone-${stage.tone}`, { active: stage.active, done: stage.done }]"
                @click="openPulseModuleByKey(stage.key)"
              >
                <i>{{ index + 1 }}</i>
                <span>
                  <strong>{{ stage.label }}</strong>
                  <em>{{ stage.status }}</em>
                </span>
                <b>{{ stage.value }}</b>
                <small>{{ stage.meta }}</small>
              </button>
            </div>
          </section>
          <section class="detail-terminal" aria-label="实时处理日志">
            <div class="detail-terminal-head">
              <span>
                <strong>{{ selectedTerminalTitle }}</strong>
                <em>{{ selectedDetailLiveText }}</em>
              </span>
              <b>{{ detailTerminalLines.length }} 行</b>
            </div>
            <div ref="detailTerminalAutoScrollRef" class="detail-terminal-screen">
              <div v-if="!detailTerminalLines.length" class="detail-terminal-empty">
                {{ selectedTerminalEmptyText }}
              </div>
              <div
                v-for="line in detailTerminalLines"
                :key="line.id"
                class="detail-terminal-line"
                :class="`channel-${line.channel}`"
              >
                <span class="detail-terminal-time">{{ line.time }}</span>
                <span class="detail-terminal-channel">{{ line.label }}</span>
                <code>{{ line.text }}</code>
                <em v-if="line.meta">{{ line.meta }}</em>
              </div>
            </div>
          </section>
          <section v-if="selectedIsModelTest" class="detail-deployment-live detail-model-test-live">
            <div class="detail-deployment-head">
              <span>第四步</span>
              <strong>模型测试详情</strong>
              <p>当前测试记录关联的训练任务、模型产物和训练数据。</p>
            </div>
            <article class="detail-deployment-block">
              <div class="detail-deployment-block-head">
                <strong>{{ selectedModelTestContext.artifact_model_name || selectedModelTestContext.model_name || '训练模型' }}</strong>
                <em>{{ selectedModelTestContext.deployment_status ? learningStatusText(String(selectedModelTestContext.deployment_status)) : (selectedModelTestContext.training_job_id || '等待评估任务') }}</em>
              </div>
              <div class="detail-model-test-grid">
                <span v-for="item in modelTestDetailCards" :key="item.label">
                  <em>{{ item.label }}</em>
                  <strong>{{ item.value }}</strong>
                </span>
              </div>
              <div class="detail-chat-actions">
                <button
                  class="tiny-btn"
                  type="button"
                  :disabled="!selectedModelTrainingJobId || trainingDatasetLoading"
                  @click="openSelectedTrainingDataset"
                >
                  {{ trainingDatasetLoading ? '加载中' : '训练数据列表' }}
                </button>
                <button class="tiny-btn primary" type="button" @click="openDeploymentChatModal">
                  对话模型
                </button>
                <button class="tiny-btn" type="button" :disabled="Boolean(deploymentChatDisabledReason)" @click="openDeploymentChat">
                  进入完整对话
                </button>
              </div>
              <p class="detail-model-test-note">{{ deploymentChatDisabledReason || '当前模型可携带训练任务、产物和部署上下文直接对话。' }}</p>
            </article>
            <article class="detail-deployment-block">
              <div class="detail-deployment-block-head">
                <strong>测试任务</strong>
                <em>{{ modelTestProcessRows.length ? `${modelTestProcessRows.length} 条测试记录` : '等待模型测试回传' }}</em>
              </div>
              <div v-if="modelTestProcessRows.length" class="detail-deployment-list">
                <button
                  v-for="item in modelTestProcessRows"
                  :key="item.key"
                  type="button"
                  class="detail-deployment-row"
                  @click="openPulseFlowRowFromModule('test_results', item.section, item.row, item.rowIndex)"
                >
                  <span>
                    <strong>{{ item.title }}</strong>
                    <em>{{ item.statusText }}</em>
                  </span>
                  <p>{{ item.summary }}</p>
                  <b>{{ item.source }} → {{ item.destination }}</b>
                  <code>{{ item.outputText }}</code>
                </button>
              </div>
              <div v-else class="detail-flow-empty">当前没有模型测试任务或输出。</div>
            </article>
          </section>
          <section v-if="selectedIsDeploymentOutput" class="detail-deployment-live">
            <div class="detail-deployment-head">
              <span>第五步</span>
              <strong>部署输出验证</strong>
              <p>这里读取当前脉动里的测试模块、部署模块和应用链路；数据刷新后会跟随处理详情同步更新。</p>
            </div>
            <article class="detail-deployment-block">
              <div class="detail-deployment-block-head">
                <strong>测试过程</strong>
                <em>{{ fifthStageTestProcessRows.length ? `${fifthStageTestProcessRows.length} 条测试记录` : '等待模型测试回传' }}</em>
              </div>
              <div v-if="fifthStageTestProcessRows.length" class="detail-deployment-list">
                <button
                  v-for="item in fifthStageTestProcessRows"
                  :key="item.key"
                  type="button"
                  class="detail-deployment-row"
                  @click="openPulseFlowRowFromModule('test_results', item.section, item.row, item.rowIndex)"
                >
                  <span>
                    <strong>{{ item.title }}</strong>
                    <em>{{ item.statusText }}</em>
                  </span>
                  <p>{{ item.summary }}</p>
                  <b>{{ item.source }} → {{ item.destination }}</b>
                  <code>{{ item.outputText }}</code>
                </button>
              </div>
              <div v-else class="detail-flow-empty">当前没有模型测试任务或输出；有评估 Agent 回传后会自动展示。</div>
            </article>
            <article class="detail-deployment-block">
              <div class="detail-deployment-block-head">
                <strong>输出过程</strong>
                <em>{{ fifthStageOutputProcessRows.length ? `${fifthStageOutputProcessRows.length} 条输出链路` : '等待部署输出' }}</em>
              </div>
              <div v-if="fifthStageOutputProcessRows.length" class="detail-deployment-list">
                <button
                  v-for="item in fifthStageOutputProcessRows"
                  :key="item.key"
                  type="button"
                  class="detail-deployment-row"
                  @click="openPulseFlowRowFromModule('application_outputs', item.section, item.row, item.rowIndex)"
                >
                  <span>
                    <strong>{{ item.title }}</strong>
                    <em>{{ item.statusText }}</em>
                  </span>
                  <p>{{ item.summary }}</p>
                  <b>{{ item.source }} → {{ item.destination }}</b>
                  <code>{{ item.outputText }}</code>
                </button>
              </div>
              <div v-else class="detail-flow-empty">当前没有部署输出链路；模型部署或运行调用产生后会自动展示。</div>
            </article>
            <article class="detail-deployment-block detail-deployment-chat">
              <div class="detail-deployment-block-head">
                <strong>训练后模型</strong>
                <em>{{ deploymentChatContextText }}</em>
              </div>
              <div v-if="fifthStageConversationRows.length" class="detail-chat-examples">
                <button
                  v-for="item in fifthStageConversationRows"
                  :key="item.key"
                  type="button"
                  @click="openPulseFlowRowFromModule('application_outputs', item.section, item.row, item.rowIndex)"
                >
                  <span>
                    <em>输入</em>
                    <strong>{{ item.inputText }}</strong>
                  </span>
                  <span>
                    <em>输出</em>
                    <strong>{{ item.outputText }}</strong>
                  </span>
                </button>
              </div>
              <div v-if="deploymentChatMessages.length" class="detail-chat-log" aria-label="部署模型直接对话记录">
                <div
                  v-for="(message, index) in deploymentChatMessages"
                  :key="`${index}-${message.role}`"
                  class="detail-chat-message"
                  :class="message.role"
                >
                  <em>{{ message.role === 'user' ? '输入' : message.role === 'assistant' ? '输出' : '状态' }}</em>
                  <p>{{ message.content || (message.streaming ? '正在生成输出...' : '') }}</p>
                  <small v-if="deploymentChatMessageMeta(message)">{{ deploymentChatMessageMeta(message) }}</small>
                </div>
              </div>
              <div class="detail-chat-compose">
                <textarea
                  v-model="deploymentChatDraft"
                  rows="3"
                  placeholder="输入要问这个部署模型的问题"
                  aria-label="部署模型对话问题"
                ></textarea>
                <div class="detail-chat-actions">
                  <button
                    class="tiny-btn primary"
                    type="button"
                    @click="openDeploymentChatModal"
                  >
                    对话模型
                  </button>
                  <button class="tiny-btn" type="button" :disabled="Boolean(deploymentChatDisabledReason)" @click="openDeploymentChat">
                    进入完整对话
                  </button>
                  <button v-if="deploymentChatDeepLink" class="tiny-btn" type="button" @click="openDeploymentPage">
                    打开部署记录
                  </button>
                </div>
                <p>{{ deploymentChatDisabledReason || '将使用当前训练部署 ID、训练任务和模型产物上下文对话。' }}</p>
              </div>
            </article>
          </section>
          <section class="detail-process">
            <div class="detail-process-head">
              <span>{{ selectedPulseModuleTitle }}</span>
              <strong>数据过程</strong>
              <p>{{ selectedPulseProcessNarrative }}</p>
            </div>
            <div v-if="selectedPulseDataProcesses.length" class="detail-process-list">
              <button
                v-for="item in selectedPulseDataProcesses"
                :key="item.key"
                type="button"
                class="detail-process-card"
                @click="openPulseFlowRow(item.section, item.row, item.rowIndex)"
              >
                <span class="detail-process-title">
                  <strong>{{ item.title }}</strong>
                  <em>{{ item.statusText }} · {{ item.syncText }}</em>
                </span>
                <span class="detail-process-line">
                  <span>
                    <em>输入</em>
                    <strong>{{ item.inputTitle }}</strong>
                    <b>{{ item.inputDetail }}</b>
                  </span>
                  <i>→</i>
                  <span>
                    <em>本步处理</em>
                    <strong>{{ item.actionTitle }}</strong>
                    <b>{{ item.actionDetail }}</b>
                  </span>
                  <i>→</i>
                  <span>
                    <em>输出</em>
                    <strong>{{ item.outputTitle }}</strong>
                    <b>{{ item.outputDetail }}</b>
                  </span>
                </span>
                <small v-if="item.reason">{{ item.reason }}</small>
                <small v-else>{{ item.changeText }}</small>
              </button>
            </div>
            <div v-else class="detail-flow-empty">{{ selectedPulseProcessingEmptyText }}</div>
          </section>
          <section class="detail-processing">
            <div class="detail-processing-head">
              <span>{{ selectedPulseModuleTitle }}</span>
              <strong>本步处理数据</strong>
              <p>{{ selectedPulseProcessingSummary }}</p>
            </div>
            <div class="detail-processing-steps">
              <span v-for="step in selectedPulseProcessingSteps" :key="step.key">
                <em>{{ step.label }}</em>
                <strong>{{ step.value }}</strong>
                <b>{{ step.detail }}</b>
              </span>
            </div>
            <div v-if="selectedPulseProcessingRows.length" class="detail-processing-list">
              <button
                v-for="item in selectedPulseProcessingRows"
                :key="item.key"
                type="button"
                class="detail-processing-row"
                @click="openPulseFlowRow(item.section, item.row, item.rowIndex)"
              >
                <span class="detail-processing-row-main">
                  <strong>{{ item.title }}</strong>
                  <em>{{ item.statusText }} · {{ item.syncText }}</em>
                </span>
                <span class="detail-processing-row-route">
                  <b>{{ item.source }}</b>
                  <i>→</i>
                  <b>{{ item.destination }}</b>
                </span>
                <p>{{ item.summary }}</p>
                <small v-if="item.reason">{{ item.reason }}</small>
                <span class="detail-processing-io">
                  <code>{{ item.inputText }}</code>
                  <code>{{ item.outputText }}</code>
                </span>
              </button>
            </div>
            <div v-else class="detail-flow-empty">{{ selectedPulseProcessingEmptyText }}</div>
          </section>
          <div v-if="detailFlowStorageRows.length" class="detail-flow-summary">
            <span v-for="item in detailFlowStorageRows" :key="item.label">
              <em>{{ item.label }}</em>
              <strong>{{ item.value }}</strong>
            </span>
          </div>
	          <article
	            v-for="section in selectedPulseFlowSections"
	            :key="section.key || section.title"
	            class="detail-flow-section"
	          >
	            <div class="detail-flow-section-head">
	              <span>
	                <strong>{{ section.title || '明细' }}</strong>
	                <em>{{ pulseSectionCountText(section) }}</em>
	              </span>
	              <p>{{ section.description || '暂无说明。' }}</p>
	              <button
	                v-if="canLoadMorePulseSection(selectedPulseModuleKey, section)"
	                class="tiny-btn"
	                type="button"
	                :disabled="isPulseSectionDrilldownLoading(selectedPulseModuleKey, section)"
	                @click="loadMorePulseSection(selectedPulseModuleKey, section)"
	              >
	                {{ isPulseSectionDrilldownLoading(selectedPulseModuleKey, section) ? '加载中' : '加载更多' }}
	              </button>
	              <small v-if="pulseSectionDrilldownError(selectedPulseModuleKey, section)">{{ pulseSectionDrilldownError(selectedPulseModuleKey, section) }}</small>
	            </div>
            <div v-if="(section.items || []).length" class="detail-flow-list">
              <button
                v-for="(row, index) in section.items"
                :key="`${section.key || section.title}-${index}-${row.entity || row.title}`"
                type="button"
                class="detail-flow-row"
                @click="openPulseFlowRow(section, row, index)"
              >
                <span class="detail-flow-row-head">
                  <strong>{{ row.title || row.entity || '流转记录' }}</strong>
                  <em>{{ row.status_text || learningStatusText(row.status) || '-' }}</em>
                </span>
                <span v-if="row.summary" class="detail-flow-row-summary">{{ row.summary }}</span>
                <span class="detail-flow-route">
                  <b>{{ row.source || '当前阶段' }}</b>
                  <i>→</i>
                  <b>{{ row.destination || row.entity || '下一阶段' }}</b>
                </span>
                <span v-if="row.reason" class="detail-flow-reason">{{ row.reason }}</span>
                <span v-if="row.input_preview || row.output_preview" class="detail-flow-io">
                  <span>
                    <em>输入</em>
                    <code>{{ previewCompact(row.input_preview) }}</code>
                  </span>
                  <span>
                    <em>输出</em>
                    <code>{{ previewCompact(row.output_preview) }}</code>
                  </span>
                </span>
              </button>
            </div>
            <div v-else class="detail-flow-empty">{{ section.empty_text || '当前没有可展示的记录。' }}</div>
          </article>
        </section>
        <details class="detail-json-wrap">
          <summary>审计 JSON</summary>
          <pre class="detail-json">{{ JSON.stringify(selected.data, null, 2) }}</pre>
        </details>
      </template>
    </a-drawer>
    <a-modal
      v-model:visible="deploymentChatModalVisible"
      title="对话模型"
      :footer="false"
      :width="760"
      modal-class="deployment-chat-modal"
    >
      <section class="detail-deployment-chat modal-chat-panel">
        <div class="detail-deployment-block-head">
          <strong>训练后的模型</strong>
          <em>{{ deploymentChatContextText }}</em>
        </div>
        <div class="deployment-model-context">
          <span>
            <em>部署</em>
            <strong>{{ selectedDeploymentChatContext.model_deployment_id || '未生成' }}</strong>
          </span>
          <span>
            <em>训练任务</em>
            <strong>{{ selectedDeploymentChatContext.training_job_id || '-' }}</strong>
          </span>
          <span>
            <em>节点</em>
            <strong>{{ selectedDeploymentChatContext.target_gateway_id || '-' }}</strong>
          </span>
        </div>
        <div v-if="deploymentChatMessages.length" class="detail-chat-log modal-chat-log" aria-label="训练后模型对话记录">
          <div
            v-for="(message, index) in deploymentChatMessages"
            :key="`${index}-${message.role}`"
            class="detail-chat-message"
            :class="message.role"
          >
            <em>{{ message.role === 'user' ? '输入' : message.role === 'assistant' ? '输出' : '状态' }}</em>
            <p>{{ message.content || (message.streaming ? '正在生成输出...' : '') }}</p>
            <small v-if="deploymentChatMessageMeta(message)">{{ deploymentChatMessageMeta(message) }}</small>
          </div>
        </div>
        <div v-else class="detail-flow-empty">
          {{ deploymentChatDisabledReason || '输入问题后会直接携带训练部署上下文调用该模型。' }}
        </div>
        <div class="detail-chat-compose">
          <textarea
            v-model="deploymentChatDraft"
            rows="4"
            placeholder="输入要问这个训练后模型的问题"
            aria-label="训练后模型对话问题"
          ></textarea>
          <div class="detail-chat-actions">
            <button
              class="tiny-btn primary"
              type="button"
              :disabled="Boolean(deploymentChatDisabledReason) || deploymentChatSending || deploymentChatConnecting"
              @click="sendDeploymentChat"
            >
              {{ deploymentChatSending || deploymentChatConnecting ? '对话中' : '发送' }}
            </button>
            <button class="tiny-btn" type="button" :disabled="Boolean(deploymentChatDisabledReason)" @click="openDeploymentChat">
              进入完整对话
            </button>
            <button v-if="deploymentChatDeepLink" class="tiny-btn" type="button" @click="openDeploymentPage">
              打开部署记录
            </button>
          </div>
          <p>{{ deploymentChatDisabledReason || '当前消息会带上训练部署 ID、训练任务、产物和训练/推理节点。' }}</p>
        </div>
      </section>
    </a-modal>
    <a-modal
      v-model:visible="trainingDatasetModalVisible"
      title="训练数据"
      :footer="false"
      :width="820"
      modal-class="training-dataset-modal"
    >
      <section class="training-dataset-panel">
        <div class="training-dataset-head">
          <span v-for="item in trainingDatasetSummaryCards" :key="item.label">
            <em>{{ item.label }}</em>
            <strong>{{ item.value }}</strong>
          </span>
        </div>
        <div class="detail-chat-actions">
          <button class="tiny-btn primary" type="button" @click="openDeploymentChatModal">
            对话模型
          </button>
          <button class="tiny-btn" type="button" :disabled="Boolean(deploymentChatDisabledReason)" @click="openDeploymentChat">
            进入完整对话
          </button>
        </div>
        <div v-if="trainingDatasetLoading" class="detail-flow-empty">训练数据加载中。</div>
        <div v-else-if="trainingDatasetError" class="detail-flow-empty">{{ trainingDatasetError }}</div>
        <div v-else-if="trainingDatasetSamples.length" class="training-dataset-list">
          <button
            v-for="sample in trainingDatasetSamples"
            :key="sample.artifact_id || sample.sink_id || sample.title"
            type="button"
            class="training-dataset-row"
            @click="openTrainingDatasetSample(sample)"
          >
            <span>
              <strong>{{ sample.title || sample.artifact_id }}</strong>
              <em>{{ artifactKindText(sample.artifact_kind) }} · {{ sample.status || '-' }}</em>
            </span>
            <p>{{ sample.summary || '训练样本摘要未返回。' }}</p>
            <b>{{ sample.skill_id || sample.target_id || '-' }} · {{ sample.run_id || sample.event_id || '-' }}</b>
            <code>{{ previewCompact(sample.lineage_summary) }}</code>
          </button>
        </div>
        <div v-else class="detail-flow-empty">当前训练任务没有可展示的训练数据。</div>
      </section>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import {
  aiclawApi,
  learningApi,
  type ImprovementCandidateRow,
  type LearningAutomationStatusResponse,
  type LearningBottlenecksResponse,
  type LearningBottleneckSection,
  type LearningArtifactRow,
  type LearningEventRow,
  type LearningFlowGraphResponse,
  type LearningFlowJourneyResponse,
  type LearningFlowJourneyRow,
  type LearningFlowTopologyNode,
  type LearningFlowTopologyResponse,
  type LearningPulseItem,
  type LearningPulseFlowDetail,
  type LearningPulseFlowRow,
  type LearningPulseFlowSection,
  type LearningPulseMetric,
  type LearningPulseModule,
  type LearningPulseResponse,
  type LearningSummaryResponse,
  type LearningTrainingJobDatasetResponse,
  type LearningTrainingJobDatasetSample,
  type LearningTrainingManifestResponse,
} from '@/api'
import { createAiclawChatSocket } from '@/api/aiclawWs'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

type PulseModuleView = LearningPulseModule & { loading?: boolean }
type Detail = { type: string; data: Record<string, unknown> } | null
type DetailEntity = { type: string; id: string } | null
type DetailProcessingRow = {
  key: string
  section: LearningPulseFlowSection
  row: LearningPulseFlowRow
  rowIndex: number
  title: string
  statusText: string
  summary: string
  source: string
  destination: string
  reason: string
  inputText: string
  outputText: string
  syncText: string
  changeText: string
}
type DetailProcessingStep = {
  key: string
  label: string
  value: string
  detail: string
}
type DetailDataProcess = {
  key: string
  section: LearningPulseFlowSection
  row: LearningPulseFlowRow
  rowIndex: number
  title: string
  statusText: string
  inputTitle: string
  inputDetail: string
  actionTitle: string
  actionDetail: string
  outputTitle: string
  outputDetail: string
  reason: string
  syncText: string
  changeText: string
}
type DetailTerminalLine = {
  id: string
  time: string
  channel: 'fetch' | 'metric' | 'input' | 'process' | 'output' | 'error'
  label: string
  text: string
  meta?: string
}
type DeploymentChatContext = {
  ready: boolean
  disabled_reason: string
  inference_ready: boolean
  inference_disabled_reason: string
  model_deployment_id: string
  model_family: string
  training_job_id: string
  department: string
  artifact_id: string
  artifact_sha256: string
  artifact_uri_present: boolean
  deployment_status: string
  target_gateway_id: string
  target_gateway_kind: string
  target_gateway_active: boolean
}
type DeploymentChatMessage = {
  role: 'user' | 'assistant' | 'system'
  content: string
  streaming?: boolean
  finishReason?: string
  metrics?: Record<string, unknown>
}
type GraphNodeRow = NonNullable<LearningFlowGraphResponse['nodes']>[number]
type FlowNode = {
  id: string
  label: string
  meta: string
  icon: string
  heat: number
  tone?: string
  payload: Record<string, unknown>
}
type FlowStage = {
  key: string
  title: string
  caption: string
  count: number
  tone: string
  nodes: FlowNode[]
}
type PulseFilterPatch = Partial<{
  days: number
  skill_id: string
  department: string
  run_id: string
  source_type: string
  event_type: string
  artifact_kind: string
  target_type: string
  sf_tool: string
  q: string
  artifact_status: string
  candidate_status: string
}>
type PulseNode = {
  key: string
  label: string
  meta: string
  value: number
  icon: string
  tone: 'source' | 'event' | 'artifact' | 'sink' | 'candidate' | 'review' | 'danger'
  x: number
  y: number
  path: string
  duration: string
  filters: PulseFilterPatch
}
type PulseInsight = {
  key: string
  label: string
  value: string
  meta: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type EventStreamItem = {
  id: string
  title: string
  meta: string
  status?: string
  source_type?: string
  data: Record<string, unknown>
}

const PULSE_LIVE_REFRESH_MS = 8000
type MaterialFlowItem = {
  id: string
  type: 'artifact' | 'candidate'
  title: string
  kind: string
  meta: string
  status?: string
  data: Record<string, unknown>
}
type DetailRefreshCard = {
  key: string
  label: string
  value: string
  meta: string
  tone: 'good' | 'warn' | 'bad' | 'info'
}
type DetailProductionStage = {
  key: string
  label: string
  value: string
  status: string
  meta: string
  tone: 'good' | 'warn' | 'bad' | 'info' | 'source' | 'artifact' | 'sink'
  active: boolean
  done: boolean
}
type FlowSignal = {
  key: string
  label: string
  count: number
  tone: string
  begin: string
}
type AutomationMetric = {
  key: string
  label: string
  value: string
  meta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  data: Record<string, unknown>
}
type QuickFocusFilter = {
  key: string
  label: string
  meta: string
  icon: string
  tone: string
  filters: PulseFilterPatch
}
type AiPerspective = {
  key: string
  label: string
  meta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info' | 'source' | 'artifact' | 'candidate'
  target: string
  filters: PulseFilterPatch
  data: Record<string, unknown>
}
type BriefItem = {
  key: string
  label: string
  value: string
  meta: string
  action: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  icon: string
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type PulseOverviewCard = {
  key: string
  label: string
  value: string
  meta: string
  reason: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  icon: string
  target: string
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type PulseDecisionItem = {
  key: string
  label: string
  value: string
  meta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  target: string
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type PulseAutomationCard = PulseDecisionItem & {
  level: number
}
type AiOperationQueueChip = PulseDecisionItem & {
  level: number
}
type AiOperationStatusItem = PulseDecisionItem
type GlobalFlowNode = {
  key: string
  label: string
  description: string
  icon: string
  tone: string
  x: number
  y: number
  total: number
  newCount: number
  updateCount: number
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type GlobalFlowEdge = {
  key: string
  from: string
  to: string
  label: string
  count: number
  newCount: number
  updateCount: number
  path: string
  labelX: number
  labelY: number
  tone: string
  data: Record<string, unknown>
}
type GlobalFlowDetailItem = {
  key: string
  title: string
  meta: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  data: Record<string, unknown>
}
type GlobalDetailMetric = {
  key: 'total' | 'new' | 'update'
  label: string
  value: string
  meta: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  data: Record<string, unknown>
}
type AiSideAction = {
  key: string
  label: string
  meta: string
  cta: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  icon: string
  kind: 'bottleneck' | 'automation' | 'capture' | 'brief' | 'panel'
  target?: string
  data: Record<string, unknown>
}
type AiSideNavItem = {
  id: string
  label: string
  meta: string
  icon: string
}
type AiSideSectionAnchor = {
  id: string
  label: string
  value: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
}
type AiStickyCommand = {
  kicker: string
  label: string
  value: string
  meta: string
  cta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  source: 'decision' | 'autopilot'
  target: string
  score: number
  motion?: MotionMode
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type AiStickyCommandChip = {
  key: string
  label: string
  value: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
}
type AiStickyCommandReason = {
  key: string
  label: string
  value: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
}
type AiStickyCommandConfidence = {
  score: number
  label: string
  meta: string
  tone: 'good' | 'warn' | 'bad' | 'info'
}
type AiExecutionGuardrail = {
  key: string
  label: string
  value: string
  meta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  target: string
  data: Record<string, unknown>
}
type AiMissionBrief = {
  kicker: string
  title: string
  meta: string
  output: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  confidence: number
  target: string
  data: Record<string, unknown>
}
type AiMissionStep = {
  key: 'sense' | 'judge' | 'execute' | 'learn'
  label: string
  value: string
  meta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  status: string
  progress: number
  target: string
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type AiSideHudCard = {
  key: string
  label: string
  value: string
  meta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  target: string
  targetScope: 'side' | 'panel'
  data: Record<string, unknown>
}
type AiSideRouteStep = {
  key: string
  label: string
  value: string
  status: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  icon: string
  target: string
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type AiMotionMeter = {
  key: string
  label: string
  value: string
  meta: string
  percent: number
  tone: 'good' | 'warn' | 'bad' | 'info'
  target: string
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type AiAutopilotStage = {
  key: string
  label: string
  meta: string
  value: string
  status: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  target: string
  progress: number
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type AiAutopilotPrimary = {
  key: string
  label: string
  meta: string
  cta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  kind: 'capture' | 'govern' | 'materialize' | 'training' | 'refresh'
  target: string
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type AiEvidenceSignal = {
  key: string
  label: string
  value: string
  meta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  target: string
  strength: number
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type AiRootCause = {
  key: string
  label: string
  reason: string
  evidence: string
  action: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  target: string
  score: number
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type AiImpactForecast = {
  key: string
  label: string
  value: string
  meta: string
  change: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  target: string
  score: number
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type AiHeatSignal = {
  key: string
  label: string
  value: string
  meta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  target: string
  score: number
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type AiDecisionQueueItem = {
  key: string
  label: string
  value: string
  meta: string
  cta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  target: string
  score: number
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type AiCommandPlanStep = {
  key: string
  phase: string
  label: string
  value: string
  meta: string
  cta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  target: string
  score: number
  motion?: MotionMode
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type AiHeartbeat = {
  title: string
  meta: string
  cta: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  score: number
  next: string
  data: Record<string, unknown>
}
type AiHeartbeatStep = {
  key: string
  label: string
  value: string
  meta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  target: string
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type AiPlaybook = {
  key: string
  label: string
  meta: string
  cta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  target: string
  score: number
  motion: MotionMode
  reset?: boolean
  filters?: PulseFilterPatch
  data: Record<string, unknown>
}
type MotionMode = 'auto' | 'calm' | 'trace'
type MotionModeOption = {
  key: MotionMode
  label: string
  meta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
}
type AiActionTrailItem = {
  id: string
  type: 'autopilot' | 'evidence' | 'rootcause' | 'impact' | 'heat' | 'decision' | 'heartbeat' | 'playbook' | 'focus' | 'plan' | 'guardrail' | 'mission' | 'overview' | 'rationale'
  label: string
  meta: string
  icon: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  target: string
  motion?: MotionMode
  reset?: boolean
  filters?: PulseFilterPatch
  detail: NonNullable<Detail>
}

const loading = ref(false)
const pulseLoading = ref(false)
const pulseRefreshing = ref(false)
const pulseError = ref('')
let pulseRequestSeq = 0
let pulseRequestInFlight = false
let pulseLiveTimer: number | null = null
let detailListsLoaded = false
let detailListsLoading: Promise<void> | null = null
let hasCompletedInitialHomeLoad = false
const backfilling = ref(false)
const automationRunning = ref(false)
const agentizing = ref(false)
const selfAuditing = ref(false)
const router = useRouter()
const route = useRoute()
const summary = ref<LearningSummaryResponse>({})
const pulse = ref<LearningPulseResponse>({ modules: [] })
const events = ref<LearningEventRow[]>([])
const artifacts = ref<LearningArtifactRow[]>([])
const candidates = ref<ImprovementCandidateRow[]>([])
const graph = ref<LearningFlowGraphResponse>({ nodes: [], edges: [] })
const topology = ref<LearningFlowTopologyResponse>({ stages: [], connectors: [] })
const journeyFlow = ref<LearningFlowJourneyResponse>({ items: [], state_counts: {}, blocker_counts: {} })
const bottlenecks = ref<LearningBottlenecksResponse>({ summary: {}, sections: [] })
const automation = ref<LearningAutomationStatusResponse>({})
const manifest = ref<LearningTrainingManifestResponse>({})
const selected = ref<Detail>(null)
const selectedNodeId = ref('')
const motionMode = ref<MotionMode>('auto')
const pulseLayoutMode = ref<'simple' | 'full'>('simple')
const aiActionTrail = ref<AiActionTrailItem[]>([])
const filtersExpanded = ref(false)
const showDiagnosticSections = ref(false)
const activeGlobalNodeKey = ref('source')
const activeGlobalEdgeKey = ref('')
const activeSidePanel = ref('panel-global-map')
const activeAiSideSection = ref('ai-side-heartbeat')
const aiSideToolsExpanded = ref(false)
const aiSideMenuRef = ref<HTMLElement | null>(null)
const aiSideScrollProgress = ref(0)
const detailLineage = ref<Array<Record<string, unknown>>>([])
const lineageLoading = ref(false)
const detailTerminalLines = ref<DetailTerminalLine[]>([])
const detailTerminalSignature = ref('')
const detailTerminalSeen = ref<Record<string, string>>({})
const detailTerminalAutoScrollRef = ref<HTMLElement | null>(null)
const pulseDrilldownLoading = ref<Record<string, boolean>>({})
const pulseDrilldownPages = ref<Record<string, number>>({})
const pulseDrilldownErrors = ref<Record<string, string>>({})
const deploymentChatDraft = ref('请基于当前训练后的模型做一次测试对话，并说明输入、输出和适用场景。')
const deploymentChatMessages = ref<DeploymentChatMessage[]>([])
const deploymentChatSending = ref(false)
const deploymentChatConnecting = ref(false)
const deploymentChatModalVisible = ref(false)
const trainingDatasetModalVisible = ref(false)
const trainingDatasetLoading = ref(false)
const trainingDatasetError = ref('')
const trainingDatasetResult = ref<LearningTrainingJobDatasetResponse | null>(null)
const trainingDatasetContext = ref<Record<string, unknown> | null>(null)
const filters = reactive({
  days: 30,
  skill_id: '',
  department: '',
  run_id: '',
  source_type: '',
  event_type: '',
  artifact_kind: '',
  target_type: '',
  sf_tool: '',
  q: '',
  artifact_status: '',
  candidate_status: '',
})

const AI_SIDE_UTILITY_SECTION_IDS = new Set([
  'ai-side-heartbeat',
  'ai-side-decision',
  'ai-side-orchestrator',
  'ai-side-focus',
  'ai-side-motion',
  'ai-side-perspective',
  'ai-side-panels',
  'ai-side-route',
  'ai-side-queue',
  'ai-side-actions',
])

const DEFAULT_PULSE_MODULES: PulseModuleView[] = [
  {
    key: 'raw_inputs',
    title: '原始数据',
    subtitle: '原始数据进来哪些、来源哪里',
    status: 'loading',
    status_text: '加载来源',
    tone: 'source',
    loading: true,
    metrics: [
      { key: 'events', label: '原始记录', value: null },
      { key: 'sources', label: '来源类型', value: null },
      { key: 'event_types', label: '事件类型', value: null },
      { key: 'latest', label: '最近进入', value: null },
    ],
    items: [],
  },
  {
    key: 'clarified_data',
    title: '数据清洗',
    subtitle: '清洗后同步入数据库和向量库，形成可复用资产',
    status: 'loading',
    status_text: '加载清洗资产',
    tone: 'artifact',
    loading: true,
    metrics: [
      { key: 'artifacts', label: '清洗资产', value: null },
      { key: 'database_synced', label: '数据库存储', value: null },
      { key: 'vector_chunks', label: '向量分片', value: null },
      { key: 'database_version', label: '数据库版本', value: null },
    ],
    items: [],
  },
  {
    key: 'finetuning',
    title: '微调模型',
    subtitle: '微调模型进行到哪、预计还要多久',
    status: 'loading',
    status_text: '加载训练进度',
    tone: 'warn',
    loading: true,
    metrics: [
      { key: 'jobs', label: '训练任务', value: null },
      { key: 'samples', label: '样本数', value: null },
      { key: 'progress', label: '进度', value: null, suffix: '%' },
      { key: 'eta', label: '预计时间', value: null },
    ],
    items: [],
  },
  {
    key: 'test_results',
    title: '模型测试',
    subtitle: '评估是否通过、产物是否可用',
    status: 'loading',
    status_text: '加载测试结果',
    tone: 'info',
    loading: true,
    metrics: [
      { key: 'passed', label: '通过', value: null },
      { key: 'failed', label: '未通过', value: null },
      { key: 'win_rate', label: '胜率', value: null },
      { key: 'artifact', label: '产物', value: null },
    ],
    items: [],
  },
  {
    key: 'application_outputs',
    title: '部署输出',
    subtitle: '应用在哪里输出、结果怎么样',
    status: 'loading',
    status_text: '加载部署链路',
    tone: 'sink',
    loading: true,
    metrics: [
      { key: 'deployments', label: '部署', value: null },
      { key: 'active', label: '已激活', value: null },
      { key: 'used_as_model', label: '参与决策', value: null },
      { key: 'outputs', label: '输出链路', value: null },
    ],
    items: [],
  },
]

const scopeText = computed(() => summary.value.scope === 'global' ? '全局范围' : `部门范围 · ${summary.value.department || '-'}`)
const graphNodes = computed<GraphNodeRow[]>(() => graph.value.nodes || [])
const pulseModules = computed<PulseModuleView[]>(() => {
  const byKey = new Map((pulse.value.modules || []).map((module) => [module.key, module]))
  return DEFAULT_PULSE_MODULES.map((fallback) => {
    const current = byKey.get(fallback.key)
    return current ? { ...fallback, ...current, loading: false } : fallback
  })
})
const pulseGeneratedText = computed(() => {
  if (pulseLoading.value && !pulse.value.generated_at) return '正在获取实时数据'
  if (pulseRefreshing.value && pulse.value.generated_at) return `正在同步 · ${formatTimeSeconds(pulse.value.generated_at)}`
  if (pulse.value.generated_at) return `实时更新 · ${formatTimeSeconds(pulse.value.generated_at)}`
  if (pulseError.value) return '数据暂未返回'
  return '等待数据同步'
})
const selectedIsPulseDetail = computed(() => Boolean(selected.value?.type?.startsWith('learning_pulse_')))
const selectedDetailLiveText = computed(() => {
  if (pulseError.value) return '实时详情同步失败'
  if (pulseLoading.value && !pulse.value.generated_at) return '正在获取实时详情'
  if (pulseRefreshing.value) return '正在同步实时详情'
  if (pulse.value.generated_at) return `实时详情已同步 · ${formatTimeSeconds(pulse.value.generated_at)}`
  return '等待实时详情同步'
})
const selectedTerminalTitle = computed(() => {
  const moduleKey = selectedPulseModuleKey.value
  const map: Record<string, string> = {
    raw_inputs: '原始数据接入日志',
    clarified_data: '数据清洗处理日志',
    finetuning: '训练过程日志',
    test_results: '模型测试日志',
    application_outputs: '部署输出日志',
  }
  return map[moduleKey] || '实时处理日志'
})
const selectedTerminalEmptyText = computed(() => selectedIsPulseDetail.value
  ? '当前步骤后端没有返回处理记录；等待 /api/learning/pulse 返回真实 flow_detail.items。'
  : '打开五步流程中的任一步后查看实时处理日志。')
const aiActionTrailSummary = computed(() => `${aiActionTrail.value.length} 步可查看记录`)
const aiSideMoreToolsSummary = computed(() => `${AI_SIDE_UTILITY_SECTION_IDS.size} 项辅助控制 · 筛选 / 路径 / 显示`)
const motionModeOptions = computed<MotionModeOption[]>(() => [
  {
    key: 'auto',
    label: '标准',
    meta: '按数据量控制动效',
    icon: 'spark',
    tone: motionIntensity.value > 0.62 ? 'good' : 'info',
  },
  {
    key: 'calm',
    label: '阅读',
    meta: '减少动效便于阅读',
    icon: 'eye',
    tone: 'info',
  },
  {
    key: 'trace',
    label: '追踪',
    meta: '突出当前处理路径',
    icon: 'search',
    tone: ['warn', 'bad'].includes(String(pulseHealth.value.tone)) ? 'warn' : 'good',
  },
])
const activeMotionModeLabel = computed(() => motionModeOptions.value.find((item) => item.key === motionMode.value)?.meta || '按数据量控制动效')
const motionModeScale = computed(() => {
  if (motionMode.value === 'calm') return { duration: 1.58, intensity: 0.52 }
  if (motionMode.value === 'trace') return { duration: 0.86, intensity: 1.18 }
  return { duration: 1, intensity: 1 }
})
const motionIntensity = computed(() => {
  const eventCount = Number(summary.value.events?.total || events.value.length || 0)
  const artifactCount = sumRecord(summary.value.artifacts?.by_kind || {}) || artifacts.value.length
  const candidateCount = sumRecord(summary.value.candidates?.by_target || {}) || candidates.value.length
  const flowCount = Number(journeyFlow.value.state_counts?.flowing || 0)
  const blockerCount = Number(bottlenecks.value.summary?.total || 0)
  return Math.min(1, (eventCount + artifactCount * 0.8 + candidateCount * 0.9 + flowCount * 2 + blockerCount * 1.4) / 80)
})
const effectiveMotionIntensity = computed(() => Math.min(1, motionIntensity.value * motionModeScale.value.intensity + (motionMode.value === 'trace' ? 0.1 : 0)))
const pageMotionStyle = computed<Record<string, string>>(() => {
  const intensity = effectiveMotionIntensity.value
  const durationScale = motionModeScale.value.duration
  const healthColor = pulseHealth.value.tone === 'bad'
    ? '#ef4444'
    : pulseHealth.value.tone === 'warn'
      ? '#f59e0b'
      : pulseHealth.value.tone === 'good'
        ? '#10b981'
        : '#2563eb'
  return {
    '--motion-intensity': intensity.toFixed(2),
    '--motion-loop-global': `${(Math.max(5.2, 9.4 - intensity * 2.4) * durationScale).toFixed(1)}s`,
    '--motion-loop-flow': scaleDuration(flowLoopDuration.value, durationScale),
    '--motion-loop-river': scaleDuration(runtimeRiverDuration.value, durationScale),
    '--motion-health': healthColor,
  }
})
const pageMotionClass = computed(() => ({
  'motion-active': effectiveMotionIntensity.value > 0.18,
  'motion-auto': motionMode.value === 'auto',
  'motion-hot': effectiveMotionIntensity.value > 0.62,
  'motion-quiet': effectiveMotionIntensity.value <= 0.08,
  'motion-alert': ['warn', 'bad'].includes(String(pulseHealth.value.tone)),
  'motion-calm': motionMode.value === 'calm',
  'motion-trace': motionMode.value === 'trace',
  'layout-simple': pulseLayoutMode.value === 'simple',
  'layout-full': pulseLayoutMode.value === 'full',
}))
const aiSideMenuStyle = computed<Record<string, string>>(() => ({
  '--ai-side-scroll-progress': `${aiSideScrollProgress.value}%`,
}))
const globalFlowDotDuration = computed(() => {
  const intensity = effectiveMotionIntensity.value
  const blockers = Number(bottlenecks.value.summary?.total || 0)
  return `${(Math.max(5.4, 8.6 - intensity * 2.1 + Math.min(1.4, blockers * 0.12)) * motionModeScale.value.duration).toFixed(1)}s`
})
const aiMotionMeters = computed<AiMotionMeter[]>(() => {
  const eventsTotal = Number(summary.value.events?.total || events.value.length || 0)
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const autoQueue = Number(automation.value.backlog?.auto_materializable || 0)
  const reviewQueue = Number(automation.value.backlog?.review_required || 0)
  const queueTotal = autoQueue + reviewQueue
  const signalPercent = Math.max(8, Math.min(100, Math.round(motionIntensity.value * 100)))
  const blockerPercent = Math.max(blockerTotal ? 18 : 6, Math.min(100, blockerTotal * 18))
  const queuePercent = Math.max(queueTotal ? 16 : 6, Math.min(100, queueTotal * 14))
  return [
    {
      key: 'signal_velocity',
      label: '记录流速',
      value: `${signalPercent}%`,
      meta: `${numberText(eventsTotal)} 记录 · ${globalFlowDotDuration.value} / 圈`,
      percent: signalPercent,
      tone: signalPercent > 70 ? 'good' : signalPercent > 28 ? 'info' : 'warn',
      target: 'panel-global-map',
      data: { events: summary.value.events, duration: globalFlowDotDuration.value, intensity: motionIntensity.value },
    },
    {
      key: 'blocker_pressure',
      label: '待处理压力',
      value: `${numberText(blockerTotal)}`,
      meta: blockerTotal ? '红橙动画会减速提示处理' : '暂无阻塞',
      percent: blockerPercent,
      tone: blockerTotal > 3 ? 'bad' : blockerTotal > 0 ? 'warn' : 'good',
      target: 'panel-bottleneck',
      filters: blockerTotal ? { candidate_status: 'open' } : undefined,
      data: { bottlenecks: bottlenecks.value },
    },
    {
      key: 'queue_load',
      label: '队列负载',
      value: `${numberText(queueTotal)}`,
      meta: `${numberText(autoQueue)} 可生成知识 · ${numberText(reviewQueue)} 待审`,
      percent: queuePercent,
      tone: reviewQueue ? 'warn' : autoQueue ? 'info' : 'good',
      target: 'panel-automation',
      filters: autoQueue ? { artifact_status: 'ready' } : undefined,
      data: { backlog: automation.value.backlog, jobs: automation.value.jobs },
    },
  ]
})
const aiHeartbeat = computed<AiHeartbeat>(() => {
  const state = automation.value.automation || {}
  const enabled = Boolean(state.enabled)
  const interval = Number(state.interval_seconds || 0)
  const latestAt = state.finished_at || automation.value.latest?.event_at || state.started_at || ''
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const autoQueue = Number(automation.value.backlog?.auto_materializable || 0)
  const reviewQueue = Number(automation.value.backlog?.review_required || 0)
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const pressure = failedJobs * 2 + reviewQueue + autoQueue * 0.6 + blockerTotal
  const next = latestAt && interval ? addSecondsText(latestAt, interval) : '等待首轮'
  const score = enabled
    ? Math.max(8, Math.min(100, Math.round(92 - pressure * 8 + Math.min(8, Number(summary.value.events?.total || events.value.length || 0)))))
    : 12
  const tone: AiHeartbeat['tone'] = !enabled ? 'info' : failedJobs ? 'bad' : reviewQueue || blockerTotal ? 'warn' : 'good'
  return {
    title: enabled ? `自动检查 · ${statusText(state.status || 'never_run')}` : '自动检查已关闭',
    meta: enabled ? `上次 ${formatTime(latestAt)} · 下次 ${next}` : '当前只保留手动同步和知识生成',
    cta: enabled ? '看节奏' : '手动模式',
    tone,
    score,
    next,
    data: {
      enabled,
      interval_seconds: interval,
      latest_at: latestAt,
      next_at: next,
      score,
      pressure,
      automation: state,
      backlog: automation.value.backlog,
      jobs: automation.value.jobs,
      blockers: bottlenecks.value.summary,
    },
  }
})
const aiHeartbeatStyle = computed<Record<string, string>>(() => ({
  '--heartbeat-score': `${aiHeartbeat.value.score}%`,
}))
const aiHeartbeatSummary = computed(() => `${aiHeartbeat.value.title} · ${aiHeartbeat.value.next}`)
const aiHeartbeatSteps = computed<AiHeartbeatStep[]>(() => {
  const state = automation.value.automation || {}
  const latestAt = state.finished_at || automation.value.latest?.event_at || state.started_at || ''
  const interval = Number(state.interval_seconds || 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const autoQueue = Number(automation.value.backlog?.auto_materializable || 0)
  const reviewQueue = Number(automation.value.backlog?.review_required || 0)
  const queueTotal = autoQueue + reviewQueue + failedJobs
  return [
    {
      key: 'last',
      label: '上次检查',
      value: formatTime(latestAt),
      meta: statusText(state.status || 'never_run'),
      icon: statusClass(state.status) === 'bad' ? 'warn' : 'check',
      tone: statusClass(state.status) === 'bad' ? 'bad' : state.status ? 'good' : 'info',
      target: 'panel-automation',
      data: { step: 'last', automation: state, latest: automation.value.latest },
    },
    {
      key: 'next',
      label: '下次节奏',
      value: interval ? `${numberText(interval)}s` : '手动',
      meta: interval && latestAt ? addSecondsText(latestAt, interval) : '未启用固定节奏',
      icon: 'spark',
      tone: state.enabled ? 'info' : 'warn',
      target: 'panel-automation',
      data: { step: 'next', interval_seconds: interval, next_at: interval && latestAt ? addSecondsText(latestAt, interval) : null },
    },
    {
      key: 'pressure',
      label: '队列压力',
      value: numberText(queueTotal),
      meta: `${numberText(autoQueue)} 生成知识 · ${numberText(reviewQueue)} 待审 · ${numberText(failedJobs)} 失败`,
      icon: queueTotal ? 'warn' : 'shield',
      tone: failedJobs ? 'bad' : reviewQueue ? 'warn' : autoQueue ? 'info' : 'good',
      target: queueTotal ? 'panel-bottleneck' : 'panel-automation',
      filters: reviewQueue || failedJobs ? { candidate_status: 'open' } : autoQueue ? { artifact_status: 'ready' } : undefined,
      data: { step: 'pressure', backlog: automation.value.backlog, jobs: automation.value.jobs, blockers: bottlenecks.value.summary },
    },
  ]
})
const aiAutopilotStages = computed<AiAutopilotStage[]>(() => {
  const eventsTotal = Number(summary.value.events?.total || events.value.length || 0)
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const samples = Number(summary.value.training?.samples || manifest.value.sample_total || 0)
  const feedback = sumRecord(summary.value.candidates?.by_target || {}) || candidates.value.length
  return [
    {
      key: 'capture',
      label: '同步记录',
      value: numberText(eventsTotal),
      meta: eventsTotal ? `${numberText(eventsTotal)} 条事件进入标准池` : '需要先同步运行 / SF / Agent 记录',
      status: eventsTotal ? '已启动' : '待同步',
      icon: eventsTotal ? 'spark' : 'play',
      tone: eventsTotal ? 'good' : 'warn',
      target: 'panel-global-map',
      progress: eventsTotal ? Math.min(100, 40 + eventsTotal * 3) : 18,
      data: { stage: 'capture', events: summary.value.events, filters: { ...filters } },
    },
    {
      key: 'materialize',
      label: '生成知识资产',
      value: numberText(readyAssets),
      meta: readyAssets ? `${numberText(readyAssets)} 个资产可自动生成知识` : '知识 / 样本 / 记忆暂无堆积',
      status: readyAssets ? '待生成知识' : '正常',
      icon: 'database',
      tone: readyAssets ? 'info' : 'good',
      target: 'panel-automation',
      progress: readyAssets ? Math.max(34, 86 - readyAssets * 8) : 100,
      filters: readyAssets ? { artifact_status: 'ready' } : undefined,
      data: { stage: 'materialize', backlog: automation.value.backlog, artifacts: artifacts.value.slice(0, 5) },
    },
    {
      key: 'govern',
      label: '待审核事项',
      value: numberText(blockerTotal + reviewRequired + failedJobs),
      meta: failedJobs ? `${numberText(failedJobs)} 个失败任务优先` : blockerTotal || reviewRequired ? `${numberText(blockerTotal)} 待处理事项 · ${numberText(reviewRequired)} 待审` : '建议与待办未阻塞流程',
      status: failedJobs ? '异常' : blockerTotal || reviewRequired ? '需处理' : '通过',
      icon: failedJobs || blockerTotal ? 'warn' : 'shield',
      tone: failedJobs ? 'bad' : blockerTotal || reviewRequired ? 'warn' : 'good',
      target: 'panel-bottleneck',
      progress: failedJobs ? 26 : blockerTotal || reviewRequired ? Math.max(36, 88 - (blockerTotal + reviewRequired) * 10) : 100,
      filters: blockerTotal || reviewRequired || failedJobs ? { candidate_status: 'open' } : undefined,
      data: { stage: 'govern', bottlenecks: bottlenecks.value, review_required: reviewRequired, failed_jobs: failedJobs },
    },
    {
      key: 'train_feedback',
      label: '训练改进建议',
      value: numberText(samples + feedback),
      meta: samples || feedback ? `${numberText(samples)} 样本 · ${numberText(feedback)} 个改进建议` : '等待可训练样本或改进建议',
      status: samples || feedback ? '可改进建议' : '待积累',
      icon: samples ? 'beaker' : 'flow',
      tone: samples || feedback ? 'good' : 'info',
      target: 'panel-journey',
      progress: Math.min(100, 24 + samples * 12 + feedback * 10),
      filters: samples ? { source_type: 'training_job' } : undefined,
      data: { stage: 'train_feedback', training: summary.value.training, manifest: manifest.value, candidates: summary.value.candidates },
    },
  ]
})
const aiAutopilotPrimary = computed<AiAutopilotPrimary>(() => {
  const eventsTotal = Number(summary.value.events?.total || events.value.length || 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0)
  const samples = Number(summary.value.training?.samples || manifest.value.sample_total || 0)
  if (!eventsTotal) {
    return {
      key: 'capture',
      label: '建议先同步首批记录',
      meta: '没有事件时动画保持低速，先让系统产生可查看血缘。',
      cta: '同步一次',
      icon: 'play',
      tone: 'warn',
      kind: 'capture',
      target: 'panel-global-map',
      data: { reason: 'empty_events', summary: summary.value },
    }
  }
  if (failedJobs || blockerTotal || reviewRequired) {
    const firstSection = bottleneckSections.value.find((section) => Number(section.count || 0) > 0)
    return {
      key: 'govern',
      label: failedJobs ? '建议先处理失败链路' : '建议先处理待审核事项',
      meta: firstSection?.title || `${numberText(blockerTotal)} 个待处理事项 · ${numberText(reviewRequired)} 个待审`,
      cta: '查看待处理项',
      icon: failedJobs || blockerTotal ? 'warn' : 'inbox',
      tone: failedJobs ? 'bad' : 'warn',
      kind: 'govern',
      target: 'panel-bottleneck',
      filters: { candidate_status: 'open' },
      data: { reason: 'blocked_loop', section: firstSection, bottlenecks: bottlenecks.value, review_required: reviewRequired, failed_jobs: failedJobs },
    }
  }
  if (readyAssets) {
    return {
      key: 'materialize',
      label: '建议生成知识可用资产',
      meta: `${numberText(readyAssets)} 个资产可进入知识 / 训练 / 记忆`,
      cta: '看队列',
      icon: 'database',
      tone: 'info',
      kind: 'materialize',
      target: 'panel-automation',
      filters: { artifact_status: 'ready' },
      data: { reason: 'ready_assets', backlog: automation.value.backlog, artifacts: artifacts.value.slice(0, 5) },
    }
  }
  if (!samples) {
    return {
      key: 'training',
      label: '建议补齐训练样本',
      meta: '系统运行正常，下一步检查样本清单和处理记录。',
      cta: '看样本',
      icon: 'beaker',
      tone: 'info',
      kind: 'training',
      target: 'panel-journey',
      filters: { source_type: 'training_job' },
      data: { reason: 'training_empty', training: summary.value.training, manifest: manifest.value },
    }
  }
  return {
    key: 'healthy',
    label: '运行状态正常',
    meta: '记录、生成知识、审核流程和训练均可继续自动观察。',
    cta: '刷新评估',
    icon: 'check',
    tone: 'good',
    kind: 'refresh',
    target: 'panel-run-brief',
    data: { reason: 'healthy_loop', health: pulseHealth.value },
  }
})
const aiAutopilotSummary = computed(() => `${aiAutopilotStages.value.filter((stage) => stage.tone === 'warn' || stage.tone === 'bad').length} 个需关注`)
const aiAutopilotStyle = computed<Record<string, string>>(() => {
  const avgProgress = aiAutopilotStages.value.length
    ? aiAutopilotStages.value.reduce((sum, stage) => sum + stage.progress, 0) / aiAutopilotStages.value.length
    : 0
  return { '--autopilot-progress': `${Math.round(avgProgress)}%` }
})
const aiEvidenceSignals = computed<AiEvidenceSignal[]>(() => {
  const eventsTotal = Number(summary.value.events?.total || events.value.length || 0)
  const latestEvent = events.value[0]
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0)
  const materialized = Number(summary.value.artifacts?.by_status?.materialized || 0)
  const readyStatus = Number(summary.value.artifacts?.by_status?.ready || 0)
  const coverageBase = materialized + readyAssets + readyStatus
  const coverage = coverageBase ? Math.round((materialized / coverageBase) * 100) : 0
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const debt = blockerTotal + reviewRequired + failedJobs
  const samples = Number(summary.value.training?.samples || manifest.value.sample_total || 0)
  const sfFeedback = Number(summary.value.candidates?.by_target?.sf || 0)
  return [
    {
      key: 'signal_evidence',
      label: '记录依据',
      value: numberText(eventsTotal),
      meta: latestEvent ? `${latestEvent.event_type || 'learning.event'} · ${formatTime(latestEvent.created_at)}` : '暂无事件，建议先同步',
      icon: eventsTotal ? 'spark' : 'play',
      tone: eventsTotal ? 'good' : 'warn',
      target: 'panel-global-map',
      strength: eventsTotal ? Math.min(100, 24 + eventsTotal * 4) : 16,
      filters: latestEvent?.source_type ? { source_type: latestEvent.source_type } : undefined,
      data: { evidence: 'signals', total: eventsTotal, latest: latestEvent, summary: summary.value.events },
    },
    {
      key: 'materialization_evidence',
      label: '知识生成覆盖',
      value: coverageBase ? `${coverage}%` : '0%',
      meta: readyAssets || readyStatus ? `${numberText(readyAssets + readyStatus)} 个待生成知识内容` : `${numberText(materialized)} 个已生成知识`,
      icon: 'database',
      tone: readyAssets || readyStatus ? 'info' : materialized ? 'good' : 'warn',
      target: 'panel-automation',
      strength: coverageBase ? Math.max(18, Math.min(100, coverage)) : 12,
      filters: readyAssets || readyStatus ? { artifact_status: 'ready' } : undefined,
      data: { evidence: 'materialization', materialized, ready_assets: readyAssets, ready_status: readyStatus, coverage },
    },
    {
      key: 'governance_evidence',
      label: '审核待办',
      value: numberText(debt),
      meta: failedJobs ? `${numberText(failedJobs)} 失败 · ${numberText(blockerTotal)} 待处理事项` : debt ? `${numberText(blockerTotal)} 待处理事项 · ${numberText(reviewRequired)} 待审` : '暂无审核流程阻塞',
      icon: debt ? 'warn' : 'shield',
      tone: failedJobs ? 'bad' : debt ? 'warn' : 'good',
      target: 'panel-bottleneck',
      strength: debt ? Math.min(100, 28 + debt * 16) : 100,
      filters: debt ? { candidate_status: 'open' } : undefined,
      data: { evidence: 'governance', blockers: bottlenecks.value.summary, review_required: reviewRequired, failed_jobs: failedJobs },
    },
    {
      key: 'feedback_evidence',
      label: '训练改进建议',
      value: numberText(samples + sfFeedback),
      meta: `${numberText(samples)} 样本 · ${numberText(sfFeedback)} 个 SF 改进建议`,
      icon: samples ? 'beaker' : sfFeedback ? 'bolt' : 'flow',
      tone: samples || sfFeedback ? 'good' : 'info',
      target: 'panel-journey',
      strength: Math.min(100, 18 + samples * 16 + sfFeedback * 12),
      filters: samples ? { source_type: 'training_job' } : sfFeedback ? { source_type: 'sf_mcp_call' } : undefined,
      data: { evidence: 'feedback', samples, sf_feedback: sfFeedback, training: summary.value.training, candidates: summary.value.candidates },
    },
  ]
})
const aiEvidenceSummary = computed(() => {
  const issues = aiEvidenceSignals.value.filter((signal) => signal.tone === 'warn' || signal.tone === 'bad').length
  return issues ? `${issues} 条解释风险` : '证据链稳定'
})
const aiRootCauses = computed<AiRootCause[]>(() => {
  const causes: AiRootCause[] = []
  const eventsTotal = Number(summary.value.events?.total || events.value.length || 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  const samples = Number(summary.value.training?.samples || manifest.value.sample_total || 0)
  const candidateTotal = sumRecord(summary.value.candidates?.by_target || {}) || candidates.value.length
  if (!eventsTotal) {
    causes.push({
      key: 'no-signals',
      label: '记录未同步',
      reason: '暂无标准学习记录，页面状态只能待机',
      evidence: '记录池为空，自动处理无法提取资产、样本或建议',
      action: '先同步',
      icon: 'play',
      tone: 'warn',
      target: 'panel-global-map',
      score: 86,
      data: { cause: 'no_signals', summary: summary.value, events: events.value },
    })
  }
  if (failedJobs) {
    causes.push({
      key: 'failed-automation',
      label: '自动处理失败',
      reason: `${numberText(failedJobs)} 个自动处理任务失败`,
      evidence: '失败任务会阻断资产生成知识和后续审核流程节奏',
      action: '查失败',
      icon: 'warn',
      tone: 'bad',
      target: 'panel-automation',
      score: Math.min(100, 84 + failedJobs * 6),
      data: { cause: 'failed_automation', failed_jobs: failedJobs, jobs: automation.value.jobs },
    })
  }
  if (blockerTotal || reviewRequired) {
    causes.push({
      key: 'governance-open',
      label: '审核未完成',
      reason: `${numberText(blockerTotal)} 待处理事项 · ${numberText(reviewRequired)} 待审`,
      evidence: '建议、审核或待办未处理时，系统不会超出权限发布，只会停在审核环节',
      action: '查看审核',
      icon: 'inbox',
      tone: 'warn',
      target: 'panel-bottleneck',
      score: Math.min(100, 72 + blockerTotal * 8 + reviewRequired * 10),
      filters: { candidate_status: 'open' },
      data: { cause: 'governance_open', blockers: bottlenecks.value, review_required: reviewRequired },
    })
  }
  if (readyAssets) {
    causes.push({
      key: 'assets-not-materialized',
      label: '内容未生成知识',
      reason: `${numberText(readyAssets)} 个资产还未转成知识 / 样本 / 记忆`,
      evidence: '已提取但未生成知识的资产会让学习流程停留在中间态',
      action: '看生成知识',
      icon: 'database',
      tone: readyAssets > 6 ? 'warn' : 'info',
      target: 'panel-automation',
      score: Math.min(100, 54 + readyAssets * 7),
      filters: { artifact_status: 'ready' },
      data: { cause: 'assets_not_materialized', ready_assets: readyAssets, backlog: automation.value.backlog, artifacts: artifacts.value.slice(0, 5) },
    })
  }
  if (eventsTotal && !samples && candidateTotal) {
    causes.push({
      key: 'training-gap',
      label: '训练样本缺口',
      reason: `${numberText(candidateTotal)} 个建议已有，但训练样本不足`,
      evidence: '建议无法生成知识成可评测样本时，下一轮 Skill / Agent 优化缺少依据',
      action: '补样本',
      icon: 'beaker',
      tone: 'info',
      target: 'panel-journey',
      score: Math.min(100, 48 + candidateTotal * 8),
      filters: { source_type: 'training_job' },
      data: { cause: 'training_gap', samples, candidates: summary.value.candidates, manifest: manifest.value },
    })
  }
  if (eventsTotal && samples && !failedJobs && !blockerTotal && !reviewRequired && !readyAssets) {
    causes.push({
      key: 'healthy-loop',
      label: '暂无明显根因',
      reason: '记录、生成知识、审核流程和训练均保持正常处理',
      evidence: '当前更像健康检查场景，继续观察系统状态即可',
      action: '检查',
      icon: 'check',
      tone: 'good',
      target: 'panel-run-brief',
      score: 42,
      data: { cause: 'healthy_loop', health: pulseHealth.value, summary: summary.value },
    })
  }
  return causes.sort((a, b) => b.score - a.score).slice(0, 3)
})
const aiRootCauseSummary = computed(() => {
  const risk = aiRootCauses.value.filter((cause) => cause.tone === 'bad' || cause.tone === 'warn').length
  return risk ? `${risk} 个高优先根因` : '未发现硬阻塞'
})
const aiImpactForecasts = computed<AiImpactForecast[]>(() => {
  const forecasts: AiImpactForecast[] = []
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  const samples = Number(summary.value.training?.samples || manifest.value.sample_total || 0)
  const candidateTotal = sumRecord(summary.value.candidates?.by_target || {}) || candidates.value.length
  const eventCount = Number(summary.value.events?.total || events.value.length || 0)
  const governanceLoad = blockerTotal + reviewRequired + failedJobs
  if (governanceLoad) {
    const gain = Math.min(34, 8 + blockerTotal * 4 + reviewRequired * 5 + failedJobs * 9)
    forecasts.push({
      key: 'governance-impact',
      label: '审核完成收益',
      value: `健康度 +${gain}`,
      meta: `预计清理 ${numberText(governanceLoad)} 个审核流程阻塞`,
      change: `${numberText(governanceLoad)} 待处理`,
      icon: failedJobs ? 'warn' : 'inbox',
      tone: failedJobs ? 'bad' : 'warn',
      target: 'panel-bottleneck',
      score: Math.min(100, 64 + gain),
      filters: { candidate_status: 'open' },
      data: { impact: 'governance', score_gain: gain, blockers: bottlenecks.value, review_required: reviewRequired, failed_jobs: failedJobs },
    })
  }
  if (readyAssets) {
    const gain = Math.min(22, 5 + readyAssets * 3)
    forecasts.push({
      key: 'materialize-impact',
      label: '知识生成收益',
      value: `+${numberText(readyAssets)} 资产`,
      meta: '预计转入知识 / 样本 / Agent 记忆',
      change: `健康度 +${gain}`,
      icon: 'database',
      tone: readyAssets > 6 ? 'warn' : 'info',
      target: 'panel-automation',
      score: Math.min(100, 46 + readyAssets * 9),
      filters: { artifact_status: 'ready' },
      data: { impact: 'materialize', score_gain: gain, ready_assets: readyAssets, backlog: automation.value.backlog, artifacts: artifacts.value.slice(0, 5) },
    })
  }
  if (samples || candidateTotal) {
    const gain = Math.min(18, 4 + samples * 3 + candidateTotal * 2)
    forecasts.push({
      key: 'feedback-impact',
      label: '训练改进收益',
      value: `${numberText(samples)} 样本`,
      meta: `${numberText(candidateTotal)} 个建议可用于下一轮改进评估`,
      change: `评测依据 +${gain}`,
      icon: samples ? 'beaker' : 'edit',
      tone: samples ? 'good' : 'info',
      target: 'panel-journey',
      score: Math.min(100, 38 + samples * 10 + candidateTotal * 6),
      filters: samples ? { source_type: 'training_job' } : { candidate_status: 'open' },
      data: { impact: 'feedback', score_gain: gain, samples, candidate_total: candidateTotal, manifest: manifest.value },
    })
  }
  if (forecasts.length < 3) {
    forecasts.push({
      key: 'patrol-impact',
      label: '自动检查收益',
      value: aiHeartbeat.value.next,
      meta: eventCount ? `${numberText(eventCount)} 条记录持续观察` : '等待首批记录后进入节奏检查',
      change: '提前发现异常',
      icon: 'spark',
      tone: eventCount ? 'good' : 'info',
      target: 'panel-automation',
      score: Math.min(100, 34 + eventCount * 3 + aiHeartbeat.value.score * 0.25),
      data: { impact: 'patrol', heartbeat: aiHeartbeat.value.data, events: summary.value.events },
    })
  }
  return forecasts.sort((a, b) => b.score - a.score).slice(0, 3)
})
const aiImpactSummary = computed(() => {
  const top = aiImpactForecasts.value[0]
  return top ? `${top.label} · ${top.value}` : '暂无预估'
})
const aiHeatSignals = computed<AiHeatSignal[]>(() => {
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0)
  const readyStatus = Number(summary.value.artifacts?.by_status?.ready || 0)
  const eventsTotal = Number(summary.value.events?.total || events.value.length || 0)
  const samples = Number(summary.value.training?.samples || manifest.value.sample_total || 0)
  const candidateTotal = sumRecord(summary.value.candidates?.by_target || {}) || candidates.value.length
  const governanceRisk = failedJobs + blockerTotal + reviewRequired
  const readyTotal = readyAssets + readyStatus
  const rank: Record<AiHeatSignal['tone'], number> = { bad: 320, warn: 240, info: 120, good: 40 }
  const signals: AiHeatSignal[] = [
    {
      key: 'governance_heat',
      label: '审核风险',
      value: numberText(governanceRisk),
      meta: failedJobs ? `${numberText(failedJobs)} 失败任务优先处理` : governanceRisk ? `${numberText(blockerTotal)} 待处理事项 · ${numberText(reviewRequired)} 待审` : '审核流程链路安全',
      icon: governanceRisk ? 'warn' : 'shield',
      tone: failedJobs ? 'bad' : governanceRisk ? 'warn' : 'good',
      target: 'panel-bottleneck',
      score: governanceRisk ? Math.min(100, 42 + governanceRisk * 16 + failedJobs * 18) : 18,
      filters: governanceRisk ? { candidate_status: 'open' } : undefined,
      data: { heat: 'governance', failed_jobs: failedJobs, blockers: bottlenecks.value, review_required: reviewRequired },
    },
    {
      key: 'materialization_heat',
      label: '知识生成机会',
      value: numberText(readyTotal),
      meta: readyTotal ? `${numberText(readyTotal)} 个资产可变成知识 / 样本 / 记忆` : '暂无待生成知识内容',
      icon: 'database',
      tone: readyTotal > 5 ? 'warn' : readyTotal ? 'info' : 'good',
      target: 'panel-automation',
      score: readyTotal ? Math.min(100, 34 + readyTotal * 13) : 16,
      filters: readyTotal ? { artifact_status: 'ready' } : undefined,
      data: { heat: 'materialization', ready_assets: readyAssets, ready_status: readyStatus, backlog: automation.value.backlog },
    },
    {
      key: 'signal_heat',
      label: '记录热度',
      value: numberText(eventsTotal),
      meta: eventsTotal ? `${globalFlowDotDuration.value} / 圈 · 最近 ${formatTime(events.value[0]?.created_at)}` : '暂无事件，动画保持待机',
      icon: eventsTotal ? 'spark' : 'play',
      tone: eventsTotal ? 'good' : 'warn',
      target: 'panel-global-map',
      score: eventsTotal ? Math.min(100, 28 + eventsTotal * 4) : 28,
      filters: events.value[0]?.source_type ? { source_type: events.value[0]?.source_type } : undefined,
      data: { heat: 'signals', events: summary.value.events, latest: events.value[0] || null },
    },
    {
      key: 'feedback_heat',
      label: '改进收益',
      value: numberText(samples + candidateTotal),
      meta: `${numberText(samples)} 样本 · ${numberText(candidateTotal)} 改进建议`,
      icon: samples ? 'beaker' : candidateTotal ? 'edit' : 'flow',
      tone: samples || candidateTotal ? 'good' : 'info',
      target: 'panel-journey',
      score: Math.min(100, 22 + samples * 12 + candidateTotal * 10),
      filters: samples ? { source_type: 'training_job' } : candidateTotal ? { candidate_status: 'open' } : undefined,
      data: { heat: 'feedback', samples, candidate_total: candidateTotal, journeys: journeys.value.slice(0, 3), manifest: manifest.value },
    },
  ]
  return signals.sort((a, b) => (rank[b.tone] + b.score) - (rank[a.tone] + a.score)).slice(0, 4)
})
const aiHeatSummary = computed(() => {
  const hottest = aiHeatSignals.value[0]
  return hottest ? `${hottest.label} · ${hottest.value}` : '暂无优先级'
})
const aiDecisionQueue = computed<AiDecisionQueueItem[]>(() => {
  const queue: AiDecisionQueueItem[] = []
  const blocker = bottleneckSections.value.find((section) => Number(section.count || 0) > 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const samples = Number(summary.value.training?.samples || manifest.value.sample_total || 0)
  const latestEvent = events.value[0]
  if (failedJobs) {
    queue.push({
      key: 'failed-jobs',
      label: '失败链路',
      value: numberText(failedJobs),
      meta: '自动处理任务失败，优先定位运行原因',
      cta: '看失败',
      icon: 'warn',
      tone: 'bad',
      target: 'panel-automation',
      score: Math.min(100, 78 + failedJobs * 8),
      data: { priority: 'failed_jobs', jobs: automation.value.jobs },
    })
  }
  if (blocker) {
    const first = blocker.items?.[0]
    const filters = blocker.action === 'materialize'
      ? { artifact_status: 'ready' }
      : blocker.action === 'create_review'
        ? { candidate_status: 'open' }
        : undefined
    queue.push({
      key: `blocker-${blocker.key}`,
      label: '优先处理项',
      value: `+${numberText(blocker.count)}`,
      meta: `${blocker.title || '待处理'} · ${first ? bottleneckTitle(blocker.key, first) : '等待查看'}`,
      cta: blocker.action === 'materialize' ? '生成知识' : blocker.action === 'create_review' ? '审核流程' : '定位',
      icon: blocker.severity === 'danger' ? 'warn' : 'search',
      tone: blocker.severity === 'danger' ? 'bad' : 'warn',
      target: 'panel-bottleneck',
      score: Math.min(100, 72 + Number(blocker.count || 0) * 8),
      filters,
      data: { priority: 'bottleneck', section: blocker },
    })
  }
  if (readyAssets) {
    queue.push({
      key: 'ready-assets',
      label: '待生成知识',
      value: numberText(readyAssets),
      meta: '资产可进入知识 / 样本 / Agent 记忆',
      cta: '看队列',
      icon: 'database',
      tone: readyAssets > 6 ? 'warn' : 'info',
      target: 'panel-automation',
      score: Math.min(100, 50 + readyAssets * 7),
      filters: { artifact_status: 'ready' },
      data: { priority: 'materialize', ready_assets: readyAssets, backlog: automation.value.backlog },
    })
  }
  if (reviewRequired) {
    queue.push({
      key: 'review-required',
      label: '待审核事项',
      value: numberText(reviewRequired),
      meta: '需要人工确认，避免系统超出权限发布',
      cta: '查看审核',
      icon: 'inbox',
      tone: 'warn',
      target: 'panel-bottleneck',
      score: Math.min(100, 58 + reviewRequired * 9),
      filters: { candidate_status: 'open' },
      data: { priority: 'review_required', review_required: reviewRequired, backlog: automation.value.backlog },
    })
  }
  if (samples) {
    queue.push({
      key: 'training-samples',
      label: '训练记录',
      value: numberText(samples),
      meta: '样本可用于结果复核，用于验证 Skill / Agent 改进',
      cta: '查看记录',
      icon: 'beaker',
      tone: 'good',
      target: 'panel-journey',
      score: Math.min(100, 42 + samples * 6),
      filters: { source_type: 'training_job' },
      data: { priority: 'training', samples, manifest: manifest.value },
    })
  }
  if (latestEvent) {
    queue.push({
      key: 'latest-event',
      label: '最新记录',
      value: latestEvent.event_type || 'event',
      meta: `${sourceTypeText(latestEvent.source_type)} · ${formatTime(latestEvent.created_at)}`,
      cta: '查看',
      icon: 'spark',
      tone: 'good',
      target: 'panel-river',
      score: 46,
      filters: latestEvent.source_type ? { source_type: latestEvent.source_type } : undefined,
      data: { priority: 'latest_event', event: latestEvent },
    })
  }
  if (!queue.length) {
    queue.push({
      key: 'standby',
      label: '全局检查',
      value: pulseHealth.value.main,
      meta: '暂无阻塞，保持自动处理建议和显示状态',
      cta: '检查',
      icon: 'flow',
      tone: 'good',
      target: 'panel-global-map',
      score: 32,
      data: { priority: 'standby', health: pulseHealth.value },
    })
  }
  return queue.sort((a, b) => b.score - a.score).slice(0, 4)
})
const aiDecisionQueueSummary = computed(() => {
  const top = aiDecisionQueue.value[0]
  return top ? `${top.label} · ${top.value}` : '暂无队列'
})
const aiStickyCommand = computed<AiStickyCommand>(() => {
  const topDecision = aiDecisionQueue.value[0]
  if (topDecision) {
    return {
      kicker: '建议操作',
      label: topDecision.label,
      value: topDecision.value,
      meta: topDecision.meta,
      cta: topDecision.cta,
      icon: topDecision.icon,
      tone: topDecision.tone,
      source: 'decision',
      target: topDecision.target,
      score: topDecision.score,
      motion: topDecision.tone === 'bad' || topDecision.tone === 'warn' ? 'trace' : 'auto',
      filters: topDecision.filters,
      data: { command: 'top_decision', decision: topDecision },
    }
  }
  const primary = aiAutopilotPrimary.value
  return {
    kicker: '检查建议',
    label: primary.label,
    value: primary.cta,
    meta: primary.meta,
    cta: primary.cta,
    icon: primary.icon,
    tone: primary.tone,
    source: 'autopilot',
    target: primary.target,
    score: primary.tone === 'bad' ? 88 : primary.tone === 'warn' ? 74 : primary.tone === 'info' ? 58 : 44,
    motion: primary.tone === 'bad' || primary.tone === 'warn' ? 'trace' : primary.kind === 'refresh' ? 'calm' : 'auto',
    filters: primary.filters,
    data: { command: 'autopilot_primary', primary },
  }
})
const aiStickyCommandDisabled = computed(() => {
  if (aiStickyCommand.value.source === 'autopilot') return loading.value || backfilling.value || automationRunning.value
  return false
})
const aiStickyCommandChips = computed<AiStickyCommandChip[]>(() => {
  const command = aiStickyCommand.value
  return [
    {
      key: 'target',
      label: '定位',
      value: targetPreviewText(command.target),
      icon: 'search',
      tone: command.tone,
    },
    {
      key: 'focus',
      label: '聚焦',
      value: filterPatchPreviewText(command.filters),
      icon: command.filters ? 'search' : 'flow',
      tone: command.filters ? 'info' : 'good',
    },
    {
      key: 'motion',
      label: '显示效果',
      value: motionPreviewText(command.motion),
      icon: command.motion === 'trace' ? 'search' : command.motion === 'calm' ? 'shield' : 'spark',
      tone: command.motion === 'trace' ? 'warn' : 'good',
    },
    {
      key: 'guardrail',
      label: '安全说明',
      value: command.tone === 'bad' || command.tone === 'warn' ? '先定位' : '不变更权限',
      icon: 'shield',
      tone: command.tone === 'bad' ? 'bad' : command.tone === 'warn' ? 'warn' : 'good',
    },
  ]
})
const aiStickyCommandReasons = computed<AiStickyCommandReason[]>(() => {
  const command = aiStickyCommand.value
  const isMaterialize = command.target === 'panel-automation' || command.filters?.artifact_status === 'ready'
  const isGovernance = command.target === 'panel-bottleneck' || command.filters?.candidate_status === 'open'
  const evidence = isMaterialize
    ? aiEvidenceSignals.value.find((item) => item.key === 'materialization_evidence')
    : isGovernance
      ? aiEvidenceSignals.value.find((item) => item.key === 'governance_evidence')
      : aiEvidenceSignals.value[0]
  const cause = isMaterialize
    ? aiRootCauses.value.find((item) => item.key === 'assets-not-materialized')
    : isGovernance
      ? aiRootCauses.value.find((item) => item.key === 'governance-open' || item.key === 'failed-automation')
      : aiRootCauses.value[0]
  const impact = isMaterialize
    ? aiImpactForecasts.value.find((item) => item.key === 'materialize-impact')
    : isGovernance
      ? aiImpactForecasts.value.find((item) => item.key === 'governance-impact')
      : aiImpactForecasts.value[0]
  return [
    evidence && {
      key: `evidence-${evidence.key}`,
      label: '证据',
      value: `${evidence.label} ${evidence.value}`,
      icon: evidence.icon,
      tone: evidence.tone,
    },
    cause && {
      key: `cause-${cause.key}`,
      label: '根因',
      value: `${cause.label} ${cause.score}%`,
      icon: cause.icon,
      tone: cause.tone,
    },
    impact && {
      key: `impact-${impact.key}`,
      label: '收益',
      value: impact.value,
      icon: impact.icon,
      tone: impact.tone,
    },
  ].filter(Boolean) as AiStickyCommandReason[]
})
const aiStickyCommandConfidence = computed<AiStickyCommandConfidence>(() => {
  const command = aiStickyCommand.value
  const reasonBoost = aiStickyCommandReasons.value.length * 7
  const focusBoost = command.filters ? 6 : 2
  const riskPenalty = command.tone === 'bad' ? 12 : command.tone === 'warn' ? 6 : 0
  const score = Math.max(32, Math.min(96, Math.round(command.score * 0.72 + reasonBoost + focusBoost - riskPenalty)))
  const tone: AiStickyCommandConfidence['tone'] = score >= 78 ? 'good' : score >= 62 ? 'info' : score >= 46 ? 'warn' : 'bad'
  return {
    score,
    label: score >= 78 ? '证据充足' : score >= 62 ? '可执行' : score >= 46 ? '先查看说明' : '需复核',
    meta: `${aiStickyCommandReasons.value.length} 条依据 · ${command.filters ? '已带筛选' : '全局视角'}`,
    tone,
  }
})
const aiStickyCommandSideEffect = computed(() => {
  const command = aiStickyCommand.value
  const primary = aiAutopilotPrimary.value
  if (command.source === 'decision') {
    return {
      value: '只定位',
      meta: '只切换筛选并打开详情，不会自动发布或变更上线状态',
      icon: 'shield',
      tone: 'good' as const,
    }
  }
  if (primary.kind === 'capture') {
    return {
      value: '会同步记录',
      meta: '同步系统记录并生成学习记录，不发布 Skill',
      icon: 'play',
      tone: 'warn' as const,
    }
  }
  if (primary.kind === 'refresh') {
    return {
      value: '只刷新',
      meta: '重新拉取可见数据，不写入业务对象',
      icon: 'refresh',
      tone: 'good' as const,
    }
  }
  if (primary.kind === 'govern') {
    return {
      value: '只定位',
      meta: '展开待审核事项，不绕过审核状态机',
      icon: 'inbox',
      tone: primary.tone,
    }
  }
  if (primary.kind === 'training') {
    return {
      value: '只查看记录',
      meta: '定位训练样本和链路，不新建训练任务',
      icon: 'beaker',
      tone: 'info' as const,
    }
  }
  return {
    value: '看队列',
    meta: '只定位可生成知识内容，后续操作仍需显式确认',
    icon: 'database',
    tone: 'info' as const,
  }
})
const aiExecutionGuardrails = computed<AiExecutionGuardrail[]>(() => {
  const command = aiStickyCommand.value
  const sideEffect = aiStickyCommandSideEffect.value
  const confidence = aiStickyCommandConfidence.value
  const riskTone = command.tone === 'bad' ? 'bad' : command.tone === 'warn' ? 'warn' : 'good'
  return [
    {
      key: 'scope',
      label: '范围',
      value: targetPreviewText(command.target),
      meta: command.filters ? `带筛选：${filterPatchPreviewText(command.filters)}` : '全局视角，不隐藏权限校验',
      icon: 'search',
      tone: command.filters ? 'info' : 'good',
      target: command.target,
      data: { guardrail: 'scope', target: command.target, filters: command.filters || {} },
    },
    {
      key: 'write',
      label: '影响范围',
      value: sideEffect.value,
      meta: sideEffect.meta,
      icon: sideEffect.icon,
      tone: sideEffect.tone,
      target: command.target,
      data: { guardrail: 'side_effect', source: command.source, command: command.data },
    },
    {
      key: 'policy',
      label: '规则',
      value: riskTone === 'good' ? '不变更权限' : '先诊断',
      meta: '不会发布 Skill、改变审核状态或读取未授权数据',
      icon: 'shield',
      tone: riskTone,
      target: 'panel-bottleneck',
      data: { guardrail: 'policy', tone: command.tone, source: command.source },
    },
    {
      key: 'trace',
      label: '查看',
      value: motionPreviewText(command.motion),
      meta: `${confidence.score}% 把握 · 操作记录可查看`,
      icon: command.motion === 'trace' ? 'search' : 'flow',
      tone: confidence.tone,
      target: command.target,
      data: { guardrail: 'traceability', confidence, motion: command.motion || 'auto' },
    },
  ]
})
const aiExecutionGuardrailSummary = computed(() => {
  const write = aiStickyCommandSideEffect.value.value
  return `${aiStickyCommandConfidence.value.label} · ${write}`
})
const aiExecutionGuardrailStyle = computed<Record<string, string>>(() => ({
  '--guardrail-confidence': `${aiStickyCommandConfidence.value.score}%`,
  '--guardrail-risk': aiStickyCommand.value.tone === 'bad' ? '100%' : aiStickyCommand.value.tone === 'warn' ? '64%' : '28%',
}))
const pulseDecisionItems = computed<PulseDecisionItem[]>(() => {
  const command = aiStickyCommand.value
  const primaryReason = aiStickyCommandReasons.value[0]
  const sideEffect = aiStickyCommandSideEffect.value
  const topImpact = aiImpactForecasts.value[0]
  return [
    {
      key: 'action',
      label: '建议操作',
      value: command.cta,
      meta: command.label,
      icon: command.icon,
      tone: command.tone,
      target: command.target,
      filters: command.filters,
      data: { decision: 'action', command: command.data },
    },
    primaryReason && {
      key: 'reason',
      label: '主要依据',
      value: primaryReason.value,
      meta: primaryReason.label,
      icon: primaryReason.icon,
      tone: primaryReason.tone,
      target: command.target,
      filters: command.filters,
      data: { decision: 'reason', reason: primaryReason, command: command.data },
    },
    {
      key: 'scope',
      label: '执行范围',
      value: sideEffect.value,
      meta: sideEffect.meta,
      icon: sideEffect.icon,
      tone: sideEffect.tone,
      target: command.target,
      filters: command.filters,
      data: { decision: 'scope', side_effect: sideEffect, command: command.data },
    },
    topImpact && {
      key: 'impact',
      label: '预期收益',
      value: topImpact.value,
      meta: topImpact.label,
      icon: topImpact.icon,
      tone: topImpact.tone,
      target: topImpact.target,
      filters: topImpact.filters,
      data: { decision: 'impact', impact: topImpact },
    },
  ].filter(Boolean) as PulseDecisionItem[]
})
const pulseAutomationCards = computed<PulseAutomationCard[]>(() => {
  const state = automation.value.automation || {}
  const enabled = Boolean(state.enabled)
  const status = String(state.status || (enabled ? 'never_run' : 'manual'))
  const latestAt = state.finished_at || automation.value.latest?.event_at || state.started_at || ''
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const running = status === 'running'
  const statusTone: PulseDecisionItem['tone'] = !enabled
    ? 'info'
    : failedJobs || statusClass(status) === 'bad'
      ? 'bad'
      : running
        ? 'info'
        : 'good'
  return [
    {
      key: 'automation-state',
      label: '自动处理状态',
      value: enabled ? statusText(status) : '手动模式',
      meta: latestAt ? `最近 ${formatTime(latestAt)}` : enabled ? '等待首次自动处理' : '仅手动同步与知识生成',
      icon: running ? 'refresh' : enabled ? 'spark' : 'play',
      tone: statusTone,
      target: 'panel-automation',
      level: aiHeartbeat.value.score,
      data: { automation_state: 'status', enabled, status, latest_at: latestAt, automation: state },
    },
    {
      key: 'automation-knowledge',
      label: '待生成知识',
      value: numberText(readyAssets),
      meta: readyAssets ? '可转入知识库、训练样本或 Agent 记忆' : '暂无待生成内容',
      icon: readyAssets ? 'database' : 'check',
      tone: readyAssets ? 'info' : 'good',
      target: 'panel-automation',
      filters: readyAssets ? { artifact_status: 'ready' } : undefined,
      level: readyAssets ? Math.min(100, 34 + readyAssets * 11) : 100,
      data: { automation_state: 'knowledge_queue', ready_assets: readyAssets, backlog: automation.value.backlog },
    },
    {
      key: 'automation-review',
      label: '待审核',
      value: numberText(reviewRequired),
      meta: reviewRequired ? '需人工确认，不会自动发布' : '无审核阻塞',
      icon: reviewRequired ? 'inbox' : 'shield',
      tone: reviewRequired ? 'warn' : 'good',
      target: reviewRequired ? 'panel-bottleneck' : 'panel-automation',
      filters: reviewRequired ? { candidate_status: 'open' } : undefined,
      level: reviewRequired ? Math.min(100, 28 + reviewRequired * 18) : 100,
      data: { automation_state: 'review_queue', review_required: reviewRequired, backlog: automation.value.backlog },
    },
    {
      key: 'automation-failed',
      label: '失败任务',
      value: numberText(failedJobs),
      meta: failedJobs ? '优先排查自动处理任务' : '自动处理正常',
      icon: failedJobs ? 'warn' : 'check',
      tone: failedJobs ? 'bad' : 'good',
      target: failedJobs ? 'panel-bottleneck' : 'panel-automation',
      filters: failedJobs ? { candidate_status: 'open' } : undefined,
      level: failedJobs ? Math.min(100, 36 + failedJobs * 18) : 100,
      data: { automation_state: 'failed_jobs', failed_jobs: failedJobs, jobs: automation.value.jobs },
    },
  ]
})
const pulseAutomationTone = computed<PulseDecisionItem['tone']>(() => {
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  if (failedJobs) return 'bad'
  if (reviewRequired) return 'warn'
  if (readyAssets) return 'info'
  return 'good'
})
const pulseAutomationSummary = computed(() => {
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  if (failedJobs) return `${numberText(failedJobs)} 个失败任务需优先排查`
  if (reviewRequired) return `${numberText(reviewRequired)} 项待审核，不会自动发布`
  if (readyAssets) return `${numberText(readyAssets)} 个内容可生成知识`
  return '自动处理正常，暂无人工介入事项'
})
const aiMissionBrief = computed<AiMissionBrief>(() => {
  const command = aiStickyCommand.value
  const confidence = aiStickyCommandConfidence.value
  const eventsTotal = Number(summary.value.events?.total || events.value.length || 0)
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  const samples = Number(summary.value.training?.samples || manifest.value.sample_total || 0)
  return {
    kicker: command.source === 'decision' ? '当前建议' : '自动处理建议',
    title: command.label,
    meta: `${numberText(eventsTotal)} 记录 · ${numberText(blockerTotal)} 待处理事项 · ${numberText(readyAssets)} 待生成知识 · ${numberText(samples)} 样本`,
    output: `${command.cta} → ${targetPreviewText(command.target)} · ${confidence.label}`,
    icon: command.icon,
    tone: command.tone,
    confidence: confidence.score,
    target: command.target,
    data: {
      mission: 'current_turn',
      command,
      confidence,
      events_total: eventsTotal,
      blockers: blockerTotal,
      ready_assets: readyAssets,
      samples,
    },
  }
})
const aiMissionSteps = computed<AiMissionStep[]>(() => {
  const command = aiStickyCommand.value
  const confidence = aiStickyCommandConfidence.value
  const sideEffect = aiStickyCommandSideEffect.value
  const topDecision = aiDecisionQueue.value[0]
  const latestEvent = events.value[0]
  const eventsTotal = Number(summary.value.events?.total || events.value.length || 0)
  const samples = Number(summary.value.training?.samples || manifest.value.sample_total || 0)
  const candidateTotal = sumRecord(summary.value.candidates?.by_target || {}) || candidates.value.length
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  return [
    {
      key: 'sense',
      label: '数据同步',
      value: eventsTotal ? `${numberText(eventsTotal)} 记录` : '待同步',
      meta: latestEvent ? `${sourceTypeText(latestEvent.source_type)} · ${formatTime(latestEvent.created_at)}` : '等待运行 / SF / Agent 记录',
      icon: eventsTotal ? 'spark' : 'play',
      tone: eventsTotal ? 'good' : 'warn',
      status: eventsTotal ? 'captured' : 'empty',
      progress: Math.min(100, eventsTotal ? 32 + eventsTotal * 4 : 18),
      target: latestEvent ? 'panel-river' : 'panel-global-map',
      filters: latestEvent?.source_type ? { source_type: latestEvent.source_type } : undefined,
      data: { step: 'sense', events: summary.value.events, latest_event: latestEvent || null },
    },
    {
      key: 'judge',
      label: '风险判断',
      value: topDecision ? `${topDecision.score}%` : '待排序',
      meta: topDecision ? `${topDecision.label} · ${topDecision.value}` : '等待建议优先级',
      icon: topDecision?.icon || 'search',
      tone: topDecision?.tone || 'info',
      status: topDecision ? 'ranked' : 'standby',
      progress: topDecision?.score || 24,
      target: 'ai-side-decision',
      filters: topDecision?.filters,
      data: { step: 'judge', decision: topDecision || null, queue: aiDecisionQueue.value },
    },
    {
      key: 'execute',
      label: '建议操作',
      value: command.cta,
      meta: `${sideEffect.value} · ${targetPreviewText(command.target)}`,
      icon: command.icon,
      tone: sideEffect.tone,
      status: command.source,
      progress: confidence.score,
      target: command.target,
      filters: command.filters,
      data: { step: 'execute', command, side_effect: sideEffect, guardrails: aiExecutionGuardrails.value },
    },
    {
      key: 'learn',
      label: '结果复核',
      value: samples ? `${numberText(samples)} 样本` : `${numberText(candidateTotal)} 建议`,
      meta: samples ? `${numberText(candidateTotal)} 建议可校验` : readyAssets ? `${numberText(readyAssets)} 资产先生成知识` : '保持处理记录',
      icon: samples ? 'beaker' : readyAssets ? 'database' : 'flow',
      tone: samples ? 'good' : readyAssets ? 'info' : 'warn',
      status: samples ? 'learnable' : 'pending',
      progress: Math.min(100, samples ? 42 + samples * 12 : readyAssets ? 36 + readyAssets * 10 : 22),
      target: samples ? 'panel-journey' : readyAssets ? 'panel-automation' : 'panel-run-brief',
      filters: samples ? { source_type: 'training_job' } : readyAssets ? { artifact_status: 'ready' } : undefined,
      data: { step: 'learn', samples, candidate_total: candidateTotal, ready_assets: readyAssets, manifest: manifest.value },
    },
  ]
})
const aiMissionActiveStep = computed<AiMissionStep['key']>(() => {
  if (!Number(summary.value.events?.total || events.value.length || 0)) return 'sense'
  if (aiStickyCommandConfidence.value.score < 62) return 'judge'
  if (aiStickyCommand.value.tone === 'bad' || aiStickyCommand.value.tone === 'warn') return 'execute'
  return 'learn'
})
const aiMissionBriefStyle = computed<Record<string, string>>(() => ({
  '--mission-confidence': `${aiMissionBrief.value.confidence}%`,
  '--mission-step-count': `${aiMissionSteps.value.length}`,
}))
const aiOperationCenterStyle = computed<Record<string, string>>(() => {
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const pressure = Math.min(100, Math.round(failedJobs * 36 + reviewRequired * 20 + readyAssets * 8 + blockerTotal * 14))
  const activeIndex = Math.max(0, aiMissionSteps.value.findIndex((step) => step.key === aiMissionActiveStep.value))
  return {
    '--operation-confidence': `${aiStickyCommandConfidence.value.score}%`,
    '--operation-active-index': `${activeIndex}`,
    '--operation-load': `${pressure ? Math.max(18, pressure) : 8}%`,
    '--operation-scan-opacity': `${failedJobs || reviewRequired || blockerTotal ? 0.62 : readyAssets ? 0.34 : 0.16}`,
  }
})
const aiOperationHeaderMeta = computed(() => {
  const state = automation.value.automation || {}
  const latestAt = state.finished_at || automation.value.latest?.event_at || state.started_at || ''
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const next = aiHeartbeat.value.next
  const latest = latestAt ? `最近 ${formatTime(latestAt)}` : '等待首次自动处理'
  if (failedJobs) return `${numberText(failedJobs)} 个失败任务 · ${latest} · 优先排查`
  if (reviewRequired) return `${numberText(reviewRequired)} 项待审核 · ${latest} · 等待人工确认`
  if (readyAssets) return `${numberText(readyAssets)} 个内容可生成知识 · 下次 ${next}`
  return `${statusText(state.status || (state.enabled ? 'never_run' : 'manual'))} · 下次 ${next}`
})
const aiOperationStatusItems = computed<AiOperationStatusItem[]>(() => {
  const state = automation.value.automation || {}
  const enabled = Boolean(state.enabled)
  const interval = Number(state.interval_seconds || 0)
  const latestAt = state.finished_at || automation.value.latest?.event_at || state.started_at || ''
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const pressureTone: AiOperationStatusItem['tone'] = failedJobs ? 'bad' : reviewRequired ? 'warn' : readyAssets ? 'info' : 'good'
  return [
    {
      key: 'operation-last-check',
      label: '上次检查',
      value: latestAt ? formatTime(latestAt) : '等待首轮',
      meta: statusText(state.status || (enabled ? 'never_run' : 'manual')),
      icon: failedJobs ? 'warn' : latestAt ? 'check' : 'play',
      tone: failedJobs ? 'bad' : latestAt ? 'good' : 'info',
      target: 'panel-automation',
      data: { operation_status: 'last_check', automation: state, latest: automation.value.latest },
    },
    {
      key: 'operation-next-check',
      label: '下次检查',
      value: enabled ? aiHeartbeat.value.next : '手动模式',
      meta: enabled && interval ? `每 ${numberText(interval)} 秒` : '未启用固定节奏',
      icon: enabled ? 'spark' : 'shield',
      tone: enabled ? 'info' : 'warn',
      target: 'panel-automation',
      data: { operation_status: 'next_check', enabled, interval_seconds: interval, next_at: aiHeartbeat.value.next },
    },
    {
      key: 'operation-pressure',
      label: '当前压力',
      value: failedJobs ? `${numberText(failedJobs)} 失败` : reviewRequired ? `${numberText(reviewRequired)} 待审` : readyAssets ? `${numberText(readyAssets)} 待生成` : '正常',
      meta: `${numberText(readyAssets)} 知识 · ${numberText(reviewRequired)} 审核 · ${numberText(failedJobs)} 失败`,
      icon: failedJobs || reviewRequired ? 'warn' : readyAssets ? 'database' : 'check',
      tone: pressureTone,
      target: failedJobs || reviewRequired ? 'panel-bottleneck' : readyAssets ? 'panel-automation' : 'panel-run-brief',
      filters: failedJobs || reviewRequired ? { candidate_status: 'open' } : readyAssets ? { artifact_status: 'ready' } : undefined,
      data: { operation_status: 'pressure', ready_assets: readyAssets, review_required: reviewRequired, failed_jobs: failedJobs },
    },
  ]
})
const aiOperationDecisionCards = computed<PulseDecisionItem[]>(() => {
  const command = aiStickyCommand.value
  const confidence = aiStickyCommandConfidence.value
  const primaryReason = aiStickyCommandReasons.value[0]
  const sideEffect = aiStickyCommandSideEffect.value
  return [
    {
      key: 'operation-next-action',
      label: '下一步操作',
      value: command.cta,
      meta: `${command.label} · ${confidence.score}% 把握`,
      icon: command.icon,
      tone: command.tone,
      target: command.target,
      filters: command.filters,
      data: { operation_decision: 'next_action', command: command.data, confidence },
    },
    {
      key: 'operation-reason',
      label: '主要依据',
      value: primaryReason?.value || confidence.label,
      meta: primaryReason?.label || confidence.meta,
      icon: primaryReason?.icon || 'search',
      tone: primaryReason?.tone || confidence.tone,
      target: command.target,
      filters: command.filters,
      data: { operation_decision: 'reason', reason: primaryReason || null, command: command.data, confidence },
    },
    {
      key: 'operation-effect',
      label: '影响范围',
      value: sideEffect.value,
      meta: `${sideEffect.meta} · 不会自动发布`,
      icon: sideEffect.icon,
      tone: sideEffect.tone,
      target: command.target,
      filters: command.filters,
      data: { operation_decision: 'effect', side_effect: sideEffect, command: command.data, guardrails: aiExecutionGuardrails.value },
    },
  ]
})
const aiOperationQueueChips = computed<AiOperationQueueChip[]>(() => {
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  return [
    {
      key: 'operation-queue-knowledge',
      label: '待生成知识',
      value: numberText(readyAssets),
      meta: readyAssets ? '可转入知识 / 样本 / 记忆' : '队列清空',
      icon: readyAssets ? 'database' : 'check',
      tone: readyAssets ? 'info' : 'good',
      target: 'panel-automation',
      filters: readyAssets ? { artifact_status: 'ready' } : undefined,
      level: readyAssets ? Math.min(100, 28 + readyAssets * 13) : 100,
      data: { operation_queue: 'knowledge', ready_assets: readyAssets, backlog: automation.value.backlog },
    },
    {
      key: 'operation-queue-review',
      label: '待审核',
      value: numberText(reviewRequired),
      meta: reviewRequired ? '等待人工确认' : '无需介入',
      icon: reviewRequired ? 'inbox' : 'shield',
      tone: reviewRequired ? 'warn' : 'good',
      target: reviewRequired ? 'panel-bottleneck' : 'panel-automation',
      filters: reviewRequired ? { candidate_status: 'open' } : undefined,
      level: reviewRequired ? Math.min(100, 32 + reviewRequired * 18) : 100,
      data: { operation_queue: 'review', review_required: reviewRequired, backlog: automation.value.backlog },
    },
    {
      key: 'operation-queue-failed',
      label: '失败任务',
      value: numberText(failedJobs),
      meta: failedJobs ? '优先排查' : '运行正常',
      icon: failedJobs ? 'warn' : 'check',
      tone: failedJobs ? 'bad' : 'good',
      target: failedJobs ? 'panel-bottleneck' : 'panel-automation',
      filters: failedJobs ? { candidate_status: 'open' } : undefined,
      level: failedJobs ? Math.min(100, 40 + failedJobs * 20) : 100,
      data: { operation_queue: 'failed', failed_jobs: failedJobs, jobs: automation.value.jobs },
    },
  ]
})
const aiCommandPlanSteps = computed<AiCommandPlanStep[]>(() => {
  const topDecision = aiDecisionQueue.value[0]
  const primary = aiAutopilotPrimary.value
  const topImpact = aiImpactForecasts.value[0]
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const guardrailRisk = failedJobs + reviewRequired
  const steps: AiCommandPlanStep[] = []
  if (topDecision) {
    steps.push({
      key: `decide-${topDecision.key}`,
      phase: '风险判断',
      label: topDecision.label,
      value: topDecision.value,
      meta: topDecision.meta,
      cta: topDecision.cta,
      icon: topDecision.icon,
      tone: topDecision.tone,
      target: topDecision.target,
      score: topDecision.score,
      motion: topDecision.tone === 'bad' || topDecision.tone === 'warn' ? 'trace' : undefined,
      filters: topDecision.filters,
      data: { step: 'decide', decision: topDecision },
    })
  }
  steps.push({
    key: `execute-${primary.key}`,
    phase: '建议操作',
    label: primary.label.replace(/^(AI )?建议/, '').trim() || primary.label,
    value: primary.cta,
    meta: primary.meta,
    cta: primary.cta,
    icon: primary.icon,
    tone: primary.tone,
    target: primary.target,
    score: primary.tone === 'bad' ? 88 : primary.tone === 'warn' ? 74 : primary.tone === 'info' ? 58 : 44,
    motion: primary.tone === 'bad' || primary.tone === 'warn' ? 'trace' : primary.kind === 'refresh' ? 'calm' : 'auto',
    filters: primary.filters,
    data: { step: 'execute', action: primary },
  })
  steps.push({
    key: 'guardrail',
    phase: '安全说明',
    label: guardrailRisk ? '需要人工确认' : blockerTotal ? '审核安全观察' : '可自动推进',
    value: failedJobs ? `${numberText(failedJobs)} 失败` : reviewRequired ? `${numberText(reviewRequired)} 待审` : '安全',
    meta: guardrailRisk
      ? '失败或审核项不会被系统超出权限发布，先定位再交给审核流程'
      : blockerTotal
        ? '存在待处理事项但无超出权限的操作，优先展示证据和影响'
        : '当前操作限定为检查、定位和授权生成知识',
    cta: guardrailRisk || blockerTotal ? '查看审核' : '看状态',
    icon: guardrailRisk || blockerTotal ? 'inbox' : 'shield',
    tone: failedJobs ? 'bad' : guardrailRisk || blockerTotal ? 'warn' : 'good',
    target: guardrailRisk || blockerTotal ? 'panel-bottleneck' : 'panel-automation',
    score: failedJobs ? 92 : reviewRequired || blockerTotal ? 78 : 40,
    motion: guardrailRisk || blockerTotal ? 'trace' : 'calm',
    filters: guardrailRisk || blockerTotal ? { candidate_status: 'open' } : undefined,
    data: { step: 'guardrail', review_required: reviewRequired, failed_jobs: failedJobs, blocker_total: blockerTotal },
  })
  if (topImpact) {
    steps.push({
      key: `verify-${topImpact.key}`,
      phase: '验证',
      label: topImpact.label,
      value: topImpact.value,
      meta: topImpact.meta,
      cta: '看收益',
      icon: topImpact.icon,
      tone: topImpact.tone,
      target: topImpact.target,
      score: topImpact.score,
      motion: topImpact.tone === 'bad' || topImpact.tone === 'warn' ? 'trace' : undefined,
      filters: topImpact.filters,
      data: { step: 'verify', impact: topImpact },
    })
  }
  return steps.slice(0, 4)
})
const aiCommandPlanSummary = computed(() => {
  const risk = aiCommandPlanSteps.value.find((step) => step.tone === 'bad' || step.tone === 'warn')
  return risk ? `${risk.phase} · ${risk.label}` : '可安全自动推进'
})
const aiCommandPlanStyle = computed<Record<string, string>>(() => {
  const steps = aiCommandPlanSteps.value
  const avg = steps.length ? steps.reduce((sum, step) => sum + step.score, 0) / steps.length : 0
  return { '--command-plan-progress': `${Math.max(18, Math.min(100, Math.round(avg)))}%` }
})
const aiPlaybooks = computed<AiPlaybook[]>(() => {
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  const samples = Number(summary.value.training?.samples || manifest.value.sample_total || 0)
  const eventsTotal = Number(summary.value.events?.total || events.value.length || 0)
  return [
    {
      key: 'risk',
      label: '优先处理风险',
      meta: failedJobs ? `${numberText(failedJobs)} 失败优先` : `${numberText(blockerTotal + reviewRequired)} 审核项`,
      cta: '查看',
      icon: failedJobs || blockerTotal ? 'warn' : 'shield',
      tone: failedJobs ? 'bad' : blockerTotal || reviewRequired ? 'warn' : 'good',
      target: 'panel-bottleneck',
      score: Math.min(100, failedJobs * 28 + blockerTotal * 16 + reviewRequired * 14 + 18),
      motion: 'trace',
      filters: { candidate_status: 'open' },
      data: { playbook: 'risk', blockers: bottlenecks.value, backlog: automation.value.backlog, failed_jobs: failedJobs },
    },
    {
      key: 'materialize',
      label: '知识生成',
      meta: readyAssets ? `${numberText(readyAssets)} 个资产待生成知识` : '资产队列无待办',
      cta: '生成知识',
      icon: 'database',
      tone: readyAssets ? 'info' : 'good',
      target: 'panel-automation',
      score: Math.min(100, readyAssets * 15 + 24),
      motion: readyAssets ? 'trace' : 'auto',
      filters: { artifact_status: 'ready' },
      data: { playbook: 'materialize', ready_assets: readyAssets, backlog: automation.value.backlog, artifacts: artifacts.value.slice(0, 5) },
    },
    {
      key: 'training',
      label: '训练采样',
      meta: samples ? `${numberText(samples)} 样本可查看记录` : '查看训练缺口',
      cta: '采样',
      icon: 'beaker',
      tone: samples ? 'good' : 'info',
      target: 'panel-journey',
      score: Math.min(100, samples * 14 + 18),
      motion: 'trace',
      filters: { source_type: 'training_job' },
      data: { playbook: 'training', samples, manifest: manifest.value, journeys: journeys.value.slice(0, 3) },
    },
    {
      key: 'patrol',
      label: '全局检查',
      meta: eventsTotal ? `${numberText(eventsTotal)} 记录全局查看` : '回到等待数据',
      cta: '全局',
      icon: 'flow',
      tone: eventsTotal ? 'good' : 'warn',
      target: 'panel-global-map',
      score: Math.min(100, eventsTotal * 4 + 28),
      motion: 'auto',
      reset: true,
      data: { playbook: 'patrol', health: pulseHealth.value, summary: summary.value },
    },
  ]
})
const aiPlaybookSummary = computed(() => `${aiPlaybooks.value.length} 套可执行`)
const aiSideSectionAnchors = computed<AiSideSectionAnchor[]>(() => {
  const riskRootCauses = aiRootCauses.value.filter((cause) => cause.tone === 'bad' || cause.tone === 'warn').length
  const topDecision = aiDecisionQueue.value[0]
  const topImpact = aiImpactForecasts.value[0]
  return [
    {
      id: 'ai-side-heartbeat',
      label: '状态',
      value: `${aiHeartbeat.value.score}`,
      icon: 'spark',
      tone: aiHeartbeat.value.tone,
    },
    {
      id: 'ai-side-decision',
      label: '决策',
      value: topDecision?.value || '0',
      icon: topDecision?.icon || 'search',
      tone: topDecision?.tone || 'info',
    },
    {
      id: 'ai-side-orchestrator',
      label: '编排',
      value: aiCommandPlanSteps.value[0]?.phase || '待机',
      icon: 'flow',
      tone: aiCommandPlanSteps.value.some((step) => step.tone === 'bad')
        ? 'bad'
        : aiCommandPlanSteps.value.some((step) => step.tone === 'warn')
          ? 'warn'
          : 'good',
    },
    {
      id: 'ai-side-guardrail',
      label: '安全说明',
      value: aiStickyCommandSideEffect.value.value,
      icon: 'shield',
      tone: aiStickyCommandConfidence.value.tone,
    },
    {
      id: 'ai-side-rootcause',
      label: '根因',
      value: riskRootCauses ? `${riskRootCauses} 风险` : '稳定',
      icon: riskRootCauses ? 'warn' : 'shield',
      tone: aiRootCauses.value.some((cause) => cause.tone === 'bad') ? 'bad' : riskRootCauses ? 'warn' : 'good',
    },
    {
      id: 'ai-side-impact',
      label: '收益',
      value: topImpact?.value || '检查',
      icon: topImpact?.icon || 'flow',
      tone: topImpact?.tone || 'info',
    },
    {
      id: 'ai-side-playbook',
      label: '方案',
      value: `${aiPlaybooks.value.length} 套`,
      icon: 'flow',
      tone: aiPlaybooks.value.some((item) => item.tone === 'bad' || item.tone === 'warn') ? 'warn' : 'good',
    },
    {
      id: 'ai-side-focus',
      label: '聚焦',
      value: activeFilterChips.value.length ? `${activeFilterChips.value.length} 个` : '全局',
      icon: 'search',
      tone: activeFilterChips.value.length ? 'info' : 'good',
    },
  ]
})
const activeAiSideAnchor = computed(() => {
  const active = aiSideSectionAnchors.value.find((item) => item.id === activeAiSideSection.value)
  if (active && (aiSideToolsExpanded.value || !AI_SIDE_UTILITY_SECTION_IDS.has(active.id))) return active
  return aiSideSectionAnchors.value.find((item) => !AI_SIDE_UTILITY_SECTION_IDS.has(item.id)) || aiSideSectionAnchors.value[0]
})
const aiSideHudCards = computed<AiSideHudCard[]>(() => {
  const activeAnchor = activeAiSideAnchor.value
  const topDecision = aiDecisionQueue.value[0]
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const autoQueue = Number(automation.value.backlog?.auto_materializable || 0)
  const reviewQueue = Number(automation.value.backlog?.review_required || 0)
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const pressure = failedJobs + autoQueue + reviewQueue + blockerTotal
  return [
    {
      key: 'active-section',
      label: '当前视窗',
      value: activeAnchor?.label || '状态',
      meta: activeAnchor ? `${activeAnchor.value} · ${Math.round(aiSideScrollProgress.value)}% 浏览` : '侧栏定位',
      icon: activeAnchor?.icon || 'search',
      tone: activeAnchor?.tone || 'info',
      target: activeAnchor?.id || 'ai-side-heartbeat',
      targetScope: 'side',
      data: { hud: 'active_section', section: activeAnchor, scroll_progress: aiSideScrollProgress.value },
    },
    {
      key: 'next-best',
      label: '最优下一步',
      value: topDecision?.cta || aiAutopilotPrimary.value.cta,
      meta: topDecision ? `${topDecision.label} · ${topDecision.value}` : aiAutopilotPrimary.value.meta,
      icon: topDecision?.icon || aiAutopilotPrimary.value.icon,
      tone: topDecision?.tone || aiAutopilotPrimary.value.tone,
      target: 'ai-side-orchestrator',
      targetScope: 'side',
      data: { hud: 'next_best_action', decision: topDecision || null, primary: aiAutopilotPrimary.value },
    },
    {
      key: 'pressure',
      label: '队列压力',
      value: numberText(pressure),
      meta: `${numberText(autoQueue)} 生成知识 · ${numberText(reviewQueue)} 待审 · ${numberText(failedJobs)} 失败`,
      icon: pressure ? 'warn' : 'shield',
      tone: failedJobs ? 'bad' : reviewQueue || blockerTotal ? 'warn' : autoQueue ? 'info' : 'good',
      target: pressure ? 'ai-side-decision' : 'ai-side-heartbeat',
      targetScope: 'side',
      data: { hud: 'queue_pressure', auto_queue: autoQueue, review_queue: reviewQueue, failed_jobs: failedJobs, blocker_total: blockerTotal },
    },
    {
      key: 'motion',
      label: '显示状态',
      value: activeMotionModeLabel.value,
      meta: `${Math.round(effectiveMotionIntensity.value * 100)}% 强度 · ${globalFlowDotDuration.value} / 圈`,
      icon: motionMode.value === 'trace' ? 'search' : motionMode.value === 'calm' ? 'shield' : 'spark',
      tone: motionMode.value === 'trace' ? 'warn' : motionMode.value === 'calm' ? 'good' : effectiveMotionIntensity.value > 0.62 ? 'info' : 'good',
      target: 'ai-side-motion',
      targetScope: 'side',
      data: { hud: 'motion_state', mode: motionMode.value, intensity: effectiveMotionIntensity.value, duration: globalFlowDotDuration.value },
    },
  ]
})

const kpis = computed(() => {
  const artifactKinds = summary.value.artifacts?.by_kind || {}
  const candidateStatus = summary.value.candidates?.by_status || {}
  return [
    { key: 'events', label: '同步最新记录', value: numberText(summary.value.events?.total), meta: `${filters.days} 天内标准记录` },
    { key: 'knowledge', label: '已入库知识', value: numberText(summary.value.knowledge?.indexed), meta: '报告 / 结果复核 / 结论' },
    { key: 'samples', label: '训练样本', value: numberText(summary.value.training?.samples), meta: 'SFT / 偏好 / 评测' },
    { key: 'agent', label: 'Agent 记忆', value: numberText(summary.value.agent?.memories), meta: `${numberText(summary.value.agent?.used_context_edges)} 次知识引用` },
    { key: 'sf', label: 'SF 迭代', value: numberText(summary.value.candidates?.by_target?.sf), meta: 'MCP / 命令 / 报告缺口' },
    {
      key: 'self_iteration',
      label: 'AI 自迭代健康',
      value: numberText(summary.value.self_iteration?.health_score),
      meta: `${numberText(summary.value.self_iteration?.ai_system_gaps_open)} 个缺口 · ${numberText(summary.value.self_iteration?.draft_validation_failed)} 个草稿阻断 · ${numberText(summary.value.self_iteration?.governance_tasks_pending)} 个治理待办`,
    },
    { key: 'blocked', label: '待处理', value: numberText((artifactKinds.knowledge_note || 0) + (candidateStatus.open || 0)), meta: '可生成知识内容 + 开放建议' },
  ]
})
const quickFocusFilters = computed<QuickFocusFilter[]>(() => [
  {
    key: 'all',
    label: '整体状态',
    meta: `${numberText(summary.value.events?.total)} 条记录`,
    icon: 'flow',
    tone: 'source',
    filters: {},
  },
  {
    key: 'skill',
    label: 'Skill 运行',
    meta: '看运行入口',
    icon: 'cube',
    tone: 'source',
    filters: { source_type: 'execution_run' },
  },
  {
    key: 'sf',
    label: 'SF / MCP',
    meta: `${numberText(summary.value.candidates?.by_target?.sf)} 个改进建议`,
    icon: 'bolt',
    tone: 'event',
    filters: { source_type: 'sf_mcp_call' },
  },
  {
    key: 'blockers',
    label: '待审核',
    meta: `${numberText(bottlenecks.value.summary?.total)} 个待处理事项`,
    icon: 'warn',
    tone: 'danger',
    filters: { candidate_status: 'open' },
  },
  {
    key: 'training',
    label: '训练样本',
    meta: `${numberText(summary.value.training?.samples || manifest.value.sample_total)} 个样本`,
    icon: 'beaker',
    tone: 'artifact',
    filters: { source_type: 'training_job' },
  },
])
const aiPerspectives = computed<AiPerspective[]>(() => {
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  return [
    {
      key: 'inspect',
      label: '检查',
      meta: `${numberText(summary.value.events?.total || events.value.length)} 记录`,
      icon: 'check',
      tone: 'good',
      target: 'panel-run-brief',
      filters: {},
      data: { intent: 'inspect', health: pulseHealth.value, summary: summary.value },
    },
    {
      key: 'govern',
      label: '审核流程',
      meta: `${numberText(blockerTotal + reviewRequired)} 待处理`,
      icon: blockerTotal ? 'warn' : 'shield',
      tone: blockerTotal || reviewRequired ? 'warn' : 'good',
      target: 'panel-bottleneck',
      filters: { candidate_status: 'open' },
      data: { intent: 'govern', bottlenecks: bottlenecks.value, review_required: reviewRequired },
    },
    {
      key: 'materialize',
      label: '生成知识',
      meta: `${numberText(readyAssets)} 可生成知识`,
      icon: 'database',
      tone: readyAssets ? 'info' : 'good',
      target: 'panel-automation',
      filters: { artifact_status: 'ready' },
      data: { intent: 'materialize', backlog: automation.value.backlog, artifacts: artifacts.value.slice(0, 5) },
    },
    {
      key: 'training',
      label: '训练',
      meta: `${numberText(summary.value.training?.samples || manifest.value.sample_total)} 样本`,
      icon: 'beaker',
      tone: 'artifact',
      target: 'panel-journey',
      filters: { source_type: 'training_job' },
      data: { intent: 'training', training: summary.value.training, manifest: manifest.value },
    },
    {
      key: 'sf',
      label: 'SF',
      meta: `${numberText(summary.value.candidates?.by_target?.sf)} 改进建议`,
      icon: 'bolt',
      tone: 'source',
      target: 'panel-river',
      filters: { source_type: 'sf_mcp_call' },
      data: { intent: 'sf', candidates: summary.value.candidates, events: events.value.slice(0, 5) },
    },
  ]
})
const activeAiPerspectiveLabel = computed(() => aiPerspectives.value.find((view) => isAiPerspectiveActive(view))?.label || '自定义聚焦')
const activeFilterChips = computed(() => {
  const labelMap: Record<string, string> = {
    days: '时间',
    skill_id: 'Skill',
    department: '部门',
    run_id: 'Run',
    source_type: '来源',
    event_type: '事件',
    artifact_kind: '资产',
    target_type: '建议',
    sf_tool: 'SF 工具',
    q: '关键词',
    artifact_status: '资产状态',
    candidate_status: '建议状态',
  }
  return Object.entries(filters)
    .filter(([key, value]) => key !== 'days' && value !== undefined && value !== null && String(value) !== '')
    .map(([key, value]) => ({ key, label: labelMap[key] || key, value: String(value) }))
})
const sideQuickFocusFilters = computed(() => quickFocusFilters.value.filter((focus) => focus.key !== 'all').slice(0, 4))
const aiFocusCommandSummary = computed(() => activeFilterChips.value.length ? `${activeFilterChips.value.length} 个锁定` : '推荐聚焦')
const aiFocusNarrative = computed(() => {
  if (!activeFilterChips.value.length) return '未锁定筛选，点击快捷入口即可同步地图与动画'
  return activeFilterChips.value.map((chip) => `${chip.label}=${chip.value}`).join(' · ')
})
const systemBriefItems = computed<BriefItem[]>(() => {
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const nextBottleneck = bottleneckSections.value.find((section) => Number(section.count || 0) > 0)
  const latestEvent = events.value[0]
  return [
    {
      key: 'status',
      label: '当前评估',
      value: pulseHealth.value.title,
      meta: pulseHealth.value.detail,
      action: blockerTotal || failedJobs ? '先处理异常' : '保持观察',
      tone: pulseHealth.value.tone === 'bad' ? 'bad' : pulseHealth.value.tone === 'warn' ? 'warn' : pulseHealth.value.tone === 'good' ? 'good' : 'info',
      icon: failedJobs || blockerTotal ? 'warn' : 'check',
      data: { health: pulseHealth.value, automation: automation.value.automation },
    },
    {
      key: 'latest',
      label: '最新记录',
      value: latestEvent?.event_type || '暂无新事件',
      meta: latestEvent ? `${sourceTypeText(latestEvent.source_type)} · ${formatTime(latestEvent.created_at)}` : '点击同步最新记录生成首批记录',
      action: latestEvent ? '查看事件' : '同步最新记录',
      tone: latestEvent ? 'info' : 'warn',
      icon: 'spark',
      filters: latestEvent?.source_type ? { source_type: latestEvent.source_type } : undefined,
      data: latestEvent ? latestEvent as Record<string, unknown> : { empty: true },
    },
    {
      key: 'blocker',
      label: '最该处理',
      value: nextBottleneck?.title || '暂无待处理问题',
      meta: nextBottleneck ? `+${numberText(nextBottleneck.count)} · ${nextBottleneck.items?.[0] ? bottleneckTitle(nextBottleneck.key, nextBottleneck.items[0]) : '待展开'}` : '流程暂无阻塞',
      action: nextBottleneck ? '查看待处理项' : '无需处理',
      tone: blockerTotal ? 'warn' : 'good',
      icon: blockerTotal ? 'warn' : 'check',
      filters: nextBottleneck?.key === 'open_candidates' ? { candidate_status: 'open' } : undefined,
      data: nextBottleneck ? nextBottleneck as unknown as Record<string, unknown> : { empty: true },
    },
    {
      key: 'materialize',
      label: '自动生成知识',
      value: `${numberText(automation.value.backlog?.auto_materializable)} 待处理`,
      meta: `${numberText(automation.value.backlog?.review_required)} 待审核 · ${numberText(failedJobs)} 失败`,
      action: Number(automation.value.backlog?.auto_materializable || 0) ? '查看生成知识' : '队列无待办',
      tone: failedJobs ? 'bad' : Number(automation.value.backlog?.review_required || 0) ? 'warn' : 'good',
      icon: 'database',
      filters: { artifact_status: 'ready' },
      data: { backlog: automation.value.backlog, jobs: automation.value.jobs },
    },
  ]
})
const pulseOverviewCards = computed<PulseOverviewCard[]>(() => {
  const command = aiStickyCommand.value
  const confidence = aiStickyCommandConfidence.value
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const samples = Number(summary.value.training?.samples || manifest.value.sample_total || 0)
  const queuePressure = failedJobs + blockerTotal + readyAssets + reviewRequired
  return [
    {
      key: 'next',
      label: '建议立即处理',
      value: command.cta,
      meta: `${command.label} · ${confidence.score}% 把握`,
      reason: '当前收益或风险最高，可直接执行或定位。',
      icon: command.icon,
      tone: command.tone,
      target: command.target,
      filters: command.filters,
      data: { overview: 'next_action', command, confidence },
    },
    {
      key: 'queue',
      label: '待处理阻塞',
      value: numberText(queuePressure),
      meta: `${numberText(readyAssets)} 待生成知识 · ${numberText(reviewRequired)} 待审 · ${numberText(failedJobs)} 失败`,
      reason: queuePressure ? '会影响自动处理进度，应优先处理。' : '当前没有明显阻塞，可继续观察结果。',
      icon: queuePressure ? 'warn' : 'shield',
      tone: failedJobs ? 'bad' : blockerTotal || reviewRequired ? 'warn' : readyAssets ? 'info' : 'good',
      target: failedJobs || readyAssets ? 'panel-automation' : blockerTotal || reviewRequired ? 'panel-bottleneck' : 'panel-run-brief',
      filters: failedJobs || readyAssets ? { artifact_status: 'ready' } : blockerTotal || reviewRequired ? { candidate_status: 'open' } : undefined,
      data: { overview: 'queue', failed_jobs: failedJobs, blockers: blockerTotal, ready_assets: readyAssets, review_required: reviewRequired },
    },
    {
      key: 'learn',
      label: samples ? '可复核样本' : '可生成知识',
      value: samples ? `${numberText(samples)} 样本` : `${numberText(readyAssets)} 资产`,
      meta: samples ? '可直接查看记录训练链路' : readyAssets ? '先生成知识再进入训练/知识' : '暂无结果复核压力',
      reason: samples ? '可用于校验改进效果和训练质量。' : readyAssets ? '可转为知识库、训练样本或 Agent 记忆。' : '当前暂无可复用产物堆积。',
      icon: samples ? 'beaker' : readyAssets ? 'database' : 'flow',
      tone: samples ? 'good' : readyAssets ? 'info' : 'warn',
      target: samples ? 'panel-journey' : readyAssets ? 'panel-automation' : 'panel-run-brief',
      filters: samples ? { source_type: 'training_job' } : readyAssets ? { artifact_status: 'ready' } : undefined,
      data: { overview: 'learning', samples, ready_assets: readyAssets, manifest: manifest.value },
    },
    {
      key: 'health',
      label: '当前状态',
      value: pulseHealth.value.main,
      meta: pulseHealth.value.detail,
      reason: '用于判断是否需要人工介入。',
      icon: pulseHealth.value.tone === 'bad' || pulseHealth.value.tone === 'warn' ? 'warn' : 'check',
      tone: pulseHealth.value.tone === 'bad' ? 'bad' : pulseHealth.value.tone === 'warn' ? 'warn' : pulseHealth.value.tone === 'good' ? 'good' : 'info',
      target: 'panel-run-brief',
      data: { overview: 'health', health: pulseHealth.value },
    },
  ]
})
const pulseValueSummary = computed(() => {
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const readyAssets = Number(automation.value.backlog?.auto_materializable || 0) + Number(summary.value.artifacts?.by_status?.ready || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const queuePressure = failedJobs + blockerTotal + readyAssets + reviewRequired
  if (failedJobs) return `${numberText(failedJobs)} 个失败任务优先处理`
  if (blockerTotal || reviewRequired) return `${numberText(blockerTotal + reviewRequired)} 项审核/处理事项优先显示`
  if (readyAssets) return `${numberText(readyAssets)} 个可复用产物优先显示`
  return '优先显示可执行建议、复用产物和人工介入依据'
})
const aiSimplePriorityItems = computed<PulseOverviewCard[]>(() => aiDecisionQueue.value.slice(0, 3).map((item) => ({
  key: `decision-${item.key}`,
  label: item.label,
  value: item.value,
  meta: item.meta,
  reason: `${item.cta} · ${Math.round(item.score)}% 优先级`,
  icon: item.icon,
  tone: item.tone,
  target: item.target,
  filters: item.filters,
  data: { overview: 'decision_queue', decision: item },
})))
const aiSimplePrioritySummary = computed(() => {
  const top = aiDecisionQueue.value[0]
  return top ? `${top.cta} · ${top.meta}` : pulseValueSummary.value
})
const pulseOverviewPath = computed<AiMissionStep[]>(() => aiMissionSteps.value)
const globalFlowNodes = computed<GlobalFlowNode[]>(() => {
  const artifactKinds = summary.value.artifacts?.by_kind || {}
  const candidateTargets = summary.value.candidates?.by_target || {}
  const candidateStatus = summary.value.candidates?.by_status || {}
  const materialized = Number(summary.value.artifacts?.by_status?.materialized || 0)
  const openCandidates = Number(candidateStatus.open || 0)
  const candidateUpdates = Number(candidateStatus.reviewing || 0)
    + Number(candidateStatus.accepted || 0)
    + Number(candidateStatus.implemented || 0)
    + Number(candidateStatus.rejected || 0)
  const knowledgeNew = Number(artifactKinds.knowledge_note || 0) + Number(artifactKinds.report_summary || 0)
  return [
    {
      key: 'source',
      label: '运行入口',
      description: 'Skill / SF / Agent / 训练产生事实',
      icon: 'cube',
      tone: 'source',
      x: 11,
      y: 50,
      total: Number(summary.value.events?.total || events.value.length),
      newCount: events.value.length,
      updateCount: Number(automation.value.latest?.event_at ? 1 : 0),
      filters: {},
      data: { summary: summary.value.events, latest: events.value[0] || null },
    },
    {
      key: 'event',
      label: '标准记录',
      description: '统一脱敏、去重、打分后进入学习池',
      icon: 'spark',
      tone: 'event',
      x: 32,
      y: 28,
      total: Number(summary.value.events?.total || events.value.length),
      newCount: events.value.length,
      updateCount: Number(journeyFlow.value.state_counts?.flowing || 0),
      filters: { event_type: '' },
      data: { events: events.value.slice(0, 6), state_counts: journeyFlow.value.state_counts },
    },
    {
      key: 'artifact',
      label: '学习资产',
      description: '提取报告、知识片段、样本、记忆和建议',
      icon: 'database',
      tone: 'artifact',
      x: 52,
      y: 50,
      total: sumRecord(artifactKinds),
      newCount: artifacts.value.length,
      updateCount: materialized,
      filters: {},
      data: { artifacts: artifacts.value.slice(0, 6), by_kind: artifactKinds },
    },
    {
      key: 'knowledge',
      label: '知识入库',
      description: '总结结论和报告进入知识库',
      icon: 'book',
      tone: 'sink',
      x: 76,
      y: 22,
      total: Number(summary.value.knowledge?.indexed || 0),
      newCount: knowledgeNew,
      updateCount: Number(summary.value.agent?.used_context_edges || 0),
      filters: { artifact_kind: 'knowledge_note' },
      data: { knowledge: summary.value.knowledge },
    },
    {
      key: 'training',
      label: '训练样本',
      description: '受控样本进入训练清单',
      icon: 'beaker',
      tone: 'artifact',
      x: 80,
      y: 50,
      total: Number(summary.value.training?.samples || manifest.value.sample_total || 0),
      newCount: Number(manifest.value.sample_total || 0),
      updateCount: Number(manifest.value.skills?.length || 0),
      filters: { source_type: 'training_job' },
      data: { training: summary.value.training, manifest: manifest.value },
    },
    {
      key: 'agent',
      label: 'Agent 记忆',
      description: '上下文引用和记忆支持会话',
      icon: 'bot',
      tone: 'candidate',
      x: 76,
      y: 78,
      total: Number(summary.value.agent?.memories || 0),
      newCount: Number(artifactKinds.agent_memory || 0),
      updateCount: Number(summary.value.agent?.used_context_edges || 0),
      filters: { source_type: 'agent_thread' },
      data: { agent: summary.value.agent },
    },
    {
      key: 'candidate',
      label: '改进建议',
      description: 'Skill / SF / Agent / 知识 / 训练的下一轮建议',
      icon: 'edit',
      tone: 'candidate',
      x: 52,
      y: 80,
      total: sumRecord(candidateTargets),
      newCount: candidates.value.length,
      updateCount: candidateUpdates,
      filters: { candidate_status: 'open' },
      data: { candidates: candidates.value.slice(0, 6), by_target: candidateTargets, by_status: candidateStatus },
    },
    {
      key: 'governance',
      label: '审核流程',
      description: '待办评审、生成知识、重试和发布控制',
      icon: 'inbox',
      tone: 'review',
      x: 30,
      y: 78,
      total: Number(bottlenecks.value.summary?.total || 0),
      newCount: Number(bottlenecks.value.summary?.total || 0),
      updateCount: Number(automation.value.backlog?.review_required || 0),
      filters: { candidate_status: 'open' },
      data: { bottlenecks: bottlenecks.value, automation: automation.value },
    },
  ]
})
const globalFlowEdges = computed<GlobalFlowEdge[]>(() => {
  const artifactTotal = sumRecord(summary.value.artifacts?.by_kind || {})
  const candidateTotal = sumRecord(summary.value.candidates?.by_target || {})
  const candidateStatus = summary.value.candidates?.by_status || {}
  const candidateUpdates = Number(candidateStatus.reviewing || 0)
    + Number(candidateStatus.accepted || 0)
    + Number(candidateStatus.implemented || 0)
    + Number(candidateStatus.rejected || 0)
  return [
    globalEdge('source-event', 'source', 'event', '同步', Number(summary.value.events?.total || events.value.length), events.value.length, Number(journeyFlow.value.state_counts?.flowing || 0), 'M112 260 C182 164 238 138 294 146', 22, 31, 'source'),
    globalEdge('event-artifact', 'event', 'artifact', '提取', artifactTotal, artifacts.value.length, Number(summary.value.artifacts?.by_status?.materialized || 0), 'M310 154 C374 146 424 190 472 260', 43, 36, 'event'),
    globalEdge('artifact-knowledge', 'artifact', 'knowledge', '入库', Number(summary.value.knowledge?.indexed || 0), Number(summary.value.artifacts?.by_kind?.knowledge_note || 0), Number(summary.value.agent?.used_context_edges || 0), 'M486 250 C566 160 638 116 700 112', 66, 30, 'sink'),
    globalEdge('artifact-training', 'artifact', 'training', '成样本', Number(summary.value.training?.samples || manifest.value.sample_total || 0), Number(manifest.value.sample_total || 0), Number(manifest.value.skills?.length || 0), 'M492 260 C570 250 644 252 736 260', 66, 50, 'artifact'),
    globalEdge('artifact-agent', 'artifact', 'agent', '成记忆', Number(summary.value.agent?.memories || 0), Number(summary.value.artifacts?.by_kind?.agent_memory || 0), Number(summary.value.agent?.used_context_edges || 0), 'M486 276 C572 352 638 402 700 406', 66, 70, 'candidate'),
    globalEdge('artifact-candidate', 'artifact', 'candidate', '提建议', candidateTotal, candidates.value.length, candidateUpdates, 'M470 286 C444 332 444 374 470 414', 44, 67, 'candidate'),
    globalEdge('candidate-governance', 'candidate', 'governance', '进入审核', Number(candidateStatus.open || 0) + Number(candidateStatus.reviewing || 0), candidates.value.length, Number(automation.value.backlog?.review_required || 0), 'M452 410 C384 430 340 420 300 404', 39, 83, 'review'),
    globalEdge('governance-source', 'governance', 'source', '改进建议', Number(candidateStatus.implemented || 0) + Number(candidateStatus.accepted || 0), Number(automation.value.backlog?.auto_materializable || 0), Number(bottlenecks.value.summary?.total || 0), 'M290 394 C176 424 88 360 112 270', 15, 69, 'source'),
  ]
})
const activeGlobalNode = computed(() => globalFlowNodes.value.find((node) => node.key === activeGlobalNodeKey.value) || globalFlowNodes.value[0])
const activeGlobalEdge = computed(() => globalFlowEdges.value.find((edge) => edge.key === activeGlobalEdgeKey.value) || null)
const activeGlobalInbound = computed(() => globalFlowEdges.value.filter((edge) => edge.to === activeGlobalNode.value?.key))
const activeGlobalOutbound = computed(() => globalFlowEdges.value.filter((edge) => edge.from === activeGlobalNode.value?.key))
const activeGlobalAdditions = computed(() => globalDetailItems(activeGlobalNode.value?.key || 'source', 'new'))
const activeGlobalUpdates = computed(() => globalDetailItems(activeGlobalNode.value?.key || 'source', 'update'))
const globalDetailMetrics = computed<GlobalDetailMetric[]>(() => {
  const node = activeGlobalNode.value || globalFlowNodes.value[0]
  if (!node) return []
  return [
    {
      key: 'total',
      label: '总量',
      value: numberText(node.total),
      meta: '累计节点记录',
      tone: 'info',
      data: { title: node.label, metric: 'total', value: node.total, node: node.data },
    },
    {
      key: 'new',
      label: '新增',
      value: `+${numberText(node.newCount)}`,
      meta: activeGlobalAdditions.value[0]?.title || '暂无新增',
      tone: activeGlobalAdditions.value.length ? 'good' : 'info',
      data: activeGlobalAdditions.value[0]?.data || { title: node.label, metric: 'new', value: node.newCount },
    },
    {
      key: 'update',
      label: '更新',
      value: numberText(node.updateCount),
      meta: activeGlobalUpdates.value[0]?.title || '暂无更新',
      tone: activeGlobalUpdates.value.length ? 'warn' : 'info',
      data: activeGlobalUpdates.value[0]?.data || { title: node.label, metric: 'update', value: node.updateCount },
    },
  ]
})
const globalDetailNarrative = computed(() => {
  const node = activeGlobalNode.value
  if (!node) return '选择节点后查看这一步如何进入、更新和产生改进建议。'
  const inbound = activeGlobalInbound.value.map((edge) => globalNodeLabel(edge.from)).join(' / ') || '当前入口'
  const outbound = activeGlobalOutbound.value.map((edge) => globalNodeLabel(edge.to)).join(' / ') || '暂无下游'
  return `${inbound} → ${node.label} → ${outbound}；本期新增 +${numberText(node.newCount)}，更新 ${numberText(node.updateCount)}。`
})
const detailTitle = computed(() => String((selected.value?.data?.title || selected.value?.data?.label || selected.value?.data?.event_type || selected.value?.data?.id || '详情') as string))
const detailEntity = computed(() => selected.value ? entityForDetail(selected.value.type, selected.value.data) : null)
const detailDeepLink = computed(() => selected.value ? deepLinkForDetail(selected.value.type, selected.value.data) : '')
const detailHighlights = computed(() => selected.value ? highlightsForDetail(selected.value.data) : [])
const selectedPulseModuleKey = computed(() => String(selected.value?.data?.module_key || ''))
const selectedPulseModule = computed<LearningPulseModule | null>(() => {
  const moduleKey = selectedPulseModuleKey.value
  return moduleKey ? moduleByKey(moduleKey) || null : null
})
const selectedPulseFlowDetail = computed<LearningPulseFlowDetail | null>(() => {
  const module = selectedPulseModule.value
  if (module?.flow_detail) return module.flow_detail
  const value = selected.value?.data?.flow_detail
  if (value && typeof value === 'object') return value as LearningPulseFlowDetail
  return null
})
const selectedPulseFlowSections = computed<LearningPulseFlowSection[]>(() => selectedPulseFlowDetail.value?.sections || [])
const selectedPulseModuleTitle = computed(() => String(selected.value?.data?.module_title || selected.value?.data?.title || selected.value?.data?.module_key || '当前步骤'))
const selectedPulseRowCount = computed(() => selectedPulseFlowSections.value.reduce((total, section) => total + (section.items || []).length, 0))
const selectedPulseLatestRowTime = computed(() => {
  const values = selectedPulseFlowSections.value
    .flatMap((section) => section.items || [])
    .map((row) => pulseRowTimeValue(row))
    .filter(Boolean)
  return values[0] || ''
})
const selectedPulseLifecycleTitle = computed(() => {
  const rows = selectedPulseRowCount.value
  if (pulseRefreshing.value) return `${selectedPulseModuleTitle.value} 正在拉取最新生产数据`
  if (rows) return `${selectedPulseModuleTitle.value} 已展开 ${numberText(rows)} 条生产记录`
  return `${selectedPulseModuleTitle.value} 等待节点或后台回传`
})
const selectedPulseLifecycleMeta = computed(() => {
  const latest = selectedPulseLatestRowTime.value ? `最近处理 ${formatTimeSeconds(selectedPulseLatestRowTime.value)}` : '暂无处理时间'
  const synced = pulse.value.generated_at ? `批次 ${formatTimeSeconds(pulse.value.generated_at)}` : '等待刷新批次'
  return `${latest} · ${synced} · 每 ${Math.round(PULSE_LIVE_REFRESH_MS / 1000)} 秒自动刷新`
})
const selectedPulseRefreshCards = computed<DetailRefreshCard[]>(() => {
  const module = selectedPulseModule.value
  const sections = selectedPulseFlowSections.value
  const rowCount = selectedPulseRowCount.value
  const outputRows = sections.reduce((total, section) => total + (section.items || []).filter((row) => row.output_preview).length, 0)
  const latest = selectedPulseLatestRowTime.value
  return [
    {
      key: 'sync',
      label: '实时刷新',
      value: pulseRefreshing.value ? '同步中' : pulseError.value ? '失败' : '已同步',
      meta: pulseError.value || (pulse.value.generated_at ? formatTimeSeconds(pulse.value.generated_at) : '等待首次数据'),
      tone: pulseError.value ? 'bad' : pulseRefreshing.value ? 'info' : 'good',
    },
    {
      key: 'stage',
      label: '当前阶段',
      value: module?.title || selectedPulseModuleTitle.value,
      meta: module ? `${learningStatusText(module.status_text || module.status) || '-'} · ${numberText(module.metrics?.length || 0)} 指标` : '临时详情',
      tone: module?.status === 'failed' ? 'bad' : module?.status === 'pending' || module?.status === 'awaiting_review' ? 'warn' : 'info',
    },
    {
      key: 'records',
      label: '生产明细',
      value: `${numberText(rowCount)} 条`,
      meta: `${numberText(sections.length)} 类处理 · ${numberText(outputRows)} 条有输出`,
      tone: rowCount ? 'good' : 'warn',
    },
    {
      key: 'latest',
      label: '最近变化',
      value: latest ? formatTimeSeconds(latest).slice(11) : '-',
      meta: latest ? formatTimeSeconds(latest).slice(0, 10) : '暂无节点时间',
      tone: latest ? 'good' : 'info',
    },
  ]
})
const detailProductionStages = computed<DetailProductionStage[]>(() => {
  const activeKey = selectedPulseModuleKey.value
  const activeIndex = DEFAULT_PULSE_MODULES.findIndex((module) => module.key === activeKey)
  return pulseModules.value.map((module, index) => {
    const rowCount = (module.flow_detail?.sections || []).reduce((total, section) => total + (section.items || []).length, 0)
    const metric = (module.metrics || [])[0]
    return {
      key: module.key,
      label: module.title || module.key,
      value: metric ? pulseMetricValue(metric) : numberText(rowCount),
      status: learningStatusText(module.status_text || module.status) || '-',
      meta: rowCount ? `${numberText(rowCount)} 条生产明细` : '等待真实数据',
      tone: normalizeDetailStageTone(module.tone),
      active: module.key === activeKey,
      done: activeIndex >= 0 && index < activeIndex,
    }
  })
})
const selectedPulseProcessingRows = computed<DetailProcessingRow[]>(() => {
  const sections = selectedPulseFlowSections.value
  const rows: DetailProcessingRow[] = []
  sections.forEach((section) => {
    ;(section.items || []).forEach((row, rowIndex) => {
      rows.push({
        key: `${section.key || section.title || 'section'}-${rowIndex}-${row.entity_id || row.entity || row.title || 'row'}`,
        section,
        row,
        rowIndex,
        title: String(row.title || row.entity || section.title || '处理记录'),
        statusText: String(row.status_text || learningStatusText(row.status) || '处理中'),
        summary: String(row.summary || section.description || '当前步骤正在处理该数据。'),
        source: String(row.source || '本步输入'),
        destination: String(row.destination || row.entity || '本步输出'),
        reason: String(row.reason || ''),
        inputText: previewCompact(row.input_preview),
        outputText: previewCompact(row.output_preview),
        syncText: pulseRowTimeText(row),
        changeText: pulseRowChangeText(row),
      })
    })
  })
  return rows.slice(0, 8)
})
const selectedPulseDataProcesses = computed<DetailDataProcess[]>(() => {
  const sections = selectedPulseFlowSections.value
  const processes: DetailDataProcess[] = []
  sections.forEach((section) => {
    ;(section.items || []).forEach((row, rowIndex) => {
      const inputPreview = previewCompact(row.input_preview)
      const outputPreview = previewCompact(row.output_preview)
      processes.push({
        key: `${section.key || section.title || 'process'}-${rowIndex}-${row.entity_id || row.entity || row.title || 'row'}`,
        section,
        row,
        rowIndex,
        title: String(row.title || row.entity || section.title || '数据处理记录'),
        statusText: String(row.status_text || learningStatusText(row.status) || '处理中'),
        inputTitle: String(row.source || row.entity || '本步输入数据'),
        inputDetail: inputPreview === '暂无' ? String(row.summary || section.description || '等待输入摘要') : inputPreview,
        actionTitle: String(section.title || selectedPulseModuleTitle.value || '本步处理'),
        actionDetail: String(row.reason || section.description || '按照当前步骤规则处理数据。'),
        outputTitle: String(row.destination || row.entity || '本步输出结果'),
        outputDetail: outputPreview === '暂无' ? String(row.summary || '等待输出摘要') : outputPreview,
        reason: String(row.reason || ''),
        syncText: pulseRowTimeText(row),
        changeText: pulseRowChangeText(row),
      })
    })
  })
  return processes.slice(0, 6)
})
const selectedPulseProcessNarrative = computed(() => {
  const count = selectedPulseDataProcesses.value.length
  const moduleKey = selectedPulseModuleKey.value
  const map: Record<string, string> = {
    raw_inputs: `展示原始数据如何进入系统、经过校验后流向数据清洗，共 ${count} 条过程记录。`,
    clarified_data: `展示数据如何被清洗、保留或丢弃，并写入数据库、向量库或训练样本，共 ${count} 条过程记录。`,
    finetuning: `展示清洗资产如何形成训练样本、进入训练 Agent 并产生训练进度，共 ${count} 条过程记录。`,
    test_results: `展示训练产物如何进入测试 Agent、产生评估指标和测试输出，共 ${count} 条过程记录。`,
    application_outputs: `展示模型产物如何部署、在哪里被调用、输入什么并输出什么，共 ${count} 条过程记录。`,
  }
  return map[moduleKey] || `展示当前步骤的数据输入、处理动作和输出结果，共 ${count} 条过程记录。`
})
const selectedPulseProcessingSummary = computed(() => {
  const moduleKey = selectedPulseModuleKey.value
  const count = selectedPulseProcessingRows.value.length
  const map: Record<string, string> = {
    raw_inputs: `正在接收并校验 ${count} 条原始进入记录，确认来源、摘要和后续清洗去向。`,
    clarified_data: `正在清洗 ${count} 条资产记录，判断保留、丢弃、入库和向量化结果。`,
    finetuning: `正在把 ${count} 条训练任务或样本血缘送入训练链路，跟踪进度和预计时间。`,
    test_results: `正在检查 ${count} 条测试任务或测试输出，确认评估 Agent、指标和产物可用性。`,
    application_outputs: `正在核对 ${count} 条部署或应用链路，确认模型部署位置、调用输入和输出结果。`,
  }
  return map[moduleKey] || `正在处理 ${count} 条本步骤数据。`
})
const selectedPulseProcessingSteps = computed<DetailProcessingStep[]>(() => {
  const rows = selectedPulseProcessingRows.value
  const sections = selectedPulseFlowSections.value
  const inputCount = rows.filter((row) => row.inputText !== '暂无').length
  const outputCount = rows.filter((row) => row.outputText !== '暂无').length
  const destinations = Array.from(new Set(rows.map((row) => row.destination).filter(Boolean)))
  return [
    {
      key: 'input',
      label: '输入数据',
      value: `${rows.length} 条`,
      detail: rows[0]?.source || sections[0]?.title || '等待实时数据进入',
    },
    {
      key: 'processing',
      label: '处理动作',
      value: `${sections.length} 类`,
      detail: sections.map((section) => section.title).filter(Boolean).slice(0, 2).join(' / ') || '等待处理动作',
    },
    {
      key: 'output',
      label: '输出去向',
      value: `${destinations.length || outputCount} 个`,
      detail: destinations.slice(0, 2).join(' / ') || '等待输出结果',
    },
    {
      key: 'result',
      label: '当前结果',
      value: `${outputCount} 条`,
      detail: inputCount ? `${inputCount} 条有输入摘要，${outputCount} 条有输出摘要` : '暂无输入输出摘要',
    },
  ]
})
const selectedPulseProcessingEmptyText = computed(() => {
  const moduleKey = selectedPulseModuleKey.value
  const map: Record<string, string> = {
    raw_inputs: '当前没有原始记录进入，本步骤等待新的系统事件、运行记录或 Agent 记忆。',
    clarified_data: '当前没有清洗记录，本步骤等待资产提取、保留/丢弃判断或存储任务。',
    finetuning: '当前没有训练任务进入，本步骤等待训练样本或训练任务。',
    test_results: '当前没有测试任务回传，本步骤等待评估 Agent 输出。',
    application_outputs: '当前没有部署或应用链路，本步骤等待模型部署、调用或对话输出。',
  }
  return map[moduleKey] || '当前没有可展示的处理数据。'
})
const selectedIsDeploymentOutput = computed(() => selectedPulseModuleKey.value === 'application_outputs')
const selectedIsModelTest = computed(() => selectedPulseModuleKey.value === 'test_results')
const modelTestProcessRows = computed<DetailProcessingRow[]>(() => (
  pulseModuleProcessRows('test_results', ['eval_tasks', 'test_output'], 8)
))
const selectedModelTestRow = computed<LearningPulseFlowRow | null>(() => {
  if (!selectedIsModelTest.value) return null
  if (selected.value?.type === 'learning_pulse_flow_row') {
    return selected.value.data as LearningPulseFlowRow
  }
  return modelTestProcessRows.value[0]?.row || null
})
const selectedModelTestContext = computed<Record<string, unknown>>(() => (
  selectedModelTestRow.value ? modelTestContextFromRow(selectedModelTestRow.value) : {}
))
const selectedModelTrainingData = computed<Record<string, unknown>>(() => (
  objectRecord(selectedModelTestContext.value.training_data) || {}
))
const modelTestDetailCards = computed(() => {
  const context = selectedModelTestContext.value
  const trainingData = selectedModelTrainingData.value
  return [
    { label: '训练模型', value: String(context.model_name || context.artifact_model_name || '-') },
    { label: '训练日期', value: context.training_date ? formatTimeSeconds(String(context.training_date)) : '-' },
    { label: '模型名称', value: String(context.artifact_model_name || context.model_name || '-') },
    { label: '训练数据', value: trainingDatasetCountText(trainingData) },
    { label: '训练任务', value: String(context.training_job_id || '-') },
    { label: '目标 Skill', value: String(context.target_skill_id || '-') },
  ]
})
const selectedModelTrainingJobId = computed(() => String(
  selectedModelTestContext.value.training_job_id
    || selectedModelTrainingData.value.job_id
    || '',
))
const trainingDatasetSamples = computed<LearningTrainingJobDatasetSample[]>(() => trainingDatasetResult.value?.items || [])
const trainingDatasetSummaryCards = computed(() => {
  const dataset = trainingDatasetResult.value?.dataset || trainingDatasetContext.value || {}
  return [
    { label: '样本', value: trainingDatasetCountText(dataset) },
    { label: '训练', value: formatDetailValue(dataset.train_count ?? '-') },
    { label: '评估', value: formatDetailValue(dataset.eval_count ?? '-') },
    { label: 'Manifest', value: String(dataset.manifest_hash || '-') },
  ]
})
const fifthStageTestProcessRows = computed<DetailProcessingRow[]>(() => (
  pulseModuleProcessRows('test_results', ['eval_tasks', 'test_output'], 8)
))
const fifthStageOutputProcessRows = computed<DetailProcessingRow[]>(() => (
  pulseModuleProcessRows('application_outputs', ['deployments', 'application_edges'], 8)
))
const fifthStageConversationRows = computed<DetailProcessingRow[]>(() => (
  pulseModuleProcessRows('application_outputs', ['conversation_examples'], 4)
))
const selectedDeploymentChatContext = computed<DeploymentChatContext>(() => {
  const modelTestRows = pulseModuleProcessRows('test_results', ['eval_tasks', 'test_output'], 12)
  const deploymentRows = [
    ...pulseModuleProcessRows('application_outputs', ['deployments'], 8),
    ...pulseModuleProcessRows('application_outputs', ['conversation_examples', 'application_edges'], 12),
  ]
  const rows = [
    ...(selectedIsModelTest.value ? modelTestRows : deploymentRows),
    ...(selectedIsModelTest.value ? deploymentRows : modelTestRows),
  ]
  const contexts = rows.map((item) => deploymentChatContextFromRow(item.row))
  const readyContext = contexts.find((context) => context.model_deployment_id && context.ready && context.inference_ready)
  if (readyContext) return readyContext
  const trainingContext = contexts.find((context) => context.training_job_id && !context.model_deployment_id)
  if (trainingContext) return trainingContext
  return emptyDeploymentChatContext()
})
const deploymentChatDisabledReason = computed(() => {
  const context = selectedDeploymentChatContext.value
  if (!context.model_deployment_id) {
    return selectedIsModelTest.value
      ? '当前模型测试还没有可对话部署，需训练完成并进入灰度或已激活后才能对话。'
      : '当前第五步还没有训练后模型部署，需训练完成并进入灰度或已激活后才能对话。'
  }
  if (!context.inference_ready) return context.inference_disabled_reason || context.disabled_reason || '当前训练后模型推理节点不可用。'
  return context.disabled_reason || ''
})
const deploymentChatDeepLink = computed(() => {
  const context = selectedDeploymentChatContext.value
  const deploymentId = context.ready && context.inference_ready ? context.model_deployment_id : ''
  return deploymentId ? `/training/deployments/${enc(deploymentId)}` : ''
})
const deploymentChatContextText = computed(() => {
  const context = selectedDeploymentChatContext.value
  const parts = [
    context.model_family || context.model_deployment_id || context.training_job_id,
    context.deployment_status ? learningStatusText(context.deployment_status) : '',
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : '等待可对话部署'
})
const detailFlowStorageRows = computed(() => {
  const detail = selectedPulseFlowDetail.value || {}
  const source = (detail.storage || detail.eta || {}) as Record<string, unknown>
  const labelMap: Record<string, string> = {
    database_version: '数据库版本',
    database_version_at: '版本时间',
    database_synced_count: '数据库存储',
    knowledge_document_count: '知识文档',
    vector_chunk_count: '向量分片',
    max_index_version: '索引版本',
    seconds: '预计秒数',
    text: '预计时间',
  }
  return Object.entries(source)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .slice(0, 8)
    .map(([key, value]) => ({ label: labelMap[key] || key, value: formatDetailValue(value) }))
})
const flowConnectors = computed(() => topology.value.connectors || [])
const journeys = computed<LearningFlowJourneyRow[]>(() => journeyFlow.value.items || [])
const bottleneckSections = computed(() => bottlenecks.value.sections || [])
const automationRuns = computed(() => automation.value.automation_runs || [])
const automationResult = computed(() => objectRecord(automation.value.automation?.result) || {})
const automationAutoLimits = computed(() => objectRecord(automationResult.value.auto_limits) || {})
const automationServiceCards = computed<AutomationMetric[]>(() => {
  const state = automation.value.automation || {}
  const enabled = Boolean(state.enabled)
  const serviceMode = String(state.service_mode || (enabled ? 'unknown' : 'disabled'))
  const serviceOwner = String(state.service_owner || (serviceMode === 'web_worker' ? 'skillforge.service' : 'skillforge-learning-auto-flow.service'))
  const latestAt = state.finished_at || state.last_loop_finished_at || automation.value.latest?.event_at || state.started_at || ''
  const intervalSeconds = Number(state.interval_seconds || 0)
  const firstDelaySeconds = Number(state.first_delay_seconds || 0)
  const firstRunPlannedAt = state.first_run_planned_at || (state.service_started_at ? addSecondsText(state.service_started_at, firstDelaySeconds) : '')
  const nextRunPlannedAt = state.next_run_planned_at || (latestAt && intervalSeconds ? addSecondsText(latestAt, intervalSeconds) : '')
  const trainingIntervalSeconds = Number(automationAutoLimits.value.training_interval_seconds || 0)
  const trainingDays = Number(automationAutoLimits.value.training_days || 0)
  const trainingNextAfter = String(automationAutoLimits.value.training_next_after || '')
  const latestTrainingAt = String(automationAutoLimits.value.latest_training_at || '')
  const serviceTone: AutomationMetric['tone'] = !enabled
    ? 'info'
    : state.service_status === 'stopped'
      ? 'warn'
      : state.service_status === 'running' || state.service_started_at
        ? 'good'
        : 'warn'
  const trainingEnabled = state.auto_training_enabled !== false
  return [
    {
      key: 'service_mode',
      label: '服务模式',
      value: enabled ? serviceModeText(serviceMode) : '已关闭',
      meta: serviceOwner,
      icon: serviceMode === 'web_worker' ? 'flow' : 'bot',
      tone: serviceTone,
      data: { metric: 'service_mode', service_mode: serviceMode, service_owner: serviceOwner, automation: state },
    },
    {
      key: 'planned_start',
      label: '计划启动',
      value: formatTimeSeconds(firstRunPlannedAt),
      meta: firstDelaySeconds ? `首跑延迟 ${durationText(firstDelaySeconds)}` : '服务启动后立即首跑',
      icon: 'play',
      tone: firstRunPlannedAt ? 'info' : 'warn',
      data: { metric: 'planned_start', first_run_planned_at: firstRunPlannedAt, first_delay_seconds: firstDelaySeconds, automation: state },
    },
    {
      key: 'service_time',
      label: '服务时间',
      value: state.service_uptime_seconds != null ? durationText(Number(state.service_uptime_seconds)) : '等待启动',
      meta: state.service_started_at ? `启动 ${formatTimeSeconds(state.service_started_at)}` : '暂无服务启动时间',
      icon: 'clock',
      tone: state.service_started_at ? 'good' : 'warn',
      data: { metric: 'service_time', service_started_at: state.service_started_at, service_uptime_seconds: state.service_uptime_seconds, automation: state },
    },
    {
      key: 'next_run',
      label: '下次检查',
      value: formatTimeSeconds(nextRunPlannedAt),
      meta: intervalSeconds ? `每 ${durationText(intervalSeconds)}` : '未配置检查间隔',
      icon: 'refresh',
      tone: nextRunPlannedAt ? 'info' : 'warn',
      data: { metric: 'next_run', next_run_planned_at: nextRunPlannedAt, interval_seconds: intervalSeconds, automation: state },
    },
    {
      key: 'training_schedule',
      label: '自动训练',
      value: trainingEnabled ? (trainingNextAfter ? formatTimeSeconds(trainingNextAfter) : '到期即训') : '已关闭',
      meta: trainingEnabled
        ? `${trainingDays ? `数据窗口 ${numberText(trainingDays)} 天` : '默认数据窗口'} · ${trainingIntervalSeconds ? `间隔 ${durationText(trainingIntervalSeconds)}` : '按自动流节奏'}`
        : '仅生成资产，不自动训练',
      icon: 'beaker',
      tone: trainingEnabled ? 'good' : 'info',
      data: {
        metric: 'training_schedule',
        auto_training_enabled: trainingEnabled,
        latest_training_at: latestTrainingAt,
        training_next_after: trainingNextAfter,
        training_interval_seconds: trainingIntervalSeconds,
        training_days: trainingDays,
        auto_limits: automationAutoLimits.value,
      },
    },
  ]
})
const automationMetrics = computed<AutomationMetric[]>(() => {
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const failedRuns = Number(automation.value.backlog?.automation_failed || 0)
  const failed = failedJobs + failedRuns
  const review = Number(automation.value.backlog?.review_required || 0)
  const autoMaterializable = Number(automation.value.backlog?.auto_materializable || 0)
  const governancePending = Number(automation.value.backlog?.governance_pending || 0)
  const governanceStale = Number(automation.value.backlog?.governance_stale || 0)
  const governanceDecisionless = Number(automation.value.backlog?.governance_decisionless || 0)
  return [
    {
      key: 'auto_materializable',
      label: '待生成知识',
      value: numberText(autoMaterializable),
      meta: autoMaterializable ? '可进入知识 / 样本 / 记忆' : '暂无待处理',
      icon: 'database',
      tone: autoMaterializable ? 'info' : 'good',
      data: { metric: 'auto_materializable', value: autoMaterializable, backlog: automation.value.backlog },
    },
    {
      key: 'review_required',
      label: '待审核',
      value: numberText(review),
      meta: review ? '需进入审核队列' : '审核队列无待办',
      icon: 'inbox',
      tone: review ? 'warn' : 'good',
      data: { metric: 'review_required', value: review, backlog: automation.value.backlog },
    },
    {
      key: 'governance_pending',
      label: '治理待办',
      value: numberText(governancePending + governanceDecisionless),
      meta: governanceDecisionless
        ? `${numberText(governanceDecisionless)} 个缺决策反馈`
        : governanceStale ? `${numberText(governanceStale)} 个已超时` : governancePending ? '等待人工决策闭环' : '治理任务无积压',
      icon: governanceStale || governanceDecisionless ? 'warn' : 'inbox',
      tone: governanceStale || governanceDecisionless ? 'bad' : governancePending ? 'warn' : 'good',
      data: {
        metric: 'governance_pending',
        value: governancePending + governanceDecisionless,
        pending: governancePending,
        stale: governanceStale,
        decisionless: governanceDecisionless,
        backlog: automation.value.backlog,
      },
    },
    {
      key: 'failed',
      label: '失败任务',
      value: numberText(failed),
      meta: failedRuns ? `${numberText(failedRuns)} 次自动循环失败` : failedJobs ? '点击查看失败概览' : '自动处理正常',
      icon: failed ? 'warn' : 'check',
      tone: failed ? 'bad' : 'good',
      data: { metric: 'failed_jobs', value: failed, failed_jobs: failedJobs, failed_runs: failedRuns, jobs: automation.value.jobs, backlog: automation.value.backlog },
    },
    {
      key: 'latest_event',
      label: '最近事件',
      value: formatTime(automation.value.latest?.event_at),
      meta: '最近一次学习记录',
      icon: 'spark',
      tone: automation.value.latest?.event_at ? 'info' : 'warn',
      data: { metric: 'latest_event', latest: automation.value.latest },
    },
  ]
})
const activePulseNode = ref('')
const pulseLoopPath = 'M320 46 C442 46 548 108 548 180 C548 252 442 318 320 318 C198 318 92 252 92 180 C92 108 198 46 320 46 Z'
const runtimeRiverPath = 'M34 132 C170 50 290 214 424 132 C558 50 672 214 886 108'
const flowLoopPath = 'M580 46 C806 46 1070 116 1070 210 C1070 304 806 374 580 374 C354 374 90 304 90 210 C90 116 354 46 580 46 Z'
const pulseLoopDuration = computed(() => {
  const activeSignals = Number(summary.value.events?.total || events.value.length || 0)
  const blockers = Number(bottlenecks.value.summary?.total || 0)
  return `${Math.max(4.4, 8.2 - Math.min(2.2, activeSignals * 0.04) + Math.min(1.6, blockers * 0.18)).toFixed(1)}s`
})
const runtimeRiverDuration = computed(() => {
  const signals = Number(events.value.length || summary.value.events?.total || 0)
  return `${Math.max(3.8, 7.4 - Math.min(2.4, signals * 0.08)).toFixed(1)}s`
})
const flowLoopDuration = computed(() => {
  const total = flowStages.value.reduce((sum, stage) => sum + Number(stage.count || 0), 0)
  return `${Math.max(5.2, 9.6 - Math.min(2.8, total * 0.035)).toFixed(1)}s`
})
const flowLoopSignals = computed<FlowSignal[]>(() => {
  const signals = flowStages.value.map((stage, index) => ({
    key: stage.key,
    label: connectorLabel(index - 1 < 0 ? 0 : index - 1),
    count: stage.count || connectorCount(index),
    tone: stage.tone,
    begin: `${index * 0.58 + 0.12}s`,
  }))
  return signals.filter((signal) => signal.count > 0).slice(0, 8)
})
const pulseHealth = computed(() => {
  const eventsTotal = Number(summary.value.events?.total || events.value.length || 0)
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const failedJobs = Number(automation.value.jobs?.by_status?.failed || 0)
  const flowing = Number(journeyFlow.value.state_counts?.flowing || 0)
  const completed = Number(journeyFlow.value.state_counts?.completed || 0)
  const score = eventsTotal <= 0
    ? 0
    : Math.max(8, Math.min(100, Math.round(92 - blockerTotal * 7 - failedJobs * 18 + Math.min(10, completed * 2) + Math.min(6, flowing))))
  if (loading.value || backfilling.value || automationRunning.value) {
    return {
      tone: 'info',
      score,
      title: '系统正在处理',
      main: '处理中',
      detail: '正在刷新血缘与记录',
      subtitle: '动态标记会沿处理路径循环，表示记录持续完成标准化、资产提取、知识生成、改进建议和审核流程，并用于下一轮运行改进。',
    }
  }
  if (failedJobs > 0) {
    return {
      tone: 'bad',
      score,
      title: '发现失败任务',
      main: '需处理',
      detail: `${numberText(failedJobs)} 个失败任务`,
      subtitle: '处理路径出现红色节点表示自动处理或知识生成任务异常，点击审核流程或待处理记录可定位阻塞。',
    }
  }
  if (blockerTotal > 0) {
    return {
      tone: 'warn',
      score,
      title: '系统运行中，有待处理事项',
      main: '待处理',
      detail: `${numberText(blockerTotal)} 个待处理事项需要审核`,
      subtitle: '橙色节点表示仍有改进建议、资产或审核等待处理；点击节点可聚焦筛选并继续处理。',
    }
  }
  if (eventsTotal > 0) {
    return {
      tone: 'good',
      score,
      title: '系统运行正常',
      main: '正常',
      detail: `${numberText(eventsTotal)} 条记录正在处理`,
      subtitle: '绿色动态标记沿轨道持续循环，表示从运行到知识、训练、Agent、改进建议和审核流程保持活跃。',
    }
  }
  return {
    tone: 'idle',
    score,
    title: '等待首批记录',
    main: '待同步',
    detail: '暂无可视动态标记',
    subtitle: '点击同步最新记录或等待 Skill / SF / Agent / 训练产生新的学习记录。',
  }
})
const pulseNodes = computed<PulseNode[]>(() => {
  const candidateTargets = summary.value.candidates?.by_target || {}
  const candidateStatus = summary.value.candidates?.by_status || {}
  const issueCount = Number(bottlenecks.value.summary?.total || 0)
  return [
    pulseNode('skill', 'Skill 运行', Number(summary.value.events?.total || events.value.length), '运行结果进入记录池', 'cube', 'source', 50, 12, 'M320 46 C398 46 478 70 538 115', { source_type: 'execution_run' }, 1.45),
    pulseNode('sf', 'SF / MCP', Number(candidateTargets.sf || 0), '工具调用形成改进建议', 'bolt', 'event', 84, 32, 'M538 115 C572 160 570 214 538 245', { source_type: 'sf_mcp_call' }, 1.7),
    pulseNode('knowledge', '知识入库', Number(summary.value.knowledge?.indexed || 0), '总结结论自动生成知识', 'book', 'sink', 84, 68, 'M538 245 C480 298 405 318 320 317', { artifact_kind: 'knowledge_note' }, 1.8),
    pulseNode('training', '训练样本', Number(summary.value.training?.samples || manifest.value.sample_total || 0), '样本清单受控生成', 'beaker', 'artifact', 50, 88, 'M320 317 C235 318 158 298 102 245', { source_type: 'training_job' }, 1.85),
    pulseNode('agent', 'Agent 记忆', Number(summary.value.agent?.memories || 0), '上下文引用与记忆', 'bot', 'candidate', 16, 68, 'M102 245 C70 204 70 156 102 115', { source_type: 'agent_thread' }, 1.65),
    pulseNode('governance', '待审核事项', issueCount || Number(candidateStatus.open || 0), '建议 / 资产等待处理', issueCount ? 'warn' : 'inbox', issueCount ? 'danger' : 'review', 16, 32, 'M102 115 C160 68 235 46 320 46', { candidate_status: 'open' }, issueCount ? 1.2 : 1.95),
  ]
})
const pulseInsights = computed<PulseInsight[]>(() => {
  const latestRun = automation.value.automation
  const totalBlockers = Number(bottlenecks.value.summary?.total || 0)
  const flowing = Number(journeyFlow.value.state_counts?.flowing || 0)
  const completed = Number(journeyFlow.value.state_counts?.completed || 0)
  return [
    {
      key: 'automation',
      label: '自动处理引擎',
      value: automationTitle.value,
      meta: latestRun?.finished_at ? `上次 ${formatTime(latestRun.finished_at)}` : '等待首轮调度',
      tone: statusClass(latestRun?.status) === 'bad' ? 'bad' : latestRun?.enabled ? 'good' : 'info',
      data: { type: 'automation', ...latestRun, backlog: automation.value.backlog },
    },
    {
      key: 'journey',
      label: '流程链路',
      value: `${numberText(completed)} 完成 / ${numberText(flowing)} 处理`,
      meta: journeys.value[0]?.title || '暂无最新链路',
      tone: flowing > 0 ? 'info' : completed > 0 ? 'good' : 'warn',
      data: { type: 'journey_summary', state_counts: journeyFlow.value.state_counts, latest: journeys.value[0] || null },
    },
    {
      key: 'blockers',
      label: '待处理事项雷达',
      value: `${numberText(totalBlockers)} 个待处理`,
      meta: totalBlockers ? '点击查看阻塞源' : '当前无阻塞',
      tone: totalBlockers ? 'warn' : 'good',
      filters: totalBlockers ? { candidate_status: 'open' } : undefined,
      data: { type: 'bottlenecks', summary: bottlenecks.value.summary, sections: bottlenecks.value.sections },
    },
  ]
})
const aiSideNavItems = computed<AiSideNavItem[]>(() => [
  {
    id: 'panel-global-map',
    label: '详细视图',
    meta: `${numberText(summary.value.events?.total || events.value.length)} 记录`,
    icon: 'flow',
  },
  {
    id: 'panel-run-brief',
    label: '运行评估',
    meta: pulseHealth.value.title,
    icon: pulseHealth.value.tone === 'bad' || pulseHealth.value.tone === 'warn' ? 'warn' : 'check',
  },
  {
    id: 'panel-filters',
    label: '筛选条件',
    meta: activeFilterChips.value.length ? `${activeFilterChips.value.length} 个筛选` : '快速聚焦',
    icon: 'search',
  },
  {
    id: 'panel-automation',
    label: '自动引擎',
    meta: automationTitle.value,
    icon: 'spark',
  },
  {
    id: 'panel-bottleneck',
    label: '待处理事项雷达',
    meta: `${numberText(bottlenecks.value.summary?.total)} 待处理`,
    icon: 'warn',
  },
  {
    id: 'panel-flow-stage',
    label: '处理流程',
    meta: `${numberText(flowStages.value.length)} 阶段`,
    icon: 'database',
  },
  {
    id: 'panel-journey',
    label: '处理记录',
    meta: `${numberText(journeys.value.length)} 条链路`,
    icon: 'branch',
  },
  {
    id: 'panel-river',
    label: '运行记录',
    meta: `${numberText(eventStreamItems.value.length)} 动态标记`,
    icon: 'bolt',
  },
])
const aiSideNextActions = computed<AiSideAction[]>(() => {
  const actions: AiSideAction[] = []
  const blocker = bottleneckSections.value.find((section) => Number(section.count || 0) > 0)
  const failed = Number(automation.value.jobs?.by_status?.failed || 0)
  const autoMaterializable = Number(automation.value.backlog?.auto_materializable || 0)
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  if (failed > 0) {
    actions.push({
      key: 'failed-jobs',
      label: '先看失败任务',
      meta: `${numberText(failed)} 个自动处理任务失败`,
      cta: '打开自动引擎',
      tone: 'bad',
      icon: 'warn',
      kind: 'panel',
      target: 'panel-automation',
      data: { jobs: automation.value.jobs },
    })
  }
  if (blocker) {
    actions.push({
      key: `blocker-${blocker.key}`,
      label: blocker.title || '处理待办',
      meta: `+${numberText(blocker.count)} · ${blocker.items?.[0] ? bottleneckTitle(blocker.key, blocker.items[0]) : '等待查看'}`,
      cta: blocker.action === 'materialize' ? '处理首条' : blocker.action === 'create_review' ? '进待办' : '定位处理',
      tone: blocker.severity === 'danger' ? 'bad' : 'warn',
      icon: 'warn',
      kind: 'bottleneck',
      target: 'panel-bottleneck',
      data: { section: blocker },
    })
  }
  if (autoMaterializable > 0) {
    actions.push({
      key: 'auto-materialize',
      label: '可自动生成知识',
      meta: `${numberText(autoMaterializable)} 个资产可生成知识到知识 / 样本 / 记忆`,
      cta: '查看队列',
      tone: 'info',
      icon: 'database',
      kind: 'automation',
      target: 'panel-automation',
      data: { metric: 'auto_materializable', value: autoMaterializable, backlog: automation.value.backlog },
    })
  }
  if (reviewRequired > 0) {
    actions.push({
      key: 'review-required',
      label: '等待人工审核流程',
      meta: `${numberText(reviewRequired)} 个变更需要审核，不会超出权限发布`,
      cta: '查看审核',
      tone: 'warn',
      icon: 'inbox',
      kind: 'panel',
      target: 'panel-bottleneck',
      data: { metric: 'review_required', value: reviewRequired, backlog: automation.value.backlog },
    })
  }
  if (!events.value.length && !Number(summary.value.events?.total || 0)) {
    actions.push({
      key: 'capture',
      label: '同步首批记录',
      meta: '页面暂无动态标记，先从运行 / SF / Agent 提取事实',
      cta: '立即同步',
      tone: 'info',
      icon: 'spark',
      kind: 'capture',
      data: { empty: true },
    })
  }
  if (!actions.length) {
    actions.push({
      key: 'healthy-loop',
      label: '系统正常处理',
      meta: '暂无阻塞；可手动触发一次自动处理做检查',
      cta: '检查一次',
      tone: 'good',
      icon: 'check',
      kind: 'automation',
      target: 'panel-automation',
      data: { health: pulseHealth.value },
    })
  }
  return actions.slice(0, 4)
})
const aiSideRouteSteps = computed<AiSideRouteStep[]>(() => {
  const eventCount = Number(summary.value.events?.total || events.value.length || 0)
  const artifactTotal = sumRecord(summary.value.artifacts?.by_kind || {}) || artifacts.value.length
  const materialized = Number(summary.value.artifacts?.by_status?.materialized || 0)
    + Number(summary.value.knowledge?.indexed || 0)
    + Number(summary.value.training?.samples || 0)
    + Number(summary.value.agent?.memories || 0)
  const blockerTotal = Number(bottlenecks.value.summary?.total || 0)
  const candidateTotal = sumRecord(summary.value.candidates?.by_target || {}) || candidates.value.length
  const reviewRequired = Number(automation.value.backlog?.review_required || 0)
  const failed = Number(automation.value.jobs?.by_status?.failed || 0)
  return [
    {
      key: 'capture',
      label: '同步',
      value: `${numberText(eventCount)} 记录`,
      status: eventCount ? '已接入' : '待同步',
      tone: eventCount ? 'good' : 'warn',
      icon: 'spark',
      target: 'panel-global-map',
      filters: {},
      data: { events: summary.value.events, latest: events.value[0] || null },
    },
    {
      key: 'extract',
      label: '提取',
      value: `${numberText(artifactTotal)} 资产`,
      status: artifactTotal ? '可复用' : '待提取',
      tone: artifactTotal ? 'good' : eventCount ? 'warn' : 'info',
      icon: 'database',
      target: 'panel-flow-stage',
      data: { by_kind: summary.value.artifacts?.by_kind, artifacts: artifacts.value.slice(0, 5) },
    },
    {
      key: 'materialize',
      label: '生成知识',
      value: `${numberText(materialized)} 更新`,
      status: Number(automation.value.backlog?.auto_materializable || 0) ? '可生成知识' : materialized ? '已生成知识' : '待生成知识',
      tone: Number(automation.value.backlog?.auto_materializable || 0) ? 'warn' : materialized ? 'good' : 'info',
      icon: 'book',
      target: 'panel-automation',
      filters: Number(automation.value.backlog?.auto_materializable || 0) ? { artifact_status: 'ready' } : undefined,
      data: { backlog: automation.value.backlog, materialized, knowledge: summary.value.knowledge, training: summary.value.training, agent: summary.value.agent },
    },
    {
      key: 'govern',
      label: '审核流程',
      value: `${numberText(blockerTotal + reviewRequired + failed)} 待看`,
      status: failed ? '有失败' : blockerTotal || reviewRequired ? '需处理' : '安全',
      tone: failed ? 'bad' : blockerTotal || reviewRequired ? 'warn' : 'good',
      icon: failed || blockerTotal ? 'warn' : 'shield',
      target: 'panel-bottleneck',
      filters: blockerTotal ? { candidate_status: 'open' } : undefined,
      data: { bottlenecks: bottlenecks.value, backlog: automation.value.backlog, jobs: automation.value.jobs },
    },
    {
      key: 'improve',
      label: '改进建议',
      value: `${numberText(candidateTotal)} 建议`,
      status: candidateTotal ? '有建议' : '待积累',
      tone: candidateTotal ? 'info' : 'good',
      icon: 'flow',
      target: 'panel-journey',
      data: { candidates: candidates.value.slice(0, 5), by_target: summary.value.candidates?.by_target, journeys: journeys.value.slice(0, 3) },
    },
  ]
})
const aiSideRouteProgress = computed(() => {
  const steps = aiSideRouteSteps.value
  const blockerIndex = steps.findIndex((step) => step.tone === 'bad' || (step.tone === 'warn' && ['materialize', 'govern'].includes(step.key)))
  if (blockerIndex >= 0) return Math.max(16, Math.round(((blockerIndex + 0.55) / Math.max(1, steps.length)) * 100))
  const activeCount = steps.filter((step) => step.tone === 'good' || step.tone === 'info').length
  return Math.max(16, Math.min(100, Math.round((activeCount / Math.max(1, steps.length)) * 100)))
})
const aiSideRouteStyle = computed<Record<string, string>>(() => ({
  '--route-progress': `${aiSideRouteProgress.value}%`,
}))
const aiSideRouteSummary = computed(() => {
  const warn = aiSideRouteSteps.value.find((step) => step.tone === 'bad' || step.tone === 'warn')
  if (warn) return `${warn.label} · ${warn.status}`
  return '全链路正常'
})
const eventStreamItems = computed<EventStreamItem[]>(() => {
  const rows = events.value.slice(0, 9).map((event) => ({
    id: String(event.id),
    title: event.event_type || sourceTypeText(event.source_type),
    meta: `${sourceTypeText(event.source_type)} · ${formatTime(event.created_at)}`,
    status: event.status,
    source_type: event.source_type,
    data: event as Record<string, unknown>,
  }))
  if (rows.length) return rows
  return journeys.value.slice(0, 6).map((journey) => ({
    id: String(journey.id || journey.event_id),
    title: journey.title || String(journey.event_id || '处理链路'),
    meta: `${sourceTypeText(journey.source_type)} · ${formatTime(journey.last_at)}`,
    status: journey.state,
    source_type: journey.source_type,
    data: journey as unknown as Record<string, unknown>,
  }))
})
const lineageFlowEdges = computed(() => (graph.value.edges || []).slice(0, 8))
const materialFlowItems = computed<MaterialFlowItem[]>(() => [
  ...artifacts.value.slice(0, 6).map((item) => ({
    id: String(item.id),
    type: 'artifact' as const,
    title: item.title || String(item.id),
    kind: artifactKindText(item.artifact_kind),
    meta: `${item.skill_id || '无 Skill'} · ${item.status || 'ready'} · Q ${percent(item.quality_score)}`,
    status: item.status,
    data: item as Record<string, unknown>,
  })),
  ...candidates.value.slice(0, 6).map((item) => ({
    id: String(item.id),
    type: 'candidate' as const,
    title: item.title || '改进建议',
    kind: `${item.target_type || '建议'} · ${item.risk_level || 'R2'}`,
    meta: `${item.status || 'open'} · 优先级 ${percent(item.priority_score)}`,
    status: item.status,
    data: item as Record<string, unknown>,
  })),
].slice(0, 12))
const automationTitle = computed(() => {
  const state = automation.value.automation
  if (!state?.enabled) return '已关闭 · 仅手动同步'
  const status = state.status || 'never_run'
  const latest = state.finished_at ? formatTime(state.finished_at) : '等待首轮'
  return `${statusText(status)} · 每 ${numberText(state.interval_seconds)} 秒 · ${latest}`
})
const flowStages = computed<FlowStage[]>(() => {
  if (topology.value.stages?.length) {
    return topology.value.stages.map((stage) => ({
      key: stage.key,
      title: stage.title || stage.key,
      caption: stage.caption || '',
      count: Number(stage.count || 0),
      tone: stage.tone || stage.key,
      nodes: (stage.nodes || []).map(normalizeTopologyNode),
    }))
  }
  const artifactKinds = summary.value.artifacts?.by_kind || {}
  const candidateTargets = summary.value.candidates?.by_target || {}
  const candidateStatus = summary.value.candidates?.by_status || {}
  const sourceNodes = graphNodeCards(
    ['run', 'execution_artifact', 'decision_log', 'sf_call', 'agent_thread', 'knowledge_query', 'training_job', 'model_deployment', 'todo', 'todo_dispatch_task'],
    'source',
  )
  const sinkNodes = [
    ...graphNodeCards(['knowledge_document', 'training_sample', 'agent_memory'], 'sink', 4),
    ...(Number(manifest.value.sample_total || 0) > 0
      ? [{
          id: 'manifest:training',
          label: '训练清单',
          meta: `${numberText(manifest.value.sample_total)} 个受控样本`,
          icon: 'database',
          heat: Math.min(1, Number(manifest.value.sample_total || 0) / 20),
          tone: 'sink',
          payload: manifest.value as Record<string, unknown>,
        }]
      : []),
  ].slice(0, 5)
  const reviewNodes = [
    ...graphNodeCards(['decision_request', 'training_job', 'skill', 'model_deployment'], 'review', 4),
    ...candidates.value
      .filter((item) => ['reviewing', 'accepted', 'implemented'].includes(String(item.status || '')))
      .slice(0, 2)
      .map(candidateNode),
  ].slice(0, 5)
  return [
    {
      key: 'source',
      title: '数据源',
      caption: '运行 / SF / Agent / 训练',
      count: Number(summary.value.events?.total || events.value.length),
      tone: 'source',
      nodes: sourceNodes.length ? sourceNodes : events.value.slice(0, 5).map(sourceEventNode),
    },
    {
      key: 'event',
      title: '标准记录',
      caption: '统一脱敏、去重、打分',
      count: Number(summary.value.events?.total || events.value.length),
      tone: 'event',
      nodes: events.value.slice(0, 5).map(eventNode),
    },
    {
      key: 'artifact',
      title: '学习资产',
      caption: '知识 / 样本 / 记忆 / 建议',
      count: sumRecord(artifactKinds),
      tone: 'artifact',
      nodes: artifacts.value.slice(0, 5).map(artifactNode),
    },
    {
      key: 'sink',
      title: '自动生成知识',
      caption: '知识库 / 训练池 / Agent 记忆',
      count: Number(summary.value.knowledge?.indexed || 0) + Number(summary.value.training?.samples || 0) + Number(summary.value.agent?.memories || 0),
      tone: 'sink',
      nodes: sinkNodes,
    },
    {
      key: 'candidate',
      title: '迭代建议',
      caption: 'Skill / SF / Agent 自动建议',
      count: sumRecord(candidateTargets),
      tone: 'candidate',
      nodes: candidates.value.slice(0, 5).map(candidateNode),
    },
    {
      key: 'review',
      title: '审核流程',
      caption: '待办评审 / 训练 / 发布控制',
      count: Number(candidateStatus.reviewing || 0) + Number(candidateStatus.accepted || 0) + Number(candidateStatus.implemented || 0),
      tone: 'review',
      nodes: reviewNodes,
    },
  ]
})
const hasFlowData = computed(() => flowStages.value.some((stage) => stage.count > 0 || stage.nodes.length > 0))

function globalEdge(
  key: string,
  from: string,
  to: string,
  label: string,
  count: number,
  newCount: number,
  updateCount: number,
  path: string,
  labelX: number,
  labelY: number,
  tone: string,
): GlobalFlowEdge {
  return {
    key,
    from,
    to,
    label,
    count: Number(count || 0),
    newCount: Number(newCount || 0),
    updateCount: Number(updateCount || 0),
    path,
    labelX,
    labelY,
    tone,
    data: { key, from, to, label, count, newCount, updateCount },
  }
}

function globalNodeLabel(key?: string) {
  return globalFlowNodes.value.find((node) => node.key === key)?.label || String(key || '-')
}

function globalDetailItems(nodeKey: string, mode: 'new' | 'update'): GlobalFlowDetailItem[] {
  if (mode === 'new') {
    if (['source', 'event'].includes(nodeKey)) {
      return events.value.slice(0, 5).map((event) => detailItem(
        `event-${event.id}`,
        event.event_type || sourceTypeText(event.source_type),
        `${sourceTypeText(event.source_type)} · ${formatTime(event.created_at)}`,
        detailTone(event.status),
        event as Record<string, unknown>,
      ))
    }
    if (['artifact', 'knowledge', 'training', 'agent'].includes(nodeKey)) {
      const rows = artifacts.value
        .filter((item) => {
          if (nodeKey === 'knowledge') return ['knowledge_note', 'report_summary'].includes(String(item.artifact_kind || ''))
          if (nodeKey === 'training') return ['training_sample', 'eval_case'].includes(String(item.artifact_kind || ''))
          if (nodeKey === 'agent') return String(item.artifact_kind || '') === 'agent_memory'
          return true
        })
        .slice(0, 5)
      if (rows.length) {
        return rows.map((item) => detailItem(
          `artifact-${item.id}`,
          item.title || artifactKindText(item.artifact_kind),
          `${artifactKindText(item.artifact_kind)} · ${item.status || 'ready'} · Q ${percent(item.quality_score)}`,
          detailTone(item.status),
          item as Record<string, unknown>,
        ))
      }
      if (nodeKey === 'training') {
        return (manifest.value.skills || []).slice(0, 5).map((skill, index) => detailItem(
          `training-skill-${skill.skill_id || index}`,
          skill.skill_id || '训练样本',
          `+${numberText(skill.sample_count)} 样本 · eval ${numberText(skill.eval_count)}`,
          'info',
          skill as unknown as Record<string, unknown>,
        ))
      }
    }
    if (nodeKey === 'candidate') {
      return candidates.value.slice(0, 5).map((item) => detailItem(
        `candidate-${item.id}`,
        item.title || '改进建议',
        `${item.target_type || '-'} · ${item.status || 'open'} · ${item.risk_level || 'R2'}`,
        detailTone(item.status),
        item as Record<string, unknown>,
      ))
    }
    if (nodeKey === 'governance') {
      return bottleneckSections.value
        .filter((section) => Number(section.count || 0) > 0)
        .slice(0, 5)
        .map((section) => detailItem(
          `bottleneck-${section.key}`,
          section.title || section.key,
          `+${numberText(section.count)} · ${section.items?.[0] ? bottleneckTitle(section.key, section.items[0]) : '待处理'}`,
          section.severity === 'danger' ? 'bad' : Number(section.count || 0) > 0 ? 'warn' : 'good',
          section as unknown as Record<string, unknown>,
        ))
    }
  }

  if (mode === 'update') {
    if (['source', 'event'].includes(nodeKey)) {
      return journeys.value.slice(0, 5).map((journey) => detailItem(
        `journey-${journey.id}`,
        journey.title || String(journey.event_id || '链路更新'),
        `${flowStateText(journey.state)} · ${journeyProgressPercent(journey)}% · ${formatTime(journey.last_at)}`,
        detailTone(journey.state),
        journey as unknown as Record<string, unknown>,
      ))
    }
    if (nodeKey === 'artifact') {
      return artifacts.value
        .filter((item) => String(item.status || '') === 'materialized')
        .slice(0, 5)
        .map((item) => detailItem(
          `artifact-update-${item.id}`,
          item.title || artifactKindText(item.artifact_kind),
          `已生成 · ${artifactKindText(item.artifact_kind)} · Q ${percent(item.quality_score)}`,
          'good',
          item as Record<string, unknown>,
        ))
    }
    if (nodeKey === 'knowledge') {
      return lineageFlowEdges.value.slice(0, 5).map((edge, index) => detailItem(
        `knowledge-edge-${String(edge.id || index)}`,
        edgeLabel(edge.target),
        `${edgeLabel(edge.source)} ${relationText(edge.relation)} ${edgeLabel(edge.target)}`,
        'info',
        edge as unknown as Record<string, unknown>,
      ))
    }
    if (nodeKey === 'training') {
      return (manifest.value.skills || []).slice(0, 5).map((skill, index) => detailItem(
        `manifest-${skill.skill_id || index}`,
        skill.skill_id || '清单更新',
        `${numberText(skill.sample_count)} 样本 · 平均质量 ${percent(skill.avg_quality)}`,
        'info',
        skill as unknown as Record<string, unknown>,
      ))
    }
    if (nodeKey === 'agent') {
      return graphNodes.value
        .filter((node) => String(node.entity_type || '').includes('agent'))
        .slice(0, 5)
        .map((node) => detailItem(
          `agent-node-${node.id}`,
          node.label || entityTypeText(node.entity_type),
          `${entityTypeText(node.entity_type)} · ${shortId(node.entity_id || node.id)}`,
          'info',
          node as Record<string, unknown>,
        ))
    }
    if (nodeKey === 'candidate') {
      return candidates.value
        .filter((item) => String(item.status || '') !== 'open')
        .slice(0, 5)
        .map((item) => detailItem(
          `candidate-update-${item.id}`,
          item.title || '建议更新',
          `${item.status || '-'} · ${item.target_type || '-'} · 优先级 ${percent(item.priority_score)}`,
          detailTone(item.status),
          item as Record<string, unknown>,
        ))
    }
    if (nodeKey === 'governance') {
      return automationRuns.value.slice(0, 5).map((run) => detailItem(
        `automation-run-${String(run.id || run.started_at)}`,
        statusText(run.status),
        `${run.trigger_type || '-'} · ${formatTime(run.finished_at || run.started_at)} · ${numberText(run.result?.materialized as number)} 生成知识`,
        detailTone(run.status),
        run as Record<string, unknown>,
      ))
    }
  }
  return []
}

function detailItem(
  key: string,
  title: string,
  meta: string,
  tone: GlobalFlowDetailItem['tone'],
  data: Record<string, unknown>,
): GlobalFlowDetailItem {
  return { key, title, meta, tone, data }
}

function detailTone(value?: string): GlobalFlowDetailItem['tone'] {
  const tone = statusClass(value)
  if (tone === 'good' || tone === 'warn' || tone === 'bad') return tone
  return 'info'
}

function designToneClass(value?: string) {
  const tone = String(value || '')
  if (tone === 'bad' || tone === 'danger') return 'sf-tone-danger'
  if (tone === 'warn' || tone === 'warning') return 'sf-tone-warning'
  if (tone === 'good' || tone === 'success') return 'sf-tone-success'
  if (tone === 'info' || tone === 'source' || tone === 'event' || tone === 'artifact' || tone === 'sink' || tone === 'candidate' || tone === 'review') return 'sf-tone-info'
  return 'sf-tone-neutral'
}

function pulseNode(
  key: PulseNode['key'],
  label: string,
  value: number,
  meta: string,
  icon: string,
  tone: PulseNode['tone'],
  x: number,
  y: number,
  path: string,
  filters: PulseFilterPatch,
  speed = 1.8,
): PulseNode {
  return {
    key,
    label,
    value: Number(value || 0),
    meta,
    icon,
    tone,
    x,
    y,
    path,
    duration: `${Math.max(1.05, speed - Math.min(0.5, Number(value || 0) * 0.015)).toFixed(2)}s`,
    filters,
  }
}

function pulseNodeStyle(node: PulseNode, index: number): Record<string, string> {
  return {
    left: `${node.x}%`,
    top: `${node.y}%`,
    '--pulse-delay': `${index * 0.12}s`,
  }
}

function globalNodeStyle(node: GlobalFlowNode, index: number): Record<string, string> {
  const flowRatio = Math.min(1, Math.max(0.18, (Number(node.newCount || 0) + Number(node.updateCount || 0)) / Math.max(1, Number(node.total || 1))))
  return {
    left: `${node.x}%`,
    top: `${node.y}%`,
    '--global-delay': `${index * 0.08}s`,
    '--global-mobile-top': `${8 + index * 12}%`,
    '--node-ring-opacity': String(0.34 + flowRatio * 0.54),
  }
}

function globalNodeMeterStyle(node: GlobalFlowNode): Record<string, string> {
  const activity = Number(node.newCount || 0) + Number(node.updateCount || 0)
  const pct = Math.max(8, Math.min(100, Math.round((activity / Math.max(1, Number(node.total || activity || 1))) * 100)))
  return { '--node-activity': `${pct}%` }
}

function globalEdgeLabelStyle(edge: GlobalFlowEdge): Record<string, string> {
  return {
    left: `${edge.labelX}%`,
    top: `${edge.labelY}%`,
  }
}

function briefNodeStyle(index: number): Record<string, string> {
  return { '--brief-delay': `${index * 0.11}s` }
}

function lensNodeStyle(index: number): Record<string, string> {
  return { '--lens-delay': `${index * 0.12}s` }
}

function sideOrbitSignalStyle(index: number, total: number): Record<string, string> {
  const safeTotal = Math.max(1, total)
  const angle = -90 + (360 * index) / safeTotal
  const rad = (angle * Math.PI) / 180
  return {
    left: `${50 + Math.cos(rad) * 39}%`,
    top: `${50 + Math.sin(rad) * 39}%`,
    '--side-orbit-delay': `${index * 0.12}s`,
  }
}

async function scrollToPanel(id: string) {
  activeSidePanel.value = id
  if (id !== 'panel-pulse-overview') {
    pulseLayoutMode.value = 'full'
    await nextTick()
  }
  if (!['panel-global-map', 'panel-run-brief', 'panel-diagnostics'].includes(id)) {
    showDiagnosticSections.value = true
    await nextTick()
  }
  if (typeof document === 'undefined') return
  document.getElementById(id)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

function togglePulseLayoutMode() {
  pulseLayoutMode.value = pulseLayoutMode.value === 'simple' ? 'full' : 'simple'
  if (pulseLayoutMode.value === 'simple') {
    showDiagnosticSections.value = false
    aiSideToolsExpanded.value = false
  }
}

function toggleAiSideTools() {
  aiSideToolsExpanded.value = !aiSideToolsExpanded.value
  if (!aiSideToolsExpanded.value && AI_SIDE_UTILITY_SECTION_IDS.has(activeAiSideSection.value)) {
    activeAiSideSection.value = 'ai-side-rootcause'
  }
}

async function scrollToAiSideSection(id: string) {
  activeAiSideSection.value = id
  if (AI_SIDE_UTILITY_SECTION_IDS.has(id)) {
    aiSideToolsExpanded.value = true
  }
  await nextTick()
  if (typeof document === 'undefined') return
  document.getElementById(id)?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' })
}

async function openAiSideHudCard(card: AiSideHudCard) {
  const detail: NonNullable<Detail> = {
    type: 'ai_side_hud',
    data: {
      title: card.label,
      value: card.value,
      meta: card.meta,
      tone: card.tone,
      target: card.target,
      target_scope: card.targetScope,
      ...card.data,
    },
  }
  setSelected(detail)
  if (card.targetScope === 'panel') {
    await scrollToPanel(card.target)
    return
  }
  await scrollToAiSideSection(card.target)
}

async function openAiMissionBrief() {
  const brief = aiMissionBrief.value
  const detail: NonNullable<Detail> = {
    type: 'ai_mission_brief',
    data: {
      title: brief.title,
      kicker: brief.kicker,
      meta: brief.meta,
      output: brief.output,
      confidence: brief.confidence,
      tone: brief.tone,
      target: brief.target,
      steps: aiMissionSteps.value,
      ...brief.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'mission',
    label: brief.title,
    meta: brief.output,
    icon: brief.icon,
    tone: brief.tone,
    target: brief.target,
    motion: aiStickyCommand.value.motion,
    filters: aiStickyCommand.value.filters,
    detail,
  })
  if (brief.target.startsWith('ai-side-')) {
    await scrollToAiSideSection(brief.target)
    return
  }
  await scrollToPanel(brief.target)
}

async function openAiMissionStep(step: AiMissionStep) {
  if (step.filters) {
    applyPulseFilters(step.filters)
  }
  const detail: NonNullable<Detail> = {
    type: 'ai_mission_step',
    data: {
      title: step.label,
      value: step.value,
      meta: step.meta,
      status: step.status,
      progress: step.progress,
      tone: step.tone,
      target: step.target,
      filters: step.filters || {},
      mission: aiMissionBrief.value,
      ...step.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'mission',
    label: `任务 · ${step.label}`,
    meta: `${step.value} · ${step.meta}`,
    icon: step.icon,
    tone: step.tone,
    target: step.target,
    motion: step.key === 'execute' ? aiStickyCommand.value.motion : undefined,
    filters: step.filters,
    detail,
  })
  if (step.target.startsWith('ai-side-')) {
    await scrollToAiSideSection(step.target)
  } else {
    await scrollToPanel(step.target)
  }
  if (step.filters) await reloadAll()
}

function syncActiveAiSideSectionFromScroll() {
  const root = aiSideMenuRef.value
  if (!root || typeof document === 'undefined') return
  const maxScroll = Math.max(0, root.scrollHeight - root.clientHeight)
  aiSideScrollProgress.value = maxScroll ? Math.round(Math.min(1, Math.max(0, root.scrollTop / maxScroll)) * 100) : 0
  const rootRect = root.getBoundingClientRect()
  const targetY = rootRect.top + Math.min(190, Math.max(116, root.clientHeight * 0.22))
  const visible = aiSideSectionAnchors.value
    .flatMap((item) => {
      const rect = document.getElementById(item.id)?.getBoundingClientRect()
      return rect && rect.height > 0 ? [{ id: item.id, rect }] : []
    })
  if (!visible.length) return
  const current = visible.find((item) => item.rect.top <= targetY && item.rect.bottom >= targetY)
  if (current) {
    activeAiSideSection.value = current.id
    return
  }
  visible.sort((a, b) => Math.abs(a.rect.top - targetY) - Math.abs(b.rect.top - targetY))
  activeAiSideSection.value = visible[0].id
}

let tickingAiSideSectionSync = false
function requestAiSideSectionSync() {
  if (tickingAiSideSectionSync || typeof window === 'undefined') return
  tickingAiSideSectionSync = true
  window.requestAnimationFrame(() => {
    tickingAiSideSectionSync = false
    syncActiveAiSideSectionFromScroll()
  })
}

async function openSideRouteStep(step: AiSideRouteStep) {
  if (step.filters) {
    applyPulseFilters(step.filters)
  }
  setSelected({
    type: 'ai_route_step',
    data: {
      title: step.label,
      value: step.value,
      status: step.status,
      tone: step.tone,
      ...step.data,
    },
  })
  await scrollToPanel(step.target)
  if (step.filters) await reloadAll()
}

async function openMotionMeter(meter: AiMotionMeter) {
  if (meter.filters) {
    applyPulseFilters(meter.filters)
  }
  setSelected({
    type: 'ai_motion_meter',
    data: {
      title: meter.label,
      value: meter.value,
      meta: meter.meta,
      percent: meter.percent,
      tone: meter.tone,
      ...meter.data,
    },
  })
  await scrollToPanel(meter.target)
  if (meter.filters) await reloadAll()
}

let aiActionTrailSeq = 0

function pushAiActionTrail(item: Omit<AiActionTrailItem, 'id'>) {
  aiActionTrailSeq += 1
  const deduped = aiActionTrail.value.filter((trail) => `${trail.type}:${trail.label}` !== `${item.type}:${item.label}`)
  aiActionTrail.value = [
    { id: `ai-trail-${aiActionTrailSeq}`, ...item },
    ...deduped,
  ].slice(0, 4)
}

async function replayAiActionTrail(item: AiActionTrailItem) {
  if (item.reset) {
    resetFilters(false)
  } else if (item.filters) {
    applyPulseFilters(item.filters)
  }
  if (item.motion) {
    motionMode.value = item.motion
  }
  setSelected(item.detail)
  await scrollToPanel(item.target)
  if (item.reset || item.filters) await reloadAll()
}

function clearAiActionTrail() {
  aiActionTrail.value = []
}

async function openAiHeartbeatCore() {
  const detail: NonNullable<Detail> = {
    type: 'ai_heartbeat',
    data: {
      title: '自动处理状态',
      ...aiHeartbeat.value.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'heartbeat',
    label: '自动处理状态',
    meta: aiHeartbeat.value.meta,
    icon: 'spark',
    tone: aiHeartbeat.value.tone,
    target: 'panel-automation',
    detail,
  })
  await scrollToPanel('panel-automation')
}

async function openAiHeartbeatStep(step: AiHeartbeatStep) {
  if (step.filters) {
    applyPulseFilters(step.filters)
  }
  const detail: NonNullable<Detail> = {
    type: 'ai_heartbeat_step',
    data: {
      title: step.label,
      value: step.value,
      meta: step.meta,
      tone: step.tone,
      target: step.target,
      filters: step.filters || {},
      ...step.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'heartbeat',
    label: step.label,
    meta: step.meta,
    icon: step.icon,
    tone: step.tone,
    target: step.target,
    filters: step.filters,
    detail,
  })
  await scrollToPanel(step.target)
  if (step.filters) await reloadAll()
}

function currentPulseFilterSnapshot(): PulseFilterPatch {
  const snapshot: PulseFilterPatch = {}
  Object.entries(filters).forEach(([key, value]) => {
    if (key === 'days') {
      if (Number(value) !== 30) snapshot.days = Number(value)
      return
    }
    if (value !== undefined && value !== null && String(value) !== '') {
      ;(snapshot as Record<string, unknown>)[key] = value
    }
  })
  return snapshot
}

function focusTargetForFilters(patch: PulseFilterPatch = {}) {
  if (patch.candidate_status || patch.target_type) return 'panel-bottleneck'
  if (patch.artifact_status || patch.artifact_kind) return 'panel-automation'
  if (patch.source_type === 'training_job') return 'panel-journey'
  if (patch.source_type || patch.sf_tool || patch.event_type || patch.run_id) return 'panel-river'
  return 'panel-global-map'
}

function actionToneForFocus(focus: QuickFocusFilter): AiActionTrailItem['tone'] {
  if (focus.key === 'blockers') return Number(bottlenecks.value.summary?.total || 0) ? 'warn' : 'good'
  if (focus.key === 'training') return Number(summary.value.training?.samples || manifest.value.sample_total || 0) ? 'good' : 'info'
  if (focus.key === 'all') return 'good'
  return 'info'
}

async function applySideQuickFocus(focus: QuickFocusFilter) {
  await applyQuickFilter(focus)
  const detail: NonNullable<Detail> = {
    type: 'ai_focus_command',
    data: {
      title: focus.label,
      meta: focus.meta,
      filters: focus.filters,
      source: 'side_focus_control',
    },
  }
  setSelected(detail)
  const target = focusTargetForFilters(focus.filters)
  pushAiActionTrail({
    type: 'focus',
    label: focus.label,
    meta: focus.meta,
    icon: focus.icon,
    tone: actionToneForFocus(focus),
    target,
    reset: focus.key === 'all',
    filters: focus.filters,
    detail,
  })
  await scrollToPanel(target)
}

async function clearPulseFilter(key: string) {
  if (!(key in filters)) return
  ;(filters as Record<string, unknown>)[key] = ''
  setSelected({
    type: 'ai_focus_control',
    data: {
      title: '移除聚焦筛选',
      key,
      filters: currentPulseFilterSnapshot(),
    },
  })
  await reloadAll()
}

async function traceCurrentFocus() {
  const snapshot = currentPulseFilterSnapshot()
  const target = focusTargetForFilters(snapshot)
  motionMode.value = 'trace'
  const detail: NonNullable<Detail> = {
    type: 'ai_focus_control',
    data: {
      title: activeFilterChips.value.length ? '查看当前聚焦' : '查看整体状态',
      perspective: activeAiPerspectiveLabel.value,
      chips: activeFilterChips.value,
      filters: snapshot,
      motion: 'trace',
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'focus',
    label: activeFilterChips.value.length ? '查看当前聚焦' : '查看整体状态',
    meta: activeFilterChips.value.length ? aiFocusNarrative.value : '详细视图 · 重点查看模式',
    icon: 'search',
    tone: activeFilterChips.value.length ? 'info' : 'good',
    target,
    motion: 'trace',
    filters: snapshot,
    detail,
  })
  await scrollToPanel(target)
}

async function openAdvancedFilters() {
  filtersExpanded.value = true
  showDiagnosticSections.value = true
  setSelected({
    type: 'ai_focus_control',
    data: {
      title: '打开高级筛选',
      filters: currentPulseFilterSnapshot(),
      active_chips: activeFilterChips.value,
    },
  })
  await scrollToPanel('panel-filters')
}

async function openPulseOverviewCard(card: PulseOverviewCard) {
  if (card.filters) {
    applyPulseFilters(card.filters)
  }
  const detail: NonNullable<Detail> = {
    type: 'pulse_overview',
    data: {
      title: card.label,
      value: card.value,
      meta: card.meta,
      tone: card.tone,
      target: card.target,
      filters: card.filters || {},
      layout_mode: pulseLayoutMode.value,
      ...card.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'mission',
    label: `总览 · ${card.label}`,
    meta: `${card.value} · ${card.meta}`,
    icon: card.icon,
    tone: card.tone,
    target: card.target,
    filters: card.filters,
    detail,
  })
  if (card.target.startsWith('ai-side-')) {
    await scrollToAiSideSection(card.target)
  } else {
    await scrollToPanel(card.target)
  }
  if (card.filters) await reloadAll()
}

async function openPulseDecisionItem(item: PulseDecisionItem) {
  if (item.filters) {
    applyPulseFilters(item.filters)
  }
  const detail: NonNullable<Detail> = {
    type: 'ai_overview_decision',
    data: {
      title: item.label,
      value: item.value,
      meta: item.meta,
      tone: item.tone,
      target: item.target,
      filters: item.filters || {},
      ...item.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'overview',
    label: item.label,
    meta: `${item.value} · ${item.meta}`,
    icon: item.icon,
    tone: item.tone,
    target: item.target,
    filters: item.filters,
    detail,
  })
  if (item.target.startsWith('ai-side-')) {
    await scrollToAiSideSection(item.target)
  } else {
    await scrollToPanel(item.target)
  }
  if (item.filters) await reloadAll()
}

async function openAutopilotStage(stage: AiAutopilotStage) {
  if (stage.filters) {
    applyPulseFilters(stage.filters)
  }
  const detail: NonNullable<Detail> = {
    type: 'ai_autopilot_stage',
    data: {
      title: stage.label,
      value: stage.value,
      status: stage.status,
      meta: stage.meta,
      progress: stage.progress,
      tone: stage.tone,
      ...stage.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'autopilot',
    label: stage.label,
    meta: stage.meta,
    icon: stage.icon,
    tone: stage.tone,
    target: stage.target,
    filters: stage.filters,
    detail,
  })
  await scrollToPanel(stage.target)
  if (stage.filters) await reloadAll()
}

async function openAiEvidence(signal: AiEvidenceSignal) {
  if (signal.filters) {
    applyPulseFilters(signal.filters)
  }
  const detail: NonNullable<Detail> = {
    type: 'ai_evidence_signal',
    data: {
      title: signal.label,
      value: signal.value,
      meta: signal.meta,
      strength: signal.strength,
      tone: signal.tone,
      target: signal.target,
      ...signal.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'evidence',
    label: signal.label,
    meta: signal.meta,
    icon: signal.icon,
    tone: signal.tone,
    target: signal.target,
    filters: signal.filters,
    detail,
  })
  await scrollToPanel(signal.target)
  if (signal.filters) await reloadAll()
}

async function openAiRootCause(cause: AiRootCause) {
  if (cause.filters) {
    applyPulseFilters(cause.filters)
  }
  if (cause.tone === 'bad' || cause.tone === 'warn') {
    motionMode.value = 'trace'
  }
  const detail: NonNullable<Detail> = {
    type: 'ai_root_cause',
    data: {
      title: cause.label,
      reason: cause.reason,
      evidence: cause.evidence,
      action: cause.action,
      score: cause.score,
      tone: cause.tone,
      target: cause.target,
      filters: cause.filters || {},
      ...cause.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'rootcause',
    label: cause.label,
    meta: cause.reason,
    icon: cause.icon,
    tone: cause.tone,
    target: cause.target,
    motion: cause.tone === 'bad' || cause.tone === 'warn' ? 'trace' : undefined,
    filters: cause.filters,
    detail,
  })
  await scrollToPanel(cause.target)
  if (cause.filters) await reloadAll()
}

async function openAiImpactForecast(impact: AiImpactForecast) {
  if (impact.filters) {
    applyPulseFilters(impact.filters)
  }
  if (impact.tone === 'bad' || impact.tone === 'warn') {
    motionMode.value = 'trace'
  }
  const detail: NonNullable<Detail> = {
    type: 'ai_impact_forecast',
    data: {
      title: impact.label,
      value: impact.value,
      meta: impact.meta,
      change: impact.change,
      score: impact.score,
      tone: impact.tone,
      target: impact.target,
      filters: impact.filters || {},
      ...impact.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'impact',
    label: impact.label,
    meta: `${impact.value} · ${impact.change}`,
    icon: impact.icon,
    tone: impact.tone,
    target: impact.target,
    motion: impact.tone === 'bad' || impact.tone === 'warn' ? 'trace' : undefined,
    filters: impact.filters,
    detail,
  })
  await scrollToPanel(impact.target)
  if (impact.filters) await reloadAll()
}

async function openAiHeatSignal(signal: AiHeatSignal) {
  if (signal.filters) {
    applyPulseFilters(signal.filters)
  }
  const detail: NonNullable<Detail> = {
    type: 'ai_heat_signal',
    data: {
      title: signal.label,
      value: signal.value,
      meta: signal.meta,
      score: signal.score,
      tone: signal.tone,
      target: signal.target,
      ...signal.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'heat',
    label: signal.label,
    meta: signal.meta,
    icon: signal.icon,
    tone: signal.tone,
    target: signal.target,
    filters: signal.filters,
    detail,
  })
  await scrollToPanel(signal.target)
  if (signal.filters) await reloadAll()
}

async function openAiExecutionGuardrail(guard: AiExecutionGuardrail) {
  const command = aiStickyCommand.value
  const detail: NonNullable<Detail> = {
    type: 'ai_execution_guardrail',
    data: {
      title: guard.label,
      value: guard.value,
      meta: guard.meta,
      tone: guard.tone,
      target: guard.target,
      command: {
        label: command.label,
        cta: command.cta,
        source: command.source,
        target: command.target,
        filters: command.filters || {},
        motion: command.motion || 'auto',
      },
      confidence: aiStickyCommandConfidence.value,
      ...guard.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'guardrail',
    label: `检查 · ${guard.label}`,
    meta: `${guard.value} · ${guard.meta}`,
    icon: guard.icon,
    tone: guard.tone,
    target: guard.target,
    motion: command.motion,
    filters: command.filters,
    detail,
  })
  if (guard.target.startsWith('ai-side-')) {
    await scrollToAiSideSection(guard.target)
    return
  }
  await scrollToPanel(guard.target)
}

async function openPrimaryGuardrail() {
  const guard = aiExecutionGuardrails.value.find((item) => item.key === 'write') || aiExecutionGuardrails.value[0]
  if (guard) await openAiExecutionGuardrail(guard)
}

async function openAiCommandPlanStep(step: AiCommandPlanStep) {
  if (step.filters) {
    applyPulseFilters(step.filters)
  }
  if (step.motion) {
    motionMode.value = step.motion
  }
  const detail: NonNullable<Detail> = {
    type: 'ai_command_plan',
    data: {
      title: step.label,
      phase: step.phase,
      value: step.value,
      meta: step.meta,
      cta: step.cta,
      score: step.score,
      tone: step.tone,
      target: step.target,
      filters: step.filters || {},
      motion: step.motion || motionMode.value,
      ...step.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'plan',
    label: `${step.phase} · ${step.label}`,
    meta: step.meta,
    icon: step.icon,
    tone: step.tone,
    target: step.target,
    motion: step.motion,
    filters: step.filters,
    detail,
  })
  await scrollToPanel(step.target)
  if (step.filters) await reloadAll()
}

async function openAiDecisionQueueItem(item: AiDecisionQueueItem) {
  if (item.filters) {
    applyPulseFilters(item.filters)
  }
  const detail: NonNullable<Detail> = {
    type: 'ai_decision_queue',
    data: {
      title: item.label,
      value: item.value,
      meta: item.meta,
      cta: item.cta,
      score: item.score,
      tone: item.tone,
      target: item.target,
      filters: item.filters || {},
      ...item.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'decision',
    label: item.label,
    meta: item.meta,
    icon: item.icon,
    tone: item.tone,
    target: item.target,
    filters: item.filters,
    detail,
  })
  await scrollToPanel(item.target)
  if (item.filters) await reloadAll()
}

async function applyAiPlaybook(playbook: AiPlaybook) {
  if (playbook.reset) {
    resetFilters(false)
  } else if (playbook.filters) {
    applyPulseFilters(playbook.filters)
  }
  motionMode.value = playbook.motion
  const detail: NonNullable<Detail> = {
    type: 'ai_playbook',
    data: {
      title: playbook.label,
      meta: playbook.meta,
      cta: playbook.cta,
      score: playbook.score,
      motion: playbook.motion,
      target: playbook.target,
      filters: playbook.filters || {},
      ...playbook.data,
    },
  }
  setSelected(detail)
  pushAiActionTrail({
    type: 'playbook',
    label: playbook.label,
    meta: playbook.meta,
    icon: playbook.icon,
    tone: playbook.tone,
    target: playbook.target,
    motion: playbook.motion,
    reset: playbook.reset,
    filters: playbook.filters,
    detail,
  })
  await scrollToPanel(playbook.target)
  if (playbook.reset || playbook.filters) await reloadAll()
}

async function runAutopilotPrimary() {
  const action = aiAutopilotPrimary.value
  if (action.filters) {
    applyPulseFilters(action.filters)
  }
  if (action.kind === 'capture') {
    await backfill(false)
    return
  }
  await scrollToPanel(action.target)
  if (action.kind === 'govern') {
    const section = action.data.section as LearningBottleneckSection | undefined
    if (section) {
      openBottleneckSection(section)
    } else {
      setSelected({ type: 'ai_autopilot_action', data: action.data })
    }
    if (action.filters) await reloadAll()
    return
  }
  setSelected({
    type: 'ai_autopilot_action',
    data: {
      title: action.label,
      meta: action.meta,
      cta: action.cta,
      kind: action.kind,
      ...action.data,
    },
  })
  if (action.kind === 'refresh') {
    await reloadAll()
    return
  }
  if (action.filters) await reloadAll()
}

async function runSideAction(action: AiSideAction) {
  if (action.target) await scrollToPanel(action.target)
  if (action.kind === 'capture') {
    await backfill(false)
    return
  }
  if (action.kind === 'bottleneck') {
    const section = action.data.section as LearningBottleneckSection | undefined
    if (section) {
      openBottleneckSection(section)
      return
    }
  }
  if (action.kind === 'automation' && action.key === 'healthy-loop') {
    await runAutomationNow()
    return
  }
  setSelected({ type: 'ai_side_action', data: action.data })
}

async function runAiStickyCommand() {
  const command = aiStickyCommand.value
  if (command.source === 'decision') {
    const topDecision = aiDecisionQueue.value[0]
    if (topDecision) {
      await openAiDecisionQueueItem(topDecision)
      return
    }
  }
  await runAutopilotPrimary()
}

function syncActiveSidePanelFromScroll() {
  if (typeof document === 'undefined' || typeof window === 'undefined') return
  const ids = aiSideNavItems.value.map((item) => item.id)
  const visible = ids
    .flatMap((id) => {
      const rect = document.getElementById(id)?.getBoundingClientRect()
      return rect && rect.height > 0 ? [{ id, rect }] : []
    })
  if (!visible.length) return
  const targetY = 132
  const current = visible.find((item) => item.rect.top <= targetY && item.rect.bottom >= targetY)
  if (current) {
    activeSidePanel.value = current.id
    return
  }
  visible.sort((a, b) => Math.abs(a.rect.top - targetY) - Math.abs(b.rect.top - targetY))
  activeSidePanel.value = visible[0].id
}

let tickingSidePanelSync = false
function requestSidePanelSync() {
  if (tickingSidePanelSync || typeof window === 'undefined') return
  tickingSidePanelSync = true
  window.requestAnimationFrame(() => {
    tickingSidePanelSync = false
    syncActiveSidePanelFromScroll()
  })
}

function isQuickFocusActive(focus: QuickFocusFilter) {
  if (focus.key === 'all') return !activeFilterChips.value.length
  return Object.entries(focus.filters).every(([key, value]) => {
    const current = (filters as Record<string, unknown>)[key]
    return String(current || '') === String(value || '')
  })
}

function isAiPerspectiveActive(view: AiPerspective) {
  if (view.key === 'inspect') return !activeFilterChips.value.length
  return Object.entries(view.filters).every(([key, value]) => {
    const current = (filters as Record<string, unknown>)[key]
    return String(current || '') === String(value || '')
  })
}

async function applyAiPerspective(view: AiPerspective) {
  if (view.key === 'inspect') {
    resetFilters(false)
  } else {
    applyPulseFilters(view.filters)
  }
  setSelected({
    type: 'ai_perspective',
    data: {
      title: view.label,
      meta: view.meta,
      filters: view.filters,
      target: view.target,
      ...view.data,
    },
  })
  await scrollToPanel(view.target)
  await reloadAll()
}

function openGlobalFlowNode(node: GlobalFlowNode) {
  activeGlobalNodeKey.value = node.key
  activeGlobalEdgeKey.value = ''
}

function openGlobalFlowNodeByKey(key: string) {
  const node = globalFlowNodes.value.find((item) => item.key === key)
  if (node) openGlobalFlowNode(node)
}

async function focusGlobalFlowNode(node: GlobalFlowNode) {
  openGlobalFlowNode(node)
  if (node.filters) {
    applyPulseFilters(node.filters)
    await reloadAll()
  }
}

function openGlobalFlowEdge(edge: GlobalFlowEdge) {
  activeGlobalEdgeKey.value = edge.key
  activeGlobalNodeKey.value = edge.to
  selected.value = {
    type: 'global_flow_edge',
    data: {
      title: edge.label,
      from: globalFlowNodes.value.find((node) => node.key === edge.from)?.label || edge.from,
      to: globalFlowNodes.value.find((node) => node.key === edge.to)?.label || edge.to,
      count: edge.count,
      new_count: edge.newCount,
      update_count: edge.updateCount,
      path: edge.path,
    },
  }
}

function openGlobalDetailMetric(metric: GlobalDetailMetric) {
  if (metric.key === 'new' && activeGlobalAdditions.value.length) {
    setSelected({ type: 'global_node_addition', data: metric.data })
    return
  }
  if (metric.key === 'update' && activeGlobalUpdates.value.length) {
    setSelected({ type: 'global_node_update', data: metric.data })
    return
  }
  setSelected({ type: 'global_node_metric', data: metric.data })
}

async function activatePulseNode(node: PulseNode) {
  activePulseNode.value = node.key
  selected.value = {
    type: 'pulse_signal',
    data: {
      id: node.key,
      title: node.label,
      metric: node.value,
      meta: node.meta,
      tone: node.tone,
      filters: node.filters,
      hint: '点击动态标记后页面已聚焦到该系统记录，可继续查看下方处理链路和待处理事项。',
    },
  }
  applyPulseFilters(node.filters)
  await reloadAll()
}

async function activatePulseInsight(insight: PulseInsight) {
  selected.value = {
    type: 'pulse_insight',
    data: {
      title: insight.label,
      value: insight.value,
      meta: insight.meta,
      tone: insight.tone,
      ...insight.data,
    },
  }
  if (insight.filters) {
    applyPulseFilters(insight.filters)
    await reloadAll()
  }
}

function showPulseCore() {
  selected.value = {
    type: 'pulse_core',
    data: {
      title: pulseHealth.value.title,
      health: pulseHealth.value.main,
      score: pulseHealth.value.score,
      detail: pulseHealth.value.detail,
      summary: summary.value,
      automation: automation.value.automation,
      blockers: bottlenecks.value.summary,
      journey_state: journeyFlow.value.state_counts,
    },
  }
}

function applyPulseFilters(patch: PulseFilterPatch) {
  const focusKeys: Array<keyof PulseFilterPatch> = [
    'source_type',
    'event_type',
    'artifact_kind',
    'target_type',
    'sf_tool',
    'run_id',
    'artifact_status',
    'candidate_status',
  ]
  focusKeys.forEach((key) => {
    ;(filters as Record<string, unknown>)[String(key)] = String(patch[key] ?? '')
  })
  if (patch.skill_id !== undefined) filters.skill_id = String(patch.skill_id || '')
  if (patch.department !== undefined) filters.department = String(patch.department || '')
  if (patch.q !== undefined) filters.q = String(patch.q || '')
  if (patch.days !== undefined && Number.isFinite(Number(patch.days))) {
    filters.days = Number(patch.days)
  }
}

async function applyQuickFilter(focus: QuickFocusFilter) {
  if (focus.key === 'all') {
    resetFilters(false)
  } else {
    applyPulseFilters(focus.filters)
  }
  selected.value = {
    type: 'quick_focus',
    data: {
      title: focus.label,
      meta: focus.meta,
      filters: focus.filters,
    },
  }
  await reloadAll()
}

async function openBriefItem(item: BriefItem) {
  selected.value = {
    type: 'system_brief',
    data: {
      title: item.label,
      value: item.value,
      meta: item.meta,
      action: item.action,
      tone: item.tone,
      ...item.data,
    },
  }
  if (item.key === 'latest' && !events.value.length) {
    await backfill(false)
    return
  }
  if (item.filters) {
    applyPulseFilters(item.filters)
    await reloadAll()
  }
}

function resetFilters(shouldReload = true) {
  filters.skill_id = ''
  filters.department = ''
  filters.run_id = ''
  filters.source_type = ''
  filters.event_type = ''
  filters.artifact_kind = ''
  filters.target_type = ''
  filters.sf_tool = ''
  filters.q = ''
  filters.artifact_status = ''
  filters.candidate_status = ''
  if (shouldReload) void reloadAll()
}

function streamSignalStyle(index: number, total: number): Record<string, string> {
  const safeTotal = Math.max(1, total)
  const pct = safeTotal <= 1 ? 50 : 8 + (84 * index) / (safeTotal - 1)
  const top = 45 + Math.sin(index * 1.7) * 24
  return {
    left: `${pct}%`,
    top: `${Math.max(18, Math.min(78, top))}%`,
    '--stream-delay': `${index * 0.13}s`,
  }
}

function materialTokenStyle(index: number, total: number): Record<string, string> {
  const safeTotal = Math.max(1, total)
  const pct = safeTotal <= 1 ? 50 : 7 + (86 * index) / (safeTotal - 1)
  const top = 50 + Math.sin((index / safeTotal) * Math.PI * 2) * 30
  return {
    left: `${pct}%`,
    top: `${Math.max(16, Math.min(84, top))}%`,
    '--token-delay': `${index * 0.16}s`,
  }
}

function bottleneckNodeStyle(index: number, total: number): Record<string, string> {
  const safeTotal = Math.max(1, total)
  const angle = -92 + (360 * index) / safeTotal
  const rad = (angle * Math.PI) / 180
  const radiusX = safeTotal <= 2 ? 29 : 41
  const radiusY = safeTotal <= 2 ? 32 : 39
  return {
    left: `${50 + Math.cos(rad) * radiusX}%`,
    top: `${50 + Math.sin(rad) * radiusY}%`,
    '--radar-delay': `${index * 0.16}s`,
  }
}

function flowStageStyle(index: number, total: number): Record<string, string> {
  const safeTotal = Math.max(1, total)
  const angle = -90 + (360 * index) / safeTotal
  const rad = (angle * Math.PI) / 180
  return {
    left: `${50 + Math.cos(rad) * 42}%`,
    top: `${50 + Math.sin(rad) * 39}%`,
    '--stage-delay': `${index * 0.14}s`,
  }
}

function flowConnectorLabelStyle(index: number, total: number): Record<string, string> {
  const safeTotal = Math.max(1, total)
  const angle = -58 + (300 * index) / safeTotal
  const rad = (angle * Math.PI) / 180
  return {
    left: `${50 + Math.cos(rad) * 26}%`,
    top: `${50 + Math.sin(rad) * 24}%`,
    '--connector-delay': `${index * 0.11}s`,
  }
}

function journeyProgressPercent(journey: LearningFlowJourneyRow) {
  return Math.max(6, Math.min(100, Math.round(Number(journey.progress || 0) * 100)))
}

function journeyRailStyle(journey: LearningFlowJourneyRow): Record<string, string> {
  const pct = journeyProgressPercent(journey)
  return {
    '--journey-progress-width': `${Math.max(5, pct * 0.84)}%`,
    '--journey-cursor-left': `${8 + pct * 0.84}%`,
  }
}

function journeyStepStyle(index: number, total: number): Record<string, string> {
  const safeTotal = Math.max(1, total)
  const pct = safeTotal <= 1 ? 50 : 8 + (84 * index) / (safeTotal - 1)
  const lift = index % 2 === 0 ? -18 : 18
  return {
    left: `${pct}%`,
    top: `calc(50% + ${lift}px)`,
    '--journey-step-delay': `${index * 0.12}s`,
  }
}

function manifestSkillStyle(index: number, total: number): Record<string, string> {
  const safeTotal = Math.max(1, total)
  const pct = safeTotal <= 1 ? 50 : 10 + (80 * index) / (safeTotal - 1)
  return {
    left: `${pct}%`,
    '--manifest-delay': `${index * 0.14}s`,
  }
}

function lineageNodeStyle(index: number, total: number): Record<string, string> {
  const safeTotal = Math.max(1, total)
  const angle = -90 + (360 * index) / safeTotal
  const rad = (angle * Math.PI) / 180
  const radius = safeTotal <= 2 ? 30 : 38
  return {
    left: `${50 + Math.cos(rad) * radius}%`,
    top: `${50 + Math.sin(rad) * radius}%`,
    '--lineage-delay': `${index * 0.1}s`,
  }
}

function openEventStream(item: EventStreamItem) {
  selected.value = {
    type: item.data.event_id ? 'flow_journey' : 'event',
    data: item.data,
  }
}

function openMaterialFlow(item: MaterialFlowItem) {
  selected.value = {
    type: item.type,
    data: item.data,
  }
}

function openFlowStage(stage: FlowStage) {
  setSelected({
    type: 'flow_stage',
    data: {
      key: stage.key,
      title: stage.title,
      caption: stage.caption,
      count: stage.count,
      tone: stage.tone,
      nodes: stage.nodes.map((node) => node.payload),
    },
  })
}

function openFlowConnector(index: number) {
  const connector = connectorAt(index)
  setSelected({
    type: 'flow_connector',
    data: {
      label: connectorLabel(index),
      count: connectorCount(index),
      from: flowStages.value[index]?.key,
      to: flowStages.value[index + 1]?.key,
      relation: connector?.relation,
      intensity: connectorOpacity(index),
      connector: connector || null,
    },
  })
}

function cleanParams(payload: Record<string, unknown>) {
  return Object.fromEntries(
    Object.entries(payload).filter(([, value]) => value !== undefined && value !== null && value !== ''),
  )
}

function commonParams(extra: Record<string, unknown> = {}) {
  return cleanParams({
    days: filters.days,
    skill_id: filters.skill_id,
    department: filters.department,
    run_id: filters.run_id,
    source_type: filters.source_type,
    event_type: filters.event_type,
    sf_tool: filters.sf_tool,
    q: filters.q,
    ...extra,
  })
}

function summaryParams() {
  return commonParams({
    artifact_kind: filters.artifact_kind,
    target_type: filters.target_type,
  })
}

function eventParams(extra: Record<string, unknown> = {}) {
  return commonParams(extra)
}

function artifactParams(extra: Record<string, unknown> = {}) {
  return commonParams({
    artifact_kind: filters.artifact_kind,
    status: filters.artifact_status,
    ...extra,
  })
}

function candidateParams(extra: Record<string, unknown> = {}) {
  return commonParams({
    target_type: filters.target_type,
    status: filters.candidate_status,
    ...extra,
  })
}

function topologyParams(extra: Record<string, unknown> = {}) {
  return commonParams({
    artifact_kind: filters.artifact_kind,
    target_type: filters.target_type,
    status: filters.artifact_status || filters.candidate_status,
    ...extra,
  })
}

function journeyParams(extra: Record<string, unknown> = {}) {
  return commonParams({
    artifact_kind: filters.artifact_kind,
    artifact_status: filters.artifact_status,
    target_type: filters.target_type,
    candidate_status: filters.candidate_status,
    ...extra,
  })
}

async function reloadPulse(options: { background?: boolean } = {}) {
  const background = Boolean(options.background)
  if (background && pulseRequestInFlight) return
  const pulseSeq = ++pulseRequestSeq
  pulseRequestInFlight = true
  if (!background || !pulse.value.generated_at) {
    pulseLoading.value = true
  } else {
    pulseRefreshing.value = true
  }
  if (!background) {
    pulseError.value = ''
    pulse.value = { modules: [] }
  }
  try {
    const res = await learningApi.pulse(topologyParams({ limit: 6 }))
    if (pulseSeq !== pulseRequestSeq) return
    pulse.value = res || { modules: [] }
    pulseError.value = ''
    syncSelectedPulseDetail(pulse.value.modules || [])
  } catch (error: any) {
    if (pulseSeq !== pulseRequestSeq) return
    pulseError.value = String(error?.message || error || 'pulse_failed')
    if (!background || !(pulse.value.modules || []).length) {
      pulse.value = { modules: [] }
    }
  } finally {
    if (pulseSeq === pulseRequestSeq) {
      pulseLoading.value = false
      pulseRefreshing.value = false
      pulseRequestInFlight = false
    }
  }
}

async function reloadDetailLists() {
  if (detailListsLoading) return detailListsLoading
  detailListsLoading = (async () => {
    const [e, a, c, g, j, b, m] = await Promise.all([
      learningApi.events(eventParams({ limit: 120 })),
      learningApi.artifacts(artifactParams({ limit: 120 })),
      learningApi.candidates(candidateParams({ limit: 120 })),
      learningApi.flowGraph(commonParams({ limit: 250 })),
      learningApi.flowJourneys(journeyParams({ limit: 8 })),
      learningApi.bottlenecks(topologyParams({ limit: 12 })),
      learningApi.trainingManifest(cleanParams({ skill_id: filters.skill_id, limit: 300 })),
    ])
    events.value = e?.items || []
    artifacts.value = a?.items || []
    candidates.value = c?.items || []
    graph.value = g || { nodes: [], edges: [] }
    journeyFlow.value = j || { items: [], state_counts: {}, blocker_counts: {} }
    bottlenecks.value = b || { summary: {}, sections: [] }
    manifest.value = m || {}
    detailListsLoaded = true
  })()
  try {
    await detailListsLoading
  } finally {
    detailListsLoading = null
  }
}

async function ensureDetailListsLoaded() {
  if (detailListsLoaded) return
  await reloadDetailLists()
}

async function reloadHome() {
  const home = await learningApi.home(summaryParams())
  summary.value = home?.summary || {}
  topology.value = home?.topology || { stages: [], connectors: [] }
  automation.value = home?.automation || {}
}

async function reloadAll(options: { includeDetails?: boolean } = {}) {
  const includeDetails = options.includeDetails ?? hasCompletedInitialHomeLoad
  loading.value = true
  void reloadPulse({ background: false })
  try {
    await reloadHome()
    hasCompletedInitialHomeLoad = true
    if (includeDetails) {
      await reloadDetailLists()
    }
    await syncRouteQuery()
  } finally {
    loading.value = false
  }
}

async function reloadFromUi() {
  await reloadAll({ includeDetails: true })
}

function queryText(key: string) {
  const value = route.query[key]
  return Array.isArray(value) ? String(value[0] || '') : String(value || '')
}

function applyRouteQuery() {
  const days = Number(queryText('days'))
  if (Number.isFinite(days) && days > 0) filters.days = Math.max(1, Math.min(365, Math.round(days)))
  ;([
    'skill_id',
    'department',
    'run_id',
    'source_type',
    'event_type',
    'artifact_kind',
    'target_type',
    'sf_tool',
    'q',
    'artifact_status',
    'candidate_status',
  ] as const).forEach((key) => {
    filters[key] = queryText(key)
  })
}

async function syncRouteQuery() {
  const query = cleanParams({
    days: filters.days === 30 ? undefined : filters.days,
    skill_id: filters.skill_id,
    department: filters.department,
    run_id: filters.run_id,
    source_type: filters.source_type,
    event_type: filters.event_type,
    artifact_kind: filters.artifact_kind,
    target_type: filters.target_type,
    sf_tool: filters.sf_tool,
    q: filters.q,
    artifact_status: filters.artifact_status,
    candidate_status: filters.candidate_status,
    artifact_id: queryText('artifact_id'),
    candidate_id: queryText('candidate_id'),
    event_id: queryText('event_id'),
    entity_type: queryText('entity_type'),
    entity_id: queryText('entity_id'),
  })
  if (JSON.stringify(cleanParams(route.query as Record<string, unknown>)) === JSON.stringify(query)) return
  await router.replace({ path: route.path || '/learning-flow', query: query as Record<string, string | number> })
}

function applyDeepSelectionFromQuery() {
  const artifactId = queryText('artifact_id')
  if (artifactId) {
    const item = artifacts.value.find((row) => row.id === artifactId)
    setSelected({ type: item ? 'artifact' : 'entity', data: item ? item as Record<string, unknown> : { entity_type: 'learning_artifact', entity_id: artifactId } })
    return
  }
  const candidateId = queryText('candidate_id')
  if (candidateId) {
    const item = candidates.value.find((row) => row.id === candidateId)
    setSelected({ type: item ? 'candidate' : 'entity', data: item ? item as Record<string, unknown> : { entity_type: 'improvement_candidate', entity_id: candidateId } })
    return
  }
  const eventId = queryText('event_id')
  if (eventId) {
    const item = events.value.find((row) => row.id === eventId)
    setSelected({ type: item ? 'event' : 'entity', data: item ? item as Record<string, unknown> : { entity_type: 'learning_event', entity_id: eventId } })
    return
  }
  const entityType = queryText('entity_type')
  const entityId = queryText('entity_id')
  if (entityType && entityId) {
    setSelected({ type: 'entity', data: { entity_type: entityType, entity_id: entityId } })
  }
}

async function reloadArtifacts() {
  const a = await learningApi.artifacts(artifactParams({ limit: 120 }))
  artifacts.value = a?.items || []
}

async function backfill(materialize: boolean) {
  backfilling.value = true
  try {
    const res = await learningApi.backfill({ days: filters.days, limit: 300, materialize })
    Message.success(materialize ? '已同步记录并尝试生成知识' : '已同步学习记录')
    selected.value = { type: 'backfill_result', data: res as Record<string, unknown> }
    await reloadAll()
  } finally {
    backfilling.value = false
  }
}

async function runAutomationNow() {
  automationRunning.value = true
  try {
    const res = await learningApi.runAutomation({ days: filters.days, limit: 300, materialize: true })
    if (String((res as Record<string, unknown>)?.status || '') === 'failed') {
      Message.info('自动处理失败，已记录原因')
    } else {
      Message.success('已执行一次自动处理')
    }
    setSelected({ type: 'automation_result', data: res as Record<string, unknown> })
    await reloadAll()
  } finally {
    automationRunning.value = false
  }
}

async function runAgentizationNow() {
  agentizing.value = true
  try {
    const res = await learningApi.runAgentization({ days: filters.days, limit: 30, use_ai: true })
    const created = Array.isArray((res as Record<string, unknown>)?.created)
      ? ((res as Record<string, unknown>).created as unknown[]).length
      : 0
    Message.success(created ? `已生成 ${created} 个 Agent 化候选` : '已完成 Agent 化判断，暂无新增候选')
    setSelected({ type: 'agentization_result', data: res as Record<string, unknown> })
    await reloadAll()
  } finally {
    agentizing.value = false
  }
}

async function runSelfAuditNow() {
  selfAuditing.value = true
  try {
    const res = await learningApi.runSelfAudit({ days: filters.days, limit: 50, auto_remediate: true, use_ai: true })
    const data = res as Record<string, unknown>
    const created = Array.isArray(data.created) ? data.created.length : 0
    const remediated = Array.isArray(data.remediated) ? data.remediated.length : 0
    Message.success(created || remediated ? `自审完成：${created} 个缺口，${remediated} 个自动修复` : '自审完成，暂无新增缺口')
    setSelected({ type: 'self_audit_result', data })
    await reloadAll()
  } finally {
    selfAuditing.value = false
  }
}

function canMaterialize(item: LearningArtifactRow) {
  return !['materialized', 'ignored'].includes(String(item.status || ''))
}

async function materialize(item: LearningArtifactRow) {
  await learningApi.materializeArtifact(item.id, { force: true })
  Message.success('已生成知识')
  await reloadAll()
}

async function ignore(item: LearningArtifactRow) {
  await learningApi.ignoreArtifact(item.id, { reason: '页面手动忽略' })
  Message.success('已忽略资产')
  await reloadAll()
}

async function acceptCandidate(item: ImprovementCandidateRow) {
  await learningApi.acceptCandidate(item.id, { reason: '页面采纳' })
  Message.success('已采纳建议')
  await reloadAll()
}

async function rejectCandidate(item: ImprovementCandidateRow) {
  await learningApi.rejectCandidate(item.id, { reason: '页面驳回' })
  Message.success('已驳回建议')
  await reloadAll()
}

async function createReview(item: ImprovementCandidateRow) {
  const res = await learningApi.createCandidateReview(item.id) as Record<string, unknown>
  Message.success(res.decision_request_id ? '已创建评审待办' : '已进入审核队列')
  await reloadAll()
}

async function createTraining(item: ImprovementCandidateRow) {
  await learningApi.createCandidateTrainingJob(item.id, {})
  Message.success('已创建训练建议')
  await reloadAll()
}

async function materializeBottleneck(item: Record<string, unknown>) {
  const id = String(item.id || item.artifact_id || '')
  if (!id) {
    Message.info('该待处理事项缺少资产 ID')
    return
  }
  await materialize({ ...(item as Record<string, unknown>), id } as LearningArtifactRow)
}

async function createReviewBottleneck(item: Record<string, unknown>) {
  const id = String(item.id || item.candidate_id || '')
  if (!id) {
    Message.info('该待处理事项缺少建议 ID')
    return
  }
  await createReview({ ...(item as Record<string, unknown>), id } as ImprovementCandidateRow)
}

async function retryIngestionBottleneck(item: Record<string, unknown>) {
  const artifactId = String(item.artifact_id || '')
  if (!artifactId) {
    Message.info('该失败任务缺少可重试资产 ID')
    return
  }
  await materialize({ id: artifactId, status: 'ready' } as LearningArtifactRow)
}

async function showManifest() {
  await ensureDetailListsLoaded()
  selected.value = { type: 'training_manifest', data: manifest.value as Record<string, unknown> }
}

function selectFlowNode(node: FlowNode) {
  selectedNodeId.value = node.id
  setSelected({ type: 'flow_node', data: node.payload })
}

function openBottleneckSection(section: LearningBottleneckSection) {
  const first = section.items?.[0]
  if (first) {
    openBottleneck(section.key, first)
    return
  }
  setSelected({ type: 'bottleneck_section', data: section as unknown as Record<string, unknown> })
}

function openBottleneck(sectionKey: string, item: Record<string, unknown>) {
  if (sectionKey.includes('artifact')) {
    setSelected({ type: 'artifact', data: item })
    return
  }
  if (sectionKey.includes('candidate')) {
    setSelected({ type: 'candidate', data: item })
    return
  }
  if (sectionKey.includes('event')) {
    setSelected({ type: 'event', data: item })
    return
  }
  if (sectionKey.includes('ingestion')) {
    setSelected({ type: 'ingestion_job', data: item })
    return
  }
  setSelected({ type: 'bottleneck', data: item })
}

function bottleneckTitle(sectionKey: string, item: Record<string, unknown>) {
  if (sectionKey.includes('ingestion')) return String(item.error || item.job_id || item.id || '知识生成任务')
  return String(item.title || item.event_type || item.redacted_summary || item.job_id || item.id || '-')
}

function bottleneckMeta(sectionKey: string, item: Record<string, unknown>) {
  const parts = [
    item.artifact_kind || item.target_type || item.source_type || item.sink_type || sectionKey,
    item.status,
    item.skill_id || item.run_id,
  ].filter(Boolean)
  return parts.join(' · ') || '-'
}

function setSelected(detail: Detail) {
  selected.value = detail
  detailLineage.value = []
  resetDetailTerminal(detail)
}

function pulseModuleIcon(key?: string) {
  const map: Record<string, string> = {
    raw_inputs: 'database',
    clarified_data: 'layers',
    finetuning: 'beaker',
    test_results: 'check',
    application_outputs: 'send',
  }
  return map[String(key || '')] || 'flow'
}

function pulseMetricValue(metric: LearningPulseMetric) {
  const value = metric.value
  const text = value === undefined || value === null || value === '' ? '-' : String(value)
  return `${text}${metric.suffix || ''}`
}

function pulseModuleStatusText(module: PulseModuleView) {
  return learningStatusText(module.status_text || module.status) || '-'
}

function pulseModuleMetrics(module: PulseModuleView): LearningPulseMetric[] {
  return (module.metrics || []).slice(0, 4)
}

function pulseModuleItems(module: PulseModuleView): LearningPulseItem[] {
  const items = (module.items || []).slice(0, 3)
  if (items.length || !module.loading) return items
  return [1, 2, 3].map((index) => ({
    id: `${module.key}-loading-${index}`,
    title: '加载中',
    meta: '等待实时数据返回',
    status: 'loading',
    payload: { loading: true },
  }))
}

function pulseItemTitle(item: LearningPulseItem) {
  return String(item.title || item.id || '-')
}

function pulseItemMeta(item: LearningPulseItem) {
  return learningStatusText(item.meta || item.status) || '-'
}

function learningStatusText(value?: string | null) {
  const text = String(value || '')
  const map: Record<string, string> = {
    ready: '待处理',
    captured: '已采集',
    materialized: '已物化',
    completed: '已完成',
    succeeded: '成功',
    passed: '已通过',
    failed: '失败',
    ignored: '已忽略',
    running: '运行中',
    pending: '等待中',
    open: '待审核',
    reviewing: '审核中',
    awaiting_review: '待审核',
    accepted: '已采纳',
    implemented: '已落地',
    applied: '已应用',
    loading: '加载中',
  }
  if (!text) return ''
  if (map[text]) return map[text]
  return text.split(' · ').map((part) => map[part] || part).join(' · ')
}

function pulseModuleDetailData(module: LearningPulseModule): Record<string, unknown> {
  return {
    title: module.title,
    subtitle: module.subtitle,
    status: module.status,
    status_text: module.status_text,
    module_key: module.key,
    metrics: module.metrics || [],
    detail: module.detail || {},
    flow_detail: module.flow_detail || {},
    filters: module.filters || {},
    items: module.items || [],
  }
}

function pulseMetricDetailData(module: LearningPulseModule, metric: LearningPulseMetric): Record<string, unknown> {
  return {
    title: module.title,
    metric,
    metric_key: metric.key,
    module_key: module.key,
    module_status: module.status,
    detail: module.detail || {},
    flow_detail: module.flow_detail || {},
    filters: module.filters || {},
  }
}

function pulseItemDetailData(module: LearningPulseModule, item: LearningPulseItem): Record<string, unknown> {
  return {
    title: item.title,
    meta: item.meta,
    status: item.status,
    entity_type: item.entity_type,
    entity_id: item.entity_id,
    item_id: item.id,
    module_key: module.key,
    module_title: module.title,
    flow_detail: module.flow_detail || {},
    ...(item.payload || {}),
  }
}

function pulseFlowRowDetailData(
  module: LearningPulseModule,
  section: LearningPulseFlowSection,
  row: LearningPulseFlowRow,
  rowIndex = 0,
): Record<string, unknown> {
  return {
    title: row.title || section.title || '流转记录',
    section: section.title,
    section_key: section.key,
    row_index: rowIndex,
    module_key: module.key,
    module_title: module.title,
    module_status: module.status,
    module_status_text: module.status_text,
    flow_detail: module.flow_detail || {},
    detail_synced_at: pulse.value.generated_at || null,
    ...row,
  }
}

function moduleByKey(key: string): LearningPulseModule | undefined {
  return (pulse.value.modules || []).find((item) => item.key === key)
}

function pulseModuleProcessRows(moduleKey: string, sectionKeys?: string[], limit = 8): DetailProcessingRow[] {
  const module = moduleByKey(moduleKey)
  const rows: DetailProcessingRow[] = []
  if (!module?.flow_detail?.sections?.length) return rows
  const allowed = sectionKeys?.length ? new Set(sectionKeys) : null
  module.flow_detail.sections.forEach((section) => {
    if (allowed && !allowed.has(String(section.key || ''))) return
    ;(section.items || []).forEach((row, rowIndex) => {
      rows.push(detailProcessingRowFromFlowRow(section, row, rowIndex))
    })
  })
  return rows.slice(0, limit)
}

function pulseCountValue(value: unknown, fallback = 0) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function pulseSectionTotal(section: LearningPulseFlowSection) {
  const itemCount = section.items?.length || 0
  return Math.max(itemCount, pulseCountValue(section.total_count, itemCount))
}

function pulseSectionReturned(section: LearningPulseFlowSection) {
  const itemCount = section.items?.length || 0
  return Math.max(itemCount, pulseCountValue(section.returned_count, itemCount))
}

function pulseSectionHidden(section: LearningPulseFlowSection) {
  return Math.max(0, pulseCountValue(section.hidden_count, pulseSectionTotal(section) - pulseSectionReturned(section)))
}

function pulseSectionCountText(section: LearningPulseFlowSection) {
  const returned = pulseSectionReturned(section)
  const total = pulseSectionTotal(section)
  const hidden = pulseSectionHidden(section)
  if (!total) return '共 0 条'
  if (hidden > 0 || total > returned) return `展示 ${numberText(returned)} / 共 ${numberText(total)}，隐藏 ${numberText(Math.max(hidden, total - returned))}`
  return `共 ${numberText(total)} 条`
}

function pulseModuleReturned(module: LearningPulseModule) {
  const ownReturned = Number(module.returned_count)
  const itemCount = module.items?.length || 0
  if (Number.isFinite(ownReturned) && ownReturned > 0) return Math.max(itemCount, ownReturned)
  const sectionReturned = (module.flow_detail?.sections || []).reduce((total, section) => total + pulseSectionReturned(section), 0)
  return Math.max(itemCount, sectionReturned)
}

function pulseModuleTotal(module: LearningPulseModule) {
  const returned = pulseModuleReturned(module)
  const ownTotal = Number(module.total_count)
  if (Number.isFinite(ownTotal) && ownTotal > 0) return Math.max(returned, ownTotal)
  const sectionTotal = (module.flow_detail?.sections || []).reduce((total, section) => total + pulseSectionTotal(section), 0)
  return Math.max(returned, sectionTotal)
}

function pulseModuleHidden(module: LearningPulseModule) {
  return Math.max(0, pulseCountValue(module.hidden_count, pulseModuleTotal(module) - pulseModuleReturned(module)))
}

function pulseModuleCountText(module: LearningPulseModule) {
  const returned = pulseModuleReturned(module)
  const total = pulseModuleTotal(module)
  const hidden = pulseModuleHidden(module)
  if (!total) return '无生产明细'
  if (hidden > 0 || total > returned) return `展示 ${numberText(returned)} / 共 ${numberText(total)}`
  return `共 ${numberText(total)} 条`
}

function pulseDrilldownKey(moduleKey: string, sectionKey?: string) {
  return `${moduleKey}:${sectionKey || ''}`
}

function isPulseSectionDrilldownLoading(moduleKey: string, section: LearningPulseFlowSection) {
  return Boolean(pulseDrilldownLoading.value[pulseDrilldownKey(moduleKey, section.key)])
}

function pulseSectionDrilldownError(moduleKey: string, section: LearningPulseFlowSection) {
  return pulseDrilldownErrors.value[pulseDrilldownKey(moduleKey, section.key)] || ''
}

function canLoadMorePulseSection(moduleKey: string, section: LearningPulseFlowSection) {
  return Boolean(moduleKey && section.key && pulseSectionTotal(section) > pulseSectionReturned(section))
}

async function loadMorePulseSection(moduleKey: string, section: LearningPulseFlowSection) {
  const sectionKey = String(section.key || '')
  if (!moduleKey || !sectionKey) return
  const stateKey = pulseDrilldownKey(moduleKey, sectionKey)
  if (pulseDrilldownLoading.value[stateKey]) return
  pulseDrilldownLoading.value = { ...pulseDrilldownLoading.value, [stateKey]: true }
  pulseDrilldownErrors.value = { ...pulseDrilldownErrors.value, [stateKey]: '' }
  try {
    const currentPage = pulseDrilldownPages.value[stateKey] || 0
    const nextPage = currentPage + 1
    const res = await learningApi.pulseDrilldown(topologyParams({
      module_key: moduleKey,
      section_key: sectionKey,
      page: nextPage,
      page_size: 50,
    }))
    const incoming = res.items || []
    mergePulseDrilldownRows(moduleKey, sectionKey, incoming, res.total)
    pulseDrilldownPages.value = { ...pulseDrilldownPages.value, [stateKey]: nextPage }
  } catch (error: any) {
    pulseDrilldownErrors.value = { ...pulseDrilldownErrors.value, [stateKey]: String(error?.message || error || '加载失败') }
  } finally {
    pulseDrilldownLoading.value = { ...pulseDrilldownLoading.value, [stateKey]: false }
  }
}

function mergePulseDrilldownRows(moduleKey: string, sectionKey: string, rows: LearningPulseFlowRow[], total?: number) {
  if (!rows.length && total === undefined) return
  const modules = (pulse.value.modules || []).map((module) => {
    if (module.key !== moduleKey) return module
    const sections = (module.flow_detail?.sections || []).map((section) => {
      if (String(section.key || '') !== sectionKey) return section
      const existing = section.items || []
      const seen = new Set(existing.map((row, index) => pulseFlowRowIdentity(row, index)))
      const merged = [...existing]
      rows.forEach((row, index) => {
        const key = pulseFlowRowIdentity(row, existing.length + index)
        if (seen.has(key)) return
        seen.add(key)
        merged.push(row)
      })
      const nextTotal = Math.max(merged.length, pulseCountValue(total, pulseSectionTotal(section)))
      return {
        ...section,
        items: merged,
        returned_count: merged.length,
        total_count: nextTotal,
        hidden_count: Math.max(0, nextTotal - merged.length),
      }
    })
    const moduleReturned = Math.max(module.items?.length || 0, pulseCountValue(module.returned_count, module.items?.length || 0))
    const moduleTotal = Math.max(moduleReturned, pulseCountValue(module.total_count, moduleReturned))
    return {
      ...module,
      flow_detail: { ...(module.flow_detail || {}), sections },
      returned_count: moduleReturned,
      total_count: moduleTotal,
      hidden_count: Math.max(0, moduleTotal - moduleReturned),
    }
  })
  pulse.value = { ...pulse.value, modules }
  syncSelectedPulseDetail(modules)
}

function pulseFlowRowIdentity(row: LearningPulseFlowRow, index: number) {
  const stable = [
    row.entity_type || '',
    row.entity_id || '',
    row.entity || '',
    row.title || '',
    row.source || '',
    row.destination || '',
  ].filter(Boolean).join(':')
  return stable || `row:${index}:${previewCompact(row.input_preview)}:${previewCompact(row.output_preview)}`
}

function detailProcessingRowFromFlowRow(
  section: LearningPulseFlowSection,
  row: LearningPulseFlowRow,
  rowIndex: number,
): DetailProcessingRow {
  return {
    key: `${section.key || section.title || 'section'}-${rowIndex}-${row.entity_id || row.entity || row.title || 'row'}`,
    section,
    row,
    rowIndex,
    title: String(row.title || row.entity || section.title || '处理记录'),
    statusText: String(row.status_text || learningStatusText(row.status) || '处理中'),
    summary: String(row.summary || section.description || '当前步骤正在处理该数据。'),
    source: String(row.source || '本步输入'),
    destination: String(row.destination || row.entity || '本步输出'),
    reason: String(row.reason || ''),
    inputText: previewCompact(row.input_preview),
    outputText: previewCompact(row.output_preview),
    syncText: pulseRowTimeText(row),
    changeText: pulseRowChangeText(row),
  }
}

function normalizeDetailStageTone(value?: string | null): DetailProductionStage['tone'] {
  const text = String(value || '')
  if (['source', 'artifact', 'sink'].includes(text)) return text as DetailProductionStage['tone']
  if (['good', 'warn', 'bad', 'info'].includes(text)) return text as DetailProductionStage['tone']
  return 'info'
}

function pulseRowMetadata(row: LearningPulseFlowRow): Record<string, unknown> {
  return row.metadata && typeof row.metadata === 'object' ? row.metadata : {}
}

function pulseRowTimeValue(row: LearningPulseFlowRow): string {
  const metadata = pulseRowMetadata(row)
  const input = objectRecord(row.input_preview)
  const output = objectRecord(row.output_preview)
  const candidates = [
    metadata.updated_at,
    metadata.finished_at,
    metadata.created_at,
    output?.updated_at,
    output?.finished_at,
    output?.created_at,
    output?.activated_at,
    input?.created_at,
  ]
  return String(candidates.find((value) => value !== undefined && value !== null && value !== '') || '')
}

function pulseRowTimeText(row: LearningPulseFlowRow) {
  const value = pulseRowTimeValue(row)
  return value ? formatTimeSeconds(value) : (pulse.value.generated_at ? `批次 ${formatTimeSeconds(pulse.value.generated_at)}` : '等待时间')
}

function pulseRowChangeText(row: LearningPulseFlowRow) {
  if (row.output_preview) return '已生成输出摘要，刷新后会同步最新输出。'
  if (row.input_preview) return '已收到输入摘要，等待本步骤输出回传。'
  if (row.value !== undefined && row.value !== null) return `统计项已更新为 ${formatDetailValue(row.value)}。`
  return '当前记录只有阶段状态，等待后端返回输入输出摘要。'
}

function pulseModuleTerminalSignature(module?: LearningPulseModule | null) {
  if (!module) return ''
  const sections = module.flow_detail?.sections || []
  const rows = sections.flatMap((section) => (section.items || []).map((row, index) => ({
    section: section.key || section.title || '',
    index,
    title: row.title || row.entity || '',
    status: row.status_text || row.status || '',
    reason: row.reason || '',
    source: row.source || '',
    destination: row.destination || '',
    input: previewCompact(row.input_preview),
    output: previewCompact(row.output_preview),
  })))
  return JSON.stringify({
    key: module.key,
    status: module.status,
    status_text: module.status_text,
    generated_at: pulse.value.generated_at || '',
    metrics: (module.metrics || []).map((metric) => [metric.key, metric.value, metric.suffix || '']),
    rows,
  })
}

function terminalTime(value?: string | null) {
  const source = value || new Date().toISOString()
  const date = new Date(source)
  if (Number.isNaN(date.getTime())) return formatTimeSeconds(source).slice(11) || '--:--:--'
  return date.toTimeString().slice(0, 8)
}

function appendDetailTerminalLine(line: Omit<DetailTerminalLine, 'id' | 'time'> & { id?: string; time?: string }) {
  const id = line.id || `${Date.now()}-${detailTerminalLines.value.length}-${line.channel}`
  detailTerminalLines.value.push({
    id,
    time: line.time || terminalTime(pulse.value.generated_at),
    channel: line.channel,
    label: line.label,
    text: line.text,
    meta: line.meta,
  })
  if (detailTerminalLines.value.length > 80) {
    detailTerminalLines.value = detailTerminalLines.value.slice(-80)
  }
  void nextTick(() => {
    const el = detailTerminalAutoScrollRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function resetDetailTerminal(detail: Detail) {
  detailTerminalLines.value = []
  detailTerminalSignature.value = ''
  detailTerminalSeen.value = {}
  if (!detail || !detail.type.startsWith('learning_pulse_')) return
  const moduleKey = String(detail.data?.module_key || '')
  const module = moduleKey ? moduleByKey(moduleKey) : null
  appendDetailTerminalSnapshot(module || null, 'opened')
}

function appendDetailTerminalSnapshot(module: LearningPulseModule | null, mode: 'opened' | 'sync') {
  if (!module) return
  appendModuleFetchTerminalLine(module, mode)
  const signature = pulseModuleTerminalSignature(module)
  if (mode === 'sync' && signature && signature === detailTerminalSignature.value) {
    appendDetailTerminalLine({
      id: `heartbeat:${module.key}:${pulse.value.generated_at || Date.now()}`,
      channel: 'fetch',
      label: 'SYNC',
      text: `本轮刷新完成，${module.title || module.key} 暂无新增生产记录`,
      meta: pulse.value.generated_at ? `generated_at=${formatTimeSeconds(pulse.value.generated_at)}` : 'generated_at=-',
    })
    return
  }
  detailTerminalSignature.value = signature
  appendModuleMetricTerminalLines(module)
  appendModuleFlowTerminalLines(module)
}

function appendModuleFetchTerminalLine(module: LearningPulseModule, mode: 'opened' | 'sync') {
  const flowItemCount = (module.flow_detail?.sections || []).reduce(
    (total, section) => total + (section.items || []).length,
    0,
  )
  appendDetailTerminalLine({
    channel: 'fetch',
    label: mode === 'opened' ? 'OPEN' : 'FETCH',
    text: `/api/learning/pulse returned module=${module.key} status=${module.status || '-'} metrics=${(module.metrics || []).length} flow_items=${flowItemCount}`,
    meta: pulse.value.generated_at ? `generated_at=${formatTimeSeconds(pulse.value.generated_at)}` : 'generated_at=-',
  })
}

function appendModuleMetricTerminalLines(module: LearningPulseModule) {
  ;(module.metrics || []).slice(0, 6).forEach((metric) => {
    const key = `metric:${module.key}:${metric.key}`
    const value = pulseMetricValue(metric)
    if (detailTerminalSeen.value[key] === value) return
    detailTerminalSeen.value[key] = value
    appendDetailTerminalLine({
      channel: 'metric',
      label: 'METRIC',
      text: `${metric.label || metric.key}: ${value}`,
      meta: `${module.title || ''}${pulse.value.generated_at ? ` · ${formatTimeSeconds(pulse.value.generated_at)}` : ''}`,
    })
  })
}

function appendModuleFlowTerminalLines(module: LearningPulseModule) {
  const sections = module.flow_detail?.sections || []
  if (!sections.length) return
  sections.forEach((section) => {
    const rows = section.items || []
    if (!rows.length) return
    rows.slice(0, 12).forEach((row, rowIndex) => {
      appendFlowRowTerminalLines(module, section, row, rowIndex)
    })
  })
}

function appendFlowRowTerminalLines(
  module: LearningPulseModule,
  section: LearningPulseFlowSection,
  row: LearningPulseFlowRow,
  rowIndex: number,
) {
  const baseKey = [
    module.key,
    section.key || section.title || 'section',
    row.entity_type || '',
    row.entity_id || '',
    row.title || row.entity || rowIndex,
  ].join(':')
  const inputText = previewCompact(row.input_preview)
  const outputText = previewCompact(row.output_preview)
  const route = `${row.source || section.title || '输入'} -> ${row.destination || row.entity || '输出'}`
  const statusText = String(row.status_text || learningStatusText(row.status) || '处理中')
  const summaryText = String(row.summary || section.description || row.reason || '处理记录已同步')
  const entityRef = [row.entity_type, row.entity_id].filter(Boolean).join(':') || row.entity || section.key || ''
  const stateSignature = JSON.stringify({
    statusText,
    summaryText,
    inputText,
    outputText,
    route,
    reason: row.reason || '',
  })
  if (detailTerminalSeen.value[baseKey] === stateSignature) return
  detailTerminalSeen.value[baseKey] = stateSignature
  if (inputText !== '暂无') {
    appendDetailTerminalLine({
      channel: 'input',
      label: 'IN',
      text: `${row.title || row.entity || section.title || '数据'} <= ${inputText}`,
      meta: [row.source || section.title || '', entityRef].filter(Boolean).join(' · '),
    })
  }
  appendDetailTerminalLine({
    channel: statusText.includes('失败') ? 'error' : 'process',
    label: 'RUN',
    text: `${row.title || row.entity || section.title || '处理'} | ${statusText} | ${summaryText}`,
    meta: [route, entityRef].filter(Boolean).join(' · '),
  })
  if (row.reason) {
    appendDetailTerminalLine({
      channel: 'process',
      label: 'WHY',
      text: row.reason,
      meta: [row.title || section.title || '', entityRef].filter(Boolean).join(' · '),
    })
  }
  if (outputText !== '暂无') {
    appendDetailTerminalLine({
      channel: 'output',
      label: 'OUT',
      text: `${row.destination || row.entity || '结果'} => ${outputText}`,
      meta: [row.title || section.title || '', entityRef].filter(Boolean).join(' · '),
    })
  }
}

function syncSelectedPulseDetail(modules: LearningPulseModule[]) {
  const current = selected.value
  if (!current || !current.type.startsWith('learning_pulse_')) return
  const data = current.data || {}
  const moduleKey = String(data.module_key || '')
  if (!moduleKey) return
  const module = modules.find((item) => item.key === moduleKey)
  if (!module) return
  if (current.type === 'learning_pulse_module') {
    selected.value = { type: current.type, data: pulseModuleDetailData(module) }
    appendDetailTerminalSnapshot(module, 'sync')
    return
  }
  if (current.type === 'learning_pulse_metric') {
    const metricKey = String((data.metric as LearningPulseMetric | undefined)?.key || data.metric_key || '')
    const metric = (module.metrics || []).find((item) => item.key === metricKey)
    selected.value = {
      type: current.type,
      data: metric
        ? pulseMetricDetailData(module, metric)
        : {
          ...data,
          module_status: module.status,
          detail: module.detail || {},
          flow_detail: module.flow_detail || {},
          filters: module.filters || {},
        },
    }
    appendDetailTerminalSnapshot(module, 'sync')
    return
  }
  if (current.type === 'learning_pulse_item') {
    const itemId = String(data.item_id || '')
    const entityType = String(data.entity_type || '')
    const entityId = String(data.entity_id || '')
    const item = (module.items || []).find((entry) => (
      (itemId && entry.id === itemId) ||
      (entityType && entityId && entry.entity_type === entityType && entry.entity_id === entityId)
    ))
    selected.value = {
      type: current.type,
      data: item
        ? pulseItemDetailData(module, item)
        : {
          ...data,
          module_title: module.title,
          flow_detail: module.flow_detail || {},
        },
    }
    appendDetailTerminalSnapshot(module, 'sync')
    return
  }
  if (current.type === 'learning_pulse_flow_row') {
    const sectionKey = String(data.section_key || '')
    const sectionTitle = String(data.section || '')
    const rowIndex = Number(data.row_index ?? -1)
    const entityType = String(data.entity_type || '')
    const entityId = String(data.entity_id || '')
    const title = String(data.title || '')
    const sections = module.flow_detail?.sections || []
    const section = sections.find((entry) => (
      (sectionKey && entry.key === sectionKey) ||
      (sectionTitle && entry.title === sectionTitle)
    ))
    if (!section) return
    const rows = section.items || []
    const row = rows.find((entry) => (
      entityType && entityId && entry.entity_type === entityType && entry.entity_id === entityId
    )) || (Number.isInteger(rowIndex) && rowIndex >= 0 ? rows[rowIndex] : undefined)
      || rows.find((entry) => String(entry.title || entry.entity || '') === title)
    if (row) {
      selected.value = { type: current.type, data: pulseFlowRowDetailData(module, section, row, rowIndex >= 0 ? rowIndex : rows.indexOf(row)) }
      appendDetailTerminalSnapshot(module, 'sync')
    }
  }
}

function openPulseModule(module: LearningPulseModule) {
  setSelected({
    type: 'learning_pulse_module',
    data: pulseModuleDetailData(module),
  })
}

function openPulseModuleByKey(moduleKey: string) {
  const module = moduleByKey(moduleKey)
  if (!module) return
  openPulseModule(module)
}

function openPulseMetric(module: LearningPulseModule, metric: LearningPulseMetric) {
  setSelected({
    type: 'learning_pulse_metric',
    data: pulseMetricDetailData(module, metric),
  })
}

function openPulseItem(module: LearningPulseModule, item: LearningPulseItem) {
  setSelected({
    type: 'learning_pulse_item',
    data: pulseItemDetailData(module, item),
  })
}

function openPulseFlowRow(section: LearningPulseFlowSection, row: LearningPulseFlowRow, rowIndex = 0) {
  const moduleKey = String(selected.value?.data?.module_key || '')
  const module = (pulse.value.modules || []).find((item) => item.key === moduleKey)
  setSelected({
    type: 'learning_pulse_flow_row',
    data: module
      ? pulseFlowRowDetailData(module, section, row, rowIndex)
      : {
        title: row.title || section.title || '流转记录',
        section: section.title,
        section_key: section.key,
        row_index: rowIndex,
        detail_synced_at: pulse.value.generated_at || null,
        ...row,
      } as Record<string, unknown>,
  })
}

function openPulseFlowRowFromModule(moduleKey: string, section: LearningPulseFlowSection, row: LearningPulseFlowRow, rowIndex = 0) {
  const module = moduleByKey(moduleKey)
  setSelected({
    type: 'learning_pulse_flow_row',
    data: module
      ? pulseFlowRowDetailData(module, section, row, rowIndex)
      : {
        title: row.title || section.title || '流转记录',
        section: section.title,
        section_key: section.key,
        row_index: rowIndex,
        module_key: moduleKey,
        detail_synced_at: pulse.value.generated_at || null,
        ...row,
      } as Record<string, unknown>,
  })
}

function emptyDeploymentChatContext(): DeploymentChatContext {
  return {
    ready: false,
    disabled_reason: '',
    inference_ready: false,
    inference_disabled_reason: '',
    model_deployment_id: '',
    model_family: '',
    training_job_id: '',
    department: '',
    artifact_id: '',
    artifact_sha256: '',
    artifact_uri_present: false,
    deployment_status: '',
    target_gateway_id: '',
    target_gateway_kind: '',
    target_gateway_active: false,
  }
}

function trainingDatasetCountText(value: Record<string, unknown>) {
  const sample = value.sample_count ?? value.artifact_count ?? value.returned_count
  const parts = [
    sample !== undefined && sample !== null && sample !== '' ? `${formatDetailValue(sample)} 条` : '',
    value.dataset_ref ? String(value.dataset_ref) : '',
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : '-'
}

function modelTestContextFromRow(row: LearningPulseFlowRow): Record<string, unknown> {
  const input = objectRecord(row.input_preview) || {}
  const output = objectRecord(row.output_preview) || {}
  const metadata = objectRecord(row.metadata) || {}
  const nested = objectRecord(metadata.model_test_context)
    || objectRecord(output.model_test_context)
    || objectRecord(input.model_test_context)
    || {}
  const trainingData = objectRecord(nested.training_data)
    || objectRecord(metadata.training_data)
    || objectRecord(input.training_data)
    || objectRecord(output.training_data)
    || {}
  return {
    ...nested,
    training_job_id: String(nested.training_job_id || output.training_job_id || input.job_id || metadata.training_job_id || row.entity_id || ''),
    training_job_title: String(nested.training_job_title || row.title || ''),
    training_date: String(nested.training_date || input.training_date || metadata.created_at || metadata.updated_at || ''),
    target_skill_id: String(nested.target_skill_id || input.target_skill_id || output.target_skill_id || ''),
    target_gateway_id: String(nested.target_gateway_id || input.gateway_id || output.target_gateway_id || ''),
    model_name: String(nested.model_name || input.training_model || output.model_name || row.title || ''),
    artifact_model_name: String(nested.artifact_model_name || output.model_name || nested.model_name || ''),
    model_deployment_id: String(nested.model_deployment_id || output.model_deployment_id || ''),
    deployment_status: String(nested.deployment_status || output.deployment_status || row.status || ''),
    artifact_id: String(nested.artifact_id || output.artifact_id || ''),
    artifact_sha256: String(nested.artifact_sha256 || output.artifact_sha256 || ''),
    win_rate: nested.win_rate ?? output.win_rate,
    passed: nested.passed ?? (row.status === 'passed'),
    training_data: trainingData,
  }
}

function deploymentChatContextFromRow(row: LearningPulseFlowRow): DeploymentChatContext {
  const input = objectRecord(row.input_preview) || {}
  const output = objectRecord(row.output_preview) || {}
  const metadata = objectRecord(row.metadata) || {}
  const nested = objectRecord(output.chat_context) || objectRecord(metadata.chat_context)
  const context = {
    ...emptyDeploymentChatContext(),
    ...(nested || {}),
    model_deployment_id: String(
      nested?.model_deployment_id
        || output.model_deployment_id
        || output.deployment_id
        || input.model_deployment_id
        || metadata.model_deployment_id
        || (row.entity_type === 'model_deployment' ? row.entity_id : '')
        || '',
    ),
    model_family: String(nested?.model_family || output.model_family || input.model_family || metadata.model_family || row.title || ''),
    training_job_id: String(nested?.training_job_id || output.training_job_id || input.training_job_id || metadata.training_job_id || ''),
    department: String(nested?.department || output.department || input.department || metadata.department || ''),
    artifact_id: String(nested?.artifact_id || output.artifact_id || input.artifact_id || metadata.artifact_id || ''),
    artifact_sha256: String(nested?.artifact_sha256 || output.artifact_sha256 || input.artifact_sha256 || metadata.artifact_sha256 || ''),
    artifact_uri_present: Boolean(nested?.artifact_uri_present || output.artifact_uri_present || input.artifact_uri_present || metadata.artifact_uri_present),
    deployment_status: String(nested?.deployment_status || output.status || metadata.deployment_status || row.status || ''),
    target_gateway_id: String(nested?.target_gateway_id || output.target_gateway_id || input.target_gateway_id || metadata.target_gateway_id || ''),
    target_gateway_kind: String(nested?.target_gateway_kind || output.target_gateway_kind || input.target_gateway_kind || metadata.target_gateway_kind || ''),
    target_gateway_active: Boolean(nested?.target_gateway_active || output.target_gateway_active || input.target_gateway_active || metadata.target_gateway_active),
  }
  const inactiveReason = context.deployment_status && !['active', 'canary'].includes(context.deployment_status)
    ? `部署状态为 ${learningStatusText(context.deployment_status) || context.deployment_status}，需进入灰度或已激活后才能对话。`
    : ''
  const explicitInferenceReady = typeof nested?.inference_ready === 'boolean'
    ? Boolean(nested.inference_ready)
    : undefined
  const explicitReady = typeof nested?.ready === 'boolean'
    ? Boolean(nested.ready)
    : undefined
  const inferenceReason = String(nested?.inference_disabled_reason || '')
  const disabledReason = String(nested?.disabled_reason || inferenceReason || inactiveReason || '')
  const ready = explicitReady ?? explicitInferenceReady ?? false
  return {
    ...context,
    ready,
    inference_ready: explicitInferenceReady ?? ready,
    inference_disabled_reason: inferenceReason,
    disabled_reason: ready ? '' : disabledReason,
  }
}

function objectRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

function deploymentChatQueryString() {
  const context = selectedDeploymentChatContext.value
  const query = new URLSearchParams()
  if (context.model_deployment_id) query.set('model_deployment_id', context.model_deployment_id)
  if (context.model_family) query.set('model_family', context.model_family)
  if (context.training_job_id) query.set('training_job_id', context.training_job_id)
  if (context.department) query.set('department', context.department)
  if (context.artifact_id) query.set('artifact_id', context.artifact_id)
  if (context.artifact_sha256) query.set('artifact_sha256', context.artifact_sha256)
  if (context.target_gateway_id) query.set('target_gateway_id', context.target_gateway_id)
  const draft = deploymentChatDraft.value.trim()
  if (draft) query.set('draft', draft)
  return query.toString()
}

function deploymentChatModelContext(): Record<string, string> {
  const context = selectedDeploymentChatContext.value
  return {
    model_deployment_id: context.model_deployment_id,
    model_family: context.model_family,
    training_job_id: context.training_job_id,
    artifact_id: context.artifact_id,
    artifact_sha256: context.artifact_sha256,
    target_gateway_id: context.target_gateway_id,
  }
}

function deploymentChatInstanceMatchesDepartment(instance: Record<string, unknown>, department: string) {
  if (!department) return true
  const values = [
    instance.department,
    instance.department_name,
    instance.department_id,
  ].map((value) => String(value || '').trim().toLowerCase()).filter(Boolean)
  return values.includes(department.trim().toLowerCase())
}

function deploymentChatInstancePurpose(instance: Record<string, unknown>) {
  return String(instance.agent_purpose || instance.purpose || '').trim()
}

function deploymentChatInstanceSupportsAnalysis(instance: Record<string, unknown>) {
  const analysis = objectRecord(instance.analysis)
  const caps = objectRecord(instance.capabilities)
  const ops = Array.isArray(analysis?.ops)
    ? analysis.ops
    : Array.isArray(caps?.ops)
      ? caps.ops
      : []
  const purpose = deploymentChatInstancePurpose(instance)
  return purpose === 'analysis' || purpose === 'mixed' || Boolean(analysis?.agent) || ops.includes('intelligence.analyze')
}

function deploymentChatInstanceSupportsTrainingInference(instance: Record<string, unknown>) {
  const training = objectRecord(instance.training)
  const analysis = objectRecord(instance.analysis)
  const caps = objectRecord(instance.capabilities)
  const ops = [
    ...(Array.isArray(training?.ops) ? training.ops : []),
    ...(Array.isArray(analysis?.ops) ? analysis.ops : []),
    ...(Array.isArray(caps?.ops) ? caps.ops : []),
  ].map((item) => String(item || ''))
  const purpose = deploymentChatInstancePurpose(instance)
  return purpose === 'training' || purpose === 'mixed' || ops.includes('training.inference')
}

async function resolveDeploymentChatTarget(): Promise<{ instanceId: string; agentId: string }> {
  const list = await aiclawApi.listInstances()
  const listRecord = objectRecord(list)
  const listItems = listRecord?.items
  const rows: unknown[] = Array.isArray(listItems)
    ? listItems
    : Array.isArray(list)
      ? list
      : []
  const online = rows
    .map((row) => objectRecord(row))
    .filter((row): row is Record<string, unknown> => Boolean(row && row.bridge_online))
  if (!online.length) throw new Error('当前没有在线 Agent，无法发起真实对话。')
  const context = selectedDeploymentChatContext.value
  const targetGatewayId = context.target_gateway_id
  const directGateway = targetGatewayId
    ? online.find((row) => String(row.id || '') === targetGatewayId)
    : null
  const department = context.department
  const scoped = online.filter((row) => deploymentChatInstanceMatchesDepartment(row, department))
  const candidates = directGateway ? [directGateway] : (scoped.length ? scoped : online)
  const instance = candidates.find(deploymentChatInstanceSupportsTrainingInference)
    || candidates.find(deploymentChatInstanceSupportsAnalysis)
    || candidates.find((row) => deploymentChatInstancePurpose(row) === 'mixed')
    || candidates.find((row) => deploymentChatInstancePurpose(row) === 'skill_runtime')
    || candidates[0]
  const instanceId = String(instance.id || '')
  if (!instanceId) throw new Error('在线 Agent 缺少实例 ID。')
  const agents = await aiclawApi.listAgents(instanceId)
  const agentsRecord = objectRecord(agents)
  const agentsItems = agentsRecord?.items
  const agentRows: unknown[] = Array.isArray(agentsItems)
    ? agentsItems
    : Array.isArray(agents)
      ? agents
      : []
  const agent = agentRows.map((row) => objectRecord(row)).find(Boolean)
  const agentId = String(agent?.id || agent?.name || '')
  if (!agentId) throw new Error('当前 Agent 实例没有可用对话 Agent。')
  return { instanceId, agentId }
}

function openDeploymentChatModal() {
  deploymentChatModalVisible.value = true
}

function appendDeploymentAssistantText(text: string, options: { replace?: boolean; done?: boolean } = {}) {
  const replace = options.replace === true
  const done = options.done === true
  const last = deploymentChatMessages.value[deploymentChatMessages.value.length - 1]
  const assistant = last?.role === 'assistant'
    ? last
    : null
  if (!assistant) {
    deploymentChatMessages.value.push({ role: 'assistant', content: text, streaming: !done })
    return
  }
  assistant.content = replace ? text : `${assistant.content || ''}${text}`
  assistant.streaming = !done
}

function formatDeploymentChatMetric(value: unknown, digits = 2): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return ''
  return num.toFixed(digits).replace(/\.?0+$/, '')
}

function deploymentChatMessageMeta(message: DeploymentChatMessage): string {
  if (message.role !== 'assistant') return ''
  const metrics = message.metrics || {}
  const parts: string[] = []
  const tokensPerSecond = formatDeploymentChatMetric(metrics.tokens_per_second ?? metrics.tokensPerSecond)
  if (tokensPerSecond) parts.push(`${tokensPerSecond} token/s`)
  const generatedTokens = formatDeploymentChatMetric(metrics.generated_tokens ?? metrics.generatedTokens, 0)
  const maxTokens = formatDeploymentChatMetric(metrics.effective_max_new_tokens ?? metrics.max_new_tokens ?? metrics.maxNewTokens, 0)
  if (generatedTokens && maxTokens) {
    parts.push(`${generatedTokens}/${maxTokens} tokens`)
  } else if (generatedTokens) {
    parts.push(`${generatedTokens} tokens`)
  }
  if (metrics.inference_runtime === 'local_bridge') parts.push('本地 Bridge')
  if (message.finishReason === 'length') parts.push('达到长度上限')
  return parts.join(' · ')
}

function deploymentChatPayloadText(payload: Record<string, unknown>) {
  if (payload.delta != null && payload.delta !== '') return String(payload.delta)
  if (payload.text != null && payload.text !== '') return String(payload.text)
  const message = objectRecord(payload.message)
  const content = message?.content
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        const item = objectRecord(block)
        if (!item) return typeof block === 'string' ? block : ''
        if (item.type === 'text' || item.text) return String(item.text || '')
        return ''
      })
      .filter(Boolean)
      .join('\n\n')
  }
  return ''
}

async function sendDeploymentChat() {
  const disabledReason = deploymentChatDisabledReason.value
  if (disabledReason) {
    Message.warning(disabledReason)
    return
  }
  const content = deploymentChatDraft.value.trim()
  if (!content) {
    Message.warning('请输入要测试的问题。')
    return
  }
  deploymentChatConnecting.value = true
  deploymentChatSending.value = true
  deploymentChatMessages.value.push({ role: 'user', content })
  deploymentChatMessages.value.push({ role: 'assistant', content: '', streaming: true })
  try {
    const target = await resolveDeploymentChatTarget()
    await new Promise<void>((resolve, reject) => {
      let settled = false
      let ws: WebSocket | null = null
      const timeout = window.setTimeout(() => {
        if (settled) return
        settled = true
        ws?.close()
        reject(new Error('模型对话超时，请进入完整对话页面继续。'))
      }, 300000)
      ws = createAiclawChatSocket(target.instanceId, target.agentId, {
        onOpen: () => {
          deploymentChatConnecting.value = false
          ws?.send(JSON.stringify({
            type: 'user_message',
            content,
            attachments: [],
            model_context: deploymentChatModelContext(),
          }))
        },
        onError: () => {
          if (settled) return
          settled = true
          window.clearTimeout(timeout)
          reject(new Error('模型对话连接失败。'))
        },
        onClose: () => {
          deploymentChatConnecting.value = false
          deploymentChatSending.value = false
          window.clearTimeout(timeout)
        },
        onMessage: (message) => {
          if (message?.type === 'error') {
            if (!settled) {
              settled = true
              window.clearTimeout(timeout)
              reject(new Error(String(message.message || '模型对话失败。')))
            }
            return
          }
          if (message?.type !== 'chat_event') return
          const payload = objectRecord(message.payload) || {}
          const text = deploymentChatPayloadText(payload)
          if (text) appendDeploymentAssistantText(text, { replace: Boolean(payload.delta || payload.text), done: false })
          if (['final', 'error', 'aborted'].includes(String(payload.state || ''))) {
            const last = deploymentChatMessages.value[deploymentChatMessages.value.length - 1]
            if (last?.role === 'assistant') {
              last.finishReason = String(payload.finishReason || payload.finish_reason || '')
              last.metrics = objectRecord(payload.metrics) || {}
              if (last.finishReason === 'length' && last.content && !last.content.includes('输出达到长度上限')) {
                last.content += '\n\n[系统提示] 输出达到长度上限，回复可能未完整结束；可以进入完整对话页继续追问“继续”。'
              }
            }
            appendDeploymentAssistantText('', { done: true })
            if (!settled) {
              settled = true
              window.clearTimeout(timeout)
              resolve()
            }
            ws?.close()
          }
        },
      })
    })
  } catch (error: any) {
    appendDeploymentAssistantText(String(error?.message || error || '模型对话失败。'), { replace: true, done: true })
    Message.error(String(error?.message || '模型对话失败。'))
  } finally {
    deploymentChatConnecting.value = false
    deploymentChatSending.value = false
  }
}

function openDeploymentChat() {
  const disabledReason = deploymentChatDisabledReason.value
  if (disabledReason) {
    Message.warning(disabledReason)
    return
  }
  const query = deploymentChatQueryString()
  router.push(query ? `/agent?${query}` : '/agent')
}

function openDeploymentPage() {
  if (!deploymentChatDeepLink.value) return
  router.push(deploymentChatDeepLink.value)
}

async function openSelectedTrainingDataset() {
  const jobId = selectedModelTrainingJobId.value
  if (!jobId) {
    Message.warning('当前模型测试记录没有训练任务 ID。')
    return
  }
  trainingDatasetModalVisible.value = true
  trainingDatasetLoading.value = true
  trainingDatasetError.value = ''
  trainingDatasetContext.value = selectedModelTrainingData.value
  try {
    trainingDatasetResult.value = await learningApi.trainingJobDataset(jobId, { limit: 200 })
  } catch (error: any) {
    trainingDatasetError.value = String(error?.message || error || '训练数据加载失败')
    Message.error(trainingDatasetError.value)
  } finally {
    trainingDatasetLoading.value = false
  }
}

function openTrainingDatasetSample(sample: LearningTrainingJobDatasetSample) {
  const artifactId = String(sample.artifact_id || '')
  if (!artifactId) return
  trainingDatasetModalVisible.value = false
  setSelected({
    type: 'artifact',
    data: {
      id: artifactId,
      entity_type: 'learning_artifact',
      entity_id: artifactId,
      title: sample.title || artifactId,
      artifact_kind: sample.artifact_kind,
      status: sample.status,
      skill_id: sample.skill_id,
      run_id: sample.run_id,
      event_id: sample.event_id,
      sink_type: sample.sink_type,
      sink_id: sample.sink_id,
      quality_score: sample.quality_score,
      confidence: sample.confidence,
      summary: sample.summary,
      labels: sample.labels || [],
      lineage_summary: sample.lineage_summary || {},
    },
  })
}

function formatDetailValue(value: unknown) {
  if (typeof value === 'number') return Number.isFinite(value) ? value.toLocaleString() : '-'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return `${value.length} 项`
  if (value && typeof value === 'object') return JSON.stringify(value)
  return String(value ?? '-')
}

function previewCompact(value: unknown) {
  if (value === undefined || value === null || value === '') return '暂无'
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  if (!text) return '暂无'
  return text.length > 180 ? `${text.slice(0, 180)}...` : text
}

function sumRecord(record: Record<string, number | undefined>) {
  return Object.values(record || {}).reduce<number>((total, value) => total + Number(value || 0), 0)
}

function graphNodeCards(types: string[], tone: string, limit = 5): FlowNode[] {
  return graphNodes.value
    .filter((node) => types.includes(String(node.entity_type || '')))
    .slice(0, limit)
    .map((node) => ({
      id: node.id,
      label: entityTypeText(node.entity_type),
      meta: shortId(node.entity_id || node.label || node.id),
      icon: iconForEntity(node.entity_type),
      heat: nodeHeat(node.entity_type),
      tone,
      payload: node as Record<string, unknown>,
    }))
}

function normalizeTopologyNode(node: LearningFlowTopologyNode): FlowNode {
  return {
    id: node.id,
    label: node.label || node.id,
    meta: node.meta || node.entity_id || node.entity_type || '-',
    icon: node.icon || 'flow',
    heat: Number(node.heat || 0.55),
    tone: node.tone || 'source',
    payload: {
      ...(node.payload || {}),
      id: node.id,
      entity_type: node.entity_type,
      entity_id: node.entity_id,
      label: node.label,
      meta: node.meta,
    },
  }
}

function sourceEventNode(event: LearningEventRow): FlowNode {
  return {
    id: `source:${event.id}`,
    label: sourceTypeText(event.source_type),
    meta: event.skill_id || event.source_id || '-',
    icon: iconForSource(event.source_type),
    heat: Number(event.quality_score || 0.55),
    tone: 'source',
    payload: event as Record<string, unknown>,
  }
}

function eventNode(event: LearningEventRow): FlowNode {
  return {
    id: `event:${event.id}`,
    label: event.event_type || 'learning.event',
    meta: `${sourceTypeText(event.source_type)} · ${formatTime(event.created_at)}`,
    icon: 'spark',
    heat: Number(event.quality_score || 0.6),
    tone: statusClass(event.status) === 'bad' ? 'danger' : 'event',
    payload: event as Record<string, unknown>,
  }
}

function artifactNode(item: LearningArtifactRow): FlowNode {
  return {
    id: `artifact:${item.id}`,
    label: item.title || artifactKindText(item.artifact_kind),
    meta: `${artifactKindText(item.artifact_kind)} · ${item.status || 'ready'}`,
    icon: iconForArtifact(item.artifact_kind),
    heat: Number(item.quality_score || item.confidence || 0.55),
    tone: item.status === 'ignored' ? 'muted' : 'artifact',
    payload: item as Record<string, unknown>,
  }
}

function candidateNode(item: ImprovementCandidateRow): FlowNode {
  return {
    id: `candidate:${item.id}`,
    label: item.title || '改进建议',
    meta: `${item.target_type || '-'} · ${item.status || 'open'} · ${item.risk_level || 'R2'}`,
    icon: item.target_type === 'sf' ? 'bolt' : item.target_type === 'agent' ? 'bot' : 'edit',
    heat: Number(item.priority_score || 0.6),
    tone: item.status === 'rejected' ? 'muted' : 'candidate',
    payload: item as Record<string, unknown>,
  }
}

function connectorCount(index: number) {
  const connector = connectorAt(index)
  if (connector) return Number(connector.count || 0)
  const counts = [
    Number(summary.value.events?.total || events.value.length),
    sumRecord(summary.value.artifacts?.by_kind || {}),
    Number(summary.value.knowledge?.indexed || 0) + Number(summary.value.training?.samples || 0) + Number(summary.value.agent?.memories || 0),
    sumRecord(summary.value.candidates?.by_target || {}),
    Number(summary.value.candidates?.by_status?.reviewing || 0) + Number(summary.value.candidates?.by_status?.accepted || 0) + Number(summary.value.candidates?.by_status?.implemented || 0),
  ]
  return counts[index] || 0
}

function connectorOpacity(index: number) {
  const connector = connectorAt(index)
  if (connector?.intensity !== undefined) return String(Math.max(0.28, Math.min(1, Number(connector.intensity || 0))))
  return String(Math.max(0.28, Math.min(1, connectorCount(index) / 16)))
}

function connectorDuration(index: number) {
  const count = Math.min(connectorCount(index), 24)
  return `${Math.max(0.85, 2.4 - count * 0.055).toFixed(2)}s`
}

function connectorLabel(index: number) {
  return connectorAt(index)?.label || ['同步', '提取', '生成知识', '提出', '审核流程'][index] || '处理'
}

function connectorAt(index: number) {
  const from = flowStages.value[index]?.key
  const to = flowStages.value[index + 1]?.key
  return flowConnectors.value.find((item) => item.from === from && item.to === to)
}

function showEntity(value?: string) {
  if (!value) return
  const [type, ...rest] = String(value).split(':')
  setSelected({ type: 'entity', data: { entity_type: type, entity_id: rest.join(':') || value } })
}

function edgeLabel(value?: string) {
  if (!value) return '-'
  const [type, ...rest] = String(value).split(':')
  const id = rest.join(':')
  return `${entityTypeText(type)} · ${shortId(id || value)}`
}

function entityTypeText(type?: string) {
  const map: Record<string, string> = {
    run: '运行',
    execution_artifact: '执行数据',
    decision_log: '决策',
    learning_artifact: '资产',
    knowledge_document: '知识',
    knowledge_query: '检索',
    training_sample: '训练样本',
    training_job: '训练',
    model_artifact: '模型产物',
    model_deployment: '模型',
    improvement_candidate: '建议',
    agent: 'Agent',
    dingtalk_feedback: '钉钉反馈',
    sf_call: 'SF',
    agent_tool: 'Agent工具',
    decision_request: '评审待办',
    agent_memory: 'Agent记忆',
    skill: 'Skill',
    todo: '待办',
    todo_dispatch_task: '派发任务',
    agent_thread: 'Agent',
    governance_queue: '审核队列',
  }
  return map[String(type || '')] || String(type || '-')
}

function sourceTypeText(type?: string) {
  const map: Record<string, string> = {
    execution_run: 'Skill 运行',
    execution_artifact: '执行数据资产',
    decision_log: '决策反馈',
    sf_mcp_call: 'SF 调用',
    sf_usage_aggregate: 'SF 高频聚合',
    agent_thread: 'Agent 会话',
    knowledge_query: '知识检索',
    training_job: '训练任务',
    model_deployment: '模型部署',
    decision_request: '评审请求',
    ai_todo: '审批反馈',
    todo_dispatch_task: '派发待办',
  }
  return map[String(type || '')] || String(type || '-')
}

function relationText(value?: string) {
  const map: Record<string, string> = {
    produced: '生成',
    indexed_as: '入库为',
    used_as_context: '引用为上下文',
    created_sample: '形成样本',
    proposed_change: '提出改进',
    reviewed_by: '进入评审',
    trained_from: '训练来源',
    deployed_to: '部署到',
    produced_artifact: '产出数据/模型',
    captured_data: '记录数据',
    remembered_as: '生成记忆',
    used_as_model: '使用模型',
    feedback_for_decision: '反馈决策',
    feedback_on_model_decision: '反馈模型决策',
    submitted_feedback: '反馈回传',
    controlled_run: '运行 Agent 控制',
    controlled_analysis: '分析 Agent 控制',
    controlled_inference: '推理 Agent 控制',
    controlled_training: '训练 Agent 控制',
    served_model_inference: '模型推理服务',
    requested_deployment_review: '提交部署审核',
    used_skill: '关联 Skill',
    called_tool: '调用工具',
    rollback_target: '回滚目标',
  }
  return map[String(value || '')] || String(value || '-')
}

function highlightsForDetail(data: Record<string, unknown>) {
  const detail = data.detail && typeof data.detail === 'object' ? data.detail as Record<string, unknown> : {}
  const storage = data.storage && typeof data.storage === 'object'
    ? data.storage as Record<string, unknown>
    : detail.storage && typeof detail.storage === 'object'
      ? detail.storage as Record<string, unknown>
      : {}
  const fields = [
    ['来源', data.source_type || data.entity_type || data.target_type || data.artifact_kind],
    ['阶段', data.stage],
    ['Skill', data.skill_id],
    ['Run', data.run_id],
    ['部门', data.department],
    ['状态', data.status || data.review_status],
    ['数据库版本', data.database_version || storage.database_version],
    ['数据库存储', data.database_synced_count ?? storage.database_synced_count],
    ['向量分片', data.vector_chunk_count ?? storage.vector_chunk_count],
    ['同步时间', data.detail_synced_at ? formatTimeSeconds(String(data.detail_synced_at)) : undefined],
    ['质量', data.quality_score !== undefined ? percent(Number(data.quality_score)) : undefined],
    ['置信', data.confidence !== undefined ? percent(Number(data.confidence)) : undefined],
  ]
  return fields
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .slice(0, 6)
    .map(([label, value]) => ({ label: String(label), value: String(value) }))
}

function entityForDetail(type: string, data: Record<string, unknown>): DetailEntity {
  const entityType = String(data.entity_type || '')
  const entityId = String(data.entity_id || '')
  if (entityType && entityId && entityType !== 'source_type') {
    if (entityType === 'learning_event') {
      return sourceEntityFromEventLike(data)
    }
    return { type: entityType, id: entityId }
  }
  if (type === 'event') return sourceEntityFromEventLike(data)
  if (type === 'artifact') return { type: 'learning_artifact', id: String(data.id || '') }
  if (type === 'candidate') return { type: 'improvement_candidate', id: String(data.id || '') }
  if (type === 'learning_pulse_item' && entityType && entityId) return { type: entityType, id: entityId }
  if (type === 'lineage_edge') {
    return { type: String(data.to_type || ''), id: String(data.to_id || '') }
  }
  return null
}

function sourceEntityFromEventLike(data: Record<string, unknown>): DetailEntity {
  const sourceType = String(data.source_type || '')
  const sourceId = String(data.source_id || '')
  if (!sourceType || !sourceId) return null
  const map: Record<string, string> = {
    execution_run: 'run',
    execution_artifact: 'execution_artifact',
    decision_log: 'decision_log',
    decision_request: 'todo',
    todo_dispatch_task: 'todo_dispatch_task',
    sf_mcp_call: 'sf_call',
    sf_usage_aggregate: 'sf_usage_aggregate',
    knowledge_query: 'knowledge_query',
    training_job: 'training_job',
    model_deployment: 'model_deployment',
    agent_thread: 'agent_thread',
  }
  return { type: map[sourceType] || sourceType, id: sourceId }
}

function deepLinkForDetail(type: string, data: Record<string, unknown>): string {
  const directUrl = String(data.url || data.deep_link || '')
  if (directUrl) return directUrl
  if (type === 'artifact') {
    if (data.sink_type === 'knowledge_document' && data.sink_id) return `/knowledge?doc_id=${enc(data.sink_id)}`
    if (['training_sample', 'eval_case'].includes(String(data.artifact_kind || ''))) return `/training/datasets?sample_id=${enc(data.id)}`
    if (data.artifact_kind === 'agent_memory') return `/agent?memory_id=${enc(data.id)}`
  }
  if (type === 'candidate') {
    return candidateDeepLink(data)
  }
  const entity = entityForDetail(type, data)
  if (entity?.type && entity.id) return deepLinkForEntity(entity.type, entity.id, data)
  if (String(data.entity_type || '') === 'source_type' && data.entity_id) {
    return `/learning-flow?source_type=${enc(data.entity_id)}`
  }
  return ''
}

function candidateDeepLink(data: Record<string, unknown>): string {
  const id = String(data.id || '')
  const target = String(data.target_type || '')
  if (target === 'skill') return data.skill_id || data.target_id ? `/skills/${enc(data.skill_id || data.target_id)}?candidate_id=${enc(id)}` : `/learning-flow?candidate_id=${enc(id)}`
  if (target === 'sf') return `/sf?candidate_id=${enc(id)}${data.target_id ? `&tool=${enc(data.target_id)}` : ''}`
  if (target === 'agent') return `/agent?candidate_id=${enc(id)}`
  if (target === 'training') return `/training?candidate_id=${enc(id)}`
  if (target === 'knowledge') return `/knowledge?candidate_id=${enc(id)}`
  return id ? `/learning-flow?candidate_id=${enc(id)}` : ''
}

function deepLinkForEntity(type: string, id: string, data: Record<string, unknown> = {}): string {
  const cleanId = String(id || '')
  if (!cleanId) return ''
  const skillId = String(data.skill_id || '')
  const runId = String(data.run_id || '')
  const tool = String(data.tool || data.target_id || '')
  const map: Record<string, string> = {
    run: `/execution/${enc(cleanId)}`,
    execution_run: `/execution/${enc(cleanId)}`,
    execution_artifact: runId ? `/execution/${enc(runId)}?artifact_id=${enc(cleanId)}` : `/executions?artifact_id=${enc(cleanId)}`,
    decision_log: runId ? `/execution/${enc(runId)}?decision_log_id=${enc(cleanId)}` : `/executions?decision_log_id=${enc(cleanId)}`,
    learning_artifact: `/learning-flow?artifact_id=${enc(cleanId)}`,
    knowledge_document: `/knowledge?doc_id=${enc(cleanId)}`,
    knowledge_query: `/knowledge?query_log_id=${enc(cleanId)}`,
    training_sample: `/training/datasets?sample_id=${enc(cleanId)}`,
    training_job: `/training/jobs/${enc(cleanId)}`,
    model_deployment: `/training/deployments/${enc(cleanId)}`,
    improvement_candidate: candidateDeepLink({ ...data, id: cleanId }),
    sf_call: `/sf?call_id=${enc(cleanId)}${tool ? `&tool=${enc(tool)}` : ''}`,
    sf_usage_aggregate: `/sf?tool=${enc(tool || cleanId.split(':')[0] || cleanId)}`,
    skill: `/skills/${enc(cleanId)}`,
    todo: `/inbox/todos/${enc(cleanId)}`,
    decision_request: `/inbox/todos/${enc(cleanId)}`,
    todo_dispatch_task: `/inbox/todos?dispatch_id=${enc(cleanId)}`,
    agent_thread: `/agent?thread_id=${enc(cleanId)}`,
    agent_memory: `/agent?memory_id=${enc(cleanId)}`,
    governance_queue: `/learning-flow?candidate_status=reviewing`,
    source_type: `/learning-flow?source_type=${enc(cleanId)}`,
  }
  if (type === 'learning_event') {
    const eventEntity = sourceEntityFromEventLike(data)
    return eventEntity ? deepLinkForEntity(eventEntity.type, eventEntity.id, data) : ''
  }
  if (type === 'learning_artifact' && data.sink_type === 'knowledge_document' && data.sink_id) {
    return `/knowledge?doc_id=${enc(data.sink_id)}`
  }
  if (type === 'learning_artifact' && ['training_sample', 'eval_case'].includes(String(data.artifact_kind || ''))) {
    return `/training/datasets?sample_id=${enc(cleanId)}`
  }
  if (type === 'improvement_candidate') return candidateDeepLink({ ...data, id: cleanId, skill_id: skillId })
  return map[type] || ''
}

function enc(value: unknown) {
  return encodeURIComponent(String(value || ''))
}

function lineageEndpoint(edge: Record<string, unknown>, side: 'source' | 'target') {
  if (side === 'source') {
    return String(edge.source || `${edge.from_type || ''}:${edge.from_id || ''}`)
  }
  return String(edge.target || `${edge.to_type || ''}:${edge.to_id || ''}`)
}

async function openDetailDeepLink() {
  if (!detailDeepLink.value) return
  await router.push(detailDeepLink.value)
}

async function loadDetailLineage() {
  if (!detailEntity.value?.type || !detailEntity.value.id) return
  lineageLoading.value = true
  try {
    const result = await learningApi.lineage(detailEntity.value.type, detailEntity.value.id, { limit: 60 }) as { edges?: Array<Record<string, unknown>> }
    detailLineage.value = (result?.edges || []).map((edge) => ({
      ...edge,
      source: `${edge.from_type}:${edge.from_id}`,
      target: `${edge.to_type}:${edge.to_id}`,
    }))
    if (!detailLineage.value.length) Message.info('暂无关联血缘')
  } finally {
    lineageLoading.value = false
  }
}

function iconForEntity(type?: string) {
  const map: Record<string, string> = {
    run: 'play',
    execution_artifact: 'database',
    decision_log: 'check',
    learning_artifact: 'layers',
    knowledge_document: 'book',
    knowledge_query: 'search',
    training_sample: 'beaker',
    training_job: 'gpu',
    model_artifact: 'database',
    model_deployment: 'bolt',
    improvement_candidate: 'spark',
    sf_call: 'bolt',
    sf_usage_aggregate: 'trend',
    agent_thread: 'bot',
    agent_tool: 'tool',
    agent_memory: 'bot',
    decision_request: 'inbox',
    skill: 'cube',
    todo: 'list',
    todo_dispatch_task: 'send',
    governance_queue: 'inbox',
  }
  return map[String(type || '')] || 'flow'
}

function iconForSource(type?: string) {
  const map: Record<string, string> = {
    execution_run: 'play',
    execution_artifact: 'database',
    decision_log: 'check',
    sf_mcp_call: 'bolt',
    sf_usage_aggregate: 'trend',
    agent_thread: 'bot',
    knowledge_query: 'search',
    training_job: 'gpu',
    model_deployment: 'bolt',
    decision_request: 'inbox',
    ai_todo: 'check',
    todo_dispatch_task: 'send',
    governance_queue: 'inbox',
  }
  return map[String(type || '')] || 'flow'
}

function iconForArtifact(value?: string) {
  const map: Record<string, string> = {
    knowledge_note: 'book',
    report_summary: 'doc',
    training_sample: 'beaker',
    eval_case: 'check',
    agent_memory: 'bot',
    skill_improvement_candidate: 'edit',
    sf_iteration_candidate: 'bolt',
    agent_policy_candidate: 'shield',
    agent_creation_candidate: 'bot',
    knowledge_review_candidate: 'book',
    training_improvement_candidate: 'gpu',
  }
  return map[String(value || '')] || 'layers'
}

function nodeHeat(type?: string) {
  const map: Record<string, number> = {
    run: 0.65,
    decision_log: 0.72,
    sf_call: 0.68,
    learning_artifact: 0.78,
    knowledge_document: 0.86,
    training_sample: 0.82,
    agent_memory: 0.74,
    improvement_candidate: 0.8,
    decision_request: 0.66,
    training_job: 0.7,
    model_artifact: 0.74,
    skill: 0.72,
  }
  return map[String(type || '')] || 0.55
}

function artifactKindText(value?: string) {
  const map: Record<string, string> = {
    knowledge_note: '知识',
    report_summary: '报告',
    training_sample: '样本',
    eval_case: '评测',
    agent_memory: '记忆',
    skill_improvement_candidate: 'Skill建议',
    sf_iteration_candidate: 'SF建议',
    agent_policy_candidate: 'Agent建议',
    agent_creation_candidate: 'Agent化',
    knowledge_review_candidate: '知识缺口',
    training_improvement_candidate: '训练建议',
  }
  return map[String(value || '')] || String(value || '-')
}

function statusClass(value?: string) {
  const status = String(value || '')
  if (status.includes('fail') || status === 'ignored' || status === 'blocked') return 'bad'
  if (status.includes('review') || status === 'ready' || status === 'open' || status === 'flowing' || status === 'pending') return 'warn'
  if (['materialized', 'captured', 'completed', 'succeeded', 'accepted', 'implemented'].includes(status)) return 'good'
  return ''
}

function flowStateText(value?: string) {
  const map: Record<string, string> = {
    completed: '已完成',
    flowing: '处理中',
    blocked: '有待处理事项',
    failed: '失败',
  }
  return map[String(value || '')] || String(value || '-')
}

function journeyStageText(value?: string) {
  const map: Record<string, string> = {
    source: '来源',
    event: '事件',
    artifact: '资产',
    sink: '生成知识',
    control: 'Agent控制',
    feedback: '反馈回传',
    deployment: '模型部署',
    decision: '决策输出',
    candidate: '建议',
    review: '审核流程',
  }
  return map[String(value || '')] || String(value || '-')
}

function blockerText(value?: string) {
  const map: Record<string, string> = {
    no_artifact_extracted: '未提取资产',
    awaiting_materialization: '等待生成知识',
    review_required: '需要审核',
    candidate_open: '建议待审核',
    ingestion_failed: '知识生成失败',
  }
  return map[String(value || '')] || String(value || '-')
}

function statusText(value?: string) {
  const map: Record<string, string> = {
    never_run: '等待首轮',
    manual: '手动模式',
    running: '运行中',
    succeeded: '正常',
    failed: '异常',
  }
  return map[String(value || '')] || String(value || '-')
}

function targetPreviewText(value?: string) {
  const map: Record<string, string> = {
    'panel-global-map': '详细视图',
    'panel-run-brief': '运行评估',
    'panel-diagnostics': '诊断区',
    'panel-bottleneck': '待处理事项',
    'panel-automation': '自动引擎',
    'panel-training': '训练清单',
    'panel-journey': '处理记录',
    'panel-river': '运行记录',
    'panel-flow-stage': '处理流程',
    'ai-side-heartbeat': '侧栏状态',
    'ai-side-decision': '侧栏决策',
    'ai-side-orchestrator': '建议操作编排',
    'ai-side-guardrail': '检查安全说明',
    'ai-side-rootcause': '原因分析',
    'ai-side-impact': '影响评估',
    'ai-side-motion': '显示模式',
  }
  return map[String(value || '')] || String(value || '定位')
}

function filterPatchPreviewText(patch?: PulseFilterPatch) {
  if (!patch || !Object.keys(patch).length) return '全局'
  const labelMap: Record<string, string> = {
    days: '时间',
    skill_id: 'Skill',
    department: '部门',
    run_id: 'Run',
    source_type: '来源',
    event_type: '事件',
    artifact_kind: '资产',
    target_type: '建议',
    sf_tool: 'SF',
    q: '关键词',
    artifact_status: '资产',
    candidate_status: '建议',
  }
  return Object.entries(patch)
    .filter(([, value]) => value !== undefined && value !== null && String(value) !== '')
    .slice(0, 2)
    .map(([key, value]) => `${labelMap[key] || key}=${String(value)}`)
    .join(' · ') || '全局'
}

function motionPreviewText(value?: MotionMode) {
  if (value === 'trace') return '查看'
  if (value === 'calm') return '降噪'
  return '自动'
}

function numberText(value?: number | string | null) {
  const n = Number(value || 0)
  return Number.isFinite(n) ? n.toLocaleString() : '0'
}

function percent(value?: number | null) {
  const n = Number(value || 0)
  return `${Math.round(n * 100)}%`
}

function scaleDuration(value: string, scale: number) {
  const seconds = Number(String(value || '').replace('s', ''))
  if (!Number.isFinite(seconds) || seconds <= 0) return value
  return `${(seconds * scale).toFixed(1)}s`
}

function durationText(value?: number | string | null) {
  const total = Math.max(0, Math.floor(Number(value || 0)))
  if (!Number.isFinite(total)) return '-'
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  const parts: string[] = []
  if (days) parts.push(`${days}天`)
  if (hours) parts.push(`${hours}小时`)
  if (minutes && parts.length < 2) parts.push(`${minutes}分钟`)
  if (!parts.length) parts.push(`${seconds}秒`)
  return parts.slice(0, 2).join(' ')
}

function serviceModeText(value?: string | null) {
  const text = String(value || '')
  if (text === 'independent_worker') return '独立 Worker'
  if (text === 'web_worker') return 'Web Worker'
  if (text === 'disabled') return '已关闭'
  return text || '未知'
}

function shortId(value?: string) {
  const text = String(value || '')
  if (text.length <= 18) return text || '-'
  return `${text.slice(0, 8)}…${text.slice(-6)}`
}

function formatTime(value?: string | null) {
  if (!value) return '-'
  return String(value).replace('T', ' ').slice(0, 16)
}

function formatTimeSeconds(value?: string | null) {
  if (!value) return '-'
  return String(value).replace('T', ' ').slice(0, 19)
}

function addSecondsText(value?: string | null, seconds = 0) {
  if (!value || !Number.isFinite(Number(seconds))) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  date.setSeconds(date.getSeconds() + Number(seconds || 0))
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function startPulseLiveRefresh() {
  if (typeof window === 'undefined' || import.meta.env.MODE === 'test') return
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
  stopPulseLiveRefresh()
  pulseLiveTimer = window.setInterval(() => {
    if (typeof document !== 'undefined' && document.visibilityState !== 'visible') {
      stopPulseLiveRefresh()
      return
    }
    void reloadPulse({ background: true })
  }, PULSE_LIVE_REFRESH_MS)
}

function stopPulseLiveRefresh() {
  if (typeof window === 'undefined' || pulseLiveTimer === null) return
  window.clearInterval(pulseLiveTimer)
  pulseLiveTimer = null
}

function handleVisibilityChange() {
  if (typeof document === 'undefined') return
  if (document.visibilityState !== 'visible') {
    stopPulseLiveRefresh()
    return
  }
  startPulseLiveRefresh()
  void reloadPulse({ background: true })
}

onMounted(async () => {
  applyRouteQuery()
  await reloadAll()
  if (queryText('artifact_id') || queryText('candidate_id') || queryText('event_id')) {
    await ensureDetailListsLoaded()
  }
  startPulseLiveRefresh()
  applyDeepSelectionFromQuery()
  await nextTick()
  syncActiveSidePanelFromScroll()
  syncActiveAiSideSectionFromScroll()
  if (typeof window !== 'undefined') {
    window.addEventListener('scroll', requestSidePanelSync, { passive: true })
    window.addEventListener('resize', requestSidePanelSync, { passive: true })
    window.addEventListener('resize', requestAiSideSectionSync, { passive: true })
  }
  if (typeof document !== 'undefined' && import.meta.env.MODE !== 'test') {
    document.addEventListener('visibilitychange', handleVisibilityChange)
  }
})

onActivated(() => {
  startPulseLiveRefresh()
  void reloadPulse({ background: true })
})

onDeactivated(() => {
  stopPulseLiveRefresh()
})

onBeforeUnmount(() => {
  stopPulseLiveRefresh()
  if (typeof window !== 'undefined') {
    window.removeEventListener('scroll', requestSidePanelSync)
    window.removeEventListener('resize', requestSidePanelSync)
    window.removeEventListener('resize', requestAiSideSectionSync)
  }
  if (typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', handleVisibilityChange)
  }
})
</script>

<style scoped>
.learning-page {
  min-height: 100%;
  --ai-text: var(--ai-ink-1);
  --ai-muted: var(--ai-ink-3);
  --ai-primary: var(--ai-accent);
  --learning-surface: var(--ai-surface);
  --learning-surface-soft: var(--ai-surface-2);
  --learning-border: var(--ai-border);
  --learning-page-x: 28px;
  --learning-page-y: 20px;
  --learning-radius: var(--ai-radius, var(--sf-radius-md, 8px));
  --learning-radius-sm: var(--ai-radius-s, var(--sf-radius-sm, 5px));
  --learning-gap: var(--sf-spacing-lg, 16px);
  --learning-gap-sm: var(--sf-spacing-sm, 8px);
  --learning-transition: var(--sf-transition-fast, 0.15s ease);
  --learning-shadow: var(--ai-shadow-1);
  color: var(--ai-text);
  background: var(--ai-bg);
}
.learning-head {
  align-items: flex-start;
  padding-inline: var(--learning-page-x);
}
.learning-actions {
  display: flex;
  gap: var(--learning-gap-sm);
  flex-wrap: wrap;
  justify-content: flex-end;
}
.layout-simple .learning-actions .advanced-action {
  display: none;
}
.learning-workspace {
  display: grid;
  grid-template-areas: "main side";
  grid-template-columns: minmax(0, 1fr) minmax(300px, 340px);
  gap: var(--sf-spacing-xl, 24px);
  align-items: start;
  padding: var(--learning-page-y) var(--learning-page-x) var(--sf-spacing-xxl, 32px);
}
.learning-content {
  grid-area: main;
  display: grid;
  gap: var(--learning-gap);
  min-width: 0;
}
.pulse-chain-board {
  display: grid;
  gap: var(--sf-spacing-lg, 16px);
  padding: var(--sf-spacing-lg, 16px);
  overflow: hidden;
  border-radius: var(--learning-radius);
  background: var(--learning-surface);
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.06);
}
.pulse-chain-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--learning-gap);
}
.pulse-chain-head h2 {
  margin: var(--sf-spacing-sm, 8px) 0 var(--sf-spacing-xs, 4px);
  font-size: 20px;
  color: var(--ai-text);
}
.pulse-chain-head p {
  margin: 0;
  max-width: 760px;
  color: var(--ai-muted);
  font-size: 13px;
  line-height: 1.6;
}
.pulse-chain-meta {
  display: flex;
  align-items: center;
  gap: var(--learning-gap-sm);
  flex-wrap: wrap;
  justify-content: flex-end;
  color: var(--ai-muted);
  font-size: 12px;
}
.pulse-live-status {
  display: inline-flex;
  align-items: center;
  min-height: 30px;
  padding: 6px 10px;
  border: 1px solid var(--learning-border);
  border-radius: var(--learning-radius-sm);
  background: var(--learning-surface-soft);
  color: var(--ai-muted);
  font-weight: 800;
  white-space: nowrap;
}
.pulse-live-status::before {
  content: "";
  width: 7px;
  height: 7px;
  margin-right: 7px;
  border-radius: 999px;
  background: #059669;
  box-shadow: 0 0 0 4px rgba(5, 150, 105, 0.10);
}
.pulse-live-status.is-error::before {
  background: #dc2626;
  box-shadow: 0 0 0 4px rgba(220, 38, 38, 0.10);
}
.pulse-chain-meta .is-loading {
  gap: 6px;
}
.pulse-chain-meta .is-loading::before {
  background: #0ea5e9;
  animation: pulse-chain-dot 1.15s ease-in-out infinite;
}
.pulse-chain-steps {
  position: relative;
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: var(--learning-gap-sm);
  align-items: stretch;
}
.pulse-chain-steps::before {
  content: "";
  position: absolute;
  left: 36px;
  right: 36px;
  top: 34px;
  height: 1px;
  background: linear-gradient(90deg, rgba(37, 99, 235, 0.20), rgba(15, 118, 110, 0.24), rgba(5, 150, 105, 0.22));
  pointer-events: none;
}
.pulse-chain-card {
  --pulse-chain-tone: #0284c7;
  position: relative;
  display: grid;
  grid-template-rows: 86px 98px 150px;
  gap: var(--learning-gap-sm);
  align-content: start;
  align-items: stretch;
  min-width: 0;
  min-height: 374px;
  padding: var(--sf-spacing-md, 12px);
  border: 1px solid color-mix(in srgb, var(--pulse-chain-tone) 22%, var(--learning-border));
  border-radius: var(--learning-radius-sm);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--pulse-chain-tone) 5%, #fff), var(--learning-surface) 44%),
    var(--learning-surface);
  animation: pulse-chain-in 0.32s ease both;
  animation-delay: var(--pulse-chain-delay);
  box-shadow: 0 8px 18px rgba(15, 23, 42, 0.045);
}
.pulse-chain-card.tone-source { --pulse-chain-tone: #2563eb; }
.pulse-chain-card.tone-artifact { --pulse-chain-tone: #0f766e; }
.pulse-chain-card.tone-good,
.pulse-chain-card.tone-sink { --pulse-chain-tone: #059669; }
.pulse-chain-card.tone-warn { --pulse-chain-tone: #d97706; }
.pulse-chain-card.tone-bad,
.pulse-chain-card.tone-danger { --pulse-chain-tone: #dc2626; }
.pulse-chain-card.is-loading {
  border-style: dashed;
}
.pulse-chain-main {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: var(--learning-gap-sm);
  align-items: start;
  width: 100%;
  min-width: 0;
  min-height: 86px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.pulse-chain-index {
  position: relative;
  z-index: 1;
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--pulse-chain-tone);
  color: #fff;
  font-weight: 800;
  font-size: 12px;
}
.pulse-chain-copy {
  display: grid;
  gap: 3px;
  align-content: start;
  min-width: 0;
}
.pulse-chain-copy small {
  min-width: 0;
  overflow: hidden;
  color: var(--pulse-chain-tone);
  font-size: 11px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pulse-chain-copy strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-text);
  font-size: 14px;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pulse-chain-copy em,
.pulse-chain-items em,
.pulse-chain-empty {
  color: var(--ai-muted);
  font-size: 12px;
  line-height: 1.45;
  font-style: normal;
}
.pulse-chain-copy em {
  display: -webkit-box;
  min-height: 35px;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.pulse-chain-count {
  display: inline-flex;
  width: fit-content;
  max-width: 100%;
  min-width: 0;
  padding: 2px 7px;
  border: 1px solid color-mix(in srgb, var(--pulse-chain-tone) 28%, var(--learning-border));
  border-radius: 999px;
  background: color-mix(in srgb, var(--pulse-chain-tone) 8%, var(--learning-surface));
  color: var(--pulse-chain-tone);
  font-size: 11px;
  font-weight: 700;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pulse-chain-main > i {
  display: inline-flex;
  justify-content: flex-end;
  width: 20px;
  color: var(--pulse-chain-tone);
}
.pulse-chain-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  grid-auto-rows: 46px;
  gap: 6px;
  align-content: start;
  min-height: 98px;
}
.pulse-chain-metrics button,
.pulse-chain-items button {
  min-width: 0;
  border: 1px solid color-mix(in srgb, var(--pulse-chain-tone) 14%, var(--learning-border));
  border-radius: var(--learning-radius-sm);
  background: color-mix(in srgb, var(--learning-surface) 96%, #fff);
  color: inherit;
  cursor: pointer;
}
.pulse-chain-metrics button {
  display: grid;
  gap: 2px;
  align-content: center;
  height: 46px;
  padding: 7px;
}
.pulse-chain-metrics em {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pulse-chain-metrics strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-text);
  font-size: 13px;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pulse-chain-items {
  display: grid;
  grid-auto-rows: 46px;
  gap: 6px;
  align-content: start;
  min-height: 150px;
}
.pulse-chain-items button {
  display: grid;
  gap: 2px;
  align-content: center;
  height: 46px;
  overflow: hidden;
  padding: 7px 8px;
  text-align: left;
}
.pulse-chain-items button span,
.pulse-chain-items button em {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pulse-chain-metrics button.is-loading strong,
.pulse-chain-items button.is-loading span,
.pulse-chain-items button.is-loading em {
  overflow: hidden;
  color: transparent;
  border-radius: 6px;
  background:
    linear-gradient(90deg, rgba(148, 163, 184, 0.16), rgba(148, 163, 184, 0.32), rgba(148, 163, 184, 0.16));
  background-size: 220% 100%;
  animation: pulse-chain-skeleton 1.15s ease-in-out infinite;
}
.pulse-chain-items button.is-loading span {
  width: 74%;
}
.pulse-chain-items button.is-loading em {
  width: 92%;
}
.pulse-chain-empty.error {
  color: #b45309;
}
@keyframes pulse-chain-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes pulse-chain-skeleton {
  0% { background-position: 100% 0; }
  100% { background-position: -120% 0; }
}
@keyframes pulse-chain-dot {
  0%, 100% { opacity: 0.35; transform: scale(0.86); }
  50% { opacity: 1; transform: scale(1.08); }
}
.pulse-overview-board {
  display: grid;
  gap: var(--sf-spacing-lg, 16px);
  padding: var(--sf-spacing-lg, 16px);
  overflow: hidden;
  border-radius: var(--learning-radius);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--learning-surface) 96%, var(--sf-tone-bg, transparent)), var(--learning-surface)),
    var(--learning-surface);
  box-shadow: var(--learning-shadow);
}
.pulse-overview-head {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
}
.pulse-overview-head h2 {
  margin: var(--sf-spacing-sm, 8px) 0 var(--sf-spacing-xs, 4px);
  color: var(--ai-text);
  font-size: 20px;
  letter-spacing: -0.02em;
}
.pulse-overview-head p {
  max-width: 720px;
  margin: 0;
  color: var(--ai-muted);
  font-size: 13px;
  line-height: 1.65;
}
.pulse-overview-actions {
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.pulse-decision-strip {
  --decision-strip-tone: #2563eb;
  position: relative;
  display: grid;
  grid-template-columns: 1.18fr 1fr 1fr 1fr;
  gap: var(--learning-gap-sm);
  padding: var(--sf-spacing-md, 12px);
  overflow: hidden;
  border: 1px solid var(--sf-tone-border, color-mix(in srgb, var(--decision-strip-tone) 16%, transparent));
  border-radius: var(--learning-radius);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--sf-tone-bg, transparent) 44%, var(--learning-surface)), var(--learning-surface));
}
.pulse-decision-strip::before {
  content: '';
  position: absolute;
  left: 18px;
  right: 18px;
  top: 50%;
  height: 2px;
  border-radius: 999px;
  background: linear-gradient(90deg, color-mix(in srgb, var(--decision-strip-tone) 60%, transparent), rgba(20, 184, 166, 0.34), rgba(245, 158, 11, 0.28));
  opacity: 0.3;
}
.pulse-decision-strip::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(115deg, transparent 0 35%, color-mix(in srgb, var(--decision-strip-tone) 9%, transparent) 46%, transparent 62% 100%);
  transform: translateX(-100%);
  animation: briefRailSweep var(--motion-loop-river, 7s) linear infinite;
  pointer-events: none;
}
.pulse-decision-item {
  --decision-item-tone: #2563eb;
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px 9px;
  align-items: center;
  min-height: 78px;
  padding: var(--sf-spacing-sm, 8px);
  overflow: hidden;
  border: 1px solid var(--sf-tone-border, color-mix(in srgb, var(--decision-item-tone) 15%, transparent));
  border-radius: var(--learning-radius);
  background: color-mix(in srgb, var(--sf-tone-bg, transparent) 22%, var(--learning-surface));
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  box-shadow: var(--learning-shadow);
  transition: border-color var(--learning-transition), box-shadow var(--learning-transition), transform var(--learning-transition);
}
.pulse-decision-item:hover {
  border-color: var(--sf-tone-border, color-mix(in srgb, var(--decision-item-tone) 34%, transparent));
  box-shadow: var(--ai-shadow-2);
  transform: translateY(-1px);
}
.pulse-decision-item i {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 13px;
  background: color-mix(in srgb, var(--decision-item-tone) 10%, transparent);
  color: var(--decision-item-tone);
  font-style: normal;
}
.pulse-decision-item span {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.pulse-decision-item strong,
.pulse-decision-item em,
.pulse-decision-item b {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pulse-decision-item strong {
  color: var(--ai-text);
  font-size: 12px;
  font-weight: 950;
}
.pulse-decision-item em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.pulse-decision-item b {
  grid-column: 2;
  width: fit-content;
  max-width: 100%;
  padding: 4px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--decision-item-tone) 9%, transparent);
  color: var(--decision-item-tone);
  font-size: 11px;
  font-weight: 950;
}
.pulse-decision-strip.tone-good { --decision-strip-tone: #059669; }
.pulse-decision-strip.tone-warn { --decision-strip-tone: #d97706; }
.pulse-decision-strip.tone-bad { --decision-strip-tone: #dc2626; }
.pulse-decision-strip.tone-info { --decision-strip-tone: #0284c7; }
.pulse-decision-item.tone-good { --decision-item-tone: #059669; }
.pulse-decision-item.tone-warn { --decision-item-tone: #d97706; }
.pulse-decision-item.tone-bad { --decision-item-tone: #dc2626; }
.pulse-decision-item.tone-info { --decision-item-tone: #0284c7; }
.pulse-automation-strip {
  position: relative;
  display: grid;
  gap: var(--learning-gap-sm);
  padding: var(--sf-spacing-md, 12px);
  overflow: hidden;
  border: 1px solid var(--sf-tone-border, var(--learning-border));
  border-radius: var(--learning-radius);
  background:
    linear-gradient(135deg, var(--sf-tone-bg, rgba(37, 99, 235, 0.06)), transparent 42%),
    var(--learning-surface);
}
.pulse-automation-strip::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 4px;
  border-radius: 999px;
  background: var(--sf-tone-fg, #2563eb);
  opacity: 0.9;
}
.pulse-automation-head {
  position: relative;
  z-index: 1;
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
}
.pulse-automation-head span {
  color: var(--ai-text);
  font-size: 13px;
  font-weight: 950;
}
.pulse-automation-head em {
  min-width: 0;
  overflow: hidden;
  color: color-mix(in srgb, var(--sf-tone-fg, #2563eb) 82%, #0f172a);
  font-size: 11px;
  font-style: normal;
  font-weight: 850;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pulse-automation-grid {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.pulse-automation-card {
  --automation-card-level: 0%;
  --automation-card-tone: var(--sf-tone-fg, #2563eb);
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  min-height: 68px;
  padding: 10px;
  overflow: hidden;
  border: 1px solid var(--sf-tone-border, color-mix(in srgb, var(--automation-card-tone) 16%, transparent));
  border-radius: calc(var(--learning-radius) - 4px);
  background: color-mix(in srgb, var(--sf-tone-bg, transparent) 38%, var(--learning-surface));
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: border-color var(--learning-transition), box-shadow var(--learning-transition), transform var(--learning-transition);
}
.pulse-automation-card::after {
  content: '';
  position: absolute;
  left: 10px;
  right: 10px;
  bottom: 7px;
  height: 3px;
  overflow: hidden;
  border-radius: 999px;
  background:
    linear-gradient(90deg, var(--automation-card-tone) var(--automation-card-level), rgba(148, 163, 184, 0.16) var(--automation-card-level));
  opacity: 0.72;
}
.pulse-automation-card:hover {
  border-color: var(--sf-tone-border, color-mix(in srgb, var(--automation-card-tone) 34%, transparent));
  box-shadow: var(--ai-shadow-2);
  transform: translateY(-1px);
}
.pulse-automation-card i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--automation-card-tone) 10%, transparent);
  color: var(--automation-card-tone);
  font-style: normal;
}
.pulse-automation-card span {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.pulse-automation-card strong,
.pulse-automation-card em,
.pulse-automation-card b {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pulse-automation-card strong {
  color: var(--ai-text);
  font-size: 12px;
  font-weight: 950;
}
.pulse-automation-card em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.pulse-automation-card b {
  padding: 4px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--automation-card-tone) 10%, transparent);
  color: var(--automation-card-tone);
  font-size: 11px;
  font-weight: 950;
}
.pulse-overview-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.pulse-value-panel {
  display: grid;
  gap: var(--learning-gap-sm);
  padding: var(--sf-spacing-md, 12px);
  border: 1px solid var(--learning-border);
  border-radius: var(--learning-radius);
  background: var(--learning-surface);
}
.pulse-value-head {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
}
.pulse-value-head span {
  color: var(--ai-text);
  font-size: 13px;
  font-weight: 950;
}
.pulse-value-head em {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  font-weight: 850;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pulse-overview-card {
  --overview-tone: #2563eb;
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px 10px;
  align-items: center;
  min-height: 112px;
  padding: var(--sf-spacing-md, 12px);
  overflow: hidden;
  border: 1px solid var(--sf-tone-border, color-mix(in srgb, var(--overview-tone) 18%, transparent));
  border-radius: var(--learning-radius);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--sf-tone-bg, transparent) 36%, var(--learning-surface)), var(--learning-surface));
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: border-color var(--learning-transition), box-shadow var(--learning-transition), transform var(--learning-transition);
}
.pulse-overview-card::after {
  content: '';
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 8px;
  height: 3px;
  border-radius: 999px;
  background: linear-gradient(90deg, color-mix(in srgb, var(--overview-tone) 72%, transparent), rgba(148, 163, 184, 0.14));
  opacity: 0.58;
}
.pulse-overview-card:hover {
  border-color: var(--sf-tone-border, color-mix(in srgb, var(--overview-tone) 34%, transparent));
  box-shadow: var(--ai-shadow-2);
  transform: translateY(-1px);
}
.pulse-overview-card i {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 14px;
  background: color-mix(in srgb, var(--overview-tone) 10%, transparent);
  color: var(--overview-tone);
  font-style: normal;
}
.pulse-overview-card span {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.pulse-overview-card small {
  width: fit-content;
  padding: 2px 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--overview-tone) 9%, transparent);
  color: color-mix(in srgb, var(--overview-tone) 84%, #0f172a);
  font-size: 10px;
  font-weight: 950;
}
.pulse-overview-card strong,
.pulse-overview-card em,
.pulse-overview-card b,
.pulse-overview-card u {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pulse-overview-card strong {
  color: var(--ai-text);
  font-size: 13px;
  font-weight: 950;
}
.pulse-overview-card em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.pulse-overview-card u {
  color: color-mix(in srgb, var(--overview-tone) 82%, #0f172a);
  font-size: 11px;
  font-weight: 850;
  text-decoration: none;
}
.pulse-overview-card b {
  grid-column: 2;
  width: fit-content;
  max-width: 100%;
  padding: 3px 7px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--overview-tone) 10%, transparent);
  color: var(--overview-tone);
  font-size: 11px;
  font-weight: 950;
}
.pulse-overview-card.tone-good { --overview-tone: #059669; }
.pulse-overview-card.tone-warn { --overview-tone: #d97706; }
.pulse-overview-card.tone-bad { --overview-tone: #dc2626; }
.pulse-overview-card.tone-info { --overview-tone: #0284c7; }
.pulse-overview-path {
  position: relative;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--learning-gap-sm);
  padding: var(--sf-spacing-md, 12px);
  overflow: hidden;
  border: 1px solid var(--learning-border);
  border-radius: var(--learning-radius);
  background: var(--learning-surface-soft);
}
.pulse-overview-path::before {
  content: '';
  position: absolute;
  left: 24px;
  right: 24px;
  top: 50%;
  height: 2px;
  border-radius: 999px;
  background: linear-gradient(90deg, #10b981, color-mix(in srgb, var(--motion-health, #2563eb) 84%, #2563eb), #f59e0b);
  opacity: 0.38;
}
.pulse-overview-step {
  --overview-step-tone: #2563eb;
  position: relative;
  z-index: 1;
  display: grid;
  gap: 4px;
  min-width: 0;
  min-height: 84px;
  padding: var(--sf-spacing-sm, 8px);
  border: 1px solid var(--sf-tone-border, color-mix(in srgb, var(--overview-step-tone) 16%, transparent));
  border-radius: var(--learning-radius);
  background: color-mix(in srgb, var(--sf-tone-bg, transparent) 22%, var(--learning-surface));
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: border-color var(--learning-transition), box-shadow var(--learning-transition), transform var(--learning-transition);
  animation: routeStepBreath 5s ease-in-out infinite;
  animation-delay: var(--overview-step-delay);
}
.pulse-overview-step::after {
  content: '';
  position: absolute;
  left: 10px;
  right: 10px;
  bottom: 8px;
  height: 3px;
  border-radius: 999px;
  background:
    linear-gradient(90deg, var(--overview-step-tone) var(--overview-step-progress), rgba(148, 163, 184, 0.14) 0);
}
.pulse-overview-step:hover,
.pulse-overview-step.active {
  border-color: var(--sf-tone-border, color-mix(in srgb, var(--overview-step-tone) 34%, transparent));
  box-shadow: var(--ai-shadow-2);
  transform: translateY(-1px);
}
.pulse-overview-step i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--overview-step-tone) 10%, transparent);
  color: var(--overview-step-tone);
  font-style: normal;
}
.pulse-overview-step span,
.pulse-overview-step strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pulse-overview-step span {
  color: var(--ai-muted);
  font-size: 11px;
  font-weight: 900;
}
.pulse-overview-step strong {
  color: var(--ai-text);
  font-size: 12px;
  font-weight: 950;
}
.pulse-overview-step.tone-good { --overview-step-tone: #059669; }
.pulse-overview-step.tone-warn { --overview-step-tone: #d97706; }
.pulse-overview-step.tone-bad { --overview-step-tone: #dc2626; }
.pulse-overview-step.tone-info { --overview-step-tone: #0284c7; }
.layout-simple .ai-side-orbit,
.layout-simple .ai-side-hud,
.layout-simple .ai-side-index,
.layout-simple #panel-run-brief,
.layout-simple #panel-diagnostics,
.layout-simple #ai-side-heartbeat,
.layout-simple #ai-side-decision,
.layout-simple #ai-side-orchestrator,
.layout-simple #ai-side-guardrail,
.layout-simple #ai-side-autopilot,
.layout-simple #ai-side-evidence,
.layout-simple #ai-side-rootcause,
.layout-simple #ai-side-impact,
.layout-simple #ai-side-heat,
.layout-simple #ai-side-playbook,
.layout-simple #ai-side-motion,
.layout-simple #ai-side-perspective,
.layout-simple #ai-side-focus,
.layout-simple #ai-side-meter,
.layout-simple #ai-side-actions,
.layout-simple #ai-side-panels,
.layout-simple #ai-side-route,
.layout-simple #ai-side-queue {
  display: none;
}
.layout-simple .ai-side-menu {
  gap: var(--sf-spacing-md, 12px);
}
.layout-simple .ai-mission-steps {
  display: none;
}
.layout-simple .ai-sticky-command {
  display: none;
}
.layout-simple .pulse-decision-strip::after,
.layout-simple .ai-mission-brief::after {
  display: none;
}
.layout-simple .pulse-overview-step,
.layout-simple .ai-mission-step {
  animation: none;
}
.ai-side-menu {
  grid-area: side;
  position: sticky;
  top: 74px;
  z-index: 8;
  display: grid;
  gap: var(--sf-spacing-md, 12px);
  max-height: calc(100vh - 92px);
  padding: var(--sf-spacing-lg, 16px);
  overflow: auto;
  scrollbar-gutter: stable;
  border-color: var(--learning-border);
  border-radius: var(--learning-radius);
  background: color-mix(in srgb, var(--learning-surface) 96%, var(--sf-tone-bg, transparent));
  box-shadow: var(--learning-shadow);
  backdrop-filter: blur(12px);
}
.ai-side-scroll-progress {
  position: sticky;
  top: calc(-1 * var(--sf-spacing-lg, 16px));
  z-index: 9;
  display: block;
  height: 4px;
  margin: calc(-1 * var(--sf-spacing-lg, 16px)) calc(-1 * var(--sf-spacing-lg, 16px)) 0;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.14);
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.08);
}
.ai-side-scroll-progress::after {
  content: '';
  display: block;
  width: var(--ai-side-scroll-progress);
  height: 100%;
  border-radius: inherit;
  background:
    linear-gradient(90deg, #10b981, color-mix(in srgb, var(--motion-health, #2563eb) 86%, #2563eb), #f59e0b);
  box-shadow: 0 0 18px color-mix(in srgb, var(--motion-health, #2563eb) 34%, transparent);
  transition: width 0.16s ease;
}
.ai-side-head {
  display: grid;
  gap: 6px;
}
.ai-side-head strong {
  color: var(--ai-text);
  font-size: 18px;
  letter-spacing: -0.03em;
}
.ai-side-head em {
  color: var(--ai-muted);
  font-size: 12px;
  font-style: normal;
  line-height: 1.5;
}
.ai-side-scroll-progress { order: 0; }
.ai-side-head { order: 1; }
.ai-side-quick-controls { order: 2; }
.ai-operation-center { order: 3; }
.ai-simple-priority { order: 4; }
.ai-side-index { order: 5; }
.ai-side-hud { order: 6; }
.ai-mission-brief { order: 7; }
#ai-side-context { order: 8; }
#ai-side-trail { order: 9; }
#ai-side-autopilot { order: 10; }
#ai-side-evidence { order: 11; }
#ai-side-rootcause { order: 12; }
#ai-side-impact { order: 13; }
#ai-side-guardrail { order: 14; }
#ai-side-heat { order: 15; }
#ai-side-playbook { order: 16; }
.ai-side-more-tools { order: 17; }
#ai-side-decision { order: 18; }
#ai-side-orchestrator { order: 19; }
#ai-side-heartbeat { order: 20; }
#ai-side-focus { order: 21; }
#ai-side-motion { order: 22; }
#ai-side-perspective { order: 23; }
#ai-side-panels { order: 24; }
#ai-side-route { order: 25; }
#ai-side-queue { order: 26; }
#ai-side-actions { order: 27; }
.ai-side-orbit { order: 28; }
.ai-sticky-command { order: 40; }
.ai-side-quick-controls {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--learning-gap-sm);
  padding: var(--sf-spacing-sm, 8px);
  border: 1px solid var(--learning-border);
  border-radius: var(--learning-radius);
  background: var(--learning-surface);
}
.ai-side-quick-controls button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-width: 0;
  min-height: 30px;
  padding: 0 var(--sf-spacing-sm, 8px);
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: var(--learning-radius-sm);
  background: var(--learning-surface-soft);
  color: var(--ai-muted);
  cursor: pointer;
  font-size: 11px;
  font-weight: 900;
  text-align: center;
  transition: background var(--learning-transition), border-color var(--learning-transition), color var(--learning-transition);
}
.ai-side-quick-controls button:hover,
.ai-side-quick-controls button.active {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 24%, transparent);
  background: color-mix(in srgb, var(--motion-health, #2563eb) 9%, var(--learning-surface));
  color: color-mix(in srgb, var(--motion-health, #2563eb) 82%, var(--ai-text));
}
.ai-side-quick-controls button[aria-pressed="true"] {
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--motion-health, #2563eb) 20%, transparent);
}
.ai-side-quick-controls svg,
.ai-side-quick-controls .icon {
  flex: 0 0 auto;
}
.ai-side-quick-controls span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.layout-simple .ai-side-more-tools,
.layout-full .ai-side-menu:not(.tools-expanded) #ai-side-heartbeat,
.layout-full .ai-side-menu:not(.tools-expanded) #ai-side-decision,
.layout-full .ai-side-menu:not(.tools-expanded) #ai-side-orchestrator,
.layout-full .ai-side-menu:not(.tools-expanded) #ai-side-focus,
.layout-full .ai-side-menu:not(.tools-expanded) #ai-side-motion,
.layout-full .ai-side-menu:not(.tools-expanded) #ai-side-perspective,
.layout-full .ai-side-menu:not(.tools-expanded) #ai-side-panels,
.layout-full .ai-side-menu:not(.tools-expanded) #ai-side-route,
.layout-full .ai-side-menu:not(.tools-expanded) #ai-side-queue,
.layout-full .ai-side-menu:not(.tools-expanded) #ai-side-actions {
  display: none;
}
.ai-side-more-tools {
  display: grid;
}
.ai-side-more-tools button {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: var(--learning-gap-sm);
  align-items: center;
  width: 100%;
  min-height: 46px;
  padding: var(--sf-spacing-sm, 8px);
  border: 1px dashed color-mix(in srgb, var(--motion-health, #2563eb) 22%, var(--learning-border));
  border-radius: var(--learning-radius);
  background: color-mix(in srgb, var(--learning-surface-soft) 66%, var(--learning-surface));
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: background var(--learning-transition), border-color var(--learning-transition), box-shadow var(--learning-transition);
}
.ai-side-more-tools button:hover {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 34%, transparent);
  background: color-mix(in srgb, var(--motion-health, #2563eb) 8%, var(--learning-surface));
  box-shadow: var(--ai-shadow-1);
}
.ai-side-more-tools i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--learning-radius-sm);
  background: color-mix(in srgb, var(--motion-health, #2563eb) 9%, transparent);
  color: color-mix(in srgb, var(--motion-health, #2563eb) 82%, #0f172a);
  font-style: normal;
}
.ai-side-more-tools span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-side-more-tools strong,
.ai-side-more-tools em,
.ai-side-more-tools b {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-side-more-tools strong {
  font-size: 12px;
  font-weight: 950;
}
.ai-side-more-tools em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-side-more-tools b {
  padding: 3px 7px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--motion-health, #2563eb) 9%, transparent);
  color: color-mix(in srgb, var(--motion-health, #2563eb) 82%, #0f172a);
  font-size: 11px;
  font-weight: 900;
}
.ai-operation-center {
  --operation-tone: #2563eb;
  --operation-confidence: 50%;
  --operation-load: 8%;
  --operation-scan-opacity: 0.18;
  position: relative;
  display: grid;
  gap: var(--learning-gap-sm);
  padding: var(--sf-spacing-md, 12px);
  overflow: hidden;
  border: 1px solid var(--sf-tone-border, color-mix(in srgb, var(--operation-tone) 18%, transparent));
  border-radius: var(--learning-radius);
  background:
    radial-gradient(circle at 8% 0%, color-mix(in srgb, var(--operation-tone) 14%, transparent), transparent 38%),
    linear-gradient(180deg, color-mix(in srgb, var(--sf-tone-bg, transparent) 38%, var(--learning-surface)), var(--learning-surface));
  box-shadow: var(--learning-shadow);
}
.layout-full .ai-operation-center,
.layout-simple .ai-mission-brief {
  display: none;
}
.ai-operation-center::before {
  content: '';
  position: absolute;
  right: 12px;
  bottom: 8px;
  left: 12px;
  z-index: 0;
  height: 3px;
  overflow: hidden;
  border-radius: 999px;
  background:
    linear-gradient(90deg, var(--operation-tone) var(--operation-load), rgba(148, 163, 184, 0.14) var(--operation-load));
  opacity: 0.58;
}
.ai-operation-center::after {
  content: '';
  position: absolute;
  inset: 0;
  background:
    linear-gradient(115deg, transparent 0 34%, color-mix(in srgb, var(--operation-tone) 9%, transparent) 48%, transparent 64% 100%);
  opacity: var(--operation-scan-opacity);
  transform: translateX(calc(-100% + var(--operation-active-index, 0) * 18%));
  pointer-events: none;
  transition: transform 0.28s ease;
}
.layout-simple:not(.motion-alert):not(.motion-trace) .ai-operation-center::after {
  display: none;
}
.ai-operation-head {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--learning-gap-sm);
  align-items: center;
}
.ai-operation-head span {
  display: grid;
  gap: 3px;
  min-width: 0;
}
.ai-operation-head small {
  color: color-mix(in srgb, var(--operation-tone) 84%, #0f172a);
  font-size: 10px;
  font-weight: 950;
  letter-spacing: 0.04em;
}
.ai-operation-head strong,
.ai-operation-head em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-operation-head strong {
  color: var(--ai-text);
  font-size: 13px;
  font-weight: 950;
}
.ai-operation-head em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-operation-head b {
  display: grid;
  place-items: center;
  width: 58px;
  height: 58px;
  border-radius: 999px;
  background:
    radial-gradient(circle, var(--learning-surface) 0 55%, transparent 56%),
    conic-gradient(var(--operation-tone) var(--operation-confidence), rgba(148, 163, 184, 0.16) 0);
  color: var(--operation-tone);
  font-weight: 950;
}
.ai-operation-head b i {
  font-size: 14px;
  font-style: normal;
  line-height: 1;
}
.ai-operation-head b small {
  max-width: 42px;
  overflow: hidden;
  color: var(--ai-muted);
  font-size: 9px;
  letter-spacing: 0;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-operation-status {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  padding: 6px;
  border: 1px solid color-mix(in srgb, var(--operation-tone) 12%, transparent);
  border-radius: var(--learning-radius-sm);
  background: color-mix(in srgb, var(--learning-surface) 88%, var(--learning-surface-soft));
}
.ai-operation-status-item {
  --operation-status-tone: #2563eb;
  display: grid;
  grid-template-columns: 7px minmax(0, 1fr);
  gap: 2px 6px;
  align-items: center;
  min-height: 34px;
  padding: 5px 6px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: background var(--learning-transition), transform var(--learning-transition);
}
.ai-operation-status-item:hover {
  background: color-mix(in srgb, var(--operation-status-tone) 8%, transparent);
  transform: translateY(-1px);
}
.ai-operation-status-item i {
  grid-row: span 2;
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--operation-status-tone);
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--operation-status-tone) 10%, transparent);
}
.ai-operation-status-item span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-operation-status-item strong,
.ai-operation-status-item em,
.ai-operation-status-item b {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-operation-status-item strong {
  color: var(--ai-muted);
  font-size: 9px;
  font-weight: 900;
}
.ai-operation-status-item em {
  color: color-mix(in srgb, var(--operation-status-tone) 86%, #0f172a);
  font-size: 10px;
  font-style: normal;
  font-weight: 950;
}
.ai-operation-status-item b {
  grid-column: 2;
  color: var(--ai-muted);
  font-size: 9px;
  font-weight: 800;
}
.ai-operation-status-item.tone-good { --operation-status-tone: #059669; }
.ai-operation-status-item.tone-warn { --operation-status-tone: #d97706; }
.ai-operation-status-item.tone-bad { --operation-status-tone: #dc2626; }
.ai-operation-status-item.tone-info { --operation-status-tone: #0284c7; }
.ai-operation-actions {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: 1.2fr 1fr 1fr;
  gap: 6px;
}
.ai-operation-actions button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-width: 0;
  min-height: 30px;
  padding: 0 7px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--operation-tone) 16%, transparent);
  border-radius: var(--learning-radius-sm);
  background: var(--learning-surface);
  color: color-mix(in srgb, var(--operation-tone) 84%, #0f172a);
  cursor: pointer;
  font-size: 10px;
  font-weight: 950;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: border-color var(--learning-transition), box-shadow var(--learning-transition), transform var(--learning-transition);
}
.ai-operation-actions button:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--operation-tone) 34%, transparent);
  box-shadow: var(--ai-shadow-2);
  transform: translateY(-1px);
}
.ai-operation-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.56;
}
.ai-operation-center.tone-good { --operation-tone: #059669; }
.ai-operation-center.tone-warn { --operation-tone: #d97706; }
.ai-operation-center.tone-bad { --operation-tone: #dc2626; }
.ai-operation-center.tone-info { --operation-tone: #0284c7; }
.ai-operation-decision {
  position: relative;
  z-index: 1;
  display: grid;
  gap: 6px;
}
.ai-operation-decision-card {
  --operation-decision-tone: #2563eb;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 3px 7px;
  align-items: center;
  min-height: 40px;
  padding: 7px;
  overflow: hidden;
  border: 1px solid var(--sf-tone-border, color-mix(in srgb, var(--operation-decision-tone) 14%, transparent));
  border-radius: var(--learning-radius-sm);
  background: color-mix(in srgb, var(--sf-tone-bg, transparent) 16%, var(--learning-surface));
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: border-color var(--learning-transition), box-shadow var(--learning-transition), transform var(--learning-transition);
}
.ai-operation-decision-card:hover {
  border-color: color-mix(in srgb, var(--operation-decision-tone) 34%, transparent);
  box-shadow: var(--ai-shadow-2);
  transform: translateY(-1px);
}
.ai-operation-decision-card i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 9px;
  background: color-mix(in srgb, var(--operation-decision-tone) 10%, transparent);
  color: var(--operation-decision-tone);
  font-style: normal;
}
.ai-operation-decision-card span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-operation-decision-card strong,
.ai-operation-decision-card em,
.ai-operation-decision-card b {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-operation-decision-card strong {
  color: var(--ai-text);
  font-size: 10px;
  font-weight: 950;
}
.ai-operation-decision-card em {
  color: var(--ai-muted);
  font-size: 9px;
  font-style: normal;
  font-weight: 800;
}
.ai-operation-decision-card b {
  max-width: 92px;
  padding: 2px 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--operation-decision-tone) 9%, transparent);
  color: var(--operation-decision-tone);
  font-size: 10px;
  font-weight: 950;
}
.ai-operation-decision-card.tone-good { --operation-decision-tone: #059669; }
.ai-operation-decision-card.tone-warn { --operation-decision-tone: #d97706; }
.ai-operation-decision-card.tone-bad { --operation-decision-tone: #dc2626; }
.ai-operation-decision-card.tone-info { --operation-decision-tone: #0284c7; }
.ai-operation-queue {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}
.ai-operation-queue-chip {
  --operation-queue-tone: #2563eb;
  --operation-queue-level: 0%;
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 2px 6px;
  align-items: center;
  min-height: 54px;
  padding: 7px;
  overflow: hidden;
  border: 1px solid var(--sf-tone-border, color-mix(in srgb, var(--operation-queue-tone) 14%, transparent));
  border-radius: var(--learning-radius-sm);
  background: color-mix(in srgb, var(--sf-tone-bg, transparent) 20%, var(--learning-surface));
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: border-color var(--learning-transition), box-shadow var(--learning-transition), transform var(--learning-transition);
}
.ai-operation-queue-chip::after {
  content: '';
  position: absolute;
  left: 7px;
  right: 7px;
  bottom: 5px;
  height: 2px;
  border-radius: 999px;
  background:
    linear-gradient(90deg, var(--operation-queue-tone) var(--operation-queue-level), rgba(148, 163, 184, 0.14) var(--operation-queue-level));
  opacity: 0.74;
}
.ai-operation-queue-chip:hover {
  border-color: color-mix(in srgb, var(--operation-queue-tone) 34%, transparent);
  box-shadow: var(--ai-shadow-2);
  transform: translateY(-1px);
}
.ai-operation-queue-chip i {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 9px;
  background: color-mix(in srgb, var(--operation-queue-tone) 10%, transparent);
  color: var(--operation-queue-tone);
  font-style: normal;
}
.ai-operation-queue-chip span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-operation-queue-chip strong,
.ai-operation-queue-chip em,
.ai-operation-queue-chip b {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-operation-queue-chip strong {
  color: var(--ai-text);
  font-size: 10px;
  font-weight: 950;
}
.ai-operation-queue-chip em {
  color: var(--ai-muted);
  font-size: 9px;
  font-style: normal;
  font-weight: 800;
}
.ai-operation-queue-chip b {
  grid-column: 2;
  width: fit-content;
  max-width: 100%;
  padding: 2px 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--operation-queue-tone) 9%, transparent);
  color: var(--operation-queue-tone);
  font-size: 10px;
  font-weight: 950;
}
.ai-operation-queue-chip.tone-good { --operation-queue-tone: #059669; }
.ai-operation-queue-chip.tone-warn { --operation-queue-tone: #d97706; }
.ai-operation-queue-chip.tone-bad { --operation-queue-tone: #dc2626; }
.ai-operation-queue-chip.tone-info { --operation-queue-tone: #0284c7; }
.ai-simple-priority {
  display: grid;
  gap: var(--learning-gap-sm);
  padding: var(--sf-spacing-md, 12px);
  border: 1px solid var(--learning-border);
  border-radius: var(--learning-radius);
  background: var(--learning-surface);
}
.layout-full .ai-simple-priority {
  display: none;
}
.ai-simple-priority-title {
  display: flex;
  gap: var(--learning-gap-sm);
  align-items: center;
  justify-content: space-between;
}
.ai-simple-priority-title span {
  color: var(--ai-text);
  font-size: 12px;
  font-weight: 950;
}
.ai-simple-priority-title em {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-simple-priority-list {
  display: grid;
  gap: var(--learning-gap-sm);
}
.ai-simple-priority-item {
  --priority-tone: #2563eb;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 2px var(--learning-gap-sm);
  align-items: center;
  min-height: 68px;
  padding: var(--sf-spacing-sm, 8px);
  border: 1px solid var(--sf-tone-border, color-mix(in srgb, var(--priority-tone) 16%, transparent));
  border-radius: var(--learning-radius);
  background: linear-gradient(90deg, color-mix(in srgb, var(--sf-tone-bg, transparent) 36%, transparent), var(--learning-surface));
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: border-color var(--learning-transition), box-shadow var(--learning-transition), transform var(--learning-transition);
}
.ai-simple-priority-item:hover {
  border-color: var(--sf-tone-border, color-mix(in srgb, var(--priority-tone) 34%, transparent));
  box-shadow: var(--ai-shadow-2);
  transform: translateY(-1px);
}
.ai-simple-priority-item.primary {
  min-height: 76px;
  border-color: color-mix(in srgb, var(--priority-tone) 30%, transparent);
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--sf-tone-bg, transparent) 54%, transparent), var(--learning-surface));
  box-shadow: inset 3px 0 0 color-mix(in srgb, var(--priority-tone) 72%, transparent);
}
.ai-simple-priority-item.primary strong {
  font-size: 13px;
}
.ai-simple-priority-item i {
  grid-row: span 3;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: var(--learning-radius-sm);
  background: color-mix(in srgb, var(--priority-tone) 10%, transparent);
  color: var(--priority-tone);
  font-style: normal;
}
.ai-simple-priority-item span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-simple-priority-item strong,
.ai-simple-priority-item em,
.ai-simple-priority-item small,
.ai-simple-priority-item b {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-simple-priority-item strong {
  color: var(--ai-text);
  font-size: 12px;
  font-weight: 950;
}
.ai-simple-priority-item em,
.ai-simple-priority-item small {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-simple-priority-item small {
  color: color-mix(in srgb, var(--priority-tone) 84%, var(--ai-ink-2));
  font-weight: 650;
}
.ai-simple-priority-item b {
  max-width: 82px;
  padding: 3px 7px;
  border-radius: var(--sf-radius-pill, 999px);
  background: color-mix(in srgb, var(--priority-tone) 9%, transparent);
  color: var(--priority-tone);
  font-size: 11px;
  font-weight: 950;
}
.ai-simple-priority-item.tone-good { --priority-tone: #059669; }
.ai-simple-priority-item.tone-warn { --priority-tone: #d97706; }
.ai-simple-priority-item.tone-bad { --priority-tone: #dc2626; }
.ai-simple-priority-item.tone-info { --priority-tone: #0284c7; }
.ai-side-hud {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--learning-gap-sm);
}
.ai-side-hud-card {
  --hud-tone: #2563eb;
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 3px var(--learning-gap-sm);
  align-items: center;
  min-height: 64px;
  padding: var(--sf-spacing-sm, 8px);
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--hud-tone) 18%, transparent);
  border-radius: var(--learning-radius);
  background: var(--learning-surface);
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  box-shadow: none;
  transition: border-color var(--learning-transition), box-shadow var(--learning-transition), background var(--learning-transition);
}
.ai-side-hud-card::after {
  content: '';
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: 6px;
  height: 2px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--hud-tone) 54%, transparent);
  opacity: 0.22;
}
.ai-side-hud-card:hover {
  border-color: color-mix(in srgb, var(--hud-tone) 34%, transparent);
  background: color-mix(in srgb, var(--sf-tone-bg, transparent) 18%, var(--learning-surface));
  box-shadow: var(--ai-shadow-2);
}
.ai-side-hud-card i {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--learning-radius-sm);
  background: color-mix(in srgb, var(--hud-tone) 10%, transparent);
  color: var(--hud-tone);
  font-style: normal;
}
.ai-side-hud-card span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-side-hud-card strong,
.ai-side-hud-card em,
.ai-side-hud-card b {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-side-hud-card strong {
  color: var(--ai-text);
  font-size: 11px;
  font-weight: 950;
}
.ai-side-hud-card em {
  color: var(--ai-muted);
  font-size: 10px;
  font-style: normal;
}
.ai-side-hud-card b {
  grid-column: 2;
  width: fit-content;
  max-width: 100%;
  padding: 2px 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--hud-tone) 10%, transparent);
  color: var(--hud-tone);
  font-size: 11px;
  font-weight: 950;
}
.ai-side-hud-card.tone-good { --hud-tone: #059669; }
.ai-side-hud-card.tone-warn { --hud-tone: #d97706; }
.ai-side-hud-card.tone-bad { --hud-tone: #dc2626; }
.ai-side-hud-card.tone-info { --hud-tone: #0284c7; }
.ai-mission-brief {
  --mission-tone: #2563eb;
  --mission-confidence: 50%;
  position: relative;
  display: grid;
  gap: var(--learning-gap-sm);
  padding: var(--sf-spacing-md, 12px);
  overflow: hidden;
  border: 1px solid var(--sf-tone-border, color-mix(in srgb, var(--mission-tone) 18%, transparent));
  border-radius: var(--learning-radius);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--sf-tone-bg, transparent) 38%, var(--learning-surface)), var(--learning-surface));
  box-shadow: var(--learning-shadow);
}
.ai-mission-brief::before {
  content: '';
  position: absolute;
  left: 10px;
  right: 10px;
  top: 8px;
  height: 3px;
  border-radius: 999px;
  background:
    linear-gradient(90deg, var(--mission-tone) var(--mission-confidence), rgba(148, 163, 184, 0.16) 0);
  box-shadow: 0 0 16px color-mix(in srgb, var(--mission-tone) 22%, transparent);
}
.ai-mission-brief::after {
  content: '';
  position: absolute;
  inset: 0;
  background:
    linear-gradient(115deg, transparent 0 30%, color-mix(in srgb, var(--mission-tone) 10%, transparent) 44%, transparent 58% 100%);
  transform: translateX(-100%);
  animation: briefRailSweep var(--motion-loop-river, 7s) linear infinite;
  pointer-events: none;
}
.ai-mission-summary {
  position: relative;
  z-index: 1;
  display: grid;
  gap: 5px;
  width: 100%;
  padding: var(--sf-spacing-sm, 8px);
  border: 1px solid var(--sf-tone-border, color-mix(in srgb, var(--mission-tone) 16%, transparent));
  border-radius: var(--learning-radius);
  background: var(--learning-surface);
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: border-color var(--learning-transition), box-shadow var(--learning-transition), transform var(--learning-transition);
}
.ai-mission-summary:hover {
  border-color: color-mix(in srgb, var(--mission-tone) 34%, transparent);
  box-shadow: 0 12px 26px color-mix(in srgb, var(--mission-tone) 13%, transparent);
  transform: translateY(-1px);
}
.ai-mission-kicker {
  display: flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
}
.ai-mission-kicker i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 12px;
  background:
    radial-gradient(circle, rgba(255, 255, 255, 0.94) 0 48%, color-mix(in srgb, var(--mission-tone) 12%, transparent) 49%),
    conic-gradient(var(--mission-tone) var(--mission-confidence), rgba(148, 163, 184, 0.16) 0);
  color: var(--mission-tone);
  font-style: normal;
}
.ai-mission-kicker small {
  color: color-mix(in srgb, var(--mission-tone) 82%, #0f172a);
  font-size: 10px;
  font-weight: 950;
  letter-spacing: 0.04em;
}
.ai-mission-kicker b {
  margin-left: auto;
  padding: 3px 7px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--mission-tone) 10%, transparent);
  color: var(--mission-tone);
  font-size: 11px;
  font-weight: 950;
}
.ai-mission-summary strong,
.ai-mission-summary em,
.ai-mission-output {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-mission-summary strong {
  color: var(--ai-text);
  font-size: 14px;
  font-weight: 950;
  letter-spacing: -0.02em;
}
.ai-mission-summary em,
.ai-mission-output {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-mission-output {
  width: fit-content;
  max-width: 100%;
  padding: 4px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--mission-tone) 8%, transparent);
  color: color-mix(in srgb, var(--mission-tone) 86%, #0f172a);
  font-weight: 900;
}
.ai-mission-steps {
  position: relative;
  z-index: 1;
  display: grid;
  gap: 7px;
}
.ai-mission-steps::before {
  content: '';
  position: absolute;
  left: 24px;
  top: 8px;
  bottom: 8px;
  width: 2px;
  border-radius: 999px;
  background: linear-gradient(180deg, color-mix(in srgb, var(--mission-tone) 62%, transparent), rgba(148, 163, 184, 0.14));
  pointer-events: none;
}
.ai-mission-step {
  --mission-step-tone: #2563eb;
  position: relative;
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr) auto;
  gap: 2px 8px;
  align-items: center;
  min-height: 52px;
  padding: var(--sf-spacing-sm, 8px);
  overflow: hidden;
  border: 1px solid var(--sf-tone-border, color-mix(in srgb, var(--mission-step-tone) 14%, transparent));
  border-radius: var(--learning-radius);
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--sf-tone-bg, transparent) 44%, transparent), var(--learning-surface));
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: border-color var(--learning-transition), box-shadow var(--learning-transition), transform var(--learning-transition);
  animation: decisionFloat 5.4s ease-in-out infinite;
  animation-delay: var(--mission-step-delay);
}
.ai-mission-step::after {
  content: '';
  position: absolute;
  left: 46px;
  right: 10px;
  bottom: 6px;
  height: 2px;
  border-radius: 999px;
  background:
    linear-gradient(90deg, var(--mission-step-tone) var(--mission-step-progress), rgba(148, 163, 184, 0.14) 0);
}
.ai-mission-step:hover,
.ai-mission-step.active {
  border-color: color-mix(in srgb, var(--mission-step-tone) 34%, transparent);
  box-shadow: 0 12px 26px color-mix(in srgb, var(--mission-step-tone) 12%, transparent);
  transform: translateY(-1px);
}
.ai-mission-step i {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--mission-step-tone) 10%, transparent);
  color: var(--mission-step-tone);
  font-style: normal;
}
.ai-mission-step span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-mission-step strong,
.ai-mission-step em,
.ai-mission-step b {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-mission-step strong {
  font-size: 11px;
  font-weight: 950;
}
.ai-mission-step em {
  color: var(--ai-muted);
  font-size: 10px;
  font-style: normal;
}
.ai-mission-step b {
  max-width: 76px;
  padding: 3px 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--mission-step-tone) 9%, transparent);
  color: var(--mission-step-tone);
  font-size: 10px;
  font-weight: 950;
}
.ai-mission-brief.tone-good { --mission-tone: #059669; }
.ai-mission-brief.tone-warn { --mission-tone: #d97706; }
.ai-mission-brief.tone-bad { --mission-tone: #dc2626; }
.ai-mission-brief.tone-info { --mission-tone: #0284c7; }
.ai-mission-step.tone-good { --mission-step-tone: #059669; }
.ai-mission-step.tone-warn { --mission-step-tone: #d97706; }
.ai-mission-step.tone-bad { --mission-step-tone: #dc2626; }
.ai-mission-step.tone-info { --mission-step-tone: #0284c7; }
.ai-side-orbit {
  position: relative;
  min-height: 132px;
  overflow: hidden;
  border: 1px solid rgba(37, 99, 235, 0.14);
  border-radius: 28px;
  background:
    radial-gradient(circle at 50% 50%, color-mix(in srgb, var(--motion-health, #2563eb) 16%, transparent), transparent 46%),
    linear-gradient(135deg, rgba(239, 246, 255, 0.86), rgba(255, 255, 255, 0.74));
  cursor: pointer;
}
.ai-side-orbit::before,
.ai-side-orbit::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  border: 1px solid rgba(37, 99, 235, 0.14);
  border-radius: 999px;
  transform: translate(-50%, -50%);
}
.ai-side-orbit::before {
  width: 72%;
  height: 72%;
}
.ai-side-orbit::after {
  width: 46%;
  height: 46%;
  border-style: dashed;
  animation: sideOrbitSpin var(--motion-loop-global, 8s) linear infinite;
}
.orbit-core {
  position: absolute;
  left: 50%;
  top: 50%;
  z-index: 2;
  display: grid;
  place-items: center;
  width: 70px;
  height: 70px;
  border: 1px solid color-mix(in srgb, var(--motion-health, #2563eb) 34%, transparent);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.86);
  color: var(--ai-text);
  transform: translate(-50%, -50%);
  box-shadow: 0 18px 42px color-mix(in srgb, var(--motion-health, #2563eb) 18%, transparent);
}
.orbit-core::after {
  content: '';
  position: absolute;
  inset: -8px;
  border: 1px solid color-mix(in srgb, var(--motion-health, #2563eb) 22%, transparent);
  border-radius: inherit;
  animation: coreWave 2.4s ease-out infinite;
}
.orbit-core b {
  position: relative;
  z-index: 1;
  color: color-mix(in srgb, var(--motion-health, #2563eb) 86%, #0f172a);
  font-size: 22px;
  line-height: 1;
}
.orbit-core i {
  position: relative;
  z-index: 1;
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  font-weight: 800;
}
.orbit-signal {
  position: absolute;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid rgba(37, 99, 235, 0.16);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.86);
  color: var(--ai-primary);
  transform: translate(-50%, -50%);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
  animation: sideSignalFloat 3.5s ease-in-out infinite;
  animation-delay: var(--side-orbit-delay);
}
.orbit-signal.tone-danger { color: #dc2626; border-color: rgba(239, 68, 68, 0.22); }
.orbit-signal.tone-sink { color: #059669; border-color: rgba(16, 185, 129, 0.22); }
.orbit-signal.tone-artifact { color: #7c3aed; border-color: rgba(124, 58, 237, 0.22); }
.orbit-signal.tone-candidate { color: #ea580c; border-color: rgba(249, 115, 22, 0.22); }
.orbit-signal.tone-review { color: #4f46e5; border-color: rgba(99, 102, 241, 0.22); }
.ai-side-index {
  position: sticky;
  top: -14px;
  z-index: 5;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  padding: 8px;
  margin: -4px -4px 0;
  border: 1px solid rgba(37, 99, 235, 0.13);
  border-radius: 22px;
  background:
    radial-gradient(circle at 12% 0%, color-mix(in srgb, var(--motion-health, #2563eb) 13%, transparent), transparent 36%),
    rgba(255, 255, 255, 0.88);
  box-shadow: 0 18px 38px rgba(15, 23, 42, 0.08);
  backdrop-filter: blur(16px);
}
.ai-side-index-button {
  position: relative;
  display: grid;
  gap: 2px;
  min-width: 0;
  padding: 8px 6px 7px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.16);
  border-radius: 16px;
  background: rgba(248, 250, 252, 0.74);
  color: var(--ai-muted);
  text-align: left;
  cursor: pointer;
  transition:
    border-color 0.2s ease,
    background 0.2s ease,
    box-shadow 0.2s ease,
    color 0.2s ease;
}
.ai-side-index-button::after {
  content: '';
  position: absolute;
  inset: auto 8px 6px 8px;
  height: 2px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--ai-index-tone, #2563eb) 68%, transparent);
  opacity: 0.25;
  box-shadow: 0 0 12px color-mix(in srgb, var(--ai-index-tone, #2563eb) 22%, transparent);
}
.ai-side-index-button i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 9px;
  background: color-mix(in srgb, var(--ai-index-tone, #2563eb) 10%, transparent);
  color: var(--ai-index-tone, #2563eb);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--ai-index-tone, #2563eb) 12%, transparent);
}
.ai-side-index-button span,
.ai-side-index-button b {
  position: relative;
  z-index: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-side-index-button span {
  color: var(--ai-muted);
  font-size: 10px;
  font-weight: 800;
}
.ai-side-index-button b {
  color: var(--ai-text);
  font-size: 11px;
  font-weight: 950;
  letter-spacing: -0.02em;
}
.ai-side-index-button:hover,
.ai-side-index-button.active {
  border-color: color-mix(in srgb, var(--ai-index-tone, #2563eb) 34%, transparent);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--ai-index-tone, #2563eb) 10%, transparent), rgba(255, 255, 255, 0.82));
  color: var(--ai-text);
  box-shadow: 0 12px 26px color-mix(in srgb, var(--ai-index-tone, #2563eb) 14%, transparent);
}
.ai-side-index-button.active::after {
  opacity: 0.78;
}
.ai-side-index-button.tone-good { --ai-index-tone: #059669; }
.ai-side-index-button.tone-warn { --ai-index-tone: #d97706; }
.ai-side-index-button.tone-bad { --ai-index-tone: #dc2626; }
.ai-side-index-button.tone-info { --ai-index-tone: #0284c7; }
.ai-side-section {
  position: relative;
  display: grid;
  gap: var(--learning-gap-sm);
  min-width: 0;
  padding: var(--sf-spacing-md, 12px);
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.14);
  border-radius: var(--learning-radius);
  background:
    radial-gradient(circle at 0% 0%, color-mix(in srgb, var(--motion-health, #2563eb) 9%, transparent), transparent 36%),
    rgba(255, 255, 255, 0.62);
}
.ai-side-section-title {
  position: relative;
  z-index: 1;
  display: flex;
  gap: 8px;
  align-items: baseline;
  justify-content: space-between;
}
.ai-side-section-title span {
  color: var(--ai-text);
  font-size: 13px;
  font-weight: 900;
}
.ai-side-section-title em {
  overflow: hidden;
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-side-context-card {
  position: relative;
  display: grid;
  gap: 7px;
  padding: 12px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--motion-health, #2563eb) 22%, transparent);
  border-radius: 20px;
  background:
    radial-gradient(circle at 8% 12%, color-mix(in srgb, var(--motion-health, #2563eb) 16%, transparent), transparent 38%),
    rgba(255, 255, 255, 0.82);
  box-shadow: 0 12px 28px color-mix(in srgb, var(--motion-health, #2563eb) 10%, transparent);
}
.ai-side-context-card::after {
  content: '';
  position: absolute;
  left: -18%;
  top: 0;
  width: 42%;
  height: 100%;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--motion-health, #2563eb) 10%, transparent), transparent);
  animation: briefRailSweep var(--motion-loop-river, 7s) linear infinite;
}
.ai-side-context-card > * {
  position: relative;
  z-index: 1;
}
.ai-side-context-type {
  width: fit-content;
  max-width: 100%;
  padding: 3px 8px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 11px;
  font-weight: 900;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-side-context-card strong,
.ai-side-context-card em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-side-context-card strong {
  color: var(--ai-text);
  font-size: 14px;
}
.ai-side-context-card em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-side-context-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.ai-side-context-actions button {
  padding: 5px 8px;
  border: 1px solid rgba(37, 99, 235, 0.18);
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.07);
  color: var(--ai-primary);
  cursor: pointer;
  font-size: 11px;
  font-weight: 800;
}
.ai-side-context-actions button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.ai-heartbeat-panel {
  position: relative;
}
.ai-heartbeat-core {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  width: 100%;
  min-height: 74px;
  padding: 11px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--motion-health, #2563eb) 22%, transparent);
  border-radius: 24px;
  background:
    radial-gradient(circle at 16% 50%, color-mix(in srgb, var(--motion-health, #2563eb) 16%, transparent), transparent 38%),
    linear-gradient(135deg, rgba(255, 255, 255, 0.94), rgba(239, 246, 255, 0.74));
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  box-shadow: 0 14px 34px color-mix(in srgb, var(--motion-health, #2563eb) 10%, transparent);
}
.ai-heartbeat-core::after {
  content: '';
  position: absolute;
  left: -24%;
  top: 0;
  width: 42%;
  height: 100%;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--motion-health, #2563eb) 10%, transparent), transparent);
  animation: briefRailSweep var(--motion-loop-river, 7s) linear infinite;
}
.ai-heartbeat-core > * {
  position: relative;
  z-index: 1;
}
.ai-heartbeat-ring {
  display: grid;
  place-items: center;
  width: 56px;
  height: 56px;
  border-radius: 999px;
  background:
    radial-gradient(circle, rgba(255, 255, 255, 0.96) 0 57%, transparent 58%),
    conic-gradient(color-mix(in srgb, var(--motion-health, #2563eb) 84%, #2563eb) var(--heartbeat-score), rgba(148, 163, 184, 0.16) 0);
  box-shadow: 0 12px 28px color-mix(in srgb, var(--motion-health, #2563eb) 14%, transparent);
}
.ai-heartbeat-ring::after {
  content: '';
  position: absolute;
  inset: -6px;
  border: 1px solid color-mix(in srgb, var(--motion-health, #2563eb) 20%, transparent);
  border-radius: inherit;
  animation: coreWave 2.8s ease-out infinite;
}
.ai-heartbeat-ring b {
  color: color-mix(in srgb, var(--motion-health, #2563eb) 88%, #0f172a);
  font-size: 16px;
  line-height: 1;
}
.ai-heartbeat-ring i {
  color: var(--ai-muted);
  font-size: 10px;
  font-style: normal;
  font-weight: 900;
}
.ai-heartbeat-core > span:not(.ai-heartbeat-ring) {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.ai-heartbeat-core strong,
.ai-heartbeat-core em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-heartbeat-core strong {
  font-size: 13px;
}
.ai-heartbeat-core em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-heartbeat-core small {
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 11px;
  font-weight: 900;
  white-space: nowrap;
}
.ai-heartbeat-core.tone-good small { color: #059669; background: rgba(16, 185, 129, 0.1); }
.ai-heartbeat-core.tone-warn small { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.ai-heartbeat-core.tone-bad small { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.ai-heartbeat-steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 7px;
}
.ai-heartbeat-step {
  position: relative;
  display: grid;
  gap: 3px;
  min-height: 84px;
  padding: 9px 8px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.78);
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
  animation: decisionFloat 4.8s ease-in-out infinite;
  animation-delay: var(--heartbeat-step-delay);
}
.ai-heartbeat-step::after {
  content: '';
  position: absolute;
  inset: auto 8px 7px 8px;
  height: 2px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(37, 99, 235, 0.1), color-mix(in srgb, var(--motion-health, #2563eb) 44%, transparent), rgba(20, 184, 166, 0.14));
  background-size: 180% 100%;
  animation: railFlow var(--motion-loop-river, 7s) linear infinite;
}
.ai-heartbeat-step:hover {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 32%, transparent);
  box-shadow: 0 10px 22px color-mix(in srgb, var(--motion-health, #2563eb) 10%, transparent);
  transform: translateY(-1px);
}
.ai-heartbeat-step i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 11px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-style: normal;
}
.ai-heartbeat-step span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-heartbeat-step strong,
.ai-heartbeat-step em,
.ai-heartbeat-step b {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-heartbeat-step strong {
  font-size: 11px;
}
.ai-heartbeat-step em {
  color: var(--ai-muted);
  font-size: 10px;
  font-style: normal;
}
.ai-heartbeat-step b {
  color: var(--ai-text);
  font-size: 12px;
}
.ai-heartbeat-step.tone-good i { color: #059669; background: rgba(16, 185, 129, 0.1); }
.ai-heartbeat-step.tone-warn i { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.ai-heartbeat-step.tone-bad i { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.ai-heartbeat-step.tone-info i { color: #0284c7; background: rgba(14, 165, 233, 0.1); }
.ai-decision-stack {
  display: grid;
  gap: 8px;
}
.ai-decision-item {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 3px 9px;
  align-items: center;
  width: 100%;
  min-height: 58px;
  padding: 9px 10px 12px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 18px;
  background:
    radial-gradient(circle at 8% 50%, color-mix(in srgb, var(--motion-health, #2563eb) 10%, transparent), transparent 38%),
    rgba(255, 255, 255, 0.82);
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
  animation: decisionFloat 4.8s ease-in-out infinite;
  animation-delay: var(--decision-delay);
}
.ai-decision-item::before {
  content: '';
  position: absolute;
  left: 48px;
  right: 12px;
  bottom: 7px;
  height: 3px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.16);
}
.ai-decision-item::after {
  content: '';
  position: absolute;
  left: 48px;
  bottom: 7px;
  width: var(--decision-score);
  max-width: calc(100% - 60px);
  height: 3px;
  border-radius: 999px;
  background: linear-gradient(90deg, color-mix(in srgb, var(--motion-health, #2563eb) 84%, #2563eb), #14b8a6, #f59e0b);
  background-size: 180% 100%;
  animation: railFlow var(--motion-loop-river, 7s) linear infinite;
}
.ai-decision-item:hover {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 34%, transparent);
  box-shadow: 0 12px 26px color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent);
  transform: translateY(-1px);
}
.ai-decision-item i {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 13px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-style: normal;
}
.ai-decision-item span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-decision-item strong,
.ai-decision-item em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-decision-item strong {
  font-size: 12px;
}
.ai-decision-item em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-decision-item b {
  grid-row: span 2;
  max-width: 86px;
  overflow: hidden;
  color: var(--ai-text);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-decision-item small {
  width: fit-content;
  padding: 3px 7px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 11px;
  font-weight: 900;
}
.ai-decision-item.tone-good i,
.ai-decision-item.tone-good small { color: #059669; background: rgba(16, 185, 129, 0.1); }
.ai-decision-item.tone-warn i,
.ai-decision-item.tone-warn small { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.ai-decision-item.tone-bad i,
.ai-decision-item.tone-bad small { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.ai-decision-item.tone-info i,
.ai-decision-item.tone-info small { color: #0284c7; background: rgba(14, 165, 233, 0.1); }
.ai-command-plan {
  position: relative;
  display: grid;
  gap: 8px;
  padding: 10px;
  overflow: hidden;
  border: 1px solid rgba(37, 99, 235, 0.14);
  border-radius: 24px;
  background:
    radial-gradient(circle at 10% 8%, color-mix(in srgb, var(--motion-health, #2563eb) 14%, transparent), transparent 40%),
    linear-gradient(145deg, rgba(255, 255, 255, 0.9), rgba(239, 246, 255, 0.68));
  box-shadow: 0 16px 36px rgba(15, 23, 42, 0.07);
}
.ai-command-plan::after {
  content: '';
  position: absolute;
  left: -30%;
  top: 0;
  width: 48%;
  height: 100%;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--motion-health, #2563eb) 10%, transparent), transparent);
  animation: briefRailSweep var(--motion-loop-river, 7s) linear infinite;
  pointer-events: none;
}
.ai-command-plan-track {
  position: absolute;
  left: 23px;
  top: 18px;
  bottom: 18px;
  width: 3px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.16);
  pointer-events: none;
}
.ai-command-plan-track::after {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  width: 100%;
  height: var(--command-plan-progress);
  border-radius: inherit;
  background: linear-gradient(180deg, #10b981, color-mix(in srgb, var(--motion-health, #2563eb) 86%, #2563eb), #f59e0b);
  box-shadow: 0 0 16px color-mix(in srgb, var(--motion-health, #2563eb) 32%, transparent);
}
.ai-command-step {
  --command-tone: #2563eb;
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  gap: 3px 9px;
  align-items: center;
  width: 100%;
  min-height: 64px;
  padding: 8px 9px 9px 8px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--command-tone) 18%, transparent);
  border-radius: 18px;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--command-tone) 8%, transparent), rgba(255, 255, 255, 0.82)),
    rgba(255, 255, 255, 0.84);
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.18s ease, box-shadow 0.18s ease, background 0.18s ease;
}
.ai-command-step::after {
  content: '';
  position: absolute;
  left: 52px;
  right: 10px;
  bottom: 6px;
  height: 2px;
  border-radius: 999px;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--command-tone) 84%, #2563eb) var(--command-step-score), rgba(148, 163, 184, 0.14) 0);
  opacity: 0.78;
}
.ai-command-step:hover {
  border-color: color-mix(in srgb, var(--command-tone) 36%, transparent);
  box-shadow: 0 12px 26px color-mix(in srgb, var(--command-tone) 13%, transparent);
}
.ai-command-step small {
  grid-column: 1 / -1;
  color: color-mix(in srgb, var(--command-tone) 82%, #0f172a);
  font-size: 10px;
  font-weight: 950;
  letter-spacing: 0.02em;
}
.ai-command-step i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 13px;
  background:
    radial-gradient(circle, rgba(255, 255, 255, 0.92) 0 48%, color-mix(in srgb, var(--command-tone) 12%, transparent) 49%),
    conic-gradient(color-mix(in srgb, var(--command-tone) 82%, #2563eb) var(--command-step-score), rgba(148, 163, 184, 0.16) 0);
  color: var(--command-tone);
  font-style: normal;
}
.ai-command-step span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-command-step strong,
.ai-command-step em,
.ai-command-step b,
.ai-command-step u {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-command-step strong {
  font-size: 12px;
  font-weight: 950;
}
.ai-command-step em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-command-step b {
  max-width: 78px;
  color: var(--ai-text);
  font-size: 12px;
  font-weight: 950;
}
.ai-command-step u {
  grid-column: 2 / -1;
  width: fit-content;
  max-width: 100%;
  padding: 3px 7px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--command-tone) 10%, transparent);
  color: var(--command-tone);
  font-size: 11px;
  font-weight: 900;
  text-decoration: none;
}
.ai-command-step.tone-good { --command-tone: #059669; }
.ai-command-step.tone-warn { --command-tone: #d97706; }
.ai-command-step.tone-bad { --command-tone: #dc2626; }
.ai-command-step.tone-info { --command-tone: #0284c7; }
.ai-execution-guardrail {
  --guardrail-confidence: 50%;
  --guardrail-risk: 28%;
}
.ai-guardrail-board {
  position: relative;
  display: grid;
  gap: 8px;
  padding: 10px;
  overflow: hidden;
  border: 1px solid rgba(37, 99, 235, 0.14);
  border-radius: 24px;
  background:
    radial-gradient(circle at 12% 8%, color-mix(in srgb, var(--motion-health, #2563eb) 15%, transparent), transparent 40%),
    linear-gradient(145deg, rgba(255, 255, 255, 0.9), rgba(248, 250, 252, 0.76));
  box-shadow: 0 16px 36px rgba(15, 23, 42, 0.07);
}
.ai-guardrail-board::before {
  content: '';
  position: absolute;
  left: 12px;
  right: 12px;
  top: 9px;
  height: 3px;
  border-radius: 999px;
  background:
    linear-gradient(90deg, #10b981 var(--guardrail-confidence), color-mix(in srgb, #ef4444 var(--guardrail-risk), rgba(148, 163, 184, 0.16)) 0);
  box-shadow: 0 0 16px color-mix(in srgb, var(--motion-health, #2563eb) 18%, transparent);
}
.ai-guardrail-scan {
  position: absolute;
  inset: 0;
  background:
    linear-gradient(115deg, transparent 0 28%, color-mix(in srgb, var(--motion-health, #2563eb) 10%, transparent) 42%, transparent 56% 100%);
  transform: translateX(-100%);
  animation: briefRailSweep var(--motion-loop-river, 7s) linear infinite;
  pointer-events: none;
}
.ai-guardrail-card {
  --guard-tone: #2563eb;
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) auto;
  gap: 3px 9px;
  align-items: center;
  width: 100%;
  min-height: 58px;
  padding: 10px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--guard-tone) 18%, transparent);
  border-radius: 18px;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--guard-tone) 8%, transparent), rgba(255, 255, 255, 0.82)),
    rgba(255, 255, 255, 0.86);
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
  animation: decisionFloat 5.2s ease-in-out infinite;
  animation-delay: var(--guardrail-delay);
}
.ai-guardrail-card::after {
  content: '';
  position: absolute;
  left: 52px;
  right: 12px;
  bottom: 7px;
  height: 2px;
  border-radius: 999px;
  background: linear-gradient(90deg, color-mix(in srgb, var(--guard-tone) 78%, #2563eb), rgba(148, 163, 184, 0.12));
  opacity: 0.52;
}
.ai-guardrail-card:hover {
  border-color: color-mix(in srgb, var(--guard-tone) 36%, transparent);
  box-shadow: 0 12px 26px color-mix(in srgb, var(--guard-tone) 13%, transparent);
  transform: translateY(-1px);
}
.ai-guardrail-card i {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 13px;
  background:
    radial-gradient(circle, rgba(255, 255, 255, 0.94) 0 48%, color-mix(in srgb, var(--guard-tone) 12%, transparent) 49%),
    conic-gradient(var(--guard-tone) 72%, rgba(148, 163, 184, 0.16) 0);
  color: var(--guard-tone);
  font-style: normal;
}
.ai-guardrail-card span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-guardrail-card strong,
.ai-guardrail-card em,
.ai-guardrail-card b {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-guardrail-card strong {
  font-size: 12px;
  font-weight: 950;
}
.ai-guardrail-card em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-guardrail-card b {
  max-width: 82px;
  padding: 3px 7px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--guard-tone) 10%, transparent);
  color: var(--guard-tone);
  font-size: 11px;
  font-weight: 950;
}
.ai-guardrail-card.tone-good { --guard-tone: #059669; }
.ai-guardrail-card.tone-warn { --guard-tone: #d97706; }
.ai-guardrail-card.tone-bad { --guard-tone: #dc2626; }
.ai-guardrail-card.tone-info { --guard-tone: #0284c7; }
.ai-autopilot {
  position: relative;
  display: grid;
  gap: 10px;
  padding: 12px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--motion-health, #2563eb) 24%, transparent);
  border-radius: 24px;
  background:
    radial-gradient(circle at 18% 18%, color-mix(in srgb, var(--motion-health, #2563eb) 18%, transparent), transparent 36%),
    linear-gradient(135deg, rgba(255, 255, 255, 0.9), rgba(248, 250, 252, 0.74));
  box-shadow: 0 14px 34px color-mix(in srgb, var(--motion-health, #2563eb) 10%, transparent);
}
.ai-autopilot::before {
  content: '';
  position: absolute;
  left: 12px;
  right: 12px;
  top: 8px;
  height: 3px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.16);
}
.ai-autopilot::after {
  content: '';
  position: absolute;
  left: 12px;
  top: 8px;
  width: var(--autopilot-progress);
  max-width: calc(100% - 24px);
  height: 3px;
  border-radius: 999px;
  background: linear-gradient(90deg, #2563eb, #14b8a6, #f59e0b);
  background-size: 180% 100%;
  box-shadow: 0 0 18px color-mix(in srgb, var(--motion-health, #2563eb) 22%, transparent);
  animation: railFlow var(--motion-loop-river, 7s) linear infinite;
}
.ai-autopilot > * {
  position: relative;
  z-index: 1;
}
.ai-autopilot-head {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 4px 9px;
  align-items: center;
  padding-top: 4px;
}
.ai-autopilot-head > span {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 14px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
}
.ai-autopilot-head strong,
.ai-autopilot-head em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-autopilot-head strong {
  color: var(--ai-text);
  font-size: 13px;
}
.ai-autopilot-head em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-autopilot-primary {
  grid-row: span 2;
  padding: 7px 9px;
  border: 1px solid color-mix(in srgb, var(--motion-health, #2563eb) 22%, transparent);
  border-radius: 999px;
  background: color-mix(in srgb, var(--motion-health, #2563eb) 10%, white);
  color: color-mix(in srgb, var(--motion-health, #2563eb) 82%, #0f172a);
  cursor: pointer;
  font-size: 11px;
  font-weight: 900;
  white-space: nowrap;
}
.ai-autopilot-primary:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.ai-autopilot-rail {
  position: relative;
  display: grid;
  gap: 7px;
  padding-left: 14px;
}
.ai-autopilot-energy {
  position: absolute;
  left: 5px;
  top: 8px;
  bottom: 8px;
  width: 3px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.16);
}
.ai-autopilot-energy::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  top: -36%;
  height: 44%;
  border-radius: inherit;
  background: linear-gradient(180deg, transparent, color-mix(in srgb, var(--motion-health, #2563eb) 72%, #14b8a6), transparent);
  animation: routeEnergyFlow var(--motion-loop-flow, 7s) linear infinite;
}
.ai-autopilot-stage {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 3px 8px;
  align-items: center;
  width: 100%;
  padding: 8px 8px 10px;
  overflow: visible;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.72);
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  box-shadow: 0 8px 18px rgba(15, 23, 42, 0.035);
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
  animation: routeStepBreath 4.8s ease-in-out infinite;
  animation-delay: var(--autopilot-stage-delay);
}
.ai-autopilot-stage::before {
  content: '';
  position: absolute;
  left: -13px;
  top: 50%;
  width: 8px;
  height: 8px;
  border: 2px solid #fff;
  border-radius: 999px;
  background: var(--ai-primary);
  box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.1);
  transform: translateY(-50%);
}
.ai-autopilot-stage::after {
  content: '';
  position: absolute;
  left: 44px;
  bottom: 6px;
  width: var(--autopilot-stage-progress);
  max-width: calc(100% - 58px);
  height: 2px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--motion-health, #2563eb) 34%, transparent);
}
.ai-autopilot-stage:hover {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 32%, transparent);
  box-shadow: 0 12px 26px color-mix(in srgb, var(--motion-health, #2563eb) 11%, transparent);
  transform: translateY(-1px);
}
.ai-autopilot-stage span {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 12px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
}
.ai-autopilot-stage strong,
.ai-autopilot-stage em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-autopilot-stage strong {
  font-size: 12px;
}
.ai-autopilot-stage em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-autopilot-stage b {
  grid-row: span 2;
  padding: 3px 7px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 11px;
  white-space: nowrap;
}
.ai-autopilot.tone-good .ai-autopilot-head > span,
.ai-autopilot-stage.tone-good span,
.ai-autopilot-stage.tone-good b { color: #059669; background: rgba(16, 185, 129, 0.1); }
.ai-autopilot-stage.tone-good::before { background: #10b981; box-shadow: 0 0 0 4px rgba(16, 185, 129, 0.1); }
.ai-autopilot.tone-warn .ai-autopilot-head > span,
.ai-autopilot-stage.tone-warn span,
.ai-autopilot-stage.tone-warn b { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.ai-autopilot-stage.tone-warn::before { background: #f59e0b; box-shadow: 0 0 0 4px rgba(245, 158, 11, 0.12); }
.ai-autopilot.tone-bad .ai-autopilot-head > span,
.ai-autopilot-stage.tone-bad span,
.ai-autopilot-stage.tone-bad b { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.ai-autopilot-stage.tone-bad::before { background: #ef4444; box-shadow: 0 0 0 4px rgba(239, 68, 68, 0.12); }
.ai-autopilot.tone-info .ai-autopilot-head > span,
.ai-autopilot-stage.tone-info span,
.ai-autopilot-stage.tone-info b { color: #0284c7; background: rgba(14, 165, 233, 0.1); }
.ai-autopilot-stage.tone-info::before { background: #0ea5e9; box-shadow: 0 0 0 4px rgba(14, 165, 233, 0.1); }
.ai-evidence-stream {
  display: grid;
  gap: 7px;
}
.ai-evidence-card {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 6px 8px;
  align-items: center;
  width: 100%;
  padding: 9px 10px 11px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 17px;
  background:
    radial-gradient(circle at 8% 20%, color-mix(in srgb, var(--motion-health, #2563eb) 9%, transparent), transparent 38%),
    rgba(255, 255, 255, 0.78);
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
  animation: routeStepBreath 5s ease-in-out infinite;
  animation-delay: var(--evidence-delay);
}
.ai-evidence-card::after {
  content: '';
  position: absolute;
  left: 42px;
  right: 10px;
  bottom: 6px;
  height: 2px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.16);
}
.ai-evidence-card::before {
  content: '';
  position: absolute;
  left: 42px;
  bottom: 6px;
  width: var(--evidence-strength);
  max-width: calc(100% - 52px);
  height: 2px;
  z-index: 1;
  border-radius: 999px;
  background: linear-gradient(90deg, color-mix(in srgb, var(--motion-health, #2563eb) 72%, #2563eb), #14b8a6);
  background-size: 160% 100%;
  animation: railFlow var(--motion-loop-river, 7s) linear infinite;
}
.ai-evidence-card:hover {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 32%, transparent);
  box-shadow: 0 12px 26px color-mix(in srgb, var(--motion-health, #2563eb) 11%, transparent);
  transform: translateY(-1px);
}
.ai-evidence-card i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 12px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-style: normal;
}
.ai-evidence-card span {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.ai-evidence-card strong,
.ai-evidence-card em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-evidence-card strong {
  font-size: 12px;
}
.ai-evidence-card em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-evidence-card b {
  padding: 3px 7px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 11px;
  white-space: nowrap;
}
.ai-evidence-card.tone-good i,
.ai-evidence-card.tone-good b { color: #059669; background: rgba(16, 185, 129, 0.1); }
.ai-evidence-card.tone-warn i,
.ai-evidence-card.tone-warn b { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.ai-evidence-card.tone-bad i,
.ai-evidence-card.tone-bad b { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.ai-evidence-card.tone-info i,
.ai-evidence-card.tone-info b { color: #0284c7; background: rgba(14, 165, 233, 0.1); }
.ai-rootcause-panel {
  display: grid;
  gap: 8px;
}
.ai-rootcause-card {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 3px 9px;
  align-items: center;
  width: 100%;
  min-height: 64px;
  padding: 10px 10px 12px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 20px;
  background:
    radial-gradient(circle at 10% 20%, color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent), transparent 40%),
    linear-gradient(135deg, rgba(255, 255, 255, 0.9), rgba(248, 250, 252, 0.72));
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
  animation: rootCauseGlow 4.8s ease-in-out infinite;
  animation-delay: var(--rootcause-delay);
}
.ai-rootcause-card::before {
  content: '';
  position: absolute;
  left: 46px;
  right: 12px;
  bottom: 7px;
  height: 3px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.16);
}
.ai-rootcause-card::after {
  content: '';
  position: absolute;
  left: 46px;
  bottom: 7px;
  width: var(--rootcause-score);
  max-width: calc(100% - 58px);
  height: 3px;
  border-radius: 999px;
  background: linear-gradient(90deg, #ef4444, #f59e0b, #14b8a6);
  background-size: 180% 100%;
  animation: railFlow var(--motion-loop-river, 7s) linear infinite;
}
.ai-rootcause-card:hover {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 34%, transparent);
  box-shadow: 0 12px 28px color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent);
  transform: translateY(-1px);
}
.ai-rootcause-card i {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 31px;
  height: 31px;
  border-radius: 13px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-style: normal;
}
.ai-rootcause-card span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-rootcause-card strong,
.ai-rootcause-card em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-rootcause-card strong {
  font-size: 12px;
}
.ai-rootcause-card em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-rootcause-card b {
  grid-row: span 2;
  color: var(--ai-text);
  font-size: 13px;
}
.ai-rootcause-card small {
  width: fit-content;
  padding: 3px 7px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 11px;
  font-weight: 900;
}
.ai-rootcause-card.tone-good i,
.ai-rootcause-card.tone-good small { color: #059669; background: rgba(16, 185, 129, 0.1); }
.ai-rootcause-card.tone-warn i,
.ai-rootcause-card.tone-warn small { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.ai-rootcause-card.tone-bad i,
.ai-rootcause-card.tone-bad small { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.ai-rootcause-card.tone-info i,
.ai-rootcause-card.tone-info small { color: #0284c7; background: rgba(14, 165, 233, 0.1); }
.ai-impact-grid {
  display: grid;
  gap: 8px;
}
.ai-impact-card {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 3px 9px;
  align-items: center;
  width: 100%;
  min-height: 60px;
  padding: 9px 10px 12px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 18px;
  background:
    radial-gradient(circle at 8% 16%, color-mix(in srgb, var(--motion-health, #2563eb) 10%, transparent), transparent 38%),
    rgba(255, 255, 255, 0.82);
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
  animation: decisionFloat 4.6s ease-in-out infinite;
  animation-delay: var(--impact-delay);
}
.ai-impact-card::before {
  content: '';
  position: absolute;
  left: 46px;
  right: 12px;
  bottom: 7px;
  height: 3px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.16);
}
.ai-impact-card::after {
  content: '';
  position: absolute;
  left: 46px;
  bottom: 7px;
  width: var(--impact-score);
  max-width: calc(100% - 58px);
  height: 3px;
  border-radius: 999px;
  background: linear-gradient(90deg, #2563eb, #14b8a6, #22c55e);
  background-size: 180% 100%;
  animation: railFlow var(--motion-loop-river, 7s) linear infinite;
}
.ai-impact-card:hover {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 34%, transparent);
  box-shadow: 0 12px 28px color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent);
  transform: translateY(-1px);
}
.ai-impact-card i {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 13px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-style: normal;
}
.ai-impact-card span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-impact-card strong,
.ai-impact-card em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-impact-card strong {
  font-size: 12px;
}
.ai-impact-card em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-impact-card b {
  grid-row: span 2;
  max-width: 92px;
  overflow: hidden;
  color: var(--ai-text);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-impact-card small {
  width: fit-content;
  padding: 3px 7px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 11px;
  font-weight: 900;
}
.ai-impact-card.tone-good i,
.ai-impact-card.tone-good small { color: #059669; background: rgba(16, 185, 129, 0.1); }
.ai-impact-card.tone-warn i,
.ai-impact-card.tone-warn small { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.ai-impact-card.tone-bad i,
.ai-impact-card.tone-bad small { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.ai-impact-card.tone-info i,
.ai-impact-card.tone-info small { color: #0284c7; background: rgba(14, 165, 233, 0.1); }
.ai-heat-stack {
  display: grid;
  gap: 7px;
}
.ai-heat-card {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 3px 8px;
  align-items: center;
  width: 100%;
  padding: 9px 10px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 18px;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--motion-health, #2563eb) 8%, transparent), rgba(255, 255, 255, 0.78)),
    rgba(255, 255, 255, 0.82);
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  box-shadow: 0 8px 18px rgba(15, 23, 42, 0.035);
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.ai-heat-card::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: var(--heat-score);
  max-width: 100%;
  opacity: 0.52;
  background: linear-gradient(90deg, color-mix(in srgb, var(--motion-health, #2563eb) 16%, transparent), transparent);
}
.ai-heat-card::after {
  content: '';
  position: absolute;
  left: -24%;
  top: 0;
  width: 30%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.34), transparent);
  animation: briefRailSweep var(--motion-loop-river, 7s) linear infinite;
  animation-delay: var(--heat-delay);
}
.ai-heat-card > * {
  position: relative;
  z-index: 1;
}
.ai-heat-card:hover {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 34%, transparent);
  box-shadow: 0 12px 28px color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent);
  transform: translateY(-1px);
}
.ai-heat-card span {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 13px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
}
.ai-heat-card strong,
.ai-heat-card em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-heat-card strong {
  font-size: 12px;
}
.ai-heat-card em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-heat-card b {
  grid-row: span 2;
  min-width: 34px;
  padding: 4px 7px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 11px;
  text-align: center;
  white-space: nowrap;
}
.ai-heat-card.tone-good span,
.ai-heat-card.tone-good b { color: #059669; background: rgba(16, 185, 129, 0.1); }
.ai-heat-card.tone-warn span,
.ai-heat-card.tone-warn b { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.ai-heat-card.tone-bad span,
.ai-heat-card.tone-bad b { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.ai-heat-card.tone-info span,
.ai-heat-card.tone-info b { color: #0284c7; background: rgba(14, 165, 233, 0.1); }
.ai-playbook-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
}
.ai-playbook {
  position: relative;
  display: grid;
  gap: 4px;
  min-height: 100px;
  padding: 10px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 20px;
  background:
    radial-gradient(circle at 12% 8%, color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent), transparent 38%),
    rgba(255, 255, 255, 0.78);
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.ai-playbook::before {
  content: '';
  position: absolute;
  left: 10px;
  right: 10px;
  bottom: 8px;
  height: 3px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.16);
}
.ai-playbook::after {
  content: '';
  position: absolute;
  left: 10px;
  bottom: 8px;
  width: var(--playbook-score);
  max-width: calc(100% - 20px);
  height: 3px;
  border-radius: 999px;
  background: linear-gradient(90deg, color-mix(in srgb, var(--motion-health, #2563eb) 82%, #2563eb), #14b8a6, #f59e0b);
  background-size: 180% 100%;
  animation: railFlow var(--motion-loop-river, 7s) linear infinite;
}
.ai-playbook:hover {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 34%, transparent);
  box-shadow: 0 12px 28px color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent);
  transform: translateY(-1px);
}
.ai-playbook i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 12px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-style: normal;
}
.ai-playbook strong,
.ai-playbook em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-playbook strong {
  font-size: 12px;
}
.ai-playbook em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-playbook b {
  width: fit-content;
  padding: 3px 7px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 11px;
  white-space: nowrap;
}
.ai-playbook.tone-good i,
.ai-playbook.tone-good b { color: #059669; background: rgba(16, 185, 129, 0.1); }
.ai-playbook.tone-warn i,
.ai-playbook.tone-warn b { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.ai-playbook.tone-bad i,
.ai-playbook.tone-bad b { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.ai-playbook.tone-info i,
.ai-playbook.tone-info b { color: #0284c7; background: rgba(14, 165, 233, 0.1); }
.ai-action-trail {
  display: grid;
  gap: 7px;
}
.ai-trail-item {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  width: 100%;
  padding: 8px 9px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 18px;
  background:
    linear-gradient(90deg, rgba(255, 255, 255, 0.9), color-mix(in srgb, var(--motion-health, #2563eb) 8%, #fff)),
    radial-gradient(circle at 10% 50%, color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent), transparent 42%);
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.ai-trail-item::after {
  content: '';
  position: absolute;
  inset: auto 10px 6px 44px;
  height: 2px;
  border-radius: 999px;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--motion-health, #2563eb) 70%, #14b8a6), transparent);
  background-size: 180% 100%;
  opacity: 0.72;
  animation: railFlow var(--motion-loop-flow, 8s) linear infinite;
}
.ai-trail-item:hover {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 34%, transparent);
  box-shadow: 0 12px 26px color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent);
  transform: translateY(-1px);
}
.ai-trail-item i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 12px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-style: normal;
}
.ai-trail-item span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-trail-item strong,
.ai-trail-item em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-trail-item strong {
  font-size: 12px;
}
.ai-trail-item em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-trail-item b,
.ai-trail-clear {
  border-radius: 999px;
  font-size: 11px;
  white-space: nowrap;
}
.ai-trail-item b {
  padding: 3px 7px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
}
.ai-trail-clear {
  width: fit-content;
  justify-self: end;
  padding: 4px 9px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  background: rgba(248, 250, 252, 0.84);
  color: var(--ai-muted);
  cursor: pointer;
}
.ai-trail-clear:hover {
  border-color: rgba(37, 99, 235, 0.24);
  color: var(--ai-primary);
}
.ai-trail-item.tone-good i,
.ai-trail-item.tone-good b { color: #059669; background: rgba(16, 185, 129, 0.1); }
.ai-trail-item.tone-warn i,
.ai-trail-item.tone-warn b { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.ai-trail-item.tone-bad i,
.ai-trail-item.tone-bad b { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.ai-trail-item.tone-info i,
.ai-trail-item.tone-info b { color: #0284c7; background: rgba(14, 165, 233, 0.1); }
.ai-side-action,
.ai-side-nav button,
.ai-side-metric {
  position: relative;
  width: 100%;
  border: 1px solid rgba(148, 163, 184, 0.2);
  background: rgba(255, 255, 255, 0.78);
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.ai-side-action:hover,
.ai-side-nav button:hover,
.ai-side-nav button.active,
.ai-side-metric:hover {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 32%, transparent);
  box-shadow: 0 12px 26px color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent);
  transform: translateY(-1px);
}
.ai-side-action {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 4px 9px;
  align-items: center;
  padding: 10px 11px;
  overflow: hidden;
  border-radius: 18px;
}
.ai-side-action::after {
  content: '';
  position: absolute;
  left: 48px;
  right: 12px;
  bottom: 7px;
  height: 2px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(37, 99, 235, 0.08), color-mix(in srgb, var(--motion-health, #2563eb) 38%, transparent), rgba(20, 184, 166, 0.12));
  transform-origin: left center;
  animation: sideActionScan var(--motion-loop-river, 7s) linear infinite;
}
.ai-side-action > span {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 14px;
  color: var(--ai-primary);
  background: rgba(37, 99, 235, 0.08);
}
.ai-side-action.tone-good > span { color: #059669; background: rgba(16, 185, 129, 0.1); }
.ai-side-action.tone-warn > span { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.ai-side-action.tone-bad > span { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.ai-side-action strong,
.ai-side-action em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-side-action strong {
  font-size: 13px;
}
.ai-side-action em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-side-action b {
  grid-row: span 2;
  width: fit-content;
  padding: 4px 7px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 11px;
  white-space: nowrap;
}
.ai-sticky-command {
  --sticky-command-tone: #2563eb;
  position: sticky;
  bottom: -14px;
  z-index: 7;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  margin: 2px -6px -14px;
  padding: 11px 12px 14px;
  overflow: hidden;
  border: 1px solid var(--sf-tone-border, color-mix(in srgb, var(--sticky-command-tone) 28%, transparent));
  border-radius: var(--learning-radius) var(--learning-radius) 0 0;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--sf-tone-bg, transparent) 36%, var(--learning-surface)), var(--learning-surface));
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  box-shadow: 0 -8px 20px rgba(20, 19, 15, 0.06);
  backdrop-filter: blur(12px);
  transition: border-color var(--learning-transition), box-shadow var(--learning-transition), background var(--learning-transition);
}
.ai-sticky-command::after {
  content: '';
  position: absolute;
  left: -24%;
  top: 0;
  width: 46%;
  height: 100%;
  background: linear-gradient(90deg, transparent, color-mix(in srgb, var(--sticky-command-tone) 11%, transparent), transparent);
  animation: briefRailSweep var(--motion-loop-river, 7s) linear infinite;
  pointer-events: none;
}
.ai-sticky-command:hover {
  border-color: var(--sf-tone-border, color-mix(in srgb, var(--sticky-command-tone) 42%, transparent));
  box-shadow: 0 -10px 24px rgba(20, 19, 15, 0.08);
}
.ai-sticky-command:disabled {
  cursor: not-allowed;
  opacity: 0.64;
}
.ai-sticky-command > * {
  position: relative;
  z-index: 1;
}
.ai-sticky-command i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 15px;
  background:
    radial-gradient(circle, rgba(255, 255, 255, 0.96) 0 52%, transparent 53%),
    conic-gradient(color-mix(in srgb, var(--sticky-command-tone) 88%, #2563eb) 76%, rgba(148, 163, 184, 0.16) 0);
  color: var(--sticky-command-tone);
  font-style: normal;
  box-shadow: 0 10px 24px color-mix(in srgb, var(--sticky-command-tone) 14%, transparent);
}
.ai-sticky-command span {
  display: grid;
  gap: 1px;
  min-width: 0;
}
.ai-sticky-command small,
.ai-sticky-command strong,
.ai-sticky-command em,
.ai-sticky-command b {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-sticky-command small {
  color: color-mix(in srgb, var(--sticky-command-tone) 82%, #0f172a);
  font-size: 10px;
  font-weight: 950;
  letter-spacing: 0.03em;
}
.ai-sticky-command strong {
  color: var(--ai-text);
  font-size: 13px;
  font-weight: 950;
}
.ai-sticky-command em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-sticky-command b {
  max-width: 72px;
  padding: 5px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--sticky-command-tone) 10%, transparent);
  color: var(--sticky-command-tone);
  font-size: 11px;
  font-weight: 950;
}
.ai-sticky-command .ai-sticky-command-confidence {
  --confidence-tone: #2563eb;
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 1px 8px;
  align-items: center;
  min-width: 0;
  padding: 7px 8px;
  border: 1px solid color-mix(in srgb, var(--confidence-tone) 16%, transparent);
  border-radius: 16px;
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--confidence-tone) 9%, transparent), rgba(255, 255, 255, 0.62));
}
.ai-sticky-command .ai-sticky-command-confidence::before {
  content: '';
  grid-row: span 2;
  width: 30px;
  height: 30px;
  border-radius: 999px;
  background:
    radial-gradient(circle, rgba(255, 255, 255, 0.96) 0 52%, transparent 53%),
    conic-gradient(var(--confidence-tone) var(--sticky-confidence), rgba(148, 163, 184, 0.18) 0);
  box-shadow: 0 8px 18px color-mix(in srgb, var(--confidence-tone) 14%, transparent);
}
.ai-sticky-command .ai-sticky-command-confidence small,
.ai-sticky-command .ai-sticky-command-confidence strong,
.ai-sticky-command .ai-sticky-command-confidence em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-sticky-command .ai-sticky-command-confidence small {
  color: color-mix(in srgb, var(--confidence-tone) 80%, #0f172a);
  font-size: 10px;
  font-weight: 950;
}
.ai-sticky-command .ai-sticky-command-confidence strong {
  color: var(--confidence-tone);
  font-size: 16px;
  line-height: 1;
}
.ai-sticky-command .ai-sticky-command-confidence em {
  color: var(--ai-muted);
  font-size: 10px;
  font-style: normal;
}
.ai-sticky-command .ai-sticky-command-confidence.tone-good { --confidence-tone: #059669; }
.ai-sticky-command .ai-sticky-command-confidence.tone-warn { --confidence-tone: #d97706; }
.ai-sticky-command .ai-sticky-command-confidence.tone-bad { --confidence-tone: #dc2626; }
.ai-sticky-command .ai-sticky-command-confidence.tone-info { --confidence-tone: #0284c7; }
.ai-sticky-command .ai-sticky-command-preview,
.ai-sticky-command .ai-sticky-command-reason {
  grid-column: 1 / -1;
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
  min-width: 0;
  padding-top: 3px;
}
.ai-sticky-command .ai-sticky-command-preview > small,
.ai-sticky-command .ai-sticky-command-reason > small {
  flex: 0 0 100%;
  color: var(--ai-muted);
  font-size: 10px;
  font-weight: 950;
}
.ai-sticky-command .ai-sticky-command-reason {
  padding-top: 0;
}
.ai-sticky-command .ai-sticky-command-preview > em,
.ai-sticky-command .ai-sticky-command-reason > em {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  max-width: 100%;
  padding: 3px 6px;
  border: 1px solid color-mix(in srgb, var(--preview-tone, #2563eb) 14%, transparent);
  border-radius: 999px;
  background: color-mix(in srgb, var(--preview-tone, #2563eb) 8%, transparent);
  color: var(--preview-tone, #2563eb);
  font-size: 10px;
  font-style: normal;
  font-weight: 900;
}
.ai-sticky-command .ai-sticky-command-reason > em {
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--preview-tone, #2563eb) 9%, transparent), rgba(255, 255, 255, 0.58));
}
.ai-sticky-command .ai-sticky-command-preview > em span,
.ai-sticky-command .ai-sticky-command-preview > em strong,
.ai-sticky-command .ai-sticky-command-reason > em span,
.ai-sticky-command .ai-sticky-command-reason > em strong {
  display: inline;
  min-width: 0;
  overflow: hidden;
  color: inherit;
  font-size: inherit;
  font-weight: inherit;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-sticky-command .ai-sticky-command-preview > em strong,
.ai-sticky-command .ai-sticky-command-reason > em strong {
  max-width: 92px;
  color: var(--ai-text);
}
.ai-sticky-command .ai-sticky-command-preview > em.tone-good,
.ai-sticky-command .ai-sticky-command-reason > em.tone-good { --preview-tone: #059669; }
.ai-sticky-command .ai-sticky-command-preview > em.tone-warn,
.ai-sticky-command .ai-sticky-command-reason > em.tone-warn { --preview-tone: #d97706; }
.ai-sticky-command .ai-sticky-command-preview > em.tone-bad,
.ai-sticky-command .ai-sticky-command-reason > em.tone-bad { --preview-tone: #dc2626; }
.ai-sticky-command .ai-sticky-command-preview > em.tone-info,
.ai-sticky-command .ai-sticky-command-reason > em.tone-info { --preview-tone: #0284c7; }
.ai-sticky-command.tone-good { --sticky-command-tone: #059669; }
.ai-sticky-command.tone-warn { --sticky-command-tone: #d97706; }
.ai-sticky-command.tone-bad { --sticky-command-tone: #dc2626; }
.ai-sticky-command.tone-info { --sticky-command-tone: #0284c7; }
.motion-auto .ai-side-orbit,
.motion-calm .ai-side-orbit {
  display: none;
}
.motion-auto .pulse-decision-strip::after,
.motion-auto .ai-mission-brief::after,
.motion-auto .ai-motion-mode button.active::after,
.motion-auto .ai-perspective.active::after,
.motion-auto .ai-side-context-card::after,
.motion-auto .ai-heartbeat-core::after,
.motion-auto .ai-heartbeat-ring::after,
.motion-auto .ai-heartbeat-step::after,
.motion-auto .ai-decision-item::after,
.motion-auto .ai-command-plan::after,
.motion-auto .ai-command-plan-track::after,
.motion-auto .ai-guardrail-scan,
.motion-auto .ai-autopilot::after,
.motion-auto .ai-autopilot-energy::after,
.motion-auto .ai-evidence-card::before,
.motion-auto .ai-rootcause-card::after,
.motion-auto .ai-impact-card::after,
.motion-auto .ai-heat-card::after,
.motion-auto .ai-playbook::after,
.motion-auto .ai-trail-item::after,
.motion-auto .ai-sticky-command::after,
.motion-auto .ai-side-action::after,
.motion-auto .ai-focus-radar::after,
.motion-auto .ai-focus-shortcut.active::after,
.motion-auto .ai-motion-meter i,
.motion-auto .ai-side-route-energy {
  animation: none !important;
}
.motion-auto .ai-mission-step,
.motion-auto .ai-heartbeat-step,
.motion-auto .ai-decision-item,
.motion-auto .ai-guardrail-card,
.motion-auto .ai-autopilot-stage,
.motion-auto .ai-evidence-card,
.motion-auto .ai-rootcause-card,
.motion-auto .ai-impact-card,
.motion-auto .ai-playbook,
.motion-auto .ai-side-route-step,
.motion-auto .journey-step-node,
.motion-auto .flow-lens-node,
.motion-auto .run-brief-node {
  animation: none !important;
}
.ai-motion-mode {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--learning-gap-sm);
}
.ai-motion-mode button {
  position: relative;
  display: grid;
  gap: var(--sf-spacing-xs, 4px);
  min-height: 70px;
  padding: var(--sf-spacing-sm, 8px);
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: var(--learning-radius);
  background: var(--learning-surface);
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
  transition: transform var(--learning-transition), border-color var(--learning-transition), box-shadow var(--learning-transition);
}
.ai-motion-mode button::after {
  content: '';
  position: absolute;
  inset: auto 8px 7px 8px;
  height: 2px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.16);
}
.ai-motion-mode button.active {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 36%, transparent);
  box-shadow: 0 12px 26px color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent);
}
.ai-motion-mode button.active::after {
  background: linear-gradient(90deg, #2563eb, #14b8a6, #f59e0b);
  background-size: 180% 100%;
  animation: railFlow var(--motion-loop-river, 7s) linear infinite;
}
.ai-motion-mode button:hover {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 32%, transparent);
  transform: translateY(-1px);
}
.ai-motion-mode i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: var(--learning-radius-sm);
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-style: normal;
}
.ai-motion-mode strong,
.ai-motion-mode em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-motion-mode strong {
  font-size: 12px;
}
.ai-motion-mode em {
  color: var(--ai-muted);
  font-size: 10px;
  font-style: normal;
}
.ai-motion-mode .tone-good i { color: #059669; background: rgba(16, 185, 129, 0.1); }
.ai-motion-mode .tone-warn i { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.motion-calm .global-edge-energy,
.motion-calm .flow-loop-energy,
.motion-calm .stream-energy,
.motion-calm .material-track-hot,
.motion-calm .orbit-signal,
.motion-calm .ai-motion-meter i {
  opacity: 0.58;
  filter: none;
}
.motion-calm .global-flow-core::before,
.motion-calm .global-flow-core::after,
.motion-calm .orbit-core::after,
.motion-calm .bottleneck-core::after {
  animation-duration: 4.8s;
}
.motion-calm .pulse-decision-strip::after,
.motion-calm .ai-mission-brief::after,
.motion-calm .ai-motion-mode button.active::after,
.motion-calm .ai-autopilot::after,
.motion-calm .ai-autopilot-energy::after,
.motion-calm .ai-guardrail-scan,
.motion-calm .ai-evidence-card::before,
.motion-calm .ai-rootcause-card::after,
.motion-calm .ai-impact-card::after,
.motion-calm .ai-heat-card::after,
.motion-calm .ai-playbook::after,
.motion-calm .ai-trail-item::after,
.motion-calm .ai-focus-radar::after,
.motion-calm .ai-focus-shortcut.active::after,
.motion-calm .ai-motion-meter i,
.motion-calm .ai-side-route-energy,
.motion-calm .pulse-overview-step,
.motion-calm .ai-mission-step,
.motion-calm .ai-autopilot-stage,
.motion-calm .ai-guardrail-card,
.motion-calm .ai-evidence-card,
.motion-calm .ai-decision-item,
.motion-calm .ai-playbook,
.motion-calm .ai-root-cause,
.motion-calm .ai-rootcause-card,
.motion-calm .ai-impact-card,
.motion-calm .ai-side-route-step,
.motion-calm .flow-lens-node,
.motion-calm .run-brief-node,
.motion-calm .journey-step-node {
  animation: none !important;
}
.motion-calm .global-edge-dot {
  display: none;
}
.motion-trace .global-edge-energy.active,
.motion-trace .stream-energy,
.motion-trace .flow-loop-energy {
  filter: drop-shadow(0 0 18px color-mix(in srgb, var(--motion-health, #2563eb) 44%, transparent));
}
.motion-trace .ai-side-menu {
  box-shadow: 0 24px 68px color-mix(in srgb, var(--motion-health, #2563eb) 16%, transparent);
}
.ai-perspective-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--learning-gap-sm);
}
.ai-perspective {
  position: relative;
  display: grid;
  gap: var(--sf-spacing-xs, 4px);
  min-height: 72px;
  padding: var(--sf-spacing-sm, 8px);
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: var(--learning-radius);
  background: var(--learning-surface);
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
  box-shadow: none;
  transition: transform var(--learning-transition), border-color var(--learning-transition), box-shadow var(--learning-transition);
}
.ai-perspective::after {
  content: '';
  position: absolute;
  inset: auto 10px 8px 10px;
  height: 2px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.16);
}
.ai-perspective.active::after {
  background: linear-gradient(90deg, #2563eb, #14b8a6, #f59e0b);
  background-size: 180% 100%;
  animation: railFlow var(--motion-loop-river, 7s) linear infinite;
}
.ai-perspective:hover,
.ai-perspective.active {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 34%, transparent);
  box-shadow: 0 12px 26px color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent);
  transform: translateY(-1px);
}
.ai-perspective i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--learning-radius-sm);
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-style: normal;
}
.ai-perspective strong,
.ai-perspective em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-perspective strong {
  font-size: 12px;
}
.ai-perspective em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-perspective.tone-good i { color: #059669; background: rgba(16, 185, 129, 0.1); }
.ai-perspective.tone-warn i { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.ai-perspective.tone-bad i { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.ai-perspective.tone-artifact i { color: #7c3aed; background: rgba(124, 58, 237, 0.1); }
.ai-perspective.tone-source i { color: #0284c7; background: rgba(14, 165, 233, 0.1); }
.ai-perspective.tone-candidate i { color: #ea580c; background: rgba(249, 115, 22, 0.1); }
.ai-focus-control {
  position: relative;
}
.ai-focus-radar {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  width: 100%;
  min-height: 62px;
  padding: 10px;
  overflow: hidden;
  border: 1px solid rgba(37, 99, 235, 0.18);
  border-radius: 20px;
  background:
    radial-gradient(circle at 13% 50%, color-mix(in srgb, var(--motion-health, #2563eb) 14%, transparent), transparent 38%),
    linear-gradient(135deg, rgba(255, 255, 255, 0.94), rgba(239, 246, 255, 0.72));
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  box-shadow: 0 12px 30px rgba(37, 99, 235, 0.08);
}
.ai-focus-radar::after {
  content: '';
  position: absolute;
  inset: 9px;
  border: 1px solid color-mix(in srgb, var(--motion-health, #2563eb) 24%, transparent);
  border-radius: 16px;
  opacity: 0.7;
  animation: focusRadarPing 3.4s ease-in-out infinite;
}
.ai-focus-radar > * {
  position: relative;
  z-index: 1;
}
.ai-focus-radar i {
  width: 34px;
  height: 34px;
  border-radius: 999px;
  background:
    radial-gradient(circle, var(--motion-health, #2563eb) 0 4px, transparent 5px),
    conic-gradient(from 120deg, rgba(37, 99, 235, 0.1), color-mix(in srgb, var(--motion-health, #2563eb) 42%, transparent), rgba(20, 184, 166, 0.12), rgba(37, 99, 235, 0.1));
  box-shadow: 0 0 0 6px rgba(37, 99, 235, 0.06);
}
.ai-focus-radar span {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.ai-focus-radar strong,
.ai-focus-radar em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-focus-radar strong {
  font-size: 13px;
}
.ai-focus-radar em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-focus-radar b {
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 11px;
}
.ai-focus-radar.active b {
  background: color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent);
}
.ai-focus-shortcuts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px;
}
.ai-focus-shortcut {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 7px;
  align-items: center;
  min-height: 48px;
  padding: 8px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.78);
  color: var(--ai-text);
  cursor: pointer;
  text-align: left;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.ai-focus-shortcut::after {
  content: '';
  position: absolute;
  inset: auto 8px 6px 36px;
  height: 2px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.16);
}
.ai-focus-shortcut.active,
.ai-focus-shortcut:hover {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 32%, transparent);
  box-shadow: 0 10px 22px color-mix(in srgb, var(--motion-health, #2563eb) 10%, transparent);
  transform: translateY(-1px);
}
.ai-focus-shortcut.active::after {
  background: linear-gradient(90deg, #2563eb, #14b8a6, #f59e0b);
  background-size: 180% 100%;
  animation: railFlow var(--motion-loop-river, 7s) linear infinite;
}
.ai-focus-shortcut i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 11px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-style: normal;
}
.ai-focus-shortcut span {
  display: grid;
  min-width: 0;
}
.ai-focus-shortcut strong,
.ai-focus-shortcut em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-focus-shortcut strong {
  font-size: 12px;
}
.ai-focus-shortcut em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-focus-shortcut.tone-danger i { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.ai-focus-shortcut.tone-artifact i { color: #7c3aed; background: rgba(124, 58, 237, 0.1); }
.ai-focus-shortcut.tone-event i { color: #0284c7; background: rgba(14, 165, 233, 0.1); }
.ai-focus-chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.ai-focus-chips button,
.ai-focus-actions button {
  border: 1px solid rgba(14, 165, 233, 0.18);
  border-radius: 999px;
  background: rgba(14, 165, 233, 0.08);
  color: #0369a1;
  cursor: pointer;
  font-size: 11px;
  font-weight: 900;
}
.ai-focus-chips button {
  display: inline-flex;
  gap: 5px;
  align-items: center;
  max-width: 100%;
  padding: 5px 7px;
}
.ai-focus-chips strong {
  max-width: 126px;
  overflow: hidden;
  color: #0f172a;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-focus-chips i {
  color: #0284c7;
  font-style: normal;
}
.ai-focus-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.ai-focus-actions button {
  padding: 5px 9px;
}
.ai-focus-actions button:hover,
.ai-focus-chips button:hover {
  border-color: rgba(37, 99, 235, 0.28);
  background: rgba(37, 99, 235, 0.1);
}
.ai-motion-meter {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 10px;
  align-items: center;
  width: 100%;
  padding: 9px 10px 12px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.78);
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
  box-shadow: 0 8px 18px rgba(15, 23, 42, 0.035);
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.ai-motion-meter:hover {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 32%, transparent);
  box-shadow: 0 12px 26px color-mix(in srgb, var(--motion-health, #2563eb) 11%, transparent);
  transform: translateY(-1px);
}
.ai-motion-meter > * {
  position: relative;
  z-index: 1;
}
.ai-motion-meter span {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.ai-motion-meter strong,
.ai-motion-meter em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-motion-meter strong {
  font-size: 12px;
}
.ai-motion-meter em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-motion-meter b {
  color: var(--ai-primary);
  font-size: 13px;
}
.ai-motion-meter i {
  grid-column: 1 / -1;
  position: relative;
  display: block;
  height: 4px;
  border-radius: 999px;
  background: linear-gradient(90deg, #2563eb, #14b8a6);
  background-size: 180% 100%;
  animation: railFlow var(--motion-loop-river, 7s) linear infinite;
}
.ai-motion-meter::after {
  content: '';
  position: absolute;
  left: 10px;
  right: 10px;
  bottom: 12px;
  z-index: 0;
  height: 4px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.14);
}
.ai-motion-meter.tone-good b { color: #059669; }
.ai-motion-meter.tone-good i { background: linear-gradient(90deg, #10b981, #22c55e); }
.ai-motion-meter.tone-warn b { color: #d97706; }
.ai-motion-meter.tone-warn i { background: linear-gradient(90deg, #f59e0b, #f97316); }
.ai-motion-meter.tone-bad b { color: #dc2626; }
.ai-motion-meter.tone-bad i { background: linear-gradient(90deg, #ef4444, #f97316); }
.ai-side-nav {
  display: grid;
  gap: 7px;
}
.ai-side-nav button {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 2px 9px;
  align-items: center;
  padding: 8px 10px;
  border-radius: 999px;
}
.ai-side-nav i {
  grid-row: span 2;
  display: inline-flex;
  color: var(--ai-primary);
  font-style: normal;
}
.ai-side-nav span,
.ai-side-nav em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-side-nav span {
  font-size: 12px;
  font-weight: 900;
}
.ai-side-nav em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-side-nav button.active {
  background:
    linear-gradient(90deg, color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent), rgba(255, 255, 255, 0.8));
}
.ai-side-route {
  position: relative;
  display: grid;
  gap: 8px;
  padding-left: 16px;
}
.ai-side-route-track,
.ai-side-route-energy {
  position: absolute;
  left: 7px;
  top: 12px;
  bottom: 12px;
  width: 4px;
  border-radius: 999px;
}
.ai-side-route-track {
  background: rgba(148, 163, 184, 0.16);
}
.ai-side-route-energy {
  bottom: auto;
  height: var(--route-progress);
  background: linear-gradient(180deg, #2563eb, #14b8a6, #f59e0b);
  background-size: 100% 220%;
  box-shadow: 0 0 18px rgba(37, 99, 235, 0.16);
  animation: routeEnergyFlow var(--motion-loop-flow, 7s) linear infinite;
}
.ai-side-route-step {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 3px 8px;
  align-items: center;
  width: 100%;
  padding: 8px 9px;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.78);
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
  box-shadow: 0 8px 18px rgba(15, 23, 42, 0.035);
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
  animation: routeStepBreath 4.6s ease-in-out infinite;
  animation-delay: var(--route-step-delay);
}
.ai-side-route-step::before {
  content: '';
  position: absolute;
  left: -13px;
  top: 50%;
  width: 8px;
  height: 8px;
  border: 2px solid #fff;
  border-radius: 999px;
  background: var(--ai-primary);
  box-shadow: 0 0 0 4px rgba(37, 99, 235, 0.1);
  transform: translateY(-50%);
}
.ai-side-route-step:hover,
.ai-side-route-step.active {
  border-color: color-mix(in srgb, var(--motion-health, #2563eb) 32%, transparent);
  box-shadow: 0 12px 26px color-mix(in srgb, var(--motion-health, #2563eb) 11%, transparent);
  transform: translateY(-1px);
}
.ai-side-route-step i {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 12px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-style: normal;
}
.ai-side-route-step strong,
.ai-side-route-step em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-side-route-step strong {
  font-size: 12px;
}
.ai-side-route-step em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.ai-side-route-step b {
  grid-row: span 2;
  padding: 3px 7px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 11px;
  white-space: nowrap;
}
.ai-side-route-step.tone-good::before { background: #10b981; box-shadow: 0 0 0 4px rgba(16, 185, 129, 0.1); }
.ai-side-route-step.tone-good i,
.ai-side-route-step.tone-good b { color: #059669; background: rgba(16, 185, 129, 0.1); }
.ai-side-route-step.tone-warn::before { background: #f59e0b; box-shadow: 0 0 0 4px rgba(245, 158, 11, 0.12); }
.ai-side-route-step.tone-warn i,
.ai-side-route-step.tone-warn b { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.ai-side-route-step.tone-bad::before { background: #ef4444; box-shadow: 0 0 0 4px rgba(239, 68, 68, 0.12); }
.ai-side-route-step.tone-bad i,
.ai-side-route-step.tone-bad b { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.ai-side-metric {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 3px 8px;
  padding: 9px 11px;
  border-radius: 16px;
}
.ai-side-metric span,
.ai-side-metric em {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ai-side-metric strong {
  grid-row: span 2;
  color: var(--ai-text);
  font-size: 18px;
}
.ai-side-metric.tone-warn strong { color: #d97706; }
.ai-side-metric.tone-bad strong { color: #dc2626; }
.ai-side-metric.tone-good strong { color: #059669; }
.ai-autopilot,
.ai-focus-radar {
  border-radius: var(--learning-radius);
  background: var(--learning-surface);
  box-shadow: var(--learning-shadow);
}
.ai-autopilot {
  padding: var(--sf-spacing-md, 12px);
}
.ai-side-index,
.ai-command-plan,
.ai-guardrail-board,
.ai-side-context-card,
.ai-autopilot-stage,
.ai-heartbeat-core,
.ai-heartbeat-step,
.ai-decision-item,
.ai-command-step,
.ai-guardrail-card,
.ai-evidence-card,
.ai-rootcause-card,
.ai-impact-card,
.ai-heat-card,
.ai-playbook,
.ai-trail-item,
.ai-focus-shortcut,
.ai-motion-meter,
.ai-side-route-step,
.ai-side-metric {
  padding: var(--sf-spacing-sm, 8px);
  border-radius: var(--learning-radius);
  background: var(--learning-surface);
  box-shadow: none;
  transition: border-color var(--learning-transition), box-shadow var(--learning-transition), transform var(--learning-transition);
}
.ai-side-index,
.ai-command-plan,
.ai-guardrail-board,
.ai-side-context-card {
  padding: var(--sf-spacing-sm, 8px);
}
.ai-focus-radar {
  padding: var(--sf-spacing-sm, 8px);
}
.ai-focus-radar::after {
  border-radius: var(--learning-radius-sm);
}
.ai-motion-meter::after {
  bottom: var(--sf-spacing-sm, 8px);
}
.motion-alert .ai-side-menu {
  border-color: color-mix(in srgb, var(--motion-health, #f59e0b) 26%, transparent);
}
.motion-quiet .global-edge-energy,
.motion-quiet .flow-loop-energy,
.motion-quiet .stream-energy {
  opacity: 0.46;
}
.motion-hot .flow-live span,
.motion-hot .learning-kpi::before {
  animation-duration: 1.1s;
}
.pulse-command {
  position: relative;
  display: grid;
  grid-template-columns: minmax(260px, 0.72fr) minmax(520px, 1.25fr) minmax(240px, 0.6fr);
  gap: 18px;
  align-items: stretch;
  margin: 18px 0;
  padding: 20px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 28px;
  background:
    radial-gradient(circle at 52% 44%, rgba(37, 99, 235, 0.18), transparent 34%),
    radial-gradient(circle at 82% 20%, rgba(20, 184, 166, 0.12), transparent 28%),
    linear-gradient(135deg, rgba(15, 23, 42, 0.96), rgba(30, 41, 59, 0.93));
  box-shadow: 0 28px 68px rgba(15, 23, 42, 0.18);
}
.pulse-command::before {
  content: '';
  position: absolute;
  inset: -30%;
  background:
    linear-gradient(120deg, transparent 0 44%, rgba(255, 255, 255, 0.08) 46%, transparent 52%),
    repeating-linear-gradient(90deg, rgba(148, 163, 184, 0.05) 0 1px, transparent 1px 36px);
  opacity: 0.28;
  pointer-events: none;
}
.pulse-copy,
.pulse-side,
.pulse-radar {
  position: relative;
  z-index: 1;
}
.pulse-copy {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
}
.pulse-copy h2 {
  margin: 14px 0 10px;
  color: #f8fafc;
  font-size: clamp(26px, 3vw, 40px);
  line-height: 1.08;
  letter-spacing: -0.04em;
}
.pulse-copy p {
  margin: 0;
  color: rgba(226, 232, 240, 0.72);
  font-size: 14px;
  line-height: 1.72;
}
.pulse-rhythm {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 20px;
}
.pulse-rhythm span {
  display: grid;
  gap: 5px;
  min-width: 86px;
  padding: 10px 12px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 16px;
  background: rgba(15, 23, 42, 0.38);
  backdrop-filter: blur(12px);
}
.pulse-rhythm em,
.pulse-insight em {
  color: rgba(226, 232, 240, 0.62);
  font-size: 11px;
  font-style: normal;
}
.pulse-rhythm strong {
  color: #fff;
  font-size: 22px;
  line-height: 1;
}
.motion-key {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 10px 0 14px;
}
.pulse-copy .motion-key {
  margin-top: 18px;
}
.motion-key span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 999px;
  color: var(--ai-muted);
  background: rgba(255, 255, 255, 0.66);
  font-size: 12px;
  font-weight: 700;
}
.motion-key.dark span {
  border-color: rgba(255, 255, 255, 0.12);
  color: rgba(226, 232, 240, 0.76);
  background: rgba(15, 23, 42, 0.42);
}
.motion-key b {
  color: var(--ai-primary);
  font-weight: 900;
}
.motion-key.dark b {
  color: #93c5fd;
}
.key-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #38bdf8;
  box-shadow: 0 0 12px rgba(56, 189, 248, 0.48);
}
.key-dot.warn {
  background: #f59e0b;
  box-shadow: 0 0 12px rgba(245, 158, 11, 0.45);
}
.flow-lens-strip {
  position: relative;
  display: flex;
  gap: 9px;
  flex-wrap: wrap;
  align-items: center;
  margin: -2px 0 14px;
  padding: 11px;
  overflow: hidden;
  border: 1px solid rgba(37, 99, 235, 0.12);
  border-radius: 999px;
  background:
    linear-gradient(90deg, rgba(255, 255, 255, 0.88), rgba(239, 246, 255, 0.68)),
    radial-gradient(circle at 8% 50%, rgba(37, 99, 235, 0.12), transparent 24%);
}
.flow-lens-strip::before {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  top: 50%;
  height: 4px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(37, 99, 235, 0.08), rgba(20, 184, 166, 0.2), rgba(245, 158, 11, 0.1));
  transform: translateY(-50%);
}
.flow-lens-label,
.flow-lens-node,
.flow-lens-clear {
  position: relative;
  z-index: 1;
}
.flow-lens-label {
  padding: 7px 10px;
  border-radius: 999px;
  color: var(--ai-primary);
  background: rgba(37, 99, 235, 0.08);
  font-size: 12px;
  font-weight: 900;
}
.flow-lens-node {
  display: inline-grid;
  grid-template-columns: auto auto;
  gap: 2px 7px;
  align-items: center;
  min-width: 138px;
  padding: 8px 10px;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.86);
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
  box-shadow: 0 10px 26px rgba(15, 23, 42, 0.05);
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.flow-lens-node::after {
  content: '';
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: 5px;
  height: 2px;
  border-radius: 999px;
  background: linear-gradient(90deg, transparent, rgba(37, 99, 235, 0.42), transparent);
  opacity: 0.58;
  animation: lensPulse 3.8s ease-in-out infinite;
  animation-delay: var(--lens-delay);
}
.flow-lens-node:hover,
.flow-lens-node.active {
  border-color: rgba(37, 99, 235, 0.32);
  box-shadow: 0 16px 34px rgba(37, 99, 235, 0.1);
  transform: translateY(-1px);
}
.flow-lens-node.active {
  background: rgba(239, 246, 255, 0.94);
}
.flow-lens-node > i {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 999px;
  color: var(--ai-primary);
  background: rgba(37, 99, 235, 0.08);
  font-style: normal;
}
.flow-lens-node.tone-danger > i { color: #dc2626; background: rgba(239, 68, 68, 0.1); }
.flow-lens-node.tone-artifact > i { color: #7c3aed; background: rgba(124, 58, 237, 0.1); }
.flow-lens-node.tone-event > i { color: #0284c7; background: rgba(14, 165, 233, 0.1); }
.flow-lens-node strong,
.flow-lens-node em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.flow-lens-node strong {
  font-size: 12px;
}
.flow-lens-node em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.flow-lens-node b {
  position: absolute;
  right: 8px;
  top: -7px;
  padding: 2px 6px;
  border-radius: 999px;
  color: #fff;
  background: var(--ai-primary);
  font-size: 10px;
}
.flow-lens-clear {
  padding: 8px 11px;
  border: 1px solid rgba(148, 163, 184, 0.24);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.8);
  color: var(--ai-muted);
  cursor: pointer;
  font-size: 12px;
  font-weight: 800;
}
.flow-lens-active {
  display: flex;
  gap: 7px;
  flex-wrap: wrap;
  margin: -7px 0 13px;
}
.flow-lens-active span {
  padding: 4px 9px;
  border-radius: 999px;
  color: #0369a1;
  background: rgba(14, 165, 233, 0.1);
  font-size: 11px;
  font-weight: 800;
}
.pulse-radar {
  min-height: 390px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 30px;
  background:
    radial-gradient(circle at center, rgba(59, 130, 246, 0.16), transparent 44%),
    rgba(15, 23, 42, 0.34);
  overflow: hidden;
}
.pulse-links {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.pulse-glow {
  fill: url(#pulse-core-glow);
}
.pulse-loop-track {
  fill: none;
  stroke: rgba(148, 163, 184, 0.18);
  stroke-width: 22;
  stroke-linecap: round;
}
.pulse-loop-energy {
  fill: none;
  stroke: url(#pulse-loop-gradient);
  stroke-width: 5;
  stroke-linecap: round;
  stroke-dasharray: 118 430;
  filter: drop-shadow(0 0 12px rgba(96, 165, 250, 0.26));
  animation: loopDash 6.8s linear infinite;
}
.pulse-loop-packet {
  fill: #e0f2fe;
  stroke: rgba(14, 165, 233, 0.5);
  stroke-width: 2;
  filter: drop-shadow(0 0 10px rgba(14, 165, 233, 0.58));
}
.pulse-loop-count {
  fill: #e0f2fe;
  stroke: rgba(15, 23, 42, 0.55);
  stroke-width: 3px;
  paint-order: stroke;
  font-size: 15px;
  font-weight: 900;
  letter-spacing: -0.03em;
  filter: drop-shadow(0 0 10px rgba(14, 165, 233, 0.62));
}
.pulse-loop-count.tone-danger {
  fill: #fecaca;
}
.pulse-loop-count.tone-sink {
  fill: #bbf7d0;
}
.pulse-loop-count.tone-artifact {
  fill: #ddd6fe;
}
.pulse-loop-count.tone-candidate {
  fill: #fed7aa;
}
.pulse-halo {
  fill: none;
  stroke: rgba(148, 163, 184, 0.2);
  stroke-width: 1;
  stroke-dasharray: 5 8;
  transform-origin: 320px 180px;
}
.halo-outer,
.halo-inner {
  animation: none;
}
.pulse-link {
  fill: none;
  stroke: rgba(96, 165, 250, 0.22);
  stroke-width: 2;
  stroke-linecap: round;
  stroke-dasharray: 3 10;
  transition: stroke-width 0.2s ease, opacity 0.2s ease;
}
.pulse-link.tone-sink { stroke: rgba(52, 211, 153, 0.3); }
.pulse-link.tone-artifact { stroke: rgba(168, 85, 247, 0.3); }
.pulse-link.tone-candidate { stroke: rgba(251, 146, 60, 0.32); }
.pulse-link.tone-review { stroke: rgba(129, 140, 248, 0.32); }
.pulse-link.tone-danger { stroke: rgba(248, 113, 113, 0.38); }
.pulse-link.active {
  stroke-width: 4;
  opacity: 1;
}
.pulse-link-hot {
  fill: none;
  stroke: transparent;
  stroke-width: 22;
  cursor: pointer;
  pointer-events: stroke;
}
.pulse-packet {
  fill: #60a5fa;
  opacity: 0.46;
  filter: drop-shadow(0 0 8px rgba(96, 165, 250, 0.7));
}
.pulse-packet.tone-sink { fill: #34d399; filter: drop-shadow(0 0 8px rgba(52, 211, 153, 0.74)); }
.pulse-packet.tone-artifact { fill: #a855f7; filter: drop-shadow(0 0 8px rgba(168, 85, 247, 0.72)); }
.pulse-packet.tone-candidate { fill: #fb923c; filter: drop-shadow(0 0 8px rgba(251, 146, 60, 0.72)); }
.pulse-packet.tone-review { fill: #818cf8; filter: drop-shadow(0 0 8px rgba(129, 140, 248, 0.72)); }
.pulse-packet.tone-danger { fill: #f87171; filter: drop-shadow(0 0 9px rgba(248, 113, 113, 0.8)); }
.pulse-core {
  position: absolute;
  left: 50%;
  top: 50%;
  display: grid;
  place-items: center;
  width: 164px;
  height: 164px;
  padding: 20px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 999px;
  background:
    radial-gradient(circle at 50% 38%, rgba(255, 255, 255, 0.2), transparent 30%),
    linear-gradient(180deg, rgba(37, 99, 235, 0.62), rgba(20, 184, 166, 0.5));
  color: #fff;
  text-align: center;
  transform: translate(-50%, -50%);
  box-shadow: 0 22px 58px rgba(37, 99, 235, 0.25), inset 0 0 28px rgba(255, 255, 255, 0.12);
  cursor: pointer;
}
.core-wave {
  position: absolute;
  inset: -12px;
  border: 1px solid rgba(125, 211, 252, 0.34);
  border-radius: inherit;
  animation: coreWave 2.2s ease-out infinite;
}
.core-title {
  color: rgba(255, 255, 255, 0.72);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.pulse-core strong {
  margin-top: 4px;
  font-size: 28px;
  letter-spacing: -0.04em;
}
.pulse-core em {
  max-width: 126px;
  color: rgba(255, 255, 255, 0.76);
  font-size: 12px;
  font-style: normal;
}
.pulse-core i {
  margin-top: 4px;
  padding: 2px 8px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 999px;
  color: rgba(255, 255, 255, 0.68);
  font-size: 10px;
  font-style: normal;
  letter-spacing: 0.04em;
}
.pulse-radar.health-warn .pulse-core {
  background: linear-gradient(180deg, rgba(245, 158, 11, 0.72), rgba(37, 99, 235, 0.48));
}
.pulse-radar.health-bad .pulse-core {
  background: linear-gradient(180deg, rgba(239, 68, 68, 0.74), rgba(124, 58, 237, 0.42));
}
.pulse-node {
  position: absolute;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 9px;
  align-items: center;
  width: min(210px, 32%);
  min-height: 66px;
  padding: 10px 12px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 22px;
  background: rgba(15, 23, 42, 0.72);
  color: #f8fafc;
  text-align: left;
  backdrop-filter: blur(16px);
  box-shadow: 0 16px 38px rgba(15, 23, 42, 0.18);
  transform: translate(-50%, -50%);
  cursor: pointer;
  transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
}
.pulse-node:hover,
.pulse-node.active {
  border-color: rgba(125, 211, 252, 0.58);
  background: rgba(30, 41, 59, 0.88);
  transform: translate(-50%, calc(-50% - 3px)) scale(1.02);
}
.pulse-node-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 14px;
  color: #93c5fd;
  background: rgba(59, 130, 246, 0.16);
}
.pulse-node.tone-sink .pulse-node-icon { color: #6ee7b7; background: rgba(16, 185, 129, 0.16); }
.pulse-node.tone-artifact .pulse-node-icon { color: #c084fc; background: rgba(168, 85, 247, 0.16); }
.pulse-node.tone-candidate .pulse-node-icon { color: #fdba74; background: rgba(249, 115, 22, 0.16); }
.pulse-node.tone-review .pulse-node-icon { color: #a5b4fc; background: rgba(99, 102, 241, 0.16); }
.pulse-node.tone-danger .pulse-node-icon { color: #fca5a5; background: rgba(239, 68, 68, 0.18); }
.pulse-node-body {
  display: grid;
  gap: 3px;
  min-width: 0;
}
.pulse-node-body strong,
.pulse-node-body em {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pulse-node-body strong {
  font-size: 13px;
}
.pulse-node-body em {
  color: rgba(226, 232, 240, 0.62);
  font-size: 11px;
  font-style: normal;
}
.pulse-node b {
  color: #fff;
  font-size: 20px;
  line-height: 1;
}
.pulse-side {
  display: grid;
  gap: 10px;
  align-content: center;
}
.pulse-insight {
  display: grid;
  gap: 6px;
  width: 100%;
  padding: 13px 14px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 20px;
  background: rgba(15, 23, 42, 0.42);
  color: #f8fafc;
  text-align: left;
  cursor: pointer;
  backdrop-filter: blur(14px);
  transition: transform 0.18s ease, background 0.18s ease, border-color 0.18s ease;
}
.pulse-insight:hover {
  border-color: rgba(125, 211, 252, 0.42);
  background: rgba(30, 41, 59, 0.72);
  transform: translateX(-2px);
}
.pulse-insight span {
  color: rgba(226, 232, 240, 0.72);
  font-size: 12px;
}
.pulse-insight strong {
  min-width: 0;
  overflow: hidden;
  font-size: 17px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pulse-insight.tone-good strong { color: #86efac; }
.pulse-insight.tone-warn strong { color: #fcd34d; }
.pulse-insight.tone-bad strong { color: #fca5a5; }
.pulse-insight.tone-info strong { color: #93c5fd; }
.run-brief {
  display: grid;
  gap: var(--learning-gap);
  margin-bottom: var(--learning-gap);
  padding: var(--sf-spacing-lg, 16px);
  border-color: rgba(37, 99, 235, 0.16);
  background:
    radial-gradient(circle at 8% 0%, rgba(37, 99, 235, 0.1), transparent 32%),
    rgba(255, 255, 255, 0.9);
  overflow: hidden;
}
.run-brief-head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
}
.run-brief-head h2 {
  margin: 8px 0 5px;
  color: var(--ai-text);
  font-size: 20px;
}
.run-brief-head p {
  margin: 0;
  color: var(--ai-muted);
}
.run-brief-flow {
  position: relative;
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  padding: 8px 0;
}
.run-brief-rail {
  position: absolute;
  left: 28px;
  right: 28px;
  top: 50%;
  height: 5px;
  overflow: hidden;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(37, 99, 235, 0.1), rgba(20, 184, 166, 0.24), rgba(245, 158, 11, 0.12));
  transform: translateY(-50%);
}
.run-brief-rail::after {
  content: '';
  position: absolute;
  inset: 0;
  width: 42%;
  border-radius: inherit;
  background: linear-gradient(90deg, transparent, rgba(37, 99, 235, 0.5), rgba(20, 184, 166, 0.42), transparent);
  animation: briefRailSweep 4.2s linear infinite;
}
.run-brief-packet {
  position: absolute;
  left: 28px;
  top: 50%;
  z-index: 1;
  width: 10px;
  height: 10px;
  border: 2px solid #fff;
  border-radius: 999px;
  background: #2563eb;
  box-shadow: 0 0 16px rgba(37, 99, 235, 0.36);
  transform: translateY(-50%);
  animation: briefPacketTravel 5.2s linear infinite;
  animation-delay: var(--brief-packet-delay);
}
.run-brief-node {
  position: relative;
  z-index: 2;
  display: inline-grid;
  grid-template-columns: auto auto minmax(148px, 1fr) auto;
  gap: 8px 10px;
  align-items: center;
  flex: 1 1 260px;
  min-height: 64px;
  padding: 10px 12px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.84);
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.04);
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
  animation: decisionNodeGlow 4.6s ease-in-out infinite;
  animation-delay: var(--brief-delay);
}
.run-brief-node:hover {
  border-color: rgba(37, 99, 235, 0.32);
  box-shadow: 0 16px 36px rgba(37, 99, 235, 0.1);
  transform: translateY(-1px);
}
.brief-icon {
  grid-row: span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 16px;
  color: var(--ai-primary);
  background: rgba(37, 99, 235, 0.09);
}
.run-brief-node.tone-good .brief-icon { color: #059669; background: rgba(16, 185, 129, 0.1); }
.run-brief-node.tone-warn .brief-icon { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.run-brief-node.tone-bad .brief-icon { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.brief-step {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 999px;
  color: var(--ai-primary);
  background: rgba(37, 99, 235, 0.08);
  font-size: 11px;
  font-weight: 900;
}
.brief-copy {
  display: grid;
  gap: 3px;
  min-width: 0;
}
.brief-copy em,
.brief-copy i {
  overflow: hidden;
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.brief-copy strong {
  overflow: hidden;
  color: var(--ai-text);
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.run-brief-node b {
  width: fit-content;
  padding: 4px 8px;
  border-radius: 999px;
  color: var(--ai-primary);
  background: rgba(37, 99, 235, 0.08);
  font-size: 11px;
}
.learning-toolbar {
  display: grid;
  gap: var(--sf-spacing-md, 12px);
  margin: var(--learning-gap) 0;
  padding: var(--sf-spacing-lg, 16px);
}
.filter-dock-head {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
}
.filter-dock-head > div:first-child {
  display: grid;
  gap: 5px;
}
.filter-dock-head strong {
  color: var(--ai-text);
  font-size: 15px;
}
.filter-dock-head em {
  color: var(--ai-muted);
  font-size: 12px;
  font-style: normal;
}
.filter-dock-actions,
.quick-filter-flow,
.active-filter-pulse {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.filter-toggle {
  border: 1px solid rgba(37, 99, 235, 0.18);
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  cursor: pointer;
  font-size: 12px;
  font-weight: 800;
  padding: 7px 11px;
}
.filter-toggle.ghost {
  border-color: rgba(148, 163, 184, 0.24);
  background: rgba(248, 250, 252, 0.86);
  color: var(--ai-muted);
}
.quick-filter-node {
  display: inline-grid;
  grid-template-columns: auto auto;
  gap: 2px 8px;
  align-items: center;
  min-width: 148px;
  padding: 9px 11px;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.82);
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
}
.quick-filter-node > span {
  grid-row: span 2;
  color: var(--ai-primary);
}
.quick-filter-node strong {
  font-size: 13px;
}
.quick-filter-node em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.quick-filter-node.tone-danger > span { color: #dc2626; }
.quick-filter-node.tone-artifact > span { color: #7c3aed; }
.quick-filter-node.tone-event > span { color: #0284c7; }
.active-filter-pulse span {
  padding: 5px 9px;
  border-radius: 999px;
  color: #0369a1;
  background: rgba(14, 165, 233, 0.1);
  font-size: 12px;
  font-weight: 800;
}
.advanced-filter-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 14px;
}
.learning-toolbar label {
  display: grid;
  gap: 6px;
  color: var(--ai-muted);
  font-size: 12px;
}
.learning-kpis {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.global-flow-board {
  position: relative;
  margin-bottom: var(--learning-gap);
  padding: var(--sf-spacing-lg, 16px);
  overflow: hidden;
}
.global-flow-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(280px, 0.65fr);
  gap: 16px;
  align-items: stretch;
}
.global-flow-canvas {
  position: relative;
  min-height: 560px;
  border: 1px solid rgba(37, 99, 235, 0.14);
  border-radius: 36px;
  background:
    radial-gradient(circle at 52% 52%, rgba(37, 99, 235, 0.18), transparent 27%),
    radial-gradient(circle at 76% 22%, rgba(16, 185, 129, 0.13), transparent 22%),
    radial-gradient(circle at 24% 76%, rgba(245, 158, 11, 0.12), transparent 24%),
    linear-gradient(135deg, rgba(248, 250, 252, 0.96), rgba(239, 246, 255, 0.76));
  overflow: hidden;
}
.global-flow-canvas::before,
.global-flow-canvas::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  z-index: 0;
  border: 1px solid rgba(37, 99, 235, 0.12);
  border-radius: 999px;
  pointer-events: none;
  transform: translate(-50%, -50%);
}
.global-flow-canvas::before {
  width: 74%;
  height: 72%;
  box-shadow: inset 0 0 44px rgba(37, 99, 235, 0.04);
}
.global-flow-canvas::after {
  width: 42%;
  height: 40%;
  border-style: dashed;
  animation: slowSpin 24s linear infinite;
}
.global-flow-svg {
  position: absolute;
  inset: 0;
  z-index: 1;
  width: 100%;
  height: 100%;
}
.global-edge-track,
.global-edge-energy {
  fill: none;
  stroke-linecap: round;
}
.global-edge-track {
  stroke: rgba(148, 163, 184, 0.22);
  stroke-width: 14;
}
.global-edge-track.active {
  stroke: rgba(37, 99, 235, 0.18);
  stroke-width: 18;
}
.global-edge-energy {
  stroke: url(#global-flow-gradient);
  stroke-width: 4;
  stroke-dasharray: 110 420;
  animation: loopDash var(--motion-loop-global, 8.8s) linear infinite;
}
.global-edge-energy.active {
  stroke-width: 7;
  stroke-dasharray: 150 360;
  filter: drop-shadow(0 0 14px rgba(37, 99, 235, 0.36));
  animation-duration: 5.4s;
}
.global-edge-dot {
  fill: #2563eb;
  filter: drop-shadow(0 0 8px rgba(37, 99, 235, 0.38));
}
.global-flow-core {
  position: absolute;
  left: 50%;
  top: 50%;
  z-index: 4;
  display: grid;
  place-items: center;
  width: 184px;
  height: 184px;
  padding: 24px;
  border: 1px solid rgba(37, 99, 235, 0.22);
  border-radius: 999px;
  background:
    radial-gradient(circle at 50% 30%, rgba(255, 255, 255, 0.92), rgba(255, 255, 255, 0.56) 42%, rgba(219, 234, 254, 0.72)),
    linear-gradient(180deg, rgba(37, 99, 235, 0.18), rgba(20, 184, 166, 0.12));
  color: var(--ai-text);
  text-align: center;
  cursor: pointer;
  box-shadow: 0 24px 70px rgba(37, 99, 235, 0.18), inset 0 0 28px rgba(255, 255, 255, 0.52);
  transform: translate(-50%, -50%);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}
.global-flow-core::before,
.global-flow-core::after {
  content: '';
  position: absolute;
  inset: -12px;
  border-radius: inherit;
  border: 1px solid rgba(37, 99, 235, 0.18);
  animation: coreWave 2.8s ease-out infinite;
}
.global-flow-core::after {
  inset: -24px;
  animation-delay: 0.9s;
}
.global-flow-core:hover {
  box-shadow: 0 30px 86px rgba(37, 99, 235, 0.24), inset 0 0 32px rgba(255, 255, 255, 0.62);
  transform: translate(-50%, calc(-50% - 2px));
}
.global-flow-core span,
.global-flow-core em,
.global-flow-core i {
  position: relative;
  z-index: 1;
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.global-flow-core span {
  font-weight: 900;
  letter-spacing: 0.08em;
}
.global-flow-core strong {
  position: relative;
  z-index: 1;
  color: var(--ai-primary);
  font-size: 28px;
  letter-spacing: -0.05em;
  line-height: 1;
}
.global-flow-core.health-warn strong,
.global-flow-core.health-idle strong { color: #d97706; }
.global-flow-core.health-bad strong { color: #dc2626; }
.global-flow-core.health-good strong { color: #059669; }
.global-edge-label {
  position: absolute;
  z-index: 2;
  max-width: 190px;
  padding: 5px 9px;
  overflow: hidden;
  border: 1px solid rgba(37, 99, 235, 0.16);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.8);
  color: var(--ai-primary);
  cursor: pointer;
  font-size: 11px;
  font-weight: 800;
  text-overflow: ellipsis;
  transform: translate(-50%, -50%);
  white-space: nowrap;
  box-shadow: 0 8px 22px rgba(15, 23, 42, 0.06);
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.global-edge-label:hover {
  border-color: rgba(37, 99, 235, 0.32);
  box-shadow: 0 14px 30px rgba(37, 99, 235, 0.1);
  transform: translate(-50%, calc(-50% - 2px));
}
.global-edge-label.active {
  border-color: rgba(37, 99, 235, 0.42);
  background: rgba(239, 246, 255, 0.96);
  box-shadow: 0 18px 38px rgba(37, 99, 235, 0.16);
}
.global-flow-node {
  position: absolute;
  z-index: 3;
  display: grid;
  grid-template-columns: 1fr;
  gap: 7px;
  justify-items: center;
  width: min(172px, 20%);
  min-height: 172px;
  padding: 16px 14px 13px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 999px;
  background:
    radial-gradient(circle at 50% 24%, rgba(255, 255, 255, 0.96), rgba(255, 255, 255, 0.72) 48%, rgba(241, 245, 249, 0.88)),
    linear-gradient(180deg, rgba(37, 99, 235, 0.08), rgba(255, 255, 255, 0.72));
  color: var(--ai-text);
  text-align: center;
  cursor: pointer;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.1), inset 0 0 24px rgba(255, 255, 255, 0.46);
  transform: translate(-50%, -50%);
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.global-flow-node::before {
  content: '';
  position: absolute;
  inset: -7px;
  border: 1px solid rgba(37, 99, 235, 0.18);
  border-radius: inherit;
  opacity: var(--node-ring-opacity);
  pointer-events: none;
}
.global-flow-node.has-flow::before {
  animation: nodePulse 3.6s ease-in-out infinite;
  animation-delay: var(--global-delay);
}
.global-flow-node:hover,
.global-flow-node.active {
  border-color: rgba(37, 99, 235, 0.34);
  box-shadow: 0 24px 58px rgba(37, 99, 235, 0.16), inset 0 0 28px rgba(255, 255, 255, 0.58);
  transform: translate(-50%, calc(-50% - 2px));
}
.global-node-icon {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border-radius: 999px;
  color: var(--ai-primary);
  background: rgba(37, 99, 235, 0.09);
}
.global-flow-node.tone-sink .global-node-icon { color: #059669; background: rgba(16, 185, 129, 0.11); }
.global-flow-node.tone-artifact .global-node-icon { color: #7c3aed; background: rgba(124, 58, 237, 0.1); }
.global-flow-node.tone-candidate .global-node-icon { color: #ea580c; background: rgba(249, 115, 22, 0.11); }
.global-flow-node.tone-review .global-node-icon { color: #4f46e5; background: rgba(99, 102, 241, 0.11); }
.global-node-copy {
  position: relative;
  z-index: 1;
  display: grid;
  gap: 2px;
  justify-items: center;
  min-width: 0;
  width: 100%;
}
.global-node-copy strong,
.global-node-copy em,
.global-node-metrics i {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.global-node-copy strong {
  color: var(--ai-text);
  font-size: 14px;
}
.global-node-copy em,
.global-node-metrics i {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.global-node-metrics {
  position: relative;
  z-index: 1;
  display: grid;
  gap: 2px;
  justify-items: center;
  min-width: 0;
  width: 100%;
}
.global-node-metrics b {
  color: var(--ai-primary);
  font-size: 22px;
  line-height: 1;
}
.global-node-meter {
  position: relative;
  z-index: 1;
  width: 68%;
  height: 4px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.16);
}
.global-node-meter i {
  display: block;
  width: var(--node-activity);
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #2563eb, #14b8a6, #f59e0b);
  background-size: 180% 100%;
  animation: railFlow 4.2s linear infinite;
}
.global-node-detail {
  display: grid;
  gap: 12px;
  align-content: start;
  padding: 16px;
  border: 1px solid rgba(37, 99, 235, 0.14);
  border-radius: 26px;
  background: rgba(248, 250, 252, 0.82);
}
.global-node-detail h3 {
  margin: 0;
  color: var(--ai-text);
  font-size: 20px;
}
.global-node-detail p {
  margin: 0;
  color: var(--ai-muted);
  line-height: 1.6;
}
.global-detail-narrative {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  padding: 9px 11px;
  overflow: hidden;
  border: 1px solid rgba(37, 99, 235, 0.14);
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.06);
}
.global-detail-narrative::after {
  content: '';
  position: absolute;
  inset: 0;
  width: 42%;
  background: linear-gradient(90deg, transparent, rgba(37, 99, 235, 0.1), transparent);
  animation: briefRailSweep 4.8s linear infinite;
}
.global-detail-narrative span {
  position: relative;
  z-index: 1;
  width: 9px;
  height: 9px;
  border-radius: 999px;
  background: #2563eb;
  box-shadow: 0 0 0 0 rgba(37, 99, 235, 0.38);
  animation: pulseLive 1.8s ease-out infinite;
}
.global-detail-narrative strong {
  position: relative;
  z-index: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--ai-primary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.global-edge-focus {
  position: relative;
  display: grid;
  gap: 6px;
  padding: 12px;
  overflow: hidden;
  border: 1px solid rgba(20, 184, 166, 0.22);
  border-radius: 18px;
  background:
    radial-gradient(circle at 6% 10%, rgba(20, 184, 166, 0.12), transparent 30%),
    rgba(240, 253, 250, 0.72);
}
.global-edge-focus::before {
  content: '';
  position: absolute;
  left: -20%;
  top: 0;
  width: 44%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(20, 184, 166, 0.12), transparent);
  animation: briefRailSweep 4.4s linear infinite;
}
.global-edge-focus > * {
  position: relative;
  z-index: 1;
}
.global-edge-focus span,
.global-edge-focus em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.global-edge-focus strong {
  color: #0f766e;
  font-size: 14px;
}
.global-edge-focus div {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.global-edge-focus button {
  padding: 6px 10px;
  border: 1px solid rgba(20, 184, 166, 0.22);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.76);
  color: #0f766e;
  cursor: pointer;
  font-size: 12px;
  font-weight: 800;
}
.global-detail-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.global-detail-metric {
  position: relative;
  display: grid;
  place-items: center;
  gap: 3px;
  min-height: 102px;
  padding: 14px 8px;
  overflow: hidden;
  border: 1px solid rgba(37, 99, 235, 0.13);
  border-radius: 999px;
  background:
    radial-gradient(circle at 50% 35%, rgba(255, 255, 255, 0.92), rgba(239, 246, 255, 0.64)),
    rgba(255, 255, 255, 0.78);
  color: var(--ai-text);
  text-align: center;
  cursor: pointer;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.05);
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.global-detail-metric:hover {
  border-color: rgba(37, 99, 235, 0.28);
  box-shadow: 0 18px 38px rgba(37, 99, 235, 0.1);
  transform: translateY(-1px);
}
.global-detail-metric i {
  position: absolute;
  inset: 6px;
  border: 2px solid rgba(37, 99, 235, 0.14);
  border-top-color: rgba(37, 99, 235, 0.52);
  border-radius: inherit;
  animation: metricSpin 7.8s linear infinite;
}
.global-detail-metric.tone-good i {
  border-color: rgba(16, 185, 129, 0.14);
  border-top-color: rgba(16, 185, 129, 0.62);
}
.global-detail-metric.tone-warn i {
  border-color: rgba(245, 158, 11, 0.14);
  border-top-color: rgba(245, 158, 11, 0.68);
}
.global-detail-metric.tone-bad i {
  border-color: rgba(239, 68, 68, 0.14);
  border-top-color: rgba(239, 68, 68, 0.62);
}
.global-detail-metric em,
.global-detail-metric span,
.global-detail-metric strong {
  position: relative;
  z-index: 1;
  min-width: 0;
  max-width: 82px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.global-detail-metric em,
.global-detail-metric span {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.global-detail-metric strong {
  color: var(--ai-text);
  font-size: 20px;
  line-height: 1;
}
.global-detail-flow {
  display: grid;
  gap: 7px;
}
.global-detail-flow strong {
  color: var(--ai-text);
  font-size: 13px;
}
.global-detail-flow button {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 999px;
  background: #fff;
  color: var(--ai-text);
  cursor: pointer;
  font-size: 12px;
  text-align: left;
}
.global-detail-flow em {
  color: var(--ai-muted);
  font-size: 12px;
  font-style: normal;
}
.global-detail-changes {
  display: grid;
  gap: 10px;
}
.global-detail-changes section {
  display: grid;
  gap: 7px;
  padding: 10px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.78);
}
.global-detail-changes section > strong {
  color: var(--ai-text);
  font-size: 13px;
}
.global-detail-changes button {
  display: grid;
  gap: 3px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-left: 4px solid rgba(37, 99, 235, 0.38);
  border-radius: 14px;
  background: rgba(248, 250, 252, 0.92);
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
  transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
}
.global-detail-changes button:hover {
  border-color: rgba(37, 99, 235, 0.28);
  box-shadow: 0 10px 24px rgba(37, 99, 235, 0.08);
  transform: translateY(-1px);
}
.global-detail-changes button.tone-good { border-left-color: #22c55e; }
.global-detail-changes button.tone-warn { border-left-color: #f59e0b; }
.global-detail-changes button.tone-bad { border-left-color: #ef4444; }
.global-detail-changes button.tone-info { border-left-color: #2563eb; }
.global-detail-changes button span,
.global-detail-changes button em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.global-detail-changes button span {
  font-size: 12px;
  font-weight: 800;
}
.global-detail-changes button em,
.global-detail-changes section > em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.diagnostic-switch {
  display: flex;
  gap: var(--sf-spacing-md, 12px);
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--learning-gap);
  padding: var(--sf-spacing-lg, 16px);
}
.diagnostic-switch > div {
  display: grid;
  gap: 5px;
}
.diagnostic-switch strong {
  color: var(--ai-text);
}
.diagnostic-sections {
  display: contents;
}

.automation-panel {
  display: grid;
  grid-template-columns: minmax(260px, 0.8fr) minmax(0, 1.2fr);
  gap: var(--learning-gap);
  align-items: center;
  margin-bottom: var(--learning-gap);
  padding: var(--sf-spacing-lg, 16px);
  border-color: rgba(37, 99, 235, 0.16);
  background:
    radial-gradient(circle at 8% 20%, rgba(37, 99, 235, 0.12), transparent 32%),
    rgba(255, 255, 255, 0.86);
}
.automation-panel h2 {
  margin: 8px 0 4px;
  font-size: 18px;
}
.automation-panel p {
  margin: 0;
  color: var(--ai-muted);
}
.automation-panel > div:first-child {
  grid-column: 1 / -1;
}
.automation-service-grid {
  display: grid;
  grid-column: 1 / -1;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}
.automation-service-card {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px 9px;
  align-items: center;
  min-height: 86px;
  padding: 10px 12px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.82);
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
  overflow: hidden;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.automation-service-card:hover {
  border-color: rgba(37, 99, 235, 0.3);
  box-shadow: 0 12px 28px rgba(37, 99, 235, 0.1);
  transform: translateY(-1px);
}
.automation-service-card > span {
  grid-row: span 3;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  color: var(--ai-primary);
  background: rgba(37, 99, 235, 0.08);
}
.automation-service-card.tone-good > span { color: #059669; background: rgba(16, 185, 129, 0.1); }
.automation-service-card.tone-warn > span { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.automation-service-card.tone-bad > span { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.automation-service-card em,
.automation-service-card i {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.automation-service-card strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-text);
  font-size: 14px;
  font-weight: 950;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.automation-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.automation-signal {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px 9px;
  align-items: center;
  padding: 10px 12px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 999px;
  background:
    linear-gradient(90deg, rgba(255, 255, 255, 0.88), rgba(239, 246, 255, 0.62));
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
  overflow: hidden;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.automation-signal::after {
  content: '';
  position: absolute;
  left: 48px;
  right: 12px;
  bottom: 7px;
  height: 2px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(37, 99, 235, 0.12), rgba(14, 165, 233, 0.45), rgba(20, 184, 166, 0.16));
}
.automation-signal:hover {
  border-color: rgba(37, 99, 235, 0.32);
  box-shadow: 0 14px 34px rgba(37, 99, 235, 0.1);
  transform: translateY(-1px);
}
.automation-signal > span {
  grid-row: span 3;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 999px;
  color: var(--ai-primary);
  background: rgba(37, 99, 235, 0.08);
}
.automation-signal.tone-good > span { color: #059669; background: rgba(16, 185, 129, 0.1); }
.automation-signal.tone-warn > span { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.automation-signal.tone-bad > span { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.automation-signal em,
.automation-signal i {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.automation-signal strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-text);
  font-size: 18px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.automation-runs {
  display: grid;
  gap: 8px;
  min-width: 0;
}
.automation-runs > strong {
  color: var(--ai-text);
  font-size: 13px;
}
.automation-run {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  padding: 8px 10px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.78);
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
}
.automation-run span {
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.12);
  color: var(--ai-muted);
  font-size: 11px;
  font-weight: 800;
}
.automation-run span.good { background: rgba(34, 197, 94, 0.12); color: #16a34a; }
.automation-run span.warn { background: rgba(245, 158, 11, 0.12); color: #d97706; }
.automation-run span.bad { background: rgba(239, 68, 68, 0.12); color: #dc2626; }
.automation-run em {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-muted);
  font-size: 12px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.automation-run b {
  color: var(--ai-primary);
  font-size: 12px;
}
.learning-manifest {
  display: grid;
  grid-template-columns: minmax(240px, 0.9fr) minmax(0, 1.2fr) auto;
  gap: var(--learning-gap);
  align-items: center;
  margin-bottom: var(--learning-gap);
  padding: var(--sf-spacing-lg, 16px);
}
.learning-manifest h2 {
  margin: 8px 0 4px;
  font-size: 18px;
}
.learning-manifest p {
  margin: 0;
  color: var(--ai-muted);
}
.manifest-beam {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px 14px;
  align-items: center;
  min-width: 0;
}
.manifest-core {
  display: grid;
  place-items: center;
  width: 112px;
  height: 112px;
  border: 1px solid rgba(37, 99, 235, 0.18);
  border-radius: 999px;
  background:
    radial-gradient(circle at 50% 32%, rgba(37, 99, 235, 0.16), transparent 55%),
    rgba(255, 255, 255, 0.82);
  color: var(--ai-text);
  cursor: pointer;
  box-shadow: 0 18px 42px rgba(37, 99, 235, 0.08);
}
.manifest-core span,
.manifest-core em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.manifest-core strong {
  color: var(--ai-primary);
  font-size: 24px;
  line-height: 1;
}
.manifest-stream {
  position: relative;
  min-height: 96px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(248, 250, 252, 0.66);
}
.manifest-track,
.manifest-energy {
  position: absolute;
  left: 7%;
  right: 7%;
  top: 50%;
  height: 7px;
  border-radius: 999px;
  transform: translateY(-50%);
}
.manifest-track {
  background: rgba(148, 163, 184, 0.16);
}
.manifest-energy {
  right: auto;
  width: 86%;
  background: linear-gradient(90deg, #7c3aed, #2563eb, #14b8a6);
  background-size: 220% 100%;
  animation: railFlow 5.8s linear infinite;
}
.manifest-skill {
  position: absolute;
  top: 50%;
  display: grid;
  gap: 2px;
  min-width: 112px;
  padding: 7px 10px;
  border: 1px solid rgba(37, 99, 235, 0.18);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.88);
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
  transform: translate(-50%, -50%);
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.07);
}
.manifest-skill strong {
  color: var(--ai-primary);
  font-size: 13px;
}
.manifest-skill em {
  max-width: 104px;
  overflow: hidden;
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.manifest-ref {
  grid-column: 2;
  overflow: hidden;
  color: var(--ai-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bottleneck-board {
  margin-bottom: var(--learning-gap);
  padding: var(--sf-spacing-lg, 16px);
  overflow: hidden;
}
.bottleneck-radar {
  position: relative;
  min-height: 360px;
  margin-top: 8px;
  border-radius: 28px;
  background:
    radial-gradient(circle at 50% 50%, rgba(37, 99, 235, 0.12), transparent 34%),
    radial-gradient(circle at 20% 24%, rgba(245, 158, 11, 0.1), transparent 28%),
    linear-gradient(135deg, rgba(248, 250, 252, 0.92), rgba(239, 246, 255, 0.72));
  overflow: hidden;
}
.bottleneck-radar-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.radar-ring,
.radar-axis {
  fill: none;
  stroke: rgba(37, 99, 235, 0.16);
  stroke-width: 1;
  stroke-dasharray: 7 8;
}
.radar-ring.ring-2 { stroke-opacity: 0.78; }
.radar-ring.ring-3 { stroke-opacity: 0.54; }
.radar-sweep {
  fill: rgba(14, 165, 233, 0.11);
  transform-origin: 360px 180px;
  animation: radarSweep 6.8s linear infinite;
}
.bottleneck-radar.empty .radar-sweep {
  opacity: 0.22;
  animation: none;
}
.bottleneck-core {
  position: absolute;
  left: 50%;
  top: 50%;
  z-index: 2;
  display: grid;
  place-items: center;
  width: 140px;
  height: 140px;
  padding: 16px;
  border: 1px solid rgba(37, 99, 235, 0.28);
  border-radius: 999px;
  background:
    radial-gradient(circle at 50% 35%, rgba(59, 130, 246, 0.22), transparent 62%),
    rgba(255, 255, 255, 0.84);
  color: var(--ai-text);
  text-align: center;
  cursor: pointer;
  transform: translate(-50%, -50%);
  box-shadow: 0 24px 62px rgba(37, 99, 235, 0.18);
}
.bottleneck-core::after {
  content: '';
  position: absolute;
  inset: -10px;
  border-radius: inherit;
  border: 1px solid rgba(37, 99, 235, 0.16);
  animation: radarPulse 2.3s ease-out infinite;
}
.bottleneck-core span,
.bottleneck-core em {
  color: var(--ai-muted);
  font-size: 12px;
  font-style: normal;
}
.bottleneck-core strong {
  color: var(--ai-primary);
  font-size: 30px;
  line-height: 1;
}
.bottleneck-radar-node {
  position: absolute;
  z-index: 3;
  width: min(240px, 30vw);
  transform: translate(-50%, -50%);
}
.bottleneck-beacon {
  position: relative;
  display: grid;
  gap: 4px;
  width: 100%;
  padding: 13px 15px 13px 18px;
  border: 1px solid rgba(148, 163, 184, 0.26);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.84);
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
  box-shadow: 0 18px 44px rgba(15, 23, 42, 0.08);
  backdrop-filter: blur(12px);
}
.bottleneck-beacon::before {
  content: '';
  position: absolute;
  left: 8px;
  top: 50%;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #22c55e;
  transform: translateY(-50%);
  box-shadow: 0 0 0 6px rgba(34, 197, 94, 0.1);
}
.bottleneck-beacon::after {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: inherit;
  border: 1px solid transparent;
  pointer-events: none;
}
.bottleneck-radar-node.severity-warning .bottleneck-beacon {
  border-color: rgba(245, 158, 11, 0.36);
  background: rgba(255, 251, 235, 0.88);
}
.bottleneck-radar-node.severity-warning .bottleneck-beacon::before {
  background: #f59e0b;
  box-shadow: 0 0 0 6px rgba(245, 158, 11, 0.14), 0 0 22px rgba(245, 158, 11, 0.34);
}
.bottleneck-radar-node.severity-danger .bottleneck-beacon {
  border-color: rgba(239, 68, 68, 0.38);
  background: rgba(254, 242, 242, 0.9);
}
.bottleneck-radar-node.severity-danger .bottleneck-beacon::before {
  background: #ef4444;
  box-shadow: 0 0 0 6px rgba(239, 68, 68, 0.14), 0 0 24px rgba(239, 68, 68, 0.38);
}
.bottleneck-radar-node.severity-warning .bottleneck-beacon::after,
.bottleneck-radar-node.severity-danger .bottleneck-beacon::after {
  border-color: currentColor;
  opacity: 0.22;
  animation: radarPulse 2.8s ease-out infinite;
  animation-delay: var(--radar-delay);
}
.bottleneck-beacon span {
  padding-left: 8px;
  color: var(--ai-muted);
  font-size: 12px;
  font-weight: 800;
}
.bottleneck-beacon strong {
  padding-left: 8px;
  color: var(--ai-text);
  font-size: 22px;
  line-height: 1;
}
.bottleneck-beacon em,
.bottleneck-beacon i {
  min-width: 0;
  overflow: hidden;
  padding-left: 8px;
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bottleneck-quick-actions {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
  justify-content: center;
  margin-top: 6px;
}
.bottleneck-action {
  border: 1px solid rgba(37, 99, 235, 0.2);
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 11px;
  font-weight: 700;
  padding: 5px 8px;
  cursor: pointer;
}
.bottleneck-empty {
  position: absolute;
  left: 50%;
  top: 74%;
  z-index: 2;
  padding: 8px 12px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.8);
  color: var(--ai-muted);
  font-size: 12px;
  transform: translateX(-50%);
}
.flow-stage-board {
  position: relative;
  margin-bottom: var(--learning-gap);
  padding: var(--sf-spacing-lg, 16px);
  overflow: hidden;
}
.board-title {
  align-items: flex-start;
}
.flow-live {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 11px;
  border: 1px solid rgba(34, 197, 94, 0.28);
  border-radius: 999px;
  color: #16a34a;
  background: rgba(34, 197, 94, 0.08);
  font-size: 12px;
  font-weight: 700;
}
.flow-live span {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #22c55e;
  box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.55);
  animation: pulseLive 1.6s ease-out infinite;
}
.flow-canvas {
  position: relative;
  min-height: 420px;
  margin: 10px 0 14px;
  border-radius: 30px;
  background:
    radial-gradient(circle at 50% 50%, rgba(37, 99, 235, 0.1), transparent 30%),
    linear-gradient(135deg, rgba(248, 250, 252, 0.9), rgba(236, 253, 245, 0.62));
  overflow: hidden;
}
.flow-canvas.empty {
  opacity: 0.82;
}
.flow-loop-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.flow-loop-track {
  fill: none;
  stroke: rgba(37, 99, 235, 0.14);
  stroke-width: 8;
  stroke-linecap: round;
}
.flow-loop-energy {
  fill: none;
  stroke: url(#flow-loop-gradient);
  stroke-width: 4;
  stroke-linecap: round;
  stroke-dasharray: 160 620;
  filter: drop-shadow(0 0 12px rgba(37, 99, 235, 0.2));
  animation: loopDash var(--motion-loop-flow, 7.2s) linear infinite;
}
.flow-loop-packet {
  fill: #fff;
  stroke: #2563eb;
  stroke-width: 2;
  filter: drop-shadow(0 0 9px rgba(37, 99, 235, 0.34));
}
.flow-loop-count {
  fill: #2563eb;
  font-size: 18px;
  font-weight: 900;
  paint-order: stroke;
  stroke: rgba(255, 255, 255, 0.9);
  stroke-width: 4px;
}
.flow-loop-count.tone-event { fill: #0284c7; }
.flow-loop-count.tone-artifact { fill: #7c3aed; }
.flow-loop-count.tone-sink { fill: #059669; }
.flow-loop-count.tone-candidate { fill: #ea580c; }
.flow-loop-count.tone-review { fill: #4f46e5; }
.flow-loop-label {
  position: absolute;
  z-index: 1;
  padding: 5px 10px;
  border: 1px solid rgba(37, 99, 235, 0.18);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.78);
  color: var(--ai-primary);
  cursor: pointer;
  font-size: 12px;
  font-weight: 800;
  transform: translate(-50%, -50%);
  box-shadow: 0 10px 28px rgba(37, 99, 235, 0.08);
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.flow-loop-label:hover {
  border-color: rgba(37, 99, 235, 0.34);
  transform: translate(-50%, calc(-50% - 2px));
  box-shadow: 0 16px 34px rgba(37, 99, 235, 0.12);
}
.flow-stage {
  position: absolute;
  z-index: 2;
  width: min(250px, 27vw);
  display: grid;
  grid-template-rows: auto 1fr;
  gap: 10px;
  padding: 13px;
  border: 1px solid rgba(148, 163, 184, 0.28);
  border-radius: 28px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.92), rgba(248, 250, 252, 0.84)),
    radial-gradient(circle at 20% 0%, rgba(37, 99, 235, 0.1), transparent 42%);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.08);
  transform: translate(-50%, -50%);
  backdrop-filter: blur(14px);
}
.flow-stage-head {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px;
  align-items: flex-start;
}
.stage-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 10px;
  color: #fff;
  background: var(--ai-primary);
  font-size: 12px;
  font-weight: 800;
}
.flow-stage-head h3 {
  margin: 0 0 4px;
  font-size: 15px;
}
.flow-stage-head p {
  margin: 0;
  color: var(--ai-muted);
  font-size: 12px;
  line-height: 1.35;
}
.flow-stage-head strong {
  color: var(--ai-text);
  font-size: 18px;
  line-height: 1;
}
.flow-node-list {
  display: grid;
  gap: 7px;
  align-content: start;
}
.flow-node {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px;
  width: 100%;
  min-height: 48px;
  padding: 8px 10px;
  border: 1px solid rgba(148, 163, 184, 0.28);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.88);
  text-align: left;
  cursor: pointer;
  overflow: hidden;
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.flow-node:hover,
.flow-node.active {
  transform: translateY(-1px);
  border-color: rgba(37, 99, 235, 0.36);
  box-shadow: 0 10px 24px rgba(37, 99, 235, 0.1);
}
.node-icon {
  z-index: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 10px;
  color: var(--ai-primary);
  background: rgba(37, 99, 235, 0.08);
}
.node-main {
  z-index: 1;
  min-width: 0;
  display: grid;
  gap: 3px;
}
.node-main strong,
.node-main em {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.node-main strong {
  color: var(--ai-text);
  font-size: 13px;
}
.node-main em {
  color: var(--ai-muted);
  font-size: 12px;
  font-style: normal;
}
.node-heat {
  position: absolute;
  left: 0;
  bottom: 0;
  height: 3px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(37, 99, 235, 0.3), rgba(14, 165, 233, 0.7));
}
.node-event .node-icon,
.tone-event .stage-index { color: #0369a1; background: rgba(14, 165, 233, 0.13); }
.node-artifact .node-icon,
.tone-artifact .stage-index { color: #7c3aed; background: rgba(124, 58, 237, 0.13); }
.node-sink .node-icon,
.tone-sink .stage-index { color: #059669; background: rgba(16, 185, 129, 0.14); }
.node-candidate .node-icon,
.tone-candidate .stage-index { color: #ea580c; background: rgba(249, 115, 22, 0.14); }
.node-review .node-icon,
.tone-review .stage-index { color: #4f46e5; background: rgba(99, 102, 241, 0.14); }
.node-danger .node-icon { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.node-muted { opacity: 0.62; }
.empty-node {
  min-height: 48px;
  align-items: center;
  justify-content: center;
  border-style: dashed;
  color: var(--ai-muted);
  cursor: default;
}
.flow-stage-more {
  width: fit-content;
  padding: 5px 10px;
  border: 1px solid rgba(37, 99, 235, 0.2);
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  cursor: pointer;
  font-size: 12px;
  font-weight: 800;
}
.flow-loop-empty {
  position: absolute;
  left: 50%;
  top: 50%;
  padding: 8px 12px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.84);
  color: var(--ai-muted);
  transform: translate(-50%, -50%);
  font-size: 12px;
}
.flow-legend {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  color: var(--ai-muted);
  font-size: 12px;
}
.flow-legend span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--ai-primary);
}
.legend-dot.event { background: #0ea5e9; }
.legend-dot.artifact { background: #7c3aed; }
.legend-dot.sink { background: #10b981; }
.legend-dot.candidate { background: #f97316; }
.journey-board {
  margin-bottom: var(--learning-gap);
  padding: var(--sf-spacing-lg, 16px);
}
.journey-stats {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.journey-stats span {
  padding: 5px 9px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 999px;
  background: rgba(248, 250, 252, 0.74);
  color: var(--ai-muted);
  font-size: 12px;
}
.journey-flow-map {
  display: grid;
  gap: 14px;
}
.journey-lane {
  position: relative;
  display: grid;
  gap: 12px;
  padding: 14px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 28px;
  background:
    radial-gradient(circle at 12% 50%, rgba(37, 99, 235, 0.08), transparent 32%),
    linear-gradient(135deg, rgba(255, 255, 255, 0.94), rgba(239, 246, 255, 0.72));
  overflow: hidden;
}
.journey-lane.state-blocked,
.journey-lane.state-failed {
  border-color: rgba(245, 158, 11, 0.32);
  background:
    radial-gradient(circle at 12% 50%, rgba(245, 158, 11, 0.12), transparent 32%),
    linear-gradient(135deg, rgba(255, 255, 255, 0.95), rgba(255, 251, 235, 0.78));
}
.journey-summary {
  position: relative;
  z-index: 2;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  width: 100%;
  border: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.journey-state {
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.12);
  color: var(--ai-muted);
  font-size: 12px;
  font-weight: 800;
}
.journey-state.good { background: rgba(34, 197, 94, 0.12); color: #16a34a; }
.journey-state.warn { background: rgba(245, 158, 11, 0.12); color: #d97706; }
.journey-state.bad { background: rgba(239, 68, 68, 0.12); color: #dc2626; }
.journey-summary-copy {
  display: grid;
  gap: 3px;
  min-width: 0;
}
.journey-summary-copy strong,
.journey-summary-copy em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.journey-summary-copy strong {
  color: var(--ai-text);
}
.journey-summary-copy em {
  color: var(--ai-muted);
  font-size: 12px;
  font-style: normal;
}
.journey-summary b {
  padding: 5px 9px;
  border-radius: 999px;
  color: var(--ai-primary);
  background: rgba(37, 99, 235, 0.08);
  font-size: 12px;
}
.journey-rail {
  position: relative;
  min-height: 150px;
  padding: 18px 4px;
}
.journey-rail-base,
.journey-rail-energy {
  position: absolute;
  left: 8%;
  right: 8%;
  top: 50%;
  display: block;
  height: 8px;
  border-radius: 999px;
  transform: translateY(-50%);
}
.journey-rail-base {
  background: rgba(148, 163, 184, 0.16);
}
.journey-rail-energy {
  right: auto;
  width: var(--journey-progress-width);
  background: linear-gradient(90deg, #2563eb, #22d3ee, #14b8a6);
  background-size: 220% 100%;
  box-shadow: 0 0 24px rgba(37, 99, 235, 0.16);
  animation: railFlow 5s linear infinite;
}
.journey-cursor {
  position: absolute;
  left: var(--journey-cursor-left);
  top: 50%;
  z-index: 3;
  width: 16px;
  height: 16px;
  border: 3px solid #fff;
  border-radius: 999px;
  background: #2563eb;
  box-shadow: 0 0 0 7px rgba(37, 99, 235, 0.12), 0 0 22px rgba(37, 99, 235, 0.32);
  transform: translate(-50%, -50%);
  animation: cursorPulse 1.8s ease-out infinite;
}
.journey-step-node {
  position: absolute;
  z-index: 4;
  display: grid;
  gap: 3px;
  width: min(168px, 19vw);
  min-height: 58px;
  padding: 9px 12px;
  border: 1px solid rgba(148, 163, 184, 0.24);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.88);
  text-align: left;
  cursor: pointer;
  box-shadow: 0 14px 32px rgba(15, 23, 42, 0.07);
  transform: translate(-50%, -50%);
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.journey-step-node:hover {
  border-color: rgba(37, 99, 235, 0.34);
  box-shadow: 0 18px 36px rgba(37, 99, 235, 0.11);
  transform: translate(-50%, calc(-50% - 2px));
}
.journey-step-node span {
  color: var(--ai-primary);
  font-size: 11px;
  font-weight: 800;
}
.journey-step-node strong,
.journey-step-node em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.journey-step-node strong {
  color: var(--ai-text);
  font-size: 12px;
}
.journey-step-node em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.journey-step-node.good {
  border-color: rgba(34, 197, 94, 0.22);
  background: rgba(240, 253, 244, 0.78);
}
.journey-step-node.warn {
  border-color: rgba(245, 158, 11, 0.26);
  background: rgba(255, 251, 235, 0.82);
}
.journey-step-node.bad {
  border-color: rgba(239, 68, 68, 0.26);
  background: rgba(254, 242, 242, 0.82);
}
.journey-blockers {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.journey-blockers span {
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(245, 158, 11, 0.12);
  color: #b45309;
  font-size: 11px;
  font-weight: 700;
}
.operation-stream {
  position: relative;
  margin-bottom: var(--learning-gap);
  padding: var(--sf-spacing-lg, 16px);
  overflow: hidden;
}
.operation-map {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(280px, 0.65fr);
  gap: 16px;
  align-items: stretch;
}
.stream-rail {
  position: relative;
  min-height: 300px;
  overflow: hidden;
  border: 1px solid rgba(37, 99, 235, 0.16);
  border-radius: 24px;
  background:
    radial-gradient(circle at 20% 18%, rgba(37, 99, 235, 0.14), transparent 30%),
    radial-gradient(circle at 74% 70%, rgba(20, 184, 166, 0.12), transparent 32%),
    linear-gradient(180deg, rgba(248, 250, 252, 0.94), rgba(241, 245, 249, 0.84));
}
.operation-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.stream-backbone,
.stream-energy {
  fill: none;
  stroke-linecap: round;
}
.stream-backbone {
  stroke: rgba(148, 163, 184, 0.24);
  stroke-width: 18;
}
.stream-energy {
  stroke: #2563eb;
  stroke-width: 4;
  stroke-dasharray: 140 520;
  filter: drop-shadow(0 0 8px rgba(37, 99, 235, 0.2));
  animation: loopDash var(--motion-loop-river, 8s) linear infinite;
}
.river-packet {
  fill: #2563eb;
  filter: drop-shadow(0 0 9px rgba(37, 99, 235, 0.58));
}
.stream-signal {
  position: absolute;
  z-index: 2;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 7px 9px;
  width: min(210px, 30%);
  min-height: 66px;
  padding: 10px 12px;
  border: 1px solid rgba(37, 99, 235, 0.2);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.82);
  text-align: left;
  box-shadow: 0 14px 30px rgba(15, 23, 42, 0.08);
  backdrop-filter: blur(12px);
  transform: translate(-50%, -50%);
  cursor: pointer;
}
.stream-signal.good { border-color: rgba(34, 197, 94, 0.26); }
.stream-signal.warn { border-color: rgba(245, 158, 11, 0.32); }
.stream-signal.bad { border-color: rgba(239, 68, 68, 0.32); }
.signal-orb {
  grid-row: 1 / span 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 14px;
  color: var(--ai-primary);
  background: rgba(37, 99, 235, 0.1);
  box-shadow: 0 0 0 0 rgba(37, 99, 235, 0.3);
}
.stream-signal strong,
.stream-signal em {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.stream-signal strong {
  color: var(--ai-text);
  font-size: 12px;
}
.stream-signal em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.stream-empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: var(--ai-muted);
  font-size: 13px;
}
.stream-console {
  display: grid;
  gap: 8px;
  align-content: start;
  min-height: 300px;
  padding: 14px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 22px;
  background: rgba(248, 250, 252, 0.82);
}
.console-title,
.lineage-node {
  display: grid;
  gap: 8px;
  align-items: center;
}
.console-title {
  color: var(--ai-muted);
  font-size: 12px;
}
.console-title strong {
  grid-column: 3;
  color: var(--ai-text);
  text-align: right;
}
.lineage-compass {
  position: relative;
  min-height: 250px;
  border-radius: 20px;
  background:
    radial-gradient(circle at 50% 50%, rgba(37, 99, 235, 0.08), transparent 34%),
    rgba(255, 255, 255, 0.56);
  overflow: hidden;
}
.lineage-core {
  position: absolute;
  left: 50%;
  top: 50%;
  z-index: 1;
  display: grid;
  place-items: center;
  width: 96px;
  height: 96px;
  border: 1px solid rgba(37, 99, 235, 0.14);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.82);
  transform: translate(-50%, -50%);
}
.lineage-core strong {
  color: var(--ai-primary);
  font-size: 22px;
  line-height: 1;
}
.lineage-core em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.lineage-node {
  position: absolute;
  z-index: 2;
  width: min(176px, 42%);
  padding: 8px 10px;
  border: 1px solid rgba(37, 99, 235, 0.14);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.88);
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
  transform: translate(-50%, -50%);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.07);
}
.lineage-node span {
  min-width: 0;
  overflow: hidden;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lineage-node i {
  width: fit-content;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 11px;
  font-style: normal;
  font-weight: 800;
}
.material-orbit {
  position: relative;
  min-height: 230px;
  margin-top: 16px;
  overflow: hidden;
  border: 1px solid rgba(124, 58, 237, 0.14);
  border-radius: 24px;
  background:
    radial-gradient(circle at 50% 50%, rgba(124, 58, 237, 0.12), transparent 30%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.9), rgba(248, 250, 252, 0.86));
}
.material-core {
  position: absolute;
  z-index: 2;
  left: 50%;
  top: 50%;
  display: grid;
  place-items: center;
  width: 116px;
  height: 116px;
  border-radius: 999px;
  background: linear-gradient(180deg, rgba(124, 58, 237, 0.88), rgba(37, 99, 235, 0.78));
  color: #fff;
  transform: translate(-50%, -50%);
  box-shadow: 0 18px 42px rgba(37, 99, 235, 0.2);
}
.material-core span,
.material-core em {
  color: rgba(255, 255, 255, 0.76);
  font-size: 11px;
  font-style: normal;
}
.material-core strong {
  font-size: 28px;
  line-height: 1;
}
.material-orbit-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}
.material-track,
.material-track-hot {
  fill: none;
  stroke-linecap: round;
}
.material-track {
  stroke: rgba(148, 163, 184, 0.18);
  stroke-width: 16;
}
.material-track-hot {
  stroke: rgba(124, 58, 237, 0.42);
  stroke-width: 4;
  stroke-dasharray: 20 14;
  animation: linkFlow 2.1s linear infinite;
}
.material-packet {
  fill: #7c3aed;
  filter: drop-shadow(0 0 9px rgba(124, 58, 237, 0.55));
}
.material-token {
  position: absolute;
  z-index: 2;
  display: grid;
  gap: 4px;
  width: min(190px, 24%);
  padding: 9px 11px;
  border: 1px solid rgba(124, 58, 237, 0.2);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.86);
  text-align: left;
  transform: translate(-50%, -50%);
  cursor: pointer;
  animation: signalBob 3.4s ease-in-out infinite;
  animation-delay: var(--token-delay);
}
.material-token.token-candidate {
  border-color: rgba(249, 115, 22, 0.24);
}
.material-token span {
  width: fit-content;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(124, 58, 237, 0.08);
  color: #7c3aed;
  font-size: 11px;
  font-weight: 800;
}
.material-token.token-candidate span {
  background: rgba(249, 115, 22, 0.1);
  color: #ea580c;
}
.material-token strong,
.material-token em {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.material-token strong {
  color: var(--ai-text);
  font-size: 12px;
}
.material-token em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.material-empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: var(--ai-muted);
}
@keyframes streamMove {
  from { transform: translateX(0); opacity: 0; }
  12% { opacity: 1; }
  86% { opacity: 1; }
  to { transform: translateX(48px); opacity: 0; }
}
@keyframes commandSweep {
  from { transform: translateX(-8%) rotate(0.001deg); }
  to { transform: translateX(8%) rotate(0.001deg); }
}
@keyframes haloSpin {
  to { transform: rotate(360deg); }
}
@keyframes linkFlow {
  to { stroke-dashoffset: -42; }
}
@keyframes closedLoopFlow {
  to { stroke-dashoffset: -72; }
}
@keyframes loopDash {
  to { stroke-dashoffset: -780; }
}
@keyframes slowSpin {
  to { transform: translate(-50%, -50%) rotate(360deg); }
}
@keyframes sideOrbitSpin {
  to { transform: translate(-50%, -50%) rotate(360deg); }
}
@keyframes sideSignalFloat {
  0%, 100% { margin-top: 0; }
  50% { margin-top: -5px; }
}
@keyframes sideActionScan {
  0% { transform: scaleX(0.18); opacity: 0.34; }
  45% { transform: scaleX(1); opacity: 0.9; }
  100% { transform: scaleX(0.18); opacity: 0.34; }
}
@keyframes routeEnergyFlow {
  to { background-position: 0 -220%; }
}
@keyframes routeStepBreath {
  0%, 100% { box-shadow: 0 8px 18px rgba(15, 23, 42, 0.035); }
  50% { box-shadow: 0 10px 24px rgba(37, 99, 235, 0.07); }
}
@keyframes coreWave {
  from { transform: scale(0.88); opacity: 0.78; }
  to { transform: scale(1.22); opacity: 0; }
}
@keyframes nodePulse {
  0%, 100% { transform: scale(0.96); opacity: 0.38; }
  50% { transform: scale(1.08); opacity: 0.82; }
}
@keyframes briefRailSweep {
  from { transform: translateX(-110%); }
  to { transform: translateX(260%); }
}
@keyframes briefPacketTravel {
  from { left: 28px; opacity: 0; }
  10% { opacity: 1; }
  88% { opacity: 1; }
  to { left: calc(100% - 28px); opacity: 0; }
}
@keyframes decisionNodeGlow {
  0%, 100% { box-shadow: 0 10px 30px rgba(15, 23, 42, 0.04); }
  50% { box-shadow: 0 14px 34px rgba(37, 99, 235, 0.09); }
}
@keyframes lensPulse {
  0%, 100% { transform: translateX(-10%); opacity: 0.28; }
  50% { transform: translateX(10%); opacity: 0.78; }
}
@keyframes metricSpin {
  to { transform: rotate(360deg); }
}
@keyframes nodeFloat {
  0%, 100% { margin-top: 0; }
  50% { margin-top: -5px; }
}
@keyframes signalBob {
  0%, 100% { margin-top: 0; }
  50% { margin-top: -6px; }
}
@keyframes edgeGlow {
  0%, 100% { box-shadow: 0 0 0 rgba(37, 99, 235, 0); }
  50% { box-shadow: 0 10px 24px rgba(37, 99, 235, 0.08); }
}
@keyframes pulseLive {
  to {
    box-shadow: 0 0 0 10px rgba(34, 197, 94, 0);
  }
}
@keyframes railFlow {
  to { background-position: -220% 0; }
}
@keyframes focusRadarPing {
  0%, 100% { transform: scale(1); opacity: 0.62; }
  50% { transform: scale(1.035); opacity: 0.95; }
}
@keyframes decisionFloat {
  0%, 100% { box-shadow: 0 8px 18px rgba(15, 23, 42, 0.035); }
  50% { box-shadow: 0 12px 26px color-mix(in srgb, var(--motion-health, #2563eb) 10%, transparent); }
}
@keyframes rootCauseGlow {
  0%, 100% { box-shadow: 0 8px 18px rgba(15, 23, 42, 0.035); }
  50% { box-shadow: 0 14px 30px color-mix(in srgb, var(--motion-health, #2563eb) 12%, transparent); }
}
@keyframes cursorPulse {
  to { box-shadow: 0 0 0 16px rgba(37, 99, 235, 0), 0 0 22px rgba(37, 99, 235, 0.28); }
}
@keyframes radarSweep {
  to { transform: rotate(360deg); }
}
@keyframes radarPulse {
  from { transform: scale(0.92); opacity: 0.75; }
  to { transform: scale(1.18); opacity: 0; }
}
@keyframes radarFloat {
  0%, 100% { margin-top: 0; }
  50% { margin-top: -7px; }
}
@keyframes stageDrift {
  0%, 100% { margin-top: 0; }
  50% { margin-top: -6px; }
}
.learning-kpi {
  position: relative;
  display: grid;
  grid-template-columns: auto auto;
  gap: 4px 10px;
  align-items: baseline;
  min-width: 188px;
  padding: 10px 13px 10px 15px;
  overflow: hidden;
  border: 1px solid rgba(37, 99, 235, 0.14);
  border-radius: 999px;
  background:
    linear-gradient(90deg, rgba(37, 99, 235, 0.08), rgba(20, 184, 166, 0.05)),
    rgba(255, 255, 255, 0.86);
  box-shadow: 0 10px 26px rgba(15, 23, 42, 0.04);
}
.learning-kpi::before {
  content: '';
  position: absolute;
  left: 8px;
  top: 50%;
  width: 5px;
  height: 5px;
  border-radius: 999px;
  background: #2563eb;
  box-shadow: 0 0 0 0 rgba(37, 99, 235, 0.42);
  transform: translateY(-50%);
  animation: pulseLive 1.8s ease-out infinite;
}
.learning-kpi span,
.learning-kpi em,
.card-title p,
.meta-line,
.time {
  color: var(--ai-muted);
  font-style: normal;
}
.learning-kpi strong {
  color: var(--ai-text);
  font-size: 22px;
  line-height: 1;
}
.learning-kpi span {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 700;
}
.learning-kpi em {
  grid-column: 1 / -1;
  margin-left: 8px;
  font-size: 11px;
}
.learning-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(360px, 0.8fr);
  gap: 16px;
  margin-bottom: 16px;
}
.learning-grid.bottom {
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
}
.flow-card,
.timeline-card,
.artifact-card,
.candidate-card {
  padding: 18px;
  min-height: 360px;
}
.card-title {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}
.card-title h2 {
  margin: 0 0 4px;
  font-size: 18px;
}
.card-title p {
  margin: 0;
  font-size: 13px;
}
.mini-stat {
  white-space: nowrap;
  color: var(--ai-muted);
  font-size: 12px;
}
.flow-list,
.timeline-list,
.asset-list,
.candidate-list {
  display: grid;
  gap: 10px;
  max-height: 520px;
  overflow: auto;
  padding-right: 4px;
}
.flow-edge {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: 14px;
  background: var(--ai-surface-soft);
}
.flow-edge button {
  border: 0;
  background: #fff;
  border-radius: 10px;
  padding: 8px 10px;
  color: var(--ai-text);
  text-align: left;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
}
.relation {
  color: var(--ai-primary);
  font-size: 12px;
  white-space: nowrap;
}
.timeline-item,
.asset-row,
.candidate-row {
  display: flex;
  gap: 12px;
  width: 100%;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: 14px;
  background: #fff;
  text-align: left;
}
.timeline-item {
  align-items: flex-start;
  cursor: pointer;
}
.dot {
  width: 10px;
  height: 10px;
  margin-top: 5px;
  border-radius: 999px;
  background: #94a3b8;
}
.dot.good { background: #22c55e; }
.dot.warn { background: #f59e0b; }
.dot.bad { background: #ef4444; }
.timeline-main {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 4px;
}
.timeline-main strong,
.asset-main strong,
.candidate-main strong {
  color: var(--ai-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.timeline-main em,
.asset-main p,
.candidate-main p {
  margin: 0;
  color: var(--ai-muted);
  font-style: normal;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.asset-main,
.candidate-main {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 6px;
  cursor: pointer;
}
.kind,
.target {
  width: fit-content;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: var(--ai-primary);
  font-size: 12px;
}
.meta-line {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12px;
}
.asset-actions,
.candidate-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-content: flex-start;
  max-width: 190px;
}
.tiny-btn {
  border: 1px solid var(--ai-border);
  background: #fff;
  border-radius: 999px;
  padding: 5px 10px;
  font-size: 12px;
  cursor: pointer;
}
.tiny-btn.primary {
  border-color: transparent;
  background: var(--ai-primary);
  color: #fff;
}
.tiny-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.detail-head {
  display: grid;
  gap: 10px;
  margin-bottom: 12px;
}
.detail-live-status {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  max-width: 100%;
  padding: 6px 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--learning-radius-sm);
  background: rgba(248, 250, 252, 0.82);
  color: var(--ai-muted);
  font-size: 12px;
  font-style: normal;
  font-weight: 850;
}
.detail-live-status::before {
  content: "";
  width: 7px;
  height: 7px;
  margin-right: 7px;
  border-radius: 999px;
  background: #059669;
  box-shadow: 0 0 0 4px rgba(5, 150, 105, 0.10);
}
.detail-live-status.is-loading::before {
  background: #0ea5e9;
  animation: pulse-chain-dot 1.15s ease-in-out infinite;
}
.detail-live-status.is-error::before {
  background: #dc2626;
  box-shadow: 0 0 0 4px rgba(220, 38, 38, 0.10);
}
.detail-kv {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}
.detail-kv span {
  display: grid;
  gap: 3px;
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: 12px;
  background: rgba(248, 250, 252, 0.78);
}
.detail-kv em {
  color: var(--ai-muted);
  font-size: 12px;
  font-style: normal;
}
.detail-kv strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-text);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-refresh-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 12px;
}
.detail-refresh-grid span {
  --detail-refresh-tone: #0284c7;
  display: grid;
  gap: 3px;
  min-width: 0;
  padding: 9px;
  border: 1px solid color-mix(in srgb, var(--detail-refresh-tone) 18%, var(--ai-border));
  border-radius: var(--learning-radius-sm);
  background: color-mix(in srgb, var(--detail-refresh-tone) 6%, var(--learning-surface));
}
.detail-refresh-grid em,
.detail-refresh-grid b {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  font-weight: 800;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-refresh-grid strong {
  min-width: 0;
  overflow: hidden;
  color: color-mix(in srgb, var(--detail-refresh-tone) 84%, var(--ai-text));
  font-size: 13px;
  font-weight: 950;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-refresh-grid .tone-good { --detail-refresh-tone: #059669; }
.detail-refresh-grid .tone-warn { --detail-refresh-tone: #d97706; }
.detail-refresh-grid .tone-bad { --detail-refresh-tone: #dc2626; }
.detail-refresh-grid .tone-info { --detail-refresh-tone: #0284c7; }
.detail-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 0 0 12px;
}
.detail-lineage {
  display: grid;
  gap: 8px;
  margin-bottom: 12px;
  padding: 10px;
  border: 1px solid rgba(37, 99, 235, 0.14);
  border-radius: 14px;
  background: rgba(59, 130, 246, 0.06);
}
.lineage-edge {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  border: 0;
  background: transparent;
  color: var(--ai-text);
  cursor: pointer;
}
.lineage-edge span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lineage-edge em {
  padding: 3px 8px;
  border-radius: 999px;
  background: #fff;
  color: var(--ai-primary);
  font-size: 11px;
  font-style: normal;
  font-weight: 700;
}
.detail-flow {
  display: grid;
  gap: 12px;
  margin-bottom: 12px;
}
.detail-flow-head {
  display: grid;
  gap: 8px;
  padding: 12px;
  border: 1px solid color-mix(in srgb, var(--ai-primary) 16%, var(--ai-border));
  border-radius: var(--learning-radius);
  background: color-mix(in srgb, var(--ai-primary) 5%, var(--learning-surface));
}
.detail-flow-head p {
  margin: 0;
  color: var(--ai-muted);
  font-size: 13px;
  line-height: 1.6;
}
.detail-production-flow {
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid color-mix(in srgb, #2563eb 18%, var(--ai-border));
  border-radius: var(--learning-radius);
  background:
    linear-gradient(180deg, color-mix(in srgb, #2563eb 5%, transparent), transparent 70%),
    var(--learning-surface);
}
.detail-production-head {
  display: grid;
  gap: 4px;
}
.detail-production-head span {
  width: fit-content;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.09);
  color: #2563eb;
  font-size: 11px;
  font-weight: 900;
}
.detail-production-head strong {
  color: var(--ai-text);
  font-size: 15px;
  font-weight: 950;
}
.detail-production-head p {
  margin: 0;
  color: var(--ai-muted);
  font-size: 12px;
  line-height: 1.55;
}
.detail-production-track {
  position: relative;
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
}
.detail-production-track::before {
  content: '';
  position: absolute;
  right: 12px;
  left: 12px;
  top: 18px;
  height: 2px;
  border-radius: 999px;
  background: linear-gradient(90deg, #2563eb, #0f766e, #059669);
  opacity: 0.22;
}
.detail-production-stage {
  --detail-stage-tone: #0284c7;
  position: relative;
  z-index: 1;
  display: grid;
  gap: 5px;
  min-width: 0;
  min-height: 104px;
  padding: 9px;
  border: 1px solid color-mix(in srgb, var(--detail-stage-tone) 16%, var(--ai-border));
  border-radius: var(--learning-radius-sm);
  background: color-mix(in srgb, var(--detail-stage-tone) 5%, #fff);
  color: inherit;
  cursor: pointer;
  text-align: left;
}
.detail-production-stage:hover,
.detail-production-stage.active {
  border-color: color-mix(in srgb, var(--detail-stage-tone) 34%, var(--ai-border));
  box-shadow: var(--ai-shadow-1);
}
.detail-production-stage.done {
  background: color-mix(in srgb, #059669 5%, #fff);
}
.detail-production-stage i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--detail-stage-tone) 12%, #fff);
  color: var(--detail-stage-tone);
  font-size: 11px;
  font-style: normal;
  font-weight: 950;
}
.detail-production-stage span {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.detail-production-stage strong,
.detail-production-stage em,
.detail-production-stage b,
.detail-production-stage small {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-production-stage strong {
  color: var(--ai-text);
  font-size: 12px;
  font-weight: 950;
}
.detail-production-stage em,
.detail-production-stage small {
  color: var(--ai-muted);
  font-size: 10px;
  font-style: normal;
  font-weight: 800;
}
.detail-production-stage b {
  color: color-mix(in srgb, var(--detail-stage-tone) 84%, var(--ai-text));
  font-size: 12px;
  font-weight: 950;
}
.detail-production-stage.tone-good,
.detail-production-stage.tone-sink { --detail-stage-tone: #059669; }
.detail-production-stage.tone-warn { --detail-stage-tone: #d97706; }
.detail-production-stage.tone-bad { --detail-stage-tone: #dc2626; }
.detail-production-stage.tone-info { --detail-stage-tone: #0284c7; }
.detail-production-stage.tone-source { --detail-stage-tone: #2563eb; }
.detail-production-stage.tone-artifact { --detail-stage-tone: #0f766e; }
.detail-terminal {
  display: grid;
  gap: 8px;
  border: 1px solid rgba(15, 23, 42, 0.16);
  border-radius: var(--learning-radius);
  background: #0f172a;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06), var(--ai-shadow-1);
  overflow: hidden;
}
.detail-terminal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(30, 41, 59, 0.82);
}
.detail-terminal-head span {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.detail-terminal-head strong {
  color: #f8fafc;
  font-size: 13px;
  font-weight: 950;
}
.detail-terminal-head em,
.detail-terminal-head b {
  color: #93c5fd;
  font-size: 11px;
  font-style: normal;
  font-weight: 800;
}
.detail-terminal-head b {
  flex: none;
  padding: 3px 7px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.18);
}
.detail-terminal-screen {
  display: grid;
  align-content: start;
  gap: 4px;
  max-height: 360px;
  min-height: 220px;
  overflow: auto;
  padding: 10px 12px 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
.detail-terminal-empty {
  padding: 28px 12px;
  color: #94a3b8;
  font-size: 12px;
  line-height: 1.7;
  text-align: center;
}
.detail-terminal-line {
  display: grid;
  grid-template-columns: 64px 54px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  min-width: 0;
  padding: 4px 0;
  color: #dbeafe;
  font-size: 11px;
  line-height: 1.55;
}
.detail-terminal-time {
  color: #64748b;
}
.detail-terminal-channel {
  width: fit-content;
  min-width: 42px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.14);
  color: #cbd5e1;
  font-weight: 950;
  text-align: center;
}
.detail-terminal-line code {
  min-width: 0;
  overflow-wrap: anywhere;
  color: #dbeafe;
  font: inherit;
  white-space: pre-wrap;
}
.detail-terminal-line em {
  grid-column: 3;
  color: #94a3b8;
  font-size: 10px;
  font-style: normal;
}
.detail-terminal-line.channel-input .detail-terminal-channel { background: rgba(37, 99, 235, 0.22); color: #bfdbfe; }
.detail-terminal-line.channel-fetch .detail-terminal-channel { background: rgba(148, 163, 184, 0.18); color: #e2e8f0; }
.detail-terminal-line.channel-process .detail-terminal-channel { background: rgba(245, 158, 11, 0.18); color: #fde68a; }
.detail-terminal-line.channel-output .detail-terminal-channel { background: rgba(16, 185, 129, 0.18); color: #bbf7d0; }
.detail-terminal-line.channel-metric .detail-terminal-channel { background: rgba(14, 165, 233, 0.18); color: #bae6fd; }
.detail-terminal-line.channel-error .detail-terminal-channel { background: rgba(239, 68, 68, 0.2); color: #fecaca; }
.detail-terminal-line.channel-error code { color: #fecaca; }
.detail-deployment-live {
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid color-mix(in srgb, #2563eb 18%, var(--ai-border));
  border-radius: var(--learning-radius);
  background:
    linear-gradient(180deg, color-mix(in srgb, #2563eb 5%, transparent), transparent 68%),
    var(--learning-surface);
}
.detail-deployment-head,
.detail-deployment-block-head {
  display: grid;
  gap: 4px;
}
.detail-deployment-head span {
  width: fit-content;
  padding: 3px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, #2563eb 10%, transparent);
  color: #1d4ed8;
  font-size: 11px;
  font-weight: 900;
}
.detail-deployment-head strong,
.detail-deployment-block-head strong {
  color: var(--ai-text);
  font-size: 14px;
  font-weight: 950;
}
.detail-deployment-head p,
.detail-deployment-block-head em {
  margin: 0;
  color: var(--ai-muted);
  font-size: 12px;
  font-style: normal;
  line-height: 1.55;
}
.detail-deployment-block {
  display: grid;
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--learning-radius-sm);
  background: rgba(255, 255, 255, 0.76);
}
.detail-deployment-list,
.detail-chat-examples,
.detail-chat-log {
  display: grid;
  gap: 7px;
}
.detail-deployment-row,
.detail-chat-examples button {
  display: grid;
  gap: 6px;
  width: 100%;
  padding: 9px;
  border: 1px solid var(--ai-border);
  border-radius: var(--learning-radius-sm);
  background: #fff;
  color: inherit;
  cursor: pointer;
  text-align: left;
}
.detail-deployment-row:hover,
.detail-chat-examples button:hover {
  border-color: color-mix(in srgb, #2563eb 24%, var(--ai-border));
  box-shadow: var(--ai-shadow-1);
}
.detail-deployment-row span,
.detail-chat-examples button,
.detail-chat-actions {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
}
.detail-deployment-row strong,
.detail-chat-examples strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-text);
  font-size: 12px;
  font-weight: 900;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-deployment-row em,
.detail-chat-examples em,
.detail-chat-message em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  font-weight: 800;
}
.detail-deployment-row p,
.detail-deployment-row b,
.detail-chat-compose p,
.detail-chat-message p {
  margin: 0;
  color: var(--ai-muted);
  font-size: 12px;
  line-height: 1.45;
}
.detail-chat-message small {
  color: #64748b;
  font-size: 11px;
  font-weight: 800;
  line-height: 1.35;
}
.detail-deployment-row b {
  min-width: 0;
  overflow: hidden;
  font-weight: 760;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-deployment-row code {
  display: block;
  min-width: 0;
  overflow: hidden;
  padding: 7px;
  border-radius: var(--learning-radius-sm);
  background: rgba(15, 23, 42, 0.04);
  color: #334155;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-chat-examples button {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.detail-chat-examples span {
  display: grid;
  gap: 3px;
  min-width: 0;
}
.detail-chat-message {
  display: grid;
  gap: 4px;
  padding: 8px;
  border-radius: var(--learning-radius-sm);
  background: rgba(15, 23, 42, 0.04);
}
.detail-chat-message.user {
  background: color-mix(in srgb, #2563eb 8%, #fff);
}
.detail-chat-message.assistant {
  background: color-mix(in srgb, #0f766e 8%, #fff);
}
.modal-chat-panel {
  display: grid;
  gap: 10px;
}
.deployment-model-context {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.deployment-model-context span {
  display: grid;
  gap: 4px;
  min-width: 0;
  padding: 9px;
  border: 1px solid var(--ai-border);
  border-radius: var(--learning-radius-sm);
  background: rgba(248, 250, 252, 0.82);
}
.deployment-model-context em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  font-weight: 800;
}
.deployment-model-context strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-text);
  font-size: 12px;
  font-weight: 900;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.modal-chat-log {
  max-height: 320px;
  overflow: auto;
  padding-right: 4px;
}
.detail-chat-compose {
  display: grid;
  gap: 8px;
}
.detail-chat-compose textarea {
  width: 100%;
  min-height: 74px;
  resize: vertical;
  padding: 9px;
  border: 1px solid var(--ai-border);
  border-radius: var(--learning-radius-sm);
  background: #fff;
  color: var(--ai-text);
  font: inherit;
  font-size: 12px;
  line-height: 1.5;
}
.detail-chat-compose textarea:focus {
  border-color: color-mix(in srgb, #2563eb 36%, var(--ai-border));
  outline: none;
  box-shadow: 0 0 0 3px color-mix(in srgb, #2563eb 12%, transparent);
}
.detail-chat-actions {
  grid-template-columns: repeat(3, minmax(0, auto));
  justify-content: start;
}
.detail-model-test-grid,
.training-dataset-head {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.detail-model-test-grid span,
.training-dataset-head span {
  display: grid;
  gap: 4px;
  min-width: 0;
  padding: 9px;
  border: 1px solid var(--ai-border);
  border-radius: var(--learning-radius-sm);
  background: rgba(248, 250, 252, 0.86);
}
.detail-model-test-grid em,
.training-dataset-head em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  font-weight: 800;
}
.detail-model-test-grid strong,
.training-dataset-head strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-text);
  font-size: 12px;
  font-weight: 900;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-model-test-note {
  margin: 0;
  color: var(--ai-muted);
  font-size: 12px;
  line-height: 1.45;
}
.training-dataset-panel,
.training-dataset-list {
  display: grid;
  gap: 10px;
}
.training-dataset-row {
  display: grid;
  gap: 7px;
  width: 100%;
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--learning-border);
  border-radius: var(--learning-radius-sm);
  background: #fff;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.training-dataset-row:hover {
  border-color: color-mix(in srgb, var(--ai-primary) 28%, var(--learning-border));
  box-shadow: var(--ai-shadow-1);
}
.training-dataset-row span {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
}
.training-dataset-row strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-text);
  font-size: 13px;
  font-weight: 900;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.training-dataset-row em,
.training-dataset-row p,
.training-dataset-row b {
  margin: 0;
  color: var(--ai-muted);
  font-size: 12px;
  font-style: normal;
  line-height: 1.45;
}
.training-dataset-row code {
  display: block;
  max-height: 72px;
  overflow: auto;
  padding: 7px;
  border-radius: var(--learning-radius-sm);
  background: rgba(15, 23, 42, 0.04);
  color: #334155;
  font-size: 11px;
  line-height: 1.45;
  white-space: pre-wrap;
}
.detail-process {
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid color-mix(in srgb, var(--ai-primary) 20%, var(--ai-border));
  border-radius: var(--learning-radius);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--ai-primary) 6%, transparent), transparent 66%),
    var(--learning-surface);
}
.detail-process-head {
  display: grid;
  gap: 4px;
}
.detail-process-head span {
  width: fit-content;
  padding: 3px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--ai-primary) 10%, transparent);
  color: color-mix(in srgb, var(--ai-primary) 86%, #0f172a);
  font-size: 11px;
  font-weight: 900;
}
.detail-process-head strong {
  color: var(--ai-text);
  font-size: 15px;
  font-weight: 950;
}
.detail-process-head p {
  margin: 0;
  color: var(--ai-muted);
  font-size: 12px;
  line-height: 1.55;
}
.detail-process-list {
  display: grid;
  gap: 9px;
}
.detail-process-card {
  display: grid;
  gap: 9px;
  width: 100%;
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--learning-radius-sm);
  background: #fff;
  color: inherit;
  cursor: pointer;
  text-align: left;
}
.detail-process-card:hover {
  border-color: color-mix(in srgb, var(--ai-primary) 26%, var(--ai-border));
  box-shadow: var(--ai-shadow-1);
}
.detail-process-title {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
}
.detail-process-title strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-text);
  font-size: 13px;
  font-weight: 950;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-process-title em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  font-weight: 850;
}
.detail-process-line {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) auto minmax(0, 1fr);
  gap: 6px;
  align-items: stretch;
}
.detail-process-line > span {
  display: grid;
  gap: 4px;
  min-width: 0;
  padding: 8px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: var(--learning-radius-sm);
  background: rgba(248, 250, 252, 0.78);
}
.detail-process-line em {
  color: var(--ai-muted);
  font-size: 10px;
  font-style: normal;
  font-weight: 900;
}
.detail-process-line strong,
.detail-process-line b {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-process-line strong {
  color: var(--ai-text);
  font-size: 11px;
  font-weight: 950;
}
.detail-process-line b {
  color: var(--ai-muted);
  font-size: 10px;
  font-weight: 750;
}
.detail-process-line i {
  align-self: center;
  color: var(--ai-primary);
  font-style: normal;
  font-weight: 950;
}
.detail-process-card small {
  color: color-mix(in srgb, var(--ai-primary) 78%, #0f172a);
  font-size: 11px;
  line-height: 1.45;
}
.detail-processing {
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid color-mix(in srgb, #0f766e 16%, var(--ai-border));
  border-radius: var(--learning-radius);
  background:
    linear-gradient(180deg, color-mix(in srgb, #0f766e 5%, transparent), transparent 68%),
    var(--learning-surface);
}
.detail-processing-head {
  display: grid;
  gap: 4px;
}
.detail-processing-head span {
  width: fit-content;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(15, 118, 110, 0.09);
  color: #0f766e;
  font-size: 11px;
  font-weight: 900;
}
.detail-processing-head strong {
  color: var(--ai-text);
  font-size: 14px;
  font-weight: 950;
}
.detail-processing-head p {
  margin: 0;
  color: var(--ai-muted);
  font-size: 12px;
  line-height: 1.55;
}
.detail-processing-steps {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.detail-processing-steps span {
  display: grid;
  gap: 3px;
  min-width: 0;
  padding: 9px;
  border: 1px solid var(--ai-border);
  border-radius: var(--learning-radius-sm);
  background: rgba(248, 250, 252, 0.78);
}
.detail-processing-steps em,
.detail-processing-row-main em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
  font-weight: 800;
}
.detail-processing-steps strong {
  color: var(--ai-text);
  font-size: 15px;
  font-weight: 950;
}
.detail-processing-steps b {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-muted);
  font-size: 11px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-processing-list {
  display: grid;
  gap: 8px;
}
.detail-processing-row {
  display: grid;
  gap: 7px;
  width: 100%;
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--learning-radius-sm);
  background: #fff;
  color: inherit;
  cursor: pointer;
  text-align: left;
}
.detail-processing-row:hover {
  border-color: color-mix(in srgb, var(--ai-primary) 24%, var(--ai-border));
  box-shadow: var(--ai-shadow-1);
}
.detail-processing-row-main,
.detail-processing-row-route {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
}
.detail-processing-row-main strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-text);
  font-size: 13px;
  font-weight: 950;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-processing-row-route {
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  color: var(--ai-muted);
  font-size: 11px;
}
.detail-processing-row-route b {
  min-width: 0;
  overflow: hidden;
  font-weight: 800;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-processing-row-route i {
  color: #0f766e;
  font-style: normal;
  font-weight: 950;
}
.detail-processing-row p,
.detail-processing-row small {
  margin: 0;
  color: var(--ai-muted);
  font-size: 12px;
  line-height: 1.45;
}
.detail-processing-row small {
  color: #0f766e;
}
.detail-processing-io {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
}
.detail-processing-io code {
  min-width: 0;
  overflow: hidden;
  padding: 7px;
  border-radius: var(--learning-radius-sm);
  background: rgba(15, 23, 42, 0.04);
  color: #334155;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-flow-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.detail-flow-summary span {
  display: grid;
  gap: 3px;
  padding: 9px 10px;
  border: 1px solid var(--learning-border);
  border-radius: var(--learning-radius-sm);
  background: var(--learning-surface-soft);
}
.detail-flow-summary em,
.detail-flow-row-head em,
.detail-flow-io em {
  color: var(--ai-muted);
  font-size: 11px;
  font-style: normal;
}
.detail-flow-summary strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-text);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-flow-section {
  display: grid;
  gap: 8px;
}
.detail-flow-section-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 4px 8px;
  align-items: start;
}
.detail-flow-section-head span {
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.detail-flow-section-head strong {
  color: var(--ai-text);
  font-size: 14px;
}
.detail-flow-section-head em {
  color: var(--ai-muted);
  font-size: 12px;
  font-style: normal;
}
.detail-flow-section-head p {
  grid-column: 1 / -1;
  margin: 0;
  color: var(--ai-muted);
  font-size: 12px;
  line-height: 1.55;
}
.detail-flow-section-head .tiny-btn {
  min-height: 26px;
  padding: 4px 8px;
}
.detail-flow-section-head small {
  grid-column: 1 / -1;
  color: var(--ai-danger);
  font-size: 12px;
}
.detail-flow-list {
  display: grid;
  gap: 8px;
}
.detail-flow-row {
  display: grid;
  gap: 7px;
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--learning-border);
  border-radius: var(--learning-radius);
  background: var(--learning-surface);
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.detail-flow-row:hover {
  border-color: color-mix(in srgb, var(--ai-primary) 32%, var(--learning-border));
  box-shadow: var(--ai-shadow-1);
}
.detail-flow-row-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
}
.detail-flow-row-head strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-text);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-flow-row-head em {
  padding: 3px 7px;
  border-radius: 999px;
  background: var(--learning-surface-soft);
  color: var(--ai-primary);
  font-weight: 800;
}
.detail-flow-row-summary,
.detail-flow-reason {
  color: var(--ai-muted);
  font-size: 12px;
  line-height: 1.5;
}
.detail-flow-route {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  padding: 7px 8px;
  border-radius: var(--learning-radius-sm);
  background: var(--learning-surface-soft);
  color: var(--ai-muted);
  font-size: 12px;
}
.detail-flow-route b {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-text);
  font-weight: 750;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-flow-route i {
  color: var(--ai-primary);
  font-style: normal;
  font-weight: 900;
}
.detail-flow-io {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.detail-flow-io span {
  display: grid;
  gap: 4px;
  min-width: 0;
}
.detail-flow-io code {
  display: block;
  min-height: 38px;
  max-height: 86px;
  overflow: auto;
  padding: 7px;
  border-radius: var(--learning-radius-sm);
  background: #0f172a;
  color: #dbeafe;
  font-size: 11px;
  line-height: 1.45;
  white-space: pre-wrap;
}
.detail-flow-empty {
  padding: 10px;
  border: 1px dashed var(--learning-border);
  border-radius: var(--learning-radius);
  background: var(--learning-surface-soft);
  color: var(--ai-muted);
  font-size: 12px;
}
.detail-json-wrap {
  display: grid;
  gap: 8px;
}
.detail-json-wrap summary {
  color: var(--ai-muted);
  cursor: pointer;
  font-size: 12px;
  font-weight: 800;
}
.detail-json {
  max-height: 70vh;
  overflow: auto;
  padding: 12px;
  border-radius: 12px;
  background: #0f172a;
  color: #dbeafe;
  font-size: 12px;
}
@media (prefers-reduced-motion: reduce) {
  .learning-page *,
  .learning-page *::before,
  .learning-page *::after {
    animation: none !important;
    scroll-behavior: auto !important;
    transition: none !important;
  }
  .global-edge-energy,
  .flow-loop-energy,
  .stream-energy,
  .material-track-hot {
    stroke-dasharray: none;
  }
  .global-edge-dot {
    display: none;
  }
}
@media (max-width: 1180px) {
  .learning-workspace {
    grid-template-areas:
      "main"
      "side";
    grid-template-columns: 1fr;
  }
  .ai-side-menu {
    position: relative;
    top: auto;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    align-items: start;
    max-height: none;
    overflow: visible;
  }
  .ai-side-scroll-progress,
  .ai-side-head,
  .ai-side-quick-controls,
  .ai-side-more-tools,
  .ai-operation-center,
  .ai-simple-priority,
  .ai-side-hud,
  .ai-mission-brief,
  .ai-side-index,
  .ai-side-context,
  .ai-focus-control,
  #ai-side-actions,
  #ai-side-panels,
  #ai-side-route,
  #ai-side-queue,
  .ai-sticky-command {
    grid-column: 1 / -1;
  }
  .ai-side-hud {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
  .ai-mission-steps {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
  .ai-mission-steps::before {
    display: none;
  }
  .ai-mission-step {
    grid-template-columns: 30px minmax(0, 1fr);
  }
  .ai-mission-step b {
    grid-column: 2;
    width: fit-content;
  }
  .ai-side-orbit {
    min-height: 132px;
  }
  .ai-sticky-command {
    position: relative;
    bottom: auto;
    margin: 0;
    border-radius: 22px;
  }
  .pulse-overview-head {
    display: grid;
  }
  .pulse-chain-head {
    display: grid;
  }
  .pulse-chain-meta {
    justify-content: flex-start;
  }
  .pulse-chain-steps {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .pulse-chain-steps::before {
    display: none;
  }
  .pulse-overview-actions {
    justify-content: flex-start;
  }
  .pulse-overview-grid,
  .pulse-overview-path,
  .pulse-automation-grid,
  .pulse-decision-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .pulse-overview-path::before {
    display: none;
  }
  .pulse-command {
    grid-template-columns: 1fr;
  }
  .pulse-radar {
    min-height: 430px;
  }
  .pulse-side {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .operation-map {
    grid-template-columns: 1fr;
  }
  .global-flow-layout,
  .run-brief-flow {
    grid-template-columns: 1fr;
  }
  .global-flow-canvas {
    min-height: 720px;
  }
  .global-flow-node {
    width: min(240px, 38vw);
  }
  .learning-toolbar,
  .learning-manifest,
  .automation-panel,
  .automation-service-grid,
  .automation-stats,
  .learning-grid,
  .learning-grid.bottom,
  .bottleneck-grid {
    grid-template-columns: 1fr;
  }
  .bottleneck-radar {
    min-height: 520px;
  }
  .bottleneck-radar-node {
    width: min(260px, 42vw);
  }
  .flow-canvas {
    min-height: 620px;
  }
  .flow-stage {
    width: min(250px, 38vw);
  }
  .manifest-beam,
  .journey-summary {
    grid-template-columns: 1fr;
  }
  .manifest-ref {
    grid-column: auto;
  }
  .manifest-core {
    justify-self: center;
  }
  .journey-step-node {
    width: min(164px, 28vw);
  }
}
@media (max-width: 760px) {
  .learning-page {
    --learning-page-x: var(--sf-spacing-lg, 16px);
    --learning-page-y: var(--sf-spacing-lg, 16px);
  }
  .ai-side-menu {
    grid-template-columns: 1fr;
    padding: var(--sf-spacing-md, 12px);
    border-radius: var(--learning-radius);
  }
  .ai-side-scroll-progress {
    top: calc(-1 * var(--sf-spacing-md, 12px));
    margin: calc(-1 * var(--sf-spacing-md, 12px)) calc(-1 * var(--sf-spacing-md, 12px)) 0;
  }
  .pulse-overview-board {
    padding: var(--sf-spacing-md, 12px);
  }
  .pulse-chain-board {
    padding: var(--sf-spacing-md, 12px);
  }
  .pulse-chain-steps {
    grid-template-columns: 1fr;
  }
  .pulse-chain-card {
    grid-template-rows: auto;
    min-height: 0;
  }
  .pulse-chain-main {
    min-height: 0;
  }
  .pulse-chain-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .ai-side-hud {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .ai-operation-status,
  .ai-operation-queue,
  .ai-operation-actions {
    grid-template-columns: 1fr;
  }
  .ai-mission-steps {
    grid-template-columns: 1fr;
  }
  .ai-mission-steps::before {
    display: block;
  }
  .ai-mission-step {
    grid-template-columns: 30px minmax(0, 1fr) auto;
  }
  .ai-mission-step b {
    grid-column: auto;
  }
  .ai-side-orbit {
    min-height: 126px;
  }
  .ai-side-index {
    top: -12px;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .pulse-overview-grid,
  .pulse-overview-path,
  .pulse-automation-grid,
  .pulse-decision-strip {
    grid-template-columns: 1fr;
  }
  .ai-side-action {
    grid-template-columns: auto minmax(0, 1fr);
  }
  .ai-side-action b {
    grid-column: 2;
    grid-row: auto;
  }
  .pulse-command {
    padding: 16px;
    border-radius: 22px;
  }
  .pulse-radar {
    min-height: 620px;
  }
  .pulse-node {
    width: min(260px, 74%);
  }
  .pulse-node:nth-of-type(2) { top: 8% !important; left: 50% !important; }
  .pulse-node:nth-of-type(3) { top: 24% !important; left: 50% !important; }
  .pulse-node:nth-of-type(4) { top: 40% !important; left: 50% !important; }
  .pulse-node:nth-of-type(5) { top: 76% !important; left: 50% !important; }
  .pulse-node:nth-of-type(6) { top: 60% !important; left: 50% !important; }
  .pulse-node:nth-of-type(7) { top: 92% !important; left: 50% !important; }
  .pulse-links {
    opacity: 0.35;
  }
  .pulse-side {
    grid-template-columns: 1fr;
  }
  .filter-dock-head,
  .run-brief-head,
  .diagnostic-switch {
    display: grid;
  }
  .global-flow-canvas {
    min-height: 1080px;
  }
  .global-flow-node {
    width: min(280px, 82vw);
    left: 50% !important;
    top: var(--global-mobile-top) !important;
  }
  .global-flow-core {
    width: 136px;
    height: 136px;
    padding: 18px;
  }
  .global-flow-core strong {
    font-size: 20px;
  }
  .global-edge-label {
    display: none;
  }
  .bottleneck-radar {
    min-height: 740px;
  }
  .bottleneck-radar-node {
    width: min(260px, 82vw);
  }
  .bottleneck-radar-node:nth-of-type(1) { top: 13% !important; left: 50% !important; }
  .bottleneck-radar-node:nth-of-type(2) { top: 30% !important; left: 50% !important; }
  .bottleneck-radar-node:nth-of-type(3) { top: 47% !important; left: 50% !important; }
  .bottleneck-radar-node:nth-of-type(4) { top: 64% !important; left: 50% !important; }
  .bottleneck-radar-node:nth-of-type(5) { top: 81% !important; left: 50% !important; }
  .bottleneck-core {
    top: 50%;
    width: 118px;
    height: 118px;
  }
  .flow-canvas {
    min-height: 880px;
  }
  .flow-stage {
    width: min(280px, 82vw);
  }
  .flow-stage:nth-of-type(1) { top: 10% !important; left: 50% !important; }
  .flow-stage:nth-of-type(2) { top: 25% !important; left: 50% !important; }
  .flow-stage:nth-of-type(3) { top: 40% !important; left: 50% !important; }
  .flow-stage:nth-of-type(4) { top: 55% !important; left: 50% !important; }
  .flow-stage:nth-of-type(5) { top: 70% !important; left: 50% !important; }
  .flow-stage:nth-of-type(6) { top: 85% !important; left: 50% !important; }
  .journey-rail {
    min-height: 520px;
  }
  .journey-rail-base,
  .journey-rail-energy {
    left: 50%;
    right: auto;
    top: 8%;
    width: 7px;
    height: 84%;
    transform: translateX(-50%);
  }
  .journey-rail-energy {
    height: var(--journey-progress-width);
  }
  .journey-cursor {
    left: 50%;
    top: var(--journey-cursor-left);
  }
  .journey-step-node {
    left: 50% !important;
    width: min(280px, 82vw);
  }
  .journey-step-node:nth-of-type(1) { top: 10% !important; }
  .journey-step-node:nth-of-type(2) { top: 28% !important; }
  .journey-step-node:nth-of-type(3) { top: 46% !important; }
  .journey-step-node:nth-of-type(4) { top: 64% !important; }
  .journey-step-node:nth-of-type(5) { top: 82% !important; }
}
</style>
