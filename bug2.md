请帮我修改 web-register-sub2api-automation 项目，增加一个统一自动入口：

POST /api/auto/start

目标：
我现在有两个入口：
1. /api/register-and-import/start：全新邮箱注册 + 邮箱验证码 + 5sim 接码 + 导入 Sub2API
2. /api/imports/resume：对 registered 状态邮箱续传导入 Sub2API

现在使用很麻烦，需要人工判断邮箱状态。请新增一个统一接口 /api/auto/start，让后端自动判断该走哪个流程。

现有邮箱生命周期：
- 无记录 / failed：可以重新作为新邮箱注册
- in_use：任务正在使用，不应重复使用
- registered：网页注册成功，但还没有导入 Sub2API，应该自动走 resume/import
- used：已经导入 Sub2API，不能再使用

重要约定：
1. 注册成功后只能标记为 registered。
2. 只有 Sub2API 导入成功后才能标记为 used。
3. 不要破坏现有 /api/register-and-import/start、/api/imports/resume、/api/emails、/api/jobs、/api/accounts 接口。
4. 新增 /api/auto/start，作为推荐入口。
5. 新接口内部复用现有 job_service / email_pool / database 逻辑，不要大面积重写 worker。

自动判断逻辑：

请求体建议支持：
{
  "url": "https://chatgpt.com/",
  "email": null,
  "enable_sms": true,
  "sms_provider": "5sim",
  "group_ids": [2],
  "concurrency": 10,
  "priority": 1,
  "limit": 1
}

如果请求里有 email：
1. 查询 email_usage / registered_accounts / Sub2API 已有账号。
2. 如果该 email 已经 used 或已经存在于 Sub2API：
   返回 code=0 或 code=1 with status="already_used"，不要启动任务。
3. 如果该 email 状态是 registered：
   自动启动 import-only / resume 流程。
4. 如果该 email 状态是 in_use：
   返回 status="in_use"，提示有任务正在运行。
5. 如果该 email 状态是 failed 或没有记录：
   自动启动 register-and-import 完整流程。

如果请求里没有 email：
1. 先查是否存在 registered 状态邮箱。
2. 如果有 registered 邮箱：
   自动调用 resume_registered_imports，limit 默认 1。
3. 如果没有 registered 邮箱：
   从 emails.txt 选一个全新可用邮箱，执行 register-and-import 完整流程。
4. 如果没有任何可用邮箱：
   返回清晰错误：
   "没有可用邮箱：没有 registered 可续传邮箱，也没有新邮箱可注册"

返回结果需要明确告诉我本次自动选择了哪个流程：
{
  "code": 1,
  "msg": "auto job scheduled",
  "data": {
    "mode": "resume_import | register_and_import | already_used | in_use",
    "email": "...",
    "register_job_id": "...",
    "import_job_id": "...",
    "reason": "..."
  }
}

请修改这些文件：
1. app/models.py
   - 新增 AutoStartRequest / AutoStartResponse，或者复用现有请求模型。
2. app/job_service.py
   - 新增 auto_start(req) 逻辑。
   - 复用已有 register-and-import 和 resume import 的函数。
3. app/api.py
   - 新增 POST /api/auto/start 路由。
4. 如需要，app/email_pool.py 或 app/database.py
   - 新增 get_email_usage(email)、list_registered_emails(limit)、is_email_used_or_imported(email) 之类的小函数。
5. 保持原有接口兼容。

请同时给出：
1. 完整修改代码片段。
2. 需要新增的函数。
3. 测试命令。
4. PowerShell 调用命令。