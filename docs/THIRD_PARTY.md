# 第三方来源与资源

| 组件 | 锁定版本 | 上游 |
|---|---|---|
| PDFMathTranslate-next | 2.9.0 | [源码](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next) |
| BabelDOC | 0.6.2 | [源码](https://github.com/funstory-ai/BabelDOC) |
| CPython | 3.12.12 | [Python](https://www.python.org/) |
| PyMuPDF | 1.25.2 | [官方文档](https://pymupdf.readthedocs.io/) |

完整 Python 依赖与版本以 [engine/uv.lock](../engine/uv.lock) 为准。构建流程保留 Python 包源码与发行元数据中的许可证，并写入 `Contents/Resources/Third-Party-Notices.txt`。PDFMathTranslate-next 与 BabelDOC 使用 AGPL-3.0；本文件不替代各组件完整许可。

五份公开示例 PDF 的作者、DOI、原始下载地址、许可与内容哈希见 [示例说明](../demos/README.md) 和 [目录清单](../demos/catalog.json)。示例保留原出版文件，不包含用户私有 PDF。机器译文须明确标注为修改版本，并保留作者和原始来源归属。

PageBridge 图标由本项目使用内置图像生成工具制作，设计提示与转换方法见 [图标说明](../assets/README.md)。

当前仓库初始化用于本地开发，尚未另行指定覆盖全部原创源码的分发许可证。对外发行时需整理对应源码、完整许可证与资源清单；ad-hoc 签名不等于 Developer ID 签名或 Apple 公证。
