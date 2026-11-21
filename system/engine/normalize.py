import calendar
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
import re
import unicodedata
from pathlib import Path

from match.names import name_form


@lru_cache(maxsize=32768)
def folded(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold())
                   if not unicodedata.combining(c))


def words(value):
    return ' '.join(re.findall(r'[^\W_]+', folded(value)))


@lru_cache(maxsize=32768)
def name_parts(value):
    # Cyrillic, Greek, Armenian, Arabic, ... names are compared in Latin letters via the
    # fixed tables in match/translit.py; otherwise they could never agree with anything.
    # Unsupported scripts stay as written. docs/plan/DECISIONS.md R3.
    form = name_form(value)
    if form.non_latin and form.latin:
        value = form.latin
    value = re.sub(r'\([^)]*\)|\[[^\]]*\]|\{[^}]*\}', ' ', value)
    value = re.sub(r"\bN\s*[/.-]\s*A\b", ' ', value, flags=re.I)
    value = re.sub(r"['’]", '', value)
    return tuple(dict.fromkeys(part for part in words(value).split()
                               if len(part) > 1 and part.isalpha()
                               and part not in {'na', 'test', 'unknown', 'xxx', 'none', 'null', 'nil', 'missing'}))


@lru_cache(maxsize=65536)
def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j-1] + 1, previous[j-1] + (left != right)))
        previous = current
    return previous[-1]


class Names:
    def __init__(self, groups, legal_forms):
        self.variants = {frozenset((a, b)) for group in groups
                         for a in group for b in group if a != b}
        self.legal = {words(form) for form in legal_forms}

    def parts(self, name, company=False):
        parts = name_parts(name)
        return tuple(part for part in parts if not company or part not in self.legal)

    def part_agrees(self, left, right):
        if left == right or frozenset((left, right)) in self.variants:
            return True
        size = min(len(left), len(right))
        if size < 4:
            return False
        tolerance = 1 if size <= 6 else 2
        return abs(len(left) - len(right)) <= tolerance and edit_distance(left, right) <= tolerance

    def agree(self, left, right):
        if not left or not right:
            return False
        if len(left) > len(right):
            left, right = right, left

        @lru_cache(maxsize=None)
        def search(used_l, used_r, count):
            if count == 2 or used_l == (1 << len(left)) - 1:
                return True
            for i, a in enumerate(left):
                mask_a = 1 << i
                if used_l & mask_a:
                    continue
                for j, b in enumerate(right):
                    mask_b = 1 << j
                    if used_r & mask_b:
                        continue
                    if self.part_agrees(a, b) and search(used_l | mask_a, used_r | mask_b, count + 1):
                        return True
                    if j + 1 < len(right) and not used_r & (mask_b << 1):
                        if a == b + right[j+1] and search(used_l | mask_a, used_r | (3 << j), count + 1):
                            return True
                    if i + 1 < len(left) and not used_l & (mask_a << 1):
                        if a + left[i+1] == b and search(used_l | (3 << i), used_r | mask_b, count + 1):
                            return True
            return False

        return search(0, 0, 0)


@dataclass(frozen=True, order=True)
class PartialDate:
    year: int
    month: int = 0
    day: int = 0

    def __str__(self):
        return f'{self.year:04}' + (f'-{self.month:02}' if self.month else '') + (f'-{self.day:02}' if self.day else '')

    def bounds(self):
        month = self.month or 12
        return (date(self.year, self.month or 1, self.day or 1),
                date(self.year, month, self.day or calendar.monthrange(self.year, month)[1]))


MONTHS = {name.casefold(): i for i in range(1, 13)
          for name in (calendar.month_name[i], calendar.month_abbr[i])}
MONTHS['sept'] = 9
MONTH = '(?:' + '|'.join(sorted(MONTHS, key=lambda v: (-len(v), v))) + ')'
YEAR = r'(?:1[5-9]\d{2}|20\d{2}|21\d{2})'
DATE_TEXT = (rf'(?:{YEAR}[-/]\d{{1,2}}[-/]\d{{1,2}}|{YEAR}-\d{{1,2}}|'
             rf'\d{{1,2}}[/-]\d{{1,2}}[/-]{YEAR}|'
             rf'\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH}\.?\s+{YEAR}|'
             rf'{MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+{YEAR}|{MONTH}\.?\s+{YEAR}|{YEAR})')


def _valid(year, month=0, day=0):
    try:
        if not 1500 <= year <= 2199 or day and not month:
            return ()
        date(year, month or 1, day or 1)
        return (PartialDate(year, month, day),)
    except ValueError:
        return ()


@lru_cache(maxsize=16384)
def dates(raw, day_first=None):
    value = raw.strip().casefold()
    if not value:
        return ()
    match = re.fullmatch(r'(\d{4})(?:[-/](\d{1,2})(?:[-/](\d{1,2}))?)?(?:[t ]\d{2}:\d{2}.*)?', value)
    if match:
        year, month, day = (int(part or 0) for part in match.groups())
        if (match[2] is not None and not month) or (match[3] is not None and not day):
            return ()
        return _valid(year, month, day)
    match = re.fullmatch(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', value)
    if match:
        a, b, y = map(int, match.groups())
        dmy = _valid(y, b, a) if a and b else ()
        mdy = _valid(y, a, b) if a and b else ()
        if day_first is True:
            return dmy or mdy
        if day_first is False:
            return mdy or dmy
        return tuple(sorted(set(dmy + mdy)))
    value = re.sub(r'(?<=\d)(?:st|nd|rd|th)\b', '', value)
    parts = re.sub(r'[.,]', ' ', value).split()
    if len(parts) == 3:
        left, right, year = parts
        if year.isdigit():
            if left in MONTHS and right.isdigit() and int(right):
                return _valid(int(year), MONTHS[left], int(right))
            if right in MONTHS and left.isdigit() and int(left):
                return _valid(int(year), MONTHS[right], int(left))
    if len(parts) == 2 and parts[0] in MONTHS and parts[1].isdigit():
        return _valid(int(parts[1]), MONTHS[parts[0]])
    return ()


def date_comparison(left, right):
    if abs(left.year - right.year) > 1:
        return 'contradicts'
    if left.year != right.year:
        return 'inconclusive'
    if left.month and right.month and left.month != right.month:
        return 'contradicts'
    if left.day and right.day and left.day != right.day:
        return 'contradicts'
    return 'agrees'


COUNTRY_ALIASES = {
    'US': 'USA|U.S.|U.S.A.|American|United States of America',
    'GB': 'UK|U.K.|British|United Kingdom of Great Britain and Northern Ireland',
    'AE': 'UAE|Emirati', 'AL': 'Albanian', 'AR': 'Argentine|Argentinian', 'AT': 'Austrian',
    'AU': 'Australian', 'BD': 'Bangladeshi', 'BE': 'Belgian', 'BR': 'Brazilian', 'CA': 'Canadian',
    'CH': 'Swiss', 'CI': "Ivory Coast|Ivorian|Cote D'Ivoire", 'CN': 'Chinese',
    'CO': 'Colombian', 'CZ': 'Czech|Czech Republic', 'DE': 'German', 'DK': 'Danish',
    'EC': 'Ecuadorian', 'EG': 'Egyptian', 'ES': 'Spanish', 'FI': 'Finnish', 'FR': 'French',
    'GH': 'Ghanaian', 'GR': 'Greek', 'HK': 'Hong Kong SAR|Hong Kong Special Administrative Region of China',
    'HR': 'Croatian', 'HU': 'Hungarian', 'ID': 'Indonesian', 'IE': 'Irish|Republic of Ireland',
    'IL': 'Israeli', 'IN': 'Indian', 'IR': 'Iran|Iranian', 'IQ': 'Iraqi', 'IS': 'Icelandic',
    'IT': 'Italian', 'JP': 'Japanese', 'KE': 'Kenyan', 'KP': 'North Korea|North Korean|DPRK',
    'KR': 'South Korea|South Korean|Republic of Korea', 'LB': 'Lebanese', 'LU': 'Luxembourgish',
    'MA': 'Moroccan', 'MD': 'Moldova|Moldovan', 'MO': 'Macau|Macao Special Administrative Region of China',
    'MX': 'Mexican', 'MY': 'Malaysian', 'NG': 'Nigerian', 'NL': 'Dutch|The Netherlands',
    'NO': 'Norwegian', 'NZ': 'New Zealander', 'PE': 'Peruvian', 'PH': 'Filipino|The Philippines',
    'PK': 'Pakistani', 'PL': 'Polish', 'PS': 'Palestine|Palestina|Palestinian Territories',
    'PT': 'Portuguese', 'RO': 'Romanian', 'RS': 'Serbian', 'RU': 'Russia|Russian|Russian Federation',
    'SA': 'Saudi|Saudi Arabian', 'SE': 'Swedish', 'SG': 'Singaporean', 'SI': 'Slovenian',
    'SO': 'Somali', 'SY': 'Syria|Syrian', 'TH': 'Thai', 'TR': 'Turkey|Turkish|Turkiye',
    'TW': 'Taiwan|Taiwanese', 'UA': 'Ukrainian', 'VE': 'Venezuela|Venezuelan',
    'VG': 'British Virgin Islands|Virgin Islands British', 'VN': 'Vietnam|Vietnamese',
    'XK': 'Kosovo|Kosovar', 'ZA': 'South African',
}
NONCOUNTRIES = {'', 'international', 'europe', 'unknown', 'na', 'n a', 'none', 'not available', 'unspecified'}


country_names = {}
countries = {}
for line in Path(__file__).with_name('countries.tsv').read_text(encoding='utf-8').splitlines():
    if not line.startswith('#'):
        code, alpha3, primary, *aliases = line.split('\t')
        country_names[code] = primary
        countries.update((words(alias), code) for alias in (code, alpha3, primary, *aliases))
country_names['XK'] = 'Kosovo'
for code, aliases in COUNTRY_ALIASES.items():
    countries.update((words(alias), code) for alias in aliases.split('|'))
country_names.update(US='United States', GB='United Kingdom', MO='Macao', RU='Russia', KP='North Korea', KR='South Korea')
COUNTRY_PHRASE = re.compile(r'\b(?:' + '|'.join(re.escape(x) for x in
    sorted((x for x in countries if len(x) > 3), key=lambda x: (-len(x), x))) + r')\b')


@lru_cache(maxsize=8192)
def country(value):
    return countries.get(words(value))


@lru_cache(maxsize=16384)
def country_end(value):
    cleaned = re.sub(r'\s*\[[^\]]*\]\s*$', '', value)
    return country(cleaned) or country(cleaned.rsplit(',', 1)[-1])


def present(value):
    return words(value) not in NONCOUNTRIES


def entity(value):
    return {'individual': 'person', 'person': 'person', 'natural person': 'person',
            'company': 'company', 'organization': 'company', 'organisation': 'company',
            'legal entity': 'company'}.get(words(value))
