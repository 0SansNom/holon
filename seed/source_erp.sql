-- Idempotent reset of the demo source data (tables already created by docker/postgres-init/01-init.sql
-- on first container boot). Used by `make seed` to reset state without wiping the whole volume.
-- Truncated together: orders.customer_id references customers(id).
TRUNCATE TABLE orders, customers RESTART IDENTITY;

INSERT INTO customers (name, email, country, segment, lifetime_value) VALUES
    ('Acme Robotics',        'contact@acme-robotics.example',   'FR', 'enterprise', 184500.00),
    ('Nordic Freight',       'ops@nordicfreight.example',       'SE', 'enterprise', 92300.50),
    ('Bluewave Retail',      'hello@bluewave.example',          'ES', 'mid-market', 41250.00),
    ('Kappa Foundries',      'info@kappafoundries.example',     'DE', 'enterprise', 265900.75),
    ('Solaris Energy Co',    'contracts@solarisenergy.example', 'IT', 'mid-market', 58700.20),
    ('Meridian Logistics',   'support@meridianlog.example',     'FR', 'smb',        12300.00),
    ('Vertex Manufacturing', 'sales@vertexmfg.example',         'PL', 'enterprise', 198400.00),
    ('Orion Data Systems',   'billing@oriondata.example',       'NL', 'mid-market', 76200.10),
    ('Cedar & Finch',        'orders@cedarfinch.example',       'FR', 'smb',        8900.00),
    ('Halcyon Pharma',       'procurement@halcyonpharma.example','BE', 'enterprise', 312750.00);

INSERT INTO orders (customer_id, product, amount, status, ordered_at) VALUES
    (1,  'Industrial Robot Arm X200',    45000.00, 'delivered', '2026-05-10T09:00:00Z'),
    (1,  'Robot Arm Maintenance Kit',     1200.00, 'delivered', '2026-06-02T11:30:00Z'),
    (1,  'Custom Automation Software',    8000.00, 'pending',   '2026-07-15T14:00:00Z'),
    (2,  'Freight Contract Q3',          32000.00, 'shipped',   '2026-06-20T08:15:00Z'),
    (2,  'Freight Contract Q4',          29000.00, 'pending',   '2026-07-25T08:15:00Z'),
    (4,  'Foundry Casting Batch A',      61000.00, 'delivered', '2026-04-18T10:00:00Z'),
    (4,  'Foundry Casting Batch B',      58000.00, 'shipped',   '2026-07-01T10:00:00Z'),
    (5,  'Solar Panel Array (50 units)', 22000.00, 'delivered', '2026-05-05T13:45:00Z'),
    (7,  'Precision Milling Machine',    87000.00, 'delivered', '2026-03-22T09:30:00Z'),
    (7,  'CNC Retrofit Kit',             15000.00, 'shipped',   '2026-06-14T09:30:00Z'),
    (7,  'Annual Service Contract',       6000.00, 'pending',   '2026-07-28T09:30:00Z'),
    (8,  'Data Pipeline License',        18000.00, 'delivered', '2026-05-30T16:00:00Z'),
    (10, 'Pharma Cold Chain Unit',       42000.00, 'delivered', '2026-04-02T12:00:00Z'),
    (10, 'Compliance Audit Package',      9500.00, 'shipped',   '2026-07-10T12:00:00Z');
