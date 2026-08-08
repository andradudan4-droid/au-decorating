from unittest.mock import MagicMock

import app as app_module


def test_admin_content_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "correct-secret")
    client = app_module.app.test_client()

    response = client.get("/admin/content")

    assert response.status_code == 401


def test_admin_content_lists_recent_items_with_correct_key(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "correct-secret")
    monkeypatch.setattr(
        app_module.marketing_db, "list_content_items",
        lambda: [{"id": 1, "content_type": "gbp_post",
                   "input_context": "repainted a terrace",
                   "generated_text": "Just finished a repaint!"}],
    )
    client = app_module.app.test_client()

    response = client.get("/admin/content?key=correct-secret")

    assert response.status_code == 200
    assert b"Just finished a repaint!" in response.data


def test_generate_gbp_post_saves_and_redirects(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "correct-secret")
    fake_generate = MagicMock(return_value="Just finished a repaint!")
    fake_insert = MagicMock(return_value=1)
    monkeypatch.setattr(app_module.content_engine, "generate", fake_generate)
    monkeypatch.setattr(app_module.marketing_db, "insert_content_item", fake_insert)
    client = app_module.app.test_client()

    response = client.post(
        "/admin/content/generate?key=correct-secret",
        data={"content_type": "gbp_post", "description": "repainted a terrace in Southsea"},
    )

    fake_generate.assert_called_once_with(
        "gbp_post", {"description": "repainted a terrace in Southsea"}
    )
    fake_insert.assert_called_once_with(
        "gbp_post", "repainted a terrace in Southsea", "Just finished a repaint!"
    )
    assert response.status_code == 302


def test_generate_review_reply_saves_and_redirects(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "correct-secret")
    fake_generate = MagicMock(return_value="Thanks so much, James!")
    fake_insert = MagicMock(return_value=2)
    monkeypatch.setattr(app_module.content_engine, "generate", fake_generate)
    monkeypatch.setattr(app_module.marketing_db, "insert_content_item", fake_insert)
    client = app_module.app.test_client()

    response = client.post(
        "/admin/content/generate?key=correct-secret",
        data={
            "content_type": "review_reply",
            "reviewer_name": "James",
            "rating": "5",
            "review_text": "Great tidy job.",
        },
    )

    fake_generate.assert_called_once_with(
        "review_reply",
        {"reviewer_name": "James", "rating": "5", "review_text": "Great tidy job."},
    )
    assert fake_insert.call_args.args[0] == "review_reply"
    assert "James" in fake_insert.call_args.args[1]
    assert response.status_code == 302


def test_admin_content_escapes_html_in_listed_items(monkeypatch):
    monkeypatch.setattr(app_module, "ADMIN_SECRET", "correct-secret")
    monkeypatch.setattr(
        app_module.marketing_db, "list_content_items",
        lambda: [{"id": 1, "content_type": "gbp_post",
                   "input_context": "<script>alert(1)</script>",
                   "generated_text": "safe text"}],
    )
    client = app_module.app.test_client()

    response = client.get("/admin/content?key=correct-secret")

    assert b"<script>alert(1)</script>" not in response.data
    assert b"&lt;script&gt;" in response.data
