"""
Telegram HTML Rendering — safe message formatting.

Ported from ai-tutor-engine/src/telegram/rendering.py:
- **bold** → <b>bold</b>
- [links](url) → <a href='url'>links</a>
- Message splitting > 4000 chars
- Image URL detection → sendPhoto
- HTML-escape all user input to prevent injection

Safe for Telegram Bot API HTML parse mode.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# TELEGRAM ACTION TYPES
# ══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SendMessage:
    """A Telegram action: send a text message (HTML parse mode)."""
    text: str


@dataclass(frozen=True)
class SendPhoto:
    """A Telegram action: send a photo by URL with optional caption."""
    photo_url: str
    caption: Optional[str] = None


TelegramAction = Union[SendMessage, SendPhoto]


# ══════════════════════════════════════════════════════════════
# REGEX PATTERNS
# ══════════════════════════════════════════════════════════════

# Match markdown images: ![alt](url) — on their own line
_IMAGE_RE = re.compile(r"(?m)^[ \t]*(?:[*-]\s+)?!\[[^\]]*]\(([^)]+)\)[ \t]*")

# Match markdown links: [text](url)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# Match markdown bold: **text**
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

# URL pattern for detecting standalone image URLs
_IMAGE_URL_RE = re.compile(
    r"https?://[^\s]+\.(?:jpg|jpeg|png|gif|webp)(?:\?[^\s]*)?",
    re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════
# MARKDOWN → HTML CONVERSION
# ══════════════════════════════════════════════════════════════

def markdown_to_html(text: str) -> str:
    """Convert limited Markdown subset to Telegram-safe HTML.

    Supported:
    - **bold** → <b>bold</b>
    - [text](url) → <a href='url'>text</a>
    - * item / - item → • item

    All other text is HTML-escaped to prevent entity parse errors.
    """
    if not text:
        return text

    # Extract links first (replace with placeholders)
    links: List[str] = []

    def repl_link(match: re.Match) -> str:
        label = match.group(1).strip()
        url = match.group(2).strip()
        if not label:
            link_html = html.escape(url, quote=False)
        else:
            link_html = (
                f"<a href='{html.escape(url, quote=True)}'>"
                f"{html.escape(label, quote=False)}"
                f"</a>"
            )
        placeholder = f"\x00LINK{len(links)}\x00"
        links.append(link_html)
        return placeholder

    text = _LINK_RE.sub(repl_link, text)

    # Extract bold (replace with placeholders)
    bolds: List[str] = []

    def repl_bold(match: re.Match) -> str:
        content = match.group(1).strip()
        bold_html = f"<b>{html.escape(content, quote=False)}</b>"
        placeholder = f"\x00BOLD{len(bolds)}\x00"
        bolds.append(bold_html)
        return placeholder

    text = _BOLD_RE.sub(repl_bold, text)

    # Escape everything else (prevents Telegram "can't parse entities")
    text = html.escape(text, quote=False)

    # Restore placeholders
    for i, link_html in enumerate(links):
        text = text.replace(f"\x00LINK{i}\x00", link_html)
    for i, bold_html in enumerate(bolds):
        text = text.replace(f"\x00BOLD{i}\x00", bold_html)

    # Nicer bullets: "* item" / "- item" → "• item"
    text = re.sub(r"(?m)^(\s*)[*-]\s+", r"\1• ", text)

    return text


# ══════════════════════════════════════════════════════════════
# TEXT SPLITTING
# ══════════════════════════════════════════════════════════════

def split_text(text: str, max_chars: int = 4000) -> List[str]:
    """Split a long string into Telegram-safe chunks.

    Telegram limit is 4096 chars; we use 4000 for safety margin.
    Splits at line boundaries when possible.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")

    if not text:
        return []

    if len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    current = ""

    for line in text.splitlines():
        extra = len(line) + (1 if current else 0)

        if extra > max_chars:
            # Hard-split very long single lines
            if current:
                chunks.append(current)
                current = ""
            start = 0
            while start < len(line):
                chunks.append(line[start:start + max_chars])
                start += max_chars
            continue

        if len(current) + extra > max_chars and current:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line

    if current:
        chunks.append(current)

    return chunks


def split_blocks(markdown: str) -> List[str]:
    """Split markdown into blocks using --- separators."""
    blocks: List[str] = []
    current: List[str] = []
    for line in markdown.splitlines():
        if line.strip() == "---":
            blocks.append("\n".join(current))
            current = []
            continue
        current.append(line)
    blocks.append("\n".join(current))
    return blocks


# ══════════════════════════════════════════════════════════════
# IMAGE DETECTION
# ══════════════════════════════════════════════════════════════

def extract_image_urls(text: str) -> List[str]:
    """Extract standalone image URLs from text."""
    if not text:
        return []
    return _IMAGE_URL_RE.findall(text)


def has_image_urls(text: str) -> bool:
    """Check if text contains image URLs."""
    return bool(extract_image_urls(text))


def extract_markdown_images(text: str) -> List[Tuple[str, str]]:
    """Extract markdown images: ![alt](url) → [(alt, url), ...]"""
    if not text:
        return []
    return [(m.group(1) or "", m.group(2) or "") for m in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", text)]


# ══════════════════════════════════════════════════════════════
# RENDER TO ACTIONS
# ══════════════════════════════════════════════════════════════

def render_to_actions(
    markdown: str,
    *,
    max_message_chars: int = 4000,
    max_caption_chars: int = 1024,
) -> List[TelegramAction]:
    """Convert text (with optional Markdown) into Telegram actions.

    Handles:
    - Markdown images → SendPhoto
    - Long text → split into chunks
    - HTML escaping for safety

    Args:
        markdown: LLM/tool output that may include Markdown images/links/bold.
        max_message_chars: Max chars per sendMessage chunk.
        max_caption_chars: Max chars per sendPhoto caption.

    Returns:
        List of TelegramAction objects to send in order.
    """
    actions: List[TelegramAction] = []
    if not markdown:
        return actions

    for block in split_blocks(markdown):
        block = block.strip()
        if not block:
            continue

        # Check for standalone image URLs
        image_urls = extract_image_urls(block)
        if image_urls:
            # Send each image as a photo, remaining text as messages
            text_without_images = block
            for url in image_urls:
                text_without_images = text_without_images.replace(url, "").strip()

            html_text = markdown_to_html(text_without_images) if text_without_images else None
            caption = html_text if html_text and len(html_text) <= max_caption_chars else None

            for url in image_urls:
                actions.append(SendPhoto(photo_url=url, caption=caption))
                caption = None  # Only first photo gets caption

            if html_text and not caption:
                for chunk in split_text(html_text, max_message_chars):
                    actions.append(SendMessage(text=chunk))
            continue

        # No images — just convert markdown to HTML and send
        html_text = markdown_to_html(block)
        if html_text:
            for chunk in split_text(html_text, max_message_chars):
                actions.append(SendMessage(text=chunk))

    return actions


# ══════════════════════════════════════════════════════════════
# HTML SAFETY
# ══════════════════════════════════════════════════════════════

def escape_html(text: str) -> str:
    """HTML-escape text for safe Telegram rendering."""
    if not text:
        return text
    return html.escape(text, quote=True)


def strip_html(text: str) -> str:
    """Remove all HTML tags from text."""
    if not text:
        return text
    return re.sub(r"<[^>]+>", "", text)


def truncate_for_telegram(text: str, max_chars: int = 4000) -> str:
    """Truncate text to fit Telegram message limit."""
    if not text or len(text) <= max_chars:
        return text
    return text[:max_chars - 3] + "..."
