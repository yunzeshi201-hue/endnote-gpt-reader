# Windows 首次设置

本 skill 在本地 Codex 中检索 EndNote XML，并读取其中指向的 PDF，使用当前模型总结和翻译，不额外调用 OpenAI API。它不会识别 EndNote 窗口当前选中的条目；在 Codex 里提供论文题名、DOI 或记录号即可。给文章后检索参考文献、下载和导入属于新增的 [文章入库流程](article-to-endnote.md)，操作实际 EndNote 还需要当前环境提供受支持的 Windows 控制工具。

需要 Codex 对目标文件夹有读取权限、Python 3.10+，以及 PyMuPDF 或 pypdf 中任意一个。目录检索不需要 PDF 库。优先使用已有依赖。

## 导出目录

1. 在 EndNote 打开文献库，选择 All References 或目标文献组。
2. 用 File → Export，保存类型选 XML（可能显示 XML / EndNote XML）。只导出选中条目时才勾选 Export Selected References；全库导出时核对勾选状态和记录数。
3. 保存到固定位置，如 `D:\References\library.xml`。这是示例，必须使用真实路径，不移动原有库。
4. 找到 PDF 目录，常见为 `.enl` 旁的 `Library.Data\PDF`，用户也可能使用绝对路径附件，按实际核实。

XML 不包含 PDF 全文字节。`internal-pdf://...` 附件的 `--pdf-root` 应指向 **PDF 这一层**，不是 `.Data`。路径失效时修复指定目录，不在整块硬盘按同名文件猜测附件。

## 检查与记住连接

取得路径后运行 `status`，检查记录数、XML 修改时间和附件。XML 全部使用绝对附件路径时可省略 `--pdf-root`。查找一篇已知论文，读取详情和 PDF 第 1 页，核对题名、页数和内容。

用户要求记住连接时，在 Codex 配置目录保存 `endnote-gpt-reader-library.json`。有 `CODEX_HOME` 用它，否则用 `%USERPROFILE%\.codex`；不改写 `config.toml`。已有配置代表现有连接，用户未要求换库时保留。字段示例：

```json
{
  "schema_version": 1,
  "library_label": "我的文献库",
  "xml": "D:/References/library.xml",
  "pdf_root": "D:/References/Library.Data/PDF"
}
```

保存真实路径，不把示例当成已配置；未要求持久化时直接复用对话路径。配置不存密钥。文件夹不可读时遵循 Codex 的文件夹授权流程，不绕过访问控制。

## Python 依赖

依次检查 `python`、`py -3` 或环境提供的 Python。检查已有 PDF 库可运行：

```powershell
python -c "import importlib.util; print({m: bool(importlib.util.find_spec(m)) for m in ['pymupdf','pypdf']})"
```

两个库都没有时，为所用 Python 安装一个即可，例如 `python -m pip install pypdf`，遵循环境权限。不能安装时仍可检索元数据，标明全文读取未就绪；不要索取 GPT Key。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 新增文献搜不到 | 在 EndNote 重新导出 XML，使用同一路径；不是实时同步。 |
| 有记录却没有全文 | 看详情的附件路径和状态，确认 PDF 已下载且目录正确。 |
| 多个 PDF | 查看附件编号，区分主文和补充材料，用 `--attachment-index` 选择。 |
| 提取为空 | 标记具体页，使用已有 OCR/图像工具；不把空文本当成空页面。 |
| 页码不一致 | 工具使用从 1 开始的 PDF 物理页码。 |
| 只有 .enl | 导出 XML；本工具不直接打开或修改内部数据库。 |

## 核对依据

阅读连接说明核对于 2026-10-29；参考文献入库扩展于 2026-10-30。

- [EndNote 官方 XML 导出说明](https://docs.endnote.com/docs/endnote/2025/v1/windows/en/content/15independentbibs_export/exporting_to_endnote_xml.htm)
- [EndNote 官方 Research Assistant 说明](https://docs.endnote.com/docs/endnote/2025/v1/windows/en/content/03editref/ai_in_endnote_research_assistant.htm)：官方内置 AI 与本 skill 的 Codex 路径不同。

真实 XML 和各版本兼容性需要实际样本验证，验证范围见包外记录。
