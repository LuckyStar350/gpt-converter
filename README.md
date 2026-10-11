# GPT 转换工具

RT（ChatGPT refresh_token）、Sub2API 和 CPA（CLIProxyAPI / Codex）凭据的本地转换工具，支持六个转换方向，默认通过本机服务联网刷新 OAuth 凭据。

当前版本：**1.3.0** · [MIT 许可证](LICENSE)

## 功能

- 自动识别 RT、Sub2API、CPA、Codex auth.json 与 Cockpit Tools 账号结构。
- 支持粘贴、多文件、文件夹、JSONL 和 ZIP 批量导入。
- 联网补全访问令牌和可获取的账号信息，保存最新 refresh_token。
- Sub2API 合并为一个 JSON；CPA 每个账号一个 JSON，批量下载为 ZIP。
- 默认脱敏预览，转换和打包在本机完成，支持保留扩展字段。
- 自动读取运行电脑的系统 / 环境代理，也支持直连和自定义代理。

## 启动

### Windows 运行包

从项目的 GitHub Releases 下载 `gpt-converter-windows-v1.3.0.zip`（需要维护者先发布运行包），完整解压后双击 **启动工具.bat**。运行包包含 EXE，无需安装 Python 或 Node.js。`start.ps1` 是启动入口调用的辅助脚本。

服务仅监听本机，通常打开 `http://127.0.0.1:8765`；端口占用时会选择后续端口。重复启动同版本工具会打开现有服务。

### 从源码运行

安装 Python 3.11 或更新版本后，在项目目录执行：

```sh
python -m pip install -r requirements.txt
python server.py --open
```

Windows 也可双击 `启动工具.bat`，脚本会优先使用同目录 EXE，没有 EXE 时查找 Python。macOS / Linux 使用 `python3`，或执行 `sh start.sh`。

前端不需要 npm install。直接打开 `index.html` 无法联网刷新，需要先启动本机服务。

## 使用

1. 粘贴 RT / JSON，或导入账号文件。
2. 选择 RT、Sub2API 或 CPA 目标格式。
3. 点击「联网转换」，查看报告后复制或下载结果。
4. 及时保存最新凭据；OAuth 刷新可能轮换 RT。

代理端口使用运行电脑的配置，页面示例不会自动生效。「检测连接」使用虚构 RT；HTTP 401 且提示 OAuth 服务可达，表示网络连接正常。

完整说明见 [输入格式、联网刷新及字段保留](docs/usage.md)，参考依据见 [参考项目与兼容性](docs/references.md)。

## 数据与隐私

账号数据不写入浏览器持久存储，不记录 Token 或代理密码。点击联网转换时，本机服务将 RT 发送到 OpenAI OAuth 服务；格式映射和下载打包在浏览器本地完成。

源码与示例不包含个人账号、固定代理或机器路径。`examples/` 与测试中的邮箱、Token 都是虚构数据。OAuth 的公开 client_id 是协议参数，不是账号密钥。

运行包、测试产物、参考仓库、缓存和账号导出文件不纳入 Git。`docs/images/` 仅保存审核后可公开的演示截图。源码仓库只包含可公开维护的文件。

## 开发与验证

安装运行依赖后执行：

```sh
npm test
python -m unittest discover -s tests -p test_server.py -v
```

前端测试使用 Node.js 24，后端使用 Python 3.11。CI 使用虚构凭据和模拟 OAuth，不提交真实 RT。

浏览器检查需要 Chrome 和 Playwright，可先执行 `python -m pip install -r requirements-dev.txt`，再运行：

```sh
python tests/browser_smoke.py
python tests/browser_online.py
```

构建与发布说明见 [发布文档](docs/releasing.md)。

## 项目结构

```text
app.js / converter.js / archive.js / online.js  页面交互与转换逻辑
index.html / styles.css / icon.svg             界面资源
server.py                                     本机 OAuth 服务
启动工具.bat / start.ps1 / start.sh              启动脚本
examples/                                     虚构输入样例
tests/                                        自动化检查
scripts/                                      可移植构建工具
docs/                                         使用与发布说明
.github/workflows/                            自动检查与构建
```

## 许可证

本项目代码采用 [MIT 许可证](LICENSE)。第三方依赖保留各自的许可证，公开参考项目见 [参考说明](docs/references.md)。
