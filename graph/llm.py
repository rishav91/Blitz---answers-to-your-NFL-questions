"""Chat model access via `init_chat_model` — no vendor SDK hard-coupling (ADR-006).

Provider and model are read from env so swapping Claude/OpenAI/Groq/etc. is a
`.env` change, not a code change. See .env.example for the option list.
"""

import os

from langchain.chat_models import init_chat_model

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_PROVIDER = "anthropic"


def get_chat_model(temperature: float = 0.0):
    model = os.getenv("CHAT_MODEL", DEFAULT_MODEL)
    provider = os.getenv("CHAT_MODEL_PROVIDER", DEFAULT_PROVIDER)
    return init_chat_model(model, model_provider=provider, temperature=temperature)
