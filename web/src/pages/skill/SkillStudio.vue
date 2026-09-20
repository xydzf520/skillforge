<template>
  <!-- B2: skill 加载失败（404/403/5xx/网络错）都走 SkillNotFound 而不是渲染空骨架 -->
  <SkillNotFound
    v-if="loadError === 'not-found'"
    :skill-id="notFoundSkillId"
  />
  <SkillNotFound
    v-else-if="loadError === 'load-error'"
    :skill-id="notFoundSkillId"
    title="加载 Skill 失败"
    :subtitle="loadErrorMessage || '可能是网络问题或服务端异常，请刷新重试；若持续失败请联系管理员'"
  />
  <WorkbenchShell
    v-else
    :navigator-open="blueprintGuideMode || rulesFocusMode ? false : ui.navigatorOpen"
    :navigator-width="ui.navigatorWidth"
    :assistant-open="studioDoc.isCreate.value || rulesFocusMode ? false : ui.assistantPaneOpen"
    :assistant-width="ui.assistantWidth"
    :bottom-open="blueprintGuideMode || rulesFocusMode ? false : ui.bottomPanelOpen"
    :bottom-height="ui.bottomPanelHeight"
    :bottom-maximized="ui.bottomMaximized"
    :fullscreen="ui.fullscreen"
    @update:bottom-height="h => ui.bottomPanelHeight = h"
    @update:navigator-width="w => ui.setNavigatorWidth(w)"
    @update:assistant-width="w => ui.setAssistantWidth(w)"
  >
    <!-- ═══ 顶栏 ═══ -->
    <template #topbar>
      <div v-if="rulesFocusMode" class="rule-focus-topbar">
        <button class="rule-focus-back" type="button" @click="handleGoModule('overview')">
          <SfShellIcon name="arrowl" />
        </button>
        <span class="rule-focus-crumb">Skills / {{ studioDoc.doc.value?.meta?.name || wb.skillId }} /</span>
        <span class="rule-focus-title">规则 · {{ ruleFocusCount }} 条</span>
        <span v-if="studioDirty" class="ai-pill warn">有未保存</span>
        <div class="rule-focus-spacer" />
        <button class="ai-btn sm" type="button" @click="handleGoModule('overview')">取消</button>
        <button class="ai-btn sm" type="button" @click="handlePreviewOutput">预览影响</button>
        <button
          v-if="!editMode"
          class="ai-btn primary sm"
          type="button"
          :disabled="!canEditPerspective"
          @click="acquireLock"
        >
          <SfShellIcon name="doc" />
          编辑
        </button>
        <button
          v-else
          class="ai-btn primary sm"
          type="button"
          :disabled="wb.studioSaving"
          @click="handleSave"
        >
          <SfShellIcon name="check" />
          保存为草稿 {{ ruleFocusVersion }}
        </button>
      </div>
      <WorkbenchTopBar
        v-else
        :title="studioDoc.isCreate.value ? '新建 Skill' : (studioDoc.doc.value?.meta?.name || wb.skillId)"
        :status="studioDoc.doc.value?.meta?.status"
        :risk-level="studioDoc.doc.value?.meta?.risk_level"
        :version-label="String(studioDoc.doc.value?.meta?.version || studioDoc.doc.value?.meta?.current_version || '')"
        :dirty="studioDirty"
        :saving="wb.studioSaving"
        :submitting-review="submittingReview"
        :is-create="studioDoc.isCreate.value"
        :has-content="studioDoc.hasContent.value"
        :edit-mode="editMode"
        :studio-mode="studioMode"
        :available-perspectives="availablePerspectives"
        :lock-held="lockHeld"
        :lock-owner="lockOwner"
        :view-mode="ui.viewMode"
        :navigator-open="ui.navigatorOpen"
        :assistant-open="ui.assistantPaneOpen"
        :bottom-open="ui.bottomPanelOpen"
        :guardian-items="guardianItems"
        :guardian-loading="guardianLoading"
        :health-score="healthScore"
        :health-loading="healthLoading"
        :training-candidate-loading="trainingCandidateLoading"
        @toggle-view="ui.setViewMode($event)"
        @change-perspective="applyStudioPerspective($event)"
        @validate="handleValidate"
        @save="handleSave"
        @create="handleCreate"
        @submit-review="handleSubmitReview"
        @publish="handlePublish"
        @deprecate="handleDeprecate"
        @run-aiclaw="handleCommand('run-aiclaw')"
        @open-deploy="openDeployModal"
        @open-portal-ui="openPersonalUi"
        @open-permissions="permissionsModalVisible = true"
        @toggle-navigator="ui.toggleNavigator()"
        @toggle-assistant="ui.toggleAssistant()"
        @toggle-bottom="ui.toggleBottomPanel()"
        @open-tab="tab => ui.toggleBottomPanel(tab)"
        @enter-edit="acquireLock"
        @cancel-edit="handleCancelEdit"
        @command-palette="ui.toggleCommandPalette()"
        @guardian-refresh="loadGuardianReport"
        @guardian-root-cause="runGuardianRootCause"
        @refresh-health="loadHealthScore"
        @generate-training-candidate="handleGenerateTrainingCandidate"
      />
    </template>

    <!-- ═══ 左侧导航 ═══ -->
    <template #navigator>
      <WorkbenchNavigator
        :modules="studioDoc.moduleList.value"
        :active-module="ui.studioActiveModule"
        :section="ui.navigatorSection"
        :commits="commitHistory"
        :loading-history="loadingHistory"
        :skill-id="wb.skillId"
        :file-list="fileList"
        :active-file="activeFile"
        :loading-files="loadingFiles"
        @select-module="handleSelectModule"
        @change-section="handleSectionChange"
        @open-bottom="tab => ui.toggleBottomPanel(tab)"
        @switch-to-code="ui.setViewMode('code')"
        @select-commit="handleSelectCommit"
        @rollback="handleRollback"
        @view-diff="handleSelectCommit"
        @select-file="handleSelectFile"
        @file-create="() => handleCreateFile(false)"
        @file-create-dir="() => handleCreateFile(true)"
        @file-rename="handleRenameFile"
        @file-delete="handleDeleteFile"
      >
        <template #references>
          <WorkbenchReferencePicker
            :references="wb.references || []"
            :model-value="wb.selectedReferenceIds || []"
            @update:modelValue="ids => wb.setSelectedReferenceIds(ids)"
          />
        </template>
      </WorkbenchNavigator>
    </template>

    <!-- ═══ 中央编辑区 + 可视化面板 ═══ -->
    <template #editor>
      <div style="display:flex;height:100%;overflow:hidden">
        <WorkbenchEditorSurface
          style="flex:1;min-width:0"
          :view-mode="ui.viewMode"
          :active-module="ui.studioActiveModule"
          :doc="studioDoc.doc.value"
          :is-create="studioDoc.isCreate.value"
          :has-content="studioDoc.hasContent.value"
          :chat-started="chatStarted"
          :loading="!wb.studioLoaded"
          :read-only="!editMode"
          :dirty-modules="documentStore.dirtyModules"
          :skill-id="wb.skillId"
          :file-content="activeFileContent"
          :file-read-only="activeFileReadOnly"
          :file-language="activeFileLanguage"
          :file-name="activeFile || 'SKILL.md'"
          :highlighted-path="testHighlightPath"
          :failed-nodes="testFailedNodes"
          :readiness="publishReadinessReport"
          :explain-pack="explainPack"
          :linting="linting"
          :pending-patch="pendingPatch"
          :applying-patch="applyingPatch"
          :editor-theme="ui.editorTheme"
          :editor-font-size="ui.editorFontSize"
          :studio-mode="studioMode"
          :health-score="healthScore"
          :guardian-items="guardianItems"
          :review-context="reviewContext"
          :static-check-override-detail="staticCheckOverrideDetail"
          :review-loading="reviewContextLoading"
          :review-acting="reviewActionLoading"
          :can-act-review="canActCurrentReview"
          :can-comment-review="canCommentCurrentReview"
          :error-count="validationErrorCount"
          :warning-count="validationWarningCount"
          :module-focus-mode="rulesFocusMode"
          @update-module="handleBlockUpdate"
          @open-canvas="handleOpenCanvas"
          @start-manual="studioDoc.setActiveModule('meta')"
          @start-chat="startChatMode"
          @apply-template="handleApplyTemplate"
          @blueprint-confirm="handleBlueprintConfirm"
          @apply-patch="handleApplyPendingPatch"
          @reject-patch="pendingPatch = null"
          @monaco-change="handleMonacoChange"
          @selection-change="sel => currentSelection = sel"
          @cursor-block-change="handleCursorBlockChange"
          @cmd-save="handleSave"
          @cmd-run-sandbox="() => ui.toggleBottomPanel('sandbox')"
          @cmd-validate-block="handleValidateCurrentBlock"
          @cmd-validate-all="handleValidate"
          @cmd-mermaid="toggleMermaid"
          @cmd-flow="toggleFlow"
          @cmd-discover-antipatterns="() => handleCommand('discover-antipatterns')"
          @cmd-suggest-branches="() => handleCommand('suggest-branches')"
          @cmd-generate-tests="() => handleCommand('generate-tests')"
          @cmd-drift-check="() => handleCommand('drift-check')"
          @cmd-submit-review="handleSubmitReview"
          @cmd-inline-ai="() => { ui.inlinePromptVisible = true }"
          @cmd-validate-antipattern="handleValidateAntipattern"
          @preview-output="handlePreviewOutput"
          @toggle-bottom-panel="() => ui.toggleBottomPanel()"
          @open-tab="tab => ui.toggleBottomPanel(tab)"
          @go-module="handleGoModule"
          @run-lint="runLintCheck"
          @ai-action="handleNodeAIAction"
          @open-review-detail="handleOpenReviewDetail"
          @approve-review="handleApproveCurrentReview"
          @reject-review="handleRejectCurrentReview"
          @comment-review="handleReviewComment"
          @resolve-review-comment="handleResolveReviewComment"
          @start-review="handleExplainToReview"
          @ask-ai-fix="handleAskAIFix"
          @edit-rule="handleEditRule"
        />
        <!-- v2.4-4: Mermaid + Flow 可视化面板由 StudioVizPanels 聚合 -->
        <StudioVizPanels
          v-model:show-mermaid="showMermaidPanel"
          v-model:show-flow="showFlowPanel"
          :skill-id="wb.skillId"
          :cursor-line-for-mermaid="cursorLineForMermaid"
          :doc="studioDoc.doc.value"
          @jump-to-line="handleMermaidJumpToLine"
        />
      </div>
    </template>

    <!-- ═══ 右侧 AI 面板（N6 W7c: 加 Chat / 依赖图 两个 tab，v2.5 下沉 StudioAssistantTabs） ═══ -->
    <template #assistant>
      <StudioAssistantTabs
        v-model:active-tab="assistantTab"
        :show-deps-tab="!!(wb.skillId && !studioDoc.isCreate.value)"
        :skill-id="wb.skillId"
      >
        <template #chat>
          <AgentChatPanel
            :messages="wb.messages"
            :loading="submitting"
            :active-module="ui.studioActiveModule"
            :selection="currentSelection"
            :references="wb.references || []"
            :selected-reference-ids="wb.selectedReferenceIds || []"
            :is-create="studioDoc.isCreate.value"
            :doc="studioDoc.doc.value"
            :conversations="conversations"
            :coach-suggestions="coachSuggestions"
            :budget-used="chatBudget.used"
            :budget-limit="chatBudget.limit"
            :budget-status="chatBudget.status"
            :tracking-enabled="trackingMode"
            :pending-permission="pendingPermissionRequest"
            :session-meta="codingSessionMeta"
            :studio-mode="studioMode"
            :active-file="activeFile"
            @send="handleSend"
            @stop="stopGeneration"
            @close="ui.toggleAssistant()"
            @update-references="ids => wb.setSelectedReferenceIds(ids)"
            @new-conversation="handleNewConversation"
            @switch-conversation="handleSwitchConversation"
            @upload-report="handleUploadReport"
            @coach-action="handleCoachAction"
            @coach-dismiss="handleCoachDismiss"
            @toggle-tracking="trackingMode = !trackingMode"
            @respond-permission="opts => respondCodingPermission(opts.behavior, { updatedInput: opts.updatedInput, message: opts.message })"
            @jump-to-file="handleJumpToFile"
          />
        </template>
      </StudioAssistantTabs>
    </template>

    <!-- ═══ 底部面板 ═══ -->
    <template #bottom>
      <WorkbenchBottomPanel
        :active-tab="ui.bottomPanelTab"
        :skill-id="wb.skillId"
        :validation="validationResult"
        :test-result="testResult"
        :test-running="testRunning"
        :sandbox-result="sandboxResult"
        :sandbox-running="sandboxRunning"
        :commits="commitHistory"
        :shadow-report="shadowReport"
        :shadow-divergence-rate="shadowDivergenceRate"
        :diff-content="commitDiffContent"
        :maximized="ui.bottomMaximized"
        @change-tab="handleBottomTabChange"
        @toggle-maximized="ui.toggleBottomMaximized()"
        @close="ui.bottomPanelOpen = false"
        @run-test="handleRunTest"
        @run-sandbox="handleRunSandbox"
        @start-shadow="handleStartShadow"
        @stop-shadow="handleStopShadow"
        @promote-shadow="handlePromoteShadow"
        :shadow-comparisons="shadowComparisons"
        @rollback="handleRollback"
        @load-comparisons="loadShadowComparisons"
        @record-human-decision="handleRecordHumanDecision"
        @highlight-test="handleHighlightTest"
      />
    </template>

    <!-- ═══ 浮层 ═══ -->
    <template #overlay>
      <WorkbenchCommandPalette
        v-if="ui.commandPaletteOpen"
        @close="ui.commandPaletteOpen = false"
        @execute="handleCommand"
      />
      <DiffPreviewModal
        :visible="showDiffPreview"
        :original-content="diffData.original"
        :modified-content="diffData.modified"
        language="markdown"
        file-name="SKILL.md"
        @update:visible="v => showDiffPreview = v"
        @confirm="executeSave"
        @cancel="showDiffPreview = false"
      />
      <RegressionDiffModal
        :visible="showRegressionDiff"
        :diff="regressionDiffData"
        @update:visible="v => showRegressionDiff = v"
      />
      <WorkbenchInlinePrompt
        v-if="ui.inlinePromptVisible"
        :selected-text="currentSelection?.text || ''"
        :position="ui.inlinePromptPosition"
        :module-id="ui.studioActiveModule"
        @submit="handleInlinePrompt"
        @cancel="ui.inlinePromptVisible = false"
      />
      <!-- v2.4-4: 3 个查看型弹窗（output preview / drift / antipattern）聚合到 StudioModalsCluster -->
      <StudioModalsCluster
        v-model:output-preview-visible="outputPreviewVisible"
        v-model:drift-modal-visible="driftModalVisible"
        v-model:antipattern-modal-visible="antipatternModalVisible"
        :output-preview-loading="outputPreviewLoading"
        :output-preview-data="outputPreviewData"
        :drift-loading="driftLoading"
        :drift-results="driftResults"
        :antipattern-loading="antipatternLoading"
        :antipattern-results="antipatternResults"
      />
      <!-- v2.10.0 V210-3：批量运行投选 -->
      <MultiRunVotingModal
        v-model:visible="multiRunModalVisible"
        :skill-id="wb.skillId || ''"
        :params="{}"
      />
      <SkillPermissionsModal
        v-model:visible="permissionsModalVisible"
        :skill-id="wb.skillId || ''"
      />
      <a-modal
        v-model:visible="deployModalVisible"
        title="运行入口"
        :width="'min(90vw, 760px)'"
        :footer="false"
        :unmount-on-close="true"
      >
        <div class="deploy-modal">
          <a-alert type="info" show-icon>
            审核通过会自动同步到对应部门的 Agent 终端。这里用于查看目标终端、手动补发或排查同步状态；终端执行后的 reports / todos 会回写到收件里的报告和待办。
          </a-alert>

          <a-spin :loading="deployLoading" style="width:100%">
            <div class="deploy-toolbar">
              <div>
                <div class="deploy-title">同步目标终端</div>
                <div class="deploy-desc">
                  审核通过时默认按 Skill 部门匹配；管理员可以手动选择任意在线终端补发。
                </div>
              </div>
              <a-space>
                <a-button size="small" @click="loadDeployTargets">刷新</a-button>
                <a-button
                  v-if="userStore.isAdmin"
                  size="small"
                  @click="router.push('/admin/agent-devices')"
                >
                  Agent终端
                </a-button>
              </a-space>
            </div>

            <a-empty v-if="!deployTargets.length" description="暂无可下发终端" />
            <a-checkbox-group v-else v-model="selectedDeployTargetIds" class="deploy-targets">
              <div
                v-for="target in deployTargets"
                :key="target.id"
                class="deploy-target"
                :class="{ selected: selectedDeployTargetIds.includes(target.id), disabled: !target.selectable }"
              >
                <a-checkbox :value="target.id" :disabled="!target.selectable" />
                <div class="deploy-target-main">
                  <div class="deploy-target-head">
                    <span class="deploy-target-name">{{ target.name || target.id }}</span>
                    <a-tag size="small" :color="target.bridge_online ? 'green' : 'gray'">
                      {{ target.bridge_online ? '在线' : '离线' }}
                    </a-tag>
                    <a-tag v-if="target.fallback_target" size="small" color="purple">平台兜底</a-tag>
                    <a-tag v-else-if="target.auto_target" size="small" color="arcoblue">部门默认</a-tag>
                    <a-tag v-if="target.is_platform_default" size="small" color="arcoblue">平台默认</a-tag>
                    <a-tag size="small" :color="agentPurposeColor(target.agent_purpose)">
                      {{ agentPurposeLabel(target.agent_purpose) }}
                    </a-tag>
                    <a-tag v-if="!target.selectable" size="small" color="orange">无权限</a-tag>
                  </div>
                  <div class="deploy-target-meta">
                    {{ target.id }} · {{ target.department || '未指定部门' }} · {{ target.agent_type || 'AIClaw' }} · {{ agentPurposeLabel(target.agent_purpose) }}
                    <span v-if="target.last_sync_at"> · 上次下发 {{ formatDeployTime(target.last_sync_at) }}</span>
                  </div>
                </div>
                <a-button size="mini" type="text" @click.stop="router.push(`/aiclaw/instances/${encodeURIComponent(target.id)}`)">
                  打开
                </a-button>
              </div>
            </a-checkbox-group>

            <a-alert v-if="platformFallbackTargets.length" type="info" show-icon>
              当前 Skill 部门没有匹配终端，审核通过时会自动使用平台默认终端兜底。
            </a-alert>
            <a-alert v-else-if="deployTargets.length && !autoDeployTargets.length" type="warning" show-icon>
              当前 Skill 部门没有匹配的在线终端。请联系管理员补充部门归属或手动选择终端下发。
            </a-alert>
          </a-spin>

          <div class="deploy-actions">
            <a-space>
              <a-button @click="deployModalVisible = false">关闭</a-button>
              <a-button :loading="deploySyncing" :disabled="deployLoading" @click="syncSkillToDepartment">
                按部门补发
              </a-button>
              <a-button
                type="primary"
                :loading="deploySyncing"
                :disabled="deployLoading || selectedDeployTargetIds.length === 0"
                @click="syncSkillToSelectedTargets"
              >
                补发到选中终端
              </a-button>
            </a-space>
          </div>
        </div>
      </a-modal>

      <!-- L3-C: 规则编辑抽屉 — 编辑单条规则（step + branch） -->
      <a-drawer
        v-model:visible="ruleDrawerOpen"
        :width="640"
        placement="right"
        :mask-closable="true"
        :unmount-on-close="true"
        :footer="false"
        :header="false"
        :body-style="{ padding: '0', background: 'var(--ai-bg)' }"
      >
        <div v-if="ruleDrawerData" class="rule-drawer">
          <div class="rule-drawer-head">
            <div class="rule-drawer-head-main">
              <div class="rule-drawer-title">规则编辑</div>
              <div class="rule-drawer-sub">
                <span class="rule-drawer-mono">{{ ruleDrawerData.nodeId }}</span>
                <span class="rule-drawer-name">{{ ruleDrawerData.nodeName || '—' }}</span>
              </div>
            </div>
            <button class="rule-drawer-close" @click="closeRuleDrawer" aria-label="关闭">
              <SfShellIcon name="x" />
            </button>
          </div>

          <div class="rule-drawer-body">
            <!-- 条件 -->
            <div class="rule-section">
              <div class="rule-section-label">条件 · WHEN</div>
              <a-textarea
                v-model="ruleDrawerForm.condition"
                placeholder="例：下滑系数 >= 0.7 且 7 日访客 < 上周 -20%"
                :auto-size="{ minRows: 3, maxRows: 8 }"
                class="rule-textarea mono"
                @input="markRuleDirty"
              />
            </div>

            <!-- 结论 -->
            <div class="rule-section">
              <div class="rule-section-label">结论 · 判定</div>
              <div class="rule-verdicts">
                <button
                  v-for="opt in ruleVerdictOptions"
                  :key="opt.value"
                  type="button"
                  class="rule-verdict"
                  :class="[`rule-verdict--${opt.kind}`, { active: ruleDrawerForm.conclusion === opt.value }]"
                  @click="setRuleVerdict(opt.value)"
                >
                  {{ opt.label }}
                </button>
              </div>
              <a-input
                v-model="ruleDrawerForm.conclusion"
                placeholder="或输入自定义结论"
                size="small"
                class="rule-input"
                @input="markRuleDirty"
              />
            </div>

            <!-- 建议动作 -->
            <div class="rule-section">
              <div class="rule-section-label">建议动作 · THEN</div>
              <a-textarea
                v-model="ruleDrawerForm.action"
                placeholder="例：派发主理人 · 生成诊断卡"
                :auto-size="{ minRows: 3, maxRows: 6 }"
                class="rule-textarea"
                @input="markRuleDirty"
              />
            </div>

            <!-- 下一步 -->
            <div class="rule-section">
              <div class="rule-section-label">下一步</div>
              <a-select
                v-model="ruleDrawerForm.nextStep"
                placeholder="选择下一个步骤或留空结束"
                allow-clear
                size="small"
                class="rule-select"
                @change="markRuleDirty"
              >
                <a-option
                  v-for="opt in ruleNextStepOptions"
                  :key="opt.value"
                  :value="opt.value"
                  :label="opt.label"
                />
              </a-select>
            </div>

            <!-- 校验 -->
            <div class="rule-section">
              <div class="rule-section-label">校验</div>
              <div class="rule-validate-row">
                <a-button size="small" :loading="ruleValidateRunning" @click="runRuleValidation">
                  校验规则
                </a-button>
                <span v-if="ruleValidateResult" class="rule-validate-result" :class="`rule-validate-result--${ruleValidateResult.level}`">
                  <span class="rule-validate-dot" />
                  {{ ruleValidateResult.message }}
                </span>
              </div>
            </div>
          </div>

          <div class="rule-drawer-actions">
            <a-button class="rule-btn" @click="closeRuleDrawer">关闭</a-button>
            <a-button class="rule-btn primary" type="primary" :disabled="!ruleDrawerDirty" @click="saveRuleDrawer">
              保存
            </a-button>
          </div>
        </div>
      </a-drawer>
    </template>
  </WorkbenchShell>

  <!-- N6 v2.2.0 W7c: 原 FAB+Drawer 已合并到 #assistant 的"依赖图" tab，删除浮动按钮 -->

</template>

<script setup lang="ts">
/**
 * SkillStudio — Cursor 式统一工作台
 *
 * 融合创建/编辑/对话/测试/验证/发布到一个页面。
 * 复用现有 ide store（文档数据）+ workbench store（session/patch）+ ui store（面板状态）。
 */
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message, Modal } from '@arco-design/web-vue'
import { workbenchApi as rawWorkbenchApi, skillApi as rawSkillApi, testApi as rawTestApi, executionApi as rawExecutionApi, reviewApi as rawReviewApi, aiclawApi as rawAiclawApi, trainingApi as rawTrainingApi } from '@/api'
// v2.8.4 下游迁移：store 入口统一走 useSkillStudio() facade；子 store 通过 `.session / .ui / .content` 访问
import { useSkillStudio } from '@/stores/skillStudio'
import { useUserStore } from '@/stores/user'
import { useRunningTasksStore } from '@/stores/runningTasks'
import { useSkillStudioDocument } from '@/composables/skillstudio/useSkillStudioDocument'
import { useSkillStudioSession } from '@/composables/skillstudio/useSkillStudioSession'
import { useSkillStudioFiles } from '@/composables/skillstudio/useSkillStudioFiles'
import { useSkillStudioReview } from '@/composables/skillstudio/useSkillStudioReview'
import { useSkillStudioAI } from '@/composables/skillstudio/useSkillStudioAI'
// v2.9.0 B3：22 composable import → 10，走 3 个 aggregate barrel
// v2.9.1：Publishing 三件套 aggregate（useStudioShadow/Publish/ReviewSubmit 已通过 useSkillPublishing 调用）
import { useSkillPublishing } from '@/composables/useSkillPublishing'
// v2.10.0 V210-1：Observability 聚合（Validation + Telemetry + CommitHistory 三调 → 一调）
import { useStudioObservability } from '@/composables/useStudioObservability'
import {
  useStudioLifecycleUnified,
  useSkillStudioLifecycle,
} from '@/composables/useStudioLifecycleUnified'
import type {
  ChatOptions,
  SkillStudioExecutionApi,
  SkillStudioReviewApi,
  SkillStudioDraftSnapshot,
  SkillStudioSkillApi,
  SkillStudioTestApi,
  SkillStudioSelectionRange,
  SkillStudioWorkbenchApi,
  WorkbenchPatchDraft,
} from '@/types/skillstudio'
import { getErrorMessage, isSkillDocument } from '@/types/skillstudio'

import WorkbenchShell from '@/components/workbench/WorkbenchShell.vue'
import WorkbenchTopBar from '@/components/workbench/WorkbenchTopBar.vue'
import WorkbenchNavigator from '@/components/workbench/WorkbenchNavigator.vue'
import WorkbenchEditorSurface from '@/components/workbench/WorkbenchEditorSurface.vue'
import AgentChatPanel from '@/components/chat/AgentChatPanel.vue'
import WorkbenchBottomPanel from '@/components/workbench/WorkbenchBottomPanel.vue'
import WorkbenchReferencePicker from '@/components/workbench/WorkbenchReferencePicker.vue'
import WorkbenchCommandPalette from '@/components/workbench/WorkbenchCommandPalette.vue'
import WorkbenchInlinePrompt from '@/components/workbench/WorkbenchInlinePrompt.vue'
import DiffPreviewModal from '@/components/editor/DiffPreviewModal.vue'
import RegressionDiffModal from '@/components/workbench/RegressionDiffModal.vue'
import SkillNotFound from '@/components/ide/SkillNotFound.vue'
import StudioAssistantTabs from '@/components/studio/StudioAssistantTabs.vue'
import StudioModalsCluster from '@/components/studio/StudioModalsCluster.vue'
import MultiRunVotingModal from '@/components/studio/MultiRunVotingModal.vue'
import SkillPermissionsModal from '@/components/studio/SkillPermissionsModal.vue'
import StudioVizPanels from '@/components/studio/StudioVizPanels.vue'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
// v2.9.1：4 个杂项小 composable 走 useStudioMisc barrel
import {
  useSkillStudioRecent,
  useStudioDraft,
  useStudioBlocks,
  useStudioViewHandlers,
} from '@/composables/useStudioMisc'
import { useStudioKeyboard } from '@/composables/useStudioKeyboard'
// v2.11.0 V211-2：3 个 handler 函数聚合
import { useStudioHandlers } from '@/composables/useStudioHandlers'
import { useStudioVisualization } from '@/composables/useStudioVisualization'
import { useStudioExplain } from '@/composables/useStudioExplain'
import { useStudioCommand } from '@/composables/useStudioCommand'
import { formatTime } from '@/utils/format'

const workbenchApi = rawWorkbenchApi as unknown as SkillStudioWorkbenchApi
const skillApi = rawSkillApi as unknown as SkillStudioSkillApi
const testApi = rawTestApi as unknown as SkillStudioTestApi
const executionApi = rawExecutionApi as unknown as SkillStudioExecutionApi
const reviewApi = rawReviewApi as unknown as SkillStudioReviewApi
const aiclawApi = rawAiclawApi as unknown as {
  listSyncTargets: (skillId: string) => Promise<{ items?: DeployTarget[] }>
  syncSkillToTargets: (skillId: string, data?: Record<string, unknown>) => Promise<DeploySyncResult>
}
const trainingApi = rawTrainingApi as unknown as {
  createSkillCandidate: (skillId: string, data?: Record<string, unknown>) => Promise<{ id?: string; status?: string }>
}

type DeployTarget = {
  id: string
  name?: string
  department?: string
  agent_type?: string
  agent_purpose?: string
  bridge_online?: boolean
  auto_target?: boolean
  fallback_target?: boolean
  is_platform_default?: boolean
  target_scope?: string | null
  selectable?: boolean
  last_sync_at?: string | null
}

function agentPurposeValue(value: any): string {
  const purpose = String(value || 'skill_runtime').toLowerCase()
  return ['skill_runtime', 'analysis', 'training', 'media', 'mixed'].includes(purpose) ? purpose : 'skill_runtime'
}

function agentPurposeLabel(value: any): string {
  const labels: Record<string, string> = {
    skill_runtime: '部门执行',
    analysis: '分析',
    training: '训练',
    media: '媒体生成',
    mixed: '混合',
  }
  return labels[agentPurposeValue(value)] || '部门执行'
}

function agentPurposeColor(value: any): string {
  const purpose = agentPurposeValue(value)
  if (purpose === 'analysis') return 'arcoblue'
  if (purpose === 'training') return 'green'
  if (purpose === 'mixed') return 'orange'
  return 'gray'
}

type DeploySyncResult = {
  ok?: boolean
  target_count?: number
  files_pushed?: number
  results?: Array<Record<string, unknown>>
}

const route = useRoute()
const router = useRouter()

const loadError = ref<null | 'not-found' | 'load-error'>(null)
const loadErrorMessage = ref<string>('')
const notFoundSkillId = ref<string>('')
// v2.10.0 V210-3：批量运行投选
const multiRunModalVisible = ref(false)
const permissionsModalVisible = ref(false)
const deployModalVisible = ref(false)
const deployLoading = ref(false)
const deploySyncing = ref(false)
const trainingCandidateLoading = ref(false)
const deployTargets = ref<DeployTarget[]>([])
const selectedDeployTargetIds = ref<string[]>([])

// L3-C: 规则编辑抽屉
type RuleDrawerContext = {
  nodeId: string
  nodeName: string
  branchIndex: number
}
type RuleDrawerForm = {
  condition: string
  conclusion: string
  action: string
  nextStep: string | null
}
type RuleVerdictKind = 'ok' | 'warn' | 'bad' | 'info'
const ruleDrawerOpen = ref(false)
const ruleDrawerData = ref<RuleDrawerContext | null>(null)
const ruleDrawerForm = ref<RuleDrawerForm>({ condition: '', conclusion: '', action: '', nextStep: null })
const ruleDrawerDirty = ref(false)
const ruleValidateRunning = ref(false)
const ruleValidateResult = ref<{ level: 'success' | 'warning' | 'error'; message: string } | null>(null)
const ruleVerdictOptions: Array<{ value: string; label: string; kind: RuleVerdictKind }> = [
  { value: '绿灯', label: '绿灯 · 通过', kind: 'ok' },
  { value: '黄灯', label: '黄灯 · 关注', kind: 'warn' },
  { value: '红灯', label: '红灯 · 阻断', kind: 'bad' },
]

// v2.8.4 下游迁移：单一入口 useSkillStudio() 取 3 个子 store
const studio = useSkillStudio()
const wb = studio.session
const ui = studio.ui
const documentStore = studio.content
const userStore = useUserStore()
const runningTasks = useRunningTasksStore()
const autoDeployTargets = computed(() =>
  deployTargets.value.filter(target => target.auto_target && target.selectable && target.bridge_online),
)
const platformFallbackTargets = computed(() =>
  deployTargets.value.filter(target => target.fallback_target && target.selectable),
)

function formatDeployTime(value?: string | null) {
  if (!value) return ''
  const formatted = formatTime(value)
  return formatted === '-' ? value : formatted
}

async function loadDeployTargets() {
  if (!wb.skillId) return
  deployLoading.value = true
  try {
    const res = await aiclawApi.listSyncTargets(wb.skillId)
    const items = Array.isArray(res?.items) ? res.items : []
    deployTargets.value = items
    const defaults = items
      .filter(target => target.auto_target && target.selectable && target.bridge_online)
      .map(target => target.id)
    selectedDeployTargetIds.value = defaults.length
      ? defaults
      : selectedDeployTargetIds.value.filter(id => items.some(target => target.id === id && target.selectable))
  } catch (e) {
    deployTargets.value = []
    Message.error(getErrorMessage(e, '加载运行终端失败'))
  } finally {
    deployLoading.value = false
  }
}

async function openDeployModal() {
  deployModalVisible.value = true
  await loadDeployTargets()
}

function openPersonalUi() {
  if (!wb.skillId) {
    Message.warning('当前 Skill 尚未保存，无法调整个人界面')
    return
  }
  router.push({
    name: 'PortalSkillDetail',
    params: { id: wb.skillId },
    query: { focus: 'ui' },
  })
}

async function handleGenerateTrainingCandidate() {
  if (!wb.skillId || trainingCandidateLoading.value) return
  trainingCandidateLoading.value = true
  try {
    const result = await trainingApi.createSkillCandidate(wb.skillId, {})
    Message.success(result?.id ? `已生成训练候选 ${result.id}` : '已生成训练候选')
    router.push('/training')
  } catch (e) {
    Message.error(getErrorMessage(e, '训练候选生成失败'))
  } finally {
    trainingCandidateLoading.value = false
  }
}

function summarizeDeployResult(res: DeploySyncResult) {
  const results = Array.isArray(res?.results) ? res.results : []
  const targetCount = Number(res?.target_count ?? results.length ?? 0)
  const failed = results.filter(item => item?.ok === false).length
  const filesPushed = Number(res?.files_pushed ?? results.reduce((sum, item) => {
    const direct = Number(item?.files_pushed ?? 0)
    const nested = Number((item?.result as Record<string, unknown> | undefined)?.files_pushed ?? 0)
    return sum + Math.max(direct, nested, 0)
  }, 0))
  return { targetCount, failed, filesPushed }
}

async function syncSkill(payload: Record<string, unknown>) {
  if (!wb.skillId) return
  deploySyncing.value = true
  try {
    const res = await aiclawApi.syncSkillToTargets(wb.skillId, payload)
    const { targetCount, failed, filesPushed } = summarizeDeployResult(res)
    if (!targetCount) {
      Message.warning('没有匹配到可下发终端')
    } else if (failed) {
      Message.warning(`已尝试下发 ${targetCount} 台，其中 ${failed} 台失败`)
    } else {
      Message.success(`已下发到 ${targetCount} 台终端，推送 ${filesPushed || 0} 个文件`)
    }
    wb.appendMessage?.(
      'assistant',
      `Skill 下发完成：目标 ${targetCount} 台，失败 ${failed} 台，文件 ${filesPushed || 0} 个。`,
    )
    await loadDeployTargets()
  } catch (e) {
    Message.error(getErrorMessage(e, '下发 Skill 失败'))
  } finally {
    deploySyncing.value = false
  }
}

async function syncSkillToDepartment() {
  await syncSkill({})
}

async function syncSkillToSelectedTargets() {
  if (!selectedDeployTargetIds.value.length) {
    Message.warning('请选择至少一个终端')
    return
  }
  await syncSkill({ instance_ids: selectedDeployTargetIds.value })
}

// v2.6: ide facade 完全消除 —— 所有 composable 接收细分依赖（wb + studioDoc + ui + documentStore），
// Studio template/script 直接访问各自 store/composable 属性（doc/isCreate 等通过 studioDoc.xxx.value 读取 ComputedRef）。
const studioDoc = useSkillStudioDocument({
  wb,
  ui,
  documentStore,
  skillApi,
})
// v2.6: 解构常用 computed ref 供 template 用（Vue auto-unwrap top-level ref）
const { doc, isCreate, hasContent, moduleList } = studioDoc

// O13 第一步：从 SkillStudio.vue 拆出的 composable（抽最小自包含单元）
const studioRecent = useSkillStudioRecent({ userStore })

// N6 W7c: 助手面板 tab 切换（chat / deps）
const assistantTab = ref<'chat' | 'deps'>('chat')

// ── 本地状态 ──
const pendingPatch = ref<WorkbenchPatchDraft | null>(null)
const currentSelection = ref<SkillStudioSelectionRange | null>(null)
// v2.5: outputPreview / applyingPatch / handlePreviewOutput / handleValidateAntipattern /
//       handleNodeAIAction / handleApplyPendingPatch 已迁到 useStudioContentActions
// v2.10.0 V210-1：Observability aggregate 一次 call（Validation + Telemetry + CommitHistory）
// 跨循环依赖（handleCommand / handleSend / runLintCheck / loadFileList 下方才定义）用 forward closure 绕开
const observability = useStudioObservability({
  wb,
  studioDoc,
  ui,
  workbenchApi,
  skillApi,
  testApi,
  executionApi,
  runningTasks,
  loadFileList: () => loadFileList(),
  handleCommand: (command: string) => handleCommand(command),
  handleSend: (prompt: string, options?: ChatOptions) => handleSend(prompt, options),
  runLintCheck: () => runLintCheck(),
})
const {
  validationResult,
  testResult,
  sandboxResult,
  testRunning,
  sandboxRunning,
  testHighlightPath,
  testFailedNodes,
  handleValidate,
  handleRunTest,
  handleRunSandbox,
  restoreCachedTaskResults,
  handleHighlightTest,
} = observability.validation
// v2.9.1 M1：useSkillPublishing 聚合（shadow + publish + review）
// 实际调用下移到 refreshAfterSubmitReview 可用之后（见下方 useSkillPublishing 块），
// 这里声明占位变量供本段之后的模板/handler 引用。但模板是在 setup 完成后才访问的，
// 所以只要 setup 结束前定义完就行 —— 为简单起见，publishReadinessReport/shadow 等的
// 所有使用都在 setup 尾部的 useSkillPublishing 之后。
// W4-F: 可视化面板（Mermaid/Flow）由 useStudioVisualization composable 托管
const {
  showMermaidPanel,
  showFlowPanel,
  toggleMermaid,
  toggleFlow,
  handleMermaidJumpToLine,
  cursorLineForMermaid,
} = useStudioVisualization({ studioDoc, ui })
// v2.3.3: explain pack + AI fix 由 useStudioExplain 托管
const {
  explainPack,
  explainPackLoading,
  loadExplainPack,
  handleExplainToReview,
  handleAskAIFix,
} = useStudioExplain({
  wb,
  studioDoc,
  ui,
  skillApi,
  applyStudioPerspective: (name: string) => applyStudioPerspective(name),
  submitReview: () => handleSubmitReview(),
  sendChat: (prompt: string, opts?: Record<string, unknown>) => handleSend(prompt, opts),
})
// v2.5: 命令面板 + drift / antipattern 两个 modal 迁到 useStudioCommand
const {
  driftModalVisible,
  driftResults,
  driftLoading,
  antipatternModalVisible,
  antipatternResults,
  antipatternLoading,
  handleCommand,
} = useStudioCommand({
  wb,
  studioDoc,
  ui,
  skillApi,
  handleValidate: () => handleValidate(),
  handleRunTest: () => handleRunTest(),
  openMultiRun: () => {
    multiRunModalVisible.value = true
  },
  toggleMermaid: () => toggleMermaid(),
  toggleFlow: () => toggleFlow(),
})

const studioDirty = computed(() => documentStore.dirty || activeFileDirty.value)

const {
  chatStarted,
  lockHeld,
  editMode,
  lockOwner,
  blueprintGuideMode,
  canEditPerspective,
  availablePerspectives,
  studioMode,
  isWritablePerspective,
  applyStudioPerspective,
  checkLockStatus,
  acquireLock,
  releaseLock,
  handleCancelEdit,
} = useSkillStudioSession({
  route,
  router,
  wb,
  studioDoc,
  documentStore,
  ui,
  userStore,
  skillApi,
})

const {
  fileList,
  loadingFiles,
  activeFile,
  activeFileContent,
  activeFileOriginalContent,
  activeFileReadOnly,
  activeFileDirty,
  activeFileLanguage,
  loadFileList,
  resetFileEditor,
  handleSelectModule,
  handleSelectFile,
  handleMonacoChange,
  handleCreateFile,
  handleRenameFile,
  handleDeleteFile,
} = useSkillStudioFiles({
  wb,
  studioDoc,
  ui,
  skillApi,
})

const rulesFocusMode = computed(() => (
  ui.viewMode === 'block'
  && ui.studioActiveModule === 'rules'
  && studioMode.value === 'edit'
  && !studioDoc.isCreate.value
))
const ruleFocusCount = computed(() => {
  const rules = (studioDoc.doc.value as any)?.rules
  return Array.isArray(rules) ? rules.length : 0
})
const ruleFocusVersion = computed(() => {
  const meta = (studioDoc.doc.value as any)?.meta || {}
  const raw = meta.version || meta.current_version || ''
  return raw ? `v${String(raw).replace(/^v/i, '')}` : '草稿'
})

// v2.10.0 V210-1：CommitHistory 来自 observability aggregate
const {
  commitHistory,
  loadingHistory,
  commitDiffContent,
  loadHistory,
  handleSelectCommit,
  handleRollback,
} = observability.history

const {
  reviewContext,
  reviewContextLoading,
  reviewActionLoading,
  staticCheckOverrideDetail,
  canActCurrentReview,
  canCommentCurrentReview,
  handleOpenReviewDetail,
  handleApproveCurrentReview,
  handleRejectCurrentReview,
  handleReviewComment,
  handleResolveReviewComment,
  refreshAfterSubmitReview,
} = useSkillStudioReview({
  route,
  router,
  wb,
  studioDoc,
  reviewApi,
  skillApi,
  userStore,
  studioMode,
  loadHistory,
})

// v2.9.1 M1：发布三件套（shadow + publish + review）一锅端
const publishing = useSkillPublishing({
  wb,
  studioDoc,
  ui,
  router,
  skillApi,
  reviewApi,
  validationResult,
  refreshAfterSubmitReview,
})
// 反解回原子命名（兼容 template 与下游 handler 里沿用的变量名）
const {
  publishReadinessReport,
  linting,
  runLintCheck,
  handlePublish,
  handleDeprecate,
} = publishing.publish
const { submittingReview, handleSubmitReview } = publishing.review
const {
  shadowReport,
  shadowComparisons,
  loadShadowReport,
  loadShadowComparisons,
  handleRecordHumanDecision,
  handleStartShadow,
  handleStopShadow,
  handlePromoteShadow,
} = publishing.shadow

// v2.10.0 V210-1：Telemetry 来自 observability aggregate
const {
  coachSuggestions,
  guardianItems,
  guardianLoading,
  healthScore,
  healthLoading,
  shadowDivergenceRate,
  initializeSkillTelemetry,
  loadHealthScore,
  loadShadowDivergenceRate,
  triggerCoachEvent,
  trackAndCheckRevert,
  handleCoachAction,
  handleCoachDismiss,
  loadGuardianReport,
  runGuardianRootCause,
  recordPostSaveTelemetry,
} = observability.telemetry

// v2.9.1 M1：Save + ContentActions 聚合为 useStudioLifecycleUnified —— 实际调用在 AI 之后（见下）
// 这里的注释块占位，原子 Save/ContentActions 在下方一起 destructure

const {
  submitting,
  conversations,
  chatBudget,
  pendingPermissionRequest,
  codingSessionMeta,
  trackingMode,
  handleUploadReport,
  startChatMode,
  handleNewConversation,
  handleSwitchConversation,
  handleSend,
  stopGeneration,
  respondCodingPermission,
  handleJumpToFile,
  autoResumeChatIfPending,
} = useSkillStudioAI({
  wb,
  studioDoc,
  ui,
  skillApi,
  testApi,
  workbenchApi,
  chatStarted,
  studioMode,
  pendingPatch,
  loadFileList,
  handleSelectFile,
  loadHistory,
  loadHealthScore: () => loadHealthScore(),
})

// v2.9.1 M1：Save + ContentActions 一锅端
const lifecycle = useStudioLifecycleUnified({
  wb,
  studioDoc,
  documentStore,
  ui,
  router,
  skillApi,
  workbenchApi,
  activeFile,
  activeFileContent,
  activeFileDirty,
  activeFileOriginalContent,
  pendingPatch,
  loadFileList,
  isWritablePerspective,
  studioMode,
  triggerCoachEvent,
  recordPostSaveTelemetry,
  sendChat: (prompt: string, options?: ChatOptions) => handleSend(prompt, options),
})
const {
  diffData,
  showDiffPreview,
  regressionDiffData,
  showRegressionDiff,
  handleSave,
  executeSave,
  fetchRegressionDiff,
  handleCreate,
} = lifecycle.save
const {
  outputPreviewVisible,
  outputPreviewData,
  outputPreviewLoading,
  applyingPatch,
  handlePreviewOutput,
  handleValidateAntipattern,
  handleNodeAIAction,
  handleApplyPendingPatch,
} = lifecycle.content

// v2.6+: 6 个 view handler 合并抽到 composable（Studio 最后降 ~50 行）
const {
  handleBlueprintConfirm,
  handleApplyTemplate,
  handleGoModule,
  handleOpenCanvas,
  handleInlinePrompt,
  loadReferences,
} = useStudioViewHandlers({
  wb,
  studioDoc,
  ui,
  router,
  skillApi,
  workbenchApi,
  currentSelection,
  sendChat: (prompt: string, options?: ChatOptions) => handleSend(prompt, options),
})

useSkillStudioLifecycle({
  route,
  wb,
  studioDoc,
  ui,
  chatStarted,
  editMode,
  canEditPerspective,
  loadError,
  loadErrorMessage,
  notFoundSkillId,
  applyStudioPerspective,
  initializeSkillTelemetry,
  loadFileList,
  loadHistory,
  triggerCoachEvent,
  loadGuardianReport,
  loadHealthScore,
  checkLockStatus,
  releaseLock,
  restoreCachedTaskResults,
  autoResumeChatIfPending,
  restoreDraft: () => restoreDraft(),
  loadReferences: () => loadReferences(),
  studioRecent,
})

watch(
  () => [studioMode.value, wb.skillId, route.query.review_id],
  ([mode, skillId]) => {
    if (mode === 'explain' && skillId && !studioDoc.isCreate.value) {
      loadExplainPack()
    }
  },
)

const {
  save: saveDraft,
  restore: restoreDraft,
  clear: clearDraft,
} = useStudioDraft({
  wb,
  userStore,
  isDirty: () => studioDirty.value && !!studioDoc.doc.value,
  dirtyRef: studioDirty,
  getSnapshot: () => ({
    doc: JSON.parse(JSON.stringify(studioDoc.doc.value)),
    activeModule: ui.studioActiveModule,
    activeFile: activeFile.value,
    activeFileContent: activeFileContent.value,
    activeFileOriginalContent: activeFileOriginalContent.value,
    activeFileLanguage: activeFileLanguage.value,
    activeFileDirty: activeFileDirty.value,
  }),
  applySnapshot: (snapshot: Record<string, unknown>) => {
    const draftSnapshot = snapshot as SkillStudioDraftSnapshot
    if (isSkillDocument(draftSnapshot.doc)) wb.setSkillDocument?.(draftSnapshot.doc)
    if (typeof draftSnapshot.activeModule === 'string') studioDoc.setActiveModule(draftSnapshot.activeModule)
    if (typeof draftSnapshot.activeFile === 'string') activeFile.value = draftSnapshot.activeFile
    activeFileContent.value = typeof draftSnapshot.activeFileContent === 'string' || draftSnapshot.activeFileContent === null
      ? draftSnapshot.activeFileContent
      : null
    activeFileOriginalContent.value = typeof draftSnapshot.activeFileOriginalContent === 'string'
      ? draftSnapshot.activeFileOriginalContent
      : ''
    activeFileLanguage.value = typeof draftSnapshot.activeFileLanguage === 'string'
      ? draftSnapshot.activeFileLanguage
      : 'skill-md'
    activeFileDirty.value = !!draftSnapshot.activeFileDirty
    documentStore.dirty = true
  },
})


const {
  currentBlock,
  validationErrorCount,
  validationWarningCount,
  handleCursorBlockChange,
  handleValidateCurrentBlock,
} = useStudioBlocks({ wb, studioDoc, skillApi })

// v2.9.1 M1：useStudioShadow 已聚合到 useSkillPublishing（见上方 `publishing.shadow` 反解）

useStudioKeyboard({
  ui,
  isWritable: () => isWritablePerspective(),
  onSaveOrCreate: () => { studioDoc.isCreate.value ? handleCreate() : handleSave() },
  notWritableHint: () => (studioMode.value === 'explain' ? '讲解模式不支持保存' : '审批模式不支持保存'),
  warn: (msg: string) => Message.warning(msg),
})

// v2.11.0 V211-2：3 个事件 handler 抽到 useStudioHandlers composable（见下方 studioHandlers 解构）

// v2.11.0 V211-2：handleBlockUpdate / handleSectionChange / handleBottomTabChange 一锅端
const { handleBlockUpdate, handleSectionChange, handleBottomTabChange } = useStudioHandlers({
  wb,
  studioDoc,
  ui,
  commitHistory,
  fileList,
  shadowReport,
  shadowDivergenceRate,
  loadHistory,
  loadFileList,
  loadShadowReport,
  loadShadowDivergenceRate,
  triggerCoachEvent: (event: string, context?: unknown) =>
    triggerCoachEvent(
      event,
      context && typeof context === 'object' && !Array.isArray(context)
        ? context as Record<string, unknown>
        : {},
    ),
  trackAndCheckRevert,
})


// v2.6+: loadReferences 迁到 useStudioViewHandlers

// ─── L3-C: 规则编辑抽屉 ───────────────────────────────────────────
const ruleNextStepOptions = computed(() => {
  const rules = (studioDoc.doc.value as any)?.rules as Array<{ id?: string; name?: string }> | undefined
  if (!Array.isArray(rules)) return []
  const currentId = ruleDrawerData.value?.nodeId
  return rules
    .filter(r => r && r.id && r.id !== currentId)
    .map(r => ({ value: r.id as string, label: r.name ? `${r.id} · ${r.name}` : (r.id as string) }))
})

function handleEditRule(payload: { nodeId: string; branchIndex: number }) {
  if (!payload || !payload.nodeId) return
  const rules = (studioDoc.doc.value as any)?.rules as Array<any> | undefined
  if (!Array.isArray(rules)) {
    Message.warning('当前 Skill 还没有规则可编辑')
    return
  }
  const step = rules.find(r => r && r.id === payload.nodeId)
  if (!step) {
    Message.error('找不到对应步骤')
    return
  }
  const branches = Array.isArray(step.branches) ? step.branches : []
  const branchIndex = Math.min(Math.max(payload.branchIndex || 0, 0), Math.max(branches.length - 1, 0))
  const branch = branches[branchIndex] || { condition: '', conclusion: '', action: '', next_step: null }
  ruleDrawerData.value = {
    nodeId: step.id,
    nodeName: step.name || '',
    branchIndex,
  }
  ruleDrawerForm.value = {
    condition: String(branch.condition || ''),
    conclusion: String(branch.conclusion || ''),
    action: String(branch.action || ''),
    nextStep: branch.next_step || null,
  }
  ruleDrawerDirty.value = false
  ruleValidateResult.value = null
  ruleDrawerOpen.value = true
}

function closeRuleDrawer() {
  ruleDrawerOpen.value = false
  ruleDrawerData.value = null
  ruleValidateResult.value = null
  ruleDrawerDirty.value = false
}

function markRuleDirty() {
  ruleDrawerDirty.value = true
  ruleValidateResult.value = null
}

function setRuleVerdict(value: string) {
  ruleDrawerForm.value.conclusion = value
  markRuleDirty()
}

function runLocalRuleValidation(): { level: 'success' | 'warning' | 'error'; message: string } {
  // 本地启发式校验（后端不可用时的兜底）：必填字段 + 自引用 + 简单关键词
  const form = ruleDrawerForm.value
  const ctx = ruleDrawerData.value
  if (!form.condition.trim()) {
    return { level: 'error', message: '条件不能为空' }
  }
  if (!form.conclusion.trim()) {
    return { level: 'error', message: '结论不能为空' }
  }
  if (!form.action.trim()) {
    return { level: 'warning', message: '建议补充动作描述' }
  }
  if (form.nextStep && ctx && form.nextStep === ctx.nodeId) {
    return { level: 'error', message: '下一步不能指向当前步骤（自引用）' }
  }
  if (!/[><=]|大于|小于|等于|超过|低于|高于|≥|≤/.test(form.condition)) {
    return { level: 'warning', message: '条件中未发现比较运算符，请确认语义' }
  }
  return { level: 'success', message: '通过基础校验' }
}

async function runRuleValidation() {
  const ctx = ruleDrawerData.value
  if (!ctx) return
  const skillId = wb.skillId
  if (!skillId) {
    ruleValidateResult.value = runLocalRuleValidation()
    return
  }
  ruleValidateRunning.value = true
  try {
    const form = ruleDrawerForm.value
    const resp = await skillApi.validateRule(skillId, {
      step_id: ctx.nodeId,
      condition: form.condition,
      verdict: form.conclusion,
      action: form.action,
      next_step: form.nextStep || null,
    })
    const errors = Array.isArray(resp?.errors) ? resp.errors : []
    const warnings = Array.isArray(resp?.warnings) ? resp.warnings : []
    const hints = Array.isArray(resp?.hints) ? resp.hints : []
    if (errors.length) {
      ruleValidateResult.value = { level: 'error', message: errors[0].message }
    } else if (warnings.length) {
      ruleValidateResult.value = { level: 'warning', message: warnings[0].message }
    } else if (hints.length) {
      ruleValidateResult.value = { level: 'warning', message: hints[0].message }
    } else {
      ruleValidateResult.value = { level: 'success', message: '通过后端校验' }
    }
  } catch (e) {
    // 后端不可用 → 退化为本地启发式
    ruleValidateResult.value = runLocalRuleValidation()
  } finally {
    ruleValidateRunning.value = false
  }
}

function saveRuleDrawer() {
  const ctx = ruleDrawerData.value
  if (!ctx) return
  const docRef = studioDoc.doc.value as any
  const rules: any[] = Array.isArray(docRef?.rules) ? [...docRef.rules] : []
  const idx = rules.findIndex(r => r && r.id === ctx.nodeId)
  if (idx < 0) {
    Message.error('保存失败：步骤已不存在')
    return
  }
  const step = { ...rules[idx] }
  const branches = Array.isArray(step.branches) ? [...step.branches] : []
  const targetIdx = Math.min(ctx.branchIndex, Math.max(branches.length - 1, 0))
  const baseBranch = branches[targetIdx] || {}
  branches[targetIdx] = {
    ...baseBranch,
    condition: ruleDrawerForm.value.condition,
    conclusion: ruleDrawerForm.value.conclusion,
    action: ruleDrawerForm.value.action,
    next_step: ruleDrawerForm.value.nextStep || null,
  }
  step.branches = branches
  rules[idx] = step
  handleBlockUpdate('rules', rules)
  Message.success('规则已保存')
  closeRuleDrawer()
}
</script>

<style scoped>
/* v2.4-4: .viz-panel / .viz-header / .viz-close 样式已下沉到 StudioVizPanels.vue */
/* v2.4-4: .dingtalk-card-preview 样式已下沉到 StudioModalsCluster.vue */
/* v2.5: 助手面板 tab 样式已下沉到 StudioAssistantTabs.vue */
.rule-focus-topbar {
  height: 44px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 24px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-family: var(--ai-font-sans);
}
.rule-focus-back {
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--ai-ink-3);
  cursor: pointer;
}
.rule-focus-back:hover {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
}
.rule-focus-back svg,
.rule-focus-topbar .ai-btn svg,
.rule-drawer-close svg {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
}
.rule-focus-crumb {
  min-width: 0;
  max-width: 44vw;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
}
.rule-focus-title {
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 600;
  white-space: nowrap;
}
.rule-focus-spacer {
  flex: 1;
  min-width: 12px;
}
.deploy-modal {
  display: flex;
  flex-direction: column;
  gap: 14px;
  font-family: var(--ai-font-sans);
}
.deploy-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin: 12px 0;
}
.deploy-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.deploy-desc {
  margin-top: 2px;
  font-size: 12px;
  color: var(--ai-ink-4);
  font-weight: 450;
}
.deploy-targets {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.deploy-target {
  display: grid;
  grid-template-columns: 22px 1fr auto;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  background: var(--ai-surface);
  transition: border-color .15s, background .15s;
}
.deploy-target.selected {
  border-color: var(--ai-border-2);
  background: var(--ai-surface-2);
}
.deploy-target.disabled {
  opacity: .62;
}
.deploy-target-main {
  min-width: 0;
}
.deploy-target-head {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.deploy-target-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.deploy-target-meta {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--ai-ink-4);
  font-weight: 450;
  font-family: var(--ai-font-mono);
}
.deploy-actions {
  display: flex;
  justify-content: flex-end;
  padding-top: 4px;
}

/* a-tag → ai-pill 映射（部署目标徽章等） */
.deploy-modal :deep(.arco-tag) {
  height: 20px; padding: 0 7px; border-radius: 4px; font-size: 11px;
  font-weight: 500; line-height: 18px;
}
.deploy-modal :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft); color: var(--ai-ok); border-color: transparent;
}
.deploy-modal :deep(.arco-tag-color-arcoblue),
.deploy-modal :deep(.arco-tag-color-blue) {
  background: var(--ai-info-soft); color: var(--ai-info); border-color: transparent;
}
.deploy-modal :deep(.arco-tag-color-orange),
.deploy-modal :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft); color: var(--ai-warn); border-color: transparent;
}
.deploy-modal :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft); color: var(--ai-bad); border-color: transparent;
}
.deploy-modal :deep(.arco-tag-color-purple),
.deploy-modal :deep(.arco-tag-color-magenta) {
  background: var(--ai-accent-soft); color: var(--ai-accent-ink); border-color: transparent;
}
.deploy-modal :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2); color: var(--ai-ink-3); border-color: transparent;
}

/* 按钮 → ai-btn 视感 */
.deploy-modal :deep(.arco-btn) {
  height: 30px; border-radius: 6px; font-size: 12.5px; font-weight: 500;
  box-shadow: none;
}
.deploy-modal :deep(.arco-btn-outline),
.deploy-modal :deep(.arco-btn-secondary) {
  border-color: var(--ai-border); background: var(--ai-surface); color: var(--ai-ink-1);
}
.deploy-modal :deep(.arco-btn-outline:hover),
.deploy-modal :deep(.arco-btn-secondary:hover) {
  background: var(--ai-surface-2); border-color: var(--ai-border-2);
}
.deploy-modal :deep(.arco-btn-primary) {
  background: var(--ai-ink-1); border-color: var(--ai-ink-1); color: var(--ai-surface);
  box-shadow: none;
}
.deploy-modal :deep(.arco-btn-primary:hover) {
  background: #000; border-color: #000;
}
.deploy-modal :deep(.arco-btn-mini) {
  height: 24px; font-size: 11.5px; padding: 0 8px;
}

/* ─── L3-C: 规则编辑抽屉 ─── */
.rule-drawer {
  display: flex; flex-direction: column;
  height: 100%; background: var(--ai-bg);
  font-family: var(--ai-font-sans); color: var(--ai-ink-1);
}
.rule-drawer-head {
  display: flex; align-items: flex-start; gap: 12px;
  padding: 16px 20px; background: var(--ai-surface);
  border-bottom: 1px solid var(--ai-border);
}
.rule-drawer-head-main { flex: 1; min-width: 0; }
.rule-drawer-title {
  font-size: 14px; font-weight: 600;
  color: var(--ai-ink-1); letter-spacing: -0.005em;
}
.rule-drawer-sub {
  margin-top: 4px; display: flex; align-items: center; gap: 8px;
  font-size: 12px; color: var(--ai-ink-4);
}
.rule-drawer-mono {
  font-family: var(--ai-font-mono); color: var(--ai-ink-3);
  background: var(--ai-surface-2); padding: 1px 6px; border-radius: 3px;
}
.rule-drawer-name { font-weight: 500; color: var(--ai-ink-2); }
.rule-drawer-close {
  width: 28px; height: 28px; border: 0; background: transparent;
  border-radius: 6px; display: grid; place-items: center;
  color: var(--ai-ink-3); cursor: pointer;
}
.rule-drawer-close:hover { background: var(--ai-surface-2); color: var(--ai-ink-1); }

.rule-drawer-body {
  flex: 1; overflow-y: auto;
  padding: 16px 20px; display: flex; flex-direction: column; gap: 12px;
}
.rule-section {
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  padding: 14px;
}
.rule-section-label {
  font-size: 11px; font-weight: 500;
  color: var(--ai-ink-4); text-transform: uppercase;
  letter-spacing: 0.06em; margin-bottom: 10px;
}

.rule-textarea :deep(.arco-textarea),
.rule-textarea :deep(textarea.arco-textarea) {
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12.5px;
  padding: 8px 10px;
}
.rule-textarea.mono :deep(textarea.arco-textarea),
.rule-textarea.mono :deep(.arco-textarea) {
  font-family: var(--ai-font-mono);
  font-size: 12.5px;
}
.rule-textarea :deep(.arco-textarea-wrapper) {
  border-radius: 6px;
  border-color: var(--ai-border);
  background: var(--ai-surface);
}
.rule-textarea :deep(.arco-textarea-wrapper:hover),
.rule-textarea :deep(.arco-textarea-wrapper:focus-within) {
  border-color: var(--ai-border-2);
}

.rule-input :deep(.arco-input-wrapper) {
  border-radius: 6px;
  border-color: var(--ai-border);
  background: var(--ai-surface);
  margin-top: 8px;
  height: 30px;
}
.rule-input :deep(.arco-input) {
  font-size: 12.5px;
}

.rule-select { width: 100%; }
.rule-select :deep(.arco-select-view) {
  border-radius: 6px;
  border-color: var(--ai-border);
  background: var(--ai-surface);
  height: 30px;
  font-size: 12.5px;
}

.rule-verdicts {
  display: flex; flex-wrap: wrap; gap: 6px;
}
.rule-verdict {
  height: 22px; padding: 0 10px;
  display: inline-flex; align-items: center;
  font-size: 11px; font-weight: 500;
  border-radius: 4px;
  border: 1px solid transparent;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  cursor: pointer; transition: filter .12s;
}
.rule-verdict:hover { filter: brightness(0.96); }
.rule-verdict.active { outline: 2px solid var(--ai-ink-1); outline-offset: 1px; }
.rule-verdict--ok { background: var(--ai-ok-soft); color: var(--ai-ok); }
.rule-verdict--warn { background: var(--ai-warn-soft); color: var(--ai-warn); }
.rule-verdict--bad { background: var(--ai-bad-soft); color: var(--ai-bad); }
.rule-verdict--info { background: var(--ai-info-soft); color: var(--ai-info); }

.rule-validate-row {
  display: flex; align-items: center; gap: 10px;
}
.rule-validate-result {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; font-weight: 500;
}
.rule-validate-dot {
  width: 6px; height: 6px; border-radius: 50%; background: currentColor;
}
.rule-validate-result--success { color: var(--ai-ok); }
.rule-validate-result--warning { color: var(--ai-warn); }
.rule-validate-result--error { color: var(--ai-bad); }

.rule-drawer-actions {
  position: sticky; bottom: 0;
  display: flex; justify-content: flex-end; gap: 8px;
  padding: 12px 20px;
  background: var(--ai-surface);
  border-top: 1px solid var(--ai-border);
}
.rule-btn :deep(.arco-btn),
.rule-btn.arco-btn {
  height: 30px; border-radius: 6px; font-size: 12.5px; font-weight: 500;
}
.rule-btn.primary :deep(.arco-btn),
.rule-btn.primary.arco-btn {
  background: var(--ai-ink-1); border-color: var(--ai-ink-1); color: var(--ai-surface);
}
.rule-btn.primary :deep(.arco-btn:hover),
.rule-btn.primary.arco-btn:hover {
  background: #000; border-color: #000;
}
</style>
