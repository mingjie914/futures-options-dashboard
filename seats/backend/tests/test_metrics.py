from seat_dashboard.domain.metrics import safe_ratio, wilson_interval


def test_safe_ratio_is_explicit_about_zero_denominator():
    assert safe_ratio(10, 2) == 5.0
    assert safe_ratio(10, 0) is None
    assert safe_ratio(None, 2) is None


def test_wilson_interval_is_bounded():
    low, high = wilson_interval(6, 10)
    assert 0 <= low <= high <= 1
    assert wilson_interval(0, 0) == (None, None)
