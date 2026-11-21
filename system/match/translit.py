"""Fixed-table transliteration of non-Latin names into Latin letters.

Deterministic by construction: plain lookup tables, no model, no randomness, so
every run gives the same spelling.

Scripts covered (one letter ~ one sound, so a table is reliable):
  Cyrillic (Russian, Ukrainian, Belarusian, Serbian/Macedonian letters),
  Greek, Armenian, Georgian, Japanese kana (katakana and hiragana).
Not covered, on purpose: Arabic and Hebrew (vowels are usually not written, so a
table gives 'mhmd' for Muhammad), Chinese, Korean, Indic scripts. Names in those
scripts get no Latin form; names.py keeps the original and flags it.

The output is a "most likely" spelling. Under the one-way rule (see names.py) a
transliterated name can add agreement but never create a mismatch, so a wrong
spelling here can cost a review, never an unsafe clearance.
"""
from __future__ import annotations

import unicodedata

# --- Cyrillic (BGN/PCGN-style, Russian readings where languages differ) ---------
_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
    # Ukrainian / Belarusian
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g", "ў": "u",
    # Serbian / Macedonian
    "ђ": "dj", "ј": "j", "љ": "lj", "њ": "nj", "ћ": "c", "џ": "dz", "ѓ": "gj",
    "ќ": "kj", "ѕ": "dz",
}

# --- Armenian (lowercase; uppercase is folded by .lower()) -----------------------
_ARMENIAN_DIGRAPHS = {"ու": "u"}
_ARMENIAN = {
    "ա": "a", "բ": "b", "գ": "g", "դ": "d", "ե": "e", "զ": "z", "է": "e", "ը": "y",
    "թ": "t", "ժ": "zh", "ի": "i", "լ": "l", "խ": "kh", "ծ": "ts", "կ": "k", "հ": "h",
    "ձ": "dz", "ղ": "gh", "ճ": "ch", "մ": "m", "յ": "y", "ն": "n", "շ": "sh", "ո": "o",
    "չ": "ch", "պ": "p", "ջ": "j", "ռ": "r", "ս": "s", "վ": "v", "տ": "t", "ր": "r",
    "ց": "ts", "ւ": "v", "փ": "p", "ք": "k", "օ": "o", "ֆ": "f", "և": "ev",
}

# --- Georgian (Mkhedruli, national romanisation) ---------------------------------
_GEORGIAN = {
    "ა": "a", "ბ": "b", "გ": "g", "დ": "d", "ე": "e", "ვ": "v", "ზ": "z", "თ": "t",
    "ი": "i", "კ": "k", "ლ": "l", "მ": "m", "ნ": "n", "ო": "o", "პ": "p", "ჟ": "zh",
    "რ": "r", "ს": "s", "ტ": "t", "უ": "u", "ფ": "p", "ქ": "k", "ღ": "gh", "ყ": "q",
    "შ": "sh", "ჩ": "ch", "ც": "ts", "ძ": "dz", "წ": "ts", "ჭ": "ch", "ხ": "kh",
    "ჯ": "j", "ჰ": "h",
}

# --- Greek (applied after accents are stripped) ----------------------------------
_GREEK_DIGRAPHS = {"ου": "ou", "αι": "ai", "ει": "ei", "οι": "oi", "ευ": "ev",
                   "αυ": "av", "γγ": "ng"}
_GREEK = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th",
    "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p",
    "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps",
    "ω": "o",
}

# --- Japanese kana (modified Hepburn; long-vowel mark dropped) --------------------
_KANA = {
    "ア": "a", "イ": "i", "ウ": "u", "エ": "e", "オ": "o",
    "カ": "ka", "キ": "ki", "ク": "ku", "ケ": "ke", "コ": "ko",
    "ガ": "ga", "ギ": "gi", "グ": "gu", "ゲ": "ge", "ゴ": "go",
    "サ": "sa", "シ": "shi", "ス": "su", "セ": "se", "ソ": "so",
    "ザ": "za", "ジ": "ji", "ズ": "zu", "ゼ": "ze", "ゾ": "zo",
    "タ": "ta", "チ": "chi", "ツ": "tsu", "テ": "te", "ト": "to",
    "ダ": "da", "ヂ": "ji", "ヅ": "zu", "デ": "de", "ド": "do",
    "ナ": "na", "ニ": "ni", "ヌ": "nu", "ネ": "ne", "ノ": "no",
    "ハ": "ha", "ヒ": "hi", "フ": "fu", "ヘ": "he", "ホ": "ho",
    "バ": "ba", "ビ": "bi", "ブ": "bu", "ベ": "be", "ボ": "bo",
    "パ": "pa", "ピ": "pi", "プ": "pu", "ペ": "pe", "ポ": "po",
    "マ": "ma", "ミ": "mi", "ム": "mu", "メ": "me", "モ": "mo",
    "ヤ": "ya", "ユ": "yu", "ヨ": "yo",
    "ラ": "ra", "リ": "ri", "ル": "ru", "レ": "re", "ロ": "ro",
    "ワ": "wa", "ヰ": "i", "ヱ": "e", "ヲ": "o", "ン": "n", "ヴ": "vu",
}
_KANA_SMALL_Y = {"ャ": "a", "ュ": "u", "ョ": "o"}
_KANA_SMALL_VOWEL = {"ァ": "a", "ィ": "i", "ゥ": "u", "ェ": "e", "ォ": "o"}
_KANA_SOKUON = "ッ"          # doubles the next consonant
_KANA_LONG = "ー"            # long vowel: dropped (Tōkyō -> tokyo)
_KANA_SEPARATORS = "・＝゠"  # between given name and family name


def _hiragana_to_katakana(c: str) -> str:
    o = ord(c)
    return chr(o + 0x60) if 0x3041 <= o <= 0x3096 else c


def _kana(text: str) -> str:
    out: list[str] = []
    double_next = False
    for c in map(_hiragana_to_katakana, text):
        if c in _KANA_SEPARATORS:
            out.append(" ")
            continue
        if c == _KANA_LONG:
            continue
        if c == _KANA_SOKUON:
            double_next = True
            continue
        if c in _KANA_SMALL_Y and out and out[-1].endswith("i"):
            prev = out.pop()[:-1]                     # 'ki' -> 'k', 'shi' -> 'sh'
            y = "" if prev.endswith(("sh", "ch", "j")) else "y"
            out.append(prev + y + _KANA_SMALL_Y[c])   # kya, sha, cho, ja
            continue
        if c in _KANA_SMALL_VOWEL and out:
            prev = out.pop()
            stem = "w" if prev == "u" else prev[:-1]  # ウィ -> wi, ファ -> fa, ティ -> ti
            out.append(stem + _KANA_SMALL_VOWEL[c])
            continue
        roman = _KANA.get(c) or _KANA_SMALL_VOWEL.get(c) or _KANA_SMALL_Y.get(c)
        if roman is None:
            out.append(c)
            continue
        if double_next:
            roman = ("t" + roman) if roman.startswith("ch") else (roman[0] + roman)
            double_next = False
        out.append(roman)
    return "".join(out)


def _apply(text: str, digraphs: dict[str, str], letters: dict[str, str]) -> str:
    for k, v in digraphs.items():
        text = text.replace(k, v)
    return "".join(letters.get(c, c) for c in text)


def script_of(c: str) -> str | None:
    """Unicode script family of one letter, e.g. 'LATIN', 'CYRILLIC', 'ARABIC', 'CJK'.
    None for anything that is not a letter (digits, spaces, punctuation)."""
    if not c.isalpha():
        return None
    name = unicodedata.name(c, "")
    if not name:
        return "UNKNOWN"
    first = name.split()[0]
    if first in ("HIRAGANA", "KATAKANA", "KATAKANA-HIRAGANA"):
        return "KANA"
    return first


def transliterate(text: str) -> str:
    """Convert every covered script to Latin letters; leave everything else as is.
    Input should be lowercase NFC. Accents on Latin/Greek letters are left for the
    caller to strip (Greek is handled here, after its own accent stripping)."""
    text = _apply(text, _ARMENIAN_DIGRAPHS, {**_CYRILLIC, **_ARMENIAN, **_GEORGIAN})
    text = _kana(text)
    # Greek: strip tonos/dialytika first so the table only needs base letters.
    if any(script_of(c) == "GREEK" for c in text):
        text = "".join(c for c in unicodedata.normalize("NFD", text)
                       if not unicodedata.combining(c))
        text = _apply(text, _GREEK_DIGRAPHS, _GREEK)
    return text


COVERED_SCRIPTS = frozenset({"CYRILLIC", "GREEK", "ARMENIAN", "GEORGIAN", "KANA"})
