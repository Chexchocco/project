import logging

log = logging.getLogger("STS_AI")


def handle_rest(state, avail):
    log.info("🔥 모닥불 에이전트 가동")

    player_hp = state.get("current_hp", 0)
    max_hp = state.get("max_hp", 80)

    if "choose" not in avail:
        log.info("휴식 나가기")
        print("proceed", flush=True)
        return

    # 실제로 고를 수 있는 모닥불 옵션 (유물에 따라 smith 등이 빠질 수 있음)
    rest_options = state.get("screen_state", {}).get("rest_options", [])
    log.info(f"가능한 모닥불 옵션: {rest_options}")

    def pick(option):
        if option in rest_options:
            log.info(f"👉 choose {option}")
            print(f"choose {option}", flush=True)
            return True
        return False

    # 우선순위: 체력 낮으면 rest, 아니면 smith → rest → 남은 거 아무거나
    if player_hp < (max_hp * 0.7) and pick("rest"):
        return
    if pick("smith"):
        return
    if pick("rest"):
        return
    if rest_options:
        pick(rest_options[0])
        return

    log.info("🚨 모닥불에 선택 가능한 옵션이 없습니다. proceed.")
    print("proceed", flush=True)
