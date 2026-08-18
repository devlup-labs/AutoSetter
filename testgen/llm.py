"""Talk to a local model.

Deliberately small. Everything interesting about extraction lives in the prompt
and in what happens to the answer, not here, and this package should not grow a
dependency on the rest of AutoSetter just to make one HTTP call.

Two things it does that a bare client does not:

  json mode      Ollama can be told the reply must parse as JSON. That removes
                 a whole class of failure (prose wrapped around the object)
                 before the schema gate ever sees it.
  thinking       qwen3 and friends emit their reasoning in <think> blocks. That
                 is not part of the answer and would break json parsing, so it
                 is asked for off and stripped anyway when the model ignores it.
"""

from __future__ import annotations

import re
from typing import Protocol

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:4b"

# Ollama defaults to a 4096 token window. The extraction prompt is the schema
# plus four worked examples plus the statement, which is comfortably past that,
# and a repair prompt carries the previous reply on top. Overflowing silently
# drops the FRONT of the prompt, so the model loses the schema and the rules
# and keeps only the statement -- which looks exactly like a model that cannot
# follow instructions. It is not. It never saw them.
DEFAULT_CONTEXT = 16384

# Reasoning models wrap their scratch work in these. Some honour a request to
# turn it off, some do not, so it gets removed either way.
THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


class LLMError(RuntimeError):
    """Raised when the model could not be reached or said nothing usable."""


class Completer(Protocol):
    """What extraction needs from a model: text in, text out.

    Narrow on purpose, so the tests can pass a canned function instead of
    standing up a server.
    """

    def __call__(self, prompt: str) -> str: ...


class Model:
    """A local Ollama model, called one prompt at a time."""

    def __init__(
        self,
        name: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        temperature: float = 0.0,
        json_mode: bool = True,
        timeout: float = 600.0,
        context: int = DEFAULT_CONTEXT,
    ) -> None:
        self.name = name
        self.host = host
        # Extraction is transcription, not writing. There is one right answer
        # and no reason to sample away from it.
        self.temperature = temperature
        self.json_mode = json_mode
        self.timeout = timeout
        self.context = context
        self._client = None

    def _connect(self):
        if self._client is not None:
            return self._client
        try:
            import ollama
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise LLMError(
                "the ollama package is not installed; "
                "pip install -r requirements.txt"
            ) from exc
        self._client = ollama.Client(host=self.host, timeout=self.timeout)
        return self._client

    def __call__(self, prompt: str) -> str:
        client = self._connect()

        kwargs = {
            "model": self.name,
            "messages": [{"role": "user", "content": prompt}],
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.context,
            },
        }
        if self.json_mode:
            kwargs["format"] = "json"

        try:
            try:
                # Not every client version takes this, so it is tried and then
                # dropped rather than assumed. Models that ignore it get their
                # <think> block stripped in _text instead.
                response = client.chat(think=False, **kwargs)
            except TypeError:
                response = client.chat(**kwargs)
        except Exception as exc:
            raise LLMError(
                f"call to {self.name!r} at {self.host} failed: {exc}\n"
                f"is `ollama serve` running, and has {self.name!r} been pulled?"
            ) from exc

        return self._text(response)

    @staticmethod
    def _text(response: object) -> str:
        content = getattr(getattr(response, "message", None), "content", None)
        if content is None and isinstance(response, dict):
            content = response.get("message", {}).get("content")

        if not content or not content.strip():
            raise LLMError(
                "the model returned nothing. Usually the context window was "
                "exceeded, or the server ran out of memory loading the model."
            )

        return THINK_BLOCK.sub("", content).strip()


def available(host: str = DEFAULT_HOST, timeout: float = 3.0) -> tuple[bool, str]:
    """Is a server up, and which models does it have?

    Used to fail with something a person can act on rather than a stack trace
    from inside an HTTP library.
    """
    try:
        import ollama

        tags = ollama.Client(host=host, timeout=timeout).list()
    except Exception as exc:
        return False, f"no Ollama server at {host}: {exc}"

    models = tags.get("models", []) if isinstance(tags, dict) else tags.models
    names = sorted(
        m.get("model", m.get("name", "?")) if isinstance(m, dict) else m.model
        for m in models
    )
    if not names:
        return False, f"the server at {host} has no models pulled"
    return True, ", ".join(names)
