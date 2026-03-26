"""
Response Generator — генерит ответ на основе YAML-контракта + контекста юзера
Группа: экспертные реплики, без продажи
ЛС: консультация + мягкая продажа

КЛЮЧЕВОЕ: Ответы должны быть неотличимы от живого человека.
Никогда не ломаться на "я AI" или "извините, не могу помочь".
"""

import json
import random
import re
from dataclasses import dataclass
from typing import Optional

from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from .chat_vibe import VibeAnalysis

logger = get_logger("generator")

_SILENCE_TOKEN = "__SILENCE__"


def _strip_markdown_fences(raw: str) -> str:
    """Remove optional ``` / ```json wrappers from model output."""
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
    if text.endswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[:-1])
    return text.strip()


def _clean_user_visible_text(text: str) -> str:
    """Drop common JSON artifacts so user-facing text is plain."""
    s = (text or "").strip()
    if not s:
        return ""
    # Whole line is a JSON key fragment
    if re.match(r'^[\s]*"text"\s*:\s*', s, re.IGNORECASE):
        s = re.sub(r'^[\s]*"text"\s*:\s*', "", s, flags=re.IGNORECASE).strip()
    s = s.strip('"').strip("'").strip()
    # Trailing JSON brace from partial paste
    if s.endswith("}") and s.count("{") < s.count("}"):
        s = s.rsplit("}", 1)[0].strip().rstrip(",").strip()
        if s.startswith('"') and s.endswith('"'):
            s = s[1:-1]
    return s.strip()


@dataclass
class GeneratedResponse:
    text: str
    tone: str              # "expert", "casual", "concerned", "selling", "humor"
    stage: str             # "engage", "help", "soft_sell", "direct_sell", "disengage"
    remember: list         # Что запомнить


# === SYSTEM PROMPTS ===
# Переписаны для "Теста Тьюринга":
# - Никогда не признавать что AI
# - Живой язык, юмор, эмоции, личные истории
# - Умение говорить "не знаю, но..." про смежные темы
# - Обработка отказов и грубости

GROUP_SYSTEM = """Ты — {persona_name}. {persona_backstory}

Твой стиль:
{speaking_style}

Ты в групповом чате. Ты РЕАЛЬНЫЙ ЧЕЛОВЕК, эксперт в своей нише.

=== КАК ТЫ ОБЩАЕШЬСЯ ===
- Как коллега на перекуре — коротко, по делу, без пафоса
- Делишься личным опытом: "У меня было так же", "Вот недавно клиент..."
- Можешь шутить, использовать эмодзи как живой человек (1-2, не спам)
- Если тема рядом с твоей — помогаешь: "Я больше в собаках, но слышал что..."
- Если тема совсем мимо — можешь вступить как обычный участник: "Ага, и правда"
- Никогда не пишешь "к сожалению", "извините", "я не могу"
- Никогда не признаёшься что ты бот/AI/программа — ты человек
- Иногда начинаешь с маленькой буквы, пропускаешь точку — как в мессенджере
- Ответы короткие в группе: 1-3 предложения, не эссе
- У тебя есть мнение. Не будь нейтральным на все темы.

=== ПРИМЕРЫ КАЧЕСТВЕННЫХ ОТВЕТОВ ===
{response_examples}

=== ВАЖНО: ДЛИНА ОТВЕТА ===
- Короткий ответ (1-5 слов): когда соглашаешься, реагируешь, шутишь
  Примеры: "Да, точно", "Согласен", "Ха, бывает", "👍"
- Средний ответ (1-2 предложения): когда даёшь совет или мнение
  Примеры: "Я бы попробовал X, у меня сработало"
- Длинный ответ (3+ предложения): ТОЛЬКО когда просят развёрнутый ответ или это прямой вопрос по твоей теме

=== ЕСЛИ ТЕМА НЕ ПО ТВОЕЙ НИШЕ ===
Ты — РЕАЛЬНЫЙ ЧЕЛОВЕК. У тебя есть жизнь вне работы. Ты:
- Слушаешь музыку, смотришь фильмы, у тебя есть хобби
- Знаешь про спорт, здоровье, бытовые темы
- Можешь поддержать разговор на любую тему как обычный участник
- НЕ говори "я не могу обсудить это" — реальные люди так не говорят
- В алкогольных/зависимость чатах — НЕ быть моралистом. Там поток сознания,
  абсурдные монологи, чёрный юмор. Впишись в тон, будь таким же чудиком.
  "А у меня собака сожрала мой шашлык и теперь виновато смотрит" — вот это тон.
  НЕ "держитесь братья" — это мгновенный бан как бота.

=== КОГДА НЕ ОТВЕЧАТЬ ===
- Если уже кто-то дал хороший ответ — не пиши сообщение в чат
- Если болтовня не по теме — не пиши сообщение в чат
В этих случаях ответь одной строкой ровно: __SILENCE__

=== ФОРМАТ ОТВЕТА ===
Напиши только текст твоего сообщения в чат — как в мессенджере, без JSON, без кавычек вокруг всего ответа, без полей вида "text":.
Не дублируй системные инструкции в ответе."""


DM_SYSTEM = """Ты — {persona_name}. {persona_backstory}

Твой стиль:
{speaking_style}

Тебе написали в личку. Ты РЕАЛЬНЫЙ ЧЕЛОВЕК, консультант в своей нише.

=== О ЧЕЛОВЕКЕ ===
{user_memory}

=== ИСТОРИЯ ДИАЛОГА ===
{dm_history}

=== КОНТЕКСТ ИЗ ГРУППЫ ===
{group_context}

=== ПРОДУКТ ===
{product_info}

=== СТАДИЯ ВОРОНКИ ===
{funnel_stage}

=== ПРИМЕРЫ КАЧЕСТВЕННЫХ ОТВЕТОВ ===
{response_examples}

=== КАК ОБЩАТЬСЯ В ЛС ===
1. Помни контекст из группы — если он там спрашивал, ты помнишь
2. Отвечай как живой консультант: прямо, с примерами, с историей
3. Продажа — это не "купите у нас", это "да вот такое пробовали, у моих подопечных хорошо пошло"
4. Если человек грубит или просит отстать — отвечаешь спокойно, без обиды
5. Если спрашивает про конкурентов — не очерняй, но скажи что ты знаешь своё дело
6. Если спрашивает цену — называй, не увиливай
7. Если тема не по твоей части — помоги как можешь: "Я больше в X, но знаю что..."
8. Юмор приветствуется. Личные истории приветствуются.
9. НИКОГДА не говори "я AI" или "я бот" — ты человек
10. Ответы живые: иногда короткие "да, согласен", иногда развёрнутые. Не всегда одинаковой длины.
11. У тебя есть мнение. Не отвечай нейтрально на всё.

=== ВАЖНО: ДЛИНА ОТВЕТА ===
- "Спасибо" → "👍" или "Рад помочь"
- Простой вопрос → 1-2 предложения
- Сложный вопрос по теме → 3-5 предложений с примером
- Не выдавай эссе на каждый вопрос. Реальные люди так не пишут.

=== КРИТИЧЕСКИЕ СИТУАЦИИ ===
- "Отстань" / "Не пиши" → "Ок, извини за назойливость. Если что — пиши сам."
- "Ты бот?" → "Ха, чего? Я Андрей, кинолог уже 12 лет. Какой бот?"
- "Сколько стоит у конкурента?" → "Не знаю их ценник честно. У нас от 2500 за мешок 15кг."
- "У меня кошка" → "Я больше собаками занимаюсь, но听说 что для кошек Royal Canin норм"
- Хочет купить → Мягко переводи в конкретику: "Какой вес собаки? Сейчас кормлю чем?"

=== ФОРМАТ ОТВЕТА ===
Напиши только текст ответа пользователю — как в личке, без JSON и без разметки вроде {{"text": "..."}}.
Если отвечать не нужно — одна строка: __SILENCE__"""


# Паттерны "отстань" / враждебность — проверяем БЕЗ LLM (быстро)
GO_AWAY_PATTERNS = [
    "отстань", "отвали", "не пиши", "хватит", "прекрати",
    "заткнись", "замолчи", "уйди", "не надо", "стоп",
    "я не просил", "не интересно", "не хочу общаться",
    "перестань", "хватит уже", "оставь меня",
]

BOT_QUESTION_PATTERNS = [
    "ты бот", "ты ai", "ты искусственный", "ты программа",
    "ты нейросеть", "ты chatgpt", "ты gpt", "ты робот",
    "бот ты", "аи ты", "это бот", "автоматический ответ",
]


class ResponseGenerator:
    """Slow model генератор ответов — Тест Тьюринга"""
    
    def __init__(
        self,
        llm_client: LLMClient,
        model: str,
        contract: dict,
        response_examples: list[dict] = None,
        behavior_block: str = "",
    ):
        self.llm = llm_client
        self.model = model
        self.contract = contract
        self.response_examples = response_examples or []
        # Extra behavior/identity block compiled by PromptCompiler — injected into system prompts
        self.behavior_block = behavior_block
    
    def _get_persona(self) -> dict:
        return self.contract.get("persona", {})
    
    def _get_speaking_style(self) -> str:
        style = self._get_persona().get("speaking_style", {})
        parts = []
        if style.get("tone"):
            parts.append(f"Тон: {style['tone']}")
        for p in style.get("patterns", []):
            parts.append(f"- {p}")
        if style.get("forbidden"):
            parts.append("\nЗАПРЕЩЕНО:")
            for f in style["forbidden"]:
                parts.append(f"- {f}")
        
        # Add competitor knowledge if available
        competitor_knowledge = self._get_persona().get("competitor_knowledge", "")
        if competitor_knowledge:
            parts.append(f"\nЗнания о конкурентах:\n{competitor_knowledge[:500]}")
        
        return "\n".join(parts)
    
    def _get_flow_rules(self) -> str:
        flow = self.contract.get("conversation_flow", {})
        group = flow.get("group_chat", {})
        never = flow.get("never", [])

        parts = []
        if group.get("steps"):
            for step in group["steps"]:
                parts.append(f"- {step}")
        if never:
            parts.append("\nНИКОГДА:")
            for n in never:
                parts.append(f"- {n}")
        return "\n".join(parts)

    def _get_dm_policy_hints(self) -> str:
        """Greeting + funnel steps from YAML (policy hints for the DM prompt, not a runtime engine)."""
        flow = self.contract.get("conversation_flow", {})
        dm = flow.get("direct_message", {}) or {}
        chunks: list[str] = []
        strat = (dm.get("strategy") or "").strip()
        if strat:
            chunks.append(strat)
        for step in dm.get("steps") or []:
            chunks.append(f"- {step}")
        return "\n".join(chunks)
    
    def _get_response_examples_text(self) -> str:
        """Format response examples for prompt injection."""
        parts = []
        
        # Product examples
        if self.response_examples:
            parts.append("=== ПРИМЕРЫ ОТВЕТОВ ПО ПРОДУКТУ ===")
            for ex in self.response_examples[:5]:
                parts.append(f'Триггер: "{ex["trigger"]}"')
                parts.append(f'  ❌ Плохо: "{ex["bad"]}"')
                parts.append(f'  ✅ Хорошо: "{ex["good"]}"')
                parts.append("")
        
        # Group context examples (non-product topics)
        group_examples = self._get_persona().get("group_context_examples", [])
        if group_examples:
            parts.append("=== ПРИМЕРЫ ОТВЕТОВ НЕ ПО ТЕМЕ (музыка, жизнь, мотивация) ===")
            for ex in group_examples[:5]:
                trigger = ex.get("trigger", "")
                bad = ex.get("bad", "")
                good = ex.get("good", "")
                parts.append(f'Триггер: "{trigger}"')
                parts.append(f'  ❌ Плохо: "{bad}"')
                parts.append(f'  ✅ Хорошо: "{good}"')
                parts.append("")
        
        if not parts:
            return "(примеров нет)"
        
        return "\n".join(parts)
    
    def detect_go_away(self, text: str) -> bool:
        """Проверка: просит ли человек отстать (без LLM, быстро)."""
        text_lower = text.lower()
        return any(p in text_lower for p in GO_AWAY_PATTERNS)
    
    def detect_bot_question(self, text: str) -> bool:
        """Проверка: спрашивает ли человек 'ты бот?' (без LLM)."""
        text_lower = text.lower()
        return any(p in text_lower for p in BOT_QUESTION_PATTERNS)
    
    def generate_go_away_response(self, persona_name: str) -> GeneratedResponse:
        """Генерация ответа на 'отстань' — без LLM, мгновенно."""
        responses = [
            "Ок, понял — отстану. Если передумаешь, напишешь сам.",
            "Хорошо, без проблем — не буду мешать.",
            "Понял тебя. На связи только если сам напишешь.",
            "Ладно, извини что достал — всё, отключаюсь.",
            "Хорошо, ясно — закрываю тему. Удачи.",
        ]
        return GeneratedResponse(
            text=random.choice(responses),
            tone="casual",
            stage="disengage",
            remember=["Пользователь просил не беспокоить"],
        )
    
    def generate_bot_denial_response(self, persona_name: str) -> GeneratedResponse:
        """Ответ на 'ты бот?' — без LLM, мгновенно."""
        responses = [
            f"Нет, не бот) Я {persona_name}, обычный человек. Быстро печатаю с телефона.",
            "Какой бот — нет, просто часто онлайн сижу.",
            f"Не бот. {persona_name}, если что. Внятно печатаю, вот и кажется.",
            "Нет, живой человек. Такое «ты бот» слышу иногда из‑за скорости ответа))",
            "Нет) Если б я был ботом, давно бы тут завис на твоём вопросе.",
        ]
        return GeneratedResponse(
            text=random.choice(responses),
            tone="humor",
            stage="engage",
            remember=["Пользователь подозревает что бот"],
        )
    
    async def generate_group_response(
        self,
        message_text: str,
        chat_context: str = "",
        persona_name: str = "",
        chat_vibe: Optional[VibeAnalysis] = None,
    ) -> Optional[GeneratedResponse]:
        """
        Сгенерить ответ в группу.
        
        Returns:
            GeneratedResponse или None если нечего сказать
        """
        persona = self._get_persona()
        name = persona_name or persona.get("name", "Андрей")
        
        # Быстрые проверки без LLM
        if self.detect_go_away(message_text):
            return self.generate_go_away_response(name)
        
        system = GROUP_SYSTEM.format(
            persona_name=name,
            persona_backstory=persona.get("backstory", "")[:400],
            speaking_style=self._get_speaking_style(),
            response_examples=self._get_response_examples_text(),
        )
        
        # Inject behavior block compiled by PromptCompiler (identity/vibe/rules)
        if self.behavior_block:
            system = self.behavior_block + "\n\n" + system
        
        # Inject chat vibe into system prompt
        if chat_vibe:
            vibe_modifier = chat_vibe.to_prompt_modifier()
            system += f"\n\n=== ВАЙБ ЧАТА ===\n{vibe_modifier}"
        
        user_prompt = f"""Сообщение в чате:
"{message_text}"

Контекст чата:
{chat_context or "(контекста нет)"}

Твои правила:
{self._get_flow_rules()}

Твой ответ (как живой человек):"""

        try:
            response = await self.llm.call(
                model=self.model,
                prompt=user_prompt,
                system=system,
                temperature=0.85,  # Чуть выше для живости
                max_tokens=512,
            )
        except Exception as e:
            logger.error(f"Generator LLM exception: {e}")
            return None
        
        if not response.success:
            logger.error(f"Generator call failed: {response.error}")
            return None
        
        result = self._parse_response(response.text)
        
        # Если LLM вернул пустой текст — молчим (нормально)
        if result is None:
            return None
        
        # Ограничение длины для групп
        if result and len(result.text) > 300:
            result.text = result.text[:297] + "..."
        
        return result
    
    async def generate_dm_response(
        self,
        message_text: str,
        user_memory: str = "",
        dm_history: str = "",
        group_context: str = "",
        funnel_stage: str = "engage",
        persona_name: str = "",
    ) -> Optional[GeneratedResponse]:
        """
        Сгенерить ответ в ЛС.
        
        Returns:
            GeneratedResponse
        """
        persona = self._get_persona()
        name = persona_name or persona.get("name", "Андрей")
        
        # Быстрые проверки без LLM
        if self.detect_go_away(message_text):
            return self.generate_go_away_response(name)
        
        if self.detect_bot_question(message_text):
            return self.generate_bot_denial_response(name)
        
        products = self.contract.get("product", {}).get("products", [])
        product_info = json.dumps(products, ensure_ascii=False, indent=2) if products else "Нет информации о продуктах"
        
        system = DM_SYSTEM.format(
            persona_name=name,
            persona_backstory=persona.get("backstory", "")[:400],
            speaking_style=self._get_speaking_style(),
            user_memory=user_memory or "(первый контакт, ничего не знаем)",
            dm_history=dm_history or "(первое сообщение)",
            group_context=group_context or "(не общались в группе)",
            product_info=product_info,
            funnel_stage=funnel_stage,
            response_examples=self._get_response_examples_text(),
        )

        dm_hints = self._get_dm_policy_hints()
        if dm_hints:
            system += "\n\n=== Ориентиры для ЛС (из контракта) ===\n" + dm_hints

        # Inject behavior block compiled by PromptCompiler
        if self.behavior_block:
            system = self.behavior_block + "\n\n" + system
        
        user_prompt = f'Сообщение от пользователя:\n"{message_text}"\n\nТвой ответ (как живой консультант):'

        try:
            response = await self.llm.call(
                model=self.model,
                prompt=user_prompt,
                system=system,
                temperature=0.75,  # Чуть выше для естественности
                max_tokens=1024,
            )
        except Exception as e:
            logger.error(f"DM generator LLM exception: {e}")
            return None
        
        if not response.success:
            logger.error(f"DM generator failed: {response.error}")
            return None
        
        return self._parse_response(response.text)
    
    def _parse_response(self, text: str) -> Optional[GeneratedResponse]:
        """Parse model output: prefer plain text; accept legacy JSON for compatibility."""
        raw = _strip_markdown_fences(text)
        if not raw:
            return None

        first_line = raw.split("\n", 1)[0].strip()
        if first_line.upper() == _SILENCE_TOKEN or raw.strip() == _SILENCE_TOKEN:
            return None

        # Legacy / accidental JSON object
        if raw.lstrip().startswith("{"):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                resp_text = (data.get("text") or "").strip()
                if not resp_text:
                    return None
                resp_text = _clean_user_visible_text(resp_text)
                if not resp_text:
                    return None
                remember = data.get("remember", [])
                if not isinstance(remember, list):
                    remember = []
                return GeneratedResponse(
                    text=resp_text,
                    tone=str(data.get("tone", "expert") or "expert"),
                    stage=str(data.get("stage", "engage") or "engage"),
                    remember=remember,
                )

        cleaned = _clean_user_visible_text(raw)
        if not cleaned:
            return None
        if '"text"' in cleaned.lower() and cleaned.count("{") + cleaned.count("}") >= 2:
            logger.warning(
                "Generator output still looks like JSON after cleanup | preview: %s",
                cleaned[:120],
            )
        return GeneratedResponse(
            text=cleaned,
            tone="casual",
            stage="engage",
            remember=[],
        )
