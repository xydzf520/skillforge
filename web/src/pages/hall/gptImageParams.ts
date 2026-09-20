export type GptImageResolution = '1K' | '2K' | '4K'

export const GPT_IMAGE_ONE_K_RATIOS = ['1:1', '3:2', '2:3'] as const
export const GPT_IMAGE_FOUR_K_RATIOS = ['16:9', '9:16', '2:1', '1:2', '21:9', '9:21'] as const

export function normalizeGptImageResolution(size: string, resolution: string): GptImageResolution {
  if (resolution === '1K' && !GPT_IMAGE_ONE_K_RATIOS.includes(size as typeof GPT_IMAGE_ONE_K_RATIOS[number])) {
    return '2K'
  }
  if (resolution === '4K' && !GPT_IMAGE_FOUR_K_RATIOS.includes(size as typeof GPT_IMAGE_FOUR_K_RATIOS[number])) {
    return '2K'
  }
  return resolution === '1K' || resolution === '2K' || resolution === '4K' ? resolution : '2K'
}
