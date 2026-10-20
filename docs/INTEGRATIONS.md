# 团比比 · 第二阶段数据接入与真实性

## 官方文档核查（2026-10-20）

- **抖音生活服务：** [查询商品线上数据](https://partner.open-douyin.com/docs/resource/zh-CN/local-life/develop/OpenAPI/general-capabilities/product-query/online.get)。文档明确要求对应 scope 和商家授权，存在 ToB 场景限制；只能查询授权范围内的商品，不能当作全网公众团购券搜索接口。
- **抖音能力申请：** [到综团购解决方案](https://partner.open-douyin.com/docs/resource/zh-CN/local-life/solution/industry-solutions/composite)。服务商或商家角色需入驻、申请并经过权限审核。
- **快手本地生活：** [整体说明](https://open.kuaishou.com/docs/develop/IndustrySolutions/introduce.html)、[商品对接](https://open.kuaishou.com/docs/develop/IndustrySolutions/introduction/saas/productMount.html)。需要申请本地生活业务权限，商品存在审核与上下架状态。
- **美团：** [开放平台查询授权信息](https://open.meituan.com/docs/detail?id=6)。证实存在应用授权检查机制，但该接口**不是**面向公众任意门店的团购商品搜索权限；具体商品授权范围尚须与平台或合作商核实。

**状态：** 目前四个平台的官方团购商品搜索接口均未接入。没有商家 API 凭据，不宣称跨平台实时可用。

## 复用开源项目边界

此前调研的 [price-monitor](https://github.com/Kumagt/price-monitor) 的多数据源和 Fallback 结构可供借鉴；但它不是具有现成完整美团团购公开数据的供应方。第一阶段已建好的 FastAPI + SQLite 系统无必要替换。第二阶段新增 `PlatformAdapter` 协议，后续可以包裹经许可的 SDK 实现，不依赖反爬或模拟接口。

## 已实现的 Signed Partner Feed

为了不让平台 API 阻塞下游比价开发，第二阶段开放**签名合作方数据上报**。签名只能证明提交方的身份，不能证明其拥有平台授权或报价真实，因此统一展示为「合作方上报」，不标注「官方实时」。

- `POST /api/partner/quotes`：签名原始 JSON 上报单条报价，严格校验门店、套餐、地址、使用条件、金额、官方来源链接、时效与购买资格。
- `GET /api/integrations/status`：公开显示各平台未连接，合作方上报是否配置。
- `GET /api/stores/{id}/comparison`：只计算两家及以上**不同平台**、仍在有效期、15 分钟内更新、明确可售、资格为 `public` 的签名合作方报价差额。
- 数据表 `partner_quotes`：追加式记录，`partner_id + event_id` 唯一，重放同一事件返回 409；不会覆盖历史报价。原 SQLite 数据兼容。

### 配置（仅可信服务端）

将不同合作方分配独立的至少 32 字节随机密钥，**不要提交密钥到仓库**：

```bash
export TBB_PARTNER_KEYS_JSON='{"merchant_alpha":"replace-with-a-random-secret-of-at-least-32-bytes"}'
# 显式准入：ID 必须是管理员创建的真实门店，不能是演示门店
export TBB_PARTNER_SCOPES_JSON='{"merchant_alpha":{"store_ids":[123],"platforms":["meituan","douyin"]}}'
```

启动服务后，合作方需要在同一个 HTTP 请求中提交：

```text
POST /api/partner/quotes
Content-Type: application/json
X-Partner-Id: merchant_alpha
X-Timestamp: 1792512000
X-Signature: sha256=<HMAC_SHA256_HEX>
```

签名内容是原始字节：`ASCII(UnixTimestamp) + b"." + raw_json_bytes`。签名密钥来自 `TBB_PARTNER_KEYS_JSON`，而可提交的平台和门店由 `TBB_PARTNER_SCOPES_JSON` 的显式准入列表限制，配置缺失将拒绝写入。时间戳最多偏移 300 秒，JSON 不得超过 32KiB，事件 ID 对合作方全局唯一。传入业务报价时间需为带时区 ISO 8601 时间，记录时间必须在最近 10 分钟内，过期时间必须是将来且距离记录时间最多 24 小时。

请求示意（字段值为虚构演示信息）：

```json
{
  "event_id": "example_unique_0001",
  "store_id": 123,
  "product_id": 456,
  "platform": "meituan",
  "product_name": "样例饮品套餐",
  "details": "中杯饮品×1，不含配送",
  "usage_terms": "全时段到店自取；不与其他活动叠加",
  "store_address": "上海市徐汇区样例路123号",
  "list_price_cents": 1990,
  "discount_cents": 300,
  "fee_cents": 0,
  "availability": "available",
  "eligibility": "public",
  "quoted_at": "2026-10-20T09:40:00+00:00",
  "valid_until": "2026-10-20T09:55:00+00:00",
  "source_url": "https://www.meituan.com/",
  "note": "测试数据，不可作为真实价格"
}
```

此 JSON 是**结构示意**，所示门店/套餐 ID 不保证存在，且签名上报禁止写入演示门店；固定的时间戳会很快失效，必须使用当前时间生成真实测试请求。更多端到端示例见 `tests/test_partner_feed.py`。

### 严格匹配

现阶段采取 **fail-closed 精确匹配**：合作方提交前须获得对应门店与平台的白名单授权，且目标不能是演示门店。传入门店数据库 ID、套餐 ID、地址、套餐名称、内容和使用条件，经 Unicode NFKC 和空白规范化后全部一致才允许入库。即使商户名称相似，也不能强行把不同地点、人数、条款的套餐合并。未来接入商家 POI 官方标识后，将关联交由可审核的映射表处理。

### 生产环境边界

当前签名验证是最小信任边界，**不能直接公开上线**，还缺少合作方的合同/平台授权核验、HTTPS 部署、限流、密钥轮换、撤销、商家映射管理、正式审计、风控监测等。一次签名真实性不等于实际商品可买，也不能单独证明有权使用该数据。

## 下一步

1. 获得合法授权的两平台同店报价（项目 Issue #2），至少验证 1 家真实门店和 1 个真正同款套餐。
2. 编写 `PlatformAdapter` 的平台官方 SDK 实现（逐个平台）；区分商家 ToB 数据范围与面向消费者的合法使用许可。
3. 使用 POI 与标准化套餐映射，替代当前严格的字符串比对；加入人工复核。
4. 有真实合作数据后加入回调失效、券领用资格核验、可靠的历史价格统计与提醒。
