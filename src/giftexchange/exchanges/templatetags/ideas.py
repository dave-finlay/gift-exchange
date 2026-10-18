import re

from django import template
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

register = template.Library()

HTTPS_LINK = re.compile(r"https://[^\s<>\"']+")


@register.filter
def https_links(text):
    """Escape a gift idea, making only https:// links clickable."""
    parts, last = [], 0
    for match in HTTPS_LINK.finditer(text):
        parts.append(escape(text[last : match.start()]))
        url = match.group(0)
        parts.append(
            format_html('<a href="{}" rel="noopener nofollow">{}</a>', url, url)
        )
        last = match.end()
    parts.append(escape(text[last:]))
    return mark_safe("".join(parts))


@register.filter
def message(text):
    """An organizer's message, with its few formatting markers."""
    from giftexchange.exchanges.markup import to_html

    return to_html(text)
