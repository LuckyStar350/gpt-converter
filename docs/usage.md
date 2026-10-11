# 详细使用说明

[返回项目首页](../README.md)

## 使用

1. 粘贴 RT 或 JSON，或拖入 JSON、JSONL、TXT、ZIP 文件；支持多个文件及文件夹。导入文件和粘贴内容会一起转换。
2. 保持「自动识别」或指定输入格式，选择 RT、Sub2API、CPA 目标格式。
3. 点击「联网转换」或 Ctrl / ⌘ + Enter，默认联网获取最新 OAuth 凭据，再输出目标格式。
4. 查看转换报告，然后复制或下载结果。

| 目标格式 | 下载结果 | 使用方式 |
| --- | --- | --- |
| RT | `refresh-tokens.txt` | 每行一个 refresh_token |
| Sub2API | `sub2api-accounts.json` | 在账号管理的「导入数据」中选择此 JSON |
| CPA，单账号 | `codex-*.json` | 作为 CPA 的 Codex 凭据导入 |
| CPA，多账号 | `cpa-accounts.zip` | 解压后把各个 JSON 导入 CPA / 放入配置的 auth-dir |

CPA 批量转换可以通过输出区的文件列表切换预览。「复制结果」复制当前文件的完整内容；下载包含所有生成文件。Token 在预览中默认脱敏，可点击「显示 Token」，下载和复制始终使用完整原文。

`examples/` 中的文件及页面示例都是**虚构 Token**，只供检查输入结构和批量导入，实际联网刷新会失败。

## 网络连接与刷新

1. 使用启动脚本打开页面，确认显示「本机服务已连接」。页面显示当前系统代理地址（不显示密码）。
2. 可以先点击「检测连接」。它向 OAuth 服务发送虚构 RT，不发送输入中的任何凭据；返回 HTTP 401 和「OAuth 服务可达」表示连接正常。
3. 如需代理，展开「网络设置」，选择「自定义代理」并填写地址，例如 `http://127.0.0.1:7890`、`https://代理地址:端口` 或 `socks5h://127.0.0.1:1080`。默认读取运行电脑的系统 / 环境代理，也可选择直连。SOCKS5h 使用代理端 DNS。示例端口仅供参考，请使用自己的代理配置。
4. 工具使用 refresh_token 请求 `https://auth.openai.com/oauth/token`。默认优先取输入凭据中的 client_id；输入没有时使用 CLIProxyAPI / Codex 的公开客户端 ID。RT 来自其他 OAuth 客户端时，在网络设置中填写正确的 client_id。
5. 输入 RT 或已有 JSON，选择目标格式，点击「联网转换」。成功后使用新 access_token、id_token、refresh_token 和有效期，从 JWT 读取可获得的身份信息。输入同步更新；切换格式和重复点击会复用本次已刷新的凭据，直到接近到期或 client_id 改变。
6. 下载保存最新结果。OAuth 刷新可能轮换 RT，旧 RT 可能不能再次使用。失败项保留原数据并在报告中说明；缺少访问令牌或账号 ID 的项目不生成 CPA / Sub2 占位文件。RT 目标仍可导出原 RT。没有 RT 的完整 JSON 会沿用原凭据，报告明确提示无法刷新。

同一批相同 RT / client_id 只刷新一次，最多同时处理两组 RT。「停止后续刷新」停止发送剩余请求，等待已发送请求完成，随后输出可导出的结果。刷新期间锁定输入。工具使用 curl_cffi 的 Chrome TLS / HTTP 特征发送请求，验证 TLS 证书，禁止重定向，不自动重试真实 RT，也不尝试其他 client_id。

### HTTP 403 和 RT 已被使用

- **HTTP 403：** 区分网络验证拦截、出口地区被拒绝以及其他访问拒绝。先「检测连接」，确认实际代理地址和状态，再检查代理软件的出口节点。403 无法通过反复提交旧 RT 解决。
- **RT 已被使用：** 请导入上次刷新保存的新 RT / CPA / Sub2 凭据；未保存需要重新登录获取，旧 RT 无法恢复。更换代理无法修复已轮换的 RT。
- **超时：** 请求可能已经到达上游；不要连续重试旧 RT。
- **client_id 不匹配：** 优先读取 JSON / JWT 中的 client_id。纯 RT 不携带此信息；如非 Codex 客户端签发，需手动填写正确值。

v1.3 同时缓存新旧 RT 对应的成功结果，避免其他标签页立即提交新 RT 再次轮换。缓存也读取 OAuth 有效期及访问 JWT 的 `exp`；剩余有效期不超过 30 秒时，使用缓存里的最新 RT 刷新，已知的旧新 RT 共用刷新锁。本机缓存仅存在于当前进程，不能找回其他工具已经消耗或重启前丢失的 RT。

联网请求只发送到 OpenAI OAuth 服务，不使用第三方在线转换网站。浏览器只连接本机服务，外部 OAuth 请求由本机服务发送，避免浏览器跨域限制。本机服务只监听 `127.0.0.1`，限制同源请求，不提供目录浏览，不记录请求体、Token 或代理密码。成功刷新结果只在内存中缓存 10 分钟，以 200 个 RT 索引为容量目标；正在续期的映射暂时保留，后续完成请求时清理，避免同一 RT 被并发消耗。停止服务后清空。

能否实际刷新取决于 RT 是否有效、所属客户端和网络是否可达。成功获得访问令牌不保证 OAuth 响应包含全部身份字段，缺失字段仍会在报告中提示。

## 数据处理

格式映射、JWT 载荷解码、打包与解压在本机浏览器内完成。没有 CDN、远程字体或统计脚本。打开页面只检查本机服务状态，点击「联网转换」或「检测连接」后才发送外部 OAuth 请求。页面 CSP 只允许同源连接，不使用 localStorage、sessionStorage 或 IndexedDB 保存账号；刷新页面后输入清空。下载文件由用户自行保存。

**RT 本身不包含完整 OAuth 凭据。** 需要有效 RT、匹配的客户端和可访问 OAuth 的网络才能补全。未知字段不伪造；格式转换成功也不保证目标服务接受该账号。

已有 access_token / id_token 为 JWT 时，读取载荷中的邮箱、账号 ID、套餐、client_id 和访问令牌到期时间；此操作不校验 JWT 签名。显式输入字段优先，不根据 id_token 到期时间猜测 access_token 到期时间，不伪造套餐、有效期或刷新时间。

## 支持的输入结构

### RT

每行一个 Token，或每行 `邮箱----Token`。也支持邮箱与 Token 之间使用制表符、竖线或逗号，取最后一列为 Token。可忽略空行、以 `#` / `//` 开头的注释和单独的 `refresh_token` 表头。

```text
rt_DEMO_ONLY_not_a_real_token_001
demo@example.com----rt_DEMO_ONLY_not_a_real_token_002
```

接受至少 16 个字符的常见 RT 字符组成；这只是文本格式检查，不表示 Token 有效。也支持包含 `refresh_token` 的简单 JSON 对象、JSON 字符串和字符串数组。

### Sub2API

支持 `accounts` 集合、单个包含 `credentials` 的账号、账号数组，以及 `data` 包装的 API 响应 / 导入请求。支持 `sub2api-data` 和历史的 `sub2api-bundle` 集合结构。仅转换 `platform: "openai"`、`type: "oauth"` 账号；其他平台和 API Key 账号会显示跳过原因。

生成的集合含 `type: "sub2api-data"`、`version: 1`、`exported_at`、`proxies` 和 `accounts`。新账号默认 `concurrency: 10`、`priority: 1`，页面可修改。源账号的并发、优先级、代理、备注和业务到期时间在启用扩展保留时沿用。

`credentials.expires_at` 对应访问令牌到期时间；账号顶层 `expires_at` 是账号业务到期时间，两者分别保留。

### CPA

支持 `type: "codex"` 的单个凭据、凭据数组、JSONL 及多个 JSON 文件。核心字段为 `access_token`、`refresh_token`、`id_token`、`account_id`、`email`、`last_refresh` 和 `expired`。也兼容 Codex `auth.json` 中的 `tokens` 对象。

也支持 Cockpit Tools 的 Codex 账号数组，保留顶层邮箱、`user_id`、套餐及组织信息。账号 ID 兼容 JWT auth 中的 `chatgpt_account_id` / `account_id`，组织 ID 兼容 `organization_id` / `chatgpt_organization_id` / `chatgpt_org_id` / `org_id`。不将 Cockpit 的本地存储 `id` 当作 ChatGPT 账号 ID。

CPA 导出坚持每账号一文件。多个账号以标准 ZIP 打包，无需外部 ZIP 库；重复文件名会追加序号。保留已有 `tokens` 嵌套对象时，同步写入最新凭据；再次导入及跨格式往返继续使用新 RT 和到期时间。

## 字段保留与去重

- 默认启用「保留扩展字段」。跨格式转换时，无法直接映射的源信息放入 `_gpt_converter` 命名空间：Sub2API 放在 `account.extra._gpt_converter`，CPA 放在凭据顶层 `_gpt_converter`。
- 本工具识别上述扩展，可恢复原有代理、调度、模型映射及其他配置。目标服务导入并重新导出时可能移除扩展，因此不承诺经过目标服务后的完整往返。取消保留可生成没有工具扩展的标准字段结构。
- RT 文本只保留 refresh_token；从 JSON 转成 RT 后，其他字段无法通过该文本恢复。需要保留完整账号信息时请保存原 JSON。
- 自动去重按 refresh_token（没有时使用 access_token / id_token）与账号 ID 判断，重复项保留凭据字段更完整的记录，不合并互相冲突的扩展配置。RT 导出还会对同一 RT 去重。取消自动去重可保留重复项。
- 无效项目和不支持的平台会逐项报告，其他可识别账号仍可导出。诊断信息不会回显 Token 内容。
- 点击转换默认刷新已有凭据并更新令牌有效期；账号顶层的业务到期时间和调度配置继续保留。切换格式复用本次结果。

限制：单文件 / 单次粘贴最多 20 MB，导入文件内容合计最多 64 MB，每次最多 5000 个账号。ZIP 导入支持 Store、Deflate 和常见目录结构，不支持加密、分卷或 ZIP64。报告表格最多显示前 200 个账号，下载包含全部结果。文本文件使用 UTF-8。
