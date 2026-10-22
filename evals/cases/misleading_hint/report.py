from stats import mean

SCORES = [70, 80, 90]


def summary(scores=SCORES) -> str:
    return f"Average score: {mean(scores):.1f}"


if __name__ == "__main__":
    print(summary())
