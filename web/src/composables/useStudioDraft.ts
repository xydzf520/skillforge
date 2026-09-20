/**
 * Studio 草稿持久化（N3）：localStorage + 按登录用户 scope + 30 天 TTL。
 *
 * 为 O13 Studio 拆分的第一步独立单元：把散在 SkillStudio.vue 里的 getDraftKey /
 * saveDraft / restoreDraft / clearDraft 4 个函数抽出来，让 Studio 主文件下降 ~50 行。
 *
 * 使用方式（在 SkillStudio.vue setup 里）：
 *   const draft = useStudioDraft({
 *     ide,
 *     userStore,
 *     getSnapshot: () => ({ ... 当前编辑态快照 ... }),
 *     applySnapshot: (s) => { ... 恢复 ... },
 *   })
 *   onMounted(() => draft.restore())
 *   onBeforeUnmount(() => draft.save())
 *   // 30s 定时自动保存
 *   setInterval(draft.save, 30000)
 *
 * 注意：本轮只提供 composable，SkillStudio.vue 内部实现仍保留（避免改大文件）；
 * 下个 milestone 把 Studio 的 4 个函数替换成调用本 composable。
 */
import { onBeforeUnmount, onMounted, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { Modal, Message } from '@arco-design/web-vue'

const DRAFT_TTL_MS = 30 * 24 * 3600 * 1000

export interface StudioDraftDeps {
  // v2.6: ide facade 已消失，改为直接依赖 workbench store（skillId 真相源）
  wb: { skillId?: string | null } | any
  userStore: { userInfo?: { user_id?: string; username?: string } | null } | any
  getSnapshot: () => Record<string, unknown>
  applySnapshot: (snapshot: Record<string, unknown>) => void
  /** 判断是否有未保存改动；true 才 save */
  isDirty: () => boolean
  /** v2.6: 自动保存 & 离开路由守护；默认开启。传 false 可纯手动模式 */
  autoSave?: boolean
  autoSaveIntervalMs?: number
  /** 变 false 时自动 clear 草稿 */
  dirtyRef?: { value: boolean } | any
}

export function useStudioDraft(deps: StudioDraftDeps) {
  function key(): string {
    const uid = deps.userStore.userInfo?.user_id || deps.userStore.userInfo?.username || 'anon'
    return `sf-wb-draft:${uid}:${deps.wb.skillId || 'new'}`
  }

  function save(): void {
    if (!deps.isDirty()) return
    try {
      const snapshot = {
        skillId: deps.wb.skillId,
        ...deps.getSnapshot(),
        timestamp: Date.now(),
      }
      localStorage.setItem(key(), JSON.stringify(snapshot))
    } catch { /* quota / disabled */ }
  }

  function restore(): void {
    try {
      const raw = localStorage.getItem(key())
      if (!raw) return
      const snapshot = JSON.parse(raw) as Record<string, unknown>
      const ts = Number(snapshot.timestamp) || 0
      const matchesSkill = snapshot.skillId === deps.wb.skillId
      const withinTtl = Date.now() - ts < DRAFT_TTL_MS
      if (!matchesSkill || !withinTtl) {
        localStorage.removeItem(key())
        return
      }
      Modal.confirm({
        title: '恢复未保存的修改？',
        content: '检测到上次退出时有未保存的改动，是否恢复？',
        okText: '恢复',
        cancelText: '丢弃',
        onOk: () => {
          deps.applySnapshot(snapshot)
          Message.success('已恢复未保存的修改')
          try { localStorage.removeItem(key()) } catch {}
        },
        onCancel: () => { try { localStorage.removeItem(key()) } catch {} },
      })
    } catch { /* 解析失败 */ }
  }

  function clear(): void {
    try { localStorage.removeItem(key()) } catch {}
  }

  // v2.6: autosave + route-leave guard 一并托管（原 Studio 内 ~25 行搬来）
  if (deps.autoSave !== false) {
    const intervalMs = deps.autoSaveIntervalMs ?? 30000
    let timer: ReturnType<typeof setInterval> | null = null
    onMounted(() => { timer = setInterval(save, intervalMs) })
    onBeforeUnmount(() => {
      if (timer) clearInterval(timer)
      save()
    })
    // dirty=false 时清理草稿（保存成功后 Studio 的 studioDirty 会变 false）
    if (deps.dirtyRef) {
      watch(deps.dirtyRef, (dirty) => { if (!dirty) clear() })
    }
    // 离开路由时提示未保存
    onBeforeRouteLeave((_to, _from, next) => {
      if (!deps.isDirty()) return next()
      Modal.confirm({
        title: '放弃未保存的修改？',
        content: '你有未保存的改动，离开当前页面会丢失这些改动。',
        okText: '放弃并离开',
        cancelText: '继续编辑',
        okButtonProps: { status: 'danger' },
        onOk: () => next(),
        onCancel: () => next(false),
      })
    })
  }

  return { save, restore, clear, key }
}
