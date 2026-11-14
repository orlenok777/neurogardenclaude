"""Builds the three language pages from index.html (the Russian source).

    python3 i18n/build.py

- index.html stays the source; only its version block (between the NGTOP markers) is rewritten.
- en.html and he.html are generated: every Russian text fragment is replaced using i18n/en.json and i18n/he.json.
  The build fails if a fragment has no translation, so new Russian text cannot slip through untranslated.
- The version block comes from releases.json: the last entry is the current release, the one before it is the previous.
  Dates there are the commit dates of the releases; they are shown in Israel time.
"""
import json, re, sys, pathlib
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'i18n'))
from extract import FRAG

TZ = ZoneInfo('Asia/Jerusalem')
MONTHS_EN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
LANGS = {
    'ru': dict(file='index.html', name='Русский', ver='Версия', upd='обновлена', prev='Предыдущая версия', tz='время Израиля',
               when=lambda d: f'{d:%d.%m.%Y} в {d:%H:%M}'),
    'he': dict(file='he.html', name='עברית', ver='גרסה', upd='עודכנה', prev='הגרסה הקודמת', tz='שעון ישראל',
               when=lambda d: f'{d:%d.%m.%Y} בשעה {d:%H:%M}'),
    'en': dict(file='en.html', name='English', ver='Version', upd='updated', prev='Previous version', tz='Israel time',
               when=lambda d: f'{d.day} {MONTHS_EN[d.month - 1]} {d.year} at {d:%H:%M}'),
}
TOP = re.compile(r'<!--NGTOP-->.*?<!--/NGTOP-->', re.S)


def top_block(lang, cur, prev):
    L = LANGS[lang]
    def stamp(r):
        d = datetime.fromisoformat(r['at']).astimezone(TZ)
        return f'<time datetime="{d.isoformat()}">{L["when"](d)}</time>'
    links = ''.join(
        f'<a href="{v["file"]}" lang="{k}"{" aria-current=\"page\"" if k == lang else ""}>{v["name"]}</a>'
        for k, v in LANGS.items())
    return ('<!--NGTOP--><div class="top" id="vers">'
            f'<p><b id="ver">{L["ver"]} {cur["version"]}</b> · {L["upd"]} {stamp(cur)} <span class="mut">({L["tz"]})</span></p>'
            f'<p class="mut" id="prev">{L["prev"]}: {prev["version"]} · {stamp(prev)}</p>'
            f'<p class="langs">{links}</p></div><!--/NGTOP-->')


def one(text, a, b):
    assert text.count(a) == 1, f'expected exactly one {a!r}, found {text.count(a)}'
    return text.replace(a, b)


def localize(src, lang):
    table = json.load(open(ROOT / 'i18n' / f'{lang}.json', encoding='utf-8'))
    missing = sorted({m.group(0) for m in FRAG.finditer(src)} - set(table))
    if missing:
        sys.exit(f'{lang}: no translation for {len(missing)} fragment(s): {missing[:10]}')
    out = FRAG.sub(lambda m: table[m.group(0)], src)
    if lang == 'he':
        out = one(out, '<html lang="ru">', '<html lang="he" dir="rtl">')
        out = one(out, "const LOC='ru', TTS_LANG='ru', SPEECH='ru-RU';", "const LOC='he-IL', TTS_LANG='he', SPEECH='he-IL';")
        out = one(out, 'family=Onest:wght@400;500;700&family=Yeseva+One&display=swap', 'family=Rubik:wght@400;500;700&family=Frank+Ruhl+Libre:wght@500;700&display=swap')
        out = one(out, '</style>', ':root{--display:"Frank Ruhl Libre","Times New Roman",serif;--body:"Rubik","Arial Hebrew",Arial,sans-serif}\n</style>')
    else:
        out = one(out, '<html lang="ru">', '<html lang="en">')
        out = one(out, "const LOC='ru', TTS_LANG='ru', SPEECH='ru-RU';", "const LOC='en-GB', TTS_LANG='en', SPEECH='en-GB';")
    return out


def main():
    rel = json.load(open(ROOT / 'releases.json', encoding='utf-8'))
    cur, prev = rel[-1], rel[-2]
    src_path = ROOT / 'index.html'
    src = src_path.read_text(encoding='utf-8')
    assert len(TOP.findall(src)) == 1, 'index.html must contain exactly one NGTOP block'
    bare = TOP.sub('<!--NGTOP--><!--/NGTOP-->', src)
    src_path.write_text(bare.replace('<!--NGTOP--><!--/NGTOP-->', top_block('ru', cur, prev)), encoding='utf-8')
    for lang in ('he', 'en'):
        page = localize(bare, lang).replace('<!--NGTOP--><!--/NGTOP-->', top_block(lang, cur, prev))
        (ROOT / LANGS[lang]['file']).write_text(page, encoding='utf-8')
    print(f'built index.html, he.html, en.html: version {cur["version"]} ({cur["at"]}), previous {prev["version"]} ({prev["at"]})')


if __name__ == '__main__':
    main()
