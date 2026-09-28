"""
autosetter.llm
==============
Lightweight, typed wrapper around the official `ollama` Python client.

Provides:
- `OllamaClient`: Manages chat interactions (multimodal vision calls and text-only calls).
- `OllamaCallError`: Exception raised when local Ollama API requests fail.
"""

from __future__ import annotations

from typing import Any, List, Optional

# pyrefly: ignore [missing-import]
import ollama
# pyrefly: ignore [missing-import]
from ollama import Client

from autosetter.config import (
    DEFAULT_NUM_CTX,
    DEFAULT_OLLAMA_HOST,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_VISION_MODEL,
)


class OllamaCallError(Exception):
    """Raised when an Ollama API call fails or returns an invalid payload."""


class OllamaClient:
    """
    Wraps an `ollama.Client` connection to the local Ollama daemon.

    Parameters
    ----------
    host : str
        Base URL of the local Ollama server.
    default_model : str
        Default model name if not overridden per call.
    request_timeout : Optional[float]
        Optional timeout in seconds for underlying HTTP requests (default: 300.0).
    num_ctx : int
        Context window token size for inference calls (default: 16384).
    """

    def __init__(
        self,
        host: str = DEFAULT_OLLAMA_HOST,
        default_model: str = DEFAULT_VISION_MODEL,
        request_timeout: Optional[float] = DEFAULT_REQUEST_TIMEOUT,
        num_ctx: int = DEFAULT_NUM_CTX,
    ) -> None:
        self.host = host
        self.default_model = default_model
        self.num_ctx = num_ctx
        self.request_timeout = request_timeout
        client_kwargs: dict[str, Any] = {"host": self.host}
        if request_timeout is not None:
            client_kwargs["timeout"] = request_timeout

        self._client: Client = ollama.Client(**client_kwargs)

    def check_connection(self, required_models: Optional[List[str]] = None) -> List[str]:
        """
        Verify that the Ollama server is accessible and reachable.
        If required_models is given, verifies whether expected models are listed.
        Returns the list of available model names.
        """
        try:
            resp = self._client.list()
            available: List[str] = []
            if hasattr(resp, "models"):
                available = [m.model for m in resp.models if hasattr(m, "model")]
            elif isinstance(resp, dict) and "models" in resp:
                available = [m.get("model") or m.get("name", "") for m in resp["models"]]
            return available
        except Exception as exc:
            raise OllamaCallError(
                f"Cannot connect to Ollama server at '{self.host}'.\n"
                f"If using Google Colab with Cloudflare Tunnel:\n"
                f"  1. Verify your Colab notebook is actively running.\n"
                f"  2. Confirm the cloudflared tunnel is active.\n"
                f"  3. Set OLLAMA_HOST to the fresh trycloudflare.com URL.\n"
                f"Details: {exc}"
            ) from exc

    def chat_with_images(
        self,
        prompt: str,
        images_base64: List[str],
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> str:
        """
        Send a multimodal request (prompt + base64 images) to a vision-capable model.

        Parameters
        ----------
        prompt : str
            The text instruction sent alongside the image(s).
        images_base64 : List[str]
            List of base64-encoded PNG payloads.
        model : Optional[str]
            Model name overriding `self.default_model`.
        temperature : float
            Sampling temperature.

        Returns
        -------
        str
            The textual response content.
        """
        if not images_base64:
            raise OllamaCallError("chat_with_images called with an empty image list")

        chosen_model = model or self.default_model

        options: dict[str, Any] = {"temperature": temperature}
        if self.num_ctx:
            options["num_ctx"] = self.num_ctx

        try:
            response = self._client.chat(
                model=chosen_model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                        "images": images_base64,
                    }
                ],
                options=options,
            )
        except Exception as exc:
            raise OllamaCallError(
                f"Vision call to model '{chosen_model}' failed: {exc}"
            ) from exc

        return self._extract_text(response)

    def chat_text(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> str:
        """
        Send a plain-text prompt to an LLM and return its response.

        Parameters
        ----------
        prompt : str
            Rendered prompt text.
        model : Optional[str]
            Model name overriding `self.default_model`.
        temperature : float
            Sampling temperature.

        Returns
        -------
        str
            The textual response content.
        """
        chosen_model = model or self.default_model

        options: dict[str, Any] = {"temperature": temperature}
        if self.num_ctx:
            options["num_ctx"] = self.num_ctx

        try:
            response = self._client.chat(
                model=chosen_model,
                messages=[{"role": "user", "content": prompt}],
                options=options,
            )
        except Exception as exc:
            raise OllamaCallError(
                f"Text call to model '{chosen_model}' failed: {exc}"
            ) from exc

        return self._extract_text(response)

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Extract and normalize string content from an Ollama ChatResponse."""
        try:
            content = response.message.content
        except AttributeError:
            content = response.get("message", {}).get("content")

        if content is None or not content.strip():
            raise OllamaCallError(
                "Model returned an empty response. Verify model availability, "
                "context window limits, or Ollama server health."
            )

        return content.strip()
