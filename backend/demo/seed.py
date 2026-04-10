"""Seed the coffee shop demo database with realistic data."""

import random
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from faker import Faker

fake = Faker()
Faker.seed(42)
random.seed(42)

# Date range: Jan 1 - Mar 31, 2026
START_DATE = datetime(2026, 1, 1)
END_DATE = datetime(2026, 3, 31)

LOCATIONS = [
    ("Downtown Roast", "123 Main St", "San Francisco", "CA", "2024-06-01"),
    ("University Perk", "456 College Ave", "Berkeley", "CA", "2024-09-15"),
    ("Suburban Sip", "789 Oak Blvd", "Walnut Creek", "CA", "2025-03-01"),
]

PRODUCTS = [
    # Coffee
    ("Drip Coffee", "coffee", "small", 2.50, 0.40),
    ("Drip Coffee", "coffee", "medium", 3.25, 0.50),
    ("Drip Coffee", "coffee", "large", 4.00, 0.60),
    ("Cold Brew", "coffee", "medium", 4.50, 0.70),
    ("Cold Brew", "coffee", "large", 5.25, 0.80),
    # Espresso
    ("Espresso", "espresso", "small", 3.00, 0.50),
    ("Latte", "espresso", "medium", 5.00, 0.90),
    ("Latte", "espresso", "large", 5.75, 1.00),
    ("Cappuccino", "espresso", "medium", 4.75, 0.85),
    ("Mocha", "espresso", "medium", 5.50, 1.10),
    ("Mocha", "espresso", "large", 6.25, 1.25),
    ("Americano", "espresso", "medium", 3.75, 0.55),
    # Tea
    ("Green Tea", "tea", "medium", 3.00, 0.35),
    ("Chai Latte", "tea", "medium", 4.50, 0.75),
    ("Chai Latte", "tea", "large", 5.25, 0.85),
    ("Herbal Tea", "tea", "medium", 3.00, 0.30),
    # Pastry
    ("Croissant", "pastry", None, 3.50, 1.20),
    ("Blueberry Muffin", "pastry", None, 3.75, 1.30),
    ("Chocolate Chip Cookie", "pastry", None, 2.75, 0.90),
    ("Banana Bread", "pastry", None, 4.00, 1.40),
    ("Scone", "pastry", None, 3.50, 1.15),
    # Sandwich
    ("Turkey Club", "sandwich", None, 8.50, 3.20),
    ("Veggie Wrap", "sandwich", None, 7.75, 2.80),
    ("Ham & Cheese", "sandwich", None, 7.50, 2.90),
    ("Avocado Toast", "sandwich", None, 8.00, 2.50),
]

CARTS = [
    "Ferry Building Cart",
    "Dolores Park Cart",
    "Mission Farmers Market Cart",
]

CART_ITEM_PRICES = [3.50, 4.00, 4.50, 5.00, 5.50, 6.00, 6.50]

CAMPAIGNS = [
    ("New Year Kickoff", "email", "2026-01-02", "2026-01-15", 2500.00, "all_customers", "completed"),
    ("Student Discount Week", "social_media", "2026-01-20", "2026-01-27", 1500.00, "students", "completed"),
    ("Valentine's Special", "in_store", "2026-02-10", "2026-02-16", 1000.00, "all_customers", "completed"),
    ("Referral Bonus", "referral", "2026-02-01", "2026-03-31", 3000.00, "loyalty_members", "active"),
    ("Spring Launch", "social_media", "2026-03-15", "2026-03-31", 2000.00, "new_customers", "active"),
]

TAX_RATE = 0.0875  # CA sales tax


def create_db(db_path: str) -> None:
    schema_path = Path(__file__).parent / "schema.sql"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Drop existing tables
    cursor.executescript("""
        DROP TABLE IF EXISTS loyalty_program;
        DROP TABLE IF EXISTS inventory;
        DROP TABLE IF EXISTS order_items;
        DROP TABLE IF EXISTS cart_orders;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS guests;
        DROP TABLE IF EXISTS campaigns;
        DROP TABLE IF EXISTS customers;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS locations;
    """)

    # Create tables
    cursor.executescript(schema_path.read_text())

    # Seed locations
    for name, address, city, state, opened in LOCATIONS:
        cursor.execute(
            "INSERT INTO locations (name, address, city, state, opened_date) VALUES (?, ?, ?, ?, ?)",
            (name, address, city, state, opened),
        )

    # Seed products
    for name, category, size, price, cost in PRODUCTS:
        cursor.execute(
            "INSERT INTO products (name, category, size, price, cost, is_active) VALUES (?, ?, ?, ?, ?, 1)",
            (name, category, size, price, cost),
        )

    # Seed customers
    customer_ids = []
    for i in range(200):
        first_order = START_DATE + timedelta(days=random.randint(0, 80))
        preferred_loc = random.choice([1, 2, 3]) if random.random() < 0.7 else None
        is_test_user = 1 if random.random() < 0.02 else 0
        if is_test_user:
            name = f"Test User {i + 1}"
            email = f"test_user_{i + 1}@example.test"
        else:
            name = fake.name()
            email = fake.unique.email()
        phone = fake.phone_number() if random.random() < 0.6 else None
        cursor.execute(
            "INSERT INTO customers (name, email, phone, first_order_date, preferred_location_id, is_test_user) VALUES (?, ?, ?, ?, ?, ?)",
            (name, email, phone, first_order.strftime("%Y-%m-%d"), preferred_loc, is_test_user),
        )
        customer_ids.append(cursor.lastrowid)

    # Seed orders and order_items
    product_count = len(PRODUCTS)
    order_id = 0
    location_weights = [0.45, 0.35, 0.20]  # Downtown busiest
    in_process_window_start = END_DATE - timedelta(days=4)  # last few days

    current = START_DATE
    while current <= END_DATE:
        is_weekend = current.weekday() >= 5
        base_orders = random.randint(15, 25) if is_weekend else random.randint(20, 35)

        for _ in range(base_orders):
            order_id += 1
            location_id = random.choices([1, 2, 3], weights=location_weights)[0]

            order_type = "online_pickup" if random.random() < 0.20 else "in_store"

            # Identity branching:
            # - online_pickup: always linked to a registered customer
            # - in_store: ~35% scanned rewards (customer_id), rest are walk-ins (guest_id)
            customer_id = None
            guest_id = None
            if order_type == "online_pickup":
                customer_id = random.choice(customer_ids)
            else:
                if random.random() < 0.35:
                    customer_id = random.choice(customer_ids)
                # else: guest will be created below alongside the order

            # Time of day: morning peak, lunch peak, afternoon
            hour_weights = {
                7: 8, 8: 15, 9: 12, 10: 8,
                11: 10, 12: 12, 13: 8,
                14: 5, 15: 5, 16: 4, 17: 3, 18: 2,
            }
            hour = random.choices(
                list(hour_weights.keys()),
                weights=list(hour_weights.values()),
            )[0]
            minute = random.randint(0, 59)
            order_time = current.replace(hour=hour, minute=minute)

            # Test order flag (~1%); a small slice of these come from test customers
            is_test = 1 if random.random() < 0.01 else 0

            # Status — only in_process within the recent window so "open today" is realistic
            status_roll = random.random()
            if current >= in_process_window_start and status_roll < 0.15:
                status = "in_process"
            elif status_roll < 0.02:
                status = "refunded"
            elif status_roll < 0.07:
                status = "cancelled"
            else:
                status = "completed"

            # Generate items (1-4 per order)
            num_items = random.choices([1, 2, 3, 4], weights=[40, 35, 18, 7])[0]
            items = []
            chosen_products = random.sample(range(1, product_count + 1), min(num_items, product_count))

            subtotal = 0.0
            for prod_id in chosen_products:
                qty = random.choices([1, 2], weights=[85, 15])[0]
                price = PRODUCTS[prod_id - 1][3]
                line_total = round(price * qty, 2)
                subtotal += line_total
                items.append((prod_id, qty, price, line_total))

            subtotal = round(subtotal, 2)
            tax = round(subtotal * TAX_RATE, 2)
            total = round(subtotal + tax, 2)

            # If this is a walk-in without rewards, create the guest row first
            if customer_id is None and order_type == "in_store":
                guest_is_test = is_test  # tag guest as test if the order is a test
                cursor.execute(
                    "INSERT INTO guests (created_at, location_id, is_test_user) VALUES (?, ?, ?)",
                    (order_time.strftime("%Y-%m-%d %H:%M:%S"), location_id, guest_is_test),
                )
                guest_id = cursor.lastrowid

            cursor.execute(
                "INSERT INTO orders (customer_id, guest_id, location_id, order_date, order_type, status, is_test, subtotal, tax, total) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    customer_id,
                    guest_id,
                    location_id,
                    order_time.strftime("%Y-%m-%d %H:%M:%S"),
                    order_type,
                    status,
                    is_test,
                    subtotal,
                    tax,
                    total,
                ),
            )
            actual_order_id = cursor.lastrowid

            for prod_id, qty, price, line_total in items:
                cursor.execute(
                    "INSERT INTO order_items (order_id, product_id, quantity, unit_price, line_total) VALUES (?, ?, ?, ?, ?)",
                    (actual_order_id, prod_id, qty, price, line_total),
                )

        current += timedelta(days=1)

    # Seed cart_orders (mobile cart stands — no location_id, no items)
    current = START_DATE
    while current <= END_DATE:
        is_weekend = current.weekday() >= 5
        cart_count = random.randint(15, 25) if is_weekend else random.randint(5, 12)
        for _ in range(cart_count):
            cart_name = random.choice(CARTS)
            hour = random.choices(
                [9, 10, 11, 12, 13, 14, 15, 16],
                weights=[5, 8, 10, 12, 10, 8, 6, 4],
            )[0]
            minute = random.randint(0, 59)
            order_time = current.replace(hour=hour, minute=minute)

            is_test = 1 if random.random() < 0.01 else 0

            status_roll = random.random()
            if current >= in_process_window_start and status_roll < 0.10:
                status = "in_process"
            elif status_roll < 0.03:
                status = "cancelled"
            elif status_roll < 0.04:
                status = "refunded"
            else:
                status = "completed"

            num_items = random.choices([1, 2, 3], weights=[55, 35, 10])[0]
            subtotal = round(sum(random.choice(CART_ITEM_PRICES) for _ in range(num_items)), 2)
            tax = round(subtotal * TAX_RATE, 2)
            total = round(subtotal + tax, 2)

            cursor.execute(
                "INSERT INTO cart_orders (cart_name, order_date, status, is_test, subtotal, tax, total) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    cart_name,
                    order_time.strftime("%Y-%m-%d %H:%M:%S"),
                    status,
                    is_test,
                    subtotal,
                    tax,
                    total,
                ),
            )
        current += timedelta(days=1)

    # Seed inventory
    for loc_id in [1, 2, 3]:
        for prod_id in range(1, product_count + 1):
            category = PRODUCTS[prod_id - 1][1]
            if category in ("coffee", "espresso", "tea"):
                qty = random.randint(20, 100)
                reorder = 15
            else:
                qty = random.randint(5, 30)
                reorder = 8
            # Make a few items intentionally low
            if random.random() < 0.1:
                qty = random.randint(1, 4)

            last_restocked = (END_DATE - timedelta(days=random.randint(1, 14))).strftime("%Y-%m-%d")
            cursor.execute(
                "INSERT INTO inventory (location_id, product_id, quantity_on_hand, reorder_level, last_restocked) VALUES (?, ?, ?, ?, ?)",
                (loc_id, prod_id, qty, reorder, last_restocked),
            )

    # Seed campaigns
    for name, channel, start, end, budget, audience, status in CAMPAIGNS:
        cursor.execute(
            "INSERT INTO campaigns (name, channel, start_date, end_date, budget, target_audience, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, channel, start, end, budget, audience, status),
        )

    # Seed loyalty program (~150 customers)
    loyalty_customers = random.sample(customer_ids, 150)
    for cust_id in loyalty_customers:
        enrolled = START_DATE + timedelta(days=random.randint(0, 60))
        points_earned = random.randint(50, 800)
        points_redeemed = random.randint(0, int(points_earned * 0.6))
        if points_earned >= 500:
            tier = "gold"
        elif points_earned >= 200:
            tier = "silver"
        else:
            tier = "bronze"
        cursor.execute(
            "INSERT INTO loyalty_program (customer_id, points_earned, points_redeemed, tier, enrolled_date) VALUES (?, ?, ?, ?, ?)",
            (cust_id, points_earned, points_redeemed, tier, enrolled.strftime("%Y-%m-%d")),
        )

    conn.commit()

    # Print summary
    for table in [
        "locations",
        "products",
        "customers",
        "guests",
        "orders",
        "order_items",
        "cart_orders",
        "inventory",
        "campaigns",
        "loyalty_program",
    ]:
        count = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count} rows")

    conn.close()


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / "coffee_shop.db")
    print(f"Seeding database at {db_path}...")
    create_db(db_path)
    print("Done!")
