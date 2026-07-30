"""把真实 grounded 叙事与确定性事件模板组成 BOOKWORLD 盲测数据集。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List, Optional, Sequence

from engine.scene_controller import SceneMode

from .pairwise import (
    PairwiseCandidate,
    PairwiseSample,
    render_human_packet,
)
from .real_runner import RealLLMReport, RealLLMRunRecord


def build_pairwise_samples(
    records: Sequence[RealLLMRunRecord],
) -> List[PairwiseSample]:
    samples = []
    seen = set()
    for index, record in enumerate(records):
        if record.case_id in seen:
            raise ValueError(f"duplicate source case: {record.case_id}")
        seen.add(record.case_id)
        if not record.narrative_text.strip():
            raise ValueError(
                f"source case has no narrative text: {record.case_id}"
            )
        actual_text = "\n".join(
            [
                record.narrative_text.strip(),
                *[
                    _display_dialogue(item.strip())
                    for item in record.dialogue_texts
                    if item.strip()
                ],
            ]
        )
        template_text = _template_text(record)
        objectively_valid = bool(
            record.objective_passed
            and record.replay_consistent
            and record.causal_valid
            and record.narrative_grounded is True
        )
        mode = SceneMode.free if index % 2 == 0 else SceneMode.script
        samples.append(
            PairwiseSample(
                sample_id=f"pair_{record.case_id}",
                scenario_id="huarong_lane",
                mode=mode,
                context=_context(record),
                candidate_a=PairwiseCandidate(
                    system_id="novelsim_grounded_llm",
                    text=actual_text,
                    objective_passed=objectively_valid,
                ),
                candidate_b=PairwiseCandidate(
                    system_id="deterministic_event_template",
                    text=template_text,
                    objective_passed=True,
                ),
            )
        )
    if not samples:
        raise ValueError("pairwise source report has no records")
    return samples


def _template_text(record: RealLLMRunRecord) -> str:
    if record.objective == "obtain_outer_robe":
        return (
            "夜轻歌取走了夜清清的外衫。外衫的持有者已经变更为夜轻歌。"
        )
    if record.objective == "reach_ye_residence":
        return "夜轻歌从华容巷步行抵达夜府。"
    raise ValueError(
        f"no deterministic template for objective: {record.objective}"
    )


def _context(record: RealLLMRunRecord) -> str:
    return "\n".join(
        [
            "世界：古代玄幻北月国；现代交通工具不可用。",
            "世界时间：北月国·某日午前。",
            "角色：夜轻歌处于华容巷；夜清清与她同场。",
            "关系：夜清清嫉妒并曾陷害夜轻歌；夺衣不会自动变成亲近或默契。",
            "因果边界：同场角色不会自动跟随；只有权威状态变化声明的角色会移动。",
            f"玩家输入：{record.user_text}",
            f"权威动作：{record.action_type or '无'}",
            "权威状态变化：" + (
                "、".join(record.operation_types)
                if record.operation_types
                else "无"
            ),
            "两段候选必须忠于同一已提交事件；只比较体验质量。",
        ]
    )


def _display_dialogue(text: str) -> str:
    return (
        text.replace("char_yeqingqing: ", "夜清清：")
        .replace("char_yeqingge: ", "夜轻歌：")
        .replace("char_lin_guanjia: ", "林管家：")
        .replace("char_yeqingtian: ", "夜青天：")
        .replace("char_yeqingqing:", "夜清清：")
        .replace("char_yeqingge:", "夜轻歌：")
        .replace("char_lin_guanjia:", "林管家：")
        .replace("char_yeqingtian:", "夜青天：")
    )


def write_pairwise_jsonl(
    path: Path,
    samples: Sequence[PairwiseSample],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            json.dumps(item.dict(), ensure_ascii=False)
            for item in samples
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--human-packet", type=Path)
    parser.add_argument("--seed", type=int, default=20260730)
    args = parser.parse_args(argv)

    report = RealLLMReport.parse_obj(
        json.loads(args.source.read_text(encoding="utf-8"))
    )
    samples = build_pairwise_samples(report.records)
    write_pairwise_jsonl(args.output, samples)
    if args.human_packet is not None:
        args.human_packet.parent.mkdir(parents=True, exist_ok=True)
        args.human_packet.write_text(
            render_human_packet(samples, random_seed=args.seed),
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "samples": len(samples),
                "output": str(args.output.resolve()),
                "human_packet": (
                    str(args.human_packet.resolve())
                    if args.human_packet is not None
                    else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
