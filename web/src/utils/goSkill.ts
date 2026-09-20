import type { Router } from 'vue-router'
import { useUserStore } from '@/stores/user'

/**
 * 大厅场景下点 Skill 的统一跳转：
 * - 工程师 / 管理员（isEngineer）→ Skill Studio，可编辑
 * - 其他角色 → Portal Skill Detail，面向业务用户的运行 / 介绍视图
 */
export function goSkill(
  router: Router,
  skillId: string,
  skill?: { permissions?: Record<string, boolean>; route_path?: string },
): void {
  if (skill?.route_path) {
    router.push(skill.route_path)
    return
  }
  const store: any = useUserStore()
  const canEdit = Boolean(skill?.permissions?.edit)
  const shouldOpenStudio = skill?.permissions ? (store.isEngineer && canEdit) : store.isEngineer
  const target = shouldOpenStudio
    ? `/skills/${skillId}`
    : `/portal/skills/${encodeURIComponent(skillId)}`
  router.push(target)
}
