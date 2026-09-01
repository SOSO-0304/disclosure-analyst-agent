#!/usr/bin/env python3
"""Execute deterministic metric analysis and generate one grounded HCX answer."""

from __future__ import annotations

import argparse

from disclosure_agent.config import get_settings
from disclosure_agent.llm.grounded_generation import generate_grounded_answer
from disclosure_agent.llm.hcx_client import HCX_MODEL, HcxClient
from disclosure_agent.llm.prompts import GROUNDING_SYSTEM_PROMPT, build_grounded_answer_prompt
from disclosure_agent.retrieval.evidence_pack import render_evidence_pack
from disclosure_agent.retrieval.metric_evidence import build_metric_evidence_pack
from disclosure_agent.retrieval.metric_query_planner import plan_metric_query
from disclosure_agent.retrieval.metric_target_resolver import resolve_metric_targets
from disclosure_agent.retrieval.source_references import (
    build_source_references,
    render_source_references,
)
from disclosure_agent.services.metric_analysis import MetricAnalysisService
from disclosure_agent.storage.database import get_engine, session_scope

PARTIAL_ANSWER = "제공된 공시에서 계산에 필요한 값을 모두 확인할 수 없습니다."


def _api_key() -> str:
    settings = get_settings()
    api_key = settings.clova_studio_api_key or settings.hcx_api_key
    if not api_key:
        raise SystemExit("Set CLOVA_STUDIO_API_KEY or HCX_API_KEY in .env before generation")
    return api_key


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--query", required=True)
    parser.add_argument("--company", help="Fallback company when the query omits one")
    parser.add_argument("--year", type=int, help="Fallback year when the query omits one")
    parser.add_argument("--max-total-chars", type=int, default=12000)
    parser.add_argument("--max-completion-tokens", type=int, default=1200)
    parser.add_argument("--show-evidence", action="store_true")
    args = parser.parse_args()

    intent = plan_metric_query(args.query)
    if intent.metric is None or intent.operation is None:
        raise SystemExit("Query is not supported by the structured metric planner")

    engine = get_engine(args.database_url)
    with session_scope(engine) as session:
        resolution = resolve_metric_targets(
            session,
            query=args.query,
            operation=intent.operation,
            fallback_company=args.company,
            fallback_year=args.year,
        )
        if resolution.status != "RESOLVED":
            reason = resolution.reason or resolution.status
            raise SystemExit(f"Metric target resolution failed: {reason}")

        result = MetricAnalysisService(session).analyze(
            metric=intent.metric,
            targets=resolution.targets,
            operation=intent.operation,
        )
        pack = build_metric_evidence_pack(
            session,
            query=args.query,
            result=result,
            max_total_chars=args.max_total_chars,
        )
        source_references = build_source_references(session, pack)

    print("=== GROUNDED METRIC ANSWER ===")
    print(f"query                           {args.query}")
    print(f"metric                          {intent.metric.value}")
    print(f"operation                       {intent.operation.value}")
    print(f"targets                         {len(resolution.targets)}")
    print(f"analysis_status                 {result.status}")
    print(f"evidence_count                  {len(pack.items)}")
    print(f"model                           {HCX_MODEL}")

    if result.status != "ANSWERABLE" or pack.retrieval_status == "NO_MATCH":
        print("generation                      skipped_incomplete_analysis")
        print("answer:")
        print(PARTIAL_ANSWER)
        print()
        print(render_source_references(source_references))
        if args.show_evidence:
            print()
            print(render_evidence_pack(pack))
        return

    prompt = build_grounded_answer_prompt(args.query, pack)
    with HcxClient(_api_key()) as client:
        answer = generate_grounded_answer(
            client,
            system_prompt=GROUNDING_SYSTEM_PROMPT,
            user_prompt=prompt,
            evidence_count=len(pack.items),
            max_completion_tokens=args.max_completion_tokens,
        )

    print(f"finish_reason                   {answer.finish_reason}")
    print(f"prompt_tokens                   {answer.prompt_tokens}")
    print(f"completion_tokens               {answer.completion_tokens}")
    print(f"total_tokens                    {answer.total_tokens}")
    print("answer:")
    print(answer.content)
    print()
    print(render_source_references(source_references))

    if args.show_evidence:
        print()
        print(render_evidence_pack(pack))


if __name__ == "__main__":
    main()
