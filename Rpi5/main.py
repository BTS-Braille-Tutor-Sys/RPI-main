import sounddevice as sd
import numpy as np
import vosk
import json
import queue
import pandas as pd
import random
import os
import sys
import time
import serial      # 피코 시리얼 통신
import braillify   # 점자 변환 라이브러리

# --- [설정 및 초기화] ---
DEVICE_ID = 1          
SAMPLERATE = 48000     
THRESHOLD = 10.0       

# 1. Vosk 모델 로드
if not os.path.exists("model"):
    print("에러: 'model' 폴더를 확인하세요.")
    sys.exit()
model = vosk.Model("model")
q = queue.Queue()

# 2. 데이터베이스(CSV) 로드
CSV_FILE = 'word_natural_2chars.csv'
try:
    df = pd.read_csv(CSV_FILE)
    topic_list = df['topic'].unique().tolist()
    print(f"[{CSV_FILE}] 로드 완료! 총 {len(topic_list)}개의 주제가 있습니다.")
except Exception as e:
    print(f"CSV 로드 오류: {e}")
    sys.exit()

# 3. 피코 시리얼 통신 초기화
print("피코와 연결 중...")
try:
    ser = serial.Serial('/dev/ttyACM0', 115200)
    print("피코 호밍 및 준비 대기 중 (5초)...")
    time.sleep(5)
    print("피코 연결 완료!")
except Exception as e:
    print(f"[경고] 피코 연결 실패: {e}")
    print("피코 없이 음성 모드로만 진행합니다.")
    ser = None

current_topic = None
topic_idx = 0
current_word_pool = []
current_word_idx = 0

# --- [점자 패턴 변환 및 전송 함수] ---
def send_braille_to_pico(text):
    if ser is None:
        return 
        
    try:
        braille_text = braillify.translate_to_unicode(text)
        bit_pattern = ""
        for char in braille_text:
            if 0x2800 <= ord(char) <= 0x28FF: 
                code = ord(char) - 0x2800
                col1 = f"{(code & 0b000111):03b}"[::-1]
                col2 = f"{(code & 0b111000) >> 3:03b}"[::-1]
                bit_pattern += col1 + col2
                
        packet = f"<{bit_pattern}>"
        print(f"[피코 전송] 단어: {text} -> 패킷: {packet}")
        ser.write((packet + "\n").encode())
        
    except Exception as e:
        print(f"[점자 전송 에러]: {e}")

# --- [음성 출력 함수] ---
def speak(text):
    global stream
    print(f"시스템 음성: {text}")
    
    if stream is not None:
        stream.stop()
        stream.close() 
    
    time.sleep(0.5)
    
    try:
        os.system("wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.8")
        os.system(f"mimic3 --voice ko_KO/kss_low '{text}' > temp.wav 2>/dev/null")
        os.system("pw-play temp.wav")
    except Exception as e:
        print(f"[오디오 출력 에러]: {e}")
        
    time.sleep(0.3)
        
    with q.mutex:
        q.queue.clear()
        
    stream = sd.RawInputStream(samplerate=SAMPLERATE, blocksize=4000, device=DEVICE_ID, dtype='int16', channels=2, callback=callback)
    stream.start()

def load_topic(idx):
    global current_topic, current_word_pool, current_word_idx, topic_idx
    topic_idx = idx
    current_topic = topic_list[topic_idx]
    current_word_pool = df[df['topic'] == current_topic].to_dict('records')
    random.shuffle(current_word_pool)
    current_word_idx = 0
    
    selected = current_word_pool[current_word_idx]
    target_word = selected['word']
    
    speak(f"{current_topic} 주제 시작. 첫 번째 단어는 {target_word}입니다.")
    send_braille_to_pico(target_word)

def process_voice(text):
    global current_word_pool, current_word_idx, topic_idx, current_topic
    
    text = text.replace(" ", "").strip()
    if not text:
        return False

    print(f"[실시간 인식]: {text}")

    random_start_keywords = ["랜덤","랜던","무지하기","무적","랜덤주제", "아무거나", "무작위", "그냥아무거", "아무거나해"]
    if any(k in text for k in random_start_keywords):
        next_idx = random.randrange(len(topic_list))
        speak("어떤 것을 할지 고민되시는군요! 제가 주제 하나를 골라보겠습니다.")
        load_topic(next_idx)
        return True

    for idx, topic in enumerate(topic_list):
        if topic in text:
            load_topic(idx)
            return True

    reset_keywords = ["새로운","새로운주제선택", "새로운주제", "원하는주제", "처음으로"]
    if any(k in text for k in reset_keywords):
        current_topic = None
        current_word_pool = []
        speak("알겠습니다. 어떤 주제로 공부하시겠습니까? 원하는 주제를 말씀해 주세요.")
        return True

    change_keywords = ["다른","당","담","다른주제", "다른걸로", "다른거", "다른과목", "주제를바꿔줘", "주제바꿔줘", "주제바꿔", "주제변경"]
    if any(k in text for k in change_keywords):
        available_indices = [i for i in range(len(topic_list)) if i != topic_idx]
        if available_indices:
            next_idx = random.choice(available_indices)
            load_topic(next_idx)
        return True

    next_keywords = ["다음", "다은", "다으", "담", "단어", "넘어가"]
    if any(k in text for k in next_keywords):
        if not current_word_pool:
            speak("먼저 주제를 선택하시거나, 랜덤 주제라고 말씀해 주세요.")
        else:
            current_word_idx += 1
            if current_word_idx >= len(current_word_pool):
                current_word_idx = 0
                speak("주제의 모든 단어를 학습했습니다. 처음부터 다시 시작합니다.")
            
            selected = current_word_pool[current_word_idx]
            target_word = selected['word']
            
            speak(f"다음 단어는 {target_word}입니다.")
            send_braille_to_pico(target_word) 
        return True
            
    return False

def callback(indata, frames, time_info, status):
    data_array = np.frombuffer(indata, dtype='int16').reshape(-1, 2)
    left_channel = data_array[:, 0]
    volume_norm = np.linalg.norm(left_channel) / np.sqrt(len(left_channel))
    
    if volume_norm > THRESHOLD:
        q.put(left_channel.tobytes())

# --- [메인 실행부] ---
stream = sd.RawInputStream(samplerate=SAMPLERATE, blocksize=4000, device=DEVICE_ID, dtype='int16', channels=2, callback=callback)

try:
    rec = vosk.KaldiRecognizer(model, SAMPLERATE)
    print(f"--- 시스템 가동 준비 완료 ---")
    
    stream.start() 
    speak("공부할 주제를 말씀하세요.")
    
    while True:
        data = q.get()
        if rec.AcceptWaveform(data):
            res = json.loads(rec.Result())
            if process_voice(res.get('text', '')):
                rec.Reset()
        else:
            partial_res = json.loads(rec.PartialResult())
            partial_text = partial_res.get('partial', '')
            if partial_text:
                if process_voice(partial_text):
                    rec.Reset()

except KeyboardInterrupt:
    print("\n종료")
    if stream is not None:
        stream.stop()
        stream.close()
    if ser is not None:
        ser.close()