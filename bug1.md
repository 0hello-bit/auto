你是资深 Python/FastAPI/Playwright 自动化工程师。请帮我修改项目：

F:\auro-reg\web-register-sub2api-automation

背景：
项目 B 负责 ChatGPT/OpenAI 注册、OAuth 授权、5sim 手机接码、导入 Sub2API。
现在手机号验证页面有两种 UI：

1. selectable 模式：
页面有 “Text Message / SMS / 短信” 和 “WhatsApp” 两个选项。
这种情况下应该优先选择 Text Message/SMS/短信。

2. whatsapp_only 模式：
页面没有发送方式选项，文案类似：
“我们会通过 WhatsApp 向该号码发送一次性验证码进行验证”
或英文：
“via WhatsApp”
这种情况下页面默认只会通过 WhatsApp 发码。

当前问题：
5sim 当前购买的是普通 OpenAI SMS 接码订单：

FIVESIM_PRODUCT=openai

这类订单通常只能收到普通短信验证码，不能收到 WhatsApp 消息。
所以如果页面进入 whatsapp_only 模式，代码不应该继续点继续并等待 SMS code，否则会一直 waiting_sms 到超时。

目标：
请新增配置项：

PHONE_ALLOW_WHATSAPP_FALLBACK=false

默认 false。

逻辑要求：

一、增加配置
在 app/config.py 增加：

phone_allow_whatsapp_fallback: bool

从环境变量 PHONE_ALLOW_WHATSAPP_FALLBACK 读取，默认 False。

.env.example 和必要文档也补充：

PHONE_ALLOW_WHATSAPP_FALLBACK=false

含义：
false = 当前 SMS provider / 5sim openai 订单不支持 WhatsApp 验证，遇到 WhatsApp-only 页面立即取消订单并换号。
true = 允许走 WhatsApp fallback。

二、增加或完善手机号发送方式检测函数

在 app/sub2api_import_worker.py 增加函数：

async def _detect_phone_delivery_mode(page) -> str:

返回值：
- "selectable"：页面存在 Text Message/SMS/短信 或 WhatsApp 可选按钮
- "whatsapp_only"：页面没有选项，但正文明确写通过 WhatsApp 发送验证码
- "default"：无法明确识别，按默认页面继续

需要兼容以下关键词：

短信选项：
- Text Message
- Text message
- SMS
- 短信
- 短消息
- 文本消息

WhatsApp 选项或文案：
- WhatsApp
- Whats App
- 通过 WhatsApp
- WhatsApp 向该号码
- via WhatsApp
- through WhatsApp
- send a one-time code via WhatsApp

三、修改 _handle_phone_verification 的接码逻辑

现有流程大概是：
1. 选择国家
2. buy_number
3. 填手机号
4. 选择 Text Message
5. 点击继续
6. 不成功再尝试 WhatsApp
7. 等待 sms_provider.wait_code

请修改成：

1. 选择国家
2. 购买 5sim 订单
3. 填手机号
4. 调用 _detect_phone_delivery_mode(page)

如果 delivery_mode == "selectable":
    - 优先选择 Text Message/SMS/短信
    - 点击继续并等待验证码输入页
    - 如果成功进入验证码页，继续 wait_code
    - 如果失败：
        - 如果 config.phone_allow_whatsapp_fallback == false：
            - cancel 当前 5sim order
            - 返回手机号页面 / 或继续下一次 attempt
            - 不等待短信码
        - 如果 config.phone_allow_whatsapp_fallback == true：
            - 尝试选择 WhatsApp
            - 点击继续
            - 如果进入验证码页，继续 wait_code

如果 delivery_mode == "whatsapp_only":
    - 如果 config.phone_allow_whatsapp_fallback == false：
        - 记录 warning 日志：
          "WhatsApp-only phone page but current SMS order does not support WhatsApp; cancel + new number"
        - cancel 当前 5sim order
        - 回到手机号页或进入下一次 attempt
        - continue
        - 不调用 sms_provider.wait_code
    - 如果 config.phone_allow_whatsapp_fallback == true：
        - 直接点击继续
        - 等待验证码输入页
        - 进入验证码页后才 wait_code

如果 delivery_mode == "default":
    - 不确定发送方式，按页面默认点击继续
    - 如果进入验证码页，继续 wait_code
    - 如果失败，cancel order + 换号

四、必须避免的问题

1. 不要在 whatsapp_only 且 PHONE_ALLOW_WHATSAPP_FALLBACK=false 时调用 sms_provider.wait_code。
2. 取消订单要用现有 _cancel_order_safe(sms_provider, order)。
3. 不能破坏原有换号重试逻辑 SMS_MAX_PHONE_ATTEMPTS。
4. 不要保存明文短信验证码、邮箱验证码、token、API key。
5. 继续保留 state 校验、OAuth callback 捕获逻辑。
6. 继续保持注册成功只标 registered，Sub2API 导入成功才标 used，不要破坏邮箱生命周期。

五、日志要求

请增加清晰日志：

- phone delivery mode detected: selectable / whatsapp_only / default
- selected phone delivery method: Text Message/SMS/短信
- SMS did not reach code page and WhatsApp fallback disabled; cancel + new number
- WhatsApp-only phone page but fallback disabled; cancel + new number
- WhatsApp fallback enabled; trying WhatsApp

六、需要输出

请给出：
1. app/config.py 需要新增/修改的代码。
2. app/sub2api_import_worker.py 需要新增的 _detect_phone_delivery_mode 函数完整代码。
3. _handle_phone_verification 中需要替换的代码块。
4. .env.example 需要新增的配置。
5. PowerShell 测试命令：
   - python -m py_compile app\config.py
   - python -m py_compile app\sub2api_import_worker.py
6. 简单说明修改后的行为。

请采用最小侵入式修改，不要重写整个项目，不要删除现有接口。