# D4 — Modal Integration

## ملاحظة قبل التنفيذ
قد تتغير الأسعار والاعتمادات وتوفر GPU؛ افحص موقع Modal الرسمي وحسابك وقت النشر. لا تعتمد على مبلغ مجاني ثابت أو توفر A10G دون التحقق.

## إعداد
- أنشئ token بالطريقة الموثقة في نسخة Modal CLI المثبتة.
- خزّن بيانات الدخول في secrets/environment، لا في Git.
- قيّد runtime، حجم الإدخال، التزامن، وحجم المخرجات.

## Skeleton توضيحي
```python
import modal
app = modal.App("aetherflow-propainter")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "git")
    .pip_install("torch", "torchvision", "easyocr", "opencv-python-headless")
    # TODO: pin a reviewed ProPainter commit and compatible dependencies
)

@app.function(gpu="A10G", timeout=3600, image=image)
def inpaint_video(input_path: str, mask_mode: str = "auto") -> str:
    # TODO: fetch input from controlled storage, run model, validate output
    # TODO: return an object reference, not a huge media byte string
    raise NotImplementedError
```

لا تستخدم `git clone` غير مثبت في صورة الإنتاج ولا تعِد bytes لفيديو ضخم من function response؛ صمّم رفع/تنزيل مع التخزين المؤقت والسياسات.

## التكلفة والحصة
احسب التكلفة باستخدام الأسعار الفعلية وقت التنفيذ والمقياس الذي يعلنه المزود. المعادلة الثابتة مثل `$0.05/min` مثال افتراضي لا يصلح كمرجع مالي دون تحقق. إذا كانت الكلفة غير معروفة، اطلب تأكيداً قبل dispatch.

## Checkpoint / Resume
قسّم المهمة إلى chunks، خزّن تقدمها في سجل دائم، وأعد محاولة chunk الفاشل فقط إذا كان checkpoint قابلاً للتحقق. نظّف الملفات المؤقتة بعد نجاح/فشل، مع نافذة احتفاظ قصيرة ومراقبة.

## Vast.ai (اختياري ومدفوع)
مغلق افتراضياً. لا تفعّله في `tier=free`؛ اطلب موافقة صريحة على التكلفة قبل التبديل.

## مخرجات
`scripts/modal_app.py`, `executors/modal_propainter.py`, إعدادات modal، واختبارات فشل/تكلفة/تنظيف.
