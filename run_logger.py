import os
import json
import logging
from datetime import datetime

from config import LOCAL_PATH

RUN_HISTORY_PATH = os.path.join(LOCAL_PATH, "run_history.log")
CHOICE_LOG_PATH = os.path.join(LOCAL_PATH, "run_choices.jsonl")
ERROR_LOG_PATH = os.path.join(LOCAL_PATH, "run_errors.log")

def log_error_context(data, error_msg, traceback_str=None):
    """엔진 에러 또는 파이썬 스크립트 에러 발생 시 상세한 컨텍스트(입력 데이터 등)를 로그로 남깁니다."""
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_content = f"[{now}] ERROR TYPE: {'Python Exception' if traceback_str else 'Engine Error'}\n"
        log_content += f"Message: {error_msg}\n"
        if traceback_str:
            log_content += f"Traceback:\n{traceback_str}\n"
        
        # We dump the full context data (which includes available_commands, game_state, etc.)
        data_dump = json.dumps(data, ensure_ascii=False, indent=2)
        log_content += f"Context Data:\n{data} \n + error : {data_dump}\n"
        log_content += "="*80 + "\n\n"
        
        with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(log_content)
    except Exception as e:
        print(f"Error while logging error context: {e}")

def log_game_over(state):
    """사망 또는 클리어 시 최종 상태를 run_history.md에 기록합니다."""
    try:
        floor = state.get("floor", "?")
        act = state.get("act", "?")
        hp = state.get("current_hp", 0)
        max_hp = state.get("max_hp", 0)
        gold = state.get("gold", 0)
        
        deck = state.get("deck", [])
        deck_names = [c.get("id", c.get("name", "Unknown")) for c in deck]
        
        relics = state.get("relics", [])
        relic_names = [r.get("id", r.get("name", "Unknown")) for r in relics]
        
        screen_state = state.get("screen_state", {})
        victory = screen_state.get("victory", False)
        score = screen_state.get("score", 0)
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        status = "VICTORY 🏆" if victory else "DEFEAT 💀"
        
        log_content = f"## Run Ended at {now} - {status}\n\n"
        log_content += f"- **Floor:** {floor} (Act {act})\n"
        log_content += f"- **Score:** {score}\n"
        log_content += f"- **Gold:** {gold}\n"
        log_content += f"- **Final HP:** {hp}/{max_hp}\n\n"
        log_content += f"### Relics ({len(relic_names)})\n"
        log_content += f"{', '.join(relic_names)}\n\n"
        log_content += f"### Deck ({len(deck_names)})\n"
        log_content += f"{', '.join(deck_names)}\n"
        log_content += "\n---\n\n"
        
        with open(RUN_HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(log_content)
    except Exception as e:
        print(f"Error logging game over: {e}")

def log_major_choice(floor, choice_type, options, picked, reasoning=""):
    """중요한 선택(카드 픽, 보스 유물 등)을 JSONL로 기록합니다."""
    try:
        record = {
            "timestamp": datetime.now().isoformat(),
            "floor": floor,
            "type": choice_type,
            "options": options,
            "picked": picked,
            "reasoning": reasoning
        }
        with open(CHOICE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        pass

def log_raw_state(state):
    """현재 상황(screen_type 등)의 전체 입력 데이터를 run_history.md에 덤프합니다."""
    try:
        screen_type = state.get("screen_type", "")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_content = f"## 📝 Raw State Dump: {screen_type} ({now})\n\n"
        log_content += "```json\n"
        log_content += json.dumps(state, ensure_ascii=False, indent=2)
        log_content += "\n```\n\n---\n\n"
        
        with open(RUN_HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(log_content)
    except Exception as e:
        print(f"Error logging raw state: {e}")