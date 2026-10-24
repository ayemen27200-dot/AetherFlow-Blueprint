# D3 — Tailscale Setup (Private SSH Tunnel)

## الهدف
استخدام شبكة خاصة لتقليل الحاجة إلى فتح منافذ عامة. تحقّق من خطة Tailscale الحالية وحدودها وسياسة الأجهزة قبل اعتماد أي رقم أو ادعاء مجاني.

## VPS
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname=aetherflow-vps
tailscale status
```
استخدم auth key قصيرة العمر/مقيدة عند الحاجة ولا تضعها في shell history أو المستودع.

## الهاتف / Termux
اتبع الوثائق الرسمية الحالية لتوفر Tailscale على Android/Termux. دعم `tailscaled` وواجهات TUN قد يختلف حسب الجهاز وطريقة التثبيت؛ لا تفترض أن `pkg install tailscale` صالح لكل Termux.

## SSH
في `~/.ssh/config` على VPS، بعد إعداد مفتاح SSH موثوق والتحقق من host key:
```sshconfig
Host phone
  HostName <phone-private-ip>
  User <termux-ssh-user>
  Port 8022
  ServerAliveInterval 30
  ServerAliveCountMax 3
```

نفق اختياري:
```bash
autossh -M 0 -N \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:8765:127.0.0.1:8765 phone
```

## خدمة تلقائية
اكتب unit لـsystemd على VPS فقط عند الحاجة، وحدد `User=` غير root، و`Restart=always`، وصلاحيات الملف. لا تنسَ أن systemd لا يعمل كخدمة نظام عادية داخل Termux؛ استخدم آلية مدعومة من Termux.

## مراقبة
- health/heartbeat من التطبيق أفضل من فحص `tailscale status` وحده.
- لا ترسل تفاصيل حساسة إلى Telegram.
- لا تستخدم port forwarding عام كـfallback تلقائي.
