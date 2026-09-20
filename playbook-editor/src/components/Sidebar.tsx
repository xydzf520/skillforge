import { useState } from 'react'
import type { DragEvent } from 'react'

import type { SkillSummary } from '../types'

interface SidebarProps {
  skills?: SkillSummary[]
  onAddNode: () => void
  readonly: boolean
}

export default function Sidebar({ skills = [], onAddNode, readonly }: SidebarProps) {
  const [search, setSearch] = useState('')

  if (readonly) return null

  const normalizedSearch = search.toLowerCase()
  const filtered = skills.filter((skill) => {
    return (
      skill.id.toLowerCase().includes(normalizedSearch) ||
      (skill.name || '').toLowerCase().includes(normalizedSearch)
    )
  })

  function handleDragStart(event: DragEvent<HTMLDivElement>, skill: SkillSummary) {
    event.dataTransfer.setData('application/playbook-skill', JSON.stringify(skill))
    event.dataTransfer.effectAllowed = 'move'
  }

  return (
    <div className="sidebar">
      <div className="sidebar-title">从这里拖 Skill 到画布</div>
      <div className="sidebar-subtitle">先搜索，再拖到中间；如果还没想好，也可以先加一个空节点。</div>

      <input
        className="sidebar-search"
        placeholder="搜索 Skill 名称或 ID"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />

      <div className="sidebar-list">
        {filtered.map((skill) => (
          <div
            key={skill.id}
            className="sidebar-item"
            draggable
            onDragStart={(event) => handleDragStart(event, skill)}
            title={`${skill.id} - ${skill.name || ''}`}
          >
            <div className="sidebar-item-id">{skill.id}</div>
            <div className="sidebar-item-name">{skill.name || skill.id}</div>
            {skill.department && <div className="sidebar-item-dept">{skill.department}</div>}
          </div>
        ))}
        {filtered.length === 0 && <div className="sidebar-empty">没有找到匹配的 Skill</div>}
      </div>

      <button className="sidebar-add-btn" onClick={onAddNode}>
        + 先加一个空节点
      </button>
    </div>
  )
}
