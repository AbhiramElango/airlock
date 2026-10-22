from pricing import apply_discount


def total(items, discount_percent=0):
    """Sum of item prices after the discount."""
    subtotal = sum(price for _, price in items)
    return round(apply_discount(subtotal, discount_percent), 2)


if __name__ == "__main__":
    cart = [("book", 40.0), ("pen", 10.0)]
    print("Total with 10% off:", total(cart, 10))
