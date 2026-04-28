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

def get_card_info(card_data):

    if isinstance(card_data, str):
        lookup_name = card_data
        
    elif isinstance(card_data, dict):
        name = card_data.get("name", "")
        upgrades = card_data.get("upgrades", 0)
        lookup_name = name
        if upgrades > 0 and not lookup_name.endswith("+"):
            lookup_name += "+"
    else:
        return None

    name_lower = lookup_name.lower()
    if name_lower in CARD_DB:
        return copy.deepcopy(CARD_DB[name_lower])
    return None