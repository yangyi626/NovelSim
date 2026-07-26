"""编译器顶层入口：TXT -> WorldPackage 的端到端编排。

对应 plan 第十二步。提供：
    - compile_novel(path, chapters) : 编译指定章节区间，返回 WorldPackage
    - CLI 入口 (python -m compiler.compile)

用法：
    # 编译前 2 章
    pkg = compile_novel("novels/第一狂妃：废柴三小姐.txt", chapters=[1, 2])
    pkg.save("worlds/huarong_lane_compiled.json")

    # 命令行
    python -m compiler.compile novels/第一狂妃：废柴三小姐.txt --chapters 1 2 \
        --out worlds/huarong_lane_compiled.json
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from world_schema import WorldState, PlotArc

from .text_loader import load_novel, split_chapters
from .extractors import EntityExtractor
from .scene_compiler import (
    ChapterCompiler,
    EntityRegistry,
    PackageBuilder,
    VolumeCompiler,
    WorldPackage,
)
from .book_compiler import BookCompiler


def compile_novel(
    path: str,
    *,
    chapters: Optional[List[int]] = None,
    package_id: str = "compiled_world",
    novel_name: str = "",
    extractor: Optional[EntityExtractor] = None,
) -> WorldPackage:
    """编译一本小说的指定章节，返回 WorldPackage。

    chapters: 要编译的章节序号列表 (从 1 开始)。None = 前 1 章 (默认最小验证)。
    extractor: 可注入 (便于 mock 测试)；默认真实 LLM EntityExtractor。
    """
    text = load_novel(path)
    all_chapters = split_chapters(text)

    targets = chapters or [1]
    selected = [c for c in all_chapters if c.index in targets]
    if not selected:
        raise ValueError(f"未找到章节 {targets} (全书共 {len(all_chapters)} 章)")

    # 初始化空世界 + 注册表
    state = _fresh_state(package_id)
    registry = EntityRegistry()
    ext = extractor or EntityExtractor()
    volume_compiler = VolumeCompiler(extractor=ext)
    builder = PackageBuilder()

    volume_result = volume_compiler.compile(
        selected,
        registry,
        state,
    )

    return builder.build(
        package_id=package_id,
        novel=novel_name or _guess_novel_name(path),
        source_chapters=[c.index for c in selected],
        state=state,
        registry=registry,
        compiler_metadata=volume_result.manifest(),
    )


def compile_book(
    path: str,
    *,
    chapters: Optional[List[int]] = None,
    package_id: str = "compiled_book",
    novel_name: str = "",
    extractor: Optional[EntityExtractor] = None,
    timeline_plan: Optional[dict] = None,
    volume_plan: Optional[dict] = None,
    volume_size: int = 20,
) -> WorldPackage:
    """按 D 阶段编译整本书或指定章节，产出分层快照元数据。"""

    text = load_novel(path)
    all_chapters = split_chapters(text)
    targets = set(chapters or [])
    selected = [
        chapter
        for chapter in all_chapters
        if not targets or chapter.index in targets
    ]
    if not selected:
        raise ValueError(
            f"未找到章节 {chapters or '全部'} "
            f"(全书共 {len(all_chapters)} 章)"
        )
    state = _fresh_state(package_id)
    registry = EntityRegistry()
    result = BookCompiler(
        extractor=extractor or EntityExtractor(),
        volume_size=volume_size,
    ).compile(
        selected,
        registry,
        state,
        timeline_plan=timeline_plan,
        volume_plan=volume_plan,
    )
    return PackageBuilder().build(
        package_id=package_id,
        novel=novel_name or _guess_novel_name(path),
        source_chapters=result.source_chapters,
        state=state,
        registry=registry,
        compiler_metadata=result.manifest(),
    )


def _fresh_state(package_id: str) -> WorldState:
    return WorldState(
        timeline_id=f"runtime_{package_id}_root",
        version=0,
        world_time="编译生成 (待标注)",
        current_scene_id=None,
        plot={
            "arc_compiled_main": PlotArc(
                arc_id="arc_compiled_main",
                title="编译生成的主线",
                kind="main",
                stage="active",
            ),
        },
    )


def _guess_novel_name(path: str) -> str:
    import os
    base = os.path.basename(path)
    name, _ = os.path.splitext(base)
    return name


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m compiler.cli",
        description="把小说 TXT 编译成 WorldPackage (JSON)",
    )
    parser.add_argument("novel_path", help="小说 TXT 路径")
    parser.add_argument(
        "--chapters", "-c", type=int, nargs="+", default=None,
        help="要编译的章节序号；D 阶段不指定表示全书",
    )
    parser.add_argument(
        "--stage", choices=["C", "D"], default="C",
        help="C=跨章节单卷，D=全书/多时间线/分层快照",
    )
    parser.add_argument(
        "--volume-size", type=int, default=20,
        help="D 阶段每卷章节数，默认 20",
    )
    parser.add_argument(
        "--out", "-o", default=None,
        help="输出 JSON 路径 (不指定则只打印摘要)",
    )
    parser.add_argument(
        "--package-id", default="compiled_world",
        help="世界包 id",
    )
    args = parser.parse_args(argv)

    target_chapters = args.chapters or (
        [1] if args.stage == "C" else None
    )
    print(
        f"[编译] 加载 {args.novel_path}，阶段 {args.stage}，"
        f"目标章节 {target_chapters or '全书'}"
    )
    if args.stage == "D":
        pkg = compile_book(
            args.novel_path,
            chapters=target_chapters,
            package_id=args.package_id,
            volume_size=args.volume_size,
        )
    else:
        pkg = compile_novel(
            args.novel_path,
            chapters=target_chapters,
            package_id=args.package_id,
        )

    snap = pkg.snapshot
    print(f"\n{'='*60}")
    print(f"[完成] {pkg.package_id} | 小说: {pkg.novel}")
    print(f"  章节: {pkg.source_chapters}")
    print(f"  角色 ({len(snap.characters)}):")
    for c in snap.characters.values():
        aliases = f" (别名: {', '.join(c.aliases)})" if c.aliases else ""
        print(f"    - {c.character_id} | {c.display_name}{aliases}"
              f" | 标签: {c.identity_tags}")
    print(f"  物品 ({len(snap.items)}):")
    for it in snap.items.values():
        print(f"    - {it.item_id} | {it.display_name}")
    print(f"  地点 ({len(snap.locations)}):")
    for loc in snap.locations.values():
        print(f"    - {loc.location_id} | {loc.display_name}")
    print(f"  关系: {len(snap.relations)} 条")
    for r in snap.relations[:8]:
        dims = r.dimensions.dict() if hasattr(r.dimensions, "dict") else dict(r.dimensions)
        active = {k: round(v, 2) for k, v in dims.items() if v}
        print(f"    - {r.source_id} -> {r.target_id} | {r.public_relation} | {active}")
    print(f"  世界规则 ({len(snap.world_rules)}):")
    for wr in snap.world_rules:
        print(f"    - [{wr.category}] {wr.statement}")
    print(f"{'='*60}")

    if args.out:
        import os
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        pkg.save(args.out)
        print(f"\n[保存] {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
