import os
import google.generativeai as genai
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

def get_completion(prompt: str, system_instruction: str = "", model_choice: str = "gemini") -> str:
    """
    Cliente de LLM Híbrido: Gemini (Primário) com Fallback para OpenAI.
    """
    
    # 1. Tentativa com Gemini
    if model_choice == "gemini":
        try:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY not found")
            
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(
                model_name="gemini-1.5-flash",
                system_instruction=system_instruction
            )
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            print(f"⚠️ Erro Gemini: {e}. Tentando OpenAI fallback...")
            model_choice = "openai"

    # 2. Fallback / Opção OpenAI
    if model_choice == "openai":
        try:
            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt}
                ]
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"❌ Erro Crítico em todos os LLMs: {e}"

    return "❌ Erro: Nenhum modelo disponível."
