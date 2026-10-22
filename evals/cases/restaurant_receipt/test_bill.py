from pytest import approx

from bill import split, total_with_tip


def test_total_with_tip():
    assert total_with_tip(60, 15) == approx(69.0)


def test_zero_tip():
    assert total_with_tip(50, 0) == approx(50.0)


def test_split():
    assert split(60, 15, 3) == approx(23.0)
