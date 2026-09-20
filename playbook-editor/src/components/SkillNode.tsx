import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import type { NodeProps } from '@xyflow/react'

import type { PlaybookNode } from '../types'

const STATUS_COLORS: Record<string, string> = {
  completed: '#0f8f6f',
  success: '#0f8f6f',
  running: '#165dff',
  pending: '#c8c2b3',
  waiting: '#c8c2b3',
  failed: '#bf3f3f',
  timeout: '#bf3f3f',
  skipped: '#c66a14',
}

const FAILURE_LABELS: Record<string, string> = {
  terminate: '终止',
  retry: '重试',
  skip: '跳过',
}

const STATUS_LABELS: Record<string, string> = {
  completed: '已完成',
  success: '成功',
  running: '执行中',
  pending: '待执行',
  waiting: '等待中',
  failed: '失败',
  timeout: '超时',
  skipped: '跳过',
}

function SkillNode({ data, selected }: NodeProps<PlaybookNode>) {
  const status = data.status
  const borderColor =
    data.statusColor ||
    (status ? STATUS_COLORS[status] || '#c8c2b3' : selected ? '#165dff' : '#c8c2b3')
  const isRunning = data.statusAnimation === 'pulse' || status === 'running'

  return (
    <div
      className={`skill-node ${selected ? 'skill-node-selected' : ''} ${isRunning ? 'skill-node-running' : ''}`}
      style={{
        borderColor,
        borderStyle: data.borderStyle || 'solid',
      }}
    >
      <Handle type="target" position={Position.Top} className="node-handle" />

      <div className="skill-node-header">
        <span className="skill-node-id">{data.stepId}</span>
        {status && (
          <span
            className="skill-node-status"
            style={{ background: data.statusColor || STATUS_COLORS[status] || '#c8c2b3' }}
          >
            {STATUS_LABELS[status] || status}
          </span>
        )}
      </div>

      <div className="skill-node-body">
        <div className="skill-node-skill" title={data.skillId}>
          {data.skillId || '未绑定 Skill'}
        </div>
        {data.label && data.label !== data.skillId && <div className="skill-node-label">{data.label}</div>}
      </div>

      <div className="skill-node-footer">
        <span className="skill-node-metric" title="超时">超时 {data.timeout}s</span>
        <span className="skill-node-metric" title="失败策略">
          失败 {FAILURE_LABELS[data.onFailure] || data.onFailure}
        </span>
      </div>

      <Handle type="source" position={Position.Bottom} className="node-handle" />
    </div>
  )
}

export default memo(SkillNode)
