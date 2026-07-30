"""用真人盲标校准已有 Pairwise 报告，全程不调用 LLM。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from .pairwise import (
    PairwiseReport,
    calibrate_pairwise_report,
    load_human_labels,
    render_pairwise_markdown,
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)

    report = PairwiseReport.parse_obj(
        json.loads(args.report.read_text(encoding="utf-8"))
    )
    calibrated = calibrate_pairwise_report(
        report,
        load_human_labels(args.labels),
    )
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(calibrated.dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(
            render_pairwise_markdown(calibrated),
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "status": "calibrated",
                "human_labels": calibrated.human_label_count,
                "agreement": calibrated.human_agreement_rate,
                "cohen_kappa": calibrated.cohen_kappa,
                "llm_calls_added": 0,
                "json": str(args.json.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
