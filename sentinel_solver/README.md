# sentinel 求解服务

2026-10-14 起 chatgpt.com 的对话接口（文字和生图）开始严格校验 sentinel 握手：

- `chat-requirements` 必须带上在**真实 chatgpt.com 页面**里算出的 Turnstile token。纯 Python 模拟
  的结果会被拒，完全缺失时返回 403 "Unusual activity"。
- requirements / proof token 里编码的 UA 必须和算 Turnstile 的页面一致。
- 只要校验质量不够，对话接口不会直接返回 403，而是返回伪装成 404 的
  `{"detail":"Model not found"}` —— 主服务据此判断是账号问题还是握手质量问题。

本服务常驻一个无头 Chromium（带 playwright-stealth），一直开着 chatgpt.com 页面，替主服务计算
Turnstile。主服务本身不打包浏览器，只通过 HTTP 调用这里。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 页面状态及页面使用的 UA（主服务据此生成 requirements/proof token） |
| GET | `/v1/clearance` | 返回本页已通过的 Cloudflare 清障（`user_agent` + `cookies`），供主服务在被盾拦截时复用 |
| POST | `/v1/turnstile` | `{"dx", "p", "requirements"}` → `{"token"}`；`requirements` 是 prepare 的完整响应，给了它就调用页面自带的 turnstile 模块求解；只给 `dx`/`p` 时退回内置 VM |
| POST | `/v1/so` | `{"collector_dx", "snapshot_dx", "p"}` → `{"token"}`；目前对话接口还不强制要求 |

`solve` 优先走页面自带的 turnstile 模块（`import` chatgpt.com 自己的 chunk 后调用
`getEnforcementToken`）。这个 chunk 文件名带内容哈希、每次发版都变，所以运行时**动态发现**：先看页面
已加载的资源，再扫页面脚本里出现的字面路径，最后才回退到内置 VM。手写 VM 要持续跟版本，而直接调用
官方模块不需要。

设置了 `SENTINEL_SOLVER_KEY` 时，请求需要带 `Authorization: Bearer <key>` 头。

### 只看挑战参数

求解服务只接收 `dx`、`p` 和 prepare 响应等挑战参数。账号 access token、Cookie、用户 prompt 都不会离开主
服务进程。建议仅在内网提供服务，并配置访问密钥。

### 清障（clearance）

主服务不带浏览器，直连 chatgpt.com 时会被 Cloudflare 挑战。本页已经在盾后面，所以可以把它的
`user_agent` 和 cookie 交给主服务：`GET /v1/clearance` 返回 `{"ready", "user_agent", "cookies"}`，
主服务把这些 cookie 注入请求，并**同时把 UA 换成这里返回的 UA** —— `cf_clearance` 只对求解它的
UA + 出口 IP 有效，UA 不一致时请求依旧会被挑战。

把清障模式设为 `solver` 即可启用（`config.json` 或 `CHATGPT2API_PROXY_RUNTIME_CLEARANCE_MODE=solver`）：

```json
"clearance": { "enabled": true, "mode": "solver" }
```

`mode=solver` 不需要额外配置 URL，直接复用 `sentinel_solver_url` / `sentinel_solver_key`，也不再要求
代理运行时处于开启状态。主服务在 bootstrap 或对话请求遇到挑战页时会自动取一次清障并重试一次；取不到
就按原样报错。provider 连续失败有 30 秒冷却，避免每个请求都白等一次超时。

## 部署

主服务通过环境变量（或 `config.json` 里的 `sentinel_solver_url` / `sentinel_solver_key`）指向本服务：

```
CHATGPT2API_SENTINEL_SOLVER_URL=http://sentinel-solver:8080
CHATGPT2API_SENTINEL_SOLVER_KEY=<与 SENTINEL_SOLVER_KEY 相同>
```

可在本目录执行 `docker build -t chatgpt2api-sentinel-solver .` 构建独立服务，容器监听 8080；
或安装 `requirements.txt`、执行 `playwright install --with-deps --no-shell chromium` 后运行 `python app.py`。
主服务无需安装 Playwright；求解服务是可选的，但遇到必需的 Turnstile 挑战时，必须配置它。

Docker 镜像不包含 Chromium；容器启动时由入口脚本下载完整 Chromium 并安装系统依赖，
成功后才启动 HTTP 服务。首次启动需要联网并以 root 安装依赖，可设置 `HTTPS_PROXY` / `HTTP_PROXY`
作为下载代理；`SENTINEL_SOLVER_PROXY` 仅控制浏览器访问 chatgpt.com 的代理。
建议将 `/ms-playwright` 挂载为持久卷，以复用已下载的浏览器；重建容器后仍需安装系统依赖。

未配置或调用失败时，主服务**直接失败**并报出明确错误，不再退回本地求解器：本地求解器跟不上新版
挑战程序，继续用空 token 只会换来伪装成 404 的 "Model not found"，白等一轮重试预算。

环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SENTINEL_SOLVER_KEY` | 空 | 访问密钥 |
| `SENTINEL_SOLVER_PROXY` | 空 | 浏览器出网代理，应与主服务访问 chatgpt.com 的出口一致；`user:pass@host` 形式的账密会被拆到 Playwright 的 `username`/`password`（Chromium 不认 URL 里的 userinfo） |
| `SENTINEL_SOLVER_UPSTREAM` | 空 | 带认证的上游（如 `socks5://u:p@host:1080`）。Chromium 无法为 SOCKS5 上游做认证，入口脚本用 gost 把它转发成本机无认证的 socks5，浏览器只连 `127.0.0.1:1080`；设置它时 `SENTINEL_SOLVER_PROXY` 被忽略 |
| `SENTINEL_SOLVER_USER_AGENT` | 按内置 Chromium 版本生成 | 页面 UA |
| `SENTINEL_SOLVER_TIMEZONE` | 跟随系统 | 页面时区（如 `Asia/Shanghai`），应与出口 IP 所在地一致 |
| `SENTINEL_SOLVER_REFRESH_SECONDS` | 1800 | 页面定期重开的间隔 |
| `SENTINEL_SOLVER_HEADLESS` | true | 调试时可设为 false |
| `PORT` | 8080 | 监听端口 |

## 注意

- 必须使用完整 Chromium 的新版无头模式（`channel="chromium"`），并加上 stealth；默认的 headless
  shell 过不了 Cloudflare，页面会停在「请稍候…」。
- `vm.js` 移植自 chatgpt.com 前端 bundle 里的挑战程序（2026-10 版本，指令编号 0-35），来源为
  MEIZU16/chatgpt2api 的 `sentinel_solver/`（MIT），保持逐字节一致以便和前端 bundle 对账。OpenAI
  更新指令集后需要同步更新。
- 本服务只解决**请求质量问题**。被脚本批量注册、账号层面受限的账号，即使在真实网页里也会提示
  "Model not found"，那不是这里能修的。

## 来源

`vm.js` 移植自 [MEIZU16/chatgpt2api](https://github.com/MEIZU16/chatgpt2api)（MIT）；
调用页面自带 Turnstile 模块的思路来自 [239573049/WebImage](https://github.com/239573049/WebImage)（MIT）。
