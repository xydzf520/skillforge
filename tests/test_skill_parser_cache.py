"""SkillParser LRU 缓存测试（v2.8.1 D3）。"""

from __future__ import annotations

from app.skills.core.parser import SkillParser, skill_parser


SAMPLE_MD = """---
name: test-skill
department: 测试部
trigger_type: manual
risk_level: R2
---

## 目的

一个测试用的 Skill。

## 执行步骤

### step_1: 判断

- 条件: x > 0 → 正数 → 记录
- 其他情况 → 零或负数 → 跳过

## 反例

- 场景: 输入为负数 → 应该跳过而不是报错
"""


def test_cache_hit_on_same_content():
    SkillParser.clear_cache()
    a = skill_parser.parse(SAMPLE_MD)
    stats1 = SkillParser.cache_stats()
    assert stats1["misses"] == 1
    assert stats1["hits"] == 0
    assert stats1["size"] == 1

    b = skill_parser.parse(SAMPLE_MD)
    stats2 = SkillParser.cache_stats()
    assert stats2["misses"] == 1
    assert stats2["hits"] == 1
    assert stats2["size"] == 1

    # 结果应该等价（字段相同）
    assert a.frontmatter == b.frontmatter
    assert len(a.steps) == len(b.steps)


def test_cache_miss_on_different_content():
    SkillParser.clear_cache()
    skill_parser.parse(SAMPLE_MD)
    skill_parser.parse(SAMPLE_MD + "\n## 输出\n- foo: bar")
    stats = SkillParser.cache_stats()
    assert stats["misses"] == 2
    assert stats["size"] == 2


def test_mutation_does_not_poison_cache():
    """调用方修改返回的 SkillStructured，不应影响缓存内容。"""
    SkillParser.clear_cache()
    a = skill_parser.parse(SAMPLE_MD)
    a.frontmatter["department"] = "被篡改部"  # 调用方 mutation
    a.steps.append(type(a.steps[0])(id="step_injected", name="注入的"))

    b = skill_parser.parse(SAMPLE_MD)  # 命中缓存
    # b 应该是原始值，不受 a 的 mutation 影响
    assert b.frontmatter["department"] == "测试部"
    assert all(s.id != "step_injected" for s in b.steps)


def test_empty_not_cached():
    SkillParser.clear_cache()
    skill_parser.parse("")
    stats = SkillParser.cache_stats()
    # 空字符串短路，既不 hit 也不 miss
    assert stats["size"] == 0


def test_cache_lru_eviction():
    """超过 _CACHE_MAX 时淘汰最老的。"""
    SkillParser.clear_cache()
    old_max = SkillParser._CACHE_MAX
    SkillParser._CACHE_MAX = 3
    try:
        for i in range(5):
            # 每次生成唯一内容
            md = SAMPLE_MD + f"\n## 输出\n- f{i}: v{i}"
            skill_parser.parse(md)
        stats = SkillParser.cache_stats()
        assert stats["size"] == 3  # 只保留最近 3 条
        assert stats["misses"] == 5
    finally:
        SkillParser._CACHE_MAX = old_max
        SkillParser.clear_cache()


def test_clear_cache_returns_prev_size():
    SkillParser.clear_cache()
    skill_parser.parse(SAMPLE_MD)
    skill_parser.parse(SAMPLE_MD + "\n## 输出\n- a: 1")
    assert SkillParser.clear_cache() == 2
    assert SkillParser.cache_stats()["size"] == 0
