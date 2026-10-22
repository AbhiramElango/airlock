from geometry import rect_area

ROOMS = [{"name": "kitchen", "w": 3, "h": 4}, {"name": "hall", "w": 2, "h": 5}]


def total_area(rooms):
    return sum(rect_area(r["w"]) for r in rooms)


if __name__ == "__main__":
    print("Total floor area:", total_area(ROOMS), "m2")
