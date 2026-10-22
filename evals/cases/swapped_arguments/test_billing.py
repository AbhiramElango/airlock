from datetime import date

from billing import rental_cost


def test_three_days():
    assert rental_cost(date(2026, 10, 1), date(2026, 10, 4)) == 75
