# pip install google-genai 이거 설치할 것

import os
import sys
import time
import random
import re
import json
import csv
import pandas as pd
import serial
import braillify
import speech_recognition as sr
from gtts import gTTS
from google import genai
from google.genai import types

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
        self.dup2(self.devnull, 2)
    def __exit__(self, *args):
        os.dup2(self.saved_stderr, 2)
        os.close(self.devnull)
        os.close(self.saved_stderr)
# -------------------------------------------------------------------

# --- [설정 및 초기화] ---
DEVICE_ID = 0  # 마이크 번호 (PyAudio 기준)
CSV_FILE = 'word_natural_2chars.csv'

# 최신 Gemini Client 객체 생성
client = genai.Client()

# 전역 상태 제어 변수
topic_list = []
df = None
current_topic = None
topic_idx = 0
current_word_pool = []
current_word_idx = 0
learned_words = set()  # 현재 주제에서 이미 출력한 단어 저장 (중복 추출 방지)

def initialize_and_load_csv():
    """CSV 파일을 로드하고 메모리 내부 상태를 동기화합니다."""
    global df, topic_list
    # CSV 파일이 없으면 초기 헤더 생성
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode='w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(["topic", "word"])
        print(f"[{CSV_FILE}] 파일이 존재하지 않아 새로 생성했습니다.")

    try:
        df = pd.read_csv(CSV_FILE)
        # 빈 데이터프레임 예외 처리
        if df.empty:
            topic_list = []
        else:
            topic_list = df['topic'].unique().tolist()
        print(f"[{CSV_FILE}] 동기화 완료! 현재 등록된 총 주제 개수: {len(topic_list)}개")
    except Exception as e:
        print(f"CSV 로드 오류: {e}")
        sys.exit()

# 최초 1회 데이터 로드
initialize_and_load_csv()

# 피코 시리얼 통신 초기화
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


# --- [구글 gTTS 음성 출력 함수] ---
def speak(text):
    print(f"시스템 음성: {text}")
    time.sleep(0.3)
    
    try:
        os.system("wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.7")
        tts = gTTS(text=text, lang='ko')
        tts.save("temp.mp3")
        os.system("pw-play temp.mp3")
    except Exception as e:
        print(f"[오디오 출력 에러]: {e}")
        
    time.sleep(0.3)


# --- [AI 헬퍼 함수] ---
def clean_json_text(text):
    """AI 응답 텍스트에서 마크다운 코드 블록 제거 후 순수 JSON만 추출합니다."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*
```$', '', text)
    return text.strip()


def generate_words_for_new_theme(theme):
    """단어장에 없는 새로운 주제가 탐지되었을 때, 2글자 이하의 실존 단어를 자동 생성하고 CSV에 추가합니다."""
    print(f"[AI] '{theme}'은(는) 새로운 주제입니다. 맞춤형 단어를 생성합니다...")
    speak(f"새로운 주제인 {theme}에 대한 단어를 준비하고 있습니다. 잠시만 기다려 주세요.")
    
    system_instruction = """
    너는 점자 학습기용 단어 생성기야. 사용자가 요청한 주제에 맞는 한국어 단어들을 생성해야 해.
    
    [필수 규칙]
    1. 반드시 실제로 존재하는 명사 단어여야 해.
    2. 점자판 크기 제한으로 인해, 각 단어의 글자 수는 반드시 '최대 2글자(1글자 또는 2글자)'여야만 해. 3글자 이상은 절대 안 돼.
    3. 주제에 어울리는 대표적인 단어로 5개만 생성해줘.
    4. 출력 형식은 설명 없이 오직 순수한 JSON 문자열 배열 형식으로만 답변해.
       예시: ["사과", "배", "포도", "감", "귤"]
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"주제: {theme}",
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json"
            )
        )
        raw_text = clean_json_text(response.text)
        new_words = json.loads(raw_text)
        filtered_words = [w for w in new_words if len(w) <= 2]
        
        # 파일에 누적 기록 및 동기화
        if filtered_words:
            with open(CSV_FILE, mode='a', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                for word in filtered_words:
                    writer.writerow([theme, word])
            initialize_and_load_csv()
            print(f"[시스템] CSV 업데이트 완료: '{theme}' 주제의 단어 {len(filtered_words)}개가 추가되었습니다.")
        return filtered_words
    except Exception as e:
        print(f"[에러] 신규 단어 생성 실패: {e}")
        return []


def output_next_word():
    """현재 지정된 pool 안에서 중복되지 않은 임의의 단어를 추출하고 점자 기기로 전송합니다."""
    global current_word_idx, current_topic, current_word_pool
    
    if not current_topic:
        speak("공부하고 싶은 주제를 말씀하시거나, 랜덤 주제라고 말씀해 주세요.")
        return

    remaining_words = [w for w in current_word_pool if w['word'] not in learned_words]

    if not remaining_words:
        speak(f"{current_topic} 주제의 모든 단어를 학습 완료했습니다. 새로운 주제를 골라주세요.")
        current_topic = None
        current_word_pool = []
        learned_words.clear()
        return

    selected = random.choice(remaining_words)
    target_word = selected['word']
    learned_words.add(target_word)

    speak(f"단어는 {target_word}입니다.")
    send_braille_to_pico(target_word)


def load_topic_by_name(theme_name):
    """지정된 주제 명칭에 맞춰 세션을 재구성하고 첫 번째 단어를 출력합니다."""
    global current_topic, current_word_pool, learned_words, topic_idx
    
    current_topic = theme_name
    if current_topic in topic_list:
        topic_idx = topic_list.index(current_topic)
        
    current_word_pool = df[df['topic'] == current_topic].to_dict('records')
    learned_words.clear()
    
    speak(f"{current_topic} 주제를 시작합니다.")
    output_next_word()


# --- [AI 지능형 의도 처리 메인 루프] ---
def process_voice_with_llm(text):
    global current_topic, current_word_pool, topic_idx, learned_words
    
    text = text.strip()
    if not text: return False

    print(f"[음성 인식 텍스트]: {text}")

    system_instruction = f"""
    너는 점자 학습기의 의도 분석기야. 사용자의 대화에서 의도(action)와 주제(theme)를 분석해줘.
    
    1. action의 종류:
       - START: 특정 주제로 학습을 시작하고 싶어할 때. (예: "날씨 공부하자", "가족 단어 보여줘", "축구", "아무거나 골라줘")
       - STOP: 학습을 그만두거나 처음 화면으로 가고 싶어할 때 (예: "그만", "종료", "처음으로", "주제 초기화")
       - NEXT: 다음 단어로 넘어가고 싶어할 때 (예: "다음", "넘어가자", "맞췄어", "그 다음 단어")
       - LIST: 현재 어떤 주제들이 등록되어 있는지 물어볼 때 (예: "카테고리 뭐 있어?", "등록된 주제 알려줘")
       - UNKNOWN: 단순 감탄사나 학습기와 무관한 대화일 때
       
    2. theme 분류 및 매핑 규칙:
       - 현재 대용량 단어장 시스템에 등록된 표준 주제 목록은 다음과 같아: {topic_list}
       - 사용자가 "랜덤", "아무거나 해줘"라고 발화하면 반드시 theme를 'RANDOM'으로 지정해야 해.
       - 사용자가 특정 단어(예: 축구, 농구)를 언급했으나, 표준 목록에 상위 카테고리(예: 운동)가 있다면 theme를 상위 카테고리인 '운동'으로 매핑해줘.
       - 목록에 존재하지 않는 완전히 새로운 단어나 가치관일 때만 사용자가 입력한 핵심 단어 자체를 theme로 설정해.
       
    3. 출력 형식:
       - 설명 없이 오직 JSON 형식으로만 답변해. 예시: {{"action": "START", "theme": "우주"}}
    """

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json"
            )
        )
        raw_text = clean_json_text(response.text)
        result = json.loads(raw_text)
        action = result.get("action")
        theme = result.get("theme")
        
        print(f"[AI 분석 결과] 의도: {action} | 감지된 주제: {theme}")
        
        # 1. 학습 시작 (START)
        if action == "START" and theme:
            if theme.upper() == "RANDOM":
                if not topic_list:
                    speak("현재 등록된 주제가 없습니다.")
                    return True
                theme = random.choice(topic_list)
                speak("제가 재미있는 주제를 하나 골라보았습니다.")

            if theme not in topic_list:
                generated = generate_words_for_new_theme(theme)
                if not generated:
                    speak("단어 생성에 실패했습니다. 다시 말씀해 주세요.")
                    return True
            
            load_topic_by_name(theme)
            return True
                
        # 2. 다음 단어 (NEXT)
        elif action == "NEXT":
            output_next_word()
            return True
        
        # 3. 정지 및 초기화 (STOP)
        elif action == "STOP":
            speak("알겠습니다. 학습을 일시 중지하고 대기 상태로 전환합니다. 새로운 주제를 말씀해 주세요.")
            current_topic = None
            current_word_pool = []
            learned_words.clear()
            return True

        # 4. 등록 리스트 조회 (LIST)
        elif action == "LIST":
            if not topic_list:
                speak("현재 등록된 주제 카테고리가 비어 있습니다.")
            else:
                speak(f"현재 선택 가능한 주제는 {', '.join(topic_list[:6])} 등이 있습니다.")
            return True
            
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            print("\n🚨 [시스템 안내] Gemini API 분당 호출 제한이 발생했습니다. 1분만 휴식 후 사용해 주세요.")
        else:
            print(f"[의도 분석 에러]: {e}")
            
    return False


# --- [구글 STT 메인 실행부] ---
r = sr.Recognizer()

try:
    print(f"--- 지능형 점자 학습기 가동 준비 완료 ---")
    speak("공부할 주제를 말씀하세요.")
    
    while True:
        try:
            with SuppressStderr():
                with sr.Microphone(device_index=DEVICE_ID) as source:
                    r.adjust_for_ambient_noise(source, duration=0.5)
                    print("\n[대기 중] 음성 명령을 기다리는 중입니다...")
                    audio = r.listen(source, timeout=5, phrase_time_limit=5)

            # 구글 클라우드 STT 연동
            text = r.recognize_google(audio, language='ko-KR')
            # AI 의도 분석 프레임워크로 전송
            process_voice_with_llm(text)
            
        except sr.WaitTimeoutError:
            pass  # 입력 공백 발생 시 다시 무음 루프로 복귀
        except sr.UnknownValueError:
            pass  # 노이즈 혹은 음성 불일치 시 무시
        except sr.RequestError as e:
            print(f"[네트워크 에러] STT 서버 연결 불가능: {e}")

except KeyboardInterrupt:
    print("\n종료")
    if ser is not None:
        ser.close()