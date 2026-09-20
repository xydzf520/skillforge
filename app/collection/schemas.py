"""Pydantic schemas for /api/collection."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CollectionFetchRequest(BaseModel):
    mcp_tool_name: str = Field(..., min_length=1, max_length=120)
    skill_id: str = Field(..., min_length=1, max_length=120)
    run_id: str = Field(..., min_length=1, max_length=120)
    instance_id: str = Field(..., min_length=1, max_length=120)
    source_id: str | None = None
    platform: str = Field(..., min_length=1, max_length=30)
    shop_id: str = Field(..., min_length=1, max_length=50)
    data_scope: str = Field(..., min_length=1, max_length=80)
    endpoint_family: str = Field(..., min_length=1, max_length=80)
    warning_group: str = Field(..., min_length=1, max_length=80)
    credential_scope: str | None = None
    credential_plan_id: str | None = None
    retry_of_proof_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class CollectionBatchItem(BaseModel):
    request_id: str | None = Field(default=None, max_length=120)
    retry_of_proof_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class CollectionFetchBatchRequest(BaseModel):
    mcp_tool_name: str = Field(..., min_length=1, max_length=120)
    skill_id: str = Field(..., min_length=1, max_length=120)
    run_id: str = Field(..., min_length=1, max_length=120)
    instance_id: str = Field(..., min_length=1, max_length=120)
    source_id: str | None = None
    platform: str = Field(..., min_length=1, max_length=30)
    shop_id: str = Field(..., min_length=1, max_length=50)
    data_scope: str = Field(..., min_length=1, max_length=80)
    endpoint_family: str = Field(..., min_length=1, max_length=80)
    warning_group: str = Field(..., min_length=1, max_length=80)
    credential_scope: str | None = None
    credential_plan_id: str | None = None
    concurrency: int | None = Field(default=None, ge=1, le=20)
    items: list[CollectionBatchItem] = Field(..., min_length=1, max_length=200)
