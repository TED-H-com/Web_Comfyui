# Original synthetic female voice examples

`scripts/generate_voice_examples.py` replays eight short voice-design clips and
an optional synthetic-reference continuation. All use **one engine: VoxCPM2**.
They are not examples of BreezyVoice, F5, or another backend. The descriptions
request generic adult female voices: warm greeting, energetic game host,
gentle narration, mature announcement, three presentation slides, and an
English greeting. Taiwan Mandarin is requested for Chinese examples; the
prompt alone does not prove the resulting accent.

The three slides share a voice description; each recorded seed is in the receipt. This does not guarantee a
consistent speaker. The optional clone instead uses the script's own generated
`warm-welcome.wav` as both reference and continuation prompt, with its exact
transcript and a verified receipt hash. No real person's recordings, private
presets, custom LoRA, API keys, or cloud services are used.

## Reproduction

Install [OpenBMB/VoxCPM](https://github.com/OpenBMB/VoxCPM) in a separate Python
environment. The reviewed code revision is
`30118a33522ef2d8698fca9bebac7224af242395` and reviewed model is
[openbmb/VoxCPM2](https://huggingface.co/openbmb/VoxCPM2/tree/32279effe8c19989596f05d353d1447f51d9e915).
Download the base model explicitly according to upstream instructions. Both
code and model declare Apache-2.0; see the [code license](https://github.com/OpenBMB/VoxCPM/blob/30118a33522ef2d8698fca9bebac7224af242395/LICENSE)
and [model card](https://huggingface.co/openbmb/VoxCPM2/blob/32279effe8c19989596f05d353d1447f51d9e915/README.md).
This repository does not redistribute weights or make guarantees about model
training-data provenance.

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=8 MKL_NUM_THREADS=8 OPENBLAS_NUM_THREADS=8 \
python scripts/generate_voice_examples.py \
  --model /explicit/path/to/base/VoxCPM2 --output /explicit/output/directory \
  --device cpu --threads 8 --clone
```

Use `--only synthetic-clone` to add the clone to a directory already containing
this script's original greeting and matching receipt. `--device cuda` is an
explicit option; coordinate shared GPU usage before selecting it. The recorded
examples used CPU to avoid interfering with an active ComfyUI GPU job. Default
settings are CFG 2, 10 steps, no denoiser, no compilation, and no automatic
bad-case retry. Seeds, complete prompts, durations, signal levels, SHA256
hashes, and explicit runtime settings are saved in `receipt.json`.

The replay records the selected model hashes alongside reviewed upstream
revisions. A different local model is not automatically claimed to match that
revision. No ambient model-path discovery or private configuration lookup is
performed. Audio is saved as 48 kHz PCM16 when using the reviewed base model.

## Validation limits

The focused tests check CLI input boundaries and hashes without loading a
model. Recorded outputs receive signal checks and automated transcription review.
The agent has not assessed listening quality; users can preview the audio to
judge naturalness and accent. Automated ASR is useful for spotting missing or
changed words, but cannot establish naturalness, distinct voice identities,
or a Taiwanese accent. A successful render does not prove those qualities.

## 中文說明

這組範例都是 VoxCPM2 原創成年女性合成聲音，不是多個不同引擎。
中文提示要求自然台灣華語，包含迎賓、遊戲主持、故事、播報與三頁簡報；
另有英文迎賓。三頁簡報使用相同描述與各自記錄的種子，但不保證聲音身分完全一致。
可選的聲音延續只使用本程式剛生成的迎賓音訊，不使用私人或真人錄音。
請實際試聽確認口音與音質；自動轉錄只能協助檢查內容。
