from datetime import date

from dates import days_between

DAILY_RATE = 25


def rental_cost(picked_up: date, returned: date) -> int:
    return days_between(returned, picked_up) * DAILY_RATE


if __name__ == "__main__":
    print("Cost:", rental_cost(date(2026, 10, 1), date(2026, 10, 4)))
