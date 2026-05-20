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
        os.dup2(self.devnull, 2)
    def __exit__(self, *args):
        os.dup2(self.saved_stderr, 2)
        os.close(self.devnull)
        os.close(self.saved_stderr)
# -------------------------------------------------------------------

# --- [설정 및 초기화] ---
DEVICE_ID = 1      # [최적화] 파이 5 하드웨어 호환 마이크 장치 번호
SAMPLE_RATE = 48000 # [최적화] 채널 충돌 방지 샘플 레이트 고정
CSV_FILE = 'word.csv'

# 최신 Gemini Client 객체 생성
client = genai.Client()

# 전역 상태 제어 변수
topic_list = []
df = None
current_topic = None
topic_idx = 0
current_word_pool = []
learned_words = set()  # 현재 주제에서 이미 학습한 단어 누적 (중복 방지)
last_word = None       # [추가] 다시 듣기/보기(REPEAT) 기능용 최근 단어 저장

def initialize_and_load_csv():
    """CSV 파일을 로드하고 메모리 내부 상태를 동기화합니다."""
    global df, topic_list
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode='w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(["topic", "word"])
        print(f"[{CSV_FILE}] 파일이 존재하지 않아 새로 생성했습니다.")

    try:
        df = pd.read_csv(CSV_FILE)
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
    if ser is None: return 
        
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
        
        # [수정] 무한 대기(Deadlock) 방지용 타임아웃 추가
        start_time = time.time()
        timeout_seconds = 7.0 
        
        while True:
            if time.time() - start_time > timeout_seconds:
                print("[에러] 기기 응답 타임아웃 발생 (7초). 대기 상태를 해제합니다.")
                break
                
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
    try:
        os.system("wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.7")
        tts = gTTS(text=text, lang='ko')
        tts.save("temp.mp3")
        os.system("pw-play temp.mp3")
    except Exception as e:
        print(f"[오디오 출력 에러]: {e}")


# --- [AI 오퍼레이션 함수군] ---
def generate_words_for_new_theme(theme):
    """단어장에 없는 새로운 주제가 들어왔을 때 실존하는 2글자 이하의 단어를 자동 생성합니다."""
    print(f"[AI] '{theme}'은(는) 새로운 주제입니다. 실존 단어를 생성하는 중...")
    speak(f"새로운 주제인 {theme}에 대한 단어를 준비하고 있습니다. 잠시만 기다려 주세요.")
    
    system_instruction = """
    너는 점자 학습기용 단어 생성기야. 사용자가 요청한 주제에 맞는 한국어 단어들을 생성해야 해.
    
    [필수 규칙]
    1. 반드시 실제로 존재하는 명사 단어여야 해.
    2. 점자판 크기 제한으로 인해, 각 단어의 글자 수는 반드시 '최대 2글자(1글자 또는 2글자)'여야만 해. 3글자 이상은 절대 안 돼.
    3. 주제에 어울리는 대표적인 단어로 5개만 생성해줘.
    """
    
    global df
    # [수정] 기존 CSV 오염 방지: 중복 단어 필터링을 위한 기존 단어 풀 로드
    existing_words = set(df['word'].dropna().tolist()) if df is not None and not df.empty else set()
    
    # [수정] 엄격한 구조화된 출력 (Structured Output) 스키마 정의
    word_schema = types.Schema(
        type=types.Type.ARRAY,
        items=types.Schema(type=types.Type.STRING)
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"주제: {theme}",
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=word_schema,
                    temperature=0.7 # 다양한 단어 생성을 위한 온도 조절
                )
            )
            
            new_words = json.loads(response.text)
            
            # [수정] LLM의 규칙 위반 시 필터링 및 중복 검증
            filtered_words = [w for w in new_words if len(w) <= 2 and w not in existing_words]
            filtered_words = list(dict.fromkeys(filtered_words)) # 리스트 내 자체 중복 제거
            
            if filtered_words:
                with open(CSV_FILE, mode='a', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f)
                    for word in filtered_words:
                        writer.writerow([theme, word])
                initialize_and_load_csv()
                print(f"[시스템] CSV 업데이트 완료: '{theme}' 주제의 단어 {len(filtered_words)}개 추가.")
                return filtered_words
            else:
                print(f"[경고] {attempt + 1}차 시도: 유효하거나 새로운 단어가 부족합니다. 재시도합니다.")
                
        except Exception as e:
            print(f"[에러] 신규 단어 생성 실패 ({attempt + 1}/{max_retries}): {e}")
            time.sleep(1)
            
    print("[에러] 유효한 단어 생성에 실패했습니다.")
    return []


def output_next_word():
    """현재 지정된 데이터 풀 안에서 중복되지 않은 임의의 단어를 추출하여 기기로 전송합니다."""
    global current_topic, current_word_pool, last_word
    
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
    
    # [추가] 다시 듣기 기능을 위한 단어 저장
    last_word = target_word

    speak(f"단어는 {target_word}입니다.")
    send_braille_to_pico(target_word)


def load_topic_by_name(theme_name):
    """지정된 주제 명칭에 맞춰 세션을 구성하고 첫 번째 단어를 띄웁니다."""
    global current_topic, current_word_pool, learned_words, topic_idx
    
    current_topic = theme_name
    if current_topic in topic_list:
        topic_idx = topic_list.index(current_topic)
        
    current_word_pool = df[df['topic'] == current_topic].to_dict('records')
    learned_words.clear()
    
    speak(f"{current_topic} 주제를 시작합니다.")
    output_next_word()


# --- [AI 지능형 의도 처리 엔진] ---
def process_voice_with_llm(text):
    global current_topic, current_word_pool, topic_idx, learned_words, last_word
    
    text = text.strip()
    if not text: return False

    print(f"[음성 인식 텍스트]: {text}")

    # 로컬 강제 종료 키워드 1차 선별 (네트워크/API 지연 없이 즉시 종료하기 위함)
    exit_keywords = ["종료", "종료해", "끝내", "그만"]
    if any(k in text.replace(" ", "") for k in exit_keywords):
        return "EXIT"

    # [수정] REPEAT, UNKNOWN 액션에 대한 지침 세분화 및 자연스러운 응답 필드 추가
    system_instruction = f"""
    너는 점자 학습기의 의도 분석기야. 사용자의 대화에서 의도(action)와 주제(theme), 자연스러운 응답(answer)을 분석해줘.
    
    1. action의 종류:
       - START: 특정 주제로 학습을 시작 (예: "날씨 공부하자", "가족 단어 보여줘", "축구", "아무거나 골라줘")
       - STOP: 학습 중지 (예: "잠깐 멈춰줘", "주제 초기화", "처음으로")
       - NEXT: 다음 단어로 이동 (예: "다음", "넘어가자", "맞췄어")
       - LIST: 주제 목록 질문 (예: "카테고리 뭐 있어?")
       - REPEAT: 현재 단어를 다시 듣거나 점자 기기로 다시 출력하길 원할 때 (예: "다시", "한 번 더", "뭐였지?", "다시 보여줘")
       - UNKNOWN: 위 의도에 해당하지 않는 일상 대화나 감탄사 (예: "오, 신기하다!", "재밌네", "너 이름이 뭐야?")
       
    2. theme 분류 및 매핑 규칙:
       - 현재 시스템 등록 표준 주제 목록: {topic_list}
       - "랜덤", "아무거나 해줘" 발화 시 theme를 'RANDOM'으로 지정.
       - 표준 목록에 상위 카테고리가 있다면 상위 카테고리로 매핑.
       - 목록에 없는 새로운 주제일 때만 사용자가 입력한 단어 자체를 theme로 설정.
       
    3. answer 작성 규칙:
       - action이 UNKNOWN일 경우, 사용자의 말에 어울리는 짧고 자연스러운 맞장구나 안내 멘트를 `answer` 필드에 작성.
       - 그 외 action일 경우 `answer`는 빈 문자열로 둬도 무방함.
    """
    
    # [수정] 의도 분석기 스키마 정의 (JSON 텍스트 정제 불필요)
    intent_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action": types.Schema(type=types.Type.STRING),
            "theme": types.Schema(type=types.Type.STRING, nullable=True),
            "answer": types.Schema(type=types.Type.STRING, nullable=True)
        },
        required=["action"]
    )

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=intent_schema
            )
        )
        
        result = json.loads(response.text)
        action = result.get("action")
        theme = result.get("theme")
        answer = result.get("answer")
        
        print(f"[AI 분석 결과] 의도: {action} | 감지된 주제: {theme} | 생성된 응답: {answer}")
        
        if action == "START" and theme:
            if theme.upper() == "RANDOM":
                if not topic_list:
                    speak("현재 등록된 주제가 없습니다.")
                    return True
                theme = random.choice(topic_list)
                speak("제가 새로운 주제를 하나 골라보았습니다.")

            if theme not in topic_list:
                generated = generate_words_for_new_theme(theme)
                if not generated:
                    speak("단어 생성에 실패했습니다. 다시 말씀해 주세요.")
                    return True
            
            load_topic_by_name(theme)
            return True
                
        elif action == "NEXT":
            output_next_word()
            return True
        
        elif action == "STOP":
            speak("학습을 일시 중지합니다. 원하시는 새로운 주제를 말씀해 주세요.")
            current_topic = None
            current_word_pool = []
            learned_words.clear()
            return True

        elif action == "LIST":
            if not topic_list:
                speak("현재 등록된 주제 카테고리가 비어 있습니다.")
            else:
                speak(f"현재 선택 가능한 주제는 {', '.join(topic_list[:6])} 등이 있습니다.")
            return True
            
        elif action == "REPEAT":
            if last_word:
                speak(f"다시 알려드릴게요. 단어는 {last_word}입니다.")
                send_braille_to_pico(last_word)
            else:
                speak("이전에 출력한 단어가 없습니다. 새로운 주제를 시작해 주세요.")
            return True
            
        elif action == "UNKNOWN":
            if answer:
                speak(answer)
            else:
                speak("잘 이해하지 못했습니다. 단어 학습을 이어서 진행하려면 다음 단어를 요청해 주세요.")
            return True
            
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            print("\n🚨 [시스템 안내] Gemini API 호출 제한이 발생했습니다. 1분 후 다시 시도해 주세요.")
        else:
            print(f"[의도 분석 에러]: {e}")
            
    return False


# --- [구글 STT 메인 실행부] ---
r = sr.Recognizer()

try:
    print(f"--- 지능형 점자 학습기 가동 준비 완료 (하드웨어 연결 최적화 모드) ---")
    speak("공부할 주제를 말씀하세요.")
    
    with sr.Microphone(device_index=DEVICE_ID, sample_rate=SAMPLE_RATE) as source:
        r.adjust_for_ambient_noise(source, duration=1.0)
        
        while True:
            try:
                print("\n[대기 중] 마이크에 대고 말씀해 주세요...")
                with SuppressStderr():
                    audio = r.listen(source, timeout=10, phrase_time_limit=5)

                text = r.recognize_google(audio, language='ko-KR')
                
                result = process_voice_with_llm(text)
                
                if result == "EXIT":
                    speak("학습을 종료합니다.")
                    if ser is not None:
                        exit_packet = "\n<EXIT>\n"
                        print(f"[피코 전송] 시스템 종료 패킷: {exit_packet.strip()}")
                        ser.write(exit_packet.encode())
                        time.sleep(0.5)
                    break
            
            except sr.WaitTimeoutError:
                pass  
            except sr.UnknownValueError:
                pass  
            except sr.RequestError as e:
                print(f"[네트워크 에러] 구글 STT 서버 연동 실패: {e}")

except KeyboardInterrupt:
    print("\n사용자에 의해 강제 종료됨")
finally:
    print("시스템을 닫습니다.")
    if ser is not None:
        ser.close()