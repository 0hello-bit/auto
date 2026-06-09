# Web 邮箱注册自动化项目文档

## 1. 项目名称

Web Email Register Automation

中文名：网页邮箱注册自动化服务

## 2. 项目目标

本项目用于自动化完成指定网页的邮箱注册流程。

项目接入前置的“邮箱验证码接收服务”，自动获取邮箱验证码，并通过浏览器自动化完成以下流程：

```text
打开固定 URL
点击“免费注册”
输入邮箱地址
点击“继续”
等待邮箱验证码
填入 6 位验证码
点击“继续”
填写姓名和年龄
点击“完成账户创建”
保存注册结果
```

本项目仅用于自有网站、测试环境或已授权系统的注册流程自动化。

## 3. 系统依赖

### 3.1 前置服务：邮箱验证码接收服务

默认地址：

```text
http://127.0.0.1:5050
```

调用接口：

```http
POST /api/code
```

请求示例：

```json
{
  "email": "test@example.com",
  "timeout": 180,
  "pattern": "\\b\\d{6}\\b",
  "subject_keyword": "",
  "from_keyword": ""
}
```

响应示例：

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "email": "test@example.com",
    "verification_code": "123456"
  }
}
```

### 3.2 自动化服务

默认地址：

```text
http://127.0.0.1:5060
```

## 4. 技术栈

```text
Python 3.10+
FastAPI
Uvicorn
Playwright
SQLite
requests
python-dotenv
```

浏览器自动化使用 Playwright。

数据库使用 SQLite 保存任务状态、成功账号和错误日志。

## 5. 项目目录结构

```text
web-email-register-automation/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI 入口
│   ├── config.py                # 配置读取
│   ├── models.py                # Pydantic 模型
│   ├── database.py              # SQLite 初始化与读写
│   ├── email_pool.py            # emails.txt 邮箱池读取
│   ├── mail_code_client.py      # 调用邮箱验证码服务
│   ├── identity_generator.py    # 随机姓名和年龄生成
│   ├── register_worker.py       # Playwright 自动注册核心逻辑
│   ├── job_service.py           # 任务调度与状态管理
│   └── api.py                   # API 路由
├── emails.txt                   # 邮箱列表，不提交 Git
├── .env                         # 本地配置，不提交 Git
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
├── run.ps1
├── screenshots/                 # 失败截图
├── traces/                      # Playwright trace，可选
└── data/
    └── register_jobs.db
```

## 6. 注册流程

### 6.1 单次注册流程

```text
1. 创建注册任务
2. 获取注册 URL
3. 获取邮箱
   - 如果请求指定 email，则使用指定 email
   - 如果未指定 email，则从 emails.txt 顺序取一个
4. 生成随机姓名
5. 生成随机年龄
6. 启动 Playwright 无痕浏览器上下文
7. 进入固定 URL
8. 点击“免费注册”
9. 等待邮箱输入框出现
10. 输入邮箱
11. 点击“继续”
12. 等待验证码页面出现
13. 调用邮箱验证码服务获取 6 位验证码
14. 填入验证码
15. 点击“继续”
16. 等待姓名/年龄页面出现
17. 填入姓名
18. 填入年龄
19. 点击“完成账户创建”
20. 判断是否注册成功
21. 保存结果
22. 关闭浏览器
```

### 6.2 批量注册流程

第一版采用顺序批量：

```text
读取 emails.txt
逐个邮箱执行单次注册
每个任务独立保存状态
失败任务保存错误截图
不做并发
```

后续可扩展并发，但第一版不建议并发，避免验证码错配、浏览器资源冲突和风控问题。

## 7. 页面选择器配置

页面元素建议通过配置管理，避免写死在代码里。

`.env.example`：

```text
API_KEY=change-this-to-a-strong-secret

REGISTER_URL=https://your-register-url.example.com

MAIL_CODE_SERVICE_BASE=http://127.0.0.1:5050
MAIL_CODE_SERVICE_API_KEY=change-this-mail-service-key

HOST=127.0.0.1
PORT=5060

HEADLESS=false
BROWSER_TIMEOUT_MS=60000
CODE_TIMEOUT_SECONDS=180

EMAILS_FILE=emails.txt
DB_FILE=data/register_jobs.db

SCREENSHOT_DIR=screenshots
TRACE_DIR=traces

# 页面选择器
FREE_REGISTER_SELECTOR=button:has-text("免费注册")
EMAIL_INPUT_SELECTOR=input[placeholder="电子邮件地址"]
EMAIL_CONTINUE_SELECTOR=button:has-text("继续")
CODE_INPUT_SELECTOR=input[name="code"], input[autocomplete="one-time-code"], input:visible
CODE_CONTINUE_SELECTOR=button:has-text("继续")
NAME_INPUT_SELECTOR=input[placeholder="姓名"], input[name="name"]
AGE_INPUT_SELECTOR=input[placeholder="年龄"], input[name="age"]
COMPLETE_BUTTON_SELECTOR=button:has-text("完成账户创建")

# 成功判断
SUCCESS_URL_KEYWORD=
SUCCESS_TEXT_KEYWORD=
```

如果网页可以加 `data-testid`，建议改成更稳定的选择器：

```html
<button data-testid="free-register-button">免费注册</button>
<input data-testid="email-input" />
<button data-testid="email-continue-button">继续</button>
<input data-testid="code-input" />
<button data-testid="code-continue-button">继续</button>
<input data-testid="name-input" />
<input data-testid="age-input" />
<button data-testid="complete-account-button">完成账户创建</button>
```

对应配置：

```text
FREE_REGISTER_SELECTOR=[data-testid="free-register-button"]
EMAIL_INPUT_SELECTOR=[data-testid="email-input"]
EMAIL_CONTINUE_SELECTOR=[data-testid="email-continue-button"]
CODE_INPUT_SELECTOR=[data-testid="code-input"]
CODE_CONTINUE_SELECTOR=[data-testid="code-continue-button"]
NAME_INPUT_SELECTOR=[data-testid="name-input"]
AGE_INPUT_SELECTOR=[data-testid="age-input"]
COMPLETE_BUTTON_SELECTOR=[data-testid="complete-account-button"]
```

## 8. 随机身份生成

### 8.1 姓名

第一版可以使用简单中文名池：

```text
姓氏：赵、钱、孙、李、周、吴、郑、王、冯、陈、刘、杨、黄、林
名字：明、华、伟、芳、娜、敏、强、磊、军、洋、静、杰、涛、超
```

生成示例：

```text
李明
王磊
陈静
```

也支持配置固定姓名：

```text
DEFAULT_NAME=
```

如果为空则随机生成。

### 8.2 年龄

默认随机年龄范围：

```text
18-45
```

配置：

```text
MIN_AGE=18
MAX_AGE=45
```

## 9. 邮箱池设计

### 9.1 emails.txt

每行一个邮箱：

```text
test001@hotmail.com
test002@hotmail.com
test003@hotmail.com
```

### 9.2 使用规则

```text
1. 如果 API 请求传入 email，则使用该 email
2. 如果未传入 email，则从 emails.txt 取下一个未成功使用的邮箱
3. 注册成功后标记该邮箱 used
4. 注册失败后标记 failed，可支持重试
```

第一版不需要复杂锁机制，因为批量注册按顺序执行。

## 10. 数据库设计

### 10.1 register_jobs 表

```sql
CREATE TABLE IF NOT EXISTS register_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    email TEXT NOT NULL,
    name TEXT,
    age INTEGER,
    status TEXT NOT NULL,
    verification_code TEXT,
    error_message TEXT,
    screenshot_path TEXT,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER
);
```

状态枚举：

```text
pending
running
success
failed
timeout
```

### 10.2 registered_accounts 表

```sql
CREATE TABLE IF NOT EXISTS registered_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    age INTEGER,
    source_job_id TEXT,
    created_at INTEGER NOT NULL
);
```

### 10.3 email_usage 表

```sql
CREATE TABLE IF NOT EXISTS email_usage (
    email TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    last_job_id TEXT,
    last_error TEXT,
    updated_at INTEGER NOT NULL
);
```

邮箱状态：

```text
unused
used
failed
locked
```

## 11. API 设计

### 11.1 健康检查

```http
GET /health
```

响应：

```json
{
  "code": 1,
  "msg": "ok"
}
```

### 11.2 启动单次注册

```http
POST /api/register/start
Header: x-api-key: <API_KEY>
Content-Type: application/json
```

请求：

```json
{
  "url": "https://your-register-url.example.com",
  "email": "test001@hotmail.com",
  "headless": false,
  "timeout": 180
}
```

字段说明：

```text
url：可选；不传则使用 REGISTER_URL
email：可选；不传则从 emails.txt 获取
headless：可选；默认读取配置
timeout：验证码等待超时时间，默认 180 秒
```

响应：

```json
{
  "code": 1,
  "msg": "started",
  "data": {
    "job_id": "job_20260604_001"
  }
}
```

### 11.3 批量注册

```http
POST /api/register/batch
Header: x-api-key: <API_KEY>
Content-Type: application/json
```

请求：

```json
{
  "url": "https://your-register-url.example.com",
  "limit": 10,
  "headless": false,
  "timeout": 180
}
```

响应：

```json
{
  "code": 1,
  "msg": "started",
  "data": {
    "batch_id": "batch_20260604_001",
    "jobs": [
      "job_001",
      "job_002"
    ]
  }
}
```

### 11.4 查询任务列表

```http
GET /api/jobs
Header: x-api-key: <API_KEY>
```

响应：

```json
{
  "code": 1,
  "msg": "success",
  "data": [
    {
      "job_id": "job_001",
      "email": "test001@hotmail.com",
      "status": "success",
      "created_at": 1780000000,
      "finished_at": 1780000100
    }
  ]
}
```

### 11.5 查询任务详情

```http
GET /api/jobs/{job_id}
Header: x-api-key: <API_KEY>
```

响应：

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "job_id": "job_001",
    "url": "https://your-register-url.example.com",
    "email": "test001@hotmail.com",
    "name": "李明",
    "age": 25,
    "status": "success",
    "verification_code": "123456",
    "error_message": "",
    "screenshot_path": "",
    "created_at": 1780000000,
    "started_at": 1780000001,
    "finished_at": 1780000100
  }
}
```

### 11.6 查询成功账号

```http
GET /api/accounts
Header: x-api-key: <API_KEY>
```

响应：

```json
{
  "code": 1,
  "msg": "success",
  "data": [
    {
      "email": "test001@hotmail.com",
      "name": "李明",
      "age": 25,
      "created_at": 1780000100
    }
  ]
}
```

## 12. Playwright 自动化细节

### 12.1 浏览器上下文

每个任务使用独立无痕上下文：

```python
browser = await playwright.chromium.launch(headless=headless)
context = await browser.new_context()
page = await context.new_page()
```

任务完成后关闭：

```python
await context.close()
await browser.close()
```

### 12.2 操作步骤

伪代码：

```python
await page.goto(url, wait_until="domcontentloaded")

await page.locator(FREE_REGISTER_SELECTOR).click()

await page.locator(EMAIL_INPUT_SELECTOR).fill(email)
await page.locator(EMAIL_CONTINUE_SELECTOR).click()

verification_code = mail_code_client.wait_code(email=email, timeout=180)

await page.locator(CODE_INPUT_SELECTOR).fill(verification_code)
await page.locator(CODE_CONTINUE_SELECTOR).click()

await page.locator(NAME_INPUT_SELECTOR).fill(name)
await page.locator(AGE_INPUT_SELECTOR).fill(str(age))
await page.locator(COMPLETE_BUTTON_SELECTOR).click()

await check_success(page)
```

### 12.3 成功判断

支持两种配置方式：

```text
SUCCESS_URL_KEYWORD：URL 包含该关键词则成功
SUCCESS_TEXT_KEYWORD：页面出现该文本则成功
```

如果两者都为空，则采用宽松判断：

```text
点击“完成账户创建”后等待 5 秒，无明显错误即标记 success_candidate
```

建议用户后续配置明确成功标志。

### 12.4 失败截图

失败时自动保存截图：

```text
screenshots/{job_id}_error.png
```

可选保存 Playwright trace：

```text
traces/{job_id}.zip
```

## 13. 安全要求

```text
1. 所有 /api/* 接口必须使用 x-api-key 鉴权
2. 默认只监听 127.0.0.1
3. 不记录邮箱服务 API Key
4. 不在日志中输出完整验证码服务响应敏感字段
5. screenshots 和 traces 不提交 Git
6. emails.txt 和 .env 不提交 Git
7. 不实现任何绕过人机验证、滑块、风控的逻辑
```

## 14. 运行方式

### 14.1 安装依赖

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 14.2 配置环境变量

复制：

```bash
cp .env.example .env
```

修改：

```text
REGISTER_URL=https://你的公网注册地址
MAIL_CODE_SERVICE_BASE=http://127.0.0.1:5050
MAIL_CODE_SERVICE_API_KEY=你的邮箱服务 API Key
API_KEY=你的注册自动化服务 API Key
```

### 14.3 启动服务

```bash
uvicorn app.main:app --host 127.0.0.1 --port 5060
```

PowerShell：

```powershell
.\run.ps1
```

### 14.4 启动单次注册

```powershell
$headers = @{
  "x-api-key" = "你的注册服务 API Key"
  "Content-Type" = "application/json"
}

$body = @{
  url = "https://你的公网注册地址"
  email = "test001@hotmail.com"
  headless = $false
  timeout = 180
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Uri "http://127.0.0.1:5060/api/register/start" `
  -Method Post `
  -Headers $headers `
  -Body $body
```

## 15. 第一版不做的功能

```text
1. 不做并发注册
2. 不做代理池
3. 不做图形验证码识别
4. 不做滑块验证绕过
5. 不做复杂 Web 管理后台
6. 不保存浏览器 Cookie
```

## 16. 后续可扩展功能

```text
1. Web 管理后台
2. 并发注册队列
3. 邮箱占用/释放机制
4. Playwright trace 可视化下载
5. 任务重试
6. Docker 部署
7. 远程 API 调用权限控制
8. IP 白名单
```