import json
import re
import logging
from collections import Counter

import ollama

from experts.synergy import (
    score_card,
    score_deck,
    build_future_sight_strategy,
    calculate_deck_avg_score,
)
from experts.reward import (
    SYNERGY_ENGINE,
    value_config,
    RELIC_INFO_BY_ID,
    enrich_relics,
)
from db.db_loader import get_card_info
from config import MODEL_NAME

log = logging.getLogger("STS_AI")
shop_log = logging.getLogger("CARD_PICKER")  # reward와 동일 파일에 기록

WAITING_FOR_SHOP = False
SHOP_DONE = False


def handle_shop_room(state, avail):
    """상점 방 진입: 주인에게 말 걸어 shop screen으로 전환."""
    global WAITING_FOR_SHOP, SHOP_DONE
    if not WAITING_FOR_SHOP:
        log.info("🛒 상점 주인에게 말을 겁니다.")
        print("choose shop", flush=True)
        WAITING_FOR_SHOP = True
        return
    if SHOP_DONE:
        WAITING_FOR_SHOP = False
        SHOP_DONE = False
        print("proceed", flush=True)


def _build_deck_context(state):
    """현재 덱/유물 상태로부터 점수 계산에 필요한 컨텍스트를 만든다."""
    current_deck_raw = state.get("deck", [])
    enriched_relics = enrich_relics(state.get("relics", []))
    act = state.get("act", 1)
    boss_name = state.get("boss", "")

    enriched_deck = [get_card_info(c) for c in current_deck_raw]
    deck_report = score_deck(enriched_deck, enriched_relics, [], {}, SYNERGY_ENGINE)

    base_strategy = build_future_sight_strategy(value_config, act, boss_name, 0.0)
    deck_score = calculate_deck_avg_score(current_deck_raw, deck_report, base_strategy, SYNERGY_ENGINE)
    final_strategy = build_future_sight_strategy(value_config, act, boss_name, deck_score)

    return {
        'current_deck_raw': current_deck_raw,
        'deck_report': deck_report,
        'final_strategy': final_strategy,
        'relic_ids': [r['id'] for r in enriched_relics],
        'deck_score': deck_score,
        'act': act,
        'boss_name': boss_name,
    }


def _affordable(price, gold):
    return "[OK]" if price <= gold else "[TOO_EXPENSIVE]"


def _format_shop_cards(shop_cards, ctx, gold):
    if not shop_cards:
        return "[Shop Cards]\n  (none)"
    lines = ["[Shop Cards]"]
    for i, card_dict in enumerate(shop_cards):
        info = get_card_info(card_dict)
        price = card_dict.get('price', 999)
        score = score_card(info, ctx['deck_report'], ctx['final_strategy'], ctx['relic_ids'], SYNERGY_ENGINE)
        provides = info.get('synergy', {}).get('provides', {})
        requires = info.get('synergy', {}).get('requires', {})
        lines.append(f"- Index [{i}]: {info['name']} (Cost: {info.get('cost')}, Price: {price}g) {_affordable(price, gold)}")
        lines.append(f"  Engine Score: {score} | PROVIDES: {provides} | REQUIRES: {requires}")
    return '\n'.join(lines)


def _format_shop_relics(shop_relics, gold):
    if not shop_relics:
        return "[Shop Relics]\n  (none)"
    lines = ["[Shop Relics]"]
    for i, relic_dict in enumerate(shop_relics):
        relic_id = (relic_dict.get('id') or relic_dict.get('name', '')).replace(' ', '_')
        price = relic_dict.get('price', 999)
        info = RELIC_INFO_BY_ID.get(relic_id, {})
        lines.append(f"- Index [{i}]: {relic_dict.get('name')} (Tier: {info.get('tier', '?')}, Price: {price}g) {_affordable(price, gold)}")
        if info.get('synergy'):
            lines.append(f"  Provides: {info['synergy'].get('provides', {})}")
        if info.get('agent_hints'):
            lines.append(f"  Hint: {info['agent_hints']}")
    return '\n'.join(lines)


def _format_shop_potions(shop_potions, gold):
    if not shop_potions:
        return "[Shop Potions]\n  (none)"
    lines = ["[Shop Potions]"]
    for i, p in enumerate(shop_potions):
        price = p.get('price', 999)
        lines.append(f"- Index [{i}]: {p.get('name')} (Price: {price}g) {_affordable(price, gold)}")
    return '\n'.join(lines)


def _format_purge(purge_available, purge_cost, gold, current_deck_raw):
    if not purge_available:
        return ""
    lines = [f"[Card Removal] Cost: {purge_cost}g {_affordable(purge_cost, gold)}"]
    name_counts = Counter(c.get('name') for c in current_deck_raw if isinstance(c, dict))
    candidates = []
    for name, cnt in name_counts.items():
        if name in ('Strike', 'Defend'):
            candidates.append(f"{name} x{cnt}")
            continue
        info = get_card_info({'name': name})
        if info.get('type') in ('Curse', 'Status'):
            candidates.append(f"{name} x{cnt}")
    if candidates:
        lines.append(f"  Removal candidates in deck: {', '.join(candidates[:8])}")
    return '\n'.join(lines)


def _get_boss_prompt(act, boss_name):
    return (
        value_config.get("act_strategies", {})
        .get(f"Act_{act}", {})
        .get("act_demand_modifier", {})
        .get("bosses", {})
        .get(boss_name, {})
        .get("llm_prompt", "")
    )


def _execute_action(action, target, shop_cards, shop_relics, shop_potions, purge_available, purge_cost, gold):
    """LLM이 결정한 action을 실행. 성공 시 True, 실패/leave면 False."""
    BUY_TARGETS = {
        'buy_card': shop_cards,
        'buy_relic': shop_relics,
        'buy_potion': shop_potions,
    }
    if action in BUY_TARGETS:
        items = BUY_TARGETS[action]
        if 0 <= target < len(items) and items[target].get('price', 999) <= gold:
            print(f"choose {items[target].get('name')}", flush=True)
            return True
        log.warning(f"🚨 {action} 조건 불만족 (index={target}, gold={gold}) → leave")
        return False

    if action == 'purge' and purge_available and purge_cost <= gold:
        print("choose purge", flush=True)
        return True

    if action != 'leave':
        log.warning(f"🚨 알 수 없는 action: {action} → leave")
    return False


def handle_shop_screen(state, avail):
    """상점 화면: 시너지 엔진으로 카드/유물/포션/제거를 평가하고 LLM이 결정."""
    global SHOP_DONE
    log.info("💰 상점 화면 진입 — 시너지 평가")

    screen_state = state.get("screen_state", {})
    gold = state.get("gold", 0)
    shop_cards = screen_state.get("cards", [])
    shop_relics = screen_state.get("relics", [])
    shop_potions = screen_state.get("potions", [])
    purge_available = screen_state.get("purge_available", False)
    purge_cost = screen_state.get("purge_cost", 75)

    if not (shop_cards or shop_relics or shop_potions or purge_available):
        log.info("🚪 상점이 비어있음. leave")
        SHOP_DONE = True
        print("leave", flush=True)
        return

    ctx = _build_deck_context(state)
    stats = ctx['deck_report']['stats']
    density = ctx['deck_report']['density_vector']
    meaningful = {k: round(v, 2) for k, v in density.items() if v > 0}
    boss_prompt = _get_boss_prompt(ctx['act'], ctx['boss_name'])
    boss_section = f"\n[Boss Strategy]\n{boss_prompt}\n" if boss_prompt else ""

    prompt = f"""
[Current State]
Act: {ctx['act']}, HP: {state.get('current_hp', 0)}/{state.get('max_hp', 0)}, Gold: {gold}g

[Deck Stats]
Size: {ctx['deck_report']['deck_size']} | Avg Cost: {stats.get('avg_cost', 0)} | Avg Dmg: {stats.get('avg_damage', 0)} | Avg Blk: {stats.get('avg_block', 0)}
Deck Power Score: {ctx['deck_score']:.2f}

[Current Synergies]
{meaningful}
{boss_section}
{_format_shop_cards(shop_cards, ctx, gold)}

{_format_shop_relics(shop_relics, gold)}

{_format_shop_potions(shop_potions, gold)}

{_format_purge(purge_available, purge_cost, gold, ctx['current_deck_raw'])}

[Task]
You are a top-tier Slay the Spire AI player at the shop.
Decide ONE shop action. You are NOT required to spend all gold — saving for future shops/events is valid.

[Decision Guidelines]
- BUY a CARD only if its Engine Score is clearly high (>15) AND affordable. Low/negative-score cards are NOT worth buying.
- BUY a RELIC if its provides/hint fits your deck direction. Boss/Rare relics are often worth it.
- BUY a POTION sparingly — only if it solves an upcoming threat.
- PURGE (card removal) is very strong in Act 1-2 to thin Strike/Defend or remove curses.
- LEAVE if nothing is clearly worth buying. Conserving gold for the next shop is a valid play.

Output EXACTLY in this JSON format:
{{
    "reasoning": "1-2 sentences",
    "action": "buy_card" | "buy_relic" | "buy_potion" | "purge" | "leave",
    "target_index": <integer, or 0 if not applicable>
}}
"""

    log.info(f"상점 LLM 호출 (gold={gold}, cards={len(shop_cards)}, relics={len(shop_relics)}, potions={len(shop_potions)}, purge={purge_available})")

    try:
        response = ollama.chat(
            model=MODEL_NAME,
            messages=[
                {'role': 'system', 'content': 'You are a master Slay the Spire shop strategist. Conservative buying, prioritize value.'},
                {'role': 'user', 'content': prompt}
            ],
            options={'temperature': 0.1, 'num_predict': 300}
        )
        content = response['message']['content'].strip()
        shop_log.info(f"shop prompt:\n{prompt}\n")
        shop_log.info(f"shop LLM response:\n{content}\n")

        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if not json_match:
            log.warning("🚨 shop LLM JSON 파싱 실패 → leave")
            SHOP_DONE = True
            print("leave", flush=True)
            return

        parsed = json.loads(json_match.group(0))
        # LLM이 null/누락으로 응답할 수 있으므로 None을 안전한 기본값으로 흡수
        action = str(parsed.get('action') or 'leave').strip().lower()
        target = int(parsed.get('target_index') or 0)
        log.info(f"🛒 shop 결정: {action} target={target} | {parsed.get('reasoning', '')[:120]}")

        if not _execute_action(action, target, shop_cards, shop_relics, shop_potions, purge_available, purge_cost, gold):
            SHOP_DONE = True
            print("leave", flush=True)

    except Exception as e:
        log.error(f"🚨 shop LLM 호출 에러: {e} → leave")
        SHOP_DONE = True
        print("leave", flush=True)
