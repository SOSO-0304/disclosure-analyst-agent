from scripts.check_regression_baseline import check_regression_baseline


def _baseline():
    return {
        "total_cases": 3,
        "automated_pass_ids": ["A", "B"],
        "manual_review_ids": ["C"],
    }


def test_baseline_accepts_pass_and_manual_review():
    results = {
        "A": {"verdict": "PASS", "hard_passed": True},
        "B": {"verdict": "PASS", "hard_passed": True},
        "C": {"verdict": "MANUAL_REVIEW", "hard_passed": True},
    }

    assert check_regression_baseline(_baseline(), results) == ()


def test_baseline_allows_manual_case_to_become_pass():
    results = {
        "A": {"verdict": "PASS", "hard_passed": True},
        "B": {"verdict": "PASS", "hard_passed": True},
        "C": {"verdict": "PASS", "hard_passed": True},
    }

    assert check_regression_baseline(_baseline(), results) == ()


def test_baseline_rejects_automated_pass_regression():
    results = {
        "A": {"verdict": "FAIL", "hard_passed": False},
        "B": {"verdict": "PASS", "hard_passed": True},
        "C": {"verdict": "MANUAL_REVIEW", "hard_passed": True},
    }

    errors = check_regression_baseline(_baseline(), results)

    assert "A: baseline PASS regressed to FAIL" in errors
    assert "A: verdict=FAIL" in errors
    assert "A: hard_passed=false" in errors


def test_baseline_rejects_missing_or_extra_case():
    results = {
        "A": {"verdict": "PASS", "hard_passed": True},
        "C": {"verdict": "MANUAL_REVIEW", "hard_passed": True},
        "D": {"verdict": "PASS", "hard_passed": True},
    }

    errors = check_regression_baseline(_baseline(), results)

    assert "missing cases: B" in errors
    assert "unexpected cases: D" in errors
