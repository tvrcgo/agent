import importlib
import logging

from agent.core.config import ModelConfig
from agent.core.llm import LLMProvider, LLMResponse

__all__ = ["LLMProvider", "LLMResponse", "create_llm"]

logger = logging.getLogger(__name__)


def create_llm(config: ModelConfig) -> LLMProvider:
    """Create an LLMProvider from config.model.provider.

    The provider value is treated as a module path under agent.providers
    (e.g. ``provider: openai`` loads ``agent.providers.openai``).
    The first LLMProvider subclass found in the module is instantiated.
    """
    module_path = f"agent.providers.{config.provider}"
    provider_cls: type[LLMProvider] | None = None

    try:
        module = importlib.import_module(module_path)
    except ImportError:
        logger.error("Failed to import provider module: %s", module_path, exc_info=True)
        raise

    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, LLMProvider)
            and attr is not LLMProvider
        ):
            provider_cls = attr
            break

    if provider_cls is None:
        raise ValueError(f"No LLMProvider subclass found in {module_path}")

    return provider_cls(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.name,
    )
