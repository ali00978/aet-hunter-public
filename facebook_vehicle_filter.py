"""Conservative Facebook vehicle-only filter and safe provider photo normalization.

Runs on saved provider listings. No Facebook requests or bypasses.
Keep all sources consistent, and do not treat accessory listings as vehicles.
"""
import re
import unicodedata
from urllib.parse import urlsplit

# Car makers and recognizable model names; word boundaries avoid accidental
# matching inside "stuurwiel" or "modelauto".
MAKES = (
    "volkswagen","vw","opel","peugeot","renault","ford","toyota","fiat",
    "honda","suzuki","hyundai","kia","bmw","audi","mercedes","seat",
    "skoda","škoda","mazda","nissan","dacia","citroen","citroën",
    "mitsubishi","chevrolet","daewoo","mini","smart","saab","subaru",
    "polo","golf","corsa","astra","clio","twingo","megane","mégane",
    "scenic","punto","panda","fiesta","focus","passat","yaris","aygo",
    "picanto","alto","swift","fabia","ibiza","civic","jazz","micra",
    "meriva","agila","zafira","auris","corolla","fusion","mondeo",
    "ka","rio","ceed","c1","c2","c3","c4","206","207","208","307",
    "308","107","108","i10","i20","i30","a170","slk","logan","sandero",
)
# Dealership-specific excluded marques: match Marktplaats Hunter user filter.
EXCLUDED_MAKES = ("volvo", "alfa romeo")
# These words identify an accessory, parts-only listing or another vehicle
# category even if a car make/model appears elsewhere in the title.
PARTS = (
    "stuurwiel","stuur wielen","stuur wheel","stuurwielen",
    "sportstuur","sportsturen","m stuur","leren stuur","lenkrad",
    "steering wheel","wieldop","wieldoppen","voorbumper",
    "achterbumper","schakelpook","stuurbediening",
    "airbag","velg","velgen","wielenset","wielset","winterwielen",
    "zomerwielen","winterband","zomerband","bandenset","reservewiel",
    "complete wielen","compleet wielen","lichtmetaal","remschijf",
    "remblok","remklauw","achterlicht","koplamp","bumper","spoiler",
    "spiegelglas","spiegelkap","buitenspiegel","stoelbekleding",
    "autostoel","kinderzitje","maxi cosi","stoelverhoger",
    "autoradio","navigatiesysteem","navigatie systeem","carplay adapter",
    "display unit","dashboard","interieurset","interieur delen",
    "motorblok los","versnellingsbak los","onderdelen","onderdeel",
    "demontage","slooponderdelen","sloperij","voor onderdelen",
    "sloopauto","te koop onderdelen","gezocht","ik zoek",
    "modelauto","miniatuur","lego","speelgoed","hot wheels","hotwheels",
    "speelauto","radiografisch","bestuurbare","kart","go kart",
    "scooter","brommer","snorfiets","motorfiets","motorscooter","motorhelm",
    "quads","quad","motorcross","crossmotor","bromfiets","atv",
    "fietsendrager","dakkoffer","imperiaal","trekhaak los",
    "velours matten","automatten","vloermatten","hoes","hoezen",
    "versnellingspook","pookknop","schakelpook","sleutelbehuizing",
    "autoband","autobanden","dakdrager","uitlaatdemper","uitlaat los",
)
# Motorcycles with misleading BMW / Honda vehicle brand names.
MOTORCYCLES = (
    r"\bbmw\s+[krsf]\s*\d{3,4}\s*(?:gt|gs|rr|rt|r)?\b",
    r"\bhonda\s+(?:cbr|vfr|cbf|crf)\b",
    r"\bsuzuki\s+(?:gsxr?|v.?strom|bandit)\b",
    r"\byamaha\s+(?:yzf|r[136]|mt.?0?\d)\b",
    r"\bkawasaki\s+(?:ninja|z[1-9]\d\d)\b",
)

def _flat(value):
    return unicodedata.normalize("NFKC",str(value or "")).casefold()

def term(text, word):
    return bool(re.search(r"(?<!\w)" + re.escape(_flat(word)) + r"(?!\w)",text))

# Do not mistake a whole car with cosmetic bumper damage for a loose
# bumper part. A mileage/APK/running-car cue is required IN ADDITION to
# an explicitly damaged bumper. Year alone is too weak: parts sellers
# commonly name the model-year of compatible cars.
BUMPER_TERMS = ("bumper", "voorbumper", "achterbumper")
COMPLETE_CAR_CUE = re.compile(
    r"\b(?:apk|kenteken|rijdt|rijdend)\b|"
    r"(?<!\d)\d{1,3}(?:[.\s]\d{3}){1,2}\s*km\b|"
    r"(?<!\d)\d{4,6}\s*km\b")
BUMPER_DAMAGE = re.compile(
    r"\b(?:voorbumper|achterbumper|bumper)\b.{0,32}"
    r"\b(?:kras|krassen|krasjes|schade|beschadigd|deuk|deuken)\b|"
    r"\b(?:kras|krassen|krasjes|schade|beschadigd|deuk|deuken)\b.{0,32}"
    r"\b(?:voorbumper|achterbumper|bumper)\b")


def _minor_bumper_damage_on_whole_car(value):
    return bool(COMPLETE_CAR_CUE.search(value) and BUMPER_DAMAGE.search(value))


def is_vehicle_title(title):
    value=_flat(title)
    if len(value.strip())<5 or len(value)>300:
        return False
    if any(term(value,make) for make in EXCLUDED_MAKES):
        return False
    bumper_damage = _minor_bumper_damage_on_whole_car(value)
    if any(term(value,word) and not (bumper_damage and word in BUMPER_TERMS)
           for word in PARTS):
        return False
    if any(re.search(expression,value) for expression in MOTORCYCLES):
        return False
    return any(term(value,brand) for brand in MAKES)

def _valid_photo(url):
    if not isinstance(url,str) or len(url)>1600:
        return None
    try:
        parts=urlsplit(url.strip())
        host=(parts.hostname or "").lower()
        allowed=host in {"facebook.com","www.facebook.com","m.facebook.com","fbcdn.net",
                         "fbcdn.com","fbsbx.com"} or any(
            host.endswith("."+domain) for domain in ("fbcdn.net","fbcdn.com","fbsbx.com"))
        if parts.scheme!="https" or not allowed or parts.username or parts.password or parts.port not in (None,443):
            return None
        if not parts.path or parts.fragment:
            return None
        return url.strip()
    except ValueError:
        return None

def provider_photos(raw):
    """Use only real image URLs in the provider response; do not scrape Facebook."""
    if not isinstance(raw,dict):
        return []
    found=[]
    seen=set()
    fields=("photo_urls","images","photos","pictures","imageUrls","photoUrls",
            "image","imageUrl","photoUrl","image_url","thumbnailUrl","thumbnail",
            "photo","picture","primaryImage","coverPhoto","mainImage","listingImage")
    def add(value,depth=0):
        if depth>3 or len(found)>=4:
            return
        if isinstance(value,str):
            checked=_valid_photo(value)
            if checked and checked not in seen:
                seen.add(checked);found.append(checked)
        elif isinstance(value,list):
            for each in value[:8]:
                add(each,depth+1)
        elif isinstance(value,dict):
            for key in ("url","uri","src","imageUrl","largeUrl","fullUrl",
                        "thumbnailUrl","originalUrl","image","photo","images"):
                if key in value:add(value[key],depth+1)
    for key in fields:
        if key in raw:add(raw[key])
    return found[:4]
