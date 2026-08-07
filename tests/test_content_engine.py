from unittest.mock import MagicMock

from marketing import content_engine


def _fake_client(reply_text):
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=reply_text)]
    client.messages.create.return_value = response
    return client


def test_generate_returns_model_text(monkeypatch):
    fake_client = _fake_client("Hey James, just checking in on your kitchen quote!")
    monkeypatch.setattr(content_engine, "_get_client", lambda: fake_client)

    result = content_engine.generate(
        "follow_up", {"name": "James", "job": "kitchen painting", "step": 1}
    )

    assert result == "Hey James, just checking in on your kitchen quote!"


def test_generate_includes_knowledge_and_context_in_prompt(monkeypatch):
    fake_client = _fake_client("ok")
    monkeypatch.setattr(content_engine, "_get_client", lambda: fake_client)

    content_engine.generate(
        "follow_up", {"name": "James", "job": "kitchen painting", "step": 2}
    )

    call_kwargs = fake_client.messages.create.call_args.kwargs
    assert "AU Decorating" in call_kwargs["system"]
    assert "Southsea" in call_kwargs["system"]
    user_message = call_kwargs["messages"][0]["content"]
    assert "James" in user_message
    assert "kitchen painting" in user_message
    assert "follow-up number 2" in user_message


def test_generate_unknown_content_type_raises(monkeypatch):
    monkeypatch.setattr(content_engine, "_get_client", lambda: _fake_client("ok"))

    try:
        content_engine.generate("not_a_real_type", {})
        assert False, "expected ValueError"
    except ValueError:
        pass
