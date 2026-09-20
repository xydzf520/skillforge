/**
 * SkillForge 语义化 UI 组件
 *
 * 使用 tone-token（utils/tone.ts + styles/tone.css）统一视觉语义。
 * 配合 Arco Design Vue，作为上层抽象，隔离"状态 → 颜色"耦合。
 *
 * 用法：
 *   import { SfKpiCard, SfStatChip, SfMetricInline, SfTagChip, SfEmptyStateV2, SfModal } from '@/components/sf'
 */

export { default as SfKpiCard } from './SfKpiCard.vue'
export { default as SfStatChip } from './SfStatChip.vue'
export { default as SfMetricInline } from './SfMetricInline.vue'
// P1-1：SfPageHeader 已废弃，改为各页面内联 .page-header > .page-kicker / .page-title / .page-subtitle 模式。
// 参见 web/src/pages/skill/SkillList.vue、web/src/pages/admin/AdminUsers.vue。
// export { default as SfPageHeader } from './SfPageHeader.vue'
// P1-2：语义化 tag chip（解决 V3 角色色失控）
export { default as SfTagChip } from './SfTagChip.vue'
// P2/G4：完整版空状态（图标+标题+说明+CTA）
// 注意：名称带 V2 后缀，避免与 @/components/common/SfEmptyState 冲突
export { default as SfEmptyStateV2 } from './SfEmptyStateV2.vue'
// P2/E3：规范化的 Modal 宽度等级（sm/md/lg）
export { default as SfModal } from './SfModal.vue'
