interface ToolbarProps {
  onAutoLayout: () => void
  onUndo: () => void
  onRedo: () => void
  canUndo: boolean
  canRedo: boolean
  onFitView: () => void
  readonly: boolean
}

export default function Toolbar({
  onAutoLayout,
  onUndo,
  onRedo,
  canUndo,
  canRedo,
  onFitView,
  readonly,
}: ToolbarProps) {
  if (readonly) return null

  return (
    <div className="toolbar">
      <button onClick={onAutoLayout} title="自动布局">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="3" width="7" height="7" />
          <rect x="14" y="3" width="7" height="7" />
          <rect x="3" y="14" width="7" height="7" />
          <rect x="14" y="14" width="7" height="7" />
        </svg>
        布局
      </button>
      <button onClick={onUndo} disabled={!canUndo} title="撤销 (Ctrl+Z)">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M3 10h13a4 4 0 0 1 0 8H7" />
          <polyline points="7 6 3 10 7 14" />
        </svg>
        撤销
      </button>
      <button onClick={onRedo} disabled={!canRedo} title="重做 (Ctrl+Shift+Z)">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M21 10H8a4 4 0 0 0 0 8h10" />
          <polyline points="17 6 21 10 17 14" />
        </svg>
        重做
      </button>
      <div className="toolbar-sep" />
      <button onClick={onFitView} title="适应画布">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
        </svg>
        适应
      </button>
    </div>
  )
}
