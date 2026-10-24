# D4 — Acceptance Criteria (G4)

## Functional
- [ ] text_removal يعمل على VPS GPU في عينة تقييم محددة.
- [ ] بدون GPU، ينقل إلى Modal فقط بموافقة صريحة وسياسة تكلفة سليمة.
- [ ] OOM ينتج checkpoint صالحاً؛ الاستئناف يتحقق من سلامة المقاطع.
- [ ] LiteLLM اختياري واختبارات الفشل/الـcooldown ناجحة.
- [ ] عند 80% يصدر تحذير مكرر مضبوط؛ عند 100% يتوقف dispatch.
- [ ] Telegram يدعم أنواع الأحداث السبعة دون بيانات حساسة.
- [ ] `local_only` يمنع كل السحابة؛ `free` يمنع أي مزود مدفوع غير مصرح به.

## Non-functional
- [ ] تكلفة كل اختبار سحابي موثقة من usage حقيقي؛ لا افتراض مبلغ ثابت مجاني.
- [ ] جودة ProPainter مقاسة بعينة ومعيار مراجعة موثق.
- [ ] المفاتيح والـvolumes المؤقتة تنظف وفق سياسة قابلة للتحقق.

## Edge cases
- [ ] لا توجد موافقة سحابة: رفض واضح.
- [ ] انتهاء الحصة: يتوقف dispatch دون fallback مدفوع تلقائي.
- [ ] تعطّل Telegram: تسجل الأحداث محلياً.
- [ ] فشل حذف volume: تنبيه وإعادة محاولة تنظيف مضبوطة.

## Deliverables
- [ ] `executors/propainter.py`, `modal_propainter.py`
- [ ] `scripts/modal_app.py`
- [ ] `orchestrator/quota_manager.py`, `notifier.py`
- [ ] `docker/litellm_config.yaml` عند تفعيل LiteLLM
- [ ] اختبارات cloud/quota/notifier

## G4
اعرض مساراً ناجحاً ومسارات الفشل مع إثبات موافقة السحابة وقياس التكلفة الفعلية قبل الانتقال إلى D5.
