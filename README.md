# organise_sra

盘点某个类群在 NCBI SRA 上的原始测序资源，一条命令完成：

1. **RunInfo**：类群名（含全部下级 taxid）、SRR/ERR/DRR 列表或网页导出的 `SraRunInfo.csv` → NCBI RunInfo 原表，并核对条数（唯一 Experiment 数 = 检索条数）
2. **元数据**：实验/研究标题、研究摘要、提交单位、**样本全部属性**（采集地、经纬度、日期、凭证号、组织……），做法同 `kingfisher annotate`
3. **分类阶元**：TaxID → 门/纲/目/科/属/种（NCBI Taxonomy）
4. **数据类型**：自动区分浅层 WGS（genome skimming）、深度 WGS、长读长 WGS、转录组、Hi-C、宏基因组等
5. **Excel 总表 + 下载清单**：`run_list_<tag>.txt` 直接交给 [kingfisher](https://github.com/wwood/kingfisher-download) 下载

本软件**不下载 fastq**，下载推荐 kingfisher。

*English: inventory a taxon's NCBI SRA raw data (RunInfo + sample attributes + phylum–species lineage + inferred data type such as shallow vs. deep WGS), write an Excel report and a run list for `kingfisher get`. Pure Python standard library, no dependencies.*

## 安装

只需 Python ≥ 3.8，**没有任何第三方依赖**（只用标准库）。

```bash
pip install git+https://github.com/deyuanyang92-dev/organise_sra.git
# 或: git clone 后 pip install .   不安装也可: python3 -m organise_sra (在 src/ 下)
```

NCBI API key 可选（有 key 快约 3 倍）：`--api-key`，或环境变量 `NCBI_API_KEY` / `NCBI_EMAIL`，或 `~/.config/organise_sra/ncbi.json`：

```json
{"email": "you@example.com", "api_key": "..."}
```

## 用法

```bash
organise-sra all Polynoidae                              # 类群名 → ./Polynoidae_sra/
organise-sra all Polynoidae --query 'WGS[Strategy]'      # 追加 Entrez 子句
organise-sra all 我的项目 --runs list.txt                 # SRR/ERR/DRR 列表, 一行一个
organise-sra all 我的项目 --runinfo SraRunInfo.csv        # 网页 Send to → File → RunInfo
organise-sra select Polynoidae --type RNA-Seq --top 1 --tag rna   # 换条件重新筛选 (不必重跑前几步)
organise-sra annotate Polynoidae --kingfisher kf.tsv     # 元数据改用已有的 kingfisher annotate -a 结果 (自动修复其乱码)

# 下载 (kingfisher)
kingfisher get --run-identifiers-list Polynoidae_sra/run_list_default.txt -m ena-ftp aws-http prefetch -f fastq.gz
```

公共选项：`-o/--outdir`（默认 `./<taxon>_sra`）、`--report-dir`（Excel 位置，默认同 outdir）、`--email`、`--api-key`。各步骤参数见 `organise-sra <步骤> -h`。

| 步骤 | 输入 → 输出 | 说明 |
|---|---|---|
| `runinfo` (01) | 类群名 / `--runs` / `--runinfo` → `runinfo.tsv`、`manifest.json`、`runs_not_found.txt` | `txid<id>[Organism:exp]` 检索，history 分页取全；条数不符退出码 1；同名类群列出候选，用 `--taxid` 指定 |
| `annotate` (02) | → `annotate.tsv` | efetch db=sra 完整 XML（含 `bioproject`、`sample_description`）；样本属性列名 = XML 原 TAG；不取联系人姓名/邮箱；`--kingfisher` 改读 kingfisher 结果 |
| `taxonomy` (03) | → `taxonomy.tsv` | phylum/class/order/family/genus/species，含已合并的旧 TaxID |
| `table` (04) | → `table.tsv`、`samples.tsv`、`species_summary.tsv`、`<Taxon>_SRA_<date>.xlsx` | 按 Run/TaxID 合并（行数不变）；派生数据类型与 `BioProject_derived`（RunInfo 为空时用 XML 回填）；Excel：Summary / Samples / 各类型 / Runs / QC / Query |
| `select` (05) | → `selected_<tag>.tsv`、`run_list_<tag>.txt` | 每 TaxID×BioProject×类型取数据量最大的 `--top` 个样本（带上样本全部 run）；`--type`、`--platform`、`--layout PAIRED\|SINGLE\|ALL`、`--per run` |

## 数据类型（`category_derived`，阈值在 `rules.json`，可用 `table --rules` 换）

NCBI 没有"浅层"字段，按样本数据量推断。原列不改，派生列都带 `_derived` 后缀，`category_rule_derived` 写明命中的规则和数值。按顺序首个命中：

1. WGS + LibrarySource METAGENOMIC/METATRANSCRIPTOMIC → `WGS_metagenomic`
2. WGS + PacBio/Nanopore → `long_read_WGS`
3. WGS 短读长，样本总量 < 0.5 Gb → `low_data_WGS`；0.5–10 Gb → `shallow_WGS`；≥ 10 Gb → `deep_WGS`
4. RNA-Seq + 长读长 → `long_read_RNA`
5. 其余 = LibraryStrategy 原值（RNA-Seq、Hi-C、RAD-Seq、AMPLICON……）

样本 = BioSample（空则 Run）；样本总量 = 同样本、同策略、同平台类型的 run 碱基和。LibrarySelection=PCR 的 WGS 不排除，只在 QC 表标 `WGS_selection_PCR`（环节动物实测多为 genome skimming）。

## 为什么按 taxid 检索，不按 BioProject 绕路

2026-10-27 实测：Polynoidae / Nereididae 按 taxid 直接检索不缺目标 run；BioProject 绕路补回 0 条，多出的全是非目标物种；且 Polynoidae 有 51 条 run 的 BioProject 为空，绕路反而会漏。

## 实测

| 类群 | run | 用时（无 key） | 内存峰值 |
|---|---|---|---|
| Polynoidae（多鳞虫科） | 525 | 35–55 秒 | 65 MB |
| Annelida（环节动物门） | 14,641 | 约 8 分钟 | 0.7 GB |

与其他工具的对比见 [docs/benchmark.md](docs/benchmark.md)，设计见 [docs/design.md](docs/design.md)。

## 测试

```bash
pip install pytest && python3 -m pytest -q                                 # 离线
organise-sra all Polynoidae && python3 tests/check_polynoidae.py Polynoidae_sra   # 联网回归
```

## License

MIT
