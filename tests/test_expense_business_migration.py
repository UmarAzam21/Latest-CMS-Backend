import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

MIGRATION_PATH = Path(__file__).parent.parent / "alembic" / "versions" / (
    "a8d7c6e5f4b3_add_expense_manager_businesses.py"
)

# The shape of the live database just before this migration: the businesses table
# already made by app startup, and the expense tables not yet linked to it.
PARTIAL_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY);
CREATE TABLE expense_manager_auth (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    business_name VARCHAR(255) NOT NULL
);
CREATE TABLE expense_manager_businesses (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    name VARCHAR(255) NOT NULL,
    business_type VARCHAR(50) NOT NULL DEFAULT 'personal',
    currency VARCHAR(3) NOT NULL DEFAULT 'PKR',
    created_at DATETIME NOT NULL,
    CONSTRAINT uq_expense_business_account_name UNIQUE (account_id, name)
);
CREATE INDEX ix_expense_manager_businesses_account_id ON expense_manager_businesses (account_id);
CREATE INDEX ix_expense_manager_businesses_id ON expense_manager_businesses (id);
CREATE TABLE expense_accounts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    account_number VARCHAR(4) NOT NULL,
    CONSTRAINT uq_expense_account_user_number UNIQUE (user_id, account_number)
);
CREATE TABLE expense_category_options (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name VARCHAR(80) NOT NULL,
    CONSTRAINT uq_expense_category_user_name UNIQUE (user_id, name)
);
CREATE TABLE income_expense (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    account_id INTEGER
);
"""

SEED = """
INSERT INTO users (id) VALUES (10), (20), (30);
INSERT INTO expense_manager_auth (id, user_id, business_name) VALUES (1, 10, 'Solo'), (2, 20, 'Two');
INSERT INTO expense_manager_businesses (id, account_id, name, created_at)
    VALUES (5, 2, 'First', '2026-01-01'), (6, 2, 'Second', '2026-01-01');
INSERT INTO expense_accounts (id, user_id, account_number)
    VALUES (1, 10, '1111'), (2, 20, '2222'), (3, 30, '3333');
INSERT INTO expense_category_options (id, user_id, name) VALUES (1, 20, 'Food'), (2, 30, 'Food');
INSERT INTO income_expense (id, user_id, account_id) VALUES (1, 20, 2), (2, 30, 3), (3, NULL, 1);
"""


@pytest.fixture
def migrated():
    spec = importlib.util.spec_from_file_location("business_migration", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite://", poolclass=StaticPool)
    with engine.begin() as connection:
        for statement in (PARTIAL_SCHEMA + SEED).split(";"):
            if statement.strip():
                connection.execute(text(statement))

        operations = Operations(MigrationContext.configure(connection))
        operations.get_bind = lambda: connection
        migration.op = operations
        migration.upgrade()
        yield migration, connection
    engine.dispose()


def rows(connection, query):
    return connection.execute(text(query)).fetchall()


def test_only_accounts_without_a_business_get_one(migrated):
    _, connection = migrated

    assert rows(
        connection,
        "SELECT account_id, name FROM expense_manager_businesses ORDER BY account_id, id",
    ) == [(1, "Solo"), (2, "First"), (2, "Second")]


def test_data_moves_to_the_accounts_first_business(migrated):
    _, connection = migrated
    solo = connection.execute(
        text("SELECT id FROM expense_manager_businesses WHERE account_id = 1")
    ).scalar()

    assert rows(connection, "SELECT id, business_id FROM expense_accounts ORDER BY id") == [
        (1, solo),
        (2, 5),
        (3, None),
    ]
    assert rows(connection, "SELECT id, business_id FROM expense_category_options ORDER BY id") == [
        (1, 5),
        (2, None),
    ]


def test_income_without_a_user_falls_back_to_its_card(migrated):
    _, connection = migrated
    solo = connection.execute(
        text("SELECT id FROM expense_manager_businesses WHERE account_id = 1")
    ).scalar()

    assert rows(connection, "SELECT id, business_id FROM income_expense ORDER BY id") == [
        (1, 5),
        (2, None),
        (3, solo),
    ]


def test_user_id_keeps_an_index_when_the_old_unique_goes(migrated):
    _, connection = migrated
    inspector = inspect(connection)

    for table, old, new in (
        ("expense_accounts", "uq_expense_account_user_number", "uq_expense_account_business_number"),
        (
            "expense_category_options",
            "uq_expense_category_user_name",
            "uq_expense_category_business_name",
        ),
    ):
        indexes = {item["name"]: item["column_names"] for item in inspector.get_indexes(table)}
        uniques = {item["name"] for item in inspector.get_unique_constraints(table)}
        assert indexes[f"ix_{table}_user_id"] == ["user_id"]
        assert old not in uniques
        assert new in uniques


def test_running_again_changes_nothing(migrated):
    migration, connection = migrated
    tables = (
        "expense_manager_businesses",
        "expense_accounts",
        "expense_category_options",
        "income_expense",
    )
    before = {table: rows(connection, f"SELECT * FROM {table}") for table in tables}

    migration.upgrade()

    assert {table: rows(connection, f"SELECT * FROM {table}") for table in tables} == before


def test_a_run_that_stopped_halfway_can_be_finished(migrated):
    migration, connection = migrated
    # As if the last run died before the category table's final step.
    connection.execute(text("DROP INDEX ix_expense_category_options_user_id"))
    connection.execute(text("UPDATE expense_category_options SET business_id = NULL"))

    migration.upgrade()

    assert rows(connection, "SELECT id, business_id FROM expense_category_options ORDER BY id") == [
        (1, 5),
        (2, None),
    ]


def test_user_index_is_created_before_the_old_unique_is_dropped():
    # SQLite allows either order and MariaDB doesn't, so check the order directly.
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    body = source[source.index("def _replace_user_unique"):source.index("def _assign_first_business")]

    assert body.index("op.create_index") < body.index("batch.drop_constraint")
