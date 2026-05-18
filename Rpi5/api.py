# api.py 수정
from google import genai
import os

client = genai.Client()

try:
    # 모델명을 'gemini-2.5-flash'로 변경하여 시도
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents='인공지능에 대해 한 문장으로 설명하세요.',
    )
    print("\n" + "="*30)
    print(response.text)
    print("="*30 + "\n")

except Exception as e:
    print(f"API 호출 중 오류 발생: {e}")