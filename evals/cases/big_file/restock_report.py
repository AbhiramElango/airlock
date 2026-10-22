from inventory import format_item, load

ROWS = [
    {"sku": "A100", "name": "bolts", "quantity": "4", "reorder_level": "20"},
    {"sku": "B200", "name": "nuts", "quantity": "300", "reorder_level": "20"},
    {"sku": "C300", "name": "washers", "quantity": "20", "reorder_level": "20"},
]


def report(rows=ROWS) -> list[str]:
    return [format_item(i) for i in load(rows).restock_list()]


if __name__ == "__main__":
    print("\n".join(report()))
