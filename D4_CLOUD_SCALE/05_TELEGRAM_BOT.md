# D4 — Telegram Bot Notifications

## إعداد
1. أنشئ bot عبر `@BotFather`.
2. احصل على `chat_id` بطريقة آمنة ولا تكشف token في سجل أو screenshot.
3. خزّن `TELEGRAM_BOT_TOKEN` و`TELEGRAM_CHAT_ID` في secret/env محلي.

## الأحداث
| Event | Priority | الرسالة |
|---|---|---|
| `job_completed` | info | اكتملت مهمة {job_id} من النوع {type} |
| `job_failed` | warning | فشلت مهمة {job_id}: {error_type} |
| `job_stalled` | warning | المهمة {job_id} متوقفة مؤقتاً |
| `quota_80` | warning | اقترب حد مزود من النفاد |
| `quota_100` | critical | تم إيقاف dispatch لمزود بسبب الحصة |
| `agent_offline` | critical | الوكيل غير متصل منذ أكثر من المهلة |
| `cloud_forbidden` | info | رُفض الإرسال للسحابة بسبب السياسة |

## التنفيذ — واجهة أولية
```python
import httpx

async def send_telegram(text: str, bot_token: str, chat_id: str) -> None:
    # TODO: validate allowlisted chat, escape text safely, handle status/error,
    # rate-limit and redact; never log token or full request URL.
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            "https://api.telegram.org/bot" + bot_token + "/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
        response.raise_for_status()
```

## قيود
- لا ترسل مسارات الملفات أو مفاتيح API أو محتوى الفيديو.
- لا تجعل Telegram شرطاً لتشغيل الوظائف الأساسية.
- أضف batching/retry محدوداً واختبار mock. احفظ الأحداث في DB عند فشل الإرسال.
