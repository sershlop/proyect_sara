@echo off
:: Cambiar el nombre con el que SARA te llama y las claves de IAs externas (Groq, Gemini, DeepSeek, Telegram).
:: Se guarda en app\.env. No necesita nada mas: reutiliza el Python propio de SARA.
call "%~dp0SARA.bat" --configurar
