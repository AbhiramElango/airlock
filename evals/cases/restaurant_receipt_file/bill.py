"""Split a restaurant bill, including tip, between friends."""


def total_with_tip(bill, tip_percent):
    """Bill plus tip. tip_percent is a percentage, e.g. 15 means 15%."""
    tip = bill * tip_percent
    return bill + tip


def split(bill, tip_percent, people):
    """Amount each person pays, rounded to cents."""
    return round(total_with_tip(bill, tip_percent) / people, 2)


if __name__ == "__main__":
    bill, tip_percent, people = 60.00, 15, 3
    print(f"Bill: ${bill:.2f}   Tip: {tip_percent}%   People: {people}")
    print(f"Total with tip:   ${total_with_tip(bill, tip_percent):.2f}")
    print(f"Each person pays: ${split(bill, tip_percent, people):.2f}")
