import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { DragEvent as ReactDragEvent } from 'react'
import {
  addEdge,
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from '@xyflow/react'
import type { Connection } from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import ConditionEdge from './components/ConditionEdge'
import ConfigPanel from './components/ConfigPanel'
import Sidebar from './components/Sidebar'
import SkillNode from './components/SkillNode'
import Toolbar from './components/Toolbar'
import type {
  HistoryState,
  LiveNodeStyle,
  PlaybookDocument,
  PlaybookEdge,
  PlaybookEdgeData,
  PlaybookNode,
  PlaybookNodeData,
  SkillSummary,
  StepStatus,
} from './types'
import { getConditionLabel } from './utils/condition'
import { onParentMessage, sendToParent } from './utils/communication'
import { flowToSteps, stepsToFlow } from './utils/converter'
import { autoLayout } from './utils/layout'

const nodeTypes = { skill: SkillNode }
const edgeTypes = { condition: ConditionEdge }

const defaultEdgeOptions = {
  type: 'default',
  markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: '#97a1b2' },
  style: { strokeWidth: 1.8, stroke: '#97a1b2' },
}

function getUrlParams() {
  const params = new URLSearchParams(window.location.search)
  return {
    name: params.get('name') || '',
    readonly: params.get('readonly') === '1',
    live: params.get('live') === '1',
  }
}

function EditorCanvas() {
  const urlParams = useMemo(() => getUrlParams(), [])
  const mode = urlParams.live ? 'live' : urlParams.readonly ? 'readonly' : 'edit'
  const isReadonly = mode !== 'edit'

  const [nodes, setNodes, onNodesChange] = useNodesState<PlaybookNode>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<PlaybookEdge>([])
  const [skills, setSkills] = useState<SkillSummary[]>([])
  const [selectedNode, setSelectedNode] = useState<PlaybookNode | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<PlaybookEdge | null>(null)

  const historyRef = useRef<HistoryState[]>([])
  const historyIdxRef = useRef(-1)
  const skipHistoryRef = useRef(false)
  const notifyChangeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const nodeIdCounter = useRef(1)
  const nodesRef = useRef<PlaybookNode[]>([])
  const edgesRef = useRef<PlaybookEdge[]>([])

  const reactFlowInstance = useReactFlow<PlaybookNode, PlaybookEdge>()

  function loadPlaybookData(data: PlaybookDocument) {
    const { nodes: nextNodes, edges: nextEdges } = stepsToFlow(data.steps || [], data._canvas_layout || {})

    let maxNum = 0
    nextNodes.forEach((node) => {
      const match = node.id.match(/^step_(\d+)$/)
      if (match) maxNum = Math.max(maxNum, Number.parseInt(match[1], 10))
    })

    nodeIdCounter.current = maxNum + 1
    skipHistoryRef.current = true
    setNodes(nextNodes)
    setEdges(nextEdges)
    historyRef.current = [{ nodes: nextNodes, edges: nextEdges }]
    historyIdxRef.current = 0
    // 两层 rAF 等待 React commit + ReactFlow 测量 node 宽高 + 浏览器 paint,
    // 比 setTimeout(80) 更可靠 (小 iframe / 弱硬件下 80ms 不够, fitView 会 no-op)
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        reactFlowInstance.fitView({ padding: isReadonly ? 0.24 : 0.2, duration: 300 })
      })
    })
  }

  function pushHistory(nextNodes: PlaybookNode[], nextEdges: PlaybookEdge[]) {
    const index = historyIdxRef.current
    const history = historyRef.current.slice(0, index + 1)
    history.push({ nodes: nextNodes, edges: nextEdges })
    if (history.length > 50) history.shift()
    historyRef.current = history
    historyIdxRef.current = history.length - 1
  }

  function notifyChange(nextNodes: PlaybookNode[], nextEdges: PlaybookEdge[]) {
    if (notifyChangeTimer.current) clearTimeout(notifyChangeTimer.current)

    notifyChangeTimer.current = setTimeout(() => {
      sendToParent('playbook-changed', flowToSteps(nextNodes, nextEdges))
    }, 300)
  }

  function applyStatusUpdate(payload: Record<string, StepStatus>) {
    setNodes((currentNodes) =>
      currentNodes.map((node) => {
        const status = payload[node.id]
        if (status === undefined) return node

        return {
          ...node,
          data: {
            ...node.data,
            status,
            statusColor: undefined,
            statusAnimation: undefined,
            borderStyle: undefined,
          },
        }
      }),
    )
  }

  function applyNodeStyleUpdate(payload: Record<string, LiveNodeStyle>) {
    setNodes((currentNodes) =>
      currentNodes.map((node) => {
        const styleInfo = payload[node.id]
        if (!styleInfo) return node

        return {
          ...node,
          data: {
            ...node.data,
            status: styleInfo.status,
            statusColor: styleInfo.color,
            statusAnimation: styleInfo.animation,
            borderStyle: styleInfo.borderStyle,
          },
        }
      }),
    )
  }

  function handleUpdateNode(nodeId: string, newData: Partial<PlaybookNodeData>) {
    setNodes((currentNodes) =>
      currentNodes.map((node) =>
        node.id === nodeId ? { ...node, data: { ...node.data, ...newData } } : node,
      ),
    )
    setSelectedNode((previousNode) =>
      previousNode && previousNode.id === nodeId
        ? { ...previousNode, data: { ...previousNode.data, ...newData } }
        : previousNode,
    )
  }

  function handleUpdateEdge(edgeId: string, newData: Partial<PlaybookEdgeData>) {
    setEdges((currentEdges) =>
      currentEdges.map((edge) => {
        if (edge.id !== edgeId) return edge

        const condition = newData.condition ?? edge.data?.condition ?? ''
        return {
          ...edge,
          type: condition ? 'condition' : 'default',
          label: getConditionLabel(condition),
          data: { ...edge.data, ...newData },
        }
      }),
    )
    setSelectedEdge((previousEdge) =>
      previousEdge && previousEdge.id === edgeId
        ? { ...previousEdge, data: { ...previousEdge.data, ...newData } }
        : previousEdge,
    )
  }

  function focusElement(payload: { nodeId?: string | null; edgeId?: string | null } | null | undefined) {
    const nodeId = payload?.nodeId || ''
    const edgeId = payload?.edgeId || ''

    // 读 reactFlowInstance.getNodes/getEdges —— 始终拿最新, 避免父页 load-playbook
    // 和 focus-element 在同一 message tick 连发时 nodesRef 尚未刷新的时序 bug
    const currentNodes = reactFlowInstance.getNodes()
    const currentEdges = reactFlowInstance.getEdges()

    if (nodeId) {
      const targetNode = currentNodes.find((node) => node.id === nodeId)
      if (!targetNode) return
      setSelectedNode(targetNode)
      setSelectedEdge(null)
      requestAnimationFrame(() => {
        reactFlowInstance.setCenter(targetNode.position.x + 120, targetNode.position.y + 40, {
          zoom: Math.max(reactFlowInstance.getZoom(), 1),
          duration: 260,
        })
      })
      return
    }

    if (edgeId) {
      const targetEdge = currentEdges.find((edge) => edge.id === edgeId)
      if (!targetEdge) return
      setSelectedEdge(targetEdge)
      setSelectedNode(null)
      const sourceNode = currentNodes.find((node) => node.id === targetEdge.source)
      if (sourceNode) {
        requestAnimationFrame(() => {
          reactFlowInstance.setCenter(sourceNode.position.x + 120, sourceNode.position.y + 40, {
            zoom: Math.max(reactFlowInstance.getZoom(), 1),
            duration: 260,
          })
        })
      }
      return
    }

    setSelectedNode(null)
    setSelectedEdge(null)
  }

  useEffect(() => {
    if (!urlParams.name) return

    fetch(`/api/playbooks/${urlParams.name}`, { credentials: 'include' })
      .then((response) => {
        if (response.ok) return response.json() as Promise<PlaybookDocument>
        throw new Error('加载失败')
      })
      .then((data) => {
        loadPlaybookData(data)
        fetch('/api/skills/', { credentials: 'include' })
          .then((response) => (response.ok ? (response.json() as Promise<SkillSummary[]>) : []))
          .then((list) => setSkills(Array.isArray(list) ? list : []))
          .catch(() => {})
      })
      .catch(() => {})
  }, [urlParams.name])

  useEffect(() => {
    nodesRef.current = nodes
  }, [nodes])

  useEffect(() => {
    edgesRef.current = edges
  }, [edges])

  useEffect(() => {
    const cleanups = [
      onParentMessage('load-playbook', (payload) => {
        loadPlaybookData(payload)
      }),
      onParentMessage('set-skills', (payload) => {
        if (Array.isArray(payload)) setSkills(payload)
      }),
      onParentMessage('update-status', (payload) => {
        applyStatusUpdate(payload)
      }),
      onParentMessage('update-node-styles', (payload) => {
        applyNodeStyleUpdate(payload)
      }),
      onParentMessage('update-condition', (payload) => {
        if (payload.edgeId) {
          handleUpdateEdge(payload.edgeId, { condition: payload.condition || '' })
        }
      }),
      onParentMessage('focus-element', (payload) => {
        focusElement(payload)
      }),
    ]

    sendToParent('editor-ready', {})
    return () => {
      cleanups.forEach((cleanup) => cleanup())
    }
  }, [])

  useEffect(() => {
    if (skipHistoryRef.current) {
      skipHistoryRef.current = false
      return
    }

    if (!isReadonly) {
      pushHistory(nodes, edges)
      notifyChange(nodes, edges)
    }
  }, [edges, isReadonly, nodes])

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (isReadonly) return
      if ((event.ctrlKey || event.metaKey) && event.key === 'z') {
        event.preventDefault()
        if (event.shiftKey) handleRedo()
        else handleUndo()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isReadonly])

  useEffect(() => {
    return () => {
      if (notifyChangeTimer.current) clearTimeout(notifyChangeTimer.current)
    }
  }, [])

  function handleUndo() {
    if (historyIdxRef.current <= 0) return
    historyIdxRef.current -= 1
    const state = historyRef.current[historyIdxRef.current]
    skipHistoryRef.current = true
    setNodes(state.nodes)
    setEdges(state.edges)
    notifyChange(state.nodes, state.edges)
  }

  function handleRedo() {
    if (historyIdxRef.current >= historyRef.current.length - 1) return
    historyIdxRef.current += 1
    const state = historyRef.current[historyIdxRef.current]
    skipHistoryRef.current = true
    setNodes(state.nodes)
    setEdges(state.edges)
    notifyChange(state.nodes, state.edges)
  }

  const onConnect = useCallback(
    (params: Connection) => {
      if (isReadonly) return

      setEdges((currentEdges) =>
        addEdge<PlaybookEdge>(
          {
            ...params,
            type: 'default',
            label: '',
            data: { condition: '' },
            markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: '#97a1b2' },
          },
          currentEdges,
        ),
      )
    },
    [isReadonly, setEdges],
  )

  const onDragOver = useCallback((event: ReactDragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (event: ReactDragEvent<HTMLDivElement>) => {
      if (isReadonly) return

      event.preventDefault()
      const rawSkill = event.dataTransfer.getData('application/playbook-skill')
      if (!rawSkill) return

      try {
        const skill = JSON.parse(rawSkill) as SkillSummary
        const position = reactFlowInstance.screenToFlowPosition({
          x: event.clientX,
          y: event.clientY,
        })
        const newId = `step_${nodeIdCounter.current++}`

        const newNode: PlaybookNode = {
          id: newId,
          type: 'skill',
          position,
          data: {
            stepId: newId,
            skillId: skill.id,
            label: skill.name || skill.id,
            timeout: 300,
            onFailure: 'terminate',
            paramsOverride: {},
            status: null,
          },
        }

        setNodes((currentNodes) => [...currentNodes, newNode])
      } catch {
        return
      }
    },
    [isReadonly, reactFlowInstance, setNodes],
  )

  function handleAddNode() {
    const newId = `step_${nodeIdCounter.current++}`
    const newNode: PlaybookNode = {
      id: newId,
      type: 'skill',
      position: { x: 200, y: nodes.length * 120 + 50 },
      data: {
        stepId: newId,
        skillId: '',
        label: newId,
        timeout: 300,
        onFailure: 'terminate',
        paramsOverride: {},
        status: null,
      },
    }
    setNodes((currentNodes) => [...currentNodes, newNode])
  }

  const onNodeClick = useCallback((_: unknown, node: PlaybookNode) => {
    setSelectedNode(node)
    setSelectedEdge(null)
    sendToParent('selection-changed', { nodeId: node.id, edgeId: null })
  }, [])

  const onEdgeClick = useCallback((_: unknown, edge: PlaybookEdge) => {
    setSelectedEdge(edge)
    setSelectedNode(null)
    sendToParent('selection-changed', { nodeId: null, edgeId: edge.id })
  }, [])

  const onPaneClick = useCallback(() => {
    // 只在"之前存在选中"时才 emit; 否则 ReactFlow 初始化/无选中状态下
    // pane-click 也会把父页记录的 selection 冲掉
    if (selectedNode === null && selectedEdge === null) return
    setSelectedNode(null)
    setSelectedEdge(null)
    sendToParent('selection-changed', { nodeId: null, edgeId: null })
  }, [selectedNode, selectedEdge])

  function handleDeleteNode(nodeId: string) {
    setNodes((currentNodes) => currentNodes.filter((node) => node.id !== nodeId))
    setEdges((currentEdges) =>
      currentEdges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
    )
    setSelectedNode(null)
  }

  function handleDeleteEdge(edgeId: string) {
    setEdges((currentEdges) => currentEdges.filter((edge) => edge.id !== edgeId))
    setSelectedEdge(null)
  }

  function handleAutoLayout() {
    const laidOutNodes = autoLayout(nodes, edges)
    setNodes(laidOutNodes)
    setTimeout(() => reactFlowInstance.fitView({ padding: 0.2 }), 50)
  }

  function handleFitView() {
    reactFlowInstance.fitView({ padding: 0.2 })
  }

  const miniMapNodeColor = useCallback((node: PlaybookNode) => {
    if (node.data.statusColor) return node.data.statusColor
    const status = node.data.status
    if (status === 'completed' || status === 'success') return '#0f8f6f'
    if (status === 'running') return '#165dff'
    if (status === 'failed' || status === 'timeout') return '#bf3f3f'
    if (status === 'skipped') return '#c66a14'
    return '#d8d1c1'
  }, [])

  const canUndo = historyIdxRef.current > 0
  const canRedo = historyIdxRef.current < historyRef.current.length - 1
  const modeLabel = mode === 'live' ? '实时执行' : mode === 'readonly' ? '只读预览' : '流程编辑'

  return (
    <div className={`editor-root mode-${mode}`}>
      {!isReadonly && <Sidebar skills={skills} onAddNode={handleAddNode} readonly={isReadonly} />}

      <div className="editor-canvas-wrapper">
        {isReadonly ? (
          <div className="canvas-shell-header">
            <div className="canvas-shell-copy">
              <div className="canvas-shell-kicker">{mode === 'live' ? '执行监控' : '流程预览'}</div>
              <div className="canvas-shell-title">{urlParams.name || '未命名编排'}</div>
            </div>
            <div className="canvas-shell-badges">
              <span className={`canvas-shell-badge is-${mode}`}>{modeLabel}</span>
              <span className="canvas-shell-badge">{nodes.length} 节点</span>
              <span className="canvas-shell-badge">{edges.length} 连线</span>
            </div>
          </div>
        ) : (
          <>
            <div className="editor-edit-guide">
              <div className="editor-edit-guide-title">编辑顺序</div>
              <div className="editor-edit-guide-steps">
                <span className="editor-edit-guide-step">
                  <strong>1</strong>
                  左侧拖 Skill
                </span>
                <span className="editor-edit-guide-step">
                  <strong>2</strong>
                  中间连线排顺序
                </span>
                <span className="editor-edit-guide-step">
                  <strong>3</strong>
                  点节点后在右侧改设置
                </span>
              </div>
            </div>
            <Toolbar
              onAutoLayout={handleAutoLayout}
              onUndo={handleUndo}
              onRedo={handleRedo}
              canUndo={canUndo}
              canRedo={canRedo}
              onFitView={handleFitView}
              readonly={isReadonly}
            />
          </>
        )}

        <div className="editor-flow-stage">
          <ReactFlow<PlaybookNode, PlaybookEdge>
            className={`editor-flow editor-flow-${mode}`}
            nodes={nodes}
            edges={edges}
            onNodesChange={isReadonly ? undefined : onNodesChange}
            onEdgesChange={isReadonly ? undefined : onEdgesChange}
            onConnect={onConnect}
            onDrop={onDrop}
            onDragOver={onDragOver}
            onNodeClick={onNodeClick}
            onEdgeClick={onEdgeClick}
            onPaneClick={onPaneClick}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            defaultEdgeOptions={defaultEdgeOptions}
            nodesDraggable={!isReadonly}
            nodesConnectable={!isReadonly}
            elementsSelectable
            fitView
            fitViewOptions={{ padding: isReadonly ? 0.24 : 0.2 }}
            minZoom={0.2}
            maxZoom={3}
            deleteKeyCode={isReadonly ? null : ['Delete']}
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={22}
              size={1.2}
              color={mode === 'edit' ? '#d8ccb3' : '#d9c7a7'}
            />
            <Controls showInteractive={false} position="bottom-right" />
            <MiniMap nodeColor={miniMapNodeColor} pannable zoomable />
          </ReactFlow>
        </div>
      </div>

      {!isReadonly && (
        <ConfigPanel
          selectedNode={selectedNode}
          selectedEdge={selectedEdge}
          onUpdateNode={handleUpdateNode}
          onUpdateEdge={handleUpdateEdge}
          onDeleteNode={handleDeleteNode}
          onDeleteEdge={handleDeleteEdge}
          readonly={isReadonly}
        />
      )}
    </div>
  )
}

export default function App() {
  return (
    <ReactFlowProvider>
      <EditorCanvas />
    </ReactFlowProvider>
  )
}
