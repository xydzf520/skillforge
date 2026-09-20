import { computed, ref, watch, type Ref } from 'vue'
import { Message } from '@arco-design/web-vue'
import { silentWarn } from '@/utils/errorBoundary'
import {
  getErrorMessage,
  getSkillStudioUserId,
  type ReviewContext,
  type SkillStudioReviewOptions,
} from '@/types/skillstudio'

export function useSkillStudioReview(options: SkillStudioReviewOptions) {
  const {
    route,
    router,
    wb,
    studioDoc,
    reviewApi,
    skillApi,
    userStore,
    studioMode,
    loadHistory,
  } = options

  const reviewContext = ref<ReviewContext | null>(null)
  const reviewContextLoading = ref(false)
  const reviewActionLoading = ref(false)
  const staticCheckOverrideDetail = ref<any>(null)

  function showApproveToast(result: Record<string, unknown>) {
    const syncResult = result?.sync_result as Record<string, unknown> | null | undefined
    const scheduleResult = result?.schedule_result as Record<string, unknown> | null | undefined
    const verifyResult = result?.verify_result as Record<string, unknown> | null | undefined
    const instances = Array.isArray(syncResult?.instances) ? syncResult.instances as Array<Record<string, unknown>> : []
    const failed = instances.filter(item => item?.ok === false).length
    const scheduleText = scheduleResult?.action
      ? `；定时已${scheduleResult.action === 'start' ? '启动' : '更新'}`
      : ''
    const verifyText = verifyResult?.status === 'ok'
      ? '；运行验证成功'
      : verifyResult?.status === 'failed'
        ? '；运行验证失败'
        : verifyResult?.status === 'skipped'
          ? '；运行验证已跳过'
          : ''
    if (!syncResult) {
      Message.success(`已通过审核${scheduleText}${verifyText}`)
      return
    }
    if (!instances.length) {
      Message.warning(`已通过审核，但未匹配到可同步终端${scheduleText}${verifyText}`)
      return
    }
    if (failed) {
      Message.warning(`已通过审核，自动同步 ${instances.length} 台终端，其中 ${failed} 台失败${scheduleText}${verifyText}`)
      return
    }
    Message.success(`已通过审核，并自动同步 ${instances.length} 台终端${scheduleText}${verifyText}`)
  }

  const canActCurrentReview = computed(() => {
    const review = reviewContext.value?.review
    if (!review || review.status !== 'pending') return false
    if (['admin', 'ai_engineer'].includes(userStore.role) || userStore.canViewAll) return true
    return review.reviewer === getSkillStudioUserId(userStore)
  })

  const canCommentCurrentReview = computed(() => {
    const review = reviewContext.value?.review
    if (!review) return false
    if (['admin', 'ai_engineer'].includes(userStore.role) || userStore.canViewAll) return true
    const userId = getSkillStudioUserId(userStore)
    return review.reviewer === userId || review.submitter === userId
  })

  async function loadReviewContext() {
    if (!wb.skillId || studioDoc.isCreate.value) {
      reviewContext.value = null
      return
    }
    reviewContextLoading.value = true
    try {
      const targetReviewId = route.query.review_id ? String(route.query.review_id) : ''
      let current = null
      if (targetReviewId) {
        current = await reviewApi.get(targetReviewId).catch((e: unknown) => { silentWarn(e, 'review.get'); return null })
      }
      if (!current?.id) {
        let list = await reviewApi.list({ skill_id: wb.skillId, status: 'pending', reviewer: 'me', page_size: 1 })
        current = list?.items?.[0] || null
        if (!current) {
          list = await reviewApi.list({ skill_id: wb.skillId, status: 'pending', page_size: 1 })
          current = list?.items?.[0] || null
        }
      }
      if (!current?.id) {
        reviewContext.value = null
        return
      }
      const commitA = current.git_commit_before || 'HEAD~1'
      const commitB = current.git_commit_after || 'HEAD'
      const [detail, semantic, reviewerReport] = await Promise.all([
        reviewApi.get(String(current.id)),
        reviewApi.semanticDiff(String(current.id)).catch((e: unknown) => { silentWarn(e, 'review.semanticDiff'); return null }),
        skillApi.reviewerSummarize(wb.skillId, commitA, commitB).catch((e: unknown) => { silentWarn(e, 'review.reviewerSummarize'); return null }),
      ])
      reviewContext.value = {
        review: detail,
        semantic_diff: semantic,
        ai_review: semantic?.ai_review || null,
        reviewer_report: reviewerReport,
      }
    } catch (e) {
      console.warn('[review-context] load failed', e)
      reviewContext.value = null
    } finally {
      reviewContextLoading.value = false
    }
  }

  function handleOpenReviewDetail(reviewId: number) {
    router.replace({
      path: `/skills/${wb.skillId}`,
      query: {
        ...route.query,
        perspective: 'review',
        review_id: String(reviewId),
      },
    })
  }

  async function handleApproveCurrentReview(payload: Record<string, unknown>) {
    const review = reviewContext.value?.review
    if (!review?.id) return
    reviewActionLoading.value = true
    try {
      const result = await reviewApi.approve(String(review.id), payload) as Record<string, unknown> | undefined
      staticCheckOverrideDetail.value = null
      showApproveToast(result || {})
      await loadReviewContext()
      await loadHistory()
    } catch (error: any) {
      const backendCode = error?._backendCode || error?.response?.data?.error?.code || error?.response?.data?.code
      if (backendCode === 'STATIC_CHECK_BLOCKED') {
        staticCheckOverrideDetail.value = error?.response?.data?.error?.detail || error?.response?.data?.detail || null
        Message.warning('静态检测阻断，填写 Override 理由后可再次提交')
        return
      }
      Message.error(getErrorMessage(error, '审核通过失败'))
    } finally {
      reviewActionLoading.value = false
    }
  }

  async function handleRejectCurrentReview(payload: Record<string, unknown>) {
    const review = reviewContext.value?.review
    if (!review?.id) return
    reviewActionLoading.value = true
    try {
      await reviewApi.reject(String(review.id), payload)
      Message.success('已驳回审核')
      await loadReviewContext()
    } catch (error) {
      Message.error(getErrorMessage(error, '审核驳回失败'))
    } finally {
      reviewActionLoading.value = false
    }
  }

  async function handleReviewComment(payload: Record<string, unknown>) {
    const review = reviewContext.value?.review
    if (!review?.id) return
    reviewActionLoading.value = true
    try {
      await reviewApi.comment(String(review.id), payload)
      Message.success('已添加评论')
      await loadReviewContext()
    } catch (error) {
      Message.error(getErrorMessage(error, '评论提交失败'))
    } finally {
      reviewActionLoading.value = false
    }
  }

  async function handleResolveReviewComment(commentId: number) {
    const review = reviewContext.value?.review
    if (!review?.id) return
    reviewActionLoading.value = true
    try {
      await reviewApi.resolveComment(String(review.id), commentId)
      Message.success('已标记为解决')
      await loadReviewContext()
    } catch (error) {
      Message.error(getErrorMessage(error, '评论解决失败'))
    } finally {
      reviewActionLoading.value = false
    }
  }

  async function refreshAfterSubmitReview() {
    if (studioMode.value === 'review') {
      await loadReviewContext()
    }
  }

  watch(
    () => [studioMode.value, wb.skillId, route.query.review_id],
    ([mode, skillId]) => {
      if (mode === 'review' && skillId && !studioDoc.isCreate.value) {
        loadReviewContext()
      }
    },
  )

  return {
    reviewContext,
    reviewContextLoading,
    reviewActionLoading,
    staticCheckOverrideDetail,
    canActCurrentReview,
    canCommentCurrentReview,
    loadReviewContext,
    handleOpenReviewDetail,
    handleApproveCurrentReview,
    handleRejectCurrentReview,
    handleReviewComment,
    handleResolveReviewComment,
    refreshAfterSubmitReview,
  }
}
