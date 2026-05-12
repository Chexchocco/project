import re
import logging

import ollama

from config import MODEL_NAME

from experts import combat as combat_expert
from experts import reward as reward_expert
from experts import map as map_expert
from experts import rest as rest_expert
from experts import shop as shop_expert
from experts import event as event_expert


log = logging.getLogger("STS_AI")


class RouterError(Exception):
    pass


def _wait_sentinel(state, avail):
    print("wait 30", flush=True)


TOOLS = [
    {
        "name": "combat",
        "description": "Play a card / end turn during an active fight.",
        "when_to_use": "room_phase is COMBAT, screen_type is NONE, and combat_state is present in game_state.",
        "fn": combat_expert.battle_module,
    },
    {
        "name": "hand_select",
        "description": "Pick cards on a HAND_SELECT prompt (e.g. Shrug It Off, Headbutt).",
        "when_to_use": "screen_type is HAND_SELECT.",
        "fn": combat_expert.handle_hand_select,
    },
    {
        "name": "combat_reward",
        "description": "Collect post-combat rewards (gold, potion, relic, then card).",
        "when_to_use": "screen_type is COMBAT_REWARD.",
        "fn": reward_expert.handle_combat_reward,
    },
    {
        "name": "card_reward",
        "description": "Choose one card from a card-reward offer (or skip).",
        "when_to_use": "screen_type is CARD_REWARD.",
        "fn": reward_expert.handle_card_reward,
    },
    {
        "name": "grid_select",
        "description": "Pick cards on a GRID screen (upgrade / transform / purge).",
        "when_to_use": "screen_type is GRID.",
        "fn": reward_expert.handle_grid_selection,
    },
    {
        "name": "chest",
        "description": "Open a treasure chest.",
        "when_to_use": "screen_type is CHEST.",
        "fn": reward_expert.handle_chest,
    },
    {
        "name": "boss_reward",
        "description": "Pick a boss-relic reward.",
        "when_to_use": "screen_type is BOSS_REWARD.",
        "fn": reward_expert.handle_boss_reward,
    },
    {
        "name": "map",
        "description": "Choose the next room on the map.",
        "when_to_use": "screen_type is MAP.",
        "fn": lambda state, _avail: map_expert.handle_map(state),
    },
    {
        "name": "rest",
        "description": "Decide rest-site action (rest / smith / etc).",
        "when_to_use": "screen_type is REST.",
        "fn": rest_expert.handle_rest,
    },
    {
        "name": "event",
        "description": "Resolve a non-combat event screen.",
        "when_to_use": "screen_type is EVENT.",
        "fn": event_expert.handle_event,
    },
    {
        "name": "shop_room",
        "description": "Enter the shop from the room view.",
        "when_to_use": "screen_type is SHOP_ROOM.",
        "fn": shop_expert.handle_shop_room,
    },
    {
        "name": "shop_screen",
        "description": "Buy / leave inside the shop screen.",
        "when_to_use": "screen_type is SHOP_SCREEN.",
        "fn": shop_expert.handle_shop_screen,
    },
    {
        "name": "wait",
        "description": "Do nothing this tick; emit `wait 30`.",
        "when_to_use": "No other tool fits the current state.",
        "fn": _wait_sentinel,
    },
]

_TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


def _state_digest(state, avail):
    screen_type = state.get("screen_type", "")
    room_phase = state.get("room_phase", "")
    floor = state.get("floor", "?")
    act = state.get("act", "?")
    hp = state.get("current_hp", "?")
    max_hp = state.get("max_hp", "?")
    gold = state.get("gold", "?")
    in_combat = "combat_state" in state
    energy = state.get("combat_state", {}).get("player", {}).get("energy", "-")
    screen_state_keys = ", ".join(sorted(state.get("screen_state", {}).keys())) or "(none)"
    avail_str = ", ".join(avail) if avail else "(none)"

    return (
        f"screen_type: {screen_type}\n"
        f"room_phase: {room_phase}\n"
        f"floor: {floor} / act {act}\n"
        f"hp: {hp}/{max_hp}   gold: {gold}   energy: {energy}\n"
        f"in_combat: {str(in_combat).lower()}\n"
        f"available_commands: {avail_str}\n"
        f"screen_state_keys: {screen_state_keys}"
    )


def _build_prompt(state, avail):
    catalog_lines = [
        f"- {t['name']}: {t['description']} Use when: {t['when_to_use']}"
        for t in TOOLS
    ]
    catalog_block = "\n".join(catalog_lines)
    digest = _state_digest(state, avail)

    return f"""[Available Tools]
{catalog_block}

[Current Game State]
{digest}

[Task]
Pick exactly ONE tool name from the list above that should handle this tick.
Do not reason about gameplay strategy — only about which tool is the right
handler for the current screen.

Output EXACTLY in this format, with no extra lines:
Reasoning: <one short sentence>
Selected Tool: <tool_name>
"""


_SYSTEM_PROMPT = (
    "You are the router for an autonomous Slay the Spire agent playing Ironclad. "
    "Your only job: read the current game state and pick exactly one tool (expert) "
    "to handle this tick. Do not reason about gameplay — only routing."
)


def route(state, avail):
    prompt = _build_prompt(state, avail)

    response = ollama.chat(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        options={"temperature": 0.0, "num_predict": 64},
    )
    content = response["message"]["content"]
    log.info(f"🧭 router LLM output:\n{content.strip()}")

    match = re.search(r"Selected Tool:\s*([A-Za-z_]+)", content)
    if not match:
        raise RouterError("could not parse 'Selected Tool:' from LLM output")

    name = match.group(1).strip().lower()
    if name not in _TOOLS_BY_NAME:
        raise RouterError(f"unknown tool name '{name}'")

    return name


def dispatch(tool_name, state, avail):
    tool = _TOOLS_BY_NAME[tool_name]
    tool["fn"](state, avail)


def deterministic_fallback(state, avail):
    screen_type = state.get("screen_type", "")
    room_phase = state.get("room_phase", "")

    if room_phase == "COMBAT" and screen_type == "NONE":
        if "combat_state" in state:
            return "combat"
        return "wait"
    if screen_type == "COMBAT_REWARD":
        return "combat_reward"
    if screen_type == "CARD_REWARD":
        return "card_reward"
    if screen_type == "GRID":
        return "grid_select"
    if screen_type == "MAP":
        return "map"
    if screen_type == "REST":
        return "rest"
    if screen_type == "EVENT":
        return "event"
    if screen_type == "CHEST":
        return "chest"
    if screen_type == "HAND_SELECT":
        return "hand_select"
    if screen_type == "SHOP_ROOM":
        return "shop_room"
    if screen_type == "SHOP_SCREEN":
        return "shop_screen"
    if screen_type == "BOSS_REWARD":
        return "boss_reward"
    return "wait"
