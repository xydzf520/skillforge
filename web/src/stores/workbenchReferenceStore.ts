import { computed, ref } from 'vue'
import { normalizeReference } from './workbenchShared'

type WorkbenchReference = NonNullable<ReturnType<typeof normalizeReference>>
type ReferenceFilters = {
  query?: unknown
  sourceType?: unknown
  source_type?: unknown
  referenceMode?: unknown
  reference_mode?: unknown
}

export function createWorkbenchReferenceStore() {
  const references = ref<WorkbenchReference[]>([])
  const selectedReferenceIds = ref<string[]>([])
  const referenceQuery = ref('')
  const referenceSourceType = ref('all')
  const referenceMode = ref('all')

  const selectedReferences = computed(() => {
    const selected = new Set(selectedReferenceIds.value)
    return references.value.filter(item => selected.has(item.id))
  })

  function setReferences(list: unknown[] | null | undefined) {
    references.value = Array.isArray(list)
      ? list.map(normalizeReference).filter((item): item is WorkbenchReference => Boolean(item))
      : []
  }

  function setSelectedReferenceIds(list: unknown[] | null | undefined) {
    selectedReferenceIds.value = Array.from(new Set((Array.isArray(list) ? list : []).map(id => String(id)).filter(Boolean)))
  }

  function toggleReference(referenceId: string) {
    const id = String(referenceId || '')
    if (!id) return
    const exists = selectedReferenceIds.value.includes(id)
    selectedReferenceIds.value = exists
      ? selectedReferenceIds.value.filter(item => item !== id)
      : [...selectedReferenceIds.value, id]
  }

  function setReferenceFilters(filters: ReferenceFilters = {}) {
    if (filters.query !== undefined) referenceQuery.value = String(filters.query || '')
    if (filters.sourceType !== undefined || filters.source_type !== undefined) {
      referenceSourceType.value = String(filters.sourceType || filters.source_type || 'all')
    }
    if (filters.referenceMode !== undefined || filters.reference_mode !== undefined) {
      referenceMode.value = String(filters.referenceMode || filters.reference_mode || 'all')
    }
  }

  function resetReferenceState() {
    references.value = []
    selectedReferenceIds.value = []
    referenceQuery.value = ''
    referenceSourceType.value = 'all'
    referenceMode.value = 'all'
  }

  return {
    references,
    selectedReferenceIds,
    selectedReferences,
    referenceQuery,
    referenceSourceType,
    referenceMode,
    setReferences,
    setSelectedReferenceIds,
    toggleReference,
    setReferenceFilters,
    resetReferenceState,
  }
}
