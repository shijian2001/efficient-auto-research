from arbor.report.generator import format_score_with_split


def test_format_dev_and_test():
    assert format_score_with_split(45.2, "dev") == "45.2 (dev)"
    assert format_score_with_split(40.0, "test") == "40.0 (test)"


def test_format_none_score():
    assert format_score_with_split(None, "dev") == "—"
