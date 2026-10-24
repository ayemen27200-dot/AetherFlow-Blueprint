# D3 — Web UI Specification

## التقنية
- HTMX 2 وJinja2.
- Tailwind CSS 4 مبني ومُستضاف محلياً؛ لا تعتمد CDN في الإنتاج.
- Alpine.js اختياري للتفاعلات الصغيرة فقط.

## الهيكل
```text
web/templates/
├── base.html
├── index.html
├── job_detail.html
├── settings.html
└── partials/
    ├── job_row.html
    ├── progress.html
    └── account_card.html
web/static/css/app.css
web/static/fonts/
web/routes.py
```

## التكيف
- Desktop ≥1280px: تنقل جانبي ومحتوى رئيسي.
- Tablet 768–1279px: تنقل مصغّر.
- Mobile ≤767px: عمود واحد والتنقل الملائم للمس.

## الألوان والتباين
اختبر قيم contrast آلياً لكل زوج foreground/background في الوضعين الفاتح والداكن؛ لا تعتبر الألوان المدرجة أدناه معتمدة دون قياس.
| العنصر | Light | Dark |
|---|---|---|
| Background | `#FFFFFF` | `#0F172A` |
| Primary text | `#1E293B` | `#E2E8F0` |
| Secondary text | `#64748B` | `#94A3B8` |
| Primary button | `#2563EB` | `#3B82F6` |
| Success | `#166534` | `#4ADE80` |
| Error | `#B91C1C` | `#F87171` |

## الخطوط واللمس
- استخدم خطوطاً عربية/لاتينية مرخصة ومُستضافة محلياً، مع `font-display: swap`.
- الحد الأدنى لهدف اللمس حسب WCAG 2.2 SC 2.5.8 هو 24×24 CSS px مع شروط الاستثناء؛ 44×44 هدف تصميمي مفضل.
- اترك مسافات كافية ولا تجعل الإجراءات الخطرة ملاصقة.

## صفحات أساسية
- Dashboard: عدد المهام، الحالة، التقدم، وإجراء إلغاء واضح.
- Job detail: ملخص، تقدم، سجل محدود، مخرجات قابلة للتنزيل.
- Settings: tier، التكاملات، الوكلاء والحصص؛ لا تعرض رموزاً خاماً.

## إمكانية الوصول
- `lang` صحيح واتجاه RTL للعربية.
- عناصر HTML دلالية، كل حقل له label، ورسائل الخطأ مرتبطة بالحقول.
- focus-visible واضح، تنقل لوحة مفاتيح كامل، status عبر النص لا اللون وحده.
- `aria-live="polite"` للتقدم، مع عدم إعلان كل log بشكل مزعج.
- دعم تكبير 200% و`prefers-reduced-motion` وإعادة تدفق بعرض 320px.
- فحص آلي مع axe-core بالإضافة إلى مراجعة يدوية بلوحة المفاتيح وقارئ الشاشة.

## الأداء
الأهداف LCP <2.5s، INP <200ms، CLS <0.1 هي أهداف Core Web Vitals يجب قياسها ضمن ظروف اختبار محددة، لا ضماناً مسبقاً.
