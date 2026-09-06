#!/usr/bin/env python3
"""Execute one disclosure question through the unified answer service."""

from __future__ import annotations

import argparse
import re

from disclosure_agent.config import get_settings
from disclosure_agent.retrieval.evidence_pack import render_evidence_pack
from disclosure_agent.retrieval.source_references import render_source_references
from disclosure_agent.services.answer_service import AnswerService
from disclosure_agent.storage.database import get_engine, session_scope


_EVIDENCE_LABEL = re.compile(r"\s*\[E\d+\]")
_NUMBERED_HEADING = re.compile(r"^(?P<indent>\s*)(?P<number>[1-9]\d{0,2})\.\s+(?P<body>.+)$")


def _renumber_top_level_items(answer: str) -> str:
    lines = answer.splitlines()
    numbered = [
        _NUMBERED_HEADING.match(line)
        for line in lines
        if line and not line[0].isspace() and _NUMBERED_HEADING.match(line)
    ]
    if len(numbered) == 1:
        only = numbered[0]
        lines = [
            only.group("body") if line == only.group(0) else line
            for line in lines
        ]
        return "\n".join(lines)
    if not numbered:
        return answer

    counter = 0
    rendered: list[str] = []
    for line in lines:
        match = _NUMBERED_HEADING.match(line)
        if match is None or match.group("indent"):
            rendered.append(line)
            continue
        counter += 1
        rendered.append(f"{counter}. {match.group('body')}")
    return "\n".join(rendered)


def _public_answer(answer: str) -> str:
    """Hide internal evidence labels and normalize filtered numbering."""

    return _renumber_top_level_items(_EVIDENCE_LABEL.sub("", answer))


def _api_key() -> str | None:
    settings = get_settings()
    return settings.clova_studio_api_key or settings.hcx_api_key


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--query", required=True)
    parser.add_argument("--company", help="Fallback company when query resolution needs it")
    parser.add_argument("--year", type=int, help="Fallback year when query resolution needs it")
    parser.add_argument("--filing-id")
    parser.add_argument("--report-name")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=40)
    parser.add_argument("--max-total-chars", type=int, default=12000)
    parser.add_argument("--max-completion-tokens", type=int, default=1200)
    parser.add_argument("--show-evidence", action="store_true")
    args = parser.parse_args()

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        result = AnswerService(session, api_key=_api_key()).answer(
            args.query,
            fallback_company=args.company,
            fallback_year=args.year,
            filing_id=args.filing_id,
            report_name=args.report_name,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            max_total_chars=args.max_total_chars,
            max_completion_tokens=args.max_completion_tokens,
        )

    rails = ",".join(rail.value for rail in result.plan.route.rails)
    print("=== UNIFIED DISCLOSURE ANSWER ===")
    print(f"query                           {result.query}")
    print(f"mode                            {result.plan.mode.value}")
    print(f"rails                           {rails}")
    print(f"plan_reason                     {result.plan.reason}")
    print(f"analysis_status                 {result.status}")
    print(f"generator                       {result.generator}")
    print(f"evidence_count                  {len(result.evidence_pack.items)}")
    for key, value in result.metadata:
        print(f"{key:<32}{value}")

    if result.model_result is not None:
        print(f"finish_reason                   {result.model_result.finish_reason}")
        print(f"prompt_tokens                   {result.model_result.prompt_tokens}")
        print(f"completion_tokens               {result.model_result.completion_tokens}")
        print(f"total_tokens                    {result.model_result.total_tokens}")

    print("answer:")
    print(_public_answer(result.answer))
    print()
    print(render_source_references(result.source_references))

    if args.show_evidence:
        print()
        print(render_evidence_pack(result.evidence_pack))


if __name__ == "__main__":
    main()
