export interface TodoDispatchTaskSpec {
  executor?: string | null
  content: string
  deadline?: string | null
  extra?: Record<string, unknown> | null
}

export interface TodoReviewSpec {
  kind: 'review'
  title: string
  summary?: string
  payload?: Record<string, unknown>
  decision_mode?: 'any_of' | 'all_of' | 'independent'
  sla_hours?: number
  reviewers?: string[]
  reviewer_role?: string
  callback?: Record<string, unknown>
}

export interface TodoDispatchSpec {
  kind: 'dispatch'
  title: string
  summary?: string
  payload?: Record<string, unknown>
  decision_mode?: 'any_of' | 'all_of' | 'independent'
  sla_hours?: number
  reviewers?: string[]
  reviewer_role?: string
  tasks: TodoDispatchTaskSpec[]
  callback?: Record<string, unknown>
}
