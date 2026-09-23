"""Pair up the same proposition across venues.

Sports: league + identical team pair + game date within a day. Doubleheaders
(two candidates) are skipped rather than guessed.

Elections: titles must share the same *discriminating* keys (state, office,
year, district, primary/runoff, country); remaining words give a similarity
score. Outcomes are then paired by party or candidate name. This is a
heuristic; `force_pairs` / `block_pairs` in the config are the escape hatch.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache

from polyarb.models import Event, Quote
from polyarb.teams import norm

PARTIES = {
    "democrat": "DEM", "democrats": "DEM", "democratic": "DEM", "dem": "DEM", "dems": "DEM",
    "republican": "GOP", "republicans": "GOP", "gop": "GOP", "rep": "GOP",
    "independent": "IND", "libertarian": "LIB", "green": "GRN",
    "labour": "LAB", "conservative": "CON", "conservatives": "CON", "reform": "REF",
}
_FILLER = {"party", "the", "candidate", "nominee", "a", "an"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
_STATE_ABBRS = set(STATES.values())
# US is implied by most markets, so it is stripped rather than used as a key.
_IMPLIED = ("united states of america", "united states", "usa", "us")
COUNTRIES = {
    "uk", "united kingdom", "canada", "france", "germany", "brazil",
    "mexico", "japan", "india", "australia", "italy", "spain", "poland", "hungary", "israel",
    "argentina", "chile", "colombia", "peru", "south korea", "netherlands", "portugal", "romania",
    "ukraine", "turkey", "taiwan", "philippines", "ireland", "scotland", "sweden", "norway",
}
OFFICES = {
    "senate": "senate", "senator": "senate", "sen": "senate",
    "house": "house", "congressional": "house", "congress": "house",
    "governor": "governor", "gubernatorial": "governor", "gov": "governor",
    "president": "president", "presidential": "president",
    "mayor": "mayor", "mayoral": "mayor",
    "prime minister": "pm", "pm": "pm", "chancellor": "chancellor",
    "attorney general": "ag", "secretary of state": "sos", "lieutenant governor": "ltgov",
    "primary": "primary", "runoff": "runoff", "nominee": "nominee", "nomination": "nominee", "special": "special",
    "parliament": "parliament", "parliamentary": "parliament",
    "speaker": "speaker", "supreme court": "scotus",
}
_STOP = {
    "who", "will", "win", "wins", "winner", "the", "of", "in", "election", "elections", "race",
    "a", "an", "be", "next", "for", "to", "and", "which", "party", "seat", "control", "general",
}


def outcome_key(label: str) -> str:
    """'Democratic Party' -> 'party:DEM'; 'Sherrod Brown (D)' -> 'person:sherrod brown'."""
    text = re.sub(r"\([^)]*\)", " ", label or "")
    tokens = [t for t in norm(text).split() if t not in _FILLER]
    if tokens and all(t in PARTIES for t in tokens):
        return f"party:{PARTIES[tokens[0]]}"
    tokens = [t for t in tokens if t not in _SUFFIXES]
    return f"person:{' '.join(tokens)}"


def outcome_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not (a.startswith("person:") and b.startswith("person:")):
        return 0.0
    ta, tb = a[7:].split(), b[7:].split()
    if not ta or not tb:
        return 0.0
    if any(t.isdigit() for t in ta + tb):
        return 0.0  # thresholds/brackets ("At least 30%" vs "<30%"): exact only
    if ta[-1] == tb[-1]:  # same surname
        if len(ta) == 1 or len(tb) == 1 or ta[0][0] == tb[0][0]:
            return 0.95
        return 0.0  # same surname, different first name: different people
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio if ratio >= 0.9 else 0.0


@lru_cache(maxsize=None)
def title_features(title: str) -> tuple[frozenset[str], frozenset[str]]:
    """(discriminating keys, other significant words)."""
    keys: set[str] = set()
    for tok in re.findall(r"\b[A-Z]{2}\b", title or ""):  # "OH-Sen", "PA-07"
        if tok in _STATE_ABBRS:
            keys.add(f"state:{tok}")
    text = f" {norm(title)} "
    for phrase, abbr in sorted(STATES.items(), key=lambda kv: -len(kv[0])):
        if f" {phrase} " in text:
            keys.add(f"state:{abbr}")
            # "west virginia" also contains "virginia"; consume the phrase.
            text = text.replace(f" {phrase} ", " ")
    for phrase in _IMPLIED:
        text = text.replace(f" {phrase} ", " ")
    for phrase in sorted(COUNTRIES, key=len, reverse=True):
        if f" {phrase} " in text:
            keys.add(f"country:{'uk' if phrase == 'united kingdom' else phrase}")
            text = text.replace(f" {phrase} ", " ")
    for phrase, office in sorted(OFFICES.items(), key=lambda kv: -len(kv[0])):
        if f" {phrase} " in text:
            keys.add(f"office:{office}")
            text = text.replace(f" {phrase} ", " ")
    for num in re.findall(r"\b\d+\b", text):
        keys.add(f"year:{num}" if len(num) == 4 else f"num:{int(num)}")
    words = {w for w in text.split() if w not in _STOP and not w.isdigit()}
    for p in PARTIES:
        if p in words:
            keys.add(f"party:{PARTIES[p]}")
            words.discard(p)
    return frozenset(keys), frozenset(words)


@dataclass
class Pair:
    key: str
    kalshi: Quote
    poly: Quote
    score: float


@dataclass
class Match:
    kalshi: Event
    poly: Event
    score: float
    pairs: list[Pair] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- sports

def _team_similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if "~" in a and "~" in b:  # no alias table (college): fuzzy on names
        na, nb = a.split("~", 1)[1], b.split("~", 1)[1]
        if na and nb and (f" {na} " in f" {nb} " or f" {nb} " in f" {na} "):
            return 0.9
        return SequenceMatcher(None, na, nb).ratio()
    return 0.0


def _pair_teams(k: Event, p: Event) -> tuple[float, list[tuple[str, str]]]:
    (k1, k2), (p1, p2) = k.teams, p.teams
    s = (_team_similarity(k1, p1), _team_similarity(k2, p2))
    c = (_team_similarity(k1, p2), _team_similarity(k2, p1))
    # Compare totals, not minimums: "New Mexico"/"New Mexico State" is 0.9
    # either way round, but only the right pairing contains an exact match.
    if (sum(s), min(s)) >= (sum(c), min(c)):
        return min(s), [(k1, p1), (k2, p2)]
    return min(c), [(k1, p2), (k2, p1)]


def match_sports(kalshi: list[Event], poly: list[Event], min_score: float) -> list[Match]:
    matches = []
    for k in kalshi:
        scored = []
        for p in poly:
            if p.league != k.league or len(p.teams) != 2 or len(k.teams) != 2:
                continue
            score, mapping = _pair_teams(k, p)
            if score < min_score:
                continue
            if k.date and p.date:
                if abs((k.date.date() - p.date.date()).days) > 1:
                    continue
            else:
                score *= 0.9  # can't confirm it's the same game
            scored.append((score, p, mapping))
        if not scored:
            continue
        scored.sort(key=lambda s: s[0], reverse=True)
        if len(scored) > 1 and scored[1][0] >= scored[0][0] - 0.02:
            continue  # doubleheader / series ambiguity: don't guess
        score, p, mapping = scored[0]
        if score < min_score:
            continue
        m = Match(k, p, score)
        for kk, pk in mapping:
            m.pairs.append(Pair(kk, k.quotes[kk], p.quotes[pk], score))
        matches.append(m)
    return matches


# ------------------------------------------------------------- politics

def event_similarity(k_title: str, p_title: str) -> float:
    kk, kw = title_features(k_title)
    pk, pw = title_features(p_title)
    k_years = {x for x in kk if x.startswith("year:")}
    p_years = {x for x in pk if x.startswith("year:")}
    if k_years and p_years and k_years != p_years:
        return 0.0
    k_rest, p_rest = kk - k_years, pk - p_years
    if not k_rest or k_rest != p_rest:
        return 0.0
    union = kw | pw
    jaccard = len(kw & pw) / len(union) if union else 1.0
    return 0.7 + 0.3 * jaccard


def _bucket(title: str) -> frozenset[str]:
    keys, _ = title_features(title)
    return frozenset(x for x in keys if not x.startswith("year:"))


def _pair_outcomes(k: Event, p: Event) -> list[Pair]:
    if "__binary__" in k.quotes or "__binary__" in p.quotes:
        if len(k.quotes) == 1 and len(p.quotes) == 1:
            return [Pair("__binary__", next(iter(k.quotes.values())), next(iter(p.quotes.values())), 1.0)]
        return []
    pairs, used = [], set()
    for kk, kq in k.quotes.items():
        best = max(((outcome_similarity(kk, pk), pk) for pk in p.quotes if pk not in used), default=(0.0, None))
        if best[1] is not None and best[0] > 0:
            used.add(best[1])
            pairs.append(Pair(kk, kq, p.quotes[best[1]], best[0]))
    return pairs


def match_politics(kalshi: list[Event], poly: list[Event], min_score: float,
                   force: list[list[str]] | None = None, block: list[list[str]] | None = None) -> list[Match]:
    forced = {(a, b) for a, b in force or []}
    blocked = {(a, b) for a, b in block or []}
    # event_similarity() requires identical non-year keys, so bucket on them
    # instead of comparing every Kalshi event against every Polymarket event.
    index: dict[frozenset[str], list[Event]] = defaultdict(list)
    for p in poly:
        index[_bucket(p.title)].append(p)
    by_id = {p.event_id: p for p in poly}
    candidates = []
    for k in kalshi:
        pool = list(index.get(_bucket(k.title), []))
        pool += [by_id[b] for a, b in forced if a == k.event_id and b in by_id]
        for p in pool:
            if (k.event_id, p.event_id) in blocked:
                continue
            score = 1.0 if (k.event_id, p.event_id) in forced else event_similarity(k.title, p.title)
            if score < min_score:
                continue
            pairs = _pair_outcomes(k, p)
            if not pairs:
                continue
            candidates.append((score, len(pairs), k, p, pairs))
    # Greedy one-to-one assignment, best first.
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    used_k, used_p, matches = set(), set(), []
    for score, _, k, p, pairs in candidates:
        if k.event_id in used_k or p.event_id in used_p:
            continue
        used_k.add(k.event_id)
        used_p.add(p.event_id)
        for pair in pairs:
            pair.score = min(pair.score, score)
        m = Match(k, p, score, pairs)
        if len(pairs) < len(k.quotes):
            m.notes.append(f"{len(k.quotes) - len(pairs)} Kalshi outcomes had no Polymarket counterpart")
        matches.append(m)
    return matches
