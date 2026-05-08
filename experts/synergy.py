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
import config
import json
import os
import 
import logging
log = logging.getLogger("STS_AI")
VALUE_PATH = os.path.join(config.DB_PATH, "value_config.json")
with open(VALUE_PATH, 'r', encoding='utf-8') as f:
    STRATEGY_CONFIG = json.load(f)


CURSE_TYPES = {"Curse"}
STATUS_TYPES = {"Status"}


def score_card(card_info, synergy_report_data, relic_names=None):
    """
    synergy_report_data: { "VULNERABLE": {"ratio": 0.2}, "BLOCK": {"ratio": 1.5}, ... }
    relic_names: 유물 이름 리스트 (특수 시너지 체크용)
    """
    card = get_card_info(card_info)
    base_score = card.get("base_value", 5.0)
    
    # 1. 시너지 리포트(Ratio)를 이용한 일반 보정 (핵심 로직)
    # 리포트만으로도 80% 이상의 가치 판단이 가능함
    provides = card.get("synergy", {}).get("provides", {})
    for tag, val in provides.items():
        ratio = synergy_report_data.get(tag, {}).get("ratio", 1.0)
        # 결핍도에 따른 가중치 부여 (STARVING 시 가치 폭등)
        base_score += val * (1.0 / (ratio + 0.1)) 

    # 2. 특수 유물/상황 보정 (수치로 안 잡히는 '궁합' 처리)
    if relic_names:
        # 종이 개구리가 있다면 취약 카드의 가치를 한 번 더 뻥튀기
        if "Paper Phrog" in relic_names and "VULNERABLE" in provides:
            base_score *= 1.5
            #이게 필요한가 싶긴한데 일단 넣고 나중에 빼겟음
    return base_score

def score_deck_summary(enriched_deck):
    #덱 평가용 
    #report는 LLM이 보기 좋은 구조로 주는거고
    #stats 는 정략적으로 나타낸 카드 별 공격 비용 
    deck_size = max(1, len(enriched_deck))
    stats = {
        "total_cards": deck_size,
        "attack_cost": 0, "total_damage": 0, "attack_count": 0,
        "block_cost": 0,  "total_block": 0,  "block_count": 0,
        "draw_count": 0,  "total_draw": 0,
        
        # --- 추가될 지표 ---
        "avg_dmg_per_card": 0.0,   # 카드 1장당 평균 데미지
        "expected_turn_dmg": 0.0,  # 1턴(5장) 기대 데미지
        "avg_blk_per_card": 0.0,   # 카드 1장당 평균 방어력
        "expected_turn_blk": 0.0,  # 1턴(5장) 기대 방어력
        "atk_efficiency": 0.0,     # 에너지 대비 공격 효율
        "def_efficiency": 0.0      # 에너지 대비 방어 효율
    }
    
    for card in enriched_deck:
        cost_str = str(card.get("cost", "0"))
        # 코스트 처리 (0코는 0.5로 보정하여 효율 계산 시 ZeroDivision 방지 및 가치 반영)
        cost = max(0.5, int(cost_str)) if cost_str.isdigit() else (2.0 if cost_str == "X" else 0.0)
        
        # 다단히트 고려한 실제 데미지 계산
        dmg = card.get("damage", 0) * card.get("hits", 1)
        blk = card.get("block", 0)
        draw = card.get("draw", 0)
        
        if dmg > 0:
            stats["total_damage"] += dmg
            stats["attack_cost"] += cost
            stats["attack_count"] += 1
        if blk > 0:
            stats["total_block"] += blk
            stats["block_cost"] += cost
            stats["block_count"] += 1
        if draw > 0:
            stats["total_draw"] += draw
            stats["draw_count"] += 1

    # 지표 계산
    stats["atk_efficiency"] = stats["total_damage"] / max(1.0, stats["attack_cost"])
    stats["def_efficiency"] = stats["total_block"] / max(1.0, stats["block_cost"])
    
    # 💡 1장당 평균 기대치 계산
    stats["avg_dmg_per_card"] = stats["total_damage"] / deck_size
    stats["avg_blk_per_card"] = stats["total_block"] / deck_size
    
    # 💡 한 턴(기본 5장 드로우) 기대치 포장
    stats["expected_turn_dmg"] = stats["avg_dmg_per_card"] * 5.0
    stats["expected_turn_blk"] = stats["avg_blk_per_card"] * 5.0

    # 드로우 효율 및 기대 카드 수
    avg_draw_per_card = stats["total_draw"] / deck_size
    expected_draws = 5.0 + (avg_draw_per_card * 5.0)

    # LLM용 리포트 구성 (포장된 데이터 사용)
    atk_density = stats["attack_count"] / deck_size
    def_density = stats["block_count"] / deck_size

    report = "[Deck Core Stats]\n"
    report += f"- Attack Efficiency: {stats['atk_efficiency']:.1f} DMG/Energy (Density: {atk_density*100:.0f}%)\n"
    report += f"- Expected Turn DMG: {stats['expected_turn_dmg']:.1f} (Avg per card: {stats['avg_dmg_per_card']:.1f})\n"
    report += f"- Block Efficiency: {stats['def_efficiency']:.1f} BLK/Energy (Density: {def_density*100:.0f}%)\n"
    report += f"- Expected Turn BLK: {stats['expected_turn_blk']:.1f}\n"
    report += f"- Expected Cards Seen Per Turn: {expected_draws:.1f} cards\n"
    
    return report, stats





def score_deck_total(enriched_deck, state, enriched_relics=None):
    
    if enriched_relics is None: enriched_relics = []
    deck_size = max(1, len(enriched_deck))
    current_act = f"Act_{state.get('act', 1)}"
    boss_name = state.get("boss", "Unknown")

    # 1. 시너지 공급량 계산 (Provides)
    supply_sum = {}
    for item in enriched_deck + [r for r in enriched_relics if r.get("trigger") != "Pickup"]:
        for tag, val in item.get("synergy", {}).get("provides", {}).items():
            supply_sum[tag] = supply_sum.get(tag, 0.0) + val
    
    current_density = {tag: val / deck_size for tag, val in supply_sum.items()}

    # 2. 막/보스 상황에 따른 목표 밀도(Target) 설정
    target_density = STRATEGY_CONFIG.get("base_demand", {}).copy()
    
    # Act/Boss 가중치 적용 (가중치가 높을수록 해당 요소가 점수에서 차지하는 비중이 커짐)
    act_mods = STRATEGY_CONFIG.get("act_strategies", {}).get(current_act, {}).get("act_base_modifiers", {})
    boss_config = STRATEGY_CONFIG.get("act_strategies", {}).get(current_act, {}).get("bosses", {}).get(boss_name, {})
    boss_mods = boss_config.get("boss_modifiers", {})

    for tag, weight in {**act_mods, **boss_mods}.items():
        if tag in target_density:
            target_density[tag] *= weight

    # 3. 💡 점수 계산: 요구 조건 충족도 (Score by Fulfillment)
    # 각 태그별로 (현재 밀도 / 목표 밀도)의 평균을 내어 점수화합니다.
    total_score = 0
    evaluated_tags = 0

    for tag, target_val in target_density.items():
        if tag == "description" or target_val == 0: continue
        
        cur_val = current_density.get(tag, 0.0)
        
        # 음수 요구치(TOXIC/CURSE) 처리: 덱이 소화 가능(Exhaust 등)하면 페널티 감소
        if target_val < 0:
            # 덱에 소멸(Exhaust)이나 진화(Evolve) 같은 처리 수단이 있다면 독성 점수를 완화
            mitigation = current_density.get("EXHAUST_EVENT", 0) + current_density.get("STATUS_SYNERGY", 0)
            penalty_score = max(0, (cur_val * abs(target_val)) - mitigation)
            total_score -= penalty_score * 10 # 독성 수치만큼 감점
        else:
            # 일반 시너지: 목표치 대비 달성률 (최대 1.2배까지만 가산점 인정)
            fulfillment = min(1.2, cur_val / target_val) if target_val > 0 else 1.0
            total_score += fulfillment * 100
            evaluated_tags += 1

    final_score = max(0, min(100, total_score / max(1, evaluated_tags)))

    # 4. 리포트 생성
    report = f"=== [Act {state.get('act')} Decision Support] ===\n"
    report += f"Current Deck Readiness: {final_score:.1f}/100\n"
    
    warnings = []
    for tag, target_val in target_density.items():
        if target_val <= 0: continue
        ratio = current_density.get(tag, 0.0) / target_val
        if ratio < 0.5: warnings.append(f"- [STARVING] {tag}")
        elif ratio > 1.8: warnings.append(f"- [OVERSATURATED] {tag}")

    report += "\n".join(warnings) if warnings else "- Deck synergy is well-balanced for the current phase."

    return report, final_score, boss_config.get("llm_prompt", "")