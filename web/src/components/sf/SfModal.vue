<template>
  <a-modal v-bind="$attrs" :width="resolvedWidth">
    <template v-for="(_, name) in $slots" #[name]="slotData">
      <slot :name="name" v-bind="slotData ?? {}" />
    </template>
  </a-modal>
</template>

<script setup lang="ts">
/**
 * SfModal — 规范化的 Modal 宽度等级
 *
 * 解决前端 UX 审计（2026-04-24）P2 / E3 — Modal 宽度无标准：
 *   各页面随手写 width="560px" / "720px" / "900px"，像素值散落。
 *   本组件收敛到三档：sm(480) / md(640) / lg(880)。
 *   需要自由指定像素时仍可透传 width 属性（会覆盖 size）。
 *
 * Props:
 * - size  — 'sm' | 'md' | 'lg'（默认 'md' → 640px）
 *
 * 透传：
 * - 其他 a-modal props（visible / title / okLoading 等）走 $attrs
 * - 所有 slots（default / title / footer ...）按名透传
 *
 * 用法：
 *   <SfModal v-model:visible="show" title="确认删除" size="sm" @ok="..." />
 *   <SfModal v-model:visible="show" title="执行对比" size="lg" :footer="false">
 *     <RunDiff />
 *   </SfModal>
 */
import { computed, useAttrs } from 'vue'

type Size = 'sm' | 'md' | 'lg'

const props = withDefaults(defineProps<{ size?: Size }>(), { size: 'md' })

defineOptions({ inheritAttrs: false })

const attrs = useAttrs()

const SIZE_MAP: Record<Size, string> = {
  sm: '480px',
  md: '640px',
  lg: '880px',
}

// 如果调用方显式传 width，优先使用 width；否则用 size 档位
const resolvedWidth = computed<string | number>(() => {
  const explicit = attrs.width as string | number | undefined
  if (explicit !== undefined && explicit !== null && explicit !== '') return explicit
  return SIZE_MAP[props.size] || SIZE_MAP.md
})
</script>
