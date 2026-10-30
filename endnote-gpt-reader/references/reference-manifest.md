# 台账与辅助命令

台账由执行 skill 的 Codex 从实际文章与检索证据填写，脚本不从一条不完整字符串自动断言匹配结果。不需要 GPT API Key；辅助脚本中的网络请求只用于书目候选和明确的 PDF 下载。

## 输入 references.json

UTF-8 JSON，最小结构如下。示例处于未核验状态，不能当成实际论文导入。

```json
{
  "schema_version": 2,
  "source_article": {
    "path": "D:/References/source-article.pdf",
    "reference_list_complete": false,
    "expected_reference_count": null,
    "pages": [8, 9]
  },
  "references": [
    {
      "id": "ref-001",
      "raw_citation": "从原文章逐字保留的第1条参考文献",
      "source_pages": [8],
      "metadata": {
        "status": "unresolved",
        "title": "从引文提取的题名，需核验",
        "doi": "",
        "authors": [],
        "year": "",
        "journal": "",
        "volume": "",
        "issue": "",
        "pages": "",
        "type": "journal-article",
        "verified_sources": []
      },
      "pdf": {
        "status": "missing",
        "path": null,
        "source_url": "",
        "sha256": ""
      }
    }
  ]
}
```

- `source_article.path` 是原文的真实绝对路径；文章来自链接时先取得并保存实际原文。`pages` 对 PDF 用从 1 开始的物理页；无分页文本可记录参考文献章节或行号定位。
- `reference_list_complete` 只有在实际检查全部参考文献页后才为 true。`expected_reference_count` 有可靠原文计数时填写；它统计原始引文条目，不是去重后的论文数。
- 每个原始引文各有唯一 `id`，去重不得删掉原始引用位置。`source_pages` 保留每条位置。
- `metadata.status` 为 `verified`、`unresolved` 或 `ambiguous`。verified 还要求真实题名、作者、四位出版年及核对来源，不能只改状态。DOI 可为空；填入时应是裸 DOI 或可规范化的 DOI 链接。
- 作者用字符串列表，个人作者按 `Family, Given`；机构作者用机构名称。题名、年份、第一作者用于无 DOI 时的严格匹配，不做相似题名自动归并。
- `type` 使用实际文献类型，例如 journal-article、book、book-chapter、proceedings-article、dissertation、dataset；未支持类型会作为通用记录导出，额外类型专用字段需要由 host 在 EndNote 核对补齐，不伪装全部书目字段无损。
- `pdf.status=downloaded` 表示已有可读文件；`path` 必须是绝对路径，`sha256` 必须来自实际文件的64位十六进制哈希。下载报告可提供它们。host 还须看 PDF 确认论文身份、版本与全文属性。

## 命令行为

在 skill 的实际 scripts 所在目录或用脚本绝对路径调用。输出文件已存在时拒绝覆盖；`prepare` 和 `verify` 的输出目录必须不存在，父目录应已创建。下载 PDF 和报告的父目录也要先创建。

| 命令 | 输出与界限 |
| --- | --- |
| `crossref --query TEXT --output candidates.json --limit 5` | 返回候选，状态保留 unresolved；得分高不表示已核验。也可用互斥的 `--doi`。 |
| `download --url HTTPS_URL --output ref-001.pdf --report download-001.json` | 默认最大50 MiB、最多两次尝试、HTTPS、校验 PDF 结构与页数、记录 SHA256。登录/签名链接等用合适的受支持下载工具，报告不保留 URL 查询中的令牌。 |
| `prepare --manifest references.json --out-dir import-batch` | 仅导出已核验元数据为 RIS；去重后映射所有原始 ID，逐项检查计划 PDF，输出附件计划、ledger 与修复报告。 |
| `prepare ... --existing-xml before.xml --pdf-root Target.Data/PDF` | 在用户准确目标库的完整导出中查重；已有记录不再次导入，含糊/冲突项待修复。新库没有 before XML 时省略这两个可选参数。 |
| `verify --manifest import-batch/ledger.json --endnote-xml after.xml --pdf-root Target.Data/PDF --out-dir verification` | 从新的 EndNote 导出查记录与附件哈希；区分库内相对附件、库内绝对链接、库外链接。不会假定 UI 已打开。 |

`prepare` 输出：

- `references.ris`：真正待新增的元数据，不含未经当前版本验证的自动附件字段。
- `attachment-plan.csv`：所有去重后论文的附件对应与操作，必须查看 action，不对 repair 项盲附。
- `ledger.json`：保留 `references` 原始条目、去重 `papers`、处理状态、错误、文件校验和及计数。
- `repair-report.md`：中文逐项原因与修改办法。

`verify` 输出 `verification.json` 与更新的 `repair-report.md`，不覆盖准备阶段 ledger。

## 计数与完成条件

`counts` 分别报告 `original_references`、`unique_papers`、`metadata_verified`、`valid_pdfs`、`library_records`、`attachments_verified`、`attachments_within_library`、`new_ris_records`、`metadata_unresolved_references`。导入前的 library_records 只反映输入基线；是否已做回读看 `library_readback_performed`。

- `metadata_eligible=true` 才可作为已核验书目导入。
- `library_match.status=unique` 是在输入 XML 中查到一个匹配，不单独保证当前 UI 的目标库正确；冲突和重复状态必须处理。
- `attachment_verification.status=verified` 仅指找到同哈希文件。
- `copied_into_library_verified=true` 还要求附件在提供的目标 PDF 根目录内，并以相对附件链接回读。
- `data_readback_complete` 只表示该数据核验阶段的条件满足，不代表整个任务完成。
- `all_complete` 在脚本输出中保留 false；脚本没有实际操纵 EndNote，所以不能证明逐条打开。不要手改这个字段伪造通过。

真实操作完成后，host 另写 `endnote-ui-evidence.json` 或等价逐条证据：目标 `.enl` 完整位置、paper_id/原始 ID、EndNote 记录号、PDF 路径与 SHA256、实际打开动作的返回状态、题名/版本核对、操作时间。只记录真正观察到的动作；有截图就引用真实截图，无截图不造路径。

最终报告结合数据回读和实际 UI 证据判定范围内哪些已完成、哪些仅有记录、哪些还缺全文。书籍/网站等没有 PDF 的引文如实记录文件未取得，不把它们当作已附 PDF 来凑完成率。
