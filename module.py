import sys
import db_util

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
        info = db_util.get_card_info(name)
        
        if info:
            parsed_data = info.get("parsed", {})
            card["damage"] = parsed_data.get("damage", 0)
            card["block"] = parsed_data.get("block", 0)
            card["effects"] = parsed_data.get("effects", {})
        else:
            card["damage"] = 0
            card["block"] = 0
            card["effects"] = {}
            
    return hand


def damage_dfs(current_hand, current_energy, is_vulnerable):
    # 💡 인자에서 CARD_DB 삭제, 내부 DB 호출 삭제 (순수 연산만 수행)
    max_dmg_here = 0    
    
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
            # 💡 2. DFS 호출 (미리 세팅된 손패를 넘겨줌)
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