# رسالة تسليم D4 للوكيل

نفّذ `D4_CLOUD_SCALE` بعد اعتماد G3.

## اقرأ
1. D0 README وArchitecture.
2. `D4_CLOUD_SCALE/01` إلى `06`.
3. قواعد D1 للتكلفة والموافقة والأخطاء.

## المهام
1. تنفيذ ProPainter على VPS GPU مع chunking/checkpoint مختبرين.
2. بناء Modal worker وExecutor مع نقل آمن وقيود موارد وتنظيف.
3. تنفيذ quota manager وkill switch مع اختبارات.
4. إضافة LiteLLM فقط إذا كانت هناك حاجة فعلية لخدمات LLM.
5. تنفيذ Telegram notifier اختياري يحفظ الأحداث عند فشل الإرسال.
6. تفعيل `guard_cloud_dispatch` في decision engine.

## القيود
- لا Kaggle برمجياً.
- لا EasyOCR على الهاتف.
- لا رفع إلى السحابة دون `cloud_consent`.
- لا مزود مدفوع عند `tier=free` دون سياسة وموافقة مناسبة.
- لا ادعاء بأن الحصة المجانية أو أسعار GPU ثابتة.

## G4
أرفق سجلات اختبار ونتائج جودة وتكلفة واستعادة checkpoint. بعد اجتياز G4 توقف وانتظر D5.
