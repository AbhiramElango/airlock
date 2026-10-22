from pytest import approx

from receipt import totals


def test_food_subtotal():
    assert totals()["food"] == approx(18.50)


def test_total_includes_tax():
    assert totals()["total"] == approx(19.98)
