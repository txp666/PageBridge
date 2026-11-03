# 开发与构建

以下命令从仓库根目录执行。架构说明见 [ARCHITECTURE.md](ARCHITECTURE.md)，Windows 构建见 [WINDOWS.md](WINDOWS.md#构建与测试)。

| 目录 | 内容 |
|---|---|
| `macOS/Sources/PageBridge` | SwiftUI/AppKit 窗口、文档库、PDFKit 阅读器、阅读稿画布、批注、助手、文献搜索、服务设置、更新 |
| `macOS/Sources/PageBridgeCore` | 配置与钥匙串、worker 协议、文档／分类／批注／对话存储、阅读稿模型、助手请求、文献下载 |
| `windows/PageBridge.Windows` | WinUI 3 窗口、阅读器、文字与批注层、设置、在线文献、助手、更新 |
| `windows/PageBridge.GeometryChecks` | Windows 连续滚动页面几何检查 |
| `desktop/` | Windows 端 Python 服务：文档库、系统凭据、阅读／批注／助手、文献、更新和 worker 管理 |
| `engine/` | 两端共享的翻译引擎（MinerU 阅读稿及其 PDF 导出）和测试，见 [engine/README.md](../engine/README.md) |
| `scripts/` | 构建、裁剪、打包和发布校验 |
| `assets/` · `demos/` | 图标原图与生成说明；公开许可的示例 PDF、来源与哈希 |

`.gitignore` 排除虚拟环境、构建产物（`dist/`）、本地验证目录（`validation/`）、密钥文件和原型。用户 PDF、配置、钥匙串数据和译文不要加入 Git。

## Mac 开发环境

- Apple Silicon Mac，Xcode Command Line Tools / Swift 6（CI 使用 Xcode 16.4），部署目标 macOS 14。
- 引擎依赖由 `engine/setup.sh` 按 `engine/uv.lock` 安装：没有 uv 时在 `engine/.tools` 安装 uv 0.9.17，再创建 CPython 3.12.12 虚拟环境。
- 真实翻译需要另装 Ollama 和模型，或使用 API。

```sh
bash engine/setup.sh
```

## 测试

```sh
engine/.venv/bin/python -B -m unittest discover -s engine/tests -v
PYTHONPATH=desktop engine/.venv/bin/python -B -m unittest discover -s desktop/tests -v
engine/.venv/bin/python -B -m unittest discover -s scripts -p 'test_*.py' -v

CLANG_MODULE_CACHE_PATH=/tmp/pagebridge-clang-cache \
SWIFT_MODULECACHE_PATH=/tmp/pagebridge-swift-cache \
swift test --package-path macOS --cache-path /tmp/pagebridge-spm-cache --disable-sandbox
```

这些测试使用固定响应，不需要真实 API Key 或模型；与 CI 运行的命令相同。Windows 测试命令见 [WINDOWS.md](WINDOWS.md#构建与测试)。

界面相关改动需在原生应用中复核，例如：文件夹与搜索后的选择、重命名、收藏、回收与恢复、缩略图与目录跳转、重启后笔记与书签恢复、阅读稿同步滚动与单段重试。导出要通过系统保存对话框实际保存，再检查译文 PDF 与双语 PDF 的页数、可选择和搜索的译文、图表，以及批注 PDF 和 Markdown 内容。助手需检查发送范围、引用跳转、未提供页码的提示、取消，以及切换文档后回答归属。

## MinerU 阅读稿

阅读模式的协议、产物格式和运行环境见 [engine/STRUCTURED_READING.md](../engine/STRUCTURED_READING.md) 与 [JSON Schema](../engine/structured-reading.schema.json)。开发时可把 MinerU 运行环境准备到单独目录：

```sh
printf '{"protocolVersion":1,"jobId":"dev","attemptId":"prepare","action":"prepare_reading"}\n' |
  PAGEBRIDGE_MINERU_RUNTIME="$PWD/validation/my-mineru-runtime" \
  PAGEBRIDGE_UV="$PWD/engine/.tools/bin/uv" \
  engine/.venv/bin/python engine/worker.py
```

worker 在 stdout 输出 NDJSON 进度，最后一行为 `result` 或 `error`。之后运行真实 worker 请求时设置同一个 `PAGEBRIDGE_MINERU_RUNTIME` 即可复用该环境；不设置时使用应用的默认位置。`PAGEBRIDGE_UV` 可省略，worker 会依次查找 `engine/.tools/bin/uv` 和 PATH 中的 uv。

验证阅读稿时检查真实文档的页／块来源、表格和公式区域、部分失败与单段重试、取消，以及重新生成后的页范围。

真实检查 `engine/verify_real.py` 用本机 Ollama 翻译示例 PDF 的一页，再从这份阅读稿导出译文 PDF 和双语 PDF，检查页数、译文页上可提取的中文和原文件未变：

```sh
engine/.venv/bin/python -B engine/verify_real.py --page 1 --output validation/my-real-reading
```

需要已准备的 MinerU 运行环境（`PAGEBRIDGE_MINERU_RUNTIME` 或应用默认位置）。默认使用 `demos/pdfs/avalanches.pdf` 和 `127.0.0.1:11434` 上的 `qwen3.5:35b-a3b-int4`，可用 `--pdf`、`--model`、`--base-url` 更换；输出目录保存两份 PDF、页面预览图和报告，可能包含文档文字，不要提交。真实翻译请串行运行，避免争用本机模型。数字、公式、图表和排版仍需打开导出的 PDF 人工查看。

## 构建 Mac 应用

```sh
bash scripts/build-app.sh
python3 scripts/verify-bundle.py
python3 scripts/package-release.py
```

`build-app.sh` 生成 `dist/PageBridge.app`：生成 ICNS，复制原生可执行文件、Sparkle framework、示例 PDF 和私有 Python，按 `scripts/bundle-profile.json` 复制引擎文件（`engineFiles`）并裁掉 pip、包内测试和开发资源，最后从内向外 ad-hoc 签名。修改包内任何文件后都要重新构建签名。

`verify-bundle.py` 检查版本与 `VERSION.json` 一致、许可证、更新源与公钥、Sparkle 签名、私有运行时与依赖版本、引擎文件、阅读模式集成文件和 uv、图标及示例清单；它不调用模型，也不安装 MinerU 环境。可用 `--app 路径 --output 报告.json` 检查解压后的应用。

`package-release.py` 生成应用和项目源码归档（`--app-only`／`--source-only` 只生成其一），拒绝覆盖已有归档。源码归档包含工作区中已跟踪和新增的未忽略文件，排除 `.git`、虚拟环境、缓存、验证输出和构建产物。

示例库由 `assemble-app.py` 从 `demos/` 复制到 `PageBridge.app/Contents/Resources/demos/`；直接 `swift run` 不会组装这些资源。新增示例时同步更新 `demos/catalog.json` 的页数、作者、来源、许可和 SHA-256。

引擎、依赖或调用链改动后，用应用包内的 Python 运行同一真实检查（包内引擎文件与源码一致，由 `verify-bundle.py` 核对）：

```sh
dist/PageBridge.app/Contents/Resources/engine/runtime/bin/python3 -B engine/verify_real.py \
  --output validation/final-bundle-reading
```

## 版本与发布

版本号和 build 号只在根目录 `VERSION.json` 中维护。发布时同时更新版本、严格递增的 build、[CHANGELOG](../CHANGELOG.md) 和相关文档；把 `## 未发布` 改为 `## <版本> / build <build> — 标题`，appcast 和 Release 说明按这个标题从 CHANGELOG 读取。标签与版本对应，例如 `0.5.0` 对应 `v0.5.0`。已公开的标签和资产不重打、不覆盖，修复须发布新版本。

每个版本发布 12 项资产：Mac ZIP、Windows 安装器、Windows 便携 ZIP、项目源码 ZIP、第三方对应源码 ZIP 及各自的 `.sha256`，加上 Mac 签名 `appcast.xml` 和 Windows 签名 `windows-update.json`。第三方源码要求见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

### Mac 更新签名

Mac 使用 Sparkle 2.10.0（锁定在 `macOS/Package.swift` 与 `Package.resolved`）。`update-config.json` 记录仓库、feed 地址、Ed25519 公钥和 Sparkle 版本，构建时写入 Info.plist。feed 为：

```text
https://github.com/txp666/PageBridge/releases/latest/download/appcast.xml
```

私钥不得进入 Git、配置、命令行或日志。本机签名使用钥匙串账户 `tech.pagebridge.mac.ota`；发布 CI 通过 `SPARKLE_PRIVATE_KEY` Actions Secret 取得私钥，普通 CI 和 PR 不提供该 Secret。本机发布步骤：

```sh
engine/.venv/bin/python scripts/fetch-third-party-sources.py
engine/.venv/bin/python scripts/create-appcast.py --account tech.pagebridge.mac.ota
engine/.venv/bin/python scripts/verify-release.py --check-latest --output validation/release-check.json
```

CI 不传 `--account`，`create-appcast.py` 从环境变量读取私钥。`verify-release.py` 只用公钥复核 feed 和 ZIP 签名，并拒绝 build 回退。

### Windows 更新清单

`desktop/pagebridge/updates.py` 通过随包的 `VERSION.json` 和 `update-config.json` 公钥检查、下载更新，界面入口在 `MainWindow.Updates.cs`。清单地址：

```text
https://github.com/txp666/PageBridge/releases/latest/download/windows-update.json
```

清单包含平台、版本与 build、最低 Windows build、安装器 URL、大小与 SHA-256 和更新说明 URL，由 `scripts/windows-update.py` 在发布签名步骤用同一个 `SPARKLE_PRIVATE_KEY` 签名（独立的签名域，Windows 不读取 Sparkle appcast）。下载保存在用户数据的 `updates/` 中；安装前再次验证签名和哈希，WinUI 锁定文件复核 SHA-256 后以普通交互方式启动安装器并退出应用。产品代码中没有跳过签名的开关。

### GitHub CI 与发布

- `.github/workflows/ci.yml`：推送 `main` 和 Pull Request 时，在 `macos-15`（arm64）上运行 Swift、引擎、desktop 和脚本测试。
- `.github/workflows/windows.yml`：Windows 测试、打包和安装器实测，上传构建产物。
- `.github/workflows/release.yml`：推送 `v*` 标签时核对仓库与标签身份，分别测试、构建 Mac 与 Windows，签名 feed 和更新清单，以草稿上传全部资产并核对大小与 SHA-256 后再公开。
- `.github/workflows/verify-release.yml`：对已公开版本做只读复验，不构建或替换资产。

先合并通过 CI 的提交，再对同一提交打标签。上传中断时，只有同标签、同提交并带本工作流来源标记的未公开草稿可以续传。也可从 `main` 手动重跑尚未公开的标签，但要求 `main` 上的 `release.yml` 与标签中的字节一致：

```sh
gh workflow run release.yml --ref main -f tag=v0.5.0
```

公开后的只读复验会检查匿名下载、SHA-256、ZIP CRC、HTTP Range、安装器与便携包对应关系、源码身份、上游源码，以及 Mac feed 和 Windows 清单签名。0.5.1 及以后的版本从 `main` 运行；0.5.0 需使用该标签中的工具运行：

```sh
gh workflow run verify-release.yml --ref main -f tag=vX.Y.Z      # 0.5.1 及以后
gh workflow run verify-release.yml --ref v0.5.0 -f tag=v0.5.0    # 0.5.0
```

本机复验公开 Release：`engine/.venv/bin/python scripts/verify-release.py --published --output validation/published-release-check.json`。发布验收清单和当前版本结果见 [VERIFICATION.md](VERIFICATION.md)。

## 贡献

贡献流程见 [CONTRIBUTING.md](../CONTRIBUTING.md)，安全问题见 [SECURITY.md](../SECURITY.md)。
