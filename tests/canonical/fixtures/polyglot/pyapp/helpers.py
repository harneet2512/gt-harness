"""Pricing helpers imported by the order API and by tests."""


def round_price(value):
    return round(value, 2)


def apply_tax(total, rate):
    return round_price(total * (1 + rate))


def format_total(cents):
    taxed = apply_tax(cents / 100.0, 0.2)
    return "total=%.2f" % taxed
