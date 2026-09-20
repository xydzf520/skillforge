import { computed, ref, type Ref } from 'vue'
import { createPatchDraft, normalizePatch } from './workbenchShared'

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

type PatchStoreOptions = {
  activeModule: Ref<string>
  setValidationReport: (payload: Record<string, unknown>) => void
  setWorkflowPreview: (payload: Record<string, unknown>) => void
}

export function createWorkbenchPatchStore(options: PatchStoreOptions) {
  const { activeModule, setValidationReport, setWorkflowPreview } = options

  const patch = ref<unknown>(null)
  const currentPatch = ref(createPatchDraft())
  const generatingPatch = ref(false)
  const applyingPatch = ref(false)
  const patchError = ref('')

  const latestPatch = currentPatch
  const patchSummary = computed(() => ({
    id: currentPatch.value.id,
    targetModule: currentPatch.value.target_module || activeModule.value,
    summary: currentPatch.value.summary || '',
    status: currentPatch.value.status || 'draft',
    hasPatch: !!Object.keys(currentPatch.value.patch || {}).length,
  }))

  function setPatch(payload: Record<string, unknown> | null | undefined) {
    patch.value = payload
    currentPatch.value = normalizePatch(payload)
    if (payload && (payload.validation || payload.latest_validation || payload.latestValidation)) {
      const report = (payload.validation || payload.latest_validation || payload.latestValidation) as Record<string, unknown>
      setValidationReport(report)
    }
    if (payload && (payload.workflow || payload.workflow_preview || payload.workflowPreview)) {
      const wf = (payload.workflow || payload.workflow_preview || payload.workflowPreview) as Record<string, unknown>
      setWorkflowPreview(wf)
    } else if (isRecord(payload?.patch) && payload.patch.workflow) {
      setWorkflowPreview(payload.patch.workflow as Record<string, unknown>)
    }
  }

  function resetPatchState() {
    patch.value = null
    currentPatch.value = createPatchDraft()
    generatingPatch.value = false
    applyingPatch.value = false
    patchError.value = ''
  }

  return {
    patch,
    currentPatch,
    latestPatch,
    generatingPatch,
    applyingPatch,
    patchError,
    patchSummary,
    setPatch,
    resetPatchState,
  }
}
