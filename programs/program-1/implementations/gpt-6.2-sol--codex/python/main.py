import sys

orders = {}


def add_order(order_id, quantity):
    if type(quantity) is not int or not 1 <= quantity <= 1000:
        raise ValueError("quantity must be an integer from 1 to 1000")
    orders[order_id] = quantity
    return quantity


def discount_rate(quantity):
    return 10 if quantity >= 10 else 0


def quote_order(order_id):
    quantity = orders[order_id]
    return quantity * 1250 * (100 - discount_rate(quantity)) // 100

add_order("A", 9)
add_order("B", 12)
print("ready", flush=True)
for line in sys.stdin:
    print(quote_order(line.rstrip("\n")), flush=True)
