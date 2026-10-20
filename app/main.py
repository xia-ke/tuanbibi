"""团比比 MVP: public read-only search + explicitly protected manual submissions."""
from contextlib import asynccontextmanager
import hmac
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from app.pricing import comparison_for, payable_cents, utc_now
from app.integrations import PLATFORM_DOMAINS, attach_feed_routes, platform_url_is_valid
from app.meituan_routes import attach_meituan_routes
from app import store as data

BASE_DIR = Path(__file__).resolve().parent.parent
SUPPORTED = PLATFORM_DOMAINS


class StoreInput(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    city: str = Field(min_length=2, max_length=30)
    district: str = Field(min_length=1, max_length=60)
    address: str = Field(min_length=2, max_length=200)


class ProductInput(BaseModel):
    store_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=120)
    details: str = Field(min_length=3, max_length=400)
    usage_terms: str = Field(min_length=2, max_length=400)


class QuoteInput(BaseModel):
    product_id: int = Field(gt=0)
    platform: str
    list_price_cents: int = Field(ge=0, le=10_000_000)
    discount_cents: int = Field(ge=0, le=10_000_000)
    fee_cents: int = Field(ge=0, le=10_000_000)
    source_url: str | None = Field(default=None, max_length=2048)
    note: str = Field(min_length=2, max_length=300)

    @field_validator("platform")
    @classmethod
    def check_platform(cls, value: str):
        if value not in SUPPORTED:
            raise ValueError("不支持的平台")
        return value

    @model_validator(mode="after")
    def validate_quote(self):
        payable_cents(self.list_price_cents, self.discount_cents, self.fee_cents)
        if self.source_url:
            if not platform_url_is_valid(self.platform, self.source_url):
                raise ValueError("来源链接必须是该平台的 HTTPS 官方域名")
        return self


def create_app(db_path: str | None = None, seed_demo: bool | None = None) -> FastAPI:
    path = db_path or os.environ.get("TBB_DB_PATH", str(BASE_DIR / "data" / "tuanbibi.db"))
    demo = (os.environ.get("TBB_SEED_DEMO", "1") == "1") if seed_demo is None else seed_demo

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        data.init_db(path, seed_demo=demo)
        yield

    app = FastAPI(title="团比比 API", version="0.3.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "web")), name="static")

    def require_admin(x_admin_token: str | None = Header(default=None)):
        expected = os.environ.get("TBB_ADMIN_TOKEN", "")
        if not expected:
            raise HTTPException(503, "写入接口未启用：请配置 TBB_ADMIN_TOKEN")
        if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
            raise HTTPException(401, "缺少或无效的管理员令牌")

    @app.get("/")
    def homepage():
        return FileResponse(BASE_DIR / "web" / "index.html")

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": "0.3.0"}

    @app.get("/api/meta")
    def meta():
        with data.connect(path) as db:
            return {
                "cities": data.cities(db),
                "demo_seed_enabled": demo,
                "platforms": list(SUPPORTED),
                "notice": "演示数据不代表真实价格；人工录入不等于已核验的实时价格。",
            }

    @app.get("/api/stores")
    def stores(city: str = Query(default="", max_length=30),
               q: str = Query(default="", max_length=80)):
        with data.connect(path) as db:
            return {"stores": data.list_stores(db, city.strip(), q.strip())}

    @app.get("/api/stores/{store_id}/comparison")
    def comparison(store_id: int):
        with data.connect(path) as db:
            store, products = data.store_and_products(db, store_id)
            if not store:
                raise HTTPException(404, "未找到门店")
            return {
                "store": store,
                "groups": [comparison_for(p, data.latest_quotes(db, p["id"])) for p in products],
                "notice": "仅同店同款可比；真实比较目前仅基于有效签名合作方报价，非平台官方实时价。",
            }

    @app.post("/api/admin/stores", status_code=201, dependencies=[Depends(require_admin)])
    def add_store(payload: StoreInput):
        with data.connect(path) as db:
            cursor = db.execute(
                "INSERT INTO stores(name,city,district,address) VALUES (?,?,?,?)",
                (payload.name, payload.city, payload.district, payload.address),
            )
            db.commit()
            return {"id": cursor.lastrowid}

    @app.post("/api/admin/products", status_code=201, dependencies=[Depends(require_admin)])
    def add_product(payload: ProductInput):
        with data.connect(path) as db:
            if not db.execute("SELECT 1 FROM stores WHERE id=?", (payload.store_id,)).fetchone():
                raise HTTPException(404, "未找到门店")
            try:
                cursor = db.execute(
                    "INSERT INTO products(store_id,name,details,usage_terms) VALUES (?,?,?,?)",
                    (payload.store_id, payload.name, payload.details, payload.usage_terms),
                )
                db.commit()
            except data.sqlite3.IntegrityError:
                raise HTTPException(409, "同门店已存在完全相同的套餐")
            return {"id": cursor.lastrowid}

    @app.post("/api/admin/quotes", status_code=201, dependencies=[Depends(require_admin)])
    def add_quote(payload: QuoteInput):
        with data.connect(path) as db:
            if not db.execute("SELECT 1 FROM products WHERE id=?", (payload.product_id,)).fetchone():
                raise HTTPException(404, "未找到套餐")
            cursor = db.execute(
                """INSERT INTO quotes(product_id,platform,list_price_cents,discount_cents,
                  fee_cents,source_type,source_url,note,captured_at)
                  VALUES (?,?,?,?,?,'manual',?,?,?)""",
                (payload.product_id, payload.platform, payload.list_price_cents,
                 payload.discount_cents, payload.fee_cents,
                 payload.source_url, payload.note, utc_now()),
            )
            db.commit()
            return {"id": cursor.lastrowid, "source_type": "manual"}

    attach_feed_routes(app, path)
    attach_meituan_routes(app, path, require_admin)
    return app


app = create_app()
