import sys
import json
import traceback
import time
import os
import re
import module
import db_util
import logging
from config import LOCAL_PATH, DB_PATH, LOG_PATH
from llm_rag import choose_card_reward , evaluate_event

log = logging.getLogger("STS_AI")
log.setLevel(logging.INFO)
file_handler = logging.FileHandler(LOG_PATH, mode='a', encoding='utf-8')
console_handler = logging.StreamHandler(sys.stderr)

formatter = logging.Formatter('%(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

log.addHandler(file_handler)
log.addHandler(console_handler)


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
            log.info(f"{next_card_idx+1}번째 카드로 방어하기")

        if next_card_idx == -1 :
            next_card_idx = module.max_damage_expert(hand, energy, monsters[target_idx])
            log.info(f"{next_card_idx+1}번째 카드로 공격하기")
        
        if next_card_idx == -1 :
            pass
        else :
            print(f"play {next_card_idx+1} {target_idx}", flush=True)
            action_taken = True
    # 카드를 냈다면 다음 턴 진행으로 넘어가고 루프 재시작
    if action_taken:
        return 0 
    
    # 낼 카드가 없다면 턴 종료
    if "end" in avail:
        #print("end", flush=True)
        log.info(" 💤 턴 종료")
        return -1
    elif "proceed" in avail:
        # print("proceed", flush=True)
        log.info(" 💤 턴 종료")
        return -1




def main():
    log.info(f"🚨 현재 통신 모드가 훔쳐 쓰고 있는 파이썬 경로: {sys.executable}")
    print("ready", flush=True)  
    log.info("✅ 파이썬 에이전트 연결 완료!")

    db_util.load_database(DB_PATH)
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
                log.info(f"⚠️ 엔진 에러: {data['error']}")
                time.sleep(10)
                continue
            
                
            
            if "game_state" in data:
                state = data["game_state"]
                
                # 💡 1. 현재 게임의 명시적 상태 변수들을 먼저 추출합니다.
                room_phase = state.get("room_phase", "")
                screen_type = state.get("screen_type", "")
                avail = data.get("available_commands", [])
                player_hp = state.get("current_hp", 0)
                max_hp = state.get("max_hp", 80)
                gold = state.get("core", {}).get("gold", 0)
                
                log.info(f"room phase {room_phase}")
                log.info(f"room phase {player_hp}")
                log.info(f"room phase {max_hp}")
                # [상황 A] 전투 중 (팝업 없고, room_phase가 COMBAT)
                if room_phase == "COMBAT" and screen_type == "NONE":
                    if "combat_state" in state:
                        log.info(f"⚔️ [전투] 에이전트 가동)")
                        battle_module(state, avail)
                  # [상황 B] 카드 보상 화면
                elif screen_type == "COMBAT_REWARD":
                    log.info("🎁 전투 보상 챙기기")
                    rewards = state.get("screen_state", {}).get("rewards", [])
                    picked_something = False
                    for i, reward in enumerate(rewards):
                        r_type = reward.get("reward_type", "")
                        if r_type in ["GOLD", "POTION", "RELIC"]:
                            print(f"choose {i}", flush=True)
                            picked_something = True
                            break
                            
                        elif r_type == "CARD":
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
                    if(choice != -1):
                        log.info(f"{choice}번 카드 선택")
                        print(f"choose {choice}", flush=True)
                    else : 
                        log.info(f"skip 선택")
                        print(f"skip", flush = True)
                    if "proceed" in avail:
                        log.info("다 골랐으니 진행2")
                        print("proceed", flush=True)
                        continue    
                # [상황 C] 맵 이동 화면
                elif screen_type == "MAP":
                    log.info("🗺️ [이동] 맵 탐색 에이전트 가동")
                    print(f"choose {0}", flush=True)
                    # 일단 멍청하게 구현
                    # run_map_routing()
                elif screen_type == "REST":
                    log.info("🔥 모닥불 에이전트 가동")
                    
                    if "choose" not in avail :
                       print(f"proceed", flush=True)
                        # 선택 다 한 상황이라 고를 게 없으면 넘기기
                    # 휴리스틱: 체력이 70% 이하면 무조건 휴식
                    # 일단 단순하게 구현;;
                    # 사실 옵션이 3개 다보니 llm 한테 물어봐도 되고
                    # 아니면
                    if player_hp < (max_hp * 0.7):
                        print(f"choose rest", flush=True)
                                
                    elif True == False: #여기다 이제 need smith 판단 함수 넣든가말든가 혹은 이 전체적으로 llm에 넣거나
                        print(f"choose smith", flush=True)
                    else :
                        print(f"choose recall", flush=True)
                    
                    #일단 멈춤 방지로 넣어둠
                    print(f"choose rest", flush=True)
                    #    
                elif screen_type == "EVENT":
                    log.info("❓ 이벤트 에이전트 가동 (LLM 호출)")
                    
                    event_name = state.get("screen_state", {}).get("event_name", "Unknown")
                    body_text = state.get("screen_state", {}).get("body_text", "")
                    options = state.get("screen_state", {}).get("options", [])
                    if event_name == "Match and Keep!":
                        log.info("🃏 짝맞추기 에이전트 가동")
                        cards = state.get("screen_state", {}).get("cards", [])
                        avail_cmds = state.get("available_commands", [])
                        
                        cmd = module.match_and_keep_expert(cards, avail_cmds)
                        print(cmd, flush=True)
                        continue
                    else:
                        log.info(f"❓ LLM 이벤트 전문가 호출: {event_name}")
                        options_text = state.get("screen_state", {}).get("options", [])
                        
                        # core 데이터 가져오기
                        core = state.get("core", {})
                        hp = core.get("hp", 0)
                        max_hp = core.get("max_hp", 80)
                        gold = core.get("gold", 0)
                        
                        # 덱 프로필 (나중에 만드실 함수, 지금은 임시 문자열)
                        deck_profile = "Balanced deck with 20 cards." 
                        
                        choice_idx = evaluate_event(event_name, options_text, hp, max_hp, gold, deck_profile)
                        print(f"choose {choice_idx}", flush=True)
                        continue





                # [그 외 상황] 
                else:
                    log.info(f"대기 중... (phase: {room_phase}, screen: {screen_type})")
                    time.sleep(0.5)  

            
        except Exception as e:
            # 파이썬 코드가 죽었을 때 원인을 검은 터미널 창에 적나라하게 출력합니다.
            log.info("\n🚨 파이썬 스크립트에 치명적 에러 발생!")
            log.info(traceback.format_exc())
            # 에러가 나더라도 게임이 완전히 멈추지 않게 턴 종료를 억지로 쏴줍니다.
            #print("end", flush=True)


if __name__ == "__main__":
    main()