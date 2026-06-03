import json
import re
import logging
from collections import Counter

import ollama
from experts.synergy import score_deck_summary
from db.db_loader import get_card_info

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

    # [중요] Knowing Skull 특수 처리: 현재 체력을 고려해 2~3번만 선택
    if "Knowing Skull" in event_name:
        safe_threshold = 20  # eventDB 힌트에서 명시

        # 선택지에서 비용 추출 (예: "Gold (cost 2 HP)" → 2)
        costs = []
        for opt in (options_text if isinstance(options_text, list) else []):
            match = re.search(r'cost (\d+)', str(opt))
            if match:
                costs.append(int(match.group(1)))

        if costs:
            min_cost = min(costs)

            # 비용 증가 패턴: 1번 min_cost, 2번 min_cost+2, 3번 min_cost+4
            cost_1st = min_cost
            cost_2nd = min_cost + 2
            cost_3rd = min_cost + 4

            # 3번 선택 가능 여부 판단
            total_for_3 = cost_1st + cost_2nd + cost_3rd
            if hp - total_for_3 >= safe_threshold:
                log.info(f"🎯 Knowing Skull: HP {hp} → 3번 선택 가능 (총 {total_for_3} HP 소비)")
            # 2번 선택 가능 여부 판단
            elif hp - (cost_1st + cost_2nd) >= safe_threshold:
                log.info(f"🎯 Knowing Skull: HP {hp} → 2번 선택 가능 (총 {cost_1st + cost_2nd} HP 소비)")
            else:
                # 1번도 위험하면 Leave
                log.info(f"⚠️ Knowing Skull: HP {hp}가 너무 낮아 Leave 선택")
                for i, option in enumerate(options_text if isinstance(options_text, list) else []):
                    if "Leave" in str(option) or "leave" in str(option):
                        return i
                return len(options_text) - 1 if isinstance(options_text, list) else 0

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

    # Knowing Skull 특수 지침 추가
    if "Knowing Skull" in event_name:
        prompt += """
    [⚠️ KNOWING SKULL SPECIAL RULES]
    - This event allows multiple selections, but each selection increases the cost for the next one.
    - Example cost pattern: 1st (2 HP), 2nd (4 HP), 3rd (6 HP)...
    - You should LEAVE after selecting 2-3 times maximum to avoid dying.
    - If you must leave soon to stay safe above 20 HP, choose LEAVE.
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

            return int(parsed_data.get('choice') or 0)
        else:
            log.warning(f"⚠️ JSON 파싱 실패, 원본 텍스트: {result_text}")
            num_match = re.search(r'\d+', result_text)
            return int(num_match.group(0)) if num_match else 0

    except Exception as e:
        log.error(f"LLM 호출 중 에러 발생: {e}")
        return 0  # 에러가 나면 멈추지 않고 0번을 고르며 게임 속행


def handle_event(state, avail):
    # '?'방이 상점으로 판명되어 이벤트로 라우팅된 경우: 상점 구매 화면이면 상점 핸들러로 위임.
    # (라우터가 SHOP_SCREEN을 EVENT로 오인해도 카드를 정상 구매/퇴장하도록.)
    from experts.shop import _at_shop_screen, handle_shop_screen
    if _at_shop_screen(state):
        log.info("🛒 (이벤트 경로) 상점 구매 화면 감지 → 상점 핸들러로 위임")
        handle_shop_screen(state, avail)
        return

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
    if "choose" not in avail:
        if "proceed" in avail:
            log.info("🚪 이벤트 진행(proceed) 가능! 바로 진행합니다.")
            print("proceed", flush=True)
            return
        if "leave" in avail:
            log.info("🚪 이벤트 퇴장(leave) 가능! 바로 나갑니다.")
            print("leave", flush=True)
            return
        if "return" in avail:
            log.info("🚪 복귀(return) 가능! 바로 돌아갑니다.")
            print("return", flush=True)
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

            current_deck_raw = state.get("deck", [])
            enriched_deck = [info for c in current_deck_raw if (info := get_card_info(c))]
            # 2. synergy.py의 함수를 이용해 덱의 핵심 스탯을 뽑아냅니다.
            summary_data = score_deck_summary(enriched_deck)
            stats = summary_data.get("stats", {})
            deck_size = summary_data.get("deck_size", 0)

            # 3. 파이썬 Counter를 이용해 어떤 카드가 몇 장 있는지 요약합니다. (예: {'Strike': 5, 'Defend': 4})
            card_counts = dict(Counter(c.get("name", "Unknown") for c in enriched_deck))

            # 4. LLM이 읽기 좋게 문자열로 예쁘게 포장합니다.
            deck_profile = (
                f"Deck Size: {deck_size} cards\n"
                f"Stats: Avg Cost {stats.get('avg_cost', 0)}, "
                f"Dmg/Energy {stats.get('dmg_per_energy', 0)}, "
                f"Blk/Energy {stats.get('blk_per_energy', 0)}\n"
                f"Card List: {card_counts}"
            )

            choice_idx = evaluate_event(event_name, options_text, player_hp, max_hp, gold, deck_profile)
            print(f"choose {choice_idx}", flush=True)
            return
