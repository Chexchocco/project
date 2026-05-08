import re
import logging

import ollama

from db.db_loader import get_card_info
from config import MODEL_NAME
from collections import Counter

log = logging.getLogger("STS_AI")

CARD_SKIP = False


def summarize_card_list(raw_card_list):
    counts = Counter()

    for card_dict in raw_card_list:
        info = get_card_info(card_dict)
        if info:
            counts[info["name"]] += 1
        else:
            counts[card_dict.get("name", "Unknown")] += 1

    return dict(counts)

def choose_card_reward(state):
    """
    현재 덱과 보상 카드들을 보고 LLM이 하나를 선택하는 함수
    :param current_deck: list of strings (e.g., ["Strike", "Strike", "Bash", "Cleave"])
    :param offered_cards: list of strings (e.g., ["Pommel Strike", "Shrug It Off", "Clash"])
    :return: 선택된 카드 이름 또는 "Skip"
    """

    offered_cards = state.get("screen_state", {}).get("cards", [])
    current_deck = state.get("deck", [])

    deck_summary = summarize_card_list(current_deck)
    # 2. RAG: 보상으로 나온 카드들의 정확한 스펙만 DB에서 추출
    reward_db_text = "[Offered Cards Info]\n"
    for card_dict in offered_cards:
        info = get_card_info(card_dict)
        if info:
            desc = info.get('description', '').replace('\n', ' ')
            reward_db_text += f"- {info['name']} (Type: {info.get('type')}, Cost: {info.get('cost')}): {desc}\n"

    # 3. 덱 빌딩 전용 프롬프트 (시너지와 덱 압축을 고려하도록 CoT 강제)
    prompt = f"""
    {reward_db_text}

    [Current Deck]
    {deck_summary}
    Total Cards: {len(current_deck)}

    [Task]
    You are an expert Slay the Spire player.
    You are offered a card reward.
    You must choose ONE card to add to your deck, or choose "Skip" if none of the cards improve your deck.

    [Strategy Guidelines]
    - Do not bloat the deck if the deck is strong enough. If the offered cards do not synergize with the [Current Deck] or are just bad, choosing "Skip" is often the best play.
    - Consider what your deck lacks (e.g., AoE damage, scaling, card draw, or defense).
    - In Act1, since default cards(strike and defend) are very low value, you need to pick good stuff cards
    - After you get important cards(rare and uncommon card that has strong impact), you should plan your deck's key strategy and pick cards according to the plan afterward.

    Output EXACTLY in this format:
    Reasoning:
    1. Deck Needs: Briefly state what the Current Deck lacks.
    2. Synergy Analysis: Evaluate the offered cards against the deck.
    3. Conclusion: Why the selected option is the best.
    Selected Option: [Index number from Offered Cards(0, 1, 2...), or "skip"]
    """

    log.info("덱 빌딩 전략을 구상하는 중...\n" + "="*50)

    # 4. LLM 호출 (객관적 판단을 위해 창의성 0)
    response = ollama.chat(
        model=MODEL_NAME,
        messages=[
            {'role': 'system', 'content': 'You are a master Slay the Spire deck-builder.'},
            {'role': 'user', 'content': prompt}
        ],
        options={'temperature': 0.0, 'num_predict': 256}
    )

    content = response['message']['content']
    log.info(f"🤖 LLM의 고민:\n{content.strip()}\n")

    # 5. 파싱
    match = re.search(r"Selected Option:\s*(.+)", content, re.IGNORECASE)
    if match:
        selected_option = match.group(1).strip()

        # 유효성 검사
        if selected_option.lower() == "skip":
            return "skip"

        return selected_option

        log.info(f"🚨 LLM이 선택지에 없는 카드({selected_option})를 골랐습니다. 안전을 위해 Skip 처리합니다.")
        return "skip"
    else:
        log.info("🚨 파싱 실패. 안전을 위해 Skip 처리합니다.")
        return "skip"


def handle_combat_reward(state, avail):
    global CARD_SKIP
    CARD_SKIP = False  # 새 전투 보상 진입 — 이전 스킵 상태 리셋
    log.info("🎁 전투 보상 챙기기")
    rewards = state.get("screen_state", {}).get("rewards", [])
    potions = state.get("potions", [])
    has_empty_potion_slot = any(p.get("id") == "Potion Slot" for p in potions)
    picked_something = False
    for i, reward in enumerate(rewards):
        r_type = reward.get("reward_type", "")
        if r_type in ["GOLD", "STOLEN_GOLD", "RELIC", "EMERALD_KEY"]:
            print(f"choose {i}", flush=True)
            picked_something = True
            break

        elif r_type == "POTION":
            if has_empty_potion_slot:
                print(f"choose {i}", flush=True)
                picked_something = True
                break
            else:
                # 꽉 찼으면 로그만 띄우고 무시 (다음 보상 탐색)
                log.info("🧪 포션 가방이 꽉 차서 스킵합니다")

        elif r_type == "CARD" and CARD_SKIP == False:
            print(f"choose {i}", flush=True)
            picked_something = True
            break

    if picked_something:
        return

    if "proceed" in avail:
        log.info("다 골랐으니 진행1")
        print("proceed", flush=True)
        return


def handle_card_reward(state, avail):
    global CARD_SKIP
    choice = choose_card_reward(state)
    if(choice == "skip"):
        log.info(f"skip 선택")
        print(f"skip", flush = True)
        CARD_SKIP= True
        return
    else :
        log.info(f"{choice}번 카드 선택")
        print(f"choose {choice}", flush=True)
        return


def handle_grid_selection(state, avail):
    log.info("🗂️ 그리드(카드 선택) 화면 진입")
    screen_state = state.get("screen_state", {})
    grid_cards = screen_state.get("cards", [])
    selected_cards = screen_state.get("selected_cards", [])
    num_cards = screen_state.get("num_cards", 1)

    # 🚨 1. [비상 방어선] 게임이 choose를 차단했는가? (애니메이션 중이거나, 선택이 완료된 직후)
    if "choose" not in avail:
        if "confirm" in avail:
            log.info("✅ 카드 선택 완료 (choose 비활성화됨). Confirm 실행!")
            print("confirm", flush=True)
        else:
            # 카드가 날아가는 애니메이션 중이거나 서버 틱 대기 중
            log.info("⏳ 화면 전환 또는 애니메이션 대기 중...")
        return

    # 2. [목표 달성 체크] (기존 로직 유지)
    if len(selected_cards) >= num_cards:
        if "confirm" in avail:
            log.info(f"✅ 목표치({num_cards}장) 선택 완료. Confirm 실행!")
            print("confirm", flush=True)
        else:
            log.info("⏳ Confirm 버튼 활성화를 대기 중입니다...")
        return

    # 3. [아직 목표 장수를 못 채웠을 때] 카드 선택
    target_index = len(selected_cards)

    if target_index >= len(grid_cards):
        log.error("❌ 선택할 수 있는 카드보다 목표 장수가 더 큽니다. 에러 방지.")
        return

    for_upgrade = screen_state.get("for_upgrade", False)
    for_purge = screen_state.get("for_purge", False)
    for_transform = screen_state.get("for_transform", False)

    if for_upgrade:
        log.info(f"🔨 [강화]할 카드를 고릅니다. ({target_index + 1}/{num_cards} 번째)")
    elif for_purge:
        log.info(f"🗑️ [제거]할 카드를 고릅니다. ({target_index + 1}/{num_cards} 번째)")
    elif for_transform:
        log.info(f"✨ [변화]시킬 카드를 고릅니다. ({target_index + 1}/{num_cards} 번째)")
    else:
        log.info(f"❓ 이벤트/다중 선택 카드를 고릅니다. ({target_index + 1}/{num_cards} 번째)")

    cmd = f"choose {target_index}"
    log.info(f"👉 명령어 전송: {cmd}")
    print(cmd, flush=True)
    return


def handle_chest(state, avail):
    chest_open = state.get("screen_state", "").get("chest_open", [])
    if(chest_open == True):
        print(f"proceed", flush =True)
        return
        #이게 보스 잡고 나서 갑자기 screen type 이 바뀜 그래서 그 경우 처리용

    else :
        log.info(f"상자 열기 : {chest_open}")
        print(f"choose open", flush=True)
        return
    # 보물상자는 여는거말고 딱히 할 게 없어서?
    # 굳이 따지면 보물상자 열 경우 패널티 생기는 저주 유물 먹은 경우인데 그건 나중에 고려
    # 그거랑 이제 유물vs초록 키 도 고려사항인데 이것도 나중에 고려
    # 열기만하면 이제 알아서 넘어가긴함 지금은... 그래서 추후에는 열고 나서 바로 여기 뒤에다가
    #붙여가지고 제어필요


def handle_boss_reward(state, avail):
    if "proceed" in avail:
        log.info("🚪 보스 유물을 성공적으로 획득했습니다. 다음 막으로 이동합니다.")
        print("proceed", flush=True)
        return

    if "choose" in avail:
        log.info("👑 보스 유물 선택 화면 진입")
        relics = state.get("screen_state", {}).get("relics", [])

        if relics:
            relic_names = [r.get("name") for r in relics]
            log.info(f" 보스 유물 후보: {relic_names}")
            log.info(f"✅ 첫 번째 유물({relic_names[0]})을 선택합니다.")

        # [임시 로직] 무조건 첫 번째(0번) 유물을 고릅니다.
        print("choose 0", flush=True)
        return

    log.info("⏳ 보스 유물 획득 처리 중... 대기합니다.")
    print("wait 30", flush=True)
    return
