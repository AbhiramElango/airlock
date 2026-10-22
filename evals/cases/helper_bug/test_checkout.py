from checkout import total


def test_no_discount():
    assert total([("a", 50.0)]) == 50.0


def test_ten_percent():
    assert total([("a", 40.0), ("b", 10.0)], 10) == 45.0
