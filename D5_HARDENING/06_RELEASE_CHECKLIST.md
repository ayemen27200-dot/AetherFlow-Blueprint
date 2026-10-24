# D5 — Release Checklist (v1.0.0)

## بوابات ما قبل الإصدار
- [ ] G1–G5 اجتازت مراجعاتها بأدلة محفوظة.
- [ ] تغطية tests ≥70% مع مراجعة جودة الاختبارات.
- [ ] نتائج bandit/dependency/image scans مراجعة وإغلاق المخاطر المقبولة.
- [ ] اختبارات accessibility آلية ويدوية مكتملة.
- [ ] أهداف Core Web Vitals مقاسة ببيئة موثقة.
- [ ] مخاطر OWASP موثقة؛ لا تدّعِ شهادة رسمية.
- [ ] RUNBOOK يغطي الأعطال الأساسية.
- [ ] تثبيت نظيف ودليل تشغيل مختبران.
- [ ] MIT LICENSE وإشعارات تراخيص dependencies والنماذج مراجعة.
- [ ] `.env.example` بلا أسرار.
- [ ] حجم الصورة مقاس؛ لا تجعل حد 500MB شرطاً دون مبرر.

## وثائق
- [ ] README
- [ ] ARCHITECTURE.md
- [ ] OpenAPI 3.1 في `D1_FOUNDATION/03_API_SPEC.yaml` موثق ويمرّ بمدقق OpenAPI
- [ ] RUNBOOK / CONTRIBUTING / CHANGELOG
- [ ] SECURITY_AUDIT وACCESSIBILITY_AUDIT بنتائج حقيقية

## Release
- [ ] tag `v1.0.0` بعد اعتماد جميع البوابات.
- [ ] release notes وartifact hashes.
- [ ] backup قبل deploy، وخطة rollback.
- [ ] رصد بعد النشر ومراجعة الحوادث.

## Definition of Done
- [ ] split وtranscription وtext_removal نجحت end-to-end على بيئات مدعومة.
- [ ] phone/VPS/cloud تعمل حيث تم إعدادها واختبارها.
- [ ] smart offloading وtier enforcement مختبران.
- [ ] backup/restore مجرّبان.
- [ ] لا توجد ثغرات حرجة غير معالجة وفق مراجعة محددة.
- [ ] المثبّت الجديد يستطيع اتباع التعليمات دون أسرار أو اعتماديات مخفية.

هذه القائمة لا تعني أن أي بند اجتاز الاختبار بمجرد وجود هذا الملف.


> `CHANGELOG.md` و`CONTRIBUTING.md` موجودان في الجذر. لا تدّعِ أن CHANGELOG يُولّد تلقائياً ما لم تُضبط أداة الإصدار وتُختبر فعلياً.
