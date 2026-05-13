from types import SimpleNamespace

from app.config import Settings
from app.llm import OpenAILLMClient, SiliconFlowLLMClient, build_llm_client


class Recorder:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}", tool_calls=None))])


def fake_openai_client(recorder):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=recorder.create)))


def test_siliconflow_adapter_uses_siliconflow_config_and_omits_tool_choice():
    settings = Settings(
        llm_provider="siliconflow",
        openai_api_key=None,
        siliconflow_api_key="sf-test-key",
        siliconflow_base_url="https://api.siliconflow.cn/v1",
        siliconflow_model="deepseek-ai/DeepSeek-V4-Flash",
    )
    llm = build_llm_client(settings)
    recorder = Recorder()
    llm._client = fake_openai_client(recorder)

    response = llm.create_chat_completion(
        messages=[{"role": "user", "content": "Return JSON."}],
        tools=[{"type": "function", "function": {"name": "noop", "parameters": {"type": "object", "properties": {}}}}],
        response_format={"type": "json_object"},
    )

    assert isinstance(llm, SiliconFlowLLMClient)
    assert response.choices[0].message.content == "{}"
    assert recorder.kwargs["model"] == "deepseek-ai/DeepSeek-V4-Flash"
    assert recorder.kwargs["response_format"] == {"type": "json_object"}
    assert "tools" in recorder.kwargs
    assert "tool_choice" not in recorder.kwargs


def test_openai_adapter_keeps_tool_choice_auto():
    settings = Settings(
        llm_provider="openai",
        openai_api_key="openai-test-key",
        openai_model="gpt-4o-mini",
    )
    llm = build_llm_client(settings)
    recorder = Recorder()
    llm._client = fake_openai_client(recorder)

    llm.create_chat_completion(
        messages=[{"role": "user", "content": "Return JSON."}],
        tools=[{"type": "function", "function": {"name": "noop", "parameters": {"type": "object", "properties": {}}}}],
        response_format={"type": "json_object"},
    )

    assert isinstance(llm, OpenAILLMClient)
    assert recorder.kwargs["tool_choice"] == "auto"
