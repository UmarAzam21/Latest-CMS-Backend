from app.router import resolve_notification_user_id, upload_profile_image_to_cloudinary, build_admin_dashboard_summary, _find_env_file
from app.schema import AdminProfileUpdate
from app.auth import _normalize_role_name


def test_find_env_file_uses_project_root_dotenv():
    env_file = _find_env_file()
    assert env_file is not None
    assert env_file.name == ".env"
    assert env_file.parent.name in {"Custom-CMS-Backend", "backend"}


def test_normalize_role_name_accepts_legacy_super_admin_alias():
    assert _normalize_role_name("super_admin") == "superadmin"
    assert _normalize_role_name("superadmin") == "superadmin"
    assert _normalize_role_name("admin") == "admin"


def test_resolve_notification_user_id_prefers_admin_email():
    current_admin = {
        "id": 7,
        "email": "admin@example.com",
        "role": "superadmin",
    }

    assert resolve_notification_user_id(current_admin) == "admin@example.com"


def test_resolve_notification_user_id_falls_back_to_admin_label():
    assert resolve_notification_user_id(None) == "admin"


def test_admin_profile_update_excludes_email_and_role_fields():
    fields = AdminProfileUpdate.model_fields.keys()
    assert "name" in fields
    assert "profile_image" in fields
    assert "phone_number" in fields
    assert "bio" in fields
    assert "current_password" in fields
    assert "new_password" in fields
    assert "email" not in fields
    assert "role" not in fields


def test_upload_profile_image_to_cloudinary_uses_secure_url(monkeypatch):
    calls = {}

    def fake_upload(file_obj, **kwargs):
        calls["file_obj"] = file_obj
        calls["kwargs"] = kwargs
        return {"secure_url": "https://cloudinary.example.com/avatar.jpg", "public_id": "avatar_123"}

    monkeypatch.setattr("app.router.cloudinary.uploader.upload", fake_upload)

    result = upload_profile_image_to_cloudinary(b"fake-image-bytes")

    assert result == "https://cloudinary.example.com/avatar.jpg"
    assert calls["kwargs"]["folder"] == "admin/profile-images"


def test_build_admin_dashboard_summary_has_expected_sections():
    summary = build_admin_dashboard_summary(
        page_count=6,
        new_message_count=3,
        last_content_update={"label": "Hero headline — Home", "time": "2h ago"},
        seo_health_score=82,
        admin_leads=800,
        recent_activity=[
            {"title": "Updated Hero headline", "detail": "Home • 2 hours ago"},
            {"title": "Added new section: Testimonials", "detail": "About • 5 hours ago"},
        ],
    )

    assert summary["total_pages"] == 6
    assert summary["new_messages"] == 3
    assert summary["last_content_update"]["label"] == "Hero headline — Home"
    assert summary["seo_health_score"] == 82
    assert summary["admin_leads"] == 800
    assert isinstance(summary["recent_activity"], list)
