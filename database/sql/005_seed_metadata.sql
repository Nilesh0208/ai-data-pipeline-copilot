INSERT INTO metadata.table_metadata (schema_name, table_name, table_type, description, created_at, updated_at)
VALUES
    ('raw', 'customers', 'source', 'Raw customer master records used as source data for future pipeline inspection.', '2024-01-01 00:00:00', '2024-01-01 00:00:00'),
    ('raw', 'orders', 'source', 'Raw customer order transactions used as source data for future revenue aggregation.', '2024-01-01 00:00:00', '2024-01-01 00:00:00'),
    ('curated', 'customer_revenue', 'target', 'Curated target table planned for customer-level revenue metrics.', '2024-01-01 00:00:00', '2024-01-01 00:00:00')
ON CONFLICT (schema_name, table_name) DO UPDATE SET
    table_type = EXCLUDED.table_type,
    description = EXCLUDED.description,
    updated_at = EXCLUDED.updated_at;

INSERT INTO metadata.column_metadata (schema_name, table_name, column_name, data_type, is_nullable, is_primary_key, description, created_at)
VALUES
    ('raw', 'customers', 'customer_id', 'BIGINT', false, true, 'Stable customer identifier from the source system.', '2024-01-01 00:00:00'),
    ('raw', 'customers', 'customer_name', 'VARCHAR', false, false, 'Customer display name.', '2024-01-01 00:00:00'),
    ('raw', 'customers', 'email', 'VARCHAR', false, false, 'Customer email address.', '2024-01-01 00:00:00'),
    ('raw', 'customers', 'country', 'VARCHAR', false, false, 'Customer country.', '2024-01-01 00:00:00'),
    ('raw', 'customers', 'created_at', 'TIMESTAMP', false, false, 'Source record creation timestamp.', '2024-01-01 00:00:00'),
    ('raw', 'customers', 'updated_at', 'TIMESTAMP', false, false, 'Source record last update timestamp.', '2024-01-01 00:00:00'),
    ('raw', 'orders', 'order_id', 'BIGINT', false, true, 'Stable order identifier from the source system.', '2024-01-01 00:00:00'),
    ('raw', 'orders', 'customer_id', 'BIGINT', false, false, 'Customer identifier linked to raw.customers.', '2024-01-01 00:00:00'),
    ('raw', 'orders', 'order_date', 'TIMESTAMP', false, false, 'Timestamp when the customer placed the order.', '2024-01-01 00:00:00'),
    ('raw', 'orders', 'status', 'VARCHAR', false, false, 'Order lifecycle status such as COMPLETED, PENDING, or CANCELLED.', '2024-01-01 00:00:00'),
    ('raw', 'orders', 'amount', 'NUMERIC(12,2)', false, false, 'Order monetary amount in the listed currency.', '2024-01-01 00:00:00'),
    ('raw', 'orders', 'currency', 'VARCHAR(3)', false, false, 'Three-letter currency code.', '2024-01-01 00:00:00'),
    ('raw', 'orders', 'created_at', 'TIMESTAMP', false, false, 'Source record creation timestamp.', '2024-01-01 00:00:00'),
    ('raw', 'orders', 'updated_at', 'TIMESTAMP', false, false, 'Source record last update timestamp.', '2024-01-01 00:00:00'),
    ('curated', 'customer_revenue', 'customer_id', 'BIGINT', false, true, 'Customer identifier for the curated revenue record.', '2024-01-01 00:00:00'),
    ('curated', 'customer_revenue', 'customer_name', 'VARCHAR', false, false, 'Customer display name carried into the curated table.', '2024-01-01 00:00:00'),
    ('curated', 'customer_revenue', 'total_orders', 'BIGINT', false, false, 'Planned count of customer orders included in the revenue aggregate.', '2024-01-01 00:00:00'),
    ('curated', 'customer_revenue', 'total_revenue', 'NUMERIC(14,2)', false, false, 'Planned customer revenue aggregate.', '2024-01-01 00:00:00'),
    ('curated', 'customer_revenue', 'last_order_date', 'TIMESTAMP', true, false, 'Most recent order timestamp planned for the aggregate.', '2024-01-01 00:00:00'),
    ('curated', 'customer_revenue', 'updated_at', 'TIMESTAMP', false, false, 'Curated record last update timestamp.', '2024-01-01 00:00:00')
ON CONFLICT (schema_name, table_name, column_name) DO UPDATE SET
    data_type = EXCLUDED.data_type,
    is_nullable = EXCLUDED.is_nullable,
    is_primary_key = EXCLUDED.is_primary_key,
    description = EXCLUDED.description;

INSERT INTO metadata.pipeline_metadata (
    pipeline_name,
    description,
    source_tables,
    target_table,
    load_type,
    schedule,
    is_active,
    created_at,
    updated_at
)
VALUES (
    'customer_revenue_daily',
    'Planned daily incremental pipeline from raw customer and order data into curated customer revenue. Execution is not implemented in Phase 2.',
    ARRAY['raw.customers', 'raw.orders'],
    'curated.customer_revenue',
    'incremental',
    'daily',
    true,
    '2024-01-01 00:00:00',
    '2024-01-01 00:00:00'
)
ON CONFLICT (pipeline_name) DO UPDATE SET
    description = EXCLUDED.description,
    source_tables = EXCLUDED.source_tables,
    target_table = EXCLUDED.target_table,
    load_type = EXCLUDED.load_type,
    schedule = EXCLUDED.schedule,
    is_active = EXCLUDED.is_active,
    updated_at = EXCLUDED.updated_at;