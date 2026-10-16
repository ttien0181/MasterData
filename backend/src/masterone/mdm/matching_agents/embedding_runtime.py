"""Select the configured embedding runtime; historical E5 remains a dev baseline."""
from masterone.config import get_settings


def advanced_config(base, fallback):
    if get_settings().embedding_provider == 'e5_local':
        from .encoder_runtime import advanced_config as local_config
        return local_config(base, fallback)
    from .openrouter_embedding import advanced_config as api_config
    return api_config(base, fallback)


def EncoderRuntime(identity, budget, progress=None):  # noqa: N802 - preserve the runtime constructor contract
    if get_settings().embedding_provider == 'e5_local':
        from .encoder_runtime import EncoderRuntime as Runtime
    else:
        from .openrouter_embedding import OpenRouterEmbeddingRuntime as Runtime
    return Runtime(identity, budget, progress)
