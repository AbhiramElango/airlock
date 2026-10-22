from settings import TAX_PERCENT


def add_tax(amount):
    """Amount plus sales tax."""
    return amount * (1 + TAX_PERCENT)
