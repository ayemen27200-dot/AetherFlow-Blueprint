# AetherFlow Runbook — Troubleshooting Matrix

| # | العَرَض | السبب المحتمل | الإجراء |
|---|---|---|---|
| 1 | SSH timeout | اتصال Tailscale أو SSH متوقف | افحص `tailscale status` وسجلات الاتصال |
| 2 | Whisper hangs | ذاكرة غير كافية أو ملف غير صالح | افحص RAM والسجلات ثم أعد بنموذج أصغر |
| 3 | ProPainter OOM | VRAM غير كافية | قلل حجم الدفعة/المقطع أو أعد التوجيه بعد موافقة السحابة |
| 4 | Quota exhausted | بلوغ الحصة | أوقف المهام السحابية وأبلغ المستخدم |
| 5 | Agent offline | شبكة/بطارية/حرارة | افحص heartbeat والطاقة ثم أعد الخدمة |
| 6 | job_events كبيرة | سياسة التنظيف لم تعمل | نفذ مهمة retention المجدولة بعد النسخ الاحتياطي |
| 7 | Cloud rejected | tier أو سياسة تكلفة تمنعها | اعرض السبب ولا تتجاوز موافقة المستخدم |
| 8 | Cloud forbidden | local_only أو عدم موافقة | أبقِ المهمة محلية واطلب موافقة صريحة عند الحاجة |
## Model lock and checksum policy

`09_models.lock.json` is JSON and must remain valid JSON; place operational notes here rather than adding comments inside the file. `FILL_ON_FIRST_RUN` and `PIN_BEFORE_RELEASE` are explicit unresolved placeholders, not trusted hashes.

Before enabling a model in production:

1. Pin the upstream repository revision or immutable artifact URL.
2. Download in a controlled environment and verify source provenance/licensing.
3. Compute SHA-256 and compare it with a checksum obtained through a trusted, independent release source where available.
4. Replace the placeholder with the reviewed checksum/revision in both lock-file copies and commit the reviewed change.
5. Make the loader fail closed when the revision/hash is unresolved or mismatched. Do not automatically bless and write the hash of an arbitrary first download.

A matching SHA-256 proves byte-level integrity against the chosen digest; it does not prove that the model is safe, licensed, or authoritative.
