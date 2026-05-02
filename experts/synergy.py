"""
Synergy expert (stub).

Per CLAUDE.md, the synergy expert is "used in all other experts". Given the current
deck and game state, it scores cards/relics for synergy with the current build —
e.g., the reward expert calls this to score the three card-reward options.

Not yet implemented; only signatures live here so other experts have a stable hook.
"""


def score_card(card, deck):
    """Return a synergy score (higher = better) for a single card against a deck."""
    raise NotImplementedError


def score_deck(deck):
    """Return a profile / score summary for the whole deck."""
    raise NotImplementedError
