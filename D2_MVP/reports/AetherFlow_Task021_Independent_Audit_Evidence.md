# حزمة أدلة التدقيق المستقل لـPR #6 (Task021)

التاريخ: 2026-10-26 (تحديث: 2026-10-27، جولة تصحيح ربط بيانات الرأس بالتشغيل).
الفرع: `arena/940efcbe-aetherflow-blueprint` — PR #6 مفتوح، غير مدموج.
آخر رأس كودي موضوع التدقيق: `70a2efad69c490ed276b0203db190703d70af2a3`
(تشغيله الأخضر 40407250031). الدفعات الأحدث (`bee2080` ثم الالتزام الحامل لهذا
التحديث) تعدّل ملفات Markdown فقط ولا تغيّر أي كود أو معيار.
الالتزام موضوع التدقيق الأصلي: `038479be93625c5eb2d3f804881e31bffb181b3c` — يبقى
في السجل التاريخي أدناه.
ملاحظة منهجية عن ربط الرأس بالتشغيل: وثيقة داخل المستودع يستحيل أن تحتوي معرّف
تشغيل الالتزام الذي يحملها (الالتزام يُدفع أولًا ثم يُخلق تشغيله)، لذلك يسجّل
هذا التقرير الدليل الأخضر لكل التزام بتسميته الكاملة، بينما يُحفظ الربط الحيّ
بين رأس الفرع الفعلي وآخر تشغيل أخضر في وصف PR #6 وآخر تعليق عليه، ويُحدَّثان
عبر واجهة API بلا إنشاء التزامات جديدة. على المراجع استقصاء الرأس الفعلي بـ
`git` أو GitHub API ثم مقابلته بالجدولين 1.1 و1.2؛ لا يُستخدم تعبير «رأس الفرع
الحالي» داخل هذه الوثيقة تفاديًا لعدم اتساق ذاتي الإحالة.
الغرض: إغلاق نقاط تحقق المراجعة المستقلة (حالة CI، نتائج الفحوص الفعلية، مصفوفة
قبول G2 كاملة، وحدود وظيفة الرفع) دون أي تغيير في سلوك الكود أو مخطط SQLite أو
عقد OpenAPI. لا يعلّق هذا التقرير قبول G2 ولا يعدّل أي معيار.

## 1) ربط تشغيل CI بالالتزام الكامل (قابل للتحقق عبر GitHub API)

### 1.1 الأدلة الخضراء الحالية (مربوطة بالالتزامات الكاملة عبر GitHub API)

#### 1.1.1 أحدث رأس: `bee2080` — تشغيل [40412849119](https://github.com/ayemen27200-dot/AetherFlow-Blueprint/actions/runs/40412849119)

دفعة تصحيح بيانات الأدلة (ملفا Markdown فقط، بلا أي تغيير كود):

- الرأس `bee2080a205b63797f205d2370022e51e30a55ce`، حدث `pull_request`،
  و`head_sha` من استعلام API يساوي الرأس حرفيًا؛ الحالة `completed` والنتيجة
  `success` (بدأ 22:29:39Z واكتمل 22:30:49Z، نحو 70 ثانية).
- الوظيفتان (check-runs على نفس الالتزام):
  1. [Specs, lint and tests](https://github.com/ayemen27200-dot/AetherFlow-Blueprint/actions/runs/40412849119/job/121887667877) —
     خطواتها الـ15 كلها `success` (بما فيها Ruff وRun test suite ورفع الأداة).
  2. [Docker Compose clean-host stack](https://github.com/ayemen27200-dot/AetherFlow-Blueprint/actions/runs/40412849119/job/121887668327) —
     7 خطوات: 5 `success` وخطوتا النشر عند الفشل `skipped` (الفشل لم يحدث).
- أداة النتائج الآلية (بيانات API فقط): الاسم `pytest-results`، المعرّف
  `12693863239`، الحجم المسجّل 4407 بايت، `expired=false`، ورابطها بعد مصادقة
  GitHub:
  `https://api.github.com/repos/ayemen27200-dot/AetherFlow-Blueprint/actions/artifacts/12693863239/zip`.
  حجم الأداة وحدَه لا يُستنتَج منه أي عدد اختبارات؛ لم تُقرأ بايتات XML
  (القيد في 1.3).

#### 1.1.2 آخر رأس كودي موضوع التدقيق: `70a2efa` — تشغيل [40407250031](https://github.com/ayemen27200-dot/AetherFlow-Blueprint/actions/runs/40407250031)

هذا الرأس يحمل آخر تغيير كودي/اختباري في الحزمة؛ كل الدفعات بعده توثيقية بحتة:

- الرأس `70a2efad69c490ed276b0203db190703d70af2a3` وتشغيله
  [40407250031](https://github.com/ayemen27200-dot/AetherFlow-Blueprint/actions/runs/40407250031):
  الحدث `pull_request`، و`head_sha` من استعلام API يساوي الرأس حرفيًا،
  الحالة `completed` والنتيجة `success` (بدأ 21:47:09Z واكتمل 22:13:53Z).
- الوظيفتان (check-runs على نفس الالتزام، كلتاهما `success`):
  1. [Specs, lint and tests](https://github.com/ayemen27200-dot/AetherFlow-Blueprint/actions/runs/40407250031/job/121868083823) —
     خطواتها الـ15 كلها `success`، ومنها Validate YAML specs، Validate OpenAPI
     3.1 contract، Byte-compile، **Ruff lint**، **Run test suite**، Publish test
     count summary وUpload machine-readable test results.
  2. [Docker Compose clean-host stack](https://github.com/ayemen27200-dot/AetherFlow-Blueprint/actions/runs/40407250031/job/121868083521) —
     خطوة التحقق المعزول `success`؛ خطوتا النشر عند الفشل `skipped` (لأن الفشل لم يحدث).
- أداة النتائج الآلية لهذا التشغيل (بيانات واجهة API فقط): الاسم `pytest-results`،
  المعرّف `12693865379`، الحجم المسجّل 4422 بايت، `expired=false`، ورابطها بعد
  مصادقة GitHub:
  `https://api.github.com/repos/ayemen27200-dot/AetherFlow-Blueprint/actions/artifacts/12693865379/zip`.
- ملاحظة شفافية عن طول التشغيل: خطوة «Install FFmpeg and ffprobe» استغرقت نحو
  26 دقيقة لبطء مرآة apt على الرانر (تأخر بنية تحتية عابر، سبق أن رُصد في تشغيلات
  سابقة) ثم اكتملت `success`؛ لا صلة لذلك بالدفعات التوثيقية التي لا تغيّر كودًا.

### 1.2 السجل التاريخي — تشغيلات الرؤوس السابقة (ليست من أدلة القسم 1.1 الحالية)

تبقى هنا للشفافية وكشف سلسلة الأدلة كاملة، ولا يجوز الاستشهاد بها كدليل على
رأس أحدث من التزامها:

| الالتزام | التشغيل | النتيجة | أداة pytest-results (معرّف / حجم API) |
|---|---|---|---|
| `05a30f25a26bf0987faef6c3992719e8359c8c1c` | [40406893165](https://github.com/ayemen27200-dot/AetherFlow-Blueprint/actions/runs/40406893165) — [verify](https://github.com/ayemen27200-dot/AetherFlow-Blueprint/actions/runs/40406893165/job/121866931267) / [compose](https://github.com/ayemen27200-dot/AetherFlow-Blueprint/actions/runs/40406893165/job/121866931085) | `success` للمهمتين | `12691358245` / 4409 بايت |
| `038479be93625c5eb2d3f804881e31bffb181b3c` (موضع التدقيق الأصلي) | [40405255560](https://github.com/ayemen27200-dot/AetherFlow-Blueprint/actions/runs/40405255560) — [verify](https://github.com/ayemen27200-dot/AetherFlow-Blueprint/actions/runs/40405255560/job/121861234312) / [compose](https://github.com/ayemen27200-dot/AetherFlow-Blueprint/actions/runs/40405255560/job/121861234548) | `success` للمهمتين | `12691575611` / 4407 بايت |

### 1.3 حدود التحقق الصادقة (لا أرقام من JUnit لم تُقرأ)

- بيئة ساندبوكس Arena تمنع تنزيل بايتات السجلات وأدوات JUnit: التوجيه إلى
  `*.blob.core.windows.net` و`results-receiver.actions.githubusercontent.com`
  خارج قائمة السماح الشبكية، والخطأ الفعلي `EOF` بعد إعادة التوجيه. لذلك **لم
  تُستخرج أعداد اختبارات الرانر من XML في هذه الحزمة**؛ المذكور عن الرانر هو
  حالات الخطوات/الوظائف وبيانات الأداة (وجود، معرّف، حجم، انتهاء صلاحية) عبر
  واجهة API فقط، والأعداد التفصيلية المثبتة قسم 2.2 هي نتائج إعادة تشغيل محلية
  موسومة صراحةً كذلك. المراجع المستقل يملك تنزيل الأداة وتحليل XML بنفسه عبر
  روابط القسمين 1.1 و1.2.
- بيانات واجهة API أعلاه قابلة لإعادة الاستقصاء لأي رأس موثق، مثال الرأسين
  الحاليين:
  `gh api repos/ayemen27200-dot/AetherFlow-Blueprint/commits/bee2080a205b63797f205d2370022e51e30a55ce/check-runs`
  و
  `gh api repos/ayemen27200-dot/AetherFlow-Blueprint/commits/70a2efad69c490ed276b0203db190703d70af2a3/check-runs`.
- التعليقات التوضيحية على check-run لا تحوي أي فشل؛ فقط تحذيرات إهمال Node.js 20
  وإشعار ترقية ubuntu-latest (بنية تحتية، لا صلة بالكود).
- مجموعة check-suite من تطبيق Vercel ظهرت `queued` في بعض الفحوص؛ لا علاقة لها
  بمشغّل GitHub Actions ولا ببوابات هذا المستودع.

## 2) نتائج pytest/ruff/OpenAPI وسبب كل اختبار متخطّى

### 2.1 نتائج الرانر (من بنية التشغيلات الخضراء القابلة للتحقق)

على أحدث تشغيل 40412849119 (الرأس `bee2080`) والتشغيل الكودي 40407250031
(الرأس `70a2efa`): خطوات `Ruff lint` و`Validate OpenAPI 3.1 contract` و
`Run test suite` كلها `success`، مع رفع أداة JUnit في كل تشغيل (المعرّفان
12693863239 و12693865379 على الترتيب) ليتمكن المراجع من فحص الأعداد والتخطيات
بنفسه. لم تُقرأ بايتات أي أداة من بيئة الإعداد (القيد موثق في 1.3)، فلا تُنسب
أي أعداد للرانر هنا؛ الأعداد المثبتة رقميًا في القسم التالي هي نتائج إعادة
تشغيل محلية موسومة كذلك، ونفس الخطوات نجحت أيضًا على تشغيلات السجل التاريخي
في 1.2.

### 2.2 إعادة محلية بالأوامر نفسها (ساندبوكس البناء، ثنائي FFmpeg 7.0.2 static)

| الأمر | النتيجة الفعلية | رمز الخروج |
|---|---|---:|
| `ruff check .` | All checks passed! | 0 |
| `python -m compileall -q agent orchestrator executors web scripts` | بلا أخطاء | 0 |
| `python scripts/validate_specs.py` | OK: 9 ملفات YAML والمراجع المحلية تُحل | 0 |
| `openapi-spec-validator D1_FOUNDATION/03_API_SPEC.yaml` | OK | 0 |
| `pytest -q` | **208 ناجحة، 2 متخطّيان**، 210 اختبارات مجمّعة | 0 |

### 2.3 التخطّيان المحليان ولماذا ينفّذان في CI (إثبات من المصدر)

1. `tests/integration/test_media_toolchain.py::test_media_binaries_available_when_required`
   — شرط التخطي في الكود: يتخطى فقط عندما لا تساوي البيئة
   `AETHERFLOW_REQUIRE_MEDIA_BINARIES=1`. خطوة «Run test suite» في CI تضبط هذا
   المتغير على `1` صراحةً، فيتحول غياب أي ثنائي إلى فشل قاطع بدل التخطي؛ ومن
   ثم الاختبار **يُنفَّذ على الرانر** وقد نجح (الخطوة `success`).
2. `tests/integration/test_split_e2e.py::test_ffprobe_path_reports_real_duration`
   — مزيّن بـ`skipif` عند غياب `ffprobe`. خطوة «Install FFmpeg and ffprobe»
   في CI انتهت `success` وتتضمن `command -v ffprobe` و`ffprobe -version`،
   فالثنائي موجود على الرانر والاختبار **يُنفَّذ هناك**. في الساندبوكس المحلي
   لا يُثبَّت إلا FFmpeg (عبر imageio-ffmpeg لأغراض الاختبار فقط، وليس تبعية
   مشروع) فيظهر التخطي.

النتيجة المنطقية: مجموعة الرانر تشغّل الاختبارات الـ210 كاملة بلا تخطّيين
محليين؛ والعد الدقيق داخل `junit.xml` المرفوع متاح للمراجع عبر رابط الأداة
أعلاه، ولا أستبدله برقم منقول بلا المصدر.

## 3) مصفوفة ربط كل بند في `05_ACCEPTANCE_CRITERIA.md` بدليل محدد

الحالات: **اجتاز** = دليل آلي/تشغيلي قائم؛ **غير متحقق** = يتطلب بيئة أو
مراجعة غير متوفرة؛ **لم يجتز** = قياس فعلي دون السقف.

### وظيفي

| البند | الحالة | الدليل المحدد |
|---|---|---|
| `POST /jobs` ينشئ مهمة ويعيد `id` بحالة `queued` | اجتاز | `test_create_job_is_accepted_persisted_and_retrievable`؛ E2E: `_create_job` يفترض 202 و`queued` |
| `GET /jobs/{id}` يعرض الحالة والمخرجات | اجتاز | E2E يقرأ الحالة قبل/بعد المعالجة؛ `test_get_unknown_job_returns_404` |
| `GET /jobs` ترتيب `priority DESC, created_at ASC` وفلترة آمنة | اجتاز | `test_list_jobs_orders_by_priority_and_filters_status` |
| split على عينة قصيرة ينتج مقاطع قابلة للفك | اجتاز | `test_g2_create_process_and_verify_real_split` + اختبارات `test_split_e2e.py` بفك مستقل `ffmpeg -v error -xerror -f null` رمز 0 وstderr فارغ |
| فيديو 30 دقيقة < 15 ثانية | **لم يجتز** | قياس فعلي بثلاثة تشغيلات، الوسيط 18.50ث (17.68/18.75/18.50) على ساندبوكس نواتين؛ قياس VPS بملف ممثل لم يُنفَّذ — يبقى مفتوحًا |
| فيديو ساعتان < 60 ثانية | **لم يجتز** | الوسيط 68.95ث (71.34/68.95/68.08)؛ نفس التعليق — مفتوح بانتظار VPS أو مراجعة معيار رسمية |
| مصفوفة الأدلة محفوظة وتُحدَّث بقياسات فعلية | اجتاز | `07_G2_EVIDENCE_LEDGER.md` القسمان 4 و4.1 بأرقام القياس الفعلية قبل/بعد Task020 |
| `/health` يفشل بوضوح عند انقطاع DB بلا أسرار | اجتاز | `test_health_returns_503_without_leaking_database_details` و`test_database_failures_return_503_without_internal_details` بمحاكاة `_FailingQueue` |

### غير وظيفي

| البند | الحالة | الدليل المحدد |
|---|---|---|
| `docker compose up` على تثبيت نظيف موثق | اجتاز تشغيليًا | وظيفة compose-smoke على الرانر خضراء في أحدث تشغيل 40412849119 (الرأس `bee2080`) والتشغيل الكودي 40407250031 (الرأس `70a2efa`) والسجل التاريخي 40405255560/40406893165 والتشغيلات الأقدم (سكربت 11 قسمًا معزولًا، بلا continue-on-error) |
| Caddy يصدر Let's Encrypt عامًا عند توفر DNS | **غير متحقق** | المثبَت `tls internal` لـlocalhost فقط؛ الشهادة العامة تتطلب مضيف DNS ومنافذ عامة |
| Uvicorn غير مكشوف مباشرة للعامة | اجتاز | `tests/unit/test_compose_config.py` (إعداد `expose` بلا `ports`) + فحص منشور المنافذ في سكربت العزل |
| السجلات منظمة بلا أسرار/بيانات وسائط | اجتاز جزئيًا | `test_failure_logs_never_contain_filesystem_paths` وفحص توكن سجلات الحاويات في compose-smoke؛ **التدقيق الشامل لكل المكونات غير متحقق** ويبقى بندًا مفتوحًا |
| `.env` مستثنى عبر `.gitignore` | اجتاز | قاعدتا `.env` و`.env.*` مع استثناء `!.env.example`؛ النموذج الخالي من الأسرار وحده المتعقب |

### حالات طرفية

| البند | الحالة | الدليل المحدد |
|---|---|---|
| مسار غير موجود/خارج المجلد المسموح: رفض واضح | اجتاز | ثلاث طبقات (API/طابور/مخزن/منفّذ)؛ `test_g2_path_like_upload_id_is_rejected_at_the_api` (400) و`test_input_rejects_relative_missing_outside_bad_extension_oversize` |
| ملف أكبر من حد الإعداد: رفض قبل المعالجة | اجتاز | اختبارات `EnqueuePolicy` واختبار المنفذ للحد 2000MB من YAML الموثوق |
| مهمتان متزامنتان: احترام حد التزامن | اجتاز | `test_serve_respects_concurrency_cap_with_separate_queues` بميزانيات 1/3/5 وتزامن 2 و3 |
| فشل FFmpeg: `failed` بـ`error_type` آمن | اجتاز | E2E الوسيط التالف والكائن المفقود → `INVALID_INPUT` بلا مسارات ولا مخرجات جزئية |
| إعادة تشغيل الخدمة لا تفقد الطابور الدائم | اجتاز | `test_g2_queued_job_survives_worker_restart` و`test_jobs_survive_a_service_restart` على ملف SQLite نفسه |

### التسليمات ومراجعة G2 العملية

- ملفات التسليم الستة كلها موجودة ومتعقبة: `orchestrator/main.py`
  و`queue.py`، `executors/base.py` و`ffmpeg.py`، `agent/cli.py`، مجموعة
  Compose/Dockerfile/Caddyfile، `tests/integration/test_split_e2e.py`،
  و`README_D2.md`.
- مراجعة G2 العملية (إنشاء مهمة وتقسيمها والتحقق): مُثبتة آليًا عبر E2E
  وتشغيليًا بتشغيل عمليتين منفصلتين موثق في السجل الحيّ وتعليقات PR؛ لكن
  **الاعتماد النهائي غير متحقق** ومحجوز للمراجعة المستقلة، ولا يُعلَن اجتياز
  G2 ما دام بندا الأداء وTLS العام وتدقيق الأسرار مفتوحة.

## 4) توضيح حدود وظيفة الرفع (إعادة صياغة بأثر توثيقي فقط)

استجابة لملاحظة أن بعض الصياغات توحي بإصدار معرّفات رفع عبر الخادم:

- `README.md` و`D2_MVP/02_IMPLEMENTATION_MAP.md` ينصان الآن صراحةً على أنه
  **لا يوجد مسار HTTP باسم `POST /uploads` في كود D2 إطلاقًا**؛ العقد في
  `D1_FOUNDATION/03_API_SPEC.yaml` يبقى عقدًا مستقبليًا لـD3 وليس وصفًا
  لوظيفة عاملة.
- معرّف `JobSpec.file` في D2 هو اسم الكائن الذي يودَع في المخزن الموثوق
  **خارج الـAPI** (تزويد مشغّل/اختبار في مجلد `LocalUploadStore`)؛ لا الـAPI
  ولا الطابور يولّدان المعرّف أو يسجّلانه، والعامل يحلّه لاحقًا داخل الجذر
  الموثوق ويرفض أي قيمة شبيهة بمسار.
- لم يتغير أي سلوك: نفس الرفض 400 للقيم الشبيهة بالمسارات، ونفس مساري CLI
  (`worker run` للطابور، و`split` للملف المحلي الموثوق).

## 5) البنود التي تبقى مفتوحة (لا ادعاء إغلاق)

1. قياس VPS المستهدف بملفات ممثلة (`BENCH_30M_INPUT`/`BENCH_2H_INPUT`،
   `BENCH_REPEATS=3`) للسقفين 15ث/60ث، أو طلب مراجعة رسمية للمعيار وفق إجراءات
   المشروع إن تعذر بلوغهما.
2. شهادة Let's Encrypt عامة بمضيف DNS حقيقي.
3. تدقيق تسريب أسرار شامل لكل المكونات.
4. بنود D3–D5 بما فيها `POST /uploads` وSSE والإلغاء ونبضات القلب.
5. PR #6 يبقى OPEN غير مدموج؛ القرار النهائي للمراجعة المستقلة.
