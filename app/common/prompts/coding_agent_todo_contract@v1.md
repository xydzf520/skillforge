10. **待办协议 (当用户要求输出待办/整改建议时)**:
   SkillForge 已提供正式待办构造能力：
   - `todo_build_dispatch`
   - `todo_build_review`

   原则：
   - 不要手写随意结构的 `todos` JSON
   - 优先用正式工具构造 payload
   - `review` 用于审批确认
   - `dispatch` 用于派发执行任务
   - `todos` 只表示 SkillForge 平台待办；Skill 运行完成时不直接推钉钉
   - 只有平台待办被用户点击通过后，`dispatch.tasks` 才由平台推送到执行人钉钉
   - `contract.json.output_schema.properties.todos.items` 必须声明完整 TodoSpec 结构，不能只写 `{ "type": "array" }`

   `todos[]` 结构至少包含：
   ```json
   {
     "kind": "review | dispatch",
     "title": "待办标题",
     "summary": "问题和建议摘要",
     "payload": {},
     "tasks": [{"executor": null, "content": "执行任务", "deadline": "可选 ISO 时间"}],
     "reviewers": ["user_id"],
     "reviewer_role": "biz_owner | operator | ai_engineer | admin | director",
     "sla_hours": 24,
     "decision_mode": "any_of | all_of | independent",
     "callback": {}
   }
   ```

   硬规则：
   - `kind` 和 `title` 必填
   - `reviewers` 或 `reviewer_role` 至少明确一个
   - `dispatch` 应包含 `tasks`
   - payload 面向运营决策，不是 debug 容器：只放关键指标、分析依据、建议动作、禁止动作、推荐决策、数据来源
   - 禁止把完整 `input_snapshot`、完整 `output_result`、页面原始响应或外部平台原始 JSON 塞进 `payload`
   - 如果需要 `payload.input` / `payload.output`，只能放小摘要；raw/debug 证据放报告 payload、data_proofs 或 execution debug 链路
   - 电商竞品/价格类待办必须写清商品金额、上下浮动 10% 的价格区间、同价位竞品数量和样例竞品；缺商品金额时写成数据缺口，不能只写“市场已采集”
   - `tasks[].deadline` 必须是 ISO 时间字符串，不要写“今日/明天/本周五”
   - 不要把执行任务和审批任务混在一条 payload 中
