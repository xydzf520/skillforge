import { computed, ref } from 'vue'
import {
  createValidationReport,
  createWorkflowPreview,
  normalizeValidationReport,
  normalizeWorkflowPreview,
} from './workbenchShared'

type ValidationReport = ReturnType<typeof createValidationReport>
type WorkflowPreview = ReturnType<typeof createWorkflowPreview>

export function createWorkbenchValidationStore() {
  const validation = ref<unknown>(null)
  const validationReport = ref<ValidationReport>(createValidationReport())
  const workflowPreview = ref<WorkflowPreview>(createWorkflowPreview())
  const loadingStructure = ref(false)
  const validatingPatch = ref(false)
  const validationError = ref('')
  const structureError = ref('')

  const latestValidation = validationReport
  const workflowDocument = workflowPreview
  const validationSummary = computed(() => {
    const all = [
      ...validationReport.value.structural_checks,
      ...validationReport.value.sample_case_checks,
      ...validationReport.value.historical_replay_checks,
    ]
    return {
      total: all.length,
      structural: validationReport.value.structural_checks.length,
      sample: validationReport.value.sample_case_checks.length,
      replay: validationReport.value.historical_replay_checks.length,
      errors: all.filter(item => ['error', 'fail'].includes(String(item.status || '').toLowerCase())).length,
      warnings: all.filter(item => String(item.status || '').toLowerCase() === 'warning').length,
    }
  })
  const validationStatus = computed(() => {
    if (validationError.value) return 'error'
    if (validationSummary.value.errors > 0) return 'error'
    if (validationSummary.value.warnings > 0) return 'warning'
    if (validationReport.value.can_apply === false) return 'warning'
    if (validationSummary.value.total > 0) return 'success'
    return 'idle'
  })
  const workflowSummary = computed(() => ({
    nodes: workflowPreview.value.nodes?.length ?? 0,
    edges: workflowPreview.value.edges?.length ?? 0,
    bindings: workflowPreview.value.bindings?.length ?? 0,
  }))

  function setValidationReport(payload: unknown) {
    validation.value = payload
    validationReport.value = normalizeValidationReport(payload)
  }

  function setWorkflowPreview(payload: unknown) {
    workflowPreview.value = normalizeWorkflowPreview(payload)
  }

  function resetValidationState() {
    validation.value = null
    validationReport.value = createValidationReport()
    workflowPreview.value = createWorkflowPreview()
    loadingStructure.value = false
    validatingPatch.value = false
    validationError.value = ''
    structureError.value = ''
  }

  return {
    validation,
    validationReport,
    workflowPreview,
    workflowDocument,
    latestValidation,
    loadingStructure,
    validatingPatch,
    validationError,
    structureError,
    validationSummary,
    validationStatus,
    workflowSummary,
    setValidationReport,
    setWorkflowPreview,
    resetValidationState,
  }
}
