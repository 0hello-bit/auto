````text
# 项目 B 优化版设计文档：网页注册自动化 + 可切换短信接码平台 + Sub2API 自动导入

## 1. 项目名称

Web Register And Sub2API Import Automation

中文名：网页注册与 Sub2API 自动导入服务

## 2. 项目定位

项目 A 邮箱验证码服务已经完成。项目 B 不再实现邮箱接收，只调用项目 A 的 HTTP API 获取邮箱验证码。

项目 B 负责：

```text
1. 自动打开自有网页项目的固定公网 URL
2. 自动点击免费注册
3. 自动填写邮箱
4. 调用项目 A 获取邮箱验证码
5. 自动填写邮箱验证码
6. 自动填写姓名和年龄
7. 自动完成账户创建
8. 调用 Sub2API 管理 API 生成 auth_url 和 session_id
9. 在同一个无痕浏览器上下文中新开授权页面
10. 在授权阶段根据页面要求自动处理手机号验证
11. 手机接码平台支持 62-US 和 5sim 自由切换
12. 监听 URL / Network，捕获 callback code/state
13. 校验 state
14. 调用 Sub2API create-from-oauth 创建账号
```

## 3. 外部依赖

### 3.1 项目 A：邮箱验证码服务

默认地址：

```text
http://127.0.0.1:5050
```

调用接口：

```http
POST /api/code
Header: x-api-key: <MAIL_CODE_SERVICE_API_KEY>
Content-Type: application/json
```

请求示例：

```json
{
  "email": "test001@hotmail.com",
  "timeout": 180,
  "pattern": "\\b\\d{6}\\b",
  "subject_keyword": "",
  "from_keyword": ""
}
```

成功响应：

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "email": "test001@hotmail.com",
    "verification_code": "123456"
  }
}
```

### 3.2 短信接码平台

短信平台必须抽象成统一接口，支持切换：

```text
SMS_PROVIDER=62us
SMS_PROVIDER=5sim
```

统一能力：

```text
1. 检查账号余额或状态
2. 下单手机号
3. 返回手机号、订单 ID、订单 token/状态
4. 轮询短信验证码
5. 成功后完成订单
6. 失败或超时后取消订单
```

#### 3.2.1 62-US

Base URL：

```text
https://api.62-us.com
```

鉴权：

```http
Authorization: Bearer <US62_API_KEY>
```

主要接口：

```text
GET  /api/v1/info
GET  /api/v1/goods
GET  /api/v1/goods/detail
POST /api/v1/get
GET  /api/v1/order/tokens
GET  /api/v1/msg
```

62-US 下单配置核心字段：

```text
US62_GOODS_ID=12-1-7
```

#### 3.2.2 5sim

Base URL：

```text
https://5sim.net
```

鉴权：

```http
Authorization: Bearer <FIVESIM_TOKEN>
Accept: application/json
```

主要接口：

```text
GET /v1/user/profile
GET /v1/user/buy/activation/{country}/{operator}/{product}
GET /v1/user/check/{id}
GET /v1/user/finish/{id}
GET /v1/user/cancel/{id}
```

5sim 下单配置核心字段：

```text
FIVESIM_COUNTRY=england
FIVESIM_OPERATOR=any
FIVESIM_PRODUCT=your-product-name
```

### 3.3 Sub2API

默认地址：

```text
http://127.0.0.1:8080
```

管理员鉴权：

```http
x-api-key: <SUB2API_ADMIN_API_KEY>
```

需要封装接口：

```text
POST /api/v1/admin/openai/generate-auth-url
POST /api/v1/admin/openai/create-from-oauth
GET  /api/v1/admin/groups/all
```

## 4. 完整一键流程

```text
1. 启动 Playwright Chromium
2. 创建独立 browser context，相当于无痕环境
3. 打开固定公网 URL
4. 点击“免费注册”
5. 输入邮箱
6. 点击继续
7. 调用项目 A 获取 6 位邮箱验证码
8. 填入邮箱验证码
9. 点击继续
10. 填写姓名和年龄
11. 点击完成账户创建
12. 调用 Sub2API generate-auth-url
13. 得到 auth_url 和 session_id
14. 从 auth_url 中解析 expected_state
15. 在同一个 browser context 中新开 page_auth
16. 打开 auth_url
17. 点击授权页里的“继续”
18. 如果页面出现手机号输入框：
    18.1 根据 SMS_PROVIDER 选择 62-US 或 5sim
    18.2 下单手机号
    18.3 填入手机号
    18.4 点击继续/发送验证码
    18.5 轮询短信平台接口
    18.6 提取 6 位短信验证码
    18.7 填入短信验证码
    18.8 点击继续
19. 监听 page_auth 的 URL、request、response
20. 捕获 /auth/callback?code=...&state=...
21. 校验 captured_state == expected_state
22. 调用 Sub2API create-from-oauth
23. 创建账号并绑定 group_ids
24. 成功后完成短信订单
25. 失败或超时后取消短信订单
26. 保存任务结果
```

## 5. 核心设计原则

### 5.1 同一个无痕上下文

网页注册阶段和授权阶段必须使用同一个 Playwright browser context：

```text
browser
  └── context
        ├── page_register
        └── page_auth
```

这样可以保留同一无痕上下文里的登录态、cookie 和授权状态。

### 5.2 短信平台统一抽象

不要在业务流程里直接写死 62-US 或 5sim。必须定义统一接口：

```python
class SmsProvider:
    def check_balance(self) -> dict:
        ...

    def buy_number(self) -> SmsOrder:
        ...

    def wait_code(self, order: SmsOrder, timeout: int) -> str:
        ...

    def finish_order(self, order: SmsOrder) -> None:
        ...

    def cancel_order(self, order: SmsOrder) -> None:
        ...
```

统一订单对象：

```python
@dataclass
class SmsOrder:
    provider: str
    order_id: str
    phone_number: str
    token: str | None = None
    raw: dict | None = None
```

业务流程只调用：

```python
provider = sms_provider_factory()
order = provider.buy_number()
code = provider.wait_code(order, timeout=180)
provider.finish_order(order)
```

### 5.3 Network 捕获 code/state

不能只依赖页面跳转成功。页面可能卡住，但 Network 中已经出现 callback 请求。

必须监听：

```text
page.url
page.on("request")
page.on("response")
```

只要任意 URL 中包含：

```text
/auth/callback?code=
```

就解析：

```text
code
state
callback_url
```

### 5.4 OAuth state 校验

必须做 state 校验：

```text
1. 从 Sub2API generate-auth-url 返回的 auth_url 中解析 expected_state
2. 从 callback_url 中解析 captured_state
3. 比较 expected_state 和 captured_state
4. 不一致则立即失败，不调用 create-from-oauth
```

## 6. 技术栈

```text
Python 3.10+
FastAPI
Uvicorn
Playwright
SQLite
requests
python-dotenv
```

## 7. 项目目录结构

```text
web-register-sub2api-automation/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── database.py
│   ├── email_pool.py
│   ├── mail_code_client.py
│   ├── sms_provider_base.py
│   ├── sms_provider_factory.py
│   ├── sms_62us_client.py
│   ├── sms_5sim_client.py
│   ├── sub2api_client.py
│   ├── oauth_network_capture.py
│   ├── identity_generator.py
│   ├── register_worker.py
│   ├── sub2api_import_worker.py
│   ├── register_and_import_worker.py
│   ├── job_service.py
│   └── api.py
├── emails.txt
├── emails.example.txt
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
├── run.ps1
├── screenshots/
├── traces/
└── data/
    └── register_jobs.db
```

## 8. 环境变量配置

`.env.example`：

```text
# 本服务
API_KEY=change-this-register-service-key
HOST=127.0.0.1
PORT=5060

# 目标网页
REGISTER_URL=https://your-public-register-url.example.com

# 浏览器
HEADLESS=false
BROWSER_TIMEOUT_MS=60000
CODE_TIMEOUT_SECONDS=180

# 文件与数据库
EMAILS_FILE=emails.txt
DB_FILE=data/register_jobs.db
SCREENSHOT_DIR=screenshots
TRACE_DIR=traces

# 项目 A：邮箱验证码服务
MAIL_CODE_SERVICE_BASE=http://127.0.0.1:5050
MAIL_CODE_SERVICE_API_KEY=change-this-mail-service-key

# 短信平台总开关
ENABLE_SMS=true
SMS_PROVIDER=62us
SMS_CODE_PATTERN=\b\d{6}\b
SMS_TIMEOUT_SECONDS=180
SMS_POLL_INTERVAL_SECONDS=5
SMS_AUTO_FINISH_ORDER=true
SMS_AUTO_CANCEL_ON_FAIL=true

# 62-US 配置
US62_BASE=https://api.62-us.com
US62_API_KEY=change-this-us62-key
US62_GOODS_ID=

# 5sim 配置
FIVESIM_BASE=https://5sim.net
FIVESIM_TOKEN=change-this-5sim-token
FIVESIM_COUNTRY=england
FIVESIM_OPERATOR=any
FIVESIM_PRODUCT=
FIVESIM_MAX_PRICE=

# Sub2API
SUB2API_BASE=http://127.0.0.1:8080
SUB2API_ADMIN_API_KEY=change-this-sub2api-admin-key
SUB2API_REDIRECT_URI=http://localhost:1455/auth/callback
SUB2API_DEFAULT_GROUP_IDS=1
SUB2API_DEFAULT_CONCURRENCY=10
SUB2API_DEFAULT_PRIORITY=1

# 随机身份
MIN_AGE=18
MAX_AGE=45

# 注册阶段选择器
FREE_REGISTER_SELECTOR=button:has-text("免费注册")
EMAIL_INPUT_SELECTOR=input[placeholder="电子邮件地址"]
EMAIL_CONTINUE_SELECTOR=button:has-text("继续")
EMAIL_CODE_INPUT_SELECTOR=input[name="code"], input[autocomplete="one-time-code"], input:visible
EMAIL_CODE_CONTINUE_SELECTOR=button:has-text("继续")
NAME_INPUT_SELECTOR=input[placeholder="姓名"], input[name="name"]
AGE_INPUT_SELECTOR=input[placeholder="年龄"], input[name="age"]
COMPLETE_BUTTON_SELECTOR=button:has-text("完成账户创建")

# 授权阶段选择器
AUTH_CONTINUE_SELECTOR=button:has-text("继续")
PHONE_INPUT_SELECTOR=input[placeholder="电话号码"], input[name="phone"], input[type="tel"]
PHONE_CONTINUE_SELECTOR=button:has-text("继续"), button:has-text("发送验证码")
SMS_CODE_INPUT_SELECTOR=input[placeholder="代码"], input[placeholder="验证码"], input[name="sms_code"], input[autocomplete="one-time-code"]
SMS_CODE_CONTINUE_SELECTOR=button:has-text("继续")

# 成功判断
SUCCESS_URL_KEYWORD=
SUCCESS_TEXT_KEYWORD=
```

## 9. 短信平台切换规则

### 9.1 使用 62-US

```text
SMS_PROVIDER=62us
US62_API_KEY=xxx
US62_GOODS_ID=12-1-7
```

执行逻辑：

```text
1. 使用 US62_API_KEY 鉴权
2. 调用 62-US 下单接口
3. 获取 order_id
4. 查询 order tokens
5. 获取 number 和 token
6. 网页填入 number
7. 轮询短信接口
8. 提取验证码
9. 成功后结束订单
10. 失败后按配置取消订单
```

### 9.2 使用 5sim

```text
SMS_PROVIDER=5sim
FIVESIM_TOKEN=xxx
FIVESIM_COUNTRY=england
FIVESIM_OPERATOR=any
FIVESIM_PRODUCT=your-product-name
```

执行逻辑：

```text
1. 使用 FIVESIM_TOKEN 鉴权
2. 调用 /v1/user/profile 检查余额
3. 调用 /v1/user/buy/activation/{country}/{operator}/{product} 下单号码
4. 返回 id 和 phone
5. 网页填入 phone
6. 轮询 /v1/user/check/{id}
7. 从 sms 字段中提取验证码
8. 成功后调用 /v1/user/finish/{id}
9. 失败或超时后调用 /v1/user/cancel/{id}
```

### 9.3 配置错误处理

如果：

```text
ENABLE_SMS=true
SMS_PROVIDER=62us
US62_GOODS_ID 为空
```

必须报错：

```text
SMS_PROVIDER=62us 但 US62_GOODS_ID 未配置
```

如果：

```text
ENABLE_SMS=true
SMS_PROVIDER=5sim
FIVESIM_PRODUCT 为空
```

必须报错：

```text
SMS_PROVIDER=5sim 但 FIVESIM_PRODUCT 未配置
```

## 10. 数据库设计

### 10.1 register_jobs

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
    sms_order_id TEXT,
    sms_verification_code TEXT,
    error_message TEXT,
    screenshot_path TEXT,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER
);
```

### 10.2 sub2api_import_jobs

```sql
CREATE TABLE IF NOT EXISTS sub2api_import_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT UNIQUE NOT NULL,
    register_job_id TEXT,
    email TEXT,
    sub2api_session_id TEXT,
    auth_url TEXT,
    expected_state TEXT,
    callback_url TEXT,
    code TEXT,
    state TEXT,
    group_ids TEXT,
    concurrency INTEGER,
    priority INTEGER,
    status TEXT NOT NULL,
    sub2api_account_id INTEGER,
    error_message TEXT,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER
);
```

### 10.3 registered_accounts

```sql
CREATE TABLE IF NOT EXISTS registered_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    age INTEGER,
    phone_number TEXT,
    sms_provider TEXT,
    sms_order_id TEXT,
    source_register_job_id TEXT,
    source_import_job_id TEXT,
    sub2api_account_id INTEGER,
    created_at INTEGER NOT NULL
);
```

### 10.4 email_usage

```sql
CREATE TABLE IF NOT EXISTS email_usage (
    email TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    last_job_id TEXT,
    last_error TEXT,
    updated_at INTEGER NOT NULL
);
```

## 11. API 设计

### 11.1 健康检查

```http
GET /health
```

### 11.2 单独执行网页注册

```http
POST /api/register/start
Header: x-api-key: <API_KEY>
```

请求：

```json
{
  "url": "https://your-public-register-url.example.com",
  "email": "test001@hotmail.com",
  "headless": false,
  "timeout": 180
}
```

### 11.3 单独执行 Sub2API 导入

```http
POST /api/sub2api/import/start
Header: x-api-key: <API_KEY>
```

请求：

```json
{
  "email": "test001@hotmail.com",
  "name": "test001@hotmail.com",
  "group_ids": [1],
  "concurrency": 10,
  "priority": 1,
  "headless": false,
  "enable_sms": true,
  "sms_provider": "5sim"
}
```

### 11.4 一键注册并导入

```http
POST /api/register-and-import/start
Header: x-api-key: <API_KEY>
```

请求：

```json
{
  "url": "https://your-public-register-url.example.com",
  "email": "test001@hotmail.com",
  "headless": false,
  "timeout": 300,
  "enable_sms": true,
  "sms_provider": "62us",
  "group_ids": [1],
  "concurrency": 10,
  "priority": 1
}
```

使用 5sim 时：

```json
{
  "url": "https://your-public-register-url.example.com",
  "email": "test001@hotmail.com",
  "headless": false,
  "timeout": 300,
  "enable_sms": true,
  "sms_provider": "5sim",
  "group_ids": [1],
  "concurrency": 10,
  "priority": 1
}
```

### 11.5 查询任务

```http
GET /api/jobs
GET /api/jobs/{job_id}
```

### 11.6 查询成功账号

```http
GET /api/accounts
```

### 11.7 查询 Sub2API 分组

```http
GET /api/sub2api/groups
```

### 11.8 查询短信平台状态

```http
GET /api/sms/providers
GET /api/sms/profile
```

`/api/sms/providers` 返回：

```json
{
  "code": 1,
  "data": {
    "current": "5sim",
    "available": ["62us", "5sim"]
  }
}
```

`/api/sms/profile` 根据当前 `SMS_PROVIDER` 查询余额或状态。

## 12. 任务状态

注册任务状态：

```text
pending
running
success
failed
timeout
```

导入任务状态：

```text
pending
generating_auth_url
waiting_auth
waiting_phone
waiting_sms
callback_captured
importing
success
failed
timeout
```

短信订单状态：

```text
none
created
waiting_sms
sms_received
finished
cancelled
failed
```

## 13. 安全要求

```text
1. 所有 /api/* 接口必须 x-api-key 鉴权
2. 默认只监听 127.0.0.1
3. 不在日志中打印完整 API Key
4. 不在日志中打印完整 token
5. .env 不提交 Git
6. emails.txt 不提交 Git
7. screenshots 和 traces 不提交 Git
8. 不实现滑块/图形验证码破解
9. 62-US / 5sim 仅用于自有项目手机号验证流程
```

## 14. 推荐开发顺序

```text
1. 保留原项目 B 的基础结构
2. 抽象 SmsProvider 接口
3. 把原 62-US 封装改成 SmsProvider 实现
4. 新增 5sim SmsProvider 实现
5. 新增 sms_provider_factory.py
6. register_and_import_worker 不直接调用 62-US，只调用统一 provider
7. 增加 /api/sms/providers 和 /api/sms/profile
8. README 里写清楚 SMS_PROVIDER=62us / 5sim 的切换方式
```xxxxxxxxxx9 1第一步：实现基础 FastAPI、配置、数据库、任务查询2第二步：实现 mail_code_client，对接项目 A3第三步：实现 register_worker，跑通网页邮箱注册4第四步：实现 sub2api_client，跑通 generate-auth-url 和 groups 查询5第五步：实现 oauth_network_capture，跑通 code/state 捕获6第六步：实现 create-from-oauth 导入7第七步：实现 sms_62us_client8第八步：把 62-US 接入授权阶段9第九步：实现一键 register-and-importtext
````