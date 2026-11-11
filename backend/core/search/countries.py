"""Country and city aliases for location matching and coverage reporting."""
import re
from functools import lru_cache

# Map canonical country names to a list of aliases that may appear in job locations.
COUNTRY_ALIASES: dict[str, list[str]] = {
    # Remote / distributed
    "Remote": ["remote", "anywhere", "worldwide", "work from home", "wfh"],
    # East Africa and nearby
    "Kenya": ["kenya", "nairobi", "mombasa", "kisumu", "nakuru", "eldoret"],
    "Uganda": ["uganda", "kampala"],
    "Tanzania": ["tanzania", "dar es salaam", "dodoma", "zanzibar", "arusha"],
    "Rwanda": ["rwanda", "kigali"],
    "Ethiopia": ["ethiopia", "addis ababa"],
    "South Africa": ["south africa", "cape town", "johannesburg", "durban", "pretoria", "sandton"],
    "Nigeria": ["nigeria", "lagos", "abuja"],
    "Ghana": ["ghana", "accra"],
    "Egypt": ["egypt", "cairo"],
    "Morocco": ["morocco", "casablanca", "rabat"],
    # Europe
    "United Kingdom": [
        "uk", "united kingdom", "england", "scotland", "wales", "northern ireland",
        "london", "manchester", "birmingham", "leeds", "glasgow", "sheffield",
        "bristol", "liverpool", "edinburgh", "cardiff", "belfast", "cambridge",
        "oxford", "reading", "brighton",
    ],
    "Ireland": ["ireland", "republic of ireland", "dublin", "cork", "galway"],
    "Germany": [
        "germany", "deutschland", "berlin", "munich", "münchen", "hamburg",
        "cologne", "köln", "frankfurt", "stuttgart", "düsseldorf", "dusseldorf",
        "leipzig", "dortmund", "essen", "bremen", "dresden", "hanover", "nuremberg",
    ],
    "France": ["france", "paris", "lyon", "marseille", "toulouse", "nice", "nantes", "strasbourg"],
    "Netherlands": ["netherlands", "holland", "amsterdam", "rotterdam", "the hague", "utrecht", "eindhoven"],
    "Spain": ["spain", "madrid", "barcelona", "valencia", "seville", "bilbao"],
    "Italy": ["italy", "rome", "milan", "naples", "turin", "florence", "bologna"],
    "Switzerland": ["switzerland", "zurich", "geneva", "basel", "bern", "lausanne"],
    "Sweden": ["sweden", "stockholm", "gothenburg", "göteborg", "malmö", "malmo"],
    "Norway": ["norway", "oslo", "bergen", "trondheim"],
    "Denmark": ["denmark", "copenhagen", "aarhus", "odense"],
    "Finland": ["finland", "helsinki", "espoo", "tampere"],
    "Belgium": ["belgium", "brussels", "bruxelles", "antwerp", "ghent"],
    "Austria": ["austria", "vienna", "wien", "salzburg", "graz", "linz"],
    "Poland": ["poland", "warsaw", "krakow", "kraków", "wrocław", "wroclaw", "gdansk", "poznan"],
    "Czech Republic": ["czech republic", "czechia", "prague", "brno"],
    "Portugal": ["portugal", "lisbon", "lisboa", "porto"],
    "Greece": ["greece", "athens", "thessaloniki"],
    "Hungary": ["hungary", "budapest"],
    "Romania": ["romania", "bucharest", "cluj"],
    # North America
    "Canada": [
        "canada", "toronto", "vancouver", "montreal", "calgary", "ottawa",
        "edmonton", "winnipeg", "quebec city", "hamilton", "kitchener", "waterloo",
    ],
    "Mexico": ["mexico", "mexico city", "guadalajara", "monterrey"],
    # United States — country + states + major cities
    "United States": [
        "usa", "united states", "america", "us",
        # States
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
        "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
        "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
        "maine", "maryland", "massachusetts", "michigan", "minnesota",
        "mississippi", "missouri", "montana", "nebraska", "nevada",
        "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
        "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
        "rhode island", "south carolina", "south dakota", "tennessee", "texas",
        "utah", "vermont", "virginia", "washington", "west virginia",
        "wisconsin", "wyoming",
        "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
        "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
        "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
        "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
        "wi", "wy",
        # Major cities
        "new york city", "los angeles", "chicago", "houston", "phoenix",
        "philadelphia", "san antonio", "san diego", "dallas", "san jose",
        "austin", "jacksonville", "fort worth", "columbus", "charlotte",
        "san francisco", "indianapolis", "seattle", "denver", "washington dc",
        "boston", "el paso", "nashville", "detroit", "oklahoma city", "portland",
        "las vegas", "louisville", "baltimore", "milwaukee", "albuquerque",
        "tucson", "fresno", "sacramento", "mesa", "kansas city", "atlanta",
        "long beach", "colorado springs", "raleigh", "omaha", "miami", "oakland",
        "minneapolis", "tulsa", "cleveland", "wichita", "arlington", "boulder",
        "pittsburgh", "cincinnati", "anchorage", "honolulu", "boise", "madison",
        "salt lake city", "des moines", "richmond", "baton rouge", "tacoma",
        "durham", "chattanooga", "spokane", "fort lauderdale", "new orleans",
    ],
    # Asia / Pacific
    "India": ["india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "chennai", "pune", "kolkata"],
    "Pakistan": ["pakistan", "karachi", "lahore", "islamabad"],
    "Bangladesh": ["bangladesh", "dhaka"],
    "Singapore": ["singapore"],
    "Japan": ["japan", "tokyo", "osaka", "yokohama", "nagoya", "sapporo", "fukuoka"],
    "South Korea": ["south korea", "seoul", "busan", "incheon"],
    "China": ["china", "beijing", "shanghai", "shenzhen", "guangzhou", "hangzhou"],
    "Hong Kong": ["hong kong"],
    "Australia": ["australia", "sydney", "melbourne", "brisbane", "perth", "adelaide"],
    "New Zealand": ["new zealand", "auckland", "wellington", "christchurch"],
    "Philippines": ["philippines", "manila", "cebu"],
    "Indonesia": ["indonesia", "jakarta", "bali"],
    "Malaysia": ["malaysia", "kuala lumpur"],
    "Thailand": ["thailand", "bangkok"],
    "Vietnam": ["vietnam", "ho chi minh city", "hanoi"],
    "United Arab Emirates": ["united arab emirates", "uae", "dubai", "abu dhabi"],
    "Israel": ["israel", "tel aviv", "jerusalem"],
    "Turkey": ["turkey", "türkiye", "istanbul", "ankara", "izmir"],
    # Latin America
    "Brazil": ["brazil", "brasil", "sao paulo", "são paulo", "rio de janeiro", "brasilia", "belo horizonte"],
    "Argentina": ["argentina", "buenos aires"],
    "Chile": ["chile", "santiago"],
    "Colombia": ["colombia", "bogota", "bogotá", "medellin", "medellín"],
    "Peru": ["peru", "lima"],
    "Uruguay": ["uruguay", "montevideo"],
    "Costa Rica": ["costa rica", "san jose"],
}

# Countries shown as nearby suggestions when coverage is low.
NEARBY_COUNTRIES: dict[str, list[str]] = {
    "Kenya": ["Uganda", "Tanzania", "Rwanda", "Ethiopia", "Remote"],
    "Uganda": ["Kenya", "Tanzania", "Rwanda", "Remote"],
    "Tanzania": ["Kenya", "Uganda", "Rwanda", "Remote"],
    "Rwanda": ["Kenya", "Uganda", "Tanzania", "Remote"],
    "Ethiopia": ["Kenya", "Uganda", "Tanzania", "Remote"],
    "South Africa": ["Remote"],
}


# Precompiled regex patterns for each alias, indexed by country.
_ALIAS_PATTERNS: dict[str, list[re.Pattern]] = {
    country: [re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE) for alias in aliases]
    for country, aliases in COUNTRY_ALIASES.items()
}


def _location_parts(location: str) -> list[str]:
    """Split a location string into candidate parts."""
    parts = re.split(r"[,;/|()]+", location)
    return [part.strip() for part in parts if part.strip()]


@lru_cache(maxsize=2048)
def _detect_countries_tuple(location: str) -> tuple[str, ...]:
    """Cached version returning an immutable tuple."""
    if not location:
        return ()
    parts = _location_parts(location)
    matched: list[str] = []
    seen: set[str] = set()

    for country, patterns in _ALIAS_PATTERNS.items():
        for pattern in patterns:
            if any(pattern.search(part) for part in parts):
                if country not in seen:
                    matched.append(country)
                    seen.add(country)
                break

    return tuple(matched)


def detect_countries(location: str) -> list[str]:
    """Return all canonical country names recognized in *location*.

    "Remote" is returned as its own category. A job with multiple locations
    (e.g. "New York, NY, London, Toronto") will count for every matched country.
    """
    return list(_detect_countries_tuple(location))


def detect_country(location: str) -> str | None:
    """Return the first canonical country name for *location*, or None."""
    countries = _detect_countries_tuple(location)
    return countries[0] if countries else None


def nearby_suggestions(country: str) -> list[str]:
    """Return nearby / related countries for coverage suggestions."""
    return NEARBY_COUNTRIES.get(country, [])
