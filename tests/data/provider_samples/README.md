# Provider wire samples

Request/response shapes used by the mocked-transport tests (`tests/test_providers_http.py`).
Copied or adapted from the provider documentation on 2026-10-09 (R13):

| File | Source |
| --- | --- |
| `anthropic_stream.sse` | https://docs.anthropic.com/en/docs/build-with-claude/streaming (verbatim example) |
| `anthropic_stream_error.sse` | same page, "Error events" (`overloaded_error` mid-stream) |
| `anthropic_error_*.json` | https://docs.anthropic.com/en/api/errors ("Error shapes"; status per error type) |
| `anthropic_tool_use.json` | https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/overview (`tool_use` block, `stop_reason`) |
| `anthropic_models.json` | https://docs.anthropic.com/en/api/models-list |
| `grok_chat.json` | https://docs.x.ai/developers/rest-api-reference/inference/chat-completions (verbatim response example) |
| `grok_tool_call.json` | same page: `choices[0].message.tool_calls`, OpenAI-compatible function call |
| `grok_stream.sse` | same page: data-only SSE terminated by `data: [DONE]` |
| `grok_models.json` | https://docs.x.ai/developers/rest-api-reference/inference/models |

Model ids in the samples are placeholders: code never relies on a model name (D25).
