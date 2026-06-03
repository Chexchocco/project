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
_shop_floor = None   # 현재 처리 중인 상점의 층 — 층이 바뀌면 새 상점 → 플래그 초기화


def _at_shop_screen(state):
    """현재 화면이 '상점 구매 화면'인지 내용 기반으로 판정.
    '?'방이 상점으로 판명되어 screen_type이 SHOP_SCREEN이 아니어도(예: EVENT) 잡아낸다."""
    if state.get("screen_type", "") == "SHOP_SCREEN":
        return True
    ss = state.get("screen_state", {}) or {}
    if "purge_available" in ss:           # 상점 전용 키
        return True
    for key in ("cards", "relics", "potions"):   # 가격표가 붙은 판매 품목 = 상점
        for it in (ss.get(key) or []):
            if isinstance(it, dict) and "price" in it:
                return True
    return False


def _free_potion_slots(state):
    """비어있는 포션 슬롯 수. 0이면 포션을 살 수 없다(구매 시 엔진 에러)."""
    return sum(1 for p in (state.get("potions") or [])
               if p.get("id", "Potion Slot") == "Potion Slot")


def handle_shop_room(state, avail):
    global WAITING_FOR_SHOP, SHOP_DONE, _shop_floor

    floor = state.get("floor")
    if floor != _shop_floor:          # 새 상점(다른 층) → 상태 초기화 (stale 제거)
        _shop_floor = floor
        WAITING_FOR_SHOP = False
        SHOP_DONE = False

    # 라우터 오인 대비: 이미 상점 구매 화면이면(내용 기반) 구매 핸들러로 위임
    if _at_shop_screen(state):
        handle_shop_screen(state, avail)
        return

    # 입구: 아직 상인과 대화 전 → 말 걸기 (WAITING 가드로 '단 한 번'만 실행)
    if not WAITING_FOR_SHOP:
        if "choose" in avail:
            log.info("🛒 상점 주인에게 말을 겁니다.")
            print("choose shop", flush=True)
            WAITING_FOR_SHOP = True
            return
        # choose가 없는 입구(예외) → 나갈 길 있으면 진행
        if "proceed" in avail:
            print("proceed", flush=True)
            return

    # 진입 후 SHOP_ROOM 재방문 = 쇼핑 종료(복귀) 또는 SHOP_SCREEN 전환 대기.
    #   나갈 수 있으면(쇼핑 끝) 나가고, 아니면 전환 대기 (재진입 안 함 → 무한루프 방지).
    if "proceed" in avail:
        WAITING_FOR_SHOP = False
        SHOP_DONE = False
        log.info("🛒 상점 종료 → 진행")
        print("proceed", flush=True)
        return
    if "leave" in avail:
        WAITING_FOR_SHOP = False
        SHOP_DONE = False
        print("leave", flush=True)
        return
    print("wait 30", flush=True)   # SHOP_SCREEN 전환 대기


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


def _evaluate_shop_items(state, ctx, shop_cards, shop_relics, shop_potions, purge_available, purge_cost, gold):
    """모든 상점 품목을 우선도(Score) 기반으로 통합 평가합니다."""
    candidates = []
    
    current_hp = state.get("current_hp", 1)
    max_hp = state.get("max_hp", 1)
    hp_ratio = current_hp / max_hp if max_hp > 0 else 1.0
    deck = ctx['current_deck_raw']
    
    # 1. 유물 (Relics)
    for i, r in enumerate(shop_relics):
        price = r.get('price', 999)
        if price > gold: continue
        
        name = r.get('name', '')
        relic_id = r.get('id', name).replace(' ', '_')
        
        # 0순위: 멤버십 카드
        if relic_id == "Membership_Card" or name == "Membership Card":
            candidates.append({'action': 'buy_relic', 'index': i, 'name': name, 'price': price, 'score': 10000, 'desc': 'Massive discount (Auto-buy)'})
            continue
            
        info = RELIC_INFO_BY_ID.get(relic_id, {})
        tier = info.get('tier', 'Common')
        
        # 티어별 기본 점수 부여
        tier_scores = {'Boss': 120, 'Shop': 100, 'Rare': 90, 'Uncommon': 70, 'Common': 40}
        base_score = tier_scores.get(tier, 30)
        
        # 시너지 보너스 (유물이 제공하는 태그가 전략에 필요한 경우)
        provides = info.get("synergy", {}).get("provides", {})
        syn_bonus = 0
        density = ctx['deck_report'].get('density_vector', {})
        for tag, val in provides.items():
            target = ctx['final_strategy'].get('synergy_weights', {}).get(tag, 0.0)
            curr = density.get(tag, 0.0) * ctx['deck_report']['deck_size']
            # 필요 태그가 부족한 상황이라면 가점
            if target > 0 and curr < (target * ctx['deck_report']['deck_size']):
                syn_bonus += 30
                
        score = base_score + syn_bonus
        candidates.append({'action': 'buy_relic', 'index': i, 'name': name, 'price': price, 'score': score, 'desc': f'Tier: {tier}, SynBonus: {syn_bonus}'})

    # 2. 제거 (Purge)
    if purge_available and purge_cost <= gold:
        has_curse = any(get_card_info({'name': c['name']}).get('type') in ('Curse', 'Status') for c in deck)
        if has_curse:
            # 1순위: 저주 제거
            candidates.append({'action': 'purge', 'index': 0, 'name': 'Purge Curse', 'price': purge_cost, 'score': 5000, 'desc': 'Remove a Curse/Status (Auto-buy)'})
        else:
            name_counts = Counter(c['name'] for c in deck)
            weak_total = name_counts.get('Strike', 0) + name_counts.get('Defend', 0)
            if weak_total >= 2:
                # 타격/수비 제거 점수: 덱 압축의 가치 (기본 60점 + 장당 5점)
                score = 60 + (weak_total * 5)
                candidates.append({'action': 'purge', 'index': 0, 'name': 'Purge Strike/Defend', 'price': purge_cost, 'score': score, 'desc': f'Thin deck (has {weak_total} basics)'})

    # 3. 카드 (Cards)
    for i, c in enumerate(shop_cards):
        price = c.get('price', 999)
        if price > gold: continue
        info = get_card_info(c)
        if not info: continue
        
        card_score = score_card(info, ctx['deck_report'], ctx['final_strategy'], ctx['relic_ids'], SYNERGY_ENGINE)
        # 카드 점수 스케일링: 유물 점수대와 비교할 수 있도록 배율 조정 (보통 카드는 10~30점대)
        # 30점짜리 핵심 시너지 카드 = 75점 (Uncommon 유물급 가치)
        adjusted_score = card_score * 2.5
        
        if card_score >= 12.0: # 살만한 카드만 후보에 올림
            candidates.append({'action': 'buy_card', 'index': i, 'name': info['name'], 'price': price, 'score': adjusted_score, 'desc': f'Card Score: {card_score:.1f}'})

    # 4. 포션 (Potions) — 빈 포션 슬롯이 있을 때만 (슬롯이 꽉 차면 구매 불가 → 엔진 에러)
    free_slots = _free_potion_slots(state)
    survival_keywords = ['blood', 'block', 'regen', 'fairy', 'ghost', 'fruit', 'heal', 'armor']
    if free_slots > 0:
        for i, p in enumerate(shop_potions):
            price = p.get('price', 999)
            if price > gold: continue
            name = p.get('id', p.get('name', ''))

            score = 15
            desc = "Consumable Potion"

            # 피가 없을 때 생존 포션은 구원줄 (매우 높은 점수)
            if hp_ratio < 0.4 and any(k in name.lower() for k in survival_keywords):
                score = 150
                desc = "CRITICAL SURVIVAL POTION (Low HP)"

            # 보스/엘리트 직전이거나 점수가 특별히 높을 때 추천
            if score >= 50 or gold >= 250:
                candidates.append({'action': 'buy_potion', 'index': i, 'name': name, 'price': price, 'score': score, 'desc': desc})
            
    # 통합 점수 내림차순 정렬
    candidates.sort(key=lambda x: -x['score'])
    return candidates


def _get_boss_prompt(act, boss_name):
    return (
        value_config.get("act_strategies", {})
        .get(f"Act_{act}", {})
        .get("act_demand_modifier", {})
        .get("bosses", {})
        .get(boss_name, {})
        .get("llm_prompt", "")
    )


def _execute_action(action, target, shop_cards, shop_relics, shop_potions, floor):
    """LLM 결정 실행. 후보 단계에서 이미 검증됨 → 여기선 인덱스 범위만 확인."""
    BUY_TARGETS = {'buy_card': shop_cards, 'buy_relic': shop_relics, 'buy_potion': shop_potions}
    
    try:
        from run_logger import log_major_choice
        if action in BUY_TARGETS and 0 <= target < len(BUY_TARGETS[action]):
            item = BUY_TARGETS[action][target]
            item_name = item.get('name') or item.get('id', 'Unknown')
            log_major_choice(floor, f"Shop_{action}", [item_name], item_name, "상점에서 아이템 구매")
        elif action == 'purge':
            log_major_choice(floor, "Shop_purge", ["Purge"], "Purge", "상점에서 카드 제거")
    except Exception as log_err:
        log.error(f"상점 로깅 에러: {log_err}")

    if action in BUY_TARGETS:
        items = BUY_TARGETS[action]
        if 0 <= target < len(items):
            item = items[target]
            name_or_id = item.get('name') or item.get('id')
            if name_or_id:
                print(f"choose {name_or_id}", flush=True)
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
    """상점 화면: 코드가 후보를 통합 평가하여 필터링하고 LLM이 최종 선택만 한다."""
    global SHOP_DONE
    log.info("💰 상점 화면 진입 — 통합 우선순위 평가")

    screen_state = state.get("screen_state", {})
    gold = state.get("gold", 0)
    shop_cards = screen_state.get("cards", [])
    shop_relics = screen_state.get("relics", [])
    shop_potions = screen_state.get("potions", [])
    purge_available = screen_state.get("purge_available", False)
    purge_cost = screen_state.get("purge_cost", 75)

    current_hp = state.get("current_hp", 1)
    max_hp = state.get("max_hp", 1)

    ctx = _build_deck_context(state)
    
    # 1. 전 품목 조사 및 우선도(Score) 측정
    candidates = _evaluate_shop_items(
        state, ctx, shop_cards, shop_relics, shop_potions, purge_available, purge_cost, gold
    )

    # 후보가 하나도 없으면 LLM 호출 없이 자동 leave
    if not candidates:
        log.info(f"🚪 살 수 있는 품목이 없거나 골드가 부족함 (gold={gold}g). leave")
        SHOP_DONE = True
        print("leave", flush=True)
        return

    # 2. 압도적인 우선도(멤버십 카드, 저주 제거 등)는 LLM 없이 즉시 구매
    top_cand = candidates[0]
    if top_cand['score'] >= 5000:
        log.info(f"🛒 필수 구매 항목 발견! (score={top_cand['score']}): {top_cand['name']}")
        if _execute_action(top_cand['action'], top_cand['index'], shop_cards, shop_relics, shop_potions, state.get("floor", "?")):
            return

    # 3. LLM에게 제공할 상위 최대 6개 후보 포맷팅
    top_candidates = candidates[:6]
    lines = []
    for c in top_candidates:
        lines.append(
            f"- Action: '{c['action']}', Target Index: {c['index']} | "
            f"Name: {c['name']} (Price: {c['price']}g) | "
            f"Priority Score: {c['score']:.1f} ({c['desc']})"
        )
    candidates_str = '\n'.join(lines)

    stats = ctx['deck_report']['stats']
    density = ctx['deck_report']['density_vector']
    meaningful = {k: round(v, 2) for k, v in density.items() if v > 0}
    boss_prompt = _get_boss_prompt(ctx['act'], ctx['boss_name'])
    boss_section = f"\n[Boss Strategy]\n{boss_prompt}\n" if boss_prompt else ""

    prompt = f"""
[Current State]
Act: {ctx['act']}, HP: {current_hp}/{max_hp}, Gold: {gold}g

[Deck Stats]
Size: {ctx['deck_report']['deck_size']} | Avg Cost: {stats.get('avg_cost', 0)}
Power Score: {ctx['deck_score']:.2f}
Synergies: {meaningful}
{boss_section}

[Top Unified Candidates]
These items have been pre-evaluated across ALL categories (Relics, Cards, Purge, Potions).
The "Priority Score" represents its estimated value for this specific deck.
Higher Score = Better purchase.

{candidates_str}

[Task]
You are a highly strategic Slay the Spire AI. Choose the SINGLE BEST item to buy from the candidates above, or 'leave'.

[Decision Rules]
- Rely heavily on the 'Priority Score'. Items with higher scores are mathematically better for your current deck.
- DO NOT save gold unnecessarily. If there is a candidate with a decent Priority Score (> 60), you should probably buy it.
- Only choose 'leave' if all available options have very low scores (< 30) and don't fit the deck at all.

Output EXACTLY in this JSON format:
{{
    "reasoning": "1-2 sentences explaining why.",
    "action": "buy_card" | "buy_relic" | "buy_potion" | "purge" | "leave",
    "target_index": <integer, or 0 if purge/leave>
}}
"""

    log.info(f"상점 LLM 호출 (Top {len(top_candidates)} candidates)")

    try:
        response = ollama.chat(
            model=MODEL_NAME,
            messages=[
                {'role': 'system', 'content': 'You are a strategic Slay the Spire shop manager. You maximize immediate and long-term value.'},
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

        if not _execute_action(action, target, shop_cards, shop_relics, shop_potions, state.get("floor", "?")):
            SHOP_DONE = True
            print("leave", flush=True)

    except Exception as e:
        log.error(f"🚨 shop LLM 호출 에러: {e} → leave")
        SHOP_DONE = True
        print("leave", flush=True)