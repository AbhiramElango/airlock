from invoices import tax_line


def test_tax_line():
    assert tax_line(200) == "Tax: 16.00"
