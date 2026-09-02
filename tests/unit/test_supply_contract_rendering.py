from disclosure_agent.rendering.supply_contract import _has_false_termination_year_premise


def test_false_termination_year_premise_is_detected() -> None:
    assert _has_false_termination_year_premise(
        "두산퓨얼셀의 연료전지 시스템 공급 계약은 2023년에 해지된 거지?",
        formation_year=2023,
    )


def test_formation_year_question_is_not_false_premise() -> None:
    assert not _has_false_termination_year_premise(
        "두산퓨얼셀이 2023년에 체결한 주요 계약 중 이후 해지된 계약이 존재하는가?",
        formation_year=2023,
    )
