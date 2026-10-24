# D3 — Agent Control Protocol

## المعمارية
- Agent على الهاتف، افتراضياً port 8765 محلياً فقط.
- اتصال Orchestrator بالوكيل عبر Tailscale أو نفق SSH خاص.
- المصادقة عبر `X-Agent-Token` منفصل عن `BEARER_TOKEN`.

## Endpoints الوكيل
```yaml
POST   /jobs
GET    /jobs/{id}
GET    /jobs/{id}/progress
POST   /jobs/{id}/cancel
GET    /capabilities
GET    /health
```

## Heartbeat
يرسل الوكيل كل 30 ثانية تقريباً: timestamp، قدرات عامة، وحالة المهمة الحالية. لا يرسل محتوى الملف أو الأسرار. بعد 90 ثانية دون نبض يمكن وضع البيئة `offline` بعد مراعاة skew الشبكة.

مثال للحمولة:
```json
{
  "timestamp": "2026-01-15T10:30:00Z",
  "capabilities": {
    "ram_available_gb": 4.2,
    "battery_pct": 78,
    "thermal_status": "normal",
    "storage_free_gb": 51
  },
  "current_job": {"id": "job_123", "progress_pct": 42}
}
```

## Progress push
`POST /agents/{id}/jobs/{job_id}/progress` مع `progress_pct`, `stage`, و`eta_sec` عند توفرها. تحقّق من ملكية agent للمهمة ومن النسب والمدخلات.

## رموز المصادقة
ولّد الرموز بمولد آمن، خزّنها بصلاحيات ملفات مناسبة، ولا تطبعها في السجلات. وفر مساراً لإبطالها وتدويرها. لا تضع رمزاً حقيقياً في repository.

## إعادة الاتصال
- استخدم `autossh`/خدمة تشغيل مناسبة مع `ServerAliveInterval` و`ExitOnForwardFailure`.
- يمكن استخدام Tailscale كشبكة خاصة؛ تحقق من قيود Termux والجهاز.
- لا تستخدم port forwarding عام كـfallback افتراضي. أي فتح عام يحتاج مراجعة أمنية مستقلة.

## مخرجات D3
`agent/agent.py`, `agent/protocol.py`, heartbeat worker، واختبارات API ومصادقة وفقد اتصال.
