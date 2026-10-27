# مرجع الأوامر وجمع الأدلة — وكيل VPS

> كل أمر في هذا الملف مبني على ملفات فعلية في المستودع روجعت في رأس الحقيبة
> `448526a4a52f45b0c5f7a667f5c27e3493595534` (الخدمات: `orchestrator`،
> `worker`، `caddy`؛ السكربتات: `scripts/*.sh`؛ المتغيرات: `.env.example`
> و`docker/docker-compose.yml`). لا أسماء مخترعة ولا بدائل موازية للسكربتات
> القائمة. افحص الآثار الجانبية لكل سكربت بقراءة مصدره قبل التشغيل.

## 0) إعداد جمع الأدلة (نفّذ أولًا)

```bash
EVIDENCE_DIR="$HOME/aetherflow-vps-evidence/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$EVIDENCE_DIR"
# دالة تسجيل: الاسم، الأمر، التوقيت UTC، رمز الخروج، المخرجات الكاملة.
run_ev() {
  local name="$1"; shift
  local log="$EVIDENCE_DIR/$name.log"
  {
    printf '# cmd: %q ' "$@"; printf '\n# utc: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    "$@"
    printf '# exit: %s\n' "$?"
  } 2>&1 | tee "$log"
}
```

- مجلد الأدلة خارج المستودع عمدًا حتى لا يصل إلى Git. المخرجات داخل المستودع
  توضع تحت `data/` (متجاهَل في `.gitignore`)، ثم تُنسخ إلى مجلد الأدلة.
- الأوامر المركبة (أنابيب/حلقات) تُلفّ بـ`bash -c '...'` كما في خطة التحقق.
- فعّل `set -o pipefail` في الجلسة حتى لا يضيع رمز فشل مكوّن وسط أنبوب.

## 1) الطبقة الأولى — قراءة فقط (المسار الافتراضي، بلا أي تغيير)

### 1.1 الهوية والنظام
```bash
date -u +%FT%TZ; hostname; id; uname -a
cat /etc/os-release
dpkg --print-architecture 2>/dev/null || true
nproc; grep -m1 'model name' /proc/cpuinfo
awk '/MemTotal/ {print}' /proc/meminfo; df -P "$HOME"
command -v docker && docker --version
docker compose version
python3 --version
command -v ffmpeg && ffmpeg -version | head -1
command -v ffprobe && ffprobe -version | head -1
```

### 1.2 حالة المستودع والفرع والـSHA
```bash
git remote -v
git branch --show-current
git rev-parse HEAD                 # الشاهد الفعلي المختبر — سجّله كاملًا
git status --porcelain             # المتوقع: بلا مخرجات (شجرة نظيفة)
git log --oneline -5
gh run list --branch arena/940efcbe-aetherflow-blueprint --limit 5   # إن توفّر gh
```

### 1.3 الأسرار والإعدادات دون طباعة قيم
```bash
[ -e .env ] && stat -c '%a %U %G %n' .env || echo '.env absent'
git check-ignore -v .env
git ls-files | grep -E '(^|/)\.env'
grep -nE '^[A-Z0-9_]+=.+' .env.example || echo 'only blank assignments in template'
git ls-files | grep -E '\.(pem|key|p12)$' || echo 'no key files tracked'
git ls-files | grep -E '\.(mp4|mkv|mov|wav|mp3|db)$'   # المتوقع: tests/fixtures/sample_5s.mp4 فقط
```

### 1.4 بنية Compose والمنافذ والموارد القائمة
```bash
# تصيير الإعلان دون إنشاء أي مورد (env_file اختياري required:false في المصدر):
docker compose -f docker/docker-compose.yml config
ss -tln                              # منافذ الاستماع؛ -p تحتاج sudo فتُتجنب افتراضيًا
docker ps -a --filter 'label=com.docker.compose.project=aetherflow' \
  --format '{{.Names}} {{.Status}} {{.Ports}}'
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -E 'aetherflow|caddy' || true
```

### 1.5 فحص صياغة السكربتات ومحتوى المستودع
```bash
for s in scripts/*.sh; do bash -n "$s" || exit 1; done
git grep -nI -E 'BEGIN ([A-Z ]+ )?PRIVATE KEY|api[_-]?key|secret|token|password' -- .
```
نتيجة `git grep` تُراجَع يدويًا: المقبول أسماء متغيرات ونماذج فارغة، والمرفوض
قيم سرية فعلية.

## 2) الطبقة الثانية — اختبارات في بيئة معزولة (آثارها محصورة)

كل ما يلي يكتب داخل `.venv/` أو `data/` أو موارد Docker موسومة بعلامات فريدة،
وكلها متجاهَلة/معزولة ولا تلمس مشروع `aetherflow` الثابت.

### 2.1 بيئة Python المعزولة والفحوص المماثلة لوظيفة CI `verify`
```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
.venv/bin/python scripts/validate_specs.py
.venv/bin/openapi-spec-validator D1_FOUNDATION/03_API_SPEC.yaml
.venv/bin/python -m compileall -q agent orchestrator executors web scripts
.venv/bin/ruff check .
mkdir -p data/vps-evidence
AETHERFLOW_REQUIRE_MEDIA_BINARIES=1 .venv/bin/python -m pytest -q -ra \
  --junitxml=data/vps-evidence/junit.xml
.venv/bin/python scripts/summarize_junit.py data/vps-evidence/junit.xml
cp data/vps-evidence/junit.xml "$EVIDENCE_DIR/"
```
يتطلب فحص الوسائط الحقيقية ثنائيي ffmpeg/ffprobe على المضيف؛ مع الحارس
`AETHERFLOW_REQUIRE_MEDIA_BINARIES=1` غياب الثنائي يفشل بدل التخطي. كل تخطٍّ
يُسجَّل بسببه الحرفي من مخرجات pytest.

### 2.2 تحقق مكدس Docker المعزول (11 قسمًا، غير مدمر)
```bash
bash scripts/verify_compose_stack.sh 2>&1 | tee "$EVIDENCE_DIR/06-isolated-compose.log"
```
آثاره المؤكدة من المصدر: مجلد `mktemp` فريد، مشروع `aetherflowsmoke<ts>-<pid>`،
صورة بوسم فريد، وحدة تخزين مسماة فريدة، توكن عابر في env وضع 0600؛ والتنظيف
مقصور على موارده عبر `trap`، وقسم 11 يثبت عدم تغيّر مشروع `aetherflow`
الثابت وشجرة `data/` بالمستودع. لا تضيف أعلامًا ولا تستخدم نسخة معدّلة.

### 2.3 (اختياري) مسار العمليتين المحلي — كل الأثر تحت data/ وعلى 127.0.0.1
```bash
TS=$(date -u +%Y%m%dT%H%M%SZ); LAB="data/local-e2e-$TS"
mkdir -p "$LAB/db" "$LAB/upload_store" "$LAB/outputs"
export BEARER_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
export DATABASE_PATH="$LAB/db/aetherflow.db"
# عملية API في طرفية منفصلة (لا تربط إلا واجهة loopback):
.venv/bin/uvicorn orchestrator.main:app --host 127.0.0.1 --port 8000
# عملية العامل:
.venv/bin/aetherflow worker run --db-path "$LAB/db/aetherflow.db" \
  --upload-store "$LAB/upload_store" --output-dir "$LAB/outputs" \
  --segment-seconds 300 --concurrency 1
# بذر الكائنات بأسماء معرّفات opaque (المعرّف اسم الكائن داخل المخزن):
cp tests/fixtures/sample_5s.mp4 "$LAB/upload_store/up-sample-5s.mp4"
python3 - "$LAB/upload_store/up-sample-5s.mp4" "$LAB/upload_store/up-corrupt.mp4" <<'PY'
import sys, pathlib
src = pathlib.Path(sys.argv[1]).read_bytes()
data = bytearray(src); mid = len(data)//2
data[mid:mid+4096] = b"\x00"*4096
pathlib.Path(sys.argv[2]).write_bytes(data)
PY
```
نداءات التحقق (من جلسة أخرى في نفس المجلد):
```bash
# 401 بدون توكن:
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/jobs
# 400 لمعرّف شبيه بمسار:
curl -sS -X POST http://127.0.0.1:8000/jobs -H "Authorization: Bearer $BEARER_TOKEN" \
  -H 'content-type: application/json' -d '{"type":"split","file":"../etc/passwd"}'
# 202 ثم متابعة الحالة:
curl -sS -X POST http://127.0.0.1:8000/jobs -H "Authorization: Bearer $BEARER_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"type":"split","file":"up-sample-5s.mp4","priority":5}' | tee "$EVIDENCE_DIR/07-create.json"
JOB_ID=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["id"])' "$EVIDENCE_DIR/07-create.json")
curl -sS -H "Authorization: Bearer $BEARER_TOKEN" "http://127.0.0.1:8000/jobs/$JOB_ID" | tee "$EVIDENCE_DIR/07-job.json"
# فك مستقل للمخرجات (المرجع نسبي مثل <job-id>/part_000.mp4):
REF=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["output_paths"][0])' "$EVIDENCE_DIR/07-job.json")
ffmpeg -hide_banner -nostdin -v error -xerror -i "$LAB/outputs/$REF" -f null -
# الحالة التالفة:
curl -sS -X POST http://127.0.0.1:8000/jobs -H "Authorization: Bearer $BEARER_TOKEN" \
  -H 'content-type: application/json' -d '{"type":"split","file":"up-corrupt.mp4"}'
# ثم أكّد غياب المخرجات الجزئية للمهمة الفاشلة تحت $LAB/outputs/<job-id>/
```
اختبار الاستمرارية: أوقف العامل بـSIGTERM، أنشئ مهمة (تبقى `queued`)، ثم أعد
تشغيل نفس أمر `aetherflow worker run` على نفس ملف SQLite وأكّد انتقالها إلى
`completed`. استخدم منفذًا شاغرًا غير مستخدم على الخادم.

### 2.4 قياس الأداء — البروتوكول القائم دون تعديل
```bash
# الوضع المعتمد للقبول: ملفان حقيقيان مرخّصان، ثلاث إعادات لكل مدة:
BENCH_REPEATS=3 \
BENCH_30M_INPUT=/path/authorized/media-30m.mp4 \
BENCH_2H_INPUT=/path/authorized/media-2h.mp4 \
FFMPEG="$(command -v ffmpeg)" FFPROBE="$(command -v ffprobe)" \
  bash scripts/benchmark_long_media.sh 2>&1 | tee "$EVIDENCE_DIR/09-benchmark.log"
cp data/bench/RESULTS.md "$EVIDENCE_DIR/09-RESULTS.md"
```
قواعد ثابتة من المصدر:
- المتغيرات المعترف بها: `BENCH_REPEATS` (افتراضي 3؛ لا تُنقص للقبول)،
  `BENCH_30M_INPUT`، `BENCH_2H_INPUT`، `FFMPEG`، `FFPROBE`، ومتغير المنفّذ
  `AETHERFLOW_VERIFY_CONCURRENCY` (يُسجَّل في صدر النتائج).
- بدون ملفي الإدخال يبني السكربت ملفين اصطناعيين بدمج عينة الخمس ثوانٍ
  (`-c copy`)؛ هذه نتيجة «اصطناعية» تُسجَّل كولا تُغلق المعيار.
- الملفات الحقيقية تُنسخ كما هي بلا إعادة ترميز، وتُسجَّل خصائصها بـffprobe.
- زمن الجدار يشمل فك الفك الإلزامي للمدخل ولكل جزء؛ التحقق السليم مدمج، فانخفاض
  الزمن بلا سلامة ليس نجاحًا.
- السكربت يحذف `data/bench` وحده (حارس مسار في الكود)؛ انسخ النتائج فورًا.
- لا تُخفِ أي محاولة فوق السقف، ولا تغيّر نص المعيار في
  `D2_MVP/05_ACCEPTANCE_CRITERIA.md`، ولا ترفع التزامن فوق عدد الأنوية.
- خط الأساس التاريخي (ساندبوكس نواتان، 3 إعادات، بعد Task020): وسيط 18.50ث
  لـ30 دقيقة (17.68/18.75/18.50) و68.95ث للساعتين (71.34/68.95/68.08) —
  تاريخي فقط، لا يُنسَب إلى VPS.

## 3) الطبقة الثالثة — تغييرات تشغيلية (ممنوعة افتراضيًا، بموافقة مسبقة صريحة)

| الأمر/الإجراء | أثره الموثّق من المصدر | سبب حجب الموافقة |
|---|---|---|
| `bash scripts/install_vps.sh` | تثبيت apt (ffmpeg وrsync وopenssh-client وpython3 ومستودع Docker الرسمي وDocker Engine/Compose)، تشغيل `init_wizard.py` (ينشئ `.env` وضع 0600)، إنشاء `data/` و`chown -R 1000:1000` عبر `sudo` بأوضاع 0750 (يتوقف بلا أي بديل 0777)، ثم `compose up -d --build` على المشروع الثابت | يعدّل نظام الخادم ويشغّل خدمة دائمة |
| `docker compose -f docker/docker-compose.yml up -d [--build]` | تشغيل المكدس الإنتاجي بمشروع `aetherflow` الثابت ونشر 80/443 | قد يصطدم بخدمة قائمة أو يعرّض وكيلًا عامًا |
| `docker compose ... restart/stop/down` | إيقاف/إعادة تشغيل خدمات قائمة | تأثير على التوفر؛ `down -v` يمسح وحدات تخزين — محظور |
| تعديل `.env` أو `DOMAIN`/`ACME_EMAIL` أو سجلات DNS أو الجدار الناري | تغيير سلوك TLS والوصول العام | قرار مالك الخادم وحده |
| أي `chmod`/`chown` خارج ما ينفّذه السكربت القائم، وأي `rm -rf` | تغيير صلاحيات/حذف محتمل | لا توجد حاجة إليه في المسار المعزول |
| توسيع عتاد/موارد مدفوعة أو خدمات سحابية | تكلفة وتغيير بنية | يتطلب موافقة صريحة حسب قيود المهمة |

القاعدة: لا حذف لمجلدات بيانات أو وحدات تخزين أو قواعد بيانات، ولا إعادة تهيئة،
ولا نشر لمنفذ 8000 (التصميم يقصر الدخول العام على Caddy عبر 80/443)، ولا دفع
إلى `main` ولا دمج PR #6.

## 4) فحوص شهادة TLS العامة (شرطية، قراءة فقط)

```bash
getent hosts "$DOMAIN"
curl -sS -o /dev/null -w 'http=%{http_code} ssl_verify=%{ssl_verify_result}\n' "https://$DOMAIN/health"
echo | openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
echo | openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" 2>/dev/null \
  | openssl x509 -outform PEM > "$EVIDENCE_DIR/10-cert.pem"
```
النجاح يتطلب جهة إصدار عامة موثوقة (سلسلة Let's Encrypt) و`ssl_verify=0`
ونطاقًا مطابقًا وتواريخ سارية. إصدار `Caddy Local Authority` يعني شهادة داخلية
للتطوير → النتيجة «غير متحقق» للبند العام، ولا يُكتب ادعاء عكسه.

## 5) إخفاء البيانات الحساسة قبل إرفاق الأدلة

1. راجع كل ملف قبل الإرفاق؛ لا تعتمد على أنماط الإخفاء وحدها.
2. نمط عام للاستبدال (عدّل القائمة حسب الحاجة، وراجع الفرق قبل الحفظ):
   ```bash
   sed -E \
     -e 's/(Bearer )[A-Za-z0-9._~+\/-]+/\1<REDACTED_TOKEN>/g' \
     -e 's/((BEARER_TOKEN|AGENTS_AUTH_TOKEN|FERNET_KEY|TELEGRAM_BOT_TOKEN|MODAL_TOKEN_ID|MODAL_TOKEN_SECRET|LITELLM_MASTER_KEY)=).*/\1<REDACTED>/' \
     -e 's/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/<REDACTED_EMAIL>/g' \
     "$EVIDENCE_DIR/raw.log" > "$EVIDENCE_DIR/clean.log"
   ```
3. عناوين IP الداخلية وأسماء المضيفين الحساسة تُخفى إن طلب مالك الخادم ذلك.
4. لا تلصق أبدًا: `.env` بمحتواه، مفاتيح SSH، كلمات المرور، أو التوكنات في
   محادثة أو تذكرة أو تعليق PR. التوكن يُمرّر بمتغير بيئة ويُفحص وجوده في
   السجلات بمتغير (لا بطباعته).
5. تحقق بعد الإخفاء:
   ```bash
   grep -E 'Bearer [A-Za-z0-9]|TOKEN=[^<]|BEGIN .*PRIVATE KEY' "$EVIDENCE_DIR"/*.log \
     && echo 'RESIDUAL SECRET — re-clean required' || echo 'clean'
   ```

## 6) قائمة مراجعة قبل تسليم الأدلة

- [ ] `git rev-parse HEAD` مسجَّل ومطابق لما اختُبر فعلًا، مع رابط تشغيل CI له.
- [ ] كل أمر له توقيت ورمز خروج ومخرجات محفوظة.
- [ ] `junit.xml` وملخصه، وسجل `verify_compose_stack.sh` الكامل، و`data/bench/RESULTS.md`
      مع القيم الخام الثلاث لكل مدة.
- [ ] لا أسرار بعد الإخفاء، ولا عناوين داخلية غير لازمة.
- [ ] كل بند في `ACCEPTANCE_MATRIX.md` له نتيجة فعلية أو علامة «غير متحقق»
      بسببه، ولا شيء مملوء مسبقًا.
- [ ] لا تعديل على `main`، لا دمج، لا تغيير لمعايير القبول، لا موارد مدفوعة.
