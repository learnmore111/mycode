"""Configuration Pydantic models.

"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# --- MCP Config ---


class McpLocalConfig(BaseModel):
    type: Literal["local"]
    command: list[str]
    environment: dict[str, str] | None = None
    enabled: bool | None = None
    timeout: int | None = None


class McpRemoteConfig(BaseModel):
    type: Literal["remote"]
    url: str
    enabled: bool | None = None
    headers: dict[str, str] | None = None
    timeout: int | None = None


McpConfig = McpLocalConfig | McpRemoteConfig


# --- Permission Config ---

PermissionAction = Literal["ask", "allow", "deny"]
PermissionRule = PermissionAction | dict[str, PermissionAction]
PermissionConfig = dict[str, PermissionRule]


# --- Agent Config ---


class AgentConfig(BaseModel):
    model: str | None = None
    variant: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    prompt: str | None = None
    description: str | None = None
    mode: Literal["subagent", "primary", "all"] | None = None
    hidden: bool | None = None
    color: str | None = None
    steps: int | None = None
    disable: bool | None = None
    permission: PermissionConfig | None = None
    options: dict[str, Any] | None = None


# --- Command Config ---


class CommandConfig(BaseModel):
    template: str
    description: str | None = None
    agent: str | None = None
    model: str | None = None
    subtask: bool | None = None


# --- Provider Model Config ---


class ModelCostConfig(BaseModel):
    input: float = 0
    output: float = 0
    cache_read: float = 0
    cache_write: float = 0


class ModelLimitConfig(BaseModel):
    context: int = 0
    input: int | None = None
    output: int = 0


class ModelConfig(BaseModel):
    id: str | None = None
    name: str | None = None
    family: str | None = None
    temperature: bool | None = None
    reasoning: bool | None = None
    attachment: bool | None = None
    tool_call: bool | None = None
    cost: ModelCostConfig | None = None
    limit: ModelLimitConfig | None = None
    status: Literal["alpha", "beta", "deprecated", "active"] | None = None
    options: dict[str, Any] | None = None
    headers: dict[str, str] | None = None
    release_date: str | None = None
    variants: dict[str, dict[str, Any]] | None = None
    provider: dict[str, str] | None = None


# --- Provider Config ---


class ProviderOptionsConfig(BaseModel):
    api_key: str | None = Field(None, alias="apiKey")
    base_url: str | None = Field(None, alias="baseURL")
    timeout: int | Literal[False] | None = None
    chunk_timeout: int | None = Field(None, alias="chunkTimeout")

    model_config = {"populate_by_name": True}


class ProviderConfig(BaseModel):
    name: str | None = None
    api: str | None = None
    npm: str | None = None
    env: list[str] | None = None
    models: dict[str, ModelConfig] | None = None
    options: dict[str, Any] | None = None
    whitelist: list[str] | None = None
    blacklist: list[str] | None = None


# --- Skills Config ---


class SkillsConfig(BaseModel):
    paths: list[str] | None = None
    urls: list[str] | None = None


# --- Server Config ---


class ServerConfig(BaseModel):
    port: int | None = None
    hostname: str | None = None
    mdns: bool | None = None
    cors: list[str] | None = None


# --- Compaction Config ---


class CompactionConfig(BaseModel):
    auto: bool | None = None
    prune: bool | None = None
    reserved: int | None = None


# --- Session Memory Config ---


class SessionMemoryModelConfig(BaseModel):
    """Model configuration for session memory summarization."""
    provider: str | None = None  # "openai" | "anthropic"
    name: str | None = None  # model name like "gpt-4o-mini"
    base_url: str | None = Field(None, alias="baseURL")
    api_key: str | None = Field(None, alias="apiKey")
    api_key_env: str | None = Field(None, alias="apiKeyEnv")

    model_config = {"populate_by_name": True}


class SessionMemoryConfig(BaseModel):
    """Configuration for auto-saving session memory/notes."""
    enabled: bool | None = None  # Whether to enable auto-save
    model: SessionMemoryModelConfig | None = None  # Model for summarization
    note_language: str | None = Field(None, alias="noteLanguage")  # "zh" | "en"
    min_duration_minutes: int | None = Field(None, alias="minDurationMinutes")  # Skip short sessions
    min_user_prompts: int | None = Field(None, alias="minUserPrompts")  # Skip if too few prompts
    max_notes_per_project: int | None = Field(None, alias="maxNotesPerProject")  # Limit notes
    max_recent_for_context: int | None = Field(None, alias="maxRecentForContext")  # Recent notes to load

    model_config = {"populate_by_name": True}


# --- Experimental Config ---


class ExperimentalConfig(BaseModel):
    disable_paste_summary: bool | None = None
    batch_tool: bool | None = None
    open_telemetry: bool | None = Field(None, alias="openTelemetry")
    primary_tools: list[str] | None = None
    continue_loop_on_deny: bool | None = None
    mcp_timeout: int | None = None

    model_config = {"populate_by_name": True}


# --- LSP Config ---


class LspServerConfig(BaseModel):
    command: list[str] | None = None
    extensions: list[str] | None = None
    disabled: bool | None = None
    env: dict[str, str] | None = None
    initialization: dict[str, Any] | None = None


# --- Formatter Config ---


class FormatterConfig(BaseModel):
    disabled: bool | None = None
    command: list[str] | None = None
    environment: dict[str, str] | None = None
    extensions: list[str] | None = None


# --- Watcher Config ---


class WatcherConfig(BaseModel):
    ignore: list[str] | None = None


# --- Enterprise Config ---


class EnterpriseConfig(BaseModel):
    url: str | None = None


# --- Main Config ---


class Config(BaseModel):
    """Root configuration model, mapping to the original Config.Info Zod schema."""

    schema_: str | None = Field(None, alias="$schema")
    log_level: str | None = Field(None, alias="logLevel")
    server: ServerConfig | None = None
    command: dict[str, CommandConfig] | None = None
    skills: SkillsConfig | None = None
    watcher: WatcherConfig | None = None
    snapshot: bool | None = None
    plugin: list[str | list[Any]] | None = None
    share: Literal["manual", "auto", "disabled"] | None = None
    autoupdate: bool | Literal["notify"] | None = None
    disabled_providers: list[str] | None = None
    enabled_providers: list[str] | None = None
    model: str | None = None
    small_model: str | None = None
    default_agent: str | None = None
    username: str | None = None
    agent: dict[str, AgentConfig] | None = None
    provider: dict[str, ProviderConfig] | None = None
    mcp: dict[str, dict[str, Any]] | None = None
    formatter: dict[str, FormatterConfig] | Literal[False] | None = None
    lsp: dict[str, LspServerConfig] | Literal[False] | None = None
    instructions: list[str] | None = None
    permission: PermissionConfig | None = None
    compaction: CompactionConfig | None = None
    session_memory: SessionMemoryConfig | None = Field(None, alias="sessionMemory")
    experimental: ExperimentalConfig | None = None
    enterprise: EnterpriseConfig | None = None

    model_config = {"populate_by_name": True, "extra": "ignore", "frozen": True}
