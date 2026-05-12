import sys
import json
import traceback
import time
import logging

from db import db_loader
from config import PARSED_ITEM_PATH, LOG_PATH
from io_bridge import communication

from experts import combat as combat_expert
from experts import reward as reward_expert
from experts import map as map_expert
from experts import rest as rest_expert
from experts import shop as shop_expert
from experts import event as event_expert


# Logging setup
log = logging.getLogger("STS_AI")
log.setLevel(logging.INFO)
file_handler = logging.FileHandler(LOG_PATH, mode='a', encoding='utf-8')
console_handler = logging.StreamHandler(sys.stderr)

formatter = logging.Formatter('%(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

log.addHandler(file_handler)
log.addHandler(console_handler)


def main():
    communication.announce_ready()
    db_loader.load_database(PARSED_ITEM_PATH)

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                log.info("❌ 게임과 연결이 끊어졌습니다.")
                break
            
            line = line.strip()
            if not line:
                continue

            data = json.loads(line)
            
            if "error" in data:
                real_error = data.get("error", "알 수 없는 에러")
                log.error(f"⚠️ 엔진 에러 발생! 이유: {real_error}")
                # 핑퐁 복구를 위해 상태를 다시 요구합니다.
                print("wait 3000", flush=True)
                continue

            if not data.get("in_game", False):
                log.info("🏠 진행 중인 게임이 없습니다. 새로운 게임을 시작합니다.")
                print("start ironclad 0", flush=True)
                continue

            if "game_state" not in data:
                log.info(f"문제 발생2 {data}")
                print("wait", flush=True)
                time.sleep(1.5)
                continue

            state = data["game_state"]
            room_phase = state.get("room_phase", "")
            screen_type = state.get("screen_type", "")
            avail = data.get("available_commands", [])

            if screen_type == "GAME_OVER":
                print("proceed", flush=True)
                continue

            # ─── Router: every branch is a single expert call ─────────────────
            if room_phase == "COMBAT" and screen_type == "NONE":
                if "combat_state" in state:
                    combat_expert.battle_module(state, avail)
            elif screen_type == "COMBAT_REWARD":
                reward_expert.handle_combat_reward(state, avail)
            elif screen_type == "CARD_REWARD":
                reward_expert.handle_card_reward(state, avail)
            elif screen_type == "GRID":
                reward_expert.handle_grid_selection(state, avail)
            elif screen_type == "MAP":
                map_expert.handle_map(state)
            elif screen_type == "REST":
                rest_expert.handle_rest(state, avail)
            elif screen_type == "EVENT":
                event_expert.handle_event(state, avail)
            elif screen_type == "CHEST":
                reward_expert.handle_chest(state, avail)
            elif screen_type == "HAND_SELECT":
                combat_expert.handle_hand_select(state, avail)
            elif screen_type == "SHOP_ROOM":
                shop_expert.handle_shop_room(state, avail)
            elif screen_type == "SHOP_SCREEN":
                shop_expert.handle_shop_screen(state, avail)
            elif screen_type == "BOSS_REWARD":
                reward_expert.handle_boss_reward(state, avail)
            else:
                log.info(f"대기 중... (phase: {room_phase}, screen: {screen_type})")
                print("wait 30", flush=True)
                time.sleep(1.5)

        except Exception:
            log.info("\n🚨 파이썬 스크립트에 치명적 에러 발생!")
            log.info(traceback.format_exc())
            # 에러가 나더라도 게임이 완전히 멈추지 않게 턴 종료를 억지로 쏴줍니다.
            #print("end", flush=True)


if __name__ == "__main__":
    main()