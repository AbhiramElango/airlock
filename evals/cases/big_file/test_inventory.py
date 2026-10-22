from inventory import Inventory, Item


def make():
    inv = Inventory()
    inv.add(Item("A100", "bolts", quantity=4, reorder_level=20))
    inv.add(Item("B200", "nuts", quantity=300, reorder_level=20))
    inv.add(Item("C300", "washers", quantity=20, reorder_level=20))
    return inv


def test_restock_list():
    assert [i.sku for i in make().restock_list()] == ["A100", "C300"]


def test_purchase_order():
    assert make().purchase_order() == {"A100": 50, "C300": 50}


def test_ship_and_receive():
    inv = make()
    assert inv.ship("B200", 100) == 200
    assert inv.receive("A100", 6) == 10
