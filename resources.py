"""塔罗牌图片资源下载工具。"""

from __future__ import annotations

import asyncio
import os
import posixpath
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from src.app.plugin_system.api.log_api import get_logger

logger = get_logger("tarot.resources")


class _DirectoryLinkParser(HTMLParser):
    """解析 Python SimpleHTTPServer 目录页中的链接。"""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.links.append(value)
                return


def _resolve_path(raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else Path.cwd() / p


def _url_join(base_url: str, path: str) -> str:
    base = base_url if base_url.endswith("/") else base_url + "/"
    return urllib.parse.urljoin(base, path)


def _safe_name_from_href(href: str) -> str | None:
    parsed = urllib.parse.urlparse(href)
    if parsed.scheme or parsed.netloc:
        return None
    path = urllib.parse.unquote(parsed.path)
    name = posixpath.basename(path.rstrip("/"))
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        return None
    return name


def _read_directory_links(url: str) -> list[str]:
    with urllib.request.urlopen(url, timeout=30) as response:
        html = response.read().decode("utf-8", "replace")
    parser = _DirectoryLinkParser()
    parser.feed(html)
    return parser.links


def _download_file(url: str, target: Path) -> None:
    tmp = target.with_name(target.name + ".tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=120) as response, tmp.open("wb") as out:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                out.write(chunk)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _download_tree(base_url: str, remote_dir: str, local_dir: Path) -> tuple[int, int]:
    url = _url_join(base_url, remote_dir)
    downloaded = 0
    skipped = 0

    for href in _read_directory_links(url):
        name = _safe_name_from_href(href)
        if name is None:
            continue

        quoted_name = urllib.parse.quote(name)
        if href.endswith("/"):
            sub_downloaded, sub_skipped = _download_tree(
                base_url,
                posixpath.join(remote_dir.rstrip("/"), quoted_name) + "/",
                local_dir / name,
            )
            downloaded += sub_downloaded
            skipped += sub_skipped
            continue

        if not re.search(r"\.(?:png|jpe?g|webp)$", name, re.IGNORECASE):
            continue

        target = local_dir / name
        if target.exists() and target.stat().st_size > 0:
            skipped += 1
            continue

        file_url = _url_join(base_url, posixpath.join(remote_dir.rstrip("/"), quoted_name))
        _download_file(file_url, target)
        downloaded += 1

    return downloaded, skipped


async def ensure_tarot_resources(resource_path: str, base_url: str) -> None:
    """从资源服务器下载缺失的塔罗牌图片到主程序 data 目录。"""

    target_dir = _resolve_path(resource_path)
    try:
        downloaded, skipped = await asyncio.to_thread(
            _download_tree,
            base_url,
            "resources/",
            target_dir,
        )
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        logger.warning(f"塔罗牌图片资源自动下载失败: {exc}")
        return

    logger.info(
        f"塔罗牌图片资源检查完成，目录: {target_dir}，新增 {downloaded} 个，已存在 {skipped} 个"
    )
