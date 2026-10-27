# 与现有工具的对比（2026-10-27）

两部分：**(A) 实测**：在同一类群上跑 kingfisher、pysradb、ffq、EDirect 与 organise_sra，逐 run 比对；**(B) 调研**：24 个 SRA 元数据/下载/类群盘点工具的功能对比。stars 与提交日期为 2026-10-27 `gh api` 快照。

## A. 实测：多鳞虫科 Polynoidae（taxid 46593）

525 run / 495 experiment / 418 BioSample（SRR 389、ERR 85、DRR 51）。WSL2，16 核；经本机代理访问 NCBI（单次 HTTPS 约 3 s），**无 API key**，所有工具串行、每秒 ≤ 3 请求。每项重复 1–3 次，**时间只宜作量级比较**。

### 安装

| 工具 | 版本 | conda 依赖闭包（含 Python） | 安装用时 |
|---|---|---|---|
| organise_sra | 0.1.0 | **0**（只用标准库） | 秒级（`pip install`） |
| kingfisher | 0.5.0 | 154 | 220 s |
| pysradb | 2.5.1 | 62 | 84 s |
| ffq | 0.3.1 | 53 | 96 s |
| entrez-direct | 26.0 | 33 | 71 s |

### 结果

| 工具 | 按类群取 RunInfo | 给 525 个 run 取元数据 | 样本属性 | 分类阶元 | 数据类型 | 非 ASCII 文本 |
|---|---|---|---|---|---|---|
| **organise_sra** | ✔ 525 run，13.5 s | ✔ 525/525，18.9 s | 命名列 | **✔ 门–种** | **✔ 浅层/深度 WGS 等** | **正确** |
| kingfisher | ✘ 不支持（只收 run 号或 BioProject） | ✔ 525/525，26.8 s | 命名列（139 列，最全） | ✘ 只有学名 | ✘ | 49 个 run 乱码 |
| pysradb search | ✔ 但默认 `-v 2` 只有 495 run（丢多 run 实验的后续 run），`-v 3` 才完整 | — | 键值对（需自行展开） | ✘ | ✘ | 48 个 run 乱码 |
| pysradb metadata | — | 一次 525 个崩溃（GET URL 过长，HTTP 414）；分 3 批共 85 s | 命名列 | ✘ | ✘ | 同上 |
| ffq | ✘ | 50 个抽样首轮 40/50；DRR 0/8（依赖 ENA，ENA 未收录这 51 个 DRR）；一个坏号整批无输出；外推全量约 2 小时 | ✘ 无 | ✘ | ✘ 无 LibraryStrategy | — |
| EDirect | ✔ 525 run，与我们逐格一致，但 6–7 分钟（每请求 5 个 UID） | 只有 RunInfo | ✘ | 需自写 | ✘ | — |

全流程（含 Excel 与筛选）：organise_sra 33–55 s，峰值 65–68 MB；第 4 步（合并 + 分类 + Excel）0.45 s。

### 数据一致性

- 525 个 run 的 bases、LibraryStrategy、geo_loc_name、collection_date、lat_lon、specimen_voucher：kingfisher、pysradb 与我们**逐 run 一致**（kingfisher 的 bases 写成浮点 `353921600.0`；5 个 NCBI 未装载读段的 run，我们保留 RunInfo 的 0，其他工具留空）。
- EDirect 的 RunInfo 与我们 47 列 × 525 行**逐格相同**。
- 与 kingfisher 同名的 108 列逐格比对（56,700 格）：1199 格不同，全部可归因——`study_links` 我们空、kingfisher 写 `[]`（518）；kingfisher 的 `sample_name` 是合成列（474）；kingfisher 乱码（202）；`sample_description` 我们漏取（5，**已修复**）。

### 乱码的原因（kingfisher、pysradb）

NCBI efetch 返回 `content-type: text/xml`，不带 charset；Python requests 对此类响应默认按 ISO-8859-1 解码（`requests.utils.get_encoding_from_headers` 实测），kingfisher（`sra_metadata.py:111`）与 pysradb（`sraweb.py:396`）都用 `.text`，于是 `Martínez` 变成 `MartÃ­nez`。organise_sra 用 urllib 取字节，由 XML 解析器按声明的 UTF-8 解码，不受影响。采集人、地名、机构名会受影响，地名乱码会让后续地理编码失败。

### organise_sra 不如别人之处

| 问题 | 状态 |
|---|---|
| RunInfo 的 BioProject 为空时（51 个 DRR）不回填；kingfisher/pysradb 从 XML 的 STUDY 补成 PRJDB35883 | **已修复**：`annotate` 新增 `bioproject`，`table` 新增 `BioProject_derived`，筛选按它分组 |
| 5 个 run 的 `SAMPLE/DESCRIPTION` 没取 | **已修复**：新增 `sample_description` |
| 少 kingfisher 的机构地址、read 长度均值/标准差、submitter、study_alias 等列 | 路线图（联系人姓名/邮箱有意不取） |
| 不给 aws/gcp/ENA fastq 下载链接（pysradb 有） | 有意为之：下载交给 kingfisher |
| ENA 提交的样本（ERR）地名在 `geographic location (...)` 系列属性里，不在 `geo_loc_name`；所有工具都没归一 | 路线图：`geo_loc_name_derived` |
| 自研、单人维护，没有社区使用量 | — |

## B. 调研：24 个工具

| 工具 | stars | 语言 | 类群入口（含下级） | 样本属性 | 分类阶元 | 数据类型 | 下载 | 条数核对 | 依赖 |
|---|---|---|---|---|---|---|---|---|---|
| **organise_sra** | — | Python | ✔ taxid 子树 | ✔ 全部 | ✔ 门–种 | ✔ 规则可改 | 交给 kingfisher | ✔ 强制 | 0 |
| [kingfisher](https://github.com/wwood/kingfisher-download) | 318 | Python | ✘ | ✔ 全部 | ✘ | ✘ | ✔ 多源回退 | 只打日志 | 6 Python 包 + sra-tools 等 |
| [pysradb](https://github.com/saketkc/pysradb) | 367 | Python | 部分（名称检索，`--max` 上限） | 部分 | ✘ | ✘ | ✔ | ✘ | 4 |
| [ffq](https://github.com/pachterlab/ffq) | 605 | Python | ✘ | JSON | ✘ | ✘ | 只给链接 | ✘ | 4；2024-03 后无提交 |
| [nf-core/fetchngs](https://github.com/nf-core/fetchngs) | 201 | Nextflow | ✘ | ENA 索引字段 | ✘ | ✘ | ✔ | md5 | Nextflow + 容器 |
| [sra-tools](https://github.com/ncbi/sra-tools) | 1371 | C++ | ✘ | ✘ | ✘ | ✘ | ✔ 官方 | ✘ | 二进制 |
| EDirect | — | Go/shell | ✔ | 需自写 | 需自写 | ✘ | ✘ | 需自写 | 二进制 |
| [SRAdb](https://github.com/zhujack/SRAdb) | 3–24 | R | 本地 SQL | 文本 | ✘ | ✘ | ✔ | ✘；库停在 2023-12，7.2 GB | R 包 |
| [grabseqs](https://github.com/louiejtaylor/grabseqs) | 109 | Python | ✘ | ✘ | ✘ | ✘ | ✔ | ✘ | 4 + sra-tools |
| [iSeq](https://github.com/BioOmics/iSeq) | 258 | Bash | ✘ | 部分 | ✘ | ✘ | ✔ | md5 | 5 个外部程序 |
| [q2-fondue](https://github.com/bokulich-lab/q2-fondue) | 25 | Python | 部分（BioSample 查询） | 部分 | ✘ | ✘ | ✔ | 失败 run 可回灌 | QIIME 2 |
| [SRA-Explorer](https://github.com/ewels/sra-explorer) | 225 | JS | 部分（≤ 500 条） | 未确证 | ✘ | ✘ | 生成脚本 | ✘ | 0（网页） |
| [MetaSRA](https://github.com/deweylab/MetaSRA-pipeline) | 47 | Python | ✘（仅人类） | ✔ 本体标准化 | ✘ | ✘ | ✘ | — | 8 |
| [sradownloader](https://github.com/s-andrews/sradownloader) | 34 | Python | ✘ | ✘ | ✘ | ✘ | ✔ | ✘ | sra-tools |
| [ncbi datasets](https://github.com/ncbi/datasets) | 563 | Go | ✔ 但不含 SRA reads | — | ✔ | — | 基因组 | — | 0 |
| [taxonkit](https://github.com/shenwei356/taxonkit) | 483 | Go | 本地分类树 | ✘ | ✔ 七级 | ✘ | ✘ | — | 0 + taxdump |
| ENA Portal API | — | HTTP | ✔ `tax_tree`，但本类群只召回 254/525 | 只含已索引字段 | taxid 串 | ✘ | 链接 | count 端点 | 0 |
| [enaBrowserTools](https://github.com/enasequence/enaBrowserTools) | 102 | Python | 按 taxid 下载 reads 目前被禁用 | XML | ✘ | ✘ | ✔ | ✘ | 0 |
| ENA File Downloader | 仓库 404 | Java | ✘ | ✘ | ✘ | ✘ | ✔ | ✘ | Java |
| [fastq-dl](https://github.com/rpetit3/fastq-dl) | 409 | Python | ✘ | ENA 字段 | ✘ | ✘ | ✔ | md5 + 重试 | pysradb 等 |
| [NCBImeta](https://github.com/ktmeaton/NCBImeta) | 56 | Python | 部分（Entrez 检索词） | ✔ | ✘ | ✘ | ✘ | 未确证 | 4；2022 后停更 |
| [geofetch](https://github.com/pepkit/geofetch) | 55 | Python | ✘ | 部分 | ✘ | ✘ | ✔ | 未确证 | pip |
| [PARTIE](https://github.com/linsalrob/partie) | 25 | Perl | ✘ | ✘ | ✘ | 部分（读序列，仅宏基因组） | ✘ | — | 多个数据库 |
| [sracha-rs](https://github.com/rnabioco/sracha-rs) | 77 | Rust | ✘ | ✘ | ✘ | ✘ | ✔ 快 | md5 | 0 |

### 空白点（在以上 24 个工具中未发现）

1. 类群（含下级）→ 完整 RunInfo，并**强制**核对条数（区分 Experiment 数 = Count 与 Run 行数）。
2. 以类群为入口的一张宽表：RunInfo + 标题 + 样本全部属性。
3. 门–种阶元直接并进 SRA 清单。
4. 基于元数据的可解释数据类型判别（浅层/深度 WGS、长读长、转录组、Hi-C）。
5. 面向人工筛选的 Excel + kingfisher 下载清单。
6. 零依赖。

### ENA 不宜作为类群入口

本类群 ENA `tax_tree(46593)` 只召回 254/525（48%）：525 个 run 中 ENA 有 474 个，缺全部 51 个 DRR；474 个里 220 个 ENA 的 tax_id 为空，所以按分类树查不到。以 ENA 为入口的工具链（fetchngs 的 ENA 元数据、fastq-dl、iSeq 的 ENA 路径、`pysradb search --db ena`）在这个类群会悄无声息地漏掉一半以上。这是单个类群的观察，原因（同步滞后或 BioSample 未解析）未确证。

### 借鉴到 organise_sra 的设计

- kingfisher `annotate` 的字段命名、多源下载回退顺序（`-m ena-ftp aws-http prefetch`）→ 清单与命令沿用。
- q2-fondue 失败项单独输出、可直接回灌 → 已有 `runs_not_found.txt` / `annotate_missing.txt`，路线图加 `--runs` 直接读它们重试。
- ffq / ENA filereport 的 md5 与文件大小 → 路线图：下载清单可附，供下载后校验。
- ncbi-bioproject-archiver 的"原始响应 + manifest + provenance" → 已有 `manifest.json` 与 Excel 的 Query 表。
- SRAdb（快照陈旧）、SRA-Explorer（500 条上限）的教训 → 每次联网、不设静默上限。
- 只用官方 E-utilities，不用 `sra-db-be` 类接口（iSeq issue #67 记录其元数据不完整、重复）。

原始数据、命令原文与比对脚本保存在开发机，未随仓库发布。
