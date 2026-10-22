from config import TAX_PERCENT


def order_total(prices):
    return round(sum(prices) * (1 + TAX_PERCENT), 2)
