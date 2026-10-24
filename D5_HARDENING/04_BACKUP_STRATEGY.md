# D5 — Backup and Restore Strategy

## البيانات
- SQLite database وفق دورة احتياطي محددة.
- `config/rules.yaml` والإعدادات غير السرية عند التغيير.
- الأسرار لا تُنسخ كنص واضح؛ تستخدم إدارة أسرار منفصلة أو نسخة مشفرة بسياسة وصول مستقلة.
- ملفات الفيديو المؤقتة لا تدخل النسخ الاحتياطي افتراضياً.

## وجهة التخزين
يمكن استخدام Backblaze B2 أو وجهة متوافقة أخرى بعد التحقق من الأسعار والسياسات والاحتفاظ. استخدم `rclone crypt` أو تشفيراً موثوقاً، مع حفظ مفتاح الاستعادة منفصلاً.

## مثال نسخ قاعدة SQLite
```bash
#!/usr/bin/env bash
set -euo pipefail
DATE="$(date +%Y%m%d_%H%M%S)"
BACKUP="/tmp/aetherflow_${DATE}.db"
sqlite3 /app/data/aetherflow.db ".backup '${BACKUP}'"
# TODO: encrypt/upload with least-privilege credentials and verify remote checksum
rclone copy "$BACKUP" remote-encrypted:aetherflow-backup/db/
rm -f "$BACKUP"
```

لا تستخدم هذا المثال قبل ضبط remote مشفر فعلياً والتحقق من الوجهة؛ لا ترفع ملفات `.env` أو مفاتيح التشفير مع النسخ نفسها.

## Cron وRetention
- جدول احتياطي كل 6 ساعات مثال مبدئي؛ اعتمد RPO/RTO الفعليين.
- سياسة دورية: يومي 7 أيام، أسبوعي 4 أسابيع، شهري 12 شهراً إذا كانت ملائمة للتكلفة والمتطلبات.
- اختبر حذف النسخ القديمة واستثناء النسخ المحمية من الحذف.

## Restore
- استعد إلى ملف مؤقت/بيئة staging أولاً.
- تحقق من `PRAGMA integrity_check;` ثم بدّل قاعدة البيانات بطريقة آمنة بعد إيقاف الخدمة.
- لا تستعد فوق قاعدة حية باستخدام أمر غير مدروس.

## الاختبار
شهرياً جرّب الاستعادة، وسجّل وقت الاستعادة والنتيجة وRPO/RTO؛ لا تُعتبر سياسة النسخ صالحة دون تجربة فعلية.
