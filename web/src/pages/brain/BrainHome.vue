<template>
  <div class="brain-page ai-main">
    <div
      class="brain-body"
      :class="{ 'is-dragging': isDragging }"
      @dragenter="onDragEnter"
      @dragover="onDragOver"
      @dragleave="onDragLeave"
      @drop="onDrop"
    >
      <!-- 拖拽时的提示遮罩 -->
      <div v-if="isDragging" class="brain-drag-overlay">
        <SfShellIcon name="attach" class="brain-drag-icon" />
        <span>拖到这里上传</span>
        <span class="brain-drag-hint">图片 / PDF / 文本，单个 ≤5MB，总 ≤10MB</span>
      </div>

      <!-- Col 1: 按部门 sidebar -->
      <aside class="brain-dept-rail ai-sidebar" :class="{ 'brain-dept-rail--open': sidebarOpen }">
        <div class="brain-dept-rail-head">
          <span class="brain-dept-rail-title">Agent</span>
          <button class="brain-dept-rail-close" @click="sidebarOpen = false" aria-label="关闭导航">
            <SfShellIcon name="x" />
          </button>
        </div>
        <div class="ai-side-group">
          <div class="ai-side-label">按部门</div>
          <button
            v-for="dept in departmentGroups"
            :key="dept.key"
            type="button"
            class="ai-side-item brain-dept-item"
            :class="{ active: dept.key === selectedDepartmentKey }"
            :data-testid="`brain-dept-${dept.key}`"
            @click="selectDepartment(dept.key)"
          >
            <SfShellIcon name="dept" class="ic brain-dept-icon" />
            <span class="brain-dept-name">{{ dept.label }}</span>
            <span
              class="brain-dept-count"
              :class="{ ok: dept.online > 0, idle: dept.online === 0 }"
            >{{ dept.online }}/{{ dept.total }}</span>
          </button>
        </div>
        <div class="ai-side-group">
          <div class="ai-side-label">视图</div>
          <button
            type="button"
            class="ai-side-item"
            :class="{ active: !selectedDepartmentKey }"
            @click="selectDepartment('')"
          >
            <SfShellIcon name="layers" class="ic brain-side-icon" />
            <span>全部 Agent</span>
            <span class="count">{{ agentStats.total }}</span>
          </button>
          <button
            type="button"
            class="ai-side-item"
            :class="{ active: showOnlyOffline }"
            @click="toggleOfflineFilter"
          >
            <SfShellIcon name="warn" class="ic brain-side-icon" />
            <span>离线 / 异常</span>
            <!-- 同 inbox sidebar：>0 才红色，避免 0 时误导。 -->
            <span class="count" :class="{ 'brain-count-bad': (agentStats.total - agentStats.online) > 0 }">{{ agentStats.total - agentStats.online }}</span>
          </button>
        </div>
      </aside>

      <!-- Col 2: 部门内 Agent 列表（按 purpose 分组） -->
      <aside class="brain-agent-rail">
        <button class="brain-dept-rail-toggle" @click="sidebarOpen = !sidebarOpen" aria-label="打开导航">
          <SfShellIcon name="list" />
          <span>{{ selectedDepartmentLabel || '导航' }}</span>
        </button>
        <header class="brain-rail-head">
          <div class="brain-rail-head-top">
            <div class="brain-rail-title-block">
              <div class="brain-rail-title">{{ selectedDepartmentLabel }}</div>
              <div class="brain-rail-sub">
                {{ instancesInDepartment.length }} 个运行 Agent · {{ businessAgentsForCurrentDepartment.length }} 个业务 Agent · {{ deptOnlineCount }} 在线
              </div>
            </div>
            <a-button
              v-if="canManageAgents"
              size="mini"
              class="brain-new-btn"
              @click="$router.push(agentCoveragePath())"
            >
              <template #icon><SfShellIcon name="plus" class="brain-button-icon" /></template>
              新建
            </a-button>
          </div>
          <div class="brain-purpose-pills">
            <button
              v-for="tab in purposeTabs"
              :key="tab.value"
              type="button"
              class="brain-purpose-pill"
              :class="{ active: agentPurposeFilter === tab.value }"
              @click="agentPurposeFilter = tab.value"
            >
              {{ tab.label }}
              <span class="brain-purpose-count">{{ purposeCount(tab.value) }}</span>
            </button>
          </div>
        </header>

        <div class="brain-agent-scroll">
          <template v-for="group in groupedAgentsInDept" :key="group.key">
            <div v-if="group.items.length" class="brain-agent-group">
              <div class="brain-agent-group-head">
                <span class="brain-agent-group-dot" :style="{ background: group.color }" />
                <span class="brain-agent-group-label">{{ group.label }}</span>
                <span class="brain-agent-group-desc">· {{ group.desc }}</span>
              </div>
              <button
                v-for="item in group.items"
                :key="item.id"
                type="button"
                class="brain-agent-row"
                :class="{ active: item.id === selectedInstanceId, offline: !item.bridge_online }"
                :data-testid="`brain-agent-${item.id}`"
                @click="selectInstance(item)"
              >
                <span
                  class="brain-agent-row-dot"
                  :class="agentStatusTone(item)"
                  aria-hidden="true"
                />
                <span class="brain-agent-row-main">
                  <span class="brain-agent-row-name">{{ item.name || item.id }}</span>
                  <span class="brain-agent-row-meta">
                    <span>{{ instanceSkillsLabel(item) }}</span>
                    <span>·</span>
                    <span class="mono">{{ instanceHardwareLabel(item) }}</span>
                    <span class="brain-agent-row-last">{{ instanceLastSeen(item) }}</span>
                  </span>
                </span>
              </button>
            </div>
          </template>
          <div v-if="!instancesInDepartment.length" class="brain-agent-empty">
            该部门暂无运行 Agent
          </div>
          <div v-else-if="!groupedAgentsInDept.some(g => g.items.length)" class="brain-agent-empty">
            没有匹配的 Agent
          </div>
        </div>
      </aside>

      <!-- Col 3: 当前 Agent 详情 / 对话 -->
      <main class="brain-detail">
        <header class="brain-detail-head">
          <div class="brain-detail-head-row">
            <div class="brain-detail-avatar">
              <SfShellIcon name="bolt" />
            </div>
            <div class="brain-detail-id-block">
              <div class="brain-detail-title-row">
                <span class="brain-detail-title">{{ selectedInstance?.name || '选择 Agent' }}</span>
                <span class="brain-status" :class="statusClass">
                  <span class="brain-status-dot" />
                  {{ statusLabel }}
                </span>
                <span
                  v-if="selectedInstance"
                  class="brain-purpose-chip"
                  :class="agentPurposeClass(selectedInstance)"
                >{{ agentPurposeLabel(agentPurposeValue(selectedInstance)) }}</span>
                <span v-if="selectedInstance" class="mono mono-id">
                  {{ selectedInstance.id }} · {{ runtimeLabel(selectedInstance) }}
                </span>
              </div>
              <div class="brain-detail-facts">
                <span class="brain-fact">
                  部门 <b>{{ departmentLabel(selectedInstance) }}</b>
                </span>
                <span class="brain-fact">
                  Agent <b>{{ selectedAgentDisplayName }}</b>
                </span>
                <span v-if="selectedInstance" class="brain-fact">
                  运行时 <b>{{ runtimeLabel(selectedInstance) }}</b>
                </span>
                <span v-if="selectedInstance?.last_heartbeat" class="brain-fact">
                  最近活跃 <b>{{ formatLastHeartbeat(selectedInstance.last_heartbeat) }}</b>
                </span>
              </div>
            </div>
            <div class="brain-detail-actions">
              <button type="button" class="ai-btn sm" @click="toggleHistory">
                <SfShellIcon name="hist" />
                历史 {{ history.length ? `(${history.length})` : '' }}
              </button>
              <button type="button" class="ai-btn sm" :disabled="loadingInstances" @click="reloadInstances">
                <SfShellIcon name="refresh" />
                刷新
              </button>
              <button
                v-if="canManageAgents"
                type="button"
                class="ai-btn sm"
                @click="$router.push('/admin/agent-devices')"
              >
                Agent终端
              </button>
              <button
                v-if="selectedTrainingSummary?.gateway"
                type="button"
                class="ai-btn sm"
                @click="$router.push(trainingConsolePath)"
              >
                <SfShellIcon name="gpu" />
                训练
              </button>
              <button
                type="button"
                class="ai-btn sm"
                :class="{ primary: rightMode !== 'chat' }"
                :disabled="!canSubmit"
                @click="toggleRightMode"
              >
                <SfShellIcon :name="rightMode === 'chat' ? 'arrowl' : 'send'" />
                {{ rightMode === 'chat' ? '返回详情' : '对话' }}
              </button>
            </div>
          </div>
        </header>

        <!-- detail 模式 -->
        <section v-if="rightMode === 'detail'" class="brain-detail-body">
          <div class="brain-detail-grid">
            <!-- 可调用 Skills（对齐设计稿 agent.jsx 左侧主卡片） -->
            <article class="ai-card brain-card-skills">
              <header class="ai-card-h">
                <span class="t">可调用 Skills</span>
                <span class="s">{{ allowedSkillsCaption }}</span>
                <div class="brain-card-h-actions">
                  <a-button
                    size="mini"
                    class="brain-config-btn"
                    :disabled="!canManageAgents"
                    @click="$router.push(agentCoveragePath())"
                  >配置范围</a-button>
                </div>
              </header>
              <div v-if="loadingAgentSkills" class="brain-card-empty">
                <div>加载 Skill 范围…</div>
              </div>
              <div v-else-if="!allowedSkills.length" class="brain-card-empty">
                <div>暂无可调用 Skill</div>
                <div class="brain-card-empty-sub">
                  当前 Agent 未上报可调用 Skill
                </div>
              </div>
              <table v-else class="ai-table brain-skills-table">
                <thead>
                  <tr>
                    <th>Skill</th>
                    <th style="width: 64px">风险</th>
                    <th style="width: 72px">来源</th>
                    <th style="width: 86px">今日调用</th>
                    <th style="width: 64px"></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="skill in allowedSkills" :key="skill.id">
                    <td>
                      <div class="brain-skill-cell">
                        <span class="brain-skill-name">{{ skill.name }}</span>
                        <span class="mono mono-id">{{ skill.id }}</span>
                      </div>
                    </td>
                    <td>
                      <span class="ai-pill brain-skill-risk" :class="`risk-${skill.risk.toLowerCase()}`">
                        {{ skill.risk }}
                      </span>
                    </td>
                    <td>
                      <span v-if="skill.shared" class="ai-pill accent">共享</span>
                      <span v-else class="tiny muted brain-skill-src">{{ skill.src }}</span>
                    </td>
                    <td><span class="mono">{{ skill.uses }}</span></td>
                    <td class="brain-skill-action">
                      <a-button
                        size="mini"
                        :disabled="!skill.id || skill.id === 'unknown'"
                        @click="$router.push(`/skills/${encodeURIComponent(skill.id)}`)"
                      >试运行</a-button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </article>

            <div class="brain-detail-right-stack">
              <!-- 运行指标 · 近 24h（对齐设计稿：KPI 三栏 + sparkline） -->
              <article class="ai-card">
                <header class="ai-card-h">
                  <span class="t">运行指标 · 近 24h</span>
                  <span class="s">Bridge 实时上报</span>
                </header>
                <div class="brain-kpi-grid">
                  <div
                    v-for="(stat, idx) in metricsStats"
                    :key="stat.key"
                    class="brain-kpi"
                    :style="{ borderRight: idx < metricsStats.length - 1 ? '1px solid var(--ai-border)' : '0' }"
                  >
                    <span class="brain-kpi-label">{{ stat.label }}</span>
                    <span class="brain-kpi-value mono">{{ stat.value }}</span>
                    <span class="brain-kpi-delta" :class="{ ok: stat.ok }">{{ stat.delta }}</span>
                  </div>
                </div>
                <div class="brain-kpi-spark">
                  <svg width="100%" height="60" viewBox="0 0 300 60" preserveAspectRatio="none">
                    <polyline
                      points="0,46 25,42 50,38 75,40 100,30 125,32 150,22 175,28 200,18 225,24 250,14 275,18 300,12"
                      fill="none"
                      stroke="var(--ai-accent)"
                      stroke-width="1.5"
                    />
                    <polyline
                      points="0,50 25,50 50,46 75,50 100,42 125,46 150,38 175,42 200,32 225,38 250,28 275,32 300,26"
                      fill="none"
                      stroke="var(--ai-ink-4)"
                      stroke-width="1"
                      stroke-dasharray="2 3"
                    />
                  </svg>
                </div>
              </article>

              <!-- 部门 Agent 覆盖 -->
              <article v-if="currentCoverageRow" class="ai-card">
                <header class="ai-card-h">
                  <span class="t">部门 Agent 覆盖</span>
                  <button
                    v-if="canManageAgents"
                    type="button"
                    class="brain-text-link brain-card-link"
                    @click="$router.push(agentCoveragePath())"
                  >管理</button>
                </header>
                <div class="brain-coverage-list-card">
                  <button
                    v-for="item in coverageCapabilityRows"
                    :key="item.key"
                    type="button"
                    class="brain-coverage-lane"
                    :class="item.tone"
                    :disabled="!canManageAgents"
                    @click="$router.push(agentCoveragePath(item.key))"
                  >
                    <span>{{ item.label }}</span>
                    <strong>{{ item.text }}</strong>
                  </button>
                </div>
              </article>

              <!-- 部门业务分析 Agent：绑定 Skill / prompt / 维度，不作为运行终端连接。 -->
              <article v-if="businessAgentsForCurrentDepartment.length" class="ai-card brain-card-branch">
                <header class="ai-card-h">
                  <span class="t">业务分析 Agent</span>
                  <span class="s">{{ businessAgentsForCurrentDepartment.length }} 个可管理 prompt</span>
                  <button
                    v-if="primaryEditableBusinessAgent"
                    type="button"
                    class="brain-business-edit-main"
                    @click="openAnalysisAgentEdit(primaryEditableBusinessAgent)"
                  >
                    <SfShellIcon name="edit" />
                    编辑 Agent
                  </button>
                </header>
                <div class="brain-branch-list">
                  <div
                    v-for="agent in businessAgentsForCurrentDepartment.slice(0, 5)"
                    :key="agent.id || agent.name"
                    class="brain-branch-row brain-business-agent-row"
                  >
                    <span class="brain-branch-main">
                      <span class="brain-branch-name">{{ agent.name || agent.id }}</span>
                      <span class="mono mono-id">
                        {{ agent.skill_id || '未绑定 Skill' }} · {{ agent.prompt_version || 'default' }}
                      </span>
                      <span class="mono mono-id">
                        {{ analysisAgentControlSummary(agent) }}
                      </span>
                      <span class="brain-business-prompt">{{ analysisAgentPromptGoal(agent) }}</span>
                      <span class="brain-business-capabilities">
                        <span
                          v-for="cap in analysisAgentCapabilities(agent).slice(0, 4)"
                          :key="cap.key || cap.label"
                          class="brain-capability"
                          :class="{ on: cap.enabled !== false }"
                        >{{ cap.label }}</span>
                      </span>
                      <span
                        v-if="analysisAgentValidation[agent.id]"
                        class="brain-business-validation"
                        :class="{ ok: analysisAgentValidation[agent.id]?.ok, bad: !analysisAgentValidation[agent.id]?.ok }"
                      >
                        {{ analysisAgentValidationLabel(analysisAgentValidation[agent.id]) }}
                      </span>
                    </span>
                    <span class="ai-pill brain-branch-type">{{ agent.owner_name || '部门管理' }}</span>
                    <span class="ai-pill ok">{{ agent.status || 'active' }}</span>
                    <span class="brain-business-agent-actions">
                      <button
                        v-if="agent.permissions?.read"
                        type="button"
                        class="brain-text-link"
                        :disabled="validatingAnalysisAgentId === agent.id"
                        @click="validateAnalysisAgent(agent)"
                      >{{ validatingAnalysisAgentId === agent.id ? '验证中' : '验证' }}</button>
                      <button
                        v-if="analysisAgentCanEdit(agent)"
                        type="button"
                        class="brain-business-edit-btn"
                        @click="openAnalysisAgentEdit(agent)"
                      >
                        <SfShellIcon name="edit" />
                        编辑 Agent
                      </button>
                      <button
                        v-if="agent.skill_id"
                        type="button"
                        class="brain-text-link"
                        @click="$router.push(`/skills/${encodeURIComponent(agent.skill_id)}`)"
                      >Skill</button>
                    </span>
                  </div>
                </div>
              </article>

              <!-- AI 自动发现的 Agent 化候选：自动添加到 Agent 工作台的治理入口 -->
              <article v-if="agentizationCandidates.length" class="ai-card brain-card-branch">
                <header class="ai-card-h">
                  <span class="t">AI 自动添加候选</span>
                  <span class="s">{{ agentizationCandidates.length }} 个待治理 Agent</span>
                </header>
                <div class="brain-branch-list">
                  <button
                    v-for="item in agentizationCandidates"
                    :key="item.id"
                    type="button"
                    class="brain-branch-row"
                    @click="$router.push(`/learning-flow?candidate_id=${encodeURIComponent(item.id)}&target_type=agent`)"
                  >
                    <span class="brain-branch-main">
                      <span class="brain-branch-name">{{ item.title || 'Agent 化候选' }}</span>
                      <span class="mono mono-id">
                        {{ agentCandidateImpact(item) }}
                      </span>
                      <span class="mono mono-id">
                        {{ agentCandidateDraftLabel(item) }}
                      </span>
                    </span>
                    <span class="ai-pill brain-branch-type">{{ item.risk_level || 'R2' }}</span>
                    <span class="ai-pill ok">{{ item.status || 'reviewing' }}</span>
                  </button>
                </div>
              </article>

              <!-- Agent 分支（设计稿没有，但当前后端的真实信息，保留为紧凑列表） -->
              <article v-if="agents.length > 1" class="ai-card brain-card-branch">
                <header class="ai-card-h">
                  <span class="t">Agent 分支</span>
                  <span class="s">{{ agents.length }} 个</span>
                </header>
                <div class="brain-branch-list">
                  <button
                    v-for="agent in agents"
                    :key="agent.id || agent.name"
                    type="button"
                    class="brain-branch-row"
                    :class="{ active: (agent.id || agent.name) === selectedAgentId }"
                    @click="onPickAgent(agent)"
                  >
                    <span class="brain-branch-main">
                      <span class="brain-branch-name">
                        {{ agent.identity?.name || agent.displayName || agent.name || agent.id }}
                      </span>
                      <span class="mono mono-id">{{ agent.id || agent.name }}</span>
                    </span>
                    <span class="ai-pill brain-branch-type">{{ agentBranchTypeLabel(agent) }}</span>
                    <span v-if="(agent.id || agent.name) === selectedAgentId" class="ai-pill ok">当前</span>
                  </button>
                </div>
              </article>

              <!-- 能力 -->
              <article class="ai-card brain-card-capability">
                <header class="ai-card-h">
                  <span class="t">能力</span>
                </header>
                <div class="brain-capability-list">
                  <span v-if="analysisEnabled(selectedInstance)" class="brain-capability on">分析</span>
                  <span v-if="selectedTrainingSummary?.gateway" class="brain-capability on">训练</span>
                  <span v-if="selectedTrainingGpuCount" class="brain-capability">GPU {{ selectedTrainingGpuCount }}</span>
                  <span v-if="selectedTrainingSummary?.worker_count" class="brain-capability">
                    Worker {{ selectedTrainingSummary.worker_count }}
                  </span>
                  <span v-for="task in selectedTrainingTasks" :key="task" class="brain-capability">
                    {{ task }}
                  </span>
                  <span v-if="!analysisEnabled(selectedInstance) && !selectedTrainingSummary?.gateway" class="brain-capability muted">
                    标准执行
                  </span>
                </div>
              </article>

              <!-- 运行中训练 -->
              <article v-if="selectedActiveTrainingJobs.length" class="ai-card brain-card-active-job">
                <header class="ai-card-h">
                  <span class="t">运行中训练</span>
                  <strong class="brain-card-strong">{{ selectedActiveTrainingJobCount }}</strong>
                </header>
                <div class="brain-active-job-list">
                  <button
                    v-for="job in selectedActiveTrainingJobs.slice(0, 4)"
                    :key="job.id || job.job_id"
                    type="button"
                    class="brain-active-job"
                    @click="$router.push(`/training/jobs/${job.id || job.job_id}`)"
                  >
                    <span>{{ job.name || job.dataset_name || job.id || job.job_id }}</span>
                    <small>{{ job.status || 'running' }}</small>
                  </button>
                </div>
              </article>

              <!-- 最近对话 -->
              <article class="ai-card brain-card-recent">
                <header class="ai-card-h">
                  <span class="t">最近对话</span>
                  <button
                    v-if="recentHistory.length"
                    type="button"
                    class="brain-text-link brain-card-link"
                    @click="toggleHistory"
                  >查看全部</button>
                </header>
                <div v-if="recentHistory.length" class="brain-recent-card-list">
                  <button
                    v-for="item in recentHistory.slice(0, 4)"
                    :key="item.ts"
                    type="button"
                    class="brain-recent-line"
                    @click="loadHistoryItemAndSwitch(item)"
                  >
                    <SfShellIcon name="send" class="brain-recent-icon" />
                    <span>{{ truncate(item.question, 56) }}</span>
                    <small>{{ formatHistoryTime(item.ts) }}</small>
                  </button>
                </div>
                <div v-else class="brain-card-empty">暂无对话记录</div>
              </article>
            </div>
          </div>

          <div v-if="!instances.length && !loadingInstances" class="brain-detail-warning">
            <SfShellIcon name="warn" />
            <span>没有可用的代理设备。请先在
              <a v-if="userStore.isAdmin" class="brain-link" @click="$router.push('/admin/agent-devices')">设置 → Agent终端</a>
              创建实例。</span>
          </div>
        </section>

        <!-- chat 模式：保留旧对话面板，行为不变 -->
        <section v-else class="brain-chat-wrap" :class="{ 'is-empty': !messages.length, 'has-model-context': hasModelChatContext }">
        <section v-if="hasModelChatContext" class="brain-model-context">
          <div>
            <span class="brain-model-kicker">模型对话</span>
            <strong>{{ modelChatContext.model_family || modelChatContext.model_deployment_id }}</strong>
            <small v-if="modelChatContext.model_deployment_id">{{ modelChatContext.model_deployment_id }}</small>
          </div>
          <button class="brain-text-link" :disabled="Boolean(modelChatDisabledReason)" @click="draft = modelChatPrompt">
            试问效果
          </button>
        </section>
        <div v-if="modelChatDisabledReason" class="brain-model-warning">
          <SfShellIcon name="warn" />
          <span>{{ modelChatDisabledReason }}</span>
        </div>
        <!-- 对话流（用户在右、智脑在左，各带名称） -->
        <div v-if="messages.length" ref="chatLogRef" class="brain-chat-log">
          <div
            v-for="(msg, i) in messages"
            :key="`${i}-${msg.role}`"
            class="brain-msg"
            :class="msg.role"
          >
            <div class="brain-msg-header">
              <span v-if="msg.role !== 'user'" class="brain-msg-avatar assistant">
                <SfShellIcon name="bolt" />
              </span>
              <span class="brain-msg-name">{{ msgSenderName(msg) }}</span>
              <span v-if="msg.role === 'user'" class="brain-msg-avatar user">
                {{ (userStore.userInfo?.name || userStore.userInfo?.username || '?')[0] }}
              </span>
            </div>

            <!-- 思考过程（仅 AI 消息，限高 + 折叠，默认展开） -->
            <div
              v-if="msg.role !== 'user' && msg.thinking"
              class="brain-msg-thinking"
              :class="{ expanded: isThinkingOpen(i) }"
            >
              <button class="brain-thinking-head" @click="toggleThinking(i)">
                <SfShellIcon name="chev" class="brain-chevron" :class="{ 'is-up': isThinkingOpen(i) }" />
                <span>思考过程</span>
                <span class="brain-thinking-len">{{ msg.thinking.length }} 字</span>
              </button>
              <div v-if="isThinkingOpen(i)" class="brain-thinking-body">
                {{ msg.thinking }}
              </div>
            </div>

            <!-- 工具调用（仅 AI 消息，默认折叠成数字角标） -->
            <div
              v-if="msg.role !== 'user' && msg.toolCalls && msg.toolCalls.length"
              class="brain-msg-tools"
            >
              <button class="brain-tool-badge" @click="toggleTools(i)">
                <SfShellIcon name="doc" />
                <span>调用了 {{ msg.toolCalls.length }} 个工具</span>
                <SfShellIcon name="chev" class="brain-chevron" :class="{ 'is-up': isToolsOpen(i) }" />
              </button>
              <div v-if="isToolsOpen(i)" class="brain-tool-list">
                <div
                  v-for="tool in msg.toolCalls"
                  :key="tool.id"
                  class="brain-tool-line"
                >
                  <span class="brain-tool-name">{{ tool.name }}</span>
                  <span v-if="tool.input" class="brain-tool-input">{{ toolInputPreview(tool.input) }}</span>
                  <span v-if="tool.result" class="brain-tool-result">↳ {{ truncate(tool.result, 120) }}</span>
                </div>
              </div>
            </div>

            <!-- 用户消息附件预览 -->
            <div
              v-if="msg.role === 'user' && msg.attachments && msg.attachments.length"
              class="brain-msg-attachments"
            >
              <div
                v-for="(att, ai) in msg.attachments"
                :key="`${i}-att-${ai}`"
                class="brain-msg-attachment"
              >
                <SfShellIcon :name="attachmentIconName(att)" />
                <span>{{ att.name }}</span>
              </div>
            </div>

            <!-- 文本气泡 -->
            <div v-if="msg.content || msg.streaming" class="brain-msg-bubble">
              <span v-html="renderMessageContent(msg.content)" />
              <span v-if="msg.streaming" class="brain-msg-cursor" />
            </div>
            <div v-if="chatMessageMeta(msg)" class="brain-msg-meta">
              {{ chatMessageMeta(msg) }}
            </div>
          </div>
        </div>

        <!-- 空状态：3 段 grid，让输入框落在垂直正中 -->
        <template v-if="!messages.length">
          <div class="brain-empty-top">
            <div class="brain-hero-icon-wrap" aria-hidden="true">
              <div class="brain-hero-icon">
                <SfShellIcon name="bolt" />
              </div>
            </div>
            <div class="brain-hero-tags">
              <span class="brain-purpose-chip" :class="agentPurposeClass(selectedInstance)">
                {{ agentPurposeLabel(agentPurposeValue(selectedInstance)) }}
              </span>
              <span class="brain-hero-state" :class="statusClass">{{ statusLabel }}</span>
            </div>
            <h2 class="brain-hero-title">{{ selectedInstance?.name || '选择 Agent' }}</h2>
            <p class="brain-hero-desc">{{ selectedAgentDisplayName }} · {{ selectedInstanceSummaryLine }}</p>
            <div class="brain-hero-facts">
              <span>{{ departmentLabel(selectedInstance) }}</span>
              <span>{{ runtimeLabel(selectedInstance) }}</span>
              <span v-if="analysisEnabled(selectedInstance)">分析 Agent</span>
              <span v-if="selectedTrainingSummary?.gateway">训练网关</span>
              <span v-if="selectedActiveTrainingJobCount">{{ selectedActiveTrainingJobCount }} 个训练运行中</span>
            </div>
          </div>

          <div class="brain-input-wrap is-empty brain-empty-input">
            <!-- 附件 chip 列表 -->
            <div v-if="attachments.length" class="brain-attach-list">
              <div
                v-for="(att, ai) in attachments"
                :key="`empty-att-${ai}`"
                class="brain-attach-chip"
              >
                <SfShellIcon :name="attachmentIconName(att)" />
                <span>{{ att.name }}</span>
                <button class="brain-attach-remove" @click="removeAttachment(ai)" title="移除">
                  <SfShellIcon name="x" />
                </button>
              </div>
            </div>
            <a-textarea
              v-model="draft"
              :auto-size="{ minRows: 2, maxRows: 6 }"
              :placeholder="chatInputPlaceholder"
              :disabled="!canSubmit"
              @keydown.enter.exact.prevent="submit"
              class="brain-textarea"
            />
            <div class="brain-input-actions">
              <input
                ref="fileInputRef"
                type="file"
                multiple
                accept="image/*,.pdf,.txt,.md,.json,.yaml,.yml,.csv,.log,.py,.js,.ts,.jsx,.tsx,.vue,.html,.css,.sh,.sql,.toml,.ini,.env,.xml"
                class="brain-file-input"
                @change="handleFileChange"
              />
              <button
                class="brain-icon-btn"
                @click="pickFiles"
                :disabled="!canSubmit"
                title="附件（图片 / PDF / 文本，单个 ≤5MB）"
              >
                <SfShellIcon name="attach" />
              </button>
              <button
                v-if="activeRunId"
                class="brain-icon-btn brain-icon-btn-warn"
                @click="abortRun"
                title="中止"
              >
                <SfShellIcon name="x" />
              </button>
              <button
                class="brain-send-btn"
                :class="{ 'is-ready': canSubmit && (draft.trim() || attachments.length) && !sending }"
                :disabled="!canSubmit || (!draft.trim() && !attachments.length) || sending"
                @click="submit"
                title="发送"
              >
                <SfShellIcon v-if="sending" name="refresh" class="brain-icon-spin" />
                <SfShellIcon v-else name="send" />
              </button>
            </div>
          </div>

          <div class="brain-empty-bottom">
            <div v-if="suggestions.length" class="brain-hero-suggestions">
              <button
                v-for="(s, i) in suggestions"
                :key="i"
                class="brain-suggestion-chip"
                @click="askQuestion(s)"
                :disabled="!canSubmit"
              >
                {{ s }}
              </button>
            </div>
            <div v-if="!canSubmit && instances.length === 0" class="brain-hero-warning">
              <SfShellIcon name="warn" />
              <span>没有可用的代理设备。请先在
                <a v-if="userStore.isAdmin" class="brain-link" @click="$router.push('/admin/agent-devices')">设置 → Agent终端</a>
                创建实例。</span>
            </div>
            <div v-else-if="!canSubmit && !connected" class="brain-hero-warning">
              <SfShellIcon name="warn" />
              <span>实例离线或 agent 未就绪，请等待连接或切换实例。</span>
            </div>
            <div v-else-if="modelChatDisabledReason" class="brain-hero-warning">
              <SfShellIcon name="warn" />
              <span>{{ modelChatDisabledReason }}</span>
            </div>
            <div class="brain-input-hint">
              {{ canSubmit ? `当前调用：${selectedInstance?.name || '-'} / ${selectedAgent?.identity?.name || selectedAgent?.name || selectedAgentId || '-'}` : '请检查代理设备连接状态' }}
            </div>

            <div v-if="recentHistory.length" class="brain-recent-section">
              <div class="brain-section-title">最近问过</div>
              <div class="brain-recent-grid">
                <button
                  v-for="(item, i) in recentHistory"
                  :key="i"
                  class="brain-recent-card"
                  @click="loadHistoryItem(item)"
                >
                  <div class="brain-recent-q">{{ truncate(item.question, 60) }}</div>
                  <div class="brain-recent-meta">
                    <span>{{ formatHistoryTime(item.ts) }}</span>
                    <span v-if="item.instanceId" class="brain-recent-inst">· {{ item.instanceId }}</span>
                  </div>
                </button>
              </div>
            </div>
          </div>
        </template>

        <!-- 对话状态：底部输入框 -->
        <div v-if="messages.length" class="brain-chat-input">
          <div class="brain-input-wrap">
            <!-- 附件 chip 列表 -->
            <div v-if="attachments.length" class="brain-attach-list">
              <div
                v-for="(att, ai) in attachments"
                :key="`chat-att-${ai}`"
                class="brain-attach-chip"
              >
                <SfShellIcon :name="attachmentIconName(att)" />
                <span>{{ att.name }}</span>
                <button class="brain-attach-remove" @click="removeAttachment(ai)" title="移除">
                  <SfShellIcon name="x" />
                </button>
              </div>
            </div>
            <a-textarea
              v-model="draft"
              :auto-size="{ minRows: 1, maxRows: 6 }"
              :placeholder="chatInputPlaceholder"
              :disabled="!canSubmit"
              @keydown.enter.exact.prevent="submit"
              class="brain-textarea"
            />
            <div class="brain-input-actions">
              <input
                ref="fileInputRef"
                type="file"
                multiple
                accept="image/*,.pdf,.txt,.md,.json,.yaml,.yml,.csv,.log,.py,.js,.ts,.jsx,.tsx,.vue,.html,.css,.sh,.sql,.toml,.ini,.env,.xml"
                class="brain-file-input"
                @change="handleFileChange"
              />
              <button
                class="brain-icon-btn"
                @click="pickFiles"
                :disabled="!canSubmit"
                title="附件"
              >
                <SfShellIcon name="attach" />
              </button>
              <button
                class="brain-icon-btn"
                @click="resetConversation"
                title="开始新会话"
              >
                <SfShellIcon name="plus" />
              </button>
              <button
                v-if="activeRunId"
                class="brain-icon-btn brain-icon-btn-warn"
                @click="abortRun"
                title="中止"
              >
                <SfShellIcon name="x" />
              </button>
              <button
                class="brain-send-btn"
                :class="{ 'is-ready': canSubmit && (draft.trim() || attachments.length) && !sending }"
                :disabled="!canSubmit || (!draft.trim() && !attachments.length) || sending"
                @click="submit"
                title="发送"
              >
                <SfShellIcon v-if="sending" name="refresh" class="brain-icon-spin" />
                <SfShellIcon v-else name="send" />
              </button>
            </div>
          </div>
        </div>
        </section>
      </main>

      <!-- 历史抽屉（可由顶部按钮唤出，不再常驻第四列） -->
      <aside v-if="historyOpen" class="brain-history-drawer">
        <div class="brain-history-panel">
          <div class="brain-history-head">
            <span class="brain-history-title">查询历史</span>
            <a-space :size="4">
              <a-button v-if="history.length" size="mini" @click="clearHistory">清空</a-button>
              <a-button size="mini" @click="historyOpen = false">
                <template #icon><SfShellIcon name="x" class="brain-button-icon" /></template>
              </a-button>
            </a-space>
          </div>
          <div v-if="!history.length" class="brain-history-empty">
            <SfShellIcon name="hist" />
            <span>暂无查询记录</span>
          </div>
          <div v-else class="brain-history-list">
            <div
              v-for="(item, i) in history"
              :key="item.ts"
              class="brain-history-item"
              role="button"
              tabindex="0"
              @click="loadHistoryItemAndSwitch(item)"
              @keydown.enter.prevent="loadHistoryItemAndSwitch(item)"
              @keydown.space.prevent="loadHistoryItemAndSwitch(item)"
            >
              <div class="brain-history-q">{{ item.question }}</div>
              <div class="brain-history-meta">
                <span>{{ formatHistoryTime(item.ts) }}</span>
                <span v-if="item.instanceId" class="brain-history-instance">{{ item.instanceId }}</span>
              </div>
              <div class="brain-history-a">{{ truncate(item.answer, 80) }}</div>
              <button class="brain-history-delete" @click.stop="deleteHistoryItem(i)" title="删除">
                <SfShellIcon name="x" />
              </button>
            </div>
          </div>
        </div>
      </aside>
    </div>

    <a-modal
      v-model:visible="analysisAgentEditOpen"
      title="业务分析 Agent"
      :width="860"
      :ok-loading="savingAnalysisAgent"
      @ok="saveAnalysisAgent"
      @cancel="analysisAgentEditOpen = false"
    >
      <a-form :model="analysisAgentForm" layout="vertical" class="brain-agent-edit-form">
        <div class="brain-agent-edit-grid">
          <a-form-item label="名称" required>
            <a-input v-model="analysisAgentForm.name" />
          </a-form-item>
          <a-form-item label="Prompt 版本" required>
            <a-input v-model="analysisAgentForm.prompt_version" />
          </a-form-item>
          <a-form-item label="模型档位">
            <a-select v-model="analysisAgentForm.model_profile">
              <a-option
                v-for="item in analysisAgentModelProfileOptions"
                :key="item.value"
                :value="item.value"
              >{{ item.label }}</a-option>
            </a-select>
          </a-form-item>
        </div>
        <a-form-item label="说明">
          <a-textarea v-model="analysisAgentForm.description" :auto-size="{ minRows: 2, maxRows: 4 }" />
        </a-form-item>
        <a-form-item label="Prompt 目标">
          <a-textarea v-model="analysisAgentForm.prompt_goal" :auto-size="{ minRows: 2, maxRows: 4 }" />
        </a-form-item>
        <div class="brain-agent-edit-grid">
          <a-form-item label="Prompt 关注点">
            <a-textarea v-model="analysisAgentForm.promptFocusText" :auto-size="{ minRows: 4, maxRows: 8 }" />
          </a-form-item>
          <a-form-item label="Prompt 约束">
            <a-textarea v-model="analysisAgentForm.promptGuardrailsText" :auto-size="{ minRows: 4, maxRows: 8 }" />
          </a-form-item>
        </div>
        <a-form-item label="验证问题">
          <a-textarea v-model="analysisAgentForm.verificationQuestionsText" :auto-size="{ minRows: 2, maxRows: 5 }" />
        </a-form-item>
        <a-form-item label="分析维度">
          <a-textarea v-model="analysisAgentForm.dimensionsText" :auto-size="{ minRows: 2, maxRows: 4 }" />
        </a-form-item>
        <a-form-item label="能力">
          <div class="brain-business-capabilities brain-business-capabilities--edit">
            <span
              v-for="cap in analysisAgentCapabilities(analysisAgentEditing)"
              :key="cap.key || cap.label"
              class="brain-capability"
              :class="{ on: cap.enabled !== false }"
            >{{ cap.label }}</span>
          </div>
        </a-form-item>
        <a-form-item label="业务预设">
          <div class="brain-agent-presets">
            <button
              v-for="preset in analysisAgentPresets"
              :key="preset.value"
              type="button"
              class="brain-purpose-pill"
              :class="{ active: analysisAgentForm.preset === preset.value }"
              @click="applyAnalysisAgentPreset(preset)"
            >{{ preset.label }}</button>
          </div>
        </a-form-item>
        <div class="brain-agent-edit-grid brain-agent-edit-grid--five">
          <a-form-item label="统计天数">
            <a-input-number v-model="analysisAgentForm.window_days" :min="1" :max="30" />
          </a-form-item>
          <a-form-item label="TopN">
            <a-input-number v-model="analysisAgentForm.top_n" :min="1" :max="20" />
          </a-form-item>
          <a-form-item label="素材页数">
            <a-input-number v-model="analysisAgentForm.video_max_pages" :min="1" :max="50" />
          </a-form-item>
          <a-form-item label="卡审页数">
            <a-input-number v-model="analysisAgentForm.audit_max_pages" :min="1" :max="30" />
          </a-form-item>
          <a-form-item label="拒因样本">
            <a-input-number v-model="analysisAgentForm.audit_reject_detail_limit" :min="0" :max="100" />
          </a-form-item>
        </div>
        <a-form-item label="输出章节">
          <a-checkbox-group v-model="analysisAgentForm.output_sections" class="brain-output-section-options">
            <a-checkbox
              v-for="item in ANALYSIS_OUTPUT_SECTION_OPTIONS"
              :key="item.value"
              :value="item.value"
            >{{ item.label }}</a-checkbox>
          </a-checkbox-group>
        </a-form-item>
        <div class="brain-agent-edit-grid">
          <a-form-item label="生成待办">
            <a-switch v-model="analysisAgentForm.todo_enabled" />
          </a-form-item>
          <a-form-item label="报告渠道">
            <a-input v-model="analysisAgentForm.report_channel" />
          </a-form-item>
        </div>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { aiclawApi as rawAiclawApi, learningApi as rawLearningApi } from '@/api'
import { createAiclawChatSocket } from '@/api/aiclawWs'
import { useUserStore } from '@/stores/user'
import { renderMd } from '@/utils/renderMd'
import { confirmDelete } from '@/utils/confirmDelete'
import { bjtDateString, formatTimeOnly, formatTimeShort } from '@/utils/format'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

const aiclawApi: any = rawAiclawApi
const learningApi: any = rawLearningApi
const userStore = useUserStore()
const route = useRoute()

// ── 状态 ──
const instances = ref<any[]>([])
const coverageRows = ref<any[]>([])
const businessAnalysisAgents = ref<any[]>([])
const loadingInstances = ref(false)
const selectedInstanceId = ref('')
const agents = ref<any[]>([])
const selectedAgentId = ref('')
const agentSkills = ref<any[]>([])
const agentizationCandidates = ref<any[]>([])
const loadingAgentSkills = ref(false)
const validatingModelChatContext = ref(false)
const modelChatValidation = ref<{ ready: boolean; disabled_reason: string; context?: Record<string, any> } | null>(null)
const messages = ref<any[]>([])
const draft = ref('')
const sending = ref(false)
const activeRunId = ref('')
const connected = ref(false)
const chatLogRef = ref<HTMLElement | null>(null)
// 附件 + 文件输入
const attachments = ref<any[]>([])
const fileInputRef = ref<HTMLInputElement | null>(null)
const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
const MAX_ATTACHMENTS_TOTAL_BYTES = 10 * 1024 * 1024
// 思考过程展开/折叠状态（按消息 index，默认展开）
const thinkingExpanded = ref<Record<number, boolean>>({})
// 工具调用展开/折叠状态（按消息 index，默认折叠）
const toolsExpanded = ref<Record<number, boolean>>({})
// 拖拽上传 active 状态
const isDragging = ref(false)
const agentPurposeFilter = ref('all')
const canManageAgents = computed(() => userStore.isAdmin)
const analysisAgentEditOpen = ref(false)
const savingAnalysisAgent = ref(false)
const analysisAgentEditing = ref<any | null>(null)
const validatingAnalysisAgentId = ref('')
const analysisAgentValidation = ref<Record<string, any>>({})
const analysisAgentForm = ref<any>({
  id: '',
  name: '',
  department: '',
  department_id: '',
  owner_user_id: '',
  skill_id: '',
  prompt_version: 'analysis_v1',
  model_profile: 'default',
  description: '',
  prompt_goal: '',
  promptFocusText: '',
  promptGuardrailsText: '',
  verificationQuestionsText: '',
  dimensionsText: '',
  window_days: 7,
  top_n: 5,
  video_max_pages: 10,
  audit_max_pages: 6,
  audit_reject_detail_limit: 8,
  output_sections: [],
  todo_enabled: true,
  report_channel: 'dingtalk_markdown',
  preset: 'weekly_standard',
})
const ANALYSIS_OUTPUT_SECTION_OPTIONS = [
  { value: 'executive_summary', label: '核心结论' },
  { value: 'audit_review', label: '团队/个人卡审' },
  { value: 'topic_comparisons', label: '同主题对比' },
  { value: 'personal_improvements', label: '个人改进' },
  { value: 'editing_mix', label: '剪辑分类' },
  { value: 'next_actions', label: '下一步动作' },
]
const DEFAULT_ANALYSIS_MODEL_PROFILE_OPTIONS = [
  { value: 'default', label: '平台默认' },
  { value: 'deepseek-v4-flash', label: 'DeepSeek V4 Flash' },
  { value: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro' },
  { value: 'cheap', label: '低成本档' },
]
const DEFAULT_ANALYSIS_AGENT_PRESETS = [
  {
    value: 'weekly_standard',
    label: '标准周报',
    params: {
      window_days: 7,
      top_n: 5,
      video_max_pages: 10,
      audit_max_pages: 6,
      audit_reject_detail_limit: 8,
      output_sections: ANALYSIS_OUTPUT_SECTION_OPTIONS.map(item => item.value),
      todo_enabled: true,
    },
  },
  {
    value: 'audit_review',
    label: '卡审复盘',
    params: {
      window_days: 7,
      top_n: 3,
      video_max_pages: 8,
      audit_max_pages: 12,
      audit_reject_detail_limit: 30,
      output_sections: ['executive_summary', 'audit_review', 'next_actions'],
      todo_enabled: true,
    },
  },
  {
    value: 'personal_improvement',
    label: '个人改进',
    params: {
      window_days: 7,
      top_n: 5,
      video_max_pages: 12,
      audit_max_pages: 6,
      audit_reject_detail_limit: 12,
      output_sections: ['executive_summary', 'topic_comparisons', 'personal_improvements', 'next_actions'],
      todo_enabled: true,
    },
  },
  {
    value: 'brief_summary',
    label: '轻量摘要',
    params: {
      window_days: 7,
      top_n: 3,
      video_max_pages: 5,
      audit_max_pages: 3,
      audit_reject_detail_limit: 5,
      output_sections: ['executive_summary', 'topic_comparisons'],
      todo_enabled: false,
    },
  },
]

// 3-列 IA：左侧 dept 选中、右侧详情 / 对话切换、离线 only 切换
const selectedDepartmentKey = ref('')
// 默认显示 detail；如果 URL 带 model_deployment_id 等模型对话上下文，初始化为 chat
const rightMode = ref<'detail' | 'chat'>(
  Object.values({
    model_deployment_id: String(route.query?.model_deployment_id ?? ''),
    model_family: String(route.query?.model_family ?? ''),
    training_job_id: String(route.query?.training_job_id ?? ''),
    artifact_id: String(route.query?.artifact_id ?? ''),
    artifact_sha256: String(route.query?.artifact_sha256 ?? ''),
    target_gateway_id: String(route.query?.target_gateway_id ?? ''),
  }).some(v => v.trim()) ? 'chat' : 'detail'
)
const showOnlyOffline = ref(false)
// 移动端 dept-rail 抽屉开关
const sidebarOpen = ref(false)

const purposeTabsAll = [
  { label: '全部', value: 'all' },
  { label: '执行', value: 'skill_runtime' },
  { label: '分析', value: 'analysis' },
  { label: '训练', value: 'training' },
  { label: '媒体', value: 'media' },
  { label: '混合', value: 'mixed' },
]
// 默认展示控制面的主要节点用途；
// 「混合」只有在当前部门确有 mixed Agent 时才出现，避免没意义的空 tab。
const purposeTabs = computed(() => purposeTabsAll.filter((tab) => {
  if (tab.value !== 'mixed') return true
  return purposeCount('mixed') > 0
}))
const AGENT_PURPOSE_LABELS: Record<string, string> = {
  skill_runtime: '部门执行',
  analysis: '分析',
  training: '训练',
  media: '媒体生成',
  mixed: '混合',
}
const AGENT_PURPOSE_VALUES = new Set(Object.keys(AGENT_PURPOSE_LABELS))

// 部门→在中间列分组用的颜色（沿用 design 的 type meta 调性）
const PURPOSE_GROUP_META: Record<string, { label: string; desc: string; color: string; order: number }> = {
  skill_runtime: { label: '执行', desc: '响应部门 Skill 调用', color: 'var(--ai-accent, #5b8def)', order: 1 },
  analysis: { label: '分析', desc: '产出报告 / 诊断 / 洞察', color: 'var(--ai-info, #4f8edb)', order: 2 },
  training: { label: '训练', desc: '消耗 GPU 训练部门模型', color: 'var(--ai-warn, #d68a3c)', order: 3 },
  media: { label: '媒体', desc: '受控执行视频生成任务', color: 'var(--ai-purple, #8b5cf6)', order: 4 },
  mixed: { label: '混合', desc: '同时承担多个受控工作负载', color: 'var(--ai-ink-3, #6a6a6a)', order: 5 },
}

// 部门颜色：用部门名做稳定哈希，配 8 色调色板
const DEPT_PALETTE = [
  '#5b8def', '#4cb392', '#d68a3c', '#a86fd6',
  '#c64a4a', '#3aa8a8', '#7a8aa3', '#c4a14f',
]

function deptColorForKey(key: string) {
  if (!key || key === '__all__') return 'var(--ai-ink-4)'
  let hash = 0
  for (let i = 0; i < key.length; i += 1) {
    hash = ((hash << 5) - hash + key.charCodeAt(i)) | 0
  }
  return DEPT_PALETTE[Math.abs(hash) % DEPT_PALETTE.length]
}

// 历史记录（localStorage 持久化）
const history = ref<any[]>(loadHistory())
const historyOpen = ref(false)
// 首屏展示最近 4 条问过（去重后）
const recentHistory = computed(() => history.value.slice(0, 4))

const HISTORY_KEY = 'sf-brain-history'
const HISTORY_MAX = 100

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveHistory() {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.value.slice(0, HISTORY_MAX)))
  } catch {
    /* 忽略存储满 */
  }
}

function pushHistory(question: string, answer: string) {
  if (!question || !answer) return
  history.value.unshift({
    ts: Date.now(),
    question,
    answer,
    instanceId: selectedInstanceId.value,
    agentId: selectedAgentId.value,
  })
  history.value = history.value.slice(0, HISTORY_MAX)
  saveHistory()
}

function clearHistory() {
  history.value = []
  saveHistory()
}

function deleteHistoryItem(idx: number) {
  const item = history.value[idx]
  const preview = String(item?.question || '').slice(0, 40) || '该条记录'
  confirmDelete(preview, () => {
    history.value.splice(idx, 1)
    saveHistory()
  })
}

function loadHistoryItem(item: any) {
  // 在对话流里加载历史 Q&A（只读 view，不重新发送）
  messages.value = [
    { role: 'user', content: item.question, streaming: false },
    { role: 'assistant', content: item.answer, streaming: false },
  ]
  historyOpen.value = false
}

function formatHistoryTime(ts: number) {
  if (bjtDateString(ts) === bjtDateString(Date.now())) return formatTimeOnly(ts)
  return formatTimeShort(ts)
}

function truncate(s: string, n: number) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '…' : s
}

function routeQueryString(value: unknown): string {
  if (Array.isArray(value)) return String(value[0] || '')
  return String(value || '')
}

// 消息发送者名称：用户用昵称，AI 用「实例名 · agent」
function msgSenderName(msg: any) {
  if (msg.role === 'user') {
    return userStore.userInfo?.name || userStore.userInfo?.username || '我'
  }
  const inst = selectedInstance.value?.name || selectedInstanceId.value || '代理设备'
  const agent = selectedAgent.value?.identity?.name
    || selectedAgent.value?.displayName
    || selectedAgent.value?.name
    || selectedAgentId.value
    || 'Agent'
  return `${inst} · ${agent}`
}

// 渲染消息内容（Markdown，空内容返回空字符串避免显示 undefined）
function renderMessageContent(content: string | undefined) {
  if (!content) return ''
  return renderMd(content)
}

// ── 计算属性 ──
const selectedInstance = computed(() => instances.value.find(i => i.id === selectedInstanceId.value))
const selectedAgent = computed(() => agents.value.find(a => (a.id || a.name) === selectedAgentId.value))
const filteredInstances = computed(() => {
  const filter = agentPurposeFilter.value
  if (filter === 'all') return instances.value
  return instances.value.filter((item) => {
    const purpose = agentPurposeValue(item)
    if (purpose === filter) return true
    if (filter === 'analysis') return analysisEnabled(item)
    if (filter === 'training') return Boolean(item?.training?.gateway || trainingActiveJobCount(item))
    return false
  })
})

// ── 部门聚合（左侧 dept 列） ──
const departmentGroups = computed(() => {
  const map = new Map<string, { key: string; label: string; total: number; online: number; color: string }>()
  for (const item of instances.value) {
    const label = departmentLabel(item) || '未指定部门'
    const key = label
    if (!map.has(key)) {
      map.set(key, { key, label, total: 0, online: 0, color: deptColorForKey(key) })
    }
    const bucket = map.get(key)!
    bucket.total += 1
    if (item.bridge_online) bucket.online += 1
  }
  for (const agent of businessAnalysisAgents.value) {
    const label = String(agent?.department || agent?.department_name || '').trim() || '未指定部门'
    if (!map.has(label)) {
      map.set(label, { key: label, label, total: 0, online: 0, color: deptColorForKey(label) })
    }
    const bucket = map.get(label)!
    bucket.total += 1
  }
  return Array.from(map.values()).sort((a, b) => {
    if (b.online !== a.online) return b.online - a.online
    return a.label.localeCompare(b.label, 'zh-Hans-CN')
  })
})

const selectedDepartmentLabel = computed(() => {
  if (showOnlyOffline.value) return '离线 / 异常 Agent'
  if (!selectedDepartmentKey.value) return '全部 Agent'
  return selectedDepartmentKey.value
})

// 在 dept / offline 过滤后的实例集合
const instancesInDepartment = computed(() => {
  let rows = instances.value
  if (showOnlyOffline.value) {
    rows = rows.filter(item => !item.bridge_online)
  } else if (selectedDepartmentKey.value) {
    rows = rows.filter(item => departmentLabel(item) === selectedDepartmentKey.value)
  }
  return rows
})

const deptOnlineCount = computed(() => instancesInDepartment.value.filter(item => item.bridge_online).length)

// 应用 purpose 过滤后的集合（中间列实际显示）
const dispatchableInstances = computed(() => {
  const filter = agentPurposeFilter.value
  const rows = instancesInDepartment.value
  if (filter === 'all') return rows
  return rows.filter((item) => {
    const purpose = agentPurposeValue(item)
    if (purpose === filter) return true
    if (filter === 'analysis') return analysisEnabled(item)
    if (filter === 'training') return Boolean(item?.training?.gateway || trainingActiveJobCount(item))
    return false
  })
})

// 按 purpose 分组（中间列）
const groupedAgentsInDept = computed(() => {
  const groups: Record<string, any[]> = {
    skill_runtime: [],
    analysis: [],
    training: [],
    mixed: [],
  }
  for (const item of dispatchableInstances.value) {
    const purpose = agentPurposeValue(item)
    if (groups[purpose]) {
      groups[purpose].push(item)
    } else {
      groups.skill_runtime.push(item)
    }
  }
  return Object.entries(groups)
    .map(([key, items]) => {
      const meta = PURPOSE_GROUP_META[key] || PURPOSE_GROUP_META.skill_runtime
      return { key, items, ...meta }
    })
    .sort((a, b) => a.order - b.order)
})

function purposeCount(value: string) {
  const rows = instancesInDepartment.value
  if (value === 'all') return rows.length
  return rows.filter((item) => {
    const purpose = agentPurposeValue(item)
    if (purpose === value) return true
    if (value === 'analysis') return analysisEnabled(item)
    if (value === 'training') return Boolean(item?.training?.gateway || trainingActiveJobCount(item))
    return false
  }).length
}

function agentStatusTone(item: any) {
  if (!item) return 'idle'
  if (!item.bridge_online) return 'offline'
  if (item.last_sync_ok === false) return 'bad'
  return 'online'
}

function instanceSkillsLabel(item: any) {
  const count = Number(item?.bridge_skill_count ?? item?.skills_count ?? 0)
  if (count) return `${count} Skills`
  if (analysisEnabled(item)) return '分析就绪'
  if (item?.training?.gateway) return '训练就绪'
  return '部门执行'
}

function instanceHardwareLabel(item: any) {
  const gpu = Number(item?.training?.gpu_count || 0)
  if (gpu) return `GPU ${gpu}`
  const platform = item?.bridge_platform
  if (platform) return String(platform).slice(0, 18)
  return '—'
}

function instanceLastSeen(item: any) {
  const ts = item?.last_heartbeat || item?.bridge_connected_at
  return formatLastHeartbeat(ts)
}

function formatLastHeartbeat(ts: any) {
  if (!ts) return '—'
  const time = typeof ts === 'number' ? ts : Date.parse(String(ts))
  if (!Number.isFinite(time)) return '—'
  const diff = Date.now() - time
  if (diff < 0) return '刚刚'
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return `${sec}s 前`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} 分钟前`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour} 小时前`
  const day = Math.floor(hour / 24)
  if (day < 7) return `${day} 天前`
  return formatTimeShort(time)
}

function agentBranchTypeLabel(agent: any) {
  return agent?.identity?.type || agent?.type || agent?.kind || 'agent'
}

function selectDepartment(key: string) {
  selectedDepartmentKey.value = key
  showOnlyOffline.value = false
  agentPurposeFilter.value = 'all'
  sidebarOpen.value = false
}

function toggleOfflineFilter() {
  showOnlyOffline.value = !showOnlyOffline.value
  if (showOnlyOffline.value) {
    selectedDepartmentKey.value = ''
  }
  sidebarOpen.value = false
}

function toggleRightMode() {
  if (!canSubmit.value && rightMode.value === 'detail' && !hasModelChatContext.value) return
  rightMode.value = rightMode.value === 'chat' ? 'detail' : 'chat'
}

function loadHistoryItemAndSwitch(item: any) {
  loadHistoryItem(item)
  rightMode.value = 'chat'
}

function onPickAgent(agent: any) {
  const next = agent?.id || agent?.name
  if (!next || next === selectedAgentId.value) return
  selectedAgentId.value = next
  void onAgentChange()
}
const analysisAgentPresets = computed(() => {
  const schemaPresets = analysisAgentEditing.value?.control?.schema?.presets
  return Array.isArray(schemaPresets) && schemaPresets.length ? schemaPresets : DEFAULT_ANALYSIS_AGENT_PRESETS
})

function analysisAgentDefaultParams(agent: any) {
  const controlParams = agent?.control?.effective?.default_params
  if (controlParams && typeof controlParams === 'object') return controlParams
  const params = agent?.default_params
  return params && typeof params === 'object' ? params : {}
}

function analysisAgentCanEdit(agent: any) {
  return Boolean(agent?.permissions?.edit)
}

function parseAgentListText(value: string) {
  return String(value || '')
    .split(/[\n,，]/)
    .map(item => item.trim())
    .filter(Boolean)
}

function parseAgentDimensionsText(value: string) {
  return parseAgentListText(value)
}

function agentPromptList(agent: any, key: string) {
  const prompt = agent?.prompt && typeof agent.prompt === 'object' ? agent.prompt : {}
  const params = analysisAgentDefaultParams(agent)
  const promptKeyMap: Record<string, string> = {
    prompt_focus: 'focus',
    prompt_guardrails: 'guardrails',
    verification_questions: 'verification_questions',
  }
  const promptKey = promptKeyMap[key] || key
  const value = params[key] ?? prompt[promptKey]
  return Array.isArray(value) ? value.map((item: unknown) => String(item || '').trim()).filter(Boolean) : []
}

function analysisAgentPromptGoal(agent: any) {
  const params = analysisAgentDefaultParams(agent)
  const promptGoal = agent?.prompt?.goal
  return String(params.prompt_goal || promptGoal || agent?.description || 'Prompt 未配置').trim()
}

function analysisAgentCapabilities(agent: any) {
  if (!agent) return []
  const caps = Array.isArray(agent?.capabilities) ? agent.capabilities : []
  if (caps.length) {
    return caps
      .map((item: any) => ({
        key: String(item?.key || item?.label || ''),
        label: String(item?.label || item?.key || '').trim(),
        enabled: item?.enabled !== false,
      }))
      .filter((item: any) => item.label)
  }
  return [
    { key: 'video_metrics', label: '云视频素材消耗', enabled: true },
    { key: 'same_topic_compare', label: '同主题对比', enabled: true },
    { key: 'audit_rejects', label: '卡审拒因复盘', enabled: true },
    { key: 'run_control', label: '运行参数控制', enabled: true },
  ]
}

function analysisAgentControlSummary(agent: any) {
  const params = analysisAgentDefaultParams(agent)
  const sections = Array.isArray(params.output_sections) ? params.output_sections.length : 0
  const parts = [
    analysisAgentDimensionLabel(agent),
    `${boundedNumber(params.window_days, 7, 1, 30)}天`,
    `Top${boundedNumber(params.top_n, 5, 1, 20)}`,
    analysisAgentModelProfileLabel(params.model_profile),
    `${sections || ANALYSIS_OUTPUT_SECTION_OPTIONS.length}章节`,
    params.todo_enabled === false ? '待办关' : '待办开',
  ]
  return parts.join(' · ')
}

function analysisAgentModelProfileLabel(value: unknown) {
  const normalized = String(value || 'default')
  const matched = analysisAgentModelProfileOptions.value.find(item => item.value === normalized)
  return matched?.label || '平台默认'
}

function analysisAgentValidationLabel(result: any) {
  const checks = Array.isArray(result?.checks) ? result.checks : []
  const failed = checks.filter((item: any) => item?.status === 'failed').length
  const warning = checks.filter((item: any) => item?.status === 'warning').length
  if (failed) return `验证失败 ${failed}`
  if (warning) return `验证提醒 ${warning}`
  return `验证通过 ${checks.length || ''}`.trim()
}

function boundedNumber(value: unknown, fallback: number, min: number, max: number) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return fallback
  return Math.max(min, Math.min(max, Math.round(parsed)))
}

function applyAnalysisAgentPreset(preset: any) {
  if (!preset || typeof preset !== 'object') return
  const params = preset.params && typeof preset.params === 'object' ? preset.params : {}
  analysisAgentForm.value = {
    ...analysisAgentForm.value,
    preset: preset.value || '',
    window_days: boundedNumber(params.window_days, analysisAgentForm.value.window_days, 1, 30),
    top_n: boundedNumber(params.top_n, analysisAgentForm.value.top_n, 1, 20),
    video_max_pages: boundedNumber(params.video_max_pages, analysisAgentForm.value.video_max_pages, 1, 50),
    audit_max_pages: boundedNumber(params.audit_max_pages, analysisAgentForm.value.audit_max_pages, 1, 30),
    audit_reject_detail_limit: boundedNumber(params.audit_reject_detail_limit, analysisAgentForm.value.audit_reject_detail_limit, 0, 100),
    output_sections: Array.isArray(params.output_sections)
      ? params.output_sections.map((item: unknown) => String(item)).filter(Boolean)
      : analysisAgentForm.value.output_sections,
    todo_enabled: typeof params.todo_enabled === 'boolean' ? params.todo_enabled : analysisAgentForm.value.todo_enabled,
    model_profile: params.model_profile ? String(params.model_profile) : analysisAgentForm.value.model_profile,
  }
}

const analysisAgentModelProfileOptions = computed(() => {
  const options = analysisAgentEditing.value?.control?.schema?.model_profiles
  return Array.isArray(options) && options.length
    ? options.map((item: any) => ({
        value: String(item?.value || '').trim(),
        label: String(item?.label || item?.value || '').trim(),
      })).filter((item: any) => item.value && item.label)
    : DEFAULT_ANALYSIS_MODEL_PROFILE_OPTIONS
})

function openAnalysisAgentEdit(agent: any) {
  if (!analysisAgentCanEdit(agent)) return
  analysisAgentEditing.value = agent
  const params = analysisAgentDefaultParams(agent)
  const prompt = agent?.prompt && typeof agent.prompt === 'object' ? agent.prompt : {}
  analysisAgentForm.value = {
    id: agent.id || '',
    name: agent.name || '',
    department: agent.department || currentCoverageDepartment.value || '',
    department_id: agent.department_id || '',
    owner_user_id: agent.owner_user_id || '',
    skill_id: agent.skill_id || '',
    prompt_version: agent.prompt_version || 'analysis_v1',
    model_profile: String(params.model_profile || 'default'),
    description: agent.description || '',
    prompt_goal: String(params.prompt_goal || prompt.goal || '').trim(),
    promptFocusText: agentPromptList(agent, 'prompt_focus').join('\n'),
    promptGuardrailsText: agentPromptList(agent, 'prompt_guardrails').join('\n'),
    verificationQuestionsText: agentPromptList(agent, 'verification_questions').join('\n'),
    dimensionsText: (Array.isArray(agent.dimensions) ? agent.dimensions : []).join('\n'),
    window_days: boundedNumber(params.window_days, 7, 1, 30),
    top_n: boundedNumber(params.top_n, 5, 1, 20),
    video_max_pages: boundedNumber(params.video_max_pages, 10, 1, 50),
    audit_max_pages: boundedNumber(params.audit_max_pages, 6, 1, 30),
    audit_reject_detail_limit: boundedNumber(params.audit_reject_detail_limit, 8, 0, 100),
    output_sections: Array.isArray(params.output_sections)
      ? params.output_sections.map((item: unknown) => String(item)).filter(Boolean)
      : ANALYSIS_OUTPUT_SECTION_OPTIONS.map(item => item.value),
    todo_enabled: params.todo_enabled !== false,
    report_channel: String(params.report_channel || 'dingtalk_markdown'),
    preset: String(params.preset || 'weekly_standard'),
  }
  analysisAgentEditOpen.value = true
}

async function saveAnalysisAgent() {
  const form = analysisAgentForm.value
  const dimensions = parseAgentDimensionsText(form.dimensionsText)
  if (!form.name || !form.department) {
    Message.warning('名称和部门不能为空')
    return false
  }
  if (!dimensions.length) {
    Message.warning('至少保留一个分析维度')
    return false
  }
  if (!Array.isArray(form.output_sections) || !form.output_sections.length) {
    Message.warning('至少选择一个输出章节')
    return false
  }
  savingAnalysisAgent.value = true
  try {
    const defaultParams = {
      window: 'rolling_7_complete_days',
      timezone: 'Asia/Shanghai',
      same_topic_rule: 'category_labels_title',
      low_spend_rule: 'zero_spend_first',
      window_days: boundedNumber(form.window_days, 7, 1, 30),
      top_n: boundedNumber(form.top_n, 5, 1, 20),
      video_max_pages: boundedNumber(form.video_max_pages, 10, 1, 50),
      audit_max_pages: boundedNumber(form.audit_max_pages, 6, 1, 30),
      audit_reject_detail_limit: boundedNumber(form.audit_reject_detail_limit, 8, 0, 100),
      output_sections: form.output_sections,
      todo_enabled: Boolean(form.todo_enabled),
      report_channel: form.report_channel || 'dingtalk_markdown',
      model_profile: form.model_profile || 'default',
      preset: form.preset || '',
      prompt_goal: String(form.prompt_goal || '').trim(),
      prompt_focus: parseAgentListText(form.promptFocusText),
      prompt_guardrails: parseAgentListText(form.promptGuardrailsText),
      verification_questions: parseAgentListText(form.verificationQuestionsText),
    }
    const resp = await aiclawApi.upsertAnalysisAgent({
      id: form.id,
      name: form.name,
      department: form.department,
      department_id: form.department_id || null,
      owner_user_id: form.owner_user_id || null,
      skill_id: form.skill_id || null,
      prompt_version: form.prompt_version || 'analysis_v1',
      description: form.description || null,
      dimensions,
      default_params: defaultParams,
      editor_user_ids: analysisAgentEditing.value?.editor_user_ids || [],
      status: analysisAgentEditing.value?.status || 'active',
    })
    const saved = resp?.agent
    if (saved) {
      const idx = businessAnalysisAgents.value.findIndex(item => item.id === saved.id)
      if (idx >= 0) businessAnalysisAgents.value.splice(idx, 1, saved)
      else businessAnalysisAgents.value.unshift(saved)
    } else {
      await reloadInstances()
    }
    analysisAgentEditOpen.value = false
    Message.success('业务分析 Agent 已更新')
    return true
  } catch (e: any) {
    Message.error(e?._message || '保存失败')
    return false
  } finally {
    savingAnalysisAgent.value = false
  }
}

async function validateAnalysisAgent(agent: any) {
  if (!agent?.id || validatingAnalysisAgentId.value) return
  validatingAnalysisAgentId.value = agent.id
  try {
    const resp = await aiclawApi.validateAnalysisAgent(agent.id, {
      params: analysisAgentDefaultParams(agent),
    })
    analysisAgentValidation.value = {
      ...analysisAgentValidation.value,
      [agent.id]: resp,
    }
    const saved = resp?.agent
    if (saved) {
      const idx = businessAnalysisAgents.value.findIndex(item => item.id === saved.id)
      if (idx >= 0) businessAnalysisAgents.value.splice(idx, 1, saved)
    }
    if (resp?.ok) Message.success(analysisAgentValidationLabel(resp))
    else Message.warning(analysisAgentValidationLabel(resp))
  } catch (e: any) {
    Message.error(e?._message || '验证失败')
  } finally {
    validatingAnalysisAgentId.value = ''
  }
}
const agentStats = computed(() => {
  const total = instances.value.length
  const online = instances.value.filter(item => item.bridge_online).length
  const analysis = instances.value.filter(item => analysisEnabled(item)).length
  const training = instances.value.filter(item => item?.training?.gateway || agentPurposeValue(item) === 'training').length
  const activeTrainingJobs = instances.value.reduce((sum, item) => sum + trainingActiveJobCount(item), 0)
  return { total, online, analysis, training, activeTrainingJobs }
})
const selectedAgentDisplayName = computed(() => selectedAgent.value?.identity?.name
  || selectedAgent.value?.displayName
  || selectedAgent.value?.name
  || selectedAgentId.value
  || '默认 Agent')
const modelChatContext = computed(() => {
  const query = route.query || {}
  return {
    model_deployment_id: routeQueryString(query.model_deployment_id).trim(),
    model_family: routeQueryString(query.model_family).trim(),
    training_job_id: routeQueryString(query.training_job_id).trim(),
    artifact_id: routeQueryString(query.artifact_id).trim(),
    artifact_sha256: routeQueryString(query.artifact_sha256).trim(),
    target_gateway_id: routeQueryString(query.target_gateway_id || query.gateway_id).trim(),
  }
})
const modelChatDepartment = computed(() => {
  const query = route.query || {}
  return routeQueryString(query.department || query.target_department).trim()
})
const hasModelChatContext = computed(() => Object.values(modelChatContext.value).some(Boolean))
const effectiveModelChatContext = computed(() => {
  const validated = modelChatValidation.value?.context
  if (hasModelChatContext.value && validated && typeof validated === 'object') {
    return {
      ...modelChatContext.value,
      ...validated,
    }
  }
  return modelChatContext.value
})
const modelChatDisabledReason = computed(() => {
  if (!hasModelChatContext.value) return ''
  if (validatingModelChatContext.value) return '正在校验训练后模型推理节点。'
  if (!modelChatValidation.value) return ''
  if (modelChatValidation.value.ready) return ''
  return modelChatValidation.value.disabled_reason || '当前训练后模型推理节点不可用。'
})
const modelChatPrompt = computed(() => {
  const context = effectiveModelChatContext.value
  const model = context.model_family || context.model_deployment_id || '当前模型'
  return `请用 ${model} 做一次测试对话，并说明它适合处理哪些业务问题。`
})
const selectedTrainingSummary = computed<Record<string, any> | null>(() => {
  const training = selectedInstance.value?.training
  return training && typeof training === 'object' ? training : null
})
const selectedActiveTrainingJobs = computed(() => activeTrainingJobs(selectedInstance.value))
const selectedActiveTrainingJobCount = computed(() => trainingActiveJobCount(selectedInstance.value))
const selectedTrainingTasks = computed<string[]>(() => {
  const tasks = selectedTrainingSummary.value?.supported_tasks
  return Array.isArray(tasks)
    ? tasks.map((item: unknown) => String(item || '').trim()).filter(Boolean)
    : []
})
const selectedTrainingGpuCount = computed(() => Number(selectedTrainingSummary.value?.gpu_count || 0))
const trainingConsolePath = computed(() => `/training?gateway=${encodeURIComponent(selectedInstanceId.value)}`)
const currentCoverageDepartment = computed(() => {
  if (selectedDepartmentKey.value) return selectedDepartmentKey.value
  const modelDepartment = modelChatDepartment.value
  if (modelDepartment) return modelDepartment
  const selectedDepartment = departmentLabel(selectedInstance.value)
  if (selectedDepartment && selectedDepartment !== '未指定部门') return selectedDepartment
  return userStore.userInfo?.department || ''
})
const currentCoverageRow = computed(() => {
  const department = currentCoverageDepartment.value
  if (!coverageRows.value.length) return null
  if (department) {
    return coverageRows.value.find((row) => String(row?.department || '') === department) || null
  }
  return coverageRows.value[0] || null
})
const businessAgentsForCurrentDepartment = computed(() => {
  if (selectedInstance.value?.is_platform_default) return businessAnalysisAgents.value
  const department = String(currentCoverageRow.value?.department || currentCoverageDepartment.value || selectedDepartmentKey.value || '').trim()
  const matched = businessAnalysisAgents.value.filter((agent) => {
    if (!department || department === '全部 Agent' || department === '离线 / 异常 Agent') return true
    return String(agent?.department || agent?.department_name || '').trim() === department
  })
  return matched.length ? matched : businessAnalysisAgents.value
})
const primaryEditableBusinessAgent = computed(() => (
  businessAgentsForCurrentDepartment.value.find(agent => analysisAgentCanEdit(agent)) || null
))
const coverageCapabilityRows = computed(() => {
  const row = currentCoverageRow.value
  return [
    coverageCapabilityRow(row, 'skill_runtime', '执行'),
    coverageCapabilityRow(row, 'analysis', '分析'),
    coverageCapabilityRow(row, 'training', '训练'),
  ]
})
const trainingCapabilityTip = computed(() => {
  const parts = []
  const workerCount = Number(selectedTrainingSummary.value?.worker_count || 0)
  if (selectedTrainingGpuCount.value) parts.push(`GPU ${selectedTrainingGpuCount.value}`)
  if (workerCount) parts.push(`Worker ${workerCount}`)
  if (selectedTrainingTasks.value.length) parts.push(selectedTrainingTasks.value.join(' / '))
  return parts.length ? parts.join(' · ') : 'Bridge 已上报训练能力'
})
const selectedInstanceSummaryLine = computed(() => {
  if (!selectedInstance.value) return '按功能选择部门 Agent、分析 Agent 或训练 Agent'
  const parts = []
  if (analysisEnabled(selectedInstance.value)) parts.push('承担分析')
  if (selectedTrainingSummary.value?.gateway) parts.push('训练网关')
  if (!parts.length) parts.push('部门执行')
  if (selectedActiveTrainingJobCount.value) parts.push(`${selectedActiveTrainingJobCount.value} 个训练运行中`)
  return parts.join(' · ')
})
const instanceOptions = computed(() => instances.value.map(i => ({
  label: i.bridge_online ? i.name : `${i.name}（离线）`,
  value: i.id,
})))
const agentOptions = computed(() => agents.value.map(a => ({
  label: a.identity?.name || a.displayName || a.name || a.id,
  value: a.id || a.name,
})))
const canSubmit = computed(() => Boolean(
  selectedInstanceId.value
    && selectedAgentId.value
    && connected.value
    && selectedInstance.value?.bridge_online
    && !modelChatDisabledReason.value,
))
const chatInputPlaceholder = computed(() => {
  if (modelChatDisabledReason.value) return modelChatDisabledReason.value
  return canSubmit.value ? '输入你的问题，回车发送（Shift+回车换行）' : '请先在右上角选择可用的实例和 agent'
})
const statusClass = computed(() => {
  if (!selectedInstance.value) return 'idle'
  if (!selectedInstance.value.bridge_online) return 'offline'
  if (connected.value) return 'online'
  return 'connecting'
})
const statusLabel = computed(() => ({
  idle: '未选择实例',
  offline: '实例离线',
  connecting: '连接中',
  online: '已就绪',
}[statusClass.value] || '未知'))
const connectionTip = computed(() => {
  if (statusClass.value === 'offline') return '当前实例未在线，请联系管理员检查 bridge 状态'
  if (statusClass.value === 'connecting') return '正在建立 WebSocket 连接'
  if (statusClass.value === 'online') return '已连接，可发送问题'
  return '请选择一个代理设备'
})

const suggestions = [
  '帮我列出当前所有 Skill 的状态',
  '今天有哪些待审核的变更？',
  '查询最近一次执行失败的原因',
]

// 可调用 Skills：优先读取当前 instance + agent 的实时 Skill 列表；
// 兜底兼容实例级 allowed_skills / skills，避免老 Bridge 没有 listSkills 时空白。
const allowedSkills = computed<Array<{
  id: string;
  name: string;
  risk: string;
  src: string;
  uses: number;
  shared: boolean;
}>>(() => {
  const inst: any = selectedInstance.value
  if (!inst) return []
  const raw: any[] = agentSkills.value.length
    ? agentSkills.value
    : Array.isArray(inst.allowed_skills)
      ? inst.allowed_skills
      : Array.isArray(inst.skills)
        ? inst.skills
        : []
  return raw.map((s: any) => ({
    id: String(s?.id || s?.skill_id || s?.name || '').trim() || 'unknown',
    name: String(s?.name || s?.display_name || s?.id || '未命名 Skill'),
    risk: String(s?.risk || s?.risk_level || 'R1').toUpperCase(),
    src: String(s?.src || s?.source || s?.department || departmentLabel(inst) || '部门'),
    uses: Number(s?.uses ?? s?.calls_today ?? 0) || 0,
    shared: Boolean(s?.shared || s?.source === '共享' || s?.is_shared),
  }))
})

const allowedSkillsCaption = computed(() => {
  const total = allowedSkills.value.length
  if (!total) return '部门授权 0'
  const shared = allowedSkills.value.filter(s => s.shared).length
  const owned = total - shared
  return `${total} 个 · 部门授权 ${owned}，共享 ${shared}`
})

// 运行指标 KPI（沿用设计稿的口径；当前后端暂无 24h 滚动统计，
// 优先使用 selectedInstance.metrics_24h.*，否则给出占位 — 后端 follow-up）
const metricsStats = computed(() => {
  const m = (selectedInstance.value as any)?.metrics_24h || {}
  const callsRaw = m.calls ?? m.invocations
  const successRaw = m.success_rate ?? m.successRate
  const latencyRaw = m.avg_latency_ms ?? m.avgLatency ?? m.p50_latency_ms
  const hasCalls = typeof callsRaw === 'number'
  const hasSuccess = typeof successRaw === 'number'
  const hasLatency = typeof latencyRaw === 'number'
  const calls = hasCalls
    ? callsRaw.toLocaleString('en-US')
    : (callsRaw ?? '—')
  const successRate = hasSuccess
    ? `${(successRaw <= 1 ? successRaw * 100 : successRaw).toFixed(1)}%`
    : (successRaw ?? '—')
  const latency = hasLatency
    ? `${latencyRaw}ms`
    : (latencyRaw ?? '—')
  // 无数据时不显示 mock delta 以免误导（之前的 ↑9% / ↑0.4 / ↓30 是 placeholder fake）
  return [
    { key: 'calls', label: '调用', value: String(calls), delta: hasCalls ? (m.calls_delta || '') : '', ok: false },
    { key: 'success', label: '成功率', value: String(successRate), delta: hasSuccess ? (m.success_delta || '') : '', ok: true },
    { key: 'latency', label: '平均耗时', value: String(latency), delta: hasLatency ? (m.latency_delta || '') : '', ok: false },
  ]
})


function agentPurposeValue(item: any) {
  const raw = String(item?.agent_purpose || item?.purpose || 'skill_runtime').trim()
  return AGENT_PURPOSE_VALUES.has(raw) ? raw : 'skill_runtime'
}

function agentPurposeLabel(value: string) {
  return AGENT_PURPOSE_LABELS[value] || AGENT_PURPOSE_LABELS.skill_runtime
}

function agentPurposeClass(item: any) {
  return `purpose-${agentPurposeValue(item)}`
}

function departmentLabel(item: any) {
  return item?.department_name
    || (typeof item?.department === 'string' ? item.department : '')
    || item?.department?.name
    || item?.dept_name
    || item?.dept_id
    || item?.department_id
    || '平台'
}

function departmentValues(item: any): string[] {
  return [
    item?.department,
    item?.department_name,
    item?.dept_name,
    item?.dept_id,
    item?.department_id,
  ].map(value => String(value || '').trim()).filter(Boolean)
}

function departmentMatches(item: any, targetDepartment: string): boolean {
  const target = targetDepartment.trim().toLowerCase()
  if (!target) return true
  return departmentValues(item).some(value => value.toLowerCase() === target)
}

function runtimeLabel(item: any) {
  return item?.runtime_type
    || item?.bridge_gateway_kind
    || item?.agent_type
    || 'Bridge'
}

function activeTrainingJobs(item: any) {
  const jobs = item?.active_training_jobs || item?.training?.active_jobs || []
  return Array.isArray(jobs) ? jobs : []
}

function trainingActiveJobCount(item: any) {
  const direct = item?.active_training_jobs_count ?? item?.training?.active_jobs_count
  const numeric = Number(direct)
  return Number.isFinite(numeric) && numeric > 0 ? numeric : activeTrainingJobs(item).length
}

function analysisEnabled(item: any) {
  if (!item) return false
  const purpose = agentPurposeValue(item)
  if (purpose === 'analysis' || purpose === 'mixed') return true
  const ops = item?.analysis?.ops || item?.capabilities?.ops || []
  return Boolean(item?.analysis?.agent || (Array.isArray(ops) && ops.includes('intelligence.analyze')))
}

function modelChatInstanceHasInferenceCapability(item: any) {
  const training = item?.training || {}
  const ops = [
    ...(Array.isArray(training?.ops) ? training.ops : []),
    ...(Array.isArray(item?.capabilities?.ops) ? item.capabilities.ops : []),
    ...(Array.isArray(item?.analysis?.ops) ? item.analysis.ops : []),
  ].map((value) => String(value || '').trim())
  return agentPurposeValue(item) === 'training' || agentPurposeValue(item) === 'mixed' || ops.includes('training.inference')
}

function preferredModelChatInstance(rows: any[]): any | null {
  const online = rows.filter(item => item?.bridge_online)
  if (!online.length) return null
  const gatewayId = String(effectiveModelChatContext.value.target_gateway_id || modelChatContext.value.target_gateway_id || '').trim()
  const directGateway = gatewayId ? online.find(item => String(item?.id || '') === gatewayId) : null
  if (directGateway) return directGateway
  const departmentScoped = online.filter(item => departmentMatches(item, modelChatDepartment.value))
  const candidates = departmentScoped.length ? departmentScoped : online
  if (hasModelChatContext.value) {
    return candidates.find(modelChatInstanceHasInferenceCapability) || null
  }
  return candidates.find(modelChatInstanceHasInferenceCapability)
    || candidates.find(item => analysisEnabled(item))
    || candidates.find(item => agentPurposeValue(item) === 'mixed')
    || candidates.find(item => agentPurposeValue(item) === 'skill_runtime')
    || candidates[0]
    || null
}

function coverageCapabilityRow(row: any, key: string, label: string) {
  const capability = row?.capabilities?.[key] || {}
  const ready = Boolean(capability.ready)
  const fallbackReady = Boolean(capability.fallback_ready)
  const online = Number(capability.online || 0)
  const count = Number(capability.count || 0)
  const fallbackCount = Number(capability.fallback_count || capability.fallback_online || 0)
  return {
    key,
    label,
    tone: ready ? 'ready' : (fallbackReady ? 'fallback' : 'missing'),
    text: ready
      ? `${online}/${count || online || 1}`
      : (fallbackReady ? `兜底 ${fallbackCount || 1}` : '缺失'),
  }
}

function agentCandidateImpact(item: any) {
  const impact = item?.expected_impact || {}
  const modules = Array.isArray(impact.affected_modules) ? impact.affected_modules.filter(Boolean).slice(0, 4) : []
  const confidence = Number(impact.confidence || item?.priority_score || 0)
  const confidenceText = confidence ? ` · 置信 ${Math.round(confidence * 100)}%` : ''
  return `${modules.length ? modules.join(' / ') : '学习流'}${confidenceText}`
}

function agentCandidateDraftLabel(item: any) {
  const ref = item?.external_ref || {}
  const draftId = String(ref.agent_draft_id || '').trim()
  return draftId ? `已生成治理草稿 · ${draftId}` : '等待生成治理草稿'
}

function analysisAgentDimensionLabel(agent: any) {
  const dims = Array.isArray(agent?.dimensions) ? agent.dimensions.filter(Boolean) : []
  if (!dims.length) return '默认维度'
  return `${dims.slice(0, 3).join(' / ')}${dims.length > 3 ? ` +${dims.length - 3}` : ''}`
}

function agentCoveragePath(purpose?: string) {
  const query = new URLSearchParams()
  const department = String(currentCoverageRow.value?.department || currentCoverageDepartment.value || '').trim()
  if (department) query.set('department', department)
  if (purpose) {
    query.set('purpose', purpose)
    query.set('create', '1')
  }
  const suffix = query.toString()
  return suffix ? `/admin/agent-devices?${suffix}` : '/admin/agent-devices'
}

// ── WebSocket ──
let socket: WebSocket | null = null
let socketSeq = 0
let reconnectAttempts = 0
let reconnectTimer: number | null = null
let statusPollTimer: number | null = null
let unmounted = false

function clearReconnectTimer() {
  if (reconnectTimer) {
    window.clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
}

function scheduleReconnect() {
  if (unmounted || reconnectTimer || !selectedInstanceId.value || !selectedAgentId.value) return
  const delay = Math.min(1000 * Math.max(1, 2 ** reconnectAttempts), 10000)
  reconnectTimer = window.setTimeout(async () => {
    reconnectTimer = null
    reconnectAttempts += 1
    await refreshConnectionState()
  }, delay)
}

async function refreshConnectionState() {
  if (unmounted) return
  const previousInstanceId = selectedInstanceId.value
  await reloadInstances()
  if (!selectedInstanceId.value) return
  if (selectedInstanceId.value !== previousInstanceId || !agents.value.length) {
    await loadAgents(selectedInstanceId.value)
    await loadAgentSkills(selectedInstanceId.value, selectedAgentId.value)
  }
  if (selectedInstance.value?.bridge_online && selectedAgentId.value && (!socket || socket.readyState >= WebSocket.CLOSING)) {
    connectSocket()
  } else if (!selectedInstance.value?.bridge_online) {
    connected.value = false
    scheduleReconnect()
  }
}

function connectSocket() {
  clearReconnectTimer()
  const seq = ++socketSeq
  socket?.close()
  socket = null
  connected.value = false
  if (!selectedInstanceId.value || !selectedAgentId.value) return

  socket = createAiclawChatSocket(selectedInstanceId.value, selectedAgentId.value, {
    onOpen: () => {
      if (seq !== socketSeq || unmounted) return
      reconnectAttempts = 0
      connected.value = true
    },
    onClose: () => {
      if (seq !== socketSeq || unmounted) return
      connected.value = false
      activeRunId.value = ''
      scheduleReconnect()
    },
    onError: () => {
      if (seq !== socketSeq || unmounted) return
      connected.value = false
    },
    onMessage: (message) => {
      if (seq !== socketSeq || unmounted) return
      handleSocketMessage(message)
    },
  })
}

function handleSocketMessage(msg: any) {
  if (msg.type === 'error') {
    Message.error(msg.message || '对话失败')
    finalizeAssistant(msg.message || '对话失败')
    return
  }
  if (msg.type !== 'chat_event') return
  const payload = msg.payload || {}
  if (payload.state === 'started') {
    activeRunId.value = payload.runId || ''
    return
  }

  const assistant = ensureAssistantMessage()

  // chat 事件携带完整的 message.content 数组（Anthropic 风格 blocks），
  // 包含 text / thinking / tool_use / tool_result 等子类型 — 优先解析它
  if (payload.event === 'chat' && payload.message?.content) {
    parseContentBlocks(assistant, payload.message.content)
  }

  // agent 事件 / chat 事件的纯文本快照都用 delta 或 text 字段（替换式更新）
  if (payload.delta != null && payload.delta !== '') {
    assistant.content = payload.delta
  } else if (payload.text != null && payload.text !== '' && !assistant.content) {
    // chat 事件已经从 message.content 解析出 text 时不要被 text 字段覆盖
    assistant.content = payload.text
  }

  if (['final', 'error', 'aborted'].includes(payload.state)) {
    const finishReason = payload.finishReason || payload.finish_reason || ''
    assistant.finishReason = finishReason
    assistant.metrics = payload.metrics || {}
    if (finishReason === 'length' && assistant.content && !assistant.content.includes('输出达到长度上限')) {
      assistant.content += '\n\n[系统提示] 输出达到长度上限，回复可能未完整结束；可以继续追问“继续”。'
    }
    assistant.streaming = false
    activeRunId.value = ''
    sending.value = false
    // 保存到历史（只保存有内容的成功对话）
    if (payload.state === 'final' && assistant.content) {
      const lastUser = [...messages.value].reverse().find((m: any) => m.role === 'user')
      if (lastUser) pushHistory(lastUser.content, assistant.content)
    }
  }
}

function formatChatMetricNumber(value: unknown, digits = 2): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return ''
  return num.toFixed(digits).replace(/\.?0+$/, '')
}

function chatMessageMeta(msg: any): string {
  if (!msg || msg.role === 'user') return ''
  const metrics = msg.metrics || {}
  const parts: string[] = []
  const tokensPerSecond = formatChatMetricNumber(metrics.tokens_per_second ?? metrics.tokensPerSecond)
  if (tokensPerSecond) parts.push(`${tokensPerSecond} token/s`)
  const generatedTokens = formatChatMetricNumber(metrics.generated_tokens ?? metrics.generatedTokens, 0)
  const maxTokens = formatChatMetricNumber(metrics.effective_max_new_tokens ?? metrics.max_new_tokens ?? metrics.maxNewTokens, 0)
  if (generatedTokens && maxTokens) {
    parts.push(`${generatedTokens}/${maxTokens} tokens`)
  } else if (generatedTokens) {
    parts.push(`${generatedTokens} tokens`)
  }
  const runtime = metrics.inference_runtime === 'local_bridge' ? '本地 Bridge' : ''
  if (runtime) parts.push(runtime)
  return parts.join(' · ')
}

// 解析 message.content 数组（Anthropic 风格 content blocks）
function parseContentBlocks(assistant: any, blocks: any[]) {
  if (!Array.isArray(blocks)) return
  const textParts = []
  const thinkingParts = []
  const toolCalls: Record<string, unknown>[] = []

  for (const block of blocks) {
    if (!block || typeof block !== 'object') continue
    const type = block.type
    if (type === 'text') {
      textParts.push(block.text || '')
    } else if (type === 'thinking') {
      thinkingParts.push(block.thinking || block.text || '')
    } else if (type === 'tool_use') {
      toolCalls.push({
        id: block.id || `tool-${toolCalls.length}`,
        name: block.name || 'tool',
        input: block.input || {},
        result: null,
      })
    } else if (type === 'tool_result') {
      // 把 result 关联到对应的 tool_use（按 tool_use_id）
      const targetId = block.tool_use_id
      const target = toolCalls.find(t => t.id === targetId)
      const resultText = typeof block.content === 'string'
        ? block.content
        : Array.isArray(block.content)
          ? block.content.map((c: Record<string, unknown>) => (typeof c === 'string' ? c : c?.text || '')).join('\n')
          : JSON.stringify(block.content)
      if (target) {
        target.result = resultText
      } else {
        // 没找到对应的 tool_use（可能是上一轮的），加为孤立结果
        toolCalls.push({
          id: targetId || `result-${toolCalls.length}`,
          name: '工具结果',
          input: null,
          result: resultText,
        })
      }
    }
  }

  if (textParts.length) assistant.content = textParts.join('\n\n')
  if (thinkingParts.length) assistant.thinking = thinkingParts.join('\n\n')
  if (toolCalls.length) assistant.toolCalls = toolCalls
}

// 思考过程默认展开（undefined 视为 true）；点击在 true ↔ false 之间切换
function isThinkingOpen(idx: number) {
  return thinkingExpanded.value[idx] !== false
}
function toggleThinking(idx: number) {
  thinkingExpanded.value = {
    ...thinkingExpanded.value,
    [idx]: !isThinkingOpen(idx),
  }
}
// 工具默认折叠：undefined → false
function isToolsOpen(idx: number) {
  return toolsExpanded.value[idx] === true
}
function toggleTools(idx: number) {
  toolsExpanded.value = {
    ...toolsExpanded.value,
    [idx]: !isToolsOpen(idx),
  }
}

function toolInputPreview(input: any) {
  if (!input) return ''
  if (typeof input === 'string') return input.slice(0, 100)
  try {
    const json = JSON.stringify(input)
    return json.length > 100 ? json.slice(0, 100) + '…' : json
  } catch {
    return String(input).slice(0, 100)
  }
}

function ensureAssistantMessage(): any {
  const last = messages.value[messages.value.length - 1]
  if (last?.role === 'assistant' && last.streaming) return last
  const m = { role: 'assistant', content: '', thinking: '', toolCalls: [], streaming: true }
  messages.value.push(m)
  return m
}

function finalizeAssistant(text: string) {
  const a = ensureAssistantMessage()
  if (text && !a.content) a.content = text
  a.streaming = false
  sending.value = false
}

// ── 操作 ──
function submit() {
  if (modelChatDisabledReason.value) {
    Message.warning(modelChatDisabledReason.value)
    return
  }
  if (!canSubmit.value || sending.value) return
  if (!draft.value.trim() && !attachments.value.length) return
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    Message.warning('连接未就绪，请稍候')
    return
  }
  const text = draft.value.trim()
  const sendingAttachments = attachments.value.slice()
  messages.value.push({
    role: 'user',
    content: text,
    attachments: sendingAttachments,
    streaming: false,
  })
  messages.value.push({
    role: 'assistant',
    content: '',
    thinking: '',
    toolCalls: [],
    streaming: true,
  })
  socket.send(JSON.stringify({
    type: 'user_message',
    content: text,
    attachments: sendingAttachments,
    model_context: hasModelChatContext.value ? effectiveModelChatContext.value : undefined,
  }))
  draft.value = ''
  attachments.value = []
  sending.value = true
}

// 附件
function pickFiles() {
  fileInputRef.value?.click()
}
function removeAttachment(index: unknown) {
  attachments.value.splice(Number(index), 1)
}
// 允许的附件类型：图片、PDF、纯文本、常见代码 / 配置
const ALLOWED_MIME_PREFIXES = ['image/', 'text/']
const ALLOWED_MIMES = new Set([
  'application/pdf',
  'application/json',
  'application/xml',
  'application/x-yaml',
  'application/yaml',
  'application/javascript',
  'application/typescript',
  'application/x-python',
])
const ALLOWED_EXT = /\.(md|txt|json|yaml|yml|csv|tsv|log|py|js|ts|jsx|tsx|vue|html|css|scss|sh|sql|toml|ini|conf|env|xml)$/i

function isAllowedFile(file: File) {
  const mime = file.type || ''
  if (ALLOWED_MIME_PREFIXES.some(p => mime.startsWith(p))) return true
  if (ALLOWED_MIMES.has(mime)) return true
  if (ALLOWED_EXT.test(file.name || '')) return true
  return false
}

function attachmentIconName(att: any) {
  const m = att?.mimeType || ''
  if (m.startsWith('image/')) return 'folder'
  if (m.startsWith('text/') || m === 'application/json' || ALLOWED_EXT.test(att?.name || '')) return 'doc'
  return 'attach'
}

async function ingestFiles(files: File[]) {
  for (const file of files) {
    if (!isAllowedFile(file)) {
      Message.warning(`${file.name} 类型不支持，已跳过`)
      continue
    }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      Message.warning(`${file.name} 超过 5MB 限制`)
      continue
    }
    const currentTotal = attachments.value.reduce((sum, a) => sum + (a.sizeBytes || 0), 0)
    if (currentTotal + file.size > MAX_ATTACHMENTS_TOTAL_BYTES) {
      Message.warning(`附件总大小将超过 10MB，已停止添加`)
      break
    }
    try {
      const encoded = await fileToAttachment(file)
      attachments.value.push(encoded)
    } catch {
      Message.error(`${file.name} 读取失败`)
    }
  }
}

async function handleFileChange(event: Event) {
  const target = event.target as HTMLInputElement | null
  await ingestFiles(Array.from(target?.files || []) as File[])
  if (target) target.value = ''
}

// 拖拽上传：dragenter / dragover 阻止默认行为，drop 处理文件
let dragCounter = 0
function onDragEnter(e: DragEvent) {
  e.preventDefault()
  if (!canSubmit.value) return
  dragCounter += 1
  isDragging.value = true
}
function onDragLeave(e: DragEvent) {
  e.preventDefault()
  dragCounter -= 1
  if (dragCounter <= 0) {
    dragCounter = 0
    isDragging.value = false
  }
}
function onDragOver(e: DragEvent) {
  e.preventDefault()
}
async function onDrop(e: DragEvent) {
  e.preventDefault()
  dragCounter = 0
  isDragging.value = false
  if (!canSubmit.value) return
  const files = Array.from(e.dataTransfer?.files || [])
  if (files.length) await ingestFiles(files)
}
function fileToAttachment(file: File): Promise<any> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = String(reader.result || '')
      const commaIdx = dataUrl.indexOf(',')
      const base64 = commaIdx >= 0 ? dataUrl.slice(commaIdx + 1) : dataUrl
      resolve({
        name: file.name,
        mimeType: file.type || 'application/octet-stream',
        data: base64,
        sizeBytes: file.size,
      })
    }
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

function askQuestion(q: string) {
  draft.value = q
  nextTick(submit)
}

function abortRun() {
  if (!activeRunId.value || !socket || socket.readyState !== WebSocket.OPEN) return
  socket.send(JSON.stringify({ type: 'abort', runId: activeRunId.value }))
}

function resetConversation() {
  messages.value = []
  draft.value = ''
}

function toggleHistory() {
  historyOpen.value = !historyOpen.value
}

async function selectInstance(item: any) {
  if (!item?.id || item.id === selectedInstanceId.value) return
  selectedInstanceId.value = item.id
  await onInstanceChange()
}

// ── 加载 ──
async function reloadInstances() {
  loadingInstances.value = true
  try {
    const [instanceResult, coverageResult, agentizationResult, businessAgentResult] = await Promise.allSettled([
      aiclawApi.listInstances(),
      aiclawApi.agentCoverage?.() || Promise.resolve(null),
      learningApi.candidates?.({ target_type: 'agent', status: 'reviewing', limit: 8 }) || Promise.resolve(null),
      aiclawApi.listAnalysisAgents?.() || Promise.resolve(null),
    ])
    if (instanceResult.status === 'rejected') {
      throw instanceResult.reason
    }
    instances.value = Array.isArray(instanceResult.value) ? instanceResult.value : []
    coverageRows.value = coverageResult.status === 'fulfilled' && Array.isArray(coverageResult.value?.items)
      ? coverageResult.value.items
      : []
    agentizationCandidates.value = agentizationResult.status === 'fulfilled' && Array.isArray(agentizationResult.value?.items)
      ? agentizationResult.value.items
      : []
    businessAnalysisAgents.value = businessAgentResult.status === 'fulfilled' && Array.isArray(businessAgentResult.value?.items)
      ? businessAgentResult.value.items
      : []
    // 模型对话优先选择可分析的在线 Agent；普通对话仍使用平台默认或任意在线实例。
    const current = instances.value.find(i => i.id === selectedInstanceId.value)
    const modelChatTarget = hasModelChatContext.value ? preferredModelChatInstance(instances.value) : null
    const platformDefault = instances.value.find(i => i.is_platform_default && i.bridge_online)
    const online = instances.value.find(i => i.bridge_online)
    const target = hasModelChatContext.value
      ? (modelChatTarget || platformDefault || online || current || instances.value[0])
      : (current || platformDefault || online || instances.value[0])
    if (target && target.id !== selectedInstanceId.value) {
      selectedInstanceId.value = target.id
    } else if (!target) {
      selectedInstanceId.value = ''
      agents.value = []
      selectedAgentId.value = ''
    }
  } catch (e: any) {
    Message.error(e._message || '加载实例失败')
    instances.value = []
    coverageRows.value = []
    businessAnalysisAgents.value = []
  } finally {
    loadingInstances.value = false
  }
}

async function loadAgents(instanceId: string) {
  agentSkills.value = []
  if (!instanceId) {
    agents.value = []
    selectedAgentId.value = ''
    return
  }
  try {
    const data = await aiclawApi.listAgents(instanceId)
    agents.value = data.items || []
    if (agents.value.length) {
      selectedAgentId.value = agents.value[0].id || agents.value[0].name
    } else {
      selectedAgentId.value = ''
    }
  } catch (e: any) {
    agents.value = []
    selectedAgentId.value = ''
  }
}

async function loadAgentSkills(instanceId: string, agentId: string) {
  agentSkills.value = []
  if (!instanceId || !agentId) return
  loadingAgentSkills.value = true
  try {
    const data = await aiclawApi.listSkills(instanceId, agentId)
    agentSkills.value = Array.isArray(data?.items) ? data.items : []
  } catch {
    agentSkills.value = []
  } finally {
    loadingAgentSkills.value = false
  }
}

async function onInstanceChange() {
  clearReconnectTimer()
  await loadAgents(selectedInstanceId.value)
  await loadAgentSkills(selectedInstanceId.value, selectedAgentId.value)
  connectSocket()
}

async function onAgentChange() {
  clearReconnectTimer()
  await loadAgentSkills(selectedInstanceId.value, selectedAgentId.value)
  connectSocket()
}

watch(() => messages.value.length, () => {
  nextTick(() => {
    if (chatLogRef.value) {
      chatLogRef.value.scrollTop = chatLogRef.value.scrollHeight
    }
  })
})

// 当 selectedInstance 变化时（含初次加载），把 dept 默认选成它所属部门，
// 避免新用户进入页面后 middle 列空白。仅当用户尚未手动选择 / 切换 offline-only 时同步。
watch(selectedInstance, (next) => {
  if (!next || showOnlyOffline.value) return
  const deptKey = departmentLabel(next) || ''
  if (!deptKey) return
  if (!selectedDepartmentKey.value) {
    selectedDepartmentKey.value = deptKey
  }
}, { immediate: false })

// 切换到 chat 模式时如果没历史消息，让光标聚焦输入框（沿用旧 UX 即可，不做额外处理）。

function applyRouteDraft() {
  const routeDraft = routeQueryString(route.query?.draft || route.query?.prompt).trim()
  if (!routeDraft || draft.value.trim()) return
  draft.value = routeDraft
}

async function validateModelChatContext() {
  if (!hasModelChatContext.value) {
    modelChatValidation.value = null
    validatingModelChatContext.value = false
    return
  }
  validatingModelChatContext.value = true
  try {
    const response = await aiclawApi.validateChatModelContext({
      model_context: modelChatContext.value,
    })
    modelChatValidation.value = {
      ready: Boolean(response?.ready),
      disabled_reason: String(response?.disabled_reason || ''),
      context: response?.context && typeof response.context === 'object' ? response.context : undefined,
    }
  } catch (error: any) {
    modelChatValidation.value = {
      ready: false,
      disabled_reason: String(error?._message || error?.message || '训练后模型上下文校验失败。'),
    }
  } finally {
    validatingModelChatContext.value = false
  }
}

watch(() => route.query, () => {
  applyRouteDraft()
  void validateModelChatContext()
}, { deep: true })

onMounted(async () => {
  unmounted = false
  applyRouteDraft()
  await validateModelChatContext()
  await reloadInstances()
  if (selectedInstanceId.value) {
    await loadAgents(selectedInstanceId.value)
    await loadAgentSkills(selectedInstanceId.value, selectedAgentId.value)
    connectSocket()
  }
  statusPollTimer = window.setInterval(() => {
    void refreshConnectionState()
  }, 15000)
})

onBeforeUnmount(() => {
  unmounted = true
  clearReconnectTimer()
  if (statusPollTimer) {
    window.clearInterval(statusPollTimer)
    statusPollTimer = null
  }
  socketSeq += 1
  socket?.close()
  socket = null
})
</script>

<style scoped>
.brain-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
  background: var(--ai-bg);
}

.brain-topbar {
  flex-shrink: 0;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  padding: 22px 28px 16px;
  margin-bottom: 0;
  background: var(--ai-surface);
  border-bottom: 1px solid var(--ai-border);
}
.brain-title-block {
  min-width: 0;
}
.brain-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  flex-wrap: nowrap;
}
/* 让所有 Arco 按钮 / 选择器在工具栏里都"无背景，仅边框"，融入页面 */
.brain-tools :deep(.arco-select-view-single),
.brain-tools :deep(.arco-btn) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  color: var(--ai-ink-1) !important;
  font-weight: 500 !important;
  box-shadow: none !important;
  height: 30px !important;
  padding: 0 12px !important;
  min-width: auto !important;
  white-space: nowrap !important;
  gap: 6px !important;
  font-size: 12.5px !important;
  border-radius: 6px !important;
}
/* 图标专属按钮（如刷新）改成正方形 */
.brain-tools :deep(.arco-btn-only-icon) {
  flex: 0 0 30px !important;
  width: 30px !important;
  min-width: 30px !important;
  max-width: 30px !important;
  padding: 0 !important;
}
.brain-tools :deep(.arco-select-view-single):hover,
.brain-tools :deep(.arco-btn:hover) {
  border-color: var(--ai-border-2) !important;
  color: var(--ai-ink-1) !important;
  background: var(--ai-surface-2) !important;
}
.brain-tools :deep(.arco-select-view-single .arco-select-view-input),
.brain-tools :deep(.arco-select-view-single .arco-select-view-value) {
  color: var(--ai-ink-1) !important;
}
/* 选择器高度对齐 */
.brain-tools :deep(.arco-select-view-single) {
  height: 30px !important;
}
.brain-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-3);
  white-space: nowrap;
  flex-shrink: 0;
  letter-spacing: 0;
}
.brain-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ai-ink-4);
}
.brain-status.online {
  color: var(--ai-ok);
  background: var(--ai-ok-soft);
  border-color: transparent;
}
.brain-status.online .brain-status-dot { background: var(--ai-ok); }
.brain-status.offline {
  color: var(--ai-ink-4);
  background: var(--ai-surface-2);
}
.brain-status.offline .brain-status-dot { background: var(--ai-ink-5); }
.brain-status.connecting {
  color: var(--ai-info);
  background: var(--ai-info-soft);
  border-color: transparent;
}
.brain-status.connecting .brain-status-dot {
  background: var(--ai-info);
  animation: brain-pulse 1.2s ease-in-out infinite;
}
.brain-status.idle {
  color: var(--ai-ink-4);
}
.brain-training-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  border: 1px solid transparent;
  color: var(--ai-warn);
  background: var(--ai-warn-soft);
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
}
@keyframes brain-pulse { 50% { opacity: 0.35; } }

/* ── 主区：3-column IA ── */
.brain-body {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
  position: relative;
  padding: 0;
  gap: 0;
  background: var(--ai-bg);
}

/* Col 1 — dept sidebar (220px) */
.brain-dept-rail {
  flex: 0 0 220px;
  min-width: 0;
  border-right: 1px solid var(--ai-border);
  background: var(--ai-surface);
  padding: 16px 12px;
  overflow-y: auto;
}

.brain-dept-rail-head {
  display: none;
  align-items: center;
  justify-content: space-between;
  padding: 0 4px 6px;
}
.brain-dept-rail-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}
.brain-dept-rail-close {
  display: none;
  background: transparent;
  border: 0;
  cursor: pointer;
  color: var(--ai-ink-4);
  padding: 4px;
}
.brain-dept-rail-close:hover {
  color: var(--ai-ink-1);
}
.brain-dept-rail-toggle {
  display: none;
  align-items: center;
  gap: 8px;
  margin: 10px 14px 0;
  padding: 0 12px;
  height: 30px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
  cursor: pointer;
  font-family: var(--ai-font-sans);
  align-self: flex-start;
}
/* 把 button 元素的内置样式抹掉，让它复用 .ai-side-item 的视觉 */
.brain-dept-rail .ai-side-item {
  width: 100%;
  border: none;
  background: transparent;
  text-align: left;
  font: inherit;
}
.brain-dept-item {
  width: 100%;
}
.brain-dept-icon,
.brain-side-icon {
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
}
.brain-dept-rail-close svg,
.brain-dept-rail-toggle svg {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
}
.brain-button-icon {
  width: 11px;
  height: 11px;
}
.brain-dept-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.brain-dept-count {
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-4);
}
.brain-dept-count.ok { color: var(--ai-ok); }
.brain-dept-count.idle { color: var(--ai-ink-4); }
.brain-count-bad { color: var(--ai-bad); }

/* Col 2 — agent list (320px) */
.brain-agent-rail {
  flex: 0 0 320px;
  min-width: 0;
  border-right: 1px solid var(--ai-border);
  background: var(--ai-surface);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.brain-rail-head {
  padding: 14px 14px 10px;
  border-bottom: 1px solid var(--ai-border);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.brain-rail-head-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.brain-rail-title-block {
  min-width: 0;
}
.brain-rail-title {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--ai-ink-1);
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.brain-rail-sub {
  margin-top: 3px;
  font-size: 11px;
  color: var(--ai-ink-4);
  font-weight: 400;
}
.brain-new-btn {
  flex-shrink: 0;
}
.brain-purpose-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.brain-purpose-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}
.brain-purpose-pill:hover {
  border-color: var(--ai-border-2);
  color: var(--ai-ink-1);
}
.brain-purpose-pill.active {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  border-color: var(--ai-ink-1);
}
.brain-purpose-count {
  font-family: var(--ai-font-mono);
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
  opacity: 0.85;
}
.brain-purpose-pill.active .brain-purpose-count { opacity: 1; }

.brain-agent-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
.brain-agent-group {
  display: flex;
  flex-direction: column;
}
.brain-agent-group-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px 6px;
}
.brain-agent-group-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex: 0 0 6px;
}
.brain-agent-group-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--ai-ink-2);
}
.brain-agent-group-desc {
  font-size: 11px;
  color: var(--ai-ink-4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.brain-agent-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: none;
  border-left: 2px solid transparent;
  border-bottom: 1px solid var(--ai-border);
  background: transparent;
  color: var(--ai-ink-1);
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.brain-agent-row:hover { background: var(--ai-surface-2); }
.brain-agent-row.active {
  background: var(--ai-surface-2);
  border-left-color: var(--ai-ink-1);
}
.brain-agent-row.offline { opacity: 0.62; }
.brain-agent-row-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: 0 0 8px;
  margin-top: 5px;
  background: var(--ai-ink-5);
}
.brain-agent-row-dot.online { background: var(--ai-ok); }
.brain-agent-row-dot.offline { background: var(--ai-ink-5); }
.brain-agent-row-dot.bad { background: var(--ai-bad); }
.brain-agent-row-dot.idle { background: var(--ai-ink-5); }
.brain-agent-row-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.brain-agent-row-name {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.brain-agent-row-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--ai-ink-4);
  font-weight: 400;
  flex-wrap: wrap;
}
.brain-agent-row-meta .mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.brain-agent-row-last {
  margin-left: auto;
  font-size: 10.5px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
}

/* Col 3 — detail / chat */
.brain-detail {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--ai-bg);
  overflow: hidden;
}
.brain-detail-head {
  flex-shrink: 0;
  padding: 18px 24px 14px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.brain-detail-head-row {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}
.brain-detail-avatar {
  width: 44px;
  height: 44px;
  border-radius: var(--ai-radius);
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  display: grid;
  place-items: center;
  flex-shrink: 0;
}
.brain-detail-id-block {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.brain-detail-title-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.brain-detail-title {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.015em;
  color: var(--ai-ink-1);
}
.brain-detail-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  font-size: 11.5px;
  color: var(--ai-ink-4);
  font-weight: 400;
}
.brain-fact { white-space: nowrap; }
.brain-fact b {
  color: var(--ai-ink-2);
  font-weight: 500;
  margin-left: 4px;
}
.brain-detail-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.brain-detail-actions .ai-btn {
  white-space: nowrap;
}
.brain-detail-actions .ai-btn svg,
.brain-detail-avatar svg {
  width: 14px;
  height: 14px;
  flex: 0 0 14px;
}
.brain-detail-avatar svg {
  width: 20px;
  height: 20px;
}
.brain-detail-actions .ai-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.brain-detail-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 24px 24px;
  background: var(--ai-bg);
}
.brain-detail-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
.brain-detail-right-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.brain-card-recent { order: 10; }
.brain-card-branch,
.brain-card-capability,
.brain-card-active-job {
  order: 20;
}
.brain-card-skills { min-width: 0; }
.brain-card-empty {
  padding: 24px 16px;
  text-align: center;
  color: var(--ai-ink-4);
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.brain-card-empty-sub {
  font-size: 11px;
  color: var(--ai-ink-5);
}
.brain-card-h-actions {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.brain-skills-table {
  table-layout: fixed;
}
.brain-skills-table tbody tr.active td {
  background: var(--ai-surface-2);
}
.brain-skill-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.brain-skill-name {
  font-weight: 500;
  font-size: 12.5px;
  color: var(--ai-ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.brain-skill-cell .mono-id {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.brain-skill-src {
  display: inline-block;
  max-width: 100%;
  font-size: 11px;
  color: var(--ai-ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.brain-skill-risk {
  font-size: 10.5px;
}
.brain-skill-risk.risk-r1 {
  color: var(--ai-ok);
  background: var(--ai-ok-soft);
  border-color: transparent;
}
.brain-skill-risk.risk-r2 {
  color: var(--ai-warn);
  background: var(--ai-warn-soft);
  border-color: transparent;
}
.brain-skill-risk.risk-r3 {
  color: var(--ai-bad);
  background: var(--ai-bad-soft);
  border-color: transparent;
}
.brain-skill-action {
  text-align: right;
}
.brain-kpi-spark {
  padding: 4px 14px 14px;
}
.brain-kpi-delta {
  font-size: 11px;
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
}
.brain-kpi-delta.ok { color: var(--ai-ok); }
.brain-branch-list {
  display: grid;
  gap: 4px;
  padding: 6px 14px 14px;
}
.brain-branch-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.brain-branch-row:hover {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.brain-branch-row.active {
  background: var(--ai-surface-2);
  border-color: var(--ai-ink-1);
}
.brain-business-agent-row {
  cursor: default;
}
.brain-business-agent-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.brain-business-edit-main,
.brain-business-edit-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: var(--ai-accent);
  color: var(--ai-accent-ink, #fff);
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.15s ease, opacity 0.15s ease;
}
.brain-business-edit-main {
  margin-left: auto;
}
.brain-business-edit-main:hover,
.brain-business-edit-btn:hover {
  transform: translateY(-1px);
  box-shadow: var(--ai-shadow-1);
}
.brain-business-edit-main:disabled,
.brain-business-edit-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
  transform: none;
  box-shadow: none;
}
.brain-business-prompt {
  display: block;
  color: var(--ai-ink-2);
  font-size: 11.5px;
  line-height: 1.45;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.brain-business-capabilities {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 2px;
}
.brain-business-capabilities--edit {
  margin-top: 0;
}
.brain-business-validation {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  height: 20px;
  margin-top: 2px;
  padding: 0 7px;
  border-radius: 4px;
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  font-size: 11px;
  font-weight: 500;
}
.brain-business-validation.ok {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
}
.brain-business-validation.bad {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}
.brain-branch-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.brain-branch-name {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.brain-branch-type {
  font-size: 10.5px;
}
.brain-kpi-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.brain-kpi {
  padding: 14px;
  border-right: 1px solid var(--ai-border);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.brain-kpi:last-child { border-right: 0; }
.brain-kpi-label {
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
}
.brain-kpi-value {
  color: var(--ai-ink-1);
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.015em;
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
}
.brain-card-link {
  margin-left: auto;
  font-size: 12px;
  color: var(--ai-ink-3);
}
.brain-agent-edit-form {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.brain-agent-edit-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.brain-agent-edit-grid--five {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}
.brain-agent-edit-grid :deep(.arco-input-number) {
  width: 100%;
}
.brain-agent-presets,
.brain-output-section-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.brain-output-section-options :deep(.arco-checkbox) {
  margin-right: 0;
}
.brain-card-strong {
  margin-left: auto;
  font-size: 12px;
  color: var(--ai-ink-3);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.brain-coverage-list-card {
  display: grid;
  gap: 6px;
  padding: 10px 14px 14px;
}
.brain-recent-card-list {
  display: grid;
  gap: 4px;
  padding: 8px 14px 14px;
}
.brain-recent-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.brain-recent-line:hover {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.brain-recent-icon {
  width: 11px;
  height: 11px;
  flex: 0 0 11px;
  color: var(--ai-ink-4);
}
.brain-recent-line span {
  min-width: 0;
  font-size: 12px;
  color: var(--ai-ink-2);
  font-weight: 400;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.brain-recent-line small {
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-family: var(--ai-font-mono);
  flex-shrink: 0;
}
.brain-detail-warning {
  margin-top: 14px;
  padding: 10px 14px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-radius: var(--ai-radius);
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  font-size: 12px;
  font-weight: 500;
}
.brain-detail-warning svg,
.brain-hero-warning svg {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
}
.brain-chat-wrap {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--ai-surface);
  overflow: hidden;
}
.brain-chat-wrap.is-empty {
  display: grid;
  grid-template-rows: 1fr auto 1fr;
  justify-items: center;
  padding: 24px;
}
.brain-chat-wrap.is-empty.has-model-context {
  grid-template-rows: auto 1fr auto 1fr;
}

/* mono helper for inline id strings (used in design's mono-id) */
.mono-id {
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-3);
  font-variant-numeric: tabular-nums;
}
.mono {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

/* 历史抽屉（独立浮层，从右侧滑入） */
.brain-history-drawer {
  position: absolute;
  top: 0;
  bottom: 0;
  right: 0;
  width: 360px;
  z-index: 20;
  padding: 14px;
  background: var(--ai-bg);
  border-left: 1px solid var(--ai-border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
@media (max-width: 1180px) {
  .brain-history-drawer { width: 100%; }
}

.brain-purpose-chip,
.brain-hero-state,
.brain-capability {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 20px;
  padding: 0 7px;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
  font-size: 11px;
  line-height: 1;
  font-weight: 500;
  white-space: nowrap;
}
.brain-purpose-chip.purpose-analysis,
.brain-capability.on {
  color: var(--ai-info);
  background: var(--ai-info-soft);
  border-color: transparent;
}
.brain-purpose-chip.purpose-training {
  color: var(--ai-warn);
  background: var(--ai-warn-soft);
  border-color: transparent;
}
.brain-purpose-chip.purpose-mixed {
  color: var(--ai-accent-ink);
  background: var(--ai-accent-soft);
  border-color: transparent;
}
.brain-agent-empty {
  padding: 18px;
  /* webkit 1px dashed 在 4 边都渲染成 solid，用 4 个 background gradient 拼出虚线框 */
  background-color: var(--ai-surface);
  background-image:
    linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to bottom, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to bottom, var(--ai-border-2) 50%, transparent 0%);
  background-position: top, bottom, left, right;
  background-size: 6px 1px, 6px 1px, 1px 6px, 1px 6px;
  background-repeat: repeat-x, repeat-x, repeat-y, repeat-y;
  border-radius: var(--ai-radius);
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 400;
  text-align: center;
}

/* 拖拽时整个 body 内嵌一层提示 */
.brain-drag-overlay {
  position: absolute;
  inset: 12px;
  z-index: 10;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  border: 2px dashed var(--ai-ink-2);
  border-radius: var(--ai-radius-l);
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  font-size: 15px;
  font-weight: 500;
  pointer-events: none;
  backdrop-filter: blur(2px);
}
.brain-drag-icon {
  width: 32px;
  height: 32px;
}
.brain-drag-hint {
  font-size: 11px;
  font-weight: 400;
  color: var(--ai-ink-4);
  text-transform: none;
}
.brain-model-context {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 16px auto 8px;
  width: min(920px, calc(100% - 32px));
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: var(--ai-radius);
  background: var(--ai-info-soft);
  color: var(--ai-info);
}
.brain-model-context > div {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.brain-model-kicker {
  color: var(--ai-info);
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.brain-model-context strong {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.brain-model-context small {
  color: var(--ai-ink-3);
  font-size: 11px;
  font-weight: 400;
  font-family: var(--ai-font-mono);
}
.brain-model-warning {
  display: flex;
  align-items: center;
  gap: 8px;
  width: min(920px, calc(100% - 32px));
  margin: -2px auto 10px;
  padding: 8px 10px;
  border: 1px solid rgba(180, 83, 9, 0.22);
  border-radius: var(--ai-radius-s);
  background: rgba(251, 191, 36, 0.1);
  color: #92400e;
  font-size: 12px;
}
.brain-model-warning svg {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
}

/* 空状态：3 段 grid，输入框落在垂直正中 */
.brain-empty-top {
  align-self: end;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding-bottom: 32px;
  text-align: center;
}
.brain-empty-input {
  width: 100%;
  max-width: 720px;
}
.brain-empty-bottom {
  align-self: start;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding-top: 24px;
  width: 100%;
  max-width: 720px;
}

/* 对话状态：底部输入框 */
.brain-chat-input {
  flex-shrink: 0;
  padding: 16px 24px 20px;
  width: 100%;
}
.brain-chat-input .brain-input-wrap {
  max-width: 920px;
  margin: 0 auto;
}

/* ── 闪电图标 + 旋转光环 ──
   设计：图标静止，外面一圈细环旋转。
   光环用 conic-gradient 画"彗星尾"效果，配合 mask-composite 切出环形。 */
.brain-hero-icon-wrap {
  position: relative;
  width: 88px;
  height: 88px;
  display: grid;
  place-items: center;
  margin-bottom: 8px;
}

/* 旋转光环（彗星尾效果，淡） */
.brain-hero-icon-wrap::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  padding: 1.5px;
  background: conic-gradient(
    from 0deg,
    transparent 0deg,
    transparent 220deg,
    var(--ai-border) 260deg,
    var(--ai-border-2) 320deg,
    var(--ai-border-3) 352deg,
    transparent 360deg
  );
  -webkit-mask:
    linear-gradient(#fff 0 0) content-box,
    linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask:
    linear-gradient(#fff 0 0) content-box,
    linear-gradient(#fff 0 0);
  mask-composite: exclude;
  animation: brain-orbit 3.4s linear infinite;
}

/* 外层柔光：整圈极淡发光，不旋转 */
.brain-hero-icon-wrap::after {
  content: '';
  position: absolute;
  inset: -6px;
  border-radius: 50%;
  background: radial-gradient(circle, var(--ai-surface-2) 30%, transparent 70%);
  z-index: -1;
  pointer-events: none;
}

/* 图标本体（方角，黑底，参考设计稿 agent 头像） */
.brain-hero-icon {
  width: 64px;
  height: 64px;
  border-radius: var(--ai-radius);
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  display: flex;
  align-items: center;
  justify-content: center;
}
.brain-hero-icon svg {
  width: 44px;
  height: 44px;
}

@keyframes brain-orbit {
  to { transform: rotate(360deg); }
}
@keyframes brain-spin {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .brain-hero-icon-wrap::before { animation: none; }
}

.brain-hero-title {
  margin: 0;
  font-size: 24px;
  font-weight: 600;
  color: var(--ai-ink-1);
  letter-spacing: -0.02em;
  line-height: 1.2;
}
.brain-hero-desc {
  margin: 0;
  font-size: 13px;
  color: var(--ai-ink-3);
  font-weight: 400;
  letter-spacing: 0;
}
.brain-hero-tags,
.brain-hero-facts {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 6px;
}
.brain-hero-state.online {
  color: var(--ai-ok);
  background: var(--ai-ok-soft);
  border-color: transparent;
}
.brain-hero-state.offline {
  color: var(--ai-bad);
  background: var(--ai-bad-soft);
  border-color: transparent;
}
.brain-hero-state.connecting {
  color: var(--ai-info);
  background: var(--ai-info-soft);
  border-color: transparent;
}
.brain-hero-state.idle {
  color: var(--ai-ink-4);
}
.brain-hero-facts span {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 11px;
  font-weight: 500;
}
.brain-hero-suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  max-width: 640px;
  margin-top: 16px;
}
.brain-recent-section {
  margin-top: 24px;
  max-width: 680px;
  text-align: left;
  width: 100%;
}
.brain-section-title {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 8px;
}
.brain-recent-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}
.brain-recent-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 14px;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.brain-recent-card:hover {
  border-color: var(--ai-border-2);
  background: var(--ai-surface-2);
}
.brain-recent-q {
  font-size: 12.5px;
  color: var(--ai-ink-1);
  font-weight: 500;
  line-height: 1.4;
}
.brain-recent-meta {
  font-size: 11px;
  color: var(--ai-ink-4);
}
.brain-recent-inst {
  font-family: var(--ai-font-mono);
  color: var(--ai-ink-3);
}
@media (max-width: 640px) {
  .brain-recent-grid { grid-template-columns: 1fr; }
}
.brain-suggestion-chip {
  height: 28px;
  padding: 0 14px;
  border-radius: 999px;
  font-size: 12.5px;
  font-weight: 500;
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-2);
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.brain-suggestion-chip:hover:not(:disabled) {
  border-color: var(--ai-border-2);
  color: var(--ai-ink-1);
  background: var(--ai-surface-2);
}
.brain-suggestion-chip:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.brain-hero-warning {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border-radius: var(--ai-radius);
  background: var(--ai-warn-soft);
  border: 1px solid transparent;
  color: var(--ai-warn);
  font-size: 12px;
  font-weight: 500;
  margin-top: 8px;
}
.brain-link {
  color: var(--ai-ink-1);
  text-decoration: underline;
  cursor: pointer;
  font-weight: 500;
}

/* 对话流 */
.brain-chat-log {
  flex: 1;
  overflow-y: auto;
  padding: 24px 24px 12px;
  scroll-behavior: smooth;
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 920px;
  margin: 0 auto;
}

/* 单条消息（左右分边） */
.brain-msg {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 18px;
  max-width: 78%;
}
.brain-msg.user {
  align-self: flex-end;
  align-items: flex-end;
}
.brain-msg.assistant {
  align-self: flex-start;
  align-items: flex-start;
}

/* 头部：avatar + 名称 */
.brain-msg-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--ai-ink-4);
  font-weight: 500;
  letter-spacing: 0;
  padding: 0 4px;
}
.brain-msg-avatar {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 600;
}
.brain-msg-avatar.assistant {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
}
.brain-msg-avatar.user {
  background: var(--ai-surface-3);
  color: var(--ai-ink-1);
}
.brain-msg-name {
  white-space: nowrap;
}
.brain-msg-meta {
  padding: 0 4px;
  font-size: 11px;
  line-height: 1.4;
  color: var(--ai-ink-4);
}
.brain-msg.user .brain-msg-meta {
  text-align: right;
}

/* 气泡 */
.brain-msg-bubble {
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-word;
  font-weight: 400;
}
.brain-msg.user .brain-msg-bubble {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  border-bottom-right-radius: 4px;
}
.brain-msg.assistant .brain-msg-bubble {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  border: 1px solid var(--ai-border);
  border-bottom-left-radius: 4px;
}

/* Markdown 内嵌样式 */
.brain-msg-bubble :deep(p) { margin: 0 0 6px; }
.brain-msg-bubble :deep(p:last-child) { margin-bottom: 0; }
.brain-msg-bubble :deep(pre) {
  background: var(--ai-surface-3);
  padding: 8px 10px;
  border-radius: var(--ai-radius-s);
  overflow-x: auto;
  font-size: 12px;
  margin: 6px 0;
  font-family: var(--ai-font-mono);
}
.brain-msg.user .brain-msg-bubble :deep(pre) {
  background: rgba(255, 255, 255, 0.12);
}
.brain-msg-bubble :deep(code) {
  padding: 1px 5px;
  border-radius: var(--ai-radius-s);
  font-size: 12px;
  background: var(--ai-surface-3);
  font-family: var(--ai-font-mono);
}
.brain-msg.user .brain-msg-bubble :deep(code) {
  background: rgba(255, 255, 255, 0.15);
}
.brain-msg-bubble :deep(ul),
.brain-msg-bubble :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}

/* 流式光标 */
.brain-msg-cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  vertical-align: text-bottom;
  margin-left: 2px;
  background: currentColor;
  animation: brain-cursor-blink 1s step-end infinite;
}
@keyframes brain-cursor-blink {
  from, 50% { opacity: 1; }
  51%, to { opacity: 0; }
}

/* ── 思考过程（折叠 + 限高滚动） ── */
.brain-msg-thinking {
  width: 100%;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
  overflow: hidden;
  font-size: 11px;
}
.brain-thinking-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 10px;
  border: none;
  background: transparent;
  color: var(--ai-ink-3);
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  cursor: pointer;
  transition: color 0.15s ease;
}
.brain-thinking-head:hover { color: var(--ai-ink-1); }
.brain-msg-avatar svg,
.brain-chevron,
.brain-tool-badge svg {
  width: 11px;
  height: 11px;
  flex: 0 0 auto;
}
.brain-chevron {
  transition: transform 0.15s ease;
}
.brain-chevron.is-up {
  transform: rotate(180deg);
}
.brain-thinking-len {
  margin-left: auto;
  font-size: 10px;
  font-weight: 400;
  color: var(--ai-ink-4);
  text-transform: none;
  letter-spacing: 0;
  font-variant-numeric: tabular-nums;
}
.brain-thinking-body {
  max-height: 180px;
  overflow-y: auto;
  padding: 0 10px 8px;
  font-size: 11px;
  line-height: 1.55;
  color: var(--ai-ink-3);
  white-space: pre-wrap;
  word-break: break-word;
  font-weight: 400;
  font-style: italic;
}

/* ── 工具调用（默认角标折叠） ── */
.brain-msg-tools {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
}
.brain-tool-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 10.5px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease;
}
.brain-tool-badge:hover {
  border-color: var(--ai-border-2);
  color: var(--ai-ink-1);
}
.brain-tool-list {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 2px;
}
.brain-tool-line {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 3px 8px;
  font-size: 11px;
  line-height: 1.5;
  border-left: 2px solid var(--ai-border);
  color: var(--ai-ink-3);
  font-weight: 400;
  font-family: var(--ai-font-mono);
  overflow: hidden;
}
.brain-tool-name {
  color: var(--ai-ink-1);
  font-weight: 500;
  flex-shrink: 0;
}
.brain-tool-input {
  color: var(--ai-ink-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-shrink: 1;
  min-width: 0;
}
.brain-tool-result {
  color: var(--ai-ink-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-shrink: 1;
  min-width: 0;
}

/* ── 用户消息附件预览 ── */
.brain-msg-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 0 4px;
}
.brain-msg-attachment {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  font-size: 11px;
  color: var(--ai-ink-2);
  font-weight: 500;
}
.brain-msg-attachment svg {
  width: 11px;
  height: 11px;
  flex: 0 0 auto;
}

/* ── 输入框附件 chip 列表 ── */
.brain-attach-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.brain-attach-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px 3px 10px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  font-size: 11px;
  color: var(--ai-ink-1);
  font-weight: 500;
  max-width: 240px;
}
.brain-attach-chip > span {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.brain-attach-chip > svg {
  width: 11px;
  height: 11px;
  flex: 0 0 auto;
}
.brain-attach-remove {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: none;
  background: var(--ai-surface-3);
  color: var(--ai-ink-2);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
}
.brain-attach-remove svg {
  width: 10px;
  height: 10px;
}
.brain-attach-remove:hover { background: var(--ai-ink-1); color: var(--ai-surface); }

.brain-file-input {
  display: none;
}

/* 输入框 — 竖向 stack：附件列表 → textarea → 按钮行 */
.brain-input-wrap {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 14px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  transition: border-color 0.15s ease;
}
.brain-input-wrap:focus-within {
  border-color: var(--ai-border-2);
}
.brain-input-wrap.is-empty {
  padding: 12px 16px;
}
/* .brain-textarea 本身就是 Arco 的 .arco-textarea-wrapper */
.brain-textarea,
.brain-textarea:hover,
.brain-textarea.arco-textarea-focus {
  flex: 1;
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  outline: none !important;
}
.brain-textarea :deep(.arco-textarea) {
  font-size: 13px !important;
  font-weight: 400;
  background: transparent !important;
  padding: 4px 0 !important;
  color: var(--ai-ink-1) !important;
  border: none !important;
  outline: none !important;
  box-shadow: none !important;
}
.brain-textarea :deep(.arco-textarea:focus),
.brain-textarea :deep(.arco-textarea:focus-visible) {
  border: none !important;
  outline: none !important;
  box-shadow: none !important;
}
.brain-textarea :deep(.arco-textarea::placeholder) {
  color: var(--ai-ink-4) !important;
  opacity: 1;
}
.brain-input-actions {
  display: flex;
  gap: 6px;
  align-items: center;
  justify-content: flex-end;
  width: 100%;
}

/* 圆形发送按钮（黑主调，参考设计稿） */
.brain-send-btn {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: none;
  background: var(--ai-surface-3);
  color: var(--ai-ink-4);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
  flex-shrink: 0;
}
.brain-send-btn:disabled {
  cursor: not-allowed;
}
.brain-send-btn svg,
.brain-icon-btn svg {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
}
.brain-send-btn svg {
  width: 16px;
  height: 16px;
}
.brain-icon-spin {
  animation: brain-spin 0.8s linear infinite;
}
.brain-send-btn.is-ready {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
}
.brain-send-btn.is-ready:hover {
  background: #000;
}

/* 圆形辅助图标按钮（中止 / 新会话） */
.brain-icon-btn {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-3);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
  flex-shrink: 0;
}
.brain-icon-btn:hover {
  border-color: var(--ai-border-2);
  color: var(--ai-ink-1);
  background: var(--ai-surface-2);
}
.brain-icon-btn-warn:hover {
  border-color: transparent;
  color: var(--ai-warn);
  background: var(--ai-warn-soft);
}
.brain-input-hint {
  width: 100%;
  margin: 0;
  font-size: 11px;
  color: var(--ai-ink-4);
  text-align: center;
  font-weight: 400;
  font-family: var(--ai-font-mono);
}

/* ── 右侧上下文 ── */
.brain-history-panel {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  overflow: hidden;
}
.brain-text-link {
  border: none;
  background: transparent;
  color: var(--ai-ink-3);
  font-weight: 500;
  font-size: 12px;
  cursor: pointer;
}
.brain-text-link:hover {
  color: var(--ai-ink-1);
}
.brain-coverage-lane {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 32px;
  padding: 0 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  cursor: pointer;
  transition: background 0.15s ease;
}
.brain-coverage-lane:hover:not(:disabled) {
  background: var(--ai-surface-2);
}
.brain-coverage-lane:disabled {
  cursor: default;
}
.brain-coverage-lane span {
  font-size: 11.5px;
  font-weight: 500;
}
.brain-coverage-lane strong {
  font-size: 11.5px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.brain-coverage-lane.ready {
  border-color: transparent;
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
}
.brain-coverage-lane.ready strong { color: var(--ai-ok); }
.brain-coverage-lane.fallback {
  border-color: transparent;
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
}
.brain-coverage-lane.fallback strong { color: var(--ai-warn); }
.brain-coverage-lane.missing {
  border-color: transparent;
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}
.brain-coverage-lane.missing strong { color: var(--ai-bad); }
.brain-capability-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.brain-capability.muted {
  opacity: 0.72;
}
.brain-active-job-list {
  display: grid;
  gap: 6px;
}
.brain-active-job {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.brain-active-job:hover {
  border-color: var(--ai-border-2);
  background: var(--ai-surface-2);
}
.brain-active-job span {
  min-width: 0;
  overflow: hidden;
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.brain-active-job small {
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-weight: 400;
  font-family: var(--ai-font-mono);
}

/* ── 历史抽屉 ── */
.brain-history-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.brain-history-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--ai-border);
}
.brain-history-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--ai-ink-1);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.brain-history-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 400;
}
.brain-history-empty svg {
  width: 20px;
  height: 20px;
}
.brain-history-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}
.brain-history-item {
  position: relative;
  display: block;
  width: 100%;
  margin-bottom: 6px;
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.brain-history-item:hover {
  border-color: var(--ai-border-2);
  background: var(--ai-surface-2);
}
.brain-history-instance {
  padding: 1px 6px;
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-3);
  font-family: var(--ai-font-mono);
  font-size: 10.5px;
}
.brain-history-q {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.brain-history-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0 6px;
  font-size: 10.5px;
  color: var(--ai-ink-4);
  font-weight: 400;
  font-family: var(--ai-font-mono);
}
.brain-history-a {
  font-size: 11px;
  color: var(--ai-ink-3);
  line-height: 1.5;
  font-weight: 400;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.brain-history-delete {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 20px;
  height: 20px;
  border: none;
  background: transparent;
  color: var(--ai-ink-4);
  cursor: pointer;
  border-radius: var(--ai-radius-s);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.15s ease, background 0.15s ease, color 0.15s ease;
}
.brain-history-delete svg {
  width: 11px;
  height: 11px;
}
.brain-history-item:hover .brain-history-delete { opacity: 1; }
.brain-history-delete:hover { background: var(--ai-bad-soft); color: var(--ai-bad); }

/* 响应式 */
@media (max-width: 1180px) {
  /* 三列 → 两列：左 dept 列收纳进一个更窄的形态 */
  .brain-dept-rail { flex-basis: 180px; }
  .brain-agent-rail { flex-basis: 280px; }
  .brain-detail-grid { grid-template-columns: 1fr; }
  .brain-agent-edit-grid--five { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 960px) {
  .brain-body {
    flex-direction: column;
  }
  .brain-dept-rail {
    flex: 0 0 auto;
    width: 100%;
    border-right: 0;
    border-bottom: 1px solid var(--ai-border);
    max-height: 220px;
  }
  .brain-agent-rail {
    flex: 0 0 auto;
    width: 100%;
    border-right: 0;
    border-bottom: 1px solid var(--ai-border);
    max-height: 320px;
  }
  .brain-topbar {
    align-items: flex-start;
    flex-direction: column;
  }
  .brain-tools {
    width: 100%;
    overflow-x: auto;
    padding-bottom: 2px;
  }
  .brain-detail-head-row { flex-wrap: wrap; }
  .brain-detail-actions {
    width: 100%;
    flex-wrap: wrap;
    justify-content: flex-start;
  }
}
@media (max-width: 768px) {
  /* dept-rail 在移动端变成抽屉，其他两列保持现有响应式行为 */
  .brain-dept-rail {
    position: fixed;
    top: 0;
    left: 0;
    height: 100vh;
    width: min(280px, 80vw);
    max-height: none;
    z-index: 200;
    border-right: 1px solid var(--ai-border);
    border-bottom: 0;
    transform: translateX(-110%);
    transition: transform 0.2s ease;
    box-shadow: var(--ai-shadow-2);
  }
  .brain-dept-rail--open {
    transform: translateX(0);
  }
  .brain-dept-rail-head {
    display: flex;
  }
  .brain-dept-rail-close {
    display: inline-flex;
  }
  .brain-dept-rail-toggle {
    display: inline-flex;
  }
}
@media (max-width: 640px) {
  .brain-agent-edit-grid,
  .brain-agent-edit-grid--five {
    grid-template-columns: 1fr;
  }
  .brain-business-agent-row {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .brain-business-agent-actions {
    width: 100%;
    justify-content: flex-start;
  }
  .brain-business-prompt {
    white-space: normal;
  }
  .brain-chat-wrap.is-empty { padding: 16px 8px; }
  .brain-chat-input { padding: 12px 8px 16px; }
  .brain-hero-title { font-size: 26px; }
}
</style>
