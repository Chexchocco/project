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


# ── 후보 필터 임계값 ─────────────────────────────────────────────
CARD_SCORE_THRESHOLD = 20.0                                    # Engine Score 이 미만은 후보 제외
RESERVE_GOLD_MIN = 50                                          # 구매 후 최소 잔액
WORTHWHILE_RELIC_TIERS = {'Rare', 'Boss', 'Shop', 'Uncommon'}  # 살 가치 있는 tier


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


# ── 후보 압축 (Python 사전 필터링) ──────────────────────────────
# 가격/점수/잔액 조건을 코드에서 모두 검증. LLM에는 통과한 후보만 전달.

def _build_card_candidates(shop_cards, ctx, gold):
    """살 만한 카드만 추려냄: 가격 OK + Score >= 20 + 잔액 >= 50g."""
    candidates = []
    for i, card_dict in enumerate(shop_cards):
        price = card_dict.get('price', 999)
        if price > gold or gold - price < RESERVE_GOLD_MIN:
            continue
        info = get_card_info(card_dict)
        score = score_card(info, ctx['deck_report'], ctx['final_strategy'],
                           ctx['relic_ids'], SYNERGY_ENGINE)
        if score < CARD_SCORE_THRESHOLD:
            continue
        candidates.append({'index': i, 'info': info, 'price': price, 'score': score})
    candidates.sort(key=lambda c: -c['score'])
    return candidates[:3]


def _build_relic_candidates(shop_relics, gold):
    """살 만한 유물만 추려냄: 가격 OK + tier가 Rare/Boss/Shop/Uncommon."""
    candidates = []
    for i, relic_dict in enumerate(shop_relics):
        price = relic_dict.get('price', 999)
        if price > gold or gold - price < RESERVE_GOLD_MIN:
            continue
        relic_id = (relic_dict.get('id') or relic_dict.get('name', '')).replace(' ', '_')
        info = RELIC_INFO_BY_ID.get(relic_id, {})
        if info.get('tier') not in WORTHWHILE_RELIC_TIERS:
            continue
        candidates.append({
            'index': i, 'name': relic_dict.get('name'),
            'price': price, 'tier': info['tier'], 'info': info,
        })
    return candidates[:3]


def _build_purge_option(purge_available, purge_cost, gold, current_deck_raw):
    """카드 제거 가치 평가: 가격 OK + 잔액 OK + 덱에 제거 후보(Strike/Defend 4장+ or Curse/Status)."""
    if not purge_available or purge_cost > gold or gold - purge_cost < RESERVE_GOLD_MIN:
        return None
    name_counts = Counter(c['name'] for c in current_deck_raw)
    weak_total = name_counts.get('Strike', 0) + name_counts.get('Defend', 0)
    curse_status = [
        n for n in name_counts
        if get_card_info({'name': n}).get('type') in ('Curse', 'Status')
    ]
    if weak_total < 4 and not curse_status:
        return None
    candidates_str = ', '.join(
        f"{n} x{name_counts[n]}"
        for n in (['Strike', 'Defend'] + curse_status)
        if n in name_counts
    )
    return {'cost': purge_cost, 'candidates_str': candidates_str}


def _format_candidates(card_cands, relic_cands, purge_opt, gold):
    """필터링된 후보만 LLM에 표시."""
    lines = []
    if card_cands:
        lines.append("[Card Candidates] (already filtered: affordable + Score>=20 + leaves 50g+)")
        for c in card_cands:
            info = c['info']
            provides = info.get('synergy', {}).get('provides', {})
            lines.append(
                f"- Index [{c['index']}]: {info['name']} "
                f"(Cost: {info.get('cost')}, Price: {c['price']}g → leaves {gold - c['price']}g) "
                f"Engine Score: {c['score']:.1f}"
            )
            if provides:
                lines.append(f"  PROVIDES: {provides}")
    if relic_cands:
        lines.append("\n[Relic Candidates] (already filtered: affordable + worthwhile tier + leaves 50g+)")
        for c in relic_cands:
            lines.append(
                f"- Index [{c['index']}]: {c['name']} "
                f"(Tier: {c['tier']}, Price: {c['price']}g → leaves {gold - c['price']}g)"
            )
            if c['info'].get('agent_hints'):
                lines.append(f"  Hint: {c['info']['agent_hints']}")
    if purge_opt:
        lines.append(
            f"\n[Purge Available] Cost: {purge_opt['cost']}g → leaves {gold - purge_opt['cost']}g"
        )
        lines.append(f"  Removal candidates: {purge_opt['candidates_str']}")
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


def _execute_action(action, target, shop_cards, shop_relics):
    """LLM 결정 실행. 후보 단계에서 가격/잔액 이미 검증됨 → 여기선 인덱스 범위만 확인."""
    BUY_TARGETS = {'buy_card': shop_cards, 'buy_relic': shop_relics}
    if action in BUY_TARGETS:
        items = BUY_TARGETS[action]
        if 0 <= target < len(items):
            print(f"choose {items[target].get('name')}", flush=True)
            return True
        log.warning(f"🚨 {action} index {target} 범위 밖 → leave")
        return False
    if action == 'purge':
        print("choose purge", flush=True)
        return True
    if action != 'leave':
        log.warning(f"🚨 알 수 없는 action: {action} → leave")
    return False


def handle_shop_screen(state, avail):
    """상점 화면: 코드가 후보를 필터링하고 LLM이 최종 선택만 한다."""
    global SHOP_DONE
    log.info("💰 상점 화면 진입 — 후보 필터링")

    screen_state = state.get("screen_state", {})
    gold = state.get("gold", 0)
    shop_cards = screen_state.get("cards", [])
    shop_relics = screen_state.get("relics", [])
    purge_available = screen_state.get("purge_available", False)
    purge_cost = screen_state.get("purge_cost", 75)

    ctx = _build_deck_context(state)
    card_cands = _build_card_candidates(shop_cards, ctx, gold)
    relic_cands = _build_relic_candidates(shop_relics, gold)
    purge_opt = _build_purge_option(purge_available, purge_cost, gold, ctx['current_deck_raw'])

    # 후보가 하나도 없으면 LLM 호출 없이 자동 leave
    if not (card_cands or relic_cands or purge_opt):
        log.info(f"🚪 살 만한 후보 없음 (gold={gold}g 보존). leave")
        SHOP_DONE = True
        print("leave", flush=True)
        return

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
{_format_candidates(card_cands, relic_cands, purge_opt, gold)}

[Task]
The candidates above have ALREADY been filtered by code: only affordable items with high score (>=20)
or worthwhile tier are shown, AND each leaves at least 50g reserve.

Your ONLY job: pick the ONE candidate that best fits this deck's long-term direction,
OR choose 'leave' if NONE of them are a clear upgrade for THIS specific deck.

[Decision Rules]
- Default to 'leave' unless an item CLEARLY synergizes with current deck direction.
- A high score alone is NOT enough — it must fit the deck's plan
  (e.g. Vulnerable card for a Bash deck, Block card for a defense deck, scaling Power for a long-game deck).
- If multiple candidates fit, pick the highest score one.
- If the deck is already strong (Power Score > 25), be MORE selective — only pick game-changing additions.
- Purge: only worth it if the deck still has many basic Strike/Defend or a Curse/Status card.

Output EXACTLY in this JSON format:
{{
    "reasoning": "1-2 sentences",
    "action": "buy_card" | "buy_relic" | "purge" | "leave",
    "target_index": <integer, or 0 if not applicable>
}}
"""

    log.info(f"상점 LLM 호출 (cards={len(card_cands)}, relics={len(relic_cands)}, purge={bool(purge_opt)})")

    try:
        response = ollama.chat(
            model=MODEL_NAME,
            messages=[
                {'role': 'system', 'content': 'You are an extremely CONSERVATIVE Slay the Spire shop strategist. Your default is to LEAVE without buying. You only spend gold on items that CLEARLY fit the deck. "I can afford it" is NOT a reason to buy.'},
                {'role': 'user', 'content': prompt}
            ],
            options={'temperature': 0.1, 'num_predict': 200}
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
        action = str(parsed.get('action') or 'leave').strip().lower()
        target = int(parsed.get('target_index') or 0)
        log.info(f"🛒 shop 결정: {action} target={target} | {parsed.get('reasoning', '')[:120]}")

        if not _execute_action(action, target, shop_cards, shop_relics):
            SHOP_DONE = True
            print("leave", flush=True)

    except Exception as e:
        log.error(f"🚨 shop LLM 호출 에러: {e} → leave")
        SHOP_DONE = True
        print("leave", flush=True)
