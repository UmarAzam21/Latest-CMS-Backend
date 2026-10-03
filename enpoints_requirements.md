# Digital Khata / Daily Khata: Backend Spec (FastAPI + MySQL 8.0.16+)

## Rules that matter (read first)
1. ONE `expense_entries` table with `kind` ENUM('income','expense','debt'). No per-kind tables/endpoints.
2. The server owns ALL card-balance math (frontend currently simulates it in localStorage). Every balance change runs in one DB transaction and writes a `card_transactions` ledger row.
3. Card delta rule (mirror of frontend `cardDelta`):
   income +amount | expense -amount | debt+lena +amount (I borrowed) | debt+dena -amount (I lent)
4. Debt entries are ledger-style: card is affected at creation and at each payment. Editing/deleting a debt does NOT touch cards. Income/expense edits reverse the old delta and apply the new one; delete reverses.
5. Every mutating endpoint returns the affected cards so the UI updates without a refetch:
   `{ "entry": {...}, "cards": [ {...updated card...} ] }`
6. All rows are scoped to the authenticated user (`user_id` from JWT). Never trust ids without checking ownership.
7. JSON is camelCase (the frontend types are camelCase). Pydantic: `alias_generator=to_camel, populate_by_name=True`, and `response_model_by_alias=True`. Money = DECIMAL in DB, serialized as JSON *number* (Pydantic v2 sends Decimal as string by default, so add a float serializer).

## MySQL DDL
```sql
CREATE TABLE expense_categories (
  id CHAR(36) PRIMARY KEY,
  user_id CHAR(36) NOT NULL,
  kind ENUM('income','expense','debt') NOT NULL,
  label VARCHAR(60) NOT NULL,
  color ENUM('primary','secondary','warning','info','danger','neutral') NOT NULL DEFAULT 'neutral',
  system_key VARCHAR(30) NULL,            -- 'udhaar_lena','udhaar_dena','other_income','other_expense' = protected defaults
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_user_kind_label (user_id, kind, label),
  KEY idx_user_kind (user_id, kind),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE cards (
  id CHAR(36) PRIMARY KEY,
  user_id CHAR(36) NOT NULL,
  label VARCHAR(60) NOT NULL,
  last4 CHAR(4) NOT NULL,
  balance DECIMAL(14,2) NOT NULL DEFAULT 0,
  expiry_month TINYINT NOT NULL,          -- server sets = created month
  expiry_year TINYINT NOT NULL,           -- server sets = (created year + 5) % 100. Ignore client values.
  gradient ENUM('primary','secondary','dark') NOT NULL DEFAULT 'dark',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_user (user_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE expense_entries (
  id CHAR(36) PRIMARY KEY,
  user_id CHAR(36) NOT NULL,
  kind ENUM('income','expense','debt') NOT NULL,
  subject VARCHAR(120) NOT NULL,
  category_id CHAR(36) NOT NULL,
  amount DECIMAL(14,2) NOT NULL,          -- for debt = REMAINING amount
  original_amount DECIMAL(14,2) NULL,     -- debt only; set = amount on create
  entry_date DATE NOT NULL,
  description TEXT NULL,
  card_id CHAR(36) NULL,
  debt_direction ENUM('lena','dena') NULL,-- debt only; sent explicitly by the frontend
  is_settled TINYINT(1) NOT NULL DEFAULT 0,
  receipt_image_url VARCHAR(500) NULL,    -- not used by current UI; keep nullable for later
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT chk_amount CHECK (amount >= 0),
  CONSTRAINT chk_debt_dir CHECK (kind <> 'debt' OR debt_direction IS NOT NULL),
  KEY idx_user_date (user_id, entry_date),
  KEY idx_user_kind_date (user_id, kind, entry_date),
  KEY idx_card (card_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (category_id) REFERENCES expense_categories(id),
  FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE SET NULL
);

CREATE TABLE card_transactions (          -- append-only audit ledger
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id CHAR(36) NOT NULL,
  card_id CHAR(36) NOT NULL,
  entry_id CHAR(36) NULL,
  source ENUM('entry_create','entry_update','entry_delete','debt_payment','transfer_in','transfer_out','manual_edit') NOT NULL,
  delta DECIMAL(14,2) NOT NULL,
  balance_after DECIMAL(14,2) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  KEY idx_card_time (card_id, created_at),
  FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE CASCADE
);
```

## Endpoints (all under /api, all require auth)
| Method | Path | Notes |
|---|---|---|
| GET | /expense-categories?kind= | filter server-side (replaces client `getCategoriesForEntryKind`) |
| POST | /expense-categories | body {label,color,kind}. 409 on duplicate (user,kind,label). Debt kind not creatable. |
| PATCH | /expense-categories/{id} | label/color only |
| DELETE | /expense-categories/{id} | 400 if kind=debt or system_key set. Reassign its entries to that kind's "Other" (system_key) in the same txn. |
| GET | /cards | |
| POST | /cards | 409 if user already has 3 cards (count inside txn with `SELECT ... FOR UPDATE`; MySQL CHECK can't span rows). Server sets expiry. |
| PATCH | /cards/{id} | label,last4,balance. Balance edit writes a `manual_edit` ledger row. |
| DELETE | /cards/{id} | entries.card_id -> NULL (FK does it) |
| POST | /cards/transfer | {fromCardId,toCardId,amount}. 400 if same card or balance < amount. Lock both rows `FOR UPDATE` ordered by id (avoid deadlock). Returns both cards. |
| GET | /expense-entries | query: kind, categoryId, cardId, from, to, q, sort(date\|amount\|subject), dir, page, pageSize (UI shows 8/page) |
| POST | /expense-entries | body below. Applies card delta if cardId. Returns {entry,cards}. |
| PATCH | /expense-entries/{id} | partial. Reconcile per rule 4. Returns {entry,cards}. |
| DELETE | /expense-entries/{id} | reverse non-debt card delta. Returns {cards}. |
| POST | /expense-entries/{id}/payments | {amount}. paid=min(amount,remaining); remaining-=paid; is_settled=(remaining==0); if cardId: lena -> card -paid, dena -> card +paid. Returns {entry,cards}. |
| GET | /khata/summary?from&to | totals (income, expense, debtPayable, debtReceivable, cardsBalance), daily/weekly/monthly trends, category breakdown WITH server-computed percentOfTotal |

Note: the UI's single "Total Udhaar" currently sums lena+dena together. Return `debtPayable` (lena, I owe) and `debtReceivable` (dena, owed to me) separately so we can split that card later.

## POST /expense-entries body (camelCase)
```json
{ "kind":"expense", "subject":"Bijli ka bill", "categoryId":"<uuid>", "amount":8500,
  "date":"2026-09-10", "description":null, "cardId":"<uuid>|null", "debtDirection":null }
```
Validation: subject 1-120; amount > 0; categoryId must belong to user AND its kind must equal `kind`; cardId (if any) must belong to user; debtDirection required iff kind=debt.

## Transaction skeleton (create)
```python
async with db.begin():
    entry = insert_entry(...)
    if entry.card_id:
        delta = card_delta(entry)          # rule 3
        card = get_card_for_update(entry.card_id, user_id)
        card.balance += delta
        insert_ledger(card, entry, "entry_create", delta)
return {"entry": entry, "cards": [card] if entry.card_id else []}
```