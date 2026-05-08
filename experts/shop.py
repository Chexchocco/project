import logging

log = logging.getLogger("STS_AI")

WAITING_FOR_SHOP = False
SHOP_DONE = False


# 상점 주인을 만난 화면을 컨트롤하는 함수. 플래그를 통해 물건을 구매할 때와 구매 후 떠날 때를 구분한다. 
def handle_shop_room(state, avail):
    global WAITING_FOR_SHOP, SHOP_DONE
    if not WAITING_FOR_SHOP:
        log.info("🛒 상점 주인에게 말을 겁니다.")
        print("choose shop", flush=True)
        WAITING_FOR_SHOP = True
        return
    if SHOP_DONE:
        WAITING_FOR_SHOP = False
        SHOP_DONE = False
        print("proceed", flush=True)
        return


def handle_shop_screen(state, avail):
    global SHOP_DONE
    log.info("💰 상점 화면 진입")
    screen_state = state.get("screen_state", {})
    gold = state.get("gold", 0)

    # 상점에 나온 카드, 유물, 포션 목록
    shop_cards = screen_state.get("cards", [])
    shop_relics = screen_state.get("relics", [])

    # [임시 로직] 돈이 되는 것 중 첫 번째 카드를 사고 바로 나가기
    can_buy = False
    for card in shop_cards:
        if gold >= card.get("price", 999):
            log.info(f"💳 {card.get('name')} 카드를 구매합니다.")
            print(f"choose {card.get('name')}", flush=True)
            can_buy = True
            break

    if not can_buy:
        log.info("🚪 살 수 있는 게 없거나 이미 샀으므로 상점을 나갑니다.")
        SHOP_DONE = True
        print("leave", flush=True)

    return
