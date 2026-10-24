# رسالة تسليم D2 للوكيل

أنت تنفذ `D2_MVP` من AetherFlow Suite.

## اقرأ
1. `D0_MASTER_BLUEPRINT/README.md` و`ARCHITECTURE.md`.
2. ملفات D1 كاملة، خصوصاً DB schema وrules وerror policies.
3. ملفات D2 من `01_SCOPE.md` إلى `05_ACCEPTANCE_CRITERIA.md`.

## المطلوب
1. تنفيذ API والطابور مع معاملات SQLite وحجز ذري.
2. تنفيذ FFmpeg عبر `subprocess` بقائمة arguments، وtimeout، والتحقق من الملف والمخرجات.
3. تنفيذ CLI Typer مع وضع local ووضع متصل واضحين.
4. إكمال Dockerfile وCompose وCaddyfile و`.gitignore` و`README_D2.md`.
5. كتابة اختبار end-to-end يستخدم fixture صغيرة ويختبر حالات الفشل.

## قيود
- لا Whisper أو ProPainter أو سحابة أو Web UI أو SSE في D2.
- لا `shell=True` ولا مسارات اعتباطية ولا أسرار في Git.
- لا تدّعِ نجاح اختبارات أو أداء قبل تشغيلها.

## G2
اعرض كل بنود `05_ACCEPTANCE_CRITERIA.md` بالأدلة. بعد اعتماد G2 توقف وانتظر D3.
