import time

import pytest

from giftexchange.exchanges.markup import to_html


@pytest.mark.parametrize(
    "text, html",
    [
        ("*about €30*", "<p><strong>about €30</strong></p>"),
        ("_low elf-esteem_", "<p><em>low elf-esteem</em></p>"),
        ("~~The election.~~", "<p><s>The election.</s></p>"),
        ("~The election.~", "<p><s>The election.</s></p>"),
        ("about ~5 people and ~10 gifts", "<p>about ~5 people and ~10 gifts</p>"),
        ("Gifts -- if you like -- welcome", "<p>Gifts -- if you like -- welcome</p>"),
        ("snake_case and 2*3*4", "<p>snake_case and 2*3*4</p>"),
        (
            "<b>bold</b> & <script>",
            "<p>&lt;b&gt;bold&lt;/b&gt; &amp; &lt;script&gt;</p>",
        ),
        ("one\ntwo\n\nthree", "<p>one<br>two</p>\n<p>three</p>"),
        (
            "see https://example.org/a_b_c",
            '<p>see <a href="https://example.org/a_b_c" rel="noopener nofollow">'
            "https://example.org/a_b_c</a></p>",
        ),
        ("javascript:alert(1) http://x.org", "<p>javascript:alert(1) http://x.org</p>"),
    ],
)
def test_formatting(text, html):
    assert to_html(text) == html


def test_formatting_stays_fast():
    started = time.perf_counter()
    to_html("*" + "a" * 100_000 + "_" * 100_000 + "~~" + "b" * 100_000)
    assert time.perf_counter() - started < 0.5
