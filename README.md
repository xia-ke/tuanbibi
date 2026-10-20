# 团比比 · TuanBiBi

**同一家店，多平台比。** 面向中国本地生活的开源团购券比价项目。

> **当前为 MVP 演示版**。仓库没有接入美团、抖音、快手等平台的实时查询接口。默认数据库里的门店与价格均为虚构样例，**不可用于真实购买决策**。管理员可手动录入来源明确的报价用于流程测试，但这不等于已核验的实时价格。

## 当前已实现

- 城市与门店搜索、移动端响应式 Web 页面
- 同一门店下按完全相同的套餐内容和使用条件分组
- 标价、优惠额、附加费用均以整数分计算预估实付
- 数据来源区分：演示数据 / 人工录入 / 未来授权 API
- 演示报价**不会参与真实省钱结论**
- 人工录入需管理员令牌，外链必须是对应平台 HTTPS 官方域名
- SQLite 数据库、REST API、自动化测试与 GitHub Actions

## 本地启动

要求 Python 3.11+（推荐 3.12）。

```bash
git clone https://github.com/xia-ke/tuanbibi.git
cd tuanbibi
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

访问 http://127.0.0.1:8000 ，API 文档 http://127.0.0.1:8000/docs 。

默认首次启动会在 `data/tuanbibi.db` 中种入演示数据。设置 `TBB_SEED_DEMO=0` 可关闭演示数据初始化；设置 `TBB_DB_PATH` 可指定数据库路径。**关闭演示初始化不会自动删除已有的演示数据**，需要纯净数据库请指定一个新的数据库文件。

## 数据录入（开发和管理员专用）

写入接口默认关闭。设置强随机值 `TBB_ADMIN_TOKEN` 后，通过 `X-Admin-Token` 请求头使用：

| 路径 | 作用 |
| --- | --- |
| `POST /api/admin/stores` | 录入门店（name, city, district, address） |
| `POST /api/admin/products` | 录入套餐（store_id, name, details, usage_terms） |
| `POST /api/admin/quotes` | 录入报价（product_id, platform, list_price_cents, discount_cents, fee_cents, source_url?, note） |

以下价格为**示例**，不是平台真实报价：

```bash
curl -X POST http://127.0.0.1:8000/api/admin/quotes \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $TBB_ADMIN_TOKEN" \
  -d '{"product_id":1,"platform":"meituan","list_price_cents":2990,"discount_cents":200,"fee_cents":0,"note":"手工测试报价，尚待核验"}'
```

外链必须是对应平台的 HTTPS 官方域名。人工录入统一标记为 `manual`，不称为实时或已核验。生产前需加正式登录鉴权、审计、限流和数据审核；目前的管理员接口只适合可信的开发环境。

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## 目录

```text
app/pricing.py     金额计算、真实/演示数据隔离
app/store.py       SQLite 数据、查询、演示样例
app/main.py        FastAPI 路由、管理员写入权限、校验
web/index.html     无需构建的响应式消费者端
tests/             单元与 API 测试
docs/ROADMAP.md    产品阶段路线
```

## 开源项目复用说明

首期调研并参考以下开源项目的产品及架构经验，**未直接复制源码、SDK 或未授权数据**：

- [JuYouHui](https://github.com/jiabaobei/ju-you-hui)（MIT）：城市、搜索、优惠卡片聚合交互
- [price-monitor](https://github.com/Kumagt/price-monitor)（MIT）：数据源分层、价格历史与监控
- [McD Saver](https://github.com/yuxinlaile/McD-saver)：套餐优惠计算思路；其依赖的 McDonald's MCP 服务另有使用限制

当后续实际合并这些项目代码时，将保留其版权许可证和 NOTICE，并再次核查具体第三方接口的使用规则。

## 数据可信原则

- 不虚构实时接口结果，不把 AI 推测价格当成真实优惠
- 不将不同门店、不同内容或不同使用条款的套餐直接比较
- 保留价格来源、采集时间、优惠前提；最终付款以原平台为准
- 不绕过登录、验证码、平台风控或进行未授权批量采集

项目路线见 [docs/ROADMAP.md](docs/ROADMAP.md)。代码使用 [MIT](LICENSE) 许可证。
