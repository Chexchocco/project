import time
import logging
from db import db_loader
from collections import Counter

log = logging.getLogger("STS_AI")


def enrich_hand(hand):
    """
    [핵심 전처리 함수]
    DFS나 모듈 연산에 들어가기 전, 손패의 카드들에 DB 스탯을 딱 한 번만 채워 넣습니다.
    """
    for card in hand:
        # 이미 전처리가 끝난 카드는 스킵 (DFS 재귀에서 중복 처리 방지)
        if "damage" in card:
            continue

        name = card.get("name")
        info = db_loader.get_card_info(name)
        upgrades = card.get("upgrades", 0)

        lookup_name = name
        if upgrades > 0:
            lookup_name += "+"

        if info:
            card["damage"] = info.get("damage", 0)
            card["block"] = info.get("block", 0)
            card["draw"] = info.get("draw", 0) # V3에서 추가된 드로우

            # 시너지와 Base Value도 그대로 가져옵니다
            card["base_value"] = info.get("base_value", 0)
            card["synergy"] = info.get("synergy", {"requires": {}, "provides": {}})
        else:
            log.info(f"info 발견 x, {name}")

    return hand


def damage_dfs(current_hand, current_energy, is_vulnerable):
    # 💡 인자에서 CARD_DB 삭제, 내부 DB 호출 삭제 (순수 연산만 수행)
    max_dmg_here = 0
    # log.info("bfs 실시")
    for i, card in enumerate(current_hand):
        cost = card.get("cost", 99)

        # 낼 수 있는 카드인가?
        if current_energy >= cost and card.get("is_playable", False):
            # 1. 이미 전처리된 데미지 가져오기
            base_dmg = card.get("damage", 0)
            if is_vulnerable:
                base_dmg = int(base_dmg * 1.5)

            next_vulnerable = is_vulnerable
            if "Vulnerable" in card.get("effects", {}):
                next_vulnerable = True

            # 재귀 호출
            remaining_hand = current_hand[:i] + current_hand[i+1:]
            extra_dmg = damage_dfs(remaining_hand, current_energy - cost, next_vulnerable)

            total = base_dmg + extra_dmg
            if total > max_dmg_here:
                max_dmg_here = total

    return max_dmg_here


def max_damage_expert(hand, energy, monster):
    is_vulnerable = any(p['id'] == 'Vulnerable' for p in monster.get('powers', []))

    best_dmg = -1
    next_card_idx = -1

    # 💡 1. 알고리즘 시작 전 손패 전처리 (딱 한 번만 실행됨)
    enriched_hand = enrich_hand(hand)

    # 첫 번째 수(Move) 결정
    for i, card in enumerate(enriched_hand):
        cost = card.get("cost", 99)

        if energy >= cost and card.get("is_playable", False):
            current_dmg = card.get("damage", 0)
            if is_vulnerable: current_dmg = int(current_dmg * 1.5)

            next_vulnerable = is_vulnerable
            if "Vulnerable" in card.get("effects", {}): next_vulnerable = True

            remaining_hand = enriched_hand[:i] + enriched_hand[i+1:]

            total_potential = current_dmg + damage_dfs(remaining_hand, energy - cost, next_vulnerable)

            if total_potential > best_dmg:
                best_dmg = total_potential
                next_card_idx = i

    return next_card_idx


def defensive_expert(hand, energy, player_block, monsters):
    total_incoming_damage = 0

    for i, m in enumerate(monsters):
        if not m.get("is_gone", False) and not m.get("half_dead", False):
            intent = m.get("intent", "")
            if "ATTACK" in intent:
                hit_count = m.get("move_adjusted_damage", 0)
                times = m.get("move_hits", 1)
                total_incoming_damage += (hit_count * times)

    if total_incoming_damage <= player_block:
        return -1

    best_defense_value = -1
    next_card_idx = -1

    # 💡 전처리된 손패 사용
    enriched_hand = enrich_hand(hand)

    for i, card in enumerate(enriched_hand):
        cost = card.get("cost", 99)

        if energy >= cost and card.get("is_playable", False):
            defense_value = card.get("block", 0)
            if defense_value == 0:
                continue

            if defense_value > best_defense_value:
                best_defense_value = defense_value
                next_card_idx = i

    return next_card_idx


def lethal_expert(hand, energy, target_monster):
    target_hp = target_monster.get("current_hp", 0)
    if target_hp <= 0:
        return -1

    is_vulnerable = any(p['id'] == 'Vulnerable' for p in target_monster.get('powers', []))

    best_card_idx = -1
    min_cost_to_kill = 999
    min_damage = 999

    # 💡 전처리된 손패 사용 및 낡은 CARD_DB 직접 호출 방식 제거
    enriched_hand = enrich_hand(hand)

    for i, card in enumerate(enriched_hand):
        cost = card.get("cost", 99)
        base_dmg = card.get("damage", 0)

        if energy >= cost and card.get("is_playable", False):
            current_dmg = base_dmg
            if is_vulnerable:
                current_dmg = int(current_dmg * 1.5)

            if current_dmg >= target_hp:
                # 킬각일 때 가장 코스트가 적은 공격 선호 (동일 코스트라면 오버킬을 줄이거나 취향껏)
                if cost <= min_cost_to_kill and min_damage > current_dmg:
                    min_cost_to_kill = cost
                    best_card_idx = i
                    min_damage = current_dmg

    return best_card_idx


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

        next_card_idx = lethal_expert(hand, energy, target_monster)
        if next_card_idx != -1:
            log.info(f"[마무리] 최소 비용으로 적 처치: {next_card_idx+1}번째 카드")


        if next_card_idx == -1 :
            next_card_idx = defensive_expert(hand, energy, player_block,  monsters)
            #log.info(f"{next_card_idx+1}번째 카드로 방어하기")

        if next_card_idx == -1 :
            next_card_idx = max_damage_expert(hand, energy, monsters[target_idx])
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


def handle_hand_select(state, avail):
    log.info("전투 중 패 선택(HAND_SELECT) 화면 진입")
    screen_state = state.get("screen_state", {})

    selected_cards = screen_state.get("selected", [])
    max_cards = screen_state.get("max_cards", 1)

    if len(selected_cards) >= max_cards:
        log.info("✅ 패 선택 완료! Confirm을 누릅니다.")
        print("confirm", flush=True)
        return

    # 2. 카드를 아직 덜 골랐을 때 고르는 로직
    # (일단은 게임이 안 멈추고 계속 굴러가게 만드는 것이 목표이므로 무조건 0번을 고릅니다)
    # 추후 '전장의 함성'이면 똥카드(상태이상/타격)를 고르고, '무장'이면 좋은 카드를 고르게 업그레이드 가능!

    log.info("👉 패에서 0번 카드를 선택합니다.")
    print("choose 0", flush=True)
    return


def get_draw_pile_summary(state): #STATE 입력 받아서 현재 뽑을 더미에 있는 덱을 LLM이 읽기 좋은 형태로 바꿔서 요약시키기
    combat = state["combat_state"]
    draw_pile = combat.get("draw_pile", [])
    
    card_counts = Counter(card["name"] for card in draw_pile)
    
    type_counts = Counter(card["type"] for card in draw_pile)
    
    # 3. LLM이 이해하기 쉬운 요약본 생성
    summary = {
        "total_remaining": len(draw_pile),
        "card_list": dict(card_counts),  
        "types": dict(type_counts)       
    }
    
    return summary