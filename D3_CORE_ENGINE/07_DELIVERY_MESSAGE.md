# رسالة تسليم D3 للوكيل

نفّذ `D3_CORE_ENGINE` بعد اعتماد G2.

## اقرأ أولاً
1. `D0_MASTER_BLUEPRINT/README.md` و`ARCHITECTURE.md`.
2. `D3_CORE_ENGINE/01` إلى `06`.
3. قيود D1 والقواعد الأمنية.

## المهام
1. تنفيذ `faster-whisper` على VPS و`whisper.cpp` للهاتف، مع ثبات إصدارات النماذج.
2. تنفيذ Agent والـheartbeat والتقدم والإلغاء الآمن.
3. تنفيذ SSE مع authorization وreconnect وlimits.
4. إنشاء Web UI بـHTMX/Jinja2/CSS محلي، مع RTL وإمكانية وصول.
5. إعداد اتصال خاص واختبارات reconnect.

## قيود
- لا faster-whisper على الهاتف.
- لا React/Vue/Next ضمن هذا التصميم.
- لا CDN في الإنتاج.
- لا ادعاء امتثال WCAG أو Core Web Vitals دون نتائج قياس.
- لا أسرار فعلية في ملفات المستودع.

## G3
شغّل واوثق كل بند في `06_ACCEPTANCE_CRITERIA.md`، ثم توقف لانتظار D4.
