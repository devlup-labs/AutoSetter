"""
ollama_client.py
=================
Thin, well-documented wrapper around the official `ollama` Python library
(https://github.com/ollama/ollama-python).

Only the modern, non-deprecated `Client.chat(...)` API is used here (the
old `ollama.generate` completion-style API and any REST-by-hand calls are
intentionally avoided per project requirements).

This module knows nothing about competitive programming or prompt content
-- it is a generic "talk to my local Ollama server" helper, used by both
`json_generator.py` (vision calls) and `file_generator.py` (text calls).
"""

from __future__ import annotations

from typing import List, Optional

import ollama
from ollama import Client


class OllamaCallError(Exception):
    """Raised when a call to the local Ollama server fails for any reason."""


class OllamaClient:
    """
    Wraps a single `ollama.Client` connection to the local Ollama daemon.

    Parameters
    ----------
    host : str
        Base URL of the local Ollama server. Defaults to the standard
        local address Ollama listens on.
    default_model : str
        Model name to use when a call site doesn't explicitly override it,
        e.g. "qwen2.5vl" for vision calls.
    request_timeout : Optional[float]
        Optional timeout (seconds) forwarded to the underlying HTTP client.
    """

    def __init__(
        self,
        host: str = "http://localhost:11434",
        default_model: str = "qwen2.5vl:3b",
        request_timeout: Optional[float] = None,
    ) -> None:
        self.default_model = default_model
        # The official client accepts a `timeout` kwarg forwarded to httpx.
        client_kwargs = {"host": host}
        if request_timeout is not None:
            client_kwargs["timeout"] = request_timeout

        self._client: Client = ollama.Client(**client_kwargs)

    def chat_with_images(
        self,
        prompt: str,
        images_base64: List[str],
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> str:
        """
        Send a single user turn containing text + one or more images to a
        vision-capable model (e.g. qwen2.5vl) and return the assistant's
        text reply.

        Parameters
        ----------
        prompt : str
            The instruction/question to send alongside the image(s).
        images_base64 : List[str]
            Base64-encoded image payloads (as produced by
            `image_parser.load_image_as_base64`).
        model : Optional[str]
            Overrides `self.default_model` if provided.
        temperature : float
            Sampling temperature. Kept low by default for deterministic,
            structured extraction tasks.

        Returns
        -------
        str
            The raw text content of the model's reply.
        """
        if not images_base64:
            raise OllamaCallError("chat_with_images called with an empty image list")

        chosen_model = model or self.default_model

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
                options={"temperature": temperature},
            )
        except Exception as exc:  # ollama raises ResponseError / ConnectionError etc.
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
        Send a plain text-only prompt to a (typically non-vision) model and
        return the assistant's text reply. Used for generating
        statement.md / solution.cpp / validator.cpp / generator.cpp /
        checker.cpp from the already-extracted JSON specification.

        Parameters
        ----------
        prompt : str
            Fully rendered prompt (JSON placeholder already substituted).
        model : Optional[str]
            Overrides `self.default_model` if provided.
        temperature : float
            Sampling temperature.

        Returns
        -------
        str
            The raw text content of the model's reply.
        """
        chosen_model = model or self.default_model

        try:
            response = self._client.chat(
                model=chosen_model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": temperature},
            )
        except Exception as exc:
            raise OllamaCallError(
                f"Text call to model '{chosen_model}' failed: {exc}"
            ) from exc

        return self._extract_text(response)

    @staticmethod
    def _extract_text(response) -> str:
        """
        Normalize the response object returned by `ollama.Client.chat(...)`
        into a plain string. The official library returns a `ChatResponse`
        object (dict-like) with a `message.content` field.
        """
        try:
            # ollama-python >= 0.4 returns a typed ChatResponse object that
            # supports both attribute and dict-style access.
            content = response.message.content
        except AttributeError:
            # Fallback for plain-dict responses, for robustness across
            # library versions.
            content = response["message"]["content"]

        if content is None or not content.strip():
            raise OllamaCallError(
                "Model returned an empty response. This can happen if the "
                "model doesn't support the vision call it was sent, ran out "
                "of context, or the Ollama server hit an internal error."
            )

        return content.strip()
