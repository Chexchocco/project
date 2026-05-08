import ollama
import json
import re
import logging

from db.db_loader import get_card_info
from collections import Counter
log = logging.getLogger("STS_AI")
from config import MODEL_NAME
from temp_module import evaluate_deck_core, evaluate_deck_vectors

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
    enriched_deck = [get_card_info(c) for c in current_deck_raw if get_card_info(c)]
    deck_summary = summarize_card_list(current_deck_raw)
    core_report, _ = evaluate_deck_core(enriched_deck)
    synergy_report, boss_prompt = evaluate_deck_vectors(enriched_deck, state, enriched_relics)
    # 2. 보상 카드 포맷팅 (agent_hints 포함)
    reward_db_text = "[Offered Cards Info]\n"
    for i, card_dict in enumerate(offered_cards):
        info = get_card_info(card_dict)
        if info:
            desc = info.get('description', '').replace('\n', ' ')
            provides = info.get("synergy", {}).get("provides", {})
            requires = info.get("synergy", {}).get("requires", {})
            hint = info.get("agent_hints", "")
            
            reward_db_text += f"- Index [{i}]: {info['name']} (Cost: {info.get('cost')})\n"
            reward_db_text += f"  * Description: {desc}\n"
            if provides: reward_db_text += f"  * PROVIDES: {provides}\n"
            if requires: reward_db_text += f"  * REQUIRES: {requires}\n"
            if hint: reward_db_text += f"  * Hint: {hint}\n"

    # 3. 💡 막(Act)에 따른 다이나믹 전략 가이드라인 생성
    if act == 1:
        dynamic_strategy = """
- [ACT 1 CRITICAL RULE]: You are in the early game. DO NOT obsess over synergies. Your primary goal is to add RAW DAMAGE (high damage Attacks) to survive early Elites (Gremlin Nob, Lagavulin).
- DO NOT choose 'skip' easily. Unless all offered cards are absolute trash (like curses or highly conditional skills), you MUST pick the best standalone card.
- If a card offers immediate high damage or good block efficiency, pick it even if it does not solve [STARVING] synergies.
"""
    elif act == 2:
        dynamic_strategy = """
- [ACT 2 RULE]: You need strong AoE damage and scaling. Start prioritizing cards that fulfill your [STARVING] and [LACKING] synergies.
- You may consider 'skip' if the cards are weak and do not fit your deck's core engine, but do not skip if you desperately need AoE or Block.
"""
    else:
        dynamic_strategy = """
- [ACT 3/4 RULE]: Your deck engine should be mostly complete. Protect your deck's consistency.
- AGGRESSIVELY CHOOSE 'skip' unless a card perfectly satisfies a [STARVING]/[LACKING] synergy or is a massive upgrade. Avoid deck bloat at all costs.
"""

    # 4. LLM 프롬프트 조립
    prompt = f"""
{core_report}

{synergy_report}

{reward_db_text}

[Current Deck Summary]
{deck_summary}
Total Cards: {len(current_deck_raw)}

[Task]
You are a top-tier Slay the Spire AI player. Choose ONE card to add, or "skip".

[Strategy Guidelines]{dynamic_strategy}
- Never pick [OVERSATURATED] synergies.
- Read the 'Hint' of each card. If a card is known as a strong standalone card, value it highly.

Output EXACTLY in this JSON format strictly:
{{
    "reasoning": "Analyze the deck's needs based on the Act, Core Stats, and Synergy. Conclude why the choice or skip is optimal.",
    "choice": "Index number (0, 1, 2...), or 'skip'"
}}
"""

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
        log.info(f"🤖 LLM의 고민:\n{content}\n")

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
        "reasoning": "Explain in 1-2 sentences why this option is the best based on current HP, gold, and deck.",
        "choice": <integer_index>
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