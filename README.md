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

## 第二阶段 · 数据接入与可信比价

第二阶段新增 **签名合作方报价接入通道**，接口 `POST /api/partner/quotes`。HMAC 验签、价格时效、资格、售罄、同店同套餐条件匹配及防重放均已纳入自动化测试；**该通道不是美团/抖音/快手官方 API 接入**，签名不代表平台验证或真实优惠保证。

本阶段开始，**人工录入不再触发可比较价格差额**。只有至少两个平台的有效签名合作方报价才会展示明确标注为「合作方上报报价差额」的结果；购买前仍必须原平台核实。

完整接入约束与数据使用边界：[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)。

## 美团商品获取实验

本阶段实现 **已知公开商品链接 → 受限提取 → 独立保存页面标价**；并研究美团联盟官方商品查询 API。两种数据不能混为官方实时结算价。

### 公开页面抓取（管理员专用）

先设置 `TBB_ADMIN_TOKEN`，启动服务后执行（**替换为真实商品详情页 URL**）：

```bash
curl -X POST http://127.0.0.1:8000/api/admin/meituan/inspect \
  -H 'Content-Type: application/json' \
  -H "X-Admin-Token: $TBB_ADMIN_TOKEN" \
  -d '{"url":"https://i.meituan.com/deal/你的商品路径"}'
```

- `POST /api/admin/meituan/inspect`：返回可提取的商品名称、品牌、说明、**页面标示价格**、证据、采集时间；不保存。
- `POST /api/admin/meituan/collect`：提交同样的 URL；只有页面存在明确的结构化商品价格时才保存到 `meituan_public_observations`。未取到价格返回 422。
- `GET /api/admin/meituan/observations`：查看历史采集快照。**采集数据不参与实时最低价排名**。
- 页面检测默认检查 robots.txt，不自动跟随重定向，不使用登录 cookie，不绕过访问限制、验证码或风控；仅允许有限的美团 HTTPS 域名，限制响应大小和超时。
- 当前只解析公开 HTML 内的 Schema.org Product JSON-LD 和明确的 Product 元数据，不猜测隐藏页面数据，碰到纯 JS 或登录页会明确返回不可提取。

### 官方美团联盟商品查询（接口字段已适配，尚未认证对接）

官方文档：[美团联盟商品查询接口](https://page.meituan.net/html/1701831807616_1db0df/index.html)，请求地址 `https://media.meituan.com/cps_open/common/api/v1/query_coupon`。官方支持到店餐饮商品查询，但需要 AppKey、签名及相应权限，且只涵盖开放的商品范围。

- `GET /api/admin/meituan/alliance/query?keyword=火锅`：生成待签名 JSON 请求体；**不发送请求**。
- `POST /api/admin/meituan/alliance/normalize`：把提供的官方 schema 格式响应转成统一商品字段；**不校验该 JSON 真正来自美团**。
- 商品显示价按官方文档的**元**转成内部整数**分**；官方响应中的门店数量不是可用于匹配同店的 POI ID。
- 美团联盟关键词查询不能同时设置 `platform`/`bizLine`；需按返回字段再次确认是否属于到店餐饮。

**验收状态：** 目前通过模拟 HTTP/官方 JSON 格式回归测试，但还没有取得真实美团商品详情页成功采集的验证样本，也没有完成美团联盟签名鉴权接入。不得用测试样例冒充真实价格。

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
