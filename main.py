import sys
import json
import traceback
import os
import re
import module

from config import LOCAL_PATH, DB_PATH, LOG_PATH

CARD_DB = {}
RELIC_DB = {}
POTION_DB = {}

def log(msg):
    # sys.stderr로 출력하면 ModTheSpire 검은 콘솔 창에 정상적으로 뜹니다.
    print(msg, file=sys.stderr, flush=True)
    # VS Code에서 볼 수 있도록 텍스트 파일에 누적해서 씁니다 ("a" 모드)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except:
        pass

def load_database():
    global CARD_DB, RELIC_DB, POTION_DB
    
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            
        valid_colors = ["Red", "Colorless", "Curse"]
        for card in raw_data.get("cards", []):
            if card.get("color") in valid_colors:
                desc = card.get("description", "")
                
                card["damage"] = 0
                card["block"] = 0
                card["effects"] = {} # 상태이상 효과들
                
                # 1. 데미지 및 방어도 추출
                multi_hit_match = re.search(r"Deal (\d+) damage (\d+) times", desc)
                if multi_hit_match:
                    card["damage"] = int(multi_hit_match.group(1))
                    card["hits"] = int(multi_hit_match.group(2))
                else:
                    # 단일 히트 처리
                    dmg_match = re.search(r"Deal (\d+) damage", desc)
                    if dmg_match:
                        card["damage"] = int(dmg_match.group(1))
                        card["hits"] = 1
                        
                dmg_match = re.search(r"Deal (\d+) damage", desc)
                if dmg_match: card["damage"] = int(dmg_match.group(1))
                    
                blk_match = re.search(r"Gain (\d+) Block", desc)
                if blk_match: card["block"] = int(blk_match.group(1))
                    
                # 2. 상태 이상 추출 (취약, 약화, 근력 등 필요한 만큼 줄줄이 추가 가능)
                vuln_match = re.search(r"Apply (\d+) Vulnerable", desc)
                if vuln_match:
                    card["effects"]["Vulnerable"] = int(vuln_match.group(1))
                    
                weak_match = re.search(r"Apply (\d+) Weak", desc)
                if weak_match:
                    card["effects"]["Weak"] = int(weak_match.group(1))
                    
                str_match = re.search(r"Gain (\d+) Strength", desc)
                if str_match:
                    card["effects"]["Strength"] = int(str_match.group(1))
                # -----------------------------------
                
                # 파싱이 끝난 카드를 전역 DB에 저장
                CARD_DB[card["name"]] = card
                
        for relic in raw_data.get("relics", []):
            RELIC_DB[relic["name"]] = relic
            
        for potion in raw_data.get("potions", []):
            POTION_DB[potion["name"]] = potion
            
        print(f"로컬 DB 로딩 완료! (카드: {len(CARD_DB)}개, 유물: {len(RELIC_DB)}개)", file=sys.stderr, flush=True)
        
    except FileNotFoundError:
        print(f"에러: {DB_PATH} 파일을 찾을 수 없습니다", file=sys.stderr, flush=True)
        sys.exit(1) # DB가 없으면 프로그램 종료
    except Exception as e:
        print(f"DB 파싱 중 에러 발생: {e}", file=sys.stderr, flush=True)




def main():
    print("ready", flush=True)
    log("✅ 파이썬 에이전트 연결 완료!")

    load_database()


    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                log("❌ 게임과 연결이 끊어졌습니다.")
                break
            
            line = line.strip()
            if not line:
                continue
                
            data = json.loads(line)
            
            # 게임 엔진 자체에서 에러를 보냈을 경우
            if "error" in data:
                log(f"⚠️ 엔진 에러: {data['error']}")
                print("end", flush=True) 
                continue
            
            # 현재 게임 화면에서 파이썬이 입력할 수 있는 '허용된 명령어' 목록
            avail = data.get("available_commands", [])
            
            if "game_state" in data:
                state = data["game_state"]
                
                # 전투 중일 때의 로직
                if "combat_state" in state and state["combat_state"] is not None:
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
                        
                        next_card_idx = module.lethal_expert(hand, energy, target_monster, CARD_DB)
                        if next_card_idx != -1:
                            log(f"[마무리] 최소 비용으로 적 처치: {next_card_idx+1}번째 카드")        

                        next_card_idx = module.defensive_expert(hand, energy, player_block, CARD_DB, monsters)
                        if next_card_idx != -1 :    
                            log(f"{next_card_idx+1}번째 카드로 방어하기")

                        if next_card_idx == -1 :
                            next_card_idx = module.max_damage_expert(hand, energy, CARD_DB, monsters[target_idx])
                            log(f"{next_card_idx+1}번째 카드로 공격하기")
                        
                        if next_card_idx == -1 :
                            pass
                        else :
                            print(f"play {next_card_idx+1} {target_idx}", flush=True)
                            action_taken = True
                    # 카드를 냈다면 다음 턴 진행으로 넘어가고 루프 재시작
                    if action_taken:
                        continue 
                    
                    # 낼 카드가 없다면 턴 종료
                    if "end" in avail:
                        #print("end", flush=True)
                        log(" 💤 턴 종료")
                    elif "proceed" in avail:
                        # print("proceed", flush=True)
                        log(" 💤 턴 종료")
                    continue

            
            # 전투가 아닐 때의 화면 처리 (보상, 이벤트, 상점 등에서 멈춤 방지)
            if "start" in avail:
                print("start ironclad", flush=True)
            elif "choose" in avail:
                print("choose 0", flush=True)
            elif "proceed" in avail:
                print("proceed", flush=True)
            elif "leave" in avail:
                print("leave", flush=True)
            elif "return" in avail:
                print("return", flush=True)
            elif "confirm" in avail:
                print("confirm", flush=True)
            elif avail:
                # 모르는 상황이 오면 허용된 명령어 중 첫 번째 것을 무작위로 전송
                cmd = avail[0]
                print(cmd, flush=True)
                log(f"자동 탈출 로직 발동: {cmd}")
            else:
                print("wait", flush=True)
            

            
        except Exception as e:
            # 파이썬 코드가 죽었을 때 원인을 검은 터미널 창에 적나라하게 출력합니다.
            log("\n🚨 파이썬 스크립트에 치명적 에러 발생!")
            log(traceback.format_exc())
            # 에러가 나더라도 게임이 완전히 멈추지 않게 턴 종료를 억지로 쏴줍니다.
            #print("end", flush=True)

if __name__ == "__main__":
    main()