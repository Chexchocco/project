import ollama
import json
import re

# 1. 로컬 DB 파일 읽어오기
try:
    with open('items.json', 'r', encoding='utf-8') as f:
        db = json.load(f)
except FileNotFoundError:
    print("🚨 items.json 파일을 찾을 수 없습니다!")
    exit()

def get_card_info(card_name):
    """DB에서 특정 카드 정보를 찾아 반환하는 헬퍼 함수"""
    for card in db.get("cards", []):
        if card["name"].lower() == card_name.lower():
            return card
    return None

# ==========================================
# 🎮 게임 클라이언트 상태 (파이썬이 관리하는 영역)
# ==========================================
energy = 3
hand = ["Swivel+", "Bash", "Dropkick", "Strike"]
buffs = [] # 파이썬이 실시간으로 추적하는 캐릭터의 버프 상태
enemies_state = "Enemy A (17 HP), Enemy B (15 HP), Enemy C (19 HP)"

print("⚔️ 1-Step AI 에이전트 턴 시작 ⚔️\n" + "="*50)

turn_step = 1

# 에너지가 남아있고 패에 카드가 있는 동안 계속 루프(핑퐁)를 돕니다.
while energy > 0 and hand:
    print(f"\n▶ [Step {turn_step}] 파이썬 상태 -> 에너지: {energy} | 버프: {buffs} | 패: {hand}")

    # 2. 현재 내 '패(Hand)'에 있는 카드 정보만 DB에서 추출
    db_text = "[Cards Database]\n"
    seen = set()
    for c_name in hand:
        if c_name.lower() not in seen:
            seen.add(c_name.lower())
            info = get_card_info(c_name)
            if info:
                desc = info.get('description', '').replace('\n', ' ')
                db_text += f"- {info['name']} (Type: {info.get('type')}, Cost: {info.get('cost')}): {desc}\n"

    db_text += "\n[Game Mechanics]\n- [R]: Represents 1 Energy.\n- Vulnerable: Take 50% more damage from Attacks.\n"

    # 3. 1-Step 전용 범용 프롬프트 (미래 계획 금지, 당장 낼 1장만 선택)
    prompt = f"""
    {db_text}

    [Current State]
    - Energy: {energy}
    - Buffs: {', '.join(buffs) if buffs else 'None'}
    - Enemies: {enemies_state}
    - Hand: {', '.join(hand)}

    [Task]
    You are an expert Slay the Spire AI. Choose EXACTLY ONE card to play.
    To avoid mistakes, you MUST fill out the Reasoning section exactly in this order:

    Output EXACTLY in this format:
    Reasoning:
    1. Buff Check: Do I have 'Next Attack costs 0'? (Yes/No). If Yes, list the original costs of Attack cards in my hand to find the highest one.
    2. Vulnerable Check: Are any enemies Vulnerable right now? (Yes/No).
    3. Conclusion: Based on 1 and 2, the most logical card is [Card Name].
    Selected Card: [Card Name]
    Target: [Enemy A/B/C or Self]
    """

    # 4. LLM 호출
    response = ollama.chat(
        model='my_sts_qwen', 
        messages=[
            {'role': 'system', 'content': 'You are a precise Slay the Spire AI.'},
            {'role': 'user', 'content': prompt}
        ],
        options={'temperature': 0.0, 'num_predict': 256}
    )

    content = response['message']['content']
    print(f"🤖 LLM의 사고 과정:\n{content.strip()}\n")

    # 5. 파이썬의 결과 파싱 및 게임 상태 업데이트 (규칙 엔진 역할)
    match = re.search(r"Selected Card:\s*(.+)", content, re.IGNORECASE)
    if match:
        selected_card = match.group(1).strip()
        
        # 패에 있는 카드인지 확인
        valid_card = None
        for c in hand:
            if c.lower() in selected_card.lower():
                valid_card = c
                break

        if not valid_card:
            print(f"🚨 LLM이 패에 없는 카드({selected_card})를 선택했습니다. 턴 강제 종료.")
            break

        # 카드 코스트 계산 및 버프 적용 로직
        info = get_card_info(valid_card)
        cost = int(info['cost']) if info['cost'].isdigit() else 0
        
        if info['type'] == 'Attack' and "Next Attack costs 0" in buffs:
            cost = 0
            buffs.remove("Next Attack costs 0")
            print("   ✨ 시스템: 버프 발동! 이번 공격 카드의 코스트가 0으로 처리됩니다.")

        # 에너지 차감 및 패에서 카드 제거
        if energy >= cost:
            energy -= cost
            hand.remove(valid_card)
            print(f"   ✅ 시스템: {valid_card} 사용 완료! (소모 에너지: {cost}, 남은 에너지: {energy})")
            
            # Swivel+ 를 냈을 경우 파이썬이 버프를 등록해 줌
            if valid_card == "Swivel+":
                buffs.append("Next Attack costs 0")
                print("   ✨ 시스템: 플레이어에게 'Next Attack costs 0' 버프가 부여되었습니다.")
        else:
            print("🚨 에너지가 부족하여 카드를 낼 수 없습니다. 턴 강제 종료.")
            break
    else:
        print("🚨 파싱 실패 (Selected Card 양식을 지키지 않음).")
        break

    turn_step += 1
    print("-" * 50)

print("\n🏁 턴 종료! 더 이상 낼 카드가 없거나 에너지가 없습니다.")