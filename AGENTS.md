# AGENTS.md — 项目记忆（auto-loaded）

> 自动化「ChatGPT/OpenAI 注册 → 邮箱验证码 → 5sim 手机接码 → 导入 Sub2API 建号」。
> 接手细节看 `HANDOFF.md`；流程图解看 `接码.md`/`操作流程.md`/`流程说明.md`；配置看 `操作文档.md`。

## 组件 / 端口
- 项目A `owned-mail-code-service` :5050 — 收 OpenAI 邮件、取 6 位码（IMAP+Outlook OAuth）。
- 项目B `web-register-sub2api-automation` :5060 — 编排全流程、驱动浏览器、调 5sim/Sub2API（FastAPI，`/api/*` 需 `x-api-key`）。
- 本机 Chrome CDP :9222（真实浏览器过 Cloudflare，独立 profile）；Sub2API :8080（Docker, `D:\sub2api-deploy`）。
- 启动：`F:\auro-reg\run-all.ps1`（`-Stop` 只关 A/B）。两项目各有 `.venv`；`python -m app.main` 起服务。

## 关键命令
```powershell
.\run-all.ps1                                   # 起 Chrome+A+B
# 日志重定向（调试时）: cd 到项目; .\.venv\Scripts\python.exe -m app.main > logs\projB.log 2>&1
.\.venv\Scripts\python.exe inspect_live.py lbl  # 项目B: 连CDP截图+dump当前OpenAI页 -> screenshots\_inspect_lbl.png
```
调 API：`x-api-key` = 项目B `.env` 的 `API_KEY`。**推荐入口 `POST /api/auto/start`**（自动判断该走注册还是续传导入）；批量流式 `POST /api/auto/run-batch`（按 emails.txt 顺序逐个跑完，先自动同步）；同步邮箱池 `POST /api/emails/sync`（从项目A `accounts.txt` 提取邮箱写回 `emails.txt`，只留未接入 Sub2API 的）；查批次 `GET /api/auto/batches[/{id}]`。旧入口仍在：注册+导入 `POST /api/register-and-import/start`；续传 `POST /api/imports/resume`；查池子 `GET /api/emails`。

一键流式：`.\run-all.ps1 -Auto`（起服务→同步 emails.txt→启动 run-batch 流式跑）。

## 必须遵守的约定（改代码别破坏）
1. **邮箱生命周期**：`in_use → registered(网页注册成功) → used(Sub2API导入成功)`；失败且账号还不存在=`failed`(可重试)；**`unavailable`=邮箱本身收不到码（refresh_token 失效 / AADSTS70000 service abuse mode），永久跳过、不再发放、不再续传**。
   - **只有导入成功才 `mark_used`**（在 `sub2api_import_worker.perform_import` 成功路径）。注册成功只 `mark_registered`（`register_worker.finalize_register_success`）。绝不要在注册成功时标 used，否则导入失败会烧掉邮箱。
   - `registered` 邮箱靠 `POST /api/imports/resume`（`job_service.resume_registered_imports` + `email_pool.list_resumable_emails`）续传补导入。
   - **接码服务报永久错误（abuse/invalid_grant）时标 `unavailable` 不是 `failed`**：项目A `/api/code` 遇 `poller.is_permanent_mailbox_error` 返回 **HTTP 409**；项目B `mail_code_client` 抛 `MailboxUnavailableError`，`finalize_register_failure` / `perform_import` 据此 `email_pool.mark_unavailable`。`emails.txt` 同步与发放都排除 `unavailable`。
2. **取邮箱码主题关键词必须 `ChatGPT,OpenAI`**（逗号=OR）：注册码邮件主题含 "ChatGPT"，授权页重新登录码邮件主题含 "OpenAI"（都来自 `noreply@tm.openai.com`）。只写 ChatGPT 会让授权登录取不到码→项目A 408（现象：A 收到邮件、B 卡住/验证码没自动填）。项目A `poller.find_code` 已支持 subject/from 逗号 OR。
3. **授权页两种情况**（`接码.md`）：情况1=有邮箱框→重新邮箱登录+取码自动填（`_handle_auth_email_login`，已加固：等待码页+必要时重点继续+取到码必填，绝不空提交）；情况2=账号卡片→点卡片（`_select_oauth_account`）。两者互斥。
4. **注册页与授权页用同一个无痕 context**（保留登录态）；回调 `/auth/callback?code=&state=` 由 `oauth_network_capture` 自动抓 + **校验 state**。
5. **本机服务调用要绕过系统代理**（`config.proxies_for` + `.env` `NO_PROXY=127.0.0.1,localhost`）。
6. **手机发码方式检测**（`bug1`+图4「Send code via」）：`_detect_phone_delivery_mode` 在**填号后**判页：`selectable`(有 Text Message/WhatsApp 选项)/`whatsapp_only`(只有 WhatsApp 文案)/`default`(没有任何发码选项)。
   - `selectable`(图4 情况一)：优先 Text Message；进不去码页且 `PHONE_ALLOW_WHATSAPP_FALLBACK=true` 才切 WhatsApp（默认 false→取消订单换号，因 5sim openai 短信单收不到 WhatsApp）。
   - `whatsapp_only` + fallback=false：直接 cancel+换号，绝不 `wait_code`。
   - `default`(图4 情况二，无 Send code via)：**直接点「继续」**进入收码，**绝不扫描不存在的发码按钮**——旧 `_select_phone_delivery` 对每个候选选择器轮询 2.5s×~37≈**90s**，表现就是「卡在填号页 / 点继续没反应」。已把 `_select_phone_delivery` 改成即时存在性判断（不轮询）。
7. **OAuth 回调是手机环节的最高优先级退出信号**：`_handle_phone_verification` 收 `capture`，等码用 `_wait_sms_or_callback`（SMS 与回调赛跑，回调先到立刻返回，不再死等 180s）；手机表单不在时**绝不买号**，而是 `_click_proceed` 点「继续」让回调触发；遇「你已准备就绪」等 ready/consent 页（`_is_ready_or_consent_page`/`_READY_PAGE_KEYWORDS`）直接点继续收尾。`perform_import` 主循环里手机环节报错但 `capture.captured` 为真时吞掉错误判成功（人工点继续也能被接住）。`AUTH_CONTINUE_SELECTOR` 已加 `[role=button]`/`Continue` 兜底。
   - **回调只认本流程 state**：`OAuthCallbackCapture.consider` 忽略 state≠expected 的回调（同一 Chrome 里别的/手动开的授权流不会被误抓）。跑自动化时别再手动开授权链接。
   - **OAuth 阶段"卡住/回退"统一靠重开授权链接自愈**（`perform_import` 外层 `for oauth_attempt in range(OAUTH_MAX_ATTEMPTS=5)` + `capture.reset()` + 重新 `goto(新 auth_url)`）。三个重开触发：① 图7「查看你的手机」(`_is_check_phone_page`，consent 后的第二道验证、收不到的码) ② 流程回退到「登录或注册」起点 (`_is_oauth_start_page`，手机失败 go_back 过头) ③ 第二次又弹手机号输入页(`phone_done` 时)。重开后走 选账号/重新邮箱登录 → 回调。
8. **`run-all.ps1` 必须存为 UTF-8 with BOM**：Windows PowerShell 5.1 把无 BOM 的 .ps1 当 GBK 解码，中文多字节会吞掉引号→脚本解析错乱、跳过「启动项目B」整块（现象：B 端口 DOWN、输出乱码 `鍏抽棴`、把 `-ForegroundColor`/`return` 当文本打印）。结构用 ASCII、中文只放双引号字符串里、存 BOM。改完用 PS AST `Parser::ParseFile` 验证无解析错误。

## 易错点
- 5sim 成功率/价格随时段大幅波动（实测 argentina/virtual62 曾 ~48%/$0.04，不是旧文档说的 5%）；下单前用 `guest/prices?product=openai` 或 `/api/sms/5sim/operators/best` 看实时数据。失败号自动 cancel→退款。
- 手机验证不保证自动成功（防滥用）；自动换号重试 `SMS_MAX_PHONE_ATTEMPTS` 次，仍不行可人工完成手机验证、其余自动收尾。
- Outlook IMAP 偶发限流，已放宽轮询，必要时冷却几分钟。
- inspect_live.py / sqlite 在 Windows 控制台打印中文是 GBK 乱码——读文件用 `encoding='utf-8'`，看截图判断 UI。

## 已验证
2026-06-05 全流程实测跑通：续传 `AlfBorquist5281@outlook.com` → Sub2API `account_id=6`（self 组=2, active）。两个历史 bug（主题过滤、邮箱生命周期）已修复。
2026-06-06 **整条流水线（含全新注册）再次实测跑通**：`MunlinZaeske828@hotmail.com` 网页注册(完成帐户创建/帐) → OAuth 选账号 → 重新邮箱登录 → consent 自动继续 → 5sim argentina/virtual62 手机验证(首次成功) → 回调(state 过滤) → **Sub2API `account_id=9`**。本轮 7 处修复（完成帐户创建字符变体、consent 页、OAuth state 过滤、第二次手机验证重开链接、abuse→unavailable、IMAP 代理、run-all BOM）全部验证。注意 5sim 运营商选 `argentina/virtual62`（40%/库存足），别用 `france/virtual51`（9.5%/库存 2→no free phones）；可在请求体临时覆盖 fivesim。邮箱先用 `POST /api/accounts/check` 验号。
2026-06-06 **再次实测跑通**：`PanzaSzollosi2316@hotmail.com` 注册→导入 → **Sub2API `account_id=11`**（argentina/virtual62 首次过、图6 consent 后直接回调）。本轮新增 OAuth 阶段自愈（图7「查看你的手机」/「登录或注册」回退 → 重开授权链接，`OAUTH_MAX_ATTEMPTS=5`，见约定7）；happy path 未触发重开（属安全网，逻辑已单测）。期间踩到的运维点：项目A 加新邮箱后**必须重启A**才会加载；多次后台重启 A/B 会残留僵尸进程占端口，乱了就按端口杀 `app.main` 重启；Chrome(9222) 被关掉会让注册 `ECONNREFUSED`，重开即可。
