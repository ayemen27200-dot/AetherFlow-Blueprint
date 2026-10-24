# D4 — LiteLLM Proxy (Optional Key Routing)

## النطاق
LiteLLM ممر اختياري لخدمات LLM المدعومة؛ لا يشغّل Modal نفسه ولا يُعد بديلاً عن FFmpeg/Whisper/ProPainter. لا تعتمد ادعاءات عدد المزودين أو ميزات rotation إلا بعد مراجعة الإصدار المثبت.

## Compose — مثال يحتاج تثبيت إصدار
```yaml
services:
  litellm:
    image: ghcr.io/berriai/litellm:<PINNED_VERSION>
    ports: ["127.0.0.1:4000:4000"]
    volumes:
      - ./litellm_config.yaml:/app/config.yaml:ro
    command: ["--config", "/app/config.yaml"]
    environment:
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}
```

## إعداد مثال
```yaml
model_list:
  - model_name: gemini-flash
    litellm_params:
      model: gemini/<VERIFIED_MODEL_ID>
      api_key: os.environ/GEMINI_KEY_1
  - model_name: gemini-flash
    litellm_params:
      model: gemini/<VERIFIED_MODEL_ID>
      api_key: os.environ/GEMINI_KEY_2
router_settings:
  num_retries: 2
  timeout: 60
```

استبدل placeholders بمعرفات نموذج وحقول يدعمها الإصدار المختبر. لا تضع `main-latest` في الإنتاج.

## تشغيل داخلي
استخدم HTTPS للمزود الخارجي، ومفتاح داخلي منفصل، وtimeout، وحدود إنفاق، وredaction للسجلات. لا تفترض أن تكرار entries بنفس `model_name` يحقق التناوب في كل إصدار؛ اختبر السلوك فعلياً.

## اختبارات
- فشل المفتاح الأول وتوافر الثاني.
- كل المفاتيح معطلة أو quota exhausted.
- timeout، استجابة 429، وعدم تسجيل secrets.
