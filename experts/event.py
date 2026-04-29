import json
import re
import logging

import ollama

log = logging.getLogger("STS_AI")

# Event spoiler DB is loaded once at import time.
EVENT_SPOILER_DB = {}

try:
    with open('db/eventDB.json', "r", encoding="utf-8") as f:
        EVENT_SPOILER_DB = json.load(f)
    log.info(f"✅ 이벤트 스포일러 DB 로드 완료! ({len(EVENT_SPOILER_DB)}개 이벤트)")
except FileNotFoundError:
    log.warning("🚨 eventDB.json 파일을 찾을 수 없습니다. (스포일러 없이 진행)")


def evaluate_card_for_match(card_id):
    """
    [임시 카드 평가 함수]
    카드가 덱에 필요한지(GOOD), 피해야 하는 저주/쓰레기인지(BAD) 평가합니다.
    나중에 LLM RAG 등을 연결해서 덱 시너지 기반으로 고도화할 수 있습니다.
    """
    if card_id is None:
        return "UNKNOWN"

    # 슬더스의 대표적인 저주 카드들 (필요에 따라 추가)
    bad_cards = ["CurseOfTheBell", "AscendersBane", "Necronomicurse",
                 "Normality", "Pain", "Regret", "Doubt", "Decay", "Writhe", "Shame", "Injury"]

    # 카드 ID에 Curse가 포함되어 있거나, 나쁜 카드 목록에 있으면 무조건 회피
    if "Curse" in card_id or card_id in bad_cards:
        return "BAD"

    # 그 외의 카드는 일단 먹을 가치가 있다고 판단
    return "GOOD"


def match_and_keep_expert(available_commands, choices):
    """
커뮤니케이션 모드의 미구현으로 간략하게만 구현...
    """
    if "proceed" in available_commands:
        log.info("🚪 짝맞추기 이벤트 완료! 진행(proceed)합니다.")
        print("proceed", flush=True)
        return

    elif "leave" in available_commands:
        log.info("🚪 짝맞추기 이벤트 완료! 나갑니다(leave).")
        print("leave", flush=True)
        return

    if "choose" in available_commands and choices:
        raw_pick = choices[0]

        # "card5" 같은 문자열에서 숫자만 추출
        match = re.search(r'\d+', str(raw_pick))
        if match:
            pick_index = match.group()
        else:
            pick_index = raw_pick

        print(f"choose {pick_index}", flush=True)
        return  # 🚨 여기서도 return!

    # 💡 3순위: 카드가 뒤집히는 애니메이션 중이거나 할 게 없을 땐 대기!
    print("wait 30", flush=True)
    return


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
        response = ollama.chat(model='my_sts_qwen', messages=[{'role': 'user', 'content': prompt}])
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


def handle_event(state, avail):
    log.info("❓ 이벤트 에이전트 가동 (LLM 호출)")

    player_hp = state.get("current_hp", 0)
    max_hp = state.get("max_hp", 80)
    gold = state.get("gold", 0)

    event_name = state.get("screen_state", {}).get("event_name", "Unknown")
    choice_list = state.get("choice_list", "")
    if(len(choice_list) == 1) :
        log.info(f"옵션하나니까 바로선택 {choice_list[0]}")
        print(f"choose {choice_list[0]}", flush=True)
        return

    else :
        body_text = state.get("screen_state", {}).get("body_text", "")
        options = state.get("screen_state", {}).get("options", [])
        if event_name == "Match and Keep!":
            log.info("🃏 짝맞추기 에이전트 가동")
            choices = state.get("choice_list", [])
            match_and_keep_expert(avail, choices)
            return
        else:
            log.info(f"❓ LLM 이벤트 전문가 호출: {event_name}")
            options_text = state.get("screen_state", {}).get("options", [])


            # 덱 프로필 (나중에 만드실 함수, 지금은 임시 문자열)
            deck_profile = "Balanced deck with 20 cards."

            choice_idx = evaluate_event(event_name, options_text, player_hp, max_hp, gold, deck_profile)
            print(f"choose {choice_idx}", flush=True)
            return
