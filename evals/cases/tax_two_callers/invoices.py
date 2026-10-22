from config import TAX_PERCENT


def tax_line(subtotal):
    return f"Tax: {subtotal * TAX_PERCENT:.2f}"
