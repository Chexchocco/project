from collections import Counter
import json
import logging
log = logging.getLogger("STS_AI")
import config
import os
VALUE_PATH = os.path.join(config.LOCAL_PATH, "value_config.json")

with open(VALUE_PATH, 'r', encoding='utf-8') as f:
    STRATEGY_CONFIG = json.load(f)

def get_draw_pile_summary(state):
    combat = state["combat_state"]
    draw_pile = combat.get("draw_pile", [])
    
    card_counts = Counter(card["name"] for card in draw_pile)
    
    type_counts = Counter(card["type"] for card in draw_pile)
    
    # 3. LLM이 이해하기 쉬운 요약본 생성
    summary = {
        "total_remaining": len(draw_pile),
        "card_list": dict(card_counts),  
        "types": dict(type_counts)       
    }
    
    return summary


def evaluate_deck_core(enriched_deck):
    stats = {
        "total_cards": max(1, len(enriched_deck)),
        "attack_cost": 0, "total_damage": 0, "attack_count": 0,
        "block_cost": 0,  "total_block": 0,  "block_count": 0,
        "draw_count": 0,  "total_draw": 0
    }
    
    for card in enriched_deck:
        cost_str = str(card.get("cost", "0"))
        cost = max(0.5, int(cost_str)) if cost_str == "0" else (2.0 if cost_str == "X" else int(cost_str) if cost_str.isdigit() else 0.0)
        
        dmg, blk, draw = card.get("damage", 0), card.get("block", 0), card.get("draw", 0)
        if dmg > 0: stats["total_damage"] += dmg; stats["attack_cost"] += cost; stats["attack_count"] += 1
        if blk > 0: stats["total_block"] += blk; stats["block_cost"] += cost; stats["block_count"] += 1
        if draw > 0: stats["total_draw"] += draw; stats["draw_count"] += 1

    atk_eff = stats["total_damage"] / max(1.0, stats["attack_cost"])
    def_eff = stats["total_block"] / max(1.0, stats["block_cost"])
    atk_density = stats["attack_count"] / stats["total_cards"]
    def_density = stats["block_count"] / stats["total_cards"]
    avg_draw_per_card = stats["total_draw"] / stats["total_cards"]
    expected_draws = 5.0 + (avg_draw_per_card * 5.0)

    report = "[Deck Core Stats]\n"
    report += f"- Attack Efficiency: {atk_eff:.1f} DMG/Energy (Density: {atk_density*100:.0f}%)\n"
    report += f"- Block Efficiency: {def_eff:.1f} BLK/Energy (Density: {def_density*100:.0f}%)\n"
    report += f"- Expected Cards Seen Per Turn: {expected_draws:.1f} cards\n"
    
    return report, stats



def evaluate_deck_vectors(enriched_deck, state, enriched_relics=None):
    if enriched_relics is None: enriched_relics = []
    deck_size = max(1, len(enriched_deck))
    
    current_act = f"Act_{state.get('act', 1)}"
    boss_name = state.get("boss", "Unknown")
    
    # 1. Base Target Density (카드 1장당 목표 밀도) 가져오기
    target_density = STRATEGY_CONFIG.get("base_demand", {}).copy()
    
    # 1.5. 액트(Act) 기본 모디파이어 적용 추가 💡
    act_modifiers = STRATEGY_CONFIG.get("act_strategies", {}).get(current_act, {}).get("act_base_modifiers", {})
    for tag, weight in act_modifiers.items():
        if tag == "description": continue
        if tag in target_density:
            target_density[tag] *= weight
        else:
            target_density[tag] = weight

    # 2. 보스/액트 모디파이어 적용 (목표 밀도 자체를 변동시킴)
    boss_config = STRATEGY_CONFIG.get("act_strategies", {}).get(current_act, {}).get("bosses", {}).get(boss_name, {})
    modifiers = boss_config.get("boss_modifiers", {})

    # 3. Dynamic Requirements (덱 안의 카드가 요구하는 수치 합산)
    # 예: 드롭킥이 있으면 VULNERABLE의 목표 밀도가 소폭 상승
    dynamic_req_sum = {}
    for item in enriched_deck + enriched_relics:
        for tag, val in item.get("synergy", {}).get("requires", {}).items():
            dynamic_req_sum[tag] = dynamic_req_sum.get(tag, 0.0) + val
            
    # 누적된 요구치(Sum)를 덱 장수로 나누어 목표 밀도에 더함
    for tag, val in dynamic_req_sum.items():
        target_density[tag] = target_density.get(tag, 0.0) + (val / deck_size)

    # 4. 💡 Supply(공급) 밀도 계산 (Average Value)
    supply_sum = {}
    for item in enriched_deck:
        for tag, val in item.get("synergy", {}).get("provides", {}).items():
            supply_sum[tag] = supply_sum.get(tag, 0.0) + val
            
    for relic in enriched_relics:
        if relic.get("trigger") == "Pickup": continue
        for tag, val in relic.get("synergy", {}).get("provides", {}).items():
            # 유물은 매 턴 효과를 발휘하거나 덱 전체에 영향을 주므로 합산에 포함
            supply_sum[tag] = supply_sum.get(tag, 0.0) + val

    # 현재 덱이 제공하는 '카드 1장당 평균 밸류(Current Density)'
    current_density = {tag: val / deck_size for tag, val in supply_sum.items()}

    # 5. LLM 리포트 생성 (밀도 비교)
    report = f"[Strategy Mode: Facing {boss_name}]\n"
    report += "[Synergy Density Diagnostics (Current Density / Target Density)]\n"
    
    warnings = []
    for tag, target_val in target_density.items():
        if tag == "description": 
            log.info(f"{tag}, {target_val}가 좀 이상함") 
            continue
        if target_val == 0: continue
            
        cur_val = current_density.get(tag, 0.0)
        
        # 음수 요구치(TOXIC) 처리
        if target_val < 0:
            if cur_val > 0:
                warnings.append(f"- ☠️ [TOXIC] Remove {tag} immediately! (Toxicity Level: {cur_val:.2f})")
            continue

        # 0 나누기 방지
        if target_val > 0:
            ratio = cur_val / target_val
        else:
            ratio = 1.0 # 타겟이 0 이하라면 만족한 것으로 취급
            
        if ratio < 0.4: warnings.append(f"- 🚨 [STARVING] Critical need for {tag} (Current: {cur_val:.2f}, Target: {target_val:.2f})")
        elif ratio < 0.8: warnings.append(f"- ⚠️ [LACKING] Need more {tag}")
        elif ratio > 1.5: warnings.append(f"- 🛑 [OVERSATURATED] Avoid picking {tag}")
        else: warnings.append(f"- ✅ [SATISFIED] {tag} is optimal")

    report += "\n".join(warnings) + "\n"
    boss_prompt = boss_config.get("llm_prompt", "")
    
    return report, boss_prompt