"""Public model selection and provider status contain no credentials."""

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, model_validator

ModelProvider = Literal["local", "deepseek"]
DeepSeekModelId = Literal["deepseek-flash", "deepseek-v4-pro"]
DEEPSEEK_MODELS = get_args(DeepSeekModelId)


class ModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: ModelProvider = "local"
    model: DeepSeekModelId | None = None

    @model_validator(mode="after")
    def check_model(self):
        if self.provider == "deepseek" and self.model is None:
            raise ValueError("Select a DeepSeek model")
        if self.provider == "local" and self.model is not None:
            raise ValueError("The local model is configured in the backend")
        return self


class ModelProviderStatus(BaseModel):
    provider: ModelProvider
    configured: bool
    models: list[DeepSeekModelId]


class ModelProviders(BaseModel):
    providers: list[ModelProviderStatus]
