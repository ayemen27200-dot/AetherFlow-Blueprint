# D2 — Acceptance Criteria (G2)

## وظيفي
- [ ] `POST /jobs` ينشئ المهمة ويعيد `id` والحالة `queued`.
- [ ] `GET /jobs/{id}` يعرض الحالة الحالية والمخرجات المتاحة.
- [ ] `GET /jobs` يرتب بـ `priority DESC, created_at ASC` ويدعم فلترة آمنة.
- [ ] مهمة split على fixture قصيرة تنتج مقاطع قابلة للفك؛ العدد المتوقع يعتمد على keyframes.
- [ ] مهمة split على فيديو 30 دقيقة: زمن جدار المسار الكامل (مع فحص الفك) أقل من 15 ثانية، يُقاس ويُسجَّل على VPS مستهدف عبر `scripts/benchmark_long_media.sh`.
- [ ] مهمة split على فيديو ساعتين: زمن جدار المسار الكامل أقل من 60 ثانية، يُقاس ويُسجَّل على VPS بنفس السكربت مع codec والحجم ومواصفات الجهاز.
- [ ] مصفوفة الأدلة وحالة كل معيار محفوظة في `D2_MVP/06_G2_EVIDENCE_LEDGER.md` وتحدَّث بقياسات فعلية فقط.
- [ ] `/health` يفشل بوضوح عند انقطاع DB ولا يكشف أسراراً.

## غير وظيفي
- [ ] `docker compose up` يعمل على تثبيت نظيف موثق.
- [ ] Caddy يصدر شهادة Let's Encrypt عند وجود DNS ومنافذ سليمة؛ `tls internal` للتطوير فقط.
- [ ] Uvicorn غير مكشوف مباشرة للعامة.
- [ ] السجلات منظمة ولا تحتوي رموزاً أو بيانات وسائط حساسة.
- [ ] `.env` مستثنى عبر `.gitignore`.

## حالات طرفية
- [ ] مسار غير موجود/خارج المجلد المسموح: رفض واضح.
- [ ] ملف أكبر من حد الإعداد: رفض قبل المعالجة.
- [ ] مهمتان متزامنتان: يلتزم worker بحد التزامن.
- [ ] FFmpeg يفشل: `failed` مع `error_type` لا يتضمن أسراراً.
- [ ] إعادة تشغيل الخدمة لا تفقد حالة الطابور الدائم.

## Deliverables
- [ ] `orchestrator/main.py`, `queue.py`
- [ ] `executors/base.py`, `ffmpeg.py`
- [ ] `agent/cli.py`
- [ ] Compose وDockerfile وCaddyfile
- [ ] `tests/integration/test_split_e2e.py`
- [ ] `README_D2.md`

## G2
مراجعة عملية تعرض إنشاء مهمة وتقسيمها والتحقق من النتيجة. لا تعتمد G2 قبل تشغيل الاختبارات فعلياً.
## توليد عينة اختبار

تُولّد عينة اصطناعية قصيرة (لا تحتوي على فيديو خاص أو ترجمة واقعية) باستخدام FFmpeg:

```bash
bash scripts/generate_test_fixture.sh
```

الأمر المكافئ:

```bash
ffmpeg -f lavfi -i 'testsrc=duration=5:size=320x240:rate=30' \
       -f lavfi -i 'sine=frequency=440:duration=5' \
       -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest -movflags +faststart \
       tests/fixtures/sample_5s.mp4
```

وجود هذه العينة لا يغني عن fixtures لترجمات متغيرة أو فيديو طويل أو اختبارات جودة ProPainter.
