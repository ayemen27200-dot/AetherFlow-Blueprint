# D4 — Quota Manager Specification

## Schema
يفضل أن تكون قيم الدولار/الساعات `REAL` لا `INT`، مع تعريف الوحدة ومصدر الرقم.
```sql
CREATE TABLE IF NOT EXISTS provider_quotas (
  provider TEXT NOT NULL,
  metric TEXT NOT NULL,
  limit_value REAL,
  current_value REAL NOT NULL DEFAULT 0,
  reset_at TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (provider, metric)
);
```

## عتبات
| الاستخدام | الإجراء |
|---|---|
| أقل من 80% | اعمل بشكل طبيعي وفق حدود السياسة |
| 80–99% | إشعار واحد أو مجمّع لتجنب التنبيه المفرط |
| 100% أو أكثر | أوقف المهام الجديدة لهذا المقياس وأبلغ المستخدم |

## حلقة دورية — واجهة مقترحة
```python
async def quota_check() -> None:
    # TODO: query only configured providers; avoid dividing by zero/null limits
    # TODO: reconcile estimates with actual provider-reported usage
    # TODO: deduplicate alerts and apply a transactional kill switch
    raise NotImplementedError
```

تحقق من حدود كل مزود من مصدر موثوق وقت التنفيذ. لا تفترض أن quota API أو `client.get_usage()` موجودة في إصدار SDK بعينه دون التحقق من وثائق ذلك الإصدار.

## API
- `GET /quotas`
- `GET /quotas/{provider}`
- `POST /quotas/{provider}/kill`

المسارات محمية بالمصادقة، والـkill switch لا يحذف سجلات ولا يبدأ تحويل المهام إلى مزود مدفوع بلا موافقة. إعادة التفعيل تتطلب قراراً صريحاً.

## الاختبارات
حدود 79/80/99/100/101%، limit غير موجود أو صفر، reset، تعطل المزود، والتكرار في التنبيهات.
