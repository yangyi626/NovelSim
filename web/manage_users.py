"""本地账户管理命令。

示例：
    python -m web.manage_users bootstrap admin strong-password
    python -m web.manage_users create editor strong-password --roles creator
    python -m web.manage_users list
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .auth import AuthError, AuthStore


def _database_path(value: str) -> Path:
    configured = Path(value)
    if configured.is_absolute():
        return configured
    return Path(__file__).resolve().parent.parent / configured


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="管理 NovelSim 本地账户")
    parser.add_argument(
        "--db",
        default=os.environ.get("AUTH_DB_PATH", "data/auth.sqlite3"),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    bootstrap = commands.add_parser("bootstrap", help="初始化首个管理员")
    bootstrap.add_argument("username")
    bootstrap.add_argument("password")

    create = commands.add_parser("create", help="创建账户")
    create.add_argument("username")
    create.add_argument("password")
    create.add_argument(
        "--roles",
        nargs="+",
        required=True,
        choices=["creator", "reviewer", "publisher", "admin"],
    )

    commands.add_parser("list", help="列出账户")
    args = parser.parse_args(argv)
    store = AuthStore(_database_path(args.db))
    try:
        if args.command == "bootstrap":
            user = store.bootstrap_admin(args.username, args.password)
            print(f"已初始化管理员: {user.username} ({user.user_id})")
        elif args.command == "create":
            user = store.create_user(
                username=args.username,
                password=args.password,
                roles=args.roles,
            )
            print(
                f"已创建账户: {user.username} "
                f"[{', '.join(sorted(user.roles))}]"
            )
        else:
            for user in store.list_users():
                status = "active" if user.active else "disabled"
                print(
                    f"{user.username}\t{','.join(sorted(user.roles))}\t"
                    f"{status}\t{user.user_id}"
                )
    except AuthError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
