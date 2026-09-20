"""Optimizer Pydantic schemas"""

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    """创建优化会话"""
    name: str = Field(..., max_length=200)
    goal: str = Field(..., max_length=200)  # 提高命中率/降低误判/降低成本
    editable_zones: list[str] = Field(default=["branch_conditions", "thresholds", "branch_order"])
    frozen_zones: list[str] = Field(default=["frontmatter", "output_definition"])
    max_iterations: int = Field(default=20, ge=1, le=100)
    max_tokens: int = Field(default=500000, ge=10000)
    max_sandbox_runs: int = Field(default=100, ge=10)
    benchmark_pack_id: str | None = None  # 已有 pack 则指定，否则自动构建


class CreateBenchmarkPackRequest(BaseModel):
    """创建评测包"""
    name: str = Field(..., max_length=200)
    source: str = Field(default="mixed")  # decision_log/test_cases/manual/mixed
    description: str | None = None
    min_rating: int | None = Field(default=None, ge=1, le=5)  # 过滤 DecisionLog 的最低评分
    max_cases: int = Field(default=200, ge=10)


class PromoteCandidateRequest(BaseModel):
    """晋升候选版本"""
    commit_message: str | None = None
