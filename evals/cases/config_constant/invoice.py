from settings import CURRENCY, TAX_RATE


def total(subtotal):
    """Subtotal plus sales tax."""
    return round(subtotal * (1 + TAX_RATE), 2)


if __name__ == "__main__":
    print(f"Total: {total(100):.2f} {CURRENCY}")
