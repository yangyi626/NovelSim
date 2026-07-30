"""命令行运行结构化场景评测并生成 JSON/Markdown 报告。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Optional, Sequence

from .models import PricingConfig
from .report import write_report
from .runner import DEFAULT_CASES, EvaluationRunner, load_benchmark_cases


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--repetitions", type=int)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/evaluation"),
    )
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument(
        "--no-ablations",
        action="store_true",
        help="只运行场景指标，不运行隔离门禁/记忆消融",
    )
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--input-per-million", type=float, default=0.0)
    parser.add_argument(
        "--cached-input-per-million",
        type=float,
        default=0.0,
    )
    parser.add_argument("--output-per-million", type=float, default=0.0)
    args = parser.parse_args(argv)

    pricing = PricingConfig(
        currency=args.currency,
        input_per_million=args.input_per_million,
        cached_input_per_million=args.cached_input_per_million,
        output_per_million=args.output_per_million,
    )
    cases = load_benchmark_cases(args.cases)
    report = asyncio.run(
        EvaluationRunner(pricing=pricing).run(
            cases,
            repetitions=args.repetitions,
            include_ablations=not args.no_ablations,
        )
    )
    json_path = args.json or args.output_dir / f"{report.run_id}.json"
    markdown_path = (
        args.markdown or args.output_dir / f"{report.run_id}.md"
    )
    write_report(
        report,
        json_path=json_path,
        markdown_path=markdown_path,
    )
    print(
        json.dumps(
            {
                "status": "passed" if report.passed else "failed",
                "run_id": report.run_id,
                "case_count": report.case_count,
                "run_count": report.run_count,
                "json": str(json_path.resolve()),
                "markdown": str(markdown_path.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
