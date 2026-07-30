"""原创密信场景的可发布 WorldPackage 与完整性校验。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from engine.world_packages import (
    PACKAGE_FORMAT,
    PACKAGE_FORMAT_VERSION,
    validate_world_package_payload,
)

from .scenario import PLAYER, build_snapshot


PACKAGE_ID = "secret_letter_v1"
DEFAULT_EXPORT_PATH = Path("portfolio/worlds/secret-letter-v1.json")
FIXED_TIMESTAMP = "2026-07-30T00:00:00+00:00"


def build_world_package_payload() -> Dict[str, Any]:
    """返回无运行时数据、可稳定复现的原创演示世界包。"""

    return {
        "format": PACKAGE_FORMAT,
        "format_version": PACKAGE_FORMAT_VERSION,
        "package_id": PACKAGE_ID,
        "novel": "密信疑云（原创演示）",
        "scenario": "午夜前的密信",
        "anchor": "守卫在门房发现一封指向摄政王阴谋的密信",
        "default_actor_id": PLAYER,
        "source_chapters": ["原创设定：序幕"],
        "snapshot": build_snapshot().dict(),
        "manifest": {
            "content_origin": "original_for_novelsim_portfolio",
            "license_spdx": "CC-BY-4.0",
            "language": "zh-CN",
            "scene_modes": ["free", "script"],
            "player_routes": [
                "destroy_letter",
                "intercept_letter",
                "expose_truth",
            ],
            "canonical_ending": "defenders_allied",
            "estimated_showcase_minutes": "10-15",
        },
        "revision": 1,
        "source": "custom",
        "created_at": FIXED_TIMESTAMP,
        "updated_at": FIXED_TIMESTAMP,
        "review_status": "published",
        "review_note": "原创求职版公开演示世界。",
        "reviewed_at": FIXED_TIMESTAMP,
        "published_at": FIXED_TIMESTAMP,
    }


def canonical_package_bytes() -> bytes:
    """经正式 WorldPackage 校验后生成稳定 JSON 字节。"""

    record = validate_world_package_payload(build_world_package_payload())
    text = json.dumps(
        record.payload(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return f"{text}\n".encode("utf-8")


def export_world_package(path: Path = DEFAULT_EXPORT_PATH) -> Tuple[Path, str]:
    """导出世界包和同名 ``.sha256`` 文件。"""

    output = Path(path)
    payload = canonical_package_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    output.with_suffix(f"{output.suffix}.sha256").write_text(
        f"{digest}  {output.name}\n",
        encoding="ascii",
    )
    return output, digest


def verify_world_package(path: Path = DEFAULT_EXPORT_PATH) -> str:
    """校验 Schema、规范化字节和 SHA-256 sidecar。"""

    output = Path(path)
    actual = output.read_bytes()
    expected = canonical_package_bytes()
    if actual != expected:
        raise ValueError("世界包与当前原创场景源码不一致，请重新导出")

    parsed = json.loads(actual.decode("utf-8"))
    validate_world_package_payload(parsed, expected_package_id=PACKAGE_ID)
    digest = hashlib.sha256(actual).hexdigest()
    checksum_path = output.with_suffix(f"{output.suffix}.sha256")
    expected_checksum = f"{digest}  {output.name}\n"
    if checksum_path.read_text(encoding="ascii") != expected_checksum:
        raise ValueError("SHA-256 sidecar 与世界包不一致")
    return digest


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_EXPORT_PATH)
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验已导出的包，不写文件",
    )
    args = parser.parse_args(argv)

    if args.check:
        digest = verify_world_package(args.output)
        status = "verified"
    else:
        _, digest = export_world_package(args.output)
        status = "exported"
    print(
        json.dumps(
            {
                "status": status,
                "package_id": PACKAGE_ID,
                "path": str(args.output.resolve()),
                "sha256": digest,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
