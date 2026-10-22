"""Receipt for one order: food and sales tax."""
from menu import price_of
from settings import CURRENCY
from tax import add_tax

ORDER = ["burger", "fries", "soda"]


def totals(order=ORDER):
    food = sum(price_of(item) for item in order)
    return {"food": food, "total": round(add_tax(food), 2)}


def receipt(order=ORDER):
    t = totals(order)
    lines = [f"{item:<12}{CURRENCY}{price_of(item):>8.2f}" for item in order]
    lines += ["-" * 21,
              f"{'Food':<12}{CURRENCY}{t['food']:>8.2f}",
              f"{'Total':<12}{CURRENCY}{t['total']:>8.2f}  (incl. tax)"]
    return "\n".join(lines)


if __name__ == "__main__":
    print(receipt())
