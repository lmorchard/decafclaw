"""OpenAI provider — direct API access (no proxy)."""

from .openai_compat import OpenAICompatProvider

# Default OpenAI API base URL
_DEFAULT_URL = "https://api.openai.com/v1/chat/completions"
_DEFAULT_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"


class OpenAIProvider(OpenAICompatProvider):
    """Provider for direct OpenAI API access.

    Inherits from OpenAICompatProvider since OpenAI uses the same format.
    The only differences are the default URL and that tool call IDs
    don't need sanitization (no LiteLLM __thought__ artifacts).
    """

    def __init__(self, api_key: str = "", url: str = ""):
        super().__init__(
            url=url or _DEFAULT_URL,
            api_key=api_key,
        )

    def _embeddings_url(self) -> str:
        """Use OpenAI's embeddings endpoint."""
        if self.url == _DEFAULT_URL:
            return _DEFAULT_EMBEDDINGS_URL
        return super()._embeddings_url()
