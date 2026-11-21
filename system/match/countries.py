"""Step 1c: countries (POLICY principles 2, 5, 6). Owner: feat/match.

Every country is reduced to one canonical ISO 3166-1 alpha-2 code ("NG", "GB").

API
  canonical(text) -> set[str]          a country FIELD: code, name or nationality word
  canonical_many(text) -> set[str]     'Spain; Italy'
  from_address(address) -> set[str]    the last part of an address that is a country
  from_addresses(text) -> set[str]     a hit's ';'-separated addresses field
  find_in_text(text) -> set[str]       every country named in free text (notes, profiles)
  residence_cue_unresolved(text) -> bool   a "lived/resided/based in ..." sentence in which
                                       no country could be found (Step 4: don't clear on location)
  name(code) -> str                    'NG' -> 'Nigeria', for reason sentences

Safety rule: a word that could be more than one country maps to ALL of them
(Congo -> CG and CD; Georgia -> GE and US). Extra countries can only prevent a
LOCATION_MISMATCH clearance; a missing one could cause an unsafe clearance.
'International' and 'Europe' are not countries (principle 5). Territories are their
own country (Hong Kong HK, Jersey JE, Puerto Rico PR...). England, Scotland, Wales
and Northern Ireland are GB.
"""
from __future__ import annotations

import re
import unicodedata

# ISO2 ISO3 | English name | other names ; ... | nationality words ; ...
_TABLE = """
AF AFG|Afghanistan||Afghan
AX ALA|Aland Islands|Aland|Alandic
AL ALB|Albania||Albanian
DZ DZA|Algeria||Algerian
AS ASM|American Samoa||American Samoan
AD AND|Andorra||Andorran
AO AGO|Angola||Angolan
AI AIA|Anguilla||Anguillan
AQ ATA|Antarctica||
AG ATG|Antigua and Barbuda|Antigua|Antiguan
AR ARG|Argentina|Argentine Republic|Argentine;Argentinian
AM ARM|Armenia||Armenian
AW ABW|Aruba||Aruban
AU AUS|Australia||Australian
AT AUT|Austria||Austrian
AZ AZE|Azerbaijan||Azerbaijani;Azeri
BS BHS|Bahamas|The Bahamas|Bahamian
BH BHR|Bahrain||Bahraini
BD BGD|Bangladesh||Bangladeshi
BB BRB|Barbados||Barbadian;Bajan
BY BLR|Belarus|Byelorussia|Belarusian
BE BEL|Belgium||Belgian
BZ BLZ|Belize||Belizean
BJ BEN|Benin|Dahomey|Beninese
BM BMU|Bermuda||Bermudian
BT BTN|Bhutan||Bhutanese
BO BOL|Bolivia|Plurinational State of Bolivia|Bolivian
BQ BES|Bonaire, Sint Eustatius and Saba|Caribbean Netherlands;Bonaire|
BA BIH|Bosnia and Herzegovina|Bosnia;Bosnia-Herzegovina|Bosnian
BW BWA|Botswana||Botswanan;Motswana
BR BRA|Brazil|Brasil|Brazilian
IO IOT|British Indian Ocean Territory||
VG VGB|British Virgin Islands|Virgin Islands, British;BVI|
BN BRN|Brunei|Brunei Darussalam|Bruneian
BG BGR|Bulgaria||Bulgarian
BF BFA|Burkina Faso|Upper Volta|Burkinabe
BI BDI|Burundi||Burundian
CV CPV|Cabo Verde|Cape Verde|Cape Verdean
KH KHM|Cambodia|Kampuchea|Cambodian
CM CMR|Cameroon||Cameroonian
CA CAN|Canada||Canadian
KY CYM|Cayman Islands|Caymans|Caymanian
CF CAF|Central African Republic||Central African
TD TCD|Chad||Chadian
CL CHL|Chile||Chilean
CN CHN|China|People's Republic of China;PRC;Mainland China|Chinese
CX CXR|Christmas Island||
CC CCK|Cocos (Keeling) Islands|Cocos Islands|
CO COL|Colombia||Colombian
KM COM|Comoros||Comoran;Comorian
CG COG|Republic of the Congo|Congo-Brazzaville;Congo Republic|
CD COD|Democratic Republic of the Congo|DR Congo;DRC;Congo-Kinshasa;Zaire;Democratic Republic of Congo|Congolese
CK COK|Cook Islands||
CR CRI|Costa Rica||Costa Rican
CI CIV|Cote d'Ivoire|Ivory Coast|Ivorian
HR HRV|Croatia||Croatian;Croat
CU CUB|Cuba||Cuban
CW CUW|Curacao||Curacaoan
CY CYP|Cyprus||Cypriot
CZ CZE|Czech Republic|Czechia|Czech
DK DNK|Denmark||Danish;Dane
DJ DJI|Djibouti||Djiboutian
DM DMA|Dominica||
DO DOM|Dominican Republic||
EC ECU|Ecuador||Ecuadorian;Ecuadorean
EG EGY|Egypt||Egyptian
SV SLV|El Salvador|Salvador|Salvadoran;Salvadorean
GQ GNQ|Equatorial Guinea||Equatoguinean;Equatorial Guinean
ER ERI|Eritrea||Eritrean
EE EST|Estonia||Estonian
SZ SWZ|Eswatini|Swaziland|Swazi
ET ETH|Ethiopia||Ethiopian
FK FLK|Falkland Islands|Falklands;Malvinas|Falkland Islander
FO FRO|Faroe Islands|Faroes|Faroese
FJ FJI|Fiji||Fijian
FI FIN|Finland||Finnish;Finn
FR FRA|France||French
GF GUF|French Guiana||French Guianese
PF PYF|French Polynesia|Tahiti|French Polynesian;Tahitian
GA GAB|Gabon||Gabonese
GM GMB|Gambia|The Gambia|Gambian
GE GEO|Georgia||Georgian
DE DEU|Germany|Deutschland;Federal Republic of Germany;West Germany;East Germany;German Democratic Republic|German
GH GHA|Ghana||Ghanaian
GI GIB|Gibraltar||Gibraltarian
GR GRC|Greece|Hellenic Republic|Greek
GL GRL|Greenland||Greenlandic;Greenlander
GD GRD|Grenada||Grenadian
GP GLP|Guadeloupe||Guadeloupean
GU GUM|Guam||Guamanian
GT GTM|Guatemala||Guatemalan
GG GGY|Guernsey||
GN GIN|Guinea|Guinea-Conakry|Guinean
GW GNB|Guinea-Bissau|Guinea Bissau|Bissau-Guinean
GY GUY|Guyana||Guyanese
HT HTI|Haiti||Haitian
VA VAT|Holy See|Vatican;Vatican City|
HN HND|Honduras||Honduran
HK HKG|Hong Kong|Hong Kong SAR;Hong Kong Special Administrative Region of China;Hong Kong SAR, China|Hongkonger
HU HUN|Hungary||Hungarian
IS ISL|Iceland||Icelandic;Icelander
IN IND|India|Bharat|Indian
ID IDN|Indonesia||Indonesian
IR IRN|Iran|Islamic Republic of Iran;Persia|Iranian;Persian
IQ IRQ|Iraq||Iraqi
IE IRL|Ireland|Republic of Ireland;Eire|Irish
IM IMN|Isle of Man||Manx
IL ISR|Israel||Israeli
IT ITA|Italy|Italia|Italian
JM JAM|Jamaica||Jamaican
JP JPN|Japan||Japanese
JE JEY|Jersey||
JO JOR|Jordan||Jordanian
KZ KAZ|Kazakhstan||Kazakh;Kazakhstani
KE KEN|Kenya||Kenyan
KI KIR|Kiribati||I-Kiribati
KP PRK|North Korea|Democratic People's Republic of Korea;DPRK;Korea, North|North Korean
KR KOR|South Korea|Republic of Korea;Korea, South;Korea, Republic of|South Korean
XK XKX|Kosovo||Kosovar;Kosovan
KW KWT|Kuwait||Kuwaiti
KG KGZ|Kyrgyzstan|Kyrgyz Republic;Kirghizia|Kyrgyz;Kyrgyzstani
LA LAO|Laos|Lao People's Democratic Republic;Lao PDR|Laotian;Lao
LV LVA|Latvia||Latvian
LB LBN|Lebanon||Lebanese
LS LSO|Lesotho||Basotho;Mosotho
LR LBR|Liberia||Liberian
LY LBY|Libya|Libyan Arab Jamahiriya|Libyan
LI LIE|Liechtenstein||Liechtensteiner
LT LTU|Lithuania||Lithuanian
LU LUX|Luxembourg||Luxembourgish;Luxembourger
MO MAC|Macao|Macau;Macao SAR;Macao Special Administrative Region of China;Macau SAR, China|Macanese
MG MDG|Madagascar||Malagasy
MW MWI|Malawi||Malawian
MY MYS|Malaysia||Malaysian
MV MDV|Maldives||Maldivian
ML MLI|Mali||Malian
MT MLT|Malta||Maltese
MH MHL|Marshall Islands||Marshallese
MQ MTQ|Martinique||Martinican
MR MRT|Mauritania||Mauritanian
MU MUS|Mauritius||Mauritian
YT MYT|Mayotte||Mahoran
MX MEX|Mexico|Mejico|Mexican
FM FSM|Micronesia|Federated States of Micronesia|Micronesian
MD MDA|Moldova|Republic of Moldova|Moldovan
MC MCO|Monaco||Monegasque;Monacan
MN MNG|Mongolia||Mongolian
ME MNE|Montenegro||Montenegrin
MS MSR|Montserrat||Montserratian
MA MAR|Morocco||Moroccan
MZ MOZ|Mozambique||Mozambican
MM MMR|Myanmar|Burma|Burmese
NA NAM|Namibia||Namibian
NR NRU|Nauru||Nauruan
NP NPL|Nepal||Nepalese;Nepali
NL NLD|Netherlands|The Netherlands;Holland|Dutch
NC NCL|New Caledonia||New Caledonian
NZ NZL|New Zealand|Aotearoa|New Zealander;Kiwi
NI NIC|Nicaragua||Nicaraguan
NE NER|Niger||Nigerien
NG NGA|Nigeria|Federal Republic of Nigeria|Nigerian
NU NIU|Niue||Niuean
NF NFK|Norfolk Island||
MK MKD|North Macedonia|Macedonia;Republic of North Macedonia;FYROM|Macedonian
MP MNP|Northern Mariana Islands|Northern Marianas|
NO NOR|Norway||Norwegian
OM OMN|Oman||Omani
PK PAK|Pakistan||Pakistani
PW PLW|Palau||Palauan
PS PSE|Palestine|Palestina;State of Palestine;Palestinian Territories;Occupied Palestinian Territory;West Bank;Gaza;Gaza Strip|Palestinian
PA PAN|Panama||Panamanian
PG PNG|Papua New Guinea||Papua New Guinean
PY PRY|Paraguay||Paraguayan
PE PER|Peru||Peruvian
PH PHL|Philippines|The Philippines|Filipino;Philippine
PN PCN|Pitcairn Islands|Pitcairn|
PL POL|Poland||Polish;Pole
PT PRT|Portugal||Portuguese
PR PRI|Puerto Rico||Puerto Rican
QA QAT|Qatar||Qatari
RE REU|Reunion||Reunionese
RO ROU|Romania|Rumania|Romanian
RU RUS|Russia|Russian Federation|Russian
RW RWA|Rwanda||Rwandan
BL BLM|Saint Barthelemy|St Barthelemy;St. Barthelemy|
SH SHN|Saint Helena|St Helena;St. Helena|
KN KNA|Saint Kitts and Nevis|St Kitts and Nevis;St. Kitts and Nevis|Kittitian
LC LCA|Saint Lucia|St Lucia;St. Lucia|Saint Lucian
MF MAF|Saint Martin|St Martin;St. Martin|
PM SPM|Saint Pierre and Miquelon||
VC VCT|Saint Vincent and the Grenadines|St Vincent and the Grenadines;St. Vincent|Vincentian
WS WSM|Samoa||Samoan
SM SMR|San Marino||Sammarinese
ST STP|Sao Tome and Principe||Sao Tomean
SA SAU|Saudi Arabia|Kingdom of Saudi Arabia;KSA|Saudi;Saudi Arabian
SN SEN|Senegal||Senegalese
RS SRB|Serbia||Serbian;Serb
SC SYC|Seychelles||Seychellois
SL SLE|Sierra Leone||Sierra Leonean
SG SGP|Singapore||Singaporean
SX SXM|Sint Maarten||
SK SVK|Slovakia|Slovak Republic|Slovak
SI SVN|Slovenia||Slovenian;Slovene
SB SLB|Solomon Islands||Solomon Islander
SO SOM|Somalia||Somali
ZA ZAF|South Africa|Republic of South Africa|South African
SS SSD|South Sudan||South Sudanese
ES ESP|Spain|Espana|Spanish;Spaniard
LK LKA|Sri Lanka|Ceylon|Sri Lankan
SD SDN|Sudan||Sudanese
SR SUR|Suriname|Surinam|Surinamese
SE SWE|Sweden||Swedish;Swede
CH CHE|Switzerland|Swiss Confederation|Swiss
SY SYR|Syria|Syrian Arab Republic|Syrian
TW TWN|Taiwan|Republic of China;Chinese Taipei;Taiwan, Province of China|Taiwanese
TJ TJK|Tajikistan||Tajik;Tajikistani
TZ TZA|Tanzania|United Republic of Tanzania|Tanzanian
TH THA|Thailand|Siam|Thai
TL TLS|Timor-Leste|East Timor|Timorese
TG TGO|Togo||Togolese
TK TKL|Tokelau||
TO TON|Tonga||Tongan
TT TTO|Trinidad and Tobago|Trinidad|Trinidadian;Tobagonian
TN TUN|Tunisia||Tunisian
TR TUR|Turkey|Turkiye;Republic of Turkey|Turkish;Turk
TM TKM|Turkmenistan||Turkmen
TC TCA|Turks and Caicos Islands|Turks and Caicos|
TV TUV|Tuvalu||Tuvaluan
UG UGA|Uganda||Ugandan
UA UKR|Ukraine|The Ukraine|Ukrainian
AE ARE|United Arab Emirates|UAE;Emirates;Dubai;Abu Dhabi|Emirati
GB GBR|United Kingdom|UK;U.K.;Great Britain;Britain;England;Scotland;Wales;Northern Ireland;United Kingdom of Great Britain and Northern Ireland|British;English;Scottish;Scot;Welsh;Northern Irish
US USA|United States|United States of America;USA;U.S.A.;U.S.;America|American
UM UMI|United States Minor Outlying Islands||
VI VIR|US Virgin Islands|U.S. Virgin Islands;Virgin Islands, U.S.;United States Virgin Islands|
UY URY|Uruguay||Uruguayan
UZ UZB|Uzbekistan||Uzbek;Uzbekistani
VU VUT|Vanuatu||Ni-Vanuatu
VE VEN|Venezuela|Bolivarian Republic of Venezuela|Venezuelan
VN VNM|Vietnam|Viet Nam|Vietnamese
WF WLF|Wallis and Futuna||
EH ESH|Western Sahara||Sahrawi
YE YEM|Yemen||Yemeni
ZM ZMB|Zambia||Zambian
ZW ZWE|Zimbabwe|Rhodesia|Zimbabwean
"""

# Words that may mean more than one country: map to all of them (safety rule).
_AMBIGUOUS = {
    "congo": {"CG", "CD"},
    "korea": {"KR", "KP"}, "korean": {"KR", "KP"},
    "georgia": {"GE", "US"},                  # the country, or the US state
    "virgin islands": {"VG", "VI"},
    "dominican": {"DO", "DM"},
    "guinean": {"GN", "GQ", "GW", "PG"},
    "sudanese": {"SD", "SS"},
    "macedonia": {"MK", "GR"},                # North Macedonia, or the Greek region
    "soviet union": {"RU", "UA", "BY", "MD", "GE", "AM", "AZ", "KZ", "KG", "TJ", "TM", "UZ", "EE", "LV", "LT"},
    "ussr": {"RU", "UA", "BY", "MD", "GE", "AM", "AZ", "KZ", "KG", "TJ", "TM", "UZ", "EE", "LV", "LT"},
    "yugoslavia": {"RS", "HR", "SI", "BA", "MK", "ME", "XK"},
    "czechoslovakia": {"CZ", "SK"},
    "serbia and montenegro": {"RS", "ME", "XK"},
    "netherlands antilles": {"CW", "SX", "BQ", "AW"},
}

# Sub-national names that contain a country name but belong elsewhere.
_OVERRIDES = {
    "new mexico": {"US"}, "new jersey": {"US"}, "new south wales": {"AU"},
    "british columbia": {"CA"}, "jordan valley": {"JO"},
}

NOT_COUNTRIES = frozenset({"international", "europe", "worldwide", "global", "unknown",
                           "n a", "na", "none", "other", "eu", "european union", "africa",
                           "asia", "middle east", "latin america", "south america",
                           "north america", "caribbean", "scandinavia", "balkans"})

_NAMES: dict[str, str] = {}        # code -> display name
_FIELD: dict[str, set[str]] = {}   # normalised field value -> codes
_TEXT: dict[str, set[str]] = {}    # normalised phrase usable inside free text -> codes


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[’'`]s\b", "", s)          # possessive: "France's" -> "France"
    s = re.sub(r"[’'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"^the ", "", s.strip())


def _build() -> None:
    for line in _TABLE.strip().splitlines():
        codes, nm, alts, demonyms = line.split("|")
        iso2, iso3 = codes.split()
        _NAMES[iso2] = nm
        _FIELD.setdefault(_norm(iso2), set()).add(iso2)
        _FIELD.setdefault(_norm(iso3), set()).add(iso2)
        for word in [nm, *alts.split(";"), *demonyms.split(";")]:
            if word.strip():
                key = _norm(word)
                _FIELD.setdefault(key, set()).add(iso2)
                _TEXT.setdefault(key, set()).add(iso2)
    for table in (_AMBIGUOUS, _OVERRIDES):
        for word, codes in table.items():
            _FIELD[_norm(word)] = set(codes)
            _TEXT[_norm(word)] = set(codes)
    # 'NA' is Namibia's code, but as a field value it is far more often "not available".
    _FIELD.pop("na", None)
    # Short abbreviations are only trusted in prose when written in capitals (below):
    # lower-case 'us', 'uk' etc. are ordinary words or noise.
    for short in ("uk", "u k", "usa", "u s a", "u s", "uae", "drc", "dprk", "prc", "ksa", "bvi"):
        _TEXT.pop(short, None)


_build()
_TEXT_UPPER_ABBREVIATIONS = {"UK": {"GB"}, "U.K.": {"GB"}, "USA": {"US"}, "U.S.A.": {"US"},
                             "U.S.": {"US"}, "UAE": {"AE"}, "DRC": {"CD"}, "DPRK": {"KP"},
                             "PRC": {"CN"}, "KSA": {"SA"}, "BVI": {"VG"}}
_MAX_WORDS = max(len(k.split()) for k in _TEXT)


def name(code: str) -> str:
    return _NAMES.get(code, code)


def canonical(text: str) -> set[str]:
    """Codes for one country FIELD value: 'NG', 'NGA', 'Nigeria', 'Nigerian',
    "Côte d'Ivoire" -> {'CI'}. 'International', 'Europe', '' -> set().
    Bracketed tags are dropped ('Spain [PEP]' -> {'ES'})."""
    s = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text or "")
    key = _norm(s)
    if not key or key in NOT_COUNTRIES:
        return set()
    if key in _FIELD:
        return set(_FIELD[key])
    # A value not in the table ('Macao Special Administrative Region of China' spelt
    # differently, 'Belgrade, Serbia'): fall back to every country phrase inside it.
    return _scan(s)


def canonical_many(text: str, sep: str = ";") -> set[str]:
    """A field holding several countries: 'Spain; Italy'."""
    out: set[str] = set()
    for part in (text or "").split(sep):
        out |= canonical(part)
    return out


def from_address(address: str) -> set[str]:
    """Country of one address: the last comma-separated part that is a country
    ('…, A1C 1K4, Canada [Business]' -> {'CA'}). Postcodes, regions and cities at the
    end are skipped. If no part is a country, set() (cities are not looked up)."""
    s = re.sub(r"\[[^\]]*\]", " ", address or "")
    for part in reversed([p.strip() for p in s.split(",") if p.strip()]):
        if _norm(part) in _FIELD:
            return set(_FIELD[_norm(part)])
    return set()


def from_addresses(text: str) -> set[str]:
    """A hit's `addresses` or `place_of_birth` field: several entries separated by ';'."""
    out: set[str] = set()
    for a in (text or "").split(";"):
        out |= from_address(a)
    return out


def _scan(text: str) -> set[str]:
    """Longest-first phrase lookup over normalised words; then upper-case abbreviations."""
    out: set[str] = set()
    words = _norm(text).split()
    i = 0
    while i < len(words):
        for n in range(min(_MAX_WORDS, len(words) - i), 0, -1):
            phrase = " ".join(words[i:i + n])
            if phrase in _TEXT:
                out |= _TEXT[phrase]
                i += n
                break
        else:
            i += 1
    for abbr, codes in _TEXT_UPPER_ABBREVIATIONS.items():
        if re.search(rf"(?<![A-Za-z]){re.escape(abbr)}(?![A-Za-z])", text or ""):
            out |= codes
    return out


_KEEP = re.compile(r"[A-ZÀ-Þ][\w'’.\-]*|\b(?:and|of|the|da|de|del|y)\b|[,;:()]")


def find_in_text(text: str, exclude_names: list[str] | tuple[str, ...] = ()) -> set[str]:
    """Every country named in free text, by name, other name, nationality word or an
    upper-case abbreviation (UK, USA, UAE). Only capitalised words count, so 'china
    plates' or 'turkey sandwich' don't, while 'China' or 'Turkey' do.

    exclude_names: names of the person/company the text is about. Their words are
    removed first, so 'Jordan Smith' or 'Georgia Brown' is not read as a country.
    ALWAYS pass it for search results: there a spurious country can make a namesake's
    result count as the hit's (unsafe). For customer notes over-collecting is safe."""
    if not text:
        return set()
    for nm in exclude_names:
        for word in sorted({w for w in re.split(r"[\s,]+", nm or "") if len(w) > 1}, key=len, reverse=True):
            text = re.sub(rf"(?<![\w]){re.escape(word)}(?![\w])", " ", text, flags=re.I)
    if text.isupper():
        # ALL-CAPS text gives no capitalisation signal: scan it as it is.
        return _scan(text)
    return _scan(" ".join(_KEEP.findall(text)))


_RESIDENCE_CUE = re.compile(
    r"\b(liv(?:e|ed|es|ing)|resid\w*|based|relocat\w*|moved|grew up|stay(?:ed|ing)?|"
    r"settled|domiciled|home in|address in|emigrat\w*|immigrat\w*)\b", re.I)


def residence_cue_unresolved(text: str) -> bool:
    """True if some sentence talks about where the person lived (lived, resided,
    based, moved, relocated, ...) but names no country we can resolve, e.g. 'used to
    live in Lagos'. Step 4 should then not clear on location (an unknown past home)."""
    for sentence in re.split(r"(?<=[.!?;])\s+|\n", text or ""):
        if _RESIDENCE_CUE.search(sentence) and not find_in_text(sentence):
            return True
    return False
