import os
import csv
import json
import random
import re
from google import genai
from google.genai import types

# AIzaSyCU-rsP_lqsOzPZPmv0gZyAyZYQx07eKq0

# 최신 Gemini Client 객체 생성
client = genai.Client()

# CSV 파일 경로 설정
CSV_FILE_PATH = r"C:\Users\n\Desktop\word.csv"

class BrailleWordManager:
    def __init__(self, csv_path):
        self.csv_path = csv_path
        self.existing_themes = set()
        
        # 현재 세션에서 진행 중인 상태 제어 변수
        self.current_theme = None      # 현재 학습 중인 주제 저장
        self.learned_words = set()     # 현재 주제에서 이미 학습한 단어 저장 (중복 방지)
        
        self.initialize_csv()
        self.load_themes_to_memory()

    def initialize_csv(self):
        """CSV 파일이 없으면 헤더를 포함하여 새로 생성합니다."""
        if not os.path.exists(self.csv_path):
            with open(self.csv_path, mode='w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(["주제", "단어"])
            print(f"[시스템] {self.csv_path} 파일이 존재하지 않아 새로 생성했습니다.")

    def load_themes_to_memory(self):
        """CSV 파일을 읽어 존재하는 고유 주제들을 메모리(Set)에 저장합니다."""
        self.existing_themes.clear()
        with open(self.csv_path, mode='r', newline='', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            next(reader, None)  # 헤더 건너뛰기
            for row in reader:
                if row:
                    self.existing_themes.add(row[0])  # 1열이 주제
        print(f"[시스템] 현재 등록된 주제 리스트 (총 {len(self.existing_themes)}개): {list(self.existing_themes)}")

    def get_all_words_in_theme(self, theme):
        """지정된 주제에 해당하는 CSV 내의 모든 단어 리스트를 반환합니다."""
        words = []
        with open(self.csv_path, mode='r', newline='', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if row and row[0] == theme:
                    words.append(row[1])
        return words

    def output_next_word(self):
        """현재 주제에서 중복되지 않은 다음 단어를 추출하여 출력합니다."""
        if not self.current_theme:
            print("[시스템] 현재 진행 중인 주제가 없습니다. 먼저 주제를 지정해 주세요. (예: '음악 시작')")
            return None

        all_words = self.get_all_words_in_theme(self.current_theme)
        remaining_words = [w for w in all_words if w not in self.learned_words]

        if not remaining_words:
            print(f"\n📢 [안내] '{self.current_theme}' 주제의 모든 단어({len(all_words)}개)를 학습 완료했습니다!")
            print("[시스템] 학습을 종료합니다. 새로운 주제를 선택해 주세요.")
            self.current_theme = None
            self.learned_words.clear()
            return None

        selected_word = random.choice(remaining_words)
        self.learned_words.add(selected_word)
        
        print(f"[출력 단어] ({len(self.learned_words)}/{len(all_words)}) 추출된 단어: {selected_word} (글자 수: {len(selected_word)})")
        return selected_word

    def append_new_words(self, theme, words):
        """새로운 주제와 단어들을 CSV 파일 끝에 추가하고 메모리도 갱신합니다."""
        with open(self.csv_path, mode='a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            for word in words:
                writer.writerow([theme, word])
        
        self.existing_themes.add(theme)
        print(f"[시스템] CSV 업데이트 완료: '{theme}' 주제의 단어 {len(words)}개가 추가되었습니다.")

    def clean_json_text(self, text):
        """AI 응답 텍스트에서 마크다운 코드 블록 등을 제거하여 순수 JSON만 추출합니다."""
        text = text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*```$', '', text)
        return text.strip()

    def generate_words_for_new_theme(self, theme):
        """단어장에 없는 새로운 주제가 들어왔을 때, 2글자 이하의 실존 단어를 생성합니다."""
        print(f"[AI] '{theme}'은(는) 새로운 주제입니다. 맞춤형 단어를 생성합니다...")
        
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
            # 원래 사용하던 gemini-2.5-flash 모델로 원상복구
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=f"주제: {theme}",
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json"
                )
            )
            raw_text = self.clean_json_text(response.text)
            new_words = json.loads(raw_text)
            filtered_words = [w for w in new_words if len(w) <= 2]
            return filtered_words
        except Exception as e:
            print(f"[에러] 단어 생성 중 오류 발생: {e}")
            return []

    def analyze_and_process(self, user_text):
        """사용자 입력을 받아 의도를 파악하고, 필요시 CSV를 자동으로 업데이트 및 단어를 반환합니다."""
        current_themes_list = list(self.existing_themes)
        
        system_instruction = f"""
        너는 점자 학습기의 의도 분석기야. 사용자의 대화에서 의도(action)와 주제(theme)를 분석해줘.
        
        1. action의 종류:
           - START: 특정 주제로 학습을 시작하고 싶어할 때. (예: "날씨", "가족", "축구하자", "아무거나 골라줘")
           - STOP: 학습을 그만두거나 종료하고 싶어할 때 (예: "그만", "종료", "끝내자")
           - NEXT: 다음 단어로 넘어가고 싶어할 때 (예: "다음", "넘어가자", "맞췄어")
           - LIST: 현재 등록되어 있는 주제 목록이 무엇인지 질문하거나 확인하고 싶어할 때 (예: "주제 어떤 거 있어?", "무슨 주제가 등록돼 있니?", "카테고리 보여줘")
           - UNKNOWN: 위의 네 가지 의도에 전혀 해당하지 않는 일반적인 대화나 감탄사
           
        2. theme 분류 및 매핑 규칙:
           - 현재 시스템에 등록된 표준 주제 리스트는 다음과 같아: {current_themes_list}
           - 사용자가 "랜덤으로 선택해줘", "아무거나 해줘" 등 임의 설정을 원할 때는 반드시 theme를 'RANDOM'으로 지정해.
           - 사용자가 특정 단어(예: 농구, 축구)를 말하면, 표준 주제 리스트에 포함되는 상위 개념인 '운동'으로 매핑해야 해.
           - 리스트에 없는 완전히 새로운 분야일 때만 사용자가 말한 핵심 단어를 theme로 설정해.
           
        3. 출력 형식:
           - 설명 없이 오직 JSON 형식으로만 답변해. 예시: {{"action": "START", "theme": "우주"}}
        """

        try:
            # 원래 사용하던 gemini-2.5-flash 모델로 원상복구
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=user_text,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json"
                )
            )
            raw_text = self.clean_json_text(response.text)
            result = json.loads(raw_text)
            action = result.get("action")
            theme = result.get("theme")
            
            print(f"\n[AI 분석 결과] 의도: {action}, 감지된 주제: {theme}")
            
            # 1. 학습 시작 (START)
            if action == "START" and theme:
                if theme.upper() == "RANDOM":
                    chosen_theme = random.choice(current_themes_list)
                    print(f"[시스템] 사용자의 랜덤 요청으로 '{chosen_theme}' 주제가 임의 선택되었습니다.")
                    theme = chosen_theme

                self.current_theme = theme
                self.learned_words.clear()

                if theme not in self.existing_themes:
                    generated_words = self.generate_words_for_new_theme(theme)
                    if generated_words:
                        self.append_new_words(theme, generated_words)
                else:
                    print(f"[시스템] '{theme}' 주제는 이미 단어장에 존재하므로 기존 데이터를 사용합니다.")
                
                self.output_next_word()
                    
            # 2. 다음 단어 요청 (NEXT)
            elif action == "NEXT":
                if self.current_theme:
                    print(f"[시스템] '{self.current_theme}' 주제의 다음 단어를 추출합니다.")
                    self.output_next_word()
                else:
                    print("[시스템] 진행 중인 주제가 없습니다. 먼저 공부할 주제를 말씀해 주세요.")
            
            # 3. 학습 종료 (STOP)
            elif action == "STOP":
                if self.current_theme:
                    print(f"[시스템] '{self.current_theme}' 학습을 종료합니다. 상태를 초기화합니다.")
                else:
                    print("[시스템] 현재 진행 중인 학습이 없습니다.")
                self.current_theme = None
                self.learned_words.clear()

            # 4. 주제 리스트 조회 (LIST)
            elif action == "LIST":
                print(f"\n📢 [AI 답변] 현재 선택 가능한 주제는 총 {len(current_themes_list)}개입니다.")
                print(f"👉 리스트: {', '.join(current_themes_list)}")
                if self.current_theme:
                    print(f"💡 현재는 '{self.current_theme}' 주제를 학습 중입니다.")
                
            return result
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                print("\n🚨 [시스템 안내] Gemini API의 일일 무료 호출 제한을 초과했습니다.")
            elif "404" in error_msg or "NOT_FOUND" in error_msg:
                print("\n🚨 [시스템 안내] 선택한 모델이 유효하지 않거나 주소를 찾을 수 없습니다.")
            elif "503" in error_msg or "UNAVAILABLE" in error_msg:
                print("\n🚨 [시스템 안내] 구글 서버가 일시적으로 혼잡합니다. 잠시 후 다시 시도해 주세요.")
            else:
                print(f"\n[에러] 의도 파악 중 오류 발생: {e}")
                
            return {"action": "UNKNOWN", "theme": None}

# === 모듈 검증 테스트 ===
if __name__ == "__main__":
    manager = BrailleWordManager(CSV_FILE_PATH)
    
    print("\n=== 점자 학습기 의도 파악 및 단어장 자동 추가 테스트 ===")
    while True:
        user_input = input("\n사용자 음성 입력 (종료: q): ")
        if user_input.lower() == 'q':
            break
            
        manager.analyze_and_process(user_input)