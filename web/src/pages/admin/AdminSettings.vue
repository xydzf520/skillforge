<template>
  <div class="page-container admin-settings-page">
    <div class="page-header">
      <div>
        <div class="page-kicker">管理后台 · 治理与合规</div>
        <h2 class="page-title">{{ pageTitle }}</h2>
        <p class="page-subtitle">{{ pageSubtitle }}</p>
      </div>
      <a-space>
        <span class="status-pill" :class="sysInfo.db_status === 'ok' ? 'ok' : 'bad'">
          <span class="status-dot"></span>
          {{ sysInfo.db_status === 'ok' ? '服务正常' : '服务异常' }}
        </span>
        <a-button class="ai-btn-like" size="small" :loading="sysInfoLoading" @click="loadSystemInfo">刷新状态</a-button>
      </a-space>
    </div>

    <a-card class="settings-shell page-list-card" :bordered="false">
      <a-tabs v-model:active-key="activeTab" type="rounded">
        <!-- ═══ Tab 1: 编辑器 AI 补全（scope: ai） ═══ -->
        <a-tab-pane v-if="showAiTabs" key="editor" title="编辑器补全">
          <div class="tab-body two-col">
            <div class="col-main">
              <div class="settings-list">
                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title-row">
                      <span class="field-title">启用 AI 编辑器补全</span>
                      <span class="status-pill" :class="aiConfig.ai_enabled ? 'ok' : 'muted'">
                        <span class="status-dot"></span>
                        {{ aiConfig.ai_enabled ? '已启用' : '已关闭' }}
                      </span>
                    </div>
                    <div class="field-key">editor.ai_enabled</div>
                    <div class="setting-meta">
                      <span>关闭后编辑器不会调用 AI</span>
                    </div>
                  </div>
                  <div class="field-editor field-editor-inline">
                    <a-switch v-model="aiConfig.ai_enabled" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">API 地址</div>
                    <div class="field-key">editor.api_base</div>
                    <div class="setting-meta">
                      <span>OpenAI 兼容端点 URL</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input v-model="aiConfig.api_base" placeholder="https://api.deepseek.com" class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">模型</div>
                    <div class="field-key">editor.model</div>
                  </div>
                  <div class="field-editor">
                    <a-input v-model="aiConfig.model" placeholder="deepseek-chat" class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">API Key</div>
                    <div class="field-key">editor.api_key</div>
                    <div class="setting-meta">
                      <span>留空表示沿用已保存值</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input-password v-model="aiConfig.api_key" placeholder="sk-..." allow-clear class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">最大 Token</div>
                    <div class="field-key">editor.max_tokens</div>
                    <div class="setting-meta">
                      <span>50-2000</span>
                      <span>补全片段长度</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input-number v-model="aiConfig.max_tokens" :min="50" :max="2000" :step="50" hide-button />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">Temperature</div>
                    <div class="field-key">editor.temperature</div>
                    <div class="setting-meta">
                      <span>0-2 · 0=保守 / 1=有创意</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input-number v-model="aiConfig.temperature" :min="0" :max="2" :step="0.1" :precision="1" hide-button />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">缓存时间（秒）</div>
                    <div class="field-key">editor.cache_ttl</div>
                    <div class="setting-meta">
                      <span>60-7200 · 相同上下文命中缓存直接返回</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input-number v-model="aiConfig.cache_ttl" :min="60" :max="7200" :step="60" hide-button />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">触发模式</div>
                    <div class="field-key">editor.trigger</div>
                    <div class="setting-meta">
                      <span>空闲自动 = 停止输入后触发；仅快捷键 = Ctrl+Shift+Space 手动触发</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-radio-group v-model="aiConfig.trigger" type="button" size="small">
                      <a-radio value="onIdle">空闲自动</a-radio>
                      <a-radio value="onDemand">仅快捷键</a-radio>
                    </a-radio-group>
                  </div>
                </section>

                <section class="setting-row setting-row-stack">
                  <div class="setting-main full-width">
                    <div class="field-title-row">
                      <span class="field-title">系统提示词</span>
                      <a-button class="ai-btn-like" size="mini" @click="copyPrompt(aiConfig.system_prompt)">
                        <icon-copy /> 复制
                      </a-button>
                    </div>
                    <div class="field-key">editor.system_prompt</div>
                    <div class="prompt-editor-wrap">
                      <MonacoEditor
                        v-model="aiConfig.system_prompt"
                        language="markdown"
                        :theme="'vs'"
                        height="220px"
                        :enable-a-i="false"
                        :options="{ lineNumbers: 'on', minimap: { enabled: false }, wordWrap: 'on' }"
                      />
                    </div>
                  </div>
                </section>

                <div class="row-actions-bar">
                  <a-button class="ai-btn-like primary" type="primary" :loading="aiSaving" @click="saveAiConfig">保存编辑器配置</a-button>
                </div>
              </div>
            </div>
            <aside class="col-side">
              <div class="help-card">
                <div class="help-card-title">编辑器补全说明</div>
                <div class="help-text">
                  <p>编辑器补全在 Skill 编辑页面提供代码续写建议。</p>
                  <p><b>触发方式：</b>空闲自动 = 停止输入后自动触发；仅快捷键 = Ctrl+Shift+Space 手动触发</p>
                  <p><b>Max Token：</b>补全片段长度，建议 100-300</p>
                  <p><b>Temperature：</b>0 = 保守，1 = 有创意</p>
                  <p><b>缓存：</b>相同上下文命中缓存直接返回，节省 API 调用</p>
                </div>
              </div>
            </aside>
          </div>
        </a-tab-pane>

        <!-- ═══ Tab 2: AI 全局配置 ═══ -->
        <a-tab-pane v-if="showAiTabs" key="ai" title="AI 全局">
          <div class="tab-body two-col">
            <div class="col-main">
              <div class="settings-list">
                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">供应商预设</div>
                    <div class="field-key">ai.provider</div>
                    <div class="setting-meta">
                      <span>SiliconFlow 会自动补齐 API 地址和默认模型；Custom 完全按手动配置</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-select v-model="globalAiConfig.provider" placeholder="custom">
                      <a-option value="custom">Custom</a-option>
                      <a-option value="siliconflow">SiliconFlow</a-option>
                    </a-select>
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">API 地址</div>
                    <div class="field-key">ai.api_base</div>
                  </div>
                  <div class="field-editor">
                    <a-input v-model="globalAiConfig.api_base" placeholder="https://api.deepseek.com" class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">模型</div>
                    <div class="field-key">ai.model</div>
                  </div>
                  <div class="field-editor">
                    <a-input v-model="globalAiConfig.model" placeholder="deepseek-chat" class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">API Key</div>
                    <div class="field-key">ai.api_key</div>
                    <div class="setting-meta">
                      <span>留空表示沿用已保存值</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input-password v-model="globalAiConfig.api_key" placeholder="sk-..." allow-clear class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">最大 Token</div>
                    <div class="field-key">ai.max_tokens</div>
                    <div class="setting-meta">
                      <span>100-8000 · 分析任务建议 2000-4000</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input-number v-model="globalAiConfig.max_tokens" :min="100" :max="8000" :step="100" hide-button />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">Temperature</div>
                    <div class="field-key">ai.temperature</div>
                    <div class="setting-meta">
                      <span>0-2</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input-number v-model="globalAiConfig.temperature" :min="0" :max="2" :step="0.1" :precision="1" hide-button />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">超时（秒）</div>
                    <div class="field-key">ai.timeout</div>
                    <div class="setting-meta">
                      <span>10-120 · 复杂分析建议 30-60s</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input-number v-model="globalAiConfig.timeout" :min="10" :max="120" :step="5" hide-button />
                  </div>
                </section>

                <div class="settings-section-title settings-section-title-spaced">便宜模型 / 项目转服务</div>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">便宜模型 API 地址</div>
                    <div class="field-key">ai.cheap.api_base</div>
                    <div class="setting-meta">
                      <span>留空沿用 ai.api_base；用于项目上传后自动转服务、低成本 AI 调用</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input v-model="cheapAiConfig.api_base" placeholder="留空沿用全局 AI API 地址" class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">便宜模型</div>
                    <div class="field-key">ai.cheap.model</div>
                  </div>
                  <div class="field-editor">
                    <a-input v-model="cheapAiConfig.model" placeholder="deepseek-chat / qwen-turbo 等低成本模型" class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">便宜模型 API Key</div>
                    <div class="field-key">ai.cheap.api_key</div>
                    <div class="setting-meta">
                      <span>留空沿用已保存值；未单独配置时沿用全局 Key</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input-password v-model="cheapAiConfig.api_key" placeholder="留空沿用全局/已保存 Key" allow-clear class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">自动转服务 AI 优化</div>
                    <div class="field-key">project.service_conversion.ai_enabled</div>
                    <div class="setting-meta">
                      <span>开启后，用户上传网页包会用便宜模型生成/优化服务契约；关闭时仍做确定性转服务</span>
                    </div>
                  </div>
                  <div class="field-editor field-editor-inline">
                    <a-switch v-model="cheapAiConfig.service_conversion_ai_enabled" />
                  </div>
                </section>

                <div class="row-actions-bar">
                  <a-button class="ai-btn-like primary" type="primary" :loading="globalAiSaving" @click="saveGlobalAiConfig">保存全局 AI 配置</a-button>
                </div>
              </div>
            </div>
            <aside class="col-side">
              <div class="help-card">
                <div class="help-card-title">覆盖范围</div>
                <div class="help-text">
                  <p>全局 AI 配置被以下功能共用：</p>
                  <ul>
                    <li>参数调优建议 (B1)</li>
                    <li>反例自动发现 (B2)</li>
                    <li>AI 语义审核 (B3)</li>
                    <li>阈值推导 (B4)</li>
                    <li>执行摘要 (B5)</li>
                    <li>分支补全 (B6)</li>
                    <li>增强测试 (B7)</li>
                    <li>模板推荐 (B8)</li>
                    <li>Agent 对话</li>
                    <li>周报生成</li>
                  </ul>
                  <p><b>Max Token：</b>分析任务建议 2000-4000</p>
                  <p><b>超时：</b>复杂分析建议 30-60s</p>
                </div>
              </div>
            </aside>
          </div>
        </a-tab-pane>

        <!-- ═══ Tab 3: 向量模型 / RAG 检索 ═══ -->
        <a-tab-pane v-if="showAiTabs" key="embedding" title="向量/RAG">
          <div class="tab-body two-col">
            <div class="col-main">
              <div class="inline-info-card embedding-guide">
                <div class="inline-info-title">向量知识库标准配置：LightRAG + embedding + Docling + VLM</div>
                <div class="inline-info-body">
                  默认使用项目标准 Docker：LightRAG <code>127.0.0.1:19621</code>，Docling <code>127.0.0.1:15001</code>；模型密钥只保存在后台。
                </div>
              </div>

              <div class="settings-list settings-section-title-spaced">
                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title-row">
                      <span class="field-title">启用向量知识库</span>
                      <span class="status-pill" :class="ragConfig.enabled ? 'ok' : 'muted'">
                        <span class="status-dot"></span>
                        {{ ragConfig.enabled ? '已启用' : '未启用' }}
                      </span>
                    </div>
                    <div class="field-key">knowledge.rag.enabled</div>
                    <div class="setting-meta">
                      <span>启用后知识库按正式 LightRAG/embedding 配置运行；未启用时仍保留本地降级索引能力。</span>
                    </div>
                  </div>
                  <div class="field-editor field-editor-inline">
                    <a-switch v-model="ragConfig.enabled" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">向量供应商</div>
                    <div class="field-key">ai.embedding.provider</div>
                    <div class="setting-meta">
                      <span>本地验证选 Ollama：免 Key；线上服务可选 SiliconFlow/OpenAI</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-select v-model="embeddingConfig.provider" @change="applyEmbeddingProviderPreset">
                      <a-option v-for="provider in embeddingProviders" :key="provider.value" :value="provider.value">
                        {{ provider.label }}
                      </a-option>
                    </a-select>
                  </div>
                </section>

                <section class="setting-row setting-row-stack">
                  <div class="setting-main full-width">
                    <div class="field-title">使用场景</div>
                    <div class="field-key">ai.embedding.scenario</div>
                    <div class="scenario-grid">
                      <button
                        v-for="scenario in embeddingScenarios"
                        :key="scenario.value"
                        type="button"
                        class="scenario-card"
                        :class="{ active: embeddingConfig.scenario === scenario.value }"
                        @click="selectEmbeddingScenario(scenario.value)"
                      >
                        <span class="scenario-title">{{ scenario.label }}</span>
                        <span class="scenario-desc">{{ scenario.description }}</span>
                        <code>{{ recommendedModelForScenario(scenario.value) }}</code>
                      </button>
                    </div>
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">向量模型</div>
                    <div class="field-key">ai.embedding.model</div>
                    <div class="setting-meta">
                      <span>{{ embeddingModelHelp }}</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-select
                      v-if="embeddingModelOptions.length"
                      v-model="embeddingConfig.model"
                      @change="applyEmbeddingModelMeta"
                    >
                      <a-option v-for="model in embeddingModelOptions" :key="model.value" :value="model.value">
                        {{ model.label }}
                      </a-option>
                    </a-select>
                    <a-input v-else v-model="embeddingConfig.model" placeholder="输入 OpenAI 兼容 embedding 模型名" class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title-row">
                      <span class="field-title">API Key</span>
                      <span v-if="!embeddingProviderRequiresKey" class="status-pill info"><span class="status-dot"></span>免 Key</span>
                      <span v-else-if="embeddingKeyConfigured" class="status-pill ok"><span class="status-dot"></span>已保存</span>
                      <span v-else class="status-pill warn"><span class="status-dot"></span>未保存</span>
                    </div>
                    <div class="field-key">ai.embedding.api_key</div>
                    <div class="setting-meta">
                      <span>{{ embeddingProviderRequiresKey ? '安全原因刷新后不会回显明文；显示“已保存”时留空就是沿用，输入新值会替换。' : 'Ollama 本地服务不需要 API Key。' }}</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input-password v-model="embeddingConfig.api_key" :disabled="!embeddingProviderRequiresKey" :placeholder="!embeddingProviderRequiresKey ? 'Ollama 本地免 Key' : (embeddingKeyConfigured ? '已保存，留空沿用；输入新 Key 替换' : selectedEmbeddingProvider.keyPlaceholder)" allow-clear class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">API 地址</div>
                    <div class="field-key">ai.embedding.api_base</div>
                    <div class="setting-meta">
                      <span>{{ embeddingConfig.provider === 'custom' ? '自定义 OpenAI-compatible /embeddings 地址' : (embeddingConfig.provider === 'ollama' ? 'Ollama 默认 http://127.0.0.1:11434/v1' : '按供应商自动填写，避免输错') }}</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input v-model="embeddingConfig.api_base" :disabled="embeddingConfig.provider !== 'custom'" class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">向量维度 / 超时</div>
                    <div class="field-key">ai.embedding.dim / ai.embedding.timeout</div>
                    <div class="setting-meta">
                      <span>维度随模型自动填写；如自定义模型请确认维度与返回向量一致</span>
                    </div>
                  </div>
                  <div class="field-editor two-field-editor">
                    <a-input-number v-model="embeddingConfig.dim" :min="128" :max="4096" :step="128" hide-button />
                    <a-input-number v-model="embeddingConfig.timeout" :min="5" :max="120" :step="5" hide-button />
                  </div>
                </section>

                <div class="settings-section-title settings-section-title-spaced">可选：Reranker 精排</div>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title-row">
                      <span class="field-title">启用 Reranker</span>
                      <span class="status-pill" :class="rerankerConfig.enabled ? 'ok' : 'muted'">
                        <span class="status-dot"></span>
                        {{ rerankerConfig.enabled ? '已启用' : '未启用' }}
                      </span>
                    </div>
                    <div class="field-key">ai.reranker.enabled</div>
                    <div class="setting-meta">
                      <span>知识库召回后再精排，提升回答依据相关性；会增加一次模型调用</span>
                    </div>
                  </div>
                  <div class="field-editor field-editor-inline">
                    <a-switch v-model="rerankerConfig.enabled" />
                  </div>
                </section>

                <section v-if="rerankerConfig.enabled" class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">Reranker 模型</div>
                    <div class="field-key">ai.reranker.model</div>
                    <div class="setting-meta">
                      <span>SiliconFlow 默认使用 BGE Reranker；API Key 留空会沿用向量 Key</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-select v-model="rerankerConfig.model" @change="applyRerankerPreset">
                      <a-option v-for="model in rerankerModelOptions" :key="model.value" :value="model.value">
                        {{ model.label }}
                      </a-option>
                    </a-select>
                  </div>
                </section>

                <section v-if="rerankerConfig.enabled" class="setting-row">
                  <div class="setting-main">
                    <div class="field-title-row">
                      <span class="field-title">Reranker API Key</span>
                      <span v-if="rerankerKeyConfigured" class="status-pill ok"><span class="status-dot"></span>已保存</span>
                    </div>
                    <div class="field-key">ai.reranker.api_key</div>
                    <div class="setting-meta">
                      <span>同平台时可留空，自动沿用 ai.embedding.api_key；刷新后不会回显明文。</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input-password v-model="rerankerConfig.api_key" placeholder="留空沿用向量 API Key" allow-clear class="mono-input" />
                  </div>
                </section>

                <div class="settings-section-title settings-section-title-spaced">标准：LightRAG Server</div>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title-row">
                      <span class="field-title">检索部署模式</span>
                      <span class="status-pill" :class="lightragConfig.mode === 'server' ? 'ok' : 'muted'">
                        <span class="status-dot"></span>
                        {{ lightragConfig.mode === 'server' ? 'Server' : 'Embedded' }}
                      </span>
                    </div>
                    <div class="field-key">knowledge.lightrag.mode</div>
                    <div class="setting-meta">
                      <span>Embedded 为当前内置模式；Server 会把索引/检索请求发到自建 LightRAG 服务。</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-select v-model="lightragConfig.mode">
                      <a-option value="embedded">Embedded 内置</a-option>
                      <a-option value="server">Server 自建</a-option>
                    </a-select>
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">Server 类型</div>
                    <div class="field-key">knowledge.lightrag.flavor</div>
                    <div class="setting-meta">
                      <span>官方 HKUDS Docker 选 official；自研兼容网关选 skillforge。</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-select v-model="lightragConfig.flavor">
                      <a-option value="official">官方 HKUDS LightRAG</a-option>
                      <a-option value="skillforge">SkillForge 兼容接口</a-option>
                    </a-select>
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">Server API 地址</div>
                    <div class="field-key">knowledge.lightrag.api_base</div>
                    <div class="setting-meta">
                      <span>项目标准 Docker 默认 <code>http://127.0.0.1:19621</code>；不要带尾部斜杠。</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input v-model="lightragConfig.api_base" placeholder="http://127.0.0.1:19621" class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title-row">
                      <span class="field-title">Server API Key</span>
                      <span v-if="lightragKeyConfigured" class="status-pill ok"><span class="status-dot"></span>已保存</span>
                      <span v-else class="status-pill muted"><span class="status-dot"></span>可选</span>
                    </div>
                    <div class="field-key">knowledge.lightrag.api_key</div>
                    <div class="setting-meta">
                      <span>官方服务启用鉴权时填写；刷新后不回显明文。</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input-password v-model="lightragConfig.api_key" :placeholder="lightragKeyConfigured ? '已保存，留空沿用；输入新 Key 替换' : '可选 API Key'" allow-clear class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">Server 超时 / 严格模式</div>
                    <div class="field-key">knowledge.lightrag.timeout / knowledge.rag.strict_server</div>
                    <div class="setting-meta">
                      <span>严格模式下 Server 不可用会让索引失败，避免静默退回本地索引。</span>
                    </div>
                  </div>
                  <div class="field-editor two-field-editor">
                    <a-input-number v-model="lightragConfig.timeout" :min="5" :max="180" :step="5" hide-button />
                    <a-switch v-model="lightragConfig.strict_server" />
                  </div>
                </section>

                <div class="row-actions-bar embedding-actions">
                  <a-space>
                    <a-button class="ai-btn-like" :loading="lightragTesting" @click="testLightragServer">测试 LightRAG Server</a-button>
                    <a-button class="ai-btn-like" :loading="lightragSyncing" @click="syncLightragServer">同步文档到 Server</a-button>
                  </a-space>
                </div>

                <div v-if="lightragTestResult" class="test-result-card" :class="lightragTestResult.ok ? 'ok' : 'bad'">
                  <template v-if="lightragTestResult.ok">
                    <div class="test-result-head">
                      <span class="status-pill ok"><span class="status-dot"></span>LightRAG Server 正常</span>
                      <span class="test-result-meta">{{ lightragTestResult.duration_ms }}ms</span>
                    </div>
                    <div class="test-result-detail">
                      api: <code>{{ lightragTestResult.api_base }}</code> ·
                      flavor: <code>{{ lightragTestResult.flavor }}</code> ·
                      key: <code>{{ lightragTestResult.used_unsaved_key ? '当前输入' : '已保存/未启用' }}</code>
                    </div>
                  </template>
                  <template v-else>
                    <div class="test-result-head">
                      <span class="status-pill bad"><span class="status-dot"></span>LightRAG Server 连接失败</span>
                    </div>
                    <div class="test-result-detail">{{ lightragTestResult.error }}</div>
                  </template>
                </div>

                <div class="settings-section-title settings-section-title-spaced">图文解析：Docling + VLM</div>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title-row">
                      <span class="field-title">启用 Docling 解析服务</span>
                      <span class="status-pill" :class="doclingConfig.enabled ? 'ok' : 'muted'">
                        <span class="status-dot"></span>
                        {{ doclingConfig.enabled ? '已启用' : '未启用' }}
                      </span>
                    </div>
                    <div class="field-key">knowledge.docling.enabled</div>
                    <div class="setting-meta">
                      <span>用于 PDF、图片和 Office 文档解析；项目标准 Docker 默认本机服务。</span>
                    </div>
                  </div>
                  <div class="field-editor field-editor-inline">
                    <a-switch v-model="doclingConfig.enabled" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">Docling API 地址</div>
                    <div class="field-key">knowledge.docling.api_base</div>
                    <div class="setting-meta">
                      <span>项目标准 Docker 默认 <code>http://127.0.0.1:15001</code></span>
                    </div>
                  </div>
                  <div class="field-editor two-field-editor">
                    <a-input v-model="doclingConfig.api_base" placeholder="http://127.0.0.1:15001" class="mono-input" />
                    <a-input-number v-model="doclingConfig.timeout" :min="5" :max="120" :step="5" hide-button />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">VLM 供应商 / 模型</div>
                    <div class="field-key">knowledge.vlm.provider / knowledge.vlm.model</div>
                    <div class="setting-meta">
                      <span>独立于全局 AI；必须选择支持图片输入的 OpenAI-compatible 模型。</span>
                    </div>
                  </div>
                  <div class="field-editor two-field-editor">
                    <a-select v-model="vlmConfig.provider">
                      <a-option value="openai">OpenAI 兼容</a-option>
                      <a-option value="custom">自定义</a-option>
                    </a-select>
                    <a-input v-model="vlmConfig.model" placeholder="qwen-vl-plus / gpt-4o-mini" class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">VLM API 地址</div>
                    <div class="field-key">knowledge.vlm.api_base</div>
                    <div class="setting-meta">
                      <span>OpenAI-compatible <code>/chat/completions</code> 地址前缀。</span>
                    </div>
                  </div>
                  <div class="field-editor two-field-editor">
                    <a-input v-model="vlmConfig.api_base" placeholder="https://api.openai.com/v1" class="mono-input" />
                    <a-input-number v-model="vlmConfig.timeout" :min="5" :max="120" :step="5" hide-button />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title-row">
                      <span class="field-title">VLM API Key</span>
                      <span v-if="vlmKeyConfigured" class="status-pill ok"><span class="status-dot"></span>已保存</span>
                      <span v-else class="status-pill warn"><span class="status-dot"></span>未保存</span>
                    </div>
                    <div class="field-key">knowledge.vlm.api_key</div>
                    <div class="setting-meta">
                      <span>刷新后不回显明文；显示“已保存”时留空就是沿用。</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input-password v-model="vlmConfig.api_key" :placeholder="vlmKeyConfigured ? '已保存，留空沿用；输入新 Key 替换' : '填写 VLM API Key'" allow-clear class="mono-input" />
                  </div>
                </section>

                <div class="row-actions-bar embedding-actions">
                  <a-space>
                    <a-button class="ai-btn-like" :loading="doclingTesting" @click="testDoclingConfig">测试 Docling</a-button>
                    <a-button class="ai-btn-like" :loading="vlmTesting" @click="testVlmConfig">测试 VLM</a-button>
                  </a-space>
                </div>

                <div v-if="doclingTestResult" class="test-result-card" :class="doclingTestResult.ok ? 'ok' : 'bad'">
                  <template v-if="doclingTestResult.ok">
                    <div class="test-result-head">
                      <span class="status-pill ok"><span class="status-dot"></span>Docling 正常</span>
                      <span class="test-result-meta">{{ doclingTestResult.duration_ms }}ms</span>
                    </div>
                    <div class="test-result-detail">api: <code>{{ doclingTestResult.api_base }}</code></div>
                  </template>
                  <template v-else>
                    <div class="test-result-head"><span class="status-pill bad"><span class="status-dot"></span>Docling 连接失败</span></div>
                    <div class="test-result-detail">{{ doclingTestResult.error }}</div>
                  </template>
                </div>

                <div v-if="vlmTestResult" class="test-result-card" :class="vlmTestResult.ok ? 'ok' : 'bad'">
                  <template v-if="vlmTestResult.ok">
                    <div class="test-result-head">
                      <span class="status-pill ok"><span class="status-dot"></span>VLM 正常</span>
                      <span class="test-result-meta">{{ vlmTestResult.duration_ms }}ms</span>
                    </div>
                    <div class="test-result-detail">
                      provider: <code>{{ vlmTestResult.provider }}</code> · model: <code>{{ vlmTestResult.model }}</code> · key: <code>{{ vlmTestResult.used_unsaved_key ? '当前输入' : '已保存配置' }}</code>
                    </div>
                  </template>
                  <template v-else>
                    <div class="test-result-head"><span class="status-pill bad"><span class="status-dot"></span>VLM 连接失败</span></div>
                    <div class="test-result-detail">{{ vlmTestResult.error }}</div>
                  </template>
                </div>

                <div class="row-actions-bar embedding-actions">
                  <a-space>
                    <a-button class="ai-btn-like" :loading="embeddingTesting" @click="testEmbeddingConfig">测试向量连接</a-button>
                    <a-button class="ai-btn-like primary" type="primary" :loading="embeddingSaving" @click="saveEmbeddingConfig">保存向量/RAG/LightRAG 配置</a-button>
                  </a-space>
                </div>

                <div v-if="embeddingTestResult" class="test-result-card" :class="embeddingTestResult.ok ? 'ok' : 'bad'">
                  <template v-if="embeddingTestResult.ok">
                    <div class="test-result-head">
                      <span class="status-pill ok"><span class="status-dot"></span>向量连接成功</span>
                      <span class="test-result-meta">{{ embeddingTestResult.duration_ms }}ms</span>
                    </div>
                    <div class="test-result-detail">
                      provider: <code>{{ embeddingTestResult.provider }}</code> ·
                      model: <code>{{ embeddingTestResult.model }}</code> ·
                      dim: <code>{{ embeddingTestResult.dimension }}</code> ·
                      key: <code>{{ embeddingTestResult.used_unsaved_key ? '当前输入' : '已保存配置' }}</code>
                      <span v-if="!embeddingTestResult.dimension_match" class="warn-text">（与配置维度不一致）</span>
                    </div>
                  </template>
                  <template v-else>
                    <div class="test-result-head">
                      <span class="status-pill bad"><span class="status-dot"></span>向量连接失败</span>
                    </div>
                    <div class="test-result-detail">{{ embeddingTestResult.error }}</div>
                  </template>
                </div>
              </div>
            </div>
            <aside class="col-side">
              <div class="help-card">
                <div class="help-card-title">怎么选</div>
                <div class="help-text">
                  <p><b>本地验证：</b>选择 Ollama + <code>bge-m3</code>，免 API Key，适合先跑通整个知识库索引/检索链路。</p>
                  <p><b>线上默认：</b>选择 SiliconFlow + <code>Qwen/Qwen3-Embedding-8B</code>，适合部门知识库、LightRAG 和长文档业务问答。</p>
                  <p><b>省钱优先：</b>选择 <code>Qwen/Qwen3-Embedding-0.6B</code>；维度仍为 1024，适合大批量低成本索引。</p>
                  <p><b>中文短文本：</b>选择 <code>BAAI/bge-large-zh-v1.5</code>；输入上限较短，不适合作为长文档默认。</p>
                  <p><b>Reranker：</b>数据量上来后建议开启，可减少“看起来相关但答非所问”的来源。</p>
                  <p><b>LightRAG：</b>项目标准 Docker 使用 <code>127.0.0.1:19621</code>，后台保存后可同步文档到 Server。</p>
                  <p><b>图文解析：</b>Docling 负责文件解析，VLM 负责图片理解；VLM 必须填支持图片输入的模型。</p>
                  <p><b>防呆规则：</b>非自定义供应商会锁定 API 地址；首次保存必须填 key；测试连接会检查真实向量维度。</p>
                </div>
              </div>
            </aside>
          </div>
        </a-tab-pane>

        <!-- Optional authoring runtime has been retired in the public edition. -->
        <a-tab-pane v-if="showAiTabs" key="coding_agent" title="编程 Harness">
          <div class="tab-body two-col">
            <div class="col-main">
              <a-alert type="warning" title="替代运行时待接入">
                旧编程运行时及其启动入口已移除。当前不能启用编程对话或自动创建 Skill，
                也不会通过“测试连接”调用模型。历史配置与会话记录保留。
              </a-alert>
              <div class="inline-info-card">
                <div class="inline-info-title">下一步接入方案</div>
                <div class="help-text">
                  <p><b>OpenCode</b>：优先评估服务接口，承接会话、事件、取消和工具审批。</p>
                  <p><b>DeepSeek Harness</b>：作为实验适配器，完成插件、隔离和日志出口验证后再启用。</p>
                  <p>以上为选型建议，当前版本尚未接入任何替代编程运行时。</p>
                </div>
              </div>
              <div class="inline-info-card">
                <div class="inline-info-title">继续可用</div>
                <div class="help-text">
                  <p>手动编辑、Skill Git 版本、审核发布和普通模型调用沿用各自流程。</p>
                  <p>模型 API 配置与编程 Harness 分开：配置 DeepSeek 等模型不会自动启用执行工具。</p>
                </div>
                <div class="inline-info-body">
                  <router-link to="/admin/mcp-servers">管理 MCP 定义</router-link>
                  <span class="sep">·</span>
                  <router-link to="/admin/platform-apis">平台 API 注册表</router-link>
                </div>
              </div>
            </div>
            <aside class="col-side">
              <div class="help-card">
                <div class="help-card-title">接入完成的标准</div>
                <div class="help-text">
                  <p>按用户和 Skill 隔离工作区，工具审批绑定具体调用，支持取消、断线恢复和会话审计。</p>
                  <p>生成内容先成为草稿，通过验证与人工审核后发布；运行时不能直接绕过发布门禁。</p>
                  <p>完整选型及验收清单见仓库 <code>docs/public/HARNESS_REPLACEMENT.md</code>。</p>
                </div>
              </div>
            </aside>
          </div>
        </a-tab-pane>

        <!-- ═══ Tab 4: Skill 管理工具（scope: system） ═══ -->
        <a-tab-pane v-if="showSystemTabs" key="skill_tools" title="Skill 管理">
          <div class="tab-body two-col">
            <div class="col-main">
              <div class="settings-section-title">仓库与数据工具</div>
              <div class="settings-list">
                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">数据卫生</div>
                    <div class="field-key">tools.data_hygiene</div>
                    <div class="setting-meta">
                      <span>检查 Skill ID 分类、部门归属和历史数据补录</span>
                    </div>
                  </div>
                  <div class="field-editor field-editor-inline">
                    <a-button class="ai-btn-like" @click="router.push('/admin/data-hygiene')">
                      <template #icon><icon-scan /></template>打开
                    </a-button>
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">Git 扫描</div>
                    <div class="field-key">tools.skill_repo_scan</div>
                    <div class="setting-meta">
                      <span>扫描 skills-repo，把磁盘存在但 DB 未登记的目录补登记为 Skill</span>
                    </div>
                  </div>
                  <div class="field-editor field-editor-inline">
                    <a-button class="ai-btn-like" :loading="skillScanLoading" @click="handleSkillRepoScan">
                      <template #icon><icon-refresh /></template>扫描仓库
                    </a-button>
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">导入 Skill 包</div>
                    <div class="field-key">tools.skill_import</div>
                    <div class="setting-meta">
                      <span>从 zip 包导入 SKILL.md、scripts、tests 和 manifest</span>
                    </div>
                  </div>
                  <div class="field-editor field-editor-inline">
                    <a-upload
                      :custom-request="handleImportSkillPackage"
                      :show-file-list="false"
                      accept=".zip"
                    >
                      <template #upload-button>
                        <a-button class="ai-btn-like" :loading="skillImportLoading">
                          <template #icon><icon-upload /></template>导入 zip
                        </a-button>
                      </template>
                    </a-upload>
                  </div>
                </section>
              </div>

              <div class="settings-section-title settings-section-title-spaced">Skill Git 外部镜像仓库</div>
              <div class="settings-list">
                <section class="setting-row setting-row-stack">
                  <div class="setting-main full-width">
                    <div class="field-title">远端 Git 地址</div>
                    <div class="field-key">skill_git.local_remote_url</div>
                    <div class="setting-meta">
                      <span>Skill 保存先写入服务器本地 skills-repo 工作区；发布/手动同步会先写入本地 Docker/Gitea 主线，再尽量 mirror 到这里配置的外部仓库。外部 mirror 失败不会阻断运行节点下发。</span>
                    </div>
                    <a-input
                      v-model="skillGitConfig.local_remote_url"
                      placeholder="http://127.0.0.1:3000/maintainer/skills-repo-production.git"
                      class="mono-input row-stack-input"
                    />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">账号</div>
                    <div class="field-key">skill_git.username</div>
                  </div>
                  <div class="field-editor">
                    <a-input v-model="skillGitConfig.username" placeholder="skillforge" class="mono-input" />
                  </div>
                </section>

                <section class="setting-row">
                  <div class="setting-main">
                    <div class="field-title">密码 / Token</div>
                    <div class="field-key">skill_git.password</div>
                    <div class="setting-meta">
                      <span>用于 git push · 留空表示沿用已保存值</span>
                    </div>
                  </div>
                  <div class="field-editor">
                    <a-input-password v-model="skillGitConfig.password" placeholder="用于 git push" allow-clear class="mono-input" />
                  </div>
                </section>

                <div class="row-actions-bar">
                  <a-button class="ai-btn-like primary" type="primary" :loading="skillGitSaving" @click="saveSkillGitConfig">
                    保存 Skill Git 配置
                  </a-button>
                </div>
              </div>
            </div>
            <aside class="col-side">
              <div class="help-card">
                <div class="help-card-title">说明</div>
                <div class="help-text">
                  <p>这里放低频、管理员向的 Skill 仓库维护动作，避免日常 Skill 列表里混入运维按钮。</p>
                  <p><b>Git 扫描</b>只做登记，不做物理删除；物理删除由删除 Skill 流程负责。</p>
                  <p><b>导入 zip</b>适合迁移或从外部环境带入 Skill。</p>
                  <p><b>本地 Docker/Gitea</b>是 Skill 版本主线；这里的外部仓库只做 mirror 备份，和 110 项目代码仓、OpenClaw 节点地址分开管理。</p>
                </div>
              </div>
            </aside>
          </div>
        </a-tab-pane>

        <!-- ═══ Tab 5: 自动优化（scope: system） ═══ -->
        <a-tab-pane v-if="showSystemTabs" key="optimizer" title="自动优化">
          <div class="tab-body">
            <div class="settings-list">
              <section class="setting-row">
                <div class="setting-main">
                  <div class="field-title">启用 Nightly 优化</div>
                  <div class="field-key">optimizer.nightly_enabled</div>
                  <div class="setting-meta">
                    <span>每日 02:00 自动为 active 状态的 Skill 运行优化</span>
                  </div>
                </div>
                <div class="field-editor field-editor-inline">
                  <a-switch v-model="optimizerConfig.nightly_enabled" @change="saveOptimizerConfig" />
                </div>
              </section>

              <section class="setting-row">
                <div class="setting-main">
                  <div class="field-title">自动晋升</div>
                  <div class="field-key">optimizer.auto_promote</div>
                  <div class="setting-meta">
                    <span>Shadow 通过后直接 promote · 关闭时需人工审批</span>
                  </div>
                </div>
                <div class="field-editor field-editor-inline">
                  <a-switch v-model="optimizerConfig.auto_promote" @change="saveOptimizerConfig" />
                </div>
              </section>

              <section class="setting-row">
                <div class="setting-main">
                  <div class="field-title">默认迭代数</div>
                  <div class="field-key">optimizer.default_iterations</div>
                  <div class="setting-meta">
                    <span>5-50</span>
                  </div>
                </div>
                <div class="field-editor">
                  <a-input-number v-model="optimizerConfig.default_iterations" :min="5" :max="50" @change="saveOptimizerConfig" hide-button />
                </div>
              </section>

              <section class="setting-row">
                <div class="setting-main">
                  <div class="field-title">单次 Token 预算</div>
                  <div class="field-key">optimizer.max_tokens_per_session</div>
                  <div class="setting-meta">
                    <span>≥ 50000</span>
                  </div>
                </div>
                <div class="field-editor">
                  <a-input-number v-model="optimizerConfig.max_tokens_per_session" :min="50000" :step="50000" @change="saveOptimizerConfig" hide-button />
                </div>
              </section>

              <section class="setting-row">
                <div class="setting-main">
                  <div class="field-title">月 Token 上限</div>
                  <div class="field-key">optimizer.monthly_token_limit</div>
                  <div class="setting-meta">
                    <span>≥ 100000</span>
                  </div>
                </div>
                <div class="field-editor">
                  <a-input-number v-model="optimizerConfig.monthly_token_limit" :min="100000" :step="100000" @change="saveOptimizerConfig" hide-button />
                </div>
              </section>
            </div>
          </div>
        </a-tab-pane>

        <!-- ═══ Tab 6: 其他配置（scope: system） ═══ -->
        <a-tab-pane v-if="showSystemTabs" key="other" title="其他配置">
          <div class="tab-body">
            <a-collapse :default-active-key="['kv']" :bordered="false" class="other-config-collapse">
              <a-collapse-item key="kv" header="配置项（全局 key-value 扩展）">
                <div class="collapse-inner">
                  <div class="collapse-toolbar">
                    <a-button class="ai-btn-like" size="small" @click="showAddConfig = true"><icon-plus /> 添加</a-button>
                  </div>
                  <a-spin :loading="configLoading" class="collapse-spin">
                    <div v-if="otherConfigs.length" class="config-list">
                      <div v-for="item in otherConfigs" :key="item.key" class="config-item">
                        <div class="config-header">
                          <span class="config-key">{{ item.key }}</span>
                          <a-space size="mini">
                            <a-button class="ai-btn-like" size="mini" @click="saveConfig(item)">保存</a-button>
                            <a-popconfirm content="确认删除？" @ok="deleteConfig(item.key)">
                              <a-button class="ai-btn-like danger" size="mini">删除</a-button>
                            </a-popconfirm>
                          </a-space>
                        </div>
                        <a-textarea
                          :model-value="JSON.stringify(item.value, null, 2)"
                          :auto-size="{ minRows: 1, maxRows: 4 }"
                          class="config-textarea"
                          @change="(val) => item._editValue = val"
                        />
                      </div>
                    </div>
                    <SfEmptyState v-else title="系统配置" description="暂无配置项" hint="当前分类下没有可展示的系统配置。" icon="grid" />
                  </a-spin>
                </div>
              </a-collapse-item>
              <a-collapse-item key="sysinfo" header="系统信息（版本 / 部署 / 依赖状态）">
                <div class="collapse-inner">
                  <a-spin :loading="sysInfoLoading" class="collapse-spin">
                    <div class="sysinfo-list">
                      <div class="sysinfo-row">
                        <span class="sysinfo-label">版本</span>
                        <span class="sysinfo-value">
                          <code>{{ sysInfo.version || '-' }}</code>
                          <span v-if="sysInfo.build" class="sysinfo-sub">build {{ sysInfo.build }}</span>
                        </span>
                      </div>
                      <div class="sysinfo-row"><span class="sysinfo-label">最近部署</span><span class="sysinfo-value sysinfo-mono">{{ sysInfo.last_deploy || '-' }}</span></div>
                      <div class="sysinfo-row"><span class="sysinfo-label">最近提交</span><span class="sysinfo-value sysinfo-mono">{{ sysInfo.last_commit || '-' }}</span></div>
                      <div class="sysinfo-row"><span class="sysinfo-label">Python</span><span class="sysinfo-value sysinfo-mono">{{ sysInfo.python || '-' }}</span></div>
                      <div class="sysinfo-row"><span class="sysinfo-label">运行平台</span><span class="sysinfo-value sysinfo-mono">{{ sysInfo.platform || '-' }}</span></div>
                      <div class="sysinfo-row"><span class="sysinfo-label">主机名</span><span class="sysinfo-value sysinfo-mono">{{ sysInfo.host || '-' }}</span></div>
                      <div class="sysinfo-row"><span class="sysinfo-label">运行时间</span><span class="sysinfo-value">{{ formatUptime(sysInfo.uptime_seconds) }}</span></div>
                      <div class="sysinfo-row">
                        <span class="sysinfo-label">数据库</span>
                        <span class="sysinfo-value">
                          <span class="status-pill" :class="sysInfo.db_status === 'ok' ? 'ok' : 'bad'">
                            <span class="status-dot"></span>{{ sysInfo.db_version || (sysInfo.db_status === 'ok' ? '正常' : '异常') }}
                          </span>
                        </span>
                      </div>
                      <div class="sysinfo-row">
                        <span class="sysinfo-label">Redis</span>
                        <span class="sysinfo-value">
                          <span class="status-pill" :class="redisStatusClass">
                            <span class="status-dot"></span>{{ sysInfo.redis_version || sysInfo.redis_status || '-' }}
                          </span>
                        </span>
                      </div>
                      <div class="sysinfo-row">
                        <span class="sysinfo-label">Gitea</span>
                        <span class="sysinfo-value">
                          <span class="status-pill" :class="giteaStatusClass">
                            <span class="status-dot"></span>{{ sysInfo.gitea_version || sysInfo.gitea_status || '-' }}
                          </span>
                        </span>
                      </div>
                      <div v-if="sysInfo.skill_repo_skills != null" class="sysinfo-row">
                        <span class="sysinfo-label">Skill 仓库</span>
                        <span class="sysinfo-value">{{ sysInfo.skill_repo_skills }} 个 Skill / {{ sysInfo.skill_repo_commits }} 次提交</span>
                      </div>
                    </div>
                  </a-spin>
                </div>
              </a-collapse-item>
              <a-collapse-item key="weekly" header="周报（生成本周执行统计）">
                <div class="collapse-inner">
                  <a-button class="ai-btn-like primary" type="primary" size="small" :loading="weeklyLoading" @click="loadWeekly">生成本周报告</a-button>
                  <div v-if="weekly" class="weekly-stats">
                    <div class="weekly-row"><span class="weekly-label">总执行</span><span class="weekly-value">{{ weekly.total_runs }}</span></div>
                    <div class="weekly-row"><span class="weekly-label">成功率</span><span class="weekly-value">{{ weekly.success_rate }}%</span></div>
                    <div class="weekly-row"><span class="weekly-label">活跃 Skill</span><span class="weekly-value">{{ weekly.active_skills }}</span></div>
                  </div>
                </div>
              </a-collapse-item>
            </a-collapse>
          </div>
        </a-tab-pane>
      </a-tabs>
    </a-card>

    <!-- 添加配置弹窗 -->
    <a-modal v-model:visible="showAddConfig" title="添加配置项" @ok="handleAddConfig" :width="'min(90vw, 480px)'">
      <a-form layout="vertical">
        <a-form-item label="Key"><a-input v-model="newConfig.key" placeholder="如 avg_minutes_per_execution" class="mono-input" /></a-form-item>
        <a-form-item label="Value">
          <a-textarea v-model="newConfig.value" :auto-size="{ minRows: 2 }" placeholder="15" class="config-textarea" />
        </a-form-item>
      </a-form>
    </a-modal>

  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { dashboardApi as rawDashboardApi, skillApi as rawSkillApi, skillBatchApi } from '@/api'
import { IconPlus, IconCopy, IconRefresh, IconScan, IconUpload } from '@arco-design/web-vue/es/icon'
import request from '@/api/request'
import MonacoEditor from '@/components/editor/MonacoEditor.vue'
import { SfEmptyState } from '@/components/common'
import { confirmAction } from '@/utils/confirmDelete'

// scope 控制：'ai' 只显示 AI 相关 tab（编辑器/AI全局/编程助手）
//              'system' 只显示系统相关 tab（自动优化/其他配置）
//              'all' 全部显示（默认，向后兼容）
const props = defineProps<{ scope?: 'ai' | 'system' | 'all' }>()
const router = useRouter()
const scopeValue = computed(() => props.scope || 'all')
const showAiTabs = computed(() => scopeValue.value === 'ai' || scopeValue.value === 'all')
const showSystemTabs = computed(() => scopeValue.value === 'system' || scopeValue.value === 'all')
const pageTitle = computed(() => {
  if (scopeValue.value === 'ai') return 'AI 配置'
  if (scopeValue.value === 'system') return '系统设置'
  return '系统配置'
})
const pageSubtitle = computed(() => {
  if (scopeValue.value === 'ai') return '编辑器补全 · 全局 AI · 向量/RAG · 编程助手 · 修改即生效，敏感项需 2 位管理员复签'
  if (scopeValue.value === 'system') return 'Skill 仓库、自动优化与系统信息 · 修改即生效，敏感项需 2 位管理员复签'
  return '采集、AI 与监控治理参数 · 修改即生效，敏感项需 2 位管理员复签'
})
const defaultTab = computed(() => {
  if (scopeValue.value === 'system') return 'skill_tools'
  return 'editor'
})
const activeTab = ref(defaultTab.value)

function copyPrompt(text: string) {
  if (!text) { Message.warning('内容为空'); return }
  navigator.clipboard.writeText(text).then(
    () => Message.success('已复制到剪贴板'),
    () => Message.error('复制失败'),
  )
}

const dashboardApi: any = rawDashboardApi
const skillApi: any = rawSkillApi

// ── AI 编辑器补全配置 ──
const aiConfig = reactive({
  ai_enabled: true,
  api_base: '',
  api_key: '',
  model: 'deepseek-chat',
  max_tokens: 200,
  temperature: 0.3,
  trigger: 'onIdle',
  cache_enabled: true,
  cache_ttl: 3600,
  system_prompt: '',
})
const aiSaving = ref(false)

function isSecretConfigKey(key: string): boolean {
  return /(api_key|app_key|token|secret|password|credential)/i.test(key)
}

function shouldSkipSecretSave(key: string, value: unknown): boolean {
  return isSecretConfigKey(key) && String(value ?? '').trim() === ''
}

function loadAiFromConfigs(allConfigs: Array<Record<string, unknown>>) {
  allConfigs.forEach((item: Record<string, unknown>) => {
    const short = String(item.key || '').replace('editor.', '')
    if (short in aiConfig) (aiConfig as Record<string, unknown>)[short] = item.value
  })
}

async function saveAiConfig() {
  aiSaving.value = true
  try {
    for (const [key, value] of Object.entries(aiConfig)) {
      if (shouldSkipSecretSave(`editor.${key}`, value)) continue
      await request.put(`/system-config/editor.${key}`, { value })
    }
    Message.success('编辑器 AI 配置已保存')
    await loadConfigs()
  } catch (e: any) { Message.error(e?._message || '保存失败') }
  finally { aiSaving.value = false }
}

// ── AI 全局配置 ──
const globalAiConfig = reactive({
  provider: 'custom',
  api_base: '',
  api_key: '',
  model: 'deepseek-chat',
  max_tokens: 2000,
  temperature: 0.3,
  timeout: 30,
})
const cheapAiConfig = reactive({
  api_base: '',
  api_key: '',
  model: 'deepseek-chat',
  max_tokens: 2000,
  temperature: 0.2,
  timeout: 30,
  service_conversion_ai_enabled: false,
})
const globalAiSaving = ref(false)

function loadGlobalAiFromConfigs(allConfigs: Array<Record<string, unknown>>) {
  allConfigs.forEach((item: Record<string, unknown>) => {
    const key = String(item.key || '')
    if (key.startsWith('ai.cheap.')) {
      const short = key.replace('ai.cheap.', '')
      if (short in cheapAiConfig) (cheapAiConfig as Record<string, unknown>)[short] = item.value
      return
    }
    if (key === 'project.service_conversion.ai_enabled') {
      cheapAiConfig.service_conversion_ai_enabled = Boolean(item.value)
      return
    }
    const short = key.replace('ai.', '')
    if (short in globalAiConfig) (globalAiConfig as Record<string, unknown>)[short] = item.value
  })
}

async function saveGlobalAiConfig() {
  globalAiSaving.value = true
  try {
    for (const [key, value] of Object.entries(globalAiConfig)) {
      if (shouldSkipSecretSave(`ai.${key}`, value)) continue
      await request.put(`/system-config/ai.${key}`, { value })
    }
    for (const [key, value] of Object.entries(cheapAiConfig)) {
      if (key === 'service_conversion_ai_enabled') continue
      if (shouldSkipSecretSave(`ai.cheap.${key}`, value)) continue
      await request.put(`/system-config/ai.cheap.${key}`, { value })
    }
    await request.put('/system-config/project.service_conversion.ai_enabled', { value: cheapAiConfig.service_conversion_ai_enabled })
    Message.success('全局 AI 配置已保存')
    await loadConfigs()
  } catch (e) { Message.error(String((e as Record<string, unknown>)?._message || '保存失败')) }
  finally { globalAiSaving.value = false }
}


// ── 向量模型 / RAG 检索配置 ──
type ModelOption = {
  value: string
  label: string
  dim?: number
  maxTokens?: number
  hint?: string
}
type EmbeddingProviderOption = {
  value: string
  label: string
  apiBase: string
  defaultModel: string
  keyPlaceholder: string
  models: ModelOption[]
  requiresKey?: boolean
}

const embeddingProviders: EmbeddingProviderOption[] = [
  {
    value: 'siliconflow',
    label: 'SiliconFlow 硅基流动（推荐）',
    apiBase: 'https://api.siliconflow.cn/v1',
    defaultModel: 'Qwen/Qwen3-Embedding-8B',
    keyPlaceholder: '填 SiliconFlow API Key',
    models: [
      { value: 'Qwen/Qwen3-Embedding-8B', label: 'Qwen3 Embedding 8B · 高质量长文档', dim: 4096, maxTokens: 32768, hint: 'Knowledge LightRAG / 多部门文档' },
      { value: 'Qwen/Qwen3-Embedding-4B', label: 'Qwen3 Embedding 4B · 均衡', dim: 2560, maxTokens: 32768, hint: '质量与成本均衡' },
      { value: 'Qwen/Qwen3-Embedding-0.6B', label: 'Qwen3 Embedding 0.6B · 快速', dim: 1024, maxTokens: 32768, hint: '低成本大批量索引' },
      { value: 'BAAI/bge-m3', label: 'BAAI/bge-m3 · RAG 推荐（非 Pro）', dim: 1024, maxTokens: 8192, hint: '中文/英文/长文档' },
      { value: 'BAAI/bge-large-zh-v1.5', label: 'BAAI/bge-large-zh-v1.5 · 中文短文本', dim: 1024, maxTokens: 512, hint: '中文 FAQ/标题' },
      { value: 'BAAI/bge-large-en-v1.5', label: 'BAAI/bge-large-en-v1.5 · 英文短文本', dim: 1024, maxTokens: 512, hint: '英文资料' },
      { value: 'netease-youdao/bce-embedding-base_v1', label: '有道 BCE embedding · 轻量', dim: 768, maxTokens: 512, hint: '轻量检索' },
    ],
  },
  {
    value: 'ollama',
    label: 'Ollama 本地（免 Key）',
    apiBase: 'http://127.0.0.1:11434/v1',
    defaultModel: 'bge-m3',
    keyPlaceholder: 'Ollama 本地不需要 API Key',
    requiresKey: false,
    models: [
      { value: 'bge-m3', label: 'bge-m3 · 本地 RAG 验证', dim: 1024, maxTokens: 8192, hint: '中文/英文/长文档' },
    ],
  },
  {
    value: 'openai',
    label: 'OpenAI',
    apiBase: 'https://api.openai.com/v1',
    defaultModel: 'text-embedding-3-large',
    keyPlaceholder: 'sk-...',
    models: [
      { value: 'text-embedding-3-large', label: 'text-embedding-3-large · 高质量', dim: 3072, maxTokens: 8191, hint: '高质量' },
      { value: 'text-embedding-3-small', label: 'text-embedding-3-small · 低成本', dim: 1536, maxTokens: 8191, hint: '低成本' },
    ],
  },
  {
    value: 'custom',
    label: 'OpenAI 兼容自定义',
    apiBase: '',
    defaultModel: '',
    keyPlaceholder: '自定义服务 API Key',
    models: [],
  },
]

const embeddingScenarios = [
  {
    value: 'knowledge_rag',
    label: '知识库 RAG',
    description: '部门 SOP、Skill、数据资产说明，默认推荐',
    models: { siliconflow: 'Qwen/Qwen3-Embedding-8B', ollama: 'bge-m3', openai: 'text-embedding-3-large' },
  },
  {
    value: 'budget_rag',
    label: '低成本 RAG',
    description: '数据量大、成本敏感，保持 8192 token 输入',
    models: { siliconflow: 'Qwen/Qwen3-Embedding-0.6B', ollama: 'bge-m3', openai: 'text-embedding-3-small' },
  },
  {
    value: 'zh_short_text',
    label: '中文短文本',
    description: 'FAQ、标题、短规则，不适合长文档默认',
    models: { siliconflow: 'BAAI/bge-large-zh-v1.5', ollama: 'bge-m3', openai: 'text-embedding-3-small' },
  },
  {
    value: 'en_short_text',
    label: '英文短文本',
    description: '英文说明、外部英文资料',
    models: { siliconflow: 'BAAI/bge-large-en-v1.5', ollama: 'bge-m3', openai: 'text-embedding-3-small' },
  },
]

const rerankerModelOptions: ModelOption[] = [
  { value: 'BAAI/bge-reranker-v2-m3', label: 'BAAI/bge-reranker-v2-m3 · 默认' },
  { value: 'netease-youdao/bce-reranker-base_v1', label: '有道 BCE reranker · 轻量' },
  { value: 'Qwen/Qwen3-Reranker-8B', label: 'Qwen3 Reranker 8B · 高质量' },
  { value: 'Qwen/Qwen3-Reranker-4B', label: 'Qwen3 Reranker 4B · 均衡' },
  { value: 'Qwen/Qwen3-Reranker-0.6B', label: 'Qwen3 Reranker 0.6B · 快速' },
]

const ragConfig = reactive({
  enabled: false,
})
const embeddingConfig = reactive({
  provider: 'siliconflow',
  scenario: 'knowledge_rag',
  api_base: 'https://api.siliconflow.cn/v1',
  api_key: '',
  model: 'Qwen/Qwen3-Embedding-8B',
  dim: 4096,
  timeout: 30,
})
const rerankerConfig = reactive({
  enabled: false,
  provider: 'siliconflow',
  api_base: 'https://api.siliconflow.cn/v1',
  api_key: '',
  model: 'BAAI/bge-reranker-v2-m3',
  timeout: 20,
})
const lightragConfig = reactive({
  mode: 'embedded',
  flavor: 'official',
  api_base: 'http://127.0.0.1:19621',
  api_key: '',
  timeout: 30,
  strict_server: true,
})
const doclingConfig = reactive({
  enabled: false,
  api_base: 'http://127.0.0.1:15001',
  timeout: 30,
})
const vlmConfig = reactive({
  provider: 'openai',
  api_base: '',
  api_key: '',
  model: '',
  timeout: 45,
})
const embeddingSaving = ref(false)
const embeddingTesting = ref(false)
const embeddingTestResult = ref<any>(null)
const lightragTesting = ref(false)
const lightragSyncing = ref(false)
const lightragTestResult = ref<any>(null)
const doclingTesting = ref(false)
const doclingTestResult = ref<any>(null)
const vlmTesting = ref(false)
const vlmTestResult = ref<any>(null)

const selectedEmbeddingProvider = computed(() =>
  embeddingProviders.find(item => item.value === embeddingConfig.provider) || embeddingProviders[0],
)
const embeddingModelOptions = computed(() => selectedEmbeddingProvider.value.models || [])
const embeddingKeyConfigured = computed(() => isSecretConfigured('ai.embedding.api_key'))
const embeddingProviderRequiresKey = computed(() => selectedEmbeddingProvider.value.requiresKey !== false)
const rerankerKeyConfigured = computed(() => isSecretConfigured('ai.reranker.api_key'))
const lightragKeyConfigured = computed(() => isSecretConfigured('knowledge.lightrag.api_key'))
const vlmKeyConfigured = computed(() => isSecretConfigured('knowledge.vlm.api_key'))
const embeddingModelHelp = computed(() => {
  const model = embeddingModelOptions.value.find(item => item.value === embeddingConfig.model)
  if (!model) return embeddingConfig.provider === 'custom' ? '请确认模型支持 OpenAI-compatible /embeddings' : '请选择模型'
  return `${model.hint || '向量模型'} · ${model.dim || '-'} 维 · 输入上限 ${model.maxTokens || '-'} tokens`
})

function recommendedModelForScenario(scenarioValue: string): string {
  const scenario = embeddingScenarios.find(item => item.value === scenarioValue)
  if (!scenario) return selectedEmbeddingProvider.value.defaultModel || '-'
  const models = scenario.models as Record<string, string>
  return models[embeddingConfig.provider] || selectedEmbeddingProvider.value.defaultModel || '-'
}

function applyEmbeddingModelMeta() {
  const model = embeddingModelOptions.value.find(item => item.value === embeddingConfig.model)
  if (model?.dim) embeddingConfig.dim = model.dim
}

function applyEmbeddingProviderPreset() {
  const provider = selectedEmbeddingProvider.value
  if (embeddingConfig.provider !== 'custom') {
    embeddingConfig.api_base = provider.apiBase
    embeddingConfig.model = recommendedModelForScenario(embeddingConfig.scenario) || provider.defaultModel
  }
  if (!embeddingConfig.model) embeddingConfig.model = provider.defaultModel
  applyEmbeddingModelMeta()
  if (embeddingConfig.provider === 'ollama') {
    embeddingConfig.api_key = ''
    rerankerConfig.enabled = false
  }
  if (rerankerConfig.provider === 'siliconflow' || embeddingConfig.provider === 'siliconflow') {
    rerankerConfig.provider = embeddingConfig.provider === 'siliconflow' ? 'siliconflow' : rerankerConfig.provider
    if (rerankerConfig.provider === 'siliconflow') rerankerConfig.api_base = 'https://api.siliconflow.cn/v1'
  }
}

function selectEmbeddingScenario(value: string) {
  embeddingConfig.scenario = value
  if (embeddingConfig.provider !== 'custom') {
    embeddingConfig.model = recommendedModelForScenario(value)
    applyEmbeddingModelMeta()
  }
}

function applyRerankerPreset() {
  rerankerConfig.provider = 'siliconflow'
  rerankerConfig.api_base = 'https://api.siliconflow.cn/v1'
}

function isSecretConfigured(key: string): boolean {
  return Boolean(configs.value.find(item => item.key === key)?.secret_configured)
}

function hasSecretValue(key: string, value: unknown): boolean {
  return String(value ?? '').trim().length > 0 || isSecretConfigured(key)
}

function loadEmbeddingFromConfigs(allConfigs: Array<Record<string, unknown>>) {
  allConfigs.forEach((item: Record<string, unknown>) => {
    const key = String(item.key || '')
    if (key === 'knowledge.rag.enabled') {
      ragConfig.enabled = Boolean(item.value)
    }
    if (key.startsWith('ai.embedding.')) {
      const short = key.replace('ai.embedding.', '')
      if (short in embeddingConfig) (embeddingConfig as Record<string, unknown>)[short] = item.value
    }
    if (key.startsWith('ai.reranker.')) {
      const short = key.replace('ai.reranker.', '')
      if (short in rerankerConfig) (rerankerConfig as Record<string, unknown>)[short] = item.value
    }
    if (key.startsWith('knowledge.lightrag.')) {
      const short = key.replace('knowledge.lightrag.', '')
      if (short in lightragConfig) (lightragConfig as Record<string, unknown>)[short] = item.value
    }
    if (key.startsWith('knowledge.docling.')) {
      const short = key.replace('knowledge.docling.', '')
      if (short in doclingConfig) (doclingConfig as Record<string, unknown>)[short] = item.value
    }
    if (key.startsWith('knowledge.vlm.')) {
      const short = key.replace('knowledge.vlm.', '')
      if (short in vlmConfig) (vlmConfig as Record<string, unknown>)[short] = item.value
    }
    if (key === 'knowledge.rag.strict_server') {
      lightragConfig.strict_server = Boolean(item.value)
    }
  })
  applyEmbeddingProviderPreset()
  if (!rerankerConfig.api_base && rerankerConfig.provider === 'siliconflow') rerankerConfig.api_base = 'https://api.siliconflow.cn/v1'
  if (!rerankerConfig.model) rerankerConfig.model = 'BAAI/bge-reranker-v2-m3'
}

async function saveEmbeddingConfig() {
  applyEmbeddingProviderPreset()
  if (!embeddingConfig.model.trim()) {
    Message.warning('请选择或填写向量模型')
    return
  }
  if (!embeddingConfig.api_base.trim()) {
    Message.warning('请填写向量 API 地址')
    return
  }
  if (embeddingProviderRequiresKey.value && !hasSecretValue('ai.embedding.api_key', embeddingConfig.api_key)) {
    Message.warning('首次配置当前向量供应商必须填写 API Key')
    return
  }
  const entries: Record<string, unknown> = {
    provider: embeddingConfig.provider,
    scenario: embeddingConfig.scenario,
    api_base: embeddingConfig.api_base.trim().replace(/\/$/, ''),
    api_key: embeddingConfig.api_key,
    model: embeddingConfig.model.trim(),
    dim: Number(embeddingConfig.dim) || 1024,
    timeout: Number(embeddingConfig.timeout) || 30,
  }
  const rerankEntries: Record<string, unknown> = {
    enabled: Boolean(rerankerConfig.enabled),
    provider: rerankerConfig.provider || embeddingConfig.provider,
    api_base: (rerankerConfig.api_base || embeddingConfig.api_base).trim().replace(/\/$/, ''),
    api_key: rerankerConfig.api_key,
    model: rerankerConfig.model,
    timeout: Number(rerankerConfig.timeout) || 20,
  }
  if (rerankerConfig.enabled && !rerankEntries.model) {
    Message.warning('启用 Reranker 后必须选择模型')
    return
  }
  if (ragConfig.enabled && lightragConfig.mode !== 'server') {
    Message.warning('启用向量知识库后请将检索部署模式设为 Server')
    return
  }
  if (lightragConfig.mode === 'server' && !lightragConfig.api_base.trim()) {
    Message.warning('启用 LightRAG Server 模式后必须填写 API 地址')
    return
  }
  if (doclingConfig.enabled && !doclingConfig.api_base.trim()) {
    Message.warning('启用 Docling 后必须填写 API 地址')
    return
  }
  if (doclingConfig.enabled && (!vlmConfig.api_base.trim() || !vlmConfig.model.trim() || !hasSecretValue('knowledge.vlm.api_key', vlmConfig.api_key))) {
    Message.warning('启用图文解析后必须填写 VLM API 地址、模型和 API Key')
    return
  }
  const lightragEntries: Record<string, unknown> = {
    mode: lightragConfig.mode,
    flavor: lightragConfig.flavor || 'official',
    api_base: lightragConfig.api_base.trim().replace(/\/$/, ''),
    api_key: lightragConfig.api_key,
    timeout: Number(lightragConfig.timeout) || 30,
  }
  const doclingEntries: Record<string, unknown> = {
    enabled: Boolean(doclingConfig.enabled),
    api_base: doclingConfig.api_base.trim().replace(/\/$/, ''),
    timeout: Number(doclingConfig.timeout) || 30,
  }
  const vlmEntries: Record<string, unknown> = {
    provider: vlmConfig.provider || 'openai',
    api_base: vlmConfig.api_base.trim().replace(/\/$/, ''),
    api_key: vlmConfig.api_key,
    model: vlmConfig.model.trim(),
    timeout: Number(vlmConfig.timeout) || 45,
  }
  embeddingSaving.value = true
  try {
    for (const [key, value] of Object.entries(entries)) {
      if (shouldSkipSecretSave(`ai.embedding.${key}`, value)) continue
      await request.put(`/system-config/ai.embedding.${key}`, { value })
    }
    for (const [key, value] of Object.entries(rerankEntries)) {
      if (shouldSkipSecretSave(`ai.reranker.${key}`, value)) continue
      await request.put(`/system-config/ai.reranker.${key}`, { value })
    }
    await request.put('/system-config/knowledge.rag.enabled', { value: Boolean(ragConfig.enabled) })
    for (const [key, value] of Object.entries(lightragEntries)) {
      if (shouldSkipSecretSave(`knowledge.lightrag.${key}`, value)) continue
      await request.put(`/system-config/knowledge.lightrag.${key}`, { value })
    }
    for (const [key, value] of Object.entries(doclingEntries)) {
      await request.put(`/system-config/knowledge.docling.${key}`, { value })
    }
    for (const [key, value] of Object.entries(vlmEntries)) {
      if (shouldSkipSecretSave(`knowledge.vlm.${key}`, value)) continue
      await request.put(`/system-config/knowledge.vlm.${key}`, { value })
    }
    await request.put('/system-config/knowledge.rag.strict_server', { value: Boolean(lightragConfig.strict_server) })
    Message.success('向量知识库 / LightRAG / 图文解析配置已保存')
    await loadConfigs()
  } catch (e) {
    Message.error(String((e as Record<string, unknown>)?._message || '保存失败'))
  } finally {
    embeddingSaving.value = false
  }
}

async function testEmbeddingConfig() {
  embeddingTesting.value = true
  embeddingTestResult.value = null
  try {
    const res: any = await request.post('/knowledge/rag/test-embedding', {
      provider: embeddingConfig.provider,
      api_base: embeddingConfig.api_base.trim().replace(/\/$/, ''),
      api_key: embeddingConfig.api_key,
      model: embeddingConfig.model.trim(),
      dim: Number(embeddingConfig.dim) || undefined,
      timeout: Number(embeddingConfig.timeout) || undefined,
    })
    embeddingTestResult.value = res
    if (res.ok) Message.success(`向量连接成功：${res.dimension} 维`)
  } catch (e) {
    const err = e as Record<string, unknown>
    const msg = String(err?._backendMessage || err?._message || '向量连接失败')
    embeddingTestResult.value = { ok: false, error: msg }
    Message.error(msg)
  } finally {
    embeddingTesting.value = false
  }
}

async function testLightragServer() {
  lightragTesting.value = true
  lightragTestResult.value = null
  try {
    const res: any = await request.post('/knowledge/rag/test-server', {
      api_base: lightragConfig.api_base.trim().replace(/\/$/, ''),
      api_key: lightragConfig.api_key,
      flavor: lightragConfig.flavor,
      timeout: Number(lightragConfig.timeout) || undefined,
    })
    lightragTestResult.value = res
    Message.success('LightRAG Server 连接成功')
  } catch (e) {
    const err = e as Record<string, unknown>
    const msg = String(err?._backendMessage || err?._message || 'LightRAG Server 连接失败')
    lightragTestResult.value = { ok: false, error: msg }
    Message.error(msg)
  } finally {
    lightragTesting.value = false
  }
}

async function testDoclingConfig() {
  doclingTesting.value = true
  doclingTestResult.value = null
  try {
    const res: any = await request.post('/knowledge/rag/test-docling', {
      enabled: Boolean(doclingConfig.enabled),
      api_base: doclingConfig.api_base.trim().replace(/\/$/, ''),
      timeout: Number(doclingConfig.timeout) || undefined,
    })
    doclingTestResult.value = res
    Message.success('Docling 连接成功')
  } catch (e) {
    const err = e as Record<string, unknown>
    const msg = String(err?._backendMessage || err?._message || 'Docling 连接失败')
    doclingTestResult.value = { ok: false, error: msg }
    Message.error(msg)
  } finally {
    doclingTesting.value = false
  }
}

async function testVlmConfig() {
  vlmTesting.value = true
  vlmTestResult.value = null
  try {
    const res: any = await request.post('/knowledge/rag/test-vlm', {
      provider: vlmConfig.provider,
      api_base: vlmConfig.api_base.trim().replace(/\/$/, ''),
      api_key: vlmConfig.api_key,
      model: vlmConfig.model.trim(),
      timeout: Number(vlmConfig.timeout) || undefined,
    })
    vlmTestResult.value = res
    Message.success('VLM 连接成功')
  } catch (e) {
    const err = e as Record<string, unknown>
    const msg = String(err?._backendMessage || err?._message || 'VLM 连接失败')
    vlmTestResult.value = { ok: false, error: msg }
    Message.error(msg)
  } finally {
    vlmTesting.value = false
  }
}

async function syncLightragServer() {
  if (lightragConfig.mode !== 'server') {
    Message.warning('请先保存为 Server 模式')
    return
  }
  lightragSyncing.value = true
  try {
    const res: any = await request.post('/knowledge/rag/sync-server', { limit: 500 })
    Message.success(`同步完成：成功 ${res.synced || 0}，失败 ${res.failed || 0}`)
  } catch (e) {
    Message.error(String((e as Record<string, unknown>)?._backendMessage || (e as Record<string, unknown>)?._message || '同步失败'))
  } finally {
    lightragSyncing.value = false
  }
}

// v2.11.2: MCP Servers 拆出到 /admin/mcp-servers，本文件不再承载相关状态。

// ── Skill 管理工具 ──
const skillScanLoading = ref(false)
const skillImportLoading = ref(false)
const skillGitSaving = ref(false)
const skillGitConfig = reactive({
  local_remote_url: '',
  username: '',
  password: '',
})

function loadSkillGitFromConfigs(allConfigs: Array<Record<string, unknown>>) {
  allConfigs.forEach((item: Record<string, unknown>) => {
    const short = String(item.key).replace('skill_git.', '')
    if (short in skillGitConfig) (skillGitConfig as Record<string, unknown>)[short] = item.value
  })
}

async function saveSkillGitConfig() {
  skillGitSaving.value = true
  try {
    for (const [key, value] of Object.entries(skillGitConfig)) {
      if (shouldSkipSecretSave(`skill_git.${key}`, value)) continue
      await request.put(`/system-config/skill_git.${key}`, { value })
    }
    Message.success('Skill Git 配置已保存')
    await loadConfigs()
  } catch (e) {
    Message.error(String((e as Record<string, unknown>)?._message || '保存失败'))
  } finally {
    skillGitSaving.value = false
  }
}

async function handleSkillRepoScan() {
  skillScanLoading.value = true
  try {
    const preview = await skillApi.scanRepo(true)
    if (!preview.imported?.length && !preview.errors?.length) {
      Message.info(`扫描完成：发现 ${preview.found || 0} 个仓库目录，全部已登记`)
      return
    }

    const lines: string[] = []
    if (preview.imported?.length) {
      lines.push(`将登记 ${preview.imported.length} 个仓库目录为 Skill：`)
      preview.imported.slice(0, 10).forEach((s: Record<string, unknown>) => {
        lines.push(`  • ${s.id} (${s.name}) — ${s.department || '-'}`)
      })
      if (preview.imported.length > 10) {
        lines.push(`  ... 还有 ${preview.imported.length - 10} 个`)
      }
    }
    if (preview.errors?.length) {
      lines.push('')
      lines.push(`${preview.errors.length} 个 Skill 解析失败，将跳过`)
    }
    lines.push('')
    lines.push(`已存在跳过：${preview.skipped?.length || 0} 个`)
    lines.push('确认后会补登记 DB；Git 仅提交本次登记目录内的未提交文件。')

    confirmAction({
      title: `扫描发现 ${preview.found || 0} 个仓库目录`,
      content: lines.join('\n'),
      okText: '确认入库',
      okStatus: 'warning',
      width: 520,
      onOk: async () => {
        skillScanLoading.value = true
        try {
          const result = await skillApi.scanRepo(false)
          const msg = `已注册 ${result.imported?.length || 0} 个 Skill` +
            (result.commit_sha ? ` · commit ${result.commit_sha.slice(0, 7)}` : '')
          Message.success(msg)
        } catch (e) {
          Message.error(String((e as Record<string, unknown>)?._message || '扫描入库失败'))
        } finally {
          skillScanLoading.value = false
        }
      },
    })
  } catch (e) {
    Message.error(String((e as Record<string, unknown>)?._message || '扫描失败'))
  } finally {
    skillScanLoading.value = false
  }
}

async function handleImportSkillPackage(options: any) {
  const file: File = options.fileItem?.file || options.file
  if (!file) return
  skillImportLoading.value = true
  try {
    const res: any = await skillBatchApi.importSkill(file)
    Message.success(`导入成功：${res.skill_id}（含 ${res.imported_files?.length || 0} 个文件）`)
  } catch (e) {
    Message.error(String((e as Record<string, unknown>)?._message || '导入失败'))
  } finally {
    skillImportLoading.value = false
  }
}

// ── Optimizer 配置 ──
const optimizerConfig = reactive({
  nightly_enabled: false,
  auto_promote: false,
  default_iterations: 20,
  max_tokens_per_session: 500000,
  monthly_token_limit: 5000000,
})

function loadOptimizerFromConfigs(allConfigs: Array<Record<string, unknown>>) {
  allConfigs.forEach((item: Record<string, unknown>) => {
    const short = String(item.key).replace('optimizer.', '')
    if (short in optimizerConfig) (optimizerConfig as Record<string, unknown>)[short] = item.value
  })
}

async function saveOptimizerConfig() {
  try {
    for (const [key, value] of Object.entries(optimizerConfig)) {
      await request.put(`/system-config/optimizer.${key}`, { value })
    }
    Message.success('优化器配置已保存')
  } catch (e) { Message.error(String((e as Record<string, unknown>)?._message || '保存失败')) }
}

// ── 通用配置 ──
const configs = ref<any[]>([])
const configLoading = ref(false)
const showAddConfig = ref(false)
const newConfig = reactive({ key: '', value: '' })

const otherConfigs = computed(() =>
  configs.value.filter(c =>
    !c.key.startsWith('editor.') &&
    !c.key.startsWith('ai.') &&
    !c.key.startsWith('coding_agent.') &&
    !c.key.startsWith('skill_git.') &&
    !c.key.startsWith('optimizer.')
  )
)

async function loadConfigs() {
  configLoading.value = true
  try {
    configs.value = await request.get('/system-config/')
    loadAiFromConfigs(configs.value)
    loadGlobalAiFromConfigs(configs.value)
    loadEmbeddingFromConfigs(configs.value)
    loadSkillGitFromConfigs(configs.value)
    loadOptimizerFromConfigs(configs.value)
  } catch { configs.value = [] }
  finally { configLoading.value = false }
}

async function saveConfig(item: any) {
  try {
    let val
    try { val = JSON.parse(item._editValue || JSON.stringify(item.value)) } catch { val = item._editValue }
    await request.put(`/system-config/${item.key}`, { value: val })
    Message.success('已保存')
    await loadConfigs()
  } catch (e: any) { Message.error(e._message || '保存失败') }
}

async function deleteConfig(key: string) {
  try {
    await request.delete(`/system-config/${key}`)
    Message.success('已删除')
    await loadConfigs()
  } catch (e: any) { Message.error(e._message || '删除失败') }
}

async function handleAddConfig() {
  if (!newConfig.key) { Message.warning('请输入 Key'); return }
  try {
    let val
    try { val = JSON.parse(newConfig.value) } catch { val = newConfig.value }
    await request.put(`/system-config/${newConfig.key}`, { value: val })
    Message.success('已添加')
    showAddConfig.value = false
    newConfig.key = ''
    newConfig.value = ''
    await loadConfigs()
  } catch (e: any) { Message.error(e._message || '添加失败') }
}

// ── 系统状态 ──
const sysInfo = ref<any>({})
const sysInfoLoading = ref(false)
const weekly = ref<any>(null)
const weeklyLoading = ref(false)

const redisStatusClass = computed(() => {
  const s = sysInfo.value.redis_status
  if (s === 'ok') return 'ok'
  if (s === '未配置') return 'muted'
  return 'bad'
})

const giteaStatusClass = computed(() => {
  const s = sysInfo.value.gitea_status
  if (s === 'ok') return 'ok'
  if (s === '未配置') return 'muted'
  return 'bad'
})

async function loadSystemInfo() {
  sysInfoLoading.value = true
  try {
    sysInfo.value = await request.get<any>('/system-info') || {}
  } catch { sysInfo.value = {} }
  finally { sysInfoLoading.value = false }
}

function formatUptime(seconds: number) {
  if (!seconds && seconds !== 0) return '-'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}天 ${h}小时`
  if (h > 0) return `${h}小时 ${m}分钟`
  return `${m}分钟`
}

async function loadWeekly() {
  weeklyLoading.value = true
  try { weekly.value = await dashboardApi.weeklyReport() }
  catch (e: any) { Message.error(e._message || '获取失败') }
  finally { weeklyLoading.value = false }
}

// v2.11.2: 平台 API 注册表 拆出到 /admin/platform-apis，本文件不再承载相关状态。

onMounted(() => {
  loadSystemInfo()
  loadConfigs()
})
</script>

<style scoped>
.admin-settings-page {
  display: grid;
  gap: 16px;
  font-family: var(--ai-font-sans);
  color: var(--ai-ink-1);
}

/* 设计稿 page chrome 覆盖 */
.admin-settings-page :deep(.page-kicker) {
  color: var(--ai-ink-4);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
  text-transform: none;
  margin: 0 0 6px;
}
.admin-settings-page :deep(.page-title) {
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--ai-ink-1);
  margin: 0;
}
.admin-settings-page :deep(.page-subtitle) {
  margin: 4px 0 0;
  color: var(--ai-ink-3);
  font-weight: 400;
  font-size: 13px;
}
.admin-settings-page :deep(.page-list-card) {
  background: var(--ai-surface) !important;
  border: 1px solid var(--ai-border) !important;
  border-radius: var(--ai-radius) !important;
  box-shadow: none !important;
}
.admin-settings-page :deep(.page-list-card .arco-card-body) {
  padding: 0 !important;
}

.settings-shell {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  box-shadow: none;
}

/* 顶部按钮统一外形 */
.admin-settings-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary)) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  border: 1px solid var(--ai-border);
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.admin-settings-page :deep(.arco-btn.ai-btn-like:not(.arco-btn-primary):hover) {
  background: var(--ai-surface-2);
  border-color: var(--ai-border-2);
}
.admin-settings-page :deep(.arco-btn-primary.ai-btn-like) {
  height: 30px;
  padding: 0 12px;
  border-radius: 6px;
  background: var(--ai-ink-1);
  border-color: var(--ai-ink-1);
  color: var(--ai-surface);
  font-size: 12.5px;
  font-weight: 500;
  box-shadow: none;
}
.admin-settings-page :deep(.arco-btn-primary.ai-btn-like:hover) {
  background: var(--ai-ink-2);
  border-color: var(--ai-ink-2);
}
.admin-settings-page :deep(.arco-btn.ai-btn-like.danger) {
  color: var(--ai-bad);
  border-color: var(--ai-border);
}
.admin-settings-page :deep(.arco-btn.ai-btn-like.danger:hover) {
  background: var(--ai-bad-soft);
  border-color: var(--ai-bad-soft);
}
.admin-settings-page :deep(.arco-btn-size-mini.ai-btn-like) {
  height: 24px;
  padding: 0 8px;
  font-size: 11.5px;
}

/* Tab body 布局 */
.tab-body {
  padding: 4px 0;
}
.tab-body.two-col {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(260px, 320px);
  gap: 20px;
}
.col-main {
  min-width: 0;
}
.col-side {
  min-width: 0;
}

.settings-list {
  display: grid;
  gap: 10px;
}

.settings-section-title {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin: 0 0 10px;
}
.settings-section-title-spaced {
  margin-top: 24px;
}

/* 设计稿 AdminConfig: 单行配置卡片 ai-card 风格 */
.setting-row {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) minmax(300px, 420px);
  align-items: center;
  gap: 16px;
  padding: 14px 16px;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.setting-row:hover {
  border-color: var(--ai-border-2);
  box-shadow: var(--ai-shadow-1);
}
.setting-row-stack {
  grid-template-columns: 1fr;
}

.setting-main {
  min-width: 0;
}
.setting-main.full-width {
  width: 100%;
}

.field-title {
  font-weight: 600;
  font-size: 13.5px;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}

.field-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.field-key {
  margin-top: 2px;
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-4);
  word-break: break-all;
}

.setting-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin-top: 8px;
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.5;
  font-variant-numeric: tabular-nums;
}

.field-editor {
  display: grid;
  grid-template-columns: 1fr;
  align-items: center;
  gap: 8px;
}
.field-editor-inline {
  justify-items: end;
}

.row-stack-input {
  margin-top: 10px;
}

.row-actions-bar {
  margin-top: 14px;
  display: flex;
  justify-content: flex-end;
}

/* 状态 pill ：复用设计稿 ai-pill 模式 */
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 20px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  background: var(--ai-surface-2);
  color: var(--ai-ink-2);
}
.status-pill .status-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}
.status-pill.ok    { color: var(--ai-ok);   background: var(--ai-ok-soft); }
.status-pill.warn  { color: var(--ai-warn); background: var(--ai-warn-soft); }
.status-pill.bad   { color: var(--ai-bad);  background: var(--ai-bad-soft); }
.status-pill.info  { color: var(--ai-info); background: var(--ai-info-soft); }
.status-pill.muted { color: var(--ai-ink-4); background: var(--ai-surface-2); }

/* 提示词 monaco 包裹 */
.prompt-editor-wrap {
  position: relative;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  overflow: hidden;
  margin-top: 10px;
}


.embedding-guide {
  margin-bottom: 10px;
}

.scenario-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}

.scenario-card {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius-s);
  background: var(--ai-surface-2);
  padding: 12px;
  text-align: left;
  font-family: inherit;
  cursor: pointer;
  display: grid;
  gap: 5px;
  color: var(--ai-ink-2);
}

.scenario-card:hover,
.scenario-card.active {
  border-color: var(--ai-info);
  background: var(--ai-info-soft);
}

.scenario-title {
  color: var(--ai-ink-1);
  font-size: 13px;
  font-weight: 600;
}

.scenario-desc {
  color: var(--ai-ink-3);
  font-size: 12px;
  line-height: 1.5;
}

.scenario-card code {
  color: var(--ai-info);
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  word-break: break-all;
}

.two-field-editor {
  grid-template-columns: 1fr 1fr;
}

.embedding-actions {
  justify-content: flex-end;
}

.warn-text {
  color: var(--ai-warn);
}

/* AI 编程助手测试结果卡片 */
.test-result-card {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  padding: 12px 14px;
  background: var(--ai-surface);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.test-result-card.ok { border-color: var(--ai-ok-soft); background: var(--ai-ok-soft); }
.test-result-card.bad { border-color: var(--ai-bad-soft); background: var(--ai-bad-soft); }
.test-result-head {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
}
.test-result-meta {
  margin-left: auto;
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-3);
}
.test-result-detail {
  font-size: 12px;
  color: var(--ai-ink-2);
  line-height: 1.5;
}
.test-result-detail code {
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-1);
}
.test-result-extra {
  font-family: var(--ai-font-mono);
  font-size: 11px;
  color: var(--ai-ink-4);
}

/* 行内提示性 info 卡 */
.inline-info-card {
  border: 1px solid var(--ai-info-soft);
  background: var(--ai-info-soft);
  border-radius: var(--ai-radius);
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.inline-info-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ai-info);
}
.inline-info-body {
  font-size: 12px;
  color: var(--ai-ink-2);
}
.inline-info-body a {
  color: var(--ai-info);
  text-decoration: none;
  font-weight: 500;
}
.inline-info-body a:hover {
  text-decoration: underline;
}
.inline-info-body .sep {
  margin: 0 6px;
  color: var(--ai-ink-4);
}

/* 右侧帮助卡 */
.help-card {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  padding: 14px 16px;
  position: sticky;
  top: 12px;
}
.help-card-title {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--ai-ink-4);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 10px;
}
.help-text {
  font-size: 12.5px;
  color: var(--ai-ink-2);
  line-height: 1.7;
}
.help-text p {
  margin: 0 0 8px;
}
.help-text ul {
  padding-left: 18px;
  margin: 4px 0 8px;
}
.help-text li {
  margin-bottom: 2px;
}
.help-text code {
  font-family: var(--ai-font-mono);
  font-size: 11.5px;
  color: var(--ai-ink-1);
  background: var(--ai-surface-2);
  padding: 1px 5px;
  border-radius: 3px;
}
.help-text b {
  color: var(--ai-ink-1);
  font-weight: 600;
}

/* 其他配置 - 列表 */
.config-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.config-item {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  padding: 12px 14px;
  background: var(--ai-surface);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.config-item:hover {
  border-color: var(--ai-border-2);
  box-shadow: var(--ai-shadow-1);
}
.config-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.config-key {
  font-weight: 600;
  font-family: var(--ai-font-mono);
  font-size: 12.5px;
  color: var(--ai-ink-1);
}

.collapse-inner {
  padding: 4px 0 8px;
}
.collapse-toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 10px;
}
.collapse-spin {
  display: block;
}

/* 系统信息 */
.sysinfo-list {
  display: grid;
  gap: 0;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  overflow: hidden;
}
.sysinfo-row {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 12px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--ai-border);
  font-size: 12.5px;
  align-items: center;
}
.sysinfo-row:last-child {
  border-bottom: 0;
}
.sysinfo-row:hover {
  background: var(--ai-surface-2);
}
.sysinfo-label {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.sysinfo-value {
  color: var(--ai-ink-1);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.sysinfo-value code {
  font-family: var(--ai-font-mono);
  font-size: 12px;
  color: var(--ai-ink-1);
}
.sysinfo-mono {
  font-family: var(--ai-font-mono);
  font-size: 12px;
  color: var(--ai-ink-2);
}
.sysinfo-sub {
  color: var(--ai-ink-4);
  font-size: 11.5px;
}

/* 周报统计 */
.weekly-stats {
  margin-top: 12px;
  display: grid;
  gap: 0;
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  overflow: hidden;
}
.weekly-row {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 12px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--ai-border);
  font-size: 12.5px;
  align-items: center;
}
.weekly-row:last-child { border-bottom: 0; }
.weekly-label {
  color: var(--ai-ink-4);
  font-size: 11.5px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.weekly-value {
  color: var(--ai-ink-1);
  font-family: var(--ai-font-mono);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

/* arco tabs → ai-tabs 风格：36px 高，1.5px ink-1 active 下划线 */
.settings-shell :deep(.arco-tabs-nav) {
  padding: 0 28px;
  margin-bottom: 0;
  border-bottom: 1px solid var(--ai-border);
}
.settings-shell :deep(.arco-tabs-nav-type-rounded .arco-tabs-tab) {
  background: transparent !important;
  border: 0 !important;
  margin: 0 !important;
  padding: 0 12px !important;
  height: 36px !important;
  line-height: 36px !important;
  font-weight: 500 !important;
  font-size: 13px !important;
  color: var(--ai-ink-3) !important;
  letter-spacing: -0.005em !important;
  border-radius: 0 !important;
}
.settings-shell :deep(.arco-tabs-nav-type-rounded .arco-tabs-tab-active) {
  color: var(--ai-ink-1) !important;
  background: transparent !important;
  font-weight: 500 !important;
}
.settings-shell :deep(.arco-tabs-tab) {
  padding: 0 12px;
  height: 36px;
  font-weight: 500;
  font-size: 13px;
  color: var(--ai-ink-3);
  letter-spacing: -0.005em;
}
.settings-shell :deep(.arco-tabs-tab-active) {
  color: var(--ai-ink-1);
  font-weight: 500;
}
.settings-shell :deep(.arco-tabs-nav-ink) {
  background: var(--ai-ink-1);
  height: 1.5px;
}
.settings-shell :deep(.arco-tabs-content) {
  padding: 18px 28px;
}

/* 输入控件统一扁平化 */
.admin-settings-page :deep(.arco-input-wrapper),
.admin-settings-page :deep(.arco-select-view),
.admin-settings-page :deep(.arco-input-number),
.admin-settings-page :deep(.arco-textarea-wrapper) {
  border: 1px solid var(--ai-border);
  border-radius: 6px;
  background: var(--ai-surface);
  box-shadow: none !important;
  transition: border-color 0.15s ease;
}
.admin-settings-page :deep(.arco-input-wrapper:hover),
.admin-settings-page :deep(.arco-select-view:hover),
.admin-settings-page :deep(.arco-input-number:hover),
.admin-settings-page :deep(.arco-textarea-wrapper:hover) {
  border-color: var(--ai-border-2);
}
.admin-settings-page :deep(.arco-input-wrapper.arco-input-focus),
.admin-settings-page :deep(.arco-select-view-focus),
.admin-settings-page :deep(.arco-input-number-focus),
.admin-settings-page :deep(.arco-textarea-wrapper.arco-textarea-focus) {
  border-color: var(--ai-ink-3) !important;
  box-shadow: none !important;
}
.admin-settings-page :deep(.arco-input),
.admin-settings-page :deep(.arco-input-number-input),
.admin-settings-page :deep(.arco-textarea) {
  font-size: 12.5px;
  color: var(--ai-ink-1);
  background: transparent;
}
.admin-settings-page :deep(.arco-input::placeholder),
.admin-settings-page :deep(.arco-input-number-input::placeholder),
.admin-settings-page :deep(.arco-textarea::placeholder) {
  color: var(--ai-ink-4);
}
.admin-settings-page :deep(.mono-input .arco-input),
.admin-settings-page :deep(.mono-input .arco-input-number-input),
.admin-settings-page :deep(.config-textarea .arco-textarea) {
  font-family: var(--ai-font-mono);
  font-size: 12px;
}

/* a-switch 沉稳风格 */
.admin-settings-page :deep(.arco-switch-checked) {
  background-color: var(--ai-ink-1) !important;
}

/* a-select 弹出菜单 */
.admin-settings-page :deep(.arco-select-view-value) {
  font-size: 12.5px;
}

/* a-radio-button (radio-group type=button) */
.admin-settings-page :deep(.arco-radio-group-button) {
  background: var(--ai-surface-2);
  border-radius: 6px;
  padding: 2px;
}
.admin-settings-page :deep(.arco-radio-button) {
  font-size: 12px;
  height: 24px;
  line-height: 22px;
  padding: 0 10px;
  color: var(--ai-ink-3);
  background: transparent;
  border-radius: 5px;
  border: 0 !important;
}
.admin-settings-page :deep(.arco-radio-button:not(.arco-radio-checked):hover) {
  color: var(--ai-ink-1);
  background: transparent;
}
.admin-settings-page :deep(.arco-radio-button.arco-radio-checked) {
  background: var(--ai-surface);
  color: var(--ai-ink-1);
  font-weight: 500;
  box-shadow: var(--ai-shadow-1);
}

/* Collapse */
.admin-settings-page :deep(.other-config-collapse .arco-collapse-item) {
  border: 1px solid var(--ai-border);
  border-radius: var(--ai-radius);
  background: var(--ai-surface);
  margin-bottom: 10px;
  overflow: hidden;
}
.admin-settings-page :deep(.other-config-collapse .arco-collapse-item-header) {
  background: var(--ai-surface);
  border-bottom: 0 !important;
  padding: 12px 16px !important;
  font-size: 13px;
  font-weight: 600;
  color: var(--ai-ink-1);
  letter-spacing: -0.005em;
}
.admin-settings-page :deep(.other-config-collapse .arco-collapse-item-active .arco-collapse-item-header) {
  border-bottom: 1px solid var(--ai-border) !important;
}
.admin-settings-page :deep(.other-config-collapse .arco-collapse-item-content) {
  background: var(--ai-surface);
  padding: 14px 16px !important;
}
.admin-settings-page :deep(.other-config-collapse .arco-collapse-item-content-box) {
  padding: 0 !important;
}

/* a-tag → ai-pill 风格映射 */
.admin-settings-page :deep(.arco-tag) {
  height: 20px;
  line-height: 18px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  border: 1px solid transparent;
}
.admin-settings-page :deep(.arco-tag-color-arcoblue) {
  background: var(--ai-info-soft) !important;
  color: var(--ai-info) !important;
  border-color: transparent !important;
}
.admin-settings-page :deep(.arco-tag-color-green) {
  background: var(--ai-ok-soft) !important;
  color: var(--ai-ok) !important;
  border-color: transparent !important;
}
.admin-settings-page :deep(.arco-tag-color-red) {
  background: var(--ai-bad-soft) !important;
  color: var(--ai-bad) !important;
  border-color: transparent !important;
}
.admin-settings-page :deep(.arco-tag-color-orange),
.admin-settings-page :deep(.arco-tag-color-orangered) {
  background: var(--ai-warn-soft) !important;
  color: var(--ai-warn) !important;
  border-color: transparent !important;
}
.admin-settings-page :deep(.arco-tag-color-gray) {
  background: var(--ai-surface-2) !important;
  color: var(--ai-ink-2) !important;
  border-color: transparent !important;
}

/* a-empty 文字 */
.admin-settings-page :deep(.arco-empty-description) {
  color: var(--ai-ink-4);
  font-size: 12.5px;
}

@media (max-width: 960px) {
  .tab-body.two-col {
    grid-template-columns: 1fr;
  }
  .help-card {
    position: static;
  }
}
@media (max-width: 760px) {
  .setting-row {
    grid-template-columns: 1fr;
  }
  .field-editor {
    grid-template-columns: 1fr;
  }
}

/* Admin sweep utilities */
@media (max-width: 900px) {
  .tab-body.two-col,
  .settings-split {
    grid-template-columns: 1fr;
  }
  .field-editor,
  .setting-main {
    min-width: 0;
  }
}

</style>
