"""Explainable repository-domain filters; no model call or extra GitHub requests."""
import re
from typing import Any, Dict, List, Optional

from .models import Repository


DOMAIN_PRESETS = {
    "ai": ("AI / 大模型", ["ai", "llm", "large language model", "artificial intelligence", "machine learning", "deep learning", "generative ai", "rag", "chatbot", "langchain", "大模型", "人工智能", "机器学习"]),
    "frontend": ("前端 / UI", ["frontend", "front end", "react", "vue", "svelte", "ui components", "design system", "前端"]),
    "devtools": ("开发工具", ["developer tools", "devtools", "code editor", "linter", "debugger", "开发工具"]),
    "data": ("数据处理", ["data analysis", "data engineering", "data pipeline", "etl", "dataframe", "数据分析"]),
    "crawler": ("爬虫 / 自动化", ["web scraping", "crawler", "scraping", "browser automation", "爬虫"]),
    "media": ("音视频", ["audio", "video", "ffmpeg", "speech", "音频", "视频"]),
    "commerce": ("电商", ["ecommerce", "e commerce", "shopping", "commerce", "电商"]),
}


def normalize_interests(value: Any) -> Dict[str, List[str]]:
    if not isinstance(value, dict):
        raise ValueError("领域偏好必须是对象")
    result = {}
    for key in ("domains", "keywords", "excluded_keywords"):
        raw = value.get(key, [])
        if isinstance(raw, str):
            raw = re.split(r"[,，\n]", raw)
        if not isinstance(raw, list) or len(raw) > 20:
            raise ValueError("每类领域偏好最多 20 项")
        items = []
        for item in raw:
            if not isinstance(item, str) or len(item) > 80:
                raise ValueError("领域关键词必须是最多 80 字的文本")
            item = item.strip().lower()
            if key == "domains" and item and item not in DOMAIN_PRESETS:
                raise ValueError("未知领域：{}".format(item))
            if item and item not in items:
                items.append(item)
        result[key] = items
    return result


def _contains(text: str, term: str) -> bool:
    # Match ai/llm as tokens, not inside unrelated words such as retail.
    text = re.sub(r"[-_]+", " ", text.casefold())
    term = re.sub(r"[-_]+", " ", term.casefold())
    pattern = re.escape(term)
    if term.isascii():
        pattern = r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def match_repository(repo: Repository, interests: Optional[dict]) -> Optional[List[str]]:
    """None means excluded; [] means allowed without a domain preference."""
    prefs = normalize_interests(interests or {})
    fields = [("仓库名", repo.full_name), ("简介", repo.description)]
    fields.extend(("Topic", topic) for topic in repo.topics)
    if any(_contains(text, term) for term in prefs["excluded_keywords"] for _, text in fields):
        return None
    reasons = []
    groups = [(DOMAIN_PRESETS[key][0], DOMAIN_PRESETS[key][1]) for key in prefs["domains"]]
    groups.extend((term, [term]) for term in prefs["keywords"])
    for label, terms in groups:
        evidence = next(((source, term) for source, text in fields for term in terms if _contains(text, term)), None)
        if evidence:
            reasons.append("领域匹配：{}（{}含 {}）".format(label, *evidence))
    return reasons if reasons or not groups else None
