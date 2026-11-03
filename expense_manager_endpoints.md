# Expense manager business accounts

The phone/PIN login owns one or more businesses. Registering creates the first
business; older accounts get a Personal business when they next log in. Expense
cards, categories, income, expenses, and debts are scoped to the active business.

All endpoints are under `/api/expenses/auth` and require the expense manager's
Bearer token except `/register` and `/login`.

| Method | Path | Purpose |
|---|---|---|
| POST | `/register` | Create the login and its first business |
| POST | `/login` | Authenticate and select the account's first business |
| GET | `/businesses` | List businesses and return `active_business_id` |
| POST | `/businesses` | Create a business and select it |
| POST | `/businesses/{business_id}/switch` | Select an owned business |

Registration accepts `phone_number`, `pin`, `business_name`, and optional
`business_type` and three-letter `currency` (defaults: `personal` and `PKR`).
Creating another business accepts `business_name`, optional `business_type`
(default `business`), and optional `currency` (default `PKR`).

Registration, login, create-business, and switch-business responses include a
new `access_token`, `active_business_id`, and the account's `businesses` list.
Use the returned token after creating or switching businesses. The token scopes
all `/api/expenses` data operations to that business. A business ID owned by a
different login cannot be selected.