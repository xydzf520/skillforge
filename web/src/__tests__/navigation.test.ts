import { describe, expect, it } from 'vitest'
import router from '@/router'

describe('route meta navigation contract', () => {
  it('keeps primary meta for new top-level sections', () => {
    const byName = Object.fromEntries(router.getRoutes().map(route => [route.name, route]))
    const skillListMeta = byName.SkillList?.meta as Record<string, unknown>
    const projectHostMeta = byName.ProjectHost?.meta as Record<string, unknown>
    const projectDetailMeta = byName.ProjectDetail?.meta as Record<string, unknown>
    const portalHomeMeta = byName.PortalHome?.meta as Record<string, unknown>
    const portalSkillDetailMeta = byName.PortalSkillDetail?.meta as Record<string, unknown>
    const portalSubmissionDetailMeta = byName.PortalSubmissionDetail?.meta as Record<string, unknown>
    const portalOverviewMeta = byName.PortalOverview?.meta as Record<string, unknown>
    const reviewListMeta = byName.ReviewList?.meta as Record<string, unknown>
    const executionListMeta = byName.ExecutionList?.meta as Record<string, unknown>
    const agentHomeMeta = byName.AgentHome?.meta as Record<string, unknown>
    const trainingHomeMeta = byName.TrainingHome?.meta as Record<string, unknown>
    const trainingDatasetsMeta = byName.TrainingDatasets?.meta as Record<string, unknown>
    const trainingModelsMeta = byName.TrainingModels?.meta as Record<string, unknown>
    const trainingDeploymentMeta = byName.TrainingDeploymentDetail?.meta as Record<string, unknown>
    const trainingJobMeta = byName.TrainingJobDetail?.meta as Record<string, unknown>
    const aiclawWorkspaceMeta = byName.AIClawWorkspace?.meta as Record<string, unknown>
    const knowledgeHomeMeta = byName.KnowledgeHome?.meta as Record<string, unknown>
    // v2: 原 TodoCenter → InboxCenter（primary 从 'todos' 改为 'inbox'，plan §6.3）
    const inboxCenterMeta = byName.InboxCenter?.meta as Record<string, unknown>
    const todoDetailMeta = byName.TodoDetail?.meta as Record<string, unknown>
    const todoDetailV2Meta = byName.TodoDetailV2?.meta as Record<string, unknown>
    const reportDetailMeta = byName.ReportDetail?.meta as Record<string, unknown>
    const adminOrgMeta = byName.AdminOrg?.meta as Record<string, unknown>
    const adminUserDetailMeta = byName.AdminUserDetail?.meta as Record<string, unknown>

    expect(skillListMeta.primary).toBe('workflow')
    expect(projectHostMeta.primary).toBe('projects')
    expect(projectDetailMeta.primary).toBe('projects')
    expect(portalHomeMeta.primary).toBe('projects')
    expect(portalSkillDetailMeta.primary).toBe('projects')
    expect(portalSubmissionDetailMeta.primary).toBe('projects')
    expect(portalOverviewMeta.primary).toBe('projects')
    expect(reviewListMeta.primary).toBe('workflow')
    expect(executionListMeta.primary).toBe('workflow')

    expect(agentHomeMeta.primary).toBe('workflow')
    expect(trainingHomeMeta.primary).toBe('training')
    expect(trainingDatasetsMeta.primary).toBe('training')
    expect(trainingModelsMeta.primary).toBe('training')
    expect(trainingDeploymentMeta.primary).toBe('training')
    expect(trainingJobMeta.primary).toBe('training')
    expect(aiclawWorkspaceMeta.primary).toBe('workflow')
    expect(knowledgeHomeMeta.primary).toBe('workflow')

    expect(inboxCenterMeta.primary).toBe('inbox')
    expect(todoDetailMeta.primary).toBe('inbox')
    expect(todoDetailV2Meta.primary).toBe('inbox')
    expect(reportDetailMeta.primary).toBe('inbox')
    expect(adminOrgMeta.primary).toBe('admin')
    expect(adminOrgMeta.roles).toContain('admin')
    expect(adminUserDetailMeta.primary).toBe('admin')
    expect(adminUserDetailMeta.roles).toContain('system_admin')
  })


  it('limits every admin route to system administrators', () => {
    const adminAllowed = ['admin', 'system_admin']
    const forbiddenRoles = ['dept_admin', 'biz_owner', 'ai_engineer', 'aibp', 'observer', 'operator']
    const adminRoutes = router.getRoutes().filter(route => route.path.startsWith('/admin') && route.name)

    expect(adminRoutes.length).toBeGreaterThan(0)
    for (const route of adminRoutes) {
      const roles = (route.meta.roles || []) as string[]
      expect(roles.length, `${String(route.name)} should declare admin roles`).toBeGreaterThan(0)
      expect(roles.every(role => adminAllowed.includes(role)), `${String(route.name)} has non-admin roles`).toBe(true)
      expect(roles.some(role => forbiddenRoles.includes(role)), `${String(route.name)} exposes non-admin roles`).toBe(false)
    }
  })

  it('keeps subnav meta for section highlighting', () => {
    const byName = Object.fromEntries(router.getRoutes().map(route => [route.name, route]))
    const skillListMeta = byName.SkillList?.meta as Record<string, unknown>
    const reviewListMeta = byName.ReviewList?.meta as Record<string, unknown>
    const executionListMeta = byName.ExecutionList?.meta as Record<string, unknown>
    const inboxCenterMeta = byName.InboxCenter?.meta as Record<string, unknown>
    const todoDetailV2Meta = byName.TodoDetailV2?.meta as Record<string, unknown>
    const reportDetailMeta = byName.ReportDetail?.meta as Record<string, unknown>
    const trainingHomeMeta = byName.TrainingHome?.meta as Record<string, unknown>
    const trainingDatasetsMeta = byName.TrainingDatasets?.meta as Record<string, unknown>
    const trainingModelsMeta = byName.TrainingModels?.meta as Record<string, unknown>
    const trainingDeploymentMeta = byName.TrainingDeploymentDetail?.meta as Record<string, unknown>
    const trainingJobMeta = byName.TrainingJobDetail?.meta as Record<string, unknown>

    expect(skillListMeta.subnav).toBe('skills')
    expect(reviewListMeta.subnav).toBe('skills')
    expect(executionListMeta.subnav).toBe('skills')
    expect(inboxCenterMeta.subnav).toBe('pending')
    expect(todoDetailV2Meta.subnav).toBe('pending')
    expect(reportDetailMeta.subnav).toBe('reports')
    expect(trainingHomeMeta.subnav).toBe('training-flow')
    expect(trainingDatasetsMeta.subnav).toBe('training-datasets')
    expect(trainingModelsMeta.subnav).toBe('training-models')
    expect(trainingDeploymentMeta.subnav).toBe('training-models')
    expect(trainingJobMeta.subnav).toBe('training-flow')
  })

  it('keeps legacy/hidden skill entry redirects', () => {
    const byPath = Object.fromEntries(router.getRoutes().map(route => [route.path, route]))

    expect(byPath['/skills/list']?.redirect).toBe('/skills')
    expect(byPath['/skills/templates']?.redirect).toBe('/hall')
    expect(byPath['/skills/templates/:id']?.redirect).toBe('/hall')
  })
})
