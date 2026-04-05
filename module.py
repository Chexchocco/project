import sys



def damage_dfs(current_hand, current_energy, CARD_DB, is_vulnerable):
    max_dmg_here = 0    
    
    for i, card in enumerate(current_hand):
        name = card.get("name")
        card["damage"] = CARD_DB[name].get("damage", 0)
        card["effects"] = CARD_DB[name].get("effects", {})
        cost = card.get("cost", 99)
        
        # 낼 수 있는 카드인가?
        if current_energy >= cost and card.get("is_playable", False):
            # 1. 현재 카드의 데미지 계산 (취약 상태라면 1.5배)
            base_dmg = card.get("damage", 0)
            if is_vulnerable:
                base_dmg = int(base_dmg * 1.5)
            
            # 2. 이 카드를 냄으로써 발생하는 상태 변화 (예: 강타를 쓰면 이후 취약 적용)
            next_vulnerable = is_vulnerable
            if "Vulnerable" in card.get("effects", {}):
                next_vulnerable = True
            
            # 3. 재귀 호출: 이 카드를 제외한 나머지 패로 얻을 수 있는 추가 데미지
            remaining_hand = current_hand[:i] + current_hand[i+1:]
            extra_dmg = damage_dfs(remaining_hand, current_energy - cost, CARD_DB, next_vulnerable)
            
            total = base_dmg + extra_dmg
            
            if total > max_dmg_here:
                max_dmg_here = total
                
    return max_dmg_here


def max_damage_expert(hand, energy, CARD_DB, monster):
    # 가장 데미지가 센 카드를 찾는 로직
    # 현재 손패를 바탕으로 DFS 를 진행해서 최대 데미지 탐색 후 다음에 낼 카드의 정보 제공
    # 그 후 매턴 dfs 진행 / 이게 LLM과 연동해서 하는거니까 매번 그렇게 해도 뭐 부하가 크지 않을듯
    # 손 패 개수가 한정되있긴하니까...
    # 고려 사항은 만약 드로우 같은 게 딜 포텐셜이 더 좋은 상황이거나(덱 카운팅 등으로 인해서)하는 경우인데
    # 덱 카운팅, 상대 행동 방식 이런거 고려해서 판단은 LLM에게 맡긴다치고 일단은 여기선 그 모듈만 단순화해서 계산
    # 단일 타깃이라고 생각합시다. 일단..
   
    # 카드별로 추가 모듈 제작 필요
    # 여러 타수인 애들 <- 입력 받아올 때 설정 필요 및 예외 처리
    # 힘 배율, 완타 등 가변적인게 큰 애들 마찬가지

    # 일단 타겟 설정은 호출 전에 정한다고 가정
    # 실제 JSON에서는 powers 리스트를 뒤져서 'Vulnerable'이 있는지 확인해야 합니다.
    is_vulnerable = any(p['id'] == 'Vulnerable' for p in monster.get('powers', []))
    
    best_dmg = -1
    next_card_idx = -1
    
    # 첫 번째 수(Move) 결정
    for i, card in enumerate(hand):
        cost = card.get("cost", 99)
        name = card.get("name")
        card["damage"] = CARD_DB[name].get("damage", 0)
        card["effects"] = CARD_DB[name].get("effects", {})

        print("{i} 번째 카드  탐색", file=sys.stderr, flush=True)
        
        if energy >= cost and card.get("is_playable", False): 
            
            # 시뮬레이션 시작
            current_dmg = card.get("damage", 0)
            if is_vulnerable: current_dmg = int(current_dmg * 1.5)
            
            next_vulnerable = is_vulnerable
            if "Vulnerable" in card.get("effects", {}): next_vulnerable = True
            
            remaining_hand = hand[:i] + hand[i+1:]
            total_potential = current_dmg + damage_dfs(remaining_hand, energy - cost, CARD_DB, next_vulnerable)
            
            if total_potential > best_dmg:
                best_dmg = total_potential
                next_card_idx = i
                
    # 만약 낼 카드가 없다면 -1 리턴 , 나가서 예외처리

    return next_card_idx

def defensive_expert(hand, energy, player_block, CARD_DB, monsters):
    # 데미지를 막기 위해 수비 카드를 먼저 내는 로직
    # 상대에게 약화 혹은 힘 디버프 등을 거는 경우를 체크하자
    # 상대를 죽여서 줄이는건 일단 나중에 생각

    total_incoming_damage = 0

    for i, m in enumerate(monsters):
        if not m.get("is_gone", False) and not m.get("half_dead", False):
            # 적의 의도가 공격(ATTACK) 계열인지 확인
            intent = m.get("intent", "")
            if "ATTACK" in intent:
                # 몬스터의 다단히트(예: 8x3) 고려
                hit_count = m.get("move_adjusted_damage", 0)
                times = m.get("move_hits", 1)
                total_incoming_damage += (hit_count * times)
    if total_incoming_damage <= player_block:
        return -1

    best_defense_value = -1
    next_card_idx = -1

    for i, card in enumerate(hand):
        name = card.get("name")
        cost = card.get("cost", 99)
        card["damage"] = CARD_DB.get(name, {}).get("damage", 0)
        card["block"] = CARD_DB.get(name, {}).get("block", 0)
        card["effects"] = CARD_DB.get(name, {}).get("effects", {})
        
        if energy >= cost and card.get("is_playable", False):
            # 순수 방어도
            defense_value = card.get("block", 0)
            
            # 개멍청한 로직이긴함
            # 방어에 전혀 도움이 안 되는 카드라면 패스
            if defense_value == 0:
                continue
                
            # 비용 대비 효율(가성비)도 고려할 수 있지만, 일단 절댓값이 가장 큰 카드를 선택
            if defense_value > best_defense_value:
                best_defense_value = defense_value
                next_card_idx = i
                
    return next_card_idx



def lethal_expert(hand, energy, target_monster, CARD_DB):
    """
    적을 죽일 수 있는 카드 중 가장 에너지를 적게 소모하는 카드의 인덱스를 찾습니다.
    """
    target_hp = target_monster.get("current_hp", 0)
    
    # 적의 체력이 이미 0 이하라면 무시
    if target_hp <= 0:
        return -1
        
    is_vulnerable = any(p['id'] == 'Vulnerable' for p in target_monster.get('powers', []))
    
    best_card_idx = -1
    min_cost_to_kill = 999
    min_damage = 999
    for i, card in enumerate(hand):
        name = card.get("name")
        cost = card.get("cost", 99)
        
        # 안전한 조회를 위해 .get(name, {}) 사용
        base_dmg = CARD_DB.get(name, {}).get("damage", 0)
        
        if energy >= cost and card.get("is_playable", False):
            # 실시간 데미지 계산 (취약 적용)
            current_dmg = base_dmg
            if is_vulnerable:
                current_dmg = int(current_dmg * 1.5)
                
            # 이 카드 한 장으로 적을 죽일 수 있다면?
            if current_dmg >= target_hp:
                # 기존에 찾은 킬각 카드보다 코스트가 낮을 때만 갱신 (예: 강타(2)보다 타격(1) 우선)
                if cost <= min_cost_to_kill and min_damage < current_dmg:
                    min_cost_to_kill = cost
                    best_card_idx = i
                    min_damage = current_dmg
                    
    return best_card_idx