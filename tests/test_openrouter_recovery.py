import io
import json
from urllib.error import HTTPError

import pytest

import app.agent.llm as llm


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


def make_402(budget):
    body = json.dumps({
        "error": {
            "message": (
                f"This request requires more credits, "
                f"but can only afford {budget} tokens."
            )
        }
    }).encode("utf-8")

    return HTTPError(
        "https://openrouter.ai/api/v1/chat/completions",
        402,
        "Payment Required",
        {},
        io.BytesIO(body),
    )


def test_openrouter_reduces_token_budget_and_retries(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)

    requests = []

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data.decode("utf-8")))

        if len(requests) == 1:
            raise make_402(1000)

        return FakeResponse(json.dumps({
            "choices": [{
                "message": {
                    "content": '{"plan": [], "edits": []}'
                }
            }],
            "model": "test-model",
        }).encode("utf-8"))

    monkeypatch.setattr(llm, "urlopen", fake_urlopen)

    result = llm.generate_with_openrouter(
        "test prompt",
        "test system prompt",
    )

    assert json.loads(result) == {"plan": [], "edits": []}
    assert len(requests) == 2
    assert requests[1]["max_tokens"] == 936
    assert requests[1]["max_tokens"] < requests[0]["max_tokens"]


def test_openrouter_reports_second_402_without_another_retry(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)

    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        raise make_402(1000 if len(calls) == 1 else 700)

    monkeypatch.setattr(llm, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="OpenRouter HTTP 402"):
        llm.generate_with_openrouter(
            "test prompt",
            "test system prompt",
        )

    assert len(calls) == 2
