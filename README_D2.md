# D2 MVP — تشغيل خط المعالجة المحلي وحالة معايير القبول

هذا المستند يصف المسار المنفَّذ فعليًا في D2 وطريقة تشغيله، ويفصل البنود
المختبَرة فعليًا عن البنود المؤجَّلة. لا يعلن هذا المستند اكتمال G2؛
القرار بيد المراجعة بعد تشغيل الأدلة.

## 1. المعمارية المنفَّذة

```
POST /jobs (Bearer) ─► SQLiteQueue (WAL، حجز ذري بسياج الأجيال)
                              │  upload://<opaque-id>  (لا مسارات محلية عبر الـAPI)
                              ▼
                    agent/worker.py (QueueWorker / serve)
                              │  مطابقة المعرّف داخل مخزن محلي موثوق
                              ▼
                  agent/upload_store.py (LocalUploadStore)
                              │  مسار مطلق محتوى داخل جذر موثوق
                              ▼
              executors/ffmpeg.py (نسخ دفق، مهلة، فك فعلي، تحقق مخرجات)
                              │
                              ▼
        jobs.output_paths / status=completed|failed (error_type آمن)
```

قواعد ثابتة:

1. **فصل القنوات**: الـAPI لا يقبل إلا معرّفات رفع opaque
   (`validate_upload_id`، تُخزَّن مسبوقة بـ`upload://`). العامل هو المكوّن
   الوحيد الذي يطابق المعرّف مع ملف داخل `AETHERFLOW_UPLOAD_STORE` بفحص
   احتواء صارم يتبع الروابط الرمزية. مسار CLI المحلي (`aetherflow split`)
   منفصل ولا يمر بالطابور.
2. **لا مسارات مطلقة عبر الـAPI**: الطابور يخزّن مراجع مخرجات نسبية إلى جذر
   المخرجات (مثل `<job-id>/part_000.mp4`)؛ المسارات المطلقة تبقى داخل
   المنفّذ/العامل، وطبقة الـAPI تحذف دفاعيًا أي مرجع مطلق أو هارب (`..`)
   حتى لو عُبث بالصفوف مباشرة. التحقق من الملفات الفعلية يتم بحل المرجع داخل
   الجذر الموثوق في العامل أو الاختبارات، ولا توجد نقطة تنزيل عامة.
3. **لا نجاج بمجرد خروج صفري**: المنفّذ يقرأ المدة (ffprobe/بانر FFmpeg) ثم
   يجري فك ترميز كاملًا يتطلب رمز خروج 0 مع stderr خالٍ من الأخطاء
   (`-xerror`)، والقاعدة نفسها على كل جزء مخرجات. المخرجات الجزئية تُحذف عند
   أي فشل.

## 2. التشغيل

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# 1) خدمة الـAPI (تحتاج BEARER_TOKEN؛ بدونه تفشل مغلقة 401)
export BEARER_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
uvicorn orchestrator.main:app --host 127.0.0.1 --port 8000

# 2) العامل (عملية منفصلة؛ نفس ملف قاعدة البيانات)
mkdir -p data/upload_store data/outputs
aetherflow worker run \
  --db-path ./data/aetherflow.db \
  --upload-store ./data/upload_store \
  --output-dir ./data/outputs \
  --segment-seconds 300 --concurrency 1

# 3) إنشاء مهمة (المعرّف يطابق اسم كائن داخل مخزن الرفع)
curl -sS -X POST http://127.0.0.1:8000/jobs \
  -H "Authorization: Bearer $BEARER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"type":"split","file":"up-sample-5s.mp4","priority":5}'

# مسار محلي موثوق خارج الطابور
aetherflow split /abs/path/clip.mp4 \
  --allowed-base /abs/trusted/dir --output-dir ./data/outputs
```

متغيرات العامل كلها اختيارية في CLI (تعطى كخيارات أو عبر متغيرات البيئة
الموثقة في `.env.example`)، ولا تحوي أي أسرار. إيقاف العامل بـSIGINT/SIGTERM
يضبط حدث التوقف بعد انتهاء المهمة الجارية؛ لا استرجاع تلقائي للمهام المعلّقة
(متعمد، انظر `queue.recover_claimed_jobs`).

## 3. الأدلة وحدودها

| البند (من `05_ACCEPTANCE_CRITERIA.md`) | الحالة | الدليل |
|---|---|---|
| `POST /jobs` ينشئ مهمة `queued` ويعيد `id` | مُختبَر | `tests/api/test_jobs_api.py` + `test_g2_pipeline_e2e.py` |
| `GET /jobs/{id}` يعرض الحالة والمخرجات | مُختبَر | E2E حقيقي: الحالة تبلغ `completed` و`output_paths` قابلة للفك |
| `GET /jobs` ترتيب priority/created وفلترة آمنة | مُختبَر | اختبارات API والطابور |
| split على عينة قصيرة ينتج مقاطع قابلة للفك | مُختبَر | `test_g2_create_process_and_verify_real_split` (FFmpeg حقيقي) |
| رفض مسار خارج المجلد المسموح | مُختبَر | وحدوي + تكامل (طابور/مخزن رفع/منفّذ) |
| ملف أكبر من حد الإعداد يُرفض قبل المعالجة | مُختبَر | اختبارات الطابور والمنفّذ |
| التزام العامل بحد التزامن | مُختبَر | `test_serve_respects_concurrency_cap_with_separate_queues` |
| فشل FFmpeg → `failed` بـ`error_type` بلا أسرار | مُختبَر | E2E لوسيط تالف ولكائن مفقود + وحدوي للأكواد |
| إعادة التشغيل لا تفقد الطابور الدائم | مُختبَر | `test_g2_queued_job_survives_worker_restart` |
| `/health` يفحص DB فعليًا ويخفق بوضوح | **غير منفَّذ** | لا يزال `degraded` ثابتًا؛ فحص DB النشط خارج هذه الدفعة |
| عينة 30 دقيقة + قياس الأداء على VPS | **غير مثبت** | تحتاج VPS المستهدف وملفًا كبيرًا |
| `docker compose up` نظيف + Caddy/TLS + Uvicorn غير مكشوف | **غير مثبت** | ملفات الإطار موجودة؛ اختبار التثبيت النظيف لم يُجرَ |
| السجلات منظمة بلا أسرار | جزئي | العامل يستخدم logging منظّم بأكواد فقط؛ تدقيق شامل مؤجَّل |

نتائج التشغيل المحلي الفعلية موثقة في تعليقات PR #6 وملخص CI (JUnit
artifact وJob Summary). اختبارات التكامل الحقيقية تتخطى فقط عند غياب FFmpeg،
وحارس `AETHERFLOW_REQUIRE_MEDIA_BINARIES` يحوّل الغياب على الرانر إلى فشل.

## 4. حدود النطاق

- لا تغييرات على عقد OpenAPI ولا على مخطط قاعدة البيانات (عمود
  `output_paths` مستخدم كما هو؛ أُضيفت دوال طابور فقط).
- لا `POST /uploads` ولا SSE ولا إلغاء عبر الـAPI ولا نبضات وكيل (D3).
- لا استرجاع تلقائي للمهام المعلّقة؛ الاسترجاع صريح ومبني على أدلة
  المشرف فقط حسب تصميم الطابور.
- العامل يدعم حاليًا نوع `split`؛ بقية الأنواع تبقى في الطابور حتى يأتي
  منفّذوها ولا يحجزها هذا العامل (فلتر `dequeue(job_types=...)`).
