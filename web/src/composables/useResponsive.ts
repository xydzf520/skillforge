/**
 * 响应式 matchMedia wrapper。
 *
 * 用法：
 *   import { useResponsive } from '@/composables/useResponsive'
 *   const { isMobile, isTablet, isDesktop } = useResponsive()
 *   // v-if="isMobile" 等
 */
import { onBeforeUnmount, ref } from 'vue'
import { bp } from '@/styles/tokens'

export function useResponsive() {
  const width = ref(typeof window !== 'undefined' ? window.innerWidth : 1440)

  function onResize() {
    width.value = window.innerWidth
  }
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', onResize)
    onBeforeUnmount(() => window.removeEventListener('resize', onResize))
  }

  return {
    width,
    isMobile: () => width.value <= bp.sm,
    isTablet: () => width.value > bp.sm && width.value <= bp.md,
    isDesktop: () => width.value > bp.md,
    bp,
  }
}
