# D3 — SSE Progress Streaming

## Endpoint
`GET /jobs/{id}/progress`, استجابة `text/event-stream` ومصادقة المستخدم المطلوبة.

## شكل الرسائل
```text
event: progress
data: {"job_id":"job_123","pct":45,"stage":"processing","eta_sec":120}

event: log
data: {"level":"info","msg":"chunk 3/10 completed"}

event: done
data: {"status":"completed","outputs":["part_000.mp4"]}
```

## مبادئ التنفيذ
- تحقّق من وجود المهمة وصلاحية الوصول قبل فتح stream.
- أرسل JSON آمن ومحدود الحجم؛ لا ترسل مسارات خاصة أو tokens.
- استخدم `id:` و`Last-Event-ID` إذا كانت الاستعادة بعد الانقطاع مطلوبة.
- أرسل تعليق keepalive دورياً، وانهِ stream عند الحالات النهائية.
- احترم `request.is_disconnected()` وقيود الاتصالات المتزامنة.
- يجب ألا يتحول polling الداخلي إلى استعلام كثيف؛ استخدم polling مضبوطاً أو إشعارات أحداث.

## مثال توضيحي
```python
import asyncio
from fastapi.responses import StreamingResponse

async def event_stream(job_id: str):
    while True:
        # TODO: read latest persisted event, not an unbounded busy loop
        yield "event: keepalive\ndata: {}\n\n"
        await asyncio.sleep(15)

@app.get("/jobs/{job_id}/progress")
async def progress(job_id: str):
    # TODO: authenticate, validate job, cap connections, and handle disconnect
    return StreamingResponse(event_stream(job_id), media_type="text/event-stream")
```

## HTMX
يمكن ربط HTMX SSE extension إن كانت نسختها مناسبة ومثبتة محلياً. اختبر reconnect وسلوك DOM؛ لا تفترض أن `hx-ext="sse"` متوفر دون تضمين extension الملائم.

## الاختبار
`tests/integration/test_sse.py`: استقبال progress ثم done، إعادة الاتصال، انتهاء المهمة، عدم صلاحية الوصول، انقطاع العميل، وlimit الاتصالات.
