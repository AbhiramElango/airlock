"""The menu and its prices."""

MENU = {"burger": 12.50, "fries": 4.00, "salad": 9.50, "soda": 2.00}


def price_of(item):
    return MENU[item]
