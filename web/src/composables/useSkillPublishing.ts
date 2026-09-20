/**
 * v2.9.0 Skill 发布聚合 composable
 *
 * 把 Shadow + Publish + ReviewSubmit 三个 composable 用**单一 deps 入口**聚合。
 * 内部负责 deps 分发（每个子 composable 只拿它需要的字段）+ 依赖排序
 * （review 要消费 publish 的 publishReadinessReport）。
 *
 * 调用方只看到一个聚合调用，返回 `{ shadow, publish, review }`。
 */
import { useStudioShadow } from './useStudioShadow'
import { useStudioPublish } from './useStudioPublish'
import { useStudioReviewSubmit } from './useStudioReviewSubmit'
import type { PublishingMegaDeps } from '@/types/studioComposableDeps'

// v2.9.0：除了 aggregate facade 本身，也 re-export 三个原子 composable；
// 调用方可以选择"一锅端 useSkillPublishing"或"按需 useStudioShadow / useStudioPublish / useStudioReviewSubmit"
export { useStudioShadow, useStudioPublish, useStudioReviewSubmit }

export type SkillPublishingDeps = PublishingMegaDeps

export function useSkillPublishing(deps: SkillPublishingDeps) {
  const shadow = useStudioShadow({ wb: deps.wb, skillApi: deps.skillApi })
  const publish = useStudioPublish({
    wb: deps.wb,
    ui: deps.ui,
    router: deps.router,
    skillApi: deps.skillApi,
  })
  const review = useStudioReviewSubmit({
    wb: deps.wb,
    studioDoc: deps.studioDoc,
    ui: deps.ui,
    skillApi: deps.skillApi,
    reviewApi: deps.reviewApi,
    validationResult: deps.validationResult,
    publishReadinessReport: publish.publishReadinessReport,
    refreshAfterSubmitReview: deps.refreshAfterSubmitReview,
  })
  return { shadow, publish, review }
}
