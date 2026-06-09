~~~text
请继续优化项目 B：Web Register And Sub2API Import Automation。

背景：
当前项目默认使用 Playwright 自带 Chromium。现在我要改成可以使用我本机安装的 Google Chrome，但不能使用我日常浏览器里的原有 Cookie、登录态、localStorage。我要为项目单独启动一个 Chrome profile，并且每个自动化任务都创建一个新的无痕 context 来执行。

目标：
1. 使用本机 Google Chrome，而不是 Playwright 自带 Chromium。
2. 不读取我日常 Chrome 的 Cookie。
3. 项目使用独立 user-data-dir。
4. 每个任务使用新的 incognito context。
5. 任务结束后关闭 context，清空 Cookie、localStorage、sessionStorage。
6. 默认不关闭整个 Chrome 进程。
7. 所有自动化 worker 都通过统一 browser_manager 获取 browser/context/page，不要各自启动浏览器。

请新增文件：

app/browser_manager.py

请新增配置项到 .env.example：

```env
# Browser mode
BROWSER_MODE=cdp
CDP_ENDPOINT=http://127.0.0.1:9222
CDP_CONTEXT_POLICY=incognito
CDP_CLOSE_BROWSER=false

# Local Chrome executable is started manually by user
# Windows Chrome example:
# "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="D:\chrome-sub2api-automation-profile" --no-first-run --no-default-browser-check
~~~

BROWSER_MODE 支持两种模式：

1. playwright

使用 Playwright 自带 Chromium：

```python
browser = await playwright.chromium.launch(headless=settings.headless)
context = await browser.new_context()
```

1. cdp

连接用户本地已经启动的 Chrome / Edge：

```python
browser = await playwright.chromium.connect_over_cdp(settings.cdp_endpoint)
```

CDP_CONTEXT_POLICY 支持两种策略：

1. first

使用：

```python
context = browser.contexts[0]
```

适合复用已有 profile 登录态。

1. incognito

每个任务强制创建新的无痕 context：

```python
context = await browser.new_context()
```

注意：

- incognito 模式下不要使用 browser.contexts[0]。
- incognito 模式下不读取已有 Cookie。
- 任务结束后必须执行 context.close()。
- context.close() 后 Cookie、localStorage、sessionStorage 应该被清理。
- 这是默认推荐模式。

请在 browser_manager.py 中实现：

```python
async def get_browser_and_context(playwright, settings):
    ...
```

返回：

```python
browser, context
```

逻辑：

```python
if settings.browser_mode == "cdp":
    browser = await playwright.chromium.connect_over_cdp(settings.cdp_endpoint)

    if settings.cdp_context_policy == "incognito":
        context = await browser.new_context()
    elif settings.cdp_context_policy == "first":
        if browser.contexts:
            context = browser.contexts[0]
        else:
            context = await browser.new_context()
    else:
        raise ValueError("Unsupported CDP_CONTEXT_POLICY")

    return browser, context

elif settings.browser_mode == "playwright":
    browser = await playwright.chromium.launch(headless=settings.headless)
    context = await browser.new_context()
    return browser, context

else:
    raise ValueError("Unsupported BROWSER_MODE")
```

请在 browser_manager.py 中实现：

```python
async def cleanup_browser(browser, context, settings):
    ...
```

清理逻辑：

```python
if settings.browser_mode == "cdp":
    # CDP 模式默认不关闭整个本地 Chrome
    await context.close()

    if settings.cdp_close_browser:
        await browser.close()

elif settings.browser_mode == "playwright":
    await context.close()
    await browser.close()
```

注意：

- CDP 模式下，除非 CDP_CLOSE_BROWSER=true，否则不要关闭 browser。
- CDP incognito 模式下，每个任务结束必须关闭 context。
- 如果 context 已经关闭，不要重复报错，做好异常处理。

请修改以下 worker，让它们都通过 browser_manager 获取浏览器上下文：

```text
register_worker.py
sub2api_import_worker.py
register_and_import_worker.py
```

要求：

- 不允许这些 worker 自己直接调用 playwright.chromium.launch。
- 不允许这些 worker 自己直接调用 connect_over_cdp。
- 统一调用 browser_manager.get_browser_and_context。
- 统一在 finally 里调用 browser_manager.cleanup_browser。
- 网页注册阶段和 auth_url 授权阶段必须使用同一个 context。
- 在同一个 context 中分别创建 page_register 和 page_auth。

一键流程中应该类似：

```python
async with async_playwright() as p:
    browser, context = await get_browser_and_context(p, settings)
    try:
        page_register = await context.new_page()
        # 执行网页注册

        page_auth = await context.new_page()
        # 打开 Sub2API auth_url
        # 监听 URL / request / response 捕获 code/state
    finally:
        await cleanup_browser(browser, context, settings)
```

请更新 config.py，读取以下配置：

```python
BROWSER_MODE
CDP_ENDPOINT
CDP_CONTEXT_POLICY
CDP_CLOSE_BROWSER
```

类型要求：

- BROWSER_MODE: str，默认 "playwright"
- CDP_ENDPOINT: str，默认 "[http://127.0.0.1:9222](http://127.0.0.1:9222/)"
- CDP_CONTEXT_POLICY: str，默认 "incognito"
- CDP_CLOSE_BROWSER: bool，默认 False

请更新 README.md，增加“使用本机 Chrome CDP 模式”的说明。

README 需要包含 Windows 启动项目专用 Chrome 的命令：

```powershell
$CHROME = "C:\Program Files\Google\Chrome\Application\chrome.exe"

& $CHROME `
  --remote-debugging-port=9222 `
  --user-data-dir="D:\chrome-sub2api-automation-profile" `
  --no-first-run `
  --no-default-browser-check
```

并说明：

1. 这个 `D:\chrome-sub2api-automation-profile` 是项目专用 profile。
2. 它不会读取用户日常 Chrome 的 Cookie。
3. 项目每个任务会创建新的 incognito context。
4. 任务结束后 context 会关闭，Cookie 和 localStorage 不保留。
5. 如果用户想复用登录态，可以改成：

```env
CDP_CONTEXT_POLICY=first
```

但默认推荐：

```env
CDP_CONTEXT_POLICY=incognito
```

README 还需要说明项目 B 推荐配置：

```env
BROWSER_MODE=cdp
CDP_ENDPOINT=http://127.0.0.1:9222
CDP_CONTEXT_POLICY=incognito
CDP_CLOSE_BROWSER=false
HEADLESS=false
```

请保留原有 Playwright 模式，不能破坏原有功能。

请输出：

1. 修改后的项目文件树。
2. 新增的 app/browser_manager.py 完整代码。
3. 修改后的 config.py 相关代码。
4. 修改后的 register_worker.py 相关代码。
5. 修改后的 sub2api_import_worker.py 相关代码。
6. 修改后的 register_and_import_worker.py 相关代码。
7. 修改后的 .env.example。
8. 修改后的 README.md 中 CDP 模式说明。
9. 如何启动本机 Chrome 的 PowerShell 命令。
10. 如何运行项目 B 的命令。

```
你后续给 Claude 时，可以把这段接在“项目 B 原始提示词”后面，让它作为**增量修改要求**执行。
```