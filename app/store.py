"""SQLite storage and explicitly synthetic demo catalog."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from app.pricing import utc_now

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS stores(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, city TEXT NOT NULL,
  district TEXT NOT NULL, address TEXT NOT NULL,
  is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id INTEGER NOT NULL REFERENCES stores(id),
  name TEXT NOT NULL, details TEXT NOT NULL, usage_terms TEXT NOT NULL,
  UNIQUE(store_id, name, details, usage_terms)
);
CREATE TABLE IF NOT EXISTS quotes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES products(id),
  platform TEXT NOT NULL CHECK(platform IN ('meituan','douyin','kuaishou','dianping')),
  list_price_cents INTEGER NOT NULL CHECK(list_price_cents >= 0),
  discount_cents INTEGER NOT NULL CHECK(discount_cents >= 0 AND discount_cents <= list_price_cents),
  fee_cents INTEGER NOT NULL CHECK(fee_cents >= 0),
  source_type TEXT NOT NULL CHECK(source_type IN ('demo','manual','authorized_api')),
  source_url TEXT,
  note TEXT NOT NULL DEFAULT '',
  captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stores_city ON stores(city);
CREATE INDEX IF NOT EXISTS idx_products_store ON products(store_id);
CREATE INDEX IF NOT EXISTS idx_quotes_product ON quotes(product_id);

CREATE TABLE IF NOT EXISTS partner_quotes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES products(id),
  partner_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  platform TEXT NOT NULL CHECK(platform IN ('meituan','douyin','kuaishou','dianping')),
  list_price_cents INTEGER NOT NULL CHECK(list_price_cents >= 0),
  discount_cents INTEGER NOT NULL CHECK(discount_cents BETWEEN 0 AND list_price_cents),
  fee_cents INTEGER NOT NULL CHECK(fee_cents >= 0),
  availability TEXT NOT NULL CHECK(availability IN ('available','sold_out','offline','unknown')),
  eligibility TEXT NOT NULL CHECK(eligibility IN ('public','coupon_required','member_only','unknown')),
  source_url TEXT NOT NULL,
  note TEXT NOT NULL,
  quoted_at TEXT NOT NULL,
  valid_until TEXT NOT NULL,
  received_at TEXT NOT NULL,
  UNIQUE(partner_id,event_id)
);
CREATE INDEX IF NOT EXISTS idx_partner_quotes_product ON partner_quotes(product_id,platform);

"""

# Illustrative and fictitious stores, products, and prices; never real market quotations.
DEMO = [
    ("示例 · 星桥咖啡（徐家汇）", "上海", "徐汇区", "上海市徐汇区 · 演示地址", [
        ("招牌拿铁 · 中杯 1 杯", "中杯拿铁×1，不含配送", "全时段到店自取；不与其他活动叠加", [
            ("meituan", 1990, 300, 0, "示例：领取满减券"),
            ("douyin", 1880, 200, 0, "示例：需要领券"),
            ("kuaishou", 1890, 0, 0, "示例：无需领券"),
        ]),
        ("双人下午茶套餐", "拿铁×2 + 甜点×1", "周一至周五到店；节假日不可用", [
            ("meituan", 6880, 600, 0, "示例：仅工作日"),
            ("douyin", 6490, 100, 0, "示例：仅工作日"),
        ]),
    ]),
    ("示例 · 江南小馆（人民广场）", "上海", "黄浦区", "上海市黄浦区 · 演示地址", [
        ("招牌双人餐", "主菜×2 + 小食×2 + 饮品×2", "周一至周日可用；需预约", [
            ("meituan", 12800, 1000, 0, "示例：需提前预约"),
            ("douyin", 11900, 500, 0, "示例：需提前预约"),
        ]),
    ]),
    ("示例 · 湖滨茶屋（西湖）", "杭州", "上城区", "杭州市上城区 · 演示地址", [
        ("下午茶双人套餐", "茶饮×2 + 甜品×2", "周一至周日可用；限堂食", [
            ("meituan", 9900, 1000, 0, "示例：限堂食"),
            ("kuaishou", 9200, 200, 0, "示例：限堂食"),
        ]),
    ]),
]


@contextmanager
def connect(db_path: str):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(db_path: str, seed_demo: bool = True) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as db:
        db.executescript(SCHEMA)
        # Upgrade a previous 0.1 database in place without touching real records.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(stores)")}
        if "is_demo" not in columns:
            db.execute("ALTER TABLE stores ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
            db.execute("UPDATE stores SET is_demo=1 WHERE name LIKE '示例 · %'")
        if not seed_demo:
            return
        already_seeded = db.execute("SELECT 1 FROM stores LIMIT 1").fetchone()
        if already_seeded:
            return
        for name, city, district, address, products in DEMO:
            cursor = db.execute(
                "INSERT INTO stores(name, city, district, address, is_demo) VALUES (?,?,?,?,1)",
                (name, city, district, address),
            )
            store_id = cursor.lastrowid
            for pname, details, terms, quotes in products:
                product_id = db.execute(
                    "INSERT INTO products(store_id, name, details, usage_terms) VALUES (?,?,?,?)",
                    (store_id, pname, details, terms),
                ).lastrowid
                for platform, price, discount, fees, note in quotes:
                    db.execute(
                        """INSERT INTO quotes(product_id,platform,list_price_cents,
                        discount_cents,fee_cents,source_type,source_url,note,captured_at)
                        VALUES (?,?,?,?,?,'demo',NULL,?,?)""",
                        (product_id, platform, price, discount, fees, note, utc_now()),
                    )


def cities(db: sqlite3.Connection) -> list[str]:
    return [r["city"] for r in db.execute("SELECT DISTINCT city FROM stores ORDER BY city")]


def list_stores(db: sqlite3.Connection, city: str, search: str) -> list[dict]:
    # ESCAPE makes %/_ literal characters in user input, not SQL wildcard operators.
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = "%" + escaped + "%"
    rows = db.execute(
        """SELECT s.*, COUNT(p.id) AS product_count FROM stores s
        LEFT JOIN products p ON p.store_id=s.id
        WHERE (? = '' OR s.city=?) AND
          (s.name LIKE ? ESCAPE '\\' OR s.district LIKE ? ESCAPE '\\')
        GROUP BY s.id ORDER BY s.id LIMIT 40""",
        (city, city, pattern, pattern),
    )
    return [dict(r) for r in rows]


def store_and_products(db: sqlite3.Connection, store_id: int):
    store = db.execute("SELECT * FROM stores WHERE id=?", (store_id,)).fetchone()
    if not store:
        return None, []
    products = [dict(r) for r in db.execute(
        "SELECT * FROM products WHERE store_id=? ORDER BY id", (store_id,)
    )]
    return dict(store), products


def latest_quotes(db: sqlite3.Connection, product_id: int) -> list[dict]:
    """Most recently reported source per platform. Never fallback from expired feed
    to a demo price, which would silently mask a data outage.
    """
    legacy = db.execute(
        """SELECT * FROM (
             SELECT q.*, ROW_NUMBER() OVER(
                PARTITION BY q.platform
                ORDER BY CASE WHEN q.source_type='demo' THEN 0 ELSE 1 END DESC,
                q.captured_at DESC, q.id DESC
             ) AS rank_number
             FROM quotes q WHERE q.product_id=?
           ) WHERE rank_number=1 ORDER BY platform""",
        (product_id,),
    )
    selected = {}
    for row in legacy:
        entry = dict(row)
        entry.pop("rank_number", None)
        selected[entry["platform"]] = entry

    feed = db.execute(
        """SELECT * FROM (
             SELECT p.*, ROW_NUMBER() OVER (
                 PARTITION BY p.platform ORDER BY p.quoted_at DESC, p.id DESC
             ) AS rank_number
             FROM partner_quotes p WHERE p.product_id=?
           ) WHERE rank_number=1 ORDER BY platform""",
        (product_id,),
    )
    for row in feed:
        entry = dict(row)
        entry.pop("rank_number", None)
        entry["source_type"] = "partner_feed"
        entry["captured_at"] = entry["quoted_at"]
        selected[entry["platform"]] = entry
    return [selected[name] for name in sorted(selected)]
