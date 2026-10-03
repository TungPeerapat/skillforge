# Adding a provider

Providers are the only place SkillForge talks to the network. Keep them thin:
build a request, parse a response, raise `ProviderError` on anything unexpected.

## 1. Implement the protocol

```python
# src/skillforge/providers/http.py
class MyVendorProvider(HTTPProvider):
    id = "myvendor"

    @property
    def default_base_url(self) -> str:
        return "https://api.myvendor.example/v1"

    @property
    def default_model(self) -> str:
        return "my-model"

    def _endpoint(self) -> str:
        return f"{self._base_url}/completions"

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self._api_key}"}

    def _payload(self, request: LLMRequest) -> dict[str, object]:
        return {
            "model": self.model,
            "prompt": request.prompt,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }

    def _parse(self, data: dict[str, object]) -> LLMResponse:
        return LLMResponse(text=str(data["text"]), model=self.model, provider=self.id)
```

`HTTPProvider` gives you `structured_generate()` with schema validation, one
retry on invalid JSON, timeout handling, and API-key redaction in `describe()`.

## 2. Register it

```python
# src/skillforge/providers/registry.py
NETWORK_PROVIDERS = frozenset({"openai", "anthropic", "openrouter", "openai-compatible", "myvendor"})

def build_provider(provider_id: str, settings: Settings) -> LLMProvider:
    ...
    if normalized == "myvendor":
        return MyVendorProvider(**common)
```

Add the API-key environment variable to `_env_for()` and to
`Settings.provider_api_key_env()` if it is the default provider.

## 3. Consent

`resolve_provider()` refuses remote providers unless the user passes
`--allow-external-llm` or sets `security.allow_external_transmission = true`.
Localhost base URLs are treated as local. Do not bypass this check.

## 4. Test it

- payload/parse round-trip with a literal response dict (see
  `tests/unit/test_providers_context.py`);
- `requires_network` is `False` for localhost endpoints;
- keys never appear in `describe()` or logs;
- invalid responses raise `ProviderError` rather than being trusted.

## Rules

1. **Never log prompts or keys.** Prompts may contain repository content.
2. **Never invent trust in the response.** Structured output is validated against
   the requested schema, and generated commands are cross-checked against
   repository evidence (`generator/enrichment.py`).
3. **One HTTP call per request.** Retries belong one level up.
4. **Fail loudly.** A provider error must surface as `ProviderError` (exit code 4),
   not as silently degraded output.
