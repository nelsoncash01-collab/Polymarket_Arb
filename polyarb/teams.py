"""Team alias tables for resolving venue-specific team names to one key.

Kalshi tends to label by city ("Kansas City", "Los Angeles C"), Polymarket by
nickname ("Chiefs"). Each row: ABBR|City|Nickname|extra aliases (comma list).
Abbreviations only match exactly (never as substrings) so "NO" / "LA" can't
collide with ordinary words.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_RAW = {
    "nfl": """
ARI|Arizona|Cardinals|
ATL|Atlanta|Falcons|
BAL|Baltimore|Ravens|
BUF|Buffalo|Bills|
CAR|Carolina|Panthers|
CHI|Chicago|Bears|
CIN|Cincinnati|Bengals|
CLE|Cleveland|Browns|
DAL|Dallas|Cowboys|
DEN|Denver|Broncos|
DET|Detroit|Lions|
GB|Green Bay|Packers|GNB
HOU|Houston|Texans|
IND|Indianapolis|Colts|
JAX|Jacksonville|Jaguars|JAC
KC|Kansas City|Chiefs|KAN
LV|Las Vegas|Raiders|LVR
LAC|Los Angeles|Chargers|Los Angeles C,LA Chargers
LAR|Los Angeles|Rams|LA,Los Angeles R,LA Rams
MIA|Miami|Dolphins|
MIN|Minnesota|Vikings|
NE|New England|Patriots|NWE
NO|New Orleans|Saints|NOR
NYG|New York|Giants|New York G,NY Giants
NYJ|New York|Jets|New York J,NY Jets
PHI|Philadelphia|Eagles|
PIT|Pittsburgh|Steelers|
SF|San Francisco|49ers|SFO,Niners
SEA|Seattle|Seahawks|
TB|Tampa Bay|Buccaneers|TAM,Bucs
TEN|Tennessee|Titans|
WAS|Washington|Commanders|WSH
""",
    "nba": """
ATL|Atlanta|Hawks|
BOS|Boston|Celtics|
BKN|Brooklyn|Nets|BRK
CHA|Charlotte|Hornets|CHO
CHI|Chicago|Bulls|
CLE|Cleveland|Cavaliers|Cavs
DAL|Dallas|Mavericks|Mavs
DEN|Denver|Nuggets|
DET|Detroit|Pistons|
GSW|Golden State|Warriors|GS
HOU|Houston|Rockets|
IND|Indiana|Pacers|
LAC|Los Angeles|Clippers|Los Angeles C,LA Clippers
LAL|Los Angeles|Lakers|Los Angeles L,LA Lakers
MEM|Memphis|Grizzlies|
MIA|Miami|Heat|
MIL|Milwaukee|Bucks|
MIN|Minnesota|Timberwolves|Wolves
NOP|New Orleans|Pelicans|NO
NYK|New York|Knicks|NY
OKC|Oklahoma City|Thunder|
ORL|Orlando|Magic|
PHI|Philadelphia|76ers|Sixers
PHX|Phoenix|Suns|PHO
POR|Portland|Trail Blazers|Blazers
SAC|Sacramento|Kings|
SAS|San Antonio|Spurs|SA
TOR|Toronto|Raptors|
UTA|Utah|Jazz|UTAH
WAS|Washington|Wizards|WSH
""",
    "wnba": """
ATL|Atlanta|Dream|
CHI|Chicago|Sky|
CON|Connecticut|Sun|CONN
DAL|Dallas|Wings|
GSV|Golden State|Valkyries|GS
IND|Indiana|Fever|
LV|Las Vegas|Aces|LVA
LA|Los Angeles|Sparks|LAS
MIN|Minnesota|Lynx|
NY|New York|Liberty|NYL
PHX|Phoenix|Mercury|PHO
SEA|Seattle|Storm|
WAS|Washington|Mystics|WSH
TOR|Toronto|Tempo|
POR|Portland|Fire|
""",
    "mlb": """
ARI|Arizona|Diamondbacks|D-backs,Dbacks,AZ
ATL|Atlanta|Braves|
BAL|Baltimore|Orioles|
BOS|Boston|Red Sox|
CHC|Chicago|Cubs|Chicago C
CWS|Chicago|White Sox|CHW,Chicago WS,Chicago W
CIN|Cincinnati|Reds|
CLE|Cleveland|Guardians|
COL|Colorado|Rockies|
DET|Detroit|Tigers|
HOU|Houston|Astros|
KC|Kansas City|Royals|KCR
LAA|Los Angeles|Angels|Los Angeles A,LA Angels,Anaheim
LAD|Los Angeles|Dodgers|Los Angeles D,LA Dodgers
MIA|Miami|Marlins|
MIL|Milwaukee|Brewers|
MIN|Minnesota|Twins|
NYM|New York|Mets|New York M,NY Mets
NYY|New York|Yankees|New York Y,NY Yankees
ATH|Sacramento|Athletics|OAK,Oakland,A's,Oakland Athletics
PHI|Philadelphia|Phillies|
PIT|Pittsburgh|Pirates|
SD|San Diego|Padres|SDP
SF|San Francisco|Giants|SFG
SEA|Seattle|Mariners|
STL|St. Louis|Cardinals|St Louis
TB|Tampa Bay|Rays|TBR
TEX|Texas|Rangers|
TOR|Toronto|Blue Jays|
WSH|Washington|Nationals|WAS,WSN
""",
    "nhl": """
ANA|Anaheim|Ducks|
BOS|Boston|Bruins|
BUF|Buffalo|Sabres|
CGY|Calgary|Flames|
CAR|Carolina|Hurricanes|
CHI|Chicago|Blackhawks|
COL|Colorado|Avalanche|
CBJ|Columbus|Blue Jackets|
DAL|Dallas|Stars|
DET|Detroit|Red Wings|
EDM|Edmonton|Oilers|
FLA|Florida|Panthers|
LAK|Los Angeles|Kings|LA
MIN|Minnesota|Wild|
MTL|Montreal|Canadiens|
NSH|Nashville|Predators|
NJD|New Jersey|Devils|NJ
NYI|New York|Islanders|New York I,NY Islanders
NYR|New York|Rangers|New York R,NY Rangers
OTT|Ottawa|Senators|
PHI|Philadelphia|Flyers|
PIT|Pittsburgh|Penguins|
SJS|San Jose|Sharks|SJ
SEA|Seattle|Kraken|
STL|St. Louis|Blues|St Louis
TBL|Tampa Bay|Lightning|TB
TOR|Toronto|Maple Leafs|
UTA|Utah|Mammoth|Utah Hockey Club
VAN|Vancouver|Canucks|
VGK|Vegas|Golden Knights|VEG
WSH|Washington|Capitals|WAS
WPG|Winnipeg|Jets|
""",
}


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ").replace("'", "").replace(".", "")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _looks_like_abbr(alias: str) -> bool:
    return alias.isupper() and len(alias) <= 4 and " " not in alias


@lru_cache(maxsize=None)
def _tables(league: str) -> tuple[dict[str, str], dict[str, str]]:
    """Returns (abbr -> abbr, name alias -> abbr), with ambiguous names removed."""
    abbrs: dict[str, str] = {}
    names: dict[str, set[str]] = {}
    for line in _RAW.get(league, "").strip().splitlines():
        abbr, city, nick, extra = (line.split("|") + [""])[:4]
        abbrs[abbr.lower()] = abbr
        aliases = [city, nick, f"{city} {nick}"] + [a for a in extra.split(",") if a]
        for alias in aliases:
            if _looks_like_abbr(alias):
                abbrs[alias.lower()] = abbr
            else:
                names.setdefault(norm(alias), set()).add(abbr)
    unique = {alias: next(iter(teams)) for alias, teams in names.items() if len(teams) == 1}
    return abbrs, unique


def has_table(league: str) -> bool:
    return league in _RAW


def resolve_team(league: str, text: str) -> str | None:
    """Canonical key like 'nfl:KC', or None if unknown/ambiguous."""
    if not text or not has_table(league):
        return None
    abbrs, names = _tables(league)
    raw = text.strip()
    if raw.isupper() and raw.lower() in abbrs:
        return f"{league}:{abbrs[raw.lower()]}"
    n = norm(raw)
    if n in names:
        return f"{league}:{names[n]}"
    # Longest alias appearing as whole words, e.g. "Kansas City Chiefs to win".
    padded = f" {n} "
    hits = sorted((a for a in names if f" {a} " in padded), key=len, reverse=True)
    if not hits:
        return None
    found = {names[a] for a in hits if len(a) == len(hits[0])}
    return f"{league}:{found.pop()}" if len(found) == 1 else None


_COLLEGE_WORDS = {"st": "state", "univ": "university", "u": "university"}


def fallback_key(league: str, text: str) -> str:
    """Key for leagues without a table (college): normalized name.

    Kalshi writes "New Mexico St." where Polymarket writes "New Mexico State".
    """
    words = [_COLLEGE_WORDS.get(w, w) for w in norm(text).split()]
    words = [w for w in words if w not in ("the", "university")] or words
    return f"{league}:~{' '.join(words)}"
