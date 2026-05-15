# 피코로 스텝 모터와 솔레노이드 제어하는 프로그램

import sys
import select
import time
from machine import Pin, PWM

# --- 하드웨어 핀 설정 ---

# 1. 스텝 모터 핀 설정 (TMC2209)
DIR_PIN = 12 # 방향 제어 핀
STEP_PIN = 11 # 스텝 제어 핀
EN_PIN = 22 # 모터 활성화 핀 (0: 활성화, 1: 비활성화)

direction = Pin(DIR_PIN, Pin.OUT) # 방향 제어 핀 설정
step = Pin(STEP_PIN, Pin.OUT) # 스텝 제어 핀 설정
enable = Pin(EN_PIN, Pin.OUT) # 모터 활성화 핀 설정
enable.value(1)  # 초기 모터 비활성화

# 2. 솔레노이드 핀 설정
PWM_PIN = 15 # 솔레노이드 제어용 PWM 핀
pwm = PWM(Pin(PWM_PIN)) # PWM 객체 생성
pwm.freq(20000) # 20kHz로 설정 (인간이 들을 수 없는 주파수)
sel_pins = [Pin(0, Pin.OUT), Pin(1, Pin.OUT), Pin(2, Pin.OUT)] # 솔레노이드 선택 핀 설정 (3개)

# 3. 호밍용 리미트 스위치 핀 설정
LIMIT_PIN = 16 # 리미트 스위치 핀 (접촉 시 LOW, 비접촉 시 HIGH)
limit_switch = Pin(LIMIT_PIN, Pin.IN, Pin.PULL_UP) # 내부 풀업 저항 사용

def get_duty(percent): # 0~100%를 16비트 PWM 값(0~65535)으로 변환하는 함수
    return int(percent * 65535 / 100)

def move_stepper(steps, delay_us=15, clockwise=True): # 스텝 모터를 지정된 스텝 수만큼 이동시키는 함수
    direction.value(1 if clockwise else 0) # 방향 설정 (1: 시계방향, 0: 반시계방향)
    for _ in range(steps): # 지정된 스텝 수만큼 반복
        step.value(1) # 스텝 핀 HIGH
        time.sleep_us(delay_us) # 지정된 딜레이만큼 대기
        step.value(0) # 스텝 핀 LOW
        time.sleep_us(delay_us) # 지정된 딜레이만큼 대기

# --- 호밍(Homing) 함수 ---
def homing(): # 모터를 리미트 스위치까지 이동시켜 초기 위치를 설정하는 함수
    print("\n[호밍 시작] 리미트 스위치를 찾습니다...")
    enable.value(0) # 모터 활성화
    time.sleep(0.1) # 모터 활성화 후 잠시 대기
    
    homing_clockwise = False # 호밍 방향 설정 (필요에 따라 True/False 변경)
    direction.value(1 if homing_clockwise else 0) # 호밍 방향 설정
    
    while limit_switch.value() == 1: # 리미트 스위치가 접촉될 때까지 반복 (접촉 시 LOW)
        step.value(1)
        time.sleep_us(30)
        step.value(0)
        time.sleep_us(30)
        
    print("[호밍 감지] 스위치 접촉.")
    
    print("스위치 해제를 위해 아주 조금(200스텝) 뒤로 이동합니다...")
    direction.value(0 if homing_clockwise else 1)
    for _ in range(200): 
        step.value(1)
        time.sleep_us(30)
        step.value(0)
        time.sleep_us(30)
        
    print("[호밍 완료]\n")


def actuate_solenoids(bits): # 솔레노이드 작동 함수 (bits는 3자리 문자열, 예: "101")
    for i in range(3): # 3개의 솔레노이드 선택 핀에 각각 bits의 값을 설정 (1: 작동, 0: 비작동)
        sel_pins[i].value(int(bits[i])) # 선택 핀 설정

    if "1" in bits: # bits에 '1'이 하나라도 있으면 솔레노이드 작동 (PWM 신호 출력)
        pwm.duty_u16(get_duty(100)) # 100% 듀티 사이클로 솔레노이드 작동
        time.sleep(0.2) # 솔레노이드가 완전히 작동할 때까지 잠시 대기
        pwm.duty_u16(get_duty(74)) # 74% 듀티 사이클로 유지 (최소한의 전력으로 유지)
        time.sleep(0.5) # 솔레노이드가 유지되는 동안 잠시 대기
    else: # bits가 모두 '0'이면 솔레노이드 비작동 (PWM 신호 끔)
        time.sleep(0.2) # 잠시 대기 후 바로 끔 (작동이 필요 없으므로)

    pwm.duty_u16(0) # PWM 신호 끔 (솔레노이드 완전히 비작동)
    for p in sel_pins: # 선택 핀 모두 LOW로 설정 (솔레노이드 선택 해제)
        p.value(0)
    time.sleep(0.1)

def run_sequence(s1, s2, pattern): # 시퀀스 실행 함수 (s1, s2는 모터 이동 스텝 수, pattern은 솔레노이드 작동 패턴 문자열)
    enable.value(0)
    time.sleep(0.1)
    
    chunks = [pattern[i:i+3] for i in range(0, len(pattern), 3)] # 패턴을 3자리씩 나누어 리스트로 저장 (예: "101010" -> ["101", "010"])
    total_chunks = len(chunks) # 총 시퀀스 수 계산
    
    for idx, chunk in enumerate(chunks): # 각 시퀀스에 대해 반복 (idx는 현재 시퀀스 인덱스, chunk는 현재 시퀀스 패턴)
        print(f"\n--- [시퀀스 {idx+1}/{total_chunks}] ---")
        
        # 1. 솔레노이드 작동 (먼저 실행)
        print(f"1. 솔레노이드 작동 패턴 '{chunk}'")
        actuate_solenoids(chunk) # 해당 시퀀스의 솔레노이드 작동 패턴에 따라 솔레노이드 작동 함수 호출
        
        # 2. 모터 이동 (마지막 시퀀스인 경우 생략)
        if idx == total_chunks - 1: # 마지막 시퀀스인 경우 모터 이동 생략
            print("2. 마지막 시퀀스이므로 모터 이동을 생략합니다.")
        else: # 마지막 시퀀스가 아닌 경우 모터 이동 실행
            current_steps = s1 if idx % 2 == 0 else s2 # 짝수 시퀀스는 s1 스텝, 홀수 시퀀스는 s2 스텝으로 이동
            print(f"2. 모터 이동: {current_steps} 스텝")
            move_stepper(steps=current_steps)
        
    print("\n모든 동작이 완료되었습니다.")

# --- 실행 부분 ---
try:
    homing()
    print("준비 완료! 라즈베리파이 5로부터 데이터를 기다립니다...")
    
    # 시리얼 입력을 감시하는 폴러(Poller) 생성
    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)
    
    while True:
        # 0.1초(100ms) 단위로 수신된 데이터가 있는지 '확인만' 함 (프로그램이 멈추지 않음)
        res = poller.poll(100) 
        
        if res:
            # 데이터가 들어왔을 때만 읽어들임
            line = sys.stdin.readline().strip()
            
            if line:
                print(f"수신된 패턴: {line}")
                s1_steps = 24528 
                s2_steps = 40544
                run_sequence(s1_steps, s2_steps, line)
                print("\n출력 완료. 다음 데이터를 기다립니다.")
        else:
            # 데이터가 없으면 아주 짧게 쉬고 다시 확인 (무한 루프 유지)
            time.sleep(0.01)

except KeyboardInterrupt:
    print("\n사용자에 의해 강제 종료되었습니다.")
finally:
    pwm.duty_u16(0)
    for p in sel_pins:
        p.value(0)
    enable.value(1)
    print("종료합니다.")
