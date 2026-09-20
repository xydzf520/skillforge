/**
 * v2.9.0+：推荐从 aggregate barrel import；新 call 点优先走 `useObservability`.
 * 本 composable 仍作为内部实现保留，aggregate 复用它来承载历史/回滚逻辑。
 *   new: import { ... } from "@/composables/useObservability"
 */
/**
 * v2.4.0 Studio 版本历史 / 回滚 composable
 *
 * 从 SkillStudio.vue 抽出：
 *   - commitHistory / loadingHistory / commitDiffContent state
 *   - loadHistory / handleSelectCommit / showRollbackPreviewModal / handleRollback
 *
 * 依赖项通过 deps 传入，保持 composable 和 SkillStudio 的状态解耦。
 */
import { ref, h } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'

export type CommitRecord = { hash?: string; message?: string; date?: string; timestamp?: string }

export interface StudioCommitHistoryDeps {
  // v2.6: 细分依赖
  wb: any  // skillId 真相源
  studioDoc: any  // initForEdit 方法
  skillApi: any
  loadFileList: (force?: boolean) => Promise<void>
  loadHealthScore: () => void | Promise<void>
}

export function useStudioCommitHistory(deps: StudioCommitHistoryDeps) {
  const commitHistory = ref<CommitRecord[]>([])
  const loadingHistory = ref(false)
  const commitDiffContent = ref<{ original: string; modified: string } | null>(null)

  async function loadHistory() {
    if (!deps.wb.skillId) return
    loadingHistory.value = true
    try {
      const r = await deps.skillApi.history(deps.wb.skillId)
      commitHistory.value = Array.isArray(r) ? r : (r.commits || [])
    } catch { /* 静默 */ }
    finally { loadingHistory.value = false }
  }

  async function handleSelectCommit(hash: string) {
    if (!deps.wb.skillId || !hash) return
    // 当前 HEAD：不弹恢复 modal，只更新底部 panel 的 diff 预览
    const headHash = commitHistory.value?.[0]?.hash
    if (hash === headHash) {
      try {
        const r = await deps.skillApi.diff(deps.wb.skillId, { commit_a: hash + '~1', commit_b: hash })
        commitDiffContent.value = {
          original: r.old_content || r.before || '',
          modified: r.new_content || r.after || '',
        }
      } catch { commitDiffContent.value = null }
      return
    }

    // 旧版本 → 拉差异统计 → 弹"恢复预览"modal
    let summary: Record<string, unknown>
    try {
      summary = await deps.skillApi.diffSummary(deps.wb.skillId, hash, 'HEAD')
    } catch (e) {
      Message.error(String((e as Record<string, unknown>)?._message || '加载差异失败'))
      return
    }
    showRollbackPreviewModal(hash, summary)
  }

  function showRollbackPreviewModal(targetHash: string, summary: Record<string, unknown>) {
    const headHash = commitHistory.value?.[0]?.hash || 'HEAD'
    const files = Array.isArray(summary?.files) ? summary.files : []
    const totalFiles = (summary?.total_files as number) ?? files.length

    if (totalFiles === 0) {
      Message.info('该版本与当前 HEAD 没有差异')
      return
    }

    const commits = commitHistory.value || []
    const idx = commits.findIndex(c => c.hash === targetHash)
    const lostCommits = idx > 0 ? commits.slice(0, idx) : []

    function cleanMsg(msg: unknown) {
      let text = String(msg || '').trim()
      text = text.replace(/^(feat|fix|refactor|chore|docs|AI|ai|style|test)[\s:：]+/i, '')
      text = text.replace(/\n?Co-Authored-By:.*/s, '').trim()
      text = text.replace(/\([a-f0-9]{7,}\)/g, '')
      if (text.length > 50) text = text.slice(0, 47) + '...'
      return text || String(msg).slice(0, 40)
    }

    const lostFeatures = lostCommits.map(c => ({
      text: cleanMsg(c.message),
      date: (c.date || c.timestamp || '').slice(0, 10),
    }))

    function renderModalBody() {
      const children: any[] = []

      children.push(h('div', {
        style: 'margin-bottom:14px;padding:10px 14px;background:rgba(255,200,100,0.08);border-left:3px solid #E67700;border-radius:6px;font-size:13px;line-height:1.8',
      }, [
        h('div', null, [h('strong', null, '当前最新版'), ': ', h('code', { style: 'font-size:12px' }, String(headHash).slice(0, 8))]),
        h('div', null, [h('strong', null, '要恢复到'), ': ', h('code', { style: 'font-size:12px' }, targetHash.slice(0, 8))]),
      ]))

      if (lostFeatures.length) {
        children.push(h('div', {
          style: 'font-size:14px;font-weight:700;color:#C0392B;margin-bottom:8px;display:flex;align-items:center;gap:6px',
        }, ['⚠ 恢复后将丢失以下 ', h('b', null, `${lostFeatures.length}`), ' 个更新:']))

        const featureItems = lostFeatures.map(f =>
          h('div', {
            style: 'display:flex;align-items:center;gap:8px;padding:6px 10px;border-radius:6px;background:rgba(230,63,63,0.05);margin-bottom:3px;font-size:13px',
          }, [
            h('span', {
              style: 'width:20px;height:20px;border-radius:50%;background:rgba(230,63,63,0.15);color:#C0392B;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:12px;flex-shrink:0',
            }, '-'),
            h('span', { style: 'flex:1;color:#162033;font-weight:600' }, f.text),
            h('span', { style: 'font-size:11px;color:#999;font-family:monospace;flex-shrink:0' }, f.date),
          ])
        )
        children.push(h('div', {
          style: 'max-height:250px;overflow-y:auto;border:1px solid rgba(0,0,0,0.06);border-radius:8px;padding:6px',
        }, featureItems))
      }

      children.push(h('div', {
        style: 'margin-top:12px;padding:8px 12px;background:rgba(22,32,51,0.04);border-radius:6px;font-size:12px;color:#666;line-height:1.6',
      }, [
        h('div', null, '恢复后 Skill 状态变为草稿，需要重新审核。'),
        h('div', null, '此操作会生成一个新的版本记录，可追溯。'),
      ]))

      return h('div', null, children)
    }

    Modal.warning({
      title: `恢复到版本 ${targetHash.slice(0, 8)}?`,
      content: renderModalBody,
      okText: '确认恢复',
      cancelText: '取消',
      hideCancel: false,
      width: 640,
      onOk: async () => {
        await handleRollback(targetHash)
      },
    })
  }

  async function handleRollback(commitHash: string) {
    if (!deps.wb.skillId) return
    try {
      await deps.skillApi.rollback(deps.wb.skillId, commitHash)
      Message.success(`已回滚到 ${commitHash.slice(0, 7)}`)
      await deps.studioDoc.initForEdit(deps.wb.skillId)
      await deps.loadFileList(true)
      await loadHistory()
      try { await deps.loadHealthScore() } catch { /* 静默 */ }
    } catch (e) {
      Message.error(String((e as Record<string, unknown>)?._message || '回滚失败'))
    }
  }

  return {
    commitHistory,
    loadingHistory,
    commitDiffContent,
    loadHistory,
    handleSelectCommit,
    handleRollback,
  }
}
