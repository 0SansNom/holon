-- Test-only MySQL fixture for the SQL multi-driver connector.
-- Mounted at /docker-entrypoint-initdb.d on first container boot.
CREATE TABLE IF NOT EXISTS orders (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    customer_id INT NOT NULL,
    product     VARCHAR(255) NOT NULL,
    amount      DECIMAL(12, 2) NOT NULL,
    status      VARCHAR(64) NOT NULL,
    ordered_at  DATETIME NOT NULL
);

INSERT INTO orders (customer_id, product, amount, status, ordered_at) VALUES
    (1, 'Industrial Robot Arm X200', 45000.00, 'delivered', '2026-05-10 09:00:00'),
    (1, 'Robot Arm Maintenance Kit',  1200.00, 'delivered', '2026-06-02 11:30:00'),
    (2, 'Freight Contract Q3',       32000.00, 'shipped',   '2026-06-20 08:15:00'),
    (4, 'Foundry Casting Batch A',   61000.00, 'delivered', '2026-04-18 10:00:00'),
    (7, 'Precision Milling Machine', 87000.00, 'delivered', '2026-03-22 09:30:00');
