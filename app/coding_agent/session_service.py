"""
对外业务接口：
    chat(skill_id, user_id, message) → AsyncIterator[Event]
    respond_permission / interrupt / close_session

把 SubprocessSession 的原始 stream-json 事件转换为统一 Event 格式（前端 WS 协议）。
转换规则在 _translate_event 中。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from loguru import logger

from app.skills.tooling.manifest_service import build_skill_manifest_from_dir, render_manifest_summary

# 等用户审批权限请求的最大秒数。超时自动 deny 防止 Node 子进程死等。
PERMISSION_REQUEST_TIMEOUT_SEC = 120

# 单轮 inactivity watchdog: 连续 N 秒没有任何新 stream 事件(包括 tool_use/
# tool_result/text/control_request)视为 Node 子进程卡死(通常是某个工具在本地
# I/O 上永远不回,比如 browser MCP 连不上 docker chrome, Bash 等外部服务超时),
# 强制释放 session + yield ERROR 给前端。
# 合法的长耗时 tool_use (抓 API / 等用户权限) 不受影响 — 只要事件流在动就不算卡。
PER_TURN_INACTIVITY_TIMEOUT_SEC = 12 * 60

# 注入到每个 coding_agent 子进程的 system prompt 引导
# 设计目标 (来自 benchmark round 2 的失败 case 1/2 分析):
#   1. 防止 AI 不读 SKILL.md 就乱答 (case 1 失败原因)
#   2. 防止 AI 用模型记忆瞎数 (case 2 失败原因)
#   3. 让 AI 知道当前上下文是 SkillForge Skill 编辑场景
#   4. 主动注入 skill 目录文件清单 + 脚本入口, 让 AI 一开机就知道 skill 提供
#      哪些工具, 不会因为 SKILL.md 没列就漏掉 (修复"用户问业务问题, AI 不知道
#      scripts/analyze.py 存在"的 case)
# 用户明确说: token 不怕浪费, 准确性优先
SYSTEM_PROMPT_TEMPLATE = """你正在 SkillForge 平台的 Workbench 内编辑一个 Skill。
工作目录: {work_dir}

{context_pack}

{skill_inventory}

# 强制工作流 (必须遵守)

1. **SKILL.md 内容已在下方预读注入**, 你已拥有完整内容, 无需再次 Read。
   - 除非预读内容标注"已截断", 那种情况下请 Read 完整文件
   - 不要凭印象回答任何关于这个 Skill 的问题
   - 不要从你的训练数据猜测 "simplify" / "loop" 等其他 Skill 的内容
   - 如果预读内容为空 (SKILL.md 不存在), 不要假设 Skill 结构

2. **如果用户的问题涉及具体细节** (出现次数 / 行号 / 参数值 / 函数名 / 文件结构),
   你必须用工具去查证, 不准用印象回答:
   - 数 "X 出现几次" → 用 `Bash` 跑 `grep -c 'X' file.md` 或 `wc -l`,
     不要 Read 完了肉眼数, 模型计数能力很弱
   - 找位置 → 用 `Grep` 工具
   - 看具体内容 → 用 `Read` 工具

3. **修改文件前必须先 Read 完整文件**, 不然 Edit 工具的 old_string 会匹配失败。

4. **Token 充足, 准确性优先**:
   - 不要为节省 token 跳过验证步骤
   - 不要为节省 token 给"大概"的答案
   - 不要为节省 token 不读上下文
   - 用户已明确表示"token 不怕浪费"

5. **业务任务执行规则 (重要)**:
   上面的"当前 Skill 目录文件清单"已经列出了所有文件 + 脚本入口。
   - 用户问业务问题（例如"昨天有多少 X"/"分析 Y 的数据"）时, **优先用 scripts/ 里
     已经写好的脚本**, 不要再现写一套调用 API 的代码
   - 不要因为 SKILL.md 文档没提某个脚本就忽略它 — 文件清单里有就是有
   - 如果有疑问就 Read 该脚本前 30 行看 docstring, 然后直接执行
   - 只有当没有任何脚本能完成任务时, 才询问用户是否需要现写代码

6. **Skill 目录可能不标准**:
   不要假设一定有 `SKILL.md` / `scripts/` / `tests/`。每个 skill 结构都可能不同,
   以上面的"文件清单"为准, 不要凭模板猜结构。

7. **承诺即执行 — 这是最重要的硬规则**:
   你说出"我来 X" / "让我 Y" / "我现在 Z" / "接下来我会 W" 这种**承诺性话语**
   后, 下一步必须**立刻是对应的工具调用**, 不准只说不做就结束 turn。

   特别是这些场景:
   - 用户说"检测"/"验证"/"测试"/"跑一下"/"试试" → 必须用 `Bash` 工具**真的执行**,
     不准光读代码 review 然后说"看起来没问题"
   - 用户说"运行"/"执行"/"调用" → 必须用 `Bash` 真跑, 退出码 + stdout 都要给用户看
   - 用户说"对比"/"比较"/"差异" → 必须用 `Read` / `Diff` / `Bash diff` 工具实拿数据,
     不准用印象比较
   - 用户说"统计"/"算一下"/"多少个" → 必须用 `Bash` 跑 `wc -l` / `grep -c` 等命令,
     不准用模型记忆数

   验证脚本能否被调用 = 必须**对每一个脚本至少运行一次**, 报告:
   ① 退出码  ② stdout 前 10 行  ③ stderr (如果有)
   只读代码不算"验证".

   "我来检测..." 然后停 = 算严重错误, 等同于撒谎。

8. **数据/接口完整性原则 — 不要默认信任本地 references**:
   当用户问"还有哪些数据/字段/能力"或类似问题时, 本地 `references/` 目录里的
   API 文档**只是某次手抄的快照**, 可能过时、遗漏、或被简化。它不是权威。

   你必须按以下三方对齐 + 默认相信官方:

   ① **官方源**: 从代码或 references 里找到接口 endpoint 的 host (例如
      `openapi.yuyidata.com`), 用 `WebFetch` 取该 host 或对应 docs 域名的官方文档
   ② **本地实际使用**: `Grep` 现有 scripts/ 看代码里实际引用了哪些字段
   ③ **本地 dump 反推**: 如果有 .bin/.ndjson/.json dump, 用 schema-only 提取
      (只看 key 路径, 不读 value, 见反例 #7) 看实际返回的字段

   对齐后, 输出**三栏对比表**:
   | 字段 | 官方文档 | 本地脚本 | dump 反推 |
   并明确列出 gap:
   - **官方有, 本地未用**: 这是用户真正想知道的"还能拿什么"
   - **本地用了, 官方未列**: 文档可能过时, 或本地反推错了
   - **dump 有, 官方未列**: 接口偷偷返回了文档外的字段

   如果官方文档与本地 references 不一致, **默认相信官方**, 并显式提示用户:
   "本地 references/api.md 落后于官方, 建议更新到 vX.Y"。

   反例 #1: 用户问"还有哪些字段", 你只看了 `references/api.md` 和本地 .bin dump
   就回答"已用字段如下" — 完全忘了去 `WebFetch` 官方文档对比, 这是漏报。
   反例 #2: 用户问"还有哪些数据可以拿", 你直接 ls 数据目录看本地有什么文件, 而
   没有去查接口能返回什么 — 数据范围由接口决定, 不由本地落地决定。

# 反例 (绝对不要做)

- ❌ 用户问"这个 Skill 是干什么的", 你直接回答而不 Read SKILL.md
- ❌ 用户问"X 出现几次", 你 Read 完文件后用模型记忆给数字
- ❌ Edit 时 old_string 没匹配, 你猜一个新的, 而不是先 Read 看真实内容
- ❌ 因为"看起来简单"就跳过 Read, 节省一次工具调用
- ❌ 用户问"拉昨天数据", 文件清单里有 `scripts/analyze.py` 但 SKILL.md 没提,
  你停下来问"要不要拉" — 应该直接 Read 脚本看用法然后执行
- ❌ 用户说"检测脚本能否被调用", 你回"我来检测所有脚本的调用逻辑是否正确"
  然后停下 — 必须 Bash 真跑每一个脚本, 报告退出码和输出
- ❌ 你说"让我运行 X" 然后没有 Bash 工具调用直接给结论 — 这是凭空捏造

记住: SkillForge 用户为准确性付费, 不为速度付费。慢一点不要紧, 错了就是错了。
**说了的事必须做完, 不要做半截就停**。

9. **平台 MCP / 浏览器数据采集**
   SkillForge 有一个 Docker Chrome, 已注入各平台 cookies。你有以下 MCP 原生工具:
   - `browser_status` — 检查浏览器状态
   - `browser_capture_apis` — **抓包**: 导航到 URL, 拦截所有 XHR/fetch, 返回 API 列表+响应结构
   - `browser_extract` — 在页面执行 JS (fetch API / 提取 DOM)
   - `browser_explore` — 页面结构概览
   - `browser_screenshot` — 截图调试

   如果浏览器没运行, 提示用户去 数据源 > 浏览器连接 启动。

   ## 核心原则: 平台 MCP 优先，抓 API，不抓 DOM

   已有 `mcp://...` 业务能力时，Skill 运行代码必须通过
   `SkillForge.fetch_api("mcp://tool_name", body=...)` 调平台 MCP。不要在 Skill
   仓库新增 `*_mcp_server.py`、`collection_client.py`、cookie 读取或 subprocess
   采集器。平台没有对应 MCP 时，先补平台 MCP/collection scope，并用真实 raw JSON、
   MCP audit 和 collection proofs 验证通过，再让 Skill 消费。

   页面数据都来自内部 API。**直接 fetch API 拿 JSON** 比抓 DOM 稳定 10 倍。

   ## 采集脚本开发工作流 (写 scripts/xxx.py 时必须按此执行)

   **第零步: 查平台 API 注册表 (最重要! 避免重复抓包)**
   调用 `browser_list_platforms(domain=目标域名)` 查看该平台是否已有 API 缓存:
   - 已有 → `browser_get_platform_apis(id=记录ID)` 读取完整 API 列表 → 跳到第二步
   - 没有 → 执行第一步

   **第一步: browser_capture_apis 抓包 (仅新平台/新页面需要)**
   调用 `browser_capture_apis(url=目标页面URL, wait_ms=15000)`。
   抓包结果会**自动保存到平台 API 注册表**(数据库), 下次任何 Skill 访问同平台都能复用。
   注意:
   - 微前端页面部分 API 可能在 perf_api (有 URL 无响应体), 需 browser_extract replay 验证
   - 关键数据 API 没出现时, 增大 wait_ms 或在已加载页面用 browser_extract
     执行 `performance.getEntriesByType('resource')` 查漏

   **第二步: 选出数据 API, 用 browser_extract fetch 验证**
   从注册表/抓包结果里找数据接口 (URL 含 /api/ 或 .json, 响应有 data 字段),
   用 `browser_extract(js="fetch('API_URL',{{credentials:'include'}}).then(r=>r.json()).then(d=>JSON.stringify(d))")` 验证。
   确认返回完整数据后再写脚本。

   **第三步: 写 Skill 脚本 (必须用 skillforge_sdk)**
   `skillforge_sdk.py` 由主仓库受控维护，运行时会自动注入受信任 SDK 目录。
   **不要手写 _load_browser_config / _post / _submit_result, 全部用 SDK。**
   也不要复制旧版 `skillforge_sdk.py`；发布同步会用平台受信任 SDK 覆盖。

   标准脚本模板:
   ```python
   from skillforge_sdk import SkillForge

   sf = SkillForge("{{skill_id}}")

   # 采集: 直接 fetch 已知 API
   data = sf.fetch_api("mcp://tmall_sycm_item_rank_top", method="POST", body={{"limit": 500}})

   # 分析
   declining = [item for item in data.get("data", []) if ...]

   # 提交结果 + 待办 (SkillForge 自动创建待办 + 钉钉推送)
   sf.submit(
       output={{"items": data, "analysis": "..."}},
       todos=[
           sf.todo_dispatch(
               title="天猫链接整改",
               summary="3个商品下滑",
               reviewers=["admin"],
               tasks=[
                   sf.task("示例品牌001 下滑35%", deadline="2026-04-12"),
                   sf.task("黑金001 下滑27%", deadline="2026-04-12"),
               ],
           ),
       ] if declining else None,
   )
   ```

   SDK 提供的方法:
   - `sf.fetch_api("mcp://tool_name", body=...)` — 通过平台 MCP Gateway 采集真实业务 JSON
   - `sf.fetch_api(url)` — 在浏览器上下文 fetch API, 返回 JSON
   - `sf.extract(js=..., url=...)` — 执行 JS 或导航后提取
   - `sf.capture_apis(url)` — 抓包发现页面 API
   - `sf.submit(output, todos=...)` — 回推结果 + 自动创建待办
   - `sf.todo_dispatch(title, tasks=...)` — 构造派发待办
   - `sf.todo_review(title)` — 构造审批待办
   - `sf.task(content, deadline=...)` — 构造执行任务

   SDK 真源文件位于主仓库 `app/skill_runtime_sdk/skillforge_sdk.py`，不是 `skills-repo` 资产。
   手工在 Skill 目录调试时，如需显式指定导入路径，可用：
   `PYTHONPATH="scripts:$SKILLFORGE_TRUSTED_SDK_DIR" python3 -m pytest tests/test_main.py -q`
   如果 MCP 返回空、HTTP 200 但业务 `ok=false`、cookie 缺失或 collection proof 失败，
   输出待补采/数据源失败，不能用汇总口径、0 值、fixture 或旧代码口径替代。

   **第四步: 运行验证**
   用 Bash 执行脚本, 确认数据采集 + submit 成功。

   ## 降级方案: 必须操作 DOM 时

   用 `sf.extract(js="...")` 探查实际 DOM 结构, 不要猜选择器。

   ## 反例
   - ❌ 不查缓存、不调 capture_apis 就写 DOM 抓取
   - ❌ **硬编码 `127.0.0.1:8000`** — 必须用 `_load_browser_config()` 自动发现
   - ❌ 猜 class 名 (`.next-tabs-tab`, `.ant-tabs-tab`)
   - ❌ `querySelectorAll('*')` 遍历整个 DOM
   - ❌ 写了 JS 不测就提交
   - ❌ fetch 失败不 debug 就换方案

10. **待办推送 (当用户要求 Skill 输出待办/任务/整改建议时)**:
   Skill 脚本的输出 JSON 中包含 `todos` 字段, SkillForge 自动解析并创建待办 + 钉钉推送。
   **不是所有 Skill 都需要**, 只在用户明确说"推待办"/"派发任务"/"通知整改"时才加。

   脚本输出格式 (写到 output dict 里):
   ```python
   output["todos"] = [
       {{
           "kind": "dispatch",       # review=审批 | dispatch=派发执行任务
           "title": "天猫链接整改任务 ({{date}})",
           "summary": "基于本次分析，以下商品需要整改",
           "reviewer_role": "ai_engineer",  # 管理者角色, 或用 reviewers: ["user_id"]
           "tasks": [
               {{
                   "executor": None,       # None=管理者自行分配; 或填 user_id
                   "content": "商品 XXX 访客下滑35%, 建议: 调整关键词出价",
                   "deadline": "2026-04-12T18:00:00",  # 可选
               }},
               # ... 更多任务
           ],
       }},
   ]
   ```

   两种 kind:
   - `review`: 需要审批人确认（通过/驳回）, 适合"数据报告需要确认"场景
   - `dispatch`: 需要派发执行任务, 适合"发现问题 → 分配人去修"场景

   完整字段参考:
   - `decision_mode`: "any_of"(任一审批人通过即可) / "all_of"(全部通过) / "independent"
   - `sla_hours`: 超时时间, 默认 24 小时
   - `callback`: 决策完成后回调 OpenClaw 让 Skill 继续执行 (高级用法)
   - 每个 task 的 `content` 会作为钉钉卡片正文推送给执行人

{preread_content}
"""


# ── 文件清单注入 (广泛兼容: 不假设 skill 结构) ──

_INVENTORY_SKIP_NAMES = {
    ".git", "__pycache__", "node_modules", ".pytest_cache",
    ".venv", "venv", "dist", "build", ".idea", ".vscode",
}
_INVENTORY_SKIP_SUFFIXES = {".pyc", ".pyo", ".so", ".o", ".class", ".lock"}
_INVENTORY_SCRIPT_SUFFIXES = {".py", ".sh", ".js", ".ts", ".rb", ".pl"}
_INVENTORY_MAX_FILES = 100
_INVENTORY_MAX_DEPTH = 4
_INVENTORY_MAX_SCRIPT_BYTES = 200 * 1024
_INVENTORY_DOCSTRING_HEAD_BYTES = 2048


def _extract_script_purpose(head: str, suffix: str) -> str:
    """从脚本前几行提取 docstring / 顶部注释, 给 AI 看一句话用途。"""
    lines = head.splitlines()
    if suffix == ".py":
        in_docstring = False
        quote = ""
        doc_lines: list[str] = []
        for raw in lines[:30]:
            line = raw.strip()
            if not in_docstring:
                if line.startswith('"""') or line.startswith("'''"):
                    quote = line[:3]
                    rest = line[3:]
                    if rest.endswith(quote) and len(rest) >= 3:
                        return rest[:-3].strip()
                    if quote in rest:
                        return rest.split(quote, 1)[0].strip()
                    in_docstring = True
                    if rest:
                        doc_lines.append(rest)
                    continue
                if line.startswith("#") and not line.startswith("#!"):
                    return line.lstrip("# ").strip()[:200]
            else:
                if line.endswith(quote):
                    doc_lines.append(line[:-3].strip())
                    return " ".join(s for s in doc_lines if s).strip()[:200]
                doc_lines.append(line)
        return " ".join(s for s in doc_lines[:3] if s).strip()[:200]
    if suffix in (".sh", ".js", ".ts", ".rb", ".pl"):
        for raw in lines[:20]:
            line = raw.strip()
            if not line or line.startswith("#!"):
                continue
            if line.startswith("#") or line.startswith("//"):
                return line.lstrip("# /").strip()[:200]
        return ""
    return ""


def build_skill_file_inventory(skill_dir: Path) -> str:
    """扫描 skill 目录, 生成精炼的文件清单 + 脚本入口描述。

    设计原则:
    - 广泛兼容: 不假设 SKILL.md / scripts/ 必须存在
    - 容量限制: 最多 {} 个文件, 最深 {} 层, 单脚本 < {}KB
    - 跳过噪声: .git/__pycache__/node_modules/dist/.pyc 等
    - 脚本入口提取 docstring, 让 AI 一眼看懂每个脚本干什么
    """.format(_INVENTORY_MAX_FILES, _INVENTORY_MAX_DEPTH, _INVENTORY_MAX_SCRIPT_BYTES // 1024)
    if not skill_dir.exists() or not skill_dir.is_dir():
        return "# 当前 Skill 目录文件清单\n  (skill 目录不存在或为空)"

    skill_root = skill_dir.resolve()
    files: list[tuple[str, int, Path]] = []
    truncated = False

    def walk(d: Path, depth: int) -> None:
        nonlocal truncated
        if depth > _INVENTORY_MAX_DEPTH:
            return
        if len(files) >= _INVENTORY_MAX_FILES:
            truncated = True
            return
        try:
            entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        for entry in entries:
            if entry.name in _INVENTORY_SKIP_NAMES or entry.name.startswith("."):
                continue
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    walk(entry, depth + 1)
                    continue
                if not entry.is_file():
                    continue
                if entry.suffix in _INVENTORY_SKIP_SUFFIXES:
                    continue
                rel = entry.relative_to(skill_root).as_posix()
                size = entry.stat().st_size
                files.append((rel, size, entry))
                if len(files) >= _INVENTORY_MAX_FILES:
                    truncated = True
                    return
            except OSError:
                continue

    walk(skill_root, 0)

    if not files:
        return "# 当前 Skill 目录文件清单\n  (skill 目录为空 — 这是新建状态)"

    out: list[str] = ["# 当前 Skill 目录文件清单"]
    for rel, size, _ in files:
        size_str = f"{size}B" if size < 1024 else f"{size // 1024}KB"
        out.append(f"  {rel} ({size_str})")
    if truncated:
        out.append(f"  ... (列表已截断, 仅展示前 {_INVENTORY_MAX_FILES} 个)")

    # 脚本入口（提取 docstring）
    script_lines: list[str] = []
    for rel, size, full in files:
        if full.suffix not in _INVENTORY_SCRIPT_SUFFIXES:
            continue
        if size > _INVENTORY_MAX_SCRIPT_BYTES:
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as fp:
                head = fp.read(_INVENTORY_DOCSTRING_HEAD_BYTES)
        except OSError:
            continue
        desc = _extract_script_purpose(head, full.suffix)
        if desc:
            script_lines.append(f"  - **{rel}**: {desc}")
        else:
            script_lines.append(f"  - **{rel}**: (无 docstring, 用 Read 看前 30 行了解用法)")

    if script_lines:
        out.append("")
        out.append("# 脚本入口（用户问业务任务时优先考虑这些）")
        out.extend(script_lines)

    return "\n".join(out)


# ── 资料包注入 ────────────────────────────────────────────
_CONTEXT_PACK_TARGETS = (
    ("SKILL.md", "技能说明", "先读真实文件，再回答技能边界和操作方式"),
    ("contract.json", "输出契约", "先看契约，再对齐输入/输出字段，别凭记忆猜"),
    ("scripts/main.py", "主入口", "真实运行/采集入口，优先从这里确认执行路径"),
    ("policy.yaml", "策略边界", "权限、采集、重试、限流等规则优先看这里"),
    ("fixtures/sample_input.json", "样例输入", "只用于测试/回归，不是运行数据"),
)
_CONTEXT_PACK_MAX_CHARS = 2600


def _read_text_head(path: Path, *, max_lines: int = 4) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return " | ".join(lines[:max_lines])[:240]


def _summarize_json_file(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    try:
        data = json.loads(raw)
    except Exception:
        return _read_text_head(path)
    if isinstance(data, dict):
        keys = list(data.keys())[:10]
        if keys:
            return "JSON keys: " + ", ".join(keys)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        keys = list(data[0].keys())[:8]
        if keys:
            return "JSON list[0] keys: " + ", ".join(keys)
    return "JSON file"


def _summarize_yaml_like_file(path: Path) -> str:
    head = _read_text_head(path, max_lines=6)
    if not head:
        return ""
    top_keys: list[str] = []
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.rstrip()
            if not line or line.lstrip().startswith("#"):
                continue
            if line.startswith((" ", "\t")):
                continue
            if ":" in line:
                key = line.split(":", 1)[0].strip()
                if key and key not in top_keys:
                    top_keys.append(key)
            if len(top_keys) >= 6:
                break
    except OSError:
        pass
    if top_keys:
        return "Top keys: " + ", ".join(top_keys)
    return head


def _summarize_key_file(skill_dir: Path, rel_path: str, manifest: dict | None = None) -> str:
    path = skill_dir / rel_path
    if not path.is_file():
        return "[missing]"

    if rel_path == "scripts/main.py" and manifest:
        for item in manifest.get("scripts") or []:
            if item.get("path") == rel_path:
                purpose = (item.get("purpose") or "").strip()
                if purpose:
                    return purpose
        return _read_text_head(path)

    if path.suffix == ".json":
        return _summarize_json_file(path)
    if path.suffix in {".yaml", ".yml"}:
        return _summarize_yaml_like_file(path)
    return _read_text_head(path)


def _build_recent_log_entry(skill_dir: Path, manifest: dict | None = None) -> str:
    candidates: list[tuple[float, str]] = []
    rel_paths = (manifest or {}).get("all_files") or []
    if rel_paths:
        for rel in rel_paths:
            lower = rel.lower()
            if any(token in lower for token in ("/logs/", "/log/", "/runs/", "/artifacts/", "/tmp/", "/output/", "/outputs/")):
                fpath = skill_dir / rel
                if fpath.is_file():
                    try:
                        candidates.append((fpath.stat().st_mtime, rel))
                    except OSError:
                        continue
            elif lower.endswith((".log", ".jsonl", ".out", ".err")):
                fpath = skill_dir / rel
                if fpath.is_file():
                    try:
                        candidates.append((fpath.stat().st_mtime, rel))
                    except OSError:
                        continue
    candidates.sort(key=lambda item: item[0], reverse=True)
    if candidates:
        return "最近日志入口: " + ", ".join(rel for _, rel in candidates[:3])

    fallback_dirs = []
    for rel in ("logs", "runs", "artifacts", "tmp", "output", "outputs"):
        if (skill_dir / rel).exists():
            fallback_dirs.append(rel)
    if fallback_dirs:
        return "最近日志入口: " + ", ".join(fallback_dirs[:3])
    return "最近日志入口: 先查 `logs/`、`runs/`、`artifacts/`、`tmp/` 下最新的 `*.log` / `*.jsonl` / `*.out` / `*.err`"


def build_skill_context_pack(skill_dir: Path, manifest: dict | None = None) -> str:
    """生成压缩版资料包，避免把全文塞进 prompt。"""
    skill_dir = Path(skill_dir)
    manifest = manifest or build_skill_manifest_from_dir(skill_dir)
    all_files = set(manifest.get("all_files") or [])

    lines: list[str] = ["# AI 助手资料包 / context pack"]
    lines.append("- 先读真实文件和 `contract.json`；不要凭训练记忆猜接口、字段或执行方式。")

    key_lines: list[str] = []
    for rel_path, label, hint in _CONTEXT_PACK_TARGETS:
        status = "存在" if rel_path in all_files else "缺失"
        summary = _summarize_key_file(skill_dir, rel_path, manifest)
        if len(summary) > 180:
            summary = summary[:180].rstrip() + "..."
        key_lines.append(f"- `{rel_path}` ({label}, {status}): {summary or hint}")

    lines.append("- 关键文件摘要:")
    lines.extend(key_lines)

    lines.append("- 输出契约提示: 先对齐 `contract.json` 的真实输出字段/结构；没有契约就明确报告缺失，不要自己补接口。")
    lines.append("- 最近失败/运行日志入口: " + _build_recent_log_entry(skill_dir, manifest).removeprefix("最近日志入口: ").strip())
    lines.append("- 数据源 / 外部 API: 先确认真实采集路径、脚本入口和调用链；如果涉及 datasource / external API，先找真实抓取或请求代码，再看官方文档。")
    lines.append("- 约束: `fixtures/sample_input.json` 只用于测试/回归，不能当运行数据；缺 API 文档时不要猜一堆 endpoint，要明确说明资料不足。")
    lines.append("- 权限边界: 不要越权读写工作目录外文件；不要默认访问未声明的外部服务；需要新增网络/文件/系统操作时先确认当前技能契约允许。")

    text = "\n".join(lines)
    if len(text) > _CONTEXT_PACK_MAX_CHARS:
        return text[:_CONTEXT_PACK_MAX_CHARS].rstrip() + "\n..."
    return text


# ── 预读核心文件内容 ──────────────────────────────────────
_PREREAD_FILES = ["SKILL.md", "policy.yaml", "policy_pack.yaml"]
_PREREAD_MAX_BYTES = 15_000  # 单文件最大 15KB，超过截断


def _read_key_file_contents(skill_dir: Path) -> str:
    """预读 SKILL.md 和 policy_pack.yaml 内容注入 system prompt。

    AI 开机就有完整文件内容，不用先 Read 再回答。
    解决根因：AI 跳过 Read SKILL.md 直接用训练记忆答题。
    """
    if not skill_dir or not skill_dir.exists() or not skill_dir.is_dir():
        return ""

    sections: list[str] = []
    for filename in _PREREAD_FILES:
        fpath = skill_dir / filename
        # 安全：拒绝符号链接（防止越界读取敏感文件）
        if not fpath.is_file() or fpath.is_symlink():
            continue
        # 安全：resolve 后确认仍在 skill_dir 内
        try:
            resolved = fpath.resolve(strict=True)
            if not str(resolved).startswith(str(skill_dir.resolve())):
                continue
        except (OSError, ValueError):
            continue
        # 限制读取大小：流式读取，避免超大文件吃内存
        try:
            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(_PREREAD_MAX_BYTES + 1)
        except OSError:
            continue
        if not content.strip():
            continue
        if len(content) > _PREREAD_MAX_BYTES:
            content = content[:_PREREAD_MAX_BYTES] + "\n... (文件过长, 已截断; 请用 Read 工具查看完整内容)"
        sections.append(
            f"# ===== {filename} 完整内容 (预读) =====\n{content}\n# ===== /{filename} ====="
        )

    if not sections:
        return ""

    header = (
        "# 预读文件内容\n"
        "# 以下是 Skill 核心文件的完整文本, 你已经拥有这些内容, 无需再 Read\n"
        "# 如果文件被截断, 需要 Read 工具获取完整版本\n"
    )
    return header + "\n\n".join(sections)


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])


def _strip_tool_paths(tool_input: dict, work_dir: Path | str | None) -> dict:
    """剥掉 tool_input 里 file_path / command 的绝对路径前缀（work_dir 优先，PROJECT_ROOT 兜底）。

    前端展示 `scripts/main.py` 比完整绝对路径更清爽，也避免服务器路径泄露。
    """
    if not isinstance(tool_input, dict):
        return tool_input
    prefixes: list[str] = []
    if work_dir:
        wd = str(work_dir).rstrip("/")
        if wd:
            prefixes.append(wd + "/")
    prefixes.append(_PROJECT_ROOT.rstrip("/") + "/")

    def _strip(v):
        if not isinstance(v, str):
            return v
        out = v
        for p in prefixes:
            out = out.replace(p, "")
        return out

    out = dict(tool_input)
    for k in ("file_path", "path", "command", "pattern"):
        if k in out:
            out[k] = _strip(out[k])
    return out


def _classify_tool_result_error(content: Any) -> dict[str, str] | None:
    """把常见工具错误归类成前端/日志更容易理解的原因。"""
    text = str(content or "")
    lower = text.lower()
    if not text:
        return None
    if any(marker in lower for marker in ("permission denied", "operation not permitted", "eacces")):
        return {
            "error_kind": "permission_denied",
            "error_detail": "工具被系统或文件权限拒绝，请检查目标路径和权限边界。",
        }
    if any(marker in lower for marker in ("no such file or directory", "enoent", "not found")):
        detail = "目标文件或目录不存在。"
        if any(marker in lower for marker in ("/tmp/", "tool-results", "claude", "aiclawcode", "coding-agent")):
            detail = "目标文件或历史临时目录不存在，可能是会话/工具结果已过期；请重新运行产生该文件的步骤。"
        return {"error_kind": "missing_path", "error_detail": detail}
    return None


def build_system_prompt_with_hash(work_dir: Path, *, mode: str = "edit") -> tuple[str, str]:
    """生成注入子进程的 system prompt 引导（含动态文件清单）+ prompt hash。

    支持的 mode:
    - edit (默认): workbench 编辑现有 Skill
    - review: edit + review 指令追加段
    - explain: edit + explain 指令追加段
    - create: v2.11.4 新建 Skill 向导 —— 用 coding_agent_skill_creation 替换
              workbench 主段，browser_policy + todo_contract 保留（创建阶段
              经常需要 curl 目标平台 API、Skill 产物也可能要推待办）
    """
    manifest = build_skill_manifest_from_dir(work_dir)
    inventory = render_manifest_summary(manifest)
    preread = _read_key_file_contents(work_dir)
    context_pack = build_skill_context_pack(work_dir, manifest)
    context = {
        "work_dir": str(work_dir),
        "skill_inventory": inventory,
        "context_pack": context_pack,
        "preread_content": preread,
    }
    try:
        from app.common.prompt_registry import init_registry, prompt_registry

        if not prompt_registry._sections:
            init_registry()

        if mode == "create":
            # v2.11.5：mode=create 只用 skill_creation 单段（~8k）—— 跟老路径
            # skill_creation_runner 对齐。之前叠加 browser_policy + todo_contract
            # 膨胀到 25k tokens 直接炸 GLM TPM 额度。
            # 浏览器 MCP 已单独注入（_build_builtin_mcp_servers 精简后只 browser
            # + skillforge_internal），不需要 browser_policy 提示文本。
            creation_prompt, _creation_hash = prompt_registry.build(
                "coding_agent_skill_creation",
                context=context,
            )
            return creation_prompt, _short_hash(creation_prompt)

        prompt, _base_hash = prompt_registry.build(
            "coding_agent_workbench",
            context=context,
        )
        browser_policy, _browser_hash = prompt_registry.build(
            "coding_agent_browser_policy",
            context=context,
        )
        todo_contract, _todo_hash = prompt_registry.build(
            "coding_agent_todo_contract",
            context=context,
        )
        sections = [prompt, browser_policy, todo_contract]
        if mode == "review":
            review_prompt, _review_hash = prompt_registry.build(
                "coding_agent_review",
                context=context,
            )
            sections.append(review_prompt)
        elif mode == "explain":
            explain_prompt, _explain_hash = prompt_registry.build(
                "coding_agent_explain",
                context=context,
            )
            sections.append(explain_prompt)
        combined = "\n\n".join(sections)
        return combined, _short_hash(combined)
    except Exception as e:  # noqa: BLE001
        logger.warning("coding_agent_workbench prompt fallback to inline template: {}", e)
        prompt = SYSTEM_PROMPT_TEMPLATE.format(**context)
        return prompt, _short_hash(prompt)


def build_system_prompt(work_dir: Path, *, mode: str = "edit") -> str:
    """生成注入子进程的 system prompt 引导（含动态文件清单）。"""
    prompt, _ = build_system_prompt_with_hash(work_dir, mode=mode)
    return prompt

from app.common.exceptions import AppError
from app.coding_agent.permission_policy import PermissionPolicy
from app.coding_agent.schemas import (
    CodingAgentErrorCode,
    Event,
    EventType,
    PermissionEvaluation,
)
from app.coding_agent.session_pool import coding_agent_pool
from app.coding_agent.subprocess_session import SubprocessSession
from app.coding_agent.runtime_availability import require_authoring_runtime, runtime_status
from app.config import settings


class SessionService:
    """业务层：把池里的子进程会话翻译成前端 Event 流。"""

    # 每个 (skill_id, user_id) 关联的权限策略
    _policies: dict[tuple[str, str], PermissionPolicy] = {}
    # 每个 (skill_id, user_id) 关联的"待审批"权限请求 (request_id → 原始 input)
    _pending_perms: dict[tuple[str, str], dict[str, dict]] = {}

    # ─────────────────────────────────────────────────────
    # chat：核心入口
    # ─────────────────────────────────────────────────────

    async def chat(
        self,
        *,
        skill_id: str,
        user_id: str,
        message: str,
        images: list | None = None,
        mode: str = "edit",
    ) -> AsyncIterator[Event]:
        """
        发送一条用户消息并 yield 事件流，直到当轮 result 事件。

        ⚠️ 注意：aiclawcode 在 stream-json 模式下不会主动发 system/init，
        必须先 send_user_message 才会有任何输出。所以这里：
            1. 确保 session (spawn 子进程, 不阻塞等 init)
            2. 发用户消息
            3. 流式读事件 (init / assistant / tool_use / tool_result / result)
            4. 看到 result 就停
        SESSION_READY 事件由 _translate_event 在收到 system/init 时合成。
        """
        session, env_used = await self._ensure_session(skill_id=skill_id, user_id=user_id, mode=mode)

        # 如果带图片附件，保存到 skill 目录并在消息中引用
        if images:
            import base64
            img_refs = []
            for i, img in enumerate(images):
                ext = img.get("media_type", "image/png").split("/")[-1]
                fname = f"_upload_{i}.{ext}"
                fpath = session.work_dir / fname
                fpath.write_bytes(base64.b64decode(img["data"]))
                img_refs.append(str(fpath))
            # 在消息前加图片路径提示，让 AI 用 vision MCP 分析
            img_hint = "\n".join(f"[附件图片: {p}]" for p in img_refs)
            message = f"{img_hint}\n\n{message}" if message else img_hint

        try:
            await session.send_user_message(message)
        except Exception as send_err:
            if not session.is_alive:
                # 子进程已崩溃：自动重建并重试一次
                from loguru import logger
                logger.warning("CodingAgent 子进程已崩溃，自动重建 skill={} user={}: {}", skill_id, user_id, send_err)
                await coding_agent_pool.release_by_key(skill_id=skill_id, user_id=user_id)
                session, env_used = await self._ensure_session(skill_id=skill_id, user_id=user_id, mode=mode)
                await session.send_user_message(message)
            else:
                raise

        # [resume-v2] 每次进入 stream_events 前申请新的 consumer token, 挤掉旧的。
        # 同一 session 只能有一个活跃 consumer, 否则两个 async for 会抢同一 queue。
        token = session.acquire_stream_token()
        async for ev in self._stream_with_watchdog(
            skill_id=skill_id, user_id=user_id, session=session, token=token,
        ):
            yield ev

    async def resume_stream(
        self,
        *,
        skill_id: str,
        user_id: str,
        last_fe_seq: int,
    ) -> AsyncIterator[Event]:
        """
        WS 重连后的事件回放 + 继续订阅。

        流程:
            1. 从 pool 获取 session; 拿不到说明已被 idle gc 回收 → yield RESUME_NO_SESSION ERROR
            2. 从 fe_history 回放 last_fe_seq 之后的所有事件 (已含 _seq)
            3. 申请新 stream consumer token 挤掉旧 chat() 的 consumer (防止两消费者抢 queue)
            4. 继续 stream_events 翻译新来的事件, 和 chat() 一样分配 fe_seq + 入 history + yield

        ⚠️ 不主动发新 user_message — resume 只是接续已经在跑的那一轮, 或者在 idle 时
        预订阅下一轮。真正驱动新一轮对话仍然由 chat() 负责 (发 user_message + stream)。
        """
        session = await coding_agent_pool.get(skill_id=skill_id, user_id=user_id)
        if session is None:
            # P1: 用户看得懂的 ERROR 事件, 不再用 DONE + resume_nosession flag
            yield Event(
                EventType.ERROR,
                {
                    "code": CodingAgentErrorCode.RESUME_NO_SESSION.value,
                    "error": "会话已过期，请重新发送消息",
                },
            )
            return

        # 1) 回放历史
        replayed: list[dict] = session.replay_fe_events_since(last_fe_seq)
        last_type: str | None = None
        for event_dict in replayed:
            etype_raw = event_dict.get("type")
            payload = {k: v for k, v in event_dict.items() if k != "type"}
            try:
                etype = EventType(etype_raw)
            except ValueError:
                # 历史里出现了未知类型, 跳过不至于崩
                continue
            last_type = etype.value
            yield Event(etype, payload)

        # 2) 如果回放已经把本轮跑完 (最后一条是 DONE/ERROR), 不再进 watchdog
        # 挂住等下一轮 — aiclawcode 此时是 idle 等 stdin, queue 里没东西,
        # 前端 WS 等到 12min inactivity timeout 才断开, UI 一直卡 streaming。
        if last_type in {EventType.DONE.value, EventType.ERROR.value}:
            return

        # 3) 挤掉旧 consumer, 接管 stream
        token = session.acquire_stream_token()
        async for ev in self._stream_with_watchdog(
            skill_id=skill_id, user_id=user_id, session=session, token=token,
        ):
            yield ev

    async def _stream_with_watchdog(
        self,
        *,
        skill_id: str,
        user_id: str,
        session: SubprocessSession,
        token: int,
    ) -> AsyncIterator[Event]:
        """
        包装 session.stream_events + 单轮 inactivity 超时保护。

        为什么用 inactivity 而不是单轮总时长:
          合法的长耗时 tool_use(抓 API / 大文件 Read / 长 Bash)是正常的, 只要事件流
          在动就不算卡。卡死的特征是 "连续 N 秒一条事件都收不到"。

        触发条件: 超过 PER_TURN_INACTIVITY_TIMEOUT_SEC 没有任何新 stream 事件。
        处置: 释放 session(杀 Node 子进程) + yield ERROR + return。Router 层的
              active_stream_task 自然结束, 前端可以发下一条消息。
        """
        stream = session.stream_events(token=token)
        it = stream.__aiter__()
        while True:
            try:
                raw = await asyncio.wait_for(
                    it.__anext__(),
                    timeout=PER_TURN_INACTIVITY_TIMEOUT_SEC,
                )
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                logger.warning(
                    "CodingAgent 单轮 inactivity {}s 超时 → 强制终止: skill={} user={} pending_tools={}",
                    PER_TURN_INACTIVITY_TIMEOUT_SEC,
                    skill_id, user_id,
                    getattr(session, "pending_tool_calls", "?"),
                )
                with suppress(Exception):
                    await coding_agent_pool.release_by_key(skill_id=skill_id, user_id=user_id)
                err = Event(
                    EventType.ERROR,
                    {
                        "code": CodingAgentErrorCode.TURN_INACTIVITY_TIMEOUT.value,
                        "error": (
                            f"Agent {PER_TURN_INACTIVITY_TIMEOUT_SEC // 60} 分钟无响应,"
                            f"已自动终止会话。请重新发送消息。"
                        ),
                    },
                )
                # 超时 ERROR 也要进 fe_history, 让 WS 重连 resume 时能看到
                fe_seq = session.next_fe_seq()
                err.payload["_seq"] = fe_seq
                session.append_fe_event(err.to_dict())
                yield err
                return

            for ev in self._translate_event(skill_id, user_id, session, raw):
                # 给每个翻译后的 Event 分配独立 fe_seq, 写进 payload, 入 fe_history。
                # 这是 resume 协议的核心: raw 1:N → fe, 每个 fe 有独立游标, 不会漏兄弟。
                fe_seq = session.next_fe_seq()
                ev.payload["_seq"] = fe_seq
                session.append_fe_event(ev.to_dict())
                yield ev
            # 一轮对话结束的标志
            if raw.get("type") == "result":
                return

    async def ensure_session(
        self,
        *,
        skill_id: str,
        user_id: str,
        mode: str = "edit",
    ) -> SubprocessSession:
        """供上层预热 session，并读取 prompt/runtime 元信息。"""
        session, _ = await self._ensure_session(skill_id=skill_id, user_id=user_id, mode=mode)
        return session

    # ─────────────────────────────────────────────────────
    # 权限响应 / 中断 / 关闭
    # ─────────────────────────────────────────────────────

    async def respond_permission(
        self,
        *,
        skill_id: str,
        user_id: str,
        request_id: str,
        behavior: str,
        updated_input: dict | None = None,
        message: str | None = None,
    ) -> None:
        sess = await coding_agent_pool.get(skill_id=skill_id, user_id=user_id)
        if not sess:
            raise AppError(CodingAgentErrorCode.PROCESS_DIED.value, 404, {"detail": "no session"})

        # 找回原始 input（用户没改的话沿用原值）
        pending = self._pending_perms.get((skill_id, user_id), {})
        original_input = pending.pop(request_id, {})
        final_input = updated_input if updated_input is not None else original_input

        await sess.respond_permission(
            request_id, behavior, updated_input=final_input, message=message,
        )

    async def _auto_deny_after_timeout(
        self,
        *,
        skill_id: str,
        user_id: str,
        request_id: str,
        tool_name: str,
        session: SubprocessSession,
    ) -> None:
        """
        若 PERMISSION_REQUEST_TIMEOUT_SEC 秒后用户仍未响应权限请求, 自动 deny。
        防止 aiclawcode Node 子进程死等导致整个会话挂死。
        """
        await asyncio.sleep(PERMISSION_REQUEST_TIMEOUT_SEC)
        pending = self._pending_perms.get((skill_id, user_id), {})
        if request_id not in pending:
            return  # 已经被用户响应处理
        pending.pop(request_id, None)
        try:
            await session.respond_permission(
                request_id, "deny",
                message=f"User did not respond within {PERMISSION_REQUEST_TIMEOUT_SEC}s, auto-denied",
            )
            logger.warning(
                f"CodingAgent auto-deny: skill={skill_id} user={user_id} "
                f"request={request_id} tool={tool_name}"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"CodingAgent auto-deny failed: {e}")

    async def interrupt(self, *, skill_id: str, user_id: str) -> None:
        sess = await coding_agent_pool.get(skill_id=skill_id, user_id=user_id)
        if not sess:
            return
        with suppress(Exception):
            await sess.send_interrupt()

    async def close_session(self, *, skill_id: str, user_id: str) -> None:
        await coding_agent_pool.release_by_key(skill_id=skill_id, user_id=user_id)
        self._pending_perms.pop((skill_id, user_id), None)
        self._policies.pop((skill_id, user_id), None)

    # ─────────────────────────────────────────────────────
    # 内部：会话获取 + 翻译
    # ─────────────────────────────────────────────────────

    async def _ensure_session(
        self, *, skill_id: str, user_id: str, mode: str = "edit"
    ) -> tuple[SubprocessSession, dict[str, str]]:
        """获取或创建 session, 同时初始化对应的 PermissionPolicy。"""
        from app.coding_agent.mcp_config import build_mcp_config_json, get_mcp_servers

        require_authoring_runtime()
        skill_dir = self._resolve_skill_dir(skill_id)
        env, provider, model = await self._build_subprocess_env_provider_model()
        env, config_dir = self._inject_runtime_isolation(
            env,
            user_id=user_id,
            session_scope=f"skill_{skill_id}",
        )
        strategy = await self._read_strategy()
        permission_mode = self._strategy_to_permission_mode(strategy)
        # v2.11.5：mode=create 强制 bypassPermissions —— agent 要在 draft 目录
        # 里大量 Write / Bash（跑 pytest / schema 自检），不能弹 permission 对话框；
        # 目录是 draft 隔离的，即使 agent 误写也只影响这个 draft，风险可控。
        if mode == "create":
            permission_mode = "bypassPermissions"
        system_prompt, prompt_hash = build_system_prompt_with_hash(skill_dir, mode=mode)

        # 构造 MCP server 配置 JSON (只含 enabled=True 的 server)
        mcp_servers = await get_mcp_servers()
        builtin_mcp = await self._build_builtin_mcp_servers(skill_id, user_id)
        mcp_servers = {**mcp_servers, **builtin_mcp}
        # mode=create / mode=edit 只加载 SkillForge 自带 MCP。选择逻辑不写死具体业务平台名：
        # _build_builtin_mcp_servers 当前提供什么，这里就给 agent 什么；以后新增业务 MCP
        # 只要进入 builtin 集合，生成/编辑阶段会自动可见。
        # zread / web-reader /
        # web-search-prime / zai-mcp-server 都是 HTTP MCP，headers 里 Bearer 和
        # coding_agent.api_key 共用同一个 GLM 账号；子进程冷启动会并发对 list-tools
        # 打 4 个请求，瞬时超账号 RPM → 1302 速率限制 → 整个 session 挂 3min 才报 429
        # （实测见 2026-04-20 路径 B）。对话阶段才需要的网页/搜索能力可在 skill 自己
        # 的 policy_pack 里声明，不必每次 session 冷启动都加载。
        if mode in ("create", "edit") and mcp_servers:
            filtered = self._select_creation_mcp_servers(mcp_servers, builtin_mcp)
            skipped = sorted(set(mcp_servers) - set(filtered))
            if skipped:
                logger.info(
                    "CodingAgent mode={} 精简 MCP：跳过 {} 个（{}）",
                    mode, len(skipped), ", ".join(skipped),
                )
            mcp_servers = filtered
        mcp_config_json = build_mcp_config_json(mcp_servers) if mcp_servers else None
        runtime_profile = self._build_runtime_profile(
            provider=provider,
            strategy=strategy,
            env=env,
            mcp_servers=mcp_servers,
        )

        session = await coding_agent_pool.acquire(
            skill_id=skill_id, user_id=user_id, work_dir=skill_dir,
            env=env, provider=provider, model=model,
            permission_mode=permission_mode,
            system_prompt_append=system_prompt,
            mcp_config_json=mcp_config_json,
            prompt_hash=prompt_hash,
            config_dir=config_dir,
        )
        session.runtime_profile = runtime_profile
        extra_roots = [config_dir] if config_dir else []
        repo_root = Path(__file__).resolve().parents[2]
        for sdk_dir in (
            repo_root / "app" / "skill_runtime_sdk",
            settings.skill_repo_dir / "_shared",
        ):
            resolved = sdk_dir.resolve()
            if resolved.exists():
                extra_roots.append(resolved)
        self._policies[(skill_id, user_id)] = PermissionPolicy(
            skill_dir,
            strategy=strategy,
            extra_allowed_roots=extra_roots,
        )
        return session, env

    @staticmethod
    def _strategy_to_permission_mode(strategy: str) -> str:
        """
        Python 端 strategy → aiclawcode --permission-mode 映射。

        - strict   → default            (Edit/Write 也走 control_request 给前端)
        - balanced → acceptEdits        (Edit/Write 在 cwd 内自动放行, Bash 走 stdio 协议)
        - loose    → acceptEdits        (Bash 仍走 Python 边界检查后自动放行)

        所有模式都搭配 --permission-prompt-tool stdio (在 SubprocessSession 内固定)，
        让"非 acceptEdits 范围"的工具调用 (Bash/WebFetch 等) 通过 control_request 协议
        走到 _translate_event 的权限决策路径。
        """
        return {
            "strict": "default",
            "balanced": "acceptEdits",
            "loose": "acceptEdits",
        }.get(strategy, "acceptEdits")

    @staticmethod
    def _resolve_skill_dir(skill_id: str) -> Path:
        # 简单防御 path traversal
        if "/" in skill_id or ".." in skill_id or "\\" in skill_id:
            raise AppError(
                CodingAgentErrorCode.PERMISSION_VIOLATION.value,
                400,
                {"detail": f"invalid skill_id: {skill_id!r}"},
            )
        skill_dir = (settings.skill_repo_dir / skill_id).resolve()
        # 确认仍在 skill_repo_dir 下
        if not str(skill_dir).startswith(str(settings.skill_repo_dir.resolve())):
            raise AppError(
                CodingAgentErrorCode.PERMISSION_VIOLATION.value,
                400,
                {"detail": "skill_id escapes skill_repo_dir"},
            )
        return skill_dir

    @staticmethod
    def _safe_runtime_segment(value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value or "").strip("._")
        return (cleaned or "unknown")[:80]

    @classmethod
    def _build_runtime_profile(
        cls,
        *,
        provider: str | None,
        strategy: str,
        env: dict[str, str],
        mcp_servers: dict | None,
    ) -> dict:
        return {
            "vendor_version": None,  # compatibility key, no bundled runtime
            "runtime": runtime_status(),
            "provider": provider or "",
            "permission_strategy": strategy,
            "bare_mode": bool(env.get("CLAUDE_CODE_SIMPLE")),
            "mcp_servers_count": len(mcp_servers or {}),
        }

    @staticmethod
    def _select_creation_mcp_servers(
        mcp_servers: dict[str, dict],
        builtin_mcp: dict[str, dict],
    ) -> dict[str, dict]:
        """Select MCP servers for generation/edit sessions.

        Keep this dynamic: generation should use the SkillForge-owned MCP servers
        currently provided by `_build_builtin_mcp_servers`, without hardcoding
        business platform names in multiple places. Admin-added remote MCP servers
        stay out of create/edit cold starts to avoid unrelated list-tools latency
        and shared API-key rate limits.
        """
        builtin_names = set(builtin_mcp)
        return {
            name: cfg
            for name, cfg in mcp_servers.items()
            if name in builtin_names
        }

    @staticmethod
    async def _build_builtin_mcp_servers(skill_id: str, user_id: str) -> dict[str, dict]:
        project_root = Path(__file__).resolve().parents[2]
        scripts_dir = project_root / "scripts"
        servers: dict[str, dict] = {}
        if not scripts_dir.exists():
            return servers

        from app.common.yuyidata_config import yuyidata_mcp_env

        yuyidata_env = await yuyidata_mcp_env()

        for script in sorted(scripts_dir.glob("*_mcp_server.py")):
            raw_name = script.name.removesuffix("_mcp_server.py")
            server_name = "skillforge_internal" if raw_name == "skillforge" else raw_name
            env = {
                # v2.11.5：aiclawcode 子进程 cwd 是 skills-repo/<skill_id>/，
                # MCP 脚本需要 from app.database import ...，必须把项目根加到
                # PYTHONPATH，否则子进程立刻 ModuleNotFoundError 挂掉。
                "PYTHONPATH": str(project_root),
                "SKILLFORGE_SKILL_ID": skill_id,
                "SKILLFORGE_USER_ID": user_id,
            }
            if server_name == "yuyidata":
                env.update(yuyidata_env)
            servers[server_name] = {
                "enabled": True,
                "type": "stdio",
                "command": sys.executable,
                "args": [str(script)],
                "env": env,
            }
        return servers

    @classmethod
    def _inject_runtime_isolation(
        cls,
        env: dict[str, str],
        *,
        user_id: str,
        session_scope: str,
    ) -> tuple[dict[str, str], Path]:
        """
        为 aiclawcode 子进程注入独立 CLAUDE_CONFIG_DIR，避免共享 ~/.aiclawcode。
        """
        base_dir = Path(settings.CODING_AGENT_CONFIG_BASE_DIR).resolve()
        config_dir = (
            base_dir
            / cls._safe_runtime_segment(user_id)
            / cls._safe_runtime_segment(session_scope)
        )
        config_dir.mkdir(parents=True, exist_ok=True)

        isolated = dict(env)
        isolated["CLAUDE_CONFIG_DIR"] = str(config_dir)
        isolated["SKILLFORGE_CODING_AGENT_SCOPE"] = session_scope
        return isolated, config_dir

    @staticmethod
    async def _build_subprocess_env_provider_model() -> tuple[dict[str, str], str | None, str | None]:
        """
        从 system_config 读 coding_agent.* 配置，构造：
            (env_dict, provider, model)
        - env_dict: 给 Node 子进程注入的环境变量（含 provider 特定的 *_API_KEY 和 *_BASE_URL）
        - provider: 传给 cli 的 --provider flag 值（如 'tencent' / 'glm' / 'kimi'）
        - model:    传给 cli 的 --model flag 值（如 'glm-5' / 'tc-code-latest'）

        provider 决定 fork 内部用哪套 base_url + auth_mode + 环境变量名。
        """
        from app.common.ai import get_coding_agent_config

        config = await get_coding_agent_config()
        env: dict[str, str] = {}

        provider = (config.get("coding_agent.provider") or "").strip() or None
        model = (config.get("coding_agent.model") or "").strip() or None
        api_base = str(config.get("coding_agent.api_base") or "").rstrip("/")
        api_key = str(config.get("coding_agent.api_key") or "")
        bare_mode = bool(config.get("coding_agent.bare_mode", True))

        # 按 provider 注入对应的 fork 专用环境变量名
        if provider == "tencent":
            if api_key:
                env["TENCENT_API_KEY"] = api_key
            if api_base:
                env["TENCENT_BASE_URL"] = api_base
        elif provider == "glm":
            if api_key:
                env["GLM_API_KEY"] = api_key
            if api_base:
                env["GLM_BASE_URL"] = api_base
        elif provider == "kimi":
            if api_key:
                env["KIMI_API_KEY"] = api_key
            if api_base:
                env["KIMI_BASE_URL"] = api_base
        else:
            # 通用 / 默认：走 ANTHROPIC_* 命名（fork 也支持 fallback 到这套）
            if api_key:
                env["ANTHROPIC_API_KEY"] = api_key
            if api_base:
                env["ANTHROPIC_BASE_URL"] = api_base
            if model:
                env["ANTHROPIC_MODEL"] = model

        if bare_mode:
            env["CLAUDE_CODE_SIMPLE"] = "1"

        # 禁止子进程向 GrowthBook/Datadog/Anthropic 发送遥测和非必要流量
        # 受限网络下这些连接会导致 5 秒超时延迟 + 安全策略违规
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        env["DISABLE_TELEMETRY"] = "1"
        env["SKILLFORGE_TRUSTED_SDK_DIR"] = str(
            Path(__file__).resolve().parents[1] / "skill_runtime_sdk"
        )

        return env, provider, model

    # 兼容旧调用方
    @classmethod
    async def _build_subprocess_env(cls) -> dict[str, str]:
        env, _, _ = await cls._build_subprocess_env_provider_model()
        return env

    @staticmethod
    async def _read_strategy() -> str:
        from app.common.ai import get_coding_agent_config

        config = await get_coding_agent_config()
        strategy = str(config.get("coding_agent.permission_strategy") or "balanced")
        if strategy not in ("strict", "balanced", "loose"):
            strategy = "balanced"
        return strategy

    # ─────────────────────────────────────────────────────
    # 事件翻译
    # ─────────────────────────────────────────────────────

    def _translate_event(
        self,
        skill_id: str,
        user_id: str,
        session: SubprocessSession,
        raw: dict[str, Any],
    ) -> list[Event]:
        """把一条原始 stream-json 事件翻译成 0 个或多个统一 Event。"""
        t = raw.get("type")
        out: list[Event] = []

        if t == "system":
            # 首次 init 事件回填 session 元数据 + 推一个 SESSION_READY 给前端
            if raw.get("subtype") == "init" and session.session_id is None:
                session.absorb_init_event(raw)
                out.append(Event(EventType.SESSION_READY, {
                    "session_id": session.session_id,
                    "model": session.model,
                    "tools": session.tools,
                    "work_dir": str(session.work_dir),
                    "prompt_hash": getattr(session, "prompt_hash", None),
                    "config_dir": str(getattr(session, "config_dir", "") or ""),
                    "runtime_profile": getattr(session, "runtime_profile", None),
                }))
            return out

        if t == "assistant":
            msg = raw.get("message") or {}
            content_blocks = msg.get("content") or []
            for block in content_blocks:
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text") or ""
                    if text:
                        out.append(Event(EventType.TEXT_DELTA, {"content": text}))
                elif btype == "tool_use":
                    tool_name = block.get("name") or "unknown"
                    tool_input = block.get("input") or {}
                    tool_use_id = block.get("id") or f"toolu_{uuid4().hex}"

                    # 权限决策（同步评估，失败时拦截发权限请求）
                    policy = self._policies.get((skill_id, user_id))
                    decision = policy.evaluate(tool_name, tool_input) if policy else PermissionEvaluation(
                        decision="ask_user", reason="no policy"
                    )

                    out.append(Event(EventType.TOOL_CALL, {
                        "id": tool_use_id,
                        "tool": tool_name,
                        "input": _strip_tool_paths(tool_input, session.work_dir),
                        "decision": decision.decision,
                        "decision_reason": decision.reason,
                        "prompt_hash": getattr(session, "prompt_hash", None),
                    }))

                    # 文件变更通知（编辑器跟踪用）
                    if tool_name in ("Edit", "Write", "MultiEdit"):
                        out.append(self._build_file_change_event(tool_name, tool_input, session.work_dir))

                    # 注意：本 Phase 1 不在这里直接做 ask_user 拦截
                    # （aiclawcode 的 control_request 是独立事件，由权限拦截层在子进程侧由
                    #  --permission-mode default 触发；Phase 4 完整接入）
            usage = msg.get("usage") or {}
            if usage and (usage.get("input_tokens") or usage.get("output_tokens")):
                out.append(Event(EventType.USAGE, {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                }))
            return out

        if t == "user":
            # 工具结果（aiclawcode 把 tool_result 包在 user role 里）
            msg = raw.get("message") or {}
            content_blocks = msg.get("content") or []
            for block in content_blocks:
                if block.get("type") != "tool_result":
                    continue
                payload = {
                    "id": block.get("tool_use_id"),
                    "is_error": bool(block.get("is_error")),
                    "content": block.get("content"),
                }
                if payload["is_error"]:
                    classified = _classify_tool_result_error(payload["content"])
                    if classified:
                        payload.update(classified)
                out.append(Event(EventType.TOOL_RESULT, payload))
            return out

        if t == "control_request":
            req = raw.get("request") or {}
            if req.get("subtype") == "can_use_tool":
                request_id = raw.get("request_id") or str(uuid4())
                tool_name = req.get("tool_name") or "unknown"
                tool_input = req.get("input") or {}

                policy = self._policies.get((skill_id, user_id))
                decision = policy.evaluate(tool_name, tool_input) if policy else PermissionEvaluation(
                    decision="ask_user", reason="no policy"
                )

                # 后端可以直接代答的几种 decision，立即回复 aiclawcode 进程
                if decision.decision == "auto_allow":
                    asyncio.create_task(session.respond_permission(
                        request_id, "allow", updated_input=tool_input,
                    ))
                    return out
                if decision.decision == "deny":
                    asyncio.create_task(session.respond_permission(
                        request_id, "deny", message=decision.reason,
                    ))
                    out.append(Event(EventType.ERROR, {
                        "code": CodingAgentErrorCode.PERMISSION_VIOLATION.value,
                        "error": decision.reason,
                        "tool": tool_name,
                    }))
                    return out

                # notify_allow：放行但通知前端
                if decision.decision == "notify_allow":
                    asyncio.create_task(session.respond_permission(
                        request_id, "allow", updated_input=tool_input,
                    ))
                    out.append(Event(EventType.PERMISSION_REQUEST, {
                        "request_id": request_id,
                        "tool": tool_name,
                        "input": tool_input,
                        "policy": "notify_allow",
                        "auto_approved": True,
                    }))
                    return out

                # ask_user：发给前端等待响应
                # 只有真正等待用户决策时才缓存 input；auto/notify/deny 都已经由后端代答，
                # 前端不应再出现一个可以重复响应的“确认”卡片。
                self._pending_perms.setdefault((skill_id, user_id), {})[request_id] = tool_input
                out.append(Event(EventType.PERMISSION_REQUEST, {
                    "request_id": request_id,
                    "tool": tool_name,
                    "input": tool_input,
                    "policy": "ask_user",
                    "auto_approved": False,
                }))
                # 超时保护：如果前端 N 秒内没回 permission_response,
                # 自动 deny 让 Node 子进程不至于死等
                asyncio.create_task(self._auto_deny_after_timeout(
                    skill_id=skill_id, user_id=user_id, request_id=request_id,
                    tool_name=tool_name, session=session,
                ))
                return out

        if t == "result":
            # 从 result 事件读真实 usage（assistant 事件里的 usage 都是 0）
            usage = raw.get("usage") or {}
            normalized_usage = {}
            if usage:
                normalized_usage = {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                }
                out.append(Event(EventType.USAGE, normalized_usage))
            done_payload = {
                "subtype": raw.get("subtype"),
                "is_error": bool(raw.get("is_error")),
                "duration_ms": raw.get("duration_ms"),
                "total_cost_usd": raw.get("total_cost_usd"),
                "stop_reason": raw.get("stop_reason"),
            }
            if normalized_usage:
                done_payload["usage"] = normalized_usage
            out.append(Event(EventType.DONE, done_payload))
            return out

        # 未知 / keep_alive / stream_event 等：忽略
        return out

    @staticmethod
    def _build_file_change_event(
        tool_name: str,
        tool_input: dict,
        work_dir: Path | str | None = None,
    ) -> Event:
        """从 Edit/Write/MultiEdit 的 input 提取文件变更摘要。"""
        path = tool_input.get("file_path") or ""
        if path:
            stripped = _strip_tool_paths({"file_path": path}, work_dir)
            path = stripped.get("file_path") or path
        operation = "edit" if tool_name == "Edit" else "write" if tool_name == "Write" else "multi_edit"
        payload: dict[str, Any] = {"path": path, "operation": operation}

        if tool_name == "Edit":
            payload["old_string"] = tool_input.get("old_string", "")
            payload["new_string"] = tool_input.get("new_string", "")
        elif tool_name == "Write":
            content = tool_input.get("content", "")
            payload["snippet_preview"] = content[:500]
        elif tool_name == "MultiEdit":
            edits = tool_input.get("edits") or []
            payload["edit_count"] = len(edits)
            payload["edits_preview"] = edits[:5]  # 前 5 条预览

        return Event(EventType.FILE_CHANGE, payload)


# 全局单例
session_service = SessionService()
