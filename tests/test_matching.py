from polyarb.kalshi import parse_ticker_date
from polyarb.matching import event_similarity, outcome_key, outcome_similarity
from polyarb.teams import resolve_team


def test_team_resolution_across_venue_styles():
    assert resolve_team("nfl", "Kansas City") == resolve_team("nfl", "Chiefs") == "nfl:KC"
    assert resolve_team("nfl", "Los Angeles C") == "nfl:LAC"
    assert resolve_team("nfl", "Los Angeles") is None  # ambiguous
    assert resolve_team("nfl", "No") is None  # not the Saints
    assert resolve_team("mlb", "Chicago WS") == "mlb:CWS"
    assert resolve_team("mlb", "St. Louis Cardinals") == "mlb:STL"
    assert resolve_team("nhl", "Montréal") == "nhl:MTL"


def test_ticker_date():
    d = parse_ticker_date("KXNFLGAME-26SEP24KCBUF")
    assert (d.year, d.month, d.day) == (2026, 9, 24)
    assert parse_ticker_date("NOPE") is None


def test_election_titles():
    assert event_similarity("Ohio Senate race winner 2026?", "Ohio Senate Election Winner") >= 0.85
    assert event_similarity("West Virginia Senate 2026", "Virginia Senate 2026") == 0
    assert event_similarity("Ohio Senate 2026", "Ohio Senate 2028") == 0
    assert event_similarity("Ohio Senate 2026", "Ohio Governor 2026") == 0
    assert event_similarity("Who will win the 2028 US Presidential Election?", "Presidential Election Winner 2028") >= 0.85
    # nominee markets are not general-election markets
    assert event_similarity("Democratic Presidential Nominee 2028", "Presidential Election Winner 2028") == 0


def test_outcomes():
    assert outcome_key("Democratic party") == outcome_key("Democrat") == "party:DEM"
    assert outcome_key("Republicans") == "party:GOP"
    assert outcome_key("Sherrod Brown (D)") == "person:sherrod brown"
    assert outcome_similarity("person:jd vance", "person:j d vance") > 0.9
    assert outcome_similarity("person:vance", "person:jd vance") > 0.9
    assert outcome_similarity("person:jon smith", "person:mary smith") == 0
    assert outcome_similarity("party:DEM", "party:GOP") == 0


def test_college_state_suffix_pairs_correctly():
    from polyarb.matching import match_sports
    from polyarb.models import Event
    from polyarb.teams import fallback_key

    k = Event("kalshi", "K", "", "sports", "ncaaf",
              teams=tuple(sorted([fallback_key("ncaaf", "New Mexico"), fallback_key("ncaaf", "New Mexico St.")])))
    p = Event("polymarket", "P", "", "sports", "ncaaf",
              teams=tuple(sorted([fallback_key("ncaaf", "New Mexico State"), fallback_key("ncaaf", "New Mexico")])))
    # use the keys themselves as stand-in quotes so the pairing is visible
    k.quotes = {t: t for t in k.teams}
    p.quotes = {t: t for t in p.teams}
    [m] = match_sports([k], [p], 0.85)
    assert [(pair.kalshi, pair.poly) for pair in m.pairs] == [
        ("ncaaf:~new mexico", "ncaaf:~new mexico"),
        ("ncaaf:~new mexico state", "ncaaf:~new mexico state"),
    ]
    assert m.score == 0.9  # no dates on either side -> 0.9 by design


def test_numeric_outcomes_need_exact_match():
    assert outcome_similarity(outcome_key("At least 30%"), outcome_key("<30%")) == 0
    assert outcome_similarity(outcome_key("At least 6"), outcome_key("6")) == 0
    assert outcome_similarity(outcome_key("Ramaswamy, 3+ pts"), outcome_key("Ramaswamy 3-6%")) == 0
