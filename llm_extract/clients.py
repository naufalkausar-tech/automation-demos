from __future__ import annotations

import copy
import importlib
import json
import os
from typing import Any, Callable, Mapping, Sequence

from .core import LLMClientError, LLMConfigError

DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class FakeClient:
    def __init__(self, responses: Sequence[Any] | None = None, handler: Callable[[str], Any] | None = None):
        self.responses = list(responses or [])
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, *, system: str, prompt: str, schema: Mapping[str, Any]) -> str:
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        if self.handler is not None:
            value = self.handler(prompt)
        elif self.responses:
            value = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        else:
            raise LLMClientError("FakeClient has no scripted responses left")
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) else json.dumps(value)


def split_type_lists(schema: Any) -> Any:
    if isinstance(schema, list):
        return [split_type_lists(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    node = {}
    for key, value in schema.items():
        if key in ("properties", "$defs", "definitions"):
            node[key] = {name: split_type_lists(sub) for name, sub in value.items()}
        elif key in ("items", "additionalProperties", "anyOf", "oneOf", "allOf"):
            node[key] = split_type_lists(value)
        else:
            node[key] = value
    if "type" not in node and "properties" in node:
        node["type"] = "object"
    types = node.get("type")
    if isinstance(types, list):
        rest = {k: v for k, v in node.items() if k not in ("type", "description", "title")}
        variants = [{"type": "null"} if t == "null" else {**rest, "type": t} for t in types]
        node = {k: node[k] for k in ("description", "title") if k in node}
        node["anyOf"] = variants
    return node


def api_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    normalized = split_type_lists(copy.deepcopy(dict(schema)))
    try:
        from anthropic import transform_schema
    except ImportError:
        return normalized
    try:
        return transform_schema(copy.deepcopy(normalized))
    except (ValueError, AssertionError, TypeError):
        return normalized


class AnthropicClient:
    def __init__(
        self,
        model: str | None = None,
        max_tokens: int = 16000,
        fallbacks: bool | None = None,
        effort: str | None = None,
        sdk_client: Any = None,
    ):
        self.model = model or os.environ.get("LLM_EXTRACT_MODEL") or DEFAULT_ANTHROPIC_MODEL
        self.max_tokens = max_tokens
        self.fallbacks = os.environ.get("LLM_EXTRACT_FALLBACKS", "1") != "0" if fallbacks is None else fallbacks
        self.effort = effort or os.environ.get("LLM_EXTRACT_EFFORT") or None
        if sdk_client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise LLMConfigError("provider 'anthropic' needs the SDK: pip install anthropic") from exc
            sdk_client = anthropic.Anthropic(max_retries=4)
        self._client = sdk_client

    def request_kwargs(self, system: str, prompt: str, schema: Mapping[str, Any]) -> dict[str, Any]:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": api_schema(schema)}}
        if self.effort:
            output_config["effort"] = self.effort
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": output_config,
        }
        if self.fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def _send(self, kwargs: dict[str, Any]) -> Any:
        if self.fallbacks:
            return self._client.beta.messages.create(**kwargs)
        return self._client.messages.create(**kwargs)

    def complete_json(self, *, system: str, prompt: str, schema: Mapping[str, Any]) -> str:
        kwargs = self.request_kwargs(system, prompt, schema)
        try:
            import anthropic
        except ImportError:
            response = self._send(kwargs)
        else:
            try:
                response = self._send(kwargs)
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
                raise LLMConfigError(f"Anthropic credentials rejected: {exc}") from exc
            except (anthropic.NotFoundError, anthropic.BadRequestError) as exc:
                raise LLMConfigError(f"Anthropic rejected the request (check model/schema): {exc}") from exc
            except anthropic.RateLimitError as exc:
                raise LLMClientError(f"rate limited after retries: {exc}") from exc
            except anthropic.APIStatusError as exc:
                raise LLMClientError(f"API error {exc.status_code}: {exc}") from exc
            except anthropic.APIConnectionError as exc:
                raise LLMClientError(f"connection error: {exc}") from exc
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            category = getattr(getattr(response, "stop_details", None), "category", None)
            raise LLMClientError(f"model declined the request (category: {category})")
        if stop_reason == "max_tokens":
            raise LLMClientError("response hit max_tokens before the JSON was complete")
        text = "".join(getattr(block, "text", "") for block in response.content if getattr(block, "type", None) == "text")
        if not text.strip():
            raise LLMClientError("model returned no text")
        return text


def load_client(provider: str | None = None, model: str | None = None, fake_responses: Sequence[Any] | None = None):
    provider = provider or os.environ.get("LLM_EXTRACT_PROVIDER")
    if not provider:
        raise LLMConfigError(
            "no LLM provider configured: set LLM_EXTRACT_PROVIDER=anthropic (or pass --provider), "
            "or plug in your own client with --provider package.module:factory"
        )
    if provider == "anthropic":
        return AnthropicClient(model=model)
    if provider == "fake":
        return FakeClient(responses=fake_responses or [])
    if ":" in provider:
        module_name, _, attribute = provider.partition(":")
        factory = getattr(importlib.import_module(module_name), attribute)
        return factory()
    raise LLMConfigError(f"unknown provider {provider!r}; use anthropic, fake, or package.module:factory")
