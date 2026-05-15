from db.db_loader import get_card_info
import json
import os
import logging

log = logging.getLogger("STS_AI")
from config import LOCAL_PATH, DB_PATH, LOG_PATH

card_log = logging.getLogger("CARD_PICKER")

def score_card(card_info, deck_report, act_strategy, relic_names=None, synergy_mgr=None):
    if relic_names is None:
        relic_names = []
        
    card = get_card_info(card_info)
    base_score = card.get("base_value", 5.0)
    deck_size = deck_report.get("deck_size", 10)
    # --- 물리적 결핍 보정 ---
    physical_bonus = 0
    targets = act_strategy.get("physical_thresholds", {})
    stats = deck_report.get("stats", {})
    
    for metric, config in targets.items():
        curr = stats.get(metric, 0)
        target = config['target']
        if not config.get('is_inverse') and curr < target:
            physical_bonus += (target - curr) * config.get('weight', 1.0)

    # --- 시너지 밀도 보정 ---
    synergy_bonus = 0
    provides = card.get("synergy", {}).get("provides", {})
    requires = card.get("synergy", {}).get("requires", {})
    density = deck_report.get("density_vector", {})
    


    for tag, val in provides.items():
        # 상태이상 생성 같은 페널티 기믹은 아예 따로 뺍니다 (밑에서 설명)
        if tag in ["STATUS_CARD", "CURSE_CARD"]: continue

        # 현재 덱에 이 태그가 몇 장(Count)이나 있는가?
        current_count = density.get(tag, 0.0) * deck_size 
        
        # 이 태그는 덱에 몇 장(Target) 있어야 하는가? (value_config에서 가져오되, 기본값은 1~2장)
        target_count = act_strategy.get("synergy_weights", {}).get(tag, 1.5) 

        # [새로운 선형 감가상각 공식] 
        # (목표 장수 - 현재 장수) 만큼만 점수를 곱해줍니다. 목표를 채웠으면 0점!
        gap = max(0.0, target_count - current_count) 
        
        bonus = val * gap * 5.0 # (5.0은 점수 체급을 맞추기 위한 기본 가중치)
        synergy_bonus += bonus


    # ---------------------------------------------------------
    # 2. REQUIRES (조건/콤보 발동
    # ---------------------------------------------------------
    for tag, req_val in requires.items():
        current_count = density.get(tag, 0.0) * deck_size
        
        if current_count > 0:
            bonus = min(15.0, req_val * current_count * 5.0)
            synergy_bonus += bonus
        else:
            # 재료가 0장이면 가차없이 페널티 (요구 조건 미달)
            synergy_bonus -= (req_val * 1.0)

    # ---------------------------------------------------------
    # 3. 예외 처리 (상태이상, 페널티 등)
    # ---------------------------------------------------------
    if "STATUS_CARD" in provides:
            # [수정 완료] STATUS_CARD가 아니라 STATUS_SYNERGY(해독제)를 찾아야 함!
            # (DB에서 진화/불뿜기 카드의 provides에 "STATUS_SYNERGY"를 꼭 넣어주세요)
            synergy_count = density.get("STATUS_SYNERGY", 0.0) * deck_size
            if synergy_count > 0:
                bonus = min(15.0, synergy_count * 5.0)
                synergy_bonus += bonus
            else:
                synergy_bonus -= 5.0
    synergy_bonus = min(15.0, synergy_bonus)

        # [핵심] 1차 계산된 점수
    raw_score = base_score + physical_bonus + synergy_bonus

    card_log.info(f"{card.get('name',{})}카드 평가 항목 : base : {base_score} + physical_bonus : {physical_bonus} + synergy_bonus + {synergy_bonus} = Total : {base_score+physical_bonus+synergy_bonus}\n")

    # [핵심] RelicModifier를 통한 기믹 유물 후처리 (스네코, 미라손 등 반영)
    final_score = RelicModifier.apply_post_process(
        raw_score, card, relic_names, stats
    )

    return round(final_score, 2)


# 2. 유물 통합 및 밀도 계산 (구 calculate_density_vector 기능 통합)
# 2. 유물 통합 및 밀도 계산 (SynergyManager 연동)
def score_deck(enriched_deck, enriched_relics, potion, game_state, synergy_manager=None):
    summary = score_deck_summary(enriched_deck)
    if not summary: return {}

    raw_density = summary['raw_synergy']
    relic_names = [r.get('id', r.get('name')) for r in enriched_relics]
    
    # 유물의 순수 제공 태그들을 raw_density에 먼저 합산
    for relic in enriched_relics:
        provides = relic.get("synergy", {}).get("provides", {})
        for tag, bonus in provides.items():
            raw_density[tag] = raw_density.get(tag, 0.0) + bonus

    # [핵심] SynergyManager가 있으면 정교한 기댓값/Roll-up 계산 수행
    density_vector = synergy_manager.flatten_and_calculate_density(
            raw_density, summary['stats'], relic_names
        )

    return {
        "deck_size": summary['deck_size'],
        "stats": summary['stats'],
        "density_vector": density_vector
    }
# 1. 덱의 물리 지표 및 태그 원재료 수집
def score_deck_summary(enriched_deck):
    deck_size = len(enriched_deck)
    if deck_size == 0: 
        return {
            "deck_size": 0,
            "stats": {"avg_damage": 0, "avg_block": 0, "avg_cost": 0, "draw_ratio": 0, "dmg_per_energy": 0, "blk_per_energy": 0},
            "raw_synergy": {}
        }

    total_dmg = 0
    total_blk = 0
    total_cost = 0
    total_draw = 0
    raw_synergy = {}

    for card in enriched_deck:
        # 카드 기본 스탯 합산
        total_dmg += card.get('damage', 0)
        total_blk += card.get('block', 0)
        total_draw += card.get('draw', 0)
        
        # 코스트 합산 (X코스트는 평균적으로 2코스트의 가치로 계산)
        c = card.get('cost', '0')
        cost_val = int(c) if str(c).isdigit() else (2 if str(c).upper() == 'X' else 0)
        total_cost += cost_val

        # 시너지 태그(provides) 수집
        provides = card.get('synergy', {}).get('provides', {})
        for tag, val in provides.items():
            raw_synergy[tag] = raw_synergy.get(tag, 0.0) + val

    avg_cost = round(total_cost / deck_size, 2)
    
    return {
        "deck_size": deck_size,
        "stats": {
            "avg_damage": round(total_dmg / deck_size, 2),
            "avg_block": round(total_blk / deck_size, 2),
            "avg_cost": avg_cost,
            "draw_ratio": round(total_draw / deck_size, 2),
            "dmg_per_energy": round(total_dmg / max(1, total_cost), 2),
            "blk_per_energy": round(total_blk / max(1, total_cost), 2)
        },
        "raw_synergy": raw_synergy
    }

def calculate_deck_avg_score(deck_raw, deck_report, act_strategy, synergy_mgr):
    """현재 덱의 시너지가 반영된 평균 파워 점수를 계산합니다."""
    if not deck_raw:
        return 0.0
        
    total_score = 0.0
    for card_dict in deck_raw:
        info = get_card_info(card_dict)
        if info:
            # 기존에 만든 score_card 함수를 재활용하여 덱 내 카드의 현재 가치를 측정!
            eval_result = score_card(info, deck_report, act_strategy, synergy_mgr=synergy_mgr)
            total_score += eval_result["score"]
            
    # 덱 사이즈로 나누어 '카드 1장당 평균 파워(avg_score)'를 구함
    return total_score / len(deck_raw)

# 3. 덱의 준비도 점수 및 자가 진단 (Aggregate 기능)
def calculate_readiness(deck_report, strategy):
    stats = deck_report['stats']
    density = deck_report['density_vector']
    
    total_score = 0
    evaluated_items = 0

    # 물리 지표 충족도 계산
    phys_targets = strategy.get("physical_thresholds", {})
    for metric, config in phys_targets.items():
        curr = stats.get(metric, 0)
        target = config['target']
        weight = config.get('weight', 1.0)
        
        # 가점/감점 방식 결정 (is_inverse 가 true 면 낮을수록 좋음)
        score = (target / max(0.1, curr)) * 100 if config.get('is_inverse') else min(1.2, curr / target) * 100
        total_score += score * weight
        evaluated_items += weight

    # 시너지 밀도 충족도 계산
    syn_targets = strategy.get("synergy_weights", {})
    for tag, target_val in syn_targets.items():
        curr_val = density.get(tag, 0.0)
        fulfillment = min(1.2, curr_val / target_val) * 100 if target_val > 0 else 100
        total_score += fulfillment
        evaluated_items += 1

    return round(total_score / max(1, evaluated_items), 1)





    

class SynergyManager:
    def __init__(self, value_config_json, tag_db_json):
        # 1. 족보(계층) 데이터 로드 (synergyTagDB.json에서 가져옴)
        self.tag_definitions = tag_db_json.get("tags", {})
        
        # 2. 가중치 데이터 로드 (value_config.json에서 가져옴)
        self.weights = value_config_json.get("base_demand", {}).get("synergy_weights", {})


    def flatten_and_calculate_density(self, raw_density, deck_stats, relic_names):
        # 1. 원본 데이터 복사 (계산하면서 값을 차감할 용도)
        flat_vector = raw_density.copy()
        
        # 2. 물리 지표 준비
        deck_size = max(1, deck_stats.get('deck_size', 10))
        avg_cost = deck_stats.get('avg_cost', 1.1)
        draw_ratio = deck_stats.get('draw_ratio', 0.2)
        
        # 콤보형(잉크병, 쿠나이 등)을 위한 기본 확률
        combo_rate = min(1.2, max(0.2, draw_ratio / max(0.5, avg_cost)))

        # --- [Step 1] 개별 유물 특수 전처리 (전수 조사 반영) ---

        # [ENERGY 관련]
        # 1. 해시계: 덱 사이즈 기반
        if "Sundial" in relic_names:
            compress_rate = max(0.2, 2.0 - (deck_size * 0.06))
            flat_vector["ENERGY"] = flat_vector.get("ENERGY", 0.0) + (2.0 * compress_rate)
            flat_vector["CONDITIONAL_ENERGY"] = max(0, flat_vector.get("CONDITIONAL_ENERGY", 0.0) - 2.0)

        # 2. 행복한 꽃: 3턴마다 확정 (고정 기댓값 0.33)
        if "Happy_Flower" in relic_names:
            # 3턴당 1에너지이므로 매 턴 평균 0.33의 가치
            flat_vector["ENERGY"] = flat_vector.get("ENERGY", 0.0) + 0.33
            # DB에 CONDITIONAL_ENERGY로 잡혀있다면 차감
            flat_vector["CONDITIONAL_ENERGY"] = max(0, flat_vector.get("CONDITIONAL_ENERGY", 0.0) - 1.0)

        # 3. 랜턴 / 찻잎: 첫 턴 한정 에너지 (전투당 1~2회)
        # 얘네는 ENERGY_T1 같은 태그가 있다면 좋지만, 없다면 고정 기댓값(약 0.2~0.3) 반영
        if "Lantern" in relic_names:
            flat_vector["ENERGY"] = flat_vector.get("ENERGY", 0.0) + 0.25 # 평균 4턴 전투 기준
            flat_vector["CONDITIONAL_ENERGY"] = max(0, flat_vector.get("CONDITIONAL_ENERGY", 0.0) - 1.0)

        # 4. 아이스크림: 에너지 효율 뻥튀기
        if "Ice_Cream" in relic_names:
            flat_vector["ENERGY"] = flat_vector.get("ENERGY", 0.0) * 1.5

        # [DEXTERITY / STRENGTH 관련]
        if "Kunai" in relic_names:
            flat_vector["DEXTERITY"] = flat_vector.get("DEXTERITY", 0.0) + (1.0 * combo_rate)
            flat_vector["CONDITIONAL_DEXTERITY"] = max(0, flat_vector.get("CONDITIONAL_DEXTERITY", 0.0) - 1.0)

        if "Shuriken" in relic_names:
            flat_vector["STRENGTH"] = flat_vector.get("STRENGTH", 0.0) + (1.0 * combo_rate)
            flat_vector["CONDITIONAL_STRENGTH"] = max(0, flat_vector.get("CONDITIONAL_STRENGTH", 0.0) - 1.0)

        # [DRAW 관련]
        if "Unceasing_Top" in relic_names:
            flat_vector["DRAW"] = flat_vector.get("DRAW", 0.0) + max(0.0, 1.5 - avg_cost)

        if "Centennial_Puzzle" in relic_names:
            # 전투당 딱 한 번 3장 드로우 -> 기댓값 약 0.5 (고정)
            flat_vector["DRAW"] = flat_vector.get("DRAW", 0.0) + 0.5
            flat_vector["CONDITIONAL_DRAW"] = max(0, flat_vector.get("CONDITIONAL_DRAW", 0.0) - 3.0)


        # --- [Step 2] 나머지 범용 조건부 태그 통합 ---
        # 위에서 특수한 애들은 이미 다 깎였으므로, 잉크병(Ink Bottle) 같은 '평범한' 애들만 남음

        
        # 1. 회중시계 (Pocketwatch) - 무거운 덱일수록 드로우가 폭발함
        if "Pocketwatch" in relic_names:
            # 코스트가 1.5에 가까울수록(무거울수록) 발동률 상승 (최대 1.0)
            pw_rate = min(1.0, max(0.2, avg_cost / 1.5))
            flat_vector["DRAW"] = flat_vector.get("DRAW", 0.0) + (3.0 * pw_rate)
            flat_vector["CONDITIONAL_DRAW"] = max(0, flat_vector.get("CONDITIONAL_DRAW", 0.0) - 3.0)

        # 2. 병법서 (Art of War) - 공격 안 할 때 에너지
        if "Art_of_War" in relic_names:
            # 일단 콤보랑 상관없으니 고정 기댓값 0.4 부여 (수비적인 덱에서 더 좋음)
            flat_vector["ENERGY"] = flat_vector.get("ENERGY", 0.0) + 0.4
            flat_vector["CONDITIONAL_ENERGY"] = max(0, flat_vector.get("CONDITIONAL_ENERGY", 0.0) - 1.0)

        # 3. 노예상인의 목걸이 (Slaver's Collar) - 엘리트/보스 한정
        if "Slavers_Collar" in relic_names:
            # 전체 게임 턴 수 대비 보스/엘리트 비중 약 35%로 계산
            flat_vector["ENERGY"] = flat_vector.get("ENERGY", 0.0) + 0.35
            flat_vector["CONDITIONAL_ENERGY"] = max(0, flat_vector.get("CONDITIONAL_ENERGY", 0.0) - 1.0)

        # 4. 붉은 해골 (Red Skull) - 체력 50% 이하
        if "Red_Skull" in relic_names:
            # 아이언클래드는 자해나 피관리를 자주 하므로 발동률 약 40%로 고정 계산
            flat_vector["STRENGTH"] = flat_vector.get("STRENGTH", 0.0) + (3.0 * 0.4)
            flat_vector["CONDITIONAL_STRENGTH"] = max(0, flat_vector.get("CONDITIONAL_STRENGTH", 0.0) - 3.0)
            
        # 5. 그렘린 뿔 (Gremlin Horn) - 킬 할 때마다
        if "Gremlin_Horn" in relic_names:
            # 고정 기댓값 부여 후 명단에서 삭제
            flat_vector["ENERGY"] = flat_vector.get("ENERGY", 0.0) + 0.2
            flat_vector["DRAW"] = flat_vector.get("DRAW", 0.0) + 0.2
            flat_vector["CONDITIONAL_ENERGY"] = max(0, flat_vector.get("CONDITIONAL_ENERGY", 0.0) - 1.0)
            flat_vector["CONDITIONAL_DRAW"] = max(0, flat_vector.get("CONDITIONAL_DRAW", 0.0) - 1.0)



        for tag in ["STRENGTH", "DEXTERITY", "ENERGY", "DRAW"]:
            cond_key = f"CONDITIONAL_{tag}"
            # 실질 기댓값 = (남은 일반 조건부 수치 * 콤보 확률)
            cond_val = flat_vector.get(cond_key, 0.0) * combo_rate
            
            # _TOTAL 키에 순수값 + 조건부 기댓값 합산
            flat_vector[f"{tag}_TOTAL"] = flat_vector.get(tag, 0.0) + cond_val

        for tag in ["STRENGTH", "DEXTERITY", "ENERGY", "DRAW"]:
            cond_key = f"CONDITIONAL_{tag}"
            if cond_key in flat_vector:
                # 기댓값 계산 후 원본(ENERGY 등)에 바로 더해버림
                cond_val = flat_vector[cond_key] * combo_rate
                flat_vector[tag] = flat_vector.get(tag, 0.0) + cond_val
                del flat_vector[cond_key] # 다 쓴 찌꺼기 삭제

        # 💡 2. 자식 -> 부모 Roll-up (_TOTAL 같은 꼼수 없이 직관적으로)
        for parent_tag, info in self.tag_definitions.items():
            children = info.get("children", [])
            if not children: continue
            
            child_sum = 0
            for c in children:
                # DB에 적힌 자식 이름 그대로 가져와서 더함 (예: ENERGY)
                child_sum += flat_vector.get(c, 0.0) 
            
            if child_sum > 0:
                flat_vector[parent_tag] = flat_vector.get(parent_tag, 0.0) + child_sum

        # 💡 3. 불필요한 0.0 값들 싹 청소 (LLM 로그가 엄청 깔끔해짐)
        flat_vector = {k: v for k, v in flat_vector.items() if v > 0}

        for key in flat_vector:
                    # 강타(2.0)가 10장 덱에 있다면 0.2로 변환되어 target(0.2)과 완벽히 맞아떨어짐
            flat_vector[key] = round(flat_vector[key] / deck_size, 3)

        return flat_vector
    





class RelicModifier:
    @staticmethod
    def apply_post_process(base_score, card, relic_names, deck_stats):
        score = base_score
        
        # 카드의 기본 정보 추출
        card_type = card.get('type', 'Skill')
        raw_cost = card.get('cost', 1)
        
        # 코스트를 숫자로 안전하게 변환 (X 코스트 등 예외 처리)
        is_x_cost = (str(raw_cost).upper() == 'X')
        cost_val = 0 if is_x_cost else int(raw_cost) if str(raw_cost).isdigit() else 1

        # ---------------------------------------------------------
        # 1. 스네코의 눈 (Snecko Eye): 슬더스 생태계 파괴자
        # ---------------------------------------------------------
        if "Snecko_Eye" in relic_names:
            if not is_x_cost:
                # 2코스트 이상: 가치가 미친듯이 폭등함
                if cost_val >= 2:
                    score *= (1.4 + (cost_val * 0.2)) # 2코는 1.8배, 3코는 2.0배
                # 1코스트: 살짝 손해 (평균 코스트가 1.5가 되므로)
                elif cost_val == 1:
                    score *= 0.8
                # 0코스트: 스네코의 저주 (집으면 안 됨)
                elif cost_val == 0:
                    score *= 0.4

        # ---------------------------------------------------------
        # 2. 네크로노미콘 (Necronomicon): 고코스트 공격 특화
        # ---------------------------------------------------------
        if "Necronomicon" in relic_names:
            # 2코스트 이상의 '공격' 카드만 가치 폭등 (2번 발동)
            if card_type == 'Attack' and cost_val >= 2:
                score *= 1.3    

        # ---------------------------------------------------------
        # 3. 미라 손 (Mummified Hand): 파워 카드 0코스트화
        # ---------------------------------------------------------
        if "Mummified_Hand" in relic_names:
            if card_type == 'Power':
                # 파워 카드 자체의 픽 가치를 크게 높임 (특히 가벼운 파워일수록)
                score *= 1.5 - (cost_val * 0.1)
                
        # ---------------------------------------------------------
        # 4. 타수/저코스트 보상 (쿠나이, 부채, 펜촉 등)
        # ---------------------------------------------------------
        hit_relics = {"Kunai", "Shuriken", "Ornamental_Fan", "Pen_Nib"}
        if any(r in relic_names for r in hit_relics) and card_type == 'Attack':
            # 0코스트 공격이면 가점
            if cost_val == 0 :
                score *= 1.3

        # 나뭇가지: 소멸(Exhaust) 카드는 그냥 사기가 됨
        if "Dead_Branch" in relic_names and "Exhaust" in card.get('effects', []):
            score *= 1.5
            
            
        # 화학물질 X: X코스트 카드의 신
        if "Chemical_X" in relic_names and is_x_cost:
            score *= 2.0
            
        # 벨벳 초커: 1턴 6장 제한 -> 저코스트 밸류 떡락, 고코스트 밸류 상승
        if "Velvet_Choker" in relic_names:
            if cost_val == 0:
                score *= 0.4
            elif cost_val >= 2:
                score *= 1.3

        # ---------------------------------------------------------
        # 6. 타격 및 디버프 시너지 (장화, 챔피언 벨트)
        # ---------------------------------------------------------
        # 장화(The Boot): 타점이 낮거나(5 미만) 연타(3타 이상)인 공격 가점
        if "The_Boot" in relic_names and card_type == 'Attack':
            if card.get('damage', 10) < 5 or card.get('hits', 1) >= 3:
                score *= 1.3
                
        # 챔피언 벨트: 취약(Vulnerable) 부여 카드가 약화까지 걸게 됨
        if "Champion_Belt" in relic_names:
            if card.get('applies_debuff') == 'Vulnerable':
                score *= 1.3

        return score