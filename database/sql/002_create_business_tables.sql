CREATE TABLE IF NOT EXISTS raw.customers (
    customer_id BIGINT PRIMARY KEY,
    customer_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    country VARCHAR(100) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS raw.orders (
    order_id BIGINT PRIMARY KEY,
    customer_id BIGINT NOT NULL REFERENCES raw.customers(customer_id),
    order_date TIMESTAMP NOT NULL,
    status VARCHAR(20) NOT NULL,
    amount NUMERIC(12,2) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    CONSTRAINT orders_status_check CHECK (status IN ('COMPLETED', 'PENDING', 'CANCELLED')),
    CONSTRAINT orders_amount_non_negative_check CHECK (amount >= 0)
);

CREATE TABLE IF NOT EXISTS curated.customer_revenue (
    customer_id BIGINT PRIMARY KEY,
    customer_name VARCHAR(255) NOT NULL,
    total_orders BIGINT NOT NULL,
    total_revenue NUMERIC(14,2) NOT NULL,
    last_order_date TIMESTAMP,
    updated_at TIMESTAMP NOT NULL
);