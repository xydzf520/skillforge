import Dagre from '@dagrejs/dagre'

import type { PlaybookEdge, PlaybookNode } from '../types'

const NODE_WIDTH = 220
const NODE_HEIGHT = 80

export function autoLayout(nodes: PlaybookNode[], edges: PlaybookEdge[], direction = 'TB'): PlaybookNode[] {
  const graph = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
  graph.setGraph({ rankdir: direction, nodesep: 60, ranksep: 80 })

  nodes.forEach((node) => {
    graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  })

  edges.forEach((edge) => {
    graph.setEdge(edge.source, edge.target)
  })

  Dagre.layout(graph)

  return nodes.map((node) => {
    const position = graph.node(node.id)
    return {
      ...node,
      position: {
        x: position.x - NODE_WIDTH / 2,
        y: position.y - NODE_HEIGHT / 2,
      },
    }
  })
}
