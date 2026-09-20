/**
 * Dagre 自动布局引擎
 */
import dagre from '@dagrejs/dagre'

type FlowNode = {
  id: string
  type?: string
  position: { x: number; y: number }
  data?: any
}

type FlowEdge = {
  id?: string
  source: string
  target: string
  type?: string
  data?: any
}

type LayoutOptions = {
  direction?: 'LR' | 'TB'
  nodeWidth?: number
  nodeHeight?: number
  rankSep?: number
  nodeSep?: number
}

/**
 * 对 Vue Flow nodes/edges 做自动布局
 * @param {Array} nodes
 * @param {Array} edges
 * @param {Object} options - { direction: 'LR'|'TB', nodeWidth, nodeHeight, rankSep, nodeSep }
 * @returns {{ nodes: Array, edges: Array }}
 */
export function autoLayout(nodes: FlowNode[], edges: FlowEdge[], options: LayoutOptions = {}): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const {
    direction = 'LR',
    nodeWidth = 240,
    nodeHeight = 100,
    rankSep = 80,
    nodeSep = 40,
  } = options

  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: direction, ranksep: rankSep, nodesep: nodeSep })

  // 不同节点类型不同尺寸
  const sizeMap: Record<string, { w: number; h: number }> = {
    dataSource: { w: 200, h: 80 },
    decisionStep: { w: 280, h: 140 },
    conclusion: { w: 160, h: 60 },
    script: { w: 220, h: 100 },
    param: { w: 200, h: 80 },
    output: { w: 200, h: 80 },
    approval: { w: 180, h: 70 },
  }

  nodes.forEach((node) => {
    const size = sizeMap[node.type || ''] || { w: nodeWidth, h: nodeHeight }
    g.setNode(node.id, { width: size.w, height: size.h })
  })

  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target)
  })

  dagre.layout(g)

  const layoutedNodes = nodes.map((node) => {
    const pos = g.node(node.id)
    const size = sizeMap[node.type || ''] || { w: nodeWidth, h: nodeHeight }
    return {
      ...node,
      position: {
        x: pos.x - size.w / 2,
        y: pos.y - size.h / 2,
      },
    }
  })

  return { nodes: layoutedNodes, edges }
}
