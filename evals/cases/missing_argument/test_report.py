from report import ROOMS, total_area


def test_total_area():
    assert total_area(ROOMS) == 22
