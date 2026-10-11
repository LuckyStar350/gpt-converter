# 参考项目与兼容性

[返回项目首页](../README.md)

格式结构核对日期：2026-10-11。本项目为独立实现，参考了下列官方字段定义与公开工具的功能安排，没有复制其实现代码，也不需要克隆或运行参考项目。

- [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api)：[账号导入导出结构](https://github.com/Wei-Shaw/sub2api/blob/main/backend/internal/handler/admin/account_data.go)，包括 `DataPayload`、`DataAccount` 和导入校验。
- [router-for-me/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)：[CodexTokenStorage](https://github.com/router-for-me/CLIProxyAPI/blob/main/internal/auth/codex/token.go)、[JWT 字段](https://github.com/router-for-me/CLIProxyAPI/blob/main/internal/auth/codex/jwt_parser.go) 和 [OAuth 刷新接口 / 默认 client_id](https://github.com/router-for-me/CLIProxyAPI/blob/main/internal/auth/codex/openai_auth.go)。
- [gtxx3600/CPA2sub2API](https://github.com/gtxx3600/CPA2sub2API)：CPA 与 Sub2API 双向转换、独立凭据导出和批量 ZIP 的使用约定。
- [dimthink/token-converter](https://github.com/dimthink/token-converter)：纯前端本地转换和 JWT 元数据读取思路。
- [NevinXuHui/cockpit-tools2](https://github.com/NevinXuHui/cockpit-tools2/tree/47bd9493e6436c6e4882eef36ea9d3f3cc87f61a)：对照 [Codex RT 刷新](https://github.com/NevinXuHui/cockpit-tools2/blob/47bd9493e6436c6e4882eef36ea9d3f3cc87f61a/src-tauri/src/modules/codex_oauth.rs#L615)、[身份字段补全](https://github.com/NevinXuHui/cockpit-tools2/blob/47bd9493e6436c6e4882eef36ea9d3f3cc87f61a/src-tauri/src/modules/codex_account.rs#L360) 与 [嵌套凭据结构](https://github.com/NevinXuHui/cockpit-tools2/blob/47bd9493e6436c6e4882eef36ea9d3f3cc87f61a/src-tauri/src/models/codex.rs#L5)。

上游版本可能调整导入规则。如果遇到特定版本的兼容问题，请对照该版本的字段定义或使用去除敏感内容的示例定位。
