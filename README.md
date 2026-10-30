# EndNote GPT Reader · 阅读与参考文献入库

A Windows Codex skill for reading EndNote papers, summarizing and translating them, and building an EndNote library from an article's reference list.

这是 **Codex skill**，操作入口在 Codex。它支持：

- 从 EndNote XML 和关联 PDF 读取论文，生成带 PDF 页码的总结、翻译与比较。
- 从给定文章提取参考文献，核对书目身份，下载可获取的正确全文。
- 准备去重 RIS、附件计划和完整台账，通过 EndNote 支持的界面导入、附 PDF。
- 回读导出数据核验记录与附件；未完成项给出原因和具体修复办法。

不会增加 EndNote 内部 GPT 按钮。真实 EndNote 操作需要 Windows、可运行的 EndNote 桌面版及 Codex 中可用的电脑控制工具。没有这些条件时，交付导入包与接续步骤，不宣称已经入库。

## 安装与使用

下载 `endnote-gpt-reader-windows-v2.zip` 并完整解压，运行包内 `Install-Skill.ps1`，或手动把 `endnote-gpt-reader` 文件夹复制到正常 Codex skills 目录。已有同名旧版时先备份再更新，保留原安装位置。

需要 Python 3.10+；PDF 解析使用 PyMuPDF 或 pypdf。使用当前 Codex 模型，不单独配置 GPT Key。完整步骤见 [使用说明](使用说明.md)。

```text
使用 $endnote-gpt-reader 读取这篇文章，提取并核对全部参考文献，
下载可获取的全文，在新的 EndNote 专用库中导入记录与附件。
逐项核验；未完成项给出具体原因和修改办法。
```

输入文章文件或本地路径；使用已有库时明确给 `.enl` 完整路径。用户文章、论文、文献库和日志存于外部任务目录，不进入 skill 安装目录或本仓库。

## 验证范围

包含脚本测试、合成前向试用及真实 Crossref 元数据查询检查。未使用用户文章完成真实 EndNote 桌面端到端验收。下载到文件、生成 RIS、记录入库、附件复制入库、从 EndNote 打开是不同状态，台账分别记录。XML 是导出快照；未取得全文的条目不会伪报成功。

可运行离线测试：

```powershell
python -X utf8 -m unittest discover -s endnote-gpt-reader/scripts -p "test_*.py" -v
```

验证细节见 [验证记录](VALIDATION.json)。通用代码不包含真实论文数据或凭据。
