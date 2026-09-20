export type AiChatModelRow = {
  id: string
  name: string
  available: boolean
  status: string
  reason?: string
  supports_vision: boolean
  vision_label: '支持图片' | '仅文本'
  context_window_tokens: number
  max_output_tokens: number
  chat_context_tokens: number
  chat_max_output_tokens: number
  max_output_display?: string
  reasoning_options: string[]
  limit_note?: string
}

export type AiChatModelGroup = {
  id: string
  name: string
  configured: boolean
  models: AiChatModelRow[]
}

type ModelSpec = Omit<AiChatModelRow, 'name' | 'available' | 'status' | 'vision_label'>

function createModel(spec: ModelSpec): AiChatModelRow {
  return {
    ...spec,
    name: spec.id,
    available: true,
    status: 'available',
    vision_label: spec.supports_vision ? '支持图片' : '仅文本',
  }
}

const modelSpecs: Record<string, ModelSpec> = {
  'deepseek-v4-pro': {
    id: 'deepseek-v4-pro', supports_vision: false,
    context_window_tokens: 1_000_000, max_output_tokens: 384_000,
    chat_context_tokens: 800_000, chat_max_output_tokens: 32_768,
    reasoning_options: ['auto', 'none', 'high'],
  },
  'glm-5.2': {
    id: 'glm-5.2', supports_vision: false,
    context_window_tokens: 1_000_000, max_output_tokens: 128_000,
    chat_context_tokens: 800_000, chat_max_output_tokens: 32_768,
    reasoning_options: ['auto', 'none', 'low', 'high', 'max'],
  },
  'kimi-k3': {
    id: 'kimi-k3', supports_vision: true,
    context_window_tokens: 1_000_000, max_output_tokens: 1_048_576,
    max_output_display: '≤1M',
    chat_context_tokens: 800_000, chat_max_output_tokens: 32_768,
    reasoning_options: ['auto', 'low', 'high', 'max'],
  },
  'claude-fable-5': {
    id: 'claude-fable-5', supports_vision: true,
    context_window_tokens: 1_000_000, max_output_tokens: 128_000,
    chat_context_tokens: 800_000, chat_max_output_tokens: 32_768,
    reasoning_options: ['auto', 'low', 'medium', 'high', 'max'],
  },
  'claude-haiku-4-5-20251001': {
    id: 'claude-haiku-4-5-20251001', supports_vision: true,
    context_window_tokens: 200_000, max_output_tokens: 64_000,
    chat_context_tokens: 120_000, chat_max_output_tokens: 16_384,
    reasoning_options: ['auto', 'none', 'low', 'medium', 'high', 'max'],
  },
  'claude-opus-4-6': {
    id: 'claude-opus-4-6', supports_vision: true,
    context_window_tokens: 1_000_000, max_output_tokens: 128_000,
    chat_context_tokens: 800_000, chat_max_output_tokens: 32_768,
    reasoning_options: ['auto', 'low', 'medium', 'high', 'max'],
  },
  'claude-opus-4-7': {
    id: 'claude-opus-4-7', supports_vision: true,
    context_window_tokens: 1_000_000, max_output_tokens: 128_000,
    chat_context_tokens: 800_000, chat_max_output_tokens: 32_768,
    reasoning_options: ['auto', 'low', 'medium', 'high', 'max'],
  },
  'claude-opus-4-8': {
    id: 'claude-opus-4-8', supports_vision: true,
    context_window_tokens: 1_000_000, max_output_tokens: 128_000,
    chat_context_tokens: 800_000, chat_max_output_tokens: 32_768,
    reasoning_options: ['auto', 'low', 'medium', 'high', 'max'],
  },
  'claude-sonnet-4-5-20250929': {
    id: 'claude-sonnet-4-5-20250929', supports_vision: true,
    context_window_tokens: 200_000, max_output_tokens: 64_000,
    chat_context_tokens: 120_000, chat_max_output_tokens: 16_384,
    reasoning_options: ['auto', 'none', 'low', 'medium', 'high', 'max'],
  },
  'claude-sonnet-4-6': {
    id: 'claude-sonnet-4-6', supports_vision: true,
    context_window_tokens: 1_000_000, max_output_tokens: 128_000,
    chat_context_tokens: 800_000, chat_max_output_tokens: 32_768,
    reasoning_options: ['auto', 'low', 'medium', 'high', 'max'],
  },
  'gpt-5.4': {
    id: 'gpt-5.4', supports_vision: true,
    context_window_tokens: 1_050_000, max_output_tokens: 128_000,
    chat_context_tokens: 256_000, chat_max_output_tokens: 128_000,
    reasoning_options: ['auto', 'none', 'low', 'medium', 'high', 'xhigh'],
  },
  'gpt-5.4-mini': {
    id: 'gpt-5.4-mini', supports_vision: true,
    context_window_tokens: 400_000, max_output_tokens: 128_000,
    chat_context_tokens: 256_000, chat_max_output_tokens: 128_000,
    reasoning_options: ['auto', 'none', 'low', 'medium', 'high', 'xhigh'],
  },
  'gpt-5.5': {
    id: 'gpt-5.5', supports_vision: true,
    context_window_tokens: 1_050_000, max_output_tokens: 128_000,
    chat_context_tokens: 256_000, chat_max_output_tokens: 128_000,
    reasoning_options: ['auto', 'none', 'low', 'medium', 'high', 'xhigh'],
  },
  'gpt-5.6-luna': {
    id: 'gpt-5.6-luna', supports_vision: true,
    context_window_tokens: 1_050_000, max_output_tokens: 128_000,
    chat_context_tokens: 256_000, chat_max_output_tokens: 128_000,
    reasoning_options: ['auto', 'none', 'low', 'medium', 'high', 'xhigh', 'max'],
  },
  'gpt-5.6-sol': {
    id: 'gpt-5.6-sol', supports_vision: true,
    context_window_tokens: 1_050_000, max_output_tokens: 128_000,
    chat_context_tokens: 256_000, chat_max_output_tokens: 128_000,
    reasoning_options: ['auto', 'none', 'low', 'medium', 'high', 'xhigh', 'max'],
  },
  'gpt-5.6-terra': {
    id: 'gpt-5.6-terra', supports_vision: true,
    context_window_tokens: 1_050_000, max_output_tokens: 128_000,
    chat_context_tokens: 256_000, chat_max_output_tokens: 128_000,
    reasoning_options: ['auto', 'none', 'low', 'medium', 'high', 'xhigh', 'max'],
  },
}

const groupSpecs = [
  { id: 'domestic', name: '国产模型', models: ['deepseek-v4-pro', 'glm-5.2', 'kimi-k3'] },
  {
    id: 'claude_max', name: 'Claude Max',
    models: [
      'claude-fable-5', 'claude-haiku-4-5-20251001', 'claude-opus-4-6',
      'claude-opus-4-7', 'claude-opus-4-8', 'claude-sonnet-4-5-20250929', 'claude-sonnet-4-6',
    ],
  },
  {
    id: 'gpt_pro', name: 'GPT Pro',
    models: ['gpt-5.4', 'gpt-5.4-mini', 'gpt-5.5', 'gpt-5.6-luna', 'gpt-5.6-sol', 'gpt-5.6-terra'],
  },
]

export const AI_CHAT_DEFAULT_MODEL = 'deepseek-v4-pro'
export const AI_CHAT_DEFAULT_VISION_MODEL = 'gpt-5.4'

export function createBuiltInAiChatModelGroups(): AiChatModelGroup[] {
  return groupSpecs.map(group => ({
    id: group.id,
    name: group.name,
    configured: true,
    models: group.models.map(id => createModel(modelSpecs[id]!)),
  }))
}
