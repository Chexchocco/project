
from db.db_loader import get_card_info
import config
import json
import os
import logging
log = logging.getLogger("STS_AI")
VALUE_PATH = os.path.join(config.DB_PATH, "value_config.json")
with open(VALUE_PATH, 'r', encoding='utf-8') as f:
    STRATEGY_CONFIG = json.load(f)


CURSE_TYPES = {"Curse"}
STATUS_TYPES = {"Status"}


def score_card(card_info, current_deck_summary, relic_names=None):
    card = get_card_info(card_info)
    
    # 1. 유물 전처리 (Pre-process: 스네코 등)
    # card = apply_relic_pre_process(card, relic_names)

    base_score = card.get("base_value", 5.0)
    
    # 2. 물리적 결핍 보정 (Gap Analysis)
    physical_bonus = 0
    targets = act_strategy.get("physical_thresholds", {})
    stats = current_deck_summary.get("stats", {})
    
    for metric, config in targets.items():
        curr = stats.get(metric, 0)
        target = config['target']
        if metric != "avg_cost" and curr < target:
            # 부족한 만큼 가산점 (Gap * Weight)
            gap = target - curr
            physical_bonus += gap * config.get('weight', 1.0)

    # 3. 시너지 밀도 보정
    synergy_bonus = 0
    provides = card.get("synergy", {}).get("provides", {})
    density = current_deck_summary.get("density_vector", {})
    
    for tag, val in provides.items():
        # 결핍도(Ratio) 역수 보정
        current_val = density.get(tag, 0.0)
        target_val = act_strategy.get("synergy_weights", {}).get(tag, 0.1)
        ratio = current_val / target_val if target_val > 0 else 1.0
        synergy_bonus += val * (1.0 / (ratio + 0.1))

    # 4. 유물 후처리 (Post-process: 종이 개구리 등)
    final_score = base_score + physical_bonus + synergy_bonus
    # final_score = apply_relic_post_process(final_score, card, relic_names)
    
    return final_score

def aggregate_deck_tags(enriched_deck):
    raw_counts = {}
    
    for card in enriched_deck:
        # 1. JSON에 적힌 provides 태그 수집
        provides = card.get('synergy', {}).get('provides', {})
        for tag, val in provides.items():
            raw_counts[tag] = raw_counts.get(tag, 0.0) + val
            
        # 2. 줏대 로직: 기본 필드 자동 추출 (노가다 방지)
        if card.get('block', 0) > 0:
            raw_counts['BLOCK'] = raw_counts.get('BLOCK', 0.0) + card['block']
        if card.get('draw', 0) > 0:
            raw_counts['DRAW'] = raw_counts.get('DRAW', 0.0) + card['draw']
        if card.get('damage', 0) > 0:
            # 깡딜 점수 합산
            raw_counts['RAW_DAMAGE'] = raw_counts.get('RAW_DAMAGE', 0.0) + card['damage']
            
    return raw_counts


def calculate_density_vector(raw_counts, deck_size):
    density_vector = {}
    
    # 카드 1장당 기여도로 변환 (덱이 두꺼워지면 개별 카드의 밀도는 낮아짐)
    for tag, total_val in raw_counts.items():
        # 단순히 개수로 나눌지, 가중치를 둘지는 조정 가능
        density_vector[tag] = round(total_val / max(1, deck_size), 3)
        
    return density_vector

def score_deck_summary(enriched_deck):
    #덱 평가용 
    deck_size = len(enriched_deck)
    if deck_size == 0: return {}

    total_dmg = 0
    total_blk = 0
    total_cost = 0
    total_draw = 0
    raw_synergy = {}

    for card in enriched_deck:
        total_dmg += card.get('damage', 0)
        total_blk += card.get('block', 0)
        total_draw += card.get('draw', 0)
        
        # Cost 처리: 숫자가 아니면(X 등) 2로 가정
        c = card.get('cost', '0')
        total_cost += int(c) if str(c).isdigit() else (2 if c == 'X' else 0)

        # 시너지 태그 수집
        provides = card.get('synergy', {}).get('provides', {})
        for tag, val in provides.items():
            raw_synergy[tag] = raw_synergy.get(tag, 0.0) + val

    return {
        "stats": {
            "avg_dmg": round(total_dmg / deck_size, 2),
            "avg_blk": round(total_blk / deck_size, 2),
            "avg_cost": round(total_cost / deck_size, 2),
            "draw_ratio": round(total_draw / deck_size, 2),
            "dmg_per_energy": round(total_dmg / max(1, total_cost), 2),
            "blk_per_energy": round(total_blk / max(1, total_cost), 2)
        },
        "raw_synergy": raw_synergy,
        "deck_size": deck_size
    }





def score_deck(enriched_deck, state, enriched_relics=None):
    ##종합본
    summary = score_deck_summary(enriched_deck)
    if not summary: return {}

    # 유물을 포함한 밀도 벡터 계산
    density_vector = {tag: val / summary['deck_size'] for tag, val in summary['raw_synergy'].items()}
    
    for relic in enriched_relics:
        provides = relic.get("synergy", {}).get("provides", {})
        for tag, bonus in provides.items():
            # 유물은 덱 사이즈를 늘리지 않으므로 합산 시 가중치가 큼
            density_vector[tag] = density_vector.get(tag, 0.0) + bonus

    # 전략 로드 (Act/Boss Context)
    act_key = f"Act_{state.get('act', 1)}"
    # 여기서 value_config의 전략을 merge하는 로직 추가 필요

    return {
        "stats": summary['stats'],
        "density_vector": density_vector,
        "deck_size": summary['deck_size']
    }