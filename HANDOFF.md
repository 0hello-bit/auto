# HANDOFF — 网页注册 + 邮箱验证码 + 5sim 接码 + Sub2API 自动导入

> 给接手者的一份「拿来即用」说明。读完这份 + `操作文档.md` 即可独立运行、排错、二次开发。
> **本文件不含任何密钥/令牌明文**；所有密钥都在各项目的 `.env` / `accounts.txt`（已 git 忽略），接手者需填自己的。

---

## 1. 这套系统做什么

把「**注册一个 ChatGPT/OpenAI 账号 → 邮箱收验证码 → 手机接码验证 → 把账号导入 Sub2API（建号、绑分组）**」整个过程自动化，一条 API 调用跑完。

数据流：

```
你 ──HTTP──> 项目B(5060) ──驱动──> 本机Chrome(CDP 9222) ──> chatgpt.com / auth.openai.com
                  │                                            （过 Cloudflare 用真实浏览器）
                  ├──取邮箱验证码──> 项目A(5050) ──IMAP/OAuth──> Outlook 邮箱
                  ├──接手机码──────> 5sim.net（走系统代理）
                  └──建号──────────> Sub2API(8080, Docker)
```

| 组件 | 作用 | 地址 | 启动 |
| --- | --- | --- | --- |
| 项目专用 Chrome | 真实浏览器过 Cloudflare，独立 profile，不读日常 Cookie | CDP `127.0.0.1:9222` | run-all.ps1 |
| 项目 A `owned-mail-code-service` | 收 OpenAI 邮件、按规则取 6 位验证码 | `127.0.0.1:5050` | run-all.ps1 |
| 项目 B `web-register-sub2api-automation` | 编排全流程、驱动浏览器、调 5sim / Sub2API | `127.0.0.1:5060` | run-all.ps1 |
| 5sim | 手机接码（买号、收短信）| `5sim.net` | 在线 |
| Sub2API（weishaw/sub2api）| 管理 API、建号 | `127.0.0.1:8080` | Docker |

---

## 2. 接手者必须自备（替换成你自己的）

1. **本机 Chrome**（路径见 `run-all.ps1` 顶部 `$CHROME`）。
2. **Outlook 邮箱 + OAuth refresh_token**：填进 `owned-mail-code-service/accounts.txt`（每行一个账号，含 token）。这些邮箱就是注册用的「邮箱池」，同时要写进 `web-register-sub2api-automation/emails.txt`。
3. **5sim 账号 token**：填进项目 B `.env` 的 `FIVESIM_TOKEN`，账号要有余额。
4. **Sub2API 实例**：`D:\sub2api-deploy` 的 docker，管理员邮箱/密码填进项目 B `.env` 的 `SUB2API_ADMIN_EMAIL/PASSWORD`。
5. **两个 API Key**：项目 A 的 `API_KEY` 必须 **等于** 项目 B 的 `MAIL_CODE_SERVICE_API_KEY`；项目 B 自己的 `API_KEY` 是你调 `/api/*` 用的 `x-api-key`。

---

## 3. 首次安装（每个组件一次）

```powershell
# 项目 A
cd F:\auro-reg\owned-mail-code-service
python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env     # 编辑：API_KEY、accounts.txt
# 项目 B
cd F:\auro-reg\web-register-sub2api-automation
python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium   # 兜底
Copy-Item .env.example .env     # 编辑：见 §4 关键项
# Sub2API
cd D:\sub2api-deploy; docker compose up -d
```

---

## 4. `.env` 关键项（项目 B）

| 配置 | 说明 |
| --- | --- |
| `API_KEY` | 调项目 B `/api/*` 的 `x-api-key` |
| `MAIL_CODE_SERVICE_API_KEY` | **必须等于项目 A 的 `API_KEY`** |
| `BROWSER_MODE=cdp` / `CDP_ENDPOINT=http://127.0.0.1:9222` / `CDP_CONTEXT_POLICY=incognito` | 连本机 Chrome，每任务新无痕 context |
| `NO_PROXY=127.0.0.1,localhost` | 让本机服务绕过系统代理（Clash/V2Ray 会拦 localhost）|
| `EMAIL_CODE_SUBJECT_KEYWORD=ChatGPT,OpenAI` | **见 §7 头号坑**：注册码主题含 ChatGPT、授权登录码主题含 OpenAI，必须都收 |
| `EMAIL_CODE_FROM_KEYWORD=openai` / `EMAIL_CODE_PATTERN=...(\d{6})` | 取码来源 + 6 位码正则 |
| `FIVESIM_TOKEN` / `FIVESIM_PRODUCT=openai` / `FIVESIM_COUNTRY` / `FIVESIM_OPERATOR(_STRATEGY)` | 接码配置（见 §7 5sim）|
| `SUB2API_BASE` / `SUB2API_ADMIN_EMAIL` / `SUB2API_ADMIN_PASSWORD` / `SUB2API_DEFAULT_GROUP_IDS=2` | Sub2API 管理 API（JWT 登录）、self 分组 id |
| `CODE_TIMEOUT_SECONDS=300` / `SMS_TIMEOUT_SECONDS=180` / `SMS_MAX_PHONE_ATTEMPTS=3` | 各超时与换号重试次数 |

---

## 5. 运行与调用

```powershell
cd F:\auro-reg; .\run-all.ps1            # 一键起 Chrome+A+B（已在跑的跳过），末尾打印 OK/DOWN
.\run-all.ps1 -Stop                      # 只关 A/B（不关 Chrome/Sub2API）
```

调用（`$h` 里的 key = 项目 B `.env` 的 `API_KEY`）：

```powershell
$base="http://127.0.0.1:5060"
$h=@{ "x-api-key"="<项目B .env 的 API_KEY>"; "Content-Type"="application/json" }

# 一键：注册+接码+导入（country 留空=自动选成功率最高国家）
$b=@{ url="https://chatgpt.com/"; email="you@hotmail.com"; enable_sms=$true; sms_provider="5sim"; group_ids=@(2) } | ConvertTo-Json
irm "$base/api/register-and-import/start" -Method Post -Headers $h -Body $b

# 续传：把「已注册成功但没进 Sub2API」的邮箱补做导入（自动重新邮箱登录接码）
irm "$base/api/imports/resume" -Method Post -Headers $h -Body (@{limit=1;enable_sms=$true;sms_provider="5sim";group_ids=@(2)}|ConvertTo-Json)

# 查询
irm "$base/api/emails"  -Headers $h | ConvertTo-Json -Depth 6   # 邮箱池状态
irm "$base/api/jobs"    -Headers $h | ConvertTo-Json -Depth 6
irm "$base/api/accounts" -Headers $h | ConvertTo-Json -Depth 6
irm "$base/api/sub2api/groups" -Headers $h | ConvertTo-Json -Depth 6
# 5sim 预览（不下单、不扣费）
irm "$base/api/sms/5sim/operators/best?country=argentina&product=openai&strategy=highest_success" -Headers $h
```

接口清单：**`/api/auto/start`（推荐：自动判断注册 or 续传导入）**、**`/api/auto/run-batch`（按 emails.txt 顺序逐个流式跑完，先自动同步）**、**`/api/emails/sync`（从项目A accounts.txt 重建 emails.txt，只留未接入 Sub2API 的）**、`/api/auto/batches[/{id}]`（查批次）、`/api/register-and-import/start`、`/api/register/start`（仅注册）、`/api/sub2api/import/start`（仅导入，会自动走「重新邮箱登录」）、`/api/imports/resume`（批量续传 registered 邮箱）、`/api/emails`、`/api/jobs[/{id}]`、`/api/accounts`、`/api/sub2api/groups`、`/api/sms/{providers,profile}`、`/api/sms/5sim/{products,operators/best}`。

> 一键流式：`.\run-all.ps1 -Auto`（起服务 → 同步 emails.txt → 启动 run-batch 逐个邮箱跑）。

---

## 6. 邮箱生命周期（核心概念，务必理解）

```
(无)  ──取出──>  in_use  ──网页注册成功──>  registered  ──Sub2API导入成功──>  used
                  │                          │
                  └── 任一步失败(账号还不存在) ──> failed（可重试，会重新发放）
```

- **`registered` = 已注册、但还没进 Sub2API**：不会再被拿去重新注册（账号已存在），但可用 `POST /api/imports/resume` **续传**补导入。
- **`used` = 注册并已导入 Sub2API**（彻底用完）。**只有导入成功才会标 used**——这样导入失败不会「烧掉」邮箱。
- 取邮箱前会查 Sub2API 现有账号去重；已在 Sub2API 的邮箱自动对齐为 `used`、不再使用。

---

## 7. 头号坑 / 已知风险（接手前必读）

1. **邮箱主题过滤（最隐蔽，曾导致「验证码没自动填」）**：注册码邮件主题是「你的临时 **ChatGPT** 登录代码」，但授权页"重新登录"的码邮件主题是「你的临时 **OpenAI** 登录代码」，都来自 `noreply@tm.openai.com`。`EMAIL_CODE_SUBJECT_KEYWORD` 必须是 `ChatGPT,OpenAI`（逗号=OR），否则授权登录取不到码、项目 A 返回 408（现象：项目 A 已收到邮件，B 却卡住）。项目 A `poller.find_code` 已支持逗号 OR。
2. **不要在注册成功时就标 used**（见 §6）；导入失败要能续传。这是 `finalize_register_success`(标 registered) 与 `perform_import`(成功才标 used) 的约定，改代码时别破坏。
3. **5sim 成功率/价格随时段波动很大**：旧文档说 argentina/openai ~5%，但实测某些时段 argentina/virtual62 高达 ~48%、$0.04/个。下单前用 5sim `guest/prices?product=openai`（或 `/api/sms/5sim/operators/best`）看实时 rate/价/库存再选国家。**失败的号会自动 cancel→退款**，所以低余额也能多试几次。
4. **手机验证不保证自动成功**（OpenAI 防滥用）。收不到可用码会自动换号重试 `SMS_MAX_PHONE_ATTEMPTS` 次；仍不行就**人工用自己的号**完成手机验证，回调捕获+建号会自动收尾。
5. **localhost 必须绕过系统代理**：`.env` 要有 `NO_PROXY=127.0.0.1,localhost`，否则连不上本机 A/Sub2API。
6. **Outlook IMAP 偶发限流**（XOAUTH2 "authenticated but not connected"）：已放宽轮询，高频跑仍可能被微软限流，等几分钟冷却。
7. **注册页与授权页用同一个无痕 context**（保留登录态），别拆开。
8. **OAuth 回调**程序自动从网络抓 `/auth/callback?code=&state=`（无需手动 F12），并**校验 state**，不一致立即失败。

---

## 8. 排错 / 调试工具

- **日志**：`run-all.ps1` 为 A/B 各开一个窗口实时打日志；或自己 `python -m app.main > logs/projX.log 2>&1` 重定向。
- **截图**：失败自动存 `web-register-sub2api-automation/screenshots/`；trace 存 `traces/<task>.zip`（用 `playwright show-trace`）。
- **实时看当前页**：`web-register-sub2api-automation/inspect_live.py [label]` —— 连 CDP Chrome，dump 当前 OpenAI 页的标题/输入框/按钮/正文并截图到 `screenshots/_inspect_<label>.png`。（控制台中文是 GBK 乱码，看截图为准。）
- **直接验证取码**：`POST http://127.0.0.1:5050/api/code`（项目 A），带 `subject_keyword/from_keyword/pattern/since`；先 `GET /api/messages?email_addr=...` 看实际收到的邮件。

| 现象 | 处理 |
| --- | --- |
| 取码 401 | 项目 B 的 `MAIL_CODE_SERVICE_API_KEY` ≠ 项目 A 的 `API_KEY` |
| 取码 408（但 A 已收到邮件）| 见 §7-1：主题关键词没含 OpenAI；或 `since` 把旧邮件挡了 |
| 验证码没自动填 | 见 §7-1（主因）；浏览器侧已加固「等待码页+取到码必填」 |
| 注册成功没进 Sub2API | 邮箱现为 `registered`，`POST /api/imports/resume` 续传；`GET /api/emails` 看状态 |
| 5sim `not enough balance` / `no free phones` | 充值 / 换国家或运营商（自动回退 any）|
| 某项 DOWN | 看对应窗口日志；Sub2API DOWN → `cd D:\sub2api-deploy; docker compose up -d` |

---

## 9. 当前已验证状态（2026-06-05）

- ✅ **完整流程实测跑通**：`AlfBorquist5281@outlook.com` 续传 → 重新邮箱登录(验证码自动填) → 5sim argentina/virtual62 手机验证(首次成功) → 回调捕获 → **Sub2API 建号 `account_id=6`（self 组、active）**。
- ✅ 两个历史 bug 已修复（§7-1 主题过滤；§6 邮箱生命周期）。
- 剩 `FandelKerzman554@hotmail.com` 状态 `registered`，可直接 `/api/imports/resume` 跑完。

### 2026-06-06 实跑修复（注册 + OAuth）

实跑中验证并修复（详见 `CLAUDE.md` 约定 6/7 与 `app/sub2api_import_worker.py`）：

1. **注册页「完成帐户创建」按钮**：真实页面是 **完成帐户创建**（帐 U+5E10），不是文档里的 **完成账户创建**（账 U+8D26）！`COMPLETE_BUTTON_SELECTOR` 现同时收两种写法并用 `户创建` 兜底；点击改为重试+「离开姓名/年龄页即成功」，不再 60s 死等。
2. **OAuth state 过滤**：`OAuthCallbackCapture` 现在**只接受 state == expected_state 的回调**，避免抓到同一 Chrome 里其它/手动打开的授权流的回调（曾导致 `OAuth state mismatch`）。**自动化跑的时候别再手动开别的授权链接。**
3. **第二次手机验证 → 重开授权链接**：第一次手机验证过了又弹第二次时，不再买新号，而是**用 Sub2API 重新生成的 auth_url 重开页面**重跑（`OAUTH_MAX_ATTEMPTS` 默认 3 次）。
4. **consent 页**（「同意使用 Codex」/授权）在手机环节前先点「继续」。
5. **手机环节卡死/人工干预**：回调为最高优先级退出信号，等码与回调赛跑（人工完成 ~0.1s 接管，不再死等 180s）；无发码选项页直接点继续；WhatsApp-only 默认取消换号。
6. **IMAP 993 被代理挡**：现象=OAuth(443) 通但 IMAP `outlook.office365.com:993` 报 `SSL: UNEXPECTED_EOF_WHILE_READING`。`imaplib` 不走 HTTP 代理直连，部分 Clash 节点(Clash Verge)路由不了 993；换能路由的节点/客户端(Clash for Windows)即可。项目A 新增 `IMAP_PROXY`（HTTP CONNECT 隧道，默认 direct）+ 连接重试。
7. **项目A 只在启动时读 `accounts.txt`**：新增邮箱后要**重启项目A**（或 POST `/api/accounts/import`），否则 `/api/code` 返回 404 account not found。
8. **邮箱健康预检**：`POST /api/accounts/check {email}`（项目A）秒级返回 `{oauth_ok, imap_ok, abuse, healthy}`，下单/跑流程前先验号，别浪费 5sim。
9. **5sim 选运营商很关键**：实测 `france/virtual51` 仅 9.5% 成功率/库存 2（会报 `no free phones`），`argentina/virtual62` 40%/库存 732/$0.04（推荐）。可在 `/api/auto/start` 请求体里传 `fivesim_country/fivesim_operator/fivesim_operator_strategy` 临时覆盖 `.env`。

### 2026-06-06 完整流程再次实测跑通

`MunlinZaeske828@hotmail.com` 全新注册 → **Sub2API `account_id=9`**：网页注册(完成帐户创建/帐) → 登录 → OAuth 选账号 → 重新邮箱登录取码 → consent「同意使用 Codex」自动继续 → 5sim argentina/virtual62 手机验证(首次成功) → 回调(state 过滤) → Sub2API 建号。以上 7 处修复全部实跑验证通过。

### 2026-06-06（续）OAuth 阶段自愈 + 再次跑通 account_id=11

按 `接码.md` 新增流程加固 OAuth 阶段（`app/sub2api_import_worker.py`，`perform_import` 外层 `for oauth_attempt in range(OAUTH_MAX_ATTEMPTS=5)` + `capture.reset()` + 重新 goto 新 auth_url）：
- **图6「使用 ChatGPT 登录到 Codex」后两种情况**：B 直接回调（已支持）；**A 弹图7「查看你的手机」第二道验证**（收不到的码）→ `_is_check_phone_page` 识别 → **重开 Sub2API 授权链接**重走。
- **流程回退到「登录或注册」起点**（手机失败 `go_back` 过头）→ `_is_oauth_start_page` 识别 → 同样**重开授权链接**换号重试。
- **回调只认本流程 state**（`OAuthCallbackCapture.consider` 过滤）；**跑自动化时别手动开别的授权链接**，否则会抓错回调。
- 实测 `PanzaSzollosi2316@hotmail.com` → **Sub2API `account_id=11`**（argentina/virtual62 首次过、图6 后直接回调；图7/起点回退是安全网，已单测，本轮 happy path 未触发）。
- 运维坑：项目A 加新邮箱后**必须重启A**才加载（否则 `/api/code` 404）；多次后台重启 A/B 会残留僵尸进程占端口，乱了按端口杀 `app.main` 重启；Chrome(9222) 被关 → 注册 `ECONNREFUSED`，重开 Chrome 即可。

---

## 10. 代码地图

```
F:\auro-reg\
├─ run-all.ps1                     一键启动/停止
├─ 操作文档.md / 流程说明.md / 接码.md / 操作流程.md   流程与图解（接码.md 含 6 张图两种情况）
├─ HANDOFF.md (本文) / CLAUDE.md   接手说明 / 项目记忆（AI 自动加载）
├─ owned-mail-code-service/        项目A：app/{api,poller,imap_client,code_extractor,microsoft_oauth,...}.py
└─ web-register-sub2api-automation/ 项目B
   └─ app/
      ├─ api.py / job_service.py / models.py        路由 / 编排 / 请求体
      ├─ register_worker.py                          阶段一：网页注册 + 共享 Playwright 助手
      ├─ sub2api_import_worker.py                    阶段二：OAuth 授权+手机验证+建号（情况1/2、换号重试）
      ├─ register_and_import_worker.py               合并流程（同一 context）
      ├─ email_pool.py / database.py                 邮箱池(生命周期) / SQLite
      ├─ sms_*_client.py / sms_provider_*.py         5sim / 62us（可切换接码平台）
      ├─ sub2api_client.py / oauth_network_capture.py Sub2API 管理API / 回调抓取
      └─ browser_manager.py / config.py              CDP/Playwright / 全部 .env 配置
```
