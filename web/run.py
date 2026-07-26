"""启动 AI 快穿 Web 服务。

用法（在任意目录均可，推荐项目根目录）：
    python web/run.py                 # 默认 127.0.0.1:8000
    python web/run.py --port 9000     # 自定义端口
    python web/run.py --reload        # 热重载 (开发用)

前端需先构建 (生产) 或另起 npm run dev (开发)。
详见 web/README.md。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 关键：把项目根目录 (本文件的上一级) 加入 sys.path，
# 这样无论从哪个工作目录运行，uvicorn 都能 import web.app。
# 否则从 web/ 子目录运行会报 "No module named 'web'"。
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
# 同时切到项目根目录，保证 .env / novels / examples 等相对路径能找到
os.chdir(_PROJECT_ROOT)

import uvicorn  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="启动 AI 快穿 Web 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="监听端口 (默认 8000)")
    parser.add_argument("--reload", action="store_true", help="热重载 (开发用)")
    args = parser.parse_args(argv)

    print(f"[web] 启动 FastAPI @ http://{args.host}:{args.port}")
    print(f"[web] 工作目录: {_PROJECT_ROOT}")
    print(f"[web] 前端: 若已构建访问根路径；开发模式请另起 npm run dev (5173)")
    uvicorn.run(
        "web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
