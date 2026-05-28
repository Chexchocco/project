import re
import logging

import ollama
import os
from experts.synergy import SynergyManager, score_card, score_deck, score_deck_summary, build_future_sight_strategy, calculate_deck_avg_score
import json
from functools import lru_cache
from db.db_loader import get_card_info
from config import MODEL_NAME
from collections import Counter

log = logging.getLogger("STS_AI")

CARD_SKIP = False

from config import LOCAL_PATH
tag_db_path = os.path.join(LOCAL_PATH, "db", "synergyTagDB.json")
value_config_path = os.path.join(LOCAL_PATH, "db", "value_config.json")

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
    boss_name = state.get("boss", "")
    # 1. 파이썬 평가 모듈 데이터 구성
    deck_tup = tuple(c.get('name') for c in current_deck_raw if isinstance(c, dict))
    relic_ids = [r.get('id') for r in enriched_relics if r.get('id')]
    relic_tup = tuple(relic_ids)

    deck_report = _get_cached_deck_report(deck_tup, relic_tup)
    
    base_act_strategy = build_future_sight_strategy(value_config, act, boss_name, 0.0)

    # 2단계: 현재 내 덱의 찐 파워(avg_score) 측정
    deck_score = calculate_deck_avg_score(current_deck_raw, deck_report, base_act_strategy, SYNERGY_ENGINE)

    # 3단계: 내 덱 파워를 기반으로 미래 가중치가 섞인 '최종 픽용 전략' 생성!
    final_pick_strategy = build_future_sight_strategy(value_config, act, boss_name, deck_score)
    # =========================================================================



    enriched_deck = [get_card_info(c) for c in current_deck_raw if get_card_info(c)]
    deck_summary = summarize_card_list(current_deck_raw)
    
    stats = deck_report.get('stats', {})
    density = deck_report.get('density_vector', {})
    meaningful_synergies = {k: round(v, 2) for k, v in density.items() if v > 0}
    
    # 2. 보상 카드 포맷팅 (agent_hints 포함)
    # 💡 LLM도 현재 덱의 파워를 알 수 있게 텍스트에 추가!
    core_report = f"[Deck Core Stats]\nAvg Cost: {stats.get('avg_cost', 0)}\nDraw Ratio: {stats.get('draw_ratio', 0)}\nCurrent Deck Power Score: {deck_score:.2f}"

    reward_db_text = "[Offered Cards Info]\n"
    for i, card_dict in enumerate(offered_cards):
        info = get_card_info(card_dict)
        if info:
            # 💡 예전 act_strategy 대신 final_pick_strategy를 엔진에 넘겨줍니다!
            score = score_card(info, deck_report, final_pick_strategy, relic_ids, SYNERGY_ENGINE,is_deck_eval=False)

            desc = info.get('description', '').replace('\n', ' ')
            provides = info.get("synergy", {}).get("provides", {})
            requires = info.get("synergy", {}).get("requires", {})
            hint = info.get("agent_hints", "")
            
            reward_db_text += f"- Index [{i}]: {info.get('name', 'Unknown')} (Cost: {info.get('cost')})\n"
            reward_db_text += f"  * Description: {desc} | Engine Score: {score}\n"
            if provides: reward_db_text += f"  * PROVIDES: {provides}\n"
            if requires: reward_db_text += f"  * REQUIRES: {requires}\n"
            if hint: reward_db_text += f"  * Hint: {hint}\n"

    # 4. LLM 프롬프트 조립 (JSON 4단 분리 적용)
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
- [CRITICAL] The 'Engine Score' ALREADY calculates all long-term scaling, macro-strategy, and card drawbacks. TRUST THE ENGINE. 
- DO NOT skip a high-scoring card (>15.0) just because its Description has a negative effect (e.g., Exhaust, Cannot draw more cards). The Engine has already accounted for it.
- In Act 1, choosing 'skip' is almost always a BAD idea unless all offered cards score below 10.0.
- Carefully read the PROVIDES and REQUIRES for the EXACT card you are evaluating. DO NOT mix up the tags of different cards.

[⚠️ CRITICAL AI RULE]
- You suffer from "Tunnel Vision" where you only look at one card and ignore the others. To fix this, you MUST list ALL offered cards in the 'all_cards_analysis' field before deciding.
- Base your understanding of the card STRICTLY on the provided 'Description' and 'PROVIDES/REQUIRES' tags.
- Output EXACTLY in this JSON format strictly:
{{
    "all_cards_analysis": "List the names and Engine Scores of ALL offered cards. (e.g., '0: CardA (15.0), 1: CardB (2.0), 2: CardC (-5.0)')",
    "highest_score_card": "Explicitly write the index, name, and score of the card with the mathematically HIGHEST Engine Score.",
    "reasoning": "Explain why you are choosing your card. You MUST choose the card you listed in 'highest_score_card' UNLESS you desperately need a lower-scoring card for immediate survival.",
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
