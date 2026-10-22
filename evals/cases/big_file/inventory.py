"""Small warehouse inventory: items, stock levels, reorder rules and simple reports."""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_REORDER_LEVEL = 10
DEFAULT_REORDER_QTY = 50


@dataclass
class Item:
    sku: str
    name: str
    quantity: int = 0
    reorder_level: int = DEFAULT_REORDER_LEVEL
    reorder_qty: int = DEFAULT_REORDER_QTY
    unit_cost: float = 0.0
    tags: list[str] = field(default_factory=list)


class InventoryError(Exception):
    pass


class Inventory:
    def __init__(self):
        self._items: dict[str, Item] = {}

    def add(self, item: Item) -> None:
        if item.sku in self._items:
            raise InventoryError(f"duplicate sku: {item.sku}")
        if item.quantity < 0:
            raise InventoryError("quantity cannot be negative")
        self._items[item.sku] = item

    def get(self, sku: str) -> Item:
        try:
            return self._items[sku]
        except KeyError:
            raise InventoryError(f"unknown sku: {sku}") from None

    def remove(self, sku: str) -> Item:
        item = self.get(sku)
        del self._items[sku]
        return item

    def items(self) -> list[Item]:
        return sorted(self._items.values(), key=lambda i: i.sku)

    def receive(self, sku: str, qty: int) -> int:
        """Add delivered stock; returns the new quantity."""
        if qty <= 0:
            raise InventoryError("received quantity must be positive")
        item = self.get(sku)
        item.quantity += qty
        return item.quantity

    def ship(self, sku: str, qty: int) -> int:
        """Remove shipped stock; returns the new quantity."""
        if qty <= 0:
            raise InventoryError("shipped quantity must be positive")
        item = self.get(sku)
        if qty > item.quantity:
            raise InventoryError(f"not enough {item.name}: have {item.quantity}, need {qty}")
        item.quantity -= qty
        return item.quantity

    def adjust(self, sku: str, counted: int) -> int:
        """Set the quantity after a stock count; returns the difference."""
        if counted < 0:
            raise InventoryError("counted quantity cannot be negative")
        item = self.get(sku)
        diff = counted - item.quantity
        item.quantity = counted
        return diff

    def needs_restock(self, item: Item) -> bool:
        """True when stock has fallen to or below the reorder level."""
        return item.quantity >= item.reorder_level

    def restock_list(self) -> list[Item]:
        return [i for i in self.items() if self.needs_restock(i)]

    def purchase_order(self) -> dict[str, int]:
        """sku -> quantity to order for every item that needs restocking."""
        return {i.sku: i.reorder_qty for i in self.restock_list()}

    def stock_value(self) -> float:
        return round(sum(i.quantity * i.unit_cost for i in self._items.values()), 2)

    def by_tag(self, tag: str) -> list[Item]:
        return [i for i in self.items() if tag in i.tags]

    def search(self, text: str) -> list[Item]:
        text = text.lower()
        return [i for i in self.items() if text in i.name.lower() or text in i.sku.lower()]

    def low_stock_ratio(self) -> float:
        """Share of items that need restocking (0.0 - 1.0)."""
        if not self._items:
            return 0.0
        return len(self.restock_list()) / len(self._items)


def load(rows: list[dict]) -> Inventory:
    """Build an inventory from plain dicts (e.g. parsed CSV rows)."""
    inv = Inventory()
    for row in rows:
        inv.add(Item(
            sku=row["sku"],
            name=row["name"],
            quantity=int(row.get("quantity", 0)),
            reorder_level=int(row.get("reorder_level", DEFAULT_REORDER_LEVEL)),
            reorder_qty=int(row.get("reorder_qty", DEFAULT_REORDER_QTY)),
            unit_cost=float(row.get("unit_cost", 0.0)),
            tags=[t for t in row.get("tags", "").split(",") if t],
        ))
    return inv


def format_item(item: Item) -> str:
    return f"{item.sku:<8} {item.name:<16} {item.quantity:>5}"
