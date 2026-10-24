# D5 — Security Audit Checklist

> ربط الضوابط بفئات OWASP Top 10 مناسب كإطار مراجعة، لكنه لا يمثل تدقيقاً أو شهادة مكتملة.

## Access control
- [ ] `/accounts` محمي برمز Bearer، والقيود المحلية enforced على مستوى الشبكة والتطبيق.
- [ ] كل endpoint للوكيل يتطلب token مستقلاً.
- [ ] `/jobs/{id}` يتحقق من ملكية المورد/السياق حتى في single-user لتقليل IDOR.
- [ ] لا وصول مباشر إلى Uvicorn من الإنترنت.

## Configuration
- [ ] `DEBUG=False` في الإنتاج.
- [ ] CORS محدود؛ لا wildcard مع credentials.
- [ ] Security headers مناسبة: HSTS عند HTTPS حقيقي، CSP، X-Content-Type-Options، frame policy.
- [ ] توثيق API معطّل أو محمي عند الحاجة.

## Supply chain
- [ ] تثبيت نسخ Python dependencies والصور.
- [ ] `pip-audit` وفحص image/dependency بمخرجات محفوظة.
- [ ] تحديث دوري ومراجعة dependabot.
- [ ] تثبيت commit/hash لأوزان النماذج والتحقق من شروط التراخيص.

## Cryptography and secrets
- [ ] مفاتيح API مشفرة عند التخزين؛ مفتاح التشفير مخزن منفصلاً.
- [ ] رموز طويلة عشوائية ومخزنة بصلاحيات محدودة.
- [ ] لا أسرار في Git أو logs أو URLs.
- [ ] `.env` في `.gitignore`، وتدوير الأسرار عند التسرب.

## Injection and files
- [ ] SQL parameterized فقط.
- [ ] FFmpeg invoked via argument list, never `shell=True` with user input.
- [ ] Jinja autoescape، وتحقيق من نوع الملف والحجم والمسار.
- [ ] فك الأرشيفات/الرفع لا يسمح بالـpath traversal.

## Design and availability
- [ ] Rate limiting فعلي واختباره.
- [ ] حد حجم الإدخال ووقت التنفيذ والتزامن.
- [ ] حدود تكلفة وحصة للسحابة، وموافقة صريحة.
- [ ] graceful shutdown واستعادة jobs العالقة.

## Logging and exceptions
- [ ] JSON logs مع redaction وrotation.
- [ ] لا stack trace للمستخدم ولا محتوى ملفات في logs.
- [ ] error policies متناسقة مع حالات قاعدة البيانات.

## Tools
- [ ] bandit أو فحص static مناسب.
- [ ] pip-audit.
- [ ] Trivy أو scanner موثوق للصورة والملفات.
- [ ] OWASP ZAP/اختبار DAST في بيئة staging.

يجب توقيع نتائج التدقيق الفعلية مع commit والإصدارات وتاريخ التشغيل؛ هذه القائمة وحدها ليست إثبات امتثال.
