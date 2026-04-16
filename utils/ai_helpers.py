import os
from groq import Groq


def get_groq_key():
    """Safely get the Groq API key from config or environment."""
    try:
        from flask import current_app
        return current_app.config.get('GROQ_API_KEY') or os.getenv('GROQ_API_KEY')
    except RuntimeError:
        return os.getenv('GROQ_API_KEY')


def call_groq(prompt, image_data=None):
    """
    Calls Groq model (GPT-OSS-120B)
    NOTE: Groq currently doesn't support image input like DeepSeek-VL
    """

    if image_data:
        print("⚠️ Image input not supported in Groq yet")

    api_key = get_groq_key()
    if not api_key:
        print("❌ GROQ API key not configured")
        return None

    try:
        client = Groq(api_key=api_key)

        # ⚠️ Groq currently TEXT ONLY (image ignore kar rahe hain)
        completion = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=1,
            max_completion_tokens=1024,
            top_p=1,
            stream=False
        )

        return completion.choices[0].message.content.strip()

    except Exception as e:
        print(f"Groq error: {e}")
        return None