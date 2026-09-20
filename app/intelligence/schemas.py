from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    skill_id: str = Field(..., min_length=1, max_length=100)
    skill_git_commit_full: str = Field(..., min_length=4, max_length=80)
    run_id: str = Field(..., min_length=1, max_length=100)
    instance_id: str | None = Field(default=None, max_length=100)
    prompt_version: str = Field(default="analysis_v1", min_length=1, max_length=50)
    context_pack: dict
    cache_key: str | None = Field(default=None, max_length=128)


class AnalyzeResponse(BaseModel):
    output: dict
    model: str
    usage: dict
    cache_hit: bool
    cache_id: int | None
    cache_hit_of_run_id: str | None = None
    cost_usd: float
    degraded: bool = False
    degraded_reason: str | None = None
    analysis_backend: str | None = None
    analysis_agent_id: str | None = None
    analysis_delegate_route: dict | None = None
    active_model_deployment: dict | None = None
    skipped_model_deployments: list[dict] | None = None
    deployed_model_inference: dict | None = None
