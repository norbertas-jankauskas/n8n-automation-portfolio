# Order processing — offline APC simulation

Simulation of a common client request type: processing new orders and sending notifications, using APIs and Google Sheets.

## Assumptions

- Single currency: EUR; prices and totals use integer cents.
- Mock data only; offline only; no services, credentials, or network nodes.
- We chose the validation rules; they are not confirmed client requirements.

## Data contract

- Input: order_id, created_at, customer_name, customer_email, and items containing
  sku, qty, unit_price_cents. The first four fields must be non-empty strings.
- Email must match `/^[^\s@]+@[^\s@]+\.[^\s@]+$/`; empty email produces both
  missing and invalid reasons. items must be a non-empty array; each qty must be
  a positive integer and each unit_price_cents a non-negative integer.
- Validate Order preserves input fields and adds valid (boolean), reasons (array
  of strings; empty when valid). Quantity/price reasons identify items[i].
- Valid output: {sheet_row, notification}. sheet_row contains order_id, created_at,
  customer_name, customer_email, items_count (line count), total_cents (sum of
  qty * unit_price_cents), total_eur (two decimals, integer-cent formatting).
- notification: {to: "ops@example.com", subject: "New order " + order_id, body};
  body includes total_eur + " EUR". Invalid output: {error: {order_id, reasons}}.
- A-1001 produces 3000 cents / "30.00"; B-1002 produces email and quantity errors.

## Later integration (not done here)

- Replace Manual Trigger + Sample Orders with Webhook or a Google Sheets/API trigger.
- Route sheet_row to Google Sheets "Append row"; route notification to an email /
  Send Email or Gmail node; route errors to a separate notification.
- All integrations need credentials and are NOT done here.
- The workflow is inactive; it was executed only in an isolated n8n 2.41.7 test instance with synthetic data (see `evidence/`).

## Tests

Run `python3 -m unittest tests.test_n8n_order`; tests check structure and Python rules without executing JS.
