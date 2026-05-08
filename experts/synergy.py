"""
Synergy expert.

Per CLAUDE.md, the synergy expert is "used in all other experts". Given the current
deck and game state, it scores cards/relics for synergy with the current build —
e.g., the reward expert calls this to score the three card-reward options.

`score_card` is still a stub. `score_deck` returns a minimal profile that the
map expert consumes for routing decisions (deck-bloat / needs-card / needs-upgrade
flags). Full synergy-aware scoring is future work.
"""

from db.db_loader import get_card_info


CURSE_TYPES = {"Curse"}
STATUS_TYPES = {"Status"}


def score_card(card, deck):
    """Return a synergy score (higher = better) for a single card against a deck."""
    raise NotImplementedError


def score_deck(deck):
    """Return a profile summary for the whole deck.

    Returns a dict with:
      - size: total card count
      - curses: number of Curse-type cards
      - statuses: number of Status-type cards
      - unupgraded_value: count of attack/skill cards with upgrades==0 (rest-site fuel)
      - needs_card: True if deck is too thin to absorb more bloat
      - needs_upgrade: True if many high-value cards are still un-upgraded
    """
    size = len(deck)
    curses = 0
    statuses = 0
    unupgraded_value = 0

    for entry in deck:
        info = get_card_info(entry)
        if not info:
            continue

        ctype = info.get("type", "")
        if ctype in CURSE_TYPES:
            curses += 1
        elif ctype in STATUS_TYPES:
            statuses += 1
        else:
            upgrades = entry.get("upgrades", 0) if isinstance(entry, dict) else 0
            base_value = info.get("base_value", 0) or 0
            if upgrades == 0 and base_value >= 8:
                unupgraded_value += 1

    playable = size - curses - statuses
    needs_card = playable < 15
    needs_upgrade = unupgraded_value >= 3

    return {
        "size": size,
        "curses": curses,
        "statuses": statuses,
        "unupgraded_value": unupgraded_value,
        "needs_card": needs_card,
        "needs_upgrade": needs_upgrade,
    }
