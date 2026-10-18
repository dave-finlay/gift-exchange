"""The organizer's messages: plain text with a few markers.

    *bold*   _italic_   ~~crossed out~~   https:// links   blank line = new paragraph

Everything is escaped first, so HTML in a message is shown as typed, never
interpreted: the text ends up in other people's inboxes. In the plain-text
version of an email the message appears exactly as typed, where the
markers still read naturally.

The patterns use possessive quantifiers (++), which never backtrack, so
the time taken stays linear in the message's length.
"""

import re

from django.utils.html import escape
from django.utils.safestring import mark_safe

LINK = re.compile(r"https://[^\s<>\"']++")
# Markers only count at word edges, so snake_case and 2*3*4 stay as typed.
BOLD = re.compile(r"(?<!\w)\*([^*\n]++)\*(?!\w)")
ITALIC = re.compile(r"(?<!\w)_([^_\n]++)_(?!\w)")
STRIKE = re.compile(r"~~([^~\n]++)~~")


def _inline(text):
    """Escape a line of text and apply the markers, leaving links alone
    (their underscores aren't italics)."""
    parts, last = [], 0
    for match in LINK.finditer(text):
        parts.append(_format(escape(text[last : match.start()])))
        url = escape(match.group(0))
        parts.append(f'<a href="{url}" rel="noopener nofollow">{url}</a>')
        last = match.end()
    parts.append(_format(escape(text[last:])))
    return "".join(parts)


def _format(escaped):
    escaped = BOLD.sub(r"<strong>\1</strong>", escaped)
    escaped = ITALIC.sub(r"<em>\1</em>", escaped)
    return STRIKE.sub(r"<s>\1</s>", escaped)


def to_html(text):
    """Paragraphs of formatted text, safe to put in a page or an email."""
    paragraphs = re.split(r"\n[ \t]*\n", text.replace("\r\n", "\n").strip())
    html = []
    for paragraph in paragraphs:
        if paragraph.strip():
            lines = [_inline(line) for line in paragraph.split("\n")]
            html.append("<p>" + "<br>".join(lines) + "</p>")
    return mark_safe("\n".join(html))
