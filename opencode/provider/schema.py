"""Provider and Model schema types.

Branded types and Pydantic models for Provider/Model identification.
Equivalent to src/provider/schema.ts + src/provider/provider.ts Model/Info schemas.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Branded type aliases (Python doesn't have branded types, use NewType for documentation)
ProviderID = str
ModelID = str


class ModelCapabilities(BaseModel):
    temperature: bool = False
    reasoning: bool = False
    attachment: bool = False
    toolcall: bool = True
    input: ModelInputCapabilities = Field(default_factory=lambda: ModelInputCapabilities())
    output: ModelOutputCapabilities = Field(default_factory=lambda: ModelOutputCapabilities())
    interleaved: bool | dict[str, str] = False


class ModelInputCapabilities(BaseModel):
    text: bool = True
    audio: bool = False
    image: bool = False
    video: bool = False
    pdf: bool = False


class ModelOutputCapabilities(BaseModel):
    text: bool = True
    audio: bool = False
    image: bool = False
    video: bool = False
    pdf: bool = False


class ModelCost(BaseModel):
    input: float = 0
    output: float = 0
    cache: CacheCost = Field(default_factory=lambda: CacheCost())


class CacheCost(BaseModel):
    read: float = 0
    write: float = 0


class ModelLimit(BaseModel):
    context: int = 0
    input: int | None = None
    output: int = 0


class ModelApi(BaseModel):
    id: str
    url: str = ""
    npm: str = "@ai-sdk/openai-compatible"


class Model(BaseModel):
    """A resolved model with full metadata."""

    id: ModelID
    provider_id: ProviderID = Field(alias="providerID")
    api: ModelApi
    name: str
    family: str = ""
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)
    cost: ModelCost = Field(default_factory=ModelCost)
    limit: ModelLimit = Field(default_factory=ModelLimit)
    status: Literal["alpha", "beta", "deprecated", "active"] = "active"
    options: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    release_date: str = ""
    variants: dict[str, dict[str, Any]] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class ProviderInfo(BaseModel):
    """A resolved provider with its models."""

    id: ProviderID
    name: str
    source: Literal["env", "config", "custom", "api"] = "custom"
    env: list[str] = Field(default_factory=list)
    key: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    models: dict[str, Model] = Field(default_factory=dict)
