"""创作者账户、RBAC、审核权限和审计闭环。"""

import importlib

from fastapi.testclient import TestClient

from engine import WorldPackageStore
from web.auth import AuthStore, AuthenticationError


web_app = importlib.import_module("web.app")


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def _configure(tmp_path, monkeypatch):
    auth = AuthStore(tmp_path / "auth.sqlite3")
    builtin = web_app.PACKAGES.get("huarong_lane").payload()
    packages = WorldPackageStore(
        tmp_path / "worlds",
        builtins={"huarong_lane": builtin},
    )
    monkeypatch.setattr(web_app, "AUTH", auth)
    monkeypatch.setattr(web_app, "PACKAGES", packages)
    return auth, packages


def test_password_tokens_and_disabled_accounts(tmp_path):
    store = AuthStore(tmp_path / "auth.sqlite3")
    user = store.bootstrap_admin("admin", "strong-password")
    assert user.can("users.manage")
    assert "password" not in repr(user.payload()).lower()

    authenticated = store.authenticate("admin", "strong-password")
    token = store.issue_token(authenticated)
    assert store.resolve_token(token).user_id == user.user_id

    store.set_active(user.user_id, False)
    try:
        store.resolve_token(token)
    except AuthenticationError:
        pass
    else:
        raise AssertionError("停用账户的旧令牌必须失效")


def test_creator_reviewer_publisher_permissions_and_audit(
    tmp_path,
    monkeypatch,
):
    auth, _ = _configure(tmp_path, monkeypatch)
    with TestClient(web_app.app) as client:
        bootstrap = client.post(
            "/api/auth/bootstrap",
            json={"username": "admin", "password": "strong-password"},
        )
        assert bootstrap.status_code == 200
        admin_token = bootstrap.json()["token"]
        admin_headers = _auth_header(admin_token)

        tokens = {}
        for username, roles in [
            ("creator", ["creator"]),
            ("reviewer", ["reviewer"]),
            ("publisher", ["publisher"]),
        ]:
            created = client.post(
                "/api/admin/users",
                headers=admin_headers,
                json={
                    "username": username,
                    "password": "strong-password",
                    "roles": roles,
                },
            )
            assert created.status_code == 200
            login = client.post(
                "/api/auth/login",
                json={
                    "username": username,
                    "password": "strong-password",
                },
            )
            tokens[username] = login.json()["token"]

        creator_headers = _auth_header(tokens["creator"])
        cloned = client.post(
            "/api/creator/packages/huarong_lane/clone",
            headers=creator_headers,
        )
        assert cloned.status_code == 200
        package = cloned.json()["package"]
        package_id = package["package_id"]

        submitted = client.post(
            f"/api/creator/packages/{package_id}/review",
            headers=creator_headers,
            json={
                "target_status": "pending_review",
                "expected_revision": package["revision"],
                "note": "创作者提交",
            },
        )
        assert submitted.status_code == 200
        package = submitted.json()["package"]

        forbidden = client.post(
            f"/api/creator/packages/{package_id}/review",
            headers=creator_headers,
            json={
                "target_status": "approved",
                "expected_revision": package["revision"],
            },
        )
        assert forbidden.status_code == 403

        reviewed = client.post(
            f"/api/creator/packages/{package_id}/review",
            headers=_auth_header(tokens["reviewer"]),
            json={
                "target_status": "approved",
                "expected_revision": package["revision"],
                "note": "审核通过",
            },
        )
        assert reviewed.status_code == 200
        package = reviewed.json()["package"]

        reviewer_publish = client.post(
            f"/api/creator/packages/{package_id}/review",
            headers=_auth_header(tokens["reviewer"]),
            json={
                "target_status": "published",
                "expected_revision": package["revision"],
            },
        )
        assert reviewer_publish.status_code == 403

        published = client.post(
            f"/api/creator/packages/{package_id}/review",
            headers=_auth_header(tokens["publisher"]),
            json={
                "target_status": "published",
                "expected_revision": package["revision"],
                "note": "正式发布",
            },
        )
        assert published.status_code == 200
        assert published.json()["package"]["review_status"] == "published"

        audit = client.get(
            "/api/creator/audit?limit=100",
            headers=admin_headers,
        )
        assert audit.status_code == 200
        actions = {event["action"] for event in audit.json()["events"]}
        assert {
            "world_package.clone",
            "world_package.review.pending_review",
            "world_package.review.approved",
            "world_package.review.published",
        }.issubset(actions)
        assert any(event["outcome"] == "denied" for event in audit.json()["events"])
        assert auth.has_users() is True
