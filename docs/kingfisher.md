# 用 kingfisher 下载 SRA 数据

organise_sra 只负责「查清有什么、挑哪些」；真正下载交给 [kingfisher](https://github.com/wwood/kingfisher-download)（Woodcroft et al.）。
`organise-sra download` 是对 `kingfisher get` 的批量封装，整合了用户以前的几个本地脚本（见文末 §7）。

最后核对：2026-10-28，kingfisher **0.5.0**（2026-04-10 发布，bioconda / PyPI 最新版）。

## 1. 安装

```bash
# 一键 (新建或升级 conda 环境 kingfisher, 并装 ascp)
bash scripts/install_kingfisher.sh            # 默认 0.5.0

# 或手动
mamba create -n kingfisher -c conda-forge -c bioconda kingfisher=0.5.0 aspera-cli
conda activate kingfisher
ascli config ascp install --http-proxy=$https_proxy   # 可选: ena-ascp 才需要; 无代理时去掉 --http-proxy
kingfisher get -r SRR12118866 -m aws-http -f fastq.gz # 测试
```

其他方式：Docker `docker run -v $(pwd):/data wwood/kingfisher:<tag> get -r SRR12118866 -m ena-ftp`；开发版用 pixi（上游已从 conda 环境文件迁到 pixi）。

`organise-sra download` 自动寻找 kingfisher：`--kingfisher` → `$KINGFISHER` → PATH → `~/miniforge3/envs/kingfisher/bin/kingfisher`（以及 mambaforge/miniconda3/anaconda3）。**不需要先 `conda activate`**：会把该环境的 bin 放到 PATH 最前，保证 fasterq-dump、aria2c、pigz 用同一环境的版本。

### 0.5.0 相对 0.4.1 的变化

- 新增 NGDC/GSA（中国国家基因组科学数据中心）`CRR` 号下载：`-m ngdc-http` / `ngdc-ascp`（实验性），`--cra-accession`
- `annotate` 对 CRR 号自动查 NGDC
- 错误信息不再打印 Python 堆栈（`--debug` 才显示）
- ascp 自动在 `~/.aspera` 下找（`ascli config ascp install` 装到 `~/.aspera/sdk/ascp`）
- `--download-threads` 对 aria2c 上限 16
- 0.4.0 起有 `--output-directory`（再早的版本没有，旧脚本 V2 因此用 `cwd=` 切目录）

## 2. 下载方法（`-m`，按顺序尝试，前一个失败才用下一个）

| 方法 | 来源 → 处理 | 备注 |
|---|---|---|
| `aws-http` | AWS 开放数据 .sra（aria2c 多线程）→ fasterq-dump | 免费；本机最快 |
| `prefetch` | NCBI prefetch .sra → fasterq-dump | 稳；kingfisher 默认取消 20 GB 上限 |
| `ena-ftp` | ENA 直接 fastq.gz（curl / aria2c） | 不需 fasterq-dump；国内常较慢 |
| `ena-ascp` | ENA fastq.gz，Aspera | 需 ascp 和 33001 端口直连；**本机经代理连不上** |
| `aws-cp` | `aws s3 cp` .sra | 通常免费 |
| `gcp-cp` | Google Cloud | **需付费账号**，`--allow-paid` |
| `ngdc-http` / `ngdc-ascp` | NGDC/GSA，CRR 号 | 0.5.0 新增，实验性 |

**本机实测**（2026-10-28，SRR12118866，双端 2×~4.5 MB fastq.gz，经 http 代理 127.0.0.1:7890）：

| 方法 | 结果 | 用时 |
|---|---|---|
| aws-http | 成功，md5 OK | 18 s（另一次 102 s，波动大） |
| prefetch | 成功 | 27 s |
| ena-ftp | 成功，md5 OK | 102 s |
| ena-ascp | 失败：`ascp: Unable to connect via SSH` | — |

因此 `organise-sra download` 默认 `-m aws-http prefetch ena-ftp`。旧脚本默认 `aws-http aws-cp gcp-cp ena-ascp ena-ftp prefetch`，需要时用 `-m` 显式写出。

输出格式 `-f`：`fastq.gz`（默认）、`fastq`、`fasta`、`fasta.gz`、`sra`；给多个时 kingfisher 选转换最少的。

## 3. organise-sra download 用法

```bash
# A. 接 organise_sra 盘点结果 (select 输出的 run_list_<tag>.txt)
organise-sra all Polynoidae
organise-sra download Polynoidae                       # -> Polynoidae_sra/fastq/
organise-sra select Polynoidae --type RNA-Seq --tag rna && organise-sra download Polynoidae --tag rna

# B. 已有 run 列表 / 单个 run / BioProject
organise-sra download 我的项目 --runs sra_list.txt -o /data/my_reads      # 一行一个 SRR/ERR/DRR
organise-sra download 我的项目 -r SRR12118866 ERR1739691
organise-sra download 我的项目 -p PRJNA621514 SRP260223                   # 先经 NCBI 查出全部 run

# C. 常用选项
  -j 4                    同时下载 4 个 run (默认 4)
  --download-threads 8    每个 run 的连接数 (≤16)
  -t 8                    fasterq-dump / pigz 线程
  -m aws-http ena-ftp     下载方法顺序
  -f fastq.gz             输出格式
  --fastq-dir DIR         下载目录 (默认 <outdir>/fastq)
  --no-md5                不校验 md5 (默认校验)
  --exclude done.txt      跳过这些 run (每行第一列)
  --check-only            只检查本地已下载情况
  --dry-run               只打印 kingfisher 命令
  --retries 1             失败重试次数
  --min-free-gb 50        磁盘剩余低于此值不再开始新下载
  --kingfisher-args '--allow-paid'   其余参数原样传给 kingfisher get
```

**断点续跑**：中断后重跑同一条命令即可。

- 每个 run 成功后写 `fastq/.done/<run>.json`（方法、用时、文件名与字节数），有标记即跳过。
- 已有文件但无标记（旧脚本下载的、被 Ctrl-C/断电中断的）：检查 gzip 完整性、`_1`/`_2` 是否成对、是否为空。通过 → 补标记跳过；不通过 → `kingfisher --force` 删除后重下，删除的文件名记入 `download_history.tsv`。`--no-verify` 恢复旧脚本「有文件就算下完」的行为。
- 为什么需要这一步：kingfisher 自己「有输出文件就跳过」，但只看文件是否存在。双端数据下完 `_1`、`_2` 未开始时被中断，下次会被误判为已完成。

**输出**（在 `<outdir>`）：

| 文件 | 内容 |
|---|---|
| `fastq/` | `<run>_1.fastq.gz`、`<run>_2.fastq.gz`（单端为 `<run>.fastq.gz`） |
| `fastq/logs/<run>.log` | 该 run 每次 kingfisher 的完整输出 |
| `download_status.tsv` | 本次每个 run：status（done/failed/missing/incomplete/skipped/excluded）、method、seconds、size_Gb、files、note |
| `runs_failed.txt` | 未完成的 run，可直接 `--runs runs_failed.txt` |
| `download_history.tsv` | 历次尝试的追加记录 |

退出码：有 failed / skipped 时为 1，便于脚本与 Snakemake 判断。

## 4. 直接用 kingfisher（不经 organise-sra）

```bash
conda activate kingfisher
kingfisher get -r SRR12118866 -m aws-http prefetch ena-ftp -f fastq.gz --check-md5sums --output-directory reads
kingfisher get --run-identifiers-list runs.txt -m aws-http prefetch ena-ftp -f fastq.gz --output-directory reads  # 串行
kingfisher get -p PRJNA621514 -m aws-http prefetch -f fastq.gz
kingfisher extract --sra SRR12118866.sra -t 16 -f fastq.gz
kingfisher annotate -r SRR12118866 -a -f csv -o meta.csv        # 元数据; organise-sra annotate --kingfisher 可读其结果
```

## 5. Snakemake

完成标记可直接当 output：

```python
RUNS = [x.strip() for x in open("runs.txt") if x.strip()]
rule all:
    input: expand("proj_sra/fastq/.done/{run}.json", run=RUNS)
rule kingfisher:
    output: "proj_sra/fastq/.done/{run}.json"
    threads: 8
    resources: download=1
    shell: "organise-sra download proj -r {wildcards.run} -j 1 -t {threads}"
```

（`snakemake --resources download=4` 控制同时下载数。注意每次调用都会重写 `download_status.tsv`，以 `.done/` 与 `download_history.tsv` 为准。）

## 6. 问题记录（原因 → 解决）

1. **`kingfisher get -h` 报 `OSError: [Errno 25] Inappropriate ioctl for device`**（0.4.1、0.5.0 都有）
   原因：bird_tool_utils 打印帮助时调用 `os.get_terminal_size()`，非终端（管道、脚本、nohup）下失败。
   解决：在真实终端里看帮助；或直接读本文 / 上游源码 `kingfisher/cli.py`。不影响 `get` 本身。
2. **aria2c 打印 `errorCode=28 ... processing the option '--all-proxy'`**
   原因：环境变量 `all_proxy=socks://127.0.0.1:7890/`，aria2c 不支持 socks 代理（只认 http）。aria2c 会忽略后继续用 `https_proxy` 下载，所以不致命但刷屏。
   解决：`organise-sra download` 传给 kingfisher 的环境里去掉 socks 形式的 `all_proxy/ALL_PROXY`（http_proxy 保留）。直接用 kingfisher 时可 `env -u all_proxy -u ALL_PROXY kingfisher get ...`。
3. **`ascli config ascp install` 报 `SSL_connect ... unexpected eof while reading`**
   原因：ascli（Ruby）不读 `https_proxy`，直连 GitHub/IBM 被墙。
   解决：`ascli config ascp install --http-proxy=http://127.0.0.1:7890`，装到 `~/.aspera/sdk/ascp`（Transfer SDK 4.4.8）。
4. **`ena-ascp` 失败：`ascp: Unable to connect via SSH`**
   原因：ascp 走 TCP 33001 + UDP，不经 http 代理；本机网络直连 fasp.sra.ebi.ac.uk:33001 不通。
   解决：本机不用 ena-ascp（已从默认方法去掉）；在能直连 ENA 的机器上可 `-m ena-ascp aws-http`。
5. **mamba 升级时 `libstdcxx ... files were already present`** 警告：conda-forge 包间文件重叠，升级后 kingfisher 0.5.0 正常运行，可忽略。
6. **旧脚本问题（已在 download 中修正）**：`kingfisher_batchv1.py` 在线程里 `sys.exit()` 只结束该线程，失败不会被汇总；`--run-identifiers-list` 分支漏传 md5 选项；V1/V2 检查只认 `.fastq`，`fastq.gz` 下载后被当成缺失；V2 用 `cwd=` 而不能指定 `--output-directory`（0.4.0 前的限制）。
7. **`--output-directory` 两级以上不存在时 kingfisher 报错**（它只建一级）。download 事先 `mkdir -p`。

## 7. 用户旧脚本对照（2026-10-28 盘点）

| 脚本（最新副本） | 日期 | 功能 | download 中对应 |
|---|---|---|---|
| `~/mybiosofts/Kingfisher/kingfisher_batchv1.py`（= `~/temple_scripts/kingfisher_batchv1.py`；`~/mybiosofts/kingfisher_batch.py` 为 2025-02 前一版） | 2025-03-15 | `-p` BioProject；`--SRA_lists` 线程池并行；`--run-identifiers-list`；`--compared-files` 跳过已下载并写 `undownload_sra.txt`；md5 默认开、`--disable-md5`；`-f` 默认 fastq.gz；`--output-directory` | `-p`；`--runs` + `-j`；`--exclude`（未完成写 `runs_failed.txt`）；`--no-md5`；`-f`；`-o/--fastq-dir` |
| `~/mybiosofts/Kingfisher/batch_download_using_kingfisherV2.py` | 2024-02-18 | 只下缺失的；`--only-check`；`--only-annotate`（`kingfisher annotate --all-columns`）；日志 `Kingfisher.log.txt` | 完成标记 + 完整性检查；`--check-only`；annotate 由 `organise-sra annotate` 负责；`logs/` + `download_history.tsv` |
| `~/temple_scripts/batch_download_using_kingfisherV1.py` | 2024-02-18 | 检查结果写 `kingfisher.check.txt` | `download_status.tsv` / `runs_failed.txt` |
| `~/mybiosofts/scripts/batch_download_using_kingfisher.py` 等 | 2023-10 | `Pool(16)` × `--download-threads 40` 并行；`-p` 单项目 | `-j`、`--download-threads`（40 超过 aria2c 上限，现截到 16） |

共同默认：`-m aws-http aws-cp gcp-cp ena-ascp ena-ftp prefetch`（见 §2，现改为实测可用的三种）。原脚本未改动。
