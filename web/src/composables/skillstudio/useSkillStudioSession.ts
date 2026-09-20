import { computed, ref } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import type { LocationQueryRaw, LocationQueryValueRaw } from 'vue-router'
import {
  getErrorMessage,
  getSkillStudioUserId,
  type SkillStudioSessionOptions,
} from '@/types/skillstudio'
import { silentWarn } from '@/utils/errorBoundary'

export function useSkillStudioSession(options: SkillStudioSessionOptions) {
  const { route, router, wb, studioDoc, documentStore, ui, userStore, skillApi } = options

  const chatStarted = ref(false)
  const lockHeld = ref(false)
  const lockError = ref('')
  const editMode = ref(false)
  const lockOwner = ref('')

  const blueprintGuideMode = computed(() =>
    studioDoc.isCreate.value && !studioDoc.hasContent.value && !chatStarted.value,
  )
  const canEditPerspective = computed(() =>
    studioDoc.isCreate.value || !['biz_owner', 'director'].includes(userStore.role),
  )
  const availablePerspectives = computed(() => (
    studioDoc.isCreate.value
      ? ['edit']
      : (canEditPerspective.value ? ['edit', 'explain', 'review'] : ['explain', 'review'])
  ))
  const studioMode = computed(() => {
    if (studioDoc.isCreate.value) return 'create'
    return ui.studioPerspective
  })

  function isWritablePerspective() {
    return studioMode.value === 'edit' || studioMode.value === 'create'
  }

  function normalizeStudioPerspective(value: string) {
    if (value === 'explain' || value === 'review') return value
    return 'edit'
  }

  function applyStudioPerspective(value: string, options: { syncRoute?: boolean } = {}) {
    const syncRoute = options.syncRoute !== false
    let next = normalizeStudioPerspective(value)
    if (studioDoc.isCreate.value) next = 'edit'
    if (!canEditPerspective.value && next === 'edit') next = 'review'
    if (next !== 'edit' && editMode.value && documentStore.dirty) {
      Message.warning('请先保存或取消编辑，再切换到讲解/审批视图')
      return
    }
    if (next !== 'edit' && lockHeld.value) {
      releaseLock()
    }
    ui.setStudioPerspective(next)
    if (syncRoute) {
      const nextQuery: LocationQueryRaw = { ...route.query }
      if (next === 'edit' || studioDoc.isCreate.value) delete nextQuery.perspective
      else nextQuery.perspective = next as LocationQueryValueRaw
      router.replace({ query: nextQuery }).catch((e: unknown) => silentWarn(e, 'studio.perspective_query_sync'))
    }
  }

  async function checkLockStatus() {
    if (!wb.skillId || studioDoc.isCreate.value) {
      lockOwner.value = ''
      return
    }
    try {
      const response = await skillApi.lockStatus(wb.skillId)
      const heldBy = response?.locked_by
      const myId = getSkillStudioUserId(userStore)
      if (heldBy && heldBy !== myId) {
        lockOwner.value = heldBy
      } else {
        lockOwner.value = ''
      }
    } catch {
      lockOwner.value = ''
    }
  }

  async function acquireLock() {
    if (!wb.skillId) return
    if (!canEditPerspective.value || studioMode.value !== 'edit') {
      Message.warning(studioMode.value === 'explain' ? '讲解模式不支持进入编辑' : '审批模式不支持进入编辑')
      return
    }
    try {
      await skillApi.acquireLock(wb.skillId)
      lockHeld.value = true
      editMode.value = true
      lockError.value = ''
      lockOwner.value = ''
    } catch (error) {
      lockHeld.value = false
      editMode.value = false
      const message = getErrorMessage(error, '')
      const apiError = error as { status?: number; detail?: { locked_by?: string } | string }
      if (message.includes('locked') || apiError.status === 423) {
        lockError.value = message || '该 Skill 正被其他人编辑'
        Message.warning(lockError.value)
        const lockedBy = typeof apiError.detail === 'object' ? apiError.detail?.locked_by : ''
        if (lockedBy) {
          lockOwner.value = lockedBy
        } else {
          checkLockStatus()
        }
      }
    }
  }

  async function releaseLock() {
    if (!wb.skillId || !lockHeld.value) return
    try {
      await skillApi.releaseLock(wb.skillId)
    } catch {
      // ignore release failures
    }
    lockHeld.value = false
    editMode.value = false
    checkLockStatus()
  }

  function handleCancelEdit() {
    if (!documentStore.dirty) {
      releaseLock()
      return
    }
    Modal.confirm({
      title: '放弃未保存的修改？',
      content: '你有未保存的改动，退出编辑模式会丢失这些改动。',
      okText: '放弃修改',
      cancelText: '继续编辑',
      okButtonProps: { status: 'danger' },
      onOk: () => releaseLock(),
    })
  }

  return {
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
  }
}
