# Media test fixture

`sample_5s.mp4` is a generated synthetic test clip (5 seconds, 320×240, 30 fps, test pattern + 440 Hz tone). It contains no real person's media.

Regenerate it when FFmpeg is installed:

```bash
bash scripts/generate_test_fixture.sh
```

Equivalent FFmpeg command:

```bash
ffmpeg -f lavfi -i 'testsrc=duration=5:size=320x240:rate=30' \
       -f lavfi -i 'sine=frequency=440:duration=5' \
       -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest -movflags +faststart \
       tests/fixtures/sample_5s.mp4
```

The clip validates basic media handling only; it is not a representative OCR, Arabic subtitle, motion, inpainting, GPU, or long-video quality benchmark.
