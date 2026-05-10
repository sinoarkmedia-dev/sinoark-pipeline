#!/usr/bin/env python3
"""
sinoark-filter/filter.py

Classifies articles with about_tech, about_ai, AND china_related tags.
  - about_ai / about_tech:  source flag (strong prior) OR keyword match on title+summary.
  - china_related:          subject-based — true ONLY when the article is *specifically*
                             about a Chinese company/person/entity/locality. A Chinese-source
                             article reporting on OpenAI is china_related=False.

Processes newest-first, offset-paginated over full table.
After completing, re-checks every 60 min for new unfiltered articles.
"""

import logging
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

PAGE_SIZE            = 1000  # articles fetched per DB query
DB_WRITE_WORKERS     = 8     # parallel PATCH threads
NEW_ARTICLE_INTERVAL = 300   # seconds between "new articles" priority checks

# Hard floor: only classify articles published on/after May 1, 2026 BJT.
# Keeps the pre-relaunch backlog (~668 about_ai rows from April 3) frozen as-is.
MIN_PUBLISHED_AT     = "2026-04-30T16:00:00Z"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/sinoark-filter.log"),
    ],
)
log = logging.getLogger(__name__)

# ── Supabase ───────────────────────────────────────────────────────────────────
_sb = requests.Session()
_sb.headers.update({
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
})

def sb_get(table, params, timeout=30):
    for attempt in range(3):
        try:
            r = _sb.get(f"{SUPABASE_URL}/rest/v1/{table}", params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code >= 500 and attempt < 2:
                wait = 10 * (attempt + 1)
                log.warning(f"Server error (attempt {attempt+1}), waiting {wait}s...")
                time.sleep(wait)
            else:
                raise

def sb_patch_one(row):
    """PATCH a single article by id."""
    article_id = row["id"]
    data = {k: v for k, v in row.items() if k not in ("id", "_done")}
    r = _sb.patch(
        f"{SUPABASE_URL}/rest/v1/articles_digest",
        params={"id": f"eq.{article_id}"},
        json=data,
        timeout=30,
    )
    r.raise_for_status()

def total_articles():
    r = _sb.get(
        f"{SUPABASE_URL}/rest/v1/articles_digest",
        headers={"Prefer": "count=exact"},
        params={
            "select": "id",
            "limit": "1",
            "published_at": f"gte.{MIN_PUBLISHED_AT}",
        },
        timeout=120,
    )
    cr = r.headers.get("content-range", "0/0")
    try:
        return int(cr.split("/")[-1])
    except Exception:
        return 0

def count_unfiltered():
    r = _sb.get(
        f"{SUPABASE_URL}/rest/v1/articles_digest",
        headers={"Prefer": "count=exact"},
        params={
            "select": "id",
            "or": "(about_tech.is.null,about_ai.is.null,china_related.is.null)",
            "published_at": f"gte.{MIN_PUBLISHED_AT}",
            "limit": "1",
        },
        timeout=30,
    )
    cr = r.headers.get("content-range", "0/0")
    try:
        return int(cr.split("/")[-1])
    except Exception:
        return 0

def fetch_page(offset):
    """Fetch a page of articles (>= MIN_PUBLISHED_AT) ordered newest-first."""
    return sb_get("articles_digest", {
        "select": "id,source_id,source_name,original_title,original_summary,published_at",
        "order":  "published_at.desc",
        "limit":  str(PAGE_SIZE),
        "offset": str(offset),
        "published_at": f"gte.{MIN_PUBLISHED_AT}",
    }, timeout=120)

def fetch_unfiltered(limit=PAGE_SIZE):
    """For monitoring mode: only articles still missing tags (>= MIN_PUBLISHED_AT)."""
    return sb_get("articles_digest", {
        "select": "id,source_id,source_name,original_title,original_summary,published_at",
        "or":     "(about_tech.is.null,about_ai.is.null,china_related.is.null)",
        "published_at": f"gte.{MIN_PUBLISHED_AT}",
        "order":  "published_at.desc",
        "limit":  str(limit),
    })

def normalize_name(name: str) -> str:
    return (name or "").lower().strip()

def load_sources():
    """Returns (by_id, by_name, by_name_norm) dicts for source lookups."""
    rows = sb_get("sources", {"select": "id,name,about_tech,about_ai", "limit": "1000"})
    by_id = {r["id"]: r for r in rows}
    by_name = {r["name"]: r for r in rows if r.get("name")}
    by_name_norm = {normalize_name(r["name"]): r for r in rows if r.get("name")}
    return by_id, by_name, by_name_norm

def fuzzy_match_source(source_name: str, by_name: dict) -> dict:
    if not source_name or not by_name:
        return None
    norm_source = normalize_name(source_name)
    best_ratio = 0
    best_match = None
    for name, src in by_name.items():
        ratio = SequenceMatcher(None, norm_source, normalize_name(name)).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = src
    return best_match if best_ratio > 0.75 else None

def lookup_source(art, by_id, by_name, by_name_norm):
    src = by_id.get(art.get("source_id") or "")
    if src is not None:
        return src
    source_name = art.get("source_name") or ""
    src = by_name.get(source_name)
    if src is not None:
        return src
    src = by_name_norm.get(normalize_name(source_name))
    if src is not None:
        return src
    return fuzzy_match_source(source_name, by_name)

# ── Keyword-based article classifier ──────────────────────────────────────────

# Any single match → about_ai = True
# These are unambiguous AI signals that appear in article titles/summaries
AI_STRONG = {
    # Generic terms — "ai" standalone is unambiguous in Chinese tech writing
    "ai", " ai ", "ai ", " ai",
    "人工智能", "大模型", "大语言模型", "生成式", "llm", "agi", "通用人工智能",
    "机器学习", "深度学习", "神经网络", "自然语言处理", "nlp", "计算机视觉",
    "强化学习", "具身智能", "智能体", "多模态",
    # English model/company names (appear verbatim in Chinese tech writing)
    "chatgpt", "openai", "anthropic", "claude", "gemini", "deepseek",
    "grok", "llama", "mistral", "midjourney", "stable diffusion",
    # Chinese model/company names
    "文心一言", "通义千问", "豆包", "混元", "讯飞星火", "kimi", "月之暗面",
    "minimax", "零一万物", "智谱", "glm", "商汤", "依图", "旷视",
    # AI technical ops
    "模型训练", "模型推理", "微调", "fine-tune", "向量数据库",
    "提示词", "prompt", "token", "transformer",
    # AI applications
    "自动驾驶", "智能驾驶", "无人驾驶",
    # Products/events with AI in name
    "ai日报", "ai研究", "ai应用", "ai时代", "ai赛道", "ai行业",
    "ai公司", "ai创业", "ai产品", "ai工具", "ai助手", "ai模型",
    "ai芯片", "ai算力", "ai训练", "ai推理", "ai落地", "ai场景",
}

# Context-dependent: need 2+ matches → about_ai = True
# These alone can be gaming/hardware/finance — but together suggest AI
AI_WEAK = {
    "智能", "算力", "gpu", "显卡", "英伟达", "nvidia",
    "agent", "推理", "芯片", "大数据", "云计算", "自动化",
}

# Any single match → about_tech = True
TECH_STRONG = {
    "科技", "技术", "互联网", "芯片", "半导体", "硬件", "软件",
    "编程", "代码", "开源", "云计算", "5g", "物联网", "机器人",
    "数字化", "区块链", "量子计算", "光刻机", "服务器", "数据库",
    "网络安全", "加密", "算法", "api", "开发者", "程序员",
    "cpu", "gpu", "显卡", "芯", "处理器",
}


def is_ai_article(text: str) -> bool:
    """
    True if article title+summary contain clear AI signals.
    Uses two tiers: any strong keyword, or 2+ weak keywords.
    """
    t = (text or "").lower()
    if any(kw in t for kw in AI_STRONG):
        return True
    weak_hits = sum(1 for kw in AI_WEAK if kw in t)
    return weak_hits >= 2


def is_tech_article(text: str) -> bool:
    """True if article title+summary contain tech signals."""
    t = (text or "").lower()
    return any(kw in t for kw in TECH_STRONG)


# ── China-specificity classifier ──────────────────────────────────────────────
#
# Logic: an article is china_related ONLY if it is specifically about a Chinese
# company / person / institution / locality / market / policy. A Chinese-source
# article that reports on OpenAI or Anthropic is NOT china_related.
#
# Rules (applied in order):
#   1. Any CHINA_STRONG hit  → True   (DeepSeek, Baidu, 百度, 中国, 工信部, …)
#   2. Any FOREIGN_ONLY hit, no CHINA_STRONG hit → False  (OpenAI / GPT-5 / Sam Altman)
#   3. Otherwise              → False  (be conservative on general/ambiguous topics)

# Subjects that anchor an article to China. Match is case-insensitive substring.
# Mix of pinyin / simplified Chinese / common English transliterations.
CHINA_STRONG = {
    # Chinese AI companies & products (most active in WeChat coverage)
    "deepseek", "deep seek", "深度求索", "梁文锋",
    "通义千问", "通义", "千问", "qwen", "阿里云", "alibaba cloud",
    "文心一言", "文心", "ernie",
    "豆包", "字节跳动", "字节", "bytedance", "tiktok china", "doubao", "火山引擎",
    "混元", "腾讯", "tencent", "微信", "wechat",
    "kimi", "月之暗面", "moonshot", "杨植麟",
    "智谱", "glm", "智谱清言", "chatglm",
    "minimax", "稀宇", "海螺",
    "零一万物", "01.ai", "yi-",
    "讯飞", "iflytek", "星火", "刘庆峰",
    "商汤", "sensetime",
    "旷视", "megvii",
    "依图", "yitu",
    "百度", "baidu", "李彦宏", "robin li", "百度智能云", "apollo",
    "阿里巴巴", "alibaba", "蚂蚁", "ant group", "马云", "jack ma",
    "京东", "jd.com", "刘强东", "richard liu",
    "美团", "meituan", "王兴",
    "拼多多", "pinduoduo", "黄峥",
    "滴滴", "didi",
    "小米", "xiaomi", "雷军", "lei jun",
    "华为", "huawei", "任正非", "ren zhengfei", "孟晚舟", "鸿蒙", "harmonyos",
    "比亚迪", "byd",
    "蔚来", "nio",
    "小鹏", "xpeng",
    "理想", "li auto", "理想汽车",
    "极氪", "zeekr",
    "宁德时代", "catl",
    "中芯国际", "smic",
    "海光", "海思", "hisilicon",
    "地平线", "horizon robotics",
    "寒武纪", "cambricon",
    "壁仞", "biren",
    "燧原", "enflame",
    "摩尔线程", "moore threads",
    "群核", "manycore",
    "傅利叶", "fourier intelligence",
    "宇树", "unitree",
    "智元", "agibot",
    "银河", "galaxea",
    "面壁", "modelbest",
    "百川", "baichuan", "王小川",

    # Chinese government / policy / regulators
    "中国", " china ", " china,", " china.", " china'", " china—", " china'",
    "工信部", "miit", "网信办", "cac", "cyberspace administration",
    "国务院", "state council", "中央", "中共", "中国共产党", "communist party",
    "央行", "people's bank of china", "pboc",
    "证监会", "csrc", "国资委", "sasac",
    "习近平", "xi jinping", "李强", "li qiang",
    "发改委", "ndrc",

    # Chinese cities & regions (high-signal in tech context)
    "北京", "beijing", "上海", "shanghai", "深圳", "shenzhen",
    "杭州", "hangzhou", "广州", "guangzhou", "成都", "chengdu",
    "苏州", "suzhou", "南京", "nanjing", "西安", "xi'an",
    "重庆", "chongqing", "天津", "tianjin", "武汉", "wuhan",
    "合肥", "hefei", "厦门", "xiamen",
    "香港", "hong kong", "澳门", "macau",
    "中关村", "zhongguancun",
    "雄安", "xiong'an", "粤港澳", "greater bay",

    # Chinese markets / finance ecosystem
    "a股", "a-share", "港股", "hong kong stock", "沪深", "上证", "深证",
    "创业板", "chinext", "科创板", "star market", "sse", "szse",
    "国资", "国企", "soes", "央企",

    # Chinese universities / research institutes
    "清华", "tsinghua", "北大", "peking university", "pku",
    "复旦", "fudan", "浙大", "zhejiang university", "上交大", "sjtu",
    "中科院", "chinese academy of sciences", "cas",
    "中科大", "ustc", "哈工大", "hit",
    "之江实验室", "zhejiang lab", "上海ai实验室", "shanghai ai laboratory",
    "智源", "baai",

    # General "Chinese" markers
    "国产", "国内", "国货", "国家队",
    "中文", "汉语", "汉字",
    "亚运", "奥运", "全运",
    "央视", "cctv", "新华社", "xinhua", "人民日报", "people's daily",
}

# Foreign-anchored subjects. Used to push toward False when no China signal exists.
# Deliberately narrow — we don't want false negatives on Chinese coverage of these.
FOREIGN_ONLY = {
    "openai", "chatgpt", "gpt-3", "gpt-4", "gpt-5", "sora",
    "anthropic", "claude", "dario amodei", "mira murati", "ilya sutskever",
    "sam altman", "altman",
    "google deepmind", "deepmind", "gemini", "sundar pichai", "demis hassabis",
    "meta ai", "llama", "mark zuckerberg",
    "microsoft", "satya nadella", "github copilot", "azure ai",
    "apple intelligence", "tim cook",
    "tesla", "spacex", "elon musk", "xai", "grok",
    "nvidia", "jensen huang",
    "amazon", "aws bedrock", "andy jassy",
    "perplexity", "stability ai", "stable diffusion", "midjourney",
    "cohere", "hugging face",
    "white house", "biden", "trump", "u.s. congress", "us senate",
    "european commission", "eu ai act",
}


def is_china_article(text: str) -> bool:
    """
    True if the article subject is specifically Chinese (company / person /
    institution / locality / policy). Returns False for general AI topics,
    pure US/EU coverage, and anything ambiguous.
    """
    t = (text or "").lower()
    if any(kw in t for kw in CHINA_STRONG):
        return True
    # Foreign-anchored with no China signal → not china_related
    if any(kw in t for kw in FOREIGN_ONLY):
        return False
    # Default: ambiguous → conservative False
    return False


# ── Per-article tagging ────────────────────────────────────────────────────────

def process_page(articles, source_maps):
    """
    Two-pass classification per article:
      1. Source flag (strong prior — dedicated AI/tech accounts)
      2. Keyword scan on title + summary (catches AI articles from general sources)

    Returns list of {id, about_tech, about_ai} ready for PATCH.
    """
    by_id, by_name, by_name_norm = source_maps
    rows = []

    ai_from_source = 0
    ai_from_keywords = 0
    tech_from_source = 0
    tech_from_keywords = 0
    china_hits = 0

    for art in articles:
        src   = lookup_source(art, by_id, by_name, by_name_norm)
        s_tech = src.get("about_tech") if src else None
        s_ai   = src.get("about_ai")   if src else None

        text = (art.get("original_title") or "") + " " + (art.get("original_summary") or "")

        # about_ai: source flag OR keyword detection
        if s_ai is True:
            ai = True
            ai_from_source += 1
        else:
            ai = is_ai_article(text)
            if ai:
                ai_from_keywords += 1

        # about_tech: source flag OR keyword detection (AI articles are also tech)
        if s_tech is True:
            tech = True
            tech_from_source += 1
        else:
            tech = ai or is_tech_article(text)
            if tech:
                tech_from_keywords += 1

        # china_related: subject-based, source-agnostic.
        # A Chinese WeChat account reporting on OpenAI is NOT china_related.
        china = is_china_article(text)
        if china:
            china_hits += 1

        rows.append({
            "id": art["id"],
            "about_tech": tech,
            "about_ai": ai,
            "china_related": china,
        })

    log.info(
        f"  [classify] {len(rows)} articles → "
        f"ai: {ai_from_source} from source + {ai_from_keywords} from keywords | "
        f"tech: {tech_from_source} from source + {tech_from_keywords} from keywords | "
        f"china: {china_hits}"
    )
    return rows


# ── New-article priority watcher ──────────────────────────────────────────────

def new_article_watcher(source_maps_ref: list, stop_event: threading.Event):
    """
    Background thread: every NEW_ARTICLE_INTERVAL seconds, fetch the newest
    untagged articles and classify them immediately.
    """
    log.info("[watcher] New-article priority watcher started")
    while not stop_event.is_set():
        stop_event.wait(NEW_ARTICLE_INTERVAL)
        if stop_event.is_set():
            break
        try:
            fresh = fetch_unfiltered(limit=200)
            if not fresh:
                continue
            log.info(f"[watcher] {len(fresh)} new untagged articles — tagging now")
            rows = process_page(fresh, source_maps_ref[0])
            write_rows(rows)
        except Exception as e:
            log.error(f"[watcher] error: {e}")


# ── Stats ──────────────────────────────────────────────────────────────────────
_stats = {"processed": 0, "errors": 0, "t0": time.time()}
_slock = threading.Lock()

def add_stats(processed=0, errors=0):
    with _slock:
        _stats["processed"] += processed
        _stats["errors"]    += errors

def log_stats(total, offset):
    elapsed   = time.time() - _stats["t0"]
    rate      = _stats["processed"] / elapsed * 3600 if elapsed > 0 else 0
    remaining = max(0, total - offset)
    eta_str   = (f"{remaining/rate:.1f}h" if rate > 0 else "∞")
    log.info(
        f"✓ {_stats['processed']:,} done  {offset:,}/{total:,} scanned  "
        f"{rate:,.0f}/hr  ETA {eta_str}  errors {_stats['errors']}"
    )


# ── DB write ───────────────────────────────────────────────────────────────────

def write_rows(rows):
    if not rows:
        return
    log.info(f"  [write] Writing {len(rows)} rows...")
    with ThreadPoolExecutor(max_workers=DB_WRITE_WORKERS) as ex:
        futures = {ex.submit(sb_patch_one, row): row for row in rows}
        ok = err = 0
        for fut in as_completed(futures):
            try:
                fut.result()
                ok += 1
            except Exception as e:
                log.error(f"PATCH error: {e}")
                err += 1
        log.info(f"  [write] Done: {ok} ok, {err} errors")
        add_stats(processed=ok, errors=err)


# ── Full re-classify pass ──────────────────────────────────────────────────────

def run_full_pass(source_maps):
    total = total_articles()
    log.info(f"Full re-classify pass: {total:,} total articles")
    _stats.update({"processed": 0, "errors": 0, "t0": time.time()})

    offset = 0
    while offset < total:
        log.info(f"Fetching page at offset {offset}...")
        articles = fetch_page(offset)
        if not articles:
            log.info("No more articles, stopping")
            break
        rows = process_page(articles, source_maps)
        write_rows(rows)
        offset += len(articles)
        log_stats(total, offset)

    log_stats(total, offset)
    log.info(f"Full pass complete — {_stats['processed']:,} articles classified.")


# ── Monitoring pass (new articles only) ───────────────────────────────────────

def run_monitor_pass(source_maps):
    remaining = count_unfiltered()
    if remaining == 0:
        log.info("Monitor check: nothing to do.")
        return
    log.info(f"Monitor pass: {remaining:,} unfiltered articles found")
    _stats.update({"processed": 0, "errors": 0, "t0": time.time()})
    while True:
        articles = fetch_unfiltered()
        if not articles:
            break
        rows = process_page(articles, source_maps)
        write_rows(rows)
        remaining = count_unfiltered()
        log_stats(remaining + _stats["processed"], _stats["processed"])
        if remaining == 0:
            break
    log.info(f"Monitor pass complete — {_stats['processed']:,} classified.")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info("SinoArk Filter — keyword + source classification")
    log.info(f"PAGE={PAGE_SIZE}  DB_WORKERS={DB_WRITE_WORKERS}")
    log.info("=" * 60)

    source_maps = load_sources()
    by_id, by_name, by_name_norm = source_maps
    log.info(f"Loaded {len(by_id)} sources")

    source_maps_ref = [source_maps]
    stop_event = threading.Event()

    watcher = threading.Thread(
        target=new_article_watcher,
        args=(source_maps_ref, stop_event),
        daemon=True,
    )
    watcher.start()

    run_full_pass(source_maps)

    log.info("Entering monitoring mode — checking every 60 min for new articles")
    while True:
        time.sleep(3600)
        log.info("--- Hourly check ---")
        source_maps = load_sources()
        source_maps_ref[0] = source_maps
        run_monitor_pass(source_maps)


if __name__ == "__main__":
    main()
