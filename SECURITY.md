# 安全说明

## 报告问题

不要在公开 Issue、PR、截图或日志中附带 API Key、用户文档、Cookie、钥匙串或凭据管理器内容、完整服务配置。发现泄露时先在对应服务撤销凭据，再联系维护者。

请通过 GitHub 私密漏洞报告提交：[Security → Report a vulnerability](https://github.com/txp666/PageBridge/security/advisories/new)，不要在公开 Issue 中写漏洞利用细节或附敏感文件。无法使用该入口时，先在仓库中请求私密联系方式。

请提供受影响版本、系统版本、最小复现、可能影响和脱敏证据。项目没有承诺固定响应时间或旧版本维护期限。

## 数据与网络

- API Key 保存在 macOS 钥匙串或 Windows 凭据管理器，普通配置文件不含 Key。
- 文档、译文、批注和对话保存在本机用户数据目录。使用云端模型时，翻译或问答会向所选服务发送所需文本。
- OpenAlex 搜索发送检索词，Sci-Hub 全文查询发送 DOI，下载 PDF 会连接对应来源；这些请求不使用翻译 API Key。
- 应用内网站验证使用临时 WebKit（Mac）或 WebView2（Windows）会话，不导出网页凭据；匿名下载器不继承该会话的 Cookie。
- MinerU 解析子进程的环境中会去掉 API Key 类变量。
- 更新检查访问本项目 GitHub Releases：Mac 的 appcast 与 ZIP、Windows 的 `windows-update.json` 与安装器都以 Ed25519 签名或哈希验证，不发送文档、笔记、模型密钥或系统配置画像。托管方仍会收到普通的网络请求元数据。
- Mac 自动检查需用户同意，不静默下载或安装；Windows 只在手动检查时联网。翻译进行时不会安装更新。
- 不通过关闭 TLS 检查或替换域名来处理证书错误。外部网站、文档和模型回答都属于不可信输入。

Mac 包为 ad-hoc 签名、未经 Apple 公证；Windows 安装器和程序没有 Authenticode 签名。代码签名、公证与软件没有漏洞是不同的事项。

## 更新签名与发布权限

`update-config.json` 中的公钥用于校验官方更新，可以公开。私钥只用于发布签名，保存在维护者的钥匙串和本仓库的 `SPARKLE_PRIVATE_KEY` Secret 中，普通 PR 和 CI 拿不到。怀疑私钥泄露时停止发布并私下通知维护者，不要把密钥作为复现附件。更换公钥会影响已安装客户端的信任链，不能只替换更新源来完成轮换。

更新验证失败时保留当前应用并检查官方发布状态；不要关闭签名检查、TLS 验证或修改已安装应用中的公钥。
