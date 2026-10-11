# 参与开发

本项目提供 RT、Sub2API 与 CPA 凭据之间的格式转换，支持 OAuth 联网刷新、批量导入和导出，以及 Windows 系统托盘操作。

欢迎通过 GitHub Issues 反馈问题、提出功能建议，也欢迎提交 Pull Request 改进项目。

可以参与的方向包括：

- 完善 RT、Sub2API 和 CPA 的格式识别与转换兼容性。
- 改进 OAuth 凭据刷新、网络连接和错误提示。
- 优化批量导入、结果下载、界面交互和系统托盘操作。
- 补充使用文档、测试，修复已知问题。

反馈问题时，请说明工具版本、操作系统、复现步骤，以及预期结果和实际表现。

提交代码前，请运行项目测试：

```sh
npm test
python -m unittest discover -s tests -p "test_*.py" -v
```

每个 Pull Request 尽量聚焦一个问题，并简要说明修改内容和验证结果。
