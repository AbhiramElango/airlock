from datetime import date


def days_between(start: date, end: date) -> int:
    return (end - start).days
