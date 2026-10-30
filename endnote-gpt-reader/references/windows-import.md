# Windows EndNote 实际导入与逐项验收

本流程用于已完成身份核验和全文获取的参考文献批次。只完善 skill 时，不启动 EndNote，也不声称完成了实际导入。
实际任务中，目标是让记录及对应 PDF 真正进入指定库，并保留可恢复的逐项证据。

## 1. 建立可操作环境

1. 确认当前电脑为 Windows，且已安装可运行的 EndNote 桌面版；记录实际版本。
2. 发现当前环境可用、受支持的 Windows 电脑控制工具，并先阅读其适用的操作说明。
3. 使用该工具查看当前应用状态、真实打开 EndNote，并根据每一步新返回的界面继续操作。
4. 不硬编码屏幕坐标；不凭历史截图、旧窗口状态或猜测执行点击。
5. 不臆造 EndNote COM 方法、命令行导入开关、数据库写入接口或成功返回值。
6. 没有可用电脑控制工具时，报告“当前环境不能控制 Windows EndNote”，交付可导入材料及具体人工步骤。
7. 不修改 PowerShell 执行策略、文件关联、EndNote 全局首选项或默认同步库来实现本次任务。

## 2. 确定并保护准确目标库

- 用户未指定库：在本任务输出目录创建一个名称唯一的新库，通过 EndNote 的 `File > New` 完成。
- `.enl` 必须由 EndNote 创建；不得写一个空文件、重命名 XML 或手工伪造 `.Data` 数据库。
- 新库不得覆盖同名现有文件，也不得因启动时自动打开其他库而改为向那个库导入。
- 用户指定已有库：用 `File > Open Library` 选择其准确 `.enl` 路径，核对读写权限。
- 在 EndNote 2025 的 `Library Status` 中核对完整 `Location`，并记录库名、记录数及附件数。
- 旧版可使用当前界面实际提供的 `Library Summary` 等受支持入口核对完整库位置。
- 同名库、多个已开库或无法确认完整位置时，先消除歧义；不能向“看起来像目标”的库写入。
- 对用户已授权写入的已有库，先通过 EndNote 创建包含附件的 `.enlx` 备份；保留其路径和完成证据。
- 若当前版本提供其他已确认可用的受支持备份方式，也可使用；备份失败则先修复，不继续修改已有库。
- 不把导出 XML 当作完整附件备份，不擅自启用 Sync，不关闭用户有未保存改动的窗口。

官方依据：[打开库](https://docs.endnote.com/docs/endnote/2025/v1/windows/en/content/02library/opening_a_library.htm)、[Library Status 的位置与计数](https://docs.endnote.com/docs/endnote/2025/v1/windows/en/content/22enweb/library_status_dialog.htm)。

## 3. 取得导入前基线并准备差量

1. 在准确目标库中显示 `All References`，确认不是仅导出当前搜索结果或选中子集。
2. 通过 EndNote 导出完整 `before.xml`；选择 XML 格式，核对“仅导出选中记录”的实际状态。
3. 记录对应的真实 `Target.Data/PDF` 路径；新空库尚无 PDF 子目录时，明确记录该状态。
4. 新库显示 0 条而软件无法导出时，可记录明确标为“新空库、UI 计数 0”的空基线；不得伪称软件已导出。
5. 将基线 XML、PDF 根目录、已核验的 `ledger.json` 交给 `reference_pipeline.py` 的 prepare 阶段。
6. 按 prepare 输出区分 `already_present`、待导入记录及需要补附 PDF 的已有记录。
7. `already_present` 不再次导入；已有记录缺 PDF 时，只执行该记录尚未完成的附件步骤。
8. 身份模糊、同题不同版本或多条可能匹配时保留为待处理项，不自动选第一条或合并记录。
9. 保存 prepare 生成的差量 RIS、`attachment-plan` 和状态清单；恢复时以这些证据定位已完成步骤。
10. 不依赖 EndNote 默认重复规则替代 DOI、题名、作者、年份和版本身份的预核验。

EndNote 默认重复标准为 Author、Year、Title、Reference Type；这不等同于 DOI 去重。
[官方导入与重复选项](https://docs.endnote.com/docs/endnote/2025/v1/windows/en/content/08import/import_text_files-win.htm)也说明 `Imported References` 是会被下次导入替换的临时组。

## 4. 在真实界面导入参考文献记录

1. 再核对当前库的完整 Location 与基线一致，记录导入前 `All References` 计数。
2. 使用 `File > Import > File`，选择 prepare 生成的差量 RIS。
3. `Import Option` 选择 `Reference Manager (RIS)`；必要时通过当前软件提供的其他过滤器列表选择。
4. 对 UTF-8 RIS，核对 `Text Translation` 为对应 UTF-8 选项，特别检查中文及带变音符号作者。
5. 可将 `Duplicates` 设为 `Discard Duplicates` 作为额外防护；不得据此跳过差量准备或验收。
6. 点击真实 Import，等待界面进度结束，读取软件给出的错误或导入结果。
7. 对照 `Imported References`、目标库总数和待导入清单核对实际新增记录；保存差异。
8. 记录被跳过或未出现的条目及原因，不能把预期数当作实际成功数。
9. 在目标库未确认前，不通过双击 RIS 或文件关联触发 Direct Export，以免误导入其他已开库。

官方支持 [RIS 与 EndNote Generated XML 导入选项](https://docs.endnote.com/docs/endnote/2025/v1/windows/en/content/08import/import_options.htm)。
官方 RIS 将 `L1` 定义为 File Attachments，但本流程不承诺 `L1 file:///...` 或 XML 本地 URL 会自动复制 PDF。
如材料包含候选附件链接，仍需按下述步骤逐条确认；记录导入成功不代表附件已成功保存。

## 5. 按 attachment-plan 精确附加 PDF

1. 每次只处理计划中的一条：用已核验 DOI 定位，再交叉核对题名、作者、年份和文献类型。
2. 无 DOI 时使用计划中的完整书目信息与来源证据；匹配不唯一则暂停该条附件操作并记录修复需求。
3. 区分正式发表版、预印本、勘误、撤稿声明及补充材料，不把它们随意替换或合并。
4. 先核对该记录现有附件；以已取得的库内附件路径和 SHA-256 与计划中的下载文件比对。
5. 已有相同内容的附件就跳过；文件名相同不证明内容相同，文件名不同也不证明需要再附。
6. 不自动移除或覆盖内容不同的已有附件；补充材料须有明确用途和与主文献的关系。
7. 对确认尚未附加的文件，选中准确记录，执行 `References > File Attachments > Attach File`。
8. 选择 attachment-plan 指向的本地 PDF，并勾选复制到默认附件目录且创建相对链接的选项。
9. 确认 Open，保存记录；该单次对话框选择无需更改全局 URLs & Links 首选项。
10. 若已有有效绝对链接，需先备份，再按计划使用 `Convert to Relative Links`，之后重新验证复制结果。
11. 不把 PDF 批量导入功能当作给既有记录附文件的替代；它可能创建新记录或读取错误 DOI。
12. 不以 PDF 内自动识别到的 DOI 一次结果认定身份；正文参考文献里的 DOI 可能属于其他论文。
13. 不手工向 `.Data` 目录塞文件冒充附件成功，也不重命名或改写 EndNote 管理的附件文件。

官方依据：[Attach File 与复制选项](https://docs.endnote.com/docs/endnote/2025/v1/windows/en/content/03editref/file_attachments.htm)、[相对链接与重附行为](https://docs.endnote.com/docs/endnote/2025/v1/windows/en/content/03editref/selctngrltvpthabspthforfileatt.htm)。

## 6. 导出后验收与幂等恢复

1. 导出准确目标库的完整 `after.xml`，保留 before、after、目标 `.enl` 位置和实际 PDF 根目录。
2. 运行辅助核验命令；下列路径为占位示例，按本次任务的真实路径替换并正确引用含空格路径：

```text
python reference_pipeline.py verify --manifest ledger.json --endnote-xml after.xml --pdf-root Target.Data/PDF --out-dir verification
```

3. 将 verify 结果用于辅助核对记录身份、附件链接和本地文件；脚本结果不能替代真实 UI 检查。
4. 每条声称已完成的 PDF 都须从 EndNote 的对应记录实际打开，检查题名页及内容身份、可读性。
5. 核对附件是目标库 `.Data/PDF` 中的可用相对附件，而非仅指向下载目录的外部绝对链接。
6. 核对 `records-state`、ledger、附件计划、导出 XML 和 UI 逐项一致；补充材料及版本分别计数。
7. 总数相符、存在回形针图标、导出 XML 含路径或文件下载成功，均不能单独证明任务完成。
8. 重跑只处理未完成项：先重建当前库基线，再排除已存在记录与已有同 hash 附件。
9. 若重复判定导致缺记录、附件失效或条目身份不一致，定位该条修复；不无条件重导整个批次。
10. 只有实际核验通过的条目才能标为完成；保留所有未解决项、证据及下一步修复办法。

## 7. 失败时保留可继续的材料

- 未安装或无法启动：报告已观察的错误，提示安装/修复 EndNote 桌面版后重新打开目标库；保留 RIS 和附件计划。
- 当前平台不支持或缺控制工具：说明无法真实控制应用，提供本页对应的人工导入步骤，不报告已导入。
- 权限或工具调用失败：记录出错步骤与目标路径，提示恢复合法读写权限或受支持控制连接后从未完成项恢复。
- 库无法打开或身份不明：保留原文件及备份，不自行恢复、转换、覆盖或写入其他库；明确需要核实的库路径。
- PDF 不可访问、非全文、损坏或身份不符：保留已核验书目，标明具体失败与合法获取/重新核验办法，不附错文件凑数。
- 验收失败：输出已成功、仅记录、仅下载、待附、待核验及失败明细，并保留 ledger、RIS、attachment-plan 和核验结果。
