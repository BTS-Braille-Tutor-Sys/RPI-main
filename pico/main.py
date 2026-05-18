# 피코로 스텝 모터와 솔레노이드 제어하는 프로그램

import sys
import select
import time
from machine import Pin, PWM

# --- 하드웨어 핀 설정 ---

# 1. 스텝 모터 핀 설정 (TMC2209)
DIR_PIN = 12
STEP_PIN = 11
EN_PIN = 22

direction = Pin(DIR_PIN, Pin.OUT)
step = Pin(STEP_PIN, Pin.OUT)
enable = Pin(EN_PIN, Pin.OUT)
enable.value(1)  # 초기 모터 비활성화

# 2. 솔레노이드 핀 설정
PWM_PIN = 15
pwm = PWM(Pin(PWM_PIN))
pwm.freq(20000)
sel_pins = [Pin(0, Pin.OUT), Pin(1, Pin.OUT), Pin(2, Pin.OUT)]

# 3. 호밍용 리미트 스위치 핀 설정
LIMIT_PIN = 16
limit_switch = Pin(LIMIT_PIN, Pin.IN, Pin.PULL_UP)

def get_duty(percent):
    return int(percent * 65535 / 100)

def move_stepper(steps, delay_us=15, clockwise=True):
    # clockwise=True(정방향: 1), clockwise=False(역방향: 0)
    direction.value(1 if clockwise else 0)
    for _ in range(steps):
        step.value(1)
        time.sleep_us(delay_us)
        step.value(0)
        time.sleep_us(delay_us)

# --- 호밍(Homing) 함수 ---
def homing():
    enable.value(0)
    time.sleep(0.1)
    
    homing_clockwise = False 
    direction.value(1 if homing_clockwise else 0)
    
    while limit_switch.value() == 1:
        step.value(1)
        time.sleep_us(30)
        step.value(0)
        time.sleep_us(30)
        
    time.sleep(0.5)
    
    direction.value(0 if homing_clockwise else 1)
    for _ in range(200): 
        step.value(1)
        time.sleep_us(30)
        step.value(0)
        time.sleep_us(30)

def actuate_solenoids(bits):
    for i in range(3):
        sel_pins[i].value(int(bits[i]))

    if "1" in bits:
        pwm.duty_u16(get_duty(100))
        time.sleep(0.2)
        pwm.duty_u16(get_duty(74))
        time.sleep(0.5)
    else:
        time.sleep(0.2)

    pwm.duty_u16(0)
    for p in sel_pins:
        p.value(0)
    time.sleep(0.1)

# --- 정방향 시퀀스 (핀 올리기) ---
def run_sequence_forward(s1, s2, pattern):
    enable.value(0)
    time.sleep(0.1)
    
    chunks = [pattern[i:i+3] for i in range(0, len(pattern), 3)]
    total_chunks = len(chunks)
    
    for idx, chunk in enumerate(chunks):
        # 1. 솔레노이드 작동
        actuate_solenoids(chunk)
        
        # 2. 모터 정방향 이동
        if idx != total_chunks - 1: # 마지막 시퀀스가 아닐 때만 모터 이동
            current_steps = s1 if idx % 2 == 0 else s2
            move_stepper(steps=current_steps, clockwise=True)

# --- 역방향 시퀀스 (핀 내리기) ---
def run_sequence_reverse(s1, s2, pattern):
    enable.value(0)
    time.sleep(0.1)
    
    chunks = [pattern[i:i+3] for i in range(0, len(pattern), 3)]
    total_chunks = len(chunks)
    
    # 끝 위치에서부터 거꾸로 순회
    for idx in range(total_chunks - 1, -1, -1):
        chunk = chunks[idx]
        actuate_solenoids(chunk)
        
        # 시작 위치가 아닐 때만 모터 이동
        if idx != 0:
            prev_idx = idx - 1
            current_steps = s1 if prev_idx % 2 == 0 else s2
            move_stepper(steps=current_steps, clockwise=False)

# --- 실행 부분 ---
try:
    homing()
    
    poller = select.poll()
    poller.register(sys.stdin, select.POLLIN)
    
    previous_pattern = None 
    s1_steps = 24528 
    s2_steps = 40544
    
    while True:
        res = poller.poll(100) 
        
        if res:
            line = sys.stdin.readline().strip()
            
            if line:
                if line.startswith('<') and line.endswith('>'):
                    payload = line[1:-1]
                    
                    # --- 종료 패킷(<EXIT>) 처리 로직 ---
                    if payload == "EXIT":
                        # 이전에 올린 핀(패턴)이 있다면 역방향으로 내려줌
                        if previous_pattern is not None:
                            run_sequence_reverse(s1_steps, s2_steps, previous_pattern)
                            homing()
                            previous_pattern = None # 초기화
                        
                        print("<DONE>") # 파이5로 완료 신호 전송
                        break # 메인 루프를 탈출하여 finally 블록(모터 비활성화)으로 이동
                        
                    # --- 기존 점자 패턴 처리 로직 ---
                    else:
                        current_pattern = payload
                        
                        # 1. 기존 핀 내리기 (되돌아가면서 역방향 실행)
                        if previous_pattern is not None:
                            run_sequence_reverse(s1_steps, s2_steps, previous_pattern)
                            homing()
                        
                        # 2. 새로운 핀 올리기 (정방향 실행)
                        run_sequence_forward(s1_steps, s2_steps, current_pattern)
                        
                        previous_pattern = current_pattern
                        print("<DONE>")
                        
        else:
            time.sleep(0.01)

except KeyboardInterrupt:
    pass
finally:
    # 종료 패킷을 받아 break로 탈출하거나 예외가 발생하면 안전하게 모든 핀과 모터를 비활성화
    pwm.duty_u16(0)
    for p in sel_pins:
        p.value(0)
    enable.value(1) # 모터 비활성화 (드라이버 설정에 따라 1이 Disable인 경우)
