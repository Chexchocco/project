import logging

from experts import shop as shop_expert
from experts import reward as reward_expert

log = logging.getLogger("STS_AI")


def handle_map(state, avail):
    log.info("🗺️ [이동] 맵 탐색 에이전트 가동")
    # 새 방으로 진입하므로 상점/카드 보상 관련 플래그를 모두 리셋한다.
    shop_expert.reset_per_room()
    reward_expert.reset_per_room()
    choices = state.get("choice_list", [])
    if choices:
        print(f"choose {choices[0]}", flush=True)
    else:
        log.info("이동 오류?")
        print("choose 0", flush=True)
    return
    # 일단 멍청하게 구현
    # run_map_routing()
