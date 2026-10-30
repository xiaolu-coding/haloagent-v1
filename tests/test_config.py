from __future__ import annotations

from haloagent.core.config import AppConfig, expand_env, load_config


def test_expand_env(monkeypatch):
    monkeypatch.setenv("HALO_TEST_VALUE", "deepseek-chat")
    assert expand_env("${HALO_TEST_VALUE}") == "deepseek-chat"
    assert expand_env("${HALO_MISSING:-fallback}") == "fallback"
    assert expand_env({"a": ["${HALO_TEST_VALUE}"]}) == {"a": ["deepseek-chat"]}


def test_load_yaml_and_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HALO_TEST_KEY", "sk-test")
    path = tmp_path / "config.yaml"
    path.write_text(
        """
llm:
  provider: deepseek
  api_key: ${HALO_TEST_KEY}
agent:
  name: Tester
  paradigm: plan_solve
tools: [calculator]
""",
        encoding="utf-8",
    )
    config = load_config(path, dotenv=False)
    assert isinstance(config, AppConfig)
    assert config.llm.api_key == "sk-test"
    assert config.agent.name == "Tester"
    assert config.agent.max_steps == 8
    assert config.tools == ["calculator"]
