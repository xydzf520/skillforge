<template>
  <div class="training-home ai-main">
    <div class="training-pagehead ai-pagehead">
      <div>
        <div class="training-crumbs ai-crumbs">训练 · 多部门过程数据 → 模型</div>
        <h1 class="training-title ai-title">训练流水</h1>
        <p class="training-sub ai-sub">把各部门跑出的过程数据，按 入口 → 样本 → 数据集 → GB10 同步 → 自动寻路 → 训练 → 评估 → Mac236 部署 → 运行 流转</p>
      </div>
      <div class="training-head-actions">
        <button type="button" class="ai-btn" @click="router.push('/training/datasets')">
          <SfShellIcon name="database" />
          数据资产
        </button>
        <button type="button" class="ai-btn" @click="router.push('/training/models')">
          <SfShellIcon name="cube" />
          模型库
        </button>
        <button type="button" class="ai-btn" :disabled="loading" @click="() => loadTraining()">
          <SfShellIcon name="gpu" />
          GPU 状态
          <span class="ai-pill" :class="gpuStatusTone">{{ gpuStatusText }}</span>
        </button>
        <button type="button" class="ai-btn" :disabled="fullAutomationRunning" @click="runAutomation">
          <SfShellIcon name="flow" />
          {{ fullAutomationRunning ? '推进中' : '全自动推进' }}
        </button>
        <button type="button" class="ai-btn primary" @click="openCreateJobModal">
          <SfShellIcon name="plus" />
          新建训练
        </button>
      </div>
    </div>

    <div class="training-pagebody ai-pagebody">
      <!-- 流水线条 — 9 段：入口 → 样本 → 数据集 → GB10 同步 → 自动寻路 → 训练 → 评估 → Mac236 部署 → 运行 -->
      <section class="training-pipeline-card">
      <header class="pipeline-head">
        <div class="pipeline-head-left">
          <SfShellIcon name="flow" class="pipeline-head-icon" />
          <span class="pipeline-head-title">训练流水线</span>
          <span class="pipeline-head-hint">点击任一阶段查看明细</span>
        </div>
        <div class="pipeline-head-right" role="group" aria-label="训练流水线统计窗口">
          <button
            v-for="option in pipelineWindowOptions"
            :key="option.id"
            type="button"
            class="ai-pill pipeline-period-button"
            :class="{ 'pipeline-period-active': selectedPipelineWindow === option.id }"
            :aria-pressed="selectedPipelineWindow === option.id"
            @click="selectPipelineWindow(option.id)"
          >
            {{ option.label }}
          </button>
        </div>
      </header>
      <div class="pipeline-grid">
        <button
          v-for="(stage, index) in pipelineStages"
          :key="stage.id"
          type="button"
          class="pipeline-stage"
          :class="{
            'pipeline-stage-last': index === pipelineStages.length - 1,
            'pipeline-stage-selected': selectedPipelineStageId === stage.id,
          }"
          :aria-pressed="selectedPipelineStageId === stage.id"
          :aria-label="`查看${stage.label}阶段明细，${stage.sub}`"
          @click="selectPipelineStage(stage.id)"
        >
          <div class="pipeline-stage-head">
            <span
              class="pipeline-stage-num"
              :class="{ 'pipeline-stage-num-active': stage.active || selectedPipelineStageId === stage.id }"
            >
              {{ index + 1 }}
            </span>
            <span class="pipeline-stage-label">{{ stage.label }}</span>
          </div>
          <div
            class="pipeline-stage-value"
            :class="{
              'pipeline-stage-value-warn': stage.tone === 'warn',
              'pipeline-stage-value-ok': stage.tone === 'ok',
            }"
          >
            {{ stage.value }}
          </div>
          <div class="pipeline-stage-desc">{{ stage.desc }}</div>
          <div
            class="pipeline-stage-sub"
            :class="{
              'pipeline-stage-sub-warn': stage.tone === 'warn',
              'pipeline-stage-sub-ok': stage.tone === 'ok',
            }"
          >
            {{ stage.sub }}
          </div>
          <span v-if="index < pipelineStages.length - 1" class="pipeline-arrow" aria-hidden="true">›</span>
        </button>
      </div>
      <div class="pipeline-detail-panel">
        <div class="pipeline-detail-main">
          <div class="pipeline-detail-title">
            <span class="ai-pill" :class="activePipelineStageDetail.tone">{{ activePipelineStageDetail.label }}</span>
            <strong>{{ activePipelineStageDetail.title }}</strong>
            <span class="pipeline-detail-count">当前查看 · {{ activePipelineStageDetail.detailCount }}</span>
          </div>
          <div class="pipeline-lane-grid">
            <div class="pipeline-lane-node">
              <span>入口</span>
              <strong>{{ activePipelineStageDetail.entry }}</strong>
            </div>
            <div class="pipeline-lane-node">
              <span>过程</span>
              <strong>{{ activePipelineStageDetail.process }}</strong>
            </div>
            <div class="pipeline-lane-node">
              <span>结果</span>
              <strong>{{ activePipelineStageDetail.result }}</strong>
            </div>
            <div class="pipeline-lane-node">
              <span>影响</span>
              <strong>{{ activePipelineStageDetail.impact }}</strong>
            </div>
          </div>
        </div>
        <div class="pipeline-detail-side">
          <div class="pipeline-detail-metrics">
            <div v-for="metric in activePipelineStageDetail.metrics" :key="metric.label" class="pipeline-detail-metric">
              <span>{{ metric.label }}</span>
              <strong :class="metric.tone">{{ metric.value }}</strong>
            </div>
          </div>
          <div v-if="activePipelineStageDetail.blockers.length" class="pipeline-detail-blockers">
            <span
              v-for="blocker in activePipelineStageDetail.blockers"
              :key="blocker.key"
              class="automation-blocker"
              :class="blocker.severity === 'error' ? 'bad' : 'warn'"
            >
              {{ blocker.label }}
            </span>
          </div>
          <div class="pipeline-detail-actions">
            <button
              type="button"
              class="ai-btn sm primary"
              :disabled="pipelineActionDisabled(activePipelineStageDetail.primaryAction)"
              @click="runPipelineAction(activePipelineStageDetail.primaryAction)"
            >
              <SfShellIcon :name="activePipelineStageDetail.primaryIcon" />
              {{ activePipelineStageDetail.primaryLabel }}
            </button>
            <button
              v-if="activePipelineStageDetail.secondaryAction"
              type="button"
              class="ai-btn sm"
              :disabled="pipelineActionDisabled(activePipelineStageDetail.secondaryAction)"
              @click="runPipelineAction(activePipelineStageDetail.secondaryAction)"
            >
              <SfShellIcon :name="activePipelineStageDetail.secondaryIcon || 'flow'" />
              {{ activePipelineStageDetail.secondaryLabel }}
            </button>
          </div>
        </div>
      </div>
      </section>

      <section class="automation-overview ai-card">
        <header class="automation-head">
          <div>
            <div class="training-card-title">GB10 → Mac236 自动化</div>
            <div class="training-card-sub">入口、过程、结果和影响自动化的因素</div>
          </div>
          <div class="automation-head-actions">
            <span class="ai-pill" :class="automationStatus?.ok ? 'ok' : 'warn'">
              {{ automationStatus?.ok ? '可全自动' : '有阻塞' }}
            </span>
            <button type="button" class="ai-btn sm" :disabled="fullAutomationRunning" @click="runAutomation">
              <SfShellIcon name="flow" />
              {{ fullAutomationRunning ? '推进中' : '推进验证' }}
            </button>
          </div>
        </header>
        <div class="automation-route-strip">
          <div class="automation-route-node">
            <SfShellIcon name="database" />
            <strong>训练数据</strong>
            <span>{{ datasetVersionCount }} 个版本</span>
          </div>
          <div class="automation-route-arrow">›</div>
          <div class="automation-route-node" :class="{ warn: !gb10Resource?.online }">
            <SfShellIcon name="gpu" />
            <strong>GB10 237</strong>
            <span>{{ gb10Resource?.online ? '在线训练' : '离线/未登记' }}</span>
          </div>
          <div class="automation-route-arrow">›</div>
          <div class="automation-route-node">
            <SfShellIcon name="check" />
            <strong>自动评估审核</strong>
            <span>{{ automationPolicy.approve_jobs === false ? '未开启' : '已开启' }}</span>
          </div>
          <div class="automation-route-arrow">›</div>
          <div class="automation-route-node" :class="{ warn: !mac236Resource?.online }">
            <SfShellIcon name="cube" />
            <strong>Mac236</strong>
            <span>{{ mac236Resource?.online ? '内网部署' : '离线/未登记' }}</span>
          </div>
        </div>
        <div class="automation-factor-grid">
          <div
            v-for="factor in automationFactors"
            :key="factor.key"
            class="automation-factor"
            :class="factor.ok ? 'ok' : 'warn'"
          >
            <span class="automation-factor-dot"></span>
            <strong>{{ factor.label }}</strong>
            <em>{{ factor.ok ? '通过' : '阻塞' }}<template v-if="factor.pending"> · {{ factor.pending }} 待处理</template></em>
          </div>
        </div>
        <div v-if="automationBlockers.length" class="automation-blocker-row">
          <span
            v-for="blocker in automationBlockers"
            :key="blocker.key"
            class="automation-blocker"
            :class="blocker.severity === 'error' ? 'bad' : 'warn'"
          >
            {{ blocker.label }}
          </span>
        </div>
        <div v-if="automationLastRun" class="automation-output-panel">
          <div class="automation-output-main">
            <strong>最近自动化输出</strong>
            <span>{{ automationLastRun.finished_at || automationLastRun.started_at || '-' }}</span>
          </div>
          <div class="automation-output-metrics">
            <span>任务 {{ automationRunStats.jobs_changed }} / {{ automationRunStats.jobs_seen }}</span>
            <span>数据同步 {{ automationRunStats.datasets_synced }} / {{ automationRunStats.datasets_seen }}</span>
            <span :class="automationRunStats.failed ? 'bad' : 'ok'">失败 {{ automationRunStats.failed }}</span>
          </div>
        </div>
        <div v-if="simulationVerification" class="simulation-output-panel">
          <div class="simulation-output-head">
            <div>
              <strong>模拟请求已验证</strong>
              <span class="mono">{{ simulationVerification.job_id }}</span>
            </div>
            <span class="ai-pill ok">{{ simulationVerification.changed_fields.length }} 项变化</span>
          </div>
          <div class="simulation-output-grid">
            <span>网关 <b class="mono">{{ simulationVerification.gateway_id || '-' }}</b></span>
            <span>状态 <b>{{ simulationVerification.before.status || '-' }} → {{ simulationVerification.after.status || '-' }}</b></span>
            <span>评估 <b>{{ simulationVerification.after.evaluation_status || '-' }}</b></span>
            <span>部署 <b>{{ simulationVerification.after.deployment_status || '等待/阻塞' }}</b></span>
            <span class="simulation-artifact">产物 <b class="mono">{{ simulationVerification.after.artifact_uri || '-' }}</b></span>
          </div>
          <div v-if="simulationVerification.changed_fields.length" class="simulation-change-list">
            <span
              v-for="item in simulationVerification.changed_fields"
              :key="item.field"
              class="simulation-change"
            >
              {{ item.field }}: {{ displayChangeValue(item.before) }} → {{ displayChangeValue(item.after) }}
            </span>
          </div>
        </div>
      </section>

      <section class="full-history-panel ai-card">
        <header class="automation-head">
          <div>
            <div class="training-card-title">全量数据 4B 三轮微调</div>
            <div class="training-card-sub">完整历史样本 → 在线 GPU 训练 → 内网/兜底部署 → 大厅对话 Skill</div>
          </div>
          <div class="automation-head-actions">
            <span class="ai-pill" :class="fullHistoryStatusTone">{{ fullHistoryStatusLabel }}</span>
            <button type="button" class="ai-btn sm" :disabled="fullAutomationRunning" @click="runFullHistoryFinetune">
              <SfShellIcon name="flow" />
              {{ fullHistoryRunning ? '推进中' : '执行三轮' }}
            </button>
            <button type="button" class="ai-btn sm" @click="router.push('/hall/finetuned-model-chat')">
              <SfShellIcon name="bot" />
              大厅对话
            </button>
          </div>
        </header>
        <div class="full-history-route">
          <div class="automation-route-node" :class="{ warn: !fullHistoryTrainingGateway?.online }">
            <SfShellIcon name="gpu" />
            <strong>{{ fullHistoryTrainingGateway?.name || '无在线训练网关' }}</strong>
            <span>{{ fullHistoryTrainingGateway?.id || '等待接入 GPU' }}</span>
          </div>
          <div class="automation-route-node" :class="{ warn: !fullHistoryDeploymentGateway?.online }">
            <SfShellIcon name="cube" />
            <strong>{{ fullHistoryDeploymentGateway?.name || '无在线部署网关' }}</strong>
            <span>{{ fullHistoryDeploymentGateway?.id || '等待 Mac236/兜底推理' }}</span>
          </div>
          <div class="automation-route-node" :class="{ warn: !fullHistoryChat?.ready }">
            <SfShellIcon name="bot" />
            <strong>{{ fullHistoryChat?.model_id || '微调模型待部署' }}</strong>
            <span>{{ fullHistoryChat?.ready ? '可对话' : (fullHistoryChat?.disabled_reason || '等待完成三轮') }}</span>
          </div>
        </div>
        <div class="full-history-cycles">
          <div
            v-for="cycle in fullHistoryCycles"
            :key="cycle.cycle_index"
            class="full-history-cycle"
            :class="cycle.status === 'completed' ? 'ok' : (cycle.job ? 'warn' : '')"
          >
            <span>{{ cycle.cycle_index }}</span>
            <strong>第 {{ cycle.cycle_index }} 轮</strong>
            <em>{{ fullHistoryCycleText(cycle) }}</em>
          </div>
        </div>
        <div class="full-history-status-grid">
          <div>
            <span>当前步骤</span>
            <strong>{{ fullHistoryCurrentStepLabel }}</strong>
          </div>
          <div>
            <span>下一步</span>
            <strong>{{ fullHistoryNextAction || '-' }}</strong>
          </div>
          <div>
            <span>数据准备</span>
            <strong>{{ fullHistoryDataPreparationText }}</strong>
          </div>
          <div>
            <span>后台运行</span>
            <strong>{{ fullHistoryBackgroundRunning ? '运行中' : '空闲' }}</strong>
          </div>
        </div>
        <div v-if="fullHistoryLastError" class="full-history-error">
          {{ fullHistoryLastError }}
        </div>
        <div v-if="fullHistoryBlockers.length" class="automation-blocker-row full-history-blockers">
          <span
            v-for="blocker in fullHistoryBlockers"
            :key="blocker.key"
            class="automation-blocker"
            :class="blocker.severity === 'error' ? 'bad' : 'warn'"
          >
            {{ blocker.label }}
          </span>
        </div>
      </section>

      <div v-if="gatewayQueryId" class="gateway-scope-bar">
      <div class="gateway-scope-main">
        <a-tag size="small" color="green">网关上下文</a-tag>
        <div class="gateway-scope-copy">
          <strong>{{ selectedGateway?.name || gatewayQueryId }}</strong>
          <span>{{ selectedGatewayHint }}</span>
        </div>
      </div>
      <a-space size="mini">
        <a-button size="small" @click="openAgentDevice(gatewayQueryId)">
          Agent 终端
        </a-button>
        <a-button size="small" @click="clearGatewayScope">
          查看全部
        </a-button>
      </a-space>
    </div>

    <div class="training-main-grid">
      <section class="training-side-card resource-card">
        <header class="training-card-head">
          <div>
            <div class="training-card-title">部门过程数据 · {{ selectedPipelineWindowLabel }}</div>
            <div class="training-card-sub">来自训练 Agent / GPU 网关的过程数据与覆盖率</div>
          </div>
          <button type="button" class="training-link-btn" @click="router.push('/training/datasets')">
            全部 {{ departmentProcessRows.length }} 个
          </button>
        </header>
        <a-spin :loading="loading">
          <a-result v-if="loadError" status="warning" :title="loadError" />
          <table v-else-if="departmentProcessRows.length" class="training-compact-table">
            <thead>
              <tr>
                <th>部门 / 入口</th>
                <th>样本 → 数据集</th>
                <th>评估 → 部署</th>
                <th>运行</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in visibleDepartmentProcessRows" :key="row.id">
                <td>
                  <div class="dept-data-main">
                    <span>{{ row.department }}</span>
                    <a-tag v-if="row.currentStageLabel" size="small" :color="row.warn ? 'orange' : 'green'">
                      {{ row.currentStageLabel }}
                    </a-tag>
                    <a-tag v-if="row.gatewayId === gatewayQueryId" size="small" color="green">已选</a-tag>
                  </div>
                  <div class="dept-data-sub">{{ row.summary }}</div>
                  <button v-if="row.gatewayId" type="button" class="training-inline-link" @click="openAgentDevice(row.gatewayId)">
                    {{ row.name }}
                  </button>
                </td>
                <td>
                  <span class="mono-cell">{{ row.cleanText }}</span>
                  <span class="dept-data-sub">{{ row.trainText }}</span>
                </td>
                <td>
                  <span class="mono-cell">{{ row.modelText }}</span>
                  <span class="dept-data-sub">{{ row.evalText }}</span>
                </td>
                <td>
                  <span :class="row.warn ? 'fresh-muted' : 'fresh-ok'">{{ row.runText }}</span>
                  <div class="healthbar" :title="`覆盖 ${row.coverage}%`">
                    <span :style="{ width: `${row.coverage}%` }" :class="{ warn: row.warn }" />
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
          <button
            v-if="departmentProcessOverflowCount > 0"
            type="button"
            class="resource-overflow-link"
            @click="router.push('/training/datasets')"
          >
            还有 {{ departmentProcessOverflowCount }} 个数据源 · 查看全部
          </button>
          <SfEmptyState
            v-else
            title="部门过程数据"
            description="暂无可用训练资源"
            hint="训练页只展示当前账号有权访问的 Agent 终端和 GPU 能力。"
          />
        </a-spin>
      </section>

    <section class="job-card ai-card">
      <div class="job-card-body-head">
        <div>
          <div class="training-card-title">{{ jobCardTitle }}</div>
          <div class="training-card-sub">
            {{ jobCardSubtitle }}
          </div>
        </div>
        <button
          v-if="collectableJobCount"
          type="button"
          class="ai-btn sm job-extra-btn"
          :disabled="collectingDue"
          @click.stop="collectDueResults"
        >
          <SfShellIcon name="refresh" />
          {{ collectingDue ? '同步中' : '批量同步' }}
        </button>
      </div>
      <a-spin :loading="loading">
        <div v-if="selectedPipelineStageMeta" class="pipeline-filter-bar">
          <div class="pipeline-filter-main">
            <a-tag size="small" color="arcoblue">阶段 · {{ selectedPipelineStageMeta.label }}</a-tag>
            <span>{{ visibleJobRows.length }} / {{ jobRows.length }} 个任务</span>
          </div>
          <a-button size="mini" @click="clearPipelineStageFilter">清除</a-button>
        </div>
        <div v-if="visibleJobRows.length" class="training-job-list">
          <article
            v-for="record in visibleJobRows"
            :key="record.id"
            class="training-job-row"
            :class="`training-job-row-${record.status || 'unknown'}`"
          >
            <div class="training-job-top">
              <div class="training-job-title-group">
                <span v-if="record.department" class="ai-pill job-dept-pill">{{ record.department }}</span>
                <button type="button" class="training-job-title mono" @click="openTrainingJob(record.id)">
                  {{ record.title || record.id }}
                </button>
                <span class="ai-pill" :class="jobStatusPillTone(record.status)">
                  {{ jobStatusLabel(record.status) }}
                </span>
                <span
                  v-if="record.latest_deployment"
                  class="ai-pill"
                  :class="deploymentStatusPillTone(record.latest_deployment.status)"
                >
                  {{ deploymentStatusLabel(record.latest_deployment.status) }}
                </span>
              </div>
              <span class="job-id-mono">{{ record.id }}</span>
            </div>

            <div class="training-job-metrics">
              <span><em>progress</em><b class="mono">{{ jobProgressText(record) }}</b></span>
              <span><em>type</em><b class="mono">{{ jobTypeLabel(record.job_type) }}</b></span>
              <span><em>gateway</em><b class="mono">{{ jobGatewayText(record) }}</b></span>
              <span><em>ETA</em><b class="mono">{{ jobEtaText(record) }}</b></span>
            </div>

            <div class="training-job-detail-line">
              <span>{{ parameterSummaryText(record.parameters) }}</span>
              <span v-if="record.dataset_ref">数据集 {{ record.dataset_ref }}</span>
              <span v-if="record.artifact_name">产物 {{ record.artifact_name }}</span>
              <span v-if="record.gateway_route_label" class="ai-pill route-pill">
                {{ record.gateway_route_label }}
              </span>
            </div>

            <div v-if="jobAutomationSteps(record).length" class="job-automation-steps">
              <span
                v-for="step in jobAutomationSteps(record)"
                :key="`${record.id}-${step.key}`"
                class="job-auto-step"
                :class="`status-${step.status}`"
                :title="`${step.label}：${step.detail || step.status}`"
              >
                <i></i>
                {{ step.label }}
              </span>
            </div>

            <div class="training-job-progress">
              <span :style="{ width: `${jobProgressPercent(record)}%` }" />
            </div>

            <svg
              v-if="isActiveTrainingJob(record)"
              class="training-job-curve"
              viewBox="0 0 600 34"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <polyline points="0,8 60,12 120,10 180,16 240,14 300,18 360,16 420,22 480,20 540,26 600,24" />
              <polyline class="accent" points="0,2 60,6 120,8 180,7 240,12 300,10 360,15 420,13 480,18 540,16 600,21" />
              <line x1="0" y1="32" x2="600" y2="32" />
            </svg>

            <div class="training-job-actions">
              <button type="button" class="ai-btn sm" @click="openTrainingJob(record.id)">查看曲线</button>
              <button
                v-if="canApproveJob(record)"
                type="button"
                class="ai-btn primary sm"
                :disabled="Boolean(approving[record.id])"
                @click="approveJob(record.id)"
              >
                审批
              </button>
              <button
                v-if="record.status === 'queued' && record.failure_stage !== 'dispatch'"
                type="button"
                class="ai-btn sm"
                :disabled="Boolean(dispatching[record.id])"
                @click="dispatchJob(record.id)"
              >
                下发
              </button>
              <button
                v-if="record.status === 'queued' && record.failure_stage === 'dispatch'"
                type="button"
                class="ai-btn sm"
                :disabled="Boolean(dispatching[record.id])"
                @click="dispatchJob(record.id, true)"
              >
                重试
              </button>
              <button
                v-if="canEvaluateJob(record)"
                type="button"
                class="ai-btn sm"
                :disabled="Boolean(evaluating[record.id])"
                @click="evaluateJob(record.id)"
              >
                评估
              </button>
              <button
                v-if="canCollectResult(record)"
                type="button"
                class="ai-btn sm"
                :disabled="Boolean(collecting[record.id])"
                @click="collectResult(record.id)"
              >
                同步
              </button>
              <button
                v-if="canSimulateJob(record)"
                type="button"
                class="ai-btn sm"
                :disabled="Boolean(simulating[record.id])"
                @click="simulateJobResult(record)"
              >
                {{ simulating[record.id] ? '验证中' : '模拟输出并验证' }}
              </button>
              <button
                v-if="canRequestDeployment(record)"
                type="button"
                class="ai-btn sm"
                :disabled="Boolean(deploymentRequesting[record.id])"
                @click="requestDeployment(record)"
              >
                部署
              </button>
              <button
                v-if="canApproveDeployment(record)"
                type="button"
                class="ai-btn sm"
                :disabled="Boolean(deploymentOperating[record.latest_deployment?.id || ''])"
                @click="approveDeployment(record)"
              >
                审批
              </button>
              <button
                v-if="canRejectDeployment(record)"
                type="button"
                class="ai-btn sm danger"
                :disabled="Boolean(deploymentOperating[record.latest_deployment?.id || ''])"
                @click="rejectDeployment(record)"
              >
                驳回
              </button>
              <button
                v-if="canActivateDeployment(record)"
                type="button"
                class="ai-btn sm"
                :disabled="Boolean(deploymentOperating[record.latest_deployment?.id || ''])"
                @click="activateDeployment(record)"
              >
                激活
              </button>
              <button
                v-if="canRollbackDeployment(record)"
                type="button"
                class="ai-btn sm danger"
                :disabled="Boolean(deploymentOperating[record.latest_deployment?.id || ''])"
                @click="rollbackDeployment(record)"
              >
                回滚
              </button>
              <button
                v-if="record.target_gateway_id"
                type="button"
                class="ai-btn sm"
                :disabled="Boolean(logLoading[record.id])"
                @click="openJobLogs(record)"
              >
                实时日志
              </button>
              <button
                v-if="canViewArtifacts(record)"
                type="button"
                class="ai-btn sm"
                :disabled="Boolean(artifactLoading[record.id])"
                @click="openJobArtifacts(record)"
              >
                产物
              </button>
              <button
                v-if="canCancelJob(record)"
                type="button"
                class="ai-btn sm danger push-right"
                :disabled="Boolean(canceling[record.id])"
                @click="cancelJob(record.id)"
              >
                取消
              </button>
              <button
                v-if="canForceCancelJob(record)"
                type="button"
                class="ai-btn sm danger"
                :disabled="Boolean(forceCanceling[record.id])"
                @click="forceCancelJob(record.id)"
              >
                强制
              </button>
              <span v-if="!hasJobActions(record)" class="muted-text">-</span>
            </div>
          </article>
        </div>
        <SfEmptyState
          v-else-if="jobRows.length && selectedPipelineStageMeta"
          title="训练任务池"
          :description="`${selectedPipelineStageMeta.label}阶段暂无匹配任务`"
          hint="任务池仍按当前网关和权限范围加载。"
        />
        <SfEmptyState
          v-else
          title="训练任务池"
          description="暂无训练任务"
          hint="训练任务先进入待审队列，审批后再由训练网关执行。"
        />
      </a-spin>
    </section>
    </div>

    <section class="training-model-card">
      <header class="training-card-head">
        <div>
          <div class="training-card-title">评估 → Mac236 部署 → 运行</div>
          <div class="training-card-sub">训练任务产出的模型、评估门禁和部署状态</div>
        </div>
        <div class="model-card-actions">
          <button type="button" class="ai-btn sm" @click="router.push('/training/models')">模型库</button>
          <button type="button" class="ai-btn sm" @click="selectPipelineStage('eval')">评估门禁</button>
        </div>
      </header>
      <table v-if="modelEvalRows.length" class="model-eval-table">
        <thead>
          <tr>
            <th>模型</th>
            <th>部门</th>
            <th>版本</th>
            <th>训练进度</th>
            <th>评估状态</th>
            <th>部署</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in modelEvalRows" :key="row.id">
            <td>
              <span class="model-name mono-cell">{{ row.modelName }}</span>
              <span class="dept-data-sub">{{ row.jobTitle }}</span>
            </td>
            <td><span class="ai-pill model-dept-pill">{{ row.department }}</span></td>
            <td><span class="mono-cell">{{ row.version }}</span></td>
            <td>
              <div class="model-progress">
                <span class="mono-cell">{{ row.progressText }}</span>
                <div class="healthbar">
                  <span :style="{ width: `${row.progressPercent}%` }" :class="{ warn: row.warn }" />
                </div>
              </div>
            </td>
            <td>
              <span class="ai-pill" :class="row.evalTone">{{ row.evalStatus }}</span>
            </td>
            <td>
              <span class="mono-cell">{{ row.servesText }}</span>
              <span class="dept-data-sub">{{ row.lastEvalText }}</span>
            </td>
            <td class="model-actions">
              <button type="button" class="ai-btn sm" @click="openTrainingJob(row.id)">查看曲线</button>
              <button
                type="button"
                class="ai-btn sm"
                :disabled="row.deployDisabled"
                @click="requestDeployment(row.record)"
              >
                部署 / 升级
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <SfEmptyState
        v-else
        title="模型评估"
        description="暂无可展示的模型产物"
        hint="训练完成或进入部署候选后会出现在这里。"
      />
    </section>
    </div>

    <a-modal v-model:visible="logsVisible" :title="logsTitle" :footer="false" :width="'min(90vw, 720px)'">
      <a-spin :loading="logsLoadingModal">
        <pre v-if="logsText" class="logs-panel">{{ logsText }}</pre>
        <SfEmptyState v-else title="训练日志" description="暂无日志" hint="日志来自训练网关 Bridge，不包含密钥字段。" />
      </a-spin>
    </a-modal>

    <a-modal v-model:visible="artifactsVisible" :title="artifactsTitle" :footer="false" :width="'min(90vw, 780px)'">
      <a-spin :loading="artifactsLoadingModal">
        <div v-if="artifactRows.length" class="artifact-list">
          <div v-for="item in artifactRows" :key="item.id" class="artifact-item">
            <div class="artifact-main">
              <a-tag size="small" color="arcoblue">{{ item.type }}</a-tag>
              <span class="artifact-name">{{ item.name }}</span>
              <span class="muted-text">{{ formatBytes(item.size_bytes) }}</span>
            </div>
            <div class="artifact-meta">
              <span>{{ item.uri || '-' }}</span>
              <span v-if="item.sha256">sha256: {{ item.sha256.slice(0, 12) }}...</span>
              <a-tag v-if="!item.downloadable" size="small" color="gray">仅元数据</a-tag>
              <a-button
                size="mini"
                type="text"
                :disabled="!item.downloadable || !artifactJobId"
                :loading="Boolean(artifactDownloading[item.id])"
                @click="downloadArtifact(item)"
              >
                下载模型
              </a-button>
            </div>
          </div>
        </div>
        <SfEmptyState v-else title="训练产物" description="暂无产物" hint="产物页只展示脱敏 manifest，不透传预签名 URL。" />
      </a-spin>
    </a-modal>

    <a-modal
      v-model:visible="createJobVisible"
      title="新建训练任务"
      :ok-loading="creatingJob"
      :width="'min(90vw, 640px)'"
      @ok="submitCreateJob"
    >
      <a-form :model="createJobForm" layout="vertical" size="small">
        <a-form-item label="任务名称" required>
          <a-input v-model="createJobForm.title" placeholder="例如：训练商品下滑动作推荐模型" :max-length="200" />
        </a-form-item>
        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="训练类型">
              <a-select v-model="createJobForm.job_type">
                <a-option value="lora">LoRA</a-option>
                <a-option value="qlora">QLoRA</a-option>
                <a-option value="eval">评估</a-option>
                <a-option value="merge">合并</a-option>
                <a-option value="inference">推理</a-option>
              </a-select>
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="目标训练 Agent">
              <a-select v-model="createJobForm.target_gateway_id" allow-clear>
                <a-option
                  v-for="item in resourceRows"
                  :key="item.id"
                  :value="item.id"
                  :disabled="!gatewaySupportsTask(item, createJobForm.job_type)"
                >
                  {{ item.name }} · {{ item.department || '-' }}
                  <span v-if="item.route_scope === 'platform_fallback'"> · 平台训练</span>
                  <span v-if="!gatewaySupportsTask(item, createJobForm.job_type)"> · 不支持 {{ taskLabel(createJobForm.job_type) }}</span>
                </a-option>
              </a-select>
              <template #help>
                {{ createJobRouteHint }}
              </template>
            </a-form-item>
          </a-col>
        </a-row>
        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="部门" required>
              <a-input v-model="createJobForm.department" placeholder="EC" :max-length="50" />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="风险等级">
              <a-select v-model="createJobForm.risk_level">
                <a-option value="R1">R1</a-option>
                <a-option value="R2">R2</a-option>
                <a-option value="R3">R3</a-option>
                <a-option value="R4">R4</a-option>
              </a-select>
            </a-form-item>
          </a-col>
        </a-row>
        <a-form-item label="目标 Skill">
          <a-input v-model="createJobForm.target_skill_id" placeholder="skill-recommend" :max-length="50" />
        </a-form-item>
        <a-row :gutter="12">
          <a-col :span="12">
            <a-form-item label="微调模型名称">
              <a-input v-model="createJobForm.model_name" placeholder="item-decline-ranker-v2" :max-length="120" />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="基座模型">
              <a-input v-model="createJobForm.base_model" placeholder="Qwen3 / Llama / 本地模型" :max-length="120" />
            </a-form-item>
          </a-col>
        </a-row>
        <a-row :gutter="12">
          <a-col :span="8">
            <a-form-item label="Epoch">
              <a-input v-model="createJobForm.epochs" placeholder="3" />
            </a-form-item>
          </a-col>
          <a-col :span="8">
            <a-form-item label="学习率">
              <a-input v-model="createJobForm.learning_rate" placeholder="2e-4" />
            </a-form-item>
          </a-col>
          <a-col :span="8">
            <a-form-item label="预计时长(小时)">
              <a-input v-model="createJobForm.estimated_gpu_hours" placeholder="1.5" />
            </a-form-item>
          </a-col>
        </a-row>
        <a-form-item label="数据集">
          <a-input v-model="createJobForm.dataset_ref" placeholder="dataset://ec/actions/v1" :max-length="200" />
        </a-form-item>
        <a-form-item label="训练目标">
          <a-textarea v-model="createJobForm.objective" :auto-size="{ minRows: 3, maxRows: 5 }" :max-length="4000" />
        </a-form-item>
      </a-form>
    </a-modal>
    <TrainingDeploymentRejectModal
      v-model:visible="rejectModalVisible"
      :loading="Boolean(rejectTarget?.latest_deployment?.id && deploymentOperating[rejectTarget.latest_deployment.id])"
      @submit="submitRejectDeployment"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { LocationQueryRaw } from 'vue-router'
import { Message, Modal } from '@arco-design/web-vue'
import { trainingApi as rawTrainingApi } from '@/api'
import { SfEmptyState } from '@/components/common'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
import TrainingDeploymentRejectModal from './TrainingDeploymentRejectModal.vue'

defineOptions({ name: 'TrainingHome' })

type GpuInfo = {
  name?: string
  vram_total_mb?: number
  vram_used_mb?: number
  vram_free_mb?: number
  gpu_util_pct?: number
}

type ResourceActiveJob = {
  id: string
  title: string
  status: string
  job_type: string
  target_skill_id: string
  updated_at: string
}

type ResourceRow = {
  id: string
  name: string
  department: string
  online: boolean
  gateway_kind: string
  gpu_count: number
  idle_gpu_count: number
  busy_gpu_count: number
  vram_total_gb: number
  vram_free_gb: number
  vram_percent: number
  supported_tasks: string[]
  active_training_jobs: ResourceActiveJob[]
  active_training_jobs_count: number
  training_ready: boolean
  route_scope: 'department' | 'platform_fallback' | 'external'
}

type TrainingJobRow = {
  id: string
  title: string
  department: string
  status: string
  job_type: string
  model_name: string
  base_model: string
  target_skill_id: string
  target_gateway_id: string
  dataset_ref: string
  parameters: Record<string, unknown>
  progress: number
  eta_text: string
  estimated_duration_seconds: number
  artifact_name: string
  gateway_route_label: string
  gateway_route_color: string
  failure_stage: string
  automation_status: TrainingJobAutomationStatus | null
  created_at: string
  latest_deployment: {
    id: string
    status: string
  } | null
}

type AutomationStep = {
  key: string
  label: string
  status: string
  detail: string
  [key: string]: unknown
}

type AutomationBlocker = {
  key: string
  label: string
  severity: string
  [key: string]: unknown
}

type TrainingJobAutomationStatus = {
  status: string
  steps: AutomationStep[]
  blockers: AutomationBlocker[]
  auto_approval?: Record<string, unknown>
}

type TrainingAutomationFactor = {
  key: string
  label: string
  ok: boolean
  pending?: number
  node_id?: string
}

type TrainingAutomationStatus = {
  ok: boolean
  policy: Record<string, unknown>
  factors: TrainingAutomationFactor[]
  blockers: AutomationBlocker[]
  summary: Record<string, unknown>
  department_flow?: TrainingDepartmentFlow | null
  nodes: Record<string, unknown>
  last_run: Record<string, unknown> | null
}

type PipelineStageId = 'data' | 'clean' | 'dataset' | 'gb10_sync' | 'route' | 'train' | 'eval' | 'deploy' | 'run'
type PipelineWindowId = 'day' | 'week' | 'month'

const PIPELINE_STAGE_IDS: readonly PipelineStageId[] = ['data', 'clean', 'dataset', 'gb10_sync', 'route', 'train', 'eval', 'deploy', 'run']
const PIPELINE_STAGE_ALIASES: Record<string, PipelineStageId> = {
  entry: 'data',
  sample: 'clean',
  samples: 'clean',
  dataset: 'dataset',
  datasets: 'dataset',
  sync: 'gb10_sync',
  gb10: 'gb10_sync',
  gb10_sync: 'gb10_sync',
  route: 'route',
  routing: 'route',
  auto_route: 'route',
  training: 'train',
  deployment: 'deploy',
  deploy: 'deploy',
  mac236: 'deploy',
  mac236_deploy: 'deploy',
  model: 'deploy',
  models: 'deploy',
}
const PIPELINE_WINDOW_OPTIONS: Array<{ id: PipelineWindowId; label: string; days: number }> = [
  { id: 'day', label: '本日', days: 1 },
  { id: 'week', label: '本周', days: 7 },
  { id: 'month', label: '本月', days: 30 },
]
const DEFAULT_PIPELINE_WINDOW_OPTION = PIPELINE_WINDOW_OPTIONS[1]!
const PIPELINE_WINDOW_ALIASES: Record<string, PipelineWindowId> = {
  day: 'day',
  today: 'day',
  daily: 'day',
  d: 'day',
  '1': 'day',
  '1d': 'day',
  week: 'week',
  weekly: 'week',
  w: 'week',
  '7': 'week',
  '7d': 'week',
  month: 'month',
  monthly: 'month',
  m: 'month',
  '30': 'month',
  '30d': 'month',
}
const FULL_HISTORY_CHAT_SKILL_ID = 'skillforge-finetuned-model-chat'
const FULL_HISTORY_MODEL_PROFILE = 'qwen3.5-4b'
const FULL_HISTORY_MODEL_FAMILY = 'skillforge-full-history-4b-chat-adapter'
const FULL_HISTORY_DATASET_REF = 'learning-artifacts://platform/training/full-history/latest'
const FULL_HISTORY_TRAINING_GATEWAY_ID = 'training-primary'
const FULL_HISTORY_DEPLOYMENT_GATEWAY_ID = 'inference-primary'
const FULL_HISTORY_DEPLOYMENT_RUNTIME_PROFILE = 'mlx-qwen3.5-4b-lora'

type ArtifactRow = {
  id: string
  type: string
  name: string
  uri: string
  sha256: string
  size_bytes: number
  downloadable: boolean
  download_url: string
}

type SimulationChange = {
  field: string
  before: unknown
  after: unknown
}

type SimulationVerification = {
  job_id: string
  gateway_id: string
  before: Record<string, unknown>
  after: Record<string, unknown>
  changed_fields: SimulationChange[]
}

const trainingApi: any = rawTrainingApi
const router = useRouter()
const route = useRoute()
const loading = ref(false)
const loadError = ref('')
const resources = ref<ResourceRow[]>([])
const automationStatus = ref<TrainingAutomationStatus | null>(null)
const fullHistoryStatus = ref<Record<string, any> | null>(null)
const resourceContextDepartment = ref('')
const jobs = ref<TrainingJobRow[]>([])
const approving = ref<Record<string, boolean>>({})
const dispatching = ref<Record<string, boolean>>({})
const evaluating = ref<Record<string, boolean>>({})
const collecting = ref<Record<string, boolean>>({})
const collectingDue = ref(false)
const automationRunning = ref(false)
const fullHistoryRunning = ref(false)
const fullHistoryPollTimer = ref<number | null>(null)
const simulating = ref<Record<string, boolean>>({})
const simulationVerification = ref<SimulationVerification | null>(null)
const deploymentRequesting = ref<Record<string, boolean>>({})
const deploymentOperating = ref<Record<string, boolean>>({})
const canceling = ref<Record<string, boolean>>({})
const forceCanceling = ref<Record<string, boolean>>({})
const logLoading = ref<Record<string, boolean>>({})
const artifactLoading = ref<Record<string, boolean>>({})
const logsVisible = ref(false)
const logsLoadingModal = ref(false)
const logsTitle = ref('训练日志')
const logsText = ref('')
const artifactsVisible = ref(false)
const artifactsLoadingModal = ref(false)
const artifactsTitle = ref('训练产物')
const artifactRows = ref<ArtifactRow[]>([])
const artifactJobId = ref('')
const artifactDownloading = ref<Record<string, boolean>>({})
const createJobVisible = ref(false)
const creatingJob = ref(false)
const rejectTarget = ref<TrainingJobRow | null>(null)
const createJobForm = ref({
  title: '',
  department: '',
  job_type: 'lora',
  training_strategy: 'lora_task_parallel',
  target_gateway_id: '',
  target_skill_id: '',
  model_name: '',
  base_model: '',
  epochs: '',
  learning_rate: '',
  estimated_gpu_hours: '',
  dataset_ref: '',
  objective: '',
  risk_level: 'R2',
})

const rejectModalVisible = computed({
  get: () => Boolean(rejectTarget.value),
  set: (value: boolean) => {
    if (!value) rejectTarget.value = null
  },
})

const gatewayQueryId = computed(() => routeQueryString(route.query?.gateway).trim())
const selectedGateway = computed(() => resources.value.find(item => item.id === gatewayQueryId.value) || null)
const selectedPipelineStageId = computed<PipelineStageId | ''>(() => normalizePipelineStageId(route.query?.stage))
const pipelineWindowOptions = PIPELINE_WINDOW_OPTIONS
const selectedPipelineWindow = computed<PipelineWindowId>(() => normalizePipelineWindowId(route.query?.window))
const selectedPipelineWindowOption = computed(() =>
  pipelineWindowOptions.find(item => item.id === selectedPipelineWindow.value) || DEFAULT_PIPELINE_WINDOW_OPTION)
const selectedPipelineWindowLabel = computed(() => selectedPipelineWindowOption.value.label)
const pipelineWindowDays = computed(() => selectedPipelineWindowOption.value.days)
const jobCardTitle = computed(() => gatewayQueryId.value ? '训练任务 · 当前网关' : '训练任务 · 进行中')
const jobCardSubtitle = computed(() => {
  const prefix = gatewayQueryId.value ? '训练任务池 · 当前网关' : '训练任务池'
  return `${prefix} · ${runningJobCount.value} 进行 / ${jobRows.value.length} 共`
})
const jobListParams = computed(() => gatewayQueryId.value ? { target_gateway_id: gatewayQueryId.value } : undefined)
const selectedGatewayHint = computed(() => {
  const gateway = selectedGateway.value
  if (!gateway) return `任务池已按 ${gatewayQueryId.value} 收敛`
  const status = gateway.online ? '在线' : '离线'
  return `${gateway.department || '-'} · ${status} · GPU ${gateway.gpu_count} · 当前任务 ${gateway.active_training_jobs_count}`
})

const resourceRows = computed<ResourceRow[]>(() => {
  const selected = gatewayQueryId.value
  if (!selected) return resources.value
  return [...resources.value].sort((left, right) => {
    if (left.id === selected) return -1
    if (right.id === selected) return 1
    return 0
  })
})
const totalGpuCount = computed(() => resourceRows.value.reduce((sum, item) => sum + safeNumber(item.gpu_count), 0))
const idleGpuCount = computed(() => resourceRows.value.reduce((sum, item) => sum + safeNumber(item.idle_gpu_count), 0))
const gpuStatusText = computed(() => `${idleGpuCount.value} / ${totalGpuCount.value}`)
const gpuStatusTone = computed(() => {
  if (idleGpuCount.value > 0) return 'ok'
  if (totalGpuCount.value > 0) return 'warn'
  return ''
})

// 旧 KPI 卡（SfKpiCard 行）的 7 个 computed 在迭代 8 流水线 strip 重构后已不再使用，已清理。
const jobRows = computed<TrainingJobRow[]>(() => jobs.value)
const visibleJobRows = computed<TrainingJobRow[]>(() => {
  const stageId = selectedPipelineStageId.value
  if (!stageId) return jobRows.value
  return jobRows.value.filter(item => matchesPipelineStage(item, stageId))
})
const awaitingJobCount = computed(() => jobRows.value.filter(item => item.status === 'awaiting_review').length)
const activeJobCount = computed(() => jobRows.value.filter(item => ['queued', 'running', 'evaluating'].includes(item.status)).length)
const queuedJobCount = computed(() => jobRows.value.filter(item => item.status === 'queued').length)
const collectableJobCount = computed(() => jobRows.value.filter(item => canCollectResult(item)).length)
const runningJobCount = computed(() => jobRows.value.filter(item => ['running', 'evaluating'].includes(item.status)).length)
const completedJobCount = computed(() => jobRows.value.filter(item => item.status === 'completed').length)
const failedJobCount = computed(() => jobRows.value.filter(item => item.status === 'failed').length)
const deployedJobCount = computed(() => jobRows.value.filter(item => item.latest_deployment?.status === 'active').length)
const canaryJobCount = computed(() => jobRows.value.filter(item => item.latest_deployment?.status === 'canary').length)
const onlineGatewayCount = computed(() => resourceRows.value.filter(item => item.online).length)
const automationFactors = computed(() => normalizeList<TrainingAutomationFactor>(automationStatus.value?.factors))
const automationBlockers = computed(() => normalizeList<AutomationBlocker>(automationStatus.value?.blockers))
const automationPolicy = computed(() => automationStatus.value?.policy || {})
const automationSummary = computed(() => automationStatus.value?.summary || {})
const departmentFlow = computed<TrainingDepartmentFlow>(() => {
  const flow = automationStatus.value?.department_flow
  return flow && typeof flow === 'object' ? flow : {}
})
const departmentFlowSummary = computed(() =>
  departmentFlow.value.summary && typeof departmentFlow.value.summary === 'object'
    ? departmentFlow.value.summary as Record<string, unknown>
    : {})
const departmentFlowStageCounts = computed(() => {
  const counts = departmentFlowSummary.value.stage_counts
  return counts && typeof counts === 'object' ? counts as Record<string, unknown> : {}
})
function pipelineStageCount(stageId: PipelineStageId, fallback: number): number {
  const counts = departmentFlowStageCounts.value
  if (Object.prototype.hasOwnProperty.call(counts, stageId)) return safeNumber(counts[stageId])
  return fallback
}
function pipelineEntrySourceText(metrics: Record<string, unknown>): string {
  const sdk = safeNumber(metrics.entry_sdk)
  const learning = safeNumber(metrics.entry_learning) || safeNumber(metrics.artifact_total)
  const manual = safeNumber(metrics.entry_manual)
  return `${formatCompactCount(sdk)} SDK / ${formatCompactCount(learning)} 学习闭环 / ${formatCompactCount(manual)} 人工`
}
function pipelineEntryTaskDatasetText(metrics: Record<string, unknown>): string {
  const tasks = safeNumber(metrics.entry_tasks) || safeNumber(metrics.jobs_total)
  const datasets = safeNumber(metrics.entry_datasets) || safeNumber(metrics.dataset_versions)
  return `${formatCompactCount(tasks)} 任务 / ${formatCompactCount(datasets)} 数据集`
}
function pipelineDatasetStatusText(metrics: Record<string, unknown>, fallbackVersions = 0): string {
  const versions = safeNumber(metrics.dataset_versions) || fallbackVersions
  const synced = safeNumber(metrics.datasets_synced)
  return `${formatCompactCount(versions)} 版本化 manifest / ${formatCompactCount(synced)} 已同步`
}
function pipelineDatasetUnsyncedCount(metrics: Record<string, unknown>, fallbackUnsynced = 0): number {
  if (Object.prototype.hasOwnProperty.call(metrics, 'datasets_unsynced')) {
    return safeNumber(metrics.datasets_unsynced)
  }
  const versions = safeNumber(metrics.dataset_versions)
  const synced = safeNumber(metrics.datasets_synced)
  if (versions || synced) return Math.max(0, versions - synced)
  return fallbackUnsynced
}
function pipelineDatasetPendingText(metrics: Record<string, unknown>, fallbackUnsynced = 0): string {
  return `${formatCompactCount(pipelineDatasetUnsyncedCount(metrics, fallbackUnsynced))} 待同步`
}
function pipelineGb10SyncText(metrics: Record<string, unknown>, fallbackSynced = 0): string {
  const synced = safeNumber(metrics.datasets_synced) || fallbackSynced
  const unsynced = pipelineDatasetUnsyncedCount(metrics, unsyncedDatasetCount.value)
  return `${formatCompactCount(synced)} 同步到 237 / ${formatCompactCount(unsynced)} 待同步`
}
function pipelineSampleModalityCounts(metrics: Record<string, unknown>, fallbackText = 0) {
  const text = safeNumber(metrics.sample_text)
  const image = safeNumber(metrics.sample_image)
  const imageText = safeNumber(metrics.sample_image_text)
  if (text + image + imageText <= 0 && fallbackText > 0) {
    return { text: fallbackText, image: 0, imageText: 0 }
  }
  return { text, image, imageText }
}
function pipelineSampleModalityText(metrics: Record<string, unknown>, fallbackText = 0): string {
  const counts = pipelineSampleModalityCounts(metrics, fallbackText)
  return `${formatCompactCount(counts.text)} 文字 / ${formatCompactCount(counts.image)} 图片 / ${formatCompactCount(counts.imageText)} 图文`
}
function pipelineTrainingStatusCounts(
  metrics: Record<string, unknown>,
  fallback: { awaiting: number; queued: number; training: number },
) {
  const hasMetrics = ['jobs_awaiting', 'jobs_queued', 'jobs_training', 'jobs_evaluating'].some(key =>
    Object.prototype.hasOwnProperty.call(metrics, key),
  )
  if (!hasMetrics) return fallback
  const awaiting = safeNumber(metrics.jobs_awaiting)
  const queued = safeNumber(metrics.jobs_queued)
  const training = Math.max(
    safeNumber(metrics.jobs_training) + safeNumber(metrics.jobs_evaluating),
    safeNumber(metrics.jobs_running) - queued,
  )
  return { awaiting, queued, training }
}
function pipelineTrainingStatusText(
  metrics: Record<string, unknown>,
  fallback: { awaiting: number; queued: number; training: number },
): string {
  const counts = pipelineTrainingStatusCounts(metrics, fallback)
  return `${formatCompactCount(counts.awaiting)} 待审 / ${formatCompactCount(counts.queued)} 队列 / ${formatCompactCount(counts.training)} 训练中`
}
function pipelineTrainingReviewText(
  metrics: Record<string, unknown>,
  fallback: { awaiting: number; queued: number; training: number },
): string {
  const counts = pipelineTrainingStatusCounts(metrics, fallback)
  return `${formatCompactCount(counts.awaiting)} 自动审核 / ${formatCompactCount(counts.training)} 训练中`
}
function pipelineTrainingInflightCount(
  metrics: Record<string, unknown>,
  fallback: { awaiting: number; queued: number; training: number },
): number {
  const counts = pipelineTrainingStatusCounts(metrics, fallback)
  return counts.awaiting + counts.queued + counts.training
}
function pipelineGb10RouteCount(metrics: Record<string, unknown>, fallback = 0): number {
  if (Object.prototype.hasOwnProperty.call(metrics, 'jobs_target_gb10')) {
    return safeNumber(metrics.jobs_target_gb10)
  }
  return fallback
}
function pipelineAutoRouteText(metrics: Record<string, unknown>, fallbackGb10 = 0): string {
  const gb10 = pipelineGb10RouteCount(metrics, fallbackGb10)
  const fallback = safeNumber(metrics.jobs_target_fallback)
  const unrouted = safeNumber(metrics.jobs_unrouted)
  return `${formatCompactCount(gb10)} 指向 GB10 / ${formatCompactCount(fallback)} 兜底 GPU / ${formatCompactCount(unrouted)} 待寻路`
}
const departmentFlowItems = computed(() => normalizeList<TrainingDepartmentFlowItem>(departmentFlow.value.departments))
const automationLastRun = computed(() => automationStatus.value?.last_run || null)
const fullHistoryCycles = computed(() => normalizeList<Record<string, any>>(fullHistoryStatus.value?.cycles))
const fullHistoryBlockers = computed(() => normalizeList<AutomationBlocker>(fullHistoryStatus.value?.blockers))
const fullHistorySelected = computed(() => fullHistoryStatus.value?.selected || {})
const fullHistoryTrainingGateway = computed(() => fullHistorySelected.value?.training_gateway || null)
const fullHistoryDeploymentGateway = computed(() => fullHistorySelected.value?.deployment_gateway || null)
const fullHistoryChat = computed(() => fullHistoryStatus.value?.chat || null)
const fullHistoryAutomationRun = computed(() => fullHistoryStatus.value?.automation_run || null)
const fullHistoryBackgroundRunning = computed(() => Boolean(fullHistoryStatus.value?.background_running))
const fullHistoryCompletedCycles = computed(() => safeNumber(fullHistoryStatus.value?.completed_cycles))
const fullHistoryCycleCount = computed(() => safeNumber(fullHistoryStatus.value?.cycle_count || 3))
const fullHistoryStatusLabel = computed(() => {
  if (fullHistoryStatus.value?.ok) return '可对话'
  if (fullHistoryBlockers.value.some(item => item.severity === 'error')) return '阻塞'
  if (fullHistoryCompletedCycles.value > 0) return `${fullHistoryCompletedCycles.value}/${fullHistoryCycleCount.value}`
  return '待启动'
})
const fullHistoryStatusTone = computed(() => {
  if (fullHistoryStatus.value?.ok) return 'ok'
  if (fullHistoryBlockers.value.some(item => item.severity === 'error')) return 'bad'
  return 'warn'
})
const fullHistoryCurrentStep = computed(() =>
  String(fullHistoryAutomationRun.value?.current_step || fullHistoryStatus.value?.current_step || '').trim())
const fullHistoryCurrentStepLabel = computed(() => fullHistoryStepLabel(fullHistoryCurrentStep.value))
const fullHistoryNextAction = computed(() => String(fullHistoryStatus.value?.next_action || '').trim())
const fullHistoryLastError = computed(() =>
  String(fullHistoryStatus.value?.last_error || fullHistoryAutomationRun.value?.error || '').trim())
const fullHistoryDataPreparation = computed(() => {
  const fromStatus = fullHistoryStatus.value?.data_preparation
  if (fromStatus && typeof fromStatus === 'object') return fromStatus as Record<string, any>
  const fromRun = fullHistoryAutomationRun.value?.data_preparation
  if (fromRun && typeof fromRun === 'object') return fromRun as Record<string, any>
  return {}
})
const fullHistorySampleSummary = computed(() => {
  const summary = fullHistoryStatus.value?.sample_summary
  return summary && typeof summary === 'object' ? summary as Record<string, any> : {}
})
const fullHistoryDataPreparationText = computed(() => {
  const prep = fullHistoryDataPreparation.value
  const status = String(prep.status || '').trim()
  const materialized = safeNumber(prep.materialized?.materialized ?? prep.materialized_count)
  const captured = safeNumber(prep.captured?.total_captured ?? prep.captured_count)
  if (status === 'completed') return `已准备 · 捕获 ${captured} / 物化 ${materialized}`
  if (status === 'deferred_capture') return `训练优先 · 物化 ${materialized}`
  if (status) return status
  const summary = fullHistorySampleSummary.value
  const sampleTotal = safeNumber(summary.sample_total || summary.latest_cycle_sample_total)
  const evalCount = safeNumber(summary.eval_count || summary.latest_cycle_eval_count)
  if (sampleTotal) return `历史样本 ${sampleTotal} · 评测 ${evalCount}`
  return '等待'
})
const fullHistoryShouldPoll = computed(() =>
  Boolean(fullHistoryBackgroundRunning.value || fullHistoryRunning.value || (
    fullHistoryStatus.value?.ready_to_run &&
    !fullHistoryStatus.value?.ok &&
    ['queued', 'running', 'blocked'].includes(String(fullHistoryAutomationRun.value?.status || ''))
  )))
const fullAutomationRunning = computed(() => automationRunning.value || fullHistoryRunning.value)
const automationRunStats = computed(() => {
  const stats = automationLastRun.value?.stats && typeof automationLastRun.value.stats === 'object'
    ? automationLastRun.value.stats as Record<string, unknown>
    : {}
  return {
    datasets_seen: safeNumber(stats.datasets_seen),
    datasets_synced: safeNumber(stats.datasets_synced),
    jobs_seen: safeNumber(stats.jobs_seen),
    jobs_changed: safeNumber(stats.jobs_changed),
    failed: safeNumber(stats.failed),
  }
})
const gb10Resource = computed(() => resourceRows.value.find(item => item.id === 'training-primary') || null)
const mac236Resource = computed(() => resourceRows.value.find(item => item.id === 'inference-primary') || null)
const datasetVersionCount = computed(() => safeNumber(automationSummary.value.dataset_versions))
const unsyncedDatasetCount = computed(() => safeNumber(automationSummary.value.dataset_unsynced))
const routeReadyJobCount = computed(() => jobRows.value.filter(item => item.target_gateway_id === FULL_HISTORY_TRAINING_GATEWAY_ID).length)
const evaluatedJobCount = computed(() => jobRows.value.filter(item => hasAutomationStepStatus(item, 'evaluate', 'completed')).length)

type PipelineStage = {
  id: PipelineStageId
  label: string
  desc: string
  value: string | number
  sub: string
  tone?: 'ok' | 'warn'
  active?: boolean
}

type PipelineAction = 'datasets' | 'models' | 'run_auto' | 'create_job' | 'hall_chat'

type PipelineStageDetail = {
  id: PipelineStageId
  label: string
  title: string
  detailCount: string
  entry: string
  process: string
  result: string
  impact: string
  tone?: 'ok' | 'warn' | 'bad'
  metrics: Array<{ label: string; value: string; tone?: 'ok' | 'warn' | 'bad' }>
  blockers: AutomationBlocker[]
  primaryAction: PipelineAction
  primaryLabel: string
  primaryIcon: string
  secondaryAction?: PipelineAction
  secondaryLabel?: string
  secondaryIcon?: string
}

type TrainingDepartmentFlowStage = {
  key: PipelineStageId | string
  label: string
  count: number
  status: string
  detail: string
}

type TrainingDepartmentFlowItem = {
  id: string
  department: string
  metrics: Record<string, unknown>
  stages: TrainingDepartmentFlowStage[]
  current_stage: PipelineStageId | string
  current_stage_label: string
  coverage: number
  latest_at?: string | null
  blockers?: AutomationBlocker[]
}

type TrainingDepartmentFlow = {
  window_days?: number
  stage_order?: Array<{ key: string; label: string }>
  summary?: Record<string, unknown>
  departments?: TrainingDepartmentFlowItem[]
}

type DepartmentProcessRow = {
  id: string
  name: string
  department: string
  summary: string
  cleanText: string
  trainText: string
  modelText: string
  evalText: string
  runText: string
  currentStageLabel: string
  coverage: number
  warn: boolean
  gatewayId: string
}

type ModelEvalRow = {
  id: string
  record: TrainingJobRow
  modelName: string
  jobTitle: string
  department: string
  version: string
  progressText: string
  progressPercent: number
  evalStatus: string
  evalTone: string
  servesText: string
  lastEvalText: string
  deployDisabled: boolean
  warn: boolean
}

const pipelineStages = computed<PipelineStage[]>(() => {
  const cleanedSamples = safeNumber(departmentFlowSummary.value.cleaned_samples)
  const rawEvents = safeNumber(departmentFlowSummary.value.raw_events)
  const artifactTotal = safeNumber(departmentFlowSummary.value.artifact_total)
  const gb10RouteCount = pipelineStageCount(
    'route',
    pipelineGb10RouteCount(departmentFlowSummary.value, routeReadyJobCount.value),
  )
  const evalCompleted = pipelineStageCount('eval', evaluatedJobCount.value)
  const deployCount = pipelineStageCount('deploy', canaryJobCount.value + deployedJobCount.value)
  const runCount = pipelineStageCount('run', deployedJobCount.value)
  const deploymentActiveCount = safeNumber(departmentFlowSummary.value.deployments_active) || runCount
  const deploymentCanaryCount = safeNumber(departmentFlowSummary.value.deployments_canary) || canaryJobCount.value
  const dataCount = pipelineStageCount('data', safeNumber(fullHistorySampleSummary.value.sample_total) || datasetVersionCount.value)
  const cleanCount = pipelineStageCount('clean', cleanedSamples || safeNumber(fullHistorySampleSummary.value.training_sample_count))
  const datasetCount = pipelineStageCount('dataset', datasetVersionCount.value)
  const gb10SyncCount = pipelineStageCount('gb10_sync', safeNumber(departmentFlowSummary.value.datasets_synced))
  const entrySourceText = pipelineEntrySourceText(departmentFlowSummary.value)
  const datasetPendingText = pipelineDatasetPendingText(departmentFlowSummary.value, unsyncedDatasetCount.value)
  const trainStatusCounts = pipelineTrainingStatusCounts(departmentFlowSummary.value, {
    awaiting: awaitingJobCount.value,
    queued: queuedJobCount.value,
    training: runningJobCount.value,
  })
  const trainingInflightCount = pipelineStageCount(
    'train',
    pipelineTrainingInflightCount(departmentFlowSummary.value, trainStatusCounts),
  )
  const trainingReviewText = pipelineTrainingReviewText(departmentFlowSummary.value, trainStatusCounts)
  const evalFailed = safeNumber(departmentFlowSummary.value.eval_failed)
  return [
    {
      id: 'data',
      label: '入口',
      desc: '部门过程与历史样本',
      value: formatCompactCount(dataCount),
      sub: dataCount > 0 ? entrySourceText : `${artifactTotal || rawEvents} 样本`,
      tone: dataCount > 0 ? undefined : 'warn',
    },
    {
      id: 'clean',
      label: '样本',
      desc: '文字 / 图片 / 图文',
      value: formatCompactCount(cleanCount),
      sub: '训练样本进入资产目录',
      tone: cleanCount > 0 ? undefined : 'warn',
    },
    {
      id: 'dataset',
      label: '数据集',
      desc: '版本化 manifest',
      value: formatCompactCount(datasetCount),
      sub: datasetPendingText,
      tone: datasetCount > 0 ? undefined : 'warn',
    },
    {
      id: 'gb10_sync',
      label: 'GB10 同步',
      desc: '全部数据同步到 237',
      value: formatCompactCount(gb10SyncCount),
      sub: gb10Resource.value?.online ? `${formatCompactCount(gb10SyncCount)} 已同步到 237` : 'GB10 未在线',
      tone: gb10SyncCount > 0 ? undefined : 'warn',
    },
    {
      id: 'route',
      label: '自动寻路',
      desc: 'GB10 237 强优先',
      value: formatCompactCount(gb10RouteCount),
      sub: `${formatCompactCount(gb10RouteCount)} 指向 GB10`,
      tone: gb10RouteCount > 0 ? undefined : 'warn',
    },
    {
      id: 'train',
      label: '训练',
      desc: `${formatCompactCount(trainingInflightCount)} 任务进行中`,
      value: formatCompactCount(trainingInflightCount),
      sub: trainingReviewText,
      active: trainStatusCounts.queued > 0 || trainStatusCounts.training > 0,
      tone: trainingInflightCount > 0 ? undefined : 'warn',
    },
    {
      id: 'eval',
      label: '评估',
      desc: '自动评估与门禁',
      value: evalCompleted,
      sub: evalFailed > 0 ? `${evalFailed} 失败` : `${evalCompleted} 已评估`,
      tone: evalFailed > 0 ? 'warn' : undefined,
    },
    {
      id: 'deploy',
      label: 'Mac236 部署',
      desc: '内网自动部署',
      value: formatCompactCount(deployCount),
      sub: mac236Resource.value?.online
        ? `${formatCompactCount(deployCount)} 部署产物 / ${formatCompactCount(deploymentActiveCount)} active`
        : 'Mac236 未在线',
      tone: deployCount > 0 ? undefined : 'warn',
    },
    {
      id: 'run',
      label: '运行',
      desc: 'Mac236 / 兜底推理',
      value: runCount,
      sub: mac236Resource.value?.online
        ? `${formatCompactCount(deploymentActiveCount)} active / ${formatCompactCount(deploymentCanaryCount)} 灰度`
        : 'Mac236 未在线',
      tone: runCount > 0 ? 'ok' : (mac236Resource.value?.online ? undefined : 'warn'),
    },
  ]
})
const selectedPipelineStageMeta = computed(() =>
  pipelineStages.value.find(item => item.id === selectedPipelineStageId.value) || null)
const activePipelineStageId = computed<PipelineStageId>(() => {
  if (selectedPipelineStageId.value) return selectedPipelineStageId.value
  const step = fullHistoryCurrentStep.value
  if (step === 'completed') return 'run'
  if (step === 'evaluate_deploy') return 'eval'
  if (step === 'waiting_gateway') return 'gb10_sync'
  if (['create_cycle_job', 'approve_job'].includes(step)) {
    return 'route'
  }
  if (['recover_dispatch', 'dispatch_job', 'wait_gateway_result', 'retry_or_blocked'].includes(step)) {
    return 'train'
  }
  const warned = pipelineStages.value.find(stage => stage.tone === 'warn')
  if (warned) return warned.id
  if (runningJobCount.value > 0 || activeJobCount.value > 0) return 'train'
  if (deployedJobCount.value > 0) return 'run'
  return 'data'
})
const pipelineStageDetails = computed<PipelineStageDetail[]>(() => {
  const summary = departmentFlowSummary.value
  const dataCount = pipelineStageCount('data', safeNumber(fullHistorySampleSummary.value.sample_total) || datasetVersionCount.value)
  const cleanCount = pipelineStageCount('clean', safeNumber(summary.cleaned_samples) || safeNumber(fullHistorySampleSummary.value.training_sample_count))
  const datasetCount = pipelineStageCount('dataset', datasetVersionCount.value)
  const gb10SyncCount = pipelineStageCount('gb10_sync', safeNumber(summary.datasets_synced))
  const routeCount = pipelineStageCount('route', pipelineGb10RouteCount(summary, routeReadyJobCount.value))
  const evalCount = pipelineStageCount('eval', evaluatedJobCount.value)
  const deployCount = pipelineStageCount('deploy', canaryJobCount.value + deployedJobCount.value)
  const runCount = pipelineStageCount('run', deployedJobCount.value)
  const entrySourceText = pipelineEntrySourceText(summary)
  const entryTaskDatasetText = pipelineEntryTaskDatasetText(summary)
  const entrySdkCount = safeNumber(summary.entry_sdk)
  const entryLearningCount = safeNumber(summary.entry_learning) || safeNumber(summary.artifact_total)
  const entryManualCount = safeNumber(summary.entry_manual)
  const sampleModalityCounts = pipelineSampleModalityCounts(summary, cleanCount)
  const sampleModalityText = pipelineSampleModalityText(summary, cleanCount)
  const datasetStatusText = pipelineDatasetStatusText(summary, datasetVersionCount.value)
  const datasetSamples = safeNumber(summary.dataset_samples)
  const datasetsUnsynced = pipelineDatasetUnsyncedCount(summary, unsyncedDatasetCount.value)
  const gb10SyncText = pipelineGb10SyncText(summary, gb10SyncCount)
  const trainStatusCounts = pipelineTrainingStatusCounts(summary, {
    awaiting: awaitingJobCount.value,
    queued: queuedJobCount.value,
    training: runningJobCount.value,
  })
  const trainInflightCount = pipelineStageCount('train', pipelineTrainingInflightCount(summary, trainStatusCounts))
  const trainStatusText = pipelineTrainingStatusText(summary, trainStatusCounts)
  const trainingReviewText = pipelineTrainingReviewText(summary, trainStatusCounts)
  const autoRouteText = pipelineAutoRouteText(summary, routeCount)
  const fallbackRouteCount = safeNumber(summary.jobs_target_fallback)
  const unroutedJobCount = safeNumber(summary.jobs_unrouted)
  const evalFailed = safeNumber(summary.eval_failed)
  const deploymentFailed = safeNumber(summary.deployments_failed)
  const deploymentActiveCount = safeNumber(summary.deployments_active) || runCount
  const deploymentCanaryCount = safeNumber(summary.deployments_canary) || canaryJobCount.value
  const deploymentReplacedCount = Math.max(
    0,
    deployCount - deploymentActiveCount - deploymentCanaryCount - deploymentFailed,
  )
  const combinedBlockers = [...automationBlockers.value, ...fullHistoryBlockers.value]
  const makeBlocker = (key: string, label: string, severity: 'warn' | 'error' = 'warn'): AutomationBlocker => ({
    key,
    label,
    severity,
  })
  const stageBlockers = (stageId: PipelineStageId): AutomationBlocker[] => {
    const selected = combinedBlockers.filter((item) => {
      const key = String(item.key || '')
      if (stageId === 'data') return key.includes('dataset') || key.includes('data') || key.includes('sample')
      if (stageId === 'clean') return key.includes('sample')
      if (stageId === 'dataset') return key.includes('sync') || key.includes('dataset')
      if (stageId === 'gb10_sync') return key.includes('gb10') || key.includes('sync') || key.includes('gateway')
      if (stageId === 'route') return key.includes('gb10') || key.includes('route') || key.includes('gateway')
      if (stageId === 'train') return key.includes('training') || key.includes('gateway')
      if (stageId === 'eval') return key.includes('eval') || key.includes('review')
      if (stageId === 'deploy') {
        return key.includes('mac') || key.includes('deploy') || key.includes('model') || key.includes('artifact')
      }
      return key.includes('mac') || key.includes('deploy') || key.includes('inference')
    })
    if (stageId === 'data' && dataCount <= 0) selected.push(makeBlocker('pipeline_no_data', '没有可训练数据'))
    if (stageId === 'clean' && cleanCount <= 0) selected.push(makeBlocker('pipeline_no_clean_samples', '没有训练样本资产'))
    if (stageId === 'dataset' && datasetCount <= 0) selected.push(makeBlocker('pipeline_no_dataset', '没有训练数据集'))
    if (stageId === 'gb10_sync' && !gb10Resource.value?.online) selected.push(makeBlocker('pipeline_gb10_offline', 'GB10 未在线', 'error'))
    if (stageId === 'route' && routeCount <= 0) selected.push(makeBlocker('pipeline_no_gb10_route', '0 指向 GB10', 'warn'))
    if (stageId === 'train' && !fullHistoryTrainingGateway.value?.online && !gb10Resource.value?.online) selected.push(makeBlocker('pipeline_training_gateway_offline', '训练网关未在线', 'error'))
    if (stageId === 'eval' && evalFailed > 0) selected.push(makeBlocker('pipeline_eval_failed', `${evalFailed} 个评估失败`, 'error'))
    if (stageId === 'deploy' && deploymentFailed > 0) selected.push(makeBlocker('pipeline_deploy_failed', `${deploymentFailed} 个部署失败`, 'error'))
    if (stageId === 'deploy' && !fullHistoryDeploymentGateway.value?.online && !mac236Resource.value?.online) selected.push(makeBlocker('pipeline_deploy_gateway_offline', '部署网关未在线', 'error'))
    if (stageId === 'run' && deploymentFailed > 0) selected.push(makeBlocker('pipeline_deploy_failed', `${deploymentFailed} 个部署失败`, 'error'))
    if (stageId === 'run' && !fullHistoryDeploymentGateway.value?.online && !mac236Resource.value?.online) selected.push(makeBlocker('pipeline_deploy_gateway_offline', '部署网关未在线', 'error'))
    if (stageId === 'run' && runCount > 0 && !fullHistoryChat.value?.ready) {
      selected.push(makeBlocker(
        'pipeline_chat_not_ready',
        String(fullHistoryChat.value?.disabled_reason || '大厅对话代理未就绪'),
        'warn',
      ))
    }
    return selected.slice(0, 4)
  }
  return [
    {
      id: 'data',
      label: '入口',
      title: '部门过程数据进入训练资产池',
      detailCount: `${formatCompactCount(dataCount)} 条数据`,
      entry: entrySourceText,
      process: entryTaskDatasetText,
      result: `${formatCompactCount(safeNumber(summary.artifact_total) || dataCount)} 训练资产`,
      impact: datasetVersionCount.value ? `${datasetVersionCount.value} 个数据版本` : '按部门样本覆盖',
      tone: dataCount > 0 ? undefined : 'warn',
      metrics: [
        { label: 'SDK', value: formatCompactCount(entrySdkCount), tone: entrySdkCount > 0 ? 'ok' : undefined },
        { label: '学习闭环', value: formatCompactCount(entryLearningCount), tone: entryLearningCount > 0 ? 'ok' : undefined },
        { label: '人工', value: formatCompactCount(entryManualCount), tone: entryManualCount > 0 ? 'ok' : undefined },
      ],
      blockers: stageBlockers('data'),
      primaryAction: dataCount > 0 ? 'run_auto' : 'datasets',
      primaryLabel: dataCount > 0 ? '入口推进' : '数据资产',
      primaryIcon: dataCount > 0 ? 'flow' : 'database',
      secondaryAction: dataCount > 0 ? 'datasets' : undefined,
      secondaryLabel: dataCount > 0 ? '数据资产' : undefined,
      secondaryIcon: dataCount > 0 ? 'database' : undefined,
    },
    {
      id: 'clean',
      label: '样本',
      title: '训练样本进入资产目录',
      detailCount: `${formatCompactCount(cleanCount)} 条样本`,
      entry: sampleModalityText,
      process: `${formatCompactCount(cleanCount)} 已物化`,
      result: unsyncedDatasetCount.value ? `${unsyncedDatasetCount.value} 待同步` : '同步就绪',
      impact: fullHistoryDataPreparationText.value,
      tone: cleanCount > 0 ? undefined : 'warn',
      metrics: [
        { label: '文字', value: formatCompactCount(sampleModalityCounts.text), tone: sampleModalityCounts.text > 0 ? 'ok' : undefined },
        { label: '图片', value: formatCompactCount(sampleModalityCounts.image), tone: sampleModalityCounts.image > 0 ? 'ok' : undefined },
        { label: '图文', value: formatCompactCount(sampleModalityCounts.imageText), tone: sampleModalityCounts.imageText > 0 ? 'ok' : undefined },
      ],
      blockers: stageBlockers('clean'),
      primaryAction: 'run_auto',
      primaryLabel: '样本同步',
      primaryIcon: 'flow',
      secondaryAction: 'datasets',
      secondaryLabel: '数据资产',
      secondaryIcon: 'database',
    },
    {
      id: 'dataset',
      label: '数据集',
      title: '版本化 manifest 下发到训练网关',
      detailCount: `${formatCompactCount(datasetCount)} 个 manifest`,
      entry: `${formatCompactCount(cleanCount)} 可用样本`,
      process: datasetStatusText,
      result: `${formatCompactCount(datasetsUnsynced)} 待同步`,
      impact: fullHistoryDataPreparationText.value,
      tone: datasetCount > 0 ? undefined : 'warn',
      metrics: [
        { label: 'manifest', value: formatCompactCount(datasetVersionCount.value), tone: datasetVersionCount.value > 0 ? 'ok' : 'warn' },
        { label: '样本', value: formatCompactCount(datasetSamples), tone: datasetSamples > 0 ? 'ok' : undefined },
        { label: '待同步', value: formatCompactCount(datasetsUnsynced), tone: datasetsUnsynced > 0 ? 'warn' : 'ok' },
      ],
      blockers: stageBlockers('dataset'),
      primaryAction: 'datasets',
      primaryLabel: '数据资产',
      primaryIcon: 'database',
      secondaryAction: 'run_auto',
      secondaryLabel: '同步推进',
      secondaryIcon: 'flow',
    },
    {
      id: 'gb10_sync',
      label: 'GB10 同步',
      title: '全部数据同步到 237',
      detailCount: `${formatCompactCount(gb10SyncCount)} 个已同步`,
      entry: `${formatCompactCount(datasetCount)} 个 manifest`,
      process: gb10SyncText,
      result: gb10Resource.value?.online ? `${formatCompactCount(gb10SyncCount)} 已同步到 237` : 'GB10 未在线',
      impact: gb10Resource.value?.online ? '在线训练网关' : '等待接入 GPU',
      tone: gb10SyncCount > 0 ? undefined : 'warn',
      metrics: [
        { label: '已同步', value: formatCompactCount(gb10SyncCount), tone: gb10SyncCount > 0 ? 'ok' : undefined },
        { label: '待同步', value: formatCompactCount(datasetsUnsynced), tone: datasetsUnsynced > 0 ? 'warn' : 'ok' },
        { label: 'GB10', value: gb10Resource.value?.online ? '在线' : '离线', tone: gb10Resource.value?.online ? 'ok' : 'warn' },
      ],
      blockers: stageBlockers('gb10_sync'),
      primaryAction: 'run_auto',
      primaryLabel: '同步到 237',
      primaryIcon: 'flow',
      secondaryAction: 'datasets',
      secondaryLabel: '数据资产',
      secondaryIcon: 'database',
    },
    {
      id: 'route',
      label: '自动寻路',
      title: 'GB10 237 强优先',
      detailCount: `${formatCompactCount(routeCount)} 指向 GB10`,
      entry: `${formatCompactCount(gb10SyncCount)} 已同步到 237`,
      process: autoRouteText,
      result: trainStatusText,
      impact: `GPU ${idleGpuCount.value} / ${totalGpuCount.value}`,
      tone: routeCount > 0 ? undefined : 'warn',
      metrics: [
        { label: 'GB10', value: formatCompactCount(routeCount), tone: routeCount > 0 ? 'ok' : 'warn' },
        { label: '兜底', value: formatCompactCount(fallbackRouteCount), tone: fallbackRouteCount > 0 ? 'warn' : 'ok' },
        { label: '待寻路', value: formatCompactCount(unroutedJobCount), tone: unroutedJobCount > 0 ? 'warn' : 'ok' },
      ],
      blockers: stageBlockers('route'),
      primaryAction: 'run_auto',
      primaryLabel: '自动寻路',
      primaryIcon: 'flow',
      secondaryAction: 'create_job',
      secondaryLabel: '新建训练',
      secondaryIcon: 'plus',
    },
    {
      id: 'train',
      label: '训练',
      title: '在线 GPU 执行 4B 三轮微调',
      detailCount: `${formatCompactCount(trainInflightCount)} 任务进行中`,
      entry: `${formatCompactCount(routeCount)} 指向 GB10`,
      process: trainingReviewText,
      result: failedJobCount.value ? `${failedJobCount.value} 失败 / ${completedJobCount.value} 完成` : `${completedJobCount.value} 完成 / ${fullHistoryCompletedCycles.value}/${fullHistoryCycleCount.value} 轮`,
      impact: `GPU ${idleGpuCount.value} / ${totalGpuCount.value}`,
      tone: trainInflightCount > 0 ? 'warn' : undefined,
      metrics: [
        { label: '自动审核', value: formatCompactCount(trainStatusCounts.awaiting), tone: trainStatusCounts.awaiting > 0 ? 'warn' : undefined },
        { label: '队列', value: formatCompactCount(trainStatusCounts.queued), tone: trainStatusCounts.queued > 0 ? 'warn' : undefined },
        { label: '训练中', value: formatCompactCount(trainStatusCounts.training), tone: trainStatusCounts.training > 0 ? 'ok' : undefined },
      ],
      blockers: stageBlockers('train'),
      primaryAction: 'run_auto',
      primaryLabel: '训练推进',
      primaryIcon: 'flow',
      secondaryAction: 'create_job',
      secondaryLabel: '新建训练',
      secondaryIcon: 'plus',
    },
    {
      id: 'eval',
      label: '评估',
      title: '评估门禁和自动审核',
      detailCount: `${evalCount} 个通过项`,
      entry: `${completedJobCount.value} 完成任务`,
      process: `${evalCount} 已通过`,
      result: evalFailed ? `${evalFailed} 未通过` : '门禁通过',
      impact: automationPolicy.value.approve_jobs === false ? '人工审核' : '自动审核开启',
      tone: evalFailed > 0 ? 'warn' : undefined,
      metrics: [
        { label: '通过', value: `${evalCount}`, tone: evalCount > 0 ? 'ok' : undefined },
        { label: '失败', value: `${evalFailed}`, tone: evalFailed > 0 ? 'bad' : 'ok' },
        { label: '待部署', value: `${Math.max(0, completedJobCount.value - deployedJobCount.value)}` },
      ],
      blockers: stageBlockers('eval'),
      primaryAction: 'run_auto',
      primaryLabel: '评估推进',
      primaryIcon: 'flow',
      secondaryAction: 'models',
      secondaryLabel: '模型库',
      secondaryIcon: 'cube',
    },
    {
      id: 'deploy',
      label: 'Mac236 部署',
      title: '内网自动部署',
      detailCount: `${formatCompactCount(deployCount)} 个部署产物`,
      entry: `${evalCount} 已通过`,
      process: mac236Resource.value?.online ? 'Mac236 在线' : 'Mac236 未在线',
      result: `${formatCompactCount(deploymentCanaryCount)} 灰度 / ${formatCompactCount(deploymentActiveCount)} active`,
      impact: fullHistoryDeploymentGateway.value?.id || FULL_HISTORY_DEPLOYMENT_GATEWAY_ID,
      tone: deployCount > 0 ? undefined : 'warn',
      metrics: [
        { label: '产物', value: formatCompactCount(deployCount), tone: deployCount > 0 ? 'ok' : 'warn' },
        { label: 'active', value: formatCompactCount(deploymentActiveCount), tone: deploymentActiveCount > 0 ? 'ok' : undefined },
        { label: '已替换', value: formatCompactCount(deploymentReplacedCount) },
      ],
      blockers: stageBlockers('deploy'),
      primaryAction: 'run_auto',
      primaryLabel: '部署推进',
      primaryIcon: 'flow',
      secondaryAction: 'models',
      secondaryLabel: '模型库',
      secondaryIcon: 'cube',
    },
    {
      id: 'run',
      label: '运行',
      title: 'Mac236 / 兜底推理提供大厅对话',
      detailCount: `${formatCompactCount(deploymentActiveCount)} 个运行部署`,
      entry: `${formatCompactCount(deploymentCanaryCount)} 灰度`,
      process: mac236Resource.value?.online ? 'Mac236 在线' : '兜底推理检查',
      result: fullHistoryChat.value?.ready
        ? '大厅对话可用'
        : (deploymentActiveCount > 0
            ? `${formatCompactCount(deploymentActiveCount)} active · 进入大厅验证`
            : `${formatCompactCount(deploymentActiveCount)} active`),
      impact: fullHistoryChat.value?.gateway_id || FULL_HISTORY_DEPLOYMENT_GATEWAY_ID,
      tone: runCount > 0 || fullHistoryChat.value?.ready ? 'ok' : 'warn',
      metrics: [
        { label: 'active', value: formatCompactCount(deploymentActiveCount), tone: deploymentActiveCount > 0 ? 'ok' : 'warn' },
        { label: '灰度', value: formatCompactCount(deploymentCanaryCount) },
        { label: '部署网关', value: mac236Resource.value?.online ? '在线' : '离线', tone: mac236Resource.value?.online ? 'ok' : 'warn' },
      ],
      blockers: stageBlockers('run'),
      primaryAction: fullHistoryChat.value?.ready || runCount > 0 ? 'hall_chat' : 'run_auto',
      primaryLabel: fullHistoryChat.value?.ready || runCount > 0 ? '大厅对话' : '运行推进',
      primaryIcon: fullHistoryChat.value?.ready || runCount > 0 ? 'bot' : 'flow',
      secondaryAction: 'models',
      secondaryLabel: '模型库',
      secondaryIcon: 'cube',
    },
  ]
})
const activePipelineStageDetail = computed<PipelineStageDetail>(() =>
  pipelineStageDetails.value.find(item => item.id === activePipelineStageId.value) || pipelineStageDetails.value[0])
const departmentProcessRows = computed<DepartmentProcessRow[]>(() => {
  const flowRows = departmentFlowItems.value
  if (flowRows.length) {
    return flowRows.map((item) => {
      const metrics = item.metrics || {}
      const cleanedSamples = safeNumber(metrics.cleaned_samples)
      const models = safeNumber(metrics.models)
      const evalCompleted = safeNumber(metrics.eval_completed)
      const evalFailed = safeNumber(metrics.eval_failed)
      const deploymentsActive = safeNumber(metrics.deployments_active)
      const deploymentsCanary = safeNumber(metrics.deployments_canary)
      const blockers = normalizeList<AutomationBlocker>(item.blockers)
      const latestText = item.latest_at ? `最近 ${formatTime(String(item.latest_at))}` : '暂无新鲜度'
      const entrySourceText = pipelineEntrySourceText(metrics)
      const sampleModalityText = pipelineSampleModalityText(metrics, cleanedSamples)
      const datasetPendingText = pipelineDatasetPendingText(metrics)
      return {
        id: item.id || `dept:${item.department}`,
        name: latestText,
        department: item.department || '未归属部门',
        summary: entrySourceText,
        cleanText: sampleModalityText,
        trainText: datasetPendingText,
        modelText: `${models} 模型`,
        evalText: `${evalCompleted} 通过${evalFailed ? ` / ${evalFailed} 失败` : ''}`,
        runText: deploymentsActive > 0 ? `${deploymentsActive} active` : (deploymentsCanary > 0 ? `${deploymentsCanary} 灰度` : '待运行'),
        currentStageLabel: item.current_stage_label || '入口',
        coverage: Math.round(safeNumber(item.coverage)),
        warn: blockers.some(blocker => blocker.severity === 'error') || safeNumber(item.coverage) < 80,
        gatewayId: '',
      }
    })
  }
  return resourceRows.value.map((item) => {
    const supported = item.supported_tasks.map(taskLabel).join(' / ') || '未上报能力'
    const activeText = item.active_training_jobs_count > 0 ? `${item.active_training_jobs_count} 个任务` : '空闲'
    const gpuRatio = item.gpu_count > 0 ? Math.round((item.idle_gpu_count / item.gpu_count) * 100) : 0
    const taskPenalty = Math.min(item.active_training_jobs_count * 8, 24)
    const coverage = Math.max(12, Math.min(100, (item.training_ready ? 58 : 28) + gpuRatio * 0.32 + (item.online ? 10 : 0) - taskPenalty))
    return {
      id: item.id,
      name: item.name,
      department: item.department || '未归属部门',
      summary: `${item.name} · ${supported}`,
      cleanText: `${item.gpu_count} 张 GPU`,
      trainText: activeText,
      modelText: item.training_ready ? '可训练' : '未就绪',
      evalText: item.supported_tasks.includes('eval') ? '可评估' : '待上报',
      runText: item.online ? '在线' : '离线',
      currentStageLabel: item.online ? '运行' : '入口',
      coverage: Math.round(coverage),
      warn: !item.online || coverage < 70,
      gatewayId: item.id,
    }
  })
})
const visibleDepartmentProcessRows = computed(() => departmentProcessRows.value.slice(0, 8))
const departmentProcessOverflowCount = computed(() =>
  Math.max(0, departmentProcessRows.value.length - visibleDepartmentProcessRows.value.length))
const modelEvalRows = computed<ModelEvalRow[]>(() => {
  const sourceRows = selectedPipelineStageId.value ? visibleJobRows.value : jobRows.value
  return sourceRows
    .filter(item => item.model_name || item.artifact_name || item.latest_deployment || ['completed', 'evaluating', 'failed'].includes(item.status))
    .slice(0, 6)
    .map((item) => {
      const deploymentStatus = item.latest_deployment?.status || ''
      const progressPercent = Math.round((item.progress || (item.status === 'completed' ? 1 : 0)) * 100)
      const evalStatus = deploymentStatus
        ? deploymentStatusLabel(deploymentStatus)
        : (item.status === 'completed' ? '待部署评估' : jobStatusLabel(item.status))
      const isBad = item.status === 'failed' || ['rejected', 'failed'].includes(deploymentStatus)
      const isGood = ['active', 'canary', 'evaluated'].includes(deploymentStatus) || item.status === 'completed'
      return {
        id: item.id,
        record: item,
        modelName: item.model_name || item.artifact_name || item.target_skill_id || item.title || item.id,
        jobTitle: item.title || item.id,
        department: item.department || '-',
        version: item.latest_deployment?.id ? item.latest_deployment.id.slice(0, 10) : taskLabel(item.job_type),
        progressText: `${progressPercent}%`,
        progressPercent,
        evalStatus,
        evalTone: isBad ? 'bad' : (isGood ? 'ok dot' : 'warn'),
        servesText: deploymentStatus === 'active' ? '1 Agent' : (deploymentStatus === 'canary' ? '灰度' : '-'),
        lastEvalText: item.created_at ? formatTime(item.created_at) : '待评估',
        deployDisabled: !canRequestDeployment(item),
        warn: isBad || progressPercent < 80,
      }
    })
})
const compatibleGatewayRows = computed(() =>
  resourceRows.value.filter(item => gatewaySupportsTask(item, createJobForm.value.job_type)))
const autoRouteGateway = computed(() => {
  const selected = createJobForm.value.target_gateway_id
  if (selected) return compatibleGatewayRows.value.find(item => item.id === selected) || null
  return compatibleGatewayRows.value[0] || null
})
const createJobRouteHint = computed(() => {
  const selected = autoRouteGateway.value
  if (!selected) return `没有可执行 ${taskLabel(createJobForm.value.job_type)} 的训练 Agent，任务仍会进入待审但下发前需要接入网关。`
  const mode = createJobForm.value.target_gateway_id ? '手动指定' : '自动选择'
  const scope = selected.route_scope === 'platform_fallback' ? '平台训练 Agent' : '部门训练 Agent'
  const departmentText = selected.route_scope === 'platform_fallback'
    ? `，任务归属 ${createJobForm.value.department || resourceContextDepartment.value || '-'}`
    : ''
  return `${mode}：${selected.name}（${scope}，${selected.online ? '在线' : '离线'}${departmentText}）`
})

function normalizeList<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[]
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    if (Array.isArray(record.items)) return record.items as T[]
    if (Array.isArray(record.data)) return record.data as T[]
  }
  return []
}

function normalizeStringList(value: unknown): string[] {
  return normalizeList<unknown>(value)
    .map(item => String(item || '').trim())
    .filter(Boolean)
}

function routeQueryString(value: unknown): string {
  if (Array.isArray(value)) return String(value[0] || '')
  return String(value || '')
}

function normalizePipelineStageId(value: unknown): PipelineStageId | '' {
  const raw = routeQueryString(value)
  if (PIPELINE_STAGE_ALIASES[raw]) return PIPELINE_STAGE_ALIASES[raw]
  return PIPELINE_STAGE_IDS.includes(raw as PipelineStageId) ? raw as PipelineStageId : ''
}

function normalizePipelineWindowId(value: unknown): PipelineWindowId {
  const raw = routeQueryString(value).trim().toLowerCase()
  if (PIPELINE_WINDOW_ALIASES[raw]) return PIPELINE_WINDOW_ALIASES[raw]
  return 'week'
}

function selectPipelineStage(stageId: PipelineStageId) {
  const nextQuery: LocationQueryRaw = { ...route.query, stage: stageId }
  router.replace({ query: nextQuery })
}

function selectPipelineWindow(windowId: PipelineWindowId) {
  const nextQuery: LocationQueryRaw = { ...route.query, window: windowId }
  if (windowId === 'week') delete nextQuery.window
  router.replace({ query: nextQuery })
}

function clearPipelineStageFilter() {
  const nextQuery: LocationQueryRaw = { ...route.query }
  delete nextQuery.stage
  router.replace({ query: nextQuery })
}

function pipelineActionDisabled(action?: PipelineAction): boolean {
  if (!action) return true
  if (action === 'run_auto') return fullAutomationRunning.value
  return false
}

function runPipelineAction(action?: PipelineAction) {
  if (!action || pipelineActionDisabled(action)) return
  if (action === 'datasets') {
    router.push('/training/datasets')
    return
  }
  if (action === 'models') {
    router.push('/training/models')
    return
  }
  if (action === 'hall_chat') {
    router.push('/hall/finetuned-model-chat')
    return
  }
  if (action === 'create_job') {
    openCreateJobModal()
    return
  }
  runAutomation()
}

function matchesPipelineStage(record: TrainingJobRow, stageId: PipelineStageId): boolean {
  const deploymentStatus = record.latest_deployment?.status || ''
  if (stageId === 'data') return true
  if (stageId === 'clean') return hasAutomationStepStatus(record, 'sample', 'completed')
  if (stageId === 'dataset') return Boolean(record.dataset_ref) || hasAutomationStepStatus(record, 'dataset', 'completed')
  if (stageId === 'gb10_sync') return hasAutomationStepStatus(record, 'gb10_sync', 'completed') || record.target_gateway_id === FULL_HISTORY_TRAINING_GATEWAY_ID
  if (stageId === 'route') return hasAutomationStepStatus(record, 'route', 'completed') || record.target_gateway_id === FULL_HISTORY_TRAINING_GATEWAY_ID
  if (stageId === 'train') return ['awaiting_review', 'queued', 'running', 'evaluating'].includes(record.status)
  if (stageId === 'eval') {
    return record.status === 'evaluating'
      || ['candidate', 'evaluated', 'awaiting_review', 'rejected', 'canary', 'active'].includes(deploymentStatus)
  }
  if (stageId === 'deploy') {
    return Boolean(record.latest_deployment)
      || ['candidate', 'evaluated', 'awaiting_review', 'rejected', 'canary', 'active'].includes(deploymentStatus)
  }
  return ['canary', 'active', 'rolled_back', 'failed'].includes(deploymentStatus)
}

function hasAutomationStepStatus(record: TrainingJobRow, key: string, status: string): boolean {
  const steps = normalizeList<AutomationStep>(record.automation_status?.steps)
  return steps.some(item => item.key === key && item.status === status)
}

function jobAutomationSteps(record: TrainingJobRow): AutomationStep[] {
  return normalizeList<AutomationStep>(record.automation_status?.steps).slice(0, 8)
}

function safeNumber(value: unknown): number {
  const num = Number(value)
  return Number.isFinite(num) ? num : 0
}

function roundOne(value: number): number {
  return Math.round(value * 10) / 10
}

function formatCompactCount(value: unknown): string {
  const count = safeNumber(value)
  if (count >= 10000) return `${roundOne(count / 10000)} 万`
  if (count >= 1000) return `${roundOne(count / 1000)}k`
  return String(Math.round(count))
}

function estimateDurationText(seconds: number): string {
  const total = safeNumber(seconds)
  if (!total) return '预计时长待配置'
  const hours = Math.floor(total / 3600)
  const minutes = Math.round((total % 3600) / 60)
  if (hours) return `预计 ${hours} 小时 ${minutes} 分钟`
  return `预计 ${Math.max(minutes, 1)} 分钟`
}

function parameterSummaryText(parameters: Record<string, unknown>): string {
  const entries = Object.entries(parameters || {})
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .slice(0, 4)
  if (!entries.length) return '参数待配置'
  return entries.map(([key, value]) => `${key}: ${String(value)}`).join(' · ')
}

function taskLabel(value: string): string {
  const labels: Record<string, string> = {
    lora: 'LoRA',
    qlora: 'QLoRA',
    eval: '评估',
    merge: '合并',
    inference: '推理',
  }
  return labels[value] || value
}

function taskColor(value: string): string {
  if (['lora', 'qlora'].includes(value)) return 'arcoblue'
  if (value === 'eval') return 'green'
  if (value === 'merge') return 'purple'
  if (value === 'inference') return 'orange'
  return 'gray'
}

function gatewaySupportsTask(item: ResourceRow, jobType: string): boolean {
  const tasks = Array.isArray(item.supported_tasks) ? item.supported_tasks : []
  return Boolean(item.training_ready && tasks.includes(String(jobType || '').toLowerCase()))
}

function jobStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    awaiting_review: '待审批',
    queued: '已排队',
    running: '训练中',
    evaluating: '评估中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
    unknown: '未知',
  }
  return labels[value] || value || '未知'
}

function jobStatusColor(value: string): string {
  if (value === 'awaiting_review') return 'orange'
  if (['queued', 'running', 'evaluating'].includes(value)) return 'arcoblue'
  if (value === 'completed') return 'green'
  if (value === 'failed') return 'red'
  if (value === 'cancelled') return 'gray'
  return 'gray'
}

function jobStatusPillTone(value: string): string {
  if (value === 'awaiting_review') return 'warn'
  if (['queued', 'running', 'evaluating'].includes(value)) return 'ok dot'
  if (value === 'completed') return 'ok'
  if (value === 'failed') return 'bad'
  return ''
}

function deploymentStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    candidate: '部署候选',
    evaluated: '部署已评估',
    awaiting_review: '部署待审',
    canary: '灰度中',
    active: '已部署',
    rejected: '已驳回',
    failed: '部署失败',
    rolled_back: '已回滚',
  }
  return labels[value] || value || '部署'
}

function deploymentStatusColor(value: string): string {
  if (value === 'awaiting_review') return 'orange'
  if (['candidate', 'evaluated', 'canary'].includes(value)) return 'arcoblue'
  if (value === 'active') return 'green'
  if (value === 'rejected') return 'red'
  if (value === 'failed') return 'red'
  if (value === 'rolled_back') return 'gray'
  return 'gray'
}

function deploymentStatusPillTone(value: string): string {
  if (value === 'awaiting_review') return 'warn'
  if (['candidate', 'evaluated', 'canary'].includes(value)) return 'warn'
  if (value === 'active') return 'ok dot'
  if (['rejected', 'failed'].includes(value)) return 'bad'
  return ''
}

function jobTypeLabel(value: string): string {
  return taskLabel(value)
}

function isActiveTrainingJob(record: TrainingJobRow): boolean {
  return ['queued', 'running', 'evaluating'].includes(record.status)
}

function jobProgressPercent(record: TrainingJobRow): number {
  if (record.status === 'completed') return 100
  return Math.max(0, Math.min(100, Math.round(safeNumber(record.progress) * 100)))
}

function jobProgressText(record: TrainingJobRow): string {
  return `${jobProgressPercent(record)}%`
}

function jobGatewayText(record: TrainingJobRow): string {
  return record.target_gateway_id || '未分配'
}

function jobEtaText(record: TrainingJobRow): string {
  return record.eta_text || estimateDurationText(record.estimated_duration_seconds)
}

function formatTime(value: string): string {
  if (!value) return '-'
  return value.replace('T', ' ').slice(0, 16)
}

function canCancelJob(record: TrainingJobRow): boolean {
  return record.failure_stage !== 'cancel' && !['completed', 'failed', 'cancelled'].includes(record.status)
}

function canApproveJob(record: TrainingJobRow): boolean {
  return record.status === 'awaiting_review'
}

function canForceCancelJob(record: TrainingJobRow): boolean {
  return record.failure_stage === 'cancel' && ['running', 'evaluating', 'unknown'].includes(record.status)
}

function canEvaluateJob(record: TrainingJobRow): boolean {
  return record.status === 'completed'
}

function canCollectResult(record: TrainingJobRow): boolean {
  return Boolean(record.target_gateway_id)
    && ['queued', 'running', 'evaluating', 'unknown'].includes(record.status)
    && record.failure_stage !== 'dispatch'
}

function canSimulateJob(record: TrainingJobRow): boolean {
  return Boolean(record.id)
    && !['failed', 'cancelled'].includes(record.status)
    && record.failure_stage !== 'dispatch'
}

function canRequestDeployment(record: TrainingJobRow): boolean {
  const blockedStatuses = ['candidate', 'evaluated', 'awaiting_review', 'canary', 'active']
  return record.status === 'completed'
    && !record.failure_stage
    && !blockedStatuses.includes(record.latest_deployment?.status || '')
}

function canApproveDeployment(record: TrainingJobRow): boolean {
  return record.latest_deployment?.status === 'awaiting_review'
}

function canRejectDeployment(record: TrainingJobRow): boolean {
  return record.latest_deployment?.status === 'awaiting_review'
}

function canActivateDeployment(record: TrainingJobRow): boolean {
  return record.latest_deployment?.status === 'canary'
}

function canRollbackDeployment(record: TrainingJobRow): boolean {
  return ['canary', 'active'].includes(record.latest_deployment?.status || '')
}

function canViewArtifacts(record: TrainingJobRow): boolean {
  return ['completed', 'failed', 'evaluating'].includes(record.status)
}

function hasJobActions(record: TrainingJobRow): boolean {
  return canApproveJob(record)
    || (record.status === 'queued')
    || canCancelJob(record)
    || canForceCancelJob(record)
    || canEvaluateJob(record)
    || canCollectResult(record)
    || canSimulateJob(record)
    || canRequestDeployment(record)
    || canApproveDeployment(record)
    || canActivateDeployment(record)
    || canRollbackDeployment(record)
    || canViewArtifacts(record)
    || Boolean(record.target_gateway_id)
}

async function loadTraining(options: { silent?: boolean } = {}) {
  const silent = Boolean(options.silent)
  if (!silent) {
    loading.value = true
    loadError.value = ''
  }
  try {
    const [res, jobRes, automationRes, fullHistoryRes] = await Promise.all([
      trainingApi.resources(),
      trainingApi.jobs(jobListParams.value),
      trainingApi.automationStatus({ window_days: pipelineWindowDays.value }),
      trainingApi.fullHistoryFinetuneStatus(),
    ])
    resourceContextDepartment.value = String(res?.context?.department || '')
    resources.value = normalizeList<Record<string, unknown>>(res?.items).map(normalizeResourceRow)
    jobs.value = normalizeList<Record<string, unknown>>(jobRes?.items).map(normalizeJobRow)
    automationStatus.value = normalizeAutomationStatus(automationRes)
    fullHistoryStatus.value = fullHistoryRes || null
  } catch (error: any) {
    if (!silent) {
      resources.value = []
      jobs.value = []
      automationStatus.value = null
      fullHistoryStatus.value = null
      resourceContextDepartment.value = ''
    }
    loadError.value = error?._message || '加载训练资源失败'
  } finally {
    if (!silent) loading.value = false
  }
}

function openCreateJobModal() {
  const shouldUseFullHistoryTemplate = !gatewayQueryId.value || gatewayQueryId.value === FULL_HISTORY_TRAINING_GATEWAY_ID
  const contextGateway = resourceRows.value.find(item => item.id === gatewayQueryId.value)
  const initialJobType = shouldUseFullHistoryTemplate
    ? 'lora'
    : (contextGateway?.supported_tasks.includes('lora')
    ? 'lora'
    : (contextGateway?.supported_tasks[0] || 'lora'))
  const firstGateway = resourceRows.value.find(item => item.id === gatewayQueryId.value && gatewaySupportsTask(item, initialJobType))
    || resourceRows.value.find(item => item.id === FULL_HISTORY_TRAINING_GATEWAY_ID && gatewaySupportsTask(item, initialJobType))
    || resourceRows.value.find(item => gatewaySupportsTask(item, initialJobType))
    || resourceRows.value[0]
  const jobDepartment = firstGateway?.route_scope === 'platform_fallback'
    ? (resourceContextDepartment.value || (shouldUseFullHistoryTemplate ? 'AI平台' : ''))
    : (firstGateway?.department || resourceContextDepartment.value || '')
  createJobForm.value = {
    title: shouldUseFullHistoryTemplate ? 'SkillForge 全量数据 4B 微调' : '',
    department: jobDepartment,
    job_type: initialJobType,
    training_strategy: shouldUseFullHistoryTemplate ? 'learning_sample_threshold' : 'lora_task_parallel',
    target_gateway_id: firstGateway?.id || '',
    target_skill_id: shouldUseFullHistoryTemplate ? FULL_HISTORY_CHAT_SKILL_ID : '',
    model_name: shouldUseFullHistoryTemplate ? FULL_HISTORY_MODEL_FAMILY : '',
    base_model: shouldUseFullHistoryTemplate ? FULL_HISTORY_MODEL_PROFILE : '',
    epochs: shouldUseFullHistoryTemplate ? '3' : '',
    learning_rate: shouldUseFullHistoryTemplate ? '2e-4' : '',
    estimated_gpu_hours: shouldUseFullHistoryTemplate ? '6' : '',
    dataset_ref: shouldUseFullHistoryTemplate ? FULL_HISTORY_DATASET_REF : '',
    objective: shouldUseFullHistoryTemplate ? '使用完整历史样本微调 4B 大厅对话模型，并自动评估、部署到 Mac236。' : '',
    risk_level: 'R2',
  }
  createJobVisible.value = true
}

function normalizeResourceRow(item: Record<string, unknown>): ResourceRow {
  const gpus = normalizeList<GpuInfo>(item.gpu)
  const tasks = normalizeStringList((item.training as Record<string, unknown> | undefined)?.supported_tasks)
  const totalVramGb = safeNumber(item.vram_total_gb)
  const freeVramGb = safeNumber(item.vram_free_gb)
  const activeJobs = normalizeList<Record<string, unknown>>(item.active_training_jobs).map((job) => ({
    id: String(job.id || ''),
    title: String(job.title || job.id || ''),
    status: String(job.status || ''),
    job_type: String(job.job_type || ''),
    target_skill_id: String(job.target_skill_id || ''),
    updated_at: String(job.updated_at || ''),
  })).filter(job => job.id)
  const routeScope = String(item.route_scope || '')
  const agentPurpose = String(item.agent_purpose || '').toLowerCase()
  const isPlatformFallback = Boolean(item.is_platform_default)
    && ['training', 'mixed'].includes(agentPurpose)
    && routeScope !== 'department'
  return {
    id: String(item.id || ''),
    name: String(item.name || item.id || ''),
    department: String(item.department || ''),
    online: Boolean(item.online),
    gateway_kind: String(item.gateway_kind || 'unknown'),
    gpu_count: safeNumber(item.gpu_count || gpus.length),
    idle_gpu_count: safeNumber(item.idle_gpu_count),
    busy_gpu_count: safeNumber(item.busy_gpu_count),
    vram_total_gb: totalVramGb,
    vram_free_gb: freeVramGb,
    vram_percent: totalVramGb > 0 ? Math.max(0, Math.min(1, freeVramGb / totalVramGb)) : 0,
    supported_tasks: tasks,
    active_training_jobs: activeJobs,
    active_training_jobs_count: safeNumber(item.active_training_jobs_count || activeJobs.length),
    training_ready: Boolean((item.training as Record<string, unknown> | undefined)?.gateway || tasks.length),
    route_scope: (routeScope === 'platform_fallback' || isPlatformFallback)
      ? 'platform_fallback'
      : (routeScope === 'external' ? 'external' : 'department'),
  }
}

function normalizeAutomationStep(item: Record<string, unknown>): AutomationStep {
  return {
    ...item,
    key: String(item.key || ''),
    label: String(item.label || item.key || ''),
    status: String(item.status || 'pending'),
    detail: String(item.detail || ''),
  }
}

function normalizeAutomationBlocker(item: Record<string, unknown>): AutomationBlocker {
  return {
    ...item,
    key: String(item.key || ''),
    label: String(item.label || item.key || ''),
    severity: String(item.severity || 'warn'),
  }
}

function normalizeJobAutomationStatus(value: unknown): TrainingJobAutomationStatus | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  return {
    status: String(record.status || ''),
    steps: normalizeList<Record<string, unknown>>(record.steps).map(normalizeAutomationStep),
    blockers: normalizeList<Record<string, unknown>>(record.blockers).map(normalizeAutomationBlocker),
    auto_approval: record.auto_approval && typeof record.auto_approval === 'object'
      ? record.auto_approval as Record<string, unknown>
      : undefined,
  }
}

function normalizeAutomationStatus(value: unknown): TrainingAutomationStatus | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  return {
    ok: Boolean(record.ok),
    policy: record.policy && typeof record.policy === 'object' ? record.policy as Record<string, unknown> : {},
    factors: normalizeList<Record<string, unknown>>(record.factors).map((item) => ({
      key: String(item.key || ''),
      label: String(item.label || item.key || ''),
      ok: Boolean(item.ok),
      pending: item.pending === undefined ? undefined : safeNumber(item.pending),
      node_id: item.node_id ? String(item.node_id) : undefined,
    })),
    blockers: normalizeList<Record<string, unknown>>(record.blockers).map(normalizeAutomationBlocker),
    summary: record.summary && typeof record.summary === 'object' ? record.summary as Record<string, unknown> : {},
    department_flow: record.department_flow && typeof record.department_flow === 'object'
      ? record.department_flow as TrainingDepartmentFlow
      : null,
    nodes: record.nodes && typeof record.nodes === 'object' ? record.nodes as Record<string, unknown> : {},
    last_run: record.last_run && typeof record.last_run === 'object' ? record.last_run as Record<string, unknown> : null,
  }
}

function normalizeJobRow(item: Record<string, unknown>): TrainingJobRow {
  const latestDeployment = item.latest_deployment && typeof item.latest_deployment === 'object'
    ? item.latest_deployment as Record<string, unknown>
    : null
  const trainingPlan = item.training_plan && typeof item.training_plan === 'object'
    ? item.training_plan as Record<string, unknown>
    : {}
  const runtime = trainingPlan.runtime && typeof trainingPlan.runtime === 'object'
    ? trainingPlan.runtime as Record<string, unknown>
    : {}
  const artifact = trainingPlan.artifact && typeof trainingPlan.artifact === 'object'
    ? trainingPlan.artifact as Record<string, unknown>
    : null
  const parameters = trainingPlan.parameters && typeof trainingPlan.parameters === 'object'
    ? trainingPlan.parameters as Record<string, unknown>
    : {}
  const routing = item.gateway_routing && typeof item.gateway_routing === 'object'
    ? item.gateway_routing as Record<string, unknown>
    : (trainingPlan.routing && typeof trainingPlan.routing === 'object'
        ? trainingPlan.routing as Record<string, unknown>
        : {})
  const routeMode = String(routing.mode || '')
  const routeScope = String(routing.scope || '')
  const routeStatus = String(routing.status || '')
  const gatewayRouteLabel = routeStatus === 'missing_gateway'
    ? '待接入'
    : (routeMode === 'auto'
        ? (routeScope === 'platform_fallback' ? '自动·平台' : '自动')
        : '')
  return {
    id: String(item.id || ''),
    title: String(item.title || item.id || ''),
    department: String(item.department || ''),
    status: String(item.status || 'unknown'),
    job_type: String(item.job_type || 'lora'),
    model_name: String(trainingPlan.model_name || latestDeployment?.model_family || item.target_skill_id || item.title || ''),
    base_model: String(trainingPlan.base_model || ''),
    target_skill_id: String(item.target_skill_id || ''),
    target_gateway_id: String(item.target_gateway_id || ''),
    dataset_ref: String(item.dataset_ref || ''),
    parameters,
    progress: Math.max(0, Math.min(safeNumber(runtime.progress), 1)),
    eta_text: String(runtime.eta_text || ''),
    estimated_duration_seconds: safeNumber(runtime.estimated_duration_seconds),
    artifact_name: String(artifact?.name || artifact?.id || ''),
    gateway_route_label: gatewayRouteLabel,
    gateway_route_color: routeStatus === 'missing_gateway' ? 'orange' : (routeScope === 'platform_fallback' ? 'purple' : 'arcoblue'),
    failure_stage: String(item.failure_stage || ''),
    automation_status: normalizeJobAutomationStatus(item.automation_status),
    created_at: String(item.created_at || ''),
    latest_deployment: latestDeployment ? {
      id: String(latestDeployment.id || ''),
      status: String(latestDeployment.status || ''),
    } : null,
  }
}

function normalizeArtifactRow(item: Record<string, unknown>): ArtifactRow {
  return {
    id: String(item.id || ''),
    type: String(item.type || 'artifact'),
    name: String(item.name || item.id || 'artifact'),
    uri: String(item.uri || ''),
    sha256: String(item.sha256 || ''),
    size_bytes: safeNumber(item.size_bytes),
    downloadable: Boolean(item.downloadable),
    download_url: String(item.download_url || ''),
  }
}

function formatBytes(value: number): string {
  const bytes = safeNumber(value)
  if (bytes <= 0) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${roundOne(bytes / 1024)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${roundOne(bytes / 1024 / 1024)} MB`
  return `${roundOne(bytes / 1024 / 1024 / 1024)} GB`
}

function filenameFromDisposition(value: string, fallback: string) {
  const match = value.match(/filename="?([^";]+)"?/i)
  return match?.[1] || fallback
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function buildCreateJobSpec(form: typeof createJobForm.value) {
  const targetSkillId = form.target_skill_id.trim()
  const datasetRef = form.dataset_ref.trim()
  const modelName = form.model_name.trim()
  const baseModel = form.base_model.trim()
  const trainingGatewayId = form.target_gateway_id || FULL_HISTORY_TRAINING_GATEWAY_ID
  const isFullHistoryJob = targetSkillId === FULL_HISTORY_CHAT_SKILL_ID
    || datasetRef.startsWith('learning-artifacts://platform/training/full-history')
  const spec: Record<string, unknown> = {
    model_name: modelName || undefined,
    model_family: modelName || undefined,
    base_model: baseModel || undefined,
    model_profile: isFullHistoryJob ? FULL_HISTORY_MODEL_PROFILE : undefined,
    estimated_gpu_hours: form.estimated_gpu_hours ? Number(form.estimated_gpu_hours) : undefined,
    parameters: {
      epochs: form.epochs.trim() || undefined,
      learning_rate: form.learning_rate.trim() || undefined,
    },
  }
  if (!isFullHistoryJob) return spec
  return {
    ...spec,
    source: 'training_home_new_job',
    training_mode: 'full_history_incremental',
    dataset: {
      ref: datasetRef || FULL_HISTORY_DATASET_REF,
      selector: {
        type: 'learning_artifacts',
        limit: 5000,
        inline_limit: 256,
        inline_eval_limit: 16,
        selected_sample_limit: 5000,
        selection_policy: 'stable_full_history_training_batch',
      },
      bridge_dataset: {
        enabled: true,
        required: true,
        storage: 'bridge_dataset',
        source_gateway_id: trainingGatewayId,
        target_gateway_id: trainingGatewayId,
      },
    },
    automation: {
      full_auto: true,
      enabled: true,
      auto_dataset_sync: true,
      auto_approve_job: true,
      auto_evaluate_on_result: true,
      training_gateway_id: trainingGatewayId,
      deployment_gateway_id: FULL_HISTORY_DEPLOYMENT_GATEWAY_ID,
    },
    deployment: {
      model_profile: FULL_HISTORY_MODEL_PROFILE,
      model_family: modelName || FULL_HISTORY_MODEL_FAMILY,
      deployment_target_gateway_id: FULL_HISTORY_DEPLOYMENT_GATEWAY_ID,
      deployment_runtime_profile: FULL_HISTORY_DEPLOYMENT_RUNTIME_PROFILE,
      auto_request: true,
      auto_approve_canary: true,
      auto_active: true,
      rollout_percent: 100,
      target_skill_ids: [targetSkillId || FULL_HISTORY_CHAT_SKILL_ID],
      reason: 'training.home new full-history 4b job',
    },
    governance: {
      skillforge_full_history_chat: true,
      full_history_new_training: true,
      fallback_online_gpu_allowed: true,
      auto_approval_reason: 'training.home full-auto',
    },
  }
}

async function submitCreateJob() {
  const form = createJobForm.value
  if (!form.title.trim()) {
    Message.error('请输入训练任务名称')
    return
  }
  if (!form.department.trim()) {
    Message.error('请输入部门')
    return
  }
  creatingJob.value = true
  try {
    const result = await trainingApi.createJob({
      title: form.title.trim(),
      department: form.department.trim(),
      job_type: form.job_type,
      training_strategy: form.training_strategy,
      target_gateway_id: form.target_gateway_id || undefined,
      target_skill_id: form.target_skill_id.trim() || undefined,
      dataset_ref: form.dataset_ref.trim() || undefined,
      objective: form.objective.trim() || undefined,
      risk_level: form.risk_level || undefined,
      spec: buildCreateJobSpec(form),
    })
    const created = normalizeJobRow(result || {})
    jobs.value = created.id ? [created, ...jobs.value.filter(item => item.id !== created.id)] : jobs.value
    createJobVisible.value = false
    Message.success('已创建训练任务')
  } catch (error: any) {
    Message.error(error?._message || '训练任务创建失败')
  } finally {
    creatingJob.value = false
  }
}

async function approveJob(id: string) {
  if (!id || approving.value[id]) return
  approving.value = { ...approving.value, [id]: true }
  try {
    const result = await trainingApi.approveJob(id)
    const updated = normalizeJobRow(result || {})
    jobs.value = jobs.value.map(item => (item.id === id ? updated : item))
    Message.success('已审批训练任务')
  } catch (error: any) {
    Message.error(error?._message || '训练任务审批失败')
  } finally {
    const next = { ...approving.value }
    delete next[id]
    approving.value = next
  }
}

async function dispatchJob(id: string, retry = false) {
  if (!id || dispatching.value[id]) return
  dispatching.value = { ...dispatching.value, [id]: true }
  try {
    const result = retry ? await trainingApi.retryJob(id) : await trainingApi.dispatchJob(id)
    const updated = normalizeJobRow(result || {})
    jobs.value = jobs.value.map(item => (item.id === id ? updated : item))
    const failedTask = normalizeList<Record<string, unknown>>((result || {}).tasks)
      .find(item => String(item.status || '') === 'failed')
    if (failedTask) {
      Message.error(String(failedTask.error_message || (retry ? '训练网关重试失败' : '训练网关下发失败')))
    } else if (updated.status === 'running') {
      Message.success(retry ? '已重试训练网关' : '已下发训练网关')
    } else {
      Message.warning('训练任务仍在排队')
    }
  } catch (error: any) {
    Message.error(error?._message || (retry ? '训练任务重试失败' : '训练任务下发失败'))
  } finally {
    const next = { ...dispatching.value }
    delete next[id]
    dispatching.value = next
  }
}

async function cancelJob(id: string) {
  if (!id || canceling.value[id]) return
  canceling.value = { ...canceling.value, [id]: true }
  try {
    const result = await trainingApi.cancelJob(id)
    const updated = normalizeJobRow(result || {})
    jobs.value = jobs.value.map(item => (item.id === id ? updated : item))
    if (updated.status === 'cancelled') {
      Message.success('已取消训练任务')
    } else if (updated.failure_stage === 'cancel') {
      Message.error('训练网关取消失败')
    } else {
      Message.warning('训练任务未取消')
    }
  } catch (error: any) {
    Message.error(error?._message || '训练任务取消失败')
  } finally {
    const next = { ...canceling.value }
    delete next[id]
    canceling.value = next
  }
}

async function forceCancelJob(id: string) {
  if (!id || forceCanceling.value[id]) return
  Modal.confirm({
    title: '强制取消训练',
    content: '仅在训练网关取消失败后使用；平台会标记任务已取消，并保留后续清理线索。',
    okText: '强制取消',
    okButtonProps: { status: 'danger' } as any,
    async onOk() {
      forceCanceling.value = { ...forceCanceling.value, [id]: true }
      try {
        const result = await trainingApi.forceCancelJob(id, '训练网关取消失败后的人工强制取消')
        const updated = normalizeJobRow(result || {})
        jobs.value = jobs.value.map(item => (item.id === id ? updated : item))
        Message.success('已强制取消训练任务')
      } catch (error: any) {
        Message.error(error?._message || '训练任务强制取消失败')
      } finally {
        const next = { ...forceCanceling.value }
        delete next[id]
        forceCanceling.value = next
      }
    },
  })
}

async function evaluateJob(id: string) {
  if (!id || evaluating.value[id]) return
  evaluating.value = { ...evaluating.value, [id]: true }
  try {
    const result = await trainingApi.evaluateJob(id)
    const updated = normalizeJobRow(result || {})
    jobs.value = jobs.value.map(item => (item.id === id ? updated : item))
    const evaluationTask = normalizeList<Record<string, unknown>>((result || {}).tasks)
      .reverse()
      .find(item => String((item.metrics as Record<string, unknown> | undefined)?.op || '') === 'training.evaluate')
    if (updated.failure_stage === 'eval' || evaluationTask?.status === 'failed') {
      Message.error(String(evaluationTask?.error_message || '训练评估未通过'))
    } else {
      Message.success('训练评估通过')
    }
  } catch (error: any) {
    Message.error(error?._message || '训练评估失败')
  } finally {
    const next = { ...evaluating.value }
    delete next[id]
    evaluating.value = next
  }
}

async function collectResult(id: string) {
  if (!id || collecting.value[id]) return
  collecting.value = { ...collecting.value, [id]: true }
  try {
    const result = await trainingApi.collectResult(id)
    const updated = normalizeJobRow(result || {})
    jobs.value = jobs.value.map(item => (item.id === id ? updated : item))
    if (updated.status === 'completed') {
      Message.success('已同步训练结果')
    } else if (updated.status === 'failed') {
      Message.error('训练网关返回失败')
    } else {
      Message.success('已同步训练状态')
    }
  } catch (error: any) {
    Message.error(error?._message || '训练结果同步失败')
  } finally {
    const next = { ...collecting.value }
    delete next[id]
    collecting.value = next
  }
}

async function collectDueResults() {
  if (collectingDue.value) return
  collectingDue.value = true
  try {
    const payload: Record<string, unknown> = { stale_seconds: 0, max_jobs: 20 }
    if (gatewayQueryId.value) payload.target_gateway_id = gatewayQueryId.value
    const result = await trainingApi.collectDue(payload)
    const stats = result?.stats || {}
    await loadTraining()
    if (safeNumber(stats.collected) > 0) {
      Message.success(`已同步 ${safeNumber(stats.collected)} 个训练任务`)
    } else if (safeNumber(stats.failed) > 0) {
      Message.error('部分训练任务同步失败')
    } else {
      Message.warning('暂无可同步训练结果')
    }
  } catch (error: any) {
    Message.error(error?._message || '训练结果批量同步失败')
  } finally {
    collectingDue.value = false
  }
}

function displayChangeValue(value: unknown): string {
  if (Array.isArray(value)) return `${value.length} 项`
  if (value && typeof value === 'object') return JSON.stringify(value).slice(0, 80)
  const text = String(value ?? '-')
  return text.length > 80 ? `${text.slice(0, 77)}...` : text
}

function normalizeSimulationVerification(value: unknown): SimulationVerification | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  const simulatedRequest = record.simulated_request && typeof record.simulated_request === 'object'
    ? record.simulated_request as Record<string, unknown>
    : {}
  const before = record.before && typeof record.before === 'object'
    ? record.before as Record<string, unknown>
    : {}
  const after = record.after && typeof record.after === 'object'
    ? record.after as Record<string, unknown>
    : {}
  const changedFields = normalizeList<Record<string, unknown>>(record.changed_fields).map(item => ({
    field: String(item.field || ''),
    before: item.before,
    after: item.after,
  })).filter(item => item.field)
  return {
    job_id: String(record.job_id || ''),
    gateway_id: String(simulatedRequest.gateway_id || ''),
    before,
    after,
    changed_fields: changedFields,
  }
}

function fullHistoryStepLabel(step: string): string {
  const labels: Record<string, string> = {
    queued: '等待启动',
    waiting_gateway: '等待网关',
    create_cycle_job: '创建训练任务',
    wait_parent_deployment: '等待上一轮部署',
    approve_job: '自动审核',
    recover_dispatch: '恢复下发',
    dispatch_job: '下发训练',
    wait_gateway_result: '等待训练结果',
    evaluate_deploy: '评估部署',
    retry_or_blocked: '重试/阻塞',
    advance_cycle: '推进轮次',
    completed: '已完成',
  }
  return labels[step] || step || '等待'
}

function fullHistoryCycleText(cycle: Record<string, any>): string {
  if (cycle.status === 'completed') return cycle.deployment?.id || '已部署'
  if (cycle.job?.id) return `${cycle.job.status || cycle.status} · ${cycle.job.id}`
  return '等待创建'
}

function fullHistoryRunPayload(trigger = 'web') {
  return {
    trigger,
    cycles: 3,
    capture_sources: true,
    blocking_source_sync: false,
    days: 3650,
    per_source_limit: 2000,
    materialize_limit: 50000,
    min_sample_count: 4,
    background_dispatch: true,
  }
}

async function runFullHistoryFinetune() {
  if (fullHistoryRunning.value) return
  fullHistoryRunning.value = true
  try {
    const result = await trainingApi.runFullHistoryFinetune(fullHistoryRunPayload('web_full_history'))
    fullHistoryStatus.value = result?.after || fullHistoryStatus.value
    await loadTraining()
    if (result?.ok) {
      Message.success('全量 4B 三轮微调已完成')
    } else {
      Message.warning('三轮微调已推进，仍需等待真实节点完成或恢复在线')
    }
  } catch (error: any) {
    Message.error(error?._message || '全量三轮微调启动失败')
  } finally {
    fullHistoryRunning.value = false
  }
}

async function runAutomation() {
  if (fullAutomationRunning.value) return
  automationRunning.value = true
  fullHistoryRunning.value = true
  let automationError: any = null
  let fullHistoryError: any = null
  let result: any = null
  let fullHistoryResult: any = null
  try {
    try {
      result = await trainingApi.runAutomation({
        trigger: 'web',
        materialize_learning: true,
        materialize_limit: 50000,
        promote_learning_samples: true,
        create_datasets: true,
        max_dataset_samples: 5000,
        datasets: true,
        jobs: true,
        dispatch: true,
        evaluate: true,
        max_datasets: 20,
        max_jobs: 50,
      })
    } catch (error: any) {
      automationError = error
    }
    try {
      fullHistoryResult = await trainingApi.runFullHistoryFinetune(fullHistoryRunPayload('web_auto'))
      fullHistoryStatus.value = fullHistoryResult?.after || fullHistoryStatus.value
    } catch (error: any) {
      fullHistoryError = error
    }
    const stats = result?.stats || {}
    await loadTraining()
    const changed = safeNumber(stats.jobs_changed)
    const synced = safeNumber(stats.datasets_synced)
    const materialized = safeNumber(stats.learning_materialized)
    const promoted = safeNumber(stats.learning_samples_promoted)
    const datasetsCreated = safeNumber(stats.datasets_created)
    const failed = safeNumber(stats.failed)
    const completedCycles = safeNumber(fullHistoryResult?.after?.completed_cycles ?? fullHistoryResult?.completed_cycles)
    const cycleCount = safeNumber(fullHistoryResult?.after?.cycle_count || 3)
    const visibleBlockers = [...automationBlockers.value, ...fullHistoryBlockers.value]
    const blockerLabel = visibleBlockers.find(item => item.severity === 'error')?.label
      || visibleBlockers[0]?.label
      || ''
    if (automationError && fullHistoryError) {
      Message.error(automationError?._message || fullHistoryError?._message || '全自动推进失败')
    } else if (automationError) {
      Message.warning(`三轮微调已推进 ${completedCycles}/${cycleCount}，训练流水自动化失败`)
    } else if (fullHistoryError) {
      Message.warning(`训练流水已推进 ${changed} 个任务，物化 ${materialized} 个样本，入库 ${promoted} 个样本，创建 ${datasetsCreated} 个 manifest，同步 ${synced} 个数据集；三轮微调等待恢复`)
    } else if (failed) {
      Message.warning(`自动化已执行，${failed} 项存在阻塞`)
    } else if (changed <= 0 && materialized <= 0 && promoted <= 0 && datasetsCreated <= 0 && synced <= 0 && completedCycles <= 0 && !fullHistoryResult?.ok) {
      Message.warning(`没有可推进项${blockerLabel ? `：${blockerLabel}` : ''}`)
    } else if (fullHistoryResult?.ok) {
      Message.success(`全自动已完成，推进 ${changed} 个任务，物化 ${materialized} 个样本，入库 ${promoted} 个样本，创建 ${datasetsCreated} 个 manifest，同步 ${synced} 个数据集，三轮微调可对话`)
    } else {
      Message.success(`全自动已推进，训练任务 ${changed} 个，样本物化 ${materialized} 个，样本入库 ${promoted} 个，manifest ${datasetsCreated} 个，数据同步 ${synced} 个，三轮 ${completedCycles}/${cycleCount}`)
    }
  } catch (error: any) {
    Message.error(error?._message || '自动化推进失败')
  } finally {
    automationRunning.value = false
    fullHistoryRunning.value = false
  }
}

async function simulateJobResult(record: TrainingJobRow) {
  const id = record.id
  if (!id || simulating.value[id]) return
  simulating.value = { ...simulating.value, [id]: true }
  try {
    const result = await trainingApi.simulateJobResult(id, {
      auto_advance_first: true,
      status: 'completed',
      progress: 1,
      metrics: {
        win_rate: 0.92,
        eval_requested_samples: 24,
        eval_evaluated_samples: 24,
      },
    })
    const updated = normalizeJobRow((result || {}).job || {})
    if (updated.id) {
      jobs.value = jobs.value.map(item => (item.id === id ? updated : item))
    }
    simulationVerification.value = normalizeSimulationVerification(result)
    await loadTraining()
    Message.success('模拟输出已写入并完成变化验证')
  } catch (error: any) {
    Message.error(error?._message || '模拟输出验证失败')
  } finally {
    const next = { ...simulating.value }
    delete next[id]
    simulating.value = next
  }
}

async function requestDeployment(record: TrainingJobRow) {
  if (!record.id || deploymentRequesting.value[record.id]) return
  deploymentRequesting.value = { ...deploymentRequesting.value, [record.id]: true }
  try {
    const result = await trainingApi.requestDeployment(record.id, {
      target_skill_ids: record.target_skill_id ? [record.target_skill_id] : [],
      reason: '训练评估通过后提交部署审批',
      rollout_percent: 0,
    })
    const updated = normalizeJobRow((result || {}).job || {})
    if (updated.id) {
      jobs.value = jobs.value.map(item => (item.id === record.id ? updated : item))
    }
    Message.success('已提交部署审批')
  } catch (error: any) {
    Message.error(error?._message || '部署审批提交失败')
  } finally {
    const next = { ...deploymentRequesting.value }
    delete next[record.id]
    deploymentRequesting.value = next
  }
}

function applyDeploymentJobResult(record: TrainingJobRow, result: any) {
  const updated = normalizeJobRow((result || {}).job || {})
  if (updated.id) {
    jobs.value = jobs.value.map(item => (item.id === record.id ? updated : item))
  }
}

async function approveDeployment(record: TrainingJobRow) {
  const deploymentId = record.latest_deployment?.id || ''
  if (!deploymentId || deploymentOperating.value[deploymentId]) return
  deploymentOperating.value = { ...deploymentOperating.value, [deploymentId]: true }
  try {
    const result = await trainingApi.approveDeployment(deploymentId)
    applyDeploymentJobResult(record, result)
    Message.success('已审批部署')
  } catch (error: any) {
    Message.error(error?._message || '部署审批失败')
  } finally {
    const next = { ...deploymentOperating.value }
    delete next[deploymentId]
    deploymentOperating.value = next
  }
}

async function rejectDeployment(record: TrainingJobRow) {
  const deploymentId = record.latest_deployment?.id || ''
  if (!deploymentId || deploymentOperating.value[deploymentId]) return
  rejectTarget.value = record
}

async function submitRejectDeployment(reason: string) {
  const record = rejectTarget.value
  const deploymentId = record?.latest_deployment?.id || ''
  if (!record || !deploymentId) return
  deploymentOperating.value = { ...deploymentOperating.value, [deploymentId]: true }
  try {
    const result = await trainingApi.rejectDeployment(deploymentId, reason)
    applyDeploymentJobResult(record, result)
    Message.success('已驳回部署')
    rejectTarget.value = null
  } catch (error: any) {
    Message.error(error?._message || '部署驳回失败')
  } finally {
    const next = { ...deploymentOperating.value }
    delete next[deploymentId]
    deploymentOperating.value = next
  }
}

async function activateDeployment(record: TrainingJobRow) {
  const deploymentId = record.latest_deployment?.id || ''
  if (!deploymentId || deploymentOperating.value[deploymentId]) return
  deploymentOperating.value = { ...deploymentOperating.value, [deploymentId]: true }
  try {
    const result = await trainingApi.activateDeployment(deploymentId)
    applyDeploymentJobResult(record, result)
    Message.success('已激活部署')
  } catch (error: any) {
    Message.error(error?._message || '部署激活失败')
  } finally {
    const next = { ...deploymentOperating.value }
    delete next[deploymentId]
    deploymentOperating.value = next
  }
}

async function rollbackDeployment(record: TrainingJobRow) {
  const deploymentId = record.latest_deployment?.id || ''
  if (!deploymentId || deploymentOperating.value[deploymentId]) return
  Modal.confirm({
    title: '回滚模型部署',
    content: '回滚只影响模型部署资产，不会修改 Skill 代码或训练任务状态。',
    okText: '回滚',
    okButtonProps: { status: 'danger' } as any,
    async onOk() {
      deploymentOperating.value = { ...deploymentOperating.value, [deploymentId]: true }
      try {
        const result = await trainingApi.rollbackDeployment(deploymentId, '灰度或线上指标异常，执行人工回滚')
        applyDeploymentJobResult(record, result)
        Message.success('已回滚部署')
      } catch (error: any) {
        Message.error(error?._message || '部署回滚失败')
      } finally {
        const next = { ...deploymentOperating.value }
        delete next[deploymentId]
        deploymentOperating.value = next
      }
    },
  })
}

async function openJobLogs(record: TrainingJobRow) {
  if (!record.id || logLoading.value[record.id]) return
  logLoading.value = { ...logLoading.value, [record.id]: true }
  logsVisible.value = true
  logsLoadingModal.value = true
  logsTitle.value = `训练日志 · ${record.title || record.id}`
  logsText.value = ''
  try {
    const result = await trainingApi.jobLogs(record.id)
    const lines = normalizeList<Record<string, unknown>>(result?.lines)
    logsText.value = lines
      .map(item => {
        const ts = String(item.ts || '').trim()
        const type = String(item.type || 'log').trim()
        const message = String(item.message || '').trim()
        return [ts, type, message].filter(Boolean).join('  ')
      })
      .join('\n')
    if (result?.available === false && result?.error) {
      Message.warning(String(result.error))
    }
  } catch (error: any) {
    Message.error(error?._message || '训练日志加载失败')
  } finally {
    logsLoadingModal.value = false
    const next = { ...logLoading.value }
    delete next[record.id]
    logLoading.value = next
  }
}

async function openJobArtifacts(record: TrainingJobRow) {
  if (!record.id || artifactLoading.value[record.id]) return
  artifactLoading.value = { ...artifactLoading.value, [record.id]: true }
  artifactsVisible.value = true
  artifactsLoadingModal.value = true
  artifactsTitle.value = `训练产物 · ${record.title || record.id}`
  artifactJobId.value = record.id
  artifactRows.value = []
  try {
    const result = await trainingApi.jobArtifacts(record.id)
    artifactRows.value = normalizeList<Record<string, unknown>>(result?.items).map(normalizeArtifactRow)
    if (result?.download_proxy_available === false && artifactRows.value.length) {
      Message.warning('当前仅展示脱敏产物元数据')
    }
  } catch (error: any) {
    Message.error(error?._message || '训练产物加载失败')
  } finally {
    artifactsLoadingModal.value = false
    const next = { ...artifactLoading.value }
    delete next[record.id]
    artifactLoading.value = next
  }
}

async function downloadArtifact(item: ArtifactRow) {
  const jobId = artifactJobId.value
  if (!jobId || !item.id || !item.downloadable || artifactDownloading.value[item.id]) return
  artifactDownloading.value = { ...artifactDownloading.value, [item.id]: true }
  try {
    const response = await trainingApi.downloadJobArtifact(jobId, item.id)
    const blob = response?.data instanceof Blob ? response.data : new Blob([response?.data || ''])
    const disposition = String(response?.headers?.['content-disposition'] || '')
    saveBlob(blob, filenameFromDisposition(disposition, item.name || `${item.id}.bin`))
    Message.success('已开始下载模型产物')
  } catch (error: any) {
    Message.error(error?._message || '模型产物下载失败')
  } finally {
    const next = { ...artifactDownloading.value }
    delete next[item.id]
    artifactDownloading.value = next
  }
}

function openAgentDevice(id: string) {
  router.push(`/admin/agent-devices/${id}`)
}

function clearGatewayScope() {
  router.push('/training')
}

function openTrainingJob(id: string) {
  if (id) router.push(`/training/jobs/${id}`)
}

function stopFullHistoryPolling() {
  if (!fullHistoryPollTimer.value) return
  window.clearInterval(fullHistoryPollTimer.value)
  fullHistoryPollTimer.value = null
}

function syncFullHistoryPolling() {
  if (!fullHistoryShouldPoll.value) {
    stopFullHistoryPolling()
    return
  }
  if (fullHistoryPollTimer.value) return
  fullHistoryPollTimer.value = window.setInterval(() => {
    loadTraining({ silent: true })
  }, 8000)
}

watch(
  () => createJobForm.value.job_type,
  (jobType) => {
    if (!createJobVisible.value) return
    const selected = resourceRows.value.find(item => item.id === createJobForm.value.target_gateway_id)
    if (selected && gatewaySupportsTask(selected, jobType)) return
    createJobForm.value.target_gateway_id = compatibleGatewayRows.value[0]?.id || ''
  },
)

watch(fullHistoryShouldPoll, syncFullHistoryPolling)

onMounted(async () => {
  await loadTraining()
  syncFullHistoryPolling()
})
watch([gatewayQueryId, pipelineWindowDays], () => {
  loadTraining()
})
onBeforeUnmount(stopFullHistoryPolling)
</script>

<style scoped>
/* ─────────── design page chrome ─────────── */
.training-home {
  max-width: none;
  padding: 0;
  margin: 0;
  min-height: calc(100vh - 52px);
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}
.training-pagehead {
  padding: 22px 28px 16px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}
.training-crumbs {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.training-title {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  letter-spacing: 0;
  color: var(--ai-ink-1);
}
.training-sub {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.training-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.training-head-actions .ai-btn svg {
  width: 12px;
  height: 12px;
  flex: 0 0 12px;
}
.training-head-actions .ai-btn:disabled {
  cursor: wait;
  opacity: 0.72;
}
.training-head-actions .ai-pill {
  height: 18px;
  margin-left: 2px;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.training-pagebody {
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
  min-width: 0;
  overflow: hidden;
}

/* ─────────── pipeline strip ─────────── */
.training-pipeline-card {
  margin-bottom: 0;
  border: 1px solid var(--ai-border);
  border-top: 0;
  border-left: 0;
  border-right: 0;
  border-radius: 0;
  background: var(--ai-surface);
  overflow: hidden;
}
.pipeline-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--ai-border);
}
.pipeline-head-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.pipeline-head-icon {
  width: 13px;
  height: 13px;
  color: var(--ai-ink-3);
  flex: 0 0 13px;
}
.pipeline-head-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--ai-ink-1);
  letter-spacing: 0;
}
.pipeline-head-hint {
  font-size: 11px;
  color: var(--ai-ink-4);
}
.pipeline-head-right {
  display: flex;
  align-items: center;
  gap: 6px;
}
.pipeline-head-right .ai-pill {
  min-width: 38px;
  justify-content: center;
  white-space: nowrap;
}
.pipeline-period-button {
  appearance: none;
  cursor: pointer;
  font: inherit;
}
.pipeline-period-button:hover {
  border-color: var(--ai-ink-3);
  color: var(--ai-ink-1);
}
.pipeline-period-active {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  border-color: var(--ai-ink-1);
}
.pipeline-period-button.pipeline-period-active:hover {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  border-color: var(--ai-ink-1);
}
.pipeline-grid {
  padding: 20px 12px;
  display: grid;
  grid-template-columns: repeat(9, minmax(0, 1fr));
  position: relative;
}
.pipeline-stage {
  position: relative;
  appearance: none;
  border: 0;
  border-radius: 0;
  padding: 0 14px 0 12px;
  /* webkit 1px dashed 渲染成 solid，用 background gradient pattern 代替 */
  background-image: linear-gradient(to bottom, var(--ai-border-2) 50%, transparent 0%);
  background-position: right;
  background-size: 1px 6px;
  background-repeat: repeat-y;
  background-color: transparent;
  cursor: pointer;
  color: inherit;
  font: inherit;
  text-align: left;
  min-width: 0;
}
.pipeline-stage:hover .pipeline-stage-label,
.pipeline-stage-selected .pipeline-stage-label {
  color: var(--ai-accent);
}
.pipeline-stage:focus-visible {
  outline: 2px solid var(--ai-accent);
  outline-offset: 4px;
  border-radius: var(--ai-radius-s);
}
.pipeline-stage-selected::before {
  content: '';
  position: absolute;
  left: 8px;
  right: 20px;
  top: -10px;
  height: 2px;
  border-radius: 999px;
  background: var(--ai-accent);
}
.pipeline-stage-last {
  background-image: none;
}
.pipeline-stage-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}
.pipeline-stage-num {
  width: 18px;
  height: 18px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 10px;
  font-weight: 600;
  display: grid;
  place-items: center;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.pipeline-stage-num-active {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
}
.pipeline-stage-label {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0;
  color: var(--ai-ink-1);
}
.pipeline-stage-value {
  font-size: 30px;
  font-weight: 600;
  letter-spacing: 0;
  line-height: 1;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.pipeline-stage-value-warn {
  color: var(--ai-warn);
}
.pipeline-stage-value-ok {
  color: var(--ai-ok);
}
.pipeline-stage-desc {
  margin-top: 4px;
  font-size: 11px;
  color: var(--ai-ink-4);
}
.pipeline-stage-sub {
  margin-top: 2px;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.pipeline-stage-sub-warn {
  color: var(--ai-warn);
}
.pipeline-stage-sub-ok {
  color: var(--ai-ok);
}
.pipeline-arrow {
  position: absolute;
  right: -7px;
  top: 6px;
  width: 14px;
  height: 14px;
  background: var(--ai-surface);
  display: grid;
  place-items: center;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  font-size: 14px;
  line-height: 1;
  pointer-events: none;
}
.pipeline-detail-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(320px, 0.46fr);
  gap: 0;
  border-top: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}
.pipeline-detail-main {
  min-width: 0;
  padding: 14px 16px;
  border-right: 1px solid var(--ai-border);
}
.pipeline-detail-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
  margin-bottom: 10px;
}
.pipeline-detail-title strong {
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
  overflow-wrap: anywhere;
}
.pipeline-detail-count {
  flex: 0 0 auto;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  font-size: 11px;
  white-space: nowrap;
}
.pipeline-lane-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}
.pipeline-lane-node {
  min-width: 0;
  padding: 9px 10px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface);
}
.pipeline-lane-node span,
.pipeline-detail-metric span {
  display: block;
  color: var(--ai-ink-4);
  font-size: 11px;
  line-height: 1.3;
}
.pipeline-lane-node strong {
  display: block;
  min-width: 0;
  margin-top: 4px;
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.35;
  overflow-wrap: anywhere;
}
.pipeline-detail-side {
  min-width: 0;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pipeline-detail-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.pipeline-detail-metric {
  min-width: 0;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--ai-border);
}
.pipeline-detail-metric strong {
  display: block;
  margin-top: 3px;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 13px;
  font-weight: 700;
  line-height: 1.2;
  overflow-wrap: anywhere;
}
.pipeline-detail-metric strong.ok {
  color: var(--ai-ok);
}
.pipeline-detail-metric strong.warn {
  color: var(--ai-warn);
}
.pipeline-detail-metric strong.bad {
  color: var(--ai-bad);
}
.pipeline-detail-blockers {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.pipeline-detail-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}
.pipeline-detail-actions .ai-btn svg {
  width: 12px;
  height: 12px;
}

.automation-overview {
  border-radius: 0;
  border-left: 0;
  border-right: 0;
  border-top: 0;
  padding: 0;
  background: var(--ai-surface);
}
.automation-head {
  min-height: 48px;
  padding: 12px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--ai-border);
}
.automation-head-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  min-width: 0;
}
.automation-route-strip {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 18px minmax(0, 1fr) 18px minmax(0, 1fr) 18px minmax(0, 1fr);
  align-items: stretch;
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--ai-border);
}
.automation-route-node {
  min-width: 0;
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr);
  gap: 2px 8px;
  align-items: center;
  padding: 8px 10px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
}
.automation-route-node svg {
  width: 14px;
  height: 14px;
  color: var(--ai-ink-3);
  grid-row: span 2;
}
.automation-route-node strong {
  min-width: 0;
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.2;
}
.automation-route-node span {
  min-width: 0;
  color: var(--ai-ink-4);
  font-size: 11px;
  line-height: 1.2;
}
.automation-route-node.warn {
  border-color: color-mix(in srgb, var(--ai-warn) 28%, var(--ai-border));
  background: var(--ai-warn-soft);
}
.automation-route-arrow {
  display: grid;
  place-items: center;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
}
.automation-factor-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  padding: 12px 16px;
}
.automation-factor {
  min-width: 0;
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr);
  gap: 2px 8px;
  align-items: center;
}
.automation-factor-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ai-ok);
  grid-row: span 2;
}
.automation-factor.warn .automation-factor-dot {
  background: var(--ai-warn);
}
.automation-factor strong {
  min-width: 0;
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 600;
}
.automation-factor em {
  min-width: 0;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-style: normal;
}
.automation-blocker-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0 16px 12px;
}
.automation-blocker {
  display: inline-flex;
  align-items: center;
  min-height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  color: var(--ai-warn);
  background: var(--ai-warn-soft);
  font-size: 11px;
  font-weight: 500;
}
.automation-blocker.bad {
  color: var(--ai-bad);
  background: var(--ai-bad-soft);
}
.automation-output-panel,
.simulation-output-panel {
  margin: 0 16px 12px;
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
}
.automation-output-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.full-history-panel {
  border-radius: 0;
  border-left: 0;
  border-right: 0;
  border-top: 0;
  padding: 0;
  background: var(--ai-surface);
}
.full-history-route {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--ai-border);
}
.full-history-route .automation-route-node {
  background: var(--ai-surface-2);
}
.full-history-cycles {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  padding: 12px 16px;
}
.full-history-cycle {
  min-width: 0;
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  gap: 2px 8px;
  align-items: center;
  padding: 9px 10px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
}
.full-history-cycle span {
  grid-row: span 2;
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 999px;
  background: var(--ai-accent-soft);
  color: var(--ai-accent-ink);
  font-family: var(--ai-font-mono);
  font-weight: 700;
  font-size: 12px;
}
.full-history-cycle strong {
  min-width: 0;
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 600;
}
.full-history-cycle em {
  min-width: 0;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-style: normal;
  overflow-wrap: anywhere;
}
.full-history-cycle.ok {
  border-color: color-mix(in srgb, var(--ai-ok) 30%, var(--ai-border));
}
.full-history-cycle.warn {
  border-color: color-mix(in srgb, var(--ai-warn) 30%, var(--ai-border));
}
.full-history-status-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  padding: 0 16px 12px;
}
.full-history-status-grid div {
  min-width: 0;
  padding: 9px 10px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
}
.full-history-status-grid span {
  display: block;
  color: var(--ai-ink-4);
  font-size: 11px;
}
.full-history-status-grid strong {
  display: block;
  min-width: 0;
  margin-top: 3px;
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.full-history-error {
  margin: 0 16px 12px;
  padding: 8px 10px;
  border: 1px solid var(--ai-bad-soft);
  border-radius: 6px;
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.full-history-blockers {
  padding-top: 0;
}
.automation-output-main,
.simulation-output-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-width: 0;
}
.automation-output-main strong,
.simulation-output-head strong {
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 600;
}
.automation-output-main span,
.simulation-output-head span {
  color: var(--ai-ink-4);
  font-size: 11px;
}
.automation-output-metrics,
.simulation-output-grid,
.simulation-change-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}
.automation-output-metrics span,
.simulation-output-grid span,
.simulation-change {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  max-width: 100%;
  padding: 0 7px;
  border-radius: 4px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-3);
  font-size: 11px;
}
.automation-output-metrics .ok {
  color: var(--ai-ok);
}
.automation-output-metrics .bad {
  color: var(--ai-bad);
}
.simulation-output-panel {
  display: grid;
  gap: 8px;
}
.simulation-artifact {
  flex: 1 1 360px;
}
.simulation-artifact b,
.simulation-change {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ─────────── shared card spacing ─────────── */
.training-main-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(320px, 0.9fr);
  gap: 0;
  align-items: stretch;
}
.training-side-card,
.training-model-card {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 0;
  box-shadow: none;
  min-width: 0;
  overflow: hidden;
}
.training-card-head {
  min-height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--ai-border);
}
.training-card-title {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.2;
  letter-spacing: 0;
}
.training-card-sub {
  margin-top: 2px;
  color: var(--ai-ink-4);
  font-size: 11.5px;
  line-height: 1.3;
}
.training-link-btn,
.training-inline-link {
  appearance: none;
  border: 0;
  background: transparent;
  color: var(--ai-ink-3);
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  padding: 0;
}
.training-link-btn:hover,
.training-inline-link:hover {
  color: var(--ai-ink-1);
}
.training-compact-table,
.model-eval-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}
.training-compact-table th,
.training-compact-table td,
.model-eval-table th,
.model-eval-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--ai-border);
  text-align: left;
  vertical-align: middle;
  font-size: 12px;
}
.training-compact-table th,
.model-eval-table th {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 600;
  background: var(--ai-surface-2);
}
.training-compact-table tr:last-child td,
.model-eval-table tr:last-child td {
  border-bottom: 0;
}
.resource-overflow-link {
  width: 100%;
  height: 34px;
  border: 0;
  border-top: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-3);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
.resource-overflow-link:hover {
  color: var(--ai-ink-1);
  background: var(--ai-surface-2);
}
.training-compact-table th:nth-child(2),
.training-compact-table td:nth-child(2) {
  width: 68px;
}
.training-compact-table th:nth-child(3),
.training-compact-table td:nth-child(3) {
  width: 68px;
}
.training-compact-table th:nth-child(4),
.training-compact-table td:nth-child(4) {
  width: 82px;
}
.dept-data-main {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.dept-data-main span:first-child,
.model-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dept-data-sub {
  display: block;
  margin-top: 2px;
  color: var(--ai-ink-4);
  font-size: 10.5px;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mono-cell {
  display: block;
  color: var(--ai-ink-2);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.fresh-ok {
  color: var(--ai-ok);
}
.fresh-muted {
  color: var(--ai-ink-4);
}
.healthbar {
  width: 100%;
  height: 5px;
  border-radius: 999px;
  overflow: hidden;
  background: var(--ai-surface-3);
}
.healthbar > span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--ai-ok);
}
.healthbar > span.warn {
  background: var(--ai-warn);
}
.model-card-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}
.job-card-body-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--ai-border);
}
.training-job-list {
  display: flex;
  flex-direction: column;
}
.training-job-row {
  padding: 14px 16px;
  border-bottom: 1px solid var(--ai-border);
}
.training-job-row:last-child {
  border-bottom: 0;
}
.training-job-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}
.training-job-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}
.training-job-title {
  appearance: none;
  border: 0;
  background: transparent;
  padding: 0;
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.training-job-title:hover {
  color: var(--ai-accent);
}
.training-job-metrics {
  display: flex;
  align-items: center;
  gap: 24px;
  margin-bottom: 8px;
  color: var(--ai-ink-2);
  font-size: 12px;
  flex-wrap: wrap;
}
.training-job-metrics span {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  min-width: 0;
}
.training-job-metrics em {
  color: var(--ai-ink-4);
  font-style: normal;
}
.training-job-metrics b {
  font-weight: 500;
  color: var(--ai-ink-1);
}
.training-job-detail-line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin-bottom: 8px;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  flex-wrap: wrap;
}
.training-job-detail-line span:not(.ai-pill) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.job-automation-steps {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.job-auto-step {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 20px;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 11px;
  line-height: 1;
  white-space: nowrap;
}
.job-auto-step i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ai-ink-4);
}
.job-auto-step.status-completed {
  color: var(--ai-ok);
  background: var(--ai-ok-soft);
}
.job-auto-step.status-completed i {
  background: var(--ai-ok);
}
.job-auto-step.status-running,
.job-auto-step.status-pending {
  color: var(--ai-warn);
  background: var(--ai-warn-soft);
}
.job-auto-step.status-running i,
.job-auto-step.status-pending i {
  background: var(--ai-warn);
}
.job-auto-step.status-blocked {
  color: var(--ai-bad);
  background: var(--ai-bad-soft);
}
.job-auto-step.status-blocked i {
  background: var(--ai-bad);
}
.training-job-progress {
  height: 4px;
  border-radius: 999px;
  overflow: hidden;
  background: var(--ai-surface-3);
  margin-bottom: 8px;
}
.training-job-progress span {
  display: block;
  height: 100%;
  background: var(--ai-accent);
  border-radius: inherit;
}
.training-job-curve {
  display: block;
  width: 100%;
  height: 34px;
  margin-bottom: 6px;
}
.training-job-curve polyline {
  fill: none;
  stroke: var(--ai-ink-3);
  stroke-width: 1;
}
.training-job-curve polyline.accent {
  stroke: var(--ai-accent);
  stroke-width: 1.5;
}
.training-job-curve line {
  stroke: var(--ai-border);
}
.training-job-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.training-job-actions .push-right {
  margin-left: auto;
}
.training-job-actions .ai-btn:disabled {
  opacity: 0.55;
  cursor: wait;
}
.training-job-actions .ai-btn.danger {
  color: var(--ai-bad);
}
.training-model-card {
  margin-top: 0;
  border-top: 0;
  border-left: 0;
  border-right: 0;
}
.model-eval-table th:nth-child(2),
.model-eval-table td:nth-child(2) {
  width: 78px;
}
.model-eval-table th:nth-child(3),
.model-eval-table td:nth-child(3) {
  width: 96px;
}
.model-eval-table th:nth-child(4),
.model-eval-table td:nth-child(4) {
  width: 120px;
}
.model-eval-table th:nth-child(5),
.model-eval-table td:nth-child(5) {
  width: 120px;
}
.model-eval-table th:nth-child(6),
.model-eval-table td:nth-child(6) {
  width: 94px;
}
.model-eval-table th:nth-child(7),
.model-eval-table td:nth-child(7) {
  width: 190px;
}
.model-progress {
  display: grid;
  grid-template-columns: 42px minmax(40px, 1fr);
  align-items: center;
  gap: 8px;
}
.model-dept-pill {
  font-size: 10.5px;
}
.model-actions {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
}
.resource-card,
.job-card {
  margin-top: 0;
}
.job-card {
  grid-column: 1;
  grid-row: 1;
  min-width: 0;
  margin-top: 0;
  border-top: 0 !important;
  border-left: 0 !important;
  border-radius: 0 !important;
}
.resource-card {
  grid-column: 2;
  grid-row: 1;
  min-width: 0;
  border-top: 0;
  border-right: 0;
  border-radius: 0;
}
.job-extra-btn svg {
  width: 12px;
  height: 12px;
}
.job-extra-btn:disabled {
  cursor: wait;
  opacity: 0.72;
}
.pipeline-filter-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  padding: 8px 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
}
.pipeline-filter-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--ai-ink-3);
  font-size: 12px;
}

/* ─────────── gateway scope bar ─────────── */
.gateway-scope-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 0;
  margin-bottom: 0;
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-left: 0;
  border-right: 0;
  border-radius: 0;
  background: var(--ai-surface-2);
}
.gateway-scope-main {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.gateway-scope-copy {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.gateway-scope-copy strong {
  color: var(--ai-ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  font-size: 13px;
}
.gateway-scope-copy span {
  color: var(--ai-ink-3);
  font-size: 12px;
}
.gateway-scope-bar + .job-card {
  margin-top: 0;
}

/* ─────────── table cells ─────────── */
.gateway-cell {
  min-width: 0;
}
.gateway-title-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.gateway-name {
  color: var(--ai-ink-1);
  font-weight: 600;
  font-size: 13px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.job-title-link {
  display: inline-flex;
  max-width: 100%;
  font-weight: 600;
  font-size: 13px;
}
.job-title-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.job-dept-pill {
  font-size: 10.5px;
  flex-shrink: 0;
}
.gateway-meta {
  margin-top: 2px;
  color: var(--ai-ink-4);
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.job-id-mono,
.job-model-mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 11.5px;
  color: var(--ai-ink-3);
}
.job-meta-sep {
  color: var(--ai-ink-5);
}
.job-param-line,
.artifact-inline {
  display: block;
  margin-top: 3px;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.job-process-cell {
  display: grid;
  gap: 4px;
  min-width: 140px;
  color: var(--ai-ink-3);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.gateway-route-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex-wrap: wrap;
  font-family: var(--ai-font-mono);
  font-size: 12px;
}
.dataset-cell {
  display: grid;
  gap: 2px;
}
.vram-cell {
  min-width: 180px;
  display: grid;
  grid-template-columns: minmax(80px, 1fr) auto;
  align-items: center;
  gap: 8px;
  color: var(--ai-ink-2);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.active-job-cell {
  min-width: 160px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.active-job-link {
  min-width: 0;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.muted-text {
  color: var(--ai-ink-4);
}
.logs-panel {
  max-height: 520px;
  overflow: auto;
  padding: 12px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-family: var(--ai-font-mono);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
.artifact-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.artifact-item {
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
}
.artifact-main,
.artifact-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.artifact-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-1);
}
.artifact-meta {
  margin-top: 6px;
  color: var(--ai-ink-4);
  font-size: 12px;
  flex-wrap: wrap;
  word-break: break-all;
  font-family: var(--ai-font-mono);
}

@media (max-width: 1024px) {
  .training-main-grid {
    grid-template-columns: 1fr;
  }
  .job-card,
  .resource-card {
    grid-column: 1;
    grid-row: auto;
  }
  .pipeline-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 16px 0;
  }
  .pipeline-detail-panel {
    grid-template-columns: 1fr;
  }
  .pipeline-detail-main {
    border-right: 0;
    border-bottom: 1px solid var(--ai-border);
  }
  .automation-route-strip,
  .automation-factor-grid,
  .full-history-route,
  .full-history-cycles,
  .full-history-status-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .automation-route-arrow {
    display: none;
  }
  .pipeline-arrow {
    display: none;
  }
}
@media (max-width: 640px) {
  .pipeline-head {
    align-items: flex-start;
    flex-direction: column;
  }
  .pipeline-head-left {
    flex-wrap: wrap;
  }
  .pipeline-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .pipeline-lane-grid,
  .pipeline-detail-metrics {
    grid-template-columns: 1fr;
  }
  .pipeline-detail-actions {
    justify-content: flex-start;
  }
  .automation-route-strip,
  .automation-factor-grid,
  .full-history-route,
  .full-history-cycles,
  .full-history-status-grid {
    grid-template-columns: 1fr;
  }
  .training-card-head,
  .model-card-actions,
  .model-actions {
    align-items: flex-start;
    flex-direction: column;
  }
  .model-eval-table {
    min-width: 720px;
  }
  .training-model-card {
    overflow-x: auto;
  }
  .pipeline-stage {
    /* 重置桌面端右侧 dashed pattern，改成底部 dashed pattern（窄屏二列布局） */
    background-image: linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%);
    background-position: bottom;
    background-size: 6px 1px;
    background-repeat: repeat-x;
    padding-bottom: 14px;
  }
}
@media (max-width: 768px) {
  .training-pagehead {
    align-items: flex-start;
    flex-direction: column;
    padding: 18px 16px 14px;
  }
  .training-pagebody {
    padding: 0;
  }
  .training-head-actions {
    justify-content: flex-start;
  }
  .gateway-scope-bar,
  .gateway-scope-main,
  .gateway-scope-copy {
    align-items: flex-start;
  }
  .gateway-scope-bar,
  .gateway-scope-copy {
    flex-direction: column;
  }
  .vram-cell {
    min-width: 140px;
    grid-template-columns: 1fr;
    gap: 4px;
  }
}
</style>
