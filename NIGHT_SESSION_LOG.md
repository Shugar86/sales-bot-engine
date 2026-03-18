# Night Session Log — Cycle 2

## Started: 2026-03-18 21:00 GMT+1

---

## CYCLE 1: ARCHITECT REVIEW

### Turing Test Failures Found:

1. **Memory hardcoded to kormoved** — `_extract_dog_info()` only extracts dog-related info. Fitness/SMM personas have no entity extraction. A person mentions "膝盖疼" (knee pain) to the fitness bot — nothing gets remembered.

2. **Anti-spam delays are INSANE** — min=180s, max=900s. A real person in an active group chat responds in 30s-3min, not 3-15 minutes. This screams "bot" to anyone paying attention.

3. **No "leave on read" behavior** — The bot either responds or ignores (LLM decision). Real humans READ messages and don't reply ~40% of the time. Need probabilistic "seen but no response."

4. **No time-of-day awareness** — A bot answering at 3 AM is suspicious. Real people sleep. Need activity windows per persona.

5. **No emoji reactions** — Sometimes 👍 or 😂 is more natural than a full response. Currently only text responses.

6. **Persona YAML key mismatch** — kormoved uses `triggers.respond_when` but persona_manager.py expects `respond_triggers`. The v1→v2 adapter `_persona_to_contract()` reads `config.respond_triggers` which may not match.

7. **`response_examples` field missing** — Task specifically asks for good/bad response pairs in persona YAML. Not implemented.

8. **`datetime.utcnow()` deprecated** — 146 pytest warnings. Easy fix.

9. **No conversation threading** — Bot doesn't know if it already responded in a thread. Could double-respond.

10. **Generator always outputs full text** — Should sometimes just react with emoji instead of typing a paragraph.

11. **`_extract_dog_info` is called for ALL personas** — Even fitness/smm personas run through dog breed extraction. Wasteful and wrong.

12. **Missing `__init__.py` for models** — `src/models/__init__.py` exists but checking completeness.

### Code Quality Issues:
- `_extract_dog_info` should be pluggable per-persona
- No retry logic in user memory (file corruption crashes)
- `get_recent_messages` reads ALL user files on every message — O(n*m) performance
- No TTL or cleanup for old memory entries

---

## CYCLE 1: BALDY RESEARCH

### Source: "Human or Bot: How AI Gives Itself Away in Conversation" (humanornot.so, Feb 2026)

Key findings from Turing test game analysis:

1. **Perfect Punctuation = Bot** — Humans on phones: lowercase starts, missing periods, typos. AI defaults to "correct" writing even in casual chat. A perfectly formatted list in a 2-minute chat = bot.

2. **Response Latency** — Humans: variable timing (short words instant, complex thoughts seconds, distracted by life). AI: uniform latency, same delay for simple and complex questions. TEST: Ask complex → simple question, same delay = bot.

3. **Over-Explaining Syndrome** — AI loves to be helpful. Ask "Do you like pizza?" → Human: "Yeah, obsessed." Bot: "I don't eat, but pizza is popular globally with many toppings." The repetition of context nobody asked for = hallmark of AI.

4. **Forced Politeness** — AI tries very hard to be friendly. In casual settings this is a RED FLAG. Real people are sometimes blunt, sarcastic, or disinterested.

5. **Safe Answers** — AI avoids controversy. Humans have opinions (even wrong ones). Generic "I'm not sure about that" to viral news = bot.

6. **Memory Inconsistency** — Bots lose the thread in meta-conversations. May contradict 3 sentences earlier. STRATEGY: Mention fake name early, reference later — bot hallucinates or plays along.

### Telegram Userbot Best Practices (from Stack Overflow, Reddit, n8n community):

1. **Typing indicator** — Telegram typing lasts 5 seconds, need to re-send periodically for long processing
2. **Telethon SetTypingRequest** — Use `SendMessageTypingAction()` for userbot typing simulation
3. **Variable typing speed** — Should correlate with message length AND complexity
4. **Session management** — Each persona needs independent session file

### Anti-Detect Patterns (synthesized from research):

1. **Delays**: 30-300s for active chat (not 180-900s). Human in active group: 30s-3min response.
2. **Leave on read**: 30-40% of messages should be "seen but not responded" — humans don't answer everything
3. **Time windows**: Activity should follow human patterns — active 8-23h, quiet at night
4. **Emoji reactions**: Sometimes 👍 is more natural than typing a full response
5. **Response length variance**: Mix short ("да, согласен") with longer expert answers
6. **Typos**: Occasional lowercase starts, missing punctuation in casual messages
7. **Opinions**: Have preferences, don't be neutral on everything

---

## CYCLE 1: CODER FIX

(implementation to be documented)

## CYCLE 1: COMPLETED

### Test Fixes
- Fixed test_memory.py: persona_name="kormoved" for dog extractor
- Fixed test_persona_manager.py: default min_delay is 30, not 120
- Fixed test_anti_spam.py: tolerance for float comparisons

### Turing Test Improvements (Phase 7)
- Generator: human-like prompts, go-away detection, bot denial, LLM fallbacks
- Router: pre-filters (spam, trivial, go-away, bot questions, bare links)  
- DISENGAGE decision for "отстань" scenarios
- Orchestrator v1+v2: handle DISENGAGE
- Userbot: bot-to-bot detection
- 20 new Turing edge case tests

### Final Status: 131/131 tests passing ✅
