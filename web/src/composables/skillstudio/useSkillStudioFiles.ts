import { ref } from 'vue'
import { Message, Modal } from '@arco-design/web-vue'
import type {
  StudioWbBridge,
  StudioDocBridge,
  SkillStudioUiBridge,
  SkillStudioSkillApi,
  ApiErrorLike,
} from '@/types/skillstudio'

type FilesComposableOptions = {
  // v2.6: 细分依赖
  wb: StudioWbBridge
  studioDoc: StudioDocBridge
  ui: SkillStudioUiBridge
  skillApi: SkillStudioSkillApi
}

export function useSkillStudioFiles(options: FilesComposableOptions) {
  const { wb, studioDoc, ui, skillApi } = options

  const fileList = ref<FlatFileItem[]>([])
  const loadingFiles = ref(false)
  const activeFile = ref('')
  const activeFileContent = ref<string | null>(null)
  const activeFileOriginalContent = ref('')
  const activeFileReadOnly = ref(false)
  const activeFileDirty = ref(false)
  const activeFileLanguage = ref('skill-md')

  async function loadFileList(force = false) {
    if (!wb.skillId) return
    if (fileList.value.length && !force) return
    loadingFiles.value = true
    try {
      const data = await skillApi.get(wb.skillId)
      const tree = data.file_tree || []
      fileList.value = flattenTree(tree)
      if (!activeFile.value && fileList.value.length) {
        activeFile.value = 'SKILL.md'
      }
    } catch (error) {
      if (force && fileList.value.length) {
        console.warn('[files] refresh file list failed, keep existing tree:', error)
      } else {
        fileList.value = []
      }
    } finally {
      loadingFiles.value = false
    }
  }

  function resetFileEditor() {
    activeFile.value = 'SKILL.md'
    activeFileContent.value = null
    activeFileOriginalContent.value = ''
    activeFileDirty.value = false
    activeFileReadOnly.value = false
    activeFileLanguage.value = 'skill-md'
  }

  function confirmDiscardFileChanges(action: () => void | Promise<void>) {
    if (!activeFileDirty.value) {
      action()
      return
    }
    Modal.confirm({
      title: '放弃当前文件的未保存修改？',
      content: activeFile.value || '当前文件',
      okText: '放弃修改',
      cancelText: '继续编辑',
      okButtonProps: { status: 'danger' },
      onOk: action,
    })
  }

  function handleSelectModule(moduleKey: string) {
    confirmDiscardFileChanges(() => {
      studioDoc.setActiveModule(moduleKey)
      ui.setViewMode?.('block')
      resetFileEditor()
    })
  }

  async function handleSelectFile(path: string) {
    confirmDiscardFileChanges(async () => {
      activeFile.value = path
      ui.setViewMode?.('code')
      if (!wb.skillId) return

      try {
        const response = await skillApi.readFile(wb.skillId, path)
        const content = typeof response === 'string' ? response : (response?.content ?? '')
        activeFileContent.value = content
        activeFileOriginalContent.value = content
        activeFileDirty.value = false
        activeFileReadOnly.value = false
        activeFileLanguage.value = detectLanguage(path)
      } catch (error: unknown) {
        const err = error as ApiErrorLike | undefined
        Message.error(err?._message || `无法读取文件: ${path}`)
      }
    })
  }

  function handleMonacoChange(value: string) {
    if (activeFileContent.value === null) return
    activeFileContent.value = value
    activeFileDirty.value = value !== activeFileOriginalContent.value
  }

  async function handleCreateFile(isDir = false) {
    if (!wb.skillId) return
    const placeholder = isDir ? 'references/new-folder' : 'references/new-file.md'
    const path = window.prompt(isDir ? '输入新目录路径' : '输入新文件路径', placeholder)
    if (!path?.trim()) return
    try {
      await skillApi.createFile(wb.skillId, { path: path.trim(), content: '', is_dir: isDir })
      await loadFileList(true)
      Message.success(isDir ? '目录已创建' : '文件已创建')
      if (!isDir) await handleSelectFile(path.trim())
    } catch (error: unknown) {
      const err = error as ApiErrorLike | undefined
      Message.error(err?._message || '创建失败')
    }
  }

  async function handleRenameFile(path: string) {
    if (!wb.skillId || !path) return
    const newPath = window.prompt('输入新的文件路径', path)
    if (!newPath?.trim() || newPath === path) return
    try {
      await skillApi.renameFile(wb.skillId, path, newPath.trim())
      await loadFileList(true)
      if (activeFile.value === path) {
        await handleSelectFile(newPath.trim())
      }
      Message.success('已重命名')
    } catch (error: unknown) {
      const err = error as ApiErrorLike | undefined
      Message.error(err?._message || '重命名失败')
    }
  }

  async function handleDeleteFile(path: string) {
    if (!wb.skillId || !path) return
    Modal.confirm({
      title: '删除该文件？',
      content: path,
      okText: '删除',
      cancelText: '取消',
      okButtonProps: { status: 'danger' },
      onOk: async () => {
        try {
          await skillApi.deleteFile(wb.skillId, path)
          await loadFileList(true)
          if (activeFile.value === path) {
            resetFileEditor()
          }
          Message.success('已删除')
        } catch (error: unknown) {
          const err = error as ApiErrorLike | undefined
          Message.error(err?._message || '删除失败')
        }
      },
    })
  }

  return {
    fileList,
    loadingFiles,
    activeFile,
    activeFileContent,
    activeFileOriginalContent,
    activeFileReadOnly,
    activeFileDirty,
    activeFileLanguage,
    loadFileList,
    resetFileEditor,
    handleSelectModule,
    handleSelectFile,
    handleMonacoChange,
    handleCreateFile,
    handleRenameFile,
    handleDeleteFile,
  }
}

type FileTreeNode = {
  path: string
  name: string
  type: 'file' | 'dir'
  children?: FileTreeNode[]
}

type FlatFileItem = {
  path: string
  name: string
  depth: number
  isDir?: boolean
}

function flattenTree(nodes: FileTreeNode[], depth = 0): FlatFileItem[] {
  const sorted = [...(nodes || [])].sort((left, right) => {
    if (left.type !== right.type) return left.type === 'dir' ? -1 : 1
    return (left.name || '').localeCompare(right.name || '', 'zh-CN')
  })
  const result: FlatFileItem[] = []
  for (const node of sorted) {
    if (node.type === 'file') {
      result.push({ path: node.path, name: node.name, depth })
    } else if (node.type === 'dir') {
      result.push({ path: `${node.path}/`, name: `${node.name}/`, isDir: true, depth })
      result.push(...flattenTree(node.children || [], depth + 1))
    }
  }
  return result
}

function detectLanguage(path: string) {
  if (path.endsWith('.md')) return 'skill-md'
  if (path.endsWith('.yaml') || path.endsWith('.yml')) return 'yaml'
  if (path.endsWith('.py')) return 'python'
  if (path.endsWith('.json')) return 'json'
  if (path.endsWith('.sh')) return 'shell'
  return 'plaintext'
}
