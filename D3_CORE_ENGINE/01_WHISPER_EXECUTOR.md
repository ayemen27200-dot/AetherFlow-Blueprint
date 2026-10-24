# D3 — Whisper Executor Specification

## المساران
1. `faster-whisper` على VPS (CPU/GPU بحسب المتاح).
2. `whisper.cpp` على هاتف ARM64 فقط ضمن هذه الخطة.

## faster-whisper — مثال مبدئي
```python
from faster_whisper import WhisperModel

model = WhisperModel("small", device="cpu", compute_type="int8")
segments, info = model.transcribe(
    audio_path,
    language="ar",       # None للكشف التلقائي
    beam_size=5,
    word_timestamps=True,
    vad_filter=True,
)
```
هذا المقتطف لا يُنتج ملفات SRT/VTT/JSON تلقائياً؛ يجب تحويل segments وwords إلى صيغة خروج محددة واختبار الترميز الزمني.

## خيارات إعداد أولية
| خيار | قيمة مقترحة | ملاحظة |
|---|---|---|
| device | cpu أو cuda | بحسب توفر GPU وتوافق الحزم |
| compute_type | int8 للـCPU؛ float16 للـGPU عند دعمها | benchmark الدقة والسرعة |
| beam_size | 5 | نقطة بداية للتقييم |
| vad_filter | true | قد يؤثر على مقاطع الكلام القصير |
| word_timestamps | true | يزيد التكلفة؛ استخدم عند الحاجة |

## whisper.cpp على الهاتف
```bash
git clone https://github.com/ggml-org/whisper.cpp
cd whisper.cpp
cmake -S . -B build -DGGML_NO_OPENMP=ON
cmake --build build -j"$(nproc)"
# نزّل النموذج من script الرسمي بعد مراجعة المصدر والترخيص والـhash
```

مثال تشغيل (تحقق من الخيارات وفق إصدار binary المثبت):
```bash
./build/bin/whisper-cli -m models/ggml-base.bin -f input.wav -l ar -t 4 --output-srt --output-vtt --output-json
```

## قيود
- لا تستخدم `faster-whisper` على الهاتف.
- لا تفترض أن أعلام بناء OpenMP/ARM NEON نفسها متاحة في كل إصدار؛ افحص CMake options للبنية المثبتة.
- اجعل threads على الهاتف قابلة للضبط، وابدأ بقيمة ≤4 لتقليل الحمل الحراري.

## اختيار النموذج
اختيار النموذج يتأثر باللغة والضجيج والذاكرة وطول الصوت والسرعة المطلوبة. لا تعتمد جدول دقة/زمن ثابتاً دون قياس.

## معالجة الصوت
```bash
ffmpeg -i in.mp4 -ar 16000 -ac 1 -c:a pcm_s16le audio.wav
```
يمكن تجربة خفض الضوضاء اختيارياً، مع اختبار ألا يضر بالكلام. تحقق من وجود مسار صوتي قبل التحويل.

## المخرجات
- `job_<id>.srt`
- `job_<id>.vtt`
- `job_<id>.json` يتضمن segments وtimestamps، وwords إن كانت متاحة.

## الأداء
أنشئ benchmark محلياً لمقطع معروف، وسجل الجهاز/CPU/GPU/model revision/compute type والدقة. الأزمنة المذكورة في الوثائق التخطيطية لا تُعتبر ضماناً.
