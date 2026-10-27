# organise_sra 设计文档

版本 0.4.0（2026-10-28；0.1.0 为 2026-10-27）。来源：yzz 分类工作流 `sra` 模块（需求表见文末附录），拆为独立软件。

## 1. 定位

**一句话**：给定一个类群，盘点它在 NCBI SRA 的全部原始测序资源，回答"有哪些物种、哪些样本、什么类型的数据（浅层/深度 WGS、长读长、转录组、Hi-C……）、多少数据量、从哪里来"，并给出可直接下载的 run 清单。

**用户**：分类学、系统发育、比较基因组研究者。典型问题："多鳞虫科有哪些物种做过 genome skimming？""环节动物哪些种已有深度 WGS + 转录组，可以直接用？"

**分工**（用户 2026-10-27 决定）：

| 环节 | 用谁 |
|---|---|
| 取 RunInfo（类群 / SRR 列表 / 网页 csv） | organise_sra |
| 取元数据（标题、样本属性） | organise_sra 内置（默认），或 kingfisher annotate 的结果（见 §5） |
| 分类阶元（门–种） | organise_sra |
| 数据类型判别、总表、筛选 | organise_sra |
| 下载 fastq | **kingfisher**；organise_sra 的 `download`（0.2.0 起）只做批量调度、续传与核对，见 [kingfisher.md](kingfisher.md) |

**不做**：自己实现下载与格式转换（交给 kingfisher / sra-tools）；组装、注释；物种有效名校订（另用 WoRMS 工具）；图形界面。

## 2. 设计原则

1. **不静默丢数据**：检索条数与取回条数核对（db=sra 的 Count = 唯一 Experiment 数）；NCBI 返回错误页、`<ERROR>`、截断响应一律重试，仍失败则非零退出；查不到的 run 单独列出。
2. **原始值不改**：RunInfo 原列名、原值保留；新增列沿用 kingfisher 同名字段；样本属性列名 = XML 原 TAG；所有推断出来的列带 `_derived` 后缀，并写明命中规则与数值（`category_rule_derived`）。
3. **零依赖**：只用 Python 标准库（csv、zipfile+XML 写 xlsx、urllib、xml.etree）。`pip install` 不拉任何包，离线机器拷过去就能用。
4. **一步一文件**：每步只读上一步的 TSV、只写自己的 TSV；失败可单独重跑；换筛选条件不必重新联网。
5. **按 taxid 子树检索**：`txid<id>[Organism:exp]`，不用自由文本、不按 BioProject 绕路（实测理由见 README）。
6. **贴近用户原流程**：步骤与用户旧做法一一对应（网页 RunInfo → kingfisher annotate → 补阶元 → 整理 Excel → 筛选 → kingfisher get），只是自动化。

## 3. 流程与数据契约

```
类群名 / --runs / --runinfo
        │
  01 runinfo ──► runinfo.tsv, manifest.json, runs_not_found.txt
        │
  02 annotate ─► annotate.tsv, annotate_missing.txt          （或: kingfisher annotate 结果, §5）
        │
  03 taxonomy ─► taxonomy.tsv, taxonomy_missing.txt
        │
  04 table ────► table.tsv, samples.tsv, species_summary.tsv, <Taxon>_SRA_<date>.xlsx
        │
  05 select ───► selected_<tag>.tsv, run_list_<tag>.txt
        │
  06 download ─► fastq/, download_status.tsv, runs_failed.txt   （调用 kingfisher get; 不在 all 中）
```

| 文件 | 一行是 | 内容 |
|---|---|---|
| `runinfo.tsv` | 一个 run | NCBI RunInfo 原表（Run、Experiment、BioSample、BioProject、bases、spots、LibraryStrategy/Source/Selection/Layout、Platform、Model、TaxID、ScientificName……） |
| `manifest.json` | — | 检索式、taxid、esearch Count、取回 experiment/run 数、核对结果 `ok`、问题列表 |
| `annotate.tsv` | 一个 run | experiment_title、design_description、study_title、study_abstract、study_links、提交单位 + 样本全部属性 |
| `taxonomy.tsv` | 一个 TaxID | phylum…species，以及查询时 NCBI 合并后的新 TaxID |
| `table.tsv` | 一个 run | 以上合并 + 派生列（数据类型、样本总量 Gb 等），行数 = runinfo |
| `samples.tsv` | 一个样本×数据类型 | 挑数据用的视图：总 Gb、run 数、run 列表、采集地、凭证号 |
| `species_summary.tsv` | 一个 TaxID | 各数据类型的样本数与 Gb |
| `selected_<tag>.tsv` / `run_list_<tag>.txt` | 一个样本 / 一个 run | 筛选结果；run 清单一行一个，直接给 `kingfisher get --run-identifiers-list` |
| Excel | — | Summary / Samples / shallow_WGS / deep_WGS / long_read_WGS / RNA-Seq / Hi-C / Other / Runs / QC / Query |

## 4. 数据类型判别（04）

NCBI 没有"浅层测序"字段，按**样本**数据量推断（同一 BioSample、同 LibraryStrategy、同平台类型的 run 碱基数之和；BioSample 为空时用 Run）。规则顺序与阈值在 `rules.json`，用户可用 `table --rules my.json` 替换：

1. WGS + METAGENOMIC/METATRANSCRIPTOMIC → `WGS_metagenomic`
2. WGS + PacBio/Nanopore → `long_read_WGS`
3. WGS 短读长：< 0.5 Gb `low_data_WGS`；0.5–10 Gb `shallow_WGS`；≥ 10 Gb `deep_WGS`
4. RNA-Seq + 长读长 → `long_read_RNA`
5. 其余 = LibraryStrategy 原值

阈值 0.5 / 10 Gb 是设计方初值（依据：Annelida 每样本 1–3 Gb 峰 251 个样本）。WGS + LibrarySelection=PCR **不排除**，只在 QC 标出（Annelida 实测 1642 条多为 genome skimming / 基因组）。

**已知局限**：数据量与"浅层/深度"的关系取决于基因组大小（1 Gb 对 0.5 Gb 基因组是 2×，对 5 Gb 基因组只有 0.2×）。路线图 §8 用基因组大小估算覆盖度。

## 5. 元数据来源：内置 vs kingfisher

两种来源产出**同形**的 `annotate.tsv`（以 `Run` 为键），后续步骤不区分：

| | 内置（默认） | `annotate --kingfisher kf.tsv` |
|---|---|---|
| 怎么取 | efetch db=sra 完整 XML（与 kingfisher 同一数据源、同样字段名） | 读用户已有的 `kingfisher annotate -a -f tsv` 结果，不联网 |
| 列 | 标题、摘要、提交单位、样本全部属性、`bioproject`、`sample_description` | kingfisher 的全部列（多机构地址、read 长度、submitter 等），`run` → `Run` |
| 编码 | 正确（urllib 取字节，XML 按 UTF-8 解析） | kingfisher 把无 charset 的 UTF-8 按 Latin-1 解码（实测 49/525 run 乱码），导入时**自动还原** |
| 联系人姓名/邮箱 | 不取 | 导入时丢弃 |

Polynoidae 实测（2026-10-27）：用 kingfisher 结果导入后 525/525 run、乱码 0；与内置结果同名列逐格比较，只有 `sample_name` 不同（474 格），因为 kingfisher 的 `sample_name` 是合成列（取 `sample name` 属性，没有则取 Title、library_name）。数据类型、样本、筛选结果完全相同。

推荐：直接用内置（一条命令、无需安装 kingfisher）；已经有 kingfisher 结果、或需要 read 长度等额外列时用 `--kingfisher`。

**BioProject 回填**（来自实测）：RunInfo 的 BioProject 可能为空（Polynoidae 51 个 DRR），XML 的 `STUDY/IDENTIFIERS/EXTERNAL_ID[@namespace="BioProject"]` 有值。`table` 生成 `BioProject_derived` = RunInfo 值，为空时取 XML 值；原 `BioProject` 列不改。Samples 表与 `select` 的分组都用它，避免不同研究因 BioProject 为空被并到同一组。

## 6. 命令行

```
organise-sra all <taxon> [--runs F | --runinfo F] [--query Q] [--taxid N] [-o DIR] [--report-dir DIR] [--email E] [--api-key K]
organise-sra runinfo|annotate|taxonomy|table|select <taxon> [...]    # 也可用 01–05
organise-sra select <taxon> --type shallow_WGS,deep_WGS,RNA-Seq --platform ILLUMINA --layout PAIRED --top 3 --per sample --tag default
```

身份优先级：命令行 > 环境变量 `NCBI_EMAIL`/`NCBI_API_KEY` > `~/.config/organise_sra/ncbi.json`（或 `$ORGANISE_SRA_CONFIG`）。key 不打印、不写入输出。

## 7. 可靠性与性能

| 风险 | 处理 |
|---|---|
| 分页漏条 | history server 分页；唯一 Experiment = esearch Count，不符 → `ok=false`、退出码 1 |
| NCBI 错误页 / `<ERROR>` / JSON error / 截断响应 | 判为失败，指数退避重试 5 次；页内 run 数校验 |
| 检索式过长（HTTP 414） | 自动改 POST |
| 限速 | 有 key 0.11 s/次，无 key 0.35 s/次 |
| XML 实体攻击 | 含 `<!ENTITY` 的响应直接拒绝（标准库 ElementTree 不加载外部实体） |
| 合并错位 | 按 Run / TaxID 合并，断言行数不变（旧脚本按行号拼表曾错位） |
| 大类群 | Excel 流式写入；Annelida 14,641 run × 369 列：全流程约 8 分钟、峰值 0.7 GB |

测试：离线单元测试 73 个（假 HTTP 传输）；联网回归 `tests/check_polynoidae.py`。

## 8. 路线图

按对用户的价值排序；每项先写失败测试再实现。

| 版本 | 项 | 理由 / 依据 |
|---|---|---|
| 0.2 | `geo_loc_name_derived`、`lat_lon_derived`：把 ENA 系属性（`geographic location (country and/or sea)`、`(region and locality)`、`(latitude)`、`(longitude)`）归一到同一列 | 实测 Polynoidae 85 个 ERR run 的 `geo_loc_name` 全空，地名在另一套属性名里；所有工具都没做 |
| 0.2 | `organise-sra download <taxon> [--tag] [--threads]`：生成 `download_<tag>.sh`（kingfisher get 命令、方法回退顺序、输出目录、断点续跑）；`--execute` 在 PATH 有 kingfisher 时直接执行 | 用户分工：下载用 kingfisher；本软件只把命令写全、写对 |
| 0.2 | `--runs` 直接接受 `runs_not_found.txt` / `annotate_missing.txt` 重试 | 借鉴 q2-fondue 失败项回灌 |
| 0.3 | 增量更新：读上次的 `runinfo.tsv`，只取新增 run 的注释与阶元 | 需求 12；Annelida 全量约 8 分钟 |
| 0.3 | 覆盖度估算：按同种/同属基因组大小（GoaT API）把 Gb 换成 ×，浅层/深度按覆盖度判 | 需求 13；数据量阈值对基因组大小差异大的类群不准 |
| 0.3 | 可选列：机构地址、read 长度均值/标准差（XML `Statistics`） | 实测 kingfisher 有、我们没有 |
| 0.4 | 下载清单附 ENA md5 与文件大小（ENA filereport，只对已知 run，不作入口） | 借鉴 ffq；ENA 不作类群入口（实测召回 48%） |
| 0.4 | 多类群批量：`organise-sra all Polynoidae Nereididae ...`，汇总一张跨类群 Summary | 用户常同时盘点多个科 |

不做：ENA 作为检索入口（实测召回不全）；自己实现下载；`sra-db-be` 等非官方接口（iSeq issue #67 记录其元数据不完整、重复）。

## 9. 与 yzz 分类工作流的关系

yzz 的 `python3 yzz.py sra ...` 将改为调用本包（同 gb2taxonomy 的做法：`vendor/organise_sra`，`pip install -e`），输出目录用 `--outdir projects/<Taxon>/02_原始数据/sra --report-dir projects/<Taxon>/04_处理数据/sra`，保持现有路径不变。本包本身不知道 yzz 的目录约定。

## 10. 竞品对比

详见 [benchmark.md](benchmark.md)。要点：

- **实测**（Polynoidae 525 run，同机同网络）：kingfisher、pysradb、EDirect 与我们取回的数据**逐 run 一致**（EDirect RunInfo 47 列逐格相同），说明数据源与解析正确。差别在于：只有 organise_sra 能按类群取全、带分类阶元和数据类型、出 Excel；依赖 0 个（其他工具 33–154 个 conda 包）；kingfisher/pysradb 有 48–49 个 run 文本乱码；pysradb metadata 一次超过约 270 个 run 即崩溃（HTTP 414）；ffq 不含 LibraryStrategy 与样本属性，且对 DRR 全部失败；EDirect 取 RunInfo 要 6–7 分钟。
- **调研**（24 个工具）：没有工具同时做到"类群入口 + 条数核对 + 样本全部属性 + 门–种阶元 + 数据类型 + Excel/下载清单 + 零依赖"。最接近的是 kingfisher（元数据最全，但无类群入口与阶元）和 EDirect（积木，需要自己拼）。
- **我们的不足**（已按实测修复两项）：BioProject 不回填 → 已修复；`SAMPLE/DESCRIPTION` 漏取 → 已修复；缺机构地址、read 长度等列，ERR 样本地名未归一 → 路线图。

## 附录：需求表（沿用 yzz sra 模块）

| # | 需求 | 来源 | 状态 |
|---|---|---|---|
| 1 | 自动获取类群的 SRA RunInfo，不再手动从网页下载 | 用户 2026-10-27 | ✅ |
| 2 | 也可输入 SRR 号列表或网页下载的 RunInfo | 用户 | ✅ |
| 3 | 补 metadata（参考 kingfisher annotate） | 用户 | ✅ |
| 4 | 自动补分类信息（门–种），整合成系统总表 | 用户 | ✅ |
| 5 | 区分浅层 WGS / 全基因组 / 转录组等 | 用户 | ✅（阈值可改） |
| 6 | 减少打开文件、手动整理 Excel | 用户 | ✅ |
| 7 | 筛选后得到下载清单 | 用户旧脚本 | ✅ |
| 8 | 不自己实现下载，用 kingfisher | 用户 | ✅ |
| 9 | 不按 BioProject 绕路、不用自由文本检索 | 实测 | ✅ |
| 10 | 不硬编码 API key / 邮箱 | 旧脚本问题 | ✅ |
| 11 | 失败不静默 | 旧脚本问题 + 实测 | ✅ |
| 12 | 增量更新 | 设计 | ⏳ |
| 13 | 用基因组大小估算覆盖度 | 设计 | ⏳ |
| 14 | WGS+PCR 不排除，只在 QC 提示 | 实测 | ✅ |
| 15 | 1.5 万 run 规模可在合理时间/内存内出 Excel | 实测 | ✅ |
| 16 | 安装方便：只用 Python 标准库 | 用户 | ✅ |
| 17 | 做成通用软件，上传 GitHub `organise_sra` | 用户 2026-10-27 | ✅ 0.1.0 |
| 18 | 整合本地 kingfisher 批量下载脚本，方便以后下载（并行、md5、续传、BioProject、检查） | 用户 2026-10-28 | ✅ 0.2.0 `download` |
| 19 | 给 SRR 清单或表格某列，自动取分类阶元并写回原表 | 用户 2026-10-28 | ✅ 0.3.0 `lineage`；`--runs` 支持 `--column`/xlsx |
| 20 | 下载时可指定 kingfisher 的 conda 环境或路径，自动激活，默认自动检查可用软件 | 用户 2026-10-28 | ✅ 0.4.0 `--conda-env`/`--kingfisher`、`tools` |
| 21 | ascp 装好并测通 | 用户 2026-10-28 | ⚠️ 已装、找到可用密钥；本机网络 SSH 握手被干扰，需代理 TUN 模式（见 kingfisher.md §6.4）；自动检测，通了才用 |
| 22 | 自动整理某科/门/种的全基因组组装数据，免去手动筛选 | 用户 2026-10-28 | ✅ 0.4.0 `genome` |
| 23 | 分类阶元与元数据获取要高效 | 用户 2026-10-28 | ✅ 0.4.0 直连+并行+缓存，Polynoidae 45 s → 12 s |
| 18 | 元数据可用本工具或 kingfisher annotate | 用户 2026-10-27 | ✅ `annotate --kingfisher`（§5） |
