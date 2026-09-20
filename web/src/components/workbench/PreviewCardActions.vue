<template>
  <div v-if="actions.length" class="pca">
    <div class="pca-actions">
      <a-button
        v-for="(a, i) in actions"
        :key="`${a}-${i}`"
        size="mini"
        :type="i === 0 ? 'outline' : 'text'"
        @click="openDrawer(a)"
      >{{ a }}</a-button>
      <span class="pca-hint">预演按钮 · 点击查看真实卡片会携带的数据</span>
    </div>

    <a-drawer
      v-model:visible="drawerVisible"
      :title="`预演详情 · ${activeAction}`"
      placement="right"
      :width="520"
      :footer="false"
      unmount-on-close
    >
      <div class="pca-drawer">
        <div class="pca-drawer-note">
          这是钉钉用户点击「{{ activeAction }}」时卡片实际携带的载荷。
          当前为预演，尚未执行，按钮不会跳转任何页面；Skill 发布后，真实卡片才会链接到执行详情页。
        </div>

        <div v-if="preview?.adapter" class="pca-section">
          <div class="pca-section-h">输出适配器</div>
          <code class="pca-code">{{ preview.adapter }}</code>
        </div>

        <div class="pca-section">
          <div class="pca-section-h">卡片载荷</div>
          <pre class="pca-json">{{ prettyJson(preview?.card_payload) }}</pre>
        </div>

        <div v-if="preview?.fixture_used" class="pca-section">
          <div class="pca-section-h">使用的样例输入</div>
          <pre class="pca-json">{{ prettyJson(preview.fixture_used) }}</pre>
        </div>

        <div v-if="preview?.rendered_output" class="pca-section">
          <div class="pca-section-h">渲染后的纯文本</div>
          <pre class="pca-json">{{ preview.rendered_output }}</pre>
        </div>
      </div>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'

const props = defineProps<{
  preview?: Record<string, any> | null
}>()

const drawerVisible = ref(false)
const activeAction = ref('')

const actions = computed<string[]>(() => {
  const a = props.preview?.card_payload?.actions
  return Array.isArray(a) ? a.filter((x): x is string => typeof x === 'string') : []
})

function openDrawer(label: string) {
  activeAction.value = label
  drawerVisible.value = true
}

function prettyJson(v: unknown) {
  if (v == null) return '（无）'
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}
</script>

<style scoped>
.pca { margin-top: 10px; }
.pca-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.pca-hint {
  font-size: 11px;
  color: var(--ai-ink-3);
  margin-left: 2px;
}
.pca-drawer-note {
  background: var(--ai-surface-2);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12px;
  color: var(--ai-ink-2);
  line-height: 1.6;
  margin-bottom: 16px;
}
.pca-section { margin-bottom: 18px; }
.pca-section-h {
  font-size: 12px;
  font-weight: 700;
  color: var(--ai-ink-2);
  margin-bottom: 6px;
}
.pca-code {
  background: var(--ai-surface-2);
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 12px;
  color: var(--ai-ink-1);
}
.pca-json {
  background: var(--ai-surface-2);
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 320px;
  overflow-y: auto;
  margin: 0;
}
</style>
