-- Sales data for "Kopi Lereng", a fictional coffee-shop chain across Indonesian cities.
-- The comments matter: they are what the model reads to understand each column.

DROP TABLE IF EXISTS order_items, orders, customers, products, stores CASCADE;

CREATE TABLE stores (
    id         integer PRIMARY KEY,
    name       text NOT NULL,
    city       text NOT NULL,
    region     text NOT NULL,
    opened_on  date NOT NULL
);

COMMENT ON TABLE stores IS 'Coffee shop outlets, one row per store.';
COMMENT ON COLUMN stores.name IS 'Store name, e.g. Kopi Lereng Braga.';
COMMENT ON COLUMN stores.city IS 'City the store is in, e.g. Jakarta, Bandung, Salatiga.';
COMMENT ON COLUMN stores.region IS 'Island group: Jawa, Bali, Sumatra or Sulawesi.';
COMMENT ON COLUMN stores.opened_on IS 'Opening date. A store has no orders before it opened.';

CREATE TABLE products (
    id         integer PRIMARY KEY,
    name       text NOT NULL,
    category   text NOT NULL CHECK (category IN ('coffee', 'non_coffee', 'food', 'beans')),
    price_idr  integer NOT NULL CHECK (price_idr > 0)
);

COMMENT ON TABLE products IS 'Menu items and retail coffee beans.';
COMMENT ON COLUMN products.name IS 'Product name as printed on the menu, e.g. Es Kopi Susu Gula Aren.';
COMMENT ON COLUMN products.category IS 'One of: coffee, non_coffee, food, beans.';
COMMENT ON COLUMN products.price_idr IS 'Current menu price in Rupiah. Past sales may have used a lower price; use order_items.unit_price_idr for revenue.';

CREATE TABLE customers (
    id            integer PRIMARY KEY,
    full_name     text NOT NULL,
    city          text NOT NULL,
    joined_on     date NOT NULL,
    loyalty_tier  text NOT NULL CHECK (loyalty_tier IN ('bronze', 'silver', 'gold'))
);

COMMENT ON TABLE customers IS 'Members of the loyalty program. Walk-in buyers are not in this table.';
COMMENT ON COLUMN customers.full_name IS 'Customer full name.';
COMMENT ON COLUMN customers.city IS 'Home city of the customer.';
COMMENT ON COLUMN customers.joined_on IS 'Date the customer joined the loyalty program.';
COMMENT ON COLUMN customers.loyalty_tier IS 'One of: bronze, silver, gold.';

CREATE TABLE orders (
    id              integer PRIMARY KEY,
    store_id        integer NOT NULL REFERENCES stores (id),
    customer_id     integer REFERENCES customers (id),
    ordered_at      timestamptz NOT NULL,
    channel         text NOT NULL CHECK (channel IN ('dine_in', 'takeaway', 'delivery')),
    payment_method  text NOT NULL CHECK (payment_method IN ('qris', 'cash', 'debit_card', 'ewallet')),
    status          text NOT NULL CHECK (status IN ('completed', 'refunded'))
);

CREATE INDEX orders_store_id_idx ON orders (store_id);
CREATE INDEX orders_customer_id_idx ON orders (customer_id);
CREATE INDEX orders_ordered_at_idx ON orders (ordered_at);

COMMENT ON TABLE orders IS 'One row per receipt. Sales figures should only count status = ''completed''. Data runs from 2025-01-01 to 2026-09-30.';
COMMENT ON COLUMN orders.store_id IS 'References stores.id.';
COMMENT ON COLUMN orders.customer_id IS 'References customers.id. NULL for walk-in buyers who are not loyalty members.';
COMMENT ON COLUMN orders.ordered_at IS 'When the order was paid. Sessions use the Asia/Jakarta (WIB) time zone.';
COMMENT ON COLUMN orders.channel IS 'One of: dine_in, takeaway, delivery.';
COMMENT ON COLUMN orders.payment_method IS 'One of: qris, cash, debit_card, ewallet.';
COMMENT ON COLUMN orders.status IS 'completed or refunded. Refunded orders are not revenue.';

CREATE TABLE order_items (
    id              integer PRIMARY KEY,
    order_id        integer NOT NULL REFERENCES orders (id),
    product_id      integer NOT NULL REFERENCES products (id),
    quantity        integer NOT NULL CHECK (quantity > 0),
    unit_price_idr  integer NOT NULL CHECK (unit_price_idr > 0)
);

CREATE INDEX order_items_order_id_idx ON order_items (order_id);
CREATE INDEX order_items_product_id_idx ON order_items (product_id);

COMMENT ON TABLE order_items IS 'Lines on a receipt. Revenue = SUM(quantity * unit_price_idr), joined to orders with status = ''completed''.';
COMMENT ON COLUMN order_items.order_id IS 'References orders.id.';
COMMENT ON COLUMN order_items.product_id IS 'References products.id.';
COMMENT ON COLUMN order_items.quantity IS 'Number of units on this line.';
COMMENT ON COLUMN order_items.unit_price_idr IS 'Price per unit in Rupiah at the time of sale.';
