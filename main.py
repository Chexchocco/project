import sys
import json
import traceback
import time
import os
import re
import module
import db_util

from config import LOCAL_PATH, DB_PATH, LOG_PATH
from db_util import CARD_DB, RELIC_DB, POTION_DB, KEYWORD_DB
from deck_building import choose_card_reward

def log(msg):
    # sys.stderr로 출력하면 ModTheSpire 검은 콘솔 창에 정상적으로 뜹니다.
    print(msg, file=sys.stderr, flush=True)
    # VS Code에서 볼 수 있도록 텍스트 파일에 누적해서 씁니다 ("a" 모드)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except:
        pass
def battle_module(state, avail):
    combat = state["combat_state"]
    player = combat.get("player", {})
    hp = player.get("current_hp", 0)
    energy = player.get("energy", 0)
    hand = combat.get("hand", [])
    player_block = player.get("block", 0)
    
    log(f"⚔️ [전투] 체력: {hp} / 남은 에너지: {energy}")
    
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
            log(f"[마무리] 최소 비용으로 적 처치: {next_card_idx+1}번째 카드")        

        next_card_idx = module.defensive_expert(hand, energy, player_block,  monsters)
        if next_card_idx != -1 :    
            log(f"{next_card_idx+1}번째 카드로 방어하기")

        if next_card_idx == -1 :
            next_card_idx = module.max_damage_expert(hand, energy, monsters[target_idx])
            log(f"{next_card_idx+1}번째 카드로 공격하기")
        
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
        log(" 💤 턴 종료")
        return -1
    elif "proceed" in avail:
        # print("proceed", flush=True)
        log(" 💤 턴 종료")
        return -1




def main():
    log("✅ 파이썬 에이전트 연결 완료!")

    db_util.load_database(DB_PATH)
    while True:
        try:
            
            line = sys.stdin.readline()
            if not line:
                log("❌ 게임과 연결이 끊어졌습니다.")
                break
            
            line = line.strip()
            if not line:
                continue


            ###################
            data = json.loads(line)
            
            if "error" in data:
                log(f"⚠️ 엔진 에러: {data['error']}")
                time.sleep(10)
                continue
            
            if "game_state" in data:
                state = data["game_state"]
                
                # 💡 1. 현재 게임의 명시적 상태 변수들을 먼저 추출합니다.
                room_phase = state.get("room_phase", "")
                screen_type = state.get("screen_type", "")
                avail = data.get("available_commands", [])
                
                # [상황 A] 전투 중 (팝업 없고, room_phase가 COMBAT)
                if room_phase == "COMBAT" and screen_type == "NONE":
                    if "combat_state" in state:
                        log(f"⚔️ [전투] 에이전트 가동)")
                        battle_module(state, avail)
                  # [상황 B] 카드 보상 화면
                elif screen_type == "CARD_REWARD":
                    log("🎁 [보상] 덱 빌딩 에이전트 가동")
                    current_deck = [c["name"] for c in state.get("deck", [])]
                    offered_cards = [c["name"] for c in state.get("screen_state", {}).get("cards", [])]
                    
                    # db_utils를 활용한 choose_card_reward 함수 호출
                    # choice = choose_card_reward(current_deck, offered_cards)
                    # send_command_to_game(choice)
                    
                # [상황 C] 맵 이동 화면
                elif screen_type == "MAP":
                    log("🗺️ [이동] 맵 탐색 에이전트 가동")
                    # run_map_routing()
                    
                # [그 외 상황] 
                else:
                    log(f"대기 중... (phase: {room_phase}, screen: {screen_type})")
                    time.sleep(0.5)  
            
            
        except Exception as e:
            # 파이썬 코드가 죽었을 때 원인을 검은 터미널 창에 적나라하게 출력합니다.
            log("\n🚨 파이썬 스크립트에 치명적 에러 발생!")
            log(traceback.format_exc())
            # 에러가 나더라도 게임이 완전히 멈추지 않게 턴 종료를 억지로 쏴줍니다.
            #print("end", flush=True)


if __name__ == "__main__":
    main()