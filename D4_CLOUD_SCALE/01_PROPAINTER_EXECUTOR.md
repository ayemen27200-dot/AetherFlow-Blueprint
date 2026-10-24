# D4 — ProPainter Executor Specification

## الهدف
إزالة نصوص/ترجمات مدمجة (hardcoded) عبر كشف مناطق النص وإنشاء mask ثم inpainting. النتيجة إعادة بناء تقديرية للخلفية وليست استرجاعاً مضموناً للمعلومات التي غطاها النص.

## السلسلة المقترحة
1. كشف المناطق (EasyOCR أو أداة مناسبة) وإنشاء mask.
2. توسيع mask بقدر صغير قابل للضبط لتغطية الحواف.
3. تشغيل ProPainter وفق واجهة المشروع وإصداره المثبت.
4. دمج المقاطع والتحقق من fps والدقة والمدة والصوت.

## واجهة Executor تصورية
```python
class ProPainterExecutor:
    def __init__(self, chunk_sec: int = 300, overlap_sec: int = 30):
        self.chunk_sec = chunk_sec
        self.overlap_sec = overlap_sec

    def run(self, job_spec: dict, on_progress) -> dict:
        # TODO: validate media; split with overlap; build mask; invoke pinned model
        # TODO: blend/crop overlap correctly; validate output; persist checkpoint
        raise NotImplementedError
```

## Chunking
- قيمة أولية: `chunk_sec=300`, `overlap_sec=30`.
- اختبار الحدود بصرياً وصوتياً؛ لا يُفترض أن crossfade صالح للفيديو دائماً أو أن يضمن إزالة artifacts.
- خزّن checkpoint في مسار محكوم، ولا تقبل مسارات من المستخدم.

## EasyOCR
يجب تشغيل كاشف النص في بيئة مدعومة وبموارد ملائمة. لا تستخدمه على الهاتف وفق قيود المشروع. مثال الاستدعاء يعتمد على إصدار EasyOCR وطرق تحويل الإطارات؛ اختبر الأوزان واللغات والترخيص.

## مخرجات
- `job_<id>_clean.mp4`
- `job_<id>_meta.json` بالدقة وfps والمدة ونسخ الأدوات
- mask preview اختياري، مع حماية خصوصية المحتوى

## الأداء والجودة
الزمن والجودة يعتمدان على طول الفيديو والدقة وGPU ونسخ النموذج. استخدم عينة تمثل النص العربي/الإنجليزي والحركة؛ قيّم الجودة بصرياً وبمعيار موثق، ولا تعِد بنسبة نجاح 90% دون اختبار.
## Mask Selection and Review

ProPainter requires a frame-aligned binary mask. The system must not launch inpainting when no mask exists or the mask/video dimensions and frame count are inconsistent. Mask creation is an explicit, reviewable stage; OCR does not guarantee correct subtitle detection.

### Supported modes

1. **Manual region (safe default for v1):** the user draws or selects a normalized rectangle `{top, bottom, left, right}` with values in `0..1`. Example: `{top: 0.82, bottom: 1.0, left: 0.0, right: 1.0}`. Generate a preview and ask the user to confirm it. A full rectangle is simple but may remove background content inside that region, so expose the rectangle visually and allow adjustment.
2. **OCR-assisted mask:** run a pinned OCR implementation on a configurable sample grid and on the frames/segments where text is expected. Keep only boxes meeting configured confidence and size/aspect-ratio filters, clip boxes to the selected region, expand by a configurable pixel margin, then rasterize the masks at original frame resolution. Sampling once every 30 seconds is not sufficient to prove all subtitle variants have been detected; record sample cadence and allow full review.
3. **Hybrid (preferred once validated):** use the manual region as the allowed search area, use OCR boxes to refine individual-frame masks, and combine with temporal smoothing/tracking. Save the unmodified OCR proposals and final mask metadata for review.

### Job contract

- `mask_region` is required for the manual/hybrid workflow unless a user explicitly chooses a reviewed automatic-mask workflow.
- Store masks as a lossless binary frame sequence or a lossless mask video with exactly the source frame count, frame rate and width/height. Do not use a lossy MP4 mask that introduces gray pixels unless the inference adapter explicitly binarizes it.
- Produce `mask_preview.jpg` and a short contact sheet/sample before the full job. Keep the user able to cancel or adjust the region.
- Persist `mask_method`, OCR/model version, selected region, threshold, dilation margin, sampled frame indices, and source media fingerprint in job metadata.
- If OCR returns no boxes, a frame decode fails, or validation fails, stop and ask for manual selection; never silently substitute an all-white or all-black mask.
- Do not run EasyOCR on the phone. OCR/inpainting must be assigned only to a compatible VPS/GPU or explicitly consented cloud worker.
- Never claim mask selection is “100% reliable”. Validate Arabic/English, changing captions, scene cuts, bottom/side captions, outlines/shadows and moving backgrounds with test data.

### Normalized coordinates

Convert a normalized coordinate to pixels using the decoded frame dimensions and clamp to `[0, width-1]` / `[0, height-1]`. Reject `bottom <= top` and `right <= left`. Add expansion in pixels after conversion, clamp again, and define whether coordinates refer to edges or pixel centers consistently in tests.

### Required acceptance checks

- Visual preview matches the intended subtitle area before inpainting.
- Mask dimensions and frame count match the source exactly.
- Zero detected text does not create an empty mask without user confirmation.
- The test set contains captions that change over time and subtitles outside the bottom band.
- Output preserves intended FPS, duration and audio, with visual review of inpainting artifacts.
