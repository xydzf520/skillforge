<template>
  <main class="style-preview">
    <header class="preview-header">
      <SfBrand />
      <span class="preview-notice">样式检查 · 合成内容 · 不连接业务服务</span>
      <a-button @click="toggleTheme">{{ dark ? '切换日间' : '切换暗色' }}</a-button>
    </header>
    <div class="preview-title">
      <p class="page-kicker">SKILLFORGE / DESIGN FOUNDATION</p>
      <h1>同一套样式，贯穿每个工作环节</h1>
      <p>品牌、表单、操作和沟通使用共享组件。成功、警告与失败保留各自含义。</p>
    </div>
    <details class="preview-workbench">
      <summary>展开真实工作台讲解视图（合成示例）</summary>
      <WorkbenchExplainView :doc="skillExample" />
    </details>
    <div class="preview-grid">
      <section class="preview-card">
        <h2>操作与状态</h2>
        <p>页面、弹窗和抽屉使用相同的按钮规则。</p>
        <a-space wrap class="preview-row">
          <a-button type="primary" @click="modal = true">预览确认弹窗</a-button>
          <a-button @click="drawer = true">预览详情抽屉</a-button>
          <a-button type="primary" disabled>暂不可用</a-button>
        </a-space>
        <a-space wrap class="preview-row">
          <a-button type="primary" status="success">成功操作</a-button>
          <a-button type="primary" status="warning">需要确认</a-button>
          <a-button type="primary" status="danger">危险操作样式</a-button>
        </a-space>
        <div class="preview-statuses">
          <span class="preview-chip sf-tone-success">已完成</span>
          <span class="preview-chip sf-tone-warning">待确认</span>
          <span class="preview-chip sf-tone-danger">执行失败</span>
          <span class="preview-chip sf-tone-brand">AI 建议</span>
        </div>
      </section>
      <section class="preview-card">
        <h2>输入与筛选</h2>
        <p>统一边框、圆角、键盘焦点和禁用状态。</p>
        <a-form :model="form" layout="vertical">
          <a-form-item label="技能名称"><a-input v-model="form.name" placeholder="输入技能名称" /></a-form-item>
          <a-form-item label="发布范围"><a-select v-model="form.scope"><a-option>当前团队</a-option><a-option>个人</a-option></a-select></a-form-item>
          <a-input placeholder="不可编辑的字段" disabled />
        </a-form>
      </section>
      <section class="preview-card preview-conversation">
        <h2>AI 沟通</h2>
        <p>实际工作台组件，使用本地示例消息；不会调用模型。</p>
        <WorkbenchChatPanel :messages="messages" placeholder="输入一条本地示例消息…" @send="addMessage" />
      </section>
      <section class="preview-card">
        <h2>业务信息与详情</h2>
        <p>减少装饰性渐变，用层级、分组和文字表达信息。</p>
        <div class="preview-example">
          <span class="preview-chip sf-tone-brand">示例技能</span>
          <h3>团队知识整理</h3>
          <p>将已确认的方法整理为可复用技能，保留输入、版本与结果。</p>
          <dl><div><dt>当前阶段</dt><dd>等待人工确认</dd></div><div><dt>运行方式</dt><dd>人工触发</dd></div><div><dt>内容来源</dt><dd>合成示例</dd></div></dl>
          <a-button type="primary" @click="drawer = true">查看详情</a-button>
        </div>
      </section>
    </div>
    <a-modal v-model:visible="modal" title="确认界面预览" ok-text="关闭预览" cancel-text="返回">
      <p>这是样式检查，不会保存或发布任何技能。</p>
      <a-input placeholder="验证弹窗中的输入焦点" />
    </a-modal>
    <a-drawer v-model:visible="drawer" title="详情界面预览" :width="420" ok-text="关闭预览" cancel-text="返回">
      <h3>团队知识整理</h3><p>按钮、输入框与卡片均引用同一套主题。</p>
      <a-input placeholder="验证抽屉中的输入焦点" />
    </a-drawer>
  </main>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import SfBrand from '@/components/brand/SfBrand.vue'
import WorkbenchChatPanel from '@/components/workbench/WorkbenchChatPanel.vue'
import WorkbenchExplainView from '@/components/workbench/WorkbenchExplainView.vue'

const dark = ref(false)
const modal = ref(false)
const drawer = ref(false)
const form = reactive({ name: '', scope: '当前团队' })
const skillExample = {
  meta: { name: '团队知识整理', department: '示例团队', trigger_type: 'manual' },
  goal: '将已确认的团队经验整理为可复用的技能说明。',
  rules: [{ id: 'review', name: '确认来源', branches: [{ condition: '来源已确认', action: '整理为草稿' }] }],
  params: [{ name: '条目上限', default_value: 10 }],
  output_table: [{ name: '整理草稿', recipient: '本人', format: 'Markdown' }],
}
const messages = ref([
  { role: 'user', content: '将团队经验整理成可复用的技能。' },
  { role: 'assistant', content: '可以先明确 **输入、输出和确认人**，再保留来源与版本，便于复核。' },
])
function toggleTheme() {
  dark.value = !dark.value
  if (dark.value) document.body.setAttribute('arco-theme', 'dark')
  else document.body.removeAttribute('arco-theme')
}
function addMessage(content: string) {
  messages.value.push({ role: 'user', content })
}
</script>

<style scoped>
.style-preview { max-width: 1200px; margin: auto; padding: 24px 32px 48px; font-family: var(--ai-font-sans); }
.preview-header { display: flex; align-items: center; gap: 20px; padding-bottom: 20px; border-bottom: 1px solid var(--ai-border); }
.preview-notice { flex: 1; color: var(--ai-ink-3); font-size: 12px; }
.preview-title { padding: 30px 0 24px; }
.preview-workbench { margin-top: 24px; border: 1px solid var(--ai-border); border-radius: var(--sf-panel-radius); overflow: hidden; }
.preview-workbench summary { padding: 16px 24px; cursor: pointer; color: var(--ai-accent); background: var(--ai-surface); }
.preview-workbench summary:focus-visible { outline: 2px solid var(--sf-focus-ring); outline-offset: -3px; }
.preview-title h1 { font-size: 28px; margin: 8px 0; font-weight: 600; letter-spacing: -.02em; }
.preview-title p, .preview-card > p { color: var(--ai-ink-3); line-height: 1.7; }
.preview-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.preview-card { background: var(--ai-surface); border: 1px solid var(--ai-border); border-radius: var(--sf-panel-radius); padding: 24px; min-width: 0; box-shadow: var(--ai-shadow-1); }
.preview-card h2 { margin: 0 0 8px; font-size: 17px; font-weight: 600; }
.preview-card > p { font-size: 13px; margin: 0 0 20px; }
.preview-row { display: flex; margin-bottom: 16px; }
.preview-statuses { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 24px; }
.preview-chip { display: inline-flex; padding: 3px 9px; border: 1px solid var(--sf-tone-border); color: var(--sf-tone-fg); background: var(--sf-tone-bg); border-radius: 6px; font-size: 12px; }
.preview-conversation :deep(.chat-panel) { height: 310px; }
.preview-conversation :deep(.chat-messages) { padding: 12px 0; }
.preview-conversation :deep(.chat-input-area) { padding: 12px 0 0; }
.preview-example { background: var(--ai-surface-2); border: 1px solid var(--ai-border); border-radius: var(--sf-panel-radius); padding: 20px; }
.preview-example h3 { font-size: 18px; margin: 16px 0 8px; }
.preview-example p { color: var(--ai-ink-2); line-height: 1.7; }
dl { margin: 20px 0; } dl div { display: flex; justify-content: space-between; padding: 8px 0; gap: 16px; border-top: 1px solid var(--ai-border); }dt { color: var(--ai-ink-3); }dd { margin: 0; }
@media (max-width: 760px) { .style-preview { padding: 20px 16px; } .preview-grid { grid-template-columns: 1fr; } .preview-header { flex-wrap: wrap; gap: 14px; } .preview-notice { order: 3; flex-basis: 100%; } .preview-header > button { margin-left: auto; } .preview-title h1 { font-size: 24px; } }
</style>
