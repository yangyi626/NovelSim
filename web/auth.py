"""SQLite 账户、令牌、RBAC 与审核审计。

账户数据独立于世界状态和编译任务。密码使用 PBKDF2-SHA256 保存，访问令牌只
保存 SHA256 摘要，数据库泄露时不会直接暴露明文密码或令牌。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


ROLES = {"creator", "reviewer", "publisher", "admin"}
ROLE_PERMISSIONS = {
    "creator": {
        "creator.read",
        "creator.write",
        "compiler.manage",
        "review.submit",
    },
    "reviewer": {
        "creator.read",
        "review.decide",
        "audit.read",
    },
    "publisher": {
        "creator.read",
        "review.publish",
        "audit.read",
    },
    "admin": {"*"},
}
USERNAME_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{2,31}")
PBKDF2_ITERATIONS = 260_000


class AuthError(RuntimeError):
    """账户或权限操作失败。"""


class AuthenticationError(AuthError):
    """凭据无效。"""


class PermissionDenied(AuthError):
    """当前账户没有操作权限。"""


class AuthConflict(AuthError):
    """账户状态冲突。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: Optional[datetime] = None) -> str:
    return (value or _now()).isoformat()


def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        salt.hex(),
        digest.hex(),
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, expected_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(digest.hex(), expected_hex)
    except (TypeError, ValueError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    username: str
    roles: Set[str]
    active: bool
    created_at: str
    updated_at: str

    @property
    def permissions(self) -> Set[str]:
        result: Set[str] = set()
        for role in self.roles:
            result.update(ROLE_PERMISSIONS.get(role, set()))
        return result

    def can(self, permission: str) -> bool:
        permissions = self.permissions
        return "*" in permissions or permission in permissions

    def payload(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "roles": sorted(self.roles),
            "permissions": sorted(self.permissions),
            "active": self.active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


SYSTEM_ACTOR = AuthUser(
    user_id="system",
    username="system",
    roles={"admin"},
    active=True,
    created_at="",
    updated_at="",
)


class AuthStore:
    """SQLite 本地账户与审计仓库。"""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _initialise(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS auth_users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    roles_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES auth_users(user_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_auth_tokens_user
                ON auth_tokens(user_id, expires_at);

                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    actor_user_id TEXT NOT NULL,
                    actor_username TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_events_created
                ON audit_events(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_events_resource
                ON audit_events(resource_type, resource_id, created_at DESC);
                """
            )

    def has_users(self) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM auth_users LIMIT 1"
            ).fetchone()
        return row is not None

    def bootstrap_admin(self, username: str, password: str) -> AuthUser:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM auth_users LIMIT 1"
            ).fetchone()
            if row is not None:
                raise AuthConflict("系统已经完成管理员初始化")
        return self.create_user(
            username=username,
            password=password,
            roles={"admin"},
        )

    def create_user(
        self,
        *,
        username: str,
        password: str,
        roles: Iterable[str],
    ) -> AuthUser:
        username = username.strip().lower()
        role_set = {str(role).strip().lower() for role in roles}
        if not USERNAME_PATTERN.fullmatch(username):
            raise AuthError(
                "用户名需以小写字母开头，长度 3-32，"
                "仅支持字母、数字、点、横线和下划线"
            )
        if len(password) < 8:
            raise AuthError("密码至少需要 8 个字符")
        invalid_roles = role_set - ROLES
        if not role_set or invalid_roles:
            raise AuthError(
                "角色无效: {}".format(
                    ", ".join(sorted(invalid_roles or role_set))
                )
            )
        now = _timestamp()
        user_id = uuid.uuid4().hex
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO auth_users (
                        user_id, username, password_hash, roles_json,
                        active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        user_id,
                        username,
                        _hash_password(password),
                        json.dumps(sorted(role_set)),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise AuthConflict(f"用户名已经存在: {username}") from exc
        return self.get_user(user_id)

    def list_users(self) -> List[AuthUser]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM auth_users ORDER BY created_at"
            ).fetchall()
        return [self._row_to_user(row) for row in rows]

    def get_user(self, user_id: str) -> AuthUser:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM auth_users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            raise AuthenticationError("账户不存在")
        return self._row_to_user(row)

    def authenticate(self, username: str, password: str) -> AuthUser:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM auth_users WHERE username = ?",
                (username.strip().lower(),),
            ).fetchone()
        if row is None or not _verify_password(password, row["password_hash"]):
            raise AuthenticationError("用户名或密码错误")
        user = self._row_to_user(row)
        if not user.active:
            raise AuthenticationError("账户已停用")
        return user

    def issue_token(
        self,
        user: AuthUser,
        *,
        ttl_hours: int = 12,
    ) -> str:
        if not user.active:
            raise AuthenticationError("账户已停用")
        token = secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(hours=max(1, min(ttl_hours, 168)))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_tokens (
                    token_hash, user_id, expires_at, created_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _token_hash(token),
                    user.user_id,
                    _timestamp(expires),
                    _timestamp(now),
                    _timestamp(now),
                ),
            )
        return token

    def resolve_token(self, token: str) -> AuthUser:
        if not token:
            raise AuthenticationError("缺少访问令牌")
        now = _timestamp()
        token_digest = _token_hash(token)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT u.*
                FROM auth_tokens t
                JOIN auth_users u ON u.user_id = t.user_id
                WHERE t.token_hash = ? AND t.expires_at > ?
                """,
                (token_digest, now),
            ).fetchone()
            if row is not None:
                conn.execute(
                    """
                    UPDATE auth_tokens SET last_used_at = ?
                    WHERE token_hash = ?
                    """,
                    (now, token_digest),
                )
        if row is None:
            raise AuthenticationError("访问令牌无效或已经过期")
        user = self._row_to_user(row)
        if not user.active:
            raise AuthenticationError("账户已停用")
        return user

    def set_active(self, user_id: str, active: bool) -> AuthUser:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE auth_users SET active = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (1 if active else 0, _timestamp(), user_id),
            )
            if cursor.rowcount != 1:
                raise AuthenticationError("账户不存在")
            if not active:
                conn.execute(
                    "DELETE FROM auth_tokens WHERE user_id = ?",
                    (user_id,),
                )
        return self.get_user(user_id)

    def audit(
        self,
        actor: AuthUser,
        *,
        action: str,
        resource_type: str,
        resource_id: str = "",
        outcome: str = "success",
        detail: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event = {
            "event_id": uuid.uuid4().hex,
            "actor_user_id": actor.user_id,
            "actor_username": actor.username,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "outcome": outcome,
            "detail": detail or {},
            "created_at": _timestamp(),
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events (
                    event_id, actor_user_id, actor_username, action,
                    resource_type, resource_id, outcome, detail_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["actor_user_id"],
                    event["actor_username"],
                    event["action"],
                    event["resource_type"],
                    event["resource_id"],
                    event["outcome"],
                    json.dumps(event["detail"], ensure_ascii=False),
                    event["created_at"],
                ),
            )
        return event

    def list_audit(
        self,
        *,
        limit: int = 100,
        resource_type: str = "",
        resource_id: str = "",
    ) -> List[Dict[str, Any]]:
        clauses = []
        values: List[Any] = []
        if resource_type:
            clauses.append("resource_type = ?")
            values.append(resource_type)
        if resource_id:
            clauses.append("resource_id = ?")
            values.append(resource_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        values.append(max(1, min(int(limit), 500)))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM audit_events
                {}
                ORDER BY created_at DESC, rowid DESC LIMIT ?
                """.format(where),
                values,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["detail"] = json.loads(item.pop("detail_json"))
            result.append(item)
        return result

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> AuthUser:
        return AuthUser(
            user_id=row["user_id"],
            username=row["username"],
            roles=set(json.loads(row["roles_json"])),
            active=bool(row["active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def require_permission(user: AuthUser, permission: str) -> None:
    if not user.can(permission):
        raise PermissionDenied(
            f"账户 {user.username} 缺少权限: {permission}"
        )
