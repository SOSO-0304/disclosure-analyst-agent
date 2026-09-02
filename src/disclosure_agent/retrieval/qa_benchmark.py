"""Independent raw-source gold validation and scoring for the contract QA baseline."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from hashlib import sha256
from math import ceil
from pathlib import Path
from typing import Any

import orjson
from lxml import html

GROUPS = {"facts": 12, "scope": 8, "paraphrase": 8, "missing": 6, "unsupported": 6}
FIELDS = {"contract_amount", "counterparty", "contract_start_date", "contract_end_date"}


def load_benchmark(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    benchmark = orjson.loads(raw)
    cases, records = benchmark["cases"], benchmark["records"]
    if benchmark["schema_version"] not in {"contract-qa-v1", "contract-qa-v2"} or len(cases) != 40:
        raise ValueError("Expected a versioned 40-case contract QA benchmark")
    if dict(Counter(case["group"] for case in cases)) != GROUPS:
        raise ValueError("Benchmark group counts changed")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Duplicate case IDs")
    if len(records) != 12 or benchmark["top_k"] != 5:
        raise ValueError("Expected 12 reviewed sources and top-k 5")
    if any(set(record["fields"]) != FIELDS for record in records.values()):
        raise ValueError("Each reviewed source must contain four gold fields")
    for case in cases:
        if not case["request"]["query"].strip():
            raise ValueError("Empty question")
        if set(case["request"]) - {
            "query",
            "company",
            "corp_code",
            "date_from",
            "date_to",
            "corrections",
        }:
            raise ValueError("Unsupported request options")
        expected = case["expected"]
        if expected["response"] not in {"fields", "unsupported", "no_results"}:
            raise ValueError("Unsupported expected response")
        if set(expected["fields"]) - FIELDS or set(expected["targets"]) - records.keys():
            raise ValueError("Unknown field or target")
        if expected["response"] == "fields" and not expected["targets"]:
            raise ValueError("Answerable case needs at least one accepted target")
    return benchmark, sha256(raw).hexdigest()


def verify_gold_sources(benchmark: Mapping[str, Any], root: Path) -> dict[str, Any]:
    """Literal XPath checks, independent of canonical parsing and field extraction.

    Gold values are hand-authored; this verifier never generates or repairs them.
    Source digest normalizes CRLF and trailing newlines only for checkout portability.
    """
    errors = []
    checked = 0
    root = root.resolve()
    for receipt, record in benchmark["records"].items():
        path = (root / record["source_path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            errors.append({"receipt": receipt, "reason": "raw_source_missing"})
            continue
        raw = path.read_bytes().replace(b"\r\n", b"\n").rstrip(b"\n")
        if sha256(raw).hexdigest() != record["source_normalized_sha256"]:
            errors.append({"receipt": receipt, "reason": "raw_source_hash_changed"})
            continue
        document = html.fromstring(raw.decode("utf-8"), parser=html.HTMLParser(no_network=True))
        for name, field in record["fields"].items():
            nodes = document.xpath(field["xpath"])
            if len(nodes) != 1 or " ".join(nodes[0].text_content().split()) != field["raw_text"]:
                errors.append(
                    {"receipt": receipt, "field": name, "reason": "gold_xpath_text_mismatch"}
                )
                continue
            literal = field["raw_text"]
            value = literal.replace(",", "") if name == "contract_amount" else literal
            value = None if literal == "-" else value
            expected_status = "missing" if value is None else "extracted"
            expected_unit = "KRW" if name == "contract_amount" and value is not None else None
            if (
                value != field["value"]
                or field["unit"] != expected_unit
                or field["status"] != expected_status
            ):
                errors.append({"receipt": receipt, "field": name, "reason": "gold_value_mismatch"})
                continue
            checked += 1
    return {
        "status": "verified" if not errors else "blocked",
        "records": len(benchmark["records"]),
        "checked_fields": checked,
        "errors": errors,
        "method": "pinned_raw_xml_literal_xpath; no production extractor used",
    }


def score_case(
    case: Mapping[str, Any],
    observed: Mapping[str, Any],
    records: Mapping[str, Any],
    target_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    expected = case["expected"]
    failures = []
    outcome = {
        "id": case["id"],
        "group": case["group"],
        "query": case["request"]["query"],
        "failures": failures,
        "retrieval_hit_at_5": None,
        "field_checks": [],
        "observed": observed,
    }
    if observed["kind"] == "error":
        failures.append("execution_error")
        outcome["passed"] = False
        return outcome
    if observed["kind"] == "clarification_required":
        failures.append("clarification_required")
        outcome["passed"] = False
        return outcome
    if expected["response"] == "unsupported":
        if (
            observed["kind"] != "unsupported"
            or not observed.get("reason")
            or observed.get("answer", {}).get("findings")
        ):
            failures.append("unsupported_intent_not_rejected")
        outcome["passed"] = not failures
        return outcome
    findings = observed.get("answer", {}).get("findings", [])
    targets = set(expected["targets"])
    actual_filters = observed.get("filters", {})
    if targets:
        companies = {target_metadata[t]["corp_code"] for t in targets}
        if any(hit["corp_code"] not in companies for hit in observed.get("hits", [])):
            failures.append("wrong_company_result")
        if expected.get("company_target") and actual_filters.get("corp_code") not in companies:
            failures.append("company_scope_not_applied")
    for key, expected_key in (("date_from", "receipt_from"), ("date_to", "receipt_to")):
        if (
            expected.get(expected_key) is not None
            and actual_filters.get(key) != expected[expected_key]
        ):
            failures.append("date_scope_not_applied")
    for hit in observed.get("hits", []):
        receipt_date = str(hit["receipt_date"])
        if (expected.get("receipt_from") and receipt_date < expected["receipt_from"]) or (
            expected.get("receipt_to") and receipt_date > expected["receipt_to"]
        ):
            failures.append("wrong_receipt_date_result")
    if expected["response"] == "no_results":
        if observed.get("hits") or findings:
            failures.append("unexpected_result_in_empty_scope")
        outcome["passed"] = not failures
        return outcome
    retrieved = [hit["receipt_number"] for hit in observed.get("hits", [])[:5]]
    outcome["retrieval_hit_at_5"] = bool(set(retrieved) & targets)
    if not outcome["retrieval_hit_at_5"]:
        failures.append("retrieval_miss")
    # Evaluate every accepted target that was actually returned; never select the best
    # duplicate finding and hide a wrong value or citation in another one.
    matching = [f for f in findings if f["citation"]["receipt_number"] in targets]
    if outcome["retrieval_hit_at_5"] and not matching:
        failures.append("extraction_missing")
    finding_statuses = sorted({f.get("status", "unknown") for f in matching})
    outcome["target_extraction_statuses"] = finding_statuses
    for finding in matching:
        receipt = finding["citation"]["receipt_number"]
        gold = records[receipt]
        for name in expected["fields"]:
            wanted, actual = gold["fields"][name], finding.get("fields", {}).get(name)
            correct = bool(
                actual
                and actual["status"] == wanted["status"]
                and actual["value"] == wanted["value"]
                and actual["unit"] == wanted["unit"]
            )
            evidence = actual.get("evidence", []) if actual else []
            url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
            grounded = bool(evidence) and all(
                e.get("value_locator") is not None
                and e["value_locator"].get("xpath") == wanted["xpath"]
                and e["value_locator"].get("source_file_id") == f"exchange_{receipt}:source:1"
                and " ".join(e.get("raw_value", "").split()) == wanted["raw_text"]
                and e.get("citation_url") == url
                and finding["citation"]["url"] == url
                and e.get("filing_id") == finding["filing_id"] == f"exchange_{receipt}"
                and e.get("table_id") == finding["table_id"]
                and e.get("document_id") == finding["document_id"]
                for e in evidence
            )
            outcome["field_checks"].append(
                {"receipt": receipt, "field": name, "correct": correct, "grounded": grounded}
            )
            if not correct:
                failures.append("field_value_status_or_unit_error")
            if not grounded:
                failures.append("evidence_error")
    outcome["failures"] = sorted(set(failures))
    outcome["passed"] = not failures
    return outcome


def summarize(results: list[dict[str, Any]], *, planned: int = 40) -> dict[str, Any]:
    ranking = [r for r in results if r["retrieval_hit_at_5"] is not None]
    fields = [field for result in results for field in result["field_checks"]]
    by_group = {}
    for group in GROUPS:
        selected = [r for r in results if r["group"] == group]
        by_group[group] = {"tested": len(selected), "passed": sum(r["passed"] for r in selected)}
    latencies = sorted(
        r["observed"]["timing_seconds"]["total"]
        for r in results
        if "timing_seconds" in r["observed"]
    )
    return {
        "planned": planned,
        "tested": len(results),
        "passed": sum(r["passed"] for r in results),
        "not_run": planned - len(results),
        "by_group": by_group,
        "retrieval_hit_at_5": sum(r["retrieval_hit_at_5"] for r in ranking) / len(ranking)
        if ranking
        else None,
        "retrieval_cases_scored": len(ranking),
        "fields_scored_on_retrieved_targets": len(fields),
        "field_accuracy_conditional": sum(f["correct"] for f in fields) / len(fields)
        if fields
        else None,
        "evidence_accuracy_conditional": sum(f["grounded"] for f in fields) / len(fields)
        if fields
        else None,
        "failure_categories": dict(Counter(f for r in results for f in r["failures"])),
        "latency_seconds": {
            "measured": len(latencies),
            "p50": latencies[ceil(len(latencies) * 0.50) - 1] if latencies else None,
            "p95": latencies[ceil(len(latencies) * 0.95) - 1] if latencies else None,
        },
        "all_development_checks_passed": len(results) == planned
        and all(r["passed"] for r in results),
        "production_readiness_claim": False,
    }
