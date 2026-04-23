import sys
import json
import traceback
import time
import os
import re
import module
from db import db_loader
import logging
from config import LOCAL_PATH, DB_PATH, LOG_PATH
from llm.llm_rag import choose_card_reward, evaluate_event

log = logging.getLogger("STS_AI")
log.setLevel(logging.INFO)
file_handler = logging.FileHandler(LOG_PATH, mode='a', encoding='utf-8')
console_handler = logging.StreamHandler(sys.stderr)

formatter = logging.Formatter('%(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

log.addHandler(file_handler)
log.addHandler(console_handler)

WAITING_FOR_SHOP = False
SHOP_DONE = False
CARD_SKIP = False
def battle_module(state, avail):
    combat = state["combat_state"]
    player = combat.get("player", {})
    hp = player.get("current_hp", 0)
    energy = player.get("energy", 0)
    hand = combat.get("hand", [])
    player_block = player.get("block", 0)
    
    log.info(f"⚔️ [전투] 체력: {hp} / 남은 에너지: {energy}")
    
    action_taken = False
    
    
    # 낼 수 있는 카드가 있고, 현재 명령 중에 'play'가 가능할 때
    if "play" in avail:
        monsters = combat.get("monsters", [])
        target_idx = 0
        target_monster = None
        for m_idx, m in enumerate(monsters):
            if not m.get("is_gone", False) and not m.get("half_dead", False) and m.get("current_hp", 0) > 0:
                target_idx = m_idx
                target_monster = m
                break
        
        next_card_idx = module.lethal_expert(hand, energy, target_monster)
        if next_card_idx != -1:
            log.info(f"[마무리] 최소 비용으로 적 처치: {next_card_idx+1}번째 카드")        

        
        if next_card_idx == -1 :    
            next_card_idx = module.defensive_expert(hand, energy, player_block,  monsters)
            #log.info(f"{next_card_idx+1}번째 카드로 방어하기")

        if next_card_idx == -1 :
            next_card_idx = module.max_damage_expert(hand, energy, monsters[target_idx])
            #log.info(f"{next_card_idx+1}번째 카드로 공격하기")
        
        if next_card_idx == -1 :
            pass
        else :# 💡 카드가 타겟팅이 필요한지(공격, 약화 등) 확인합니다!
            if hand[next_card_idx].get("has_target", False):
                print(f"play {next_card_idx+1} {target_idx}", flush=True)
            else:
                # 타겟이 필요 없는 카드(방어, 버프 등)는 대상 없이 카드 번호만 보냅니다.
                print(f"play {next_card_idx+1}", flush=True)
                
            action_taken = True
    # 카드를 냈다면 다음 턴 진행으로 넘어가고 루프 재시작
    if action_taken:
        return 0 
    
    # 낼 카드가 없다면 턴 종료
    if "end" in avail:
        print("end", flush=True)
        log.info(" 💤 턴 종료")
        return -1
    
    time.sleep(0.5)
    print("wait", flush=True)
    return 0




def main():
    
    global WAITING_FOR_SHOP, SHOP_DONE, CARD_SKIP
    log.info(f"🚨 현재 통신 모드가 훔쳐 쓰고 있는 파이썬 경로: {sys.executable}")
    print("ready", flush=True)  
    log.info("✅ 파이썬 에이전트 연결 완료!")

    db_loader.load_database(DB_PATH)
    while True:
        try:
            
            line = sys.stdin.readline()
            if not line:
                log.info("❌ 게임과 연결이 끊어졌습니다.")
                break
            
            line = line.strip()
            if not line:
                continue


            ###################
            data = json.loads(line)
            
            if "error" in data:
                # 💡 available_commands 대신 진짜 에러 내용을 뽑아냅니다!
                real_error = data.get("error", "알 수 없는 에러")
                
                log.error(f"⚠️ 엔진 에러 발생! 이유: {real_error}")
                
                # 핑퐁 복구를 위해 상태를 다시 요구합니다.
                print("wait 3000", flush=True) 
                continue
            
            if not data.get("in_game", False):
                available_cmds = data.get("available_commands", [])
                log.info("🏠 진행 중인 게임이 없습니다. 새로운 게임을 시작합니다.")
                print("start ironclad 0", flush=True) 
                # communication mod가 모든 상태를 주고 받게 해주는건 아니어서
                # 세이브 된 거 시작을 못함;;
                # 이게 좀 문제긴한데 만약 계속 돌려보고 싶으면 이렇게 돌리고 아니면 주석처리하고 킨담에
                # continue 눌러줘야함    
                # 루프를 넘겨서 게임이 켜진 후의 상태를 기다립니다.
                continue
                        
            
            if "game_state" in data:
                
                state = data["game_state"]
                # 💡 1. 현재 게임의 명시적 상태 변수들을 먼저 추출합니다.
                room_phase = state.get("room_phase", "")
                screen_type = state.get("screen_type", "")
                avail = data.get("available_commands", [])
                player_hp = state.get("current_hp", 0)
                max_hp = state.get("max_hp", 80)
                gold = state.get("gold", 0)

                if screen_type =="GAME_OVER" :
                    print(f"proceed", flush= True)
                    continue
                

                
                # [상황 A] 전투 중 (팝업 없고, room_phase가 COMBAT)
                if room_phase == "COMBAT" and screen_type == "NONE":
                    if "combat_state" in state:
                        #log.info(f"⚔️ [전투] 에이전트 가동)")
                        battle_module(state, avail)
                        continue
                  # [상황 B] 카드 보상 화면
                elif screen_type == "COMBAT_REWARD":
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
                        continue
                        
                    if "proceed" in avail:
                        
                        log.info("다 골랐으니 진행1")
                        print("proceed", flush=True)
                        continue
                elif screen_type == "CARD_REWARD":
                    log.info("🎁 [보상] 덱 빌딩 에이전트 가동")
                    current_deck = [c["name"] for c in state.get("deck", [])]
                    offered_cards = [c["name"] for c in state.get("screen_state", {}).get("cards", [])]
                    
                    choice = choose_card_reward(current_deck, offered_cards)
                    if(choice == "skip"):
                        log.info(f"skip 선택")
                        print(f"skip", flush = True)
                        CARD_SKIP= True
                        continue
                    else : 
                        log.info(f"{choice}번 카드 선택")
                        print(f"choose {choice}", flush=True)
                        continue
                elif screen_type == "GRID":
                    log.info("🗂️ 그리드(카드 선택) 화면 진입")
                    screen_state = state.get("screen_state", {})
                    grid_cards = screen_state.get("cards", [])
                    
                    for_upgrade = screen_state.get("for_upgrade", False)
                    for_purge = screen_state.get("for_purge", False)
                    for_transform = screen_state.get("for_transform", False)
                    

                    selected_cards = screen_state.get("selected_cards", [])
                    # -> 다른 거 누르지 말고 무조건 Confirm만 누르고 루프를 넘깁니다!
                    if "confirm" in avail:
                        print("confirm", flush=True)
                        continue
                    if for_upgrade:
                        log.info("🔨 [강화]할 카드를 고릅니다.")
                        #cmd = module.smith_expert(grid_cards)
                        cmd= (f"choose 0")
                        print(cmd, flush=True)
                        continue
                        
                    elif for_purge:
                        log.info("🗑️ [제거]할 카드를 고릅니다.")
                        # 타격(Strike) 1순위, 수비(Defend) 2순위로 지우는 로직
                        #cmd = module.purge_expert(grid_cards) 
                        cmd= (f"choose 0")
                        print(cmd, flush=True)
                        continue
                        
                    elif for_transform:
                        log.info("✨ [변화]시킬 카드를 고릅니다.")
                        # 타격/수비를 우선적으로 고르되, 제거 로직과 동일하게 써도 무방함
                        #cmd = module.purge_expert(grid_cards)
                        cmd= (f"choose 0")
                        print(cmd, flush=True)
                        continue
                        
                    # 4. 기타 (병 속의 번개, 도박꾼의 물약 등)
                    else:
                        log.info("❓ 기타 그리드 선택 (기본 1번 선택)")
                        print("choose 0", flush=True)
                        continue

                # [상황 C] 맵 이동 화면
                elif screen_type == "MAP":
                    log.info("🗺️ [이동] 맵 탐색 에이전트 가동")
                    WAITING_FOR_SHOP = False
                    SHOP_DONE = False
                    CARD_SKIP = False
                    choices = state.get("choice_list", [])
                    if choices:
                        print(f"choose {choices[0]}", flush=True)
                    else:
                        log.info("이동 오류?")
                        print("choose 0", flush=True)
                    continue
                    # 일단 멍청하게 구현
                    # run_map_routing()
                elif screen_type == "REST":
                    log.info("🔥 모닥불 에이전트 가동")
                    
                    if "choose" not in avail :
                       log.info("휴식 나가기")
                       print(f"proceed", flush=True)
                       continue
                        # 선택 다 한 상황이라 고를 게 없으면 넘기기
                    # 휴리스틱: 체력이 70% 이하면 무조건 휴식
                    # 일단 단순하게 구현;;
                    # 사실 옵션이 3개 다보니 llm 한테 물어봐도 되고
                    # 아니면
                    if player_hp < (max_hp * 0.7):
                        print(f"choose rest", flush=True)
                        continue
                                
                    else: #여기다 이제 need smith 판단 함수 넣든가말든가 혹은 이 전체적으로 llm에 넣거나
                        print(f"choose smith", flush=True)
                        continue
                    #else :
                    #    print(f"choose recall", flush=True)
                    #    continue
                    
                    #일단 멈춤 방지로 넣어둠
                    print(f"choose rest", flush=True)
                    continue
                    #    
                elif screen_type == "EVENT":
                    log.info("❓ 이벤트 에이전트 가동 (LLM 호출)")
                    
                    event_name = state.get("screen_state", {}).get("event_name", "Unknown")
                    choice_list = state.get("choice_list", "")
                    if(len(choice_list) == 1) :
                        log.info(f"옵션하나니까 바로선택 {choice_list[0]}")
                        print(f"choose {choice_list[0]}", flush=True)
                        continue

                    else :
                        body_text = state.get("screen_state", {}).get("body_text", "")
                        options = state.get("screen_state", {}).get("options", [])
                        if event_name == "Match and Keep!":
                            log.info("🃏 짝맞추기 에이전트 가동")
                            choices = state.get("choice_list", [])
                            module.match_and_keep_expert(avail, choices)
                            continue
                        else:
                            log.info(f"❓ LLM 이벤트 전문가 호출: {event_name}")
                            options_text = state.get("screen_state", {}).get("options", [])
                            
                            
                            # 덱 프로필 (나중에 만드실 함수, 지금은 임시 문자열)
                            deck_profile = "Balanced deck with 20 cards." 
                            
                            choice_idx = evaluate_event(event_name, options_text, player_hp, max_hp, gold, deck_profile)
                            print(f"choose {choice_idx}", flush=True)
                            continue

                    
                elif screen_type == "CHEST":

                    chest_open = state.get("screen_state", "").get("chest_open", [])
                    if(chest_open == True):
                        print(f"proceed", flush =True)
                        continue
                        #이게 보스 잡고 나서 갑자기 screen type 이 바뀜 그래서 그 경우 처리용 
                        
                    else :
                        log.info(f"상자 열기 : {chest_open}")
                        print(f"choose open", flush=True) 
                        continue
                    # 보물상자는 여는거말고 딱히 할 게 없어서?
                    # 굳이 따지면 보물상자 열 경우 패널티 생기는 저주 유물 먹은 경우인데 그건 나중에 고려
                    # 그거랑 이제 유물vs초록 키 도 고려사항인데 이것도 나중에 고려
                    # 열기만하면 이제 알아서 넘어가긴함 지금은... 그래서 추후에는 열고 나서 바로 여기 뒤에다가 
                    #붙여가지고 제어필요
                elif screen_type == "HAND_SELECT":
                    log.info("전투 중 패 선택(HAND_SELECT) 화면 진입")
                    screen_state = state.get("screen_state", {})
                    
                    selected_cards = screen_state.get("selected", [])
                    max_cards = screen_state.get("max_cards", 1)
                    
                    if len(selected_cards) >= max_cards:
                        log.info("✅ 패 선택 완료! Confirm을 누릅니다.")
                        print("confirm", flush=True)
                        continue
                        
                    # 2. 카드를 아직 덜 골랐을 때 고르는 로직
                    # (일단은 게임이 안 멈추고 계속 굴러가게 만드는 것이 목표이므로 무조건 0번을 고릅니다)
                    # 추후 '전장의 함성'이면 똥카드(상태이상/타격)를 고르고, '무장'이면 좋은 카드를 고르게 업그레이드 가능!
                    
                    log.info("👉 패에서 0번 카드를 선택합니다.")
                    print("choose 0", flush=True)
                    continue
                elif screen_type == "SHOP_ROOM":
                    if not WAITING_FOR_SHOP :
                        log.info("🛒 상점 주인에게 말을 겁니다.")
                        print("choose shop", flush=True)
                        WAITING_FOR_SHOP = True
                        continue
                    if SHOP_DONE :
                        print("proceed", flush=True)
                        continue
                elif screen_type == "SHOP_SCREEN":
                    log.info("💰 상점 화면 진입")
                    screen_state = state.get("screen_state", {})

                    # 상점에 나온 카드, 유물, 포션 목록
                    shop_cards = screen_state.get("cards", [])
                    shop_relics = screen_state.get("relics", [])

                    # [임시 로직] 돈이 되는 것 중 첫 번째 카드를 사고 바로 나가기
                    can_buy = False
                    for i, card in enumerate(shop_cards):
                        if gold >= card.get("price", 999):
                            log.info(f"💳 {card.get('name')} 카드를 구매합니다.")
                            print(f"choose {card.get('name')}", flush=True)
                            can_buy = True
                            break
                            
                    if not can_buy:
                        log.info("🚪 살 수 있는 게 없거나 이미 샀으므로 상점을 나갑니다.")
                        SHOP_DONE = True
                        print("leave", flush=True)
                        
                    continue     
                

                elif screen_type == "BOSS_REWARD":
                    
                    if "proceed" in avail:
                        log.info("🚪 보스 유물을 성공적으로 획득했습니다. 다음 막으로 이동합니다.")
                        print("proceed", flush=True)
                        continue
                        
                    if "choose" in avail:
                        log.info("👑 보스 유물 선택 화면 진입")
                        relics = state.get("screen_state", {}).get("relics", [])
                        
                        if relics:
                            relic_names = [r.get("name") for r in relics]
                            log.info(f" 보스 유물 후보: {relic_names}")
                            log.info(f"✅ 첫 번째 유물({relic_names[0]})을 선택합니다.")
                        
                        # [임시 로직] 무조건 첫 번째(0번) 유물을 고릅니다.
                        print("choose 0", flush=True)
                        continue
                        
                    log.info("⏳ 보스 유물 획득 처리 중... 대기합니다.")
                    print("wait 30", flush=True)
                    continue

                

                # [그 외 상황] 
                else:
                    log.info(f"대기 중... (phase: {room_phase}, screen: {screen_type})")
                    
                    print("wait 30", flush= True)
                    time.sleep(1.5)  
                
                log.info(f"문제 발생3 {data}")
                print("wait 30", flush= True)
                time.sleep(1.5)  

            
            
            log.info(f"문제 발생2 {data}")
            print("wait", flush= True)
            time.sleep(1.5)  

        except Exception as e:
            # 파이썬 코드가 죽었을 때 원인을 검은 터미널 창에 적나라하게 출력합니다.
            log.info("\n🚨 파이썬 스크립트에 치명적 에러 발생!")
            log.info(traceback.format_exc())
            # 에러가 나더라도 게임이 완전히 멈추지 않게 턴 종료를 억지로 쏴줍니다.
            #print("end", flush=True)


if __name__ == "__main__":
    
    main()