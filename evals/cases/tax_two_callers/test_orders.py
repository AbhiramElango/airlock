from orders import order_total


def test_order_total():
    assert order_total([50, 50]) == 108.0
