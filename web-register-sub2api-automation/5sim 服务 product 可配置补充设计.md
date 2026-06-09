# 5sim 服务 product 可配置补充设计

## 1. 目标

5sim 下单需要三个核心参数：

```text
country
operator
product
```

其中：

```text
country：国家，例如 argentina
product：服务，例如 google、telegram、facebook
operator：运营商，例如 virtual62、virtual34、any
```

项目要求：

```text
1. 服务 product 可以由用户自由选择
2. 国家 country 可以由用户自由选择
3. 运营商 operator 默认由系统自动选择成功率最高
4. API 请求参数可以覆盖 .env 默认配置
```

## 2. 配置项

`.env.example` 中保留：

```text
FIVESIM_COUNTRY=argentina
FIVESIM_PRODUCT=google
FIVESIM_OPERATOR_STRATEGY=highest_success
FIVESIM_OPERATOR=any
FIVESIM_OPERATOR_FALLBACK=any
FIVESIM_MIN_SUCCESS_RATE=0
FIVESIM_MAX_PRICE=
FIVESIM_MIN_COUNT=1
FIVESIM_EXCLUDE_OPERATORS=
```

含义：

```text
FIVESIM_COUNTRY：默认国家
FIVESIM_PRODUCT：默认服务
FIVESIM_OPERATOR_STRATEGY：运营商选择策略
FIVESIM_OPERATOR：manual 模式下使用
FIVESIM_OPERATOR_FALLBACK：自动选择失败后的兜底运营商
```

## 3. API 请求覆盖配置

以下接口必须支持请求中覆盖 5sim 参数：

```text
POST /api/sub2api/import/start
POST /api/register-and-import/start
GET  /api/sms/5sim/operators/best
```

### 3.1 一键注册并导入

请求示例：

```json
{
  "url": "https://your-public-register-url.example.com",
  "email": "test001@hotmail.com",
  "headless": false,
  "timeout": 300,
  "enable_sms": true,
  "sms_provider": "5sim",
  "fivesim_country": "argentina",
  "fivesim_product": "google",
  "fivesim_operator_strategy": "highest_success",
  "group_ids": [1],
  "concurrency": 10,
  "priority": 1
}
```

如果不传：

```text
fivesim_country
fivesim_product
fivesim_operator_strategy
```

则使用 `.env` 默认值。

### 3.2 单独 Sub2API 导入

请求示例：

```json
{
  "email": "test001@hotmail.com",
  "name": "test001@hotmail.com",
  "group_ids": [1],
  "concurrency": 10,
  "priority": 1,
  "headless": false,
  "enable_sms": true,
  "sms_provider": "5sim",
  "fivesim_country": "argentina",
  "fivesim_product": "telegram",
  "fivesim_operator_strategy": "highest_success"
}
```

## 4. 服务 product 查询接口

为了知道 5sim 支持哪些服务，需要提供接口：

```http
GET /api/sms/5sim/products?country=argentina&operator=any
```

内部调用：

```http
GET https://5sim.net/v1/guest/products/{country}/{operator}
```

返回应包含服务 slug、价格、库存等信息。

响应示例：

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "country": "argentina",
    "operator": "any",
    "products": [
      {
        "product": "google",
        "price": 0.2,
        "count": 767,
        "rate": 50.37
      },
      {
        "product": "telegram",
        "price": 0.15,
        "count": 1200,
        "rate": 43.1
      }
    ]
  }
}
```

## 5. 最佳运营商预览接口

接口：

```http
GET /api/sms/5sim/operators/best?country=argentina&product=google&strategy=highest_success
```

逻辑：

```text
1. 根据 country 和 product 查询候选运营商
2. 根据 strategy 选择运营商
3. 返回候选列表和最终 selected
4. 不下单，不扣费
```

响应：

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "country": "argentina",
    "product": "google",
    "strategy": "highest_success",
    "selected": {
      "operator": "virtual62",
      "price": 0.2,
      "success_rate": 50.37,
      "count": 767
    },
    "candidates": [
      {
        "operator": "virtual62",
        "price": 0.2,
        "success_rate": 50.37,
        "count": 767
      }
    ]
  }
}
```

## 6. 购买逻辑

最终下单时：

```text
country = 请求参数 fivesim_country 或 .env FIVESIM_COUNTRY
product = 请求参数 fivesim_product 或 .env FIVESIM_PRODUCT
strategy = 请求参数 fivesim_operator_strategy 或 .env FIVESIM_OPERATOR_STRATEGY
operator = 根据 strategy 自动选择
```

然后调用：

```http
GET /v1/user/buy/activation/{country}/{operator}/{product}
```

示例：

```http
GET /v1/user/buy/activation/argentina/virtual62/google
```

## 7. 任务记录

任务中需要记录：

```text
sms_provider = 5sim
sms_country = argentina
sms_product = google
sms_operator_strategy = highest_success
sms_operator_selected = virtual62
sms_price = 0.2
sms_success_rate = 50.37
sms_order_id = 123456
phone_number = +xxxxxxxx
```

数据库 register_jobs 增加字段：

```sql
sms_country TEXT,
sms_product TEXT,
sms_operator TEXT,
sms_operator_strategy TEXT,
sms_price REAL,
sms_success_rate REAL
```

完整建表建议：

```sql
CREATE TABLE IF NOT EXISTS register_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    email TEXT NOT NULL,
    name TEXT,
    age INTEGER,
    status TEXT NOT NULL,
    email_verification_code TEXT,
    phone_number TEXT,
    sms_provider TEXT,
    sms_country TEXT,
    sms_product TEXT,
    sms_operator TEXT,
    sms_operator_strategy TEXT,
    sms_order_id TEXT,
    sms_price REAL,
    sms_success_rate REAL,
    sms_verification_code TEXT,
    error_message TEXT,
    screenshot_path TEXT,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER
);
```