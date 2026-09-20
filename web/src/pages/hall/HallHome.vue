<template>
  <div class="ai-app page-container page-wide hall-home">
    <!-- 设计稿 ability-hall.jsx 的 ai-sidebar：我的 / 按部门 / 视图 三组 -->
    <aside class="hall-sidebar ai-sidebar" :class="{ 'hall-sidebar--open': sidebarOpen }">
      <div class="hall-sidebar-head">
        <span class="hall-sidebar-title">能力大厅</span>
        <button class="hall-sidebar-close" type="button" @click="sidebarOpen = false" aria-label="关闭导航">
          <SfShellIcon name="x" />
        </button>
      </div>
      <div class="ai-side-group">
        <div class="ai-side-label">我的</div>
        <div
          class="ai-side-item"
          :class="{ active: viewMode === 'ability' && !directCategoryFilter && !directSearchQuery && !sidebarDeptFilter && !directCollectionFilter }"
          @click="resetDirectFilter"
        >
          <SfShellIcon name="spark" class="ic" />
          <span>为你推荐</span>
          <span class="count">{{ totalDirectCapabilities || 0 }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: viewMode === 'ability' && directCollectionFilter === 'recent' }"
          @click="setDirectCollectionFilter('recent')"
        >
          <SfShellIcon name="clock" class="ic" />
          <span>最近用过</span>
          <span class="count">{{ directRecent.length }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: viewMode === 'ability' && directCollectionFilter === 'favorite' }"
          @click="setDirectCollectionFilter('favorite')"
        >
          <SfShellIcon name="star" class="ic" />
          <span>我收藏的</span>
          <span class="count">{{ directFavorites.length }}</span>
        </div>
        <div class="ai-side-item" :class="{ active: artifactsDrawerOpen }" @click="openArtifactsDrawer">
          <SfShellIcon name="doc" class="ic" />
          <span>我的产物</span>
          <span class="count">{{ directArtifactTotal }}</span>
        </div>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">视图</div>
        <div
          class="ai-side-item"
          :class="{ active: viewMode === 'ability' }"
          @click="switchViewMode('ability')"
        >
          <SfShellIcon name="spark" class="ic" />
          <span>直接能力</span>
          <span class="count">{{ totalDirectCapabilities || 0 }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: viewMode === 'capability' }"
          @click="switchViewMode('capability')"
        >
          <SfShellIcon name="cube" class="ic" />
          <span>Skill 资产</span>
          <span class="count">{{ totalCapabilities || 0 }}</span>
        </div>
        <div
          class="ai-side-item"
          :class="{ active: viewMode === 'type' }"
          @click="switchViewMode('type')"
        >
          <SfShellIcon name="layers" class="ic" />
          <span>数据 / 团队</span>
          <span class="count">3</span>
        </div>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">按部门</div>
        <div
          v-for="d in sidebarDepartments"
          :key="d.id"
          class="ai-side-item"
          :class="{ active: viewMode === 'ability' && ((d.id === 'all' && !sidebarDeptFilter) || sidebarDeptFilter === d.id) }"
          @click="setSidebarDept(d.id)"
        >
          <SfShellIcon name="dept" class="ic" />
          <span class="ai-side-text">{{ d.name }}</span>
          <span class="count">{{ d.n }}</span>
        </div>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">按能力</div>
        <div
          v-for="cap in sidebarCapabilityTypes"
          :key="cap.id"
          class="ai-side-item"
        >
          <span class="deptdot" :style="{ background: cap.color }" />
          <span class="ai-side-text">{{ cap.name }}</span>
        </div>
      </div>

      <div class="ai-side-group">
        <div class="ai-side-label">场景</div>
        <div
          v-for="s in sidebarScenarios"
          :key="s.id"
          class="ai-side-item"
          :class="{ active: viewMode === 'ability' && directCategoryFilter === s.name }"
          @click="setDirectCategoryFilter(s.name)"
        >
          <span class="deptdot" :style="{ background: s.color }" />
          <span class="ai-side-text">{{ s.name }}</span>
        </div>
      </div>
    </aside>

    <main class="hall-main ai-main">
      <button class="hall-sidebar-toggle" type="button" @click="sidebarOpen = !sidebarOpen" aria-label="打开导航">
        <SfShellIcon name="list" />
        <span>{{ sidebarLabel || '导航' }}</span>
      </button>

      <div class="ai-pagehead hall-pagehead">
          <div>
            <div class="ai-crumbs">资产中心 · 能力大厅</div>
            <h1 class="ai-title">能力大厅</h1>
            <p class="ai-sub">默认展示可直接运行的能力，也可进入 Skill 资产和数据 / 团队视图</p>
          </div>
          <div class="hall-head-actions">
            <a-radio-group v-model="viewMode" type="button" size="small" @change="onModeChange">
              <a-radio value="ability">能力</a-radio>
              <a-radio value="capability">Skill</a-radio>
              <a-radio value="type">数据 / 团队</a-radio>
            </a-radio-group>
            <button class="ai-btn" @click="$router.push('/inbox')" type="button">
              <SfShellIcon name="inbox" />
              我的收件
              <span v-if="pendingRequestCount" class="ai-pill bad hall-head-count">{{ pendingRequestCount }}</span>
            </button>
            <button class="ai-btn" @click="$router.push('/executions')" type="button">
              <SfShellIcon name="hist" />
              运行历史
            </button>
          </div>
      </div>

    <a-alert
      v-if="userStore.isAdmin && pendingRequestCount > 0"
      class="pending-banner"
      type="warning"
      show-icon
      closable
    >
      <template #icon><SfShellIcon name="warn" /></template>
      你有 {{ pendingRequestCount }} 个数据访问申请等待审批
      <template #action>
        <a-button size="small" type="outline" @click="goPendingReviews">去处理</a-button>
      </template>
    </a-alert>

    <!-- 直接运行能力视图 -->
    <template v-if="viewMode === 'ability'">
      <section class="hall-content ai-pagebody">
        <div class="filter-bar">
          <a-input-search
            v-model="directSearchQuery"
            placeholder="搜索能力 / Skill ID"
            allow-clear
            size="small"
            class="hall-search hall-search-wide"
          />
          <a-select
            v-model="directCategoryFilter"
            placeholder="分类"
            allow-clear
            size="small"
            class="hall-filter-select"
          >
            <a-option v-for="cat in directCategories" :key="cat" :value="cat">{{ cat }}</a-option>
          </a-select>
          <a-select v-model="directSortBy" size="small" class="hall-filter-select">
            <a-option value="name_asc">能力名</a-option>
            <a-option value="updated_at">最近更新</a-option>
            <a-option value="risk_asc">风险等级</a-option>
          </a-select>
          <div class="filter-spacer" />
          <!-- 设计稿 ability-hall.jsx：右侧 5 个状态 pill (推荐 N / 最近 N / 收藏 N / NEW N / 全部 N) -->
          <div class="hall-status-pills">
            <span
              class="ai-pill"
              :class="{ 'is-active': !directCategoryFilter && !directSearchQuery && !sidebarDeptFilter && !directCollectionFilter }"
              @click="resetDirectFilter"
            >推荐 {{ totalDirectCapabilities }}</span>
            <span
              class="ai-pill"
              :class="{ 'is-active': directCollectionFilter === 'recent' }"
              @click="setDirectCollectionFilter('recent')"
            >最近 {{ directRecent.length }}</span>
            <span
              class="ai-pill"
              :class="{ 'is-active': directCollectionFilter === 'favorite' }"
              @click="setDirectCollectionFilter('favorite')"
            >收藏 {{ directFavorites.length }}</span>
            <span class="ai-pill ok">NEW {{ newCount }}</span>
            <span class="ai-pill">全部 {{ totalDirectCapabilities }}</span>
          </div>
        </div>

        <a-spin
          :loading="directLoading"
          tip="加载能力..."
          class="hall-full-spin"
          role="status"
          aria-live="polite"
        >
          <a-row v-if="displayedDirectCapabilities.length" :gutter="[16, 16]">
            <a-col
              v-for="cap in displayedDirectCapabilities"
              :key="cap.id"
              :xs="24"
              :sm="12"
              :md="8"
              :lg="6"
            >
              <article
                class="direct-capability-card"
                role="button"
                tabindex="0"
                :aria-label="`能力 ${cap.display_name || cap.id}`"
                @click="runDirectCapabilityNow(cap)"
                @keydown.enter.prevent="runDirectCapabilityNow(cap)"
                @keydown.space.prevent="runDirectCapabilityNow(cap)"
              >
                <div class="direct-card-head">
                  <span
                    class="direct-icon"
                    :class="`direct-icon--${hallCapabilityTone(cap)}`"
                    aria-hidden="true"
                  >
                    <SfShellIcon :name="hallShellIconName(cap.icon || cap.category)" />
                  </span>
                  <div class="direct-card-headline">
                    <h3 class="direct-card-name">{{ cap.display_name || cap.name || cap.id }}</h3>
                    <div class="direct-card-pills">
                      <span class="ai-pill accent">可直接运行</span>
                    </div>
                  </div>
                </div>
                <p class="direct-card-desc">{{ cap.description || '暂无描述' }}</p>
                <div class="direct-card-foot">
                  <button
                    class="ai-btn primary direct-run-btn"
                    type="button"
                    :disabled="!directCanExecute(cap)"
                    data-testid="direct-run-btn"
                    @click.stop="runDirectCapabilityNow(cap)"
                  >
                    <SfShellIcon name="play" /> 直接运行
                  </button>
                  <button
                    v-if="directCanInspect(cap)"
                    class="ai-iconbtn direct-more-btn"
                    type="button"
                    title="调试详情：参数 / 运行中 / 日志 / 结果"
                    @click.stop="openCapabilityMenu(cap)"
                  >
                    <SfShellIcon name="more" />
                  </button>
                  <button
                    class="ai-iconbtn direct-more-btn"
                    :class="{ active: isDirectFavorite(cap.id) }"
                    type="button"
                    title="收藏"
                    @click.stop="toggleDirectCapabilityFavorite(cap)"
                  >
                    <SfShellIcon name="star" />
                  </button>
                </div>
              </article>
            </a-col>
          </a-row>

          <SfEmptyState
            v-else-if="!directLoading"
            title="暂无可直接运行的能力"
            description="当前筛选下没有可直接运行的能力"
            :hint="directFilterActive ? '可以清除筛选后再查看。' : '请先发布可运行 Skill，或联系管理员接入平台能力。'"
            icon="spark"
          >
            <template v-if="directFilterActive" #action>
              <button class="ai-btn sm" type="button" @click="resetDirectFilter">清除筛选</button>
            </template>
          </SfEmptyState>
        </a-spin>

        <div v-if="totalDirectCapabilities > directPageSize" class="table-footer">
          <a-pagination
            v-model:current="directPage"
            :total="totalDirectCapabilities"
            :page-size="directPageSize"
            size="small"
            show-total
          />
        </div>
      </section>
    </template>

    <!-- Skill 聚合视图 -->
    <template v-else-if="viewMode === 'capability'">
      <section class="hall-content ai-pagebody hall-capability-body">
        <div class="hall-capability-overview ai-card">
          <div class="hall-capability-copy">
            <div class="hall-capability-eyebrow">
              <SfShellIcon name="cube" />
              Skill 资产视图
            </div>
            <h2>按业务能力聚合 Skill</h2>
            <p>把已发布、可复用、可治理的 Skill 统一沉淀为资产目录，和能力大厅保持同一套页面壳、卡片密度与筛选规范。</p>
            <div class="hall-capability-tags" aria-label="Skill 资产规范">
              <span class="ai-pill accent">统一设计 token</span>
              <span class="ai-pill">部门可见性</span>
              <span class="ai-pill">运行与治理闭环</span>
            </div>
          </div>
          <div class="hall-capability-metrics" aria-label="Skill 资产统计">
            <div class="hall-metric-card">
              <div class="hall-metric-label">
                <SfShellIcon name="grid" />
                <span>能力分类</span>
              </div>
              <strong>{{ totalCapabilities }}</strong>
            </div>
            <div class="hall-metric-card">
              <div class="hall-metric-label">
                <SfShellIcon name="bolt" />
                <span>活跃 Skill</span>
              </div>
              <strong>{{ capabilityActiveSkillTotal }}</strong>
            </div>
            <div class="hall-metric-card">
              <div class="hall-metric-label">
                <SfShellIcon name="dept" />
                <span>覆盖部门</span>
              </div>
              <strong>{{ capabilityDepartmentTotal }}</strong>
            </div>
          </div>
        </div>

        <div v-if="recent.length" class="recent-row" aria-label="最近浏览的能力">
          <span class="recent-label">最近浏览</span>
          <button
            v-for="r in recent"
            :key="r"
            type="button"
            class="ai-pill accent recent-chip"
            @click="onRecentClick(r)"
          >#{{ r }}</button>
        </div>

        <!-- G3: 横向 band 区，保证首屏视觉饱满（不依赖能力分类的数量） -->
        <div class="hall-bands" aria-label="能力推荐">
          <section
            v-for="group in capabilityBandGroups"
            :key="group.key"
            class="capability-band ai-card"
            :class="`capability-band-${group.tone}`"
          >
            <header class="capability-band-head">
              <div class="capability-band-title">
                <span class="capability-band-icon"><SfShellIcon :name="group.icon" /></span>
                <span>{{ group.title }}</span>
                <span v-if="!group.loading && group.items.length" class="ai-pill">{{ group.items.length }}</span>
              </div>
              <p>{{ group.subtitle }}</p>
              <button class="ai-btn sm" type="button" @click="$router.push({ path: '/hall', query: { view: 'type', tab: 'skill' } })">
                查看更多 <SfShellIcon name="arrowr" />
              </button>
            </header>

            <div v-if="group.loading" class="capability-band-list capability-band-skeleton" aria-label="加载中">
              <div v-for="i in 4" :key="i" class="capability-mini-card skeleton-card" />
            </div>
            <div v-else-if="group.items.length" class="capability-band-list" role="list">
              <button
                v-for="skill in group.items.slice(0, 4)"
                :key="skill.id"
                class="capability-mini-card"
                type="button"
                role="listitem"
                @click="onBandItemClick(skill)"
              >
                <span class="capability-mini-top">
                  <span class="capability-mini-name">{{ skill.display_name || skill.name || skill.id }}</span>
                  <span class="ai-pill">{{ skill.risk_level || 'R1' }}</span>
                </span>
                <span class="capability-mini-desc">{{ skill.description || '暂无描述' }}</span>
                <span class="capability-mini-meta">
                  <span><SfShellIcon name="dept" /> {{ skill.department || '平台' }}</span>
                  <span><SfShellIcon name="hist" /> {{ skill.usage_count || 0 }}</span>
                </span>
              </button>
            </div>
            <div v-else class="capability-band-empty">
              <SfShellIcon name="cube" />
              <span>{{ group.emptyText }}</span>
            </div>
          </section>
        </div>

      <div class="filter-bar capability-filter-bar">
        <div class="capability-filter-label">
          <SfShellIcon name="filter" />
          <span>目录筛选</span>
        </div>
        <a-input-search
          v-model="searchQuery"
          placeholder="搜索能力名"
          allow-clear
          size="small"
          class="hall-search"
        />
        <a-select
          v-model="departmentFilter"
          placeholder="部门"
          multiple
          allow-clear
          size="small"
          class="hall-filter-select"
        >
          <a-option v-for="d in availableDepartments" :key="d" :value="d">{{ d }}</a-option>
        </a-select>
        <a-select v-model="sortBy" size="small" class="hall-filter-select">
          <a-option value="active_desc">活跃 Skill</a-option>
          <a-option value="total_desc">Skill 总数</a-option>
          <a-option value="name_asc">能力名</a-option>
        </a-select>
        <a-checkbox v-model="groupByDomain" class="filter-checkbox">按领域分组</a-checkbox>
        <span v-if="filterActive" class="ai-pill accent">{{ displayedCapabilities.length }} / {{ capabilities.length }}</span>
        <button v-if="filterActive" class="ai-btn sm" type="button" @click="resetFilter">清除</button>
      </div>

      <a-spin
        :loading="loading"
        tip="加载能力目录..."
        class="hall-full-spin"
        role="status"
        aria-live="polite"
      >
        <!-- 分组模式：按 domain 折叠展开 -->
        <template v-if="groupByDomain && groupedCapabilities.length">
          <a-collapse
            :default-active-key="groupedCapabilities.map((g) => g.domain)"
            class="domain-groups"
          >
            <a-collapse-item
              v-for="g in groupedCapabilities"
              :key="g.domain"
              :header="`${g.domain} · ${g.items.length} 项`"
            >
              <a-row :gutter="[16, 16]">
                <a-col
                  v-for="cap in g.items"
                  :key="cap.category"
                  :xs="24"
                  :sm="12"
                  :md="8"
                  :lg="6"
                >
                  <div
                    class="cap-card-wrap"
                    role="button"
                    tabindex="0"
                    :aria-label="`能力 ${cap.category}，${cap.skill_count_active} 个活跃 Skill`"
                    @keydown.enter.prevent="goDetail(cap)"
                    @keydown.space.prevent="goDetail(cap)"
                  >
                    <CapabilityCard
                      :cap="cap"
                      :favorite="favorites.includes(cap.category)"
                      @click="goDetail(cap)"
                      @toggle-fav="onFavClick"
                      @go-team="goTeam"
                    />
                  </div>
                </a-col>
              </a-row>
            </a-collapse-item>
          </a-collapse>
        </template>

        <!-- 平铺模式 -->
        <a-row v-else-if="displayedCapabilities.length" :gutter="[16, 16]">
          <a-col
            v-for="cap in displayedCapabilities"
            :key="cap.category"
            :xs="24"
            :sm="12"
            :md="8"
            :lg="6"
          >
            <div
              class="cap-card-wrap"
              role="button"
              tabindex="0"
              :aria-label="`能力 ${cap.category}，${cap.skill_count_active} 个活跃 Skill`"
              @keydown.enter.prevent="goDetail(cap)"
              @keydown.space.prevent="goDetail(cap)"
            >
              <CapabilityCard
                :cap="cap"
                :favorite="favorites.includes(cap.category)"
                @click="goDetail(cap)"
                @toggle-fav="onFavClick"
                @go-team="goTeam"
              />
            </div>
          </a-col>
        </a-row>

        <SfEmptyState
          v-else-if="!loading"
          title="暂无 Skill 资产"
          :description="emptyDesc"
          :hint="filterActive ? '请清除筛选或调整关键词。' : '还没有已登记的能力。如需接入业务场景，请联系 AI 管理员或本部门 AI 工程师。'"
          icon="cube"
        >
          <template #action>
            <button v-if="userStore.isEngineer && !filterActive" class="ai-btn primary" type="button" @click="$router.push('/skills?new=1')">
              <SfShellIcon name="plus" /> 创建第一个 Skill
            </button>
            <button v-else-if="filterActive" class="ai-btn" type="button" @click="resetFilter">清除筛选</button>
          </template>
        </SfEmptyState>
      </a-spin>

      <div v-if="totalCapabilities > capPageSize" class="table-footer">
        <a-pagination
          v-model:current="capPage"
          :total="totalCapabilities"
          :page-size="capPageSize"
          size="small"
          show-total
        />
      </div>
      </section>
    </template>

    <!-- 按类型视图 -->
    <template v-else>
      <section class="hall-content ai-pagebody hall-type-body">
        <section class="hall-type-panel ai-card" aria-label="按类型浏览能力">
          <div class="hall-type-head">
            <div class="hall-type-copy">
              <div class="hall-capability-eyebrow">
                <SfShellIcon name="layers" />
                按类型浏览
              </div>
              <h2>{{ currentTypeMeta.title }}</h2>
              <p>{{ currentTypeMeta.desc }}</p>
            </div>
            <div class="hall-type-switch" role="tablist" aria-label="能力类型切换">
              <button
                v-for="item in typeTabs"
                :key="item.key"
                type="button"
                role="tab"
                class="hall-type-tab"
                :class="{ active: typeTab === item.key }"
                :aria-selected="typeTab === item.key"
                @click="onTypeTabChange(item.key)"
              >
                <SfShellIcon :name="item.icon" />
                <span>{{ item.label }}</span>
              </button>
            </div>
          </div>

          <div class="hall-type-content">
            <KeepAlive>
              <component :is="currentTabComp" :key="typeTab" />
            </KeepAlive>
          </div>
        </section>
      </section>
    </template>
    </main>

    <!-- L3-A · 能力运行抽屉（设计稿 l3-pages.jsx · RunDrawer 简化版） -->
    <a-drawer
      v-model:visible="runDrawerOpen"
      :width="runDrawerWidth"
      :footer="false"
      :header="false"
      :mask="true"
      :unmount-on-close="true"
      placement="right"
      class="run-drawer"
      data-testid="run-drawer"
      @close="onRunDrawerClose"
    >
      <div v-if="runDrawerCap" class="run-drawer-shell">
        <header class="run-drawer-head">
          <div class="run-drawer-icon"><SfShellIcon name="spark" /></div>
          <div class="run-drawer-title-wrap">
            <h3 class="run-drawer-title">
              {{ runDrawerCap.display_name || runDrawerCap.name || runDrawerCap.id }}
            </h3>
            <div class="run-drawer-pills">
              <span class="ai-pill">{{ runDrawerCap.department || '平台' }}</span>
              <span
                class="ai-pill"
                :class="(runDrawerCap.risk_level || 'R1').toLowerCase() === 'r1' ? 'ok' : 'warn'"
              >{{ runDrawerCap.risk_level || 'R1' }}</span>
              <span class="ai-pill">{{ runDrawerCap.category || '通用' }}</span>
            </div>
            <div class="run-drawer-meta">
              {{ runDrawerCap.id }}<template v-if="runDrawerLiveTask?.id"> · 运行 ID {{ shortTaskId(runDrawerLiveTask.id) }}</template>
            </div>
          </div>
          <button
            class="ai-iconbtn run-drawer-close"
            type="button"
            title="关闭"
            data-testid="run-drawer-close"
            @click="closeRunDrawer"
          >
            <SfShellIcon name="x" />
          </button>
        </header>

        <nav class="run-drawer-tabs" aria-label="运行抽屉视图">
          <button
            v-for="tab in runDrawerTabs"
            :key="tab.key"
            type="button"
            class="run-drawer-tab"
            :class="{ active: runDrawerTab === tab.key }"
            @click="runDrawerTab = tab.key"
          >
            {{ tab.label }}
            <span v-if="tab.key === 'running' && runDrawerLiveActive" class="ai-pill ok run-live-pill">
              {{ runDrawerElapsedLabel }}
            </span>
          </button>
        </nav>

        <div class="run-drawer-body">
          <!-- KPI 条 -->
          <div class="run-kpi-strip" data-testid="run-drawer-kpi">
            <div class="run-kpi-cell">
              <div class="run-kpi-label">上次运行</div>
              <div class="run-kpi-value">{{ runDrawerKpis.last_run_at }}</div>
            </div>
            <div class="run-kpi-cell">
              <div class="run-kpi-label">平均耗时</div>
              <div class="run-kpi-value">{{ runDrawerKpis.avg_duration }}</div>
            </div>
            <div class="run-kpi-cell">
              <div class="run-kpi-label">成功率</div>
              <div class="run-kpi-value">
                {{ runDrawerKpis.success_rate }}<span v-if="runDrawerKpis.success_rate !== '—'" class="run-kpi-suffix">%</span>
              </div>
            </div>
            <div class="run-kpi-cell">
              <div class="run-kpi-label">调用次数</div>
              <div class="run-kpi-value">{{ runDrawerKpis.run_count }}</div>
            </div>
          </div>

          <template v-if="runDrawerTab === 'params'">
            <!-- 参数 -->
            <section class="run-drawer-section">
              <div class="run-section-label">参数</div>
              <div v-if="!runDrawerParamFields.length" class="run-empty-hint">
                该能力无需参数，可直接运行。
              </div>
              <a-form
                v-else
                :model="runDrawerForm"
                layout="vertical"
                size="small"
                class="run-drawer-form"
                data-testid="run-drawer-form"
              >
                <a-form-item
                  v-for="field in runDrawerParamFields"
                  :key="field.key"
                  :field="field.key"
                  :label="field.label"
                  :required="field.required"
                  :extra="field.description"
                >
                  <a-select
                    v-if="field.enum && field.enum.length"
                    v-model="runDrawerForm[field.key]"
                    :placeholder="`选择 ${field.label}`"
                    allow-clear
                  >
                    <a-option v-for="opt in field.enum" :key="String(opt)" :value="opt">{{ opt }}</a-option>
                  </a-select>
                  <a-input-number
                    v-else-if="field.type === 'number' || field.type === 'integer'"
                    v-model="runDrawerForm[field.key]"
                    :placeholder="`输入 ${field.label}`"
                    :precision="field.type === 'integer' ? 0 : undefined"
                    allow-clear
                  />
                  <a-textarea
                    v-else-if="field.widget === 'textarea'"
                    v-model="runDrawerForm[field.key]"
                    :placeholder="`输入 ${field.label}`"
                    :auto-size="{ minRows: 2, maxRows: 4 }"
                  />
                  <a-input
                    v-else
                    v-model="runDrawerForm[field.key]"
                    :placeholder="`输入 ${field.label}`"
                    allow-clear
                  />
                </a-form-item>
              </a-form>
            </section>

            <!-- 最近运行 -->
            <section class="run-drawer-section">
              <div class="run-section-head">
                <div class="run-section-label">最近运行</div>
                <a-spin v-if="runDrawerTasksLoading" size="small" />
              </div>
              <div
                v-if="runDrawerTasks.length"
                class="run-task-list"
                data-testid="run-drawer-task-list"
              >
                <div v-for="t in runDrawerTasks" :key="t.id" class="run-task-row">
                  <span class="ai-pill" :class="taskPillClass(t.status)">{{ taskStatusLabel(t.status) }}</span>
                  <span class="run-task-time mono">{{ formatTaskTime(t.created_at) }}</span>
                  <span class="run-task-duration mono">{{ formatTaskDuration(t) }}</span>
                  <button
                    class="run-task-detail"
                    type="button"
                    @click="runDrawerTab = 'result'"
                  >
                    详情
                  </button>
                </div>
              </div>
              <div v-else-if="!runDrawerTasksLoading" class="run-empty-hint">
                暂无运行记录
              </div>
            </section>
          </template>

          <template v-else-if="runDrawerTab === 'running'">
            <section class="run-param-summary">
              <span v-for="item in runDrawerParamSummary" :key="item.key">
                <span class="muted">{{ item.label }}</span> {{ item.value }}
              </span>
              <button class="run-task-detail" type="button" @click="runDrawerTab = 'params'">修改参数</button>
            </section>

            <section class="run-progress-card" data-testid="run-drawer-progress">
              <div class="run-section-head run-progress-head">
                <div>
                  <div class="run-section-label">执行进度</div>
                  <div class="run-progress-subtitle">{{ runDrawerProgressText }}</div>
                </div>
                <span class="ai-pill" :class="taskPillClass(runDrawerLiveTask?.status)">
                  {{ taskStatusLabel(runDrawerLiveTask?.status || (runDrawerLiveActive ? 'running' : 'queued')) }}
                </span>
              </div>
              <div class="run-progress-bar">
                <span :style="{ width: `${runDrawerProgressPercent}%` }" />
              </div>
              <div class="run-step-list">
                <div v-for="step in runDrawerSteps" :key="step.id" class="run-step-row">
                  <span class="run-step-dot" :class="step.state">{{ step.symbol }}</span>
                  <div class="run-step-main">
                    <span>{{ step.name }}</span>
                    <small>{{ step.meta }}</small>
                  </div>
                  <span class="run-step-time mono">{{ step.time }}</span>
                </div>
              </div>
            </section>
          </template>

          <template v-else-if="runDrawerTab === 'logs'">
            <section class="run-drawer-section">
              <div class="run-section-head">
                <div class="run-section-label">实时日志</div>
                <span class="ai-pill" :class="runDrawerLiveActive ? 'ok' : ''">{{ runDrawerLiveActive ? '跟随' : '已暂停' }}</span>
              </div>
              <div class="run-terminal" data-testid="run-drawer-log">
                <div v-for="(line, index) in runDrawerLogLines" :key="`${line.time}-${index}`">
                  <span class="run-log-time">{{ line.time }}</span>
                  <span :class="`run-log-level ${line.level}`">[{{ line.level.toUpperCase() }}]</span>
                  <span>{{ line.text }}</span>
                </div>
                <div v-if="!runDrawerLogLines.length" class="run-terminal-empty">等待运行日志...</div>
              </div>
            </section>
          </template>

          <template v-else>
            <section class="run-drawer-section" data-testid="run-drawer-result">
              <div class="run-section-label">结果</div>
              <div v-if="!runDrawerLiveTask" class="run-empty-hint">运行后会在这里显示结构化结果。</div>
              <div v-else-if="runDrawerLiveTask.error" class="run-result-error">{{ runDrawerLiveTask.error }}</div>
              <div v-else class="run-result-card">
                <div class="run-result-head">
                  <span class="ai-pill" :class="taskPillClass(runDrawerLiveTask.status)">
                    {{ taskStatusLabel(runDrawerLiveTask.status) }}
                  </span>
                  <span class="mono">{{ shortTaskId(runDrawerLiveTask.id) }}</span>
                </div>
                <div v-if="runDrawerImageUrls.length" class="run-result-images">
                  <img v-for="url in runDrawerImageUrls" :key="url" :src="url" alt="运行结果" />
                </div>
                <pre class="run-result-json">{{ runDrawerResultText }}</pre>
              </div>
            </section>
          </template>
        </div>

        <footer class="run-drawer-foot">
          <button
            class="ai-btn"
            type="button"
            @click="closeRunDrawer"
          >
            关闭
          </button>
          <button
            class="ai-btn"
            type="button"
            :disabled="runDrawerSubmitting"
            @click="resetRunDrawerForm"
          >
            重置
          </button>
          <div class="run-drawer-spacer" />
          <button
            class="ai-btn primary"
            type="button"
            :disabled="!directCanExecute(runDrawerCap) || runDrawerSubmitting"
            data-testid="run-drawer-submit"
            @click="runDrawerSubmit"
          >
            <SfShellIcon name="play" /> {{ runDrawerSubmitting ? '运行中' : '运行' }}
          </button>
        </footer>
      </div>
    </a-drawer>

    <!-- ability-hall.jsx · 我的产物：聚合当前用户直接能力生成结果 -->
    <a-drawer
      v-model:visible="artifactsDrawerOpen"
      :width="560"
      :footer="false"
      :header="false"
      placement="right"
      class="artifacts-drawer"
    >
      <div class="artifacts-shell">
        <header class="artifacts-head">
          <div>
            <div class="page-kicker">我的产物</div>
            <h3>直接能力生成结果</h3>
            <p>共 {{ directArtifactTotal }} 个产物，按生成时间倒序</p>
          </div>
          <button class="ai-iconbtn" type="button" title="关闭" @click="artifactsDrawerOpen = false">
            <SfShellIcon name="x" />
          </button>
        </header>

        <a-spin :loading="artifactsLoading" class="hall-full-spin">
          <div v-if="directArtifacts.length" class="artifact-grid">
            <article v-for="item in directArtifacts" :key="item.id" class="artifact-card">
              <img :src="item.image_url || item.url" :alt="item.name || '生成产物'" loading="lazy" />
              <div class="artifact-card-body">
                <div class="artifact-name">{{ item.name || '生成图片' }}</div>
                <div class="artifact-meta">
                  <span>{{ item.capability_name || item.capability_id }}</span>
                  <span>{{ formatTaskTime(item.created_at) }}</span>
                </div>
                <p>{{ item.prompt || '无提示词记录' }}</p>
                <div class="artifact-actions">
                  <button class="ai-btn sm" type="button" @click="goArtifactCapability(item)">
                    打开能力
                  </button>
                  <a
                    class="ai-btn sm"
                    :href="item.image_url || item.url"
                    target="_blank"
                    rel="noreferrer"
                  >
                    查看原图
                  </a>
                </div>
              </div>
            </article>
          </div>
          <SfEmptyState
            v-else-if="!artifactsLoading"
            title="暂无生成产物"
            description="运行直接能力后，生成结果会聚合在这里。"
            icon="image"
          >
            <template #action>
              <button class="ai-btn primary" type="button" @click="artifactsDrawerOpen = false">
                去运行能力
              </button>
            </template>
          </SfEmptyState>
        </a-spin>

        <div v-if="directArtifactTotal > artifactPageSize" class="table-footer">
          <a-pagination
            v-model:current="artifactPage"
            :total="directArtifactTotal"
            :page-size="artifactPageSize"
            size="small"
            show-total
          />
        </div>
      </div>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { datasourceApi, hallApi, skillApi } from '@/api'
import { pushRecent, toggleFavorite, useHallMemory } from '@/utils/hallFavorites'
import { useUserStore } from '@/stores/user'
import { goSkill as routeSkill } from '@/utils/goSkill'
import CapabilityCard from '@/components/hall/CapabilityCard.vue'
import HallDataTab from '@/pages/hall/HallDataTab.vue'
import HallTeamTab from '@/pages/hall/HallTeamTab.vue'
import HallSkillListEmbed from '@/pages/hall/HallSkillListEmbed.vue'
import SfEmptyState from '@/components/common/SfEmptyState.vue'
import SfShellIcon from '@/components/icons/SfShellIcon.vue'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const viewMode = ref<string>((route.query.view as string) || 'ability')
const typeTab = ref<string>((route.query.tab as string) || 'skill')
// 移动端 sidebar 抽屉开关
const sidebarOpen = ref(false)
const loading = ref(false)
const capabilities = ref<any[]>([])
const totalCapabilities = ref(0)
const capPage = ref(1)
const capPageSize = 60
const knownDepartments = ref<string[]>([])
const directLoading = ref(false)
const directCapabilities = ref<any[]>([])
const totalDirectCapabilities = ref(0)
const directCategoryOptions = ref<string[]>([])
const directDepartmentOptions = ref<Array<{ name: string; count: number }>>([])
const directPage = ref(1)
const directPageSize = 60
const artifactsDrawerOpen = ref(false)
const artifactsLoading = ref(false)
const directArtifacts = ref<any[]>([])
const directArtifactTotal = ref(0)
const artifactPage = ref(1)
const artifactPageSize = 20

const searchQuery = ref('')
const departmentFilter = ref<string[]>([])
const sortBy = ref<string>('active_desc')
const directSearchQuery = ref('')
const directCategoryFilter = ref<string>('')
const directSortBy = ref<string>('name_asc')
const directCollectionFilter = ref<'recent' | 'favorite' | ''>('')

// v2.7.5：按业务领域分组
const GROUP_PREF_KEY = 'sf.hall.groupByDomain'
const groupByDomain = ref<boolean>(
  (() => {
    try {
      return window.localStorage.getItem(GROUP_PREF_KEY) === '1'
    } catch {
      return false
    }
  })(),
)
watch(groupByDomain, (v) => {
  try {
    window.localStorage.setItem(GROUP_PREF_KEY, v ? '1' : '0')
  } catch {
    // ignore storage write failure
  }
})

const DIRECT_MEMORY_SCOPE = 'anon.direct'
const { favorites, recent } = useHallMemory('anon')
const { favorites: directFavorites, recent: directRecent } = useHallMemory(DIRECT_MEMORY_SCOPE)

const pendingRequestCount = ref(0)

// G3：能力大厅首屏横向 band 数据（最近更新 / 热门 / 部门）。
// 使用已有的 skillApi.hall 按三种排序拉取，前端做归并，不新增 API。
const BAND_SIZE = 8
const bandRecent = ref<any[]>([])
const bandPopular = ref<any[]>([])
const bandDepartment = ref<any[]>([])
const bandLoading = reactive({ recent: true, popular: true, department: true })

const userDepartment = computed(() => (userStore.department as string) || '')

const deptBandTitle = computed(() => {
  const dept = userDepartment.value
  return dept ? `${dept} 部门推荐` : '部门推荐'
})
const deptBandSubtitle = computed(() => {
  return userDepartment.value ? '本部门最近活跃的 Skill' : '登录后自动匹配你的部门'
})
const deptBandEmpty = computed(() => {
  if (!userDepartment.value) return '当前账号未绑定部门，暂无专属推荐'
  return `${userDepartment.value} 部门暂无可见 Skill`
})
const capabilityBandGroups = computed(() => [
  {
    key: 'recent',
    title: '最近更新',
    subtitle: '近 14 天内有改动',
    icon: 'clock',
    tone: 'info',
    items: bandRecent.value,
    loading: bandLoading.recent,
    emptyText: '暂无最近更新的 Skill',
  },
  {
    key: 'popular',
    title: '热门推荐',
    subtitle: '按历史执行次数排序',
    icon: 'bolt',
    tone: 'brand',
    items: bandPopular.value,
    loading: bandLoading.popular,
    emptyText: '还没有执行记录，等大家跑起来再来看看',
  },
  {
    key: 'department',
    title: deptBandTitle.value,
    subtitle: deptBandSubtitle.value,
    icon: 'dept',
    tone: 'success',
    items: bandDepartment.value,
    loading: bandLoading.department,
    emptyText: deptBandEmpty.value,
  },
])

async function loadBand(
  key: 'recent' | 'popular' | 'department',
  params: Record<string, unknown>,
) {
  bandLoading[key] = true
  try {
    // band 卡片不展示 profile（为了节省纵向空间），故不带 include=profile
    const r: any = await skillApi.hall({
      page: 1,
      page_size: BAND_SIZE,
      ...params,
    })
    const list: any[] = Array.isArray(r) ? r : r?.items || []
    if (key === 'recent') bandRecent.value = list
    else if (key === 'popular') bandPopular.value = list
    else bandDepartment.value = list
  } catch {
    if (key === 'recent') bandRecent.value = []
    else if (key === 'popular') bandPopular.value = []
    else bandDepartment.value = []
  } finally {
    bandLoading[key] = false
  }
}

async function loadBands() {
  const dept = userDepartment.value
  // 三条 band 并行加载；部门 band 无部门时作为"热门"的副本回退并不展示（由 empty-text 提示）
  await Promise.all([
    loadBand('recent', { sort_by: 'updated_at' }),
    loadBand('popular', { sort_by: 'popularity' }),
    dept
      ? loadBand('department', { sort_by: 'updated_at', department: dept })
      : Promise.resolve().then(() => {
          bandDepartment.value = []
          bandLoading.department = false
        }),
  ])
}

function onBandItemClick(skill: any) {
  if (!skill?.id) return
  routeSkill(router, skill.id, skill)
}

let searchTimer: ReturnType<typeof setTimeout> | null = null
let directSearchTimer: ReturnType<typeof setTimeout> | null = null

async function loadPendingRequests() {
  const role: string = (userStore.role as string) || ''
  if (!['admin', 'system_admin'].includes(role)) {
    pendingRequestCount.value = 0
    return
  }
  try {
    const r: any = await datasourceApi.listPendingRequests()
    const list = Array.isArray(r) ? r : r?.items || []
    pendingRequestCount.value = list.length
  } catch {
    pendingRequestCount.value = 0
  }
}

function goPendingReviews() {
  router.push('/admin/data-requests')
}

const tabComponents: Record<string, any> = {
  skill: HallSkillListEmbed,
  data: HallDataTab,
  team: HallTeamTab,
}
const typeTabs = [
  { key: 'skill', label: 'Skill 能力', icon: 'cube', title: 'Skill 能力', desc: '查看已发布、可复用、可治理的业务 Skill。' },
  { key: 'data', label: '数据能力', icon: 'database', title: '数据能力', desc: '查看可申请、可复用并有时效状态的数据源。' },
  { key: 'team', label: '团队能力', icon: 'users', title: '团队能力', desc: '按部门查看 Skill、数据源和 AI 联系人覆盖情况。' },
]
const currentTabComp = computed(() => tabComponents[typeTab.value] || HallSkillListEmbed)
const currentTypeMeta = computed(() => typeTabs.find(item => item.key === typeTab.value) || typeTabs[0])

function normalizeDepartmentName(item: unknown): string {
  if (typeof item === 'string') return item.trim()
  if (!item || typeof item !== 'object') return ''
  const row = item as Record<string, unknown>
  const name = row.name || row.department
  return typeof name === 'string' ? name.trim() : ''
}

async function loadDepartments() {
  try {
    const r: any = await hallApi.departments()
    const list: unknown[] = Array.isArray(r) ? r : Array.isArray(r?.departments) ? r.departments : []
    const departments = Array.from(
      new Set<string>(
        list.map(normalizeDepartmentName).filter((name): name is string => Boolean(name)),
      ),
    ).sort()
    knownDepartments.value = departments
  } catch {
    knownDepartments.value = []
  }
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, unknown> = {
      page: capPage.value,
      page_size: capPageSize,
      sort_by: sortBy.value,
    }
    if (searchQuery.value.trim()) params.q = searchQuery.value.trim()
    if (departmentFilter.value.length) params.department = departmentFilter.value
    const r: any = await hallApi.capabilities(params)
    if (Array.isArray(r)) {
      capabilities.value = r
      totalCapabilities.value = r.length
    } else {
      capabilities.value = r?.items || []
      totalCapabilities.value = r?.total || 0
    }
  } catch {
    capabilities.value = []
    totalCapabilities.value = 0
  } finally {
    loading.value = false
  }
}

const availableDepartments = computed(() => knownDepartments.value)
const capabilityActiveSkillTotal = computed(() =>
  capabilities.value.reduce((sum, cap) => sum + Number(cap?.skill_count_active || 0), 0),
)
const capabilityDepartmentTotal = computed(() => {
  const names = new Set<string>()
  for (const cap of capabilities.value) {
    const departments = Array.isArray(cap?.top_departments) ? cap.top_departments : []
    for (const row of departments) {
      const name = String(row?.department || '').trim()
      if (name) names.add(name)
    }
  }
  return names.size || availableDepartments.value.length
})
// 移动端 toggle 按钮上展示的当前视图标签
const sidebarLabel = computed(() => {
  if (sidebarDeptFilter.value) return sidebarDeptFilter.value
  if (directCategoryFilter.value) return directCategoryFilter.value
  if (viewMode.value === 'capability') return 'Skill 资产'
  if (viewMode.value === 'type') return '数据 / 团队'
  return '直接运行'
})
const directCategories = computed(() => {
  if (directCategoryOptions.value.length) return directCategoryOptions.value
  return Array.from(new Set(directCapabilities.value.map(item => item.category).filter(Boolean))).sort()
})

const shellIconNames = new Set([
  'search', 'bell', 'clock', 'chev', 'chevr', 'spark', 'cube', 'tree', 'bot', 'beaker', 'inbox',
  'shield', 'doc', 'book', 'edit', 'archive', 'trash', 'docplus', 'hist', 'user', 'users',
  'filter', 'grid', 'refresh', 'bolt', 'link', 'trend', 'database', 'list', 'check', 'gpu',
  'flow', 'layers', 'dept', 'star', 'play', 'plus', 'arrowr', 'arrowl', 'folder', 'attach',
  'upload', 'download', 'more', 'send', 'warn', 'x', 'copy',
])

function hallShellIconName(name?: string): string {
  const raw = String(name || '').toLowerCase()
  if (raw.includes('图') || raw.includes('内容') || raw.includes('image') || raw.includes('picture') || raw.includes('photo') || raw.includes('创作')) return 'spark'
  if (raw.includes('通用') || raw.includes('能力')) return 'spark'
  if (raw.includes('报表') || raw.includes('文档') || raw.includes('report') || raw.includes('doc')) return 'doc'
  if (raw.includes('客服') || raw.includes('用户') || raw.includes('customer') || raw.includes('user')) return 'users'
  if (raw.includes('风险') || raw.includes('合规') || raw.includes('risk') || raw.includes('safe')) return 'shield'
  if (raw.includes('数据') || raw.includes('采集') || raw.includes('database') || raw.includes('data')) return 'database'
  if (raw.includes('成本') || raw.includes('gpu')) return 'gpu'
  if (raw.includes('电商') || raw.includes('分析') || raw.includes('trend') || raw.includes('chart')) return 'trend'
  const iconMap: Record<string, string> = {
    apps: 'grid',
    chart: 'trend',
    code: 'flow',
    file: 'doc',
    safe: 'shield',
    settings: 'cube',
    thunderbolt: 'bolt',
  }
  const resolved = iconMap[raw] || raw
  return shellIconNames.has(resolved) ? resolved : 'spark'
}

function hallCapabilityTone(cap: any): 'accent' | 'info' | 'ok' | 'warn' | 'bad' {
  const raw = [
    cap?.category,
    cap?.display_name,
    cap?.name,
    cap?.id,
    cap?.icon,
  ].map(value => String(value || '').toLowerCase()).join(' ')
  if (raw.includes('风险') || raw.includes('合规') || raw.includes('risk') || raw.includes('safe')) return 'bad'
  if (raw.includes('客服') || raw.includes('用户') || raw.includes('customer') || raw.includes('user')) return 'ok'
  if (raw.includes('数据') || raw.includes('采集') || raw.includes('分析') || raw.includes('database') || raw.includes('data') || raw.includes('chart')) return 'info'
  if (raw.includes('监控') || raw.includes('巡检') || raw.includes('成本') || raw.includes('gpu')) return 'warn'
  return 'accent'
}

const displayedDirectCapabilities = computed(() => {
  const list = directCapabilities.value.slice()
  if (directCollectionFilter.value === 'recent') {
    const order = new Map(directRecent.value.map((id, index) => [id, index]))
    return list
      .filter((item) => order.has(String(item.id)))
      .sort((a, b) => (order.get(String(a.id)) || 0) - (order.get(String(b.id)) || 0))
  }
  if (directCollectionFilter.value === 'favorite') {
    const favoriteSet = new Set(directFavorites.value)
    return list.filter((item) => favoriteSet.has(String(item.id)))
  }
  return list
})

// 设计稿 ability-hall.jsx：按部门聚合（HOME_DEPTS 模式 — 全部 + 各 dept 计数）
const sidebarDepartments = computed(() => {
  if (directDepartmentOptions.value.length) {
    const total = directDepartmentOptions.value.reduce((sum, item) => sum + Number(item.count || 0), 0)
    return [
      { id: 'all', name: '全部能力', n: total },
      ...directDepartmentOptions.value.map((item) => ({
        id: item.name,
        name: item.name,
        n: Number(item.count || 0),
      })),
    ]
  }
  const counts: Record<string, number> = {}
  for (const cap of directCapabilities.value) {
    const d = (cap as any).department || '平台'
    counts[d] = (counts[d] || 0) + 1
  }
  const items = Object.entries(counts)
    .map(([name, n]) => ({ id: name, name, n }))
    .sort((a, b) => b.n - a.n)
  return [{ id: 'all', name: '全部能力', n: directCapabilities.value.length }, ...items]
})

// 设计稿 ability-hall.jsx：按能力 6 类（用户对话 / 数据分析 / 内容生成 / 报表生成 / 监控巡检 / 工作流编排）
// 这是一个固定的能力分类法，与具体业务 category 解耦
const sidebarCapabilityTypes = [
  { id: 'dialog', name: '用户对话能力', color: 'var(--ai-accent)' },
  { id: 'analysis', name: '数据分析能力', color: 'var(--ai-ink-2)' },
  { id: 'content', name: '内容生成能力', color: 'var(--ai-ok)' },
  { id: 'report', name: '报表生成能力', color: 'var(--ai-info)' },
  { id: 'monitor', name: '监控巡检能力', color: 'var(--ai-warn)' },
  { id: 'workflow', name: '工作流编排', color: 'var(--ai-bad)' },
]

// NEW 标记数量：近 14 天新增的能力数（用 updated_at 近似；无字段时 0）
const newCount = computed(() => {
  const cutoff = Date.now() - 14 * 24 * 60 * 60 * 1000
  return directCapabilities.value.filter((c: any) => {
    const t = c.created_at ? new Date(c.created_at).getTime() : 0
    return t > cutoff
  }).length
})

// 设计稿 ability-hall.jsx：场景 4 类（电商分析 / 报表生成 / 客服洞察 / 风险与合规）
const sidebarScenarios = [
  { id: 'ec', name: '电商分析', color: 'var(--ai-accent)' },
  { id: 'report', name: '报表生成', color: 'var(--ai-ok)' },
  { id: 'cs', name: '客服洞察', color: 'var(--ai-warn)' },
  { id: 'risk', name: '风险与合规', color: 'var(--ai-bad)' },
]

const sidebarDeptFilter = ref<string>('')
function setSidebarDept(deptId: string) {
  viewMode.value = 'ability'
  if (deptId === 'all' || sidebarDeptFilter.value === deptId) {
    sidebarDeptFilter.value = ''
  } else {
    sidebarDeptFilter.value = deptId
  }
  directPage.value = 1
  loadDirectCapabilities()
  sidebarOpen.value = false
}

const filterActive = computed(
  () => !!searchQuery.value.trim() || departmentFilter.value.length > 0,
)
const directFilterActive = computed(
  () => !!directSearchQuery.value.trim()
    || !!directCategoryFilter.value
    || !!sidebarDeptFilter.value
    || !!directCollectionFilter.value,
)

const displayedCapabilities = computed(() => {
  const list = capabilities.value.slice()
  const favSet = new Set(favorites.value)
  list.sort((a, b) => {
    const af = favSet.has(a.category) ? 0 : 1
    const bf = favSet.has(b.category) ? 0 : 1
    return af - bf
  })
  return list
})

const DOMAIN_ORDER = ['营销', '客服', '风控', '销售', '运营', '通用']
const groupedCapabilities = computed(() => {
  const buckets: Record<string, any[]> = {}
  for (const c of displayedCapabilities.value) {
    const d = c.domain || '通用'
    if (!buckets[d]) buckets[d] = []
    buckets[d].push(c)
  }
  return DOMAIN_ORDER.filter((d) => buckets[d]?.length)
    .concat(Object.keys(buckets).filter((d) => !DOMAIN_ORDER.includes(d)))
    .map((domain) => ({ domain, items: buckets[domain] || [] }))
})

const emptyDesc = computed(() => {
  if (filterActive.value) return '没有匹配的能力'
  return '暂无能力分类数据'
})

function resetFilter() {
  searchQuery.value = ''
  departmentFilter.value = []
  capPage.value = 1
  load()
}

async function loadDirectCapabilities() {
  directLoading.value = true
  try {
    const params: Record<string, unknown> = {
      page: directPage.value,
      page_size: directPageSize,
      sort_by: directSortBy.value,
    }
    if (directSearchQuery.value.trim()) params.q = directSearchQuery.value.trim()
    if (directCategoryFilter.value) params.category = directCategoryFilter.value
    if (sidebarDeptFilter.value) params.department = sidebarDeptFilter.value
    const r: any = await hallApi.directCapabilities(params)
    if (Array.isArray(r)) {
      directCapabilities.value = r
      totalDirectCapabilities.value = r.length
      directCategoryOptions.value = Array.from(new Set(r.map(item => item.category).filter(Boolean))).sort()
      directDepartmentOptions.value = []
    } else {
      directCapabilities.value = r?.items || []
      totalDirectCapabilities.value = r?.total || 0
      directCategoryOptions.value = Array.isArray(r?.categories) ? r.categories : []
      directDepartmentOptions.value = Array.isArray(r?.departments) ? r.departments : []
    }
  } catch {
    directCapabilities.value = []
    totalDirectCapabilities.value = 0
    directCategoryOptions.value = []
    directDepartmentOptions.value = []
  } finally {
    directLoading.value = false
  }
}

async function loadDirectArtifacts() {
  artifactsLoading.value = true
  try {
    const r: any = await hallApi.directCapabilityArtifacts({
      page: artifactPage.value,
      page_size: artifactPageSize,
    })
    directArtifacts.value = Array.isArray(r?.items) ? r.items : []
    directArtifactTotal.value = Number(r?.total || directArtifacts.value.length || 0)
  } catch {
    directArtifacts.value = []
    directArtifactTotal.value = 0
  } finally {
    artifactsLoading.value = false
  }
}

function openArtifactsDrawer() {
  artifactsDrawerOpen.value = true
  sidebarOpen.value = false
  if (artifactPage.value !== 1) artifactPage.value = 1
  else loadDirectArtifacts()
}

function goArtifactCapability(item: any) {
  const id = item?.capability_id
  if (!id) return
  artifactsDrawerOpen.value = false
  router.push(`/hall/abilities/${encodeURIComponent(id)}`)
}

function resetDirectFilter() {
  directSearchQuery.value = ''
  directCategoryFilter.value = ''
  sidebarDeptFilter.value = ''
  directCollectionFilter.value = ''
  directPage.value = 1
  loadDirectCapabilities()
  sidebarOpen.value = false
}

function setDirectCollectionFilter(type: 'recent' | 'favorite') {
  viewMode.value = 'ability'
  directCollectionFilter.value = directCollectionFilter.value === type ? '' : type
  sidebarOpen.value = false
}

// 左侧 sidebar：点击场景项设置 category 筛选并跳回 ability 视图
function setDirectCategoryFilter(category: string) {
  viewMode.value = 'ability'
  directCategoryFilter.value = directCategoryFilter.value === category ? '' : category
  directPage.value = 1
  loadDirectCapabilities()
  sidebarOpen.value = false
}

// 左侧 sidebar：切换 viewMode
function switchViewMode(mode: 'ability' | 'capability' | 'type') {
  viewMode.value = mode
  onModeChange(mode)
  sidebarOpen.value = false
}

function debouncedReload() {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    capPage.value = 1
    load()
  }, 250)
}

function debouncedReloadDirect() {
  if (directSearchTimer) clearTimeout(directSearchTimer)
  directSearchTimer = setTimeout(() => {
    directPage.value = 1
    loadDirectCapabilities()
  }, 250)
}

watch(searchQuery, debouncedReload)
watch(departmentFilter, () => {
  capPage.value = 1
  load()
})
watch(sortBy, () => {
  capPage.value = 1
  load()
})
watch(capPage, load)
watch(directSearchQuery, debouncedReloadDirect)
watch(directCategoryFilter, () => {
  directPage.value = 1
  loadDirectCapabilities()
})
watch(directSortBy, () => {
  directPage.value = 1
  loadDirectCapabilities()
})
watch(directPage, loadDirectCapabilities)
watch(artifactPage, loadDirectArtifacts)

function onModeChange(v: string | number | boolean) {
  const m = String(v)
  viewMode.value = m
  router.replace({ query: { ...route.query, view: m === 'ability' ? undefined : m } })
}

function onTypeTabChange(k: string | number) {
  const key = String(k)
  typeTab.value = key
  router.replace({ query: { ...route.query, tab: key === 'skill' ? undefined : key } })
}

function goDetail(cap: any) {
  pushRecent(cap.category, 'anon')
  router.push(`/hall/capability/${encodeURIComponent(cap.category)}`)
}

function goDirectCapability(cap: any) {
  if (!cap?.id) return
  pushRecent(String(cap.id), DIRECT_MEMORY_SCOPE)
  router.push(`/hall/abilities/${encodeURIComponent(cap.id)}`)
}

function directCanExecute(cap: any) {
  return cap?.permissions?.execute !== false
}

function directCanInspect(cap: any) {
  const permissions = cap?.permissions || {}
  if (permissions.inspect === true || permissions.debug === true || permissions.manage === true) return true
  const role = String((userStore as any).role || userStore.userInfo?.role || '').toLowerCase()
  return ['admin', 'system_admin', 'dept_admin', 'ai_engineer', 'aibp'].includes(role)
}

function isDirectFavorite(id: string | undefined): boolean {
  return !!id && directFavorites.value.includes(String(id))
}

function toggleDirectCapabilityFavorite(cap: any) {
  if (!cap?.id) return
  toggleFavorite(String(cap.id), DIRECT_MEMORY_SCOPE)
}

function onRecentClick(cat: string) {
  pushRecent(cat, 'anon')
  router.push(`/hall/capability/${encodeURIComponent(cat)}`)
}

function onFavClick(cap: any) {
  toggleFavorite(cap.category, 'anon')
}

function goTeam(department: string) {
  router.push(`/hall/team/${encodeURIComponent(department)}`)
}

watch(() => route.query.view, (v) => { viewMode.value = (v as string) || 'ability' })
watch(() => route.query.tab, (v) => { typeTab.value = (v as string) || 'skill' })

// ───────────────────────── L3-A · 运行抽屉 ─────────────────────────
type ParamField = {
  key: string
  label: string
  type: string
  required: boolean
  description?: string
  enum?: any[]
  widget?: string
  default?: any
}

type RunDrawerTab = 'params' | 'running' | 'logs' | 'result'
type OpenRunDrawerOptions = {
  initialTab?: RunDrawerTab
  autoStart?: boolean
}
type RunLogLine = {
  time: string
  level: 'info' | 'warn' | 'error'
  text: string
}
type RunStep = {
  id: string
  name: string
  state: 'done' | 'doing' | 'queued' | 'failed'
  symbol: string
  meta: string
  time: string
}

const runDrawerOpen = ref(false)
const runDrawerCap = ref<any | null>(null)
const runDrawerDetail = ref<any | null>(null) // 包含 input_schema 的完整 payload
const runDrawerDetailLoading = ref(false)
const runDrawerForm = ref<Record<string, any>>({})
const runDrawerTasks = ref<any[]>([])
const runDrawerTaskStats = ref<any | null>(null)
const runDrawerTasksLoading = ref(false)
const runDrawerSubmitting = ref(false)
const runDrawerTab = ref<RunDrawerTab>('params')
const runDrawerLiveTask = ref<any | null>(null)
const runDrawerLogLines = ref<RunLogLine[]>([])
const runDrawerSocket = ref<WebSocket | null>(null)
const runDrawerStartedAt = ref<number | null>(null)
const runDrawerElapsedSeconds = ref(0)
const runDrawerTimer = ref<number | null>(null)
const runDrawerWidth = 720

const runDrawerTabs: Array<{ key: RunDrawerTab; label: string }> = [
  { key: 'params', label: '参数' },
  { key: 'running', label: '运行中' },
  { key: 'logs', label: '日志' },
  { key: 'result', label: '结果' },
]

const runDrawerParamFields = computed<ParamField[]>(() => {
  const schema =
    runDrawerDetail.value?.input_schema ||
    runDrawerCap.value?.input_schema ||
    null
  if (!schema || typeof schema !== 'object') return []
  const props = (schema as any).properties
  if (!props || typeof props !== 'object') return []
  const required: string[] = Array.isArray((schema as any).required) ? (schema as any).required : []
  return Object.keys(props).map((key) => {
    const def = props[key] || {}
    return {
      key,
      label: String(def.title || def.label || key),
      type: String(def.type || 'string'),
      required: required.includes(key),
      description: def.description || def.help,
      enum: Array.isArray(def.enum) ? def.enum : undefined,
      widget: def['x-widget'] || def.widget,
      default: def.default,
    } as ParamField
  })
})

const runDrawerKpis = computed(() => {
  const stats = runDrawerTaskStats.value
  if (stats && typeof stats === 'object') {
    return {
      last_run_at: stats.last_run_at ? formatTaskTime(stats.last_run_at) : '—',
      avg_duration: typeof stats.avg_duration_seconds === 'number' ? formatDurationSeconds(stats.avg_duration_seconds) : '—',
      success_rate: typeof stats.success_rate === 'number' ? formatRate(stats.success_rate) : '—',
      run_count: Number(stats.total || 0),
    }
  }
  const tasks = runDrawerTasks.value || []
  let lastRun = '—'
  let avgDuration = '—'
  let successRate: number | '—' = '—'
  const runCount = tasks.length
  if (tasks.length) {
    const last = tasks[0]
    if (last?.created_at) lastRun = formatTaskTime(last.created_at)
    const terminal = tasks.filter((t) => isTaskTerminal(t)).length
    const succeeded = tasks.filter((t) => isTaskSuccess(t)).length
    successRate = terminal ? Math.round((succeeded / terminal) * 1000) / 10 : '—'
    const durations = tasks
      .map(taskDurationSeconds)
      .filter((s): s is number => typeof s === 'number' && s > 0)
    if (durations.length) {
      const avg = durations.reduce((a, b) => a + b, 0) / durations.length
      avgDuration = formatDurationSeconds(avg)
    }
  }
  return {
    last_run_at: lastRun,
    avg_duration: avgDuration,
    success_rate: successRate === '—' ? '—' : formatRate(successRate),
    run_count: runCount,
  }
})

const runDrawerLiveActive = computed(() => {
  if (runDrawerSubmitting.value) return true
  const s = String(runDrawerLiveTask.value?.status || '').toLowerCase()
  return !!s && !['completed', 'succeeded', 'success', 'failed', 'error', 'canceled', 'cancelled'].includes(s)
})

const runDrawerElapsedLabel = computed(() => formatDurationSeconds(runDrawerElapsedSeconds.value || 0))

const runDrawerParamSummary = computed(() => {
  const fields = runDrawerParamFields.value
  if (!fields.length) return [{ key: 'none', label: '参数', value: '无需参数' }]
  return fields.slice(0, 6).map((field) => {
    const raw = runDrawerForm.value[field.key]
    const value = raw === undefined || raw === null || raw === '' ? '—' : String(raw)
    return { key: field.key, label: field.label, value }
  })
})

const runDrawerProgressPercent = computed(() => {
  const s = String(runDrawerLiveTask.value?.status || '').toLowerCase()
  if (!s) return runDrawerSubmitting.value ? 8 : 0
  if (['completed', 'succeeded', 'success'].includes(s)) return 100
  if (['failed', 'error', 'canceled', 'cancelled'].includes(s)) return 100
  if (s === 'queued') return 18
  if (s === 'submitting') return 32
  if (s === 'rate_limited') return 42
  if (s === 'in_progress' || s === 'running') {
    const root = runDrawerResultRoot.value
    const progress = Number(root.progress ?? root.percent ?? root.completion)
    if (Number.isFinite(progress) && progress > 0) {
      return Math.max(45, Math.min(96, progress <= 1 ? progress * 100 : progress))
    }
    return 68
  }
  return 48
})

const runDrawerProgressText = computed(() => {
  const task = runDrawerLiveTask.value
  if (!task) return runDrawerSubmitting.value ? '正在创建运行任务...' : '点击运行后展示实时执行进度'
  const status = taskStatusLabel(task.status)
  const elapsed = runDrawerElapsedLabel.value
  return `当前 ${status} · 已运行 ${elapsed}`
})

const runDrawerResultRoot = computed<Record<string, any>>(() => {
  const task = runDrawerLiveTask.value
  const raw = task?.result
  if (!raw || typeof raw !== 'object') return {}
  const nested = raw.result
  return nested && typeof nested === 'object' ? nested : raw
})

const runDrawerImageUrls = computed<string[]>(() => {
  const taskUrls = Array.isArray(runDrawerLiveTask.value?.image_urls) ? runDrawerLiveTask.value.image_urls : []
  if (taskUrls.length) return taskUrls.map(String)
  const root = runDrawerResultRoot.value
  if (Array.isArray(root.image_urls)) return root.image_urls.map(String)
  const single = root.image_url || root.url
  return single ? [String(single)] : []
})

const runDrawerResultText = computed(() => {
  const root = runDrawerResultRoot.value
  if (!Object.keys(root).length) return '暂无结构化结果'
  return JSON.stringify(root, null, 2)
})

const runDrawerSteps = computed<RunStep[]>(() => {
  const task = runDrawerLiveTask.value
  const status = String(task?.status || '').toLowerCase()
  const terminalOk = ['completed', 'succeeded', 'success'].includes(status)
  const terminalBad = ['failed', 'error', 'canceled', 'cancelled'].includes(status)
  const elapsed = runDrawerElapsedLabel.value
  const hasTask = !!task
  const queuedDoing = runDrawerSubmitting.value && !hasTask
  return [
    {
      id: 'create',
      name: '创建运行任务',
      state: hasTask ? 'done' : (queuedDoing ? 'doing' : 'queued'),
      symbol: hasTask ? '✓' : '1',
      meta: hasTask ? `任务 ${shortTaskId(task.id)} 已创建` : '等待提交参数',
      time: hasTask ? formatTaskTime(task.created_at) : '—',
    },
    {
      id: 'submit',
      name: '启动能力执行',
      state: terminalBad ? 'failed' : (['submitting', 'in_progress', 'running', 'rate_limited'].includes(status) || terminalOk ? 'done' : (status === 'queued' ? 'doing' : 'queued')),
      symbol: terminalBad ? '!' : (['submitting', 'in_progress', 'running', 'rate_limited'].includes(status) || terminalOk ? '✓' : '2'),
      meta: status === 'queued' ? '等待可用执行槽' : status === 'rate_limited' ? '限流重试中' : 'runner 已接收请求',
      time: task?.started_at ? formatTaskTime(task.started_at) : '—',
    },
    {
      id: 'poll',
      name: '等待结果回传',
      state: terminalBad ? 'failed' : (terminalOk ? 'done' : (['in_progress', 'running', 'rate_limited'].includes(status) ? 'doing' : 'queued')),
      symbol: terminalBad ? '!' : (terminalOk ? '✓' : '3'),
      meta: terminalBad ? (task?.error || '运行失败') : terminalOk ? '已拿到最终结果' : '持续轮询任务状态',
      time: hasTask ? elapsed : '—',
    },
    {
      id: 'result',
      name: '整理结果与历史',
      state: terminalBad ? 'failed' : (terminalOk ? 'done' : 'queued'),
      symbol: terminalBad ? '!' : (terminalOk ? '✓' : '4'),
      meta: terminalOk ? `${runDrawerImageUrls.value.length} 个图片结果 / JSON 已记录` : '等待结果',
      time: task?.completed_at ? formatTaskTime(task.completed_at) : '—',
    },
  ]
})

async function openRunDrawer(cap: any, options: OpenRunDrawerOptions = {}) {
  if (!cap?.id) return
  if (!directCanExecute(cap)) return
  pushRecent(String(cap.id), DIRECT_MEMORY_SCOPE)
  stopRunDrawerStream()
  runDrawerCap.value = cap
  runDrawerDetail.value = null
  runDrawerForm.value = {}
  runDrawerTasks.value = []
  runDrawerTaskStats.value = null
  runDrawerLiveTask.value = null
  runDrawerLogLines.value = []
  runDrawerTab.value = options.initialTab || 'params'
  runDrawerOpen.value = true
  const detailPromise = loadRunDrawerDetail(cap.id)
  void loadRunDrawerTasks(cap.id)
  if (options.autoStart) {
    await detailPromise
    if (!runDrawerOpen.value || runDrawerCap.value?.id !== cap.id) return
    await runDrawerSubmit()
  }
}

function openCapabilityMenu(cap: any) {
  void openRunDrawer(cap, { initialTab: 'params' })
}

function runDirectCapabilityNow(cap: any) {
  goDirectCapability(cap)
}

function closeRunDrawer() {
  stopRunDrawerStream()
  runDrawerOpen.value = false
}

function onRunDrawerClose() {
  stopRunDrawerStream()
  runDrawerCap.value = null
  runDrawerDetail.value = null
  runDrawerForm.value = {}
  runDrawerTasks.value = []
  runDrawerTaskStats.value = null
  runDrawerLiveTask.value = null
  runDrawerLogLines.value = []
  runDrawerTab.value = 'params'
}

async function loadRunDrawerDetail(capId: string) {
  runDrawerDetailLoading.value = true
  try {
    const r: any = await hallApi.directCapability(capId)
    runDrawerDetail.value = r || null
    // 根据 schema 默认值初始化表单
    const fields = runDrawerParamFields.value
    const next: Record<string, any> = {}
    fields.forEach((f) => {
      next[f.key] = f.default !== undefined ? f.default : undefined
    })
    runDrawerForm.value = next
  } catch {
    runDrawerDetail.value = null
  } finally {
    runDrawerDetailLoading.value = false
  }
}

async function loadRunDrawerTasks(capId: string) {
  runDrawerTasksLoading.value = true
  try {
    const r: any = await hallApi.directCapabilityTasks(capId, { page: 1, page_size: 10, advance: false })
    const list = Array.isArray(r) ? r : r?.items || []
    runDrawerTasks.value = list
    runDrawerTaskStats.value = Array.isArray(r) ? null : (r?.stats || null)
  } catch {
    runDrawerTasks.value = []
    runDrawerTaskStats.value = null
  } finally {
    runDrawerTasksLoading.value = false
  }
}

function resetRunDrawerForm() {
  const fields = runDrawerParamFields.value
  const next: Record<string, any> = {}
  fields.forEach((f) => {
    next[f.key] = f.default !== undefined ? f.default : undefined
  })
  runDrawerForm.value = next
}

async function runDrawerSubmit() {
  if (!runDrawerCap.value) return
  const missing = runDrawerParamFields.value.filter((f) => {
    const value = runDrawerForm.value[f.key]
    return f.required && (value === undefined || value === null || value === '')
  })
  if (missing.length) {
    Message.warning(`请先填写 ${missing[0].label}`)
    runDrawerTab.value = 'params'
    return
  }
  runDrawerSubmitting.value = true
  runDrawerTab.value = 'running'
  runDrawerLiveTask.value = null
  runDrawerLogLines.value = []
  runDrawerStartedAt.value = Date.now()
  runDrawerElapsedSeconds.value = 0
  startRunDrawerTimer()
  pushRunLog('info', '提交运行参数')
  try {
    startRunDrawerStream(runDrawerCap.value.id, { ...runDrawerForm.value })
  } catch (error: any) {
    runDrawerSubmitting.value = false
    stopRunDrawerTimer()
    pushRunLog('error', error?.message || '运行启动失败')
    Message.error(error?.message || '运行启动失败')
  } finally {
    // 运行状态由 WebSocket 回调更新。
  }
}

function startRunDrawerTimer() {
  stopRunDrawerTimer()
  runDrawerTimer.value = window.setInterval(() => {
    if (!runDrawerStartedAt.value) {
      runDrawerElapsedSeconds.value = 0
      return
    }
    runDrawerElapsedSeconds.value = Math.max(0, Math.floor((Date.now() - runDrawerStartedAt.value) / 1000))
  }, 1000)
}

function stopRunDrawerTimer() {
  if (runDrawerTimer.value != null) {
    window.clearInterval(runDrawerTimer.value)
    runDrawerTimer.value = null
  }
}

function stopRunDrawerStream() {
  const socket = runDrawerSocket.value
  runDrawerSocket.value = null
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.close()
  } else if (socket && socket.readyState === WebSocket.CONNECTING) {
    socket.close()
  }
  stopRunDrawerTimer()
  runDrawerSubmitting.value = false
}

function startRunDrawerStream(capabilityId: string, params: Record<string, any>) {
  stopRunDrawerStream()
  runDrawerSubmitting.value = true
  startRunDrawerTimer()
  const socket = hallApi.openDirectCapabilityTaskStream(capabilityId)
  runDrawerSocket.value = socket

  socket.onopen = () => {
    socket.send(JSON.stringify({ type: 'params', params }))
    pushRunLog('info', '运行流已连接')
  }
  socket.onmessage = (event) => {
    let payload: any
    try {
      payload = JSON.parse(event.data)
    } catch {
      return
    }
    if (payload.type === 'heartbeat') return
    if (payload.type === 'started') {
      pushRunLog('info', '服务端已开始创建任务')
      return
    }
    if (payload.type === 'runner_event') {
      applyRunDrawerRunnerEvent(payload.event)
      return
    }
    if (payload.type === 'task' || payload.type === 'result') {
      applyRunDrawerTask(payload.task, payload.phase || payload.type)
      return
    }
    if (payload.type === 'error') {
      runDrawerSubmitting.value = false
      pushRunLog('error', payload.message || '运行失败')
      Message.error(payload.message || '运行失败')
      runDrawerTab.value = 'logs'
    }
  }
  socket.onerror = () => {
    pushRunLog('warn', '运行流连接异常，任务可能仍在后台执行')
  }
  socket.onclose = () => {
    runDrawerSocket.value = null
    if (!runDrawerLiveActive.value) {
      runDrawerSubmitting.value = false
      stopRunDrawerTimer()
    } else if (!runDrawerLiveTask.value) {
      runDrawerSubmitting.value = false
      stopRunDrawerTimer()
      pushRunLog('error', '运行流已断开，未创建任务')
    } else {
      runDrawerSubmitting.value = false
      stopRunDrawerTimer()
      pushRunLog('warn', '运行流已断开，任务已保留在后台，可稍后刷新最近运行')
      const capId = runDrawerLiveTask.value.capability_id || runDrawerCap.value?.id
      if (capId) void loadRunDrawerTasks(capId)
    }
  }
}

function applyRunDrawerRunnerEvent(event: any) {
  if (!event || typeof event !== 'object') return
  if (event.type === 'log') {
    const level: RunLogLine['level'] =
      event.level === 'error' ? 'error' : event.level === 'warn' || event.stream === 'stderr' ? 'warn' : 'info'
    pushRunLog(level, String(event.text || '').slice(-1200))
    return
  }
  if (event.type === 'process_started') {
    pushRunLog('info', '子进程已启动')
    return
  }
  if (event.type === 'process_exit') {
    const code = Number(event.exit_code)
    pushRunLog(code === 0 ? 'info' : 'error', `子进程退出：${Number.isFinite(code) ? code : 'unknown'}`)
    return
  }
  if (event.type === 'process_timeout') {
    pushRunLog('error', '子进程执行超时')
  }
}

function applyRunDrawerTask(task: any, phase: string) {
  if (!task || typeof task !== 'object') return
  const previousStatus = String(runDrawerLiveTask.value?.status || '')
  runDrawerLiveTask.value = task
  const existed = runDrawerTasks.value.some((t) => t.id === task.id)
  if (!existed) {
    runDrawerTasks.value = [task, ...runDrawerTasks.value].slice(0, 10)
  } else {
    runDrawerTasks.value = runDrawerTasks.value.map((t) => (t.id === task.id ? task : t))
  }
  updateRunDrawerStatsFromTasks({ inserted: !existed })
  const status = String(task.status || '')
  if (phase === 'created') {
    pushRunLog('info', `任务已创建 ${shortTaskId(task.id)}`)
  } else if (status && status !== previousStatus) {
    pushRunLog(status === 'failed' ? 'error' : status === 'rate_limited' ? 'warn' : 'info', `状态更新：${taskStatusLabel(status)}`)
  }
  const root = taskResultRoot(task)
  if (root.stderr) pushRunLog('warn', String(root.stderr).slice(-300))
  if (root.stdout) pushRunLog('info', String(root.stdout).slice(-300))
  if (task.error) pushRunLog('error', String(task.error).slice(0, 300))
  if (['completed', 'succeeded', 'success', 'failed', 'error', 'canceled', 'cancelled'].includes(status.toLowerCase())) {
    runDrawerSubmitting.value = false
    stopRunDrawerTimer()
    runDrawerTab.value = status.toLowerCase() === 'failed' || task.error ? 'logs' : 'result'
    const capId = task.capability_id || runDrawerCap.value?.id
    if (capId) void loadRunDrawerTasks(capId)
    void loadDirectArtifacts()
  }
}

function pushRunLog(level: RunLogLine['level'], text: string) {
  if (!text) return
  const time = new Date().toTimeString().slice(0, 8)
  const last = runDrawerLogLines.value[runDrawerLogLines.value.length - 1]
  if (last && last.level === level && last.text === text) return
  runDrawerLogLines.value = [...runDrawerLogLines.value, { time, level, text }].slice(-120)
}

function taskResultRoot(task: any): Record<string, any> {
  const raw = task?.result
  if (!raw || typeof raw !== 'object') return {}
  const nested = raw.result
  return nested && typeof nested === 'object' ? nested : raw
}

function shortTaskId(id: string | undefined): string {
  if (!id) return '—'
  return String(id).slice(0, 8)
}

function taskPillClass(status: string | undefined) {
  const s = String(status || '').toLowerCase()
  if (s === 'completed' || s === 'succeeded' || s === 'success') return 'ok'
  if (s === 'failed' || s === 'error' || s === 'canceled' || s === 'cancelled') return 'bad'
  if (s === 'pending' || s === 'queued') return 'info'
  return 'warn'
}

function taskStatusLabel(status: string | undefined) {
  const s = String(status || '').toLowerCase()
  const map: Record<string, string> = {
    completed: '成功',
    succeeded: '成功',
    success: '成功',
    failed: '失败',
    error: '失败',
    canceled: '已取消',
    cancelled: '已取消',
    pending: '排队',
    queued: '排队',
    submitting: '提交中',
    in_progress: '运行中',
    running: '运行中',
    rate_limited: '限流重试',
  }
  return map[s] || (status || '—')
}

function formatTaskTime(ts: string | number | null | undefined): string {
  if (!ts) return '—'
  try {
    const d = typeof ts === 'number' ? new Date(ts * (ts < 1e12 ? 1000 : 1)) : new Date(ts)
    if (Number.isNaN(d.getTime())) return '—'
    const pad = (n: number) => (n < 10 ? '0' + n : '' + n)
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  } catch {
    return '—'
  }
}

function taskDurationSeconds(t: any): number | null {
  if (!t) return null
  const start = t.started_at || t.created_at
  const end = t.completed_at
  if (!start || !end) return null
  try {
    const s = new Date(start).getTime()
    const e = new Date(end).getTime()
    if (Number.isNaN(s) || Number.isNaN(e)) return null
    const diff = (e - s) / 1000
    return diff > 0 ? diff : null
  } catch {
    return null
  }
}

function formatDurationSeconds(seconds: number): string {
  if (!seconds || seconds <= 0) return '—'
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}m${s}s`
}

function formatRate(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

function isTaskTerminal(t: any): boolean {
  const s = String(t?.status || '').toLowerCase()
  return ['completed', 'succeeded', 'success', 'failed', 'error', 'canceled', 'cancelled'].includes(s)
}

function isTaskSuccess(t: any): boolean {
  const s = String(t?.status || '').toLowerCase()
  return ['completed', 'succeeded', 'success'].includes(s) && !t?.error
}

function updateRunDrawerStatsFromTasks(options: { inserted?: boolean } = {}) {
  const tasks = runDrawerTasks.value || []
  if (runDrawerTaskStats.value) {
    runDrawerTaskStats.value = {
      ...runDrawerTaskStats.value,
      total: Number(runDrawerTaskStats.value.total || 0) + (options.inserted ? 1 : 0),
      last_run_at: tasks[0]?.created_at || runDrawerTaskStats.value.last_run_at || null,
    }
    return
  }
  if (!tasks.length) {
    runDrawerTaskStats.value = null
    return
  }
  const terminal = tasks.filter(isTaskTerminal)
  const successCount = terminal.filter(isTaskSuccess).length
  const failedCount = terminal.length - successCount
  const durations = tasks
    .map(taskDurationSeconds)
    .filter((s): s is number => typeof s === 'number' && s > 0)
  runDrawerTaskStats.value = {
    total: Math.max(Number(runDrawerTaskStats.value?.total || 0), tasks.length),
    success_count: successCount,
    failed_count: failedCount,
    terminal_count: terminal.length,
    success_rate: terminal.length ? Math.round((successCount / terminal.length) * 1000) / 10 : null,
    avg_duration_seconds: durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null,
    last_run_at: tasks[0]?.created_at || runDrawerTaskStats.value?.last_run_at || null,
  }
}

function formatTaskDuration(t: any): string {
  const s = taskDurationSeconds(t)
  return s != null ? formatDurationSeconds(s) : '—'
}

onMounted(() => {
  loadDirectCapabilities()
  loadDirectArtifacts()
  loadDepartments()
  load()
  loadBands()
  loadPendingRequests()
})

onBeforeUnmount(() => {
  stopRunDrawerStream()
})
</script>

<style scoped>
.hall-home {
  display: flex;
  flex-direction: row;
  gap: 0;
  align-items: stretch;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
  background: var(--ai-bg);
  min-height: calc(100vh - 52px);
  padding: 0 !important;
  max-width: none !important;
}

/* 左侧 ai-sidebar */
.hall-sidebar {
  width: 220px;
  flex: 0 0 220px;
  border-right: 1px solid var(--ai-border);
  background: var(--ai-surface);
  padding: 16px 12px;
  overflow-y: auto;
}

.hall-sidebar-head {
  display: none;
  align-items: center;
  justify-content: space-between;
  padding: 0 4px 6px;
}
.hall-sidebar-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}
.hall-sidebar-close {
  display: none;
  background: transparent;
  border: 0;
  cursor: pointer;
  color: var(--ai-ink-4);
  padding: 4px;
}
.hall-sidebar-close:hover {
  color: var(--ai-ink-1);
}
.hall-sidebar-close svg {
  width: 13px;
  height: 13px;
}
.hall-sidebar-toggle {
  display: none;
  align-items: center;
  gap: 8px;
  margin: 12px 16px 0;
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
}
.hall-sidebar-toggle svg {
  width: 13px;
  height: 13px;
}

.hall-sidebar .ai-side-group {
  margin-bottom: 18px;
}
.hall-sidebar .ai-side-label {
  font-size: 11px;
  font-weight: 500;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 0 8px 6px;
}
.hall-sidebar .ai-side-item {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 28px;
  padding: 0 8px;
  border-radius: 5px;
  font-size: 13px;
  color: var(--ai-ink-2);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.hall-sidebar .ai-side-item .ic {
  color: var(--ai-ink-4);
  width: 13px;
  height: 13px;
  flex: 0 0 13px;
}
.hall-sidebar .ai-side-item:hover {
  background: var(--ai-surface-2);
}
.hall-sidebar .ai-side-item.active {
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  font-weight: 500;
}
.hall-sidebar .ai-side-item.active .ic {
  color: var(--ai-ink-1);
}
.hall-sidebar .ai-side-item .count {
  margin-left: auto;
  font-size: 11px;
  color: var(--ai-ink-4);
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
}

/* 设计稿 ability-hall.jsx 的 deptdot：8×8 圆角方块，左侧贴边 */
.hall-sidebar .deptdot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  display: inline-block;
  flex: 0 0 8px;
}
.hall-sidebar .ai-side-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 设计稿 ability-hall.jsx：头部右侧 actions 区 */
.hall-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  /* 不收缩到挤压文字（窄屏 900px 时 segmented control 被压成单字符堆叠的 bug） */
  flex-shrink: 0;
}
.hall-head-actions .ai-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  font-family: var(--ai-font-sans);
  cursor: pointer;
  white-space: nowrap;
}
.hall-head-actions .ai-btn:hover {
  background: var(--ai-surface-2);
}
.hall-head-actions svg {
  width: 12px;
  height: 12px;
  flex: 0 0 12px;
}
.hall-head-count {
  margin-left: 4px;
}

/* 设计稿 ability-hall.jsx 的 filter-bar：search + dropdowns + spacer + 5 pill */
.hall-home .filter-bar .filter-spacer {
  flex: 1;
}
.hall-search {
  width: 200px;
  max-width: 100%;
}
.hall-search-wide {
  width: 320px;
}
.hall-filter-select {
  width: 140px;
  max-width: 100%;
}
.hall-full-spin {
  width: 100%;
}
.hall-status-pills {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.hall-status-pills .ai-pill {
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.hall-status-pills .ai-pill.is-active {
  background: var(--ai-ink-1);
  color: var(--ai-surface);
  border-color: var(--ai-ink-1);
}
.hall-status-pills .ai-pill:hover:not(.is-active) {
  background: var(--ai-surface-3);
}

/* 主区 */
.hall-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
  padding: 0;
  overflow-x: auto;
}
.hall-pagehead {
  flex: 0 0 auto;
}

@media (max-width: 768px) {
  .hall-home {
    flex-direction: column;
  }
  .hall-sidebar {
    position: fixed;
    top: 0;
    left: 0;
    height: 100vh;
    width: min(280px, 80vw);
    max-height: none;
    z-index: 200;
    flex: 0 0 auto;
    border-right: 1px solid var(--ai-border);
    border-bottom: 0;
    transform: translateX(-110%);
    transition: transform 0.2s ease;
    box-shadow: var(--ai-shadow-2);
  }
  .hall-sidebar--open {
    transform: translateX(0);
  }
  .hall-sidebar-head {
    display: flex;
  }
  .hall-sidebar-close {
    display: inline-flex;
  }
  .hall-sidebar-toggle {
    display: inline-flex;
    align-self: flex-start;
  }
  .hall-pagehead {
    flex-direction: column;
    align-items: stretch;
    padding: 16px;
  }
  .hall-content {
    padding: 16px;
  }
  .pending-banner {
    margin-left: 16px;
    margin-right: 16px;
  }
}

.hall-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
  background: transparent;
}

.pending-banner {
  margin: 16px 28px 0;
  border-radius: var(--ai-radius);
}

.filter-bar {
  display: flex;
  gap: 8px;
  padding: 10px 14px;
  border-radius: var(--ai-radius);
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  flex-wrap: wrap;
  align-items: center;
}

.filter-checkbox {
  font-size: 12px;
}

.recent-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 0;
  padding: 6px 10px;
  background: var(--ai-surface-2);
  border-radius: 10px;
}

/* G3: 能力大厅首屏的横向 band 区，和下方能力卡网格之间留透气 */
.hall-bands {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin: 0;
}
.hall-capability-body {
  gap: 16px;
}
.hall-capability-overview {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 18px;
  border-radius: var(--ai-radius-l);
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.hall-capability-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.hall-capability-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
  height: 24px;
  padding: 0 9px;
  border-radius: 999px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-accent-ink);
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.04em;
}
.hall-capability-eyebrow svg,
.hall-metric-label svg,
.capability-filter-label svg,
.capability-band-icon svg,
.capability-mini-meta svg,
.capability-band-empty svg {
  width: 13px;
  height: 13px;
}
.hall-capability-copy h2 {
  margin: 0;
  color: var(--ai-ink-1);
  font-size: 20px;
  font-weight: 650;
  letter-spacing: -0.02em;
  line-height: 1.25;
}
.hall-capability-copy p {
  margin: 0;
  color: var(--ai-ink-3);
  font-size: 13px;
  line-height: 1.6;
}
.hall-capability-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}
.hall-capability-metrics {
  min-width: 360px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.hall-metric-card {
  min-height: 70px;
  padding: 12px;
  border-radius: var(--ai-radius);
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}
.hall-metric-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-weight: 500;
}
.hall-metric-card strong {
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-size: 24px;
  font-weight: 650;
  letter-spacing: -0.02em;
}
.capability-filter-bar {
  position: sticky;
  top: 0;
  z-index: 3;
  box-shadow: var(--ai-shadow-1);
}
.capability-filter-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 8px 0 0;
  color: var(--ai-ink-2);
  font-size: 12px;
  font-weight: 600;
}
.capability-band {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-l);
  background: var(--ai-surface);
  box-shadow: var(--ai-shadow-1);
}
.capability-band-info {
  --cap-band-tone: var(--ai-info);
  --cap-band-tone-soft: var(--ai-info-soft);
}
.capability-band-brand {
  --cap-band-tone: var(--ai-accent);
  --cap-band-tone-soft: var(--ai-accent-soft);
}
.capability-band-success {
  --cap-band-tone: var(--ai-ok);
  --cap-band-tone-soft: var(--ai-ok-soft);
}
.capability-band-head {
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-areas:
    "title action"
    "sub action";
  align-items: center;
  gap: 2px 8px;
}
.capability-band-title {
  grid-area: title;
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--ai-ink-1);
  font-size: 14px;
  font-weight: 650;
}
.capability-band-icon {
  width: 24px;
  height: 24px;
  border-radius: 7px;
  display: grid;
  place-items: center;
  color: var(--cap-band-tone);
  background: var(--cap-band-tone-soft);
}
.capability-band-head p {
  grid-area: sub;
  margin: 0;
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.4;
}
.capability-band-head .ai-btn {
  grid-area: action;
  align-self: start;
}
.capability-band-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.capability-mini-card {
  min-height: 104px;
  padding: 10px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  display: flex;
  flex-direction: column;
  gap: 7px;
  text-align: left;
  font-family: var(--ai-font-sans);
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
}
.capability-mini-card:hover {
  border-color: var(--ai-border-2);
  background: var(--ai-surface-2);
  box-shadow: var(--ai-shadow-1);
}
.capability-mini-card:focus-visible {
  outline: 2px solid var(--ai-accent);
  outline-offset: 2px;
}
.capability-mini-top,
.capability-mini-meta {
  display: flex;
  align-items: center;
  gap: 6px;
}
.capability-mini-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
  font-weight: 600;
}
.capability-mini-desc {
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.45;
  min-height: 34px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.capability-mini-meta {
  margin-top: auto;
  justify-content: space-between;
  color: var(--ai-ink-4);
  font-size: 11px;
  font-family: var(--ai-font-mono);
}
.capability-mini-meta span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.skeleton-card {
  pointer-events: none;
  background: var(--ai-surface-2);
  position: relative;
  overflow: hidden;
}
.skeleton-card::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--ai-surface);
  opacity: 0.55;
  animation: hall-band-skeleton 1.4s linear infinite;
}
@keyframes hall-band-skeleton {
  from { transform: translateX(-100%); }
  to { transform: translateX(100%); }
}
.capability-band-empty {
  min-height: 104px;
  display: grid;
  place-items: center;
  gap: 6px;
  padding: 12px;
  border-radius: var(--ai-radius);
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
  color: var(--ai-ink-3);
  font-size: 12px;
}
.recent-label {
  font-size: 12px;
  color: var(--ai-ink-3);
  margin-right: 4px;
}
.recent-chip {
  cursor: pointer;
}
.cap-card-wrap {
  outline: none;
  border-radius: 8px;
}
.cap-card-wrap:focus-visible {
  outline: 2px solid var(--ai-accent);
  outline-offset: 2px;
}
/* 能力卡片（设计稿 ability-hall.jsx · Crisp Mono 风格） */
.direct-capability-card {
  min-height: 200px;
  padding: 14px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 10px;
  position: relative;
  transition: border-color 0.15s, box-shadow 0.15s;
  font-family: var(--ai-font-sans);
}
.direct-capability-card:hover {
  border-color: var(--ai-border-2);
  box-shadow: var(--ai-shadow-2);
}
.direct-capability-card:focus-visible {
  outline: 2px solid var(--ai-accent);
  outline-offset: 2px;
}
.direct-card-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.direct-icon {
  --hall-card-icon-fg: var(--ai-accent-ink);
  --hall-card-icon-bg: var(--ai-accent-soft);
  width: 26px;
  height: 26px;
  border-radius: 999px;
  display: grid;
  place-items: center;
  color: var(--hall-card-icon-fg);
  background: var(--hall-card-icon-bg);
  border: 1px solid color-mix(in srgb, var(--hall-card-icon-fg), transparent 82%);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.42);
  flex: 0 0 26px;
  font-size: 13px;
}
.direct-icon--info {
  --hall-card-icon-fg: var(--ai-info);
  --hall-card-icon-bg: var(--ai-info-soft);
}
.direct-icon--ok {
  --hall-card-icon-fg: var(--ai-ok);
  --hall-card-icon-bg: var(--ai-ok-soft);
}
.direct-icon--warn {
  --hall-card-icon-fg: var(--ai-warn);
  --hall-card-icon-bg: var(--ai-warn-soft);
}
.direct-icon--bad {
  --hall-card-icon-fg: var(--ai-bad);
  --hall-card-icon-bg: var(--ai-bad-soft);
}
.direct-icon svg,
.direct-card-foot svg,
.run-drawer-icon svg,
.run-drawer-close svg,
.artifacts-head .ai-iconbtn svg {
  width: 14px;
  height: 14px;
}
.direct-card-headline {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.direct-card-name {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.01em;
  line-height: 1.3;
  color: var(--ai-ink-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.direct-card-pills {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.direct-card-desc {
  margin: 0;
  color: var(--ai-ink-3);
  font-size: 12.5px;
  line-height: 1.5;
  min-height: 36px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.direct-card-foot {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: auto;
  padding-top: 8px;
  /* 1px dashed 在 webkit 经常被渲染成 solid，用 background dash pattern 替代以保证视觉一致 */
  background-image: linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%);
  background-position: top;
  background-size: 6px 1px;
  background-repeat: repeat-x;
}
.direct-run-btn {
  flex: 1;
  justify-content: center;
  height: 30px;
}
.direct-run-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.direct-more-btn {
  width: 30px;
  height: 30px;
  border: 1px solid var(--ai-border);
}
.direct-more-btn.active {
  color: var(--ai-warn);
  border-color: color-mix(in srgb, var(--ai-warn), var(--ai-border) 45%);
  background: var(--ai-warn-soft);
}
.domain-groups {
  background: transparent;
}
.hall-type-body {
  display: grid;
  gap: 16px;
}
.hall-type-panel {
  display: grid;
  gap: 16px;
  padding: 18px;
}
.hall-type-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--ai-border);
}
.hall-type-copy {
  min-width: 0;
}
.hall-type-copy h2 {
  margin: 8px 0 4px;
  color: var(--ai-ink-1);
  font-size: 18px;
  line-height: 1.25;
  letter-spacing: -0.02em;
}
.hall-type-copy p {
  margin: 0;
  color: var(--ai-ink-3);
  font-size: 12.5px;
}
.hall-type-switch {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-l);
  background: var(--ai-surface-2);
  box-shadow: var(--ai-shadow-1);
}
.hall-type-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border: 0;
  border-radius: var(--ai-radius);
  background: transparent;
  color: var(--ai-ink-3);
  font: inherit;
  font-size: 12px;
  font-weight: 650;
  cursor: pointer;
}
.hall-type-tab:hover {
  color: var(--ai-ink-1);
  background: var(--ai-surface);
}
.hall-type-tab.active {
  color: var(--ai-accent-ink);
  background: var(--ai-surface);
  box-shadow: var(--ai-shadow-1);
}
.hall-type-content :deep(.filter-bar) {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 16px;
  padding: 12px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-l);
  background: var(--ai-surface);
  box-shadow: var(--ai-shadow-1);
}
.hall-type-content :deep(.arco-card),
.hall-type-content :deep(.skill-card),
.hall-type-content :deep(.ds-card),
.hall-type-content :deep(.team-card) {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-l);
  background: var(--ai-surface);
  box-shadow: var(--ai-shadow-1);
}
.hall-type-content :deep(.arco-card:hover),
.hall-type-content :deep(.skill-card:hover),
.hall-type-content :deep(.ds-card:hover),
.hall-type-content :deep(.team-card:hover) {
  border-color: var(--ai-border-2);
  box-shadow: var(--ai-shadow-2);
}
.empty-hint {
  color: var(--ai-ink-3);
  font-size: 13px;
  margin-top: 4px;
}
.table-footer {
  margin-top: 24px;
  display: flex;
  justify-content: flex-end;
}

/* 窄屏（≤960px）时 actions 换行到下方，避免 segmented control 被挤压成单字符堆叠 */
@media (max-width: 960px) {
  .hall-pagehead {
    flex-direction: column;
    align-items: flex-start;
  }
  .hall-head-actions {
    width: 100%;
  }
  .hall-bands {
    grid-template-columns: 1fr;
  }
  .hall-capability-overview {
    flex-direction: column;
    align-items: stretch;
  }
  .hall-type-head {
    flex-direction: column;
    align-items: stretch;
  }
  .hall-type-switch {
    width: 100%;
    flex-wrap: wrap;
  }
  .hall-type-tab {
    flex: 1;
    justify-content: center;
  }
  .hall-capability-metrics {
    min-width: 0;
    width: 100%;
  }
}

@media (min-width: 961px) and (max-width: 1320px) {
  .hall-bands {
    grid-template-columns: 1fr;
  }
  .capability-band-list {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

@media (max-width: 560px) {
  .hall-capability-metrics,
  .capability-band-list {
    grid-template-columns: 1fr;
  }
  .capability-band-head {
    grid-template-columns: 1fr;
    grid-template-areas:
      "title"
      "sub"
      "action";
  }
  .capability-band-head .ai-btn {
    justify-self: flex-start;
    margin-top: 4px;
  }
}

/* ───────── L3-A · 能力运行抽屉 ───────── */
.run-drawer :deep(.arco-drawer) {
  background: var(--ai-bg);
}
.run-drawer :deep(.arco-drawer-body) {
  padding: 0;
  background: var(--ai-bg);
  display: flex;
  flex-direction: column;
}
.run-drawer-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}

.run-drawer-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 16px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.run-drawer-icon {
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  border-radius: 7px;
  display: grid;
  place-items: center;
  background: var(--ai-surface-2);
  color: var(--ai-ink-1);
  font-size: 14px;
}
.run-drawer-title-wrap {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.run-drawer-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.015em;
  color: var(--ai-ink-1);
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.run-drawer-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.run-drawer-meta {
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.run-drawer-close {
  flex: 0 0 28px;
}

.run-drawer-tabs {
  display: flex;
  align-items: center;
  gap: 18px;
  min-height: 38px;
  padding: 0 16px;
  border-bottom: 1px solid var(--ai-border);
  background: var(--ai-surface);
}
.run-drawer-tab {
  height: 38px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 0;
  border-bottom: 1.5px solid transparent;
  background: transparent;
  color: var(--ai-ink-3);
  font-family: var(--ai-font-sans);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}
.run-drawer-tab:hover,
.run-drawer-tab.active {
  color: var(--ai-ink-1);
}
.run-drawer-tab.active {
  border-bottom-color: var(--ai-ink-1);
}
.run-live-pill {
  height: 16px;
  padding: 0 5px;
  font-size: 10px;
}

.run-drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: var(--ai-bg);
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.run-kpi-strip {
  display: flex;
  align-items: stretch;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  border: 1px solid var(--ai-border);
  overflow: hidden;
}
.run-kpi-cell {
  flex: 1 1 0;
  min-width: 0;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  border-left: 1px solid var(--ai-border);
}
.run-kpi-cell:first-child {
  border-left: 0;
}
.run-kpi-label {
  color: var(--ai-ink-4);
  font-size: 10.5px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  line-height: 1.2;
}
.run-kpi-value {
  color: var(--ai-ink-1);
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  font-family: var(--ai-font-mono);
  display: inline-flex;
  align-items: baseline;
}
.run-kpi-suffix {
  font-size: 12px;
  font-weight: 500;
  color: var(--ai-ink-4);
  margin-left: 2px;
  font-family: var(--ai-font-mono);
}

.run-drawer-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.run-section-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.run-section-label {
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--ai-ink-4);
}
.run-empty-hint {
  color: var(--ai-ink-4);
  font-size: 12.5px;
  padding: 10px 12px;
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  background-image:
    linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to right, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to bottom, var(--ai-border-2) 50%, transparent 0%),
    linear-gradient(to bottom, var(--ai-border-2) 50%, transparent 0%);
  background-position: top, bottom, left, right;
  background-size: 6px 1px, 6px 1px, 1px 6px, 1px 6px;
  background-repeat: repeat-x, repeat-x, repeat-y, repeat-y;
}

.run-drawer-form :deep(.arco-input),
.run-drawer-form :deep(.arco-input-wrapper),
.run-drawer-form :deep(.arco-input-number),
.run-drawer-form :deep(.arco-select-view-single),
.run-drawer-form :deep(.arco-textarea-wrapper) {
  border-radius: 6px !important;
  background: var(--ai-surface) !important;
}
.run-drawer-form :deep(.arco-form-item-label-col label) {
  font-size: 12px;
  color: var(--ai-ink-2);
  font-weight: 500;
}

.run-task-list {
  display: flex;
  flex-direction: column;
  border-radius: var(--ai-radius);
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  overflow: hidden;
}
.run-task-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--ai-border);
  font-size: 12.5px;
}
.run-task-row:last-child {
  border-bottom: 0;
}
.run-task-time {
  color: var(--ai-ink-2);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  min-width: 90px;
}
.run-task-duration {
  color: var(--ai-ink-3);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  min-width: 56px;
}
.run-task-detail {
  margin-left: auto;
  background: transparent;
  border: 0;
  padding: 0;
  font-size: 12px;
  color: var(--ai-accent);
  cursor: pointer;
}
.run-task-detail:hover {
  text-decoration: underline;
}

.run-param-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  padding: 11px 14px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  color: var(--ai-ink-2);
  font-size: 12.5px;
}
.run-param-summary .muted {
  color: var(--ai-ink-4);
  margin-right: 3px;
}
.run-progress-card,
.run-result-card {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  overflow: hidden;
}
.run-progress-head {
  padding: 12px 14px;
}
.run-progress-subtitle {
  margin-top: 3px;
  color: var(--ai-ink-4);
  font-size: 12px;
}
.run-progress-bar {
  height: 4px;
  background: var(--ai-surface-3);
}
.run-progress-bar span {
  display: block;
  height: 100%;
  background: var(--ai-accent);
  transition: width 0.2s ease;
}
.run-step-list {
  display: flex;
  flex-direction: column;
}
.run-step-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-top: 1px solid var(--ai-border);
}
.run-step-dot {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  flex: 0 0 22px;
  background: var(--ai-surface-3);
  color: var(--ai-ink-4);
  font-family: var(--ai-font-mono);
  font-size: 11px;
  font-weight: 600;
}
.run-step-dot.done {
  background: var(--ai-ok);
  color: var(--ai-surface);
}
.run-step-dot.doing {
  background: var(--ai-accent);
  color: var(--ai-surface);
}
.run-step-dot.failed {
  background: var(--ai-bad);
  color: var(--ai-surface);
}
.run-step-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.run-step-main span {
  font-size: 13px;
  font-weight: 500;
  color: var(--ai-ink-1);
}
.run-step-main small {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.run-step-time {
  width: 78px;
  text-align: right;
  color: var(--ai-ink-4);
  font-size: 11.5px;
}
.run-terminal {
  min-height: 240px;
  max-height: 360px;
  overflow: auto;
  padding: 14px;
  border-radius: var(--ai-radius);
  background: var(--ai-ink-1);
  color: var(--ai-surface-2);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  line-height: 1.7;
}
.run-log-time {
  color: var(--ai-ink-4);
  margin-right: 8px;
}
.run-log-level {
  margin-right: 8px;
  color: var(--ai-surface-2);
}
.run-log-level.warn {
  color: var(--ai-warn);
}
.run-log-level.error {
  color: var(--ai-bad);
}
.run-terminal-empty {
  color: var(--ai-ink-4);
}
.run-result-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--ai-border);
}
.run-result-error {
  padding: 12px 14px;
  border-radius: var(--ai-radius);
  background: var(--ai-bad-soft);
  color: var(--ai-bad);
  font-size: 12.5px;
}
.run-result-images {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 10px;
  padding: 14px;
  border-bottom: 1px solid var(--ai-border);
}
.run-result-images img {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface-2);
}
.run-result-json {
  margin: 0;
  max-height: 320px;
  overflow: auto;
  padding: 14px;
  color: var(--ai-ink-2);
  background: var(--ai-surface-2);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  line-height: 1.65;
  white-space: pre-wrap;
}

.run-drawer-foot {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: var(--ai-surface);
  border-top: 1px solid var(--ai-border);
}
.run-drawer-spacer {
  flex: 1;
}

.artifacts-drawer :deep(.arco-drawer-body) {
  padding: 0;
  background: var(--ai-bg);
}
.artifacts-shell {
  min-height: 100%;
  display: flex;
  flex-direction: column;
}
.artifacts-head {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 20px 16px;
  background: var(--ai-surface);
  border-bottom: 1px solid var(--ai-border);
}
.artifacts-head h3 {
  margin: 2px 0 4px;
  font-size: 18px;
  color: var(--ai-ink-1);
}
.artifacts-head p {
  margin: 0;
  color: var(--ai-ink-4);
  font-size: 12.5px;
}
.artifact-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  padding: 16px;
}
.artifact-card {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  overflow: hidden;
}
.artifact-card img {
  display: block;
  width: 100%;
  aspect-ratio: 4 / 3;
  object-fit: cover;
  background: var(--ai-surface-2);
  border-bottom: 1px solid var(--ai-border);
}
.artifact-card-body {
  padding: 10px;
}
.artifact-name {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.artifact-meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 4px;
  color: var(--ai-ink-4);
  font-size: 11.5px;
}
.artifact-meta span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.artifact-card p {
  min-height: 36px;
  margin: 8px 0 10px;
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.artifact-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.artifact-actions a.ai-btn {
  text-decoration: none;
}
</style>
