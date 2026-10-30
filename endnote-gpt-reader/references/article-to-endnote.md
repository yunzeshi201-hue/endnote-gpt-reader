# 从一篇文章建立参考文献库

这个流程处理用户给定文章的全部参考文献，并下载可获取的全文、导入 Windows EndNote、验证附件，最后返回未完成项与修复办法。无需先选研究方向，也不自动扩展成整个领域的综述。

## 1. 输入与范围

使用用户提供的 PDF、Word、文本、DOI 或公开文章链接。没有文章时请用户提供文件或路径，不搜索整台电脑猜文章。链接指向的版本须核对题名、作者、年份；只有摘要而无参考文献列表时，不能宣称已提取全部参考文献。

复用用户指定的目标 `.enl`。未指定现有库时，默认在任务输出目录创建一个文章专用的新库，先说明库名和路径，由 EndNote 的 File > New 创建；若当前有多个候选库、或用户说“当前库”但无法识别，先确认目标。不要直接生成一个假的空 `.enl`。

原文章、下载 PDF、清单、RIS、日志和库保存在任务目录中，遵从当前输出位置约定；不要塞入安装后的 skill 文件夹。准备阶段不需要提前打开或改动用户其他文献库。

## 2. 完整提取原始引用

用已有 PDF/文档工具读取 References/Bibliography/参考文献章节及跨页内容；需要时渲染末尾页或 OCR，保留物理页码。逐条保存原始引文，赋予稳定 ID（例如 `ref-001`）。处理换行、双栏、连字符、合并引用及无编号作者年份格式。

交叉核对列表起止、连续编号、附录及用户提供的补充材料。不能只用正文 DOI 正则，正文可能出现不是被引文献的 DOI；也不能把 Crossref `reference-count` 当作原文实际列表。保持原始条目总数与去重后论文数分开。

用户给了全文时优先以实际参考文献列表为准；Crossref 的 references 可作补充核对，但未登记的项目不能无声消失。尚未读到完整列表时令 `source_article.reference_list_complete=false`，报告提取缺口，继续可确认条目。清单结构见 [台账与辅助命令](reference-manifest.md)。

## 3. 核验每一条身份

优先用 DOI 查出版社/登记元数据；没有 DOI 时按题名、作者、年份和期刊检索候选，书籍/章节/标准/软件等使用对应的 ISBN、官方目录、版本号等证据。保留无 DOI 的真实文献，不为凑齐 DOI 捏造标识。

内置助手可查 Crossref：

```powershell
python scripts/reference_pipeline.py crossref --query '题名 作者 年份' --output candidates-ref-001.json --limit 5
python scripts/reference_pipeline.py crossref --doi '10.xxxx/实际DOI' --output doi-ref-001.json
```

这些结果只是候选。比对原始引文与候选的题名、作者、年份、刊物/卷页，处理在线/印刷年份、预印本/发表版区别，然后将证据写进 `metadata.verified_sources`，才能设 `status=verified`。无法唯一匹配的保持 `ambiguous`，查无结果保持 `unresolved`；不给未核验候选自动导出正式记录。

联网查询只发所需的公开书目信息，不上传用户文章全文、私有批注或无关路径。使用现有联网工具、官方 API 或出版社页面；跨网络失败不等于查无文献。

## 4. 获取和验证 PDF

优先出版社开放全文、PMC、arXiv、机构仓储、作者公开稿或用户已有授权访问。区分发表版、接受稿、预印本和补充材料；优先取得对应版本，不把相近题名的另一篇论文当作目标。遇到登录或订阅限制时由用户完成正常授权，不绕过访问控制。

若已有可下载的公开 HTTPS PDF URL：

```powershell
python scripts/reference_pipeline.py download --url 'https://正式来源/真实PDF链接' --output 'pdfs/ref-001.pdf' --report 'downloads/ref-001.json'
```

下载助手检查有效 PDF 及页数，记录来源、文件大小和 SHA256；这还不能证明内容与书目一致。必须读取首页/出版信息并核对题名、作者、DOI，必要时检查后续页和版本信息。PDF 是用户提供的扫描件时使用 OCR 或看图核对；HTML 登录页、错误页、损坏文件、另一篇论文及补充材料不能标为正确全文。

没有直链时用正常浏览/下载工具获取文件，再检查结构、哈希和内容，填入台账。保留无法下载的已核验文献元数据，写明原因，不自动删除，也不把只有摘要记为全文。对一次检索失败适度切换来源；相同失败最多重试两次，随后记录阻碍并处理其他条目。

## 5. 生成可恢复的导入包

整理台账，打开准确目标库并取得导入前 XML 快照（新空库可省略）。用助手按已核验身份去重：

```powershell
python scripts/reference_pipeline.py prepare --manifest 'references.json' --existing-xml 'before.xml' --pdf-root 'Target.Data/PDF' --out-dir 'import-batch-01'
```

输出 RIS 元数据、PDF 附件计划、完整 ledger 与修复报告。没有 `before.xml` 时去掉该参数。不要让 EndNote 只从一堆 PDF 猜书目：部分 PDF 无 DOI、多 DOI 或元数据不全，会造成错误或重复。

默认 RIS 不使用未经当前版本验证的 `L1 file:///...` 自动附加捷径。记录先导入，再按照附件计划精确附到相应条目。原文引用位置到去重后论文的映射必须保留，台账不得因多个引用指向同一论文而丢失原始条目。

## 6. 真实 EndNote 操作与核验

按 [Windows 导入](windows-import.md) 用当前可用的受支持电脑控制工具完成。没有控制能力或软件缺失时，不能用“已生成导入文件”代替实际入库；交付现有文件与具体接续步骤。

导入后将目标库重新导出为 `after.xml`，运行：

```powershell
python scripts/reference_pipeline.py verify --manifest 'import-batch-01/ledger.json' --endnote-xml 'after.xml' --pdf-root 'Target.Data/PDF' --out-dir 'verification-01'
```

同时在 EndNote 实际打开每篇应附的 PDF，记录目标库完整路径、条目编号、题名、相对附件和打开结果。脚本查到文件不等于 EndNote 已实际打开；绝对链接能打开也不等于 PDF 已复制到目标 `.Data\PDF`。

## 7. 最终报告和修复

分别报告原始引文条目、去重后论文、身份已核验、有效全文已下载、目标库中记录、附件哈希匹配、库内相对附件、EndNote 实际打开的数量。已有记录和本批新增记录分开统计。没有 PDF 的项目依然出现在清单中。

只有原文列表完整且每个目标都有可核查结果，才可称本范围已处理完；“全部文件已导入”还需要每个下载附件实际入库并打开。任何缺口按 [失败与修复](failure-and-repair.md) 列出状态、原因、修改办法和恢复入口。重跑仅处理失败/未完成项，不重新导入已确认的条目或重复附文件。

来源核对：2026-10-30。[Crossref 官方 REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api) 支持 DOI/书目候选查询；[Unpaywall 官方 API](https://unpaywall.org/api) 可提供开放全文位置，但要求使用者提供真实 email 参数，本 skill 不填造邮箱、不把它设为必须依赖。
