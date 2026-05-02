import logging

log = logging.getLogger("STS_AI")


def handle_rest(state, avail):
    log.info("🔥 모닥불 에이전트 가동")

    player_hp = state.get("current_hp", 0)
    max_hp = state.get("max_hp", 80)

    if "choose" not in avail :
       log.info("휴식 나가기")
       print(f"proceed", flush=True)
       return
        # 선택 다 한 상황이라 고를 게 없으면 넘기기
    # 휴리스틱: 체력이 70% 이하면 무조건 휴식
    # 일단 단순하게 구현;;
    # 사실 옵션이 3개 다보니 llm 한테 물어봐도 되고
    # 아니면
    if player_hp < (max_hp * 0.7):
        print(f"choose rest", flush=True)
        return

    else: #여기다 이제 need smith 판단 함수 넣든가말든가 혹은 이 전체적으로 llm에 넣거나
        print(f"choose smith", flush=True)
        return
    #else :
    #    print(f"choose recall", flush=True)
    #    continue

    #일단 멈춤 방지로 넣어둠
    print(f"choose rest", flush=True)
    return
    #
