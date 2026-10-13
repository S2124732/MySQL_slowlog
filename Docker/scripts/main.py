#!/usr/bin/env python3
"""
make_fake_slowlog.py - generate fake rows for the `mysql_slowlog` table.

    python -m pip install faker
    python make_fake_slowlog.py                        # 10,000 rows
    python make_fake_slowlog.py --rows 50000 --seed 7 --out-dir out

Writes two files next to the script (or into --out-dir):
    mysql_slowlog_fake.sql   INSERT statements, 500 rows per statement
    mysql_slowlog_fake.csv   the same rows with a header line

`id` is left out on purpose so AUTO_INCREMENT numbers the rows. Rows are written in
start_time order, so ids climb with time like in a real log. The same --seed gives
the same queries; start_time is anchored to the moment you run the script.
"""
import argparse
import csv
import math
import random
import string
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, NamedTuple

from faker import Faker

# =============================================================================
# Settings you might want to change
# =============================================================================
NUM_ROWS = 10_000
SEED = 42
DAYS_BACK = 30          # start_time is spread over the last 30 days
INSERT_BATCH = 500      # rows per INSERT statement in the .sql file

MIN_QUERY_TIME = 0.50   # hard floor: nothing faster than this ever appears
MAX_QUERY_TIME = 45.00  # hard ceiling: longer draws are thrown away and re-drawn
# query_time = MIN_QUERY_TIME + LogNormal(mu, sigma). The median lands around 1.5 s,
# a couple of percent of queries run past 10 s, and a few stretch out to 30-45 s.
TAIL_MU, TAIL_SIGMA = 0.0, 1.1

# Relative query volume per hour of day (index 0 = midnight): busy 9-17, quiet
# overnight, small bump around 02:00 for batch jobs.
HOUR_WEIGHTS = [3, 6, 9, 3, 2, 2, 3, 5, 8, 12, 14, 14, 12, 12, 13, 14, 13, 11, 8, 6, 5, 4, 4, 3]

rng = random.Random()          # all structural randomness (seeded in generate())
fake = Faker("en_US")          # all fake-looking values: emails, names, cities, ...


# =============================================================================
# Small helpers
# =============================================================================
def q(text):
    """Quote text as a MySQL string literal (backslashes and single quotes escaped)."""
    return "'" + str(text).replace("\\", "\\\\").replace("'", "''") + "'"


def fmt_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def fmt_day(dt):
    return dt.strftime("%Y-%m-%d")


# =============================================================================
# Fake schema catalog
#
# Every query is built from these tables, so a template can never pair a table
# with a column that doesn't belong to it (no "SELECT carrier FROM customers").
# Column "kinds" tell the templates what each column is good for:
#   id/fk      primary key / foreign key        ts        datetime column
#   money      decimal amount                   qty       small integer
#   status     lifecycle state (choices are in forward order, e.g. pending -> paid)
#   category   fixed set of labels (used for GROUP BY / filters)
#   email, first_name, last_name, city, company, code, word, path    searchable text
# =============================================================================
TEXT_KINDS = ("email", "first_name", "last_name", "city", "company", "code", "word", "path")


@dataclass(frozen=True)
class Col:
    name: str
    kind: str
    choices: tuple = ()   # allowed values for status/category; a bothify pattern for 'code'


def C(name, kind, *choices):
    return Col(name, kind, choices)


@dataclass(frozen=True)
class Table:
    name: str
    cols: tuple
    purge: bool = False   # log-style table that archive jobs DELETE from

    def of(self, *kinds):
        """All columns whose kind is one of `kinds`."""
        return [c for c in self.cols if c.kind in kinds]

    def col(self, name):
        return next(c for c in self.cols if c.name == name)

    @property
    def pk(self):
        return self.of("id")[0]


class Rel(NamedTuple):
    child: Table
    fk: Col               # foreign-key column on the child
    parent: Table         # fk joins to parent.pk


@dataclass(frozen=True)
class Database:
    name: str
    weight: int           # share of the slow queries that hit this database
    users: tuple          # (main application account, secondary reporting account)
    tables: tuple
    rels: tuple

    def parents_of(self, table):
        return [r for r in self.rels if r.child is table]


def make_db(name, weight, users, tables, fks):
    by_name = {t.name: t for t in tables}
    rels = tuple(Rel(by_name[c], by_name[c].col(fk), by_name[p]) for c, fk, p in fks)
    return Database(name, weight, users, tuple(tables), rels)


# --- 1. Online store ---------------------------------------------------------
SHOP_PROD = make_db(
    "shop_prod", 55, ("app_user", "report_user"),
    tables=[
        Table("customers", (
            C("customer_id", "id"), C("email", "email"),
            C("first_name", "first_name"), C("last_name", "last_name"), C("city", "city"),
            C("loyalty_points", "qty"), C("created_at", "ts"), C("last_login_at", "ts"),
            C("account_status", "status", "active", "inactive", "suspended", "closed"),
        )),
        Table("products", (
            C("product_id", "id"), C("sku", "code", "???-#####"), C("product_name", "word"),
            C("category", "category", "electronics", "home_garden", "apparel", "toys",
              "sports", "beauty", "books", "grocery"),
            C("unit_price", "money"), C("stock_qty", "qty"), C("created_at", "ts"),
            C("status", "status", "draft", "active", "backordered", "discontinued"),
        )),
        Table("orders", (
            C("order_id", "id"), C("customer_id", "fk"),
            C("order_status", "status", "pending", "paid", "shipped", "delivered",
              "cancelled", "refunded"),
            C("total_amount", "money"), C("shipping_city", "city"),
            C("coupon_code", "code", "SAVE##??"),
            C("created_at", "ts"), C("updated_at", "ts"),
        )),
        Table("order_items", (
            C("order_item_id", "id"), C("order_id", "fk"), C("product_id", "fk"),
            C("quantity", "qty"), C("unit_price", "money"), C("created_at", "ts"),
        )),
        Table("login_attempts", (
            C("attempt_id", "id"), C("customer_id", "fk"),
            C("result", "category", "success", "failed", "locked"),
            C("attempted_at", "ts"),
        ), purge=True),
    ],
    fks=[("orders", "customer_id", "customers"),
         ("order_items", "order_id", "orders"),
         ("order_items", "product_id", "products"),
         ("login_attempts", "customer_id", "customers")],
)

# --- 2. SaaS subscription billing -------------------------------------------
BILLING_PROD = make_db(
    "billing_prod", 20, ("billing_svc", "report_user"),
    tables=[
        Table("accounts", (
            C("account_id", "id"), C("company_name", "company"), C("billing_email", "email"),
            C("plan_tier", "category", "starter", "growth", "business", "enterprise"),
            C("created_at", "ts"),
            C("status", "status", "trialing", "active", "past_due", "suspended", "closed"),
        )),
        Table("plans", (
            C("plan_id", "id"), C("plan_name", "word"),
            C("billing_interval", "category", "monthly", "quarterly", "annual"),
            C("list_price", "money"),
        )),
        Table("subscriptions", (
            C("subscription_id", "id"), C("account_id", "fk"), C("plan_id", "fk"),
            C("monthly_price", "money"), C("seats", "qty"),
            C("started_at", "ts"), C("renews_at", "ts"),
            C("status", "status", "trialing", "active", "past_due", "canceled"),
        )),
        Table("invoices", (
            C("invoice_id", "id"), C("account_id", "fk"),
            C("invoice_number", "code", "INV-2026-######"),
            C("amount_due", "money"), C("issued_at", "ts"), C("due_at", "ts"),
            C("status", "status", "draft", "open", "paid", "void", "uncollectible"),
        )),
        Table("webhook_events", (
            C("event_id", "id"), C("account_id", "fk"),
            C("event_type", "category", "invoice.paid", "invoice.payment_failed",
              "customer.subscription.updated", "customer.subscription.deleted",
              "charge.refunded"),
            C("received_at", "ts"),
        ), purge=True),
    ],
    fks=[("subscriptions", "account_id", "accounts"),
         ("subscriptions", "plan_id", "plans"),
         ("invoices", "account_id", "accounts"),
         ("webhook_events", "account_id", "accounts")],
)

# --- 3. Marketing / web analytics warehouse ---------------------------------
ANALYTICS_DW = make_db(
    "analytics_dw", 25, ("etl_user", "bi_reader"),
    tables=[
        Table("campaigns", (
            C("campaign_id", "id"), C("campaign_name", "word"),
            C("channel", "category", "email", "paid_search", "social", "display",
              "affiliate", "organic"),
            C("started_at", "ts"),
            C("status", "status", "draft", "running", "paused", "ended"),
        )),
        Table("sessions", (
            C("session_id", "id"), C("campaign_id", "fk"),
            C("device_type", "category", "desktop", "mobile", "tablet"),
            C("country_code", "category", "US", "GB", "DE", "CA", "AU", "FR", "BR", "IN"),
            C("duration_sec", "qty"), C("started_at", "ts"),
        )),
        Table("page_views", (
            C("view_id", "id"), C("session_id", "fk"), C("page_path", "path"),
            C("load_ms", "qty"), C("viewed_at", "ts"),
        ), purge=True),
        Table("conversions", (
            C("conversion_id", "id"), C("session_id", "fk"), C("campaign_id", "fk"),
            C("revenue", "money"), C("converted_at", "ts"),
            C("status", "status", "pending", "attributed", "rejected"),
        )),
    ],
    fks=[("sessions", "campaign_id", "campaigns"),
         ("page_views", "session_id", "sessions"),
         ("conversions", "session_id", "sessions"),
         ("conversions", "campaign_id", "campaigns")],
)

DATABASES = [SHOP_PROD, BILLING_PROD, ANALYTICS_DW]


# =============================================================================
# Building blocks the query templates share
# =============================================================================
@dataclass
class Ctx:
    start: datetime       # when this query ran; dates inside the SQL are chosen relative to it
    examined: int         # roughly how many rows the query should look like it scanned


def literal(col, ctx):
    """A realistic SQL literal for a column of this kind (quoted when it needs to be)."""
    k = col.kind
    if k in ("id", "fk"):
        return str(rng.randint(1_000, 4_800_000))
    if k == "money":
        return f"{rng.uniform(5, 2500):.2f}"
    if k == "qty":
        return str(rng.randint(1, 40))
    if k == "ts":
        return q(fmt_dt(ctx.start - timedelta(days=rng.randint(1, 90), seconds=rng.randint(0, 86_399))))
    if k in ("status", "category"):
        return q(rng.choice(col.choices))
    if k == "email":
        return q(fake.email())
    if k == "first_name":
        return q(fake.first_name())
    if k == "last_name":
        return q(fake.last_name())
    if k == "city":
        return q(fake.city())
    if k == "company":
        return q(fake.company())
    if k == "path":
        return q("/" + fake.uri_path())
    if k == "code":
        pattern = col.choices[0] if col.choices else "??-####"
        return q(fake.bothify(pattern, letters=string.ascii_uppercase))
    return q(fake.word())


def search_term(col):
    """A fragment someone might type into a search box for this column."""
    k = col.kind
    if k == "email":
        return fake.last_name().lower()
    if k == "first_name":
        return fake.first_name()[:4]
    if k == "last_name":
        return fake.last_name()[:5]
    if k == "city":
        return fake.city()[:6]
    if k == "company":
        return fake.last_name()
    if k == "code":
        return fake.numerify("###")
    if k == "path":
        return rng.choice(["checkout", "cart", "product", "search", "account", "blog", "sale"])
    return fake.word()


def window_start(ctx, days):
    """Cutoff `days` before the query ran, written as a date literal or as NOW() - INTERVAL."""
    if rng.random() < 0.5:
        return f"NOW() - INTERVAL {days} DAY"
    cutoff = (ctx.start - timedelta(days=days)).replace(hour=0, minute=0, second=0)
    return q(fmt_dt(cutoff))


def date_window(ctx, days):
    """(first, last) strings covering the `days` days before the query ran."""
    first = fmt_day(ctx.start - timedelta(days=days))
    last = fmt_day(ctx.start - timedelta(days=1)) + " 23:59:59"
    return first, last


def condition(ref, col, ctx):
    """One plausible WHERE predicate for `ref` (the column, possibly alias-qualified)."""
    k = col.kind
    if k in ("status", "category"):
        if rng.random() < 0.6:
            return f"{ref} = {q(rng.choice(col.choices))}"
        picks = rng.sample(col.choices, k=min(len(col.choices), rng.randint(2, 3)))
        return f"{ref} IN ({', '.join(q(v) for v in picks)})"
    if k == "ts":
        return f"{ref} >= {window_start(ctx, rng.choice([7, 14, 30, 60, 90]))}"
    if k == "money":
        return f"{ref} > {rng.choice([10, 25, 50, 100, 250, 500, 1000])}"
    if k == "qty":
        return f"{ref} >= {rng.randint(2, 20)}"
    return f"{ref} = {literal(col, ctx)}"


def select_list(table):
    """`*` about a third of the time, otherwise the PK plus a few other columns of the table."""
    if rng.random() < 0.3:
        return "*"
    others = [c for c in table.cols if c is not table.pk]
    picked = sorted(rng.sample(others, k=rng.randint(2, min(4, len(others)))), key=table.cols.index)
    return ", ".join(c.name for c in [table.pk] + picked)


def aliases(*tables):
    """Table aliases for multi-table queries. Hand-written SQL uses short aliases (o, oi, c);
    ORM-generated SQL repeats the full table name. Both show up in real slow logs."""
    if rng.random() < 0.4:
        return [t.name for t in tables]
    used, result = set(), []
    for t in tables:
        initials = "".join(part[0] for part in t.name.split("_"))
        alias = next(a for a in (initials, t.name[:3], t.name) if a not in used)
        used.add(alias)
        result.append(alias)
    return result


def from_item(table, alias):
    return table.name if alias == table.name else f"{table.name} {alias}"


def pick_table(db, wanted):
    return rng.choice([t for t in db.tables if wanted(t)])


def pick_rel(db, wanted):
    return rng.choice([r for r in db.rels if wanted(r)])


# =============================================================================
# The 10 query templates. Each returns (sql_text, rows_sent).
# =============================================================================
def like_search(db, ctx):
    """Leading-wildcard LIKE on a text column. No index can help, so MySQL scans the table."""
    t = pick_table(db, lambda t: t.of(*TEXT_KINDS) and t.of("ts"))
    col, ts = rng.choice(t.of(*TEXT_KINDS)), rng.choice(t.of("ts"))
    limit = rng.choice([20, 25, 50, 100])
    where = f"{col.name} LIKE {q('%' + search_term(col) + '%')}"
    if t.of("status") and rng.random() < 0.5:
        st = rng.choice(t.of("status"))
        where += " AND " + condition(st.name, st, ctx)
    sql = f"SELECT {select_list(t)} FROM {t.name} WHERE {where} ORDER BY {ts.name} DESC LIMIT {limit}"
    return sql, (limit if rng.random() < 0.7 else rng.randint(0, limit))


def deep_pagination(db, ctx):
    """Admin-style list page far down a long result set: every skipped row is still read."""
    t = pick_table(db, lambda t: t.of("status") and t.of("ts"))
    st, ts = rng.choice(t.of("status")), rng.choice(t.of("ts"))
    limit = rng.choice([10, 20, 25, 50, 100])
    offset = max(0, round((ctx.examined - limit) / 50) * 50)
    page = f"LIMIT {limit} OFFSET {offset}" if rng.random() < 0.5 else f"LIMIT {offset}, {limit}"
    sql = (f"SELECT {select_list(t)} FROM {t.name} WHERE {condition(st.name, st, ctx)} "
           f"ORDER BY {ts.name} DESC {page}")
    return sql, limit


def daily_rollup(db, ctx):
    """Per-day totals. DATE() around the column in GROUP BY forces a full pass over the range."""
    t = pick_table(db, lambda t: t.of("money") and t.of("ts") and t.of("fk"))
    money, ts = rng.choice(t.of("money")), rng.choice(t.of("ts"))
    days = rng.choice([7, 14, 30, 60, 90])
    first, last = date_window(ctx, days)
    extra = ""
    if t.of("status", "category") and rng.random() < 0.5:
        s = rng.choice(t.of("status", "category"))
        extra = " AND " + condition(s.name, s, ctx)
    sql = (f"SELECT DATE({ts.name}) AS day, COUNT(*) AS cnt, SUM({money.name}) AS total, "
           f"ROUND(AVG({money.name}), 2) AS avg_value FROM {t.name} "
           f"WHERE {ts.name} BETWEEN {q(first)} AND {q(last)}{extra} "
           f"GROUP BY DATE({ts.name}) ORDER BY day")
    return sql, days - rng.randint(0, 2)


def join_filter_sort(db, ctx):
    """Child JOIN parent, filtered on the child and sorted on a column without an index."""
    r = pick_rel(db, lambda r: r.child.of("ts") and r.child.of("status", "money")
                 and r.parent.of(*TEXT_KINDS))
    c, p = r.child, r.parent
    ac, ap = aliases(c, p)
    ts, filt = rng.choice(c.of("ts")), rng.choice(c.of("status", "money"))
    sort = rng.choice(c.of("money", "ts"))
    shown = c.of("status", "money", "ts")
    child_cols = [c.pk] + rng.sample(shown, k=min(2, len(shown)))
    text_cols = p.of(*TEXT_KINDS)
    parent_cols = rng.sample(text_cols, k=min(2, len(text_cols)))
    select = ", ".join([f"{ac}.{x.name}" for x in child_cols] + [f"{ap}.{x.name}" for x in parent_cols])
    where = " AND ".join([condition(f"{ac}.{filt.name}", filt, ctx),
                          condition(f"{ac}.{ts.name}", ts, ctx)])
    join = rng.choice(["JOIN", "INNER JOIN", "LEFT JOIN"])
    limit = rng.choice([25, 50, 100, 200])
    sql = (f"SELECT {select} FROM {from_item(c, ac)} {join} {from_item(p, ap)} "
           f"ON {ap}.{p.pk.name} = {ac}.{r.fk.name} "
           f"WHERE {where} ORDER BY {ac}.{sort.name} DESC LIMIT {limit}")
    return sql, (limit if rng.random() < 0.7 else rng.randint(1, limit))


def three_table_report(db, ctx):
    """Fact table joined to two lookup tables, then grouped, HAVING-filtered and ranked."""
    c = rng.choice([t for t in db.tables if t.of("ts") and len(db.parents_of(t)) >= 2])
    r1, r2 = rng.sample(db.parents_of(c), 2)
    ac, a1, a2 = aliases(c, r1.parent, r2.parent)
    group = rng.choice(r1.parent.of("category", "status"))
    side = rng.choice(r2.parent.of("category", "status"))
    side_filter = condition(f"{a2}.{side.name}", side, ctx)
    ts = rng.choice(c.of("ts"))
    measures = c.of("money", "qty")
    total = f", SUM({ac}.{rng.choice(measures).name}) AS total" if measures else ""
    order_by = "total" if measures else "num_rows"
    first, last = date_window(ctx, rng.choice([7, 30, 90]))
    limit = rng.choice([10, 20, 25, 50])
    sql = (f"SELECT {a1}.{group.name}, COUNT(*) AS num_rows{total} "
           f"FROM {from_item(c, ac)} "
           f"JOIN {from_item(r1.parent, a1)} ON {a1}.{r1.parent.pk.name} = {ac}.{r1.fk.name} "
           f"JOIN {from_item(r2.parent, a2)} ON {a2}.{r2.parent.pk.name} = {ac}.{r2.fk.name} "
           f"WHERE {ac}.{ts.name} BETWEEN {q(first)} AND {q(last)} AND {side_filter} "
           f"GROUP BY {a1}.{group.name} HAVING COUNT(*) > {rng.choice([5, 10, 50, 100])} "
           f"ORDER BY {order_by} DESC LIMIT {limit}")
    groups = len(group.choices)
    return sql, min(limit, rng.randint(max(1, groups - 2), groups))


def in_subquery(db, ctx):
    """Parent rows picked with IN (SELECT ...), which MySQL often handles poorly."""
    r = pick_rel(db, lambda r: r.child.of("status") and r.child.of("ts"))
    c, p = r.child, r.parent
    st, ts = rng.choice(c.of("status")), rng.choice(c.of("ts"))
    order = rng.choice(p.of("ts") or [p.pk])
    limit = rng.choice([50, 100, 500, 1000])
    sql = (f"SELECT {select_list(p)} FROM {p.name} WHERE {p.pk.name} IN "
           f"(SELECT {r.fk.name} FROM {c.name} "
           f"WHERE {condition(st.name, st, ctx)} AND {condition(ts.name, ts, ctx)}) "
           f"ORDER BY {order.name} DESC LIMIT {limit}")
    return sql, (limit if rng.random() < 0.6 else rng.randint(1, limit))


def anti_join(db, ctx):
    """'Rows that have no matching child rows', written as LEFT JOIN ... IS NULL."""
    r = pick_rel(db, lambda r: r.parent.of("ts"))
    c, p = r.child, r.parent
    ac, ap = aliases(c, p)
    ts = rng.choice(p.of("ts"))
    shown = p.of(*TEXT_KINDS, "ts")
    cols = [p.pk] + rng.sample(shown, k=min(2, len(shown)))
    select = ", ".join(f"{ap}.{x.name}" for x in cols)
    sql = (f"SELECT {select} FROM {from_item(p, ap)} "
           f"LEFT JOIN {from_item(c, ac)} ON {ac}.{r.fk.name} = {ap}.{p.pk.name} "
           f"WHERE {ac}.{c.pk.name} IS NULL "
           f"AND {ap}.{ts.name} < {window_start(ctx, rng.choice([30, 90, 180, 365]))}")
    return sql, rng.randint(50, 25_000)


def distinct_count(db, ctx):
    """COUNT(DISTINCT fk) with the date column wrapped in DATE(), so a date index is useless."""
    r = pick_rel(db, lambda r: r.child.of("ts"))
    c = r.child
    ts = rng.choice(c.of("ts"))
    day = fmt_day(ctx.start - timedelta(days=rng.choice([1, 3, 7, 14, 30, 90])))
    where = f"DATE({ts.name}) >= {q(day)}"
    if c.of("status", "category") and rng.random() < 0.5:
        s = rng.choice(c.of("status", "category"))
        where += " AND " + condition(s.name, s, ctx)
    sql = f"SELECT COUNT(DISTINCT {r.fk.name}) AS unique_{r.fk.name} FROM {c.name} WHERE {where}"
    return sql, 1


def bulk_update(db, ctx):
    """Status sweep over old rows. Takes row locks as it goes, so it can queue behind writers."""
    t = pick_table(db, lambda t: t.of("status") and t.of("ts"))
    st, ts = rng.choice(t.of("status")), rng.choice(t.of("ts"))
    i = rng.randrange(len(st.choices) - 1)
    old, new = st.choices[i], rng.choice(st.choices[i + 1:])     # only moves forward in the lifecycle
    sql = (f"UPDATE {t.name} SET {st.name} = {q(new)} "
           f"WHERE {st.name} = {q(old)} "
           f"AND {ts.name} < {window_start(ctx, rng.choice([14, 30, 60, 90, 180]))}")
    if rng.random() < 0.4:
        sql += f" LIMIT {rng.choice([1000, 5000, 10000])}"
    return sql, 0


def archive_delete(db, ctx):
    """Nightly purge of old log rows, in batches."""
    t = pick_table(db, lambda t: t.purge and t.of("ts"))
    ts = rng.choice(t.of("ts"))
    where = f"{ts.name} < {window_start(ctx, rng.choice([30, 60, 90, 180, 365]))}"
    if t.of("category") and rng.random() < 0.5:
        s = rng.choice(t.of("category"))
        where += " AND " + condition(s.name, s, ctx)
    return f"DELETE FROM {t.name} WHERE {where} LIMIT {rng.choice([1000, 5000, 10000])}", 0


class Template(NamedTuple):
    name: str
    weight: int           # relative frequency in the log
    build: Callable
    lock_prone: bool = False   # True for statements that wait on other transactions' locks


TEMPLATES = [
    Template("like_search",        12, like_search),
    Template("deep_pagination",    14, deep_pagination),
    Template("daily_rollup",       14, daily_rollup),
    Template("join_filter_sort",   16, join_filter_sort),
    Template("three_table_report", 10, three_table_report),
    Template("in_subquery",         9, in_subquery),
    Template("anti_join",           5, anti_join),
    Template("distinct_count",      8, distinct_count),
    Template("bulk_update",         7, bulk_update, lock_prone=True),
    Template("archive_delete",      5, archive_delete, lock_prone=True),
]


# =============================================================================
# Row generation
# =============================================================================
class Row(NamedTuple):
    start_time: datetime
    query_time_seconds: float
    lock_time_seconds: float
    rows_sent: int
    rows_examined: int
    db_name: str
    user_host: str
    sql_text: str
    template: str          # only used for the summary, not written to the files


def draw_query_time():
    """0.5 s floor plus a log-normal tail; anything above MAX_QUERY_TIME is re-drawn."""
    while True:
        t = MIN_QUERY_TIME + rng.lognormvariate(TAIL_MU, TAIL_SIGMA)
        if t <= MAX_QUERY_TIME:
            return round(t, 2)


def draw_lock_time(query_time, lock_prone):
    """Lock_time is usually near zero; writes sometimes sit waiting on another transaction."""
    if rng.random() < (0.30 if lock_prone else 0.01):
        lock = rng.uniform(0.2, 0.9) * query_time
    elif rng.random() < 0.15:
        lock = rng.uniform(0.05, 0.5)
    else:
        lock = rng.uniform(0.0, 0.05)
    return round(min(lock, query_time), 2)


def draw_start_time(now):
    day = now - timedelta(days=rng.randint(1, DAYS_BACK))
    hour = rng.choices(range(24), weights=HOUR_WEIGHTS)[0]
    return day.replace(hour=hour, minute=rng.randint(0, 59), second=rng.randint(0, 59), microsecond=0)


def make_row(now, app_ips):
    db = rng.choices(DATABASES, weights=[d.weight for d in DATABASES])[0]
    tpl = rng.choices(TEMPLATES, weights=[t.weight for t in TEMPLATES])[0]
    start = draw_start_time(now)
    query_time = draw_query_time()
    lock_time = draw_lock_time(query_time, tpl.lock_prone)
    # Rows examined grows with the time spent working (not waiting on locks).
    rows_per_second = rng.lognormvariate(math.log(60_000), 0.7)
    examined = int(max(query_time - lock_time, 0.05) * rows_per_second)
    sql, sent = tpl.build(db, Ctx(start, examined))
    user = rng.choices(db.users, weights=[85, 15])[0]
    user_host = f"{user}[{user}] @ {rng.choice(app_ips)}"
    return Row(start, query_time, lock_time, sent, max(examined, sent),
               db.name, user_host, sql, tpl.name)


def generate(n, seed):
    rng.seed(seed)
    Faker.seed(seed)
    now = datetime.now().replace(microsecond=0)
    app_ips = [f"10.0.{rng.randint(1, 40)}.{rng.randint(2, 250)}" for _ in range(12)]
    rows = [make_row(now, app_ips) for _ in range(n)]
    rows.sort(key=lambda r: r.start_time)
    return rows


def check(rows):
    assert all(r.query_time_seconds >= MIN_QUERY_TIME for r in rows), "query_time below floor"
    assert all(r.query_time_seconds <= MAX_QUERY_TIME for r in rows), "query_time above ceiling"
    assert all(r.lock_time_seconds <= r.query_time_seconds for r in rows), "lock_time > query_time"
    assert all(r.rows_examined >= r.rows_sent for r in rows), "rows_examined < rows_sent"


# =============================================================================
# Output
# =============================================================================
COLUMNS = ["start_time", "query_time_seconds", "lock_time_seconds", "rows_sent",
           "rows_examined", "db_name", "user_host", "sql_text"]


def as_fields(r):
    return [fmt_dt(r.start_time), f"{r.query_time_seconds:.2f}", f"{r.lock_time_seconds:.2f}",
            r.rows_sent, r.rows_examined, r.db_name, r.user_host, r.sql_text]


def write_csv(rows, path):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        writer.writerows(as_fields(r) for r in rows)


def write_sql(rows, path):
    head = f"INSERT INTO mysql_slowlog ({', '.join(COLUMNS)}) VALUES\n"
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("START TRANSACTION;\n")
        for i in range(0, len(rows), INSERT_BATCH):
            tuples = []
            for r in rows[i:i + INSERT_BATCH]:
                v = as_fields(r)
                tuples.append(f"({q(v[0])}, {v[1]}, {v[2]}, {v[3]}, {v[4]}, "
                              f"{q(v[5])}, {q(v[6])}, {q(v[7])})")
            f.write(head + ",\n".join(tuples) + ";\n")
        f.write("COMMIT;\n")


def summarize(rows):
    times = sorted(r.query_time_seconds for r in rows)

    def pct(p):
        return times[min(len(times) - 1, int(p * len(times)))]

    print(f"query_time_seconds: min {times[0]:.2f} | median {pct(0.5):.2f} | "
          f"p95 {pct(0.95):.2f} | p99 {pct(0.99):.2f} | max {times[-1]:.2f}")
    print(f"rows over 10 s: {sum(t > 10 for t in times)} | over 30 s: {sum(t > 30 for t in times)}")
    print("rows per database:", dict(Counter(r.db_name for r in rows)))
    print("rows per template:", dict(Counter(r.template for r in rows)))


def main():
    parser = argparse.ArgumentParser(description="Generate fake rows for the mysql_slowlog table.")
    parser.add_argument("--rows", type=int, default=NUM_ROWS)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()

    rows = generate(args.rows, args.seed)
    check(rows)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_sql(rows, args.out_dir / "mysql_slowlog_fake.sql")
    write_csv(rows, args.out_dir / "mysql_slowlog_fake.csv")
    print(f"Wrote {len(rows):,} rows to {args.out_dir}")
    summarize(rows)


if __name__ == "__main__":
    main()