import json
import re
import copy
# ==========================================
# 💡 시너지 사전 (Synergy Dictionary)
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
    "perfected strike": {"requires": {"STRIKE_CARD": 2.5}, "provides": {"STRIKE_CARD": 1.0}},
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

def calculate_base_value(damage, block, hits, draw,is_x_cost, is_aoe, effects):
    """
    이건 좀 논란이 있긴한데 일단 대충 계산-> 아직 활용 안하는 값이긴해
    """
    multiplier = 2 if is_x_cost else (hits if isinstance(hits, int) else 0) # 💡 hit가 없으면 0으로 계산
    
    effect_val = 0
    if "vulnerable" in effects: effect_val += effects["vulnerable"] * 3.0  # 취약은 스택당 3점
    if "weak" in effects: effect_val += effects["weak"] * 4.0            # 약화는 생존력을 크게 높이므로 4점
    if "strength" in effects: effect_val += effects["strength"] * 5.0    # 근력은 영구 성장성이므로 5점
    if "dexterity" in effects: effect_val += effects["dexterity"] * 5.0  # 민첩도 영구 성장성이므로 5점
    if "poison" in effects: effect_val += effects["poison"] * 1.5        # 독은 누적 데미지이므로 스택당 1.5점

    dmg_val = (damage * multiplier) * (1.5 if is_aoe else 1.0) 
    
    value = dmg_val + block + (draw * 3.0)
    return value

def create_database():
    print("🔄  DB 변환 시작...")
    
    try:
        with open('items.json', "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            
        new_cards = []
        for card in raw_data.get("cards", []):
            name_lower = card.get("name", "").lower()
            name_lower = name_lower.rstrip("+")
            # 일단 강화카드도 같이 처리 <- 별로 안좋은 방법이긴해 
            
            desc = card.get("description", "").lower()

            damage, block, draw = 0, 0, 0
            hits = 0 
            is_x_cost = False
            is_aoe = False
            
            # (기존의 damage, block, hits, draw 파싱 로직 그대로 유지...)
            # ...
            
            # ==========================================
            # ☠️ 버프 & 디버프 (Effects) 정밀 파싱
            # ==========================================
            effects = {}
            
            # 1. 적에게 부여하는 디버프 (Apply X ...)
            for eff in ["vulnerable", "weak", "poison", "frail"]:
                match = re.search(rf"apply (\d+) {eff}", desc)
                if match:
                    effects[eff] = int(match.group(1))
                    
            # 2. 내가 얻는 버프 (Gain X ...)
            for eff in ["strength", "dexterity", "focus"]:
                match = re.search(rf"gain (\d+) {eff}", desc)
                if match:
                    effects[eff] = int(match.group(1))
                
                
            

            x_hit_match = re.search(r"deal (\d+) damage x times", desc)
            if x_hit_match:
                damage, hits, is_x_cost = int(x_hit_match.group(1)), "X", True
            else:
                multi_hit_match = re.search(r"deal (\d+) damage (\d+) times", desc)
                if multi_hit_match: damage, hits = int(multi_hit_match.group(1)), int(multi_hit_match.group(2))
                else:
                    dmg_match = re.search(r"deal (\d+) damage", desc)
                    if dmg_match: 
                        damage = int(dmg_match.group(1))
                        hits =1 
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

            


            # ==========================================
            # 시너지 및 JSON 재조립
            # ==========================================
            synergy_data = copy.deepcopy(SYNERGY_MAP.get(name_lower, {"requires": {}, "provides": {}}))
            
            
            override_val = synergy_data.pop("base_value_override", None)
            
            card["damage"] = damage
            card["hits"] = hits
            card["block"] = block
            card["draw"] = draw
            card["is_x_cost"] = is_x_cost
            card["is_aoe"] = is_aoe
            card["effects"] = effects  # 💡 이제 카드 JSON에 effects 객체가 당당히 들어갑니다!
            
            if override_val is not None:
                card["base_value"] = override_val
            else:
                card["base_value"] = calculate_base_value(damage, block, hits, draw, is_x_cost, is_aoe, effects)
                
            card["synergy"] = synergy_data 
            if "exhaust" in desc and "EXHAUST_EVENT" not in synergy_data["provides"]:
                synergy_data["provides"]["EXHAUST_EVENT"] = 1.0
            
            new_cards.append(card)
            
        raw_data["cards"] = new_cards
        
        with open('tagged_items.json', "w", encoding="utf-8") as f:
            json.dump(raw_data, f, indent=4, ensure_ascii=False)
            
        print("✅  'tagged_items.json' 생성.")
        
    except Exception as e:
        print(f"🚨 에러 발생: {e}")
if __name__ == "__main__":
    create_database()