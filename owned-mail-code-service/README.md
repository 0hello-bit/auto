# Owned Mail Verification Code Service

自有邮箱验证码接收服务。批量接入**你自己拥有或已授权管理**的 Hotmail / Outlook 邮箱，后台实时轮询邮件，自动提取验证码，并通过带鉴权的 HTTP API 提供给你自己的业务 / 测试系统调用。

> ⚠️ **合规边界**：本项目仅用于自有邮箱、自有系统、授权测试环境与内部自动化流程。**不得**用于未授权读取他人邮箱、第三方平台批量注册、绕过第三方验证码 / 风控，或对外出售验证码读取服务。

---

## 功能特性

- 批量导入邮箱账号（本地文件 + API），格式 `email----password----client_id----refresh_token`
- Microsoft OAuth2 `refresh_token` 换 `access_token`（带内存缓存，自动按需刷新）
- IMAP `XOAUTH2` 登录 Outlook / Office 365，**只读**方式拉取邮件（不修改已读状态）
- 解析邮件主题、发件人、纯文本正文、HTML 正文
- 正则自动提取验证码，默认 `\b\d{4,8}\b`，支持调用时自定义
- 后台 daemon 线程定时轮询（默认 20 秒），可手动触发
- 邮件与验证码落库 SQLite，按 `(email, message_id)` 去重
- 记录每个邮箱最近轮询时间与错误信息
- 全部 `/api/*` 接口使用 `x-api-key` 鉴权；响应中**绝不返回** `password / refresh_token / access_token`
- 默认只监听 `127.0.0.1`

---

## 目录结构

```text
owned-mail-code-service/
├── app/
│   ├── __init__.py
│   ├── main.py            # FastAPI 入口 + 启动/关闭生命周期
│   ├── config.py          # 环境变量配置
│   ├── models.py          # dataclass / pydantic 模型
│   ├── account_parser.py  # 账号行解析（split("----", 3)）
│   ├── microsoft_oauth.py # refresh_token -> access_token（带缓存）
│   ├── imap_client.py     # IMAP XOAUTH2 登录与拉取邮件
│   ├── mail_parser.py     # 邮件 MIME 解析
│   ├── code_extractor.py  # 验证码正则提取
│   ├── database.py        # SQLite 初始化与读写
│   ├── poller.py          # 后台轮询 + 单邮箱轮询 + 验证码查找
│   └── api.py             # API 路由 + 鉴权
├── accounts.example.txt
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
├── run.ps1                # Windows 一键启动
└── tests/
```

---

## 1. 安装

要求 Python 3.10+。

### Windows（一键）

```powershell
.\run.ps1
```

`run.ps1` 会自动创建虚拟环境、安装依赖、从 `.env.example` 生成 `.env`、并启动服务。

### 手动安装（任意平台）

```bash
python -m venv .venv
# Windows:  . .\.venv\Scripts\Activate.ps1
# Linux/macOS:  source .venv/bin/activate
pip install -r requirements.txt
```

---

## 2. 配置

复制示例配置并修改：

```bash
cp .env.example .env      # Windows: Copy-Item .env.example .env
```

务必设置一个**强随机** `API_KEY`：

```text
API_KEY=change-this-to-a-strong-secret
ACCOUNTS_FILE=accounts.txt
DB_FILE=mail_codes.db
POLL_INTERVAL_SECONDS=20
LATEST_MAIL_LIMIT=20
IMAP_HOST=outlook.office365.com
IMAP_PORT=993
TOKEN_URL=https://login.microsoftonline.com/consumers/oauth2/v2.0/token
IMAP_SCOPE=https://outlook.office.com/IMAP.AccessAsUser.All offline_access
HOST=127.0.0.1
PORT=5050
```

生成强密钥示例：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 3. 启动服务

```bash
uvicorn app.main:app --host 127.0.0.1 --port 5050
```

或直接运行模块（读取 `.env` 里的 HOST/PORT）：

```bash
python -m app.main
```

启动后健康检查：

```bash
curl http://127.0.0.1:5050/health
# {"code":1,"msg":"ok"}
```

交互式文档：<http://127.0.0.1:5050/docs>

---

## 4. 导入邮箱账号

账号格式（每行一个）：

```text
email----password----client_id----refresh_token
```

> `refresh_token` 可能包含 `----` 等特殊字符，解析时**只切前 3 个分隔符**（`line.split("----", 3)`），token 不会被截断。

### 方式 A：本地文件

把账号写入 `accounts.txt`（参考 `accounts.example.txt`），重启服务即自动加载。

### 方式 B：API 导入

```bash
curl -X POST http://127.0.0.1:5050/api/accounts/import \
  -H "x-api-key: change-this-to-a-strong-secret" \
  -H "Content-Type: application/json" \
  -d '{"text":"a@hotmail.com----pass----client_id----refresh_token\nb@hotmail.com----pass----client_id----refresh_token"}'
```

响应：

```json
{"code":1,"msg":"success","data":{"imported":2,"errors":[]}}
```

---

## 5. 获取验证码

```bash
curl -X POST http://127.0.0.1:5050/api/code \
  -H "x-api-key: change-this-to-a-strong-secret" \
  -H "Content-Type: application/json" \
  -d '{"email":"user@hotmail.com","timeout":180,"pattern":"\\b\\d{6}\\b"}'
```

逻辑：先查 SQLite 已有邮件 → 没有则主动轮询该邮箱 → 每 5 秒重试 → 超过 `timeout` 返回 408。

成功：

```json
{"code":1,"msg":"success","data":{"email":"user@hotmail.com","verification_code":"123456"}}
```

超时（HTTP 408）：

```json
{"code":408,"msg":"timeout","data":{"email":"user@hotmail.com"}}
```

可选过滤字段：`subject_keyword`（标题关键词）、`from_keyword`（发件人关键词）、`pattern`（自定义正则，默认 `\b\d{4,8}\b`）。`subject_keyword` / `from_keyword` 支持逗号 OR 列表（如 `ChatGPT,OpenAI`）：命中任一关键词即匹配——ChatGPT 注册码邮件主题含「ChatGPT」，而 OpenAI 授权登录码邮件主题含「OpenAI」，需同时接受。

> 说明：`/api/code` 会返回**最新一封**匹配到的验证码（含历史邮件）。请在触发发送验证码之后再调用，以避免拿到旧验证码。

---

## 6. 其他接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET  | `/health` | 健康检查（无需鉴权） |
| POST | `/api/accounts/import` | 批量导入账号 |
| GET  | `/api/accounts` | 邮箱列表（不含敏感字段） |
| POST | `/api/poll` | 立即手动轮询所有邮箱 |
| POST | `/api/code` | 获取验证码（阻塞等待） |
| GET  | `/api/messages?email_addr=...` | 最近 30 封邮件摘要（不含正文） |

查看邮箱状态：

```bash
curl http://127.0.0.1:5050/api/accounts -H "x-api-key: change-this-to-a-strong-secret"
```

```json
{"code":1,"msg":"success","data":[
  {"email":"user@hotmail.com","client_id":"xxx","has_refresh_token":true,"last_poll_at":1780000000,"last_error":""}
]}
```

手动触发轮询：

```bash
curl -X POST http://127.0.0.1:5050/api/poll -H "x-api-key: change-this-to-a-strong-secret"
# {"code":1,"msg":"success","data":{"ok":1,"failed":[]}}
```

查询最近邮件摘要：

```bash
curl "http://127.0.0.1:5050/api/messages?email_addr=user@hotmail.com" \
  -H "x-api-key: change-this-to-a-strong-secret"
```

未授权统一返回（HTTP 401）：

```json
{"code":401,"msg":"unauthorized"}
```

---

## 7. 运行测试

```bash
pytest -q
```

覆盖：账号行解析、refresh_token 特殊字符不截断、验证码正则提取、API 鉴权、SQLite 去重、`/api/code` 超时。

---

## 8. 安全须知

- **不要**把 `accounts.txt`、`.env`、`mail_codes.db`、`*.log` 上传到 GitHub（已在 `.gitignore`）。
- 日志中不会打印完整 `refresh_token / access_token / password`。
- 所有 `/api/*` 必须带 `x-api-key`；未设置 `API_KEY` 时所有接口一律拒绝。
- 默认只监听 `127.0.0.1`。如需公网访问，请额外配置 **HTTPS + 强 API Key + IP 白名单 + 反向代理访问控制**。

---

## 9. 常见错误排查

| 现象 | 可能原因 / 处理 |
| --- | --- |
| `/api/accounts` 返回 401 | `x-api-key` 不正确，或 `.env` 未设置 `API_KEY` |
| `last_error` 含 `invalid_grant` | `refresh_token` 失效 / 已过期 / 与 `client_id` 不匹配，需要重新获取 token |
| `last_error` 含 `AUTHENTICATE failed` / `XOAUTH2 auth failed` | scope 不含 IMAP 权限、账号未开启 IMAP、或 access_token 无效；确认 `IMAP_SCOPE` 正确 |
| `last_error` 含 `IMAP connect failed` | 网络 / 防火墙问题，确认能访问 `outlook.office365.com:993` |
| `/api/code` 一直 408 | 邮件还没到、`pattern` 不匹配、或被 `subject_keyword` / `from_keyword` 过滤掉；先用 `/api/messages` 看看实际收到的邮件 |
| `imported` 为 0 | 账号行格式不对，必须是 4 段、用 `----` 分隔；查看返回的 `errors` |
| 启动日志提示 `API_KEY is not set` | 在 `.env` 设置 `API_KEY` 后重启 |

---

## 10. 关于 client_id / refresh_token

本服务消费的是 Microsoft OAuth2 的 `client_id` 与 `refresh_token`（针对个人 / 消费者账号的 `consumers` 端点）。你需要事先通过自己的 Azure 应用注册与授权流程，为你**拥有或授权管理**的邮箱获取这些凭据。本项目只负责用它们刷新 token 并通过 IMAP 读取邮件，不包含获取 refresh_token 的授权流程。 
