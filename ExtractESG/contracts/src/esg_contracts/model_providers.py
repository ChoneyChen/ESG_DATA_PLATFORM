"""Public model selection and provider status contain no credentials."""

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, model_validator

ModelProvider = Literal["local", "deepseek", "dashscope"]
CloudModelProvider = Literal["deepseek", "dashscope"]
DeepSeekModelId = Literal["deepseek-flash", "deepseek-v4-pro"]
DashScopeModelId = Literal["qwen3.8-omni-flash"]
ModelId = DeepSeekModelId | DashScopeModelId
DEEPSEEK_MODELS = get_args(DeepSeekModelId)
DASHSCOPE_MODELS = get_args(DashScopeModelId)
CLOUD_MODELS = {"deepseek": DEEPSEEK_MODELS, "dashscope": DASHSCOPE_MODELS}
CLOUD_NAMES = {"deepseek": "DeepSeek", "dashscope": "DashScope"}


class ModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: ModelProvider = "local"
    model: ModelId | None = None

    @model_validator(mode="after")
    def check_model(self):
        if self.provider == "local" and self.model is not None:
            raise ValueError("The local model is configured in the backend")
        if self.provider in CLOUD_MODELS and self.model not in CLOUD_MODELS[self.provider]:
            raise ValueError(f"Select a {CLOUD_NAMES[self.provider]} model")
        return self


class ModelProviderStatus(BaseModel):
    provider: ModelProvider
    configured: bool
    models: list[ModelId]


class ModelProviders(BaseModel):
    providers: list[ModelProviderStatus]
