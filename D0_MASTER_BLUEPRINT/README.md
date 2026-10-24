# AetherFlow Suite — Master Blueprint v1.0

**نظام تنسيق مهام معالجة الفيديو الموزعة** — مجاني، مفتوح المصدر، Local-First.

## الهدف
توزيع مهام معالجة الفيديو (تفريغ، إزالة نصوص، تقسيم) بين الهاتف (Termux/ARM64)، وVPS، والسحابة (Modal).

## الهوية
- الرخصة: MIT
- الوضع الافتراضي: `tier: free`
- الجمهور: Single-User
- الإصدار: v1.0 blueprint

## مراحل التسليم
| Dossier | المرحلة | المدة التقديرية | المحتوى |
|---|---|---|---|
| D1_FOUNDATION | 0 | 3–5 أيام | مواصفات الأساس |
| D2_MVP | 1 | أسبوع | Orchestrator + FFmpeg + SQLite |
| D3_CORE_ENGINE | 2 | أسبوعان | Whisper + Agent + Web UI |
| D4_CLOUD_SCALE | 3 | أسبوعان | ProPainter + Modal + Quotas |
| D5_HARDENING | 4 | أسبوعان | Tests + Security + Release |

## المعايير المرجعية
- ISO 21500/21502 لإدارة المشاريع
- ISO/IEC 25010 لجودة البرمجيات
- OWASP Top 10 لأمن التطبيقات
- WCAG 2.2 AA لإمكانية الوصول
- Core Web Vitals للأداء
- تقليل البيانات والاحتفاظ المحدود بها

> يجب التحقق من إصدارات المعايير والتراخيص والأسعار قبل التنفيذ؛ الإشارات هنا متطلبات تصميم وليست شهادة امتثال.

## القيود الصارمة
- Single-User فقط — لا مصادقة متعددة المستخدمين.
- SQLite فقط — لا Redis/Postgres.
- `rules.yaml` مصدر قرارات التوجيه.
- Modal هو مسار السحابة المعتمد في هذه الخطة.
- SQLite Queue — لا Celery/RabbitMQ.
- Docker Compose — لا Kubernetes.
- EasyOCR على السحابة فقط، وليس الهاتف.
- Caddy أمام الخدمة؛ لا تعريض Uvicorn مباشرة للإنترنت.
- `default_fallback` إلزامي في `rules.yaml`.
- `faster-whisper` ممنوع على الهاتف.

## Tech Stack المقترح
- Backend: FastAPI + Pydantic v2
- Frontend: HTMX + Jinja2 + Tailwind CSS
- DB: SQLite مع WAL
- AI Voice: faster-whisper (VPS) / whisper.cpp (Phone)
- AI Video: ProPainter
- Cloud: Modal؛ تحقق من حدود الخطة الحالية قبل الاعتماد عليها
- Proxy: Caddy + LiteLLM عند الحاجة
- Container: Docker Compose v2

## بوابات الجودة
| بوابة | نهاية | معيار القبول |
|---|---|---|
| G1 | D1 | مراجعة المواصفات والتحقق منها |
| G2 | D2 | مهمة split ناجحة end-to-end |
| G3 | D3 | تفريغ عربي + واجهة متجاوبة |
| G4 | D4 | معالجة OOM وإعادة توجيه موثقة |
| G5 | D5 | اختبارات وأمن وإمكانية وصول موثقة |

## تعليمات التسليم للوكيل
1. اقرأ هذا الملف أولاً.
2. التزم بالقيود الصارمة.
3. لا تنتقل إلى المرحلة التالية قبل اعتماد بوابة الجودة.
4. وثّق الافتراضات والمخاطر، واسأل عند وجود غموض جوهري.

## Definition of Done
- [ ] مراجعة ملفات D1 والتحقق منها آلياً
- [ ] `docker compose up` يعمل على VPS نظيف
- [ ] مهام split وtranscription وtext_removal موثقة باختبارات
- [ ] تدقيق WCAG 2.2 AA موثق
- [ ] مراجعة OWASP موثقة
- [ ] تغطية الاختبارات المستهدفة 70% أو أكثر
- [ ] RUNBOOK يغطي الأعطال الأساسية
- [ ] دليل تثبيت واضح
