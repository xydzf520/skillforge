<template>
  <div class="sf-page ai-main">
    <div class="sf-pagehead ai-pagehead">
      <div>
        <div class="ai-crumbs">SkillForge · SF 调用观测</div>
        <h1 class="ai-title">SF 功能</h1>
        <p class="ai-sub">查看 sf / Codex 调用量、形成的报告、调用过的 SF 能力和最近调用明细。</p>
      </div>
      <div class="sf-head-actions">
        <button class="ai-btn" type="button" :disabled="refreshing" @click="refreshOverview">
          <SfShellIcon name="refresh" />
          {{ refreshing ? '刷新中' : '刷新' }}
        </button>
        <button class="ai-btn" type="button" @click="commandCenterDrawerVisible = true">
          <SfShellIcon name="list" />
          命令中心 <span class="sf-tab-count">{{ numberText(commandCount) }}</span>
        </button>
        <button class="ai-btn" type="button" @click="toolsCatalogDrawerVisible = true">
          <SfShellIcon name="bolt" />
          MCP 能力 <span class="sf-tab-count">{{ numberText(catalogTools.length) }}</span>
        </button>
        <button class="ai-btn" type="button" :disabled="!events.length || exportingEvents" @click="exportEvents">
          <SfShellIcon name="download" />
          导出调用
        </button>
      </div>
    </div>

    <nav class="sf-section-tabs ai-tabs" aria-label="SF 功能分区">
      <button
        class="ai-tab"
        :class="{ active: activeSection === 'overview' }"
        type="button"
        @click="scrollToSection('sf-overview', 'overview')"
      >
        总览 <span class="sf-tab-count">{{ numberText(summary.total_calls) }}</span>
      </button>
      <button
        class="ai-tab"
        :class="{ active: activeSection === 'reports' }"
        type="button"
        @click="scrollToSection('sf-reports', 'reports')"
      >
        报告 <span class="sf-tab-count">{{ numberText(summary.report_count) }}</span>
      </button>
      <button
        class="ai-tab"
        :class="{ active: activeSection === 'data' }"
        type="button"
        @click="scrollToSection('sf-data', 'data')"
      >
        数据 <span class="sf-tab-count">{{ numberText(dataRecords.total) }}</span>
      </button>
      <button
        class="ai-tab"
        :class="{ active: activeSection === 'events' }"
        type="button"
        @click="scrollToSection('sf-events', 'events')"
      >
        调用记录 <span class="sf-tab-count">{{ numberText(recentEvents.total) }}</span>
      </button>
    </nav>

    <div class="sf-workspace ai-pagebody">
      <section class="sf-filter-panel" aria-label="SF 调用筛选">
        <div class="sf-filter-row sf-filter-row-primary">
          <label class="sf-field sf-field-narrow">
            <span>时间范围</span>
            <a-select v-model="days" size="small" @change="applyFilters">
              <a-option :value="7">近 7 天</a-option>
              <a-option :value="30">近 30 天</a-option>
              <a-option :value="90">近 90 天</a-option>
              <a-option :value="180">近 180 天</a-option>
            </a-select>
          </label>

          <label class="sf-field sf-field-kind">
            <span>调用类型</span>
            <div class="sf-segment">
              <button :class="{ active: kind === '' }" type="button" @click="setKind('')">全部</button>
              <button :class="{ active: kind === 'api' }" type="button" @click="setKind('api')">SF/API</button>
              <button :class="{ active: kind === 'mcp' }" type="button" @click="setKind('mcp')">MCP</button>
            </div>
          </label>

          <label class="sf-field sf-field-wide">
            <span>关键词</span>
            <a-input-search
              v-model="q"
              size="small"
              allow-clear
              placeholder="搜索用户 / 动作 / Skill / detail"
              @search="applyFilters"
              @press-enter="applyFilters"
            />
          </label>

          <div class="sf-filter-actions">
            <button class="ai-btn primary sf-apply" type="button" :disabled="loading" @click="applyFilters">
              <SfShellIcon name="filter" />
              应用筛选
            </button>
            <button v-if="activeFilterCount" class="ai-btn sf-reset" type="button" @click="resetFilters">清空 {{ activeFilterCount }}</button>
          </div>
        </div>

        <div class="sf-filter-row sf-filter-row-secondary">
          <label class="sf-field">
            <span>SF 能力</span>
            <a-select
              v-model="tool"
              size="small"
              allow-clear
              allow-search
              placeholder="全部 MCP 工具"
              @change="applyFilters"
            >
              <a-option v-for="item in toolOptions" :key="item.tool" :value="item.tool">
                {{ item.tool }} · {{ item.count }}
              </a-option>
            </a-select>
          </label>

          <label class="sf-field">
            <span>Skill ID</span>
            <a-input
              v-model="skillId"
              size="small"
              allow-clear
              placeholder="按 Skill 过滤"
              @press-enter="applyFilters"
            />
          </label>

          <label class="sf-field">
            <span>用户 ID</span>
            <a-input
              v-model="userId"
              size="small"
              allow-clear
              :disabled="isPersonalScope"
              :placeholder="isPersonalScope ? '个人范围不可切换' : '按用户过滤'"
              @press-enter="applyFilters"
            />
          </label>

          <label class="sf-field sf-field-wide">
            <span>报告关键词</span>
            <a-input-search
              v-model="reportQ"
              size="small"
              allow-clear
              placeholder="搜索报告标题 / 标签"
              @search="applyFilters"
              @press-enter="applyFilters"
            />
          </label>
        </div>

        <div class="sf-scope-row">
          <span class="ai-pill" :class="summary.scope === 'personal' ? 'info' : 'accent'">{{ scopeText }}</span>
          <span class="sf-scope-text">{{ scopeDescription }}</span>
          <span class="sf-scope-stat"><b>周期</b>{{ days }} 天</span>
          <span class="sf-scope-stat"><b>生成</b>{{ formatTime(summary.generated_at) }}</span>
        </div>
      </section>

      <main class="sf-content">
        <section id="sf-overview" class="sf-kpi-strip" :aria-busy="loading">
          <article v-for="item in kpis" :key="item.key" class="sf-kpi">
            <span class="sf-kpi-label">{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
            <em>{{ item.meta }}</em>
          </article>
        </section>


        <section class="sf-grid-three">
          <a-card class="sf-card ai-card" :bordered="false">
            <template #title>
              <div class="sf-card-title">
                <span>插件健康</span>
                <small>版本、会话、catalog 与 token 风险</small>
              </div>
            </template>
            <div class="health-list">
              <div class="health-meta">
                <span>最新版本</span><strong>{{ health.latest_version || '-' }}</strong>
                <span>活跃会话</span><strong>{{ numberText(health.active_sessions) }}</strong>
                <span>即将过期</span><strong>{{ numberText(health.expiring_sessions) }}</strong>
                <span>版本未知</span><strong>{{ numberText(health.version_unknown_sessions) }}</strong>
              </div>
              <div v-if="healthAlerts.length" class="alert-list">
                <article v-for="alert in healthAlerts" :key="alert.code" class="sf-alert" :class="alert.level">
                  <span class="sf-tag dot" :class="alert.level === 'danger' ? 'bad' : alert.level === 'warning' ? 'warn' : 'info'">{{ alert.level }}</span>
                  <p>{{ alert.message }}</p>
                </article>
              </div>
              <div v-else class="sf-inline-ok">
                <SfShellIcon name="check" />
                <span>当前范围暂无健康告警。</span>
              </div>
            </div>
          </a-card>

          <a-card class="sf-card ai-card" :bordered="false">
            <template #title>
              <div class="sf-card-title">
                <span>失败分析</span>
                <small>按工具和 error_code 聚合</small>
              </div>
            </template>
            <div v-if="failureTools.length || failureErrors.length" class="mini-list">
              <article v-for="item in failureTools.slice(0, 4)" :key="item.tool" class="mini-row">
                <code>{{ item.tool }}</code>
                <span>{{ item.failed_count }} 失败 · {{ item.failure_rate }}%</span>
              </article>
              <article v-for="item in failureErrors.slice(0, 3)" :key="item.error_code" class="mini-row subtle">
                <code>{{ item.error_code }}</code>
                <span>{{ item.count }} 次</span>
              </article>
            </div>
            <div v-else class="sf-inline-ok">
              <SfShellIcon name="check" />
              <span>暂无 MCP 失败记录。</span>
            </div>
          </a-card>

          <a-card class="sf-card ai-card" :bordered="false">
            <template #title>
              <div class="sf-card-title">
                <span>产出分析</span>
                <small>报告、待办和已读沉淀</small>
              </div>
            </template>
            <div class="health-meta">
              <span>报告</span><strong>{{ numberText(outputSummary.report_count) }}</strong>
              <span>待办</span><strong>{{ numberText(outputSummary.todo_count) }}</strong>
              <span>关联待办</span><strong>{{ numberText(outputSummary.related_todo_count) }}</strong>
              <span>报告打开</span><strong>{{ numberText(outputSummary.report_open_count) }}</strong>
            </div>
          </a-card>
        </section>

        <section class="sf-grid-two">
          <a-card class="sf-card ai-card" :bordered="false">
            <template #title>
              <div class="sf-card-title">
                <span>调用趋势</span>
                <small>{{ days }} 天 · API / MCP / 报告</small>
              </div>
            </template>
            <a-spin :loading="loading">
              <div v-if="dailyUsage.length" class="sf-trend" role="img" aria-label="SF 每日调用趋势">
                <div v-for="day in dailyUsage" :key="day.day" class="sf-trend-day" :title="trendTitle(day)">
                  <span class="sf-trend-stack">
                    <i class="api" :style="{ height: barHeight(day.api_calls, day) }"></i>
                    <i class="mcp" :style="{ height: barHeight(day.mcp_calls, day) }"></i>
                    <i class="report" :style="{ height: barHeight(day.report_count, day) }"></i>
                  </span>
                  <small>{{ shortDay(day.day) }}</small>
                </div>
              </div>
              <div v-else class="sf-empty small">
                <SfShellIcon name="trend" />
                <strong>暂无趋势数据</strong>
                <span>筛选周期内没有 sf 调用。</span>
              </div>
            </a-spin>
          </a-card>

          <a-card class="sf-card ai-card" :bordered="false">
            <template #title>
              <div class="sf-card-title">
                <span>调用了哪些 SF 能力</span>
                <small>MCP 工具排行，可点击过滤</small>
              </div>
            </template>
            <a-spin :loading="loading">
              <div v-if="topTools.length" class="tool-list">
                <button
                  v-for="item in topTools"
                  :key="item.tool"
                  class="tool-item"
                  :class="{ active: tool === item.tool }"
                  type="button"
                  @click="selectTool(item.tool)"
                >
                  <span class="tool-row">
                    <code>{{ item.tool }}</code>
                    <strong>{{ item.count }}</strong>
                  </span>
                  <span class="tool-bar"><i :style="{ width: toolWidth(item.count) }"></i></span>
                  <span class="tool-meta">
                    <span>{{ item.success_count || 0 }} 成功</span>
                    <span v-if="item.failed_count" class="bad">{{ item.failed_count }} 失败</span>
                    <span>{{ formatTime(item.last_called_at) }}</span>
                  </span>
                </button>
              </div>
              <div v-else class="sf-empty small">
                <SfShellIcon name="bolt" />
                <strong>暂无能力调用</strong>
                <span>执行 sf mcp call 或真实 MCP 验证后会产生记录。</span>
              </div>
            </a-spin>
          </a-card>
        </section>

        <section class="sf-grid-two">
          <a-card class="sf-card ai-card" :bordered="false">
            <template #title>
              <div class="sf-card-title">
                <span>用户 / 团队排行</span>
                <small>谁在用 SF、哪个团队更活跃</small>
              </div>
            </template>
            <div class="ranking-grid">
              <div>
                <h4>用户</h4>
                <article v-for="item in rankings.users?.slice(0, 6)" :key="item.user_id" class="mini-row">
                  <span>{{ displayUserName(item) }}</span>
                  <strong>{{ numberText(item.total_calls) }}</strong>
                </article>
              </div>
              <div>
                <h4>部门</h4>
                <article v-for="item in rankings.departments?.slice(0, 6)" :key="item.department" class="mini-row">
                  <span>{{ item.department }}</span>
                  <strong>{{ numberText(item.total_calls) }}</strong>
                </article>
              </div>
            </div>
          </a-card>

          <a-card class="sf-card ai-card" :bordered="false">
            <template #title>
              <div class="sf-card-title">
                <span>Skill 排行</span>
                <small>SF 调试或 MCP 调用最多的 Skill</small>
              </div>
            </template>
            <div class="mini-list">
              <article v-for="item in rankings.skills?.slice(0, 8)" :key="item.skill_id" class="mini-row">
                <button class="mono-link" type="button" @click="goSkill(item.skill_id)">{{ item.skill_name || item.skill_id }}</button>
                <strong>{{ numberText(item.mcp_calls) }}</strong>
              </article>
              <div v-if="!rankings.skills?.length" class="sf-inline-ok">
                <SfShellIcon name="cube" />
                <span>暂无 Skill 调用排行。</span>
              </div>
            </div>
          </a-card>
        </section>

        <section id="sf-reports">
          <a-card class="sf-card ai-card" :bordered="false">
            <template #title>
              <div class="sf-card-title">
                <span>SF 触发形成的报告</span>
                <small>{{ reports.length }} / {{ numberText(summary.report_count) }} · 可预览、跳转收件、导出 JSON</small>
              </div>
            </template>
            <template #extra>
              <button class="ai-btn sm" type="button" :disabled="!reports.length || exportingReports" @click="exportReports">
                <SfShellIcon name="download" />
                导出报告
              </button>
            </template>
            <a-spin :loading="loading">
              <div v-if="reports.length" class="report-list">
                <article v-for="report in reports" :key="report.id" class="report-item" @click="openReportPreview(report)">
                  <div class="report-topline">
                    <span class="sf-tag accent">{{ report.channel || 'report' }}</span>
                    <span>{{ formatTime(report.created_at) }}</span>
                  </div>
                  <h3>{{ report.title || '未命名报告' }}</h3>
                  <p>{{ report.summary || '无摘要' }}</p>
                  <div class="report-meta">
                    <span v-if="report.skill_name">{{ report.skill_name }}</span>
                    <button v-if="report.skill_id" type="button" class="mono-link" @click.stop="goSkill(report.skill_id)">{{ report.skill_id }}</button>
                    <button v-if="report.run_id" type="button" class="mono-link" @click.stop="goRun(report.run_id)">{{ report.run_id }}</button>
                  </div>
                  <div v-if="report.tags?.length" class="report-tags">
                    <span v-for="tag in report.tags.slice(0, 5)" :key="tag">{{ tag }}</span>
                  </div>
                  <div class="report-actions">
                    <button class="sf-link-btn" type="button" @click.stop="openReportPreview(report)">预览</button>
                    <button class="sf-link-btn" type="button" @click.stop="openReport(report.id)">打开收件</button>
                    <button class="sf-link-btn" type="button" @click.stop="copyText(report.id)">复制 ID</button>
                  </div>
                </article>
              </div>
              <div v-else class="sf-empty">
                <SfShellIcon name="doc" />
                <strong>暂无 SF 触发报告</strong>
                <span>当 Skill 通过 sf / Codex 触发并输出 reports 后，会显示在这里。</span>
              </div>
            </a-spin>
          </a-card>
        </section>

        <section id="sf-data">
          <a-card class="sf-card ai-card" :bordered="false">
            <template #title>
              <div class="sf-card-title">
                <span>SF 数据库</span>
                <small>{{ numberText(dataRecords.total) }} 条 · JSON / 文本 / 表格 / artifact 引用</small>
              </div>
            </template>
            <template #extra>
              <div class="sf-table-extra">
                <button class="ai-btn sm" type="button" :disabled="dataLoading" @click="loadDataRecords(false)">
                  <SfShellIcon name="refresh" />
                  刷新
                </button>
              </div>
            </template>
            <div class="sf-data-filters">
              <a-input v-model="dataNamespace" size="small" allow-clear placeholder="namespace" @press-enter="applyDataFilters" />
              <a-select v-model="dataContentType" size="small" allow-clear placeholder="类型" @change="applyDataFilters">
                <a-option value="json">JSON</a-option>
                <a-option value="text">文本</a-option>
                <a-option value="table">表格</a-option>
                <a-option value="binary">Artifact</a-option>
              </a-select>
              <a-input-search
                v-model="dataQuery"
                size="small"
                allow-clear
                placeholder="搜索标题 / 预览 / Skill / Run"
                @search="applyDataFilters"
                @press-enter="applyDataFilters"
              />
              <button class="ai-btn sm primary" type="button" :disabled="dataLoading" @click="applyDataFilters">
                <SfShellIcon name="filter" />
                筛选
              </button>
            </div>
            <a-table
              class="sf-table"
              :data="dataItems"
              :loading="dataLoading || loading"
              :pagination="dataPagination"
              row-key="id"
              size="small"
              @page-change="onDataPageChange"
              @row-click="openDataRecord"
            >
              <template #columns>
                <a-table-column title="时间" :width="150">
                  <template #cell="{ record }">{{ formatTime(record.created_at) }}</template>
                </a-table-column>
                <a-table-column title="数据">
                  <template #cell="{ record }">
                    <div class="event-main">
                      <code>{{ record.title || record.namespace }}</code>
                      <span>{{ record.text_preview || '-' }}</span>
                    </div>
                  </template>
                </a-table-column>
                <a-table-column title="类型" :width="112">
                  <template #cell="{ record }">
                    <span class="sf-tag" :class="record.artifact_ref ? 'warn' : 'info'">{{ dataTypeText(record) }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="来源" :width="190">
                  <template #cell="{ record }">
                    <div class="event-main">
                      <span>{{ record.source || '-' }}</span>
                      <small>{{ record.source_tool || record.source_ref || '-' }}</small>
                    </div>
                  </template>
                </a-table-column>
                <a-table-column title="Skill / Run" :width="190">
                  <template #cell="{ record }">
                    <div class="event-main">
                      <button v-if="record.skill_id" class="mono-link" type="button" @click.stop="goSkill(record.skill_id)">{{ record.skill_id }}</button>
                      <span v-else>-</span>
                      <small>{{ record.run_id || record.namespace || '-' }}</small>
                    </div>
                  </template>
                </a-table-column>
                <a-table-column title="大小" :width="92">
                  <template #cell="{ record }">{{ sizeText(record.size_bytes) }}</template>
                </a-table-column>
                <a-table-column title="操作" :width="86">
                  <template #cell="{ record }">
                    <button class="sf-link-btn" type="button" @click.stop="openDataRecord(record)">详情</button>
                  </template>
                </a-table-column>
              </template>
              <template #empty>
                <div class="sf-empty table-empty">
                  <SfShellIcon name="database" />
                  <strong>暂无 SF 数据</strong>
                  <span>通过 sf data write 或 skillforge_sf_data_write 写入后会显示在这里。</span>
                </div>
              </template>
            </a-table>
          </a-card>
        </section>

        <section id="sf-events">
          <a-card class="sf-card ai-card" :bordered="false">
            <template #title>
              <div class="sf-card-title">
                <span>最近调用记录</span>
                <small>{{ numberText(recentEvents.total) }} 条 · 点击行查看 detail JSON</small>
              </div>
            </template>
            <template #extra>
              <div class="sf-table-extra">
                <span v-if="activeFilterCount" class="ai-pill">已筛选 {{ activeFilterCount }}</span>
                <button class="ai-btn sm" type="button" :disabled="!events.length || exportingEvents" @click="exportEvents">
                  <SfShellIcon name="download" />
                  CSV
                </button>
              </div>
            </template>
            <a-table
              class="sf-table"
              :data="events"
              :loading="loading"
              :pagination="pagination"
              row-key="id"
              size="small"
              @page-change="onPageChange"
              @row-click="openEvent"
            >
              <template #columns>
                <a-table-column title="时间" :width="150">
                  <template #cell="{ record }">{{ formatTime(record.created_at) }}</template>
                </a-table-column>
                <a-table-column title="类型" :width="86">
                  <template #cell="{ record }">
                    <span class="sf-tag" :class="record.kind === 'mcp' ? 'warn' : 'info'">{{ record.kind === 'mcp' ? 'MCP' : 'SF' }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="功能 / 动作">
                  <template #cell="{ record }">
                    <div class="event-main">
                      <code>{{ record.tool || actionLabel(record.action) }}</code>
                      <span v-if="record.target_id || record.run_mode">{{ [record.target_id, record.run_mode].filter(Boolean).join(' · ') }}</span>
                    </div>
                  </template>
                </a-table-column>
                <a-table-column title="用户" :width="168">
                  <template #cell="{ record }">
                    <div class="event-main">
                      <span class="ink">{{ displayUserName(record) }}</span>
                      <small>{{ displayUserMeta(record) }}</small>
                    </div>
                  </template>
                </a-table-column>
                <a-table-column title="Skill / Scope" :width="180">
                  <template #cell="{ record }">
                    <div class="event-main">
                      <button v-if="record.skill_id" class="mono-link" type="button" @click.stop="goSkill(record.skill_id)">{{ record.skill_id }}</button>
                      <span v-else>-</span>
                      <small>{{ [record.shop_id, record.data_scope].filter(Boolean).join(' · ') || '-' }}</small>
                    </div>
                  </template>
                </a-table-column>
                <a-table-column title="结果" :width="96">
                  <template #cell="{ record }">
                    <span class="sf-tag dot" :class="resultClass(record)">{{ resultText(record) }}</span>
                  </template>
                </a-table-column>
                <a-table-column title="操作" :width="106">
                  <template #cell="{ record }">
                    <button class="sf-link-btn" type="button" @click.stop="openEvent(record)">详情</button>
                    <button class="sf-link-btn" type="button" @click.stop="openTrace(record)">链路</button>
                  </template>
                </a-table-column>
              </template>
              <template #empty>
                <div class="sf-empty table-empty">
                  <SfShellIcon name="list" />
                  <strong>暂无调用记录</strong>
                  <span>调整筛选条件或扩大时间范围后再试。</span>
                </div>
              </template>
            </a-table>
          </a-card>
        </section>
      </main>
    </div>

    <a-drawer v-model:visible="eventDrawerVisible" :width="620" :footer="false" title="调用详情">
      <div v-if="selectedEvent" class="sf-drawer">
        <div class="drawer-head">
          <span class="sf-tag" :class="selectedEvent.kind === 'mcp' ? 'warn' : 'info'">{{ selectedEvent.kind === 'mcp' ? 'MCP' : 'SF/API' }}</span>
          <span class="sf-tag dot" :class="resultClass(selectedEvent)">{{ resultText(selectedEvent) }}</span>
          <span v-if="selectedEvent.dry_run !== null && selectedEvent.dry_run !== undefined" class="ai-pill">{{ selectedEvent.dry_run ? 'dry-run' : 'real' }}</span>
        </div>
        <h2>{{ selectedEvent.tool || actionLabel(selectedEvent.action) }}</h2>
        <div class="drawer-grid">
          <span>时间</span><b>{{ formatTime(selectedEvent.created_at) }}</b>
          <span>用户</span><b>{{ displayUserName(selectedEvent) }}</b>
          <span>用户 ID</span><button class="mono-link" type="button" @click="copyText(selectedEvent.user_id)">{{ selectedEvent.user_id || '-' }}</button>
          <span>Skill</span><button class="mono-link" type="button" @click="goSkill(selectedEvent.skill_id)">{{ selectedEvent.skill_id || '-' }}</button>
          <span>运行模式</span><b>{{ selectedEvent.run_mode || '-' }}</b>
          <span>数据范围</span><b>{{ selectedEvent.data_scope || '-' }}</b>
          <span>记录 ID</span><button class="mono-link" type="button" @click="copyText(selectedEvent.id)">{{ selectedEvent.id }}</button>
        </div>
        <div class="drawer-actions">
          <button class="ai-btn sm" type="button" @click="copyEventDetail">
            <SfShellIcon name="copy" />
            复制 JSON
          </button>
          <button v-if="selectedEvent.skill_id" class="ai-btn sm" type="button" @click="goSkill(selectedEvent.skill_id)">
            <SfShellIcon name="cube" />
            打开 Skill
          </button>
        </div>
        <LearningFlowMini
          v-if="selectedEventFlowId"
          class="sf-drawer-flow"
          entity-type="sf_call"
          :entity-id="selectedEventFlowId"
          title="本次 SF 调用数据流"
          subtitle="查看本次 MCP 调用是否沉淀报告、知识或 SF 迭代候选。"
          compact
          :limit="6"
        />
        <pre class="json-box">{{ formatJson(selectedEvent.detail || selectedEvent) }}</pre>
      </div>
    </a-drawer>

    <a-drawer v-model:visible="dataDrawerVisible" :width="680" :footer="false" title="数据详情">
      <a-spin :loading="dataDetailLoading">
        <div v-if="selectedDataRecord" class="sf-drawer">
          <div class="drawer-head">
            <span class="sf-tag" :class="selectedDataRecord.artifact_ref ? 'warn' : 'info'">{{ dataTypeText(selectedDataRecord) }}</span>
            <span class="ai-pill">{{ selectedDataRecord.namespace || '-' }}</span>
            <span v-if="selectedDataRecord.visibility" class="ai-pill">{{ selectedDataRecord.visibility }}</span>
          </div>
          <h2>{{ selectedDataRecord.title || selectedDataRecord.id }}</h2>
          <p class="drawer-summary">{{ selectedDataRecord.text_preview || '无预览' }}</p>
          <div class="drawer-grid">
            <span>记录 ID</span><button class="mono-link" type="button" @click="copyText(selectedDataRecord.id)">{{ selectedDataRecord.id }}</button>
            <span>时间</span><b>{{ formatTime(selectedDataRecord.created_at) }}</b>
            <span>大小</span><b>{{ sizeText(selectedDataRecord.size_bytes) }}</b>
            <span>SHA256</span><button class="mono-link" type="button" @click="copyText(selectedDataRecord.sha256)">{{ selectedDataRecord.sha256 || '-' }}</button>
            <span>来源</span><b>{{ [selectedDataRecord.source, selectedDataRecord.source_tool].filter(Boolean).join(' · ') || '-' }}</b>
            <span>Skill</span><button class="mono-link" type="button" @click="goSkill(selectedDataRecord.skill_id)">{{ selectedDataRecord.skill_id || '-' }}</button>
            <span>Run</span><button class="mono-link" type="button" @click="goRun(selectedDataRecord.run_id)">{{ selectedDataRecord.run_id || '-' }}</button>
          </div>
          <div class="drawer-actions">
            <button class="ai-btn sm" type="button" @click="copyText(formatJson(selectedDataRecord))">
              <SfShellIcon name="copy" />
              复制 JSON
            </button>
            <button v-if="selectedDataRecord.skill_id" class="ai-btn sm" type="button" @click="goSkill(selectedDataRecord.skill_id)">
              <SfShellIcon name="cube" />
              打开 Skill
            </button>
          </div>
          <div class="data-detail-grid">
            <section>
              <h3>数据</h3>
              <pre class="json-box">{{ formatJson(selectedDataRecord.data || selectedDataRecord.artifact_ref || {}) }}</pre>
            </section>
            <section>
              <h3>元数据 / Schema</h3>
              <pre class="json-box">{{ formatJson({ metadata: selectedDataRecord.metadata || {}, schema: selectedDataRecord.schema || {} }) }}</pre>
            </section>
          </div>
        </div>
      </a-spin>
    </a-drawer>

    <a-drawer v-model:visible="reportDrawerVisible" :width="620" :footer="false" title="报告预览">
      <div v-if="selectedReport" class="sf-drawer">
        <div class="drawer-head">
          <span class="sf-tag accent">{{ selectedReport.channel || 'report' }}</span>
          <span class="ai-pill">{{ formatTime(selectedReport.created_at) }}</span>
        </div>
        <h2>{{ selectedReport.title || '未命名报告' }}</h2>
        <p class="drawer-summary">{{ selectedReport.summary || '无摘要' }}</p>
        <div class="report-tags drawer-tags" v-if="selectedReport.tags?.length">
          <span v-for="tag in selectedReport.tags" :key="tag">{{ tag }}</span>
        </div>
        <div class="drawer-grid">
          <span>报告 ID</span><button class="mono-link" type="button" @click="copyText(selectedReport.id)">{{ selectedReport.id }}</button>
          <span>Skill</span><button class="mono-link" type="button" @click="goSkill(selectedReport.skill_id)">{{ selectedReport.skill_id || '-' }}</button>
          <span>Run</span><button class="mono-link" type="button" @click="goRun(selectedReport.run_id)">{{ selectedReport.run_id || '-' }}</button>
          <span>DecisionLog</span><b>{{ selectedReport.decision_log_id || '-' }}</b>
        </div>
        <div v-if="selectedReport.metrics?.length" class="drawer-metrics">
          <article v-for="metric in selectedReport.metrics" :key="metric.label || metric.name">
            <span>{{ metric.label || metric.name }}</span>
            <strong>{{ metric.value }}</strong>
          </article>
        </div>
        <div class="drawer-actions">
          <button class="ai-btn sm primary" type="button" @click="openReport(selectedReport.id)">
            <SfShellIcon name="inbox" />
            打开收件详情
          </button>
          <button class="ai-btn sm" type="button" @click="copyText(formatJson(selectedReport))">
            <SfShellIcon name="copy" />
            复制报告 JSON
          </button>
        </div>
      </div>
    </a-drawer>

    <a-drawer v-model:visible="commandCenterDrawerVisible" :width="760" :footer="false" title="SF 命令中心">
      <div class="sf-drawer">
        <div class="drawer-toolbar">
          <a-input-search v-model="commandQuery" size="small" allow-clear placeholder="搜索命令 / 说明" />
          <span class="ai-pill">{{ numberText(commandCount) }} 条命令</span>
        </div>
        <div v-if="filteredCommandGroups.length" class="command-groups">
          <article v-for="group in filteredCommandGroups" :key="group.title" class="command-group">
            <h3>{{ group.title }}</h3>
            <div class="command-list">
              <div v-for="item in group.items" :key="item.command" class="command-row plain">
                <code>{{ item.command }}</code>
                <span>{{ item.description }}</span>
                <button class="sf-link-btn" type="button" @click="copyText(item.command)">复制</button>
              </div>
            </div>
          </article>
        </div>
        <div v-else class="sf-empty small">
          <SfShellIcon name="search" />
          <strong>没有匹配命令</strong>
          <span>换个关键词试试。</span>
        </div>
      </div>
    </a-drawer>

    <a-drawer v-model:visible="toolsCatalogDrawerVisible" :width="920" :footer="false" title="MCP 能力目录">
      <div class="sf-drawer">
        <div class="drawer-toolbar tool-filters">
          <a-input-search v-model="toolQuery" size="small" allow-clear placeholder="搜索工具" style="width: 220px" />
          <a-select v-model="toolPlatform" size="small" allow-clear placeholder="平台" style="width: 140px">
            <a-option v-for="item in catalogPlatforms" :key="item" :value="item">{{ item }}</a-option>
          </a-select>
          <a-select v-model="toolWriteFilter" size="small" allow-clear placeholder="读写" style="width: 120px">
            <a-option value="read">读取</a-option>
            <a-option value="write">写入</a-option>
          </a-select>
          <span class="ai-pill">{{ numberText(filteredCatalogTools.length) }} / {{ numberText(catalogTools.length) }}</span>
        </div>
        <div v-if="filteredCatalogTools.length" class="catalog-grid drawer-catalog-grid">
          <article v-for="item in filteredCatalogTools" :key="item.name" class="catalog-tool-card">
            <div class="tool-card-head">
              <button class="favorite-btn" :class="{ active: isFavoriteTool(item.name) }" type="button" @click="toggleFavoriteTool(item.name)">
                <SfShellIcon name="star" />
              </button>
              <code>{{ item.name }}</code>
              <span class="sf-tag" :class="item.write ? 'warn' : 'info'">{{ item.write ? '写' : '读' }}</span>
            </div>
            <p>{{ item.provides || item.description }}</p>
            <div class="tool-card-meta">
              <span>{{ item.platform || 'skillforge' }}</span>
              <span>{{ item.count || 0 }} 调用</span>
              <span>{{ item.success_rate === null || item.success_rate === undefined ? '-' : `${item.success_rate}%` }}</span>
            </div>
            <div class="tool-card-actions">
              <button class="sf-link-btn" type="button" @click="openToolDetail(item)">详情</button>
              <button class="sf-link-btn" type="button" @click="copyText(item.command_template)">复制命令</button>
              <button class="sf-link-btn" type="button" @click="openDryRun(item)">dry-run</button>
              <button class="sf-link-btn" type="button" @click="selectTool(item.name, true)">看调用</button>
            </div>
          </article>
        </div>
        <div v-else class="sf-empty small">
          <SfShellIcon name="bolt" />
          <strong>没有匹配工具</strong>
          <span>调整平台、读写或关键词筛选。</span>
        </div>
      </div>
    </a-drawer>

    <a-drawer v-model:visible="toolDrawerVisible" :width="620" :footer="false" title="MCP 能力详情">
      <div v-if="selectedTool" class="sf-drawer">
        <div class="drawer-head">
          <span class="sf-tag" :class="selectedTool.write ? 'warn' : 'info'">{{ selectedTool.write ? '写工具' : '读工具' }}</span>
          <span class="ai-pill">{{ selectedTool.platform || 'skillforge' }}</span>
          <span v-if="selectedTool.requires_shop_id" class="sf-tag warn">需要 shop_id</span>
        </div>
        <h2>{{ selectedTool.name }}</h2>
        <p class="drawer-summary">{{ selectedTool.provides || selectedTool.description }}</p>
        <p class="drawer-summary">{{ selectedTool.use_when }}</p>
        <div class="drawer-grid">
          <span>调用次数</span><b>{{ numberText(selectedTool.count) }}</b>
          <span>成功率</span><b>{{ selectedTool.success_rate === null || selectedTool.success_rate === undefined ? '-' : `${selectedTool.success_rate}%` }}</b>
          <span>最近调用</span><b>{{ formatTime(selectedTool.last_called_at) }}</b>
          <span>权限提示</span><b>{{ selectedTool.permission_hint || '-' }}</b>
        </div>
        <div class="drawer-actions">
          <button class="ai-btn sm" type="button" @click="copyText(selectedTool.command_template)">
            <SfShellIcon name="copy" />
            复制命令
          </button>
          <button class="ai-btn sm primary" type="button" @click="openDryRun(selectedTool)">
            <SfShellIcon name="play" />
            dry-run 调试
          </button>
          <button class="ai-btn sm" type="button" @click="selectTool(selectedTool.name)">
            <SfShellIcon name="list" />
            查看调用
          </button>
        </div>
        <pre class="json-box">{{ formatJson(selectedTool.input_schema || selectedTool.inputSchema || {}) }}</pre>
      </div>
    </a-drawer>

    <a-drawer v-model:visible="dryRunDrawerVisible" :width="660" :footer="false" title="MCP Dry-run 调试">
      <div class="sf-drawer">
        <div class="drawer-head">
          <span class="sf-tag info">仅 dry-run</span>
          <span v-if="selectedTool" class="ai-pill">{{ selectedTool.name }}</span>
        </div>
        <p class="drawer-summary">Web 侧不会真实写入或发送通知；后端强制 dry_run=true，并记录 proof。</p>
        <label class="sf-field">
          <span>Skill ID（可选，用于权限和 proof）</span>
          <a-input v-model="dryRunSkillId" allow-clear placeholder="skill_id" />
        </label>
        <label class="sf-field">
          <span>Shop ID（工具需要时填写）</span>
          <a-input v-model="dryRunShopId" allow-clear placeholder="shop_id" />
        </label>
        <label class="sf-field">
          <span>参数 JSON</span>
          <a-textarea v-model="dryRunArguments" :auto-size="{ minRows: 8, maxRows: 14 }" placeholder="{ }" />
        </label>
        <div class="drawer-actions">
          <button class="ai-btn primary" type="button" :disabled="dryRunLoading || !selectedTool" @click="runDryRun">
            <SfShellIcon name="play" />
            {{ dryRunLoading ? '执行中' : '执行 dry-run' }}
          </button>
          <button class="ai-btn" type="button" @click="copyText(dryRunArguments)">
            <SfShellIcon name="copy" />
            复制参数
          </button>
        </div>
        <pre v-if="dryRunResult" class="json-box">{{ formatJson(dryRunResult) }}</pre>
      </div>
    </a-drawer>

    <a-drawer v-model:visible="traceDrawerVisible" :width="660" :footer="false" title="SF 调用链路">
      <a-spin :loading="traceLoading">
        <div v-if="traceResult" class="sf-drawer">
          <div class="drawer-head">
            <span class="sf-tag" :class="traceResult.confidence === 'exact' ? 'ok' : 'warn'">{{ traceResult.confidence }}</span>
            <span v-if="traceResult.proof_id" class="ai-pill">proof {{ traceResult.proof_id }}</span>
          </div>
          <div v-if="traceResult.reasons?.length" class="alert-list">
            <article v-for="reason in traceResult.reasons" :key="reason" class="sf-alert warning">
              <span class="sf-tag warn">提示</span>
              <p>{{ reason }}</p>
            </article>
          </div>
          <div class="trace-grid">
            <article>
              <span>调用事件</span>
              <code>{{ traceResult.event?.tool || traceResult.event?.action || '-' }}</code>
            </article>
            <article>
              <span>Debug Run</span>
              <code>{{ traceResult.debug_run?.id || '-' }}</code>
            </article>
            <article>
              <span>Execution Run</span>
              <button v-if="traceResult.execution_run?.id" class="mono-link" type="button" @click="goRun(traceResult.execution_run.id)">{{ traceResult.execution_run.id }}</button>
              <code v-else>-</code>
            </article>
            <article>
              <span>报告 / 待办</span>
              <code>{{ traceResult.reports?.length || 0 }} / {{ traceResult.todos?.length || 0 }}</code>
            </article>
          </div>
          <div v-if="traceResult.commands?.length" class="command-list compact">
            <div v-for="item in traceResult.commands" :key="item.command" class="command-row">
              <code>{{ item.command }}</code>
              <span>{{ item.label }}</span>
              <button class="sf-link-btn" type="button" @click="copyText(item.command)">复制</button>
            </div>
          </div>
          <pre class="json-box">{{ formatJson(traceResult) }}</pre>
        </div>
      </a-spin>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { sfApi } from '@/api'
import { formatTime } from '@/utils/format'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'
import LearningFlowMini from '@/components/learning/LearningFlowMini.vue'

type SfRecord = Record<string, any>

type KpiItem = {
  key: string
  label: string
  value: string
  meta: string
}

const router = useRouter()
const loading = ref(false)
const exportingEvents = ref(false)
const exportingReports = ref(false)
const days = ref(30)
const page = ref(1)
const pageSize = 50
const kind = ref<'' | 'api' | 'mcp'>('')
const q = ref('')
const reportQ = ref('')
const tool = ref('')
const skillId = ref('')
const userId = ref('')
const dataNamespace = ref('')
const dataContentType = ref('')
const dataQuery = ref('')
const dataPage = ref(1)
const dataPageSize = 20
const activeSection = ref<'overview' | 'reports' | 'data' | 'events'>('overview')

const summary = ref<SfRecord>({})
const topTools = ref<SfRecord[]>([])
const reports = ref<SfRecord[]>([])
const dailyUsage = ref<SfRecord[]>([])
const recentEvents = ref<SfRecord>({ total: 0, page: 1, page_size: pageSize, items: [] })
const dataRecords = ref<SfRecord>({ total: 0, page: 1, page_size: dataPageSize, items: [] })
const selectedEvent = ref<SfRecord | null>(null)
const selectedReport = ref<SfRecord | null>(null)
const selectedDataRecord = ref<SfRecord | null>(null)
const selectedTool = ref<SfRecord | null>(null)
const eventDrawerVisible = ref(false)
const reportDrawerVisible = ref(false)
const dataDrawerVisible = ref(false)
const commandCenterDrawerVisible = ref(false)
const toolsCatalogDrawerVisible = ref(false)
const toolDrawerVisible = ref(false)
const dryRunDrawerVisible = ref(false)
const traceDrawerVisible = ref(false)
const dryRunLoading = ref(false)
const traceLoading = ref(false)
const dataLoading = ref(false)
const dataDetailLoading = ref(false)
const dryRunArguments = ref('{}')
const dryRunSkillId = ref('')
const dryRunShopId = ref('')
const dryRunResult = ref<SfRecord | null>(null)
const traceResult = ref<SfRecord | null>(null)
const catalog = ref<SfRecord>({ command_groups: [], commands: [], mcp_capabilities: { tools: [] }, plugin_update: {} })
const commandQuery = ref('')
const toolQuery = ref('')
const toolPlatform = ref('')
const toolWriteFilter = ref('')
const favoriteTools = ref<string[]>(loadStoredList('sf:favorites:tools:v1'))
const health = ref<SfRecord>({})
const failureSummary = ref<SfRecord>({ by_tool: [], by_error_code: [], recent: [] })
const rankings = ref<SfRecord>({ users: [], departments: [], skills: [] })
const outputSummary = ref<SfRecord>({})

const events = computed<SfRecord[]>(() => recentEvents.value.items || [])
const dataItems = computed<SfRecord[]>(() => dataRecords.value.items || [])
const refreshing = computed(() => loading.value || dataLoading.value)
const commandGroups = computed<SfRecord[]>(() => catalog.value.command_groups || [])
const commandCount = computed(() => (catalog.value.commands || []).length)
const catalogTools = computed<SfRecord[]>(() => catalog.value.mcp_capabilities?.tools || [])
const healthAlerts = computed<SfRecord[]>(() => health.value.alerts || [])
const failureTools = computed<SfRecord[]>(() => failureSummary.value.by_tool || [])
const failureErrors = computed<SfRecord[]>(() => failureSummary.value.by_error_code || [])
const catalogPlatforms = computed(() => Array.from(new Set(catalogTools.value.map((item) => item.platform || 'skillforge'))).sort())
const filteredCommandGroups = computed<SfRecord[]>(() => {
  const query = commandQuery.value.trim().toLowerCase()
  return commandGroups.value
    .map((group) => {
      const items = (group.items || []).filter((item: SfRecord) => {
        if (!query) return true
        return `${item.command || ''}
${item.description || ''}`.toLowerCase().includes(query)
      })
      return { ...group, items }
    })
    .filter((group: SfRecord) => group.items.length)
})
const filteredCatalogTools = computed(() => {
  const query = toolQuery.value.trim().toLowerCase()
  return catalogTools.value.filter((item) => {
    const text = `${item.name || ''}\n${item.description || ''}\n${item.provides || ''}\n${item.use_when || ''}`.toLowerCase()
    if (query && !text.includes(query)) return false
    if (toolPlatform.value && item.platform !== toolPlatform.value) return false
    if (toolWriteFilter.value === 'read' && item.write) return false
    if (toolWriteFilter.value === 'write' && !item.write) return false
    return true
  })
})
const isPersonalScope = computed(() => summary.value.scope === 'personal')
const scopeText = computed(() => isPersonalScope.value ? '我的范围' : '全局范围')
const scopeDescription = computed(() => (
  isPersonalScope.value
    ? '当前账号仅查看自己通过 sf / Codex 产生的会话、调用和报告；用户筛选会自动锁定为本人。'
    : '管理员视角汇总全平台 sf / Codex 会话、调用审计、MCP 工具和触发报告。'
))
const successRateText = computed(() => {
  const value = summary.value.success_rate
  return value === null || value === undefined ? '-' : `${value}%`
})
const activeFilterCount = computed(() => [
  kind.value,
  q.value.trim(),
  reportQ.value.trim(),
  tool.value,
  skillId.value.trim(),
  isPersonalScope.value ? '' : userId.value.trim(),
].filter(Boolean).length)
const maxToolCount = computed(() => Math.max(1, ...topTools.value.map((item) => Number(item.count || 0))))
const maxTrendValue = computed(() => Math.max(1, ...dailyUsage.value.map((item) => Math.max(Number(item.total_calls || 0), Number(item.report_count || 0)))))
const toolOptions = computed(() => {
  const existing = new Map<string, SfRecord>()
  for (const item of topTools.value) {
    if (item.tool) existing.set(String(item.tool), item)
  }
  if (tool.value && !existing.has(tool.value)) existing.set(tool.value, { tool: tool.value, count: 0 })
  return Array.from(existing.values())
})
const selectedEventFlowId = computed(() => selectedEvent.value?.kind === 'mcp' && selectedEvent.value?.id ? String(selectedEvent.value.id) : '')
const pagination = computed(() => ({
  current: page.value,
  pageSize,
  total: recentEvents.value.total || 0,
  showTotal: true,
  showPageSize: false,
}))
const dataPagination = computed(() => ({
  current: dataPage.value,
  pageSize: dataPageSize,
  total: dataRecords.value.total || 0,
  showTotal: true,
  showPageSize: false,
}))
const kpis = computed<KpiItem[]>(() => [
  {
    key: 'total',
    label: 'SF 调用',
    value: numberText(summary.value.total_calls),
    meta: `${numberText(summary.value.api_calls)} API · ${numberText(summary.value.mcp_calls)} MCP`,
  },
  {
    key: 'sessions',
    label: '活跃会话',
    value: numberText(summary.value.active_sessions),
    meta: `${numberText(summary.value.sf_users || summary.value.plugin_users)} 个插件用户`,
  },
  {
    key: 'reports',
    label: '形成报告',
    value: numberText(summary.value.report_count),
    meta: 'SF 触发 reports 输出',
  },
  {
    key: 'failed',
    label: '失败 MCP',
    value: numberText(summary.value.failed_mcp_calls),
    meta: `成功率 ${successRateText.value}`,
  },
  {
    key: 'debug',
    label: '调试运行',
    value: numberText(summary.value.debug_runs),
    meta: `${scopeText.value} · ${days.value} 天`,
  },
])

function numberText(value: unknown) {
  return Number(value || 0).toLocaleString('zh-CN')
}

function loadStoredList(key: string) {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || '[]')
    return Array.isArray(parsed) ? parsed.filter((item) => typeof item === 'string') : []
  } catch {
    return []
  }
}

function saveStoredList(key: string, values: string[]) {
  try {
    localStorage.setItem(key, JSON.stringify(values))
  } catch {
    // ignore localStorage failures
  }
}

function clean(value: string) {
  return value.trim() || undefined
}

function requestParams(resetPage = false) {
  if (resetPage) page.value = 1
  const params: SfRecord = {
    days: days.value,
    page: page.value,
    page_size: pageSize,
  }
  if (kind.value) params.kind = kind.value
  if (tool.value) params.tool = tool.value
  if (clean(q.value)) params.q = clean(q.value)
  if (clean(reportQ.value)) params.report_q = clean(reportQ.value)
  if (clean(skillId.value)) params.skill_id = clean(skillId.value)
  if (!isPersonalScope.value && clean(userId.value)) params.user_id = clean(userId.value)
  return params
}

async function loadOverview(resetPage = false) {
  loading.value = true
  try {
    const res: any = await sfApi.overview(requestParams(resetPage))
    summary.value = res?.summary || {}
    topTools.value = res?.top_tools || []
    reports.value = res?.reports || []
    dailyUsage.value = res?.daily_usage || []
    recentEvents.value = res?.recent_events || { total: 0, page: page.value, page_size: pageSize, items: [] }
    health.value = res?.health || {}
    failureSummary.value = res?.failure_summary || { by_tool: [], by_error_code: [], recent: [] }
    rankings.value = res?.rankings || { users: [], departments: [], skills: [] }
    outputSummary.value = res?.output_summary || {}
    if (isPersonalScope.value) userId.value = String(res?.filters?.user_id || '')
  } catch (err: any) {
    Message.error(err?.message || '加载 SF 调用情况失败')
  } finally {
    loading.value = false
  }
}

async function loadCatalog() {
  try {
    const res: any = await sfApi.catalog({ days: days.value })
    catalog.value = res || { command_groups: [], commands: [], mcp_capabilities: { tools: [] } }
  } catch (err: any) {
    Message.error(err?.message || '加载 SF 命令和 MCP 目录失败')
  }
}

async function loadAll(resetPage = false) {
  await Promise.all([loadOverview(resetPage), loadCatalog(), loadDataRecords(resetPage)])
}

function refreshOverview() {
  loadAll(false)
}

function applyFilters() {
  loadAll(true)
}

function resetFilters() {
  kind.value = ''
  q.value = ''
  reportQ.value = ''
  tool.value = ''
  skillId.value = ''
  if (!isPersonalScope.value) userId.value = ''
  loadAll(true)
}

function setKind(next: '' | 'api' | 'mcp') {
  kind.value = next
  if (next !== 'mcp') tool.value = ''
  applyFilters()
}

function selectTool(nextTool: string, jumpToEvents = false) {
  tool.value = tool.value === nextTool ? '' : nextTool
  if (tool.value) kind.value = 'mcp'
  if (jumpToEvents) toolsCatalogDrawerVisible.value = false
  applyFilters()
  if (jumpToEvents) requestAnimationFrame(() => scrollToSection('sf-events', 'events'))
}

function onPageChange(nextPage: number) {
  page.value = nextPage
  loadOverview(false)
}

function dataRequestParams(resetPage = false) {
  if (resetPage) dataPage.value = 1
  const params: SfRecord = {
    page: dataPage.value,
    page_size: dataPageSize,
  }
  if (clean(dataNamespace.value)) params.namespace = clean(dataNamespace.value)
  if (dataContentType.value) params.content_type = dataContentType.value
  if (clean(dataQuery.value)) params.q = clean(dataQuery.value)
  if (clean(skillId.value)) params.skill_id = clean(skillId.value)
  return params
}

async function loadDataRecords(resetPage = false) {
  dataLoading.value = true
  try {
    const res: any = await sfApi.data(dataRequestParams(resetPage))
    dataRecords.value = res || { total: 0, page: dataPage.value, page_size: dataPageSize, items: [] }
    dataPage.value = Number(dataRecords.value.page || dataPage.value)
  } catch (err: any) {
    Message.error(err?.message || '加载 SF 数据失败')
  } finally {
    dataLoading.value = false
  }
}

function applyDataFilters() {
  loadDataRecords(true)
}

function onDataPageChange(nextPage: number) {
  dataPage.value = nextPage
  loadDataRecords(false)
}

function scrollToSection(id: string, section?: typeof activeSection.value) {
  if (section) activeSection.value = section
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function toolWidth(count: unknown) {
  const pct = Math.max(8, Math.round((Number(count || 0) / maxToolCount.value) * 100))
  return `${pct}%`
}

function barHeight(value: unknown, day: SfRecord) {
  const total = Math.max(Number(value || 0), Number(day.total_calls || 0) ? 0 : Number(day.report_count || 0))
  const pct = Math.round((total / maxTrendValue.value) * 100)
  return `${Math.max(total ? 8 : 2, pct)}%`
}

function trendTitle(day: SfRecord) {
  return `${day.day}: API ${day.api_calls || 0}, MCP ${day.mcp_calls || 0}, 失败 ${day.failed_mcp_calls || 0}, 报告 ${day.report_count || 0}`
}

function shortDay(value?: string) {
  if (!value) return '-'
  const parts = value.split('-')
  return parts.length === 3 ? `${parts[1]}/${parts[2]}` : value
}

function displayUserName(record: SfRecord) {
  return record.user_name || record.username || record.user_id || '-'
}

function displayUserMeta(record: SfRecord) {
  return [record.department, record.role].filter(Boolean).join(' · ') || record.user_id || '-'
}

function actionLabel(action?: string) {
  const raw = String(action || '')
  if (!raw) return '-'
  return raw.replace(/^codex\./, 'sf.')
}

function resultClass(record: SfRecord) {
  if (record.kind !== 'mcp') return 'ok'
  return record.ok ? 'ok' : 'bad'
}

function resultText(record: SfRecord) {
  if (record.kind !== 'mcp') return '已记录'
  return record.ok ? '成功' : '失败'
}

function openEvent(record: SfRecord) {
  selectedEvent.value = record
  eventDrawerVisible.value = true
}

function openReportPreview(report: SfRecord) {
  selectedReport.value = report
  reportDrawerVisible.value = true
}

async function openDataRecord(record: SfRecord) {
  selectedDataRecord.value = record
  dataDrawerVisible.value = true
  dataDetailLoading.value = true
  try {
    selectedDataRecord.value = (await sfApi.dataDetail(String(record.id))) as SfRecord
  } catch (err: any) {
    Message.error(err?.message || '加载数据详情失败')
  } finally {
    dataDetailLoading.value = false
  }
}

function isFavoriteTool(name: string) {
  return favoriteTools.value.includes(name)
}

function toggleFavoriteTool(name: string) {
  favoriteTools.value = isFavoriteTool(name)
    ? favoriteTools.value.filter((item) => item !== name)
    : [name, ...favoriteTools.value]
  saveStoredList('sf:favorites:tools:v1', favoriteTools.value)
}

function openToolDetail(item: SfRecord) {
  selectedTool.value = item
  toolsCatalogDrawerVisible.value = false
  toolDrawerVisible.value = true
}

function sampleArgumentsForTool(item: SfRecord) {
  const sample: SfRecord = {}
  if (item.requires_shop_id) sample.shop_id = dryRunShopId.value || '<shop_id>'
  if (item.write) sample.dry_run = true
  return formatJson(sample)
}

function openDryRun(item: SfRecord) {
  toolsCatalogDrawerVisible.value = false
  selectedTool.value = item
  dryRunResult.value = null
  dryRunArguments.value = sampleArgumentsForTool(item)
  dryRunDrawerVisible.value = true
}

async function runDryRun() {
  if (!selectedTool.value || dryRunLoading.value) return
  let args: SfRecord
  try {
    args = JSON.parse(dryRunArguments.value || '{}')
  } catch {
    Message.error('参数 JSON 格式不正确')
    return
  }
  dryRunLoading.value = true
  try {
    const res: any = await sfApi.dryRunMcp({
      tool: selectedTool.value.name,
      arguments: args,
      skill_id: clean(dryRunSkillId.value),
      shop_id: clean(dryRunShopId.value),
    })
    dryRunResult.value = res || {}
    Message.success('dry-run 执行完成')
    await loadOverview(false)
    await loadCatalog()
  } catch (err: any) {
    Message.error(err?.message || 'dry-run 执行失败')
    dryRunResult.value = err?.detail || { error: err?.message || 'dry-run 执行失败' }
  } finally {
    dryRunLoading.value = false
  }
}

async function openTrace(record: SfRecord) {
  traceDrawerVisible.value = true
  traceLoading.value = true
  traceResult.value = null
  try {
    traceResult.value = (await sfApi.trace({ event_id: record.id })) as SfRecord
  } catch (err: any) {
    Message.error(err?.message || '加载调用链路失败')
  } finally {
    traceLoading.value = false
  }
}

function openReport(id: string) {
  if (!id) return
  router.push(`/inbox/reports/${encodeURIComponent(id)}`)
}

function goSkill(id?: string) {
  if (!id) return
  router.push(`/skills/${encodeURIComponent(id)}`)
}

function goRun(id?: string) {
  if (!id) return
  router.push(`/execution/${encodeURIComponent(id)}`)
}

function formatJson(value: unknown) {
  try {
    return JSON.stringify(value || {}, null, 2)
  } catch {
    return String(value || '')
  }
}

function dataTypeText(record: SfRecord) {
  const type = String(record.content_type || 'json')
  if (record.artifact_ref) return `${type} / artifact`
  if (type === 'table') return '表格'
  if (type === 'text') return '文本'
  if (type === 'binary') return '二进制'
  return 'JSON'
}

function sizeText(value: unknown) {
  const size = Number(value || 0)
  if (!size) return '0 B'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

async function copyText(value: unknown) {
  const text = String(value || '')
  if (!text || text === '-') return
  try {
    await navigator.clipboard.writeText(text)
    Message.success('已复制')
  } catch {
    Message.warning('复制失败，请手动复制')
  }
}

function copyEventDetail() {
  if (!selectedEvent.value) return
  copyText(formatJson(selectedEvent.value))
}

function csvEscape(value: unknown) {
  const text = String(value ?? '').replace(/"/g, '""')
  return `"${text}"`
}

function downloadFile(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

async function exportEvents() {
  if (!events.value.length || exportingEvents.value) return
  exportingEvents.value = true
  try {
    const res: any = await sfApi.overview({ ...requestParams(false), page: 1, page_size: 200 })
    const exportItems: SfRecord[] = res?.recent_events?.items || events.value
    const headers = ['id', 'created_at', 'kind', 'tool_or_action', 'user_id', 'skill_id', 'run_mode', 'dry_run', 'ok', 'data_scope']
    const rows = exportItems.map((item) => [
      item.id,
      item.created_at,
      item.kind,
      item.tool || item.action,
      item.user_id,
      item.skill_id,
      item.run_mode,
      item.dry_run,
      item.ok,
      item.data_scope,
    ])
    const csv = [headers, ...rows].map((row) => row.map(csvEscape).join(',')).join('\n')
    downloadFile(`sf-events-${new Date().toISOString().slice(0, 10)}.csv`, `\ufeff${csv}`, 'text/csv;charset=utf-8')
    Message.success(`已导出 ${exportItems.length} 条调用记录`)
  } catch (err: any) {
    Message.error(err?.message || '导出调用记录失败')
  } finally {
    exportingEvents.value = false
  }
}

function exportReports() {
  if (!reports.value.length || exportingReports.value) return
  exportingReports.value = true
  try {
    downloadFile(`sf-reports-${new Date().toISOString().slice(0, 10)}.json`, formatJson(reports.value), 'application/json;charset=utf-8')
    Message.success(`已导出 ${reports.value.length} 份报告`)
  } finally {
    exportingReports.value = false
  }
}

onMounted(() => loadAll(false))
</script>

<style scoped>
.sf-page {
  min-height: 100%;
  background: var(--ai-bg);
}

.sf-pagehead {
  align-items: flex-start;
}

.sf-section-tabs {
  position: sticky;
  top: 0;
  z-index: 4;
  overflow-x: auto;
}

.sf-section-tabs .ai-tab {
  border: 0;
  border-bottom: 1.5px solid transparent;
  background: transparent;
  white-space: nowrap;
}

.sf-tab-count {
  min-width: 16px;
  height: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 5px;
  border-radius: 4px;
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
}

.sf-section-tabs .ai-tab.active .sf-tab-count {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
}

.sf-head-actions,
.sf-table-extra,
.drawer-head,
.drawer-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.sf-drawer-flow {
  margin: 14px 0;
}

.sf-head-actions .ai-btn,
.sf-table-extra .ai-btn,
.drawer-actions .ai-btn,
.tool-card-actions .sf-link-btn,
.report-actions .sf-link-btn {
  white-space: nowrap;
}

.sf-page .ai-title {
  letter-spacing: 0;
}

.sf-workspace {
  display: flex;
  flex-direction: column;
  gap: 18px;
  align-items: stretch;
}

.sf-filter-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}

.sf-filter-row {
  display: grid;
  gap: 12px;
  align-items: end;
  min-width: 0;
}

.sf-filter-row-primary {
  grid-template-columns: minmax(128px, 0.35fr) minmax(220px, 0.45fr) minmax(260px, 1fr) auto;
}

.sf-filter-row-secondary {
  grid-template-columns: minmax(260px, 1fr) minmax(180px, 0.55fr) minmax(180px, 0.55fr) minmax(220px, 0.8fr);
}

.sf-field {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 6px;
  margin: 0;
}

.sf-field > span {
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-4);
}

.sf-field-kind {
  min-width: 220px;
}

.sf-filter-actions {
  display: flex;
  align-items: end;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.sf-filter-actions .sf-apply {
  width: auto;
}

.sf-segment {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  padding: 2px;
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface-2);
}

.sf-segment button {
  height: 26px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--ai-ink-3);
  font-size: 12px;
  font-weight: 500;
}

.sf-segment button.active {
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  box-shadow: var(--ai-shadow-1);
}

.sf-scope-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding-top: 10px;
  border-top: 1px solid var(--ai-border);
  color: var(--ai-ink-3);
  font-size: 12px;
}

.sf-scope-text {
  flex: 1 1 320px;
  min-width: 0;
  color: var(--ai-ink-3);
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.sf-scope-stat {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--ai-ink-2);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.sf-scope-stat b {
  color: var(--ai-ink-4);
  font-weight: 500;
}

.drawer-grid {
  display: grid;
  grid-template-columns: 82px minmax(0, 1fr);
  gap: 8px 10px;
  align-items: center;
  font-size: 12px;
}

.drawer-grid span {
  color: var(--ai-ink-4);
}

.drawer-grid b {
  min-width: 0;
  color: var(--ai-ink-2);
  font-weight: 500;
  overflow-wrap: anywhere;
}

.sf-content {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 18px;
  scroll-padding-top: 48px;
}

.sf-content > section {
  scroll-margin-top: 48px;
}

.sf-page :deep(.sf-shell-icon) {
  width: 13px;
  height: 13px;
}

.sf-kpi-strip {
  display: flex;
  align-items: stretch;
  overflow: hidden;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}

.sf-kpi {
  min-width: 0;
  min-height: 92px;
  flex: 1 1 0;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-left: 1px solid var(--ai-border);
  background: transparent;
}

.sf-kpi:first-child {
  border-left: 0;
}

.sf-kpi-label {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
}

.sf-kpi strong {
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 24px;
  line-height: 1.1;
  font-weight: 600;
  letter-spacing: 0;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

.sf-kpi em {
  margin-top: auto;
  color: var(--ai-ink-3);
  font-style: normal;
  font-size: 12px;
  line-height: 1.35;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sf-grid-two {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
  gap: 18px;
  align-items: stretch;
}

.sf-grid-three {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
  align-items: stretch;
}

.sf-card {
  border-radius: var(--ai-radius) !important;
  overflow: hidden;
}

.sf-page :deep(.arco-card) {
  border: 1px solid var(--ai-border) !important;
  background: var(--ai-surface) !important;
  box-shadow: none !important;
  transform: none !important;
}

.sf-page :deep(.arco-card:hover) {
  box-shadow: none !important;
  transform: none !important;
}

.sf-card :deep(.arco-card-header) {
  min-height: 0;
  padding: 12px 14px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
}

.sf-card :deep(.arco-card-header-title) {
  min-width: 0;
}

.sf-card :deep(.arco-card-extra) {
  min-width: 0;
  margin-left: auto;
}

.sf-card :deep(.arco-card-body) {
  padding: 14px;
}

.sf-card-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sf-card-title span {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
}

.sf-card-title small {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 400;
}

.sf-trend {
  display: flex;
  gap: 6px;
  align-items: end;
  min-height: 210px;
  overflow-x: auto;
  padding: 0 0 2px;
}

.sf-trend-day {
  min-width: 0;
  flex: 0 0 22px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}

.sf-trend-stack {
  height: 170px;
  width: 100%;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  gap: 2px;
  padding: 0 1px;
  border-bottom: 1px solid var(--ai-border);
}

.sf-trend-stack i {
  width: 4px;
  min-height: 2px;
  display: block;
  border-radius: 2px 2px 0 0;
  background: var(--ai-border-3);
}

.sf-trend-stack .api { background: var(--ai-ink-3); }
.sf-trend-stack .mcp { background: var(--ai-accent); }
.sf-trend-stack .report { background: var(--ai-ok); }

.sf-trend-day small {
  color: var(--ai-ink-4);
  font-size: 10.5px;
  white-space: nowrap;
}

.tool-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 250px;
  overflow: auto;
  padding-right: 2px;
}

.tool-item {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  color: inherit;
  text-align: left;
}

.tool-item:hover,
.tool-item.active {
  border-color: var(--ai-border-2);
  background: var(--ai-surface-2);
}

.tool-row,
.tool-meta,
.report-topline,
.report-meta,
.report-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.tool-row {
  justify-content: space-between;
}

.tool-row code,
.event-main code,
.mono-link,
.json-box {
  font-family: var(--ai-font-mono);
}

.tool-row code,
.event-main code {
  min-width: 0;
  color: var(--ai-ink-1);
  font-size: 11.5px;
  overflow-wrap: anywhere;
}

.tool-row strong {
  color: var(--ai-ink-1);
  font-variant-numeric: tabular-nums;
}

.tool-bar {
  height: 5px;
  overflow: hidden;
  border-radius: 99px;
  background: var(--ai-surface-3);
}

.tool-bar i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--ai-accent);
}

.tool-meta,
.report-topline,
.report-meta {
  color: var(--ai-ink-4);
  font-size: 12px;
}

.tool-meta .bad {
  color: var(--ai-bad);
  font-weight: 500;
}

.health-list,
.mini-list,
.command-groups,
.command-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.health-meta {
  display: grid;
  grid-template-columns: 82px minmax(0, 1fr);
  gap: 8px 10px;
  font-size: 12px;
}

.health-meta span {
  color: var(--ai-ink-4);
}

.health-meta strong {
  color: var(--ai-ink-1);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.alert-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sf-alert {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 9px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
}

.sf-alert p {
  margin: 0;
  color: var(--ai-ink-3);
  font-size: 12.5px;
  line-height: 1.45;
}

.sf-inline-ok {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  color: var(--ai-ink-3);
  font-size: 12.5px;
}

.mini-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 30px;
  padding: 7px 0;
  border-bottom: 1px solid var(--ai-border);
  color: var(--ai-ink-3);
  font-size: 12.5px;
}

.mini-row:last-child {
  border-bottom: 0;
}

.mini-row code {
  min-width: 0;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  overflow-wrap: anywhere;
}

.mini-row strong {
  color: var(--ai-ink-1);
  font-variant-numeric: tabular-nums;
}

.mini-row.subtle {
  opacity: 0.8;
}

.command-group h3,
.ranking-grid h4 {
  margin: 0 0 8px;
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 600;
}

.command-row {
  display: grid;
  grid-template-columns: 24px minmax(180px, 0.65fr) minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  min-height: 34px;
  padding: 7px 0;
  border-bottom: 1px solid var(--ai-border);
}

.command-list.compact .command-row {
  grid-template-columns: minmax(0, 1fr) 110px auto;
}

.command-row.plain {
  grid-template-columns: minmax(180px, 0.65fr) minmax(0, 1fr) auto;
}

.command-row:last-child {
  border-bottom: 0;
}

.command-row code {
  min-width: 0;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  overflow-wrap: anywhere;
}

.command-row span {
  color: var(--ai-ink-3);
  font-size: 12px;
}

.favorite-btn {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 5px;
  color: var(--ai-ink-4);
  background: transparent;
}

.favorite-btn.active,
.favorite-btn:hover {
  color: var(--ai-warn);
  background: var(--ai-warn-soft);
}

.drawer-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: space-between;
}

.drawer-toolbar .arco-input-wrapper {
  max-width: 320px;
}

.tool-filters {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.catalog-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.catalog-tool-card {
  display: flex;
  min-height: 188px;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}

.catalog-tool-card:hover {
  border-color: var(--ai-border-2);
  background: var(--ai-surface-2);
}

.tool-card-head,
.tool-card-meta,
.tool-card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.tool-card-head code {
  min-width: 0;
  flex: 1;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  overflow-wrap: anywhere;
}

.catalog-tool-card p {
  margin: 0;
  color: var(--ai-ink-3);
  font-size: 12.5px;
  line-height: 1.5;
}

.tool-card-meta {
  margin-top: auto;
  color: var(--ai-ink-4);
  font-size: 12px;
}

.ranking-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.sf-data-filters {
  display: grid;
  grid-template-columns: minmax(180px, 0.7fr) 130px minmax(240px, 1fr) auto;
  gap: 10px;
  align-items: center;
  margin-bottom: 12px;
}

.data-detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.data-detail-grid section {
  min-width: 0;
}

.data-detail-grid h3 {
  margin: 0 0 8px;
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 600;
}

.trace-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.trace-grid article {
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}

.trace-grid span {
  display: block;
  color: var(--ai-ink-4);
  font-size: 11.5px;
}

.trace-grid code {
  display: block;
  margin-top: 5px;
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  overflow-wrap: anywhere;
}

.report-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.report-item {
  min-height: 206px;
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  cursor: pointer;
}

.report-item:hover {
  border-color: var(--ai-border-2);
  background: var(--ai-surface-2);
}

.report-item h3 {
  margin: 0;
  color: var(--ai-ink-1);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.35;
}

.report-item p,
.drawer-summary {
  margin: 0;
  color: var(--ai-ink-3);
  font-size: 12.5px;
  line-height: 1.55;
}

.report-item p {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.report-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: auto;
}

.report-tags span {
  min-height: 20px;
  padding: 2px 7px;
  border: 1px solid var(--ai-border);
  border-radius: 4px;
  color: var(--ai-ink-3);
  background: var(--ai-surface-2);
  font-size: 11.5px;
}

.sf-link-btn,
.mono-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ai-accent-ink);
  font-size: 12px;
  font-weight: 500;
  text-decoration: none;
}

.mono-link {
  min-width: 0;
  justify-self: start;
  max-width: 100%;
  overflow-wrap: anywhere;
  font-size: 11.5px;
}

.sf-link-btn:hover,
.mono-link:hover {
  color: var(--ai-accent);
  text-decoration: underline;
}

.event-main {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.event-main span,
.event-main small {
  min-width: 0;
  color: var(--ai-ink-4);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.event-main .ink {
  color: var(--ai-ink-2);
}

.sf-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 20px;
  padding: 2px 7px;
  border-radius: 4px;
  border: 1px solid transparent;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
  font-size: 11.5px;
  font-weight: 500;
  line-height: 1.2;
  white-space: nowrap;
}

.sf-tag.dot::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.sf-tag.ok { color: var(--ai-ok); background: var(--ai-ok-soft); }
.sf-tag.bad { color: var(--ai-bad); background: var(--ai-bad-soft); }
.sf-tag.warn { color: var(--ai-warn); background: var(--ai-warn-soft); }
.sf-tag.info { color: var(--ai-info); background: var(--ai-info-soft); }
.sf-tag.accent { color: var(--ai-accent-ink); background: var(--ai-accent-soft); }

.sf-table :deep(.arco-table-th) {
  background: var(--ai-surface);
  color: var(--ai-ink-4);
  border-color: var(--ai-border);
  font-size: 11.5px;
  font-weight: 500;
}

.sf-table :deep(.arco-table-td) {
  border-color: var(--ai-border);
  color: var(--ai-ink-2);
  font-size: 12.5px;
  white-space: normal;
}

.sf-table :deep(.arco-table-tr) {
  cursor: pointer;
}

.sf-table :deep(.arco-table-tr:hover .arco-table-td) {
  background: var(--ai-surface-2);
}

.sf-empty {
  min-height: 170px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 18px;
  text-align: center;
  border: 1px dashed var(--ai-border-2);
  border-radius: var(--ai-radius);
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
}

.sf-empty :deep(svg) {
  width: 30px;
  height: 30px;
  color: var(--ai-ink-4);
}

.sf-empty strong {
  color: var(--ai-ink-2);
  font-size: 13px;
}

.sf-empty span {
  max-width: 320px;
  font-size: 12.5px;
}

.sf-empty.small {
  min-height: 156px;
}

.table-empty {
  margin: 12px;
}

.sf-drawer {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.sf-drawer h2 {
  margin: 0;
  color: var(--ai-ink-1);
  font-size: 18px;
  font-weight: 600;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.drawer-head {
  justify-content: flex-start;
}

.drawer-grid {
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}

.drawer-actions {
  justify-content: flex-start;
}

.drawer-tags {
  margin-top: 0;
}

.drawer-metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.drawer-metrics article {
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
}

.drawer-metrics span {
  display: block;
  color: var(--ai-ink-4);
  font-size: 11.5px;
}

.drawer-metrics strong {
  display: block;
  margin-top: 4px;
  color: var(--ai-ink-1);
  font-size: 16px;
  font-weight: 600;
}

.json-box {
  max-height: 420px;
  margin: 0;
  padding: 12px;
  overflow: auto;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  font-size: 11.5px;
  line-height: 1.55;
}

:deep(.arco-select-view-single),
:deep(.arco-input-wrapper),
:deep(.arco-input-search) {
  border-color: var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface);
}

:deep(.arco-select-view-single:focus-within),
:deep(.arco-input-wrapper:focus-within) {
  border-color: var(--ai-border-2);
  box-shadow: none;
}

@media (max-width: 1240px) {
  .sf-filter-row-primary,
  .sf-filter-row-secondary,
  .sf-data-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .sf-filter-actions {
    justify-content: flex-start;
  }
  .sf-kpi-strip {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .sf-kpi {
    border-left: 0;
    border-top: 1px solid var(--ai-border);
  }
  .sf-kpi:nth-child(-n + 3) {
    border-top: 0;
  }
  .report-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .sf-grid-two {
    grid-template-columns: 1fr;
  }
  .sf-grid-three,
  .catalog-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 920px) {
  .sf-pagehead {
    flex-direction: column;
  }
  .sf-head-actions {
    justify-content: flex-start;
  }
  .sf-filter-row-primary,
  .sf-filter-row-secondary,
  .sf-data-filters {
    grid-template-columns: 1fr;
  }
  .sf-field-kind {
    min-width: 0;
  }
  .sf-filter-actions {
    align-items: stretch;
  }
  .sf-filter-actions .sf-apply,
  .sf-filter-actions .sf-reset {
    width: 100%;
    justify-content: center;
  }
  .sf-kpi-strip,
  .report-list,
  .ranking-grid,
  .data-detail-grid,
  .trace-grid {
    grid-template-columns: 1fr;
  }
  .sf-kpi:nth-child(-n + 3) {
    border-top: 1px solid var(--ai-border);
  }
  .sf-kpi:first-child {
    border-top: 0;
  }
  .command-row {
    grid-template-columns: 24px minmax(0, 1fr) auto;
  }
  .command-list.compact .command-row,
  .command-row.plain {
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .command-row span {
    grid-column: 2 / -1;
  }
  .command-list.compact .command-row span,
  .command-row.plain span {
    grid-column: 1 / -1;
  }
}
</style>
