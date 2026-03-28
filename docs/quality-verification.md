# Quality Verification

## Gold Standard Pipeline

The highest quality standard for the library:

1. **Download lossless** (FLAC, ALAC, WAV) from Soulseek
2. **Verify with spectral analysis** — confirm the lossless file is genuinely lossless (not a lossy transcode wrapped in a lossless container)
3. **Convert to VBR V0** — `ffmpeg -codec:a libmp3lame -q:a 0`
4. **Import to beets** — the VBR V0 bitrate acts as an ongoing quality fingerprint

Why VBR V0 and not keep FLAC? Because VBR bitrate IS the quality signal. A genuine CD rip converted to V0 produces ~240-260kbps. A transcode produces ~190kbps. CBR 320 would hide this information.

## Current Verification Methods

### 1. VBR V0 Bitrate Check (implemented)

After FLAC-to-V0 conversion, the resulting bitrate reveals source quality:
- **Genuine lossless**: ~220-280kbps (varies by musical complexity)
- **Transcode from ~192kbps**: ~190-210kbps
- **Transcode from ~128kbps**: ~160-180kbps

Threshold: `TRANSCODE_MIN_BITRATE_KBPS = 210` in `import_one.py`

Limitation: Only works when we download FLAC and convert. Doesn't catch bad MP3 downloads (e.g. 320kbps that was upsampled from 128kbps).

### 2. Spectral Band Energy Analysis (research phase)

Uses `sox` bandpass filtering to measure energy ratios in high-frequency bands relative to a 1-4kHz reference band. Genuine high-quality audio has consistent energy across the spectrum. Transcodes show a sharp drop at the original encoding's lowpass cutoff frequency.

#### Test Results (2026-03-28)

```
Label                                  ref RMS     14-16k%  16-18k%  18-20k%
Genuine FLAC (lossless)                0.118154      4.1%     2.7%     1.8%
Genuine V0 (from FLAC)                 0.118188      4.2%     2.8%     1.8%
Genuine 320 (from FLAC)                0.118158      4.1%     2.7%     1.8%
TRANSCODE 128->320                     0.112545      4.2%     1.0%     0.0%
TRANSCODE 192->320                     0.114673      4.2%     2.2%     0.7%
Hot Garden Stomp (suspect 320)         0.075199      0.7%     0.6%     0.4%
```

Observations:
- Genuine V0 is spectrally identical to FLAC — the conversion preserves the quality fingerprint
- The **18-20kHz band** is the most discriminating: 0.0% for 128 transcode vs 1.8% for genuine
- The **16-18kHz band** separates 192 transcodes: 1.0% (128-transcode) vs 2.2% (192-transcode) vs 2.7% (genuine)
- Hot Garden Stomp (320kbps, 1993 cassette) has less high-frequency energy than a 128->320 transcode — source was likely ~96kbps or lower
- LAC (Lossless Audio Checker) is useless for this purpose — reported "Clean" on all files including obvious transcodes

#### Method

```python
# For each track, measure RMS energy in bandpass-filtered ranges
sox file.mp3 -n sinc 1000-4000 stat    # Reference band (1-4kHz)
sox file.mp3 -n sinc 14000-16000 stat  # High frequency band 1
sox file.mp3 -n sinc 16000-18000 stat  # High frequency band 2
sox file.mp3 -n sinc 18000-20000 stat  # High frequency band 3

# Calculate: band_energy / reference_energy * 100 = percentage
# Genuine: 14-16k > 2.5%, 16-18k > 2.0%, 18-20k > 1.0%
# Suspect: any band significantly below these thresholds
```

Dependencies: `sox` (in nixpkgs)

#### Known Frequency Cutoffs by Original Bitrate

| Original Bitrate | Lowpass Cutoff | 14-16k% | 16-18k% | 18-20k% |
|-----------------|----------------|---------|---------|---------|
| 96kbps          | ~14kHz         | < 1%    | < 1%    | < 1%    |
| 128kbps         | ~16kHz         | normal  | ~1%     | ~0%     |
| 192kbps         | ~19kHz         | normal  | normal  | < 1%    |
| 256kbps         | ~20kHz         | normal  | normal  | reduced |
| 320kbps/V0      | ~20.5kHz       | normal  | normal  | normal  |
| Lossless (CD)   | 22.05kHz       | normal  | normal  | normal  |

#### Edge Cases

- **Lo-fi recordings** (boombox, cassette, AM radio): Naturally have limited high-frequency content. The energy ratio approach handles this because it compares RELATIVE to the 1-4kHz band, not absolute levels. But very lo-fi material may have low ratios simply due to recording quality, not transcoding.
- **Classical/acoustic music**: May have less high-frequency energy than rock/electronic, but still maintains relative proportions. Need wider thresholds.
- **Cassette recordings**: Tape hiss adds energy across all frequencies including high bands. Genuine cassette rips may actually show MORE high frequency energy (as noise) than clean digital recordings.

## TODO

- [ ] Integrate spectral check into `import_one.py` post-download pipeline
- [ ] Determine reliable thresholds that minimize false positives
- [ ] Handle per-track vs per-album decisions (flag album if majority of tracks fail?)
- [ ] Add spectral quality score to pipeline DB and web UI display
- [ ] Research: should we run spectral check on downloaded FLACs BEFORE conversion, or on the converted V0 files, or both?
- [ ] Consider: for MP3 downloads (non-FLAC), run spectral check post-download to catch upsampled 320s
