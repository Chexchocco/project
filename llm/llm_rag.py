import ollama
import json
import re
import logging

from db.db_loader import get_card_info
from collections import Counter
log = logging.getLogger("STS_AI")
from config import MODEL_NAME
from functools import lru_cache
from config import LOCAL_PATH
from experts.synergy import SynergyManager, score_card, score_deck, score_deck_summary
import os

tag_db_path = os.path.join(LOCAL_PATH, "DB", "synergyTagDB.json")
value_config_path = os.path.join(LOCAL_PATH, "DB", "value_config.json")

with open(tag_db_path, "r", encoding="utf-8") as f:
    synergy_tag_db = json.load(f)

with open(value_config_path, "r", encoding="utf-8") as f:
    value_config = json.load(f)


SYNERGY_ENGINE = SynergyManager(value_config, synergy_tag_db)

# 💡 카드 선택 해설 전용 로거 생성
# ----------------------------------------------------
card_log = logging.getLogger("CARD_PICKER")
card_log.setLevel(logging.INFO)

# card_picks.txt 파일에만 따로 저장되도록 핸들러 설정
log_file_path = os.path.join(LOCAL_PATH, "card_picks.txt")
card_handler = logging.FileHandler(log_file_path, encoding="utf-8")
card_handler.setFormatter(logging.Formatter('%(asctime)s\n%(message)s\n' + '-'*50))

# 이 로그가 기존 전체 로그(agent log) 화면/파일에 중복으로 찍히는 걸 막음
card_log.propagate = False 
card_log.addHandler(card_handler)



@lru_cache(maxsize=128)
def _get_cached_deck_report(deck_tuple, relics_tuple):
    """
    동일한 덱/유물 상태에서 중복 계산을 막기 위해 결과를 캐싱합니다.
    인자로 리스트 대신 튜플을 받아야 캐싱이 작동합니다.
    """
    # 튜플을 다시 딕셔너리 형태의 리스트로 복원하여 엔진에 전달
    current_deck = [get_card_info({"name": name}) for name in deck_tuple]
    current_deck = [c for c in current_deck if c is not None] # 안전 장치
    
    current_relics = [{"id": r_id} for r_id in relics_tuple]
    return score_deck(current_deck, current_relics, [], {}, SYNERGY_ENGINE)



EVENT_SPOILER_DB = {}

try:
    with open('db/eventDB.json', "r", encoding="utf-8") as f:
        EVENT_SPOILER_DB = json.load(f)
    log.info(f"✅ 이벤트 스포일러 DB 로드 완료! ({len(EVENT_SPOILER_DB)}개 이벤트)")
except FileNotFoundError:
    log.warning("🚨 eventDB.json 파일을 찾을 수 없습니다. (스포일러 없이 진행)")

def summarize_card_list(raw_card_list):
    """
    나중에 다른데로 옮길거임
    """
    counts = Counter()
    
    for card_dict in raw_card_list:
        info = get_card_info(card_dict)
        if info:
            counts[info["name"]] += 1
        else:
            counts[card_dict.get("name", "Unknown")] += 1
            
    return dict(counts) 


def choose_card_reward(state, enriched_relics=None):
    if enriched_relics is None:
        enriched_relics = []
        
    offered_cards = state.get("screen_state", {}).get("cards", [])
    current_deck_raw = state.get("deck", [])
    
    # 💡 현재 위치 파악 (Act 1, 2, 3)
    act = state.get("act", 1)
    floor = state.get("floor", 1)

    # 1. 파이썬 평가 모듈 데이터 구성
    deck_tup = tuple(c.get('name') for c in current_deck_raw if isinstance(c, dict))
    relic_ids = [r.get('id') for r in enriched_relics if r.get('id')]
    relic_tup = tuple(relic_ids)

    deck_report = _get_cached_deck_report(deck_tup, relic_tup)
    act_strategy = value_config.get(f"Act_{act}", {}).get("act_base_modifiers", {})
    

    enriched_deck = [get_card_info(c) for c in current_deck_raw if get_card_info(c)]
    deck_summary = summarize_card_list(current_deck_raw)
    
    stats = deck_report.get('stats', {})
    density = deck_report.get('density_vector', {})
    meaningful_synergies = {k: round(v, 2) for k, v in density.items() if v > 0}
    # 2. 보상 카드 포맷팅 (agent_hints 포함)
    core_report = f"[Deck Core Stats]\nAvg Cost: {stats.get('avg_cost', 0)}\nDraw Ratio: {stats.get('draw_ratio', 0)}"

    reward_db_text = "[Offered Cards Info]\n"
    for i, card_dict in enumerate(offered_cards):
        info = get_card_info(card_dict)
        if info:
            score = score_card(info, deck_report, act_strategy, relic_ids, SYNERGY_ENGINE)


            desc = info.get('description', '').replace('\n', ' ')
            provides = info.get("synergy", {}).get("provides", {})
            requires = info.get("synergy", {}).get("requires", {})
            hint = info.get("agent_hints", "")
            
            reward_db_text += f"- Index [{i}]: {info['name']} (Cost: {info.get('cost')})\n"
            reward_db_text += f"  * Description: {desc} | Engine Score: {score}\n"
            if provides: reward_db_text += f"  * PROVIDES: {provides}\n"
            if requires: reward_db_text += f"  * REQUIRES: {requires}\n"
    # 3. 💡 막(Act)에 따른 다이나믹 전략 가이드라인 생성
    

    # 4. LLM 프롬프트 조립
    prompt = f"""
{core_report}

[Current Deck Synergies]
{meaningful_synergies}

{reward_db_text}

[Current Deck Summary]
{deck_summary}
Total Cards: {len(current_deck_raw)}

[Task]
You are a top-tier Slay the Spire AI player. Choose ONE card to add, or "skip".

[Strategy Guidelines]
- Never pick [OVERSATURATED] synergies.
- Read the 'Hint' of each card. If a card is known as a strong standalone card, value it highly.

[⚠️ CRITICAL AI RULE: Engine vs Tactical Reality]
- act_strategyThe 'Engine Score' provided for each card calculates pure mathematical synergy and long-term value.
- However, the Engine is BLIND to your immediate survival needs (e.g., current HP, upcoming Elite fights, need for immediate Block/AoE).
- DO NOT BLINDLY TRUST the highest Engine Score. You MUST override the Engine if a lower-scoring card is tactically necessary to survive the current Act.
- If the Engine Scores are similar, use the Card Description and Hints to make a nuanced human-like decision.
- If you choose a lower-scoring card for tactical reasons, explicitly acknowledge that its score is lower. DO NOT hallucinate or misrepresent the mathematical scores to justify your choice.
Output EXACTLY in this JSON format strictly:
{{
    "reasoning": "Discuss the Engine Score vs Tactical Needs. Justify why you chose this card or skipped.",
    "choice": "Index number (0, 1, 2...), or 'skip'"
}}
"""
# 이게 막 별 요구치 모디파이어를 넣어서 그거 반영해서
# 넣는게 깔끔하지 않나 싶기도 하고... 
# 지금 생각은 현재 생존률 구현 후
# 막별 덱 스탯+시너지 요구치를 
# 전체 = 현재 막 + (생존률) * 다음 막의 평균 요구치 + (생존률 ^2) * 다다음막의 평균 요구치 + ~~ 이렇게 해서 구하는건 어떤가 싶긴함
# 근데 일단 이건 나중에




    log.info(f"덱 빌딩 전략 구상 중... (Act {act} 맞춤형)\n" + "="*50)

    # 5. LLM 호출 및 파싱 (기존과 동일)
    try:
        response = ollama.chat(
            model=MODEL_NAME, 
            messages=[
                {'role': 'system', 'content': 'You are a master Slay the Spire deck-builder.'},
                {'role': 'user', 'content': prompt}
            ],
            options={'temperature': 0.1, 'num_predict': 300}
        )

        content = response['message']['content'].strip()
        card_log.info(f"현재 reward_db_text:\n{reward_db_text}\n")
        card_log.info(f"현재 deck_summary:\n{deck_summary}\n")
        card_log.info(f"현재 density:\n{density}\n")
        card_log.info(f"🤖 LLM의 고민:\n{content}\n")

        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            parsed_data = json.loads(json_match.group(0))
            choice = str(parsed_data.get('choice', 'skip')).strip().lower()
            
            if choice == "skip": return "skip"
            elif choice.isdigit() and 0 <= int(choice) < len(offered_cards): return choice
            else: return "skip"
        else:
            return "skip"
            
    except Exception as e:
        log.error(f"LLM 에러: {e}")
        return "skip"


def evaluate_event(event_name, options_text, hp, max_hp, gold, deck_profile):
    """
    이벤트 이름과 현재 상태를 받아 최적의 선택지 인덱스(0, 1, 2...)를 반환합니다.
    """
    # 1. 스포일러 탐색 (부분 일치)
    spoiler_info = None
    for key, val in EVENT_SPOILER_DB.items():
        if key in event_name:
            spoiler_info = val
            break
            
    # 2. 프롬프트 생성
    
    prompt = f"""
    You are a top-tier Slay the Spire AI player.

    [Current State]
    - HP: {hp}/{max_hp}
    - Gold: {gold}
    - Deck Summary: {deck_profile}

    [Event Info]
    - Name: {event_name}
    - Available Options: {options_text}
    """
    # 스포일러가 있으면 추가
    if spoiler_info:
        prompt += f"""
    [⚠️ CRITICAL SPOILER/HINT for this event]
    - Mechanics: {spoiler_info.get('spoiler', '')}
    - Strategy: {spoiler_info.get('hint', '')}
    """

    prompt += """
    Based on the information, decide the best option.
    You MUST output your response in the following JSON format strictly. Do not add markdown or other text outside the JSON.
    {
        "score_analysis": "List the exact Engine Score for each card. Acknowledge if any score is negative or surprisingly high.",
        "mechanic_analysis": "Briefly state what the card ACTUALLY does based ONLY on its tags (e.g., 'Provides CARD_DRAW and EXHAUST_SYNERGY'). DO NOT invent damage values or features.",
        "reasoning": "Explain your choice. You must logically connect the card's mechanics to the current Act's strategy.",
        "choice": "Index number (0, 1, 2...), or 'skip'"
    }
    """
    
    try:
        response = ollama.chat(model=MODEL_NAME, messages=[{'role': 'user', 'content': prompt}])
        result_text = response['message']['content'].strip()
        
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        
        if json_match:
            parsed_data = json.loads(json_match.group(0))
            
            log.info(f"🤖 LLM의 생각: {parsed_data.get('reasoning', '이유 없음')}")
            
            return int(parsed_data.get('choice', 0))
        else:
            log.warning(f"⚠️ JSON 파싱 실패, 원본 텍스트: {result_text}")
            num_match = re.search(r'\d+', result_text)
            return int(num_match.group(0)) if num_match else 0
            
    except Exception as e:
        log.error(f"LLM 호출 중 에러 발생: {e}")
        return 0  # 에러가 나면 멈추지 않고 0번을 고르며 게임 속행