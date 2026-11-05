# Expense manager notifications

One notification system for the mobile app and the web dashboard. Both clients
use the same API, read the same inbox, and share read state.

## How it works

```
Expense route / reminder job
        |  service.publish(), in the caller's database transaction
        v
expense_notifications            the inbox, read by every client
expense_notification_deliveries  the outbox, one row per channel
        |  dispatcher claims due rows (FOR UPDATE SKIP LOCKED)
        v
push channel       -> FirebasePushProvider -> Android, iOS, web push
websocket channel  -> Redis pub/sub -> API processes -> open sockets
```

- **Nothing is lost or invented.** A notification commits with the expense it
  describes. If the expense rolls back, so does the notification, and a Firebase
  or Redis outage only delays delivery.
- **The request path makes no network calls.** `publish()` only inserts rows.
- **A notification failure can't fail a transaction.** `publish()` logs the
  problem and carries on.
- **Delivery is at-least-once.** Clients ignore notification ids they already have.

A delivery that fails retries with exponential backoff, up to
`NOTIFICATION_MAX_ATTEMPTS`, then becomes `dead` and is logged at error level. After a
partial multicast failure, only the devices that failed are retried. Deliveries older
than `NOTIFICATION_DELIVERY_MAX_AGE_HOURS` are skipped, so an outage doesn't end in a
flood of stale pushes.

## Layout

| Path | Purpose |
|---|---|
| `events.py` | Catalog of notification types, templates and category defaults |
| `service.py` | `publish()`, devices and preferences |
| `model.py`, `schema.py`, `router.py` | Tables, API shapes, HTTP and WebSocket endpoints |
| `channels/in_app.py` | Inbox queries |
| `channels/push.py` | Device fan-out and handling of per-device results |
| `channels/websockets.py` | Socket registry and Redis fan-out |
| `provider/` | `PushProvider` interface and its Firebase implementation |
| `worker/outbox_dispatcher.py` | Claim, deliver, retry and retention logic |
| `worker/notification_tasks.py`, `worker/reminder_tasks.py` | Celery tasks |
| `worker/scheduler.py` | Beat schedule, in-process loop, cron commands |

## Sending a notification

Add a `NotificationType` and its `NotificationSpec` to `events.py`, then call this
before your `db.commit()`:

```python
db.flush()  # So new rows have ids for the context.
publish(
    db,
    user_id=user.id,
    business_id=business_id,  # None for account-wide, such as security alerts.
    notification_type=NotificationType.low_balance,
    context={"card_id": card.id, "last_4": "1234", "balance": Decimal("10")},
    dedupe_key=None,  # Set one if the same event could fire twice.
)
```

`Decimal` values are formatted with the business's currency.

Default push behaviour by category:

| Category | Push by default | Can be turned off |
|---|---|---|
| `transactions` | No | Yes |
| `alerts` | Yes | Yes |
| `reminders` | Yes | Yes |
| `system` | Yes | Yes |
| `security` | Yes | No |

Everything always reaches the inbox and open sockets.

## Setup

1. Install dependencies: `pip install -r requirement.txt`
2. Apply the migration: `alembic upgrade head`
3. In the Firebase console, create a service account key and store it outside the
   repo. Set the environment variables below.
4. Choose a deployment mode.

| Variable | Purpose |
|---|---|
| `FIREBASE_CREDENTIALS_PATH` | Path to the service account JSON |
| `FIREBASE_CREDENTIALS_JSON` | The JSON itself, for secret stores. Takes priority over the path |
| `REDIS_URL` | Enables Celery and multi-process WebSockets |
| `NOTIFICATION_WEB_BASE_URL` | https dashboard URL for web push click-through |
| `NOTIFICATION_WEB_ICON_URL` | Icon shown on web push notifications |
| `NOTIFICATION_LOW_BALANCE_THRESHOLD` | Default `0`. Applies to every currency |
| `NOTIFICATION_REMINDER_HOUR_UTC` | Default `4`, which is 09:00 in Pakistan |

The remaining tuning values are in `config.py`. Without Firebase credentials, push
deliveries are recorded as `skipped` and everything else keeps working.

## Deployment modes

The database must be MySQL 8+ or MariaDB 10.6+, which `SKIP LOCKED` needs.

### With Redis (recommended)

Supports WebSockets and any number of API processes. Delay before delivery is up to
`NOTIFICATION_DISPATCH_INTERVAL_SECONDS` (default 3).

```bash
uvicorn app.main:app --workers 4
celery -A app.expense_manager.notification.worker.celery_app:celery_app worker -l info
celery -A app.expense_manager.notification.worker.celery_app:celery_app beat -l info
```

Run exactly one `beat`. On Windows, add `--pool=solo` to `worker`.

The API must run on an ASGI server such as uvicorn. WebSockets don't work through
WSGI bridges such as a2wsgi or Passenger.

### Shared hosting: no Redis, cron available

Push and the inbox work. WebSockets are unavailable, so clients poll
`GET /unread-count`. Push is delayed by up to one minute.

```cron
* * * * *   cd /path/to/app && python -m app.expense_manager.notification.worker.scheduler dispatch
0 4 * * *   cd /path/to/app && python -m app.expense_manager.notification.worker.scheduler reminders
0 4 * * 1   cd /path/to/app && python -m app.expense_manager.notification.worker.scheduler weekly-summaries
30 22 * * * cd /path/to/app && python -m app.expense_manager.notification.worker.scheduler purge
```

### Development: API process only

With no `REDIS_URL`, the API dispatches the outbox itself. WebSockets work within that
single process. Reminders and retention need the commands above.

## Client integration

All endpoints are under `/api/expenses/notifications` and use the expense manager
Bearer token.

| Method | Path | Purpose |
|---|---|---|
| GET | `` | Inbox. Query: `limit`, `cursor`, `unread_only`, `category` |
| GET | `/unread-count` | `unread_count` for the active business, `total_unread_count` for all |
| POST | `/read` | Mark read. Body: `{"ids": [...]}`, at most 100 |
| POST | `/read-all` | Mark the active business read |
| DELETE | `/{notification_id}` | Delete a notification |
| POST | `/devices` | Register a device, or refresh one |
| DELETE | `/devices/{device_id}` | Unregister a device on logout |
| GET | `/preferences` | Push settings per category |
| PUT | `/preferences/{category}` | Body: `{"push_enabled": true}`. `409` for locked categories |
| POST | `/test` | Send a test through every channel, once a minute |
| WS | `/ws` | Live events |

The inbox shows the active business plus account-wide notifications. Use `next_cursor`
to fetch the next page; it is `null` on the last page.

### Registering a device

Call `POST /devices` after login and on every app start, which keeps the device from
being retired as stale. Store the returned `id` for logout.

```json
{"token": "<identifier>", "platform": "android", "token_type": "fid"}
```

`platform` is `android`, `ios` or `web`. Set `token_type` to match the client SDK:

| Client SDK provides | `token_type` |
|---|---|
| Installation ID, from `register()` / `onRegistered()` | `fid` |
| Registration token, from `getToken()` | `fcm_token` (the default) |

Firebase has deprecated registration tokens, so new client work should use FIDs where
the SDK supports them. When an installed app moves from a token to a FID, register the
FID and call `DELETE /devices/{old_id}`, or that device receives everything twice until
the old token expires.

### Push payload

Every push has a title and body, plus these string `data` fields:

| Field | Notes |
|---|---|
| `notification_id` | Use it to ignore duplicates and to mark as read |
| `type`, `category` | See `events.py` |
| `action_url` | Optional. A path such as `/cards/12`. Web opens it; mobile maps it to a screen |
| `business_id` | Optional. Switch to this business before opening `action_url` |

On Android, create these notification channels: `expense_transactions`, `expense_alerts`,
`expense_reminders`, `expense_security`, `expense_system`.

On the web, foreground messages need `onMessage`, and background messages need a
`firebase-messaging-sw.js` service worker plus your VAPID key in `getToken()`.

### WebSocket

Authenticate with the first message. The token never goes in the URL, because URLs
are written to access logs.

```json
{"type": "auth", "token": "<access token>"}
```

| Server message | Meaning |
|---|---|
| `{"type": "connection.ready", "data": {}}` | Authenticated. Refetch the inbox to catch up |
| `{"type": "notification.created", "data": <notification>}` | Same shape as an inbox item |
| `{"type": "notification.read", "data": {"ids": [...]}}` | Read on another client |
| `{"type": "notification.read_all", "data": {"business_id": 1}}` | All read on another client |
| `{"type": "notification.deleted", "data": {"id": "..."}}` | Deleted on another client |

Events arrive for every business on the login, so check `business_id`. Sending the text
`ping` returns `pong`.

| Close code | Meaning | Client action |
|---|---|---|
| `4401` | Bad, missing or expired token | Sign in again before reconnecting |
| `4429` | Too many connections for this user | Close unused sockets |
| Anything else | Network or server restart | Reconnect with backoff |

Events aren't stored, so always treat the inbox as the source of truth. If a socket
can't be kept open, poll `/unread-count`.

## Operations

```sql
-- Should stay near zero; a growing backlog means the dispatcher isn't running.
SELECT status, channel, COUNT(*) FROM expense_notification_deliveries GROUP BY status, channel;

-- Why deliveries gave up.
SELECT channel, last_error, COUNT(*) FROM expense_notification_deliveries
WHERE status = 'dead' GROUP BY channel, last_error;
```

To re-send dead deliveries after fixing the cause, set them back to `pending`, with
`attempts = 0` and `next_attempt_at = UTC_TIMESTAMP()`.

The dispatcher stops while the emergency block file exists. It is checked on the host
running the dispatcher, so a separate worker host needs its own block file.

## Tests

```bash
pytest tests/test_expense_notifications.py
```
