import os
import sys
import time
import random
import pandas as pd
import serial
import braillify
import speech_recognition as sr  # [변경] 구글 STT용
from gtts import gTTS            # [변경] 구글 TTS용

# -------------------------------------------------------------------
# [에러 메시지 완벽 차단 로직]
# -------------------------------------------------------------------
from ctypes import *
try:
    ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
    def py_error_handler(filename, line, function, err, fmt): pass
    c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
    asound = cdll.LoadLibrary('libasound.so.2')
    asound.snd_lib_error_set_handler(c_error_handler)
except OSError: pass

class SuppressStderr:
    def __enter__(self):
        self.saved_stderr = os.dup(2)
        self.devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(self.devnull, 2)
    def __exit__(self, *args):
        os.dup2(self.saved_stderr, 2)
        os.close(self.devnull)
        os.close(self.saved_stderr)
# -------------------------------------------------------------------

# --- [설정 및 초기화] ---
DEVICE_ID = 0  # 마이크 번호 (PyAudio 기준)

# 1. 데이터베이스(CSV) 로드
CSV_FILE = 'word_natural_2chars.csv'
try:
    df = pd.read_csv(CSV_FILE)
    topic_list = df['topic'].unique().tolist()
    print(f"[{CSV_FILE}] 로드 완료! 총 {len(topic_list)}개의 주제가 있습니다.")
except Exception as e:
    print(f"CSV 로드 오류: {e}")
    sys.exit()

# 2. 피코 시리얼 통신 초기화
print("피코와 연결 중...")
try:
    ser = serial.Serial('/dev/ttyACM0', 115200)
    print("피코 호밍 및 준비 대기 중 (5초)...")
    time.sleep(5)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
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
                
        packet = f"\n<{bit_pattern}>\n"
        print(f"\n[피코 전송] 단어: {text} -> 패킷: <{bit_pattern}>")
        ser.write(packet.encode())
        
        print("[대기 중] 점자 기기 출력이 끝날 때까지 기다립니다...")
        while True:
            if ser.in_waiting > 0:
                received_data = ser.readline().decode('utf-8', errors='ignore').strip()
                if received_data == "<DONE>":
                    print("[완료] 피코 출력 완료 신호 수신!\n")
                    break
                elif received_data:
                    print(f"[피코 상태] {received_data}")
            time.sleep(0.01) 
    except Exception as e:
        print(f"[점자 전송 에러]: {e}")

# --- [신규: 구글 gTTS 음성 출력 함수] ---
def speak(text):
    print(f"시스템 음성: {text}")
    time.sleep(0.3)
    
    try:
        os.system("wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.7")
        
        # 구글 서버에서 음성을 mp3로 받아오기
        tts = gTTS(text=text, lang='ko')
        tts.save("temp.mp3")
        
        # 노이즈 없는 pw-play로 mp3 파일 재생
        os.system("pw-play temp.mp3")
    except Exception as e:
        print(f"[오디오 출력 에러]: {e}")
        
    time.sleep(0.3)

# --- [로직 처리 함수] ---
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
    if not text: return False

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

# --- [신규: 구글 STT 메인 실행부] ---
r = sr.Recognizer()

try:
    print(f"--- 시스템 가동 준비 완료 (온라인 모드) ---")
    speak("공부할 주제를 말씀하세요.")
    
    while True:
        try:
            # 블랙홀 클래스로 불필요한 C 레벨 경고 완벽 차단
            with SuppressStderr():
                with sr.Microphone(device_index=DEVICE_ID) as source:
                    r.adjust_for_ambient_noise(source, duration=0.5)
                    print("\n[대기 중] 마이크에 대고 말씀해 주세요...")
                    # 타임아웃을 걸어 무한정 대기하는 것을 방지
                    audio = r.listen(source, timeout=5, phrase_time_limit=5)

            # 구글 서버로 오디오 데이터를 보내 텍스트로 변환
            text = r.recognize_google(audio, language='ko-KR')
            process_voice(text)
            
        except sr.WaitTimeoutError:
            pass # 5초간 말이 없으면 조용히 다시 대기 루프로 돌아감
        except sr.UnknownValueError:
            pass # 말은 했으나 구글이 인식하지 못했을 때 무시
        except sr.RequestError as e:
            print(f"[네트워크 에러] 구글 서버에 연결할 수 없습니다: {e}")

except KeyboardInterrupt:
    print("\n종료")
    if ser is not None:
        ser.close()
