import json
import re

# ==========================================
# 💡 시너지 사전 (Synergy Dictionary)
# 용진 님이 앞으로 편하게 "딸깍" 하실 공간입니다.
# 언제든 여기만 슥슥 수정하고 스크립트를 다시 돌리면 DB 전체가 업데이트됩니다.
# ==========================================
SYNERGY_MAP = {
    # 1. 상태이상(Status) 시너지
    "fire breathing": {"requires": {"STATUS_CARD": 2.0}, "provides": {}},
    "evolve": {"requires": {"STATUS_CARD": 1.5}, "provides": {}},
    "wild strike": {"requires": {}, "provides": {"STATUS_CARD": 1.0, "STRIKE_CARD": 1.0}},
    "reckless charge": {"requires": {}, "provides": {"STATUS_CARD": 1.0}},
    "power through": {"requires": {}, "provides": {"STATUS_CARD": 2.0}},
    
    # 2. 소멸(Exhaust) 시너지
    "feel no pain": {"requires": {"EXHAUST_EVENT": 1.5}, "provides": {}},
    "dark embrace": {"requires": {"EXHAUST_EVENT": 2.0}, "provides": {}},
    "true grit": {"requires": {}, "provides": {"EXHAUST_EVENT": 1.0}},
    "corruption": {"requires": {"SKILL_CARD": 1.0}, "provides": {"EXHAUST_EVENT": 3.0}},
    "fiend fire": {"requires": {"HAND_SIZE": 1.0}, "provides": {"EXHAUST_EVENT": 3.0}}, # 손패를 많이 소멸시킴
    
    # 3. 완타(Perfected Strike) 시너지
    "perfected strike": {"requires": {"STRIKE_CARD": 1.5}, "provides": {"STRIKE_CARD": 1.0}},
    "strike": {"requires": {}, "provides": {"STRIKE_CARD": 1.0}},
    "twin strike": {"requires": {}, "provides": {"STRIKE_CARD": 1.0}},
    "pommel strike": {"requires": {}, "provides": {"STRIKE_CARD": 1.0}},

    # 🛡️ 방어구축(Block) 시너지
    "body slam": {
        "base_value_override": 5.0,
        "requires": {"BLOCK_GENERATION": 1.5}, # 덱에 방어도를 펌핑하는 카드가 많을수록 밸류 폭발
        "provides": {"BLOCK_ARCHETYPE": 1.0}   # 이 카드를 집음으로써 '방밀덱'의 이정표(아키타입) 제공
    },
    "entrench": { # 참호
        "requires": {"BLOCK_ARCHETYPE": 1.0, "BLOCK_GENERATION": 1.0}, 
        "provides": {}
    },
    "Barricade": { # 바리케이드
        "requires": {"BLOCK_GENERATION": 1.0},
        "provides": {"BLOCK_ARCHETYPE": 3.0} # 
    }
}

def calculate_base_value(damage, block, hits, draw, is_x_cost):
    """
    순수 깡스탯 가치만 계산합니다. (시너지는 제외)
    """
    multiplier = 2 if is_x_cost else (hits if isinstance(hits, int) else 1)
    value = (damage * multiplier) + (block * multiplier) + (draw * 3.0)
    return round(value, 1)

def create_ultimate_database():
    print("🔄 [최종 진화형] 공급-수요 시너지 DB 변환 시작...")
    
    try:
        with open('items.json', "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            
        new_cards = []
        for card in raw_data.get("cards", []):
            name_lower = card.get("name", "").lower()
            desc = card.get("description", "").lower()
            name_lower= name_lower.rstrip('+')


            damage, block, draw = 0, 0, 0
            hits = 1
            is_x_cost = False
            
            # 1. 깡스탯 파싱
            x_hit_match = re.search(r"deal (\d+) damage x times", desc)
            if x_hit_match:
                damage, hits, is_x_cost = int(x_hit_match.group(1)), "X", True
            else:
                multi_hit_match = re.search(r"deal (\d+) damage (\d+) times", desc)
                if multi_hit_match: damage, hits = int(multi_hit_match.group(1)), int(multi_hit_match.group(2))
                else:
                    dmg_match = re.search(r"deal (\d+) damage", desc)
                    if dmg_match: damage = int(dmg_match.group(1))
                    
            x_blk_match = re.search(r"gain (\d+) block x times", desc)
            if x_blk_match:
                block, is_x_cost = int(x_blk_match.group(1)), True
            else:
                blk_match = re.search(r"gain (\d+) block", desc)
                if blk_match: block = int(blk_match.group(1))
                
            draw_match = re.search(r"draw (\d+) card", desc)
            if draw_match: draw = int(draw_match.group(1))

            # 2. 시너지 객체(Synergy Object) 세팅
            # SYNERGY_MAP에 이름이 있으면 가져오고, 없으면 빈 껍데기를 줍니다.
            synergy_data = SYNERGY_MAP.get(name_lower, {"requires": {}, "provides": {}})
            override_val = synergy_data.pop("base_value_override", None)

            # (자동 추론) 설명에 exhaust가 있으면 기본적으로 EXHAUST_EVENT를 1 제공한다고 간주
            if "exhaust." in desc and "EXHAUST_EVENT" not in synergy_data["provides"]:
                synergy_data["provides"]["EXHAUST_EVENT"] = 1.0

            # 3. JSON 재조립
            card["damage"] = damage
            card["hits"] = hits
            card["block"] = block
            card["draw"] = draw
            card["is_x_cost"] = is_x_cost
            
            if override_val is not None:
                card["base_value"] = override_val
            else:
                card["base_value"] = calculate_base_value(damage, block, hits, draw, is_x_cost)
 
            card["synergy"] = synergy_data 
            
            new_cards.append(card)
            
        raw_data["cards"] = new_cards
        
        with open('tagged_items.json', "w", encoding="utf-8") as f:
            json.dump(raw_data, f, indent=4, ensure_ascii=False)
            
        print("✅ 성공!  'tagged_items.json'이 탄생했습니다.")
        
    except FileNotFoundError:
        print("🚨 에러: items.json 파일을 찾을 수 없습니다.")

if __name__ == "__main__":
    create_ultimate_database()