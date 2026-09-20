/**
 * v2.9.0+：推荐从 aggregate barrel import；新 call 点优先走 `useObservability`.
 * 本 composable 仍作为内部实现保留，aggregate 复用它来承载验证/测试/沙箱逻辑。
 *   new: import { ... } from "@/composables/useObservability"
 */
/**
 * O13 v2.2.1 W7c+: Studio 验证 / 测试 / 沙箱三件套的 composable
 *
 * 从 SkillStudio.vue 抽出的独立单元。覆盖：
 *   - handleValidate: 跑 /api/skills/{id}/validate，把结果挂 validationResult，展开底部"验证"tab
 *   - handleRunTest: 跑 /api/test/{id}?run_all=true，记录到 runningTasks dock
 *   - handleRunSandbox: 跑 /api/execution/run sandbox=true，记录 runningTasks + 结果
 *
 * 导出 3 个响应式 result/loading ref 供模板绑定，3 个 handler 供 @event 绑定。
 */
import { ref } from 'vue'
import { Message } from '@arco-design/web-vue'

export interface StudioValidationDeps {
  // v2.6: 细分依赖
  wb: any  // skillId
  studioDoc: any  // doc (ComputedRef)
  ui: { toggleBottomPanel: (tab?: string) => void } | any
  runningTasks: {
    start: (opts: Record<string, unknown>) => string
    finish: (id: string, opts: Record<string, unknown>) => void
    fail: (id: string, msg: string) => void
  } | any
  skillApi: { validate: (id: string) => Promise<unknown> } | any
  testApi: { runTest: (id: string, opts: Record<string, unknown>) => Promise<unknown> } | any
  executionApi: { run: (opts: Record<string, unknown>) => Promise<unknown> } | any
}

export function useStudioValidation(deps: StudioValidationDeps) {
  // v2.6+: 类型从 `unknown` 放宽到 `Record<string, unknown> | null`，兼容 WorkbenchBottomPanel
  // 的 ValidationLike / TestResultLike prop（它们本质是 { [key: string]: unknown }）。
  // 消除 3 条 pre-existing TS 错误（SkillStudio.vue:243-246）。
  const validationResult = ref<Record<string, unknown> | null>(null)
  const testResult = ref<Record<string, unknown> | null>(null)
  const sandboxResult = ref<Record<string, unknown> | null>(null)
  const testRunning = ref(false)
  const sandboxRunning = ref(false)
  const testHighlightPath = ref<string[]>([])
  const testFailedNodes = ref<string[]>([])

  async function handleValidate() {
    if (!deps.wb.skillId) return
    try {
      validationResult.value = await deps.skillApi.validate(deps.wb.skillId)
      deps.ui.toggleBottomPanel('validation')
    } catch (e) {
      Message.error(String((e as Record<string, unknown>)?._message || '验证失败'))
    }
  }

  async function handleRunTest() {
    if (!deps.wb.skillId) return
    testRunning.value = true
    const taskId = deps.runningTasks.start({
      kind: 'test',
      title: '测试',
      skillId: deps.wb.skillId,
      skillName: deps.studioDoc.doc.value?.meta?.name || deps.wb.skillId,
      returnPath: `/skills/${deps.wb.skillId}`,
    })
    try {
      const r = await deps.testApi.runTest(deps.wb.skillId, { run_all: true })
      testResult.value = r
      const passed = (r as Record<string, unknown>)?.passed as number | undefined
      const total = (r as Record<string, unknown>)?.total as number | undefined
      deps.runningTasks.finish(taskId, {
        status: 'success',
        message: passed != null && total != null ? `通过 ${passed}/${total}` : '测试完成',
        result: r,
      })
    } catch (e) {
      const msg = String((e as Record<string, unknown>)?._message || '测试失败')
      Message.error(msg)
      deps.runningTasks.fail(taskId, msg)
    } finally {
      testRunning.value = false
    }
  }

  async function handleRunSandbox(paramsJson: unknown) {
    if (!deps.wb.skillId) return
    sandboxRunning.value = true
    const taskId = deps.runningTasks.start({
      kind: 'sandbox',
      title: '沙箱执行',
      skillId: deps.wb.skillId,
      skillName: deps.studioDoc.doc.value?.meta?.name || deps.wb.skillId,
      returnPath: `/skills/${deps.wb.skillId}`,
    })
    try {
      let params = {}
      try { params = JSON.parse(String(paramsJson || '{}')) } catch { /* 空 */ }
      const r = await deps.executionApi.run({ skill_id: deps.wb.skillId, params, sandbox: true })
      sandboxResult.value = r
      const status = (r as Record<string, unknown>)?.status as string | undefined
      const conclusion = ((r as Record<string, unknown>)?.output as Record<string, unknown> | undefined)?.conclusion as string | undefined
      deps.runningTasks.finish(taskId, {
        status: status === 'blocked' ? 'blocked' : 'success',
        message: status === 'blocked' ? '数据质量门禁阻止' : (conclusion || '执行完成'),
        runId: (r as Record<string, unknown>)?.run_id as string | undefined,
        result: r,
      })
    } catch (e) {
      const msg = String((e as Record<string, unknown>)?._message || '执行失败')
      Message.error(msg)
      deps.runningTasks.fail(taskId, msg)
    } finally {
      sandboxRunning.value = false
    }
  }

  // v2.6: 从 Studio 内聚入 ——
  //  - restoreCachedTaskResults: 从 runningTasks dock 缓存中恢复沙箱/测试结果
  //  - handleHighlightTest: 测试失败时高亮决策链
  function restoreCachedTaskResults() {
    if (!deps.wb.skillId) return
    const sandboxCached = deps.runningTasks.getCachedResult(deps.wb.skillId, 'sandbox')
    const testCached = deps.runningTasks.getCachedResult(deps.wb.skillId, 'test')
    if (sandboxCached) sandboxResult.value = sandboxCached.result
    if (testCached) testResult.value = testCached.result
    const preferTab = (() => {
      if (sandboxCached && testCached) return sandboxCached.at >= testCached.at ? 'sandbox' : 'test'
      if (sandboxCached) return 'sandbox'
      if (testCached) return 'test'
      return ''
    })()
    if (preferTab) {
      deps.ui.bottomPanelOpen = true
      deps.ui.bottomPanelTab = preferTab
    }
  }

  function handleHighlightTest(tc: { passed?: boolean; decision_chain?: Array<{ step_id?: string; step?: string }>; decisions?: Array<{ step_id?: string; step?: string }> }) {
    const chain = tc.decision_chain || tc.decisions || []
    testHighlightPath.value = chain.map((d) => d.step_id || d.step || '')
    testFailedNodes.value = tc.passed ? [] : chain.map((d) => d.step_id || d.step || '').slice(-1)
    deps.studioDoc.setActiveModule('rules')
  }

  return {
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
  }
}
