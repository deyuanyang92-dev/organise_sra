"""sra 模块测试公共件: 假 HTTP 传输、按文件名加载步骤脚本、临时输出目录."""
import importlib
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
sys.path.insert(0, str(HERE.parent / "src"))       # 未安装时也能跑测试

TAXON = "_test_sra"


STEP = {"01_runinfo": "runinfo", "02_annotate": "annotate", "03_taxonomy": "taxonomy",
        "04_table": "table", "05_select": "selection"}


def load_step(name):
    return importlib.import_module(f"organise_sra.{STEP[name]}")


class FakeTransport:
    """rules: [(url 子串, 响应体或 callable(url)->str)], 按顺序取第一个匹配; 记录全部请求."""

    def __init__(self, rules):
        self.rules = rules
        self.urls = []
        self.posts = []

    def __call__(self, url, data=None):
        self.urls.append(url)
        self.posts.append(data)
        url = url + ("?" + data.decode() if data else "")
        for key, body in self.rules:
            if key in url:
                return body(url) if callable(body) else body
        raise AssertionError(f"unexpected url {url}")


def fake_eutils(rules):
    from organise_sra.eutils import Eutils
    t = FakeTransport(rules)
    return Eutils(transport=t, sleep=lambda s: None), t


@pytest.fixture
def project(tmp_path, monkeypatch):
    """在临时目录运行; 步骤默认输出到 ./<taxon>_sra."""
    monkeypatch.chdir(tmp_path)
    return tmp_path / f"{TAXON}_sra"
