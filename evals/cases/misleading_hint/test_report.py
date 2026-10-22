from report import summary


def test_summary():
    assert summary() == "Average score: 80.0"
