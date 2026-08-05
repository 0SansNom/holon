// Seed data for the second, structurally different connector source
// (§9.1) — a document store, not a relational table. Uneven distribution
// across customers mirrors seed/source_erp.sql's orders: customers 3, 6
// and 9 get no tickets at all.
db = db.getSiblingDB("support_desk");

db.support_tickets.insertMany([
    { id: 1,  customer_id: 1,  subject: "Robot arm calibration issue",        status: "open",   priority: "high",   created_at: new Date("2026-06-01T09:00:00Z") },
    { id: 2,  customer_id: 1,  subject: "Request training session",          status: "closed", priority: "low",    created_at: new Date("2026-06-15T10:00:00Z") },
    { id: 3,  customer_id: 2,  subject: "Invoice discrepancy Q3",             status: "open",   priority: "medium", created_at: new Date("2026-07-02T11:00:00Z") },
    { id: 4,  customer_id: 4,  subject: "Casting batch B delayed",            status: "open",   priority: "high",   created_at: new Date("2026-07-05T08:30:00Z") },
    { id: 5,  customer_id: 5,  subject: "Panel installation question",        status: "closed", priority: "low",    created_at: new Date("2026-05-20T14:00:00Z") },
    { id: 6,  customer_id: 7,  subject: "CNC retrofit compatibility",         status: "open",   priority: "medium", created_at: new Date("2026-06-20T09:15:00Z") },
    { id: 7,  customer_id: 7,  subject: "Service contract renewal",           status: "closed", priority: "low",    created_at: new Date("2026-07-01T09:15:00Z") },
    { id: 8,  customer_id: 8,  subject: "Data pipeline license key issue",    status: "open",   priority: "high",   created_at: new Date("2026-06-10T16:30:00Z") },
    { id: 9,  customer_id: 10, subject: "Cold chain unit alarm false trigger", status: "open",   priority: "medium", created_at: new Date("2026-06-25T12:00:00Z") },
    { id: 10, customer_id: 10, subject: "Compliance audit follow-up",         status: "closed", priority: "low",    created_at: new Date("2026-07-12T12:00:00Z") },
]);
