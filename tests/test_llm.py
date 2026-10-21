import datetime as dt
import sys
import types

import pytest
from pydantic import BaseModel

from jobpipe.llm import base, router
from jobpipe.llm.base import ProviderTimeout, RateLimited, extract_json


class Item(BaseModel):
    name: str
    n: int


def _fake_module(name: str, behaviour):
    mod = types.ModuleType(f"jobpipe.llm.{name}")

    class Adapter(base.Provider):
        calls = 0

        def run(self, prompt: str) -> str:
            Adapter.calls += 1
            return behaviour(prompt)

    mod.Adapter = Adapter
    sys.modules[mod.__name__] = mod
    return Adapter


@pytest.fixture
def providers(monkeypatch):
    def install(conf):
        monkeypatch.setattr(router, "providers_config", lambda: conf)

    yield install
    for k in [k for k in sys.modules if k.startswith("jobpipe.llm.fake")]:
        del sys.modules[k]


def test_extract_json_strips_fences_and_chatter():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here it is:\n[{"a": 1}]\nHope that helps.') == [{"a": 1}]
    assert extract_json('{"a": [1, 2]}') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_router_validates_and_records_provider(providers):
    _fake_module("fake_ok", lambda p: '```json\n[{"name": "x", "n": 1}]\n```')
    providers({"providers": {"a": {"adapter": "fake_ok"}}, "roles": {"extract": ["a"]}})
    res = router.complete("extract", "prompt", list[Item])
    assert res.provider == "a"
    assert res.value == [Item(name="x", n=1)]


@pytest.mark.parametrize(
    "failure",
    [
        lambda p: "not json at all",
        lambda p: '[{"name": "x"}]',  # schema failure: missing n
        lambda p: (_ for _ in ()).throw(ProviderTimeout("slow")),
        lambda p: (_ for _ in ()).throw(RateLimited("429")),
    ],
)
def test_router_falls_through(providers, failure):
    _fake_module("fake_bad", failure)
    good = _fake_module("fake_good", lambda p: '{"name": "y", "n": 2}')
    providers(
        {
            "providers": {"bad": {"adapter": "fake_bad"}, "good": {"adapter": "fake_good"}},
            "roles": {"tailor": ["bad", "good"]},
        }
    )
    res = router.complete("tailor", "prompt", Item)
    assert res.provider == "good"
    assert res.attempts[0]["provider"] == "bad" and "error" in res.attempts[0]
    assert good.calls == 1


def test_router_skips_expired_provider(providers):
    expired = _fake_module("fake_expired", lambda p: '{"name": "z", "n": 3}')
    _fake_module("fake_next", lambda p: '{"name": "w", "n": 4}')
    providers(
        {
            "providers": {
                "old": {"adapter": "fake_expired", "enabled_until": "2027-02-07"},
                "new": {"adapter": "fake_next"},
            },
            "roles": {"readme": ["old", "new"]},
        }
    )
    res = router.complete("readme", "p", Item, today=dt.date(2027, 2, 8))
    assert res.provider == "new" and expired.calls == 0
    res = router.complete("readme", "p", Item, today=dt.date(2027, 2, 7))
    assert res.provider == "old"


def test_router_check_hook_counts_as_validation_failure(providers):
    _fake_module("fake_first", lambda p: '{"name": "bad", "n": 1}')
    _fake_module("fake_second", lambda p: '{"name": "good", "n": 1}')
    providers(
        {
            "providers": {"a": {"adapter": "fake_first"}, "b": {"adapter": "fake_second"}},
            "roles": {"extract": ["a", "b"]},
        }
    )

    def check(item):
        if item.name == "bad":
            raise ValueError("rejected")

    assert router.complete("extract", "p", Item, check=check).provider == "b"


def test_router_raises_when_all_fail(providers):
    _fake_module("fake_none", lambda p: "nope")
    providers({"providers": {"a": {"adapter": "fake_none"}}, "roles": {"extract": ["a"]}})
    with pytest.raises(router.AllProvidersFailed):
        router.complete("extract", "p", Item)


def test_real_config_roles_resolve():
    for role in ("extract", "tailor", "readme"):
        names = [n for n, _ in router.providers_for(role)]
        assert names == ["antigravity_cli", "codex_cli", "gemini_cli"]


def test_antigravity_parses_stream_result():
    from jobpipe.llm.antigravity_cli import _response_text

    stdout = "\n".join(
        [
            '{"event": "init", "permission_mode": "request-review"}',
            '{"event": "step_update", "text_delta": "{\\"a\\""}',
            '{"event": "result", "conversation_id": "c1", "status": "SUCCESS", "response": "{\\"a\\": 1}\\n"}',
        ]
    )
    assert extract_json(_response_text(stdout)) == {"a": 1}


@pytest.mark.parametrize(
    "line, exc",
    [
        ('{"event": "result", "status": "ERROR", "response": "", "error": "429 quota exceeded"}', RateLimited),
        ('{"event": "result", "status": "ERROR", "response": "", "error": "authentication required"}', base.ProviderError),
        ('{"event": "result", "status": "SUCCESS", "response": "  "}', base.ProviderError),
        ('{"event": "init"}', base.ProviderError),
    ],
)
def test_antigravity_errors(line, exc):
    from jobpipe.llm.antigravity_cli import _response_text

    with pytest.raises(exc):
        _response_text(line)
