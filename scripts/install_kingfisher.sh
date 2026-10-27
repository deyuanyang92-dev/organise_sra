#!/usr/bin/env bash
# 安装 / 升级 kingfisher (bioconda) 到 conda 环境 "kingfisher", 可选装 ascp (ena-ascp 方法用).
# 用法: bash scripts/install_kingfisher.sh [版本, 默认 0.5.0]
# 代理: 若设置了 https_proxy, ascli 下载 ascp 时需显式传 --http-proxy (见 docs/kingfisher.md 问题 3).
set -euo pipefail
VER=${1:-0.5.0}
ENV=kingfisher
CONDA_BASE=$(conda info --base 2>/dev/null || echo "$HOME/miniforge3")
source "$CONDA_BASE/etc/profile.d/conda.sh"
SOLVER=$(command -v mamba || command -v conda)

if conda env list | awk '{print $1}' | grep -qx "$ENV"; then
    "$SOLVER" install -y -n "$ENV" -c conda-forge -c bioconda "kingfisher=$VER" aspera-cli
else
    "$SOLVER" create -y -n "$ENV" -c conda-forge -c bioconda "kingfisher=$VER" aspera-cli
fi
conda activate "$ENV"
python -c "import kingfisher; print('kingfisher', kingfisher.__version__)"

# ascp: kingfisher 0.5.0 会在 PATH 或 ~/.aspera 下自动找
if ! command -v ascp >/dev/null && [ -z "$(find ~/.aspera -name ascp -type f 2>/dev/null | head -1)" ]; then
    proxy=${https_proxy:-${HTTPS_PROXY:-}}
    ascli config ascp install ${proxy:+--http-proxy=$proxy} || echo "ascp 安装失败 (不影响 aws-http/prefetch/ena-ftp)"
fi
echo "测试: conda activate $ENV && kingfisher get -r SRR12118866 -m aws-http -f fastq.gz --output-directory kf_test"
