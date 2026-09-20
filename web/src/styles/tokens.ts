/**
 * 设计 token：断点、间距、圆角 统一定义。
 *
 * CSS `@media` 查询不能用 `var()`，所以断点在 JS 里集中定义常量，
 * @media 查询里用常量转换后的 px 字面值（保持一处改、其他地方同步）。
 *
 * 典型用法：
 *   import { bp } from '@/styles/tokens'
 *   @media (max-width: ${bp.md}px) { ... }  // 写 .vue 时用模板字符串
 *
 * 实际项目里常直接写 `@media (max-width: 768px)`，这里的 token 是"文档"作用，
 * 未来如果换设计系统只需动本文件 + 一次项目内 grep 批改即可。
 */
export const bp = {
  xs: 480,  // 手机竖屏
  sm: 768,  // 平板竖屏 / 手机横屏
  md: 1024, // 平板横屏 / 小笔记本
  lg: 1440, // 主流桌面
  xl: 1920, // 大屏
} as const

export type BreakpointKey = keyof typeof bp
