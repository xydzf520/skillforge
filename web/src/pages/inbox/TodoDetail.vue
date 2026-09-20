<template>
  <div class="detail-root">
    <a-spin :loading="loading" style="width: 100%">
      <!-- ━━━━━━━━━━━━━━━━━━━━ 顶部工具栏 ━━━━━━━━━━━━━━━━━━━━ -->
      <div class="detail-toolbar">
        <a-button size="small" @click="router.push('/inbox?tab=pending')">
          <template #icon><icon-left /></template>返回
        </a-button>
        <div class="toolbar-tags">
          <a-tag v-if="allDispatchesDone" color="green">已结案</a-tag>
          <a-tag v-else :color="todoKindTagColor(detail.request?.kind)">{{
            todoKindLabel(detail.request?.kind)
          }}</a-tag>
          <a-tag :color="todoStatusTagColor(detail.todo?.status)">{{
            todoStatusLabel(detail.todo?.status)
          }}</a-tag>
        </div>
      </div>

      <!-- ━━━━━━━━━━━━━━━━━━━━ Hero 区 ━━━━━━━━━━━━━━━━━━━━ -->
      <header class="detail-hero">
        <div class="hero-main">
          <div class="hero-kicker-row">
            <span class="hero-kicker">{{ todoKindLabel(detail.request?.kind) || '待办' }} · {{ detail.skill_meta?.department || '运营' }} · 决策卡</span>
            <span v-if="decisionPriority" class="ai-pill bad">{{ decisionPriority }}</span>
            <span v-if="decisionConfidence" class="hero-confidence">置信度 {{ decisionConfidence }}</span>
          </div>
          <h1 class="hero-title">{{ heroTitle }}</h1>
          <p v-if="heroSummaryText" class="hero-summary">
            {{ heroSummaryText }}
          </p>
          <div class="hero-meta">
            <span>{{ detail.skill_meta?.name || detail.request?.skill_id || '-' }}</span>
            <span v-if="isVideoAgentCard">账号 <span class="mono">{{ videoAccountName || '-' }}</span></span>
            <span v-else>SKU <span class="mono">{{ itemId || '-' }}</span></span>
            <span>{{ heroDataTime || '-' }}</span>
            <span>{{ heroTypeLabel }}</span>
          </div>
          <div class="hero-pill-row">
            <span class="ai-pill" :class="businessActionAllowed ? 'ok' : 'warn'">
              {{ businessActionAllowed ? '可参考建议' : '先补数据/复核' }}
            </span>
            <span v-if="dataQualityText" class="ai-pill">数据 {{ dataQualityText }}</span>
            <span v-if="detail.request?.sla_at" class="ai-pill mono">SLA {{ formatTime(detail.request.sla_at) }}</span>
          </div>
        </div>
        <div class="hero-kpi">
          <div class="hero-kpi-label">{{ primaryIndicator.label }}</div>
          <div class="hero-kpi-value mono" :class="piSeverityClass">{{
            primaryIndicator.display_value || primaryIndicator.value || '-'
          }}</div>
          <div v-if="primaryIndicator.severity" class="hero-kpi-status">
            <span class="hero-kpi-dot" :class="`dot-${primaryIndicator.severity}`"></span>
            {{ severityLabel(primaryIndicator.severity) }}
          </div>
          <div v-if="primaryIndicator.delta_text" class="hero-kpi-delta mono">
            {{ primaryIndicator.delta_text }}
          </div>
        </div>
      </header>

      <RelatedReportLink
        v-if="detail.related_report"
        class="detail-related-report"
        :report="detail.related_report"
        @open="openRelatedReport"
      />

      <!-- ━━━━━━━━━━━━━━━━━━━━ 双栏布局 ━━━━━━━━━━━━━━━━━━━━ -->
      <div class="detail-shell">
        <main class="detail-main">
          <a-card v-if="postTrainingEvaluation" :bordered="false" class="detail-card post-training-card">
            <template #title>后训练模型</template>
            <div class="post-training-head">
              <div class="post-training-title-block">
                <div class="post-training-title">{{ postTrainingStatusLabel }}</div>
                <div class="post-training-subtitle">
                  <span>{{ postTrainingModelName }}</span>
                  <span v-if="postTrainingEvaluation.evaluated_at">{{ formatTime(postTrainingEvaluation.evaluated_at) }}</span>
                </div>
              </div>
              <span class="micro-tag" :class="postTrainingStatusClass">{{ postTrainingStatusLabel }}</span>
            </div>
            <div v-if="postTrainingEvaluation.text" class="post-training-text">
              {{ postTrainingEvaluation.text }}
            </div>
            <div v-else class="post-training-empty">
              {{ postTrainingEvaluation.reason || postTrainingEvaluation.error || '暂无模型评估文本' }}
            </div>
            <div class="post-training-meta-grid">
              <div v-for="item in postTrainingMetaRows" :key="item.label" class="post-training-meta-item">
                <span>{{ item.label }}</span>
                <strong>{{ item.value }}</strong>
              </div>
            </div>
          </a-card>

          <!-- 业务上下文（无决策卡时） -->
          <a-card v-if="hasStructuredContext && !hasDecisionCard" :bordered="false" class="detail-card">
            <template #title>业务上下文</template>
            <a-descriptions :column="1" bordered size="small">
              <a-descriptions-item label="结论摘要">{{ structuredSummaryText || '-' }}</a-descriptions-item>
              <a-descriptions-item label="决策说明">
                <ul v-if="structuredReasoning.length" class="inline-list">
                  <li v-for="item in structuredReasoning" :key="item">{{ item }}</li>
                </ul>
                <span v-else>-</span>
              </a-descriptions-item>
              <a-descriptions-item label="建议动作">
                <ul v-if="structuredSuggestedActions.length" class="inline-list">
                  <li v-for="item in structuredSuggestedActions" :key="item">{{ item }}</li>
                </ul>
                <span v-else>-</span>
              </a-descriptions-item>
            </a-descriptions>
          </a-card>

          <template v-if="isVideoAgentCard">
            <!-- ━━━━ 视频低消耗 Agent 待办 ━━━━ -->
            <template v-if="isSampleBrandLowConsumptionSkill">
              <a-card :bordered="false" class="detail-card agent-video-card samplebrand-manager-card">
                <template #title>处理结论</template>
                <div class="agent-video-summary">
                  <p class="agent-video-summary-text">{{ samplebrandDecisionSummary }}</p>
                  <div class="agent-video-kpi-grid">
                    <div v-for="fact in samplebrandDecisionFacts" :key="fact.label" class="metric-tile tile-neutral">
                      <span class="metric-name">{{ fact.label }}</span>
                      <strong class="metric-value">{{ fact.value }}</strong>
                    </div>
                  </div>
                </div>
              </a-card>

              <a-card v-if="samplebrandDispatchSummaries.length" :bordered="false" class="detail-card agent-video-card samplebrand-manager-card">
                <template #title>建议派发</template>
                <div class="agent-video-action-stack">
                  <article v-for="task in samplebrandDispatchSummaries" :key="task.key" class="sub-card agent-video-action-card">
                    <div class="agent-video-action-head">
                      <div class="agent-video-action-title">
                        <span>派发任务 {{ task.index }}</span>
                        <strong>{{ task.output }}</strong>
                      </div>
                      <div class="action-sub-tags">
                        <span class="micro-tag mt-info">{{ task.status }}</span>
                      </div>
                    </div>
                    <div class="agent-video-meta-line">
                      <span v-if="task.executor">执行人：{{ task.executor }}</span>
                      <span v-if="task.account">账号：{{ task.account }}</span>
                    </div>
                  </article>
                </div>
              </a-card>

              <a-card :bordered="false" class="detail-card agent-video-card samplebrand-manager-card">
                <template #title>视频与对标</template>
                <div class="agent-video-kpi-grid">
                  <div v-for="fact in samplebrandVideoFacts" :key="fact.label" class="metric-tile tile-neutral">
                    <span class="metric-name">{{ fact.label }}</span>
                    <strong class="metric-value">{{ fact.value }}</strong>
                  </div>
                </div>
              </a-card>

              <a-card v-if="samplebrandChartSections.length" :bordered="false" class="detail-card agent-video-card samplebrand-manager-card samplebrand-chart-card">
                <template #title>数据图表</template>
                <div class="samplebrand-chart-grid">
                  <section v-for="section in samplebrandChartSections" :key="section.key" class="samplebrand-chart-panel">
                    <div class="samplebrand-chart-head">
                      <strong>{{ section.title }}</strong>
                      <span v-if="section.description">{{ section.description }}</span>
                    </div>
                    <div v-if="section.type === 'bar_compare'" class="samplebrand-compare-chart">
                      <div v-for="item in section.items" :key="item.key" class="samplebrand-compare-row">
                        <div class="samplebrand-chart-label">{{ item.label }}</div>
                        <div class="samplebrand-compare-bars">
                          <div class="samplebrand-bar-line">
                            <span>当前</span>
                            <div class="samplebrand-bar-track">
                              <i class="samplebrand-bar-current" :style="{ width: `${item.lowWidth}%` }"></i>
                            </div>
                            <b>{{ item.lowText }}</b>
                          </div>
                          <div class="samplebrand-bar-line">
                            <span>参考</span>
                            <div class="samplebrand-bar-track">
                              <i class="samplebrand-bar-benchmark" :style="{ width: `${item.benchmarkWidth}%` }"></i>
                            </div>
                            <b>{{ item.benchmarkText }}</b>
                          </div>
                        </div>
                      </div>
                    </div>
                    <div v-else class="samplebrand-share-chart">
                      <div v-for="item in section.items" :key="item.key" class="samplebrand-share-row">
                        <div class="samplebrand-chart-label">{{ item.label }}</div>
                        <div class="samplebrand-share-track">
                          <i :style="{ width: `${item.width}%` }"></i>
                        </div>
                        <strong>{{ item.valueText }}</strong>
                        <span v-if="item.detail">{{ item.detail }}</span>
                      </div>
                    </div>
                  </section>
                </div>
              </a-card>

              <a-card v-if="samplebrandReasonRows.length" :bordered="false" class="detail-card agent-video-card samplebrand-manager-card">
                <template #title>为什么这样处理</template>
                <ul class="agent-video-list samplebrand-manager-list">
                  <li v-for="item in samplebrandReasonRows" :key="item">{{ item }}</li>
                </ul>
              </a-card>

              <a-card v-if="samplebrandLifecycleCards.length" :bordered="false" class="detail-card agent-video-card samplebrand-manager-card">
                <template #title>高点击依据</template>
                <div class="agent-video-block-grid">
                  <section v-for="card in samplebrandLifecycleCards" :key="card.title" class="agent-video-block">
                    <div class="operator-action-label">{{ card.title }}</div>
                    <p class="samplebrand-evidence-text">{{ card.text }}</p>
                  </section>
                </div>
              </a-card>

              <a-card v-if="samplebrandActionItems.length" :bordered="false" class="detail-card agent-video-card samplebrand-manager-card">
                <template #title>建议动作</template>
                <ol class="agent-video-list ordered samplebrand-manager-list">
                  <li v-for="item in samplebrandActionItems" :key="item">{{ item }}</li>
                </ol>
              </a-card>

              <a-card v-if="samplebrandVisualCards.length" :bordered="false" class="detail-card agent-video-card samplebrand-manager-card">
                <template #title>视觉证据</template>
                <div class="agent-video-block-grid">
                  <section v-for="card in samplebrandVisualCards" :key="card.title" class="agent-video-block">
                    <div class="operator-action-label">{{ card.title }}</div>
                    <p class="samplebrand-evidence-text">{{ card.text }}</p>
                  </section>
                </div>
              </a-card>

              <a-card v-if="samplebrandRetestText || dataQualityRows.length || samplebrandForbiddenActions.length" :bordered="false" class="detail-card agent-video-card samplebrand-manager-card">
                <template #title>复测与风险边界</template>
                <div class="agent-video-split">
                  <section v-if="samplebrandRetestText" class="section-panel">
                    <div class="section-eyebrow">上线后看这些指标</div>
                    <p class="basis-text">{{ samplebrandRetestText }}</p>
                  </section>
                  <section v-if="dataQualityRows.length" class="section-panel">
                    <div class="section-eyebrow">补查数据</div>
                    <div v-for="row in dataQualityRows" :key="row.key" class="gap-row">
                      <strong class="gap-dimension">{{ row.dimension }}</strong>
                      <span class="gap-issue">{{ row.issue }}</span>
                    </div>
                  </section>
                  <section v-if="samplebrandForbiddenActions.length" class="section-panel">
                    <div class="section-eyebrow">不建议事项</div>
                    <ul class="forbidden-list">
                      <li v-for="item in samplebrandForbiddenActions" :key="item">{{ item }}</li>
                    </ul>
                  </section>
                </div>
              </a-card>

              <a-card v-if="dataSourceRows.length" :bordered="false" class="detail-card agent-video-card samplebrand-manager-card">
                <template #title>数据来源</template>
                <div class="stack">
                  <div v-for="source in dataSourceRows" :key="source.key" class="source-row">
                    <div class="source-info">
                      <span class="source-name">{{ source.label }}</span>
                      <span class="source-hint">{{ source.check_hint || source.url }}</span>
                    </div>
                    <a-button v-if="source.url" size="mini" @click="openSourceUrl(source.url)">打开</a-button>
                  </div>
                </div>
              </a-card>
            </template>

            <template v-else>
            <a-card :bordered="false" class="detail-card agent-video-card">
              <template #title>待办结论</template>
              <div class="agent-video-summary">
                <p class="agent-video-summary-text">{{ recommendedDecision || detail.request?.summary || '-' }}</p>
                <div class="agent-video-meta-row">
                  <span v-if="videoAccountName" class="micro-tag mt-info">账号 {{ videoAccountName }}</span>
                  <span v-if="videoTopicText" class="micro-tag mt-neutral">主题 {{ videoTopicText }}</span>
                  <span v-if="videoAnalysisDate" class="micro-tag mt-neutral">{{ videoAnalysisDate }}</span>
                  <span v-if="dataQualityText" class="micro-tag mt-warn">数据 {{ dataQualityText }}</span>
                </div>
                <div v-if="keyMetrics.length" class="agent-video-kpi-grid">
                  <div v-for="metric in keyMetrics" :key="metric.key" class="metric-tile" :class="metric.tone">
                    <span class="metric-name">{{ metric.name }}</span>
                    <strong class="metric-value">{{ metric.value }}</strong>
                    <span v-if="metric.detail" class="metric-detail-text">{{ metric.detail }}</span>
                    <span v-if="metric.status && metric.status !== '-'" class="metric-status-text">{{ metric.status }}</span>
                  </div>
                </div>
              </div>
            </a-card>

            <a-card v-if="videoDetailMarkdown" :bordered="false" class="detail-card agent-video-card">
              <template #title>详细分析</template>
              <div class="agent-video-detail-markdown" v-html="renderedVideoDetailMarkdown" />
            </a-card>

            <a-card v-if="videoActionRows.length" :bordered="false" class="detail-card agent-video-card">
              <template #title>逐视频建议</template>
              <div class="agent-video-action-stack">
                <article v-for="action in videoActionRows" :key="action.key" class="sub-card agent-video-action-card">
                  <div class="agent-video-action-head">
                    <div class="agent-video-action-title">
                      <span>{{ action.section }}</span>
                      <strong>{{ action.scenario || action.action }}</strong>
                    </div>
                    <div class="action-sub-tags">
                      <span v-if="action.priority" class="micro-tag" :class="`mt-${prioritySlug(action.priority)}`">{{ action.priority }}</span>
                      <span v-if="action.dimension" class="micro-tag mt-info">{{ action.dimension }}</span>
                    </div>
                  </div>
                  <div class="agent-video-meta-line">
                    <span v-if="action.theme_name || action.topic_key">主题：{{ action.theme_name || action.topic_key }}</span>
                    <span v-if="action.editor_code">剪辑人：{{ action.editor_code }}</span>
                    <span v-if="action.product_code">产品：{{ action.product_code }}</span>
                    <span v-if="action.video_id">视频 ID：{{ action.video_id }}</span>
                    <span v-if="action.benchmark_label">参考等级：{{ action.benchmark_label }}</span>
                    <span v-if="action.benchmark_title">参考样本：{{ action.benchmark_title }}</span>
                    <span v-if="action.benchmark_editor_code">参考剪辑人：{{ action.benchmark_editor_code }}</span>
                  </div>
                  <div v-if="action.benchmark_selection_reason" class="agent-video-evidence benchmark-selection-evidence">
                    <strong>为什么这样对标</strong>
                    <span>{{ action.benchmark_selection_reason }}</span>
                  </div>
                  <div v-if="action.benchmark_similarity_text" class="agent-video-evidence benchmark-selection-evidence">
                    <strong>相似参考依据</strong>
                    <span>{{ action.benchmark_similarity_text }}</span>
                  </div>
                  <div v-if="action.benchmark_warning" class="agent-video-evidence">对标说明：{{ action.benchmark_warning }}</div>
                  <div v-if="action.evidence" class="agent-video-evidence">证据：{{ action.evidence }}</div>
                  <div class="agent-video-block-grid">
                    <section v-if="action.low_visual_evidence || action.benchmark_visual_evidence" class="agent-video-block agent-video-block-wide">
                      <div class="operator-action-label">画面证据</div>
                      <ul class="agent-video-list">
                        <li v-if="action.low_visual_evidence"><strong>低消耗视频：</strong>{{ action.low_visual_evidence }}</li>
                        <li v-if="action.benchmark_visual_evidence"><strong>参考视频：</strong>{{ action.benchmark_visual_evidence }}</li>
                      </ul>
                    </section>
                    <section v-if="action.root_causes.length" class="agent-video-block">
                      <div class="operator-action-label">原因判断</div>
                      <ul class="agent-video-list">
                        <li v-for="item in action.root_causes" :key="`${action.key}-cause-${item}`">{{ item }}</li>
                      </ul>
                    </section>
                    <section v-if="action.actions.length" class="agent-video-block">
                      <div class="operator-action-label">优化建议</div>
                      <ol class="agent-video-list ordered">
                        <li v-for="item in action.actions" :key="`${action.key}-action-${item}`">{{ item }}</li>
                      </ol>
                    </section>
                    <section v-if="action.keep_points.length" class="agent-video-block">
                      <div class="operator-action-label">保留点</div>
                      <ul class="agent-video-list">
                        <li v-for="item in action.keep_points" :key="`${action.key}-keep-${item}`">{{ item }}</li>
                      </ul>
                    </section>
                    <section v-if="action.data_checks.length" class="agent-video-block">
                      <div class="operator-action-label">补查项</div>
                      <ul class="agent-video-list">
                        <li v-for="item in action.data_checks" :key="`${action.key}-check-${item}`">{{ item }}</li>
                      </ul>
                    </section>
                  </div>
                  <div v-if="action.ai_action" class="action-sub-ai">AI补采：{{ action.ai_action }}</div>
                </article>
              </div>
            </a-card>

            <a-card v-if="metricSections.length" :bordered="false" class="detail-card agent-video-card">
              <template #title>指标与参考建议</template>
              <div v-for="section in metricSections" :key="section.key" class="section-block section-panel">
                <div class="section-eyebrow">{{ section.title }}<span class="eyebrow-extra" v-if="section.metrics.length"> · {{ section.metrics.length }} 项</span></div>
                <div class="metric-grid agent-video-metric-grid">
                  <div v-for="metric in section.metrics" :key="metric.key" class="metric-tile" :class="metric.tone">
                    <span class="metric-name">{{ metric.name }}</span>
                    <strong class="metric-value">{{ metric.value }}</strong>
                    <span v-if="metric.detail" class="metric-detail-text">{{ metric.detail }}</span>
                    <span class="metric-status-text" v-if="metric.status && metric.status !== '-'">{{ metric.status }}</span>
                  </div>
                </div>
              </div>
            </a-card>

            <a-card v-if="analysisBasis.length || dataQualityRows.length || forbiddenActions.length" :bordered="false" class="detail-card agent-video-card">
              <template #title>判断依据与边界</template>
              <div class="agent-video-split">
                <section v-if="analysisBasis.length" class="section-panel">
                  <div class="section-eyebrow">判断依据</div>
                  <article v-for="basis in analysisBasis" :key="basis.key" class="basis-sub-card">
                    <div class="basis-sub-head">
                      <strong>{{ basis.dimension }}</strong>
                      <span class="micro-tag" :class="confidenceSlug(basis.confidence)">{{ basis.confidence || '-' }}</span>
                    </div>
                    <p class="basis-text">{{ basis.basis }}</p>
                    <div v-if="basis.data_gap" class="basis-gap-note">缺口：{{ basis.data_gap }}</div>
                  </article>
                </section>
                <section v-if="dataQualityRows.length" class="section-panel">
                  <div class="section-eyebrow">补查数据</div>
                  <div v-for="row in dataQualityRows" :key="row.key" class="gap-row">
                    <strong class="gap-dimension">{{ row.dimension }}</strong>
                    <span class="gap-issue">{{ row.issue }}</span>
                  </div>
                </section>
                <section v-if="forbiddenActions.length" class="section-panel">
                  <div class="section-eyebrow">不建议事项</div>
                  <ul class="forbidden-list">
                    <li v-for="item in forbiddenActions" :key="item">{{ item }}</li>
                  </ul>
                </section>
              </div>
            </a-card>

            <a-card v-if="dataSourceRows.length" :bordered="false" class="detail-card agent-video-card">
              <template #title>数据来源</template>
              <div class="stack">
                <div v-for="source in dataSourceRows" :key="source.key" class="source-row">
                  <div class="source-info">
                    <span class="source-name">{{ source.label }}</span>
                    <span class="source-hint">{{ source.check_hint || source.url }}</span>
                  </div>
                  <a-button v-if="source.url" size="mini" @click="openSourceUrl(source.url)">打开</a-button>
                </div>
              </div>
            </a-card>
            </template>
          </template>

          <!-- ━━━━ 运营建议 ━━━━ -->
          <a-card v-if="!isVideoAgentCard && hasDecisionCard && hasOperationContent" :bordered="false" class="detail-card">
            <template #title>运营建议</template>
            <template #extra>
              <a-space v-if="canEditDraft" wrap size="mini">
                <a-button v-if="!editingDraft" size="small" @click="startDraftEdit">编辑</a-button>
                <template v-else>
                  <a-button size="small" type="primary" :loading="savingDraft" @click="saveDraftEdit">保存</a-button>
                  <a-button size="small" :disabled="savingDraft" @click="cancelDraftEdit">取消</a-button>
                </template>
              </a-space>
            </template>

            <div v-if="editingDraft" class="edit-block">
              <div class="edit-field">
                <div class="section-eyebrow">决策建议</div>
                <a-textarea v-model="draft.recommended_decision" :auto-size="{ minRows: 2, maxRows: 5 }" placeholder="主管确认后的决策建议" />
              </div>
              <div class="edit-field">
                <div class="section-eyebrow">运营建议</div>
                <div v-for="(_, index) in draft.suggested_actions" :key="`act-${index}`" class="edit-row">
                  <a-textarea v-model="draft.suggested_actions[index]" :auto-size="{ minRows: 2, maxRows: 4 }" placeholder="运营建议" />
                  <a-button size="mini" status="danger" @click="removeDraftAction(index)">删除</a-button>
                </div>
                <a-button size="small" @click="addDraftAction">新增建议</a-button>
              </div>
            </div>

            <div v-else-if="operatorActionGroups.length" class="operator-action-stack">
              <section v-for="group in operatorActionGroups" :key="group.section" class="operator-action-section">
                <div class="operator-section-head">
                  <strong>{{ group.section }}</strong>
                  <span>{{ group.rows.length }} 个命中场景</span>
                </div>
                <article v-for="action in group.rows" :key="action.key" class="sub-card operator-action-card">
                  <div class="action-sub-head">
                    <strong>{{ action.scenario || action.action }}</strong>
                    <div class="action-sub-tags">
                      <span v-if="action.priority" class="micro-tag" :class="`mt-${prioritySlug(action.priority)}`">{{ action.priority }}</span>
                      <span v-if="action.dimension" class="micro-tag mt-info">{{ action.dimension }}</span>
                    </div>
                  </div>
                  <div v-if="action.evidence" class="action-sub-evidence">证据：{{ action.evidence }}</div>
                  <div v-if="action.root_causes.length" class="operator-action-block">
                    <div class="operator-action-label">核心原因</div>
                    <div class="operator-chip-row">
                      <span v-for="cause in action.root_causes" :key="`${action.key}-${cause}`" class="chip-tag">{{ cause }}</span>
                    </div>
                  </div>
                  <div v-if="action.actions.length" class="operator-action-block">
                    <div class="operator-action-label">落地建议</div>
                    <ol class="operator-action-list">
                      <li v-for="item in action.actions" :key="`${action.key}-${item}`">{{ item }}</li>
                    </ol>
                  </div>
                  <div v-if="action.ai_action" class="action-sub-ai">AI补采：{{ action.ai_action }}</div>
                </article>
              </section>
            </div>

            <!-- 运营建议卡片列表 -->
            <div v-else-if="operationActionRows.length" class="card-grid action-grid">
              <article v-for="action in operationActionRows" :key="action.key" class="sub-card action-sub-card">
                <div class="action-sub-head">
                  <strong>{{ action.action }}</strong>
                  <div class="action-sub-tags">
                    <span v-if="action.priority" class="micro-tag" :class="`mt-${prioritySlug(action.priority)}`">{{ action.priority }}</span>
                    <span v-if="action.dimension" class="micro-tag mt-info">{{ action.dimension }}</span>
                  </div>
                </div>
                <div v-if="action.evidence" class="action-sub-evidence">证据：{{ action.evidence }}</div>
                <div v-if="action.ai_action" class="action-sub-ai">AI补采：{{ action.ai_action }}</div>
              </article>
            </div>

            <ol v-else-if="suggestedActions.length" class="inline-list">
              <li v-for="item in suggestedActions" :key="item">{{ item }}</li>
            </ol>

            <div v-else class="empty-note">暂无运营建议</div>
          </a-card>

          <!-- ━━━━ 不建议事项 ━━━━ -->
          <a-card v-if="!isVideoAgentCard && hasDecisionCard && (forbiddenActions.length || editingDraft)" :bordered="false" class="detail-card detail-card-danger">
            <template #title>不建议事项</template>
            <div v-if="editingDraft" class="edit-block">
              <div v-for="(_, index) in draft.forbidden_actions" :key="`forbid-${index}`" class="edit-row">
                <a-input v-model="draft.forbidden_actions[index]" placeholder="不建议事项" />
                <a-button size="mini" status="danger" @click="removeDraftForbidden(index)">删除</a-button>
              </div>
              <a-button size="small" @click="addDraftForbidden">新增不建议事项</a-button>
            </div>
            <ul v-else class="forbidden-list">
              <li v-for="item in forbiddenActions" :key="item">{{ item }}</li>
            </ul>
          </a-card>

          <!-- ━━━━ 为什么会异常 ━━━━ -->
          <a-card v-if="!isVideoAgentCard && rootCauseRows.length" :bordered="false" class="detail-card">
            <template #title>为什么会异常</template>
            <div class="stack">
              <article v-for="cause in rootCauseRows" :key="cause.key" class="sub-card cause-sub-card">
                <div class="cause-sub-head">
                  <span class="cause-rank-badge">{{ cause.rank }}</span>
                  <strong>{{ cause.dimension }}</strong>
                  <div class="cause-sub-tags">
                    <span class="micro-tag" :class="cause.role === '主因' ? 'mt-danger' : cause.role === '次因' ? 'mt-warn' : 'mt-info'">{{ cause.role }}</span>
                    <span v-if="cause.priority" class="micro-tag" :class="`mt-${prioritySlug(cause.priority)}`">{{ cause.priority }}优</span>
                  </div>
                </div>
                <div class="cause-sub-evidence">{{ cause.evidence || '-' }}</div>
                <div v-if="cause.actions && cause.actions.length" class="cause-sub-chips">
                  <span v-for="act in cause.actions.slice(0, 3)" :key="act" class="chip-tag">{{ act }}</span>
                </div>
              </article>
            </div>
          </a-card>

          <!-- ━━━━ 判断依据 ━━━━ -->
          <a-card v-if="!isVideoAgentCard && analysisBasisRows.length" :bordered="false" class="detail-card">
            <template #title>判断依据</template>
            <div class="stack">
              <article v-for="basis in analysisBasisRows" :key="basis.key" class="sub-card basis-sub-card">
                <div class="basis-sub-head">
                  <strong>{{ basis.dimension }}</strong>
                  <span class="micro-tag" :class="confidenceSlug(basis.confidence)">{{ basis.confidence || '-' }}</span>
                </div>
                <p class="basis-text">{{ basis.basis }}</p>
                <div v-if="basis.data_gap" class="basis-gap-note">缺口：{{ basis.data_gap }}</div>
              </article>
            </div>
          </a-card>

          <!-- ━━━━ 数据支撑 ━━━━ -->
          <a-card v-if="!isVideoAgentCard && hasDecisionCard && (freeSearchCompareRows.length || standardCompareTable || metricSections.length)" :bordered="false" class="detail-card detail-card-data">
            <template #title>数据支撑</template>

            <div v-if="freeSearchCompareRows.length" class="section-block section-panel">
              <div class="section-eyebrow">免费搜索昨日同刻对比</div>
              <div class="snapshot-compare-panel">
                <div class="snapshot-compare-head">
                  <div>
                    <div class="snapshot-compare-title">免费搜索昨日同刻对比</div>
                    <div class="snapshot-compare-period">{{ freeSearchComparePeriod }}</div>
                  </div>
                  <span class="micro-tag mt-info">本地快照</span>
                </div>
                <div class="snapshot-compare-grid">
                  <div v-for="row in freeSearchCompareRows" :key="row.key" class="snapshot-compare-cell" :class="row.tone">
                    <span>{{ row.label }}</span>
                    <strong>{{ row.currentText }} <small>vs {{ row.previousText }}</small></strong>
                    <em>{{ row.changeText }}</em>
                  </div>
                </div>
              </div>
            </div>

            <!-- 标准口径对比表 -->
            <div v-if="standardCompareTable" class="section-block section-panel">
              <div class="section-eyebrow">{{ standardCompareTable.title }}</div>
              <div v-if="freeSearchRealtimeFacts" class="free-search-fact-strip">
                <div class="free-search-fact-head">
                  <div>
                    <div class="free-search-fact-title">免费搜索实时事实</div>
                    <div class="free-search-fact-source">{{ freeSearchRealtimeFacts.source }}</div>
                  </div>
                  <span class="micro-tag mt-info">已采集</span>
                </div>
                <div class="free-search-fact-grid">
                  <div v-for="fact in freeSearchRealtimeFacts.items" :key="fact.key" class="free-search-fact-cell">
                    <span>{{ fact.label }}</span>
                    <strong>{{ fact.value }}</strong>
                  </div>
                </div>
                <div class="free-search-compare-note">{{ freeSearchRealtimeFacts.compareNote }}</div>
              </div>
              <div v-if="standardCompareTable.promotedRows.length" class="metric-grid promoted-metric-grid">
                <div
                  v-for="row in standardCompareTable.promotedRows"
                  :key="row.key"
                  class="metric-tile promoted-metric-tile"
                  :class="row.tone"
                >
                  <div class="promoted-metric-head">
                    <span class="metric-name">{{ row.label }}</span>
                    <strong class="metric-value">{{ row.curVal }}</strong>
                  </div>
                  <div v-if="row.compareDetail" class="promoted-compare-detail">
                    <div v-for="line in row.compareDetail.lines" :key="line.key" class="promoted-period-line">
                      <span class="promoted-period-label">{{ line.label }}</span>
                      <div v-if="line.metrics.length" class="promoted-metric-pills">
                        <span v-for="metric in line.metrics" :key="metric.key" class="promoted-metric-pill">
                          <b>{{ metric.label }}</b>
                          <span>{{ metric.value }}</span>
                        </span>
                      </div>
                      <span v-else class="promoted-period-text">{{ line.text }}</span>
                    </div>
                    <div v-if="row.compareDetail.summaryMetrics.length" class="promoted-change-pills">
                      <span v-for="metric in row.compareDetail.summaryMetrics" :key="metric.key" class="promoted-change-pill">
                        <b>{{ metric.label }}</b>
                        <span>{{ metric.value }}</span>
                      </span>
                    </div>
                    <div v-else-if="row.compareDetail.summary" class="promoted-change-summary">{{ row.compareDetail.summary }}</div>
                  </div>
                  <span v-else-if="row.deltaVal" class="metric-detail-text">{{ row.deltaVal }}</span>
                  <span class="metric-status-text" v-if="row.status && row.status !== '-'">{{ row.status }}</span>
                </div>
              </div>
              <div class="compare-core-grid">
                <article v-for="row in standardCompareTable.coreRows" :key="row.key" class="compare-core-card" :class="`row-${row.status}`">
                  <div class="compare-core-head">
                    <strong>{{ row.label }}</strong>
                    <span class="micro-tag" :class="deltaToneSlug(row.status)">{{ row.status }}</span>
                  </div>
                  <div class="compare-core-values">
                    <div>
                      <span>当前24h</span>
                      <b>{{ row.curVal }}</b>
                    </div>
                    <div>
                      <span>前24h</span>
                      <b :class="{ 'ct-prev': row.prevVal === '未获取' }">{{ row.prevVal }}</b>
                    </div>
                    <div class="compare-core-delta">
                      <span>变化率</span>
                      <b>{{ row.deltaVal }}</b>
                    </div>
                  </div>
                </article>
              </div>
              <div v-if="standardCompareTable.extraRows.length" class="compare-extra-list">
                <article v-for="row in standardCompareTable.extraRows" :key="row.key" class="row-extra compare-extra-row">
                  <div class="compare-extra-head">
                    <strong class="ct-label">{{ row.label }}</strong>
                    <span class="micro-tag" :class="extraStatusSlug(row.status)">{{ row.status }}</span>
                  </div>
                  <div class="ct-extra-detail">
                    <div v-if="row.paidCompareRows?.length" class="ct-paid-compare">
                      <div v-if="row.paidComparePeriod" class="ct-paid-period">{{ row.paidComparePeriod }}</div>
                      <div class="ct-paid-metrics">
                        <span v-for="metric in row.paidCompareRows" :key="metric.key" class="ct-paid-pill" :class="metric.tone">
                          <b>{{ metric.label }}</b>
                          <span>{{ metric.currentText }} / {{ metric.previousText }}</span>
                          <em>{{ metric.changeText }}</em>
                        </span>
                      </div>
                    </div>
                    <span v-else>{{ row.deltaVal }}</span>
                  </div>
                </article>
              </div>
            </div>

            <!-- 指标分组 -->
            <div v-for="section in metricSections" :key="section.key" class="section-block section-panel">
              <div class="section-eyebrow">{{ section.title }}<span class="eyebrow-extra" v-if="section.metrics.length"> · {{ section.metrics.length }} 项</span></div>
              <div class="metric-grid">
                <div v-for="metric in section.metrics" :key="metric.key" class="metric-tile" :class="metric.tone">
                  <span class="metric-name">{{ metric.name }}</span>
                  <strong class="metric-value">{{ metric.value }}</strong>
                  <span v-if="metric.detail" class="metric-detail-text">{{ metric.detail }}</span>
                  <span class="metric-status-text" v-if="metric.status && metric.status !== '-'">{{ metric.status }}</span>
                </div>
              </div>
            </div>
          </a-card>

          <!-- ━━━━ 付费实时数据 ━━━━ -->
          <a-card v-if="!isVideoAgentCard && (paidRealtimeCompareRows.length || paidFlowRows.length || paidDetailRows.length || paidVisiblePlanRows.length || paidPlanGapText)" :bordered="false" class="detail-card detail-card-paid">
            <template #title>付费实时数据</template>

            <div v-if="paidRealtimeCompareRows.length" class="section-block section-panel">
              <div class="section-eyebrow">付费商品汇总实时<span class="eyebrow-extra"> · 昨日同刻对比</span></div>
              <div class="paid-compare-board">
                <div class="paid-compare-cards">
                  <article v-for="row in paidRealtimeCompareRows" :key="row.key" class="paid-compare-card" :class="row.tone">
                    <span>{{ row.label }}</span>
                    <strong>{{ row.currentText }}</strong>
                    <div>
                      <small>昨日同刻 {{ row.previousText }}</small>
                      <em>{{ row.changeText }}</em>
                    </div>
                  </article>
                </div>
                <div class="paid-compare-period">{{ paidRealtimeComparePeriod }}</div>
              </div>
            </div>

            <div v-if="paidFlowRows.length" class="section-block section-panel">
              <div class="section-eyebrow">关键词推广实时与环比<span class="eyebrow-extra"> · {{ paidFlowRows.length }} 项</span></div>
              <div class="metric-grid">
                <div v-for="row in paidFlowRows" :key="row.key" class="metric-tile" :class="row.tone">
                  <span class="metric-name">{{ row.label }}</span>
                  <strong class="metric-value">{{ row.value }}</strong>
                  <span v-if="row.detail" class="metric-detail-text">{{ row.detail }}</span>
                  <span class="metric-status-text" v-if="row.status && row.status !== '-'">{{ row.status }}</span>
                  <span v-if="row.meta" class="metric-source-text">{{ row.meta }}</span>
                </div>
              </div>
            </div>

            <div v-if="paidDetailRows.length" class="section-block section-panel">
              <div class="section-eyebrow">付费异常明细<span class="eyebrow-extra"> · {{ paidDetailRows.length }} 项</span></div>
              <div class="stack">
                <div v-for="row in paidDetailRows" :key="row.key" class="detail-row">
                  <strong class="detail-row-label">{{ row.label }}</strong>
                  <span class="detail-row-text">{{ row.text }}</span>
                </div>
              </div>
            </div>

            <div v-if="paidVisiblePlanRows.length" class="section-block section-panel">
              <div class="section-eyebrow">计划级明细<span class="eyebrow-extra"> · {{ paidVisiblePlanRows.length }} 项</span></div>
              <div class="mini-table-wrap">
                <table class="mini-table">
                  <thead>
                    <tr>
                      <th>计划</th><th>花费</th><th>直接ROI</th><th>CPC</th><th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in paidVisiblePlanRows" :key="row.key">
                      <td class="mini-label">{{ row.name }}</td>
                      <td>{{ row.charge }}</td>
                      <td>{{ row.roi }}</td>
                      <td>{{ row.cpc }}</td>
                      <td>{{ row.status }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
            <div v-else-if="paidPlanGapText" class="section-block section-panel">
              <div class="section-eyebrow">计划级明细</div>
              <div class="gap-note">{{ paidPlanGapText }}</div>
            </div>
          </a-card>

          <!-- ━━━━ 免费流分析依据 ━━━━ -->
          <a-card v-if="!isVideoAgentCard && freeFlowRows.length" :bordered="false" class="detail-card">
            <template #title>免费流分析依据</template>
            <div class="metric-grid">
              <div v-for="row in freeFlowRows" :key="row.key" class="metric-tile" :class="row.tone">
                <span class="metric-name">{{ row.label }}</span>
                <strong class="metric-value">{{ row.value }}</strong>
                <span v-if="row.detail" class="metric-detail-text">{{ row.detail }}</span>
                <span class="metric-status-text" v-if="row.status && row.status !== '-'">{{ row.status }}</span>
                <span v-if="row.meta" class="metric-source-text">{{ row.meta }}</span>
              </div>
            </div>
          </a-card>

          <!-- ━━━━ 数据缺口 ━━━━ -->
          <a-card v-if="!isVideoAgentCard && dataQualityRows.length" :bordered="false" class="detail-card">
            <template #title>数据缺口</template>
            <div class="stack">
              <div v-for="row in visibleGapRows" :key="row.key" class="gap-row">
                <strong class="gap-dimension">{{ row.dimension }}</strong>
                <span class="gap-issue">{{ row.issue }}</span>
              </div>
            </div>
            <div v-if="hasMoreGaps" class="fold-toggle">
              <a-button size="mini" type="text" @click="showAllGaps = !showAllGaps">
                {{ showAllGaps ? '收起全部' : `展开全部 ${dataQualityRows.length} 条` }}
              </a-button>
            </div>
          </a-card>

          <!-- ━━━━ 数据来源 ━━━━ -->
          <a-card v-if="!isVideoAgentCard && dataSourceRows.length" :bordered="false" class="detail-card">
            <template #title>数据来源</template>
            <div class="stack">
              <div v-for="source in dataSourceRows" :key="source.key" class="source-row">
                <div class="source-info">
                  <span class="source-name">{{ source.label }}</span>
                  <span class="source-hint">{{ source.check_hint || source.url }}</span>
                </div>
                <a-button v-if="source.url" size="mini" @click="openSourceUrl(source.url)">打开</a-button>
              </div>
            </div>
          </a-card>

          <!-- ━━━━ 派发任务清单 ━━━━ -->
          <a-card
            v-if="detail.request?.kind === 'dispatch'"
            :bordered="false"
            class="detail-card"
          >
            <template #title>派发任务清单 ({{ detail.dispatch_tasks?.length || 0 }})</template>
            <template #extra>
              <a-space v-if="canEditDraft" wrap size="mini">
                <a-button v-if="!editingDraft" size="small" @click="startDraftEdit">编辑</a-button>
                <template v-else>
                  <a-button size="small" type="primary" :loading="savingDraft" @click="saveDraftEdit">保存</a-button>
                  <a-button size="small" :disabled="savingDraft" @click="cancelDraftEdit">取消</a-button>
                </template>
              </a-space>
            </template>

            <div v-if="detail.todo?.status === 'pending'" class="notice-banner">
              通过审批后，下列任务会自动推送到每位执行人的钉钉。
            </div>
            <div v-else-if="detail.request?.aggregate_decision === 'approved' && !allDispatchesDone" class="notice-banner notice-ok">
              已派发。等待执行人在钉钉点击「标记完成」即可结案。
            </div>
            <div v-else-if="detail.request?.aggregate_decision === 'approved' && allDispatchesDone" class="notice-banner notice-ok">
              全部执行人已在钉钉标记完成，任务结案。
            </div>
            <div v-else-if="detail.request?.aggregate_decision === 'rejected'" class="notice-banner notice-warn">
              已驳回。所有子任务已取消。
            </div>
            <div v-if="assignableUsersError" class="notice-banner notice-warn">
              {{ assignableUsersError }}
            </div>

            <a-table :data="detail.dispatch_tasks || []" :pagination="false" size="small" row-key="id" class="dispatch-table">
              <template #columns>
                <a-table-column title="#" data-index="id" :width="50" />
                <a-table-column title="执行人" :width="170">
                  <template #cell="{ record }">
                    <a-select
                      v-if="editingDraft"
                      :model-value="draftTask(record).executor || undefined"
                      allow-clear placeholder="选择执行人"
                      @change="updateDraftTaskField(record, 'executor', String($event || ''))"
                    >
                      <a-option v-for="user in assignableUsers" :key="user.id" :value="user.id">
                        {{ assignableUserLabel(user) }}
                      </a-option>
                    </a-select>
                    <span v-else>{{ dispatchExecutorLabel(record) }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="任务内容">
                  <template #cell="{ record }">
                    <div v-if="editingDraft" class="edit-block">
                      <a-textarea
                        :model-value="draftTask(record).content"
                        :auto-size="{ minRows: 3, maxRows: 8 }"
                        placeholder="主管确认后的任务内容"
                        @update:model-value="updateDraftTaskField(record, 'content', $event)"
                      />
                    </div>
                    <div v-else class="dispatch-cell">
                      <div class="dispatch-cell-primary">{{ dispatchPrimary(record) }}</div>
                      <ul class="dispatch-cell-segments">
                        <li v-for="segment in dispatchSegments(record.display_content || record.content)" :key="segment.label">
                          <strong>{{ segment.label }}：</strong><span>{{ segment.text }}</span>
                        </li>
                      </ul>
                    </div>
                  </template>
                </a-table-column>
                <a-table-column title="截止" :width="150">
                  <template #cell="{ record }">
                    <a-input v-if="editingDraft" :model-value="draftTask(record).deadline" placeholder="YYYY-MM-DDTHH:mm:ss" @update:model-value="updateDraftTaskField(record, 'deadline', $event)" size="small" />
                    <span v-else class="text-caption">{{ formatTime(record.deadline) || '-' }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="状态" :width="100">
                  <template #cell="{ record }">
                    <span class="micro-tag" :class="dispatchToneSlug(record.status)">{{ dispatchStatusLabel(record.status) }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="指派" :width="240">
                  <template #cell="{ record }">
                    <div v-if="canAssignDispatchTask(record)" class="dispatch-assign-cell">
                      <a-select
                        v-model="assignSelections[record.id]"
                        placeholder="选择本部门成员"
                        allow-clear
                        size="small"
                        :disabled="!assignableUsers.length"
                      >
                        <a-option v-for="user in assignableUsers" :key="user.id" :value="user.id">
                          {{ assignableUserLabel(user) }}
                        </a-option>
                      </a-select>
                      <a-button
                        size="mini"
                        type="primary"
                        :loading="assigningTaskId === record.id"
                        :disabled="!assignSelections[record.id]"
                        @click="assignDispatchTask(record)"
                      >
                        {{ record.executor ? '改派' : '指派' }}
                      </a-button>
                    </div>
                    <span v-else class="text-caption text-muted">{{ record.assigned_at ? `已指派于 ${formatTime(record.assigned_at)}` : '-' }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="确认时间" :width="150">
                  <template #cell="{ record }">{{ formatTime(record.ack_at) || '-' }}</template>
                </a-table-column>
                <a-table-column title="完成说明" :width="180">
                  <template #cell="{ record }">
                    <span v-if="record.ack_note" class="text-caption">{{ record.ack_note }}</span>
                    <span v-else class="text-caption text-muted">-</span>
                  </template>
                </a-table-column>
              </template>
            </a-table>

            <!-- 钉钉回复 -->
            <div v-if="dispatchAckNotes.length" class="dingtalk-replies">
              <div class="section-eyebrow">钉钉回复</div>
              <div v-for="note in dispatchAckNotes" :key="note.id" class="reply-item">
                <span class="reply-time">{{ formatTime(note.ack_at) }}</span>
                <span class="reply-text">{{ note.ack_note }}</span>
              </div>
            </div>
          </a-card>
        </main>

        <!-- ━━━━━━━━━━━━━━━ 右侧栏 ━━━━━━━━━━━━━━━ -->
        <aside class="detail-aside">
          <div class="aside-sticky">
            <!-- 处理 -->
            <a-card v-if="detail.todo?.status === 'pending'" :bordered="false" class="aside-card aside-card-action">
              <template #title>处理</template>
              <div class="aside-actions">
                <a-button long type="primary" status="success" @click="decide('approved')">
                  {{ detail.request?.kind === 'dispatch' ? '通过并派发' : '通过' }}
                </a-button>
                <a-button long type="primary" status="danger" @click="decide('rejected')">驳回</a-button>
                <a-button v-if="userStore.isAdmin" long @click="extendSla">延期 24h</a-button>
              </div>
            </a-card>

            <!-- 基本信息 -->
            <a-card :bordered="false" class="aside-card">
              <template #title>基本信息</template>
              <dl class="info-dl">
                <div class="info-row"><dt>待办 ID</dt><dd>{{ detail.todo?.id || '-' }}</dd></div>
                <div class="info-row"><dt>类型</dt><dd>{{ allDispatchesDone ? '派发·已结案' : todoKindLabel(detail.request?.kind) }}</dd></div>
                <div class="info-row"><dt>聚合状态</dt><dd>{{ allDispatchesDone ? '已结案' : (detail.request?.aggregate_status || '-') }}</dd></div>
                <div class="info-row"><dt>截止时间</dt><dd>{{ formatTime(detail.request?.sla_at) || '-' }}</dd></div>
                <div class="info-row"><dt>处理人</dt><dd>{{ detail.todo?.decided_by || '-' }}</dd></div>
                <div class="info-row"><dt>Skill</dt><dd><code class="info-code">{{ detail.request?.skill_id || '-' }}</code></dd></div>
                <div class="info-row"><dt>Skill 名称</dt><dd>{{ detail.skill_meta?.name || '-' }}</dd></div>
                <div class="info-row"><dt>所属部门</dt><dd>{{ detail.skill_meta?.department || '-' }}</dd></div>
                <div class="info-row"><dt>决策模式</dt><dd>{{ decisionModeLabel(detail.request?.decision_mode) }}</dd></div>
              </dl>
            </a-card>

            <!-- 同 Skill 历史 -->
            <a-card v-if="timelineItems.length" :bordered="false" class="aside-card">
              <template #title>同 Skill 历史 ({{ timelineItems.length }})</template>
              <div class="timeline-stack">
                <div v-for="item in timelineItems" :key="item.todo_id" class="timeline-row">
                  <span class="timeline-decision" :class="item.decision === 'approved' ? 'tl-ok' : 'tl-no'">
                    {{ item.decision === 'approved' ? '通过' : '驳回' }}
                  </span>
                  <div class="timeline-body">
                    <div class="timeline-title-text">{{ item.title }}</div>
                    <div class="timeline-meta-text">{{ formatTime(item.decided_at) }} · {{ item.decided_by || '—' }}</div>
                    <div v-if="item.decision_reason" class="timeline-reason-text">{{ item.decision_reason }}</div>
                  </div>
                </div>
              </div>
            </a-card>

            <!-- 调试 -->
            <a-card :bordered="false" class="aside-card">
              <template #title>调试</template>
              <a-button size="mini" @click="showDebugPayload = !showDebugPayload">
                {{ showDebugPayload ? '收起原始决策 JSON' : '展开原始决策 JSON' }}
              </a-button>
              <pre v-if="showDebugPayload" class="debug-block">{{ prettyPayload }}</pre>
            </a-card>
          </div>
        </aside>
      </div>
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { computed, h, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { Message, Modal, Input, Option as AOption, Select as ASelect, Tag as ATag } from '@arco-design/web-vue'
import { IconLeft } from '@arco-design/web-vue/es/icon'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { formatTime } from '@/utils/format'
import { renderMd } from '@/utils/renderMd'
import type { InboxDispatchTask, InboxTodoDetailResponse, PostTrainingModelEvaluation } from '@/types/inbox'
import RelatedReportLink from './components/RelatedReportLink.vue'
import { todoApi } from '@/api'
import {
  decisionModeLabel,
  dispatchStatusLabel,
  prettyJson,
  todoKindLabel,
  todoStatusLabel,
} from './presentation'

const route: any = useRoute()
const router: any = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const detail = ref<InboxTodoDetailResponse>({
  todo: null, request: null, skill_meta: null, payload: {}, structured: null,
  dispatch_tasks: [], related_report: null,
})

const todoId = computed(() => Number(route.params.id))
const showDebugPayload = ref(false)
const prettyPayload = computed(() => showDebugPayload.value ? prettyJson(detail.value.payload || {}) : '')
const payload = computed(() => asRecord(detail.value.payload) || {})
const outputPayload = computed(() => asRecord(payload.value.output) || {})
const inputPayload = computed(() => asRecord(payload.value.input) || {})
const postTrainingEvaluation = computed<PostTrainingModelEvaluation | null>(() => {
  const direct = detail.value.post_training_model_evaluation
  if (direct && typeof direct === 'object') return direct
  const fromPayload = asRecord(payload.value.post_training_model_evaluation)
  return fromPayload ? fromPayload as PostTrainingModelEvaluation : null
})
const postTrainingStatusLabel = computed(() => {
  const status = String(postTrainingEvaluation.value?.status || '').toLowerCase()
  if (status === 'used') return '已评估'
  if (status === 'skipped') return '已跳过'
  if (status === 'failed') return '评估失败'
  return status || '未知'
})
const postTrainingStatusClass = computed(() => {
  const status = String(postTrainingEvaluation.value?.status || '').toLowerCase()
  if (status === 'used') return 'mt-ok'
  if (status === 'failed') return 'mt-danger'
  if (status === 'skipped') return 'mt-warn'
  return 'mt-neutral'
})
const postTrainingModelName = computed(() => {
  const ev = postTrainingEvaluation.value
  if (!ev) return '-'
  return [ev.model_family, ev.model_deployment_id].filter(Boolean).join(' · ') || '-'
})
const postTrainingMetaRows = computed(() => {
  const ev = postTrainingEvaluation.value
  if (!ev) return []
  const metrics = asRecord(ev.metrics)
  const rows = [
    { label: '部署', value: textValue(ev.model_deployment_id) },
    { label: '模型', value: textValue(ev.model_family) || textValue(ev.model_profile) },
    { label: '范围', value: postTrainingScopeLabel(ev.model_scope) },
    { label: '节点', value: textValue(ev.gateway_id) },
    { label: 'token/s', value: textValue(metrics?.tokens_per_second) },
    { label: '生成 tokens', value: textValue(metrics?.generated_tokens) },
    { label: '状态原因', value: textValue(ev.reason) || textValue(ev.error_code) },
  ]
  return rows.filter(row => row.value).slice(0, 8)
})
const structuredSummaryText = computed(() => summaryText(detail.value.structured?.output_summary))
const structuredReasoning = computed(() => stringList(detail.value.structured?.decision_reasoning))
const structuredSuggestedActions = computed(() => stringList(detail.value.structured?.suggested_actions))
const allDispatchesDone = computed(() => {
  const tasks = detail.value.dispatch_tasks || []
  return tasks.length > 0 && tasks.every((t: any) => t.status === 'done')
})
const dispatchAckNotes = computed(() =>
  (detail.value.dispatch_tasks || [])
    .filter((t: any) => t.status === 'done' && t.ack_note)
    .map((t: any) => ({ id: t.id, ack_at: t.ack_at, ack_note: t.ack_note }))
)
const hasStructuredContext = computed(() =>
  Boolean(detail.value.structured?.output_summary || detail.value.structured?.decision_reasoning || detail.value.structured?.suggested_actions?.length)
)
const isVideoAgentCard = computed(() =>
  payload.value.card_type === 'video_low_consumption_decision_card'
  || payload.value.analysis_schema === 'samplebrand_video_low_consumption_daily_operator_v1'
)
const SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID = 'samplebrand-video-low-consumption-operator-v1'
const isSampleBrandLowConsumptionSkill = computed(() => detail.value.request?.skill_id === SAMPLEBRAND_LOW_CONSUMPTION_SKILL_ID)
const hasDecisionCard = computed(() =>
  Boolean(payload.value.card_type === 'product_decline_decision_card' || isVideoAgentCard.value || payload.value.recommended_decision || payload.value.key_metrics || payload.value.analysis_basis)
)
const hasOperationContent = computed(() =>
  operatorActionGroups.value.length > 0 || operationActionRows.value.length > 0 || suggestedActions.value.length > 0 || editingDraft.value
)
const decisionPriority = computed(() => textValue(payload.value.priority))
const decisionConfidence = computed(() => textValue(payload.value.confidence))
const businessActionAllowed = computed(() => payload.value.business_action_allowed === true)
const recommendedDecision = computed(() => textValue(payload.value.recommended_decision) || textValue(outputPayload.value.recommendation))
const paymentChangeIndicator = computed(() => {
  const section = standardCompareTable.value
  const payRow = section?.coreRows.find(row => row.label === '支付金额')
  if (!payRow || !payRow.deltaVal || payRow.deltaVal === '-') return null
  const numeric = Number(String(payRow.deltaVal).replace('%', ''))
  const severity = Number.isFinite(numeric) && numeric < -10 ? 'critical' : Number.isFinite(numeric) && numeric < 0 ? 'medium' : 'low'
  return {
    label: '支付金额变化率',
    value: payRow.deltaVal,
    display_value: payRow.deltaVal,
    severity,
    delta_text: `${payRow.curVal || '-'} / ${payRow.prevVal || '-'}`,
  }
})
const primaryIndicator = computed(() => paymentChangeIndicator.value || normalizedPrimaryIndicator.value || { label: '支付金额变化率', value: '-', severity: '' })
const normalizedPrimaryIndicator = computed(() => {
  const raw = asRecord(payload.value.primary_indicator)
  if (!raw) return null
  const field = textValue(raw.field)
  const label = textValue(raw.label)
  if (field !== 'decline_coefficient' && !label.includes('下滑系数')) return raw
  const numeric = numberValue(raw.numeric_value ?? raw.display_value ?? raw.value)
  if (numeric == null) return raw
  const pct = Math.abs(numeric) <= 1 ? numeric * 100 : numeric
  const display = `${pct < 0 ? '' : '-'}${Math.abs(pct).toFixed(2).replace(/\.?0+$/, '')}%`
  return {
    ...raw,
    label: '支付金额变化率',
    value: display,
    display_value: display,
  }
})
const piSeverityClass = computed(() => {
  const sev = (primaryIndicator.value || {}).severity
  return sev === 'critical' ? 'pi-critical' : sev === 'medium' ? 'pi-medium' : 'pi-normal'
})
const itemId = computed(() => textValue(payload.value.item_id) || textValue(inputPayload.value.item_id))
const itemTitle = computed(() => textValue(payload.value.item_title) || textValue(inputPayload.value.item_title))
const dataTime = computed(() => textValue(payload.value.data_time) || textValue(inputPayload.value.data_time))
const decisionTypeLabel = computed(() => textValue(payload.value.type) || textValue(payload.value.decision_type))
const videoAccountName = computed(() =>
  textValue(payload.value.account_name)
  || textValue(payload.value.person)
  || textValue(inputPayload.value.account_name)
  || textValue(inputPayload.value.person)
)
const videoAnalysisDate = computed(() => textValue(payload.value.analysis_date) || textValue(inputPayload.value.analysis_date))
const videoDetailMarkdown = computed(() =>
  textValue(payload.value.detail_markdown)
  || textValue(payload.value.account_detail_markdown)
  || textValue(payload.value.content_markdown)
)
const renderedVideoDetailMarkdown = computed(() => renderMd(videoDetailMarkdown.value))
const videoTopicText = computed(() => {
  const direct = stringList(payload.value.topic_keys).join('、') || stringList(payload.value.topics).join('、')
  if (direct) return direct
  const topics = new Set<string>()
  for (const raw of arrayValue(payload.value.operation_actions)) {
    const row = asRecord(raw)
    const topic = textValue(row?.topic_key)
    if (topic) topics.add(topic)
  }
  return [...topics].join('、')
})
const heroTitle = computed(() => itemTitle.value || detail.value.request?.title || (isVideoAgentCard.value ? '视频低消耗待办' : '未命名商品'))
const heroDataTime = computed(() => isVideoAgentCard.value ? (videoAnalysisDate.value || dataTime.value) : dataTime.value)
const heroTypeLabel = computed(() => isVideoAgentCard.value ? '视频诊断' : (decisionTypeLabel.value || '决策'))
const heroSummaryText = computed(() => isSampleBrandLowConsumptionSkill.value ? samplebrandDecisionSummary.value : (recommendedDecision.value || detail.value.request?.summary || ''))
const dataQualityText = computed(() => textValue(payload.value.data_quality) || textValue(outputPayload.value.data_quality))
const visibleGapRows = computed(() => showAllGaps.value ? dataQualityRows.value : dataQualityRows.value.slice(0, 6))
const hasMoreGaps = computed(() => dataQualityRows.value.length > 6)

// ── 语义辅助 ──
function severityLabel(sev: string) {
  return sev === 'critical' ? '严重' : sev === 'medium' ? '注意' : '正常'
}
function postTrainingScopeLabel(scope: unknown) {
  const value = textValue(scope)
  if (value === 'shared_platform_history_fallback') return '全平台历史模型兜底'
  if (value === 'target_skill_deployment') return '当前 Skill 专属部署'
  return value
}
function prioritySlug(p: string) {
  return p === 'P0' ? 'mt-danger' : p === 'P1' ? 'mt-warn' : 'mt-info'
}
function confidenceSlug(c: string) {
  if (c.includes('高')) return 'mt-ok'
  if (c.includes('中')) return 'mt-info'
  return 'mt-warn'
}
function deltaToneSlug(s: string) {
  return s === '下降' ? 'mt-danger' : s === '上升' ? 'mt-ok' : 'mt-neutral'
}
function extraStatusSlug(s: string) {
  if (s === 'critical' || s === '严重' || s === '下降') return 'mt-danger'
  if (s === '上升' || s === '昨日同刻已对比') return 'mt-ok'
  if (/未采集|市场实时榜为空|权限不足|安全校验|待补采|待复核|采集失败/.test(s)) return 'mt-warn'
  return 'mt-neutral'
}
function dispatchToneSlug(s: string) {
  return s === 'done' ? 'mt-ok' : s === 'in_progress' ? 'mt-info' : s === 'cancelled' ? 'mt-danger' : 'mt-warn'
}
function todoKindTagColor(kind: string | undefined) {
  return kind === 'dispatch' ? 'arcoblue' : kind === 'review' ? 'orange' : 'gray'
}
function todoStatusTagColor(s: string | undefined) {
  return s === 'pending' ? 'orange' : s === 'approved' ? 'green' : s === 'rejected' ? 'red' : 'gray'
}

/* ── 指标 / 数据 ── */
const paidDetailMetricNames = new Set(['付费商品汇总实时', '付费计划明细'])
const paidDetailBasisLabels = new Set(['关键词推广-低直接ROI计划', '关键词推广-低ROI计划', '关键词推广-低直接ROI/高CPC词', '关键词推广-低ROI/高PPC词', '关键词推广-低效创意'])
const freeSearchFactMetricNames = new Set([
  '免费搜索-访客数',
  '免费搜索-支付买家数',
  '免费搜索-支付转化率',
  '免费搜索-支付金额',
])
const promotedCompareMetricNames = new Set([
  '免费搜索/免费访客环比',
  '搜索/免费转化环比',
  '付费访客环比',
  '付费转化环比',
])
const paidRealtimeSummaryMetricNames = new Set([
  '付费商品汇总实时',
  '关键词推广-商品汇总实时花费/直接ROI/CPC',
])

function normalizeMetricRows(rows: unknown[], prefix = 'metric') {
  return rows
    .filter((raw) => {
      if (!arrayValue(payload.value.paid_flow_analysis_basis).length) return true
      return !paidDetailMetricNames.has(textValue(asRecord(raw)?.name))
    })
    .map((raw, index) => {
      const row = asRecord(raw) || {}
      const status = textValue(row.status)
      return {
        key: `${prefix}-${index}-${textValue(row.name)}`,
        name: textValue(row.name) || '指标',
        value: textValue(row.value) || '-',
        detail: textValue(row.delta) || textValue(row.detail) || textValue(row.note),
        status: status || '-',
        tone: metricTone(status, textValue(row.value)),
      }
    })
}

function compactSearchMetricText(text: string) {
  return text
    .replace(/访客数/g, '访客')
    .replace(/支付买家数/g, '买家')
    .replace(/支付转化率/g, '转化')
    .replace(/支付金额/g, '金额')
    .trim()
}

function promotedMetricFallbackLabel(metricName: string) {
  if (metricName.includes('访客')) return '访客'
  if (metricName.includes('转化')) return '转化'
  if (metricName.includes('买家')) return '买家'
  if (metricName.includes('金额') || metricName.includes('支付')) return '金额'
  return '数值'
}

function splitPromotedMetricItems(text: string, fallbackLabel = '数值') {
  const compact = compactSearchMetricText(text).replace(/：/g, ' ')
  return compact
    .split(/\s*\/\s*/)
    .map(part => part.trim())
    .filter(Boolean)
    .map((part, index) => {
      const match = part.match(/^([^\d¥￥+\-.%]+?)\s*([¥￥]?\s*[-+]?\d[\d,.]*(?:\.\d+)?%?)$/)
      return {
        key: `metric-${index}-${part}`,
        label: match ? match[1].trim() : fallbackLabel,
        value: (match ? match[2] : part).replace(/\s+/g, ''),
      }
    })
}

function parsePromotedPeriodPart(part: string) {
  const timed = part.match(/^(.+?\d{1,2}:\d{2})\s+(.+)$/)
  if (timed) return { period: timed[1], valueText: timed[2] }
  const labeled = part.match(/^(当前24h|前24h|对比24h|当前|对比)\s+(.+)$/)
  if (labeled) return { period: labeled[1], valueText: labeled[2] }
  return { period: '', valueText: part }
}

function splitPromotedPeriodParts(text: string) {
  const timed = text.match(/^(.+?\d{1,2}:\d{2})\s+(.+?)\s+\/\s+(.+?\d{1,2}:\d{2})\s+(.+)$/)
  if (timed) return [`${timed[1]} ${timed[2]}`, `${timed[3]} ${timed[4]}`]
  const labeled = text.match(/^(当前24h|当前)\s+(.+?)\s+\/\s+(前24h|对比24h|对比)\s+(.+)$/)
  if (labeled) return [`${labeled[1]} ${labeled[2]}`, `${labeled[3]} ${labeled[4]}`]
  return []
}

function parsePromotedCompareDetail(text: string, metricName = '') {
  const rawText = text.trim()
  if (!rawText) return null
  const semicolonParts = rawText.split('；').map(part => part.trim()).filter(Boolean)
  const periodParts = semicolonParts.length >= 2 ? semicolonParts.slice(0, 2) : splitPromotedPeriodParts(rawText)
  if (periodParts.length < 2) return null
  const fallbackLabel = promotedMetricFallbackLabel(metricName)
  const lines = periodParts.map((part, index) => {
    const parsed = parsePromotedPeriodPart(part)
    const periodText = parsed.period ? `${parsed.period}：${parsed.valueText}` : part
    return {
      key: `line-${index}`,
      label: index === 0 ? '当前24h' : '前24h',
      text: compactSearchMetricText(periodText),
      metrics: splitPromotedMetricItems(parsed.valueText, fallbackLabel),
    }
  })
  const summary = semicolonParts.length >= 3 ? semicolonParts.slice(2).join('；').replace(/^环比\s*/, '') : ''
  return {
    lines,
    summary: compactSearchMetricText(summary),
    summaryMetrics: summary ? splitPromotedMetricItems(summary.replace(/^环比\s*/, ''), '环比') : [],
  }
}

function compareDetailHasMissing(detail: any) {
  if (!detail) return true
  const lines = Array.isArray(detail.lines) ? detail.lines : []
  return lines.some((line: any) => {
    const metricText = Array.isArray(line?.metrics)
      ? line.metrics.map((metric: any) => `${textValue(metric?.label)} ${textValue(metric?.value)}`).join(' ')
      : ''
    return /未采集|未获取|不可判定/.test(`${textValue(line?.text)} ${metricText}`)
  })
    || /未采集|未获取|不可判定/.test(textValue(detail.summary))
}

function buildPaidRealtimeSummaryRow(row: any) {
  const paidRows = paidRealtimeCompareRows.value
  if (!paidRealtimeSummaryMetricNames.has(textValue(row.label))) return row
  if (!paidRows.length) return { ...row, status: '昨日同刻未采集' }
  const period = paidRealtimeComparePeriod.value
  const hasPrevious = paidRows.some((metric: any) => metric.previousText && metric.previousText !== '未采集' && metric.previousText !== '-')
  return {
    ...row,
    deltaVal: period || row.deltaVal,
    status: hasPrevious ? '昨日同刻已对比' : '昨日同刻未采集',
    paidCompareRows: paidRows,
    paidComparePeriod: period,
  }
}

const keyMetrics = computed(() => {
  const rows = arrayValue(payload.value.key_metrics).length
    ? arrayValue(payload.value.key_metrics)
    : arrayValue(outputPayload.value.metrics)
  return normalizeMetricRows(rows)
})

const standardCompareTable = computed(() => {
  const rawSections = arrayValue(payload.value.metric_sections).length
    ? arrayValue(payload.value.metric_sections)
    : arrayValue(outputPayload.value.metric_sections)
  if (!rawSections.length) return null
  const sec0 = asRecord(rawSections[0]) || {}
  const title = textValue(sec0.title) || ''
  if (!title.startsWith('标准口径')) return null
  const metrics = arrayValue(sec0.metrics)
  if (metrics.length < 3) return null
  const cur = asRecord(metrics[0]) || {}; const prev = asRecord(metrics[1]) || {}; const delta = asRecord(metrics[2]) || {}
  if (textValue(cur.name) !== '—当前24h—' || textValue(prev.name) !== '—对比24h—' || textValue(delta.name) !== '—变化率—') return null
  const curNote = textValue(cur.note) || ''; const prevNote = textValue(prev.note) || ''; const deltaNote = textValue(delta.note) || ''
  const curParts = curNote.split('｜').map(s => s.trim())
  const prevParts = prevNote.split('｜').map(s => s.trim())
  const deltaParts = deltaNote.split('｜').map(s => s.trim())
  const labelMap: Record<string, string> = { '支付': '支付金额', '件数': '支付件数', '转化': '支付转化率', '访客': '商品访客数', '加购': '商品加购件数' }
  const coreRows: any[] = []
  for (let i = 0; i < curParts.length; i++) {
    const lm = curParts[i].match(/^([^\d¥\-+%.]+)/)
    const rawLabel = lm ? lm[1] : `指标${i + 1}`
    const label = labelMap[rawLabel] || rawLabel
    const curVal = curParts[i].replace(rawLabel, '').trim()
    const rawPrevVal = prevParts[i] ? prevParts[i].replace(rawLabel, '').trim() : ''
    const prevVal = rawPrevVal || '未获取'
    const deltaVal = deltaParts[i] ? deltaParts[i].replace(rawLabel, '').trim() : ''
    const dn = parseFloat(deltaVal)
    const prevUnavailable = prevVal === '未获取'
    const status = prevUnavailable ? '未获取' : deltaVal === '0.0%' || deltaVal === '0' ? '正常' : !isNaN(dn) && dn < 0 ? '下降' : !isNaN(dn) && dn > 0 ? '上升' : textValue(delta.status) || '-'
    coreRows.push({ label, curVal, prevVal, deltaVal: prevUnavailable ? '-' : deltaVal, status, key: `std-${i}` })
  }
  const extraRows: any[] = []
  const promotedRows: any[] = []
  const freeSearchFactRows: any[] = []
  for (let i = 3; i < metrics.length; i++) {
    const m = asRecord(metrics[i]) || {}
    const name = textValue(m.name) || ''
    if (!name || name.startsWith('—')) continue
    const row = {
      label: name,
      curVal: textValue(m.value) || '-',
      prevVal: '未获取',
      deltaVal: textValue(m.delta) || textValue(m.note) || textValue(m.status) || '-',
      status: textValue(m.status) || '-',
      source: textValue(m.source),
      key: `std-extra-${i}`,
      tone: metricTone(textValue(m.status) || '-', textValue(m.value) || '-'),
      compareDetail: parsePromotedCompareDetail(textValue(m.delta) || textValue(m.note) || '', name),
    }
    if (freeSearchFactMetricNames.has(name)) freeSearchFactRows.push(row)
    else if (promotedCompareMetricNames.has(name)) promotedRows.push(row)
    else extraRows.push(buildPaidRealtimeSummaryRow(row))
  }
  return { title, coreRows, promotedRows, extraRows, freeSearchFactRows }
})

const freeSearchRealtimeFacts = computed(() => {
  const rows = standardCompareTable.value?.freeSearchFactRows || []
  if (!rows.length) return null
  const byLabel = new Map(rows.map((row: any) => [row.label, row]))
  const items = [
    { key: 'visitor', label: '访客', row: byLabel.get('免费搜索-访客数') },
    { key: 'buyer', label: '买家', row: byLabel.get('免费搜索-支付买家数') },
    { key: 'conversion', label: '转化率', row: byLabel.get('免费搜索-支付转化率') },
    { key: 'amount', label: '支付金额', row: byLabel.get('免费搜索-支付金额') },
  ]
    .map(item => ({
      key: item.key,
      label: item.label,
      value: textValue(item.row?.curVal) || textValue(item.row?.value) || '-',
    }))
    .filter(item => item.value && item.value !== '-' && item.value !== '未采集' && item.value !== '待补采')
  if (!items.length) return null
  const sourceRow = rows.find((row: any) => textValue(row.source)) || rows[0]
  const promotedRows = standardCompareTable.value?.promotedRows || []
  const freeCompareRows = promotedRows.filter((row: any) => /免费搜索|搜索\/免费/.test(textValue(row.label)))
  const compareRows = freeCompareRows.length ? freeCompareRows : promotedRows
  const hasCollectedCompare = compareRows.some((row: any) => row.compareDetail && !compareDetailHasMissing(row.compareDetail))
  const prevMissing = hasCollectedCompare
    ? false
    : compareRows.length
      ? compareRows.some((row: any) => row.compareDetail
      ? compareDetailHasMissing(row.compareDetail)
      : /未采集|未获取|不可判定/.test(`${row.deltaVal || ''} ${row.status || ''}`))
      : rows.some((row: any) => /未采集|未获取|不可判定/.test(`${row.deltaVal || ''} ${row.status || ''}`))
  return {
    items,
    source: textValue(sourceRow?.source) || '生意参谋-商品360流量来源-搜索节点实时数据',
    compareNote: prevMissing ? '前24h 未采集，环比不可判定' : '前24h 已采集，环比见上方口径',
  }
})

const metricSections = computed(() => {
  const rawSections = arrayValue(payload.value.metric_sections).length
    ? arrayValue(payload.value.metric_sections)
    : arrayValue(outputPayload.value.metric_sections)
  const skipFirst = standardCompareTable.value !== null
  const sections = rawSections.map((raw, index) => {
    if (skipFirst && index === 0) return null
    const section = asRecord(raw) || {}
    const metrics = normalizeMetricRows(arrayValue(section.metrics), `section-${index}`)
    return { key: `${index}-${textValue(section.key) || textValue(section.title) || 'section'}`, title: textValue(section.title) || '指标分组', metrics }
  }).filter((s): s is NonNullable<typeof s> => s !== null && s.metrics.length > 0)
  if (sections.length) return sections
  return keyMetrics.value.length
    ? [{ key: 'default', title: '关键指标', metrics: keyMetrics.value }]
    : []
})

function normalizeCompareRows(raw: unknown, prefix: string) {
  const compare = asRecord(raw)
  const rows = arrayValue(compare?.metrics)
  return rows.map((item, index) => {
    const row = asRecord(item) || {}
    const changeText = textValue(row.change_text) || '-'
    return {
      key: `${prefix}-${textValue(row.key) || index}`,
      label: textValue(row.label) || '指标',
      currentText: textValue(row.current_text) || '未采集',
      previousText: textValue(row.previous_text) || '未采集',
      changeText,
      tone: changeText.startsWith('-') ? 'down' : changeText.startsWith('+') ? 'up' : 'neutral',
    }
  }).filter(row => row.currentText !== '未采集' || row.previousText !== '未采集')
}

function comparePeriodText(raw: unknown) {
  const period = asRecord(asRecord(raw)?.period)
  const current = textValue(period?.current_label)
  const previous = textValue(period?.compare_label)
  if (current && previous) return `${current} / ${previous}`
  return current || previous || ''
}

const freeSearchCompareRows = computed(() => normalizeCompareRows(payload.value.free_search_compare, 'free-search-compare'))
const freeSearchComparePeriod = computed(() => comparePeriodText(payload.value.free_search_compare))

function signedPercentText(v: number | null) {
  if (v == null) return '-'
  const rounded = Math.round(v * 100) / 100
  return `${rounded > 0 ? '+' : ''}${rounded}%`
}

function previousFromChange(current: number | null, changePct: number | null) {
  if (current == null || changePct == null) return null
  const denominator = 1 + changePct / 100
  if (!Number.isFinite(denominator) || Math.abs(denominator) < 0.000001) return null
  return current / denominator
}

function previousFromDelta(current: number | null, delta: number | null) {
  if (current == null || delta == null) return null
  return current - delta
}

function paidSummaryPeriod() {
  for (const raw of arrayValue(payload.value.paid_flow_analysis_basis)) {
    const row = asRecord(raw)
    const period = asRecord(row?.period)
    if (period && (textValue(period.current_label) || textValue(period.compare_label))) return period
  }
  return null
}

function paidSummaryCompareRows() {
  const paidDetail = asRecord(asRecord(payload.value.decision_context)?.paid_detail)
  const summary = asRecord(paidDetail?.product_summary)
  if (!summary) return []
  const specs = [
    { key: 'charge', label: '实时花费金额', currentKey: 'charge', previousKey: 'charge_prev', changeKey: 'charge_change_pct', formatter: moneyText },
    { key: 'roi', label: 'ROI', currentKey: 'roi', previousKey: 'roi_prev', deltaKey: 'roi_delta', changeKey: 'roi_change_pct', formatter: metricNumberText },
    { key: 'cpc', label: 'CPC', currentKey: 'cpc', previousKey: 'cpc_prev', deltaKey: 'cpc_delta', changeKey: 'cpc_change_pct', formatter: moneyText },
  ]
  return specs.map(spec => {
    const current = numberValue(summary[spec.currentKey])
    const change = numberValue(summary[spec.changeKey])
    const previous = numberValue(summary[spec.previousKey])
      ?? previousFromDelta(current, numberValue(spec.deltaKey ? summary[spec.deltaKey] : null))
      ?? previousFromChange(current, change)
    const changeText = signedPercentText(change)
    return {
      key: `paid-summary-${spec.key}`,
      label: spec.label,
      currentText: spec.formatter(current),
      previousText: spec.formatter(previous),
      changeText,
      tone: changeText.startsWith('-') ? 'down' : changeText.startsWith('+') ? 'up' : 'neutral',
    }
  }).filter(row => row.currentText !== '-' || row.previousText !== '-')
}

const paidRealtimeCompareRows = computed(() => {
  const explicitRows = normalizeCompareRows(payload.value.paid_realtime_compare, 'paid-realtime-compare')
  return explicitRows.length ? explicitRows : paidSummaryCompareRows()
})
const paidRealtimeComparePeriod = computed(() => {
  const explicitPeriod = comparePeriodText(payload.value.paid_realtime_compare)
  if (explicitPeriod) return explicitPeriod
  const period = paidSummaryPeriod()
  const current = textValue(period?.current_label)
  const previous = textValue(period?.compare_label)
  if (current && previous) return `${current} / ${previous}`
  return current || previous || ''
})

function normalizeBasisRows(rows: unknown[], prefix: string) {
  return rows.map((raw, index) => {
    const row = asRecord(raw) || {}
    const label = textValue(row.label) || textValue(row.name) || textValue(row.dimension) || '指标'
    const status = textValue(row.status)
    const source = textValue(row.source)
    const period = asRecord(row.period)
    const currentLabel = textValue(period?.current_label)
    return {
      key: `${prefix}-${index}-${label}`,
      label,
      value: textValue(row.value) || '-',
      detail: textValue(row.detail) || textValue(row.delta) || textValue(row.note),
      status: status || '-',
      source,
      meta: [source, currentLabel].filter(Boolean).join(' · '),
      tone: metricTone(status, textValue(row.value)),
      raw: row,
    }
  })
}

const paidFlowRows = computed(() =>
  normalizeBasisRows(arrayValue(payload.value.paid_flow_analysis_basis), 'paid-flow')
    .filter(row => !paidDetailBasisLabels.has(row.label))
)
const paidDetailRows = computed(() => {
  const rows = normalizeBasisRows(arrayValue(payload.value.paid_flow_analysis_basis), 'paid-detail')
    .filter(row => paidDetailBasisLabels.has(row.label) && (row.value !== '0' || row.detail || row.status !== '-'))
    .map(row => ({
      key: row.key,
      label: row.label,
      text: [row.value, row.detail, row.status !== '-' ? row.status : ''].filter(Boolean).join(' · '),
    }))
  const paidDetail = asRecord(asRecord(payload.value.decision_context)?.paid_detail)
  if (!paidDetail) return rows
  const budgetStatus = textValue(paidDetail.budget_status)
  if (budgetStatus) rows.push({ key: 'paid-budget-status', label: '预算/计划状态', text: budgetStatus })
  return rows
})
function meaningfulPlanStatus(row: Record<string, any>) {
  const diagnosis = textValue(row.diagnosis)
  if (diagnosis) return diagnosis
  const status = textValue(row.status)
  return /^(0|1|2|3|4|5)$/.test(status) ? '' : status
}
const paidVisiblePlanRows = computed(() => {
  const paidDetail = asRecord(asRecord(payload.value.decision_context)?.paid_detail)
  const plans = arrayValue(paidDetail?.plan_details)
  return plans.map((raw, index) => {
    const row = asRecord(raw) || {}
    const charge = numberValue(row.charge)
    const roi = numberValue(row.roi)
    const cpc = numberValue(row.ppc)
    const status = meaningfulPlanStatus(row)
    return {
      key: `paid-plan-${index}-${textValue(row.plan_id)}`,
      name: textValue(row.plan_name) || textValue(row.plan_id) || `计划 ${index + 1}`,
      charge: moneyText(charge),
      roi: metricNumberText(roi),
      cpc: moneyText(cpc),
      status: status || '-',
      hasSignal: Boolean((charge && charge > 0) || (roi && roi > 0) || (cpc && cpc > 0) || status),
    }
  }).filter(row => row.hasSignal).slice(0, 8)
})
const paidPlanGapText = computed(() => {
  const paidDetail = asRecord(asRecord(payload.value.decision_context)?.paid_detail)
  const plans = arrayValue(paidDetail?.plan_details)
  if (!plans.length || paidVisiblePlanRows.value.length) return ''
  const status = textValue(paidDetail?.paid_detail_status)
  const budget = textValue(paidDetail?.budget_status)
  return [status || '已采集计划列表，但计划级花费/直接ROI/CPC未返回', budget].filter(Boolean).join(' · ')
})
const freeFlowRows = computed(() => normalizeBasisRows(arrayValue(payload.value.free_flow_analysis_basis), 'free-flow'))
const rootCauseRows = computed(() => arrayValue(payload.value.root_cause_ranking).map((raw, index) => {
  const row = asRecord(raw) || {}
  return {
    key: `${index}-${textValue(row.dimension)}`,
    rank: textValue(row.rank) || String(index + 1),
    role: textValue(row.role),
    score: textValue(row.score),
    priority: textValue(row.priority),
    dimension: textValue(row.dimension) || '原因',
    evidence: textValue(row.evidence),
    actions: Array.isArray(row.actions) ? (row.actions as string[]) : [],
  }
}))

const operationActionRows = computed(() => {
  const rows = normalizeActionRows(payload.value.operation_actions, 'operation')
  return rows.length ? rows : normalizeActionRows(payload.value.top_actions, 'top-action')
})
const operatorActionGroups = computed(() => {
  const rows = normalizeOperatorActionRows(payload.value.operation_actions)
  const groups: Array<{ section: string; rows: ReturnType<typeof normalizeOperatorActionRows> }> = []
  for (const row of rows) {
    let group = groups.find(item => item.section === row.section)
    if (!group) {
      group = { section: row.section, rows: [] }
      groups.push(group)
    }
    group.rows.push(row)
  }
  return groups
})
const videoActionRows = computed(() => normalizeVideoActionRows(payload.value.operation_actions))
const samplebrandPrimaryAction = computed(() => videoActionRows.value[0] || null)
const samplebrandTopicText = computed(() => (
  textValue(samplebrandPrimaryAction.value?.theme_name) ||
  textValue(samplebrandPrimaryAction.value?.topic_key) ||
  videoTopicText.value
))
const samplebrandDecisionSummary = computed(() => {
  const account = videoAccountName.value
  const date = videoAnalysisDate.value || heroDataTime.value
  const count = textValue(asRecord(payload.value.primary_indicator)?.value) || metricValueByName('低消耗视频数') || '1'
  const topic = samplebrandTopicText.value
  if (account && topic) return `${account}账号 ${date || ''} 有 ${count} 条低消耗视频建议关注；主题为 ${topic}；可按下方建议派发轻改。`
  return detail.value.request?.summary || recommendedDecision.value || '-'
})
const samplebrandDecisionFacts = computed(() => compactFacts([
  { label: '账号', value: videoAccountName.value },
  { label: '分析日期', value: videoAnalysisDate.value || heroDataTime.value },
  { label: '建议关注', value: metricValueByName('低消耗视频数') || textValue(asRecord(payload.value.primary_indicator)?.value) },
  { label: '主题范围', value: samplebrandTopicText.value },
  { label: '数据质量', value: dataQualityText.value },
]))
const samplebrandVideoFacts = computed(() => compactFacts([
  { label: '视频', value: textValue(samplebrandPrimaryAction.value?.scenario) },
  { label: '主题', value: textValue(samplebrandPrimaryAction.value?.theme_name) || textValue(samplebrandPrimaryAction.value?.topic_key) || samplebrandTopicText.value },
  { label: '剪辑人', value: textValue(samplebrandPrimaryAction.value?.editor_code) },
  { label: '产品', value: textValue(samplebrandPrimaryAction.value?.product_code) },
  { label: '分型', value: textValue(samplebrandPrimaryAction.value?.dimension) },
  { label: '优先级', value: textValue(samplebrandPrimaryAction.value?.priority) || decisionPriority.value },
  { label: 'video_id', value: textValue(samplebrandPrimaryAction.value?.video_id) },
  { label: '参考等级', value: textValue(samplebrandPrimaryAction.value?.benchmark_label) || textValue(samplebrandPrimaryAction.value?.benchmark_level) },
  { label: '参考样本', value: textValue(samplebrandPrimaryAction.value?.benchmark_title) },
  { label: '参考剪辑人', value: textValue(samplebrandPrimaryAction.value?.benchmark_editor_code) },
]))
const samplebrandReasonRows = computed(() => {
  const rows: string[] = []
  const selectionReason = textValue(samplebrandPrimaryAction.value?.benchmark_selection_reason)
  if (selectionReason) rows.push(`为什么这样对标：${selectionReason}`)
  const similarityText = textValue(samplebrandPrimaryAction.value?.benchmark_similarity_text)
  if (similarityText) rows.push(`相似参考依据：${similarityText}`)
  const benchmarkWarning = textValue(samplebrandPrimaryAction.value?.benchmark_warning)
  if (benchmarkWarning) rows.push(`对标说明：${benchmarkWarning}`)
  const lifecyclePoints = asRecord(samplebrandPrimaryAction.value?.qianchuan_lifecycle_value_points)
  const managerValue = textValue(lifecyclePoints?.manager_value)
  const consumerValue = textValue(lifecyclePoints?.consumer_value)
  if (managerValue) rows.push(`管理者价值：${managerValue}`)
  if (consumerValue) rows.push(`消费者触发点：${consumerValue}`)
  for (const metric of arrayValue(payload.value.key_metrics)) {
    const row = asRecord(metric)
    const name = textValue(row?.name)
    if (!name || ['Agent 分型', '首条视频', '证据门槛'].includes(name)) continue
    const value = textValue(row?.value)
    const status = textValue(row?.status)
    const detailText = textValue(row?.delta)
    rows.push([`${name}：${value}`, status, detailText].filter(Boolean).join('｜'))
  }
  for (const item of samplebrandPrimaryAction.value?.root_causes || []) {
    let text = textValue(item)
    if (!text || /低消耗画面证据|参考视频画面证据|同主题好视频画面证据/.test(text)) continue
    if (text.startsWith('分型：')) text = text.includes('。') ? text.split('。').slice(1).join('。').trim() : ''
    if (text) rows.push(text)
  }
  return uniqueTextList(rows, 6, 360)
})
const samplebrandLifecycleCards = computed(() => {
  const points = asRecord(samplebrandPrimaryAction.value?.qianchuan_lifecycle_value_points)
  if (!points) return []
  return [
    { title: '管理者价值', text: textValue(points.manager_value) },
    { title: '消费者触发点', text: textValue(points.consumer_value) },
    { title: '优化焦点', text: textValue(points.optimization_focus) },
  ].filter(card => card.text)
})
const samplebrandChartSections = computed(() => normalizeSampleBrandChartSections(payload.value.chart_sections, videoActionRows.value))
const samplebrandActionItems = computed(() =>
  uniqueTextList(samplebrandPrimaryAction.value?.actions || [], 4, 420)
    .filter(item => !item.startsWith('复盘输出建议'))
)
const samplebrandVisualCards = computed(() => {
  const cards = []
  const low = textValue(samplebrandPrimaryAction.value?.low_visual_evidence)
  const benchmark = textValue(samplebrandPrimaryAction.value?.benchmark_visual_evidence)
  if (low) cards.push({ title: '当前视频画面', text: low })
  if (benchmark) cards.push({ title: '参考视频画面', text: benchmark })
  return cards
})
const samplebrandRetestText = computed(() => textValue(payload.value.verification_metrics) || textValue(payload.value.retest_metrics) || '上线后用消耗、点击率、3秒播放率、转化率、ROI、成交金额复测。')
const samplebrandForbiddenActions = computed(() =>
  forbiddenActions.value.filter(item => !/视觉证据|完整视频/.test(item)).slice(0, 4)
)
const samplebrandDispatchSummaries = computed(() =>
  (detail.value.dispatch_tasks || []).slice(0, 5).map((task, index) => {
    const extra = asRecord(task.extra)
    const output = textValue(extra?.required_output) || textValue(extra?.deliverable) || '提交改版视频方向、复用的高质量样本、复测指标。'
    const account = textValue(extra?.account_name) || textValue(extra?.account) || videoAccountName.value
    return {
      key: `samplebrand-dispatch-${task.id || index}`,
      index: index + 1,
      output,
      account,
      executor: dispatchExecutorLabel(task),
      status: dispatchStatusLabel(task.status),
    }
  })
)

const dataQualityRows = computed(() => {
  const explicit = arrayValue(payload.value.data_quality_items)
    .map((raw, i) => {
      const row = asRecord(raw) || {}
      return { key: `${i}-${textValue(row.dimension)}`, dimension: textValue(row.dimension) || '数据缺口', issue: textValue(row.issue) || textValue(row.detail) || textValue(row.data_gap) || '-' }
    })
    .filter(r => r.issue && r.issue !== '-')
  if (explicit.length) return explicit
  return analysisBasis.value
    .filter(r => r.data_gap)
    .map((r, i) => ({ key: `gap-${i}-${r.dimension}`, dimension: r.dimension, issue: r.data_gap }))
})

const analysisBasis = computed(() =>
  arrayValue(payload.value.analysis_basis).slice(0, 6).map((raw, index) => {
    const row = asRecord(raw) || {}
    return { key: `${index}-${textValue(row.dimension)}`, dimension: textValue(row.dimension) || '分析维度', basis: textValue(row.basis) || '-', data_gap: textValue(row.data_gap), confidence: textValue(row.confidence) }
  })
)
const analysisBasisRows = computed(() => rootCauseRows.value.length ? [] : analysisBasis.value)
const suggestedActions = computed(() => {
  for (const c of [payload.value.operation_actions, payload.value.suggested, outputPayload.value.suggestions]) {
    const items = stringList(c); if (items.length) return items
  }
  return []
})
const forbiddenActions = computed(() => stringList(payload.value.forbidden_actions))
const dataSourceRows = computed(() => normalizeDataSourceRows(payload.value.data_source_links, stringList(payload.value.data_sources).slice(0, 12)))

const callbackLabel = computed(() => ({ pending: '等待发送', sent: '已通知', failed: '失败', skipped: '无' } as Record<string, string>)[detail.value.request?.callback_status || ''] || '-')
const callbackTagColor = computed(() => ({ pending: 'gray', sent: 'green', failed: 'red', skipped: 'gray' } as Record<string, string>)[detail.value.request?.callback_status || ''] || 'gray')

// ── 状态 ──
const timelineItems = ref<any[]>([])
let deferredLoadTimer: number | null = null
const editingDraft = ref(false)
const savingDraft = ref(false)
const showAllGaps = ref(true)
const assignableUsers = ref<Array<{ id: string; name?: string; role?: string; department?: string; dingtalk_bound?: boolean }>>([])
const assignableUsersError = ref('')
const assigningTaskId = ref<number | null>(null)
const assignSelections = ref<Record<number, string>>({})
const draft = ref({ recommended_decision: '', suggested_actions: [] as string[], forbidden_actions: [] as string[], dispatch_tasks: [] as Array<{ id: number; executor: string; content: string; deadline: string }> })
const canEditDraft = computed(() => ['pending', 'approved'].includes(detail.value.todo?.status || '') && detail.value.request?.aggregate_decision !== 'rejected')

// ── 数据加载 ──
async function loadDetail() {
  if (!Number.isFinite(todoId.value)) return
  cancelDeferredDetailLoads()
  showDebugPayload.value = false
  timelineItems.value = []
  loading.value = true
  try {
    detail.value = await todoApi.get(todoId.value) as InboxTodoDetailResponse
    if (!editingDraft.value) resetDraft()
    scheduleDeferredDetailLoads()
  } catch (error: any) {
    Message.error(error._message || '加载待办详情失败')
  } finally { loading.value = false }
}

function scheduleDeferredDetailLoads() {
  const run = () => {
    void loadAssignableUsers()
    void loadRelatedTimeline()
  }
  nextTick(() => {
    deferredLoadTimer = window.setTimeout(run, 0)
  })
}

function cancelDeferredDetailLoads() {
  if (deferredLoadTimer != null) {
    clearTimeout(deferredLoadTimer)
    deferredLoadTimer = null
  }
}

async function loadRelatedTimeline() {
  try {
    const data: any = await todoApi.relatedTimeline(todoId.value, 10)
    timelineItems.value = Array.isArray(data?.items) ? data.items : []
  } catch {
    timelineItems.value = []
  }
}

async function loadAssignableUsers() {
  assignableUsers.value = []; assignableUsersError.value = ''; assignSelections.value = {}
  if (detail.value.request?.kind !== 'dispatch' || !detail.value.request?.id) return
  try {
    const data = await todoApi.listAssignableUsers({ request_id: detail.value.request.id }) as Array<Record<string, unknown>>
    assignableUsers.value = Array.isArray(data) ? data.map(item => ({
      id: String(item.id || ''), name: String(item.name || ''),
      role: String(item.role || ''), department: String(item.department || ''),
      dingtalk_bound: Boolean(item.dingtalk_bound),
    })).filter(item => item.id) : []
  } catch (error: any) { assignableUsersError.value = error?._message || '可选执行人加载失败'; assignableUsers.value = [] }
  for (const task of detail.value.dispatch_tasks || []) {
    const executorId = effectiveDispatchExecutorId(task)
    if (executorId) assignSelections.value[task.id] = executorId
  }
}

// ── 草稿 ──
function resetDraft() {
  draft.value = {
    recommended_decision: recommendedDecision.value || detail.value.request?.summary || '',
    suggested_actions: suggestedActions.value.length ? [...suggestedActions.value] : [''],
    forbidden_actions: [...forbiddenActions.value],
    dispatch_tasks: (detail.value.dispatch_tasks || []).map(task => ({
      id: task.id, executor: effectiveDispatchExecutorId(task), content: task.content || '', deadline: task.deadline || '',
    })),
  }
}
function startDraftEdit() { resetDraft(); editingDraft.value = true }
function cancelDraftEdit() { editingDraft.value = false; resetDraft() }
function addDraftAction() { draft.value.suggested_actions.push('') }
function removeDraftAction(i: number) { draft.value.suggested_actions.splice(i, 1) }
function addDraftForbidden() { draft.value.forbidden_actions.push('') }
function removeDraftForbidden(i: number) { draft.value.forbidden_actions.splice(i, 1) }
function draftTask(record: InboxDispatchTask) {
  let t = draft.value.dispatch_tasks.find(item => item.id === record.id)
  if (!t) { t = { id: record.id, executor: effectiveDispatchExecutorId(record), content: record.content || '', deadline: record.deadline || '' }; draft.value.dispatch_tasks.push(t) }
  return t
}
function updateDraftTaskField(record: InboxDispatchTask, field: 'executor' | 'content' | 'deadline', value: string) { draftTask(record)[field] = value }
async function saveDraftEdit() {
  savingDraft.value = true
  try {
    await todoApi.updateDraft(todoId.value, {
      recommended_decision: draft.value.recommended_decision,
      suggested_actions: draft.value.suggested_actions.map(s => s.trim()).filter(Boolean),
      forbidden_actions: draft.value.forbidden_actions.map(s => s.trim()).filter(Boolean),
      dispatch_tasks: draft.value.dispatch_tasks.map(t => ({ id: t.id, executor: t.executor, content: t.content, deadline: t.deadline || null })),
    })
    Message.success('待办已更新'); editingDraft.value = false; await loadDetail()
  } catch (error: any) { Message.error(error._message || '保存待办失败') } finally { savingDraft.value = false }
}

// ── 审批 ──
async function promptRejectReason(): Promise<string | null> {
  const reason = ref('')
  return new Promise(resolve => {
    Modal.open({
      title: '驳回理由',
      content: () => h('div', { style: 'padding-top:4px' }, [
        h('p', { style: 'margin:0 0 8px;color:var(--sf-ink-3)' }, '请简要说明驳回原因：'),
        h(Input.TextArea as any, {
          modelValue: reason.value, placeholder: '必填，最多500字', maxLength: 500,
          autoSize: { minRows: 3, maxRows: 6 },
          'onUpdate:modelValue': (val: string) => { reason.value = val },
        }),
      ]),
      okText: '确认驳回', cancelText: '取消',
      onBeforeOk: () => {
        const t = reason.value.trim(); if (!t) { Message.warning('请填写驳回理由'); return false }
        resolve(t); return true
      },
      onCancel: () => resolve(null),
    })
  })
}
function missingDispatchExecutors(tasks: InboxDispatchTask[]) {
  return tasks.filter(t => !String(t.executor || '').trim() && !['done', 'cancelled'].includes(String(t.status || '')))
}
function actionableDispatchTasks(tasks: InboxDispatchTask[]) {
  return tasks.filter(t => !['done', 'cancelled'].includes(String(t.status || '')))
}
function requiresDispatchSelection(sourceType: unknown) { return String(sourceType || '').startsWith('skill_execution_') }
function assignableDepartmentLabel(users: Array<{ department?: string }>) {
  const depts = [...new Set(users.map(u => String(u.department || '').trim()).filter(Boolean))]
  if (!depts.length) return '当前部门'; if (depts.length === 1) return depts[0]; return `${depts[0]} 等 ${depts.length} 个部门`
}

async function ensureDispatchExecutorsForApproval(): Promise<boolean> {
  if (detail.value.request?.kind !== 'dispatch') return true
  const confirmAll = requiresDispatchSelection(detail.value.request?.source_type)
  const candidates = confirmAll ? actionableDispatchTasks(detail.value.dispatch_tasks || []) : missingDispatchExecutors(detail.value.dispatch_tasks || [])
  if (!candidates.length) return true
  if (!assignableUsers.value.length) {
    Message.warning(assignableUsersError.value || (confirmAll ? '当前部门没有可用执行人' : '当前部门没有可用执行人，请先补充成员归属'))
    return false
  }
  const selections = ref<Record<number, string>>(
    Object.fromEntries(candidates.map(task => {
      const assigned = String(task.executor || '').trim()
      const fallback = String(task.default_executor || '').trim()
      const preset = isAssignableExecutor(assigned) ? assigned : (isAssignableExecutor(fallback) ? fallback : '')
      return [task.id, preset || (assignableUsers.value.length === 1 ? assignableUsers.value[0].id : '')]
    }))
  )
  return new Promise(resolve => {
    Modal.open({
      title: confirmAll ? '确认派发执行人' : '选择当前部门执行人', okText: confirmAll ? '确认并通过' : '保存并通过', cancelText: '取消', width: 680,
      content: () => h('div', { class: 'dispatch-modal' }, [
        h('div', { class: 'dispatch-modal-head' }, [
          h('p', { class: 'dispatch-modal-tip' }, confirmAll ? 'Skill生成的派发任务需由平台确认执行人后才能通过。' : '通过前请从当前部门组织架构中选择执行人。'),
          h('div', { class: 'dispatch-modal-meta' }, [
            h(ATag, { size: 'small', color: 'arcoblue' }, () => assignableDepartmentLabel(assignableUsers.value)),
            h(ATag, { size: 'small', color: 'green' }, () => `可选 ${assignableUsers.value.length} 人`),
          ]),
        ]),
        ...candidates.map((task, idx) => h('div', { key: task.id, class: 'dispatch-modal-row' }, [
          h('div', { class: 'dispatch-modal-summary' }, [
            h('strong', `任务 ${idx + 1}`),
            h('span', String(task.content || '').slice(0, 120) || '未填写任务内容'),
          ]),
          h(ASelect, {
            class: 'dispatch-modal-select', modelValue: selections.value[task.id] || undefined,
            placeholder: '选择本部门成员', allowSearch: true,
            onChange: (val: any) => { selections.value = { ...selections.value, [task.id]: String(val || '') } },
          }, { default: () => assignableUsers.value.map(u => h(AOption, { key: u.id, value: u.id }, () => assignableUserLabel(u))) }),
        ])),
      ]),
      onBeforeOk: async () => {
        const missing = candidates.find(t => !String(selections.value[t.id] || '').trim())
        if (missing) { Message.warning(confirmAll ? '请先确认每条派发任务的执行人' : '请先为每条派发任务选择执行人'); return false }
        try { await todoApi.updateDraft(todoId.value, { dispatch_tasks: candidates.map(t => ({ id: t.id, executor: selections.value[t.id] })) }); await loadDetail(); resolve(true); return true }
        catch (error: any) { Message.error(error?._message || '保存执行人失败'); return false }
      },
      onCancel: () => resolve(false),
    })
  })
}

async function decide(decision: 'approved' | 'rejected') {
  let reason = ''
  if (decision === 'rejected') { const r = await promptRejectReason(); if (r === null) return; reason = r }
  if (decision === 'approved' && !(await ensureDispatchExecutorsForApproval())) return
  try { await todoApi.decide(todoId.value, { decision, reason }); Message.success(decision === 'approved' ? '已通过' : '已驳回'); await loadDetail() }
  catch (error: any) { Message.error(error._message || '操作失败') }
}
function extendSla() {
  Modal.confirm({
    title: '确认延期', content: '将 SLA 截止时间延后 24 小时，是否继续？',
    onOk: async () => { try { await todoApi.extendSla(todoId.value, { hours: 24 }); Message.success('已延期 24 小时'); await loadDetail() } catch (error: any) { Message.error(error._message || '延期失败') } },
  })
}
function openRelatedReport(reportId: string) { router.push(`/inbox/reports/${encodeURIComponent(reportId)}`) }
function openSourceUrl(url: string) { if (!url) return; window.open(url, '_blank', 'noopener,noreferrer') }

// ── 派发 ──
function assignableUserLabel(u: { id: string; name?: string; role?: string; department?: string; dingtalk_bound?: boolean }) {
  return [u.name || u.id, u.department || '', u.role || '', u.dingtalk_bound ? '已绑钉钉' : '未绑钉钉'].filter(Boolean).join(' · ')
}
function isAssignableExecutor(executorId?: string | null) {
  const id = String(executorId || '').trim()
  return !!id && assignableUsers.value.some(u => u.id === id)
}
function effectiveDispatchExecutorId(record: InboxDispatchTask) {
  const assigned = String(record.executor || '').trim()
  if (assigned) return assigned
  const fallback = String(record.default_executor || '').trim()
  return fallback
}
function dispatchExecutorName(executorId: string, record?: InboxDispatchTask) {
  const user = assignableUsers.value.find(u => u.id === executorId)
  if (user) return assignableUserLabel(user)
  if (record?.executor === executorId && record.executor_name) {
    return [record.executor_name, record.executor_department || ''].filter(Boolean).join(' · ')
  }
  if (record?.default_executor === executorId && record.default_executor_name) {
    return [record.default_executor_name, record.default_executor_department || ''].filter(Boolean).join(' · ')
  }
  return executorId
}
function dispatchExecutorLabel(record: InboxDispatchTask) {
  const assigned = String(record.executor || '').trim()
  if (assigned) return dispatchExecutorName(assigned, record)
  const fallback = effectiveDispatchExecutorId(record)
  if (fallback) return `${dispatchExecutorName(fallback, record)} · 默认派发人`
  return '待指定'
}
function canAssignDispatchTask(record: InboxDispatchTask) {
  if (detail.value.request?.kind !== 'dispatch') return false
  if (detail.value.request?.aggregate_decision !== 'approved') return false
  return ['pending_assignment', 'awaiting_dispatch', 'sent', 'pushed_no_dingtalk'].includes(record.status)
}
async function assignDispatchTask(record: InboxDispatchTask) {
  const eid = String(assignSelections.value[record.id] || '').trim()
  if (!eid) { Message.warning('请先选择执行人'); return }
  assigningTaskId.value = record.id
  try { await todoApi.assignDispatchTask(record.id, { executor_id: eid }); Message.success(record.executor ? '已重新指派' : '已完成指派'); await loadDetail() }
  catch (error: any) { Message.error(error._message || '指派失败') }
  finally { assigningTaskId.value = null }
}

// ── 工具 ──
function asRecord(v: unknown): Record<string, any> | null { return v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, any> : null }
function arrayValue(v: unknown): unknown[] { return Array.isArray(v) ? v : [] }
function textValue(v: unknown): string { if (v == null) return ''; if (typeof v === 'string') return v.trim(); if (typeof v === 'number' || typeof v === 'boolean') return String(v); return '' }
function compactFacts(rows: Array<{ label: string; value: unknown }>) {
  return rows
    .map(row => ({ label: row.label, value: textValue(row.value) }))
    .filter(row => row.value)
}
function uniqueTextList(items: unknown[], limit = 8, textLimit = 700) {
  const result: string[] = []
  const seen = new Set<string>()
  for (const item of items) {
    const text = textValue(item).slice(0, textLimit).trim()
    const key = text.replace(/\s+/g, ' ')
    if (!key || seen.has(key)) continue
    seen.add(key)
    result.push(text)
    if (result.length >= limit) break
  }
  return result
}
function metricValueByName(name: string) {
  for (const raw of arrayValue(payload.value.key_metrics)) {
    const row = asRecord(raw)
    if (textValue(row?.name) === name) return textValue(row?.value)
  }
  return ''
}
function numberValue(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v
  if (typeof v === 'string') {
    const parsed = Number(v.replace(/[¥,%\s,]/g, ''))
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}
function moneyText(v: number | null) {
  if (v == null) return '-'
  return `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}
function metricNumberText(v: number | null) {
  if (v == null) return '-'
  return String(Math.round(v * 10000) / 10000)
}
function chartValueText(value: number, unit = '') {
  const rounded = Math.round(value * 100) / 100
  const text = rounded.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  return `${unit === '¥' ? '¥' : ''}${text}${unit && unit !== '¥' ? unit : ''}`
}
function normalizeSampleBrandChartItem(raw: unknown) {
  const row = asRecord(raw) || {}
  const label = textValue(row.label) || textValue(row.name) || textValue(row.key)
  const value = numberValue(row.value)
  if (!label || value == null) return null
  const unit = textValue(row.unit)
  return {
    key: textValue(row.key) || label,
    label,
    value,
    unit,
    detail: textValue(row.detail),
    valueText: chartValueText(value, unit),
    width: 0,
  }
}
function normalizeSampleBrandCompareItem(raw: unknown) {
  const row = asRecord(raw) || {}
  const label = textValue(row.label) || textValue(row.name) || textValue(row.key)
  const low = numberValue(row.low)
  const benchmark = numberValue(row.benchmark)
  if (!label || (low == null && benchmark == null)) return null
  const unit = textValue(row.unit)
  return {
    key: textValue(row.key) || label,
    label,
    low: low ?? 0,
    benchmark: benchmark ?? 0,
    unit,
    lowText: chartValueText(low ?? 0, unit),
    benchmarkText: chartValueText(benchmark ?? 0, unit),
    lowWidth: 0,
    benchmarkWidth: 0,
  }
}
function fallbackSampleBrandChartSections(actions: Array<Record<string, any>>) {
  const issueCounts = new Map<string, number>()
  const benchmarkCounts = new Map<string, number>()
  for (const row of actions) {
    const issue = textValue(row.dimension)
    if (issue) issueCounts.set(issue, (issueCounts.get(issue) || 0) + 1)
    const benchmark = textValue(row.benchmark_label) || textValue(row.benchmark_level)
    if (benchmark) benchmarkCounts.set(benchmark, (benchmarkCounts.get(benchmark) || 0) + 1)
  }
  const result: any[] = []
  if (issueCounts.size) {
    result.push({
      type: 'segmented_bar',
      key: 'issue_mix_fallback',
      title: '问题分型分布',
      description: '根据当前待办视频建议汇总。',
      items: [...issueCounts.entries()].map(([label, value]) => ({ key: label, label, value })),
    })
  }
  if (benchmarkCounts.size) {
    result.push({
      type: 'segmented_bar',
      key: 'benchmark_mix_fallback',
      title: '对标质量',
      description: '根据当前待办参考等级汇总。',
      items: [...benchmarkCounts.entries()].map(([label, value]) => ({ key: label, label, value })),
    })
  }
  return result
}
function normalizeSampleBrandChartSections(rawSections: unknown, actions: Array<Record<string, any>>) {
  const source = arrayValue(rawSections).length ? arrayValue(rawSections) : fallbackSampleBrandChartSections(actions)
  return source.map((raw, index) => {
    const section = asRecord(raw) || {}
    const type = textValue(section.type) === 'bar_compare' ? 'bar_compare' : 'bar'
    const title = textValue(section.title) || `图表 ${index + 1}`
    const description = textValue(section.description)
    if (type === 'bar_compare') {
      const items = arrayValue(section.items).map(normalizeSampleBrandCompareItem).filter(Boolean) as any[]
      const max = Math.max(1, ...items.flatMap(item => [item.low, item.benchmark]))
      for (const item of items) {
        item.lowWidth = Math.max(4, Math.round((item.low / max) * 100))
        item.benchmarkWidth = Math.max(4, Math.round((item.benchmark / max) * 100))
      }
      return items.length ? {
        key: textValue(section.key) || `samplebrand-chart-${index}`,
        type,
        title,
        description,
        items,
      } : null
    }
    const items = arrayValue(section.items).map(normalizeSampleBrandChartItem).filter(Boolean) as any[]
    const max = Math.max(1, ...items.map(item => item.value))
    for (const item of items) {
      item.width = Math.max(4, Math.round((item.value / max) * 100))
    }
    return items.length ? {
      key: textValue(section.key) || `samplebrand-chart-${index}`,
      type,
      title,
      description,
      items,
    } : null
  }).filter(Boolean) as any[]
}
function stringList(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(item => {
    if (typeof item === 'string') return item.trim()
    const r = asRecord(item); if (!r) return ''
    return textValue(r.action) || textValue(r.text) || textValue(r.content) || textValue(r.suggestion) || textValue(r.basis)
  }).filter(Boolean)
  const t = textValue(v); return t ? [t] : []
}
function businessConclusionFallback(row: Record<string, unknown>) {
  const conclusion = textValue(row.business_conclusion)
  return conclusion ? [conclusion] : []
}
function normalizeActionRows(v: unknown, prefix: string) {
  return arrayValue(v).map((raw, i) => {
    const row = asRecord(raw) || {}; const action = textValue(row.action) || textValue(raw)
    return { key: `${prefix}-${i}-${action}`, action, priority: textValue(row.priority), dimension: textValue(row.dimension), evidence: textValue(row.evidence), ai_action: textValue(row.ai_action) }
  }).filter(r => r.action)
}
function normalizeOperatorActionRows(v: unknown) {
  return arrayValue(v).map((raw, i) => {
    const row = asRecord(raw)
    if (!row || textValue(row.source) !== 'operator_playbook') return null
    const actions = stringList(row.actions)
    const action = textValue(row.action) || actions[0]
    return {
      key: `operator-${i}-${textValue(row.playbook_id) || action}`,
      section: textValue(row.section) || '运营建议',
      scenario: textValue(row.scenario),
      action,
      priority: textValue(row.priority),
      dimension: textValue(row.dimension),
      evidence: textValue(row.evidence) || textValue(row.business_conclusion),
      ai_action: textValue(row.ai_action),
      root_causes: stringList(row.root_causes).length ? stringList(row.root_causes) : businessConclusionFallback(row),
      actions,
    }
  }).filter((row): row is NonNullable<typeof row> => Boolean(row && row.action))
}
function normalizeVideoActionRows(v: unknown) {
  return arrayValue(v).map((raw, i) => {
    const row = asRecord(raw) || {}
    const actions = stringList(row.actions)
    const action = textValue(row.action) || actions[0]
    const scenario = textValue(row.scenario) || textValue(row.title) || textValue(row.video_title)
    if (!action && !scenario) return null
    return {
      key: `video-action-${i}-${textValue(row.video_id) || scenario || action}`,
      section: textValue(row.section) || (textValue(row.rank) ? `视频 ${textValue(row.rank)}` : `视频 ${i + 1}`),
      scenario,
      action,
      priority: textValue(row.priority),
      dimension: textValue(row.dimension) || textValue(row.classification),
      evidence: textValue(row.evidence),
      ai_action: textValue(row.ai_action),
      topic_key: textValue(row.topic_key) || textValue(row.topic),
      theme_name: textValue(row.theme_name),
      editor_code: textValue(row.editor_code),
      product_code: textValue(row.product_code),
      video_id: textValue(row.video_id) || textValue(row.item_id),
      benchmark_title: textValue(row.benchmark_title) || textValue(row.benchmark_video_title) || textValue(row.benchmark),
      benchmark_level: textValue(row.benchmark_level),
      benchmark_label: textValue(row.benchmark_label),
      benchmark_warning: textValue(row.benchmark_warning),
      benchmark_editor_code: textValue(row.benchmark_editor_code),
      benchmark_theme_name: textValue(row.benchmark_theme_name),
      benchmark_selection_reason: textValue(row.benchmark_selection_reason),
      same_theme_benchmark_status: textValue(row.same_theme_benchmark_status),
      benchmark_similarity_text: textValue(row.benchmark_similarity_text),
      low_visual_evidence: textValue(row.low_visual_evidence),
      benchmark_visual_evidence: textValue(row.benchmark_visual_evidence),
      qianchuan_lifecycle_value_points: asRecord(row.qianchuan_lifecycle_value_points) || {},
      business_conclusion: textValue(row.business_conclusion),
      root_causes: stringList(row.root_causes).length ? stringList(row.root_causes) : businessConclusionFallback(row),
      actions,
      keep_points: stringList(row.keep_points),
      data_checks: stringList(row.data_checks),
    }
  }).filter((row): row is NonNullable<typeof row> => Boolean(row))
}
function normalizeDataSourceRows(v: unknown, fallback: string[]) {
  const rows = arrayValue(v).map((item, i) => {
    const r = asRecord(item)
    if (!r) { const t = textValue(item); return t ? { key: `src-${i}-${t}`, label: t, source: t, url: '', check_hint: '' } : null }
    const label = textValue(r.label) || textValue(r.name) || textValue(r.source) || `来源 ${i + 1}`
    return { key: textValue(r.key) || `src-${i}-${label}`, label, source: textValue(r.source) || label, url: textValue(r.url) || textValue(r.page_url), check_hint: textValue(r.check_hint) || textValue(r.description) }
  }).filter(Boolean) as any[]
  if (rows.length) return rows
  return fallback.map((src, i) => ({ key: `fallback-${i}-${src}`, label: src, source: src, url: '', check_hint: '当前待办未提供来源 URL' }))
}
function summaryText(v: unknown) { const t = textValue(v); if (t) return t; const r = asRecord(v); return r ? (textValue(r.recommendation) || textValue(r.summary) || textValue(r.text) || textValue(r.reason)) : '' }
function metricTone(s: string, v: string) {
  const t = `${s} ${v}`
  return /下滑|下降|异常|缺少|未采集|风险|权限不足|安全校验|待补采|待复核|采集失败|-/.test(t) ? 'tile-warn' : /上升|正常|已采集|有/.test(t) ? 'tile-ok' : 'tile-neutral'
}
function dispatchPrimary(record: InboxDispatchTask) {
  if (isSampleBrandLowConsumptionSkill.value) return '派发任务'
  const e = asRecord(record.extra); const id = textValue(e?.item_id); const p = textValue(e?.priority); const dt = textValue(e?.data_time)
  return [p, id ? `商品 ${id}` : '', dt].filter(Boolean).join(' · ') || '派发任务'
}
function dispatchSegments(content: string) {
  if (isSampleBrandLowConsumptionSkill.value) {
    const task = (detail.value.dispatch_tasks || []).find(item => item.content === content || item.display_content === content)
    const extra = asRecord(task?.extra)
    const output = textValue(extra?.required_output) || textValue(extra?.deliverable)
    return [
      { label: '交付物', text: output || '提交改版视频方向、复用的高质量样本、复测指标。' },
    ]
  }
  return String(content || '').split('；').map(p => p.trim()).filter(Boolean).slice(0, 5).map((part, i) => {
    const [label, ...rest] = part.split('：')
    return rest.length ? { label: label.trim(), text: rest.join('：').trim() } : { label: `要点 ${i + 1}`, text: part }
  })
}

watch(() => route.params.id, loadDetail, { immediate: true })
onBeforeUnmount(cancelDeferredDetailLoads)
</script>

<style scoped>
/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   页面根容器 — Crisp Mono / ai-tokens
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.detail-root {
  max-width: 1360px;
  margin: 0 auto;
  padding: 16px 24px 48px;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
  background: var(--ai-bg);
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   工具栏 — flat surface + ai-border
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.detail-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  padding: 8px 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}
.toolbar-tags {
  display: flex;
  gap: 6px;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Hero 区 — flat ai-surface + ai-border (no gradient/shadow)
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.detail-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 200px;
  gap: 24px;
  padding: 22px 26px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  box-shadow: none;
  margin-bottom: 18px;
}
.hero-main {
  min-width: 0;
}
.hero-kicker-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.hero-kicker {
  font-size: 12px;
  color: var(--ai-ink-4);
  font-weight: 500;
  letter-spacing: 0;
}
.hero-confidence {
  font-size: 11.5px;
  color: var(--ai-ink-4);
  font-weight: 500;
}
.hero-title {
  font-size: 22px;
  font-weight: 600;
  color: var(--ai-ink-1);
  letter-spacing: -0.02em;
  margin: 0 0 6px;
  line-height: 1.3;
}
.hero-summary {
  margin: 0 0 12px;
  max-width: 920px;
  font-size: 13px;
  color: var(--ai-ink-3);
  line-height: 1.6;
}
.hero-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 11.5px;
  color: var(--ai-ink-4);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
}
.hero-meta > span {
  display: inline-flex;
  align-items: center;
}
.hero-meta > span + span::before {
  content: '·';
  margin-right: 6px;
  color: var(--ai-ink-5);
}
.hero-meta .mono {
  font-family: var(--ai-font-mono);
}
.hero-pill-row {
  display: flex;
  gap: 6px;
  margin-top: 10px;
  flex-wrap: wrap;
}

/* Hero KPI */
.hero-kpi {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
  flex-shrink: 0;
  min-width: 0;
}
.hero-kpi-label {
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0;
  color: var(--ai-ink-4);
  text-transform: none;
}
.hero-kpi-value {
  font-size: 34px;
  font-weight: 600;
  line-height: 1;
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  color: var(--ai-ink-1);
  letter-spacing: -0.02em;
}
.hero-kpi-value.pi-critical { color: var(--ai-bad); }
.hero-kpi-value.pi-medium { color: var(--ai-warn); }
.hero-kpi-value.pi-normal { color: var(--ai-ok); }
.hero-kpi-status {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  font-weight: 500;
  color: var(--ai-ink-3);
}
.hero-kpi-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--ai-ink-5);
}
.hero-kpi-dot.dot-critical { background: var(--ai-bad); }
.hero-kpi-dot.dot-high { background: var(--ai-bad); }
.hero-kpi-dot.dot-medium { background: var(--ai-warn); }
.hero-kpi-dot.dot-low { background: var(--ai-ok); }
.hero-kpi-delta {
  font-size: 11.5px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}

/* hero arco tag → ai-pill */
.detail-hero :deep(.arco-tag),
.detail-toolbar :deep(.arco-tag) {
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.detail-hero :deep(.arco-tag-color-arcoblue),
.detail-toolbar :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft);
  color: var(--ai-info);
  border-color: transparent;
}
.detail-hero :deep(.arco-tag-color-orange),
.detail-toolbar :deep(.arco-tag-color-orange) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.detail-hero :deep(.arco-tag-color-orangered),
.detail-toolbar :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  border-color: transparent;
}
.detail-hero :deep(.arco-tag-color-red),
.detail-toolbar :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}
.detail-hero :deep(.arco-tag-color-green),
.detail-toolbar :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.detail-hero :deep(.arco-tag-color-gray),
.detail-toolbar :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
}

.detail-related-report { margin-bottom: 12px; }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   双栏布局
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.detail-shell {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: 18px;
  align-items: start;
}
.detail-main {
  display: grid;
  gap: 14px;
  min-width: 0;
  overflow: hidden;
}
.detail-card {
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  margin-bottom: 0 !important;
  min-width: 0;
  overflow: hidden;
}
.detail-card :deep(.arco-card-header) {
  padding: 12px 16px;
  border-bottom: 1px solid var(--ai-border);
}
.detail-card :deep(.arco-card-head-title) {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}
.detail-card :deep(.arco-card-body) {
  padding: 14px 16px 16px;
  overflow: hidden;
}
.detail-card-data {
  background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(248,250,252,0.96)) !important;
}
.detail-card-paid {
  border-color: rgba(22,93,255,0.14) !important;
}
.detail-card-danger {
  border-color: var(--ai-bad-soft) !important;
}
.detail-card-danger :deep(.arco-card-head-title) {
  color: var(--ai-bad);
}

.post-training-card :deep(.arco-card-body) {
  display: grid;
  gap: 12px;
}
.post-training-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.post-training-title-block {
  min-width: 0;
  display: grid;
  gap: 3px;
}
.post-training-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.post-training-subtitle {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 11.5px;
  color: var(--ai-ink-4);
}
.post-training-text,
.post-training-empty {
  padding: 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--ai-ink-2);
  white-space: pre-wrap;
  word-break: break-word;
}
.post-training-empty {
  color: var(--ai-ink-4);
}
.post-training-meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 8px;
}
.post-training-meta-item {
  display: grid;
  gap: 3px;
  min-width: 0;
  padding: 8px 10px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
}
.post-training-meta-item span {
  color: var(--ai-ink-4);
  font-size: 11px;
}
.post-training-meta-item strong {
  color: var(--ai-ink-1);
  font-size: 12px;
  font-weight: 500;
  word-break: break-word;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   章节块 & eyebrow
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.section-block { margin-top: 18px; }
.section-block:first-child { margin-top: 0; }
.section-panel {
  padding: 14px;
  border-radius: 8px;
  background: rgba(248,250,252,0.66);
  border: 1px solid rgba(98,115,142,0.08);
}
.section-eyebrow {
  margin-bottom: 10px;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.eyebrow-extra {
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  color: var(--ai-ink-4);
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   子卡片 / 列表项
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.sub-card {
  padding: 14px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  transition: box-shadow var(--sf-transition-fast);
}
.sub-card:hover {
  background: var(--ai-surface-2);
}
.stack {
  display: grid;
  gap: 8px;
}

/* 运营建议 */
.card-grid { display: grid; gap: 8px; }
.action-grid { grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); }
.action-sub-card { display: grid; gap: 6px; }
.action-sub-head {
  display: flex; align-items: flex-start; justify-content: space-between;
  gap: 8px; flex-wrap: wrap;
}
.action-sub-head strong {
  font-size: 13px; font-weight: 500; color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}
.action-sub-tags { display: flex; gap: 4px; flex-wrap: wrap; }
.action-sub-evidence,
.action-sub-ai {
  font-size: 12px; color: var(--ai-ink-3); line-height: 1.5;
}
.operator-action-stack { display: grid; gap: 16px; }
.operator-action-section { display: grid; gap: 10px; }
.operator-section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding: 0 2px;
}
.operator-section-head strong {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}
.operator-section-head span {
  font-size: 12px;
  color: var(--ai-ink-3);
}
.operator-action-card { display: grid; gap: 12px; }
.operator-action-block { display: grid; gap: 8px; }
.operator-action-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--ai-ink-4);
  text-transform: uppercase;
}
.operator-chip-row { display: flex; flex-wrap: wrap; gap: 6px; }
.operator-action-list {
  margin: 0;
  padding-left: 20px;
  display: grid;
  gap: 6px;
}
.operator-action-list li {
  font-size: 12px;
  line-height: 1.55;
  color: var(--ai-ink-2);
}

/* 为什么会异常 */
.cause-sub-card { display: grid; gap: 6px; }
.cause-sub-head {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
}
.cause-sub-head strong {
  font-size: 13px; font-weight: 500; color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}
.cause-rank-badge {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 4px;
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  font-family: var(--ai-font-mono);
  font-size: 11px; font-weight: 600;
  flex-shrink: 0;
}
.cause-sub-tags { display: flex; gap: 4px; flex-wrap: wrap; margin-left: auto; }
.cause-sub-evidence {
  font-size: 12px; color: var(--ai-ink-3); line-height: 1.5;
}
.cause-sub-chips { display: flex; gap: 4px; flex-wrap: wrap; }

.chip-tag {
  display: inline-flex; align-items: center;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  border: 1px solid var(--ai-border);
}

/* 判断依据 */
.basis-sub-card { display: grid; gap: 6px; }
.basis-sub-head {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
}
.basis-sub-head strong {
  font-size: 12.5px; font-weight: 500; color: var(--ai-ink-1);
}
.basis-text {
  margin: 0; font-size: 12px; color: var(--ai-ink-2); line-height: 1.55;
}
.basis-gap-note {
  font-size: 11.5px; color: var(--ai-warn);
}
.gap-note {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-warn-soft);
  color: var(--ai-warn);
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.55;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Micro Tag — 项目统一小型标签 → ai-pill 风格
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.micro-tag {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0;
  white-space: nowrap;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  border: 1px solid var(--ai-border);
}
.mt-ok     { color: var(--ai-ok);     background: var(--ai-ok-soft);   border-color: transparent; }
.mt-danger { color: var(--ai-bad);    background: var(--ai-bad-soft);  border-color: transparent; }
.mt-warn   { color: var(--ai-warn);   background: var(--ai-warn-soft); border-color: transparent; }
.mt-info   { color: var(--ai-info);   background: var(--ai-info-soft); border-color: transparent; }
.mt-neutral{ color: var(--ai-ink-3);  background: var(--ai-surface-2); }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   标准口径指标面板
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.compare-table-wrap {
  overflow-x: auto;
  border-radius: var(--ai-radius);
  border: 1px solid var(--ai-border);
}
.compare-table {
  width: 100%; border-collapse: collapse;
  font-size: 12.5px;
}
.compare-table th {
  text-align: left;
  padding: 8px 10px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-4);
  font-weight: 500;
  font-size: 11.5px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  white-space: nowrap;
}
.compare-table td {
  padding: 10px;
  border-bottom: 1px solid var(--ai-border);
  vertical-align: middle;
}
.compare-table tbody tr:hover td { background: var(--ai-surface-2); }
.compare-table tbody tr:last-child td { border-bottom: none; }
.ct-label {
  font-weight: 500; color: var(--ai-ink-1);
}
.ct-val {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  color: var(--ai-ink-2);
}
.ct-delta {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  font-size: 13px;
  color: var(--ai-ink-1);
}
.row-下降 .ct-delta { color: var(--ai-bad); }
.row-上升 .ct-delta { color: var(--ai-ok); }
.row-未获取 .ct-delta,
.row-未获取 .ct-prev { color: var(--ai-ink-4); font-weight: 400; }
.row-sep td {
  padding: 4px 0;
  /* webkit 1px dashed 渲染成 solid，用 background gradient pattern 代替 */
  background-image: linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%);
  background-position: bottom;
  background-size: 6px 1px;
  background-repeat: repeat-x;
  background-color: transparent;
}
.row-extra td {
  padding: 6px 10px;
  color: var(--ai-ink-3);
}
.ct-extra-detail { font-size: 12px; line-height: 1.5; }
.compare-core-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}
.compare-core-card {
  display: grid;
  gap: 10px;
  min-width: 0;
  padding: 12px;
  border-radius: 8px;
  background: rgba(255,255,255,0.86);
  border: 1px solid rgba(98,115,142,0.1);
  box-shadow: 0 6px 14px rgba(18,29,46,0.04);
}
.compare-core-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.compare-core-head strong,
.ct-label {
  min-width: 0;
  font-size: var(--sf-text-caption);
  font-weight: 800;
  color: var(--sf-ink-1);
  line-height: 1.35;
}
.compare-core-values {
  display: grid;
  gap: 7px;
}
.compare-core-values div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}
.compare-core-values span {
  flex-shrink: 0;
  color: var(--sf-ink-3);
  font-size: 11px;
  font-weight: 700;
}
.compare-core-values b {
  min-width: 0;
  color: var(--sf-ink-1);
  font-size: var(--sf-text-caption);
  font-weight: 850;
  font-variant-numeric: tabular-nums;
  text-align: right;
  overflow-wrap: anywhere;
}
.compare-core-delta b {
  font-size: 18px;
}
.row-下降 .compare-core-delta b { color: var(--sf-accent-red); }
.row-上升 .compare-core-delta b { color: var(--sf-accent-green); }
.row-未获取 .compare-core-delta b,
.row-未获取 .ct-prev { color: var(--sf-ink-3); font-weight: 600; }
.compare-extra-list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}
.compare-extra-row {
  display: grid;
  grid-template-columns: minmax(150px, 0.28fr) minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(255,255,255,0.72);
  border: 1px solid rgba(98,115,142,0.08);
  color: var(--sf-ink-2);
}
.compare-extra-head {
  display: grid;
  gap: 6px;
  align-content: start;
}
.ct-extra-detail { font-size: var(--sf-text-caption); line-height: 1.5; }
.ct-paid-compare {
  display: grid;
  gap: 8px;
  min-width: 0;
}
.ct-paid-period {
  color: var(--sf-ink-3);
  font-size: 11px;
  font-weight: 800;
  line-height: 1.35;
}
.ct-paid-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}
.ct-paid-pill {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  max-width: 100%;
  padding: 5px 9px;
  border-radius: 8px;
  background: rgba(248,250,252,0.95);
  border: 1px solid rgba(98,115,142,0.1);
  color: var(--sf-ink-1);
  font-variant-numeric: tabular-nums;
  line-height: 1.35;
}
.ct-paid-pill b {
  flex-shrink: 0;
  color: var(--sf-ink-3);
  font-size: 11px;
  font-weight: 800;
}
.ct-paid-pill span {
  min-width: 0;
  font-weight: 800;
  overflow-wrap: anywhere;
}
.ct-paid-pill em {
  flex-shrink: 0;
  color: var(--sf-ink-3);
  font-style: normal;
  font-weight: 900;
}
.ct-paid-pill.down em { color: var(--sf-accent-red); }
.ct-paid-pill.up em { color: var(--sf-accent-green); }

.free-search-fact-strip {
  display: grid;
  gap: 12px;
  padding: 14px;
  margin-bottom: 14px;
  border-radius: var(--ai-radius);
  background: var(--ai-info-soft);
  border: 1px solid transparent;
}
.free-search-fact-head {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
}
.free-search-fact-title {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.free-search-fact-source {
  margin-top: 3px;
  font-size: 11px;
  color: var(--ai-ink-4);
}
.free-search-fact-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}
.free-search-fact-cell {
  min-width: 0;
  padding: 10px 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}
.free-search-fact-cell span {
  display: block;
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-4);
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.free-search-fact-cell strong {
  display: block;
  font-size: 20px;
  font-weight: 850;
  line-height: 1.1;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
  overflow-wrap: anywhere;
}
.free-search-compare-note {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--ai-warn);
}
.snapshot-compare-panel {
  display: grid;
  gap: 12px;
  padding: 0;
  margin-bottom: 0;
  border-radius: 0;
  border: 1px solid rgba(22,93,255,0.12);
  background: transparent;
  border: 0;
}
.snapshot-compare-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.snapshot-compare-title {
  font-size: 13px;
  font-weight: 800;
  color: var(--sf-ink-1);
}
.snapshot-compare-period,
.paid-compare-period {
  margin-top: 3px;
  font-size: var(--sf-text-tiny);
  color: var(--sf-ink-3);
}
.snapshot-compare-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.snapshot-compare-cell {
  min-width: 0;
  padding: 12px;
  border-radius: 8px;
  background: #fff;
  border: 1px solid rgba(98,115,142,0.08);
  display: grid;
  gap: 5px;
}
.snapshot-compare-cell span {
  font-size: 11px;
  font-weight: 700;
  color: var(--sf-ink-3);
}
.snapshot-compare-cell strong {
  font-size: 18px;
  color: var(--sf-ink-1);
  font-variant-numeric: tabular-nums;
}
.snapshot-compare-cell small {
  font-size: 12px;
  color: var(--sf-ink-3);
}
.snapshot-compare-cell em {
  font-style: normal;
  font-size: var(--sf-text-caption);
  font-weight: 800;
  color: var(--sf-ink-2);
}
.snapshot-compare-cell.down em,
.paid-compare-table tr.down td:last-child { color: var(--sf-accent-red); }
.snapshot-compare-cell.up em,
.paid-compare-table tr.up td:last-child { color: var(--sf-accent-green); }
.paid-compare-board {
  display: grid;
  gap: 10px;
}
.paid-compare-cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.paid-compare-card {
  display: grid;
  gap: 8px;
  min-width: 0;
  padding: 14px;
  border-radius: 8px;
  background: rgba(255,255,255,0.88);
  border: 1px solid rgba(98,115,142,0.1);
}
.paid-compare-card span {
  font-size: var(--sf-text-caption);
  font-weight: 800;
  color: var(--sf-ink-2);
}
.paid-compare-card strong {
  color: var(--sf-ink-1);
  font-size: 24px;
  font-weight: 850;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.paid-compare-card div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}
.paid-compare-card small {
  min-width: 0;
  color: var(--sf-ink-3);
  font-size: var(--sf-text-tiny);
  font-weight: 700;
  overflow-wrap: anywhere;
}
.paid-compare-card em {
  flex-shrink: 0;
  color: var(--sf-ink-2);
  font-size: var(--sf-text-body);
  font-style: normal;
  font-weight: 900;
  font-variant-numeric: tabular-nums;
}
.paid-compare-card.down em { color: var(--sf-accent-red); }
.paid-compare-card.up em { color: var(--sf-accent-green); }

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   指标网格
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.metric-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
@media (min-width: 1400px) {
  .metric-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
.metric-tile {
  padding: 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  display: grid;
  gap: 4px;
  align-content: start;
}
.metric-tile:hover {
  background: var(--ai-surface-2);
}
.metric-value {
  font-size: 18px;
  font-weight: 800;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  line-height: 1.25;
  letter-spacing: -0.01em;
}
.metric-tile:has(.metric-detail-text) .metric-value {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0;
}
.promoted-metric-tile {
  gap: 12px;
}
.promoted-metric-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}
.promoted-metric-head .metric-name {
  min-width: 0;
}
.promoted-metric-head .metric-value {
  flex-shrink: 0;
  color: var(--sf-ink-1);
}
.promoted-metric-tile:has(.promoted-compare-detail) .metric-value {
  font-size: 20px;
  font-weight: 800;
}
.promoted-compare-detail {
  display: grid;
  gap: 8px;
  min-width: 0;
}
.promoted-period-line {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(255,255,255,0.72);
  border: 1px solid rgba(98,115,142,0.08);
}
.promoted-period-label {
  color: var(--sf-ink-3);
  font-size: 11px;
  font-weight: 800;
  white-space: nowrap;
}
.promoted-period-text {
  min-width: 0;
  color: var(--sf-ink-2);
  font-size: var(--sf-text-caption);
  line-height: 1.45;
  overflow-wrap: anywhere;
}
.promoted-metric-pills,
.promoted-change-pills {
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.promoted-metric-pill,
.promoted-change-pill {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  max-width: 100%;
  padding: 3px 8px;
  border-radius: 8px;
  background: rgba(248,250,252,0.92);
  border: 1px solid rgba(98,115,142,0.1);
  color: var(--sf-ink-1);
  font-size: var(--sf-text-caption);
  line-height: 1.35;
}
.promoted-metric-pill b,
.promoted-change-pill b {
  color: var(--sf-ink-3);
  font-size: 11px;
  font-weight: 800;
  white-space: nowrap;
}
.promoted-metric-pill span,
.promoted-change-pill span {
  min-width: 0;
  font-weight: 800;
  overflow-wrap: anywhere;
}
.promoted-change-pills {
  padding-top: 2px;
}
.promoted-change-pill {
  background: rgba(22,93,255,0.06);
  border-color: rgba(22,93,255,0.12);
}
.promoted-change-summary {
  color: var(--sf-ink-1);
  font-size: var(--sf-text-caption);
  font-weight: 800;
  line-height: 1.45;
  overflow-wrap: anywhere;
}
.metric-name {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-weight: 500;
  line-height: 1.35;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.metric-detail-text {
  font-size: 12px;
  color: var(--ai-ink-3);
  line-height: 1.5;
  overflow-wrap: anywhere;
  margin-top: 2px;
}
.metric-status-text {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 500;
  margin-top: 3px;
  text-transform: none;
  letter-spacing: 0;
  color: var(--ai-ink-3);
}
.metric-status-text::before {
  content: '';
  width: 5px; height: 5px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}
.metric-source-text {
  font-size: 11px;
  color: var(--ai-ink-4);
  line-height: 1.45;
  overflow-wrap: anywhere;
}
.tile-ok {
  background: var(--ai-ok-soft);
  border-color: transparent;
}
.tile-ok .metric-value { color: var(--ai-ok); }
.tile-ok .metric-status-text { color: var(--ai-ok); }
.tile-warn {
  background: var(--ai-warn-soft);
  border-color: transparent;
}
.tile-warn .metric-value { color: var(--ai-warn); }
.tile-warn .metric-status-text { color: var(--ai-warn); }
.tile-neutral .metric-status-text { color: var(--ai-ink-4); }

.promoted-metric-grid {
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   数据缺口 / 来源
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.gap-row {
  display: flex; gap: 8px; align-items: flex-start;
  padding: 8px 0;
  border-bottom: 1px solid var(--ai-border);
}
.gap-row:last-child { border-bottom: none; }
.gap-dimension {
  min-width: 100px;
  font-size: 12px;
  font-weight: 500;
  color: var(--ai-warn);
}
.gap-issue {
  font-size: 12px;
  color: var(--ai-ink-2);
  line-height: 1.5;
}
.fold-toggle { margin-top: 6px; }

.source-row {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
}
.source-row:hover {
  background: var(--ai-surface-2);
}
.source-info { min-width: 0; display: grid; gap: 3px; }
.source-name {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.source-hint {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  overflow-wrap: anywhere;
}

.detail-row {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 8px 0;
  border-bottom: 1px solid var(--ai-border);
}
.detail-row:last-child { border-bottom: none; }
.detail-row-label {
  min-width: 150px;
  font-size: 12px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.detail-row-text {
  min-width: 0;
  font-size: 12px;
  color: var(--ai-ink-2);
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.mini-table-wrap {
  overflow-x: auto;
  border-radius: var(--ai-radius);
  border: 1px solid var(--ai-border);
}
.mini-table {
  width: 100%;
  min-width: 640px;
  border-collapse: collapse;
  font-size: 12.5px;
}
.mini-table th {
  text-align: left;
  padding: 8px 10px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-4);
  font-weight: 500;
  font-size: 11.5px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  white-space: nowrap;
}
.mini-table td {
  padding: 10px;
  border-bottom: 1px solid var(--ai-border);
  color: var(--ai-ink-2);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
}
.mini-table tbody tr:hover td { background: var(--ai-surface-2); }
.mini-table tbody tr:last-child td { border-bottom: none; }
.mini-label {
  color: var(--ai-ink-1) !important;
  font-weight: 500;
  font-family: var(--ai-font-sans);
  max-width: 260px;
  overflow-wrap: anywhere;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   视频低消耗 Agent 待办
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.agent-video-card {
  border-color: rgba(22,93,255,0.14) !important;
}
.agent-video-summary {
  display: grid;
  gap: 14px;
}
.agent-video-summary-text {
  margin: 0;
  color: var(--ai-ink-1);
  font-size: 13px;
  line-height: 1.65;
}
.agent-video-meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.agent-video-kpi-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.agent-video-detail-markdown {
  overflow-wrap: anywhere;
  color: var(--ai-ink-2);
  font-size: 13px;
  line-height: 1.8;
  font-family: var(--ai-font-sans);
}
.agent-video-detail-markdown :deep(*) {
  font-family: inherit;
}
.agent-video-detail-markdown :deep(h1) {
  margin: 0 0 14px;
  color: var(--ai-ink-1);
  font-size: 20px;
  font-weight: 700;
  line-height: 1.35;
}
.agent-video-detail-markdown :deep(h2) {
  margin: 22px 0 10px;
  padding-top: 14px;
  border-top: 1px solid var(--ai-border);
  color: var(--ai-ink-1);
  font-size: 15px;
  font-weight: 700;
  line-height: 1.4;
}
.agent-video-detail-markdown :deep(h3) {
  margin: 18px 0 8px;
  color: var(--ai-ink-1);
  font-size: 13.5px;
  font-weight: 700;
  line-height: 1.45;
}
.agent-video-detail-markdown :deep(p) {
  margin: 8px 0;
  color: var(--ai-ink-2);
}
.agent-video-detail-markdown :deep(ul),
.agent-video-detail-markdown :deep(ol) {
  margin: 8px 0 12px;
  padding-left: 22px;
}
.agent-video-detail-markdown :deep(li) {
  margin: 6px 0;
  color: var(--ai-ink-2);
  line-height: 1.85;
}
.agent-video-detail-markdown :deep(li > ul),
.agent-video-detail-markdown :deep(li > ol) {
  margin-top: 6px;
}
.agent-video-detail-markdown :deep(strong) {
  color: var(--ai-ink-1);
  font-weight: 700;
}
.agent-video-detail-markdown :deep(blockquote) {
  margin: 12px 0;
  padding: 10px 12px;
  border-left: 3px solid var(--ai-info);
  border-radius: 0 var(--ai-radius) var(--ai-radius) 0;
  background: var(--ai-info-soft);
  color: var(--ai-ink-2);
}
.agent-video-detail-markdown :deep(code) {
  padding: 1px 5px;
  border-radius: 5px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 12px;
}
.agent-video-detail-markdown :deep(hr) {
  margin: 18px 0;
  border: 0;
  border-top: 1px solid var(--ai-border);
}
.agent-video-detail-markdown :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 12px 0;
  font-size: 12px;
}
.agent-video-detail-markdown :deep(th),
.agent-video-detail-markdown :deep(td) {
  padding: 8px 10px;
  border: 1px solid var(--ai-border);
  text-align: left;
  vertical-align: top;
}
.agent-video-detail-markdown :deep(th) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  font-weight: 700;
}
.agent-video-action-stack {
  display: grid;
  gap: 12px;
}
.agent-video-action-card {
  display: grid;
  gap: 12px;
}
.agent-video-action-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.agent-video-action-title {
  min-width: 0;
  display: grid;
  gap: 4px;
}
.agent-video-action-title span {
  color: var(--ai-info);
  font-size: 11px;
  font-weight: 600;
}
.agent-video-action-title strong {
  color: var(--ai-ink-1);
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.45;
  overflow-wrap: anywhere;
}
.agent-video-meta-line {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  color: var(--ai-ink-4);
  font-size: 11.5px;
  line-height: 1.45;
}
.agent-video-evidence {
  padding: 10px 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-info-soft);
  color: var(--ai-ink-2);
  font-size: 12px;
  line-height: 1.6;
}
.benchmark-selection-evidence {
  display: grid;
  gap: 4px;
  border-left: 3px solid var(--ai-info);
}
.benchmark-selection-evidence strong {
  font-size: 12px;
  color: var(--ai-ink-1);
}
.benchmark-selection-evidence span {
  overflow-wrap: anywhere;
}
.agent-video-block-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.agent-video-block {
  min-width: 0;
  display: grid;
  gap: 8px;
  padding: 10px 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
}
.agent-video-block-wide {
  grid-column: 1 / -1;
}
.agent-video-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 6px;
}
.agent-video-list li {
  color: var(--ai-ink-2);
  font-size: 12px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}
.agent-video-list.ordered {
  padding-left: 20px;
}
.agent-video-metric-grid {
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}
.agent-video-split {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 10px;
}
.agent-video-split .basis-sub-card + .basis-sub-card {
  margin-top: 8px;
}
.samplebrand-chart-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
}
.samplebrand-chart-panel {
  min-width: 0;
  display: grid;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
}
.samplebrand-chart-head {
  display: grid;
  gap: 4px;
}
.samplebrand-chart-head strong {
  color: var(--ai-ink-1);
  font-size: 13px;
  line-height: 1.35;
}
.samplebrand-chart-head span {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  line-height: 1.45;
}
.samplebrand-share-chart,
.samplebrand-compare-chart {
  display: grid;
  gap: 10px;
}
.samplebrand-share-row {
  display: grid;
  grid-template-columns: minmax(72px, 108px) minmax(92px, 1fr) auto;
  align-items: center;
  gap: 8px;
}
.samplebrand-chart-label {
  min-width: 0;
  color: var(--ai-ink-2);
  font-size: 12px;
  line-height: 1.35;
  overflow-wrap: anywhere;
}
.samplebrand-share-track,
.samplebrand-bar-track {
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(22, 32, 51, 0.08);
}
.samplebrand-share-track i,
.samplebrand-bar-track i {
  display: block;
  height: 100%;
  border-radius: inherit;
}
.samplebrand-share-track i {
  background: linear-gradient(90deg, #165dff, #14b8a6);
}
.samplebrand-share-row strong,
.samplebrand-share-row span,
.samplebrand-bar-line b {
  color: var(--ai-ink-1);
  font-size: 11.5px;
  font-weight: 600;
  white-space: nowrap;
}
.samplebrand-share-row span {
  color: var(--ai-ink-4);
  font-weight: 400;
}
.samplebrand-compare-row {
  display: grid;
  grid-template-columns: minmax(68px, 88px) minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}
.samplebrand-compare-bars {
  display: grid;
  gap: 6px;
}
.samplebrand-bar-line {
  display: grid;
  grid-template-columns: 34px minmax(80px, 1fr) auto;
  align-items: center;
  gap: 8px;
}
.samplebrand-bar-line span {
  color: var(--ai-ink-4);
  font-size: 11px;
}
.samplebrand-bar-current {
  background: #f97316;
}
.samplebrand-bar-benchmark {
  background: #165dff;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   派发
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.notice-banner {
  padding: 10px 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  font-size: 12px;
  font-weight: 500;
  color: var(--ai-ink-2);
  margin-bottom: 12px;
}
.notice-ok {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
  border-color: transparent;
}
.notice-warn {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  border-color: transparent;
}
.dispatch-table {
  margin-top: 6px;
}
.dispatch-table :deep(.arco-table-container) {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}
.dispatch-table :deep(table) {
  min-width: 900px;
  table-layout: auto;
}
.dispatch-table :deep(.arco-table-th) {
  background: var(--ai-surface-2);
  color: var(--ai-ink-4);
  font-weight: 500;
  font-size: 11.5px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--ai-border);
}
.dispatch-table :deep(.arco-table-td) {
  font-size: 12.5px;
  color: var(--ai-ink-1);
  border-bottom: 1px solid var(--ai-border);
}
.dispatch-table :deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2);
}
.dispatch-table :deep(th),
.dispatch-table :deep(td) {
  white-space: nowrap;
}
.dispatch-table :deep(td) {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dispatch-assign-cell { display: flex; gap: 6px; align-items: center; }
.dispatch-cell {
  display: grid; gap: 4px;
  max-width: 340px;
  overflow-wrap: break-word;
  word-break: break-all;
}
.dispatch-cell-primary {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.dispatch-cell-segments {
  display: grid; gap: 3px;
  margin: 0;
  padding-left: 14px;
  font-size: 11.5px;
  color: var(--ai-ink-3);
  line-height: 1.5;
}
.dispatch-cell-segments li strong {
  color: var(--ai-ink-1);
  font-weight: 500;
  margin-right: 4px;
}
.dispatch-cell-segments li span { word-break: break-all; }

/* 钉钉回复 */
.dingtalk-replies {
  margin-top: 14px;
  padding: 12px 14px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
}
.reply-item {
  display: flex; gap: 10px;
  padding: 6px 0;
  border-bottom: 1px solid var(--ai-border);
}
.reply-item:last-child { border-bottom: none; }
.reply-time {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  min-width: 130px;
}
.reply-text {
  font-size: 12px;
  color: var(--ai-ink-1);
  line-height: 1.55;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   右侧栏
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.detail-aside {
  min-width: 0;
}
.aside-sticky {
  display: grid; gap: 12px;
  max-height: calc(100vh - 120px);
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--ai-ink-5) transparent;
}
.aside-sticky::-webkit-scrollbar { width: 6px; }
.aside-sticky::-webkit-scrollbar-thumb {
  background: var(--ai-ink-5);
  border-radius: 999px;
}
.aside-sticky::-webkit-scrollbar-track { background: transparent; }

@media (min-width: 1101px) {
  .detail-aside {
    position: sticky;
    top: 16px;
    align-self: start;
  }
}

.aside-card {
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  margin-bottom: 0 !important;
}
.aside-card :deep(.arco-card-header) {
  padding: 12px 14px !important;
  border-bottom: 1px solid var(--ai-border);
}
.aside-card :deep(.arco-card-body) {
  padding: 12px 14px 14px !important;
}
.aside-card :deep(.arco-card-head-title) {
  font-size: 13px !important;
  font-weight: 500 !important;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}
.aside-card-action {
  border-color: var(--ai-ink-1) !important;
}

.aside-actions { display: flex; flex-direction: column; gap: 6px; }
.aside-actions :deep(.arco-btn) {
  height: 34px;
  font-weight: 500;
  font-size: 13px;
  border-radius: 6px;
}

/* 基本信息 DL */
.info-dl { display: grid; gap: 0; }
.info-row {
  display: flex; align-items: baseline; justify-content: space-between;
  gap: 10px;
  padding: 7px 0;
  /* webkit 1px dashed 渲染成 solid，用 background gradient pattern 代替 */
  background-image: linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%);
  background-position: bottom;
  background-size: 6px 1px;
  background-repeat: repeat-x;
}
.info-row:last-child { border-bottom: none; }
.info-row dt {
  font-size: 11.5px;
  color: var(--ai-ink-4);
  font-weight: 500;
  flex-shrink: 0;
}
.info-row dd {
  font-size: 12px;
  color: var(--ai-ink-1);
  font-weight: 500;
  text-align: right;
  word-break: break-word;
  margin: 0;
}
.info-code {
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-size: 11px;
  background: var(--ai-surface-2);
  padding: 1px 6px;
  border-radius: 3px;
  color: var(--ai-ink-2);
}

/* 时间线 */
.timeline-stack { display: grid; gap: 6px; }
.timeline-row {
  display: grid;
  grid-template-columns: 40px 1fr;
  gap: 10px;
  align-items: start;
  padding: 8px 10px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
}
.timeline-decision {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 18px;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 10.5px;
  font-weight: 500;
}
.timeline-decision.tl-ok {
  background: var(--ai-ok-soft);
  color: var(--ai-ok);
}
.timeline-decision.tl-no {
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
}
.timeline-body { min-width: 0; }
.timeline-title-text {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--ai-ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.timeline-meta-text {
  font-size: 11px;
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  margin-top: 2px;
}
.timeline-reason-text {
  font-size: 11.5px;
  color: var(--ai-ink-3);
  margin-top: 4px;
}

/* 调试 */
.debug-block {
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  line-height: 1.55;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   通用
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
.inline-list { margin: 0; padding-left: 18px; }
.inline-list li {
  margin: 4px 0;
  font-size: 12.5px;
  color: var(--ai-ink-1);
  line-height: 1.55;
}
.forbidden-list { margin: 0; padding-left: 18px; }
.forbidden-list li {
  margin: 4px 0;
  font-size: 12.5px;
  color: var(--ai-bad);
  line-height: 1.55;
}
.empty-note {
  padding: 24px 0;
  text-align: center;
  font-size: 12px;
  color: var(--ai-ink-4);
}
.text-caption { font-size: 12px; }
.text-muted { color: var(--ai-ink-4); }

/* 编辑 */
.edit-block { display: grid; gap: 12px; }
.edit-field { display: grid; gap: 6px; }
.edit-row {
  display: grid;
  grid-template-columns: minmax(0,1fr) auto;
  gap: 8px;
  align-items: start;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Arco button overrides — flat Crisp Mono style
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
:deep(.arco-btn) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  font-size: 12.5px;
  font-weight: 500;
  border-color: var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  box-shadow: none;
}
:deep(.arco-btn:not(.arco-btn-primary):not(.arco-btn-status-success):not(.arco-btn-status-danger):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
:deep(.arco-btn-primary) {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  border-color: var(--ai-ink-1);
}
:deep(.arco-btn-primary:hover) {
  background: #000;
  border-color: #000;
}
:deep(.arco-btn-size-mini) {
  height: 26px;
  padding: 0 10px;
  font-size: 12px;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   派发审批弹窗
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
:deep(.dispatch-modal) {
  display: flex; flex-direction: column; gap: 12px;
}
:deep(.dispatch-modal-head) {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap;
}
:deep(.dispatch-modal-tip) {
  margin: 0;
  color: var(--ai-ink-2);
  font-size: 12.5px;
}
:deep(.dispatch-modal-meta) { display: flex; gap: 8px; flex-wrap: wrap; }
:deep(.dispatch-modal-row) {
  display: flex; flex-direction: column; gap: 8px;
  padding: 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
  border: 1px solid var(--ai-border);
}
:deep(.dispatch-modal-summary) {
  display: flex; flex-direction: column; gap: 4px;
}
:deep(.dispatch-modal-select) { width: 100%; }
:deep(.dispatch-modal-select .arco-select-view) {
  min-height: 32px;
  border-radius: 6px;
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   响应式
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
@media (max-width: 1100px) {
  .detail-shell { grid-template-columns: 1fr; }
  .detail-aside { position: static; }
  .aside-sticky { max-height: none; overflow: visible; }
  .compare-core-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
  .detail-root { padding: 12px 16px 32px; }
  .detail-hero {
    grid-template-columns: 1fr;
  }
  .hero-kpi { align-items: flex-start; }
  .section-panel { padding: 12px; }
  .free-search-fact-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .snapshot-compare-grid { grid-template-columns: 1fr; }
  .compare-core-grid { grid-template-columns: 1fr; }
  .compare-extra-row { grid-template-columns: 1fr; }
  .paid-compare-cards { grid-template-columns: 1fr; }
  .metric-grid { grid-template-columns: 1fr; }
  .promoted-metric-head { flex-direction: column; gap: 4px; }
  .promoted-period-line { grid-template-columns: 1fr; gap: 4px; }
  .samplebrand-chart-grid { grid-template-columns: 1fr; }
  .samplebrand-share-row,
  .samplebrand-compare-row {
    grid-template-columns: 1fr;
  }
  .free-search-fact-cell strong { font-size: 20px; }
}
</style>
