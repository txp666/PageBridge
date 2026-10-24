# 参与 PageBridge

欢迎提交可复现的问题、文档改进和代码修改。项目原创代码按 [AGPL-3.0](LICENSE) 分发；贡献者保留自己的版权，提交贡献即表示有权按相同许可证提供该贡献。第三方资源遵循各自许可证，见 [第三方说明](THIRD_PARTY_NOTICES.md)。

## 开发

需要 Apple Silicon Mac、Xcode Command Line Tools / Swift 6；构建目标为 macOS 14 及以上。先阅读 [开发指南](docs/DEVELOPMENT.md)，再执行：

```sh
bash engine/setup.sh
CLANG_MODULE_CACHE_PATH=/tmp/pagebridge-clang-cache \
SWIFT_MODULECACHE_PATH=/tmp/pagebridge-swift-cache \
swift test --package-path macOS --cache-path /tmp/pagebridge-spm-cache --disable-sandbox
engine/.venv/bin/python -B -m unittest discover -s engine/tests -v
bash scripts/build-app.sh
python3 scripts/verify-bundle.py
```

引擎安装会下载锁定依赖；首次翻译还可能下载模型与字体。Ollama 及语言模型另行安装。常规测试使用固定响应，不需要云 API Key；可选的 Keychain/Ollama 集成测试必须显式启用。

## 提交修改

- 描述具体问题、复现步骤、修改后的行为和实际验证结果。附系统版本与 PageBridge 版本。
- 新增网络来源或服务时，保持取消、超时、错误显示和密钥隔离；不要将真实 API Key 写入测试。
- 阅读、批注、导出改动要在原生 App 中验证；翻译引擎改动按开发指南重新检查受影响 PDF 的文字、公式、排版和链接。
- 使用有公开分发许可的最小示例。不要上传个人文档、完整对话、模型请求日志、用户配置或钥匙串数据。
- 不提交 `dist/`、`validation/`、虚拟环境、缓存与历史网页原型。新资源需标明来源和许可证。

应用版本与 build 号集中于 `VERSION.json`。发布修改同时更新 README、使用指南、CHANGELOG 和验证记录。仅编译通过不代表完成真实网站、模型或 PDF 质量验收。

## 问题反馈

普通问题可在项目的 Issue 区提供最小复现；真实凭据和安全漏洞按 [安全说明](SECURITY.md) 处理。提交 PR 前运行与修改有关的检查，并明确没有测试的范围，不要把历史结果写成新结果。
