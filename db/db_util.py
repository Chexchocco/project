import json
import copy
import sys

CARD_DB = {}
RELIC_DB = {}
POTION_DB = {}
KEYWORD_DB = {}

def load_database(DB_PATH): # 💡 파일명 변경
    global CARD_DB, RELIC_DB, POTION_DB, KEYWORD_DB
    
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: {DB_PATH} not found.", file=sys.stderr)
        return False
        
    for card in raw_data.get("cards", []):
        card_name_lower = card.get("name", "").lower()
        
        if card_name_lower in {"strike", "defend", "strike+", "defend+"}:
            if card.get("color") != "Red":
                continue
                
        CARD_DB[card_name_lower] = card
        
    return True

def get_card_info(name):
    """
    카드 이름을 받아 DB에서 정보를 깊은 복사하여 반환합니다.
    (깊은 복사를 통해 module.py에서 값을 수정해도 원본 DB가 오염되지 않음)
    """
    name_lower = name.lower()
    if name_lower in CARD_DB:
        return copy.deepcopy(CARD_DB[name_lower])
    return None