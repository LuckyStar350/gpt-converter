# 参与开发

提交前运行 `npm test` 与后端单元测试，保持 Windows 的唯一启动入口为 `启动工具.bat`。界面改动可使用 `tests/browser_smoke.py` 验证。

所有示例、截图和测试凭据应为虚构数据；邮箱使用 `example.com`，Token 使用明显的 DEMO / FAKE 标记。不要提交真实账号文件、访问令牌、刷新令牌或代理密码。

路径从项目目录动态计算，代理从运行电脑的配置读取。请使用通用示例说明配置，避免写入自己的用户名、机器绝对路径和网络地址。

EXE、ZIP、参考项目和测试产物由忽略规则排除。发布产物使用 `scripts/build_release.py` 或 GitHub Actions 生成。
