from invoice import total


def test_total_includes_tax():
    assert total(100) == 108.0
