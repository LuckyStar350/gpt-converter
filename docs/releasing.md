# 构建与发布

[返回项目首页](../README.md)

## 源码准备

确认 `.gitignore` 已生效，再将需要发布的源码加入暂存区。不要添加个人账号、Token、代理密码、截图、日志或机器配置。`examples/` 与测试使用虚构凭据。

制作可分享的源码包：

```sh
python scripts/build_release.py --source-only
```

生成 `dist/gpt-converter-source-v1.3.0.zip`。脚本从 Git 跟踪 / 暂存清单读取文件，包含其当前工作区内容；EXE、其他 ZIP 与本机生成目录不会写入源码包。生成物由 `.gitignore` 排除。

## Windows 运行包

在 Windows 上安装构建依赖并执行：

```sh
python -m pip install -r requirements-dev.txt
python scripts/build_release.py
```

生成 `dist/gpt-converter-windows-v1.3.0.zip`，运行包内只有一个 Windows 启动入口：`启动工具.bat`。构建过程不覆盖根目录下正在使用的 EXE。

完整解压到任意目录，双击启动入口，检查页面连接、格式转换与下载。联网检测使用虚构 RT；实际账号刷新仍需有效 RT 和可达网络。

## GitHub 构建

仓库上传后，Actions 会自动运行前端和后端测试。可以手动运行 **Build Windows package**，或推送 `v*` 标签触发运行包构建。该工作流只生成可下载的构建产物，不自动创建 GitHub Release。

维护者检查构建产物后，可创建对应版本的 GitHub Release 并上传 Windows ZIP。源码使用普通 Git 提交维护，不将生成的 EXE、ZIP 或本机测试产物提交到仓库。

发布前确认 Git 作者名和邮箱符合自己的公开隐私偏好。当前准备版本使用项目贡献者署名；如需个人署名，可在这个仓库单独配置 Git 作者。
