import json
import re
import sys
import copy

# 전역 변수로 DB 캐싱 (메모리에 한 번만 로드)
CARD_DB = {}
RELIC_DB = {}
POTION_DB = {}
KEYWORD_DB = {}

def load_database(db_path='items.json'):
    global CARD_DB, RELIC_DB, POTION_DB, KEYWORD_DB
    
    try:
        with open(db_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            
        basic_cards = {"strike", "defend", "strike+", "defend+"}
        
        # 타수는 모든 직업이 중복으로 갖고 있어서 별도로 제외
        # 근데 다른 직업(color) 카드를 뜨게하는 유물도 있으니까
        # 일~단 다 받아두는걸로
        
        for card in raw_data.get("cards", []):
            card_name_lower = card.get("name", "").lower()
            
            # 타수는 빨강(아클 카드)만 받도록
            if card_name_lower in basic_cards:
                if card.get("color") != "Red":
                    continue
                
            desc = card.get("description", "")
            
            card["parsed"] = {
                "damage": 0,
                "hits": 1,
                "block": 0,
                "effects": {}
            }
            
            # (이하 기존 정규식 파싱 로직 동일)
            multi_hit_match = re.search(r"Deal (\d+) damage (\d+) times", desc)
            if multi_hit_match:
                card["parsed"]["damage"] = int(multi_hit_match.group(1))
                card["parsed"]["hits"] = int(multi_hit_match.group(2))
            else:
                dmg_match = re.search(r"Deal (\d+) damage", desc)
                if dmg_match: 
                    card["parsed"]["damage"] = int(dmg_match.group(1))
                    
            blk_match = re.search(r"Gain (\d+) Block", desc)
            if blk_match: 
                card["parsed"]["block"] = int(blk_match.group(1))
                
            vuln_match = re.search(r"Apply (\d+) Vulnerable", desc)
            if vuln_match: card["parsed"]["effects"]["Vulnerable"] = int(vuln_match.group(1))
                
            weak_match = re.search(r"Apply (\d+) Weak", desc)
            if weak_match: card["parsed"]["effects"]["Weak"] = int(weak_match.group(1))
                
            str_match = re.search(r"Gain (\d+) Strength", desc)
            if str_match: card["parsed"]["effects"]["Strength"] = int(str_match.group(1))
            
            # O(1) 검색을 위한 소문자 키 저장
            CARD_DB[card_name_lower] = card
            
        # 2. 유물, 포션, 키워드 저장 (기존과 동일)
        for relic in raw_data.get("relics", []):
            RELIC_DB[relic["name"].lower()] = relic
            
        for potion in raw_data.get("potions", []):
            POTION_DB[potion["name"].lower()] = potion
            
        for kw in raw_data.get("keywords", []):
            KEYWORD_DB[kw["name"].lower()] = kw
            
        print(f"✅ 로컬 DB 로딩 완료! (카드: {len(CARD_DB)}개, 유물: {len(RELIC_DB)}개)", flush=True)
        return True
        
    except FileNotFoundError:
        print(f"🚨 에러: {db_path} 파일을 찾을 수 없습니다")
        return False
    except Exception as e:
        print(f"🚨 DB 파싱 중 에러 발생: {e}")
        return False
    
def get_card_info(card_name):
    """
    이름으로 카드를 검색하여 반환합니다.
    원본 DB 오염을 막기 위해 깊은 복사(deepcopy)본을 반환합니다.
    """
    if not card_name:
        return None
        
    # 소문자 및 공백 제거로 검색
    target_name = card_name.strip().lower()
    card = CARD_DB.get(target_name)
    
    if card:
        return copy.deepcopy(card)
    else:
        # 디버깅용 출력
        print(f"[Warning] DB에서 '{card_name}' 카드를 찾을 수 없습니다.")
        return None