from typing import Any, Iterable, Literal

from groq import AsyncGroq
from config import logger
from config import settings
from groq.types.chat import ChatCompletionMessageParam


class AiModel:

    def __init__(self):
        self.active = False
        if settings.use_ai_in_chat:
            try:
                self.client = AsyncGroq(api_key=settings.groq_api_key)
                self.active = True
            except Exception as e:
                logger.error(f"AI Model client activation failed: {e}") 


    async def ask_me(self, content:str) -> str | None:
        response:str | None = "AI is inactive"        
        if self.active:
            try:
                chat_completion = await self.client.chat.completions.create(
                                    messages = [{"role": "user", "content": content}],
                                    model = settings.groq_model_name,        
                                )
                response = chat_completion.choices[0].message.content
            except Exception as e:
                response = f"AI Model does not respond. Error: {e}"
                logger.error(response) 
        return response    

    async def send_session_history(self, sess_history: list[ChatCompletionMessageParam]) -> str | None:
        response:str | None = "AI is inactive"        
        if self.active:
            try:
                chat_completion = await self.client.chat.completions.create(
                                    messages = sess_history,
                                    model = settings.groq_model_name,        
                                )
                response = chat_completion.choices[0].message.content
            except Exception as e:
                response = f"AI Model does not respond. Error: {e}"
                logger.error(response) 
        return response    



aiModel = AiModel()
