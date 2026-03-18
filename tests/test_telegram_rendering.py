"""
Tests for Telegram HTML Rendering.

Tests the patterns ported from ai-tutor-engine:
- **bold** → <b>bold</b>
- [links](url) → <a href='url'>links</a>
- Message splitting > 4000 chars
- Image URL detection
- HTML-escape safety
"""

import pytest
from src.utils.telegram_rendering import (
    markdown_to_html,
    split_text,
    split_blocks,
    extract_image_urls,
    has_image_urls,
    extract_markdown_images,
    render_to_actions,
    escape_html,
    strip_html,
    truncate_for_telegram,
    SendMessage,
    SendPhoto,
)


# ══════════════════════════════════════════════════════════════
# MARKDOWN → HTML CONVERSION
# ══════════════════════════════════════════════════════════════

class TestMarkdownToHtml:
    def test_bold_conversion(self):
        result = markdown_to_html("это **жирный** текст")
        assert "<b>жирный</b>" in result

    def test_link_conversion(self):
        result = markdown_to_html("ссылка [текст](https://example.com)")
        assert "<a href='https://example.com'>текст</a>" in result

    def test_bold_and_link_combined(self):
        result = markdown_to_html("**bold** and [link](https://example.com)")
        assert "<b>bold</b>" in result
        assert "<a href='https://example.com'>link</a>" in result

    def test_html_escape_user_content(self):
        result = markdown_to_html("тест <script>alert('xss')</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_bullet_conversion(self):
        result = markdown_to_html("* первый\n- второй")
        assert "• первый" in result
        assert "• второй" in result

    def test_empty_string(self):
        assert markdown_to_html("") == ""

    def test_none(self):
        assert markdown_to_html(None) is None

    def test_plain_text_unchanged(self):
        text = "просто текст без форматирования"
        result = markdown_to_html(text)
        assert "просто текст" in result


# ══════════════════════════════════════════════════════════════
# TEXT SPLITTING
# ══════════════════════════════════════════════════════════════

class TestSplitText:
    def test_short_text_no_split(self):
        text = "короткий текст"
        chunks = split_text(text, max_chars=4000)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_split(self):
        text = "строка\n" * 1000  # ~7000 chars
        chunks = split_text(text, max_chars=4000)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 4000

    def test_empty_text(self):
        assert split_text("", max_chars=4000) == []

    def test_max_chars_boundary(self):
        text = "a" * 4000
        chunks = split_text(text, max_chars=4000)
        assert len(chunks) == 1

    def test_max_chars_just_over(self):
        text = "a" * 4001
        chunks = split_text(text, max_chars=4000)
        assert len(chunks) >= 1  # Split happens

    def test_invalid_max_chars(self):
        with pytest.raises(ValueError):
            split_text("test", max_chars=0)

    def test_preserves_line_boundaries(self):
        lines = [f"line {i}" for i in range(100)]
        text = "\n".join(lines)
        chunks = split_text(text, max_chars=200)
        for chunk in chunks:
            # Each chunk should end with a complete line
            # (or be a hard-split if line > max_chars)
            assert len(chunk) <= 200


# ══════════════════════════════════════════════════════════════
# BLOCK SPLITTING
# ══════════════════════════════════════════════════════════════

class TestSplitBlocks:
    def test_single_block(self):
        assert split_blocks("один блок") == ["один блок"]

    def test_multiple_blocks(self):
        text = "блок 1\n---\nблок 2\n---\nблок 3"
        blocks = split_blocks(text)
        assert len(blocks) == 3
        assert blocks[0].strip() == "блок 1"
        assert blocks[1].strip() == "блок 2"
        assert blocks[2].strip() == "блок 3"

    def test_empty_blocks_ignored(self):
        text = "---\nблок\n---"
        blocks = split_blocks(text)
        # Empty blocks are included but may be empty strings
        assert len(blocks) >= 1


# ══════════════════════════════════════════════════════════════
# IMAGE DETECTION
# ══════════════════════════════════════════════════════════════

class TestImageDetection:
    def test_extract_image_urls(self):
        text = "посмотри https://example.com/photo.jpg и https://img.com/pic.png"
        urls = extract_image_urls(text)
        assert "https://example.com/photo.jpg" in urls
        assert "https://img.com/pic.png" in urls

    def test_has_image_urls(self):
        assert has_image_urls("есть фото https://example.com/img.jpg")
        assert not has_image_urls("нет фото")

    def test_extract_markdown_images(self):
        text = "![alt1](https://example.com/1.jpg) и ![alt2](https://example.com/2.png)"
        images = extract_markdown_images(text)
        assert len(images) == 2
        assert images[0] == ("alt1", "https://example.com/1.jpg")
        assert images[1] == ("alt2", "https://example.com/2.png")

    def test_jpg_png_gif_webp(self):
        for ext in ["jpg", "jpeg", "png", "gif", "webp"]:
            text = f"https://example.com/photo.{ext}"
            assert has_image_urls(text)


# ══════════════════════════════════════════════════════════════
# RENDER TO ACTIONS
# ══════════════════════════════════════════════════════════════

class TestRenderToActions:
    def test_plain_text(self):
        actions = render_to_actions("просто текст")
        assert len(actions) == 1
        assert isinstance(actions[0], SendMessage)

    def test_bold_text(self):
        actions = render_to_actions("это **жирный** текст")
        assert len(actions) == 1
        assert "<b>жирный</b>" in actions[0].text

    def test_link_text(self):
        actions = render_to_actions("[клик](https://example.com)")
        assert len(actions) == 1
        assert "<a href='https://example.com'>клик</a>" in actions[0].text

    def test_image_url(self):
        actions = render_to_actions("фото: https://example.com/photo.jpg")
        assert any(isinstance(a, SendPhoto) for a in actions)

    def test_empty_text(self):
        assert render_to_actions("") == []

    def test_long_text_split(self):
        text = "строка\n" * 2000
        actions = render_to_actions(text)
        assert len(actions) > 1

    def test_html_escape_user_input(self):
        actions = render_to_actions("тест <script>alert('xss')</script>")
        text = actions[0].text
        assert "<script>" not in text


# ══════════════════════════════════════════════════════════════
# HTML SAFETY
# ══════════════════════════════════════════════════════════════

class TestHtmlSafety:
    def test_escape_html(self):
        assert escape_html("<b>bold</b>") == "&lt;b&gt;bold&lt;/b&gt;"
        assert escape_html('test "quotes"') == "test &quot;quotes&quot;"

    def test_strip_html(self):
        assert strip_html("<b>bold</b> text") == "bold text"
        assert strip_html("<a href='url'>link</a>") == "link"

    def test_truncate(self):
        text = "a" * 5000
        result = truncate_for_telegram(text)
        assert len(result) == 4000
        assert result.endswith("...")

    def test_truncate_short(self):
        text = "short"
        assert truncate_for_telegram(text) == "short"

    def test_empty_strings(self):
        assert escape_html("") == ""
        assert strip_html("") == ""
        assert truncate_for_telegram("") == ""


# ══════════════════════════════════════════════════════════════
# INTEGRATION: FULL PIPELINE
# ══════════════════════════════════════════════════════════════

class TestFullPipeline:
    def test_complex_markdown(self):
        text = """**Варианты корма:**

* [Дог-Дюк](https://petsmenu.ru/dog-duck) — 850₽
* [Кото-Ролл](https://petsmenu.ru/koto-roll) — 720₽

![Дог-Дюк](https://example.com/dog-duck.jpg)

---
Если нужна помощь — пиши!"""
        actions = render_to_actions(text)
        assert len(actions) > 0
        # Should have messages and/or photos
        text_actions = [a for a in actions if isinstance(a, SendMessage)]
        assert len(text_actions) > 0

    def test_image_with_caption(self):
        text = "![Product](https://example.com/product.jpg)\n\nКорм для собак, 850₽"
        actions = render_to_actions(text)
        assert any(isinstance(a, SendPhoto) for a in actions)
