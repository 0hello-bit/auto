# Web Register And Sub2API Import Automation

网页注册与 Sub2API 自动导入服务。

自动完成 **你自己拥有或已获授权** 的网页项目注册流程：网页邮箱注册 → （授权阶段）可切换短信接码平台做手机号验证 → 捕获 OAuth `callback code/state` → 调用本地部署的 **Sub2API** 管理接口创建 OAuth 账号并绑定分组。

- 项目 A（邮箱验证码服务）已完成，本项目 **不实现** 任何邮箱收件 / IMAP / Outlook OAuth / 邮件轮询，只通过 HTTP 调用项目 A 获取 6 位邮箱验证码。
- 短信接码平台抽象为统一接口，支持 `62us` / `5sim` **自由切换**（环境变量或单次请求覆盖）。

> ⚠️ **合规声明**：本项目仅用于 **你本人拥有或已获明确授权** 的网页项目、你自己的邮箱、你自己购买的接码号码、以及你自己部署的 Sub2API。项目 **不包含、也不会实现** 任何滑块 / 人机验证 / 图形验证码破解或检测规避逻辑。请勿用于未授权目标。

---

## 目录

- [架构要点](#架构要点)
- [技术栈与目录结构](#技术栈与目录结构)
- [安装](#安装)
- [配置（.env）](#配置env)
- [启动](#启动)
- [使用本机 Chrome（CDP 模式）](#使用本机-chrome-cdp-模式)
- [62-US 配置示例](#62-us-配置示例)
- [5sim 配置示例](#5sim-配置示例)
- [API 一览](#api-一览)
- [PowerShell 调用示例](#powershell-调用示例)
- [完整自动化流程](#完整自动化流程)
- [数据库与任务状态](#数据库与任务状态)
- [关于 62-US 接口的说明](#关于-62-us-接口的说明)
- [安全要求](#安全要求)
- [常见错误排查](#常见错误排查)

---

## 架构要点

1. **短信平台统一抽象**：业务流程（workers）只依赖 `SmsProvider` 抽象类与 `SmsOrder` 数据类，绝不直接 import 具体平台。
   - `app/sms_provider_base.py`：`SmsProvider`（ABC）+ `SmsOrder`（dataclass）+ `extract_code()`。
   - `app/sms_provider_factory.py`：根据 `SMS_PROVIDER`（可被请求覆盖）返回 `Us62Provider` 或 `FiveSimProvider`。
   - `app/sms_62us_client.py` / `app/sms_5sim_client.py`：具体实现。
2. **同一个无痕浏览器上下文**：网页注册页 `page_register` 与授权页 `page_auth` 使用同一个 Playwright `browser context`，保留注册阶段产生的 cookie / 登录态。
3. **Network 捕获 code/state**：不仅依赖页面跳转。通过监听 `request` / `response` / `framenavigated` / `requestfailed`，只要任意 URL 含 `/auth/callback?code=` 就立刻捕获 `code` / `state` / `callback_url`（即使 `redirect_uri` 指向本机未监听端口、页面加载失败也能捕获）。
4. **OAuth state 校验**：从 `auth_url` 解析 `expected_state`，从 callback 解析 `captured_state`，不一致立即失败，**不会**调用 `create-from-oauth`。
5. **短信订单生命周期**：成功并完成导入后（`SMS_AUTO_FINISH_ORDER=true`）调用 finish；失败/超时/state 不一致/创建失败时（`SMS_AUTO_CANCEL_ON_FAIL=true`）调用 cancel。finish/cancel 失败只写日志，**不会覆盖**原始任务失败原因。

---

## 技术栈与目录结构

Python 3.10+ · FastAPI · Uvicorn · Playwright（Chromium）· SQLite · requests · python-dotenv

```text
web-register-sub2api-automation/
├── app/
│   ├── __init__.py
│   ├── main.py                      # FastAPI app + 启动入口
│   ├── config.py                    # .env 加载与类型转换、密钥脱敏
│   ├── models.py                    # Pydantic 请求模型 + {code,msg,data} 信封
│   ├── database.py                  # SQLite 建表与读写
│   ├── email_pool.py                # emails.txt + email_usage 邮箱池
│   ├── mail_code_client.py          # 调用项目 A 获取邮箱验证码
│   ├── sms_provider_base.py         # SmsProvider 抽象 + SmsOrder
│   ├── sms_provider_factory.py      # 根据 SMS_PROVIDER 选择 provider
│   ├── sms_62us_client.py           # 62-US 实现
│   ├── sms_5sim_client.py           # 5sim 实现
│   ├── sub2api_client.py            # Sub2API 管理接口封装
│   ├── oauth_network_capture.py     # 抓取 /auth/callback?code=&state=
│   ├── identity_generator.py        # 随机中文姓名 + 18-45 岁
│   ├── register_worker.py           # 网页注册 + 共享 Playwright 助手
│   ├── sub2api_import_worker.py     # 授权 + 接码 + 回调 + 创建账号
│   ├── register_and_import_worker.py# 一键：注册+导入（同一 context）
│   ├── job_service.py               # 编排、参数解析、后台任务调度
│   └── api.py                       # FastAPI 路由 + x-api-key 鉴权
├── emails.example.txt
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
├── run.ps1
├── screenshots/                     # 失败截图（git 忽略）
├── traces/                          # Playwright trace（git 忽略）
└── data/                            # SQLite 数据库（git 忽略）
```

---

## 安装

### 一键安装（推荐，Windows / PowerShell）

```powershell
cd web-register-sub2api-automation
.\run.ps1 -Setup
```

`-Setup` 会：创建 `.venv` → 安装 `requirements.txt` → 安装 Playwright Chromium → 从 `.env.example` 复制出 `.env`、从 `emails.example.txt` 复制出 `emails.txt`。

### 手动安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt

# Playwright 浏览器安装命令（必须执行一次）
playwright install chromium

Copy-Item .env.example .env
Copy-Item emails.example.txt emails.txt
```

> **Playwright 安装命令**：`playwright install chromium`
> 若使用 venv：`.\.venv\Scripts\python.exe -m playwright install chromium`

随后编辑 `.env`（填入各项密钥、`REGISTER_URL`、接码平台配置）与 `emails.txt`（每行一个你自己的邮箱）。

---

## 配置（.env）

复制 `.env.example` 为 `.env` 后修改。关键项：

| 变量 | 说明 |
| --- | --- |
| `API_KEY` | 本服务的 x-api-key，所有 `/api/*` 必须携带 |
| `HOST` / `PORT` | 默认 `127.0.0.1:5060` |
| `REGISTER_URL` | 目标注册页 URL（请求未传 `url` 时使用） |
| `HEADLESS` | `false` 可观察浏览器；`true` 无头 |
| `BROWSER_TIMEOUT_MS` | 单个元素/操作默认超时（毫秒） |
| `CODE_TIMEOUT_SECONDS` | 邮箱验证码等待超时（秒） |
| `MAIL_CODE_SERVICE_BASE` / `MAIL_CODE_SERVICE_API_KEY` | 项目 A 地址与密钥（KEY 必须等于项目 A 的 `API_KEY`） |
| `EMAIL_CODE_PATTERN` / `EMAIL_CODE_SUBJECT_KEYWORD` / `EMAIL_CODE_FROM_KEYWORD` | 邮箱验证码提取规则（透传项目 A）；默认锁定 ChatGPT/OpenAI 登录码，捕获组 `(\d{6})` 即 6 位码 |
| `ENABLE_SMS` | 授权阶段是否启用手机接码 |
| `SMS_PROVIDER` | `62us` 或 `5sim` |
| `SMS_CODE_PATTERN` | 短信验证码正则，默认 `\b\d{6}\b` |
| `SMS_TIMEOUT_SECONDS` / `SMS_POLL_INTERVAL_SECONDS` | 短信等待超时 / 轮询间隔 |
| `SMS_AUTO_FINISH_ORDER` / `SMS_AUTO_CANCEL_ON_FAIL` | 成功 finish / 失败 cancel 开关 |
| `SUB2API_BASE` | Sub2API(weishaw/sub2api) 地址（默认 `http://127.0.0.1:8080`） |
| `SUB2API_ADMIN_EMAIL` / `SUB2API_ADMIN_PASSWORD` | Sub2API 管理员账号（JWT 登录；= sub2api 的 `ADMIN_EMAIL`/`ADMIN_PASSWORD`） |
| `SUB2API_PLATFORM` / `SUB2API_ACCOUNT_TYPE` | 建号平台/类型，默认 `openai` / `oauth` |
| `SUB2API_REDIRECT_URI` | OAuth 回调地址（默认 `http://localhost:1455/auth/callback`） |
| `SUB2API_DEFAULT_GROUP_IDS` / `_CONCURRENCY` / `_PRIORITY` | 创建账号默认值（如 openai 的 self 分组 id） |
| `MIN_AGE` / `MAX_AGE` | 随机年龄范围（默认 18–45） |
| `FREE_REGISTER_SELECTOR` … `SMS_CODE_CONTINUE_SELECTOR` | 各阶段 Playwright 选择器，全部从 .env 读取，不硬编码 |
| `SUCCESS_URL_KEYWORD` / `SUCCESS_TEXT_KEYWORD` | 可选的注册成功判断 |

**选择器说明**：每个 `*_SELECTOR` 的值可以写多个用英文逗号分隔的 CSS 选择器，程序会按顺序把每个片段当作 **备选项** 逐个尝试，命中第一个「可见（且按钮可点击）」的元素。请避免在单个 `:has-text("a, b")` 里写英文逗号。

`timeout` 请求参数语义：
- `/api/register/start` 的 `timeout` = 邮箱验证码等待超时；
- `/api/sub2api/import/start`、`/api/register-and-import/start` 的 `timeout` = 整个授权阶段超时（含等待短信）。

---

## 启动

```powershell
# 方式一：脚本（自动使用 .venv）
.\run.ps1

# 方式二：模块入口（读取 .env 的 HOST/PORT）
python -m app.main

# 方式三：uvicorn
uvicorn app.main:app --host 127.0.0.1 --port 5060
```

启动后：健康检查 `http://127.0.0.1:5060/health`，交互式文档 `http://127.0.0.1:5060/docs`。

---

## 使用本机 Chrome（CDP 模式）

默认 `BROWSER_MODE=playwright`，用 Playwright 自带 Chromium。也可以改用 **你本机安装的 Google Chrome**（连真实浏览器，指纹更“正常”），且 **不读取你日常浏览器的 Cookie/登录态** —— 通过一个 **项目专用 profile** + **每个任务新建无痕 context** 实现。

所有 worker 都通过 `app/browser_manager.py` 统一获取浏览器（`get_browser_and_context` / `cleanup_browser`），不再各自启动浏览器。

### 1) 手动启动项目专用 Chrome（独立 profile）

```powershell
$CHROME = "C:\Program Files\Google\Chrome\Application\chrome.exe"

& $CHROME `
  --remote-debugging-port=9222 `
  --user-data-dir="D:\chrome-sub2api-automation-profile" `
  --no-first-run `
  --no-default-browser-check
```

- `D:\chrome-sub2api-automation-profile` 是**项目专用 profile**，与你日常 Chrome 完全隔离。
- 它**不会读取**你日常 Chrome 的 Cookie / 登录态 / localStorage。
- 项目每个任务会**新建一个无痕 context**；任务结束 `context.close()`，Cookie/localStorage/sessionStorage 都不保留。
- 默认**不关闭**整个 Chrome 进程（`CDP_CLOSE_BROWSER=false`）。

### 2) 项目 B `.env` 推荐配置

```env
BROWSER_MODE=cdp
CDP_ENDPOINT=http://127.0.0.1:9222
CDP_CONTEXT_POLICY=incognito
CDP_CLOSE_BROWSER=false
HEADLESS=false
```

> 若系统设置了 `HTTP_PROXY/HTTPS_PROXY`（如 Clash/V2Ray），请确保 `NO_PROXY` 含 `127.0.0.1,localhost`，否则 Playwright 连本机 `:9222` 的 CDP 会被代理拦截。

### 3) `CDP_CONTEXT_POLICY` 两种策略

| 值 | 行为 |
| --- | --- |
| `incognito`（默认推荐）| 每个任务新建 `new_context()`，不读已有 Cookie；任务结束关闭并清空 |
| `first` | 复用 `browser.contexts[0]`，保留已登录 profile；该共享 context 不会被关闭 |

想复用登录态就把 `CDP_CONTEXT_POLICY` 改成 `first`；默认建议 `incognito`。

> `playwright` 模式不受影响、完全保留；`HEADLESS` 仅对 `playwright` 模式生效（CDP 连的是你已启动的可见 Chrome）。

---

## 62-US 配置示例

```text
ENABLE_SMS=true
SMS_PROVIDER=62us

US62_BASE=https://api.62-us.com
US62_API_KEY=你的-62us-key
US62_GOODS_ID=12-1-7
```

> `ENABLE_SMS=true` 且 `SMS_PROVIDER=62us` 时，`US62_GOODS_ID` 为空会 **明确报错**：`SMS_PROVIDER=62us 但 US62_GOODS_ID 未配置`。

## 5sim 配置示例

```text
ENABLE_SMS=true
SMS_PROVIDER=5sim

FIVESIM_BASE=https://5sim.net
FIVESIM_TOKEN=你的-5sim-token
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

> `ENABLE_SMS=true` 且 `SMS_PROVIDER=5sim` 时：
> - `FIVESIM_PRODUCT` 为空且请求未传 `fivesim_product` → **明确报错**：`FIVESIM_PRODUCT 未配置，且请求中没有传 fivesim_product`
> - `FIVESIM_COUNTRY` 为空且请求未传 `fivesim_country` → **明确报错**：`FIVESIM_COUNTRY 未配置，且请求中没有传 fivesim_country`
>
> `FIVESIM_MAX_PRICE` 仅在最终运营商为 `any` 时作为 `maxPrice` 生效。
> 5sim 返回的号码为国际格式（如 `+54...`）。若目标网页只接受本地号码，请在 `sms_5sim_client.py` 中按需裁剪国家码。

**切换平台**：改 `.env` 的 `SMS_PROVIDER`，或在单次请求里传 `"sms_provider": "62us" | "5sim"` 覆盖。

### 5sim 服务（product）与运营商（operator）选择

下单需要三个参数：`country`（国家）、`product`（服务，如 google/telegram/facebook）、`operator`（运营商）。**product / country 由你自由选择，operator 默认由系统按策略自动挑选成功率最高的。**

**1) 查看 5sim 支持哪些服务**（不下单、不扣费）：

```powershell
# country / operator 不传则用 .env 默认值
Invoke-RestMethod -Uri "$base/api/sms/5sim/products?country=argentina&operator=any" -Headers $headers | ConvertTo-Json -Depth 6
```

返回 `data.products`，每项含 `product / price / count / rate`。
注意：5sim 的 products 接口本身**不返回成功率**，`rate` 是从 prices 接口按「该服务下各运营商的最高成功率」补充而来；若补充失败则为 `null`（属正常，可改用 `/operators/best` 查看成功率）。`data.raw` 保留 5sim 原始返回，便于按实际结构调整解析。

**2) 设置默认服务**：在 `.env` 写 `FIVESIM_COUNTRY` 和 `FIVESIM_PRODUCT`（示例默认 `argentina` / `google`）。

**3) 在 API 请求中临时选择服务**：`/api/sub2api/import/start` 与 `/api/register-and-import/start` 支持以下字段覆盖 `.env`（不传则用 `.env`）：

```json
{
  "sms_provider": "5sim",
  "fivesim_country": "argentina",
  "fivesim_product": "telegram",
  "fivesim_operator_strategy": "highest_success",
  "fivesim_operator": "any",
  "fivesim_max_price": 0.5
}
```

**4) 预览成功率最高的运营商**（不下单、不扣费）：

```powershell
Invoke-RestMethod -Uri "$base/api/sms/5sim/operators/best?country=argentina&product=google&strategy=highest_success" -Headers $headers | ConvertTo-Json -Depth 6
```

返回 `data.selected`（最终选中的运营商及其 `price / success_rate / count`）和 `data.candidates`（按策略排序的候选列表）。

**运营商选择策略 `FIVESIM_OPERATOR_STRATEGY` / `fivesim_operator_strategy`**：

| 策略 | 说明 |
| --- | --- |
| `highest_success` | 成功率最高（默认；同率再比库存、价格） |
| `lowest_price` | 价格最低 |
| `most_available` | 库存最多 |
| `manual` | 使用 `FIVESIM_OPERATOR` / `fivesim_operator` 指定的运营商 |

过滤项：`FIVESIM_MIN_SUCCESS_RATE`（最低成功率）、`FIVESIM_MIN_COUNT`（最低库存）、`FIVESIM_EXCLUDE_OPERATORS`（排除运营商，逗号分隔）、`FIVESIM_MAX_PRICE`（最高价格）。若全部候选被过滤掉，则回退到 `FIVESIM_OPERATOR_FALLBACK`（默认 `any`）。

> **重要**：`/api/sms/5sim/operators/best` 与 `/api/sms/5sim/products` 仅做预览，**不会下单、不会扣费**。真正下单只发生在注册/导入流程页面需要手机号时（`/v1/user/buy/activation/{country}/{operator}/{product}`）。下单使用的 `country / operator / product` 即最终解析出来的值，**operator 不写死、product 不写死**。

---

## API 一览

所有 `/api/*` 必须携带请求头 `x-api-key: <API_KEY>`。统一返回 `{ "code": 1, "msg": "...", "data": ... }`，出错时 `code=0` 并返回相应 HTTP 状态码（鉴权失败 401、参数/配置错误 400、未找到 404）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查（无需鉴权），返回 `{"code":1,"msg":"ok"}` |
| POST | `/api/register/start` | 仅执行网页邮箱注册 |
| POST | `/api/sub2api/import/start` | 仅执行 Sub2API 授权导入 |
| POST | `/api/register-and-import/start` | 一键：注册 + 接码 + 导入 |
| GET | `/api/jobs?limit=50` | 最近任务列表（注册 + 导入） |
| GET | `/api/jobs/{job_id}` | 任务详情 |
| GET | `/api/accounts` | 成功注册并导入的账号 |
| GET | `/api/sub2api/groups` | 调用 Sub2API `groups/all`，确认 group_ids |
| GET | `/api/sms/providers` | 当前平台与可用平台 |
| GET | `/api/sms/profile?provider=` | 当前（或指定）平台余额/状态 |
| GET | `/api/sms/5sim/products?country=&operator=` | 查询 5sim 某国家/运营商支持的服务（预览，不扣费） |
| GET | `/api/sms/5sim/operators/best?country=&product=&strategy=` | 预览最佳运营商（预览，不下单不扣费） |

> **单独导入（`/api/sub2api/import/start`）的限制**：它会开一个全新的无痕上下文，**没有** 注册阶段的登录态。若目标 OAuth 授权页需要先登录网站，单独导入可能停在登录页而无法自动完成。**全自动闭环请使用 `/api/register-and-import/start`**（注册与授权共用同一上下文）。单独导入适合调试 Sub2API 侧，或授权页不依赖网站会话的场景。

---

## PowerShell 调用示例

```powershell
$base = "http://127.0.0.1:5060"
$headers = @{ "x-api-key" = "change-this-register-service-key"; "Content-Type" = "application/json" }
```

### 12. 查询 Sub2API 分组 ID

```powershell
Invoke-RestMethod -Uri "$base/api/sub2api/groups" -Headers $headers | ConvertTo-Json -Depth 6
```

### 13. 查询当前短信平台状态

```powershell
# 当前 / 可用平台
Invoke-RestMethod -Uri "$base/api/sms/providers" -Headers $headers | ConvertTo-Json -Depth 6

# 当前平台余额/状态（62us 调 /api/v1/info，5sim 调 /v1/user/profile）
Invoke-RestMethod -Uri "$base/api/sms/profile" -Headers $headers | ConvertTo-Json -Depth 6

# 指定平台
Invoke-RestMethod -Uri "$base/api/sms/profile?provider=5sim" -Headers $headers | ConvertTo-Json -Depth 6
```

### 14. 单独注册

```powershell
$body = @{
  url      = "https://your-public-register-url.example.com"
  email    = "test001@hotmail.com"   # 省略则从 emails.txt 顺序取未使用邮箱
  headless = $false
  timeout  = 180                       # 邮箱验证码等待超时（秒）
} | ConvertTo-Json

Invoke-RestMethod -Uri "$base/api/register/start" -Method Post -Headers $headers -Body $body
```

### 15. 单独 Sub2API 导入

```powershell
$body = @{
  email        = "test001@hotmail.com"
  name         = "test001@hotmail.com"
  group_ids    = @(1)
  concurrency  = 10
  priority     = 1
  headless     = $false
  enable_sms   = $true
  sms_provider = "5sim"
} | ConvertTo-Json

Invoke-RestMethod -Uri "$base/api/sub2api/import/start" -Method Post -Headers $headers -Body $body
```

### 16. 一键注册并导入

使用 **62-US**：

```powershell
$body = @{
  url          = "https://your-public-register-url.example.com"
  email        = "test001@hotmail.com"
  headless     = $false
  timeout      = 300
  enable_sms   = $true
  sms_provider = "62us"
  group_ids    = @(1)
  concurrency  = 10
  priority     = 1
} | ConvertTo-Json

Invoke-RestMethod -Uri "$base/api/register-and-import/start" -Method Post -Headers $headers -Body $body
```

使用 **5sim**：把上面 `sms_provider` 改成 `"5sim"` 即可（其余不变）。

### 查询任务 / 账号

```powershell
Invoke-RestMethod -Uri "$base/api/jobs" -Headers $headers | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "$base/api/jobs/<job_id>" -Headers $headers | ConvertTo-Json -Depth 6
Invoke-RestMethod -Uri "$base/api/accounts" -Headers $headers | ConvertTo-Json -Depth 6
```

> 一键接口返回 `register_job_id` 与 `import_job_id`，两者都可用 `/api/jobs/{job_id}` 查询。

---

## 完整自动化流程

```text
1.  打开 REGISTER_URL（或请求传入的 url）
2.  点击「免费注册」
3.  输入邮箱
4.  点击「继续」
5.  调用项目 A 获取 6 位邮箱验证码
6.  填入邮箱验证码
7.  点击「继续」
8.  填写随机中文姓名 + 18–45 岁年龄
9.  点击「完成账户创建」
10. 调用 Sub2API generate-auth-url，得到 auth_url 和 session_id
11. 从 auth_url 解析 expected_state
12. 在同一 browser context 新开页面打开 auth_url
13. 自动点击授权页「继续」
14. 若出现手机号输入框：
      - 按 SMS_PROVIDER 选择 62-US 或 5sim
      - 下单号码 → 填入手机号 → 点击继续/发送验证码
      - 轮询短信平台 → 提取 6 位验证码 → 回填 → 点击继续
15. 监听授权页 URL / request / response
16. 捕获 /auth/callback?code=...&state=...
17. 校验 captured_state == expected_state
18. 一致则调用 Sub2API create-from-oauth
19. 创建账号并绑定 group_ids
20. 成功后 finish 短信订单
21. 失败/超时后 cancel 短信订单
22. 保存任务结果到 SQLite，失败截图存 screenshots/
```

第一版 **不做** 并发注册；**不做** 滑块/图形验证码破解。

---

## 数据库与任务状态

SQLite 文件默认 `data/register_jobs.db`，表：`register_jobs`、`sub2api_import_jobs`、`registered_accounts`、`email_usage`（结构见设计文档/`app/database.py`）。

`register_jobs` 额外记录本次短信选择的明细（5sim 自动选号时写入，启动时自动迁移补列）：`sms_provider`、`sms_country`、`sms_product`、`sms_operator`、`sms_operator_strategy`、`sms_price`、`sms_success_rate`、`sms_order_id`、`phone_number`、`sms_verification_code`。这些字段同时会打印到日志（一行汇总），便于审计每次下单用了哪个国家/服务/运营商及价格成功率。

- 注册任务状态：`pending` → `running` → `success` / `failed` / `timeout`
- 导入任务状态：`pending` → `generating_auth_url` → `waiting_auth` → `waiting_phone` → `waiting_sms` → `callback_captured` → `importing` → `success` / `failed` / `timeout`
- 邮箱状态：`in_use` / `used` / `failed`（`failed` 可被重新取用）

---

## 关于 62-US 接口的说明

62-US 没有公开的 API 文档（接口说明在登录后的账户后台），因此 `app/sms_62us_client.py` 中的 **请求字段名与响应字段名是基于端点语义的合理推测**，并做了「多候选字段名 + 兼容 `{code,msg,data}` 信封 / 扁平结构」的防御式解析：

- 下单：`POST /api/v1/get`（请求体默认 `{"goods_id": <US62_GOODS_ID>}`）→ 取 `order_id`；
- 取号：`GET /api/v1/order/tokens?order_id=...` → 取 `phone` / `number` 与 `token`；
- 轮询：`GET /api/v1/msg?order_id=...&token=...` → 从短信文本用 `SMS_CODE_PATTERN` 提取验证码。

**若你账户的真实字段名不同**，请按后台文档修改 `sms_62us_client.py` 里 `_pick(...)` 的候选 key 或请求体字段。

**finish / cancel**：你提供的 62-US 端点列表中 **没有** finish/cancel 接口，因此 `finish_order()` / `cancel_order()` 实现为 **no-op（仅写日志）**。如果你的账户其实有结单/释放接口，请在该文件中补充实现。5sim 则使用真实的 `/v1/user/finish/{id}` 与 `/v1/user/cancel/{id}`。

---

## 安全要求

- 所有 `/api/*` 接口强制 `x-api-key` 鉴权；默认仅监听 `127.0.0.1`。
- `API_KEY` / `MAIL_CODE_SERVICE_API_KEY` / `US62_API_KEY` / `FIVESIM_TOKEN` / `SUB2API_ADMIN_PASSWORD` 只从环境变量读取；日志中不打印完整密钥/`token`（见 `config.mask_secret`），也不打印 `code` / `refresh_token` / `access_token`。
- `.gitignore` 已忽略 `.env`、`emails.txt`、`data/*.db`、`screenshots/*`、`traces/*`、`*.log`。

---

## 常见错误排查

| 现象 / 报错 | 原因与处理 |
| --- | --- |
| `401 invalid or missing x-api-key` | 请求头缺少或不匹配 `x-api-key`，需等于 `.env` 的 `API_KEY` |
| `no register URL provided and REGISTER_URL is empty` | 请求未传 `url` 且未配置 `REGISTER_URL` |
| `SMS_PROVIDER=62us 但 US62_GOODS_ID 未配置` | 启用 62us 但未填 `US62_GOODS_ID` |
| `FIVESIM_PRODUCT 未配置，且请求中没有传 fivesim_product` | 启用 5sim 但 `.env` 与请求都没有 product |
| `FIVESIM_COUNTRY 未配置，且请求中没有传 fivesim_country` | 启用 5sim 但 `.env` 与请求都没有 country |
| 本机服务（项目A 5050 / Sub2API 8080）连不上，但服务确实在跑 | 多为系统代理（Clash/V2Ray 等 `HTTP_PROXY`）拦截了 localhost。本项目已对 `127.0.0.1/localhost` 目标自动绕过代理；若仍异常，设置环境变量 `NO_PROXY=127.0.0.1,localhost` 再启动 |
| `emails file not found` | 复制 `emails.example.txt` 为 `emails.txt` 并填入邮箱 |
| `no unused email available` | `emails.txt` 中邮箱都已 `in_use/used`；新增邮箱或在 DB `email_usage` 中重置状态 |
| `mail-code service ...` / 超时 | 确认项目 A 在 `MAIL_CODE_SERVICE_BASE` 运行、`MAIL_CODE_SERVICE_API_KEY` 与项目A 的 `API_KEY` 一致、邮箱已接入项目 A |
| `Executable doesn't exist ... playwright install` | 执行 `playwright install chromium`（venv 内：`.\.venv\Scripts\python.exe -m playwright install chromium`） |
| `selector not ready: ...` | 目标站点 DOM 与默认选择器不符，按真实页面修改 `.env` 中对应 `*_SELECTOR`；可设 `HEADLESS=false` 观察 |
| `authorization callback not captured within timeout` | 授权页卡住/需要额外交互；增大请求 `timeout`、检查授权页选择器、确认 `SUB2API_REDIRECT_URI` 与 Sub2API 实际一致；用 `traces/<job>.zip` 复盘 |
| `OAuth state mismatch` | `auth_url` 与 callback 的 `state` 不一致；通常是会话/redirect_uri 不一致，按设计这会中止导入 |
| `Sub2API HTTP 4xx/5xx` 或登录失败 | 确认 Sub2API 已起、`SUB2API_ADMIN_EMAIL`/`SUB2API_ADMIN_PASSWORD` 与 sub2api 的 `ADMIN_EMAIL`/`ADMIN_PASSWORD` 一致；用 `/api/sub2api/groups` 验证连通性 |
| 5sim `no free phones` / `not enough user balance` | 换 `fivesim_product`/`fivesim_country`/策略或充值；用 `/api/sms/5sim/operators/best` 先看库存与成功率；`maxPrice` 仅在 operator=any 时生效 |
| 5sim `/products` 的 `rate` 为 `null` | products 接口不含成功率属正常；成功率在 `/operators/best` 或 prices 接口里 |
| 62-US 取号/取码失败 | 多半是字段名不符，按后台文档调整 `sms_62us_client.py`（见上节） |
| Windows 上 Playwright `NotImplementedError` | 不要把事件循环改成 `SelectorEventLoop`；本项目使用默认（Proactor）循环，按 README 启动即可 |
| 单独导入停在登录页 | 单独导入是全新无痕上下文、无登录态；请改用一键 `/api/register-and-import/start` |

---

### 调试产物

- 失败截图：`screenshots/<job_id>_*_<ts>.png`
- Playwright trace：`traces/<trace_name>.zip`，可用 `playwright show-trace traces/xxx.zip` 打开复盘。
