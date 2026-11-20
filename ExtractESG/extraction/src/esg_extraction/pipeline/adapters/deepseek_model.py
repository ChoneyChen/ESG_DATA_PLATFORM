"""DeepSeek transport for text and Flash page images."""

from esg_contracts.model_providers import DEEPSEEK_MODELS
from .chat_completion_model import ChatCompletionModel


class DeepSeekModel(ChatCompletionModel):
    provider_name = "deepseek"
    display_name = "DeepSeek"
    endpoint = "https://api.deepseek.com/chat/completions"
    models = DEEPSEEK_MODELS
    vision_models = ("deepseek-flash",)
    request_options = {"thinking": {"type": "disabled"}}
    image_options = {"detail": "high"}
