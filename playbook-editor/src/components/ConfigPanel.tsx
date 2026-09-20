import { useEffect, useState } from 'react'

import type { PlaybookEdge, PlaybookEdgeData, PlaybookNode, PlaybookNodeData } from '../types'
import { parseConditionInput, serializeConditionForEditor } from '../utils/condition'

interface ConfigPanelProps {
  selectedNode: PlaybookNode | null
  selectedEdge: PlaybookEdge | null
  onUpdateNode: (nodeId: string, newData: Partial<PlaybookNodeData>) => void
  onUpdateEdge: (edgeId: string, newData: Partial<PlaybookEdgeData>) => void
  onDeleteNode: (nodeId: string) => void
  onDeleteEdge: (edgeId: string) => void
  readonly: boolean
}

interface NodeConfigProps {
  node: PlaybookNode
  onUpdate: (nodeId: string, newData: Partial<PlaybookNodeData>) => void
  onDelete: (nodeId: string) => void
}

interface EdgeConfigProps {
  edge: PlaybookEdge
  onUpdate: (edgeId: string, newData: Partial<PlaybookEdgeData>) => void
  onDelete: (edgeId: string) => void
}

export default function ConfigPanel({
  selectedNode,
  selectedEdge,
  onUpdateNode,
  onUpdateEdge,
  onDeleteNode,
  onDeleteEdge,
  readonly,
}: ConfigPanelProps) {
  if (readonly) return null

  if (!selectedNode && !selectedEdge) {
    return (
      <div className="config-panel">
        <div className="config-panel-empty">
          <div className="config-panel-empty-title">先选一个对象</div>
          <div className="config-panel-empty-copy">点中间的节点改 Skill 和参数，点连线改条件。</div>
        </div>
      </div>
    )
  }

  if (selectedEdge) {
    return <EdgeConfig edge={selectedEdge} onUpdate={onUpdateEdge} onDelete={onDeleteEdge} />
  }

  if (!selectedNode) return null
  return <NodeConfig node={selectedNode} onUpdate={onUpdateNode} onDelete={onDeleteNode} />
}

function NodeConfig({ node, onUpdate, onDelete }: NodeConfigProps) {
  const data = node.data
  const [skillId, setSkillId] = useState(data.skillId)
  const [label, setLabel] = useState(data.label)
  const [timeout, setTimeoutValue] = useState(String(data.timeout))
  const [onFailure, setOnFailure] = useState(data.onFailure)
  const [paramsText, setParamsText] = useState(JSON.stringify(data.paramsOverride || {}, null, 2))

  useEffect(() => {
    setSkillId(data.skillId)
    setLabel(data.label)
    setTimeoutValue(String(data.timeout))
    setOnFailure(data.onFailure)
    setParamsText(JSON.stringify(data.paramsOverride || {}, null, 2))
  }, [node.id, data.skillId, data.label, data.timeout, data.onFailure, data.paramsOverride])

  function handleSave() {
    let parsedParams: Record<string, unknown> = {}
    try {
      parsedParams = JSON.parse(paramsText) as Record<string, unknown>
    } catch {
      parsedParams = {}
    }

    onUpdate(node.id, {
      skillId,
      label,
      timeout: Number(timeout) || 300,
      onFailure,
      paramsOverride: parsedParams,
    })
  }

  return (
    <div className="config-panel">
      <div className="config-panel-title">节点设置</div>
      <div className="config-panel-id">ID: {data.stepId}</div>

      <label>执行什么 Skill</label>
      <input value={skillId} onChange={(event) => setSkillId(event.target.value)} placeholder="如 EC-投放-01" />
      <div className="config-hint">这里填真正执行的 Skill ID，节点名称只是给人看的。</div>

      <label>节点名称</label>
      <input value={label} onChange={(event) => setLabel(event.target.value)} />

      <label>超时时间（秒）</label>
      <input
        type="number"
        value={timeout}
        onChange={(event) => setTimeoutValue(event.target.value)}
        min={10}
        max={3600}
      />

      <label>失败后怎么处理</label>
      <select value={onFailure} onChange={(event) => setOnFailure(event.target.value)}>
        <option value="terminate">终止 (terminate)</option>
        <option value="retry">重试 (retry)</option>
        <option value="skip">跳过 (skip)</option>
      </select>

      <label>参数覆盖（JSON，可留空）</label>
      <textarea
        className="config-params-textarea"
        value={paramsText}
        onChange={(event) => setParamsText(event.target.value)}
        rows={4}
      />

      <div className="config-panel-actions">
        <button className="config-btn-save" onClick={handleSave}>
          保存节点设置
        </button>
        <button className="config-btn-delete" onClick={() => onDelete(node.id)}>
          删除节点
        </button>
      </div>
    </div>
  )
}

function EdgeConfig({ edge, onUpdate, onDelete }: EdgeConfigProps) {
  const [conditionText, setConditionText] = useState(serializeConditionForEditor(edge.data?.condition || null))

  useEffect(() => {
    setConditionText(serializeConditionForEditor(edge.data?.condition || null))
  }, [edge.id, edge.data?.condition])

  function handleSave() {
    onUpdate(edge.id, { condition: parseConditionInput(conditionText) })
  }

  return (
    <div className="config-panel">
      <div className="config-panel-title">连线设置</div>
      <div className="config-panel-id">
        {edge.source} → {edge.target}
      </div>

      <label>什么时候走这条线</label>
      <textarea
        className="config-params-textarea"
        value={conditionText}
        onChange={(event) => setConditionText(event.target.value)}
        rows={4}
        placeholder="如 steps.step_1.output.score > 80 或结构化条件 JSON"
      />
      <div className="config-hint">
        可用变量：steps.&lt;id&gt;.output.&lt;field&gt;, params.&lt;name&gt;
        <br />
        运算符：==, !=, &gt;, &lt;, &gt;=, &lt;=
      </div>

      <div className="config-panel-actions">
        <button className="config-btn-save" onClick={handleSave}>
          保存连线设置
        </button>
        <button className="config-btn-delete" onClick={() => onDelete(edge.id)}>
          删除连线
        </button>
      </div>
    </div>
  )
}
