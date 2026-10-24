# D2 — SQLite Queue Design

## المبدأ
طابور دائم باستخدام SQLite (لا Redis). ترتيب الاستدعاء: الأولوية تنازلياً ثم وقت الإنشاء تصاعدياً.

## Schema مرجعي
يجب أن يتسق مع `D1_FOUNDATION/02_DB_SCHEMA.sql`. عمود JSON في SQLite يُخزن كنص JSON عند الحاجة، مع تحقق صريح في التطبيق.
```sql
CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
```

## Enqueue
```sql
INSERT INTO jobs (id, type, priority, status, input_path, cloud_consent)
VALUES (?, ?, ?, 'queued', ?, ?);
```
استخدم معاملات parameterized فقط، وتحقق من الملف والمسار والحجم قبل إدخال المهمة.

## Dequeue ذري (SQLite 3.35+)
```sql
BEGIN IMMEDIATE;
UPDATE jobs
SET status = 'assigned', started_at = CURRENT_TIMESTAMP
WHERE id = (
  SELECT id FROM jobs
  WHERE status = 'queued'
  ORDER BY priority DESC, created_at ASC
  LIMIT 1
)
RETURNING *;
COMMIT;
```
نفّذ الحجز داخل معاملة مع معالجة حالة عدم وجود صف. لا تبقِ المعاملة مفتوحة أثناء تشغيل FFmpeg.

## استعادة المهام العالقة
قاعدة الاستعادة أدناه توضيحية؛ لا تستخدم `started_at` كبديل عن heartbeat لكل بيئة في النسخة الموزعة النهائية. يجب فصل stale-running عن حالة processing الفعلية وتحديد سياسة إعادة المحاولة:
```sql
UPDATE jobs
SET status = CASE WHEN retry_count >= 3 THEN 'failed' ELSE 'queued' END,
    retry_count = retry_count + 1
WHERE status = 'processing'
  AND started_at < datetime('now', '-90 seconds');
```

## التزامن
- `PRAGMA journal_mode=WAL;`
- `PRAGMA foreign_keys=ON;`
- `PRAGMA busy_timeout=5000;`
- حد افتراضي مهمة نشطة واحدة لكل بيئة في D2.
- قفل الكتابة واحد في SQLite؛ الأداء الفعلي يعتمد على نمط الوصول وحجم الأحداث.

## أهداف أداء أولية
- Enqueue < 5ms وdequeue < 10ms كأهداف benchmark لا شروطاً مضمونة.
- اختبار 1000 مهمة انتظاراً/نشطة، مع قياس زمن المعاملة وbusy errors.
