"""The three public owner tiles shipped with v1."""

from __future__ import annotations

EXAMPLES: list[dict] = [
    {
        "id": "video-analysis",
        "owner": "fotiosb",
        "title": "Multi-Agent Behavioral Video Analysis",
        "url": "https://github.com/fotiosb/Multi-Agent-Behavioral-Video-Analysis",
        "blurb": "RTSP anomaly detection with YOLO + Gemini + Claude. FastAPI / React.",
        "size": "small",
        "image": "/examples/video-analysis.svg",
        "warm_first": True,
        "wall_seconds": 60,
    },
    {
        "id": "proxy-aggregator",
        "owner": "fotiosb",
        "title": "Residential Proxy Aggregator",
        "url": "https://github.com/fotiosb/residential-proxy-aggregator",
        "blurb": "Windows edge nodes into a SOCKS5 pool. Ships ~27MB binaries — cache warm.",
        "size": "~27MB binaries",
        "image": "/examples/proxy-aggregator.svg",
        "warm_first": False,
        "wall_seconds": 90,
    },
    {
        "id": "mac-presenter",
        "owner": "fotiosb",
        "title": "MacPresenterView",
        "url": "https://github.com/fotiosb/MacPresenterView",
        "blurb": "Slides to NDI notes. Node + Chrome extension.",
        "size": "small",
        "image": "/examples/mac-presenter.svg",
        "warm_first": True,
        "wall_seconds": 60,
    },
]


def example_by_url(url: str) -> dict | None:
    needle = (url or "").rstrip("/").lower().removesuffix(".git")
    for ex in EXAMPLES:
        target = ex["url"].rstrip("/").lower()
        if needle == target or needle.startswith(target + "/"):
            return ex
    return None


def example_urls() -> list[str]:
    return [ex["url"] for ex in EXAMPLES]


def warm_order() -> list[dict]:
    first = [ex for ex in EXAMPLES if ex.get("warm_first")]
    rest = [ex for ex in EXAMPLES if not ex.get("warm_first")]
    return first + rest
