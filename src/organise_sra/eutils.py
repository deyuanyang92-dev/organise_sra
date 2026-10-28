"""NCBI E-utilities 的极简 HTTP 客户端 (不依赖 edirect).

- 身份: --email/--api-key 参数 > 环境变量 NCBI_EMAIL / NCBI_API_KEY > ~/.config/organise_sra/ncbi.json; key 不打印.
- 限速: 请求的发起间隔 有 key 0.11 s, 无 key 0.35 s (NCBI 上限 10/3 次每秒); 线程安全, 可并行取页
  (efetch_history / map_parallel: 无 key 3 路, 有 key 8 路; 大页面服务器生成要数秒, 并行收益大).
- 连接: proxy="auto" (默认) 先试直连 NCBI, 不通再用环境代理. 2026-10-28 本机实测: 经 http 代理每次请求
  固定多 ~3.5 s (esearch 直连 0.5 s / 代理 4 s; 4.4 MB efetch 直连 7.5 s / 代理 11 s). 也可 ORGANISE_SRA_PROXY=direct|env.
- 失败: HTTP 错误、HTML 错误页、JSON "error"、XML <ERROR> 都算失败, 指数退避重试, 仍失败抛 EutilsError.
- transport 可注入 (测试用): callable(url) -> str.
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .parse import xml_root

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


class EutilsError(RuntimeError):
    pass


def load_identity(email=None, api_key=None, config=None):
    """-> (email, api_key). 优先级: 参数 > 环境变量 NCBI_EMAIL/NCBI_API_KEY > 配置文件
    (config, 默认 $ORGANISE_SRA_CONFIG 或 ~/.config/organise_sra/ncbi.json)."""
    f = Path(config or os.environ.get("ORGANISE_SRA_CONFIG") or Path.home() / ".config" / "organise_sra" / "ncbi.json")
    cfg = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    email = email or os.environ.get("NCBI_EMAIL") or cfg.get("email") or None
    key = api_key or os.environ.get("NCBI_API_KEY") or cfg.get("api_key") or None
    return email, key


DIRECT_MAX = 1200      # 直连时请求 (URL 或 POST 体) 超过此字节数改走代理: 2026-10-28 实测本机直连 NCBI,
                       # 1300 B 的 POST 正常, 1400 B 起卡 20 s 后 HTTP 408 (GET 长 URL 同样卡死), 疑为路径 MTU 黑洞
_OPENERS = {"direct": urllib.request.build_opener(urllib.request.ProxyHandler({})),
            "env": urllib.request.build_opener()}
_MODE = {}


def connection_mode(want=None):
    """-> 'direct' 或 'env'. auto: 直连 einfo 5 s 内成功则直连 (每进程只测一次)."""
    want = want or os.environ.get("ORGANISE_SRA_PROXY", "auto")
    if want in _OPENERS:
        return want
    if "auto" not in _MODE:
        try:
            with _OPENERS["direct"].open(BASE + "einfo.fcgi?retmode=json&db=sra", timeout=5) as r:
                r.read(100)
            _MODE["auto"] = "direct"
        except Exception:
            _MODE["auto"] = "env"
    return _MODE["auto"]


def _urlopen(url, data=None, mode=None):
    m = connection_mode(mode)
    if m == "direct" and mode is None and os.environ.get("ORGANISE_SRA_PROXY", "auto") == "auto" \
            and len(data or url) > DIRECT_MAX:
        m = "env"
    with _OPENERS[m].open(url, data=data, timeout=180) as r:
        return r.read().decode("utf-8", errors="replace")


def _error_in(body, expect=None):
    s = body.lstrip()
    if expect == "json":
        try:
            json.loads(s)
        except ValueError:
            return "empty or invalid JSON"
    if expect == "runinfo":
        import csv
        import io
        rows = [r for r in csv.reader(io.StringIO(s.lstrip("\ufeff"))) if r]
        if not rows or rows[0][0] != "Run":
            return "empty or invalid RunInfo page"
        if any(len(r) != len(rows[0]) for r in rows):
            return "truncated RunInfo page"
    if expect == "xml":
        try:
            xml_root(s)
        except Exception as e:  # truncated / empty XML
            return f"invalid XML: {e}"
    if s[:15].upper().startswith("<!DOCTYPE HTML") or s[:5].lower() == "<html":
        return "HTML error page"
    if "<ERROR>" in s[:3000]:
        return s[s.find("<ERROR>"):][:200]
    if s.startswith("{"):
        try:
            j = json.loads(s)
        except ValueError:
            return "invalid JSON"
        if "error" in j:
            return str(j["error"])[:200]
        if isinstance(j.get("esearchresult"), dict) and j["esearchresult"].get("ERROR"):
            return str(j["esearchresult"]["ERROR"])[:200]
    return None


class Eutils:
    def __init__(self, email=None, api_key=None, transport=None, retries=5, sleep=time.sleep, workers=None):
        self.email, self.api_key = email, api_key
        self.transport = transport or _urlopen
        self.retries, self.sleep = retries, sleep
        self.gap = 0.11 if api_key else 0.35
        self.workers = workers or (8 if api_key else 3)
        self._lock, self._next = threading.Lock(), 0.0

    def _wait(self):
        """按发起时间限速 (线程安全): 每个请求至少间隔 gap 秒发出."""
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.gap
        if delay:
            self.sleep(delay)

    def map_parallel(self, fn, items):
        """并行执行 fn(item), 结果保持输入顺序; 每个请求仍经 _wait 限速."""
        items = list(items)
        if len(items) <= 1 or self.workers <= 1:
            return [fn(x) for x in items]
        with ThreadPoolExecutor(max_workers=min(self.workers, len(items))) as ex:
            return list(ex.map(fn, items))

    def get(self, endpoint, params, expect=None):
        p = {k: v for k, v in params.items() if v not in (None, "")}
        p["tool"] = "organise_sra"
        if self.email:
            p["email"] = self.email
        if self.api_key:
            p["api_key"] = self.api_key
        query = urllib.parse.urlencode(p)
        # 长检索式 (如 200 个 accession OR) 用 POST, 否则 HTTP 414
        url, data = (BASE + endpoint, query.encode()) if len(query) > 1500 else (BASE + endpoint + "?" + query, None)
        last = None
        for i in range(self.retries):
            self._wait()
            try:
                if self.transport is _urlopen:
                    # 直连连续失败 2 次后改走环境代理 (2026-10-28 Annelida taxonomy 直连遇 HTTP 408)
                    body = _urlopen(url, data, mode="env" if i >= 2 else None)
                else:
                    body = self.transport(url, data) if data else self.transport(url)
                err = _error_in(body, expect)
                if err is None:
                    return body
                last = err
            except Exception as e:  # network / HTTP status
                last = f"{type(e).__name__}: {e}"
            if i < self.retries - 1:
                self.sleep(2 ** i)
        raise EutilsError(f"{endpoint} 失败 {self.retries} 次: {last}")

    def esearch(self, db, term, retmax=0):
        body = self.get("esearch.fcgi", {"db": db, "term": term, "usehistory": "y",
                                         "retmax": retmax, "retmode": "json"}, expect="json")
        r = json.loads(body)["esearchresult"]
        return {"count": int(r.get("count", 0)), "webenv": r.get("webenv", ""),
                "query_key": r.get("querykey", ""), "ids": r.get("idlist", [])}

    def esearch_ids(self, db, term, page=10000):
        """-> 全部 UID (不用 history; 每页 10000 个, 约 1 s, 页间并行). 增量更新用来比对新增/删除."""
        def one(start):
            body = self.get("esearch.fcgi", {"db": db, "term": term, "retstart": start, "retmax": page,
                                             "retmode": "json"}, expect="json")
            r = json.loads(body)["esearchresult"]
            return int(r.get("count", 0)), r.get("idlist", [])
        n, ids = one(0)
        for _, more in self.map_parallel(one, range(page, n, page)):
            ids += more
        return list(dict.fromkeys(ids))

    def sra_experiments(self, uids, batch=200):
        """db=sra UID -> Experiment 号 (esummary; 已删除/撤回的 UID 查不到则不在结果中)."""
        out = {}
        for body in self.map_parallel(lambda b: self.get("esummary.fcgi", {"db": "sra", "id": ",".join(b),
                                                                           "retmode": "json"}, expect="json"),
                                      [uids[i:i + batch] for i in range(0, len(uids), batch)]):
            res = json.loads(body).get("result", {})
            for u in res.get("uids", []):
                x = res.get(u, {}).get("expxml", "")
                i = x.find('<Experiment acc="')
                if i >= 0:
                    out[u] = x[i + 17:x.find('"', i + 17)]
        return out

    def efetch_history(self, db, webenv, query_key, count, rettype="", retmode="xml", page=500):
        """-> 各页响应列表 (按 retstart 顺序), 页间并行."""
        expect = "xml" if retmode == "xml" else "runinfo" if rettype == "runinfo" else None
        return self.map_parallel(
            lambda start: self.get("efetch.fcgi", {"db": db, "WebEnv": webenv, "query_key": query_key,
                                                   "retstart": start, "retmax": page,
                                                   "rettype": rettype, "retmode": retmode}, expect=expect),
            range(0, count, page))

    def efetch_ids(self, db, ids, rettype="", retmode="xml"):
        """ids 可为 UID; db=sra 时也可直接给 SRR/ERR/DRR 号 (省去 esearch)."""
        return self.get("efetch.fcgi", {"db": db, "id": ",".join(ids), "rettype": rettype, "retmode": retmode},
                        expect="xml" if retmode == "xml" else "runinfo" if rettype == "runinfo" else None)


def resolve_taxid(eu, name):
    """类群名 -> NCBI taxid; 纯数字原样返回; 找不到或多个同名时报错并列出候选."""
    name = name.strip()
    if name.isdigit():
        return name
    r = eu.esearch("taxonomy", f'"{name}"[Scientific Name]', retmax=20)
    if r["count"] == 0:
        raise EutilsError(f"NCBI Taxonomy 未找到类群 {name!r}; 检查拼写或用 --taxid 指定")
    if r["count"] == 1 and len(r["ids"]) == 1:
        return r["ids"][0]
    root = xml_root(eu.efetch_ids("taxonomy", r["ids"]))
    lines = [f"  {t.findtext('TaxId')}  {t.findtext('ScientificName')}  ({t.findtext('Lineage', '')[-80:]})"
             for t in root.findall("Taxon")]
    raise EutilsError(f"类群名 {name!r} 在 NCBI 有 {r['count']} 个同名, 请用 --taxid 指定:\n" + "\n".join(lines))
