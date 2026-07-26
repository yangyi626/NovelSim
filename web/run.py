"""启动 AI 快穿 Web 服务。

用法：
    python web/run.py                 # 默认 0.0.0.0:8000
    python web/run.py --port 9000     # 自定义端口

前端需先构建 (生产) 或另起 npm run dev (开发)。
详见 web/README.md。
"""

from __future__ import annotations

import argparse
import sys

import uvicorn


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="启动 AI 快穿 Web 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="监听端口 (默认 8000)")
    parser.add_argument("--reload", action="store_true", help="热重载 (开发用)")
    args = parser.parse_args(argv)

    print(f"[web] 启动 FastAPI @ http://{args.host}:{args.port}")
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
