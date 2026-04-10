CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'CA',
    opened_date DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('coffee', 'espresso', 'tea', 'pastry', 'sandwich', 'other')),
    size TEXT CHECK (size IN ('small', 'medium', 'large')),
    price REAL NOT NULL,
    cost REAL NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE,
    phone TEXT,
    first_order_date DATE NOT NULL,
    preferred_location_id INTEGER REFERENCES locations(id),
    is_test_user INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS guests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at DATETIME NOT NULL,
    location_id INTEGER REFERENCES locations(id),
    is_test_user INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER REFERENCES customers(id),
    guest_id INTEGER REFERENCES guests(id),
    location_id INTEGER NOT NULL REFERENCES locations(id),
    order_date DATETIME NOT NULL,
    order_type TEXT NOT NULL CHECK (order_type IN ('in_store', 'online_pickup')),
    status TEXT NOT NULL DEFAULT 'completed'
        CHECK (status IN ('in_process', 'pending', 'completed', 'cancelled', 'refunded')),
    is_test INTEGER NOT NULL DEFAULT 0,
    subtotal REAL NOT NULL,
    tax REAL NOT NULL,
    total REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cart_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cart_name TEXT NOT NULL,
    order_date DATETIME NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed'
        CHECK (status IN ('in_process', 'completed', 'cancelled', 'refunded')),
    is_test INTEGER NOT NULL DEFAULT 0,
    subtotal REAL NOT NULL,
    tax REAL NOT NULL,
    total REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price REAL NOT NULL,
    line_total REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER NOT NULL REFERENCES locations(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity_on_hand INTEGER NOT NULL DEFAULT 0,
    reorder_level INTEGER NOT NULL DEFAULT 10,
    last_restocked DATE,
    UNIQUE(location_id, product_id)
);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('email', 'social_media', 'in_store', 'referral')),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    budget REAL NOT NULL,
    target_audience TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('planned', 'active', 'completed', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS loyalty_program (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    points_earned INTEGER NOT NULL DEFAULT 0,
    points_redeemed INTEGER NOT NULL DEFAULT 0,
    tier TEXT NOT NULL DEFAULT 'bronze' CHECK (tier IN ('bronze', 'silver', 'gold')),
    enrolled_date DATE NOT NULL,
    UNIQUE(customer_id)
);
