<template>
  <!-- 输出 + 钉钉卡片预览 -->
  <a-modal
    v-model:visible="outputPreviewVisible"
    title="输出 & 钉钉卡片预览"
    :width="680"
    :footer="false"
    :mask-closable="true"
    unmount-on-close
  >
    <a-spin :loading="outputPreviewLoading" style="width:100%;min-height:120px">
      <template v-if="outputPreviewData">
        <div v-if="outputPreviewData.message" style="margin-bottom:12px;color:var(--ai-ink-3);font-size:13px">
          {{ outputPreviewData.message }}
        </div>
        <div v-if="outputPreviewData.output_text" style="margin-bottom:16px">
          <div style="font-weight:600;margin-bottom:6px;font-size:13px;color:var(--ai-ink-2)">渲染输出</div>
          <pre style="background:var(--ai-surface-2);padding:12px;border-radius:8px;font-size:12px;white-space:pre-wrap;word-break:break-all;max-height:280px;overflow-y:auto;margin:0">{{ outputPreviewData.output_text }}</pre>
        </div>
        <div v-if="outputPreviewData.dingtalk_card_html">
          <div style="font-weight:600;margin-bottom:6px;font-size:13px;color:var(--ai-ink-2)">钉钉消息卡片</div>
          <div class="dingtalk-card-preview" v-html="sanitizedDingtalkCardHtml" />
        </div>
        <div v-if="!outputPreviewData.output_text && !outputPreviewData.dingtalk_card_html" style="text-align:center;color:var(--ai-ink-4);padding:24px 0">
          暂无预览内容
        </div>
      </template>
      <div v-else-if="!outputPreviewLoading" style="text-align:center;color:var(--ai-ink-4);padding:24px 0">
        加载失败
      </div>
    </a-spin>
  </a-modal>

  <!-- 参数漂移检测 -->
  <a-modal
    v-model:visible="driftModalVisible"
    title="参数漂移检测"
    :width="640"
    :footer="false"
    :mask-closable="true"
    unmount-on-close
  >
    <a-spin :loading="driftLoading" style="width:100%;min-height:120px">
      <template v-if="driftResults">
        <div v-if="driftResults.message" style="margin-bottom:12px;color:var(--ai-ink-3);font-size:13px">
          {{ driftResults.message }}
        </div>
        <div v-if="driftResults.drifts?.length" style="display:flex;flex-direction:column;gap:10px">
          <div
            v-for="(drift, i) in driftResults.drifts"
            :key="i"
            style="background:var(--ai-surface-2);padding:12px;border-radius:8px;font-size:13px"
          >
            <div style="font-weight:600;margin-bottom:4px">
              {{ (drift as RecObj).data_input || (drift as RecObj).param || `漂移 #${i + 1}` }}
            </div>
            <div style="color:var(--ai-ink-3)">
              {{ (drift as RecObj).message || (drift as RecObj).description || JSON.stringify(drift) }}
            </div>
            <div v-if="(drift as RecObj).severity" style="margin-top:4px">
              <a-tag
                :color="(drift as RecObj).severity === 'high' ? 'red' : (drift as RecObj).severity === 'medium' ? 'orange' : 'blue'"
                size="small"
              >
                {{ (drift as RecObj).severity }}
              </a-tag>
            </div>
          </div>
        </div>
        <div v-else style="text-align:center;color:var(--ai-ink-4);padding:24px 0">
          未检测到参数漂移
        </div>
      </template>
    </a-spin>
  </a-modal>

  <!-- AI 反例发现 -->
  <a-modal
    v-model:visible="antipatternModalVisible"
    title="AI 反例发现"
    :width="720"
    :footer="false"
    :mask-closable="true"
    unmount-on-close
  >
    <a-spin :loading="antipatternLoading" style="width:100%;min-height:120px">
      <template v-if="antipatternResults">
        <div v-if="antipatternResults.stats" style="margin-bottom:12px;display:flex;gap:16px;font-size:13px;color:var(--ai-ink-3)">
          <span>否决记录: {{ (antipatternResults.stats as RecObj).total_overridden }}</span>
          <span>发现模式: {{ (antipatternResults.stats as RecObj).clustered }}</span>
          <span>已覆盖: {{ (antipatternResults.stats as RecObj).already_covered }}</span>
        </div>
        <div v-if="antipatternResults.discovered?.length" style="display:flex;flex-direction:column;gap:10px">
          <div
            v-for="(item, i) in antipatternResults.discovered"
            :key="i"
            style="background:var(--ai-surface-2);padding:12px;border-radius:8px;font-size:13px"
          >
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
              <span style="font-weight:600">{{ (item as RecObj).title }}</span>
              <a-tag
                :color="(item as RecObj).confidence === 'high' ? 'red' : (item as RecObj).confidence === 'medium' ? 'orange' : 'blue'"
                size="small"
              >{{ (item as RecObj).confidence }}</a-tag>
              <span style="color:var(--ai-ink-4);font-size:12px">出现 {{ (item as RecObj).occurrences }} 次</span>
            </div>
            <div style="color:var(--ai-ink-3);margin-bottom:4px">
              <strong>触发条件：</strong>{{ (item as RecObj).trigger_condition }}
            </div>
            <div style="color:var(--ai-ink-2)">
              <strong>建议规则：</strong>{{ (item as RecObj).suggested_rule }}
            </div>
            <div v-if="(item as RecObj).feedback_summary" style="color:var(--ai-ink-4);font-size:12px;margin-top:4px">
              运营反馈：{{ (item as RecObj).feedback_summary }}
            </div>
          </div>
        </div>
        <div v-else style="text-align:center;color:var(--ai-ink-4);padding:24px 0">
          {{ (antipatternResults.stats as RecObj)?.message || '未发现新的反例模式' }}
        </div>
      </template>
    </a-spin>
  </a-modal>
</template>

<script setup lang="ts">
/**
 * v2.4.0 Studio 弹窗群（Modals Cluster）
 *
 * 聚合 SkillStudio 的 3 个"查看型"弹窗：
 *   - 输出 + 钉钉卡片预览
 *   - 参数漂移检测结果
 *   - AI 反例发现结果
 *
 * v2.4-4: 从 SkillStudio.vue 抽出以缩减主文件行数 + 让每个弹窗独立可测。
 * visible 3 个模型都用 Vue 3.4+ 的 defineModel 双向绑定。
 */
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import type { DriftCheckResponse, DiscoverAntipatternsResponse } from '@/types/skillstudio'

type RecObj = Record<string, unknown>
type OutputPreviewData = { output_text?: string; dingtalk_card_html?: string; message?: string }

const outputPreviewVisible = defineModel<boolean>('outputPreviewVisible', { default: false })
const driftModalVisible = defineModel<boolean>('driftModalVisible', { default: false })
const antipatternModalVisible = defineModel<boolean>('antipatternModalVisible', { default: false })

const props = defineProps<{
  outputPreviewLoading: boolean
  outputPreviewData: OutputPreviewData | null
  driftLoading: boolean
  driftResults: DriftCheckResponse | null
  antipatternLoading: boolean
  antipatternResults: DiscoverAntipatternsResponse | null
}>()

// 钉钉卡片 HTML 来自后端模板渲染，仍过一遍 DOMPurify 防 XSS
// 允许表格/内联样式（卡片本身需要）；strip script/iframe/事件属性/危险协议
const sanitizedDingtalkCardHtml = computed(() => {
  const raw = props.outputPreviewData?.dingtalk_card_html
  if (!raw) return ''
  return DOMPurify.sanitize(raw, {
    ALLOWED_TAGS: [
      'p', 'br', 'strong', 'b', 'em', 'i', 'u', 'del', 's', 'code', 'pre',
      'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
      'ul', 'ol', 'li',
      'blockquote', 'hr',
      'a', 'span', 'div', 'img',
      'table', 'thead', 'tbody', 'tr', 'th', 'td',
    ],
    ALLOWED_ATTR: ['href', 'title', 'target', 'rel', 'class', 'alt', 'align', 'style', 'src', 'width', 'height'],
    ALLOW_DATA_ATTR: false,
    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
  })
})
</script>

<style scoped>
.dingtalk-card-preview {
  border: 1px solid var(--ai-border);
  border-radius: 8px;
  padding: 16px;
  background: var(--ai-surface-2);
  max-height: 320px;
  overflow-y: auto;
  font-size: 13px;
  line-height: 1.6;
}
.dingtalk-card-preview :deep(table) {
  border-collapse: collapse; width: 100%;
}
.dingtalk-card-preview :deep(td),
.dingtalk-card-preview :deep(th) {
  border: 1px solid var(--ai-border);
  padding: 6px 10px; font-size: 12px;
}
</style>
