# Original synthetic voice examples

In the original set (`--set original`, the default),
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

## Closed-loop demo voices (--set demo2)

With `--set demo2`, `scripts/generate_voice_examples.py` replays 16 tracks (8
voice-design clips and 8 synthetic-reference clones) using the same VoxCPM2
engine and settings. The voice designs cover adult women and men for the
fictional demo subjects 晴晴, 志遠, 星羽 and 辰翼, two English greetings
(`qingqing-english` and `zhiyuan-english`), a male station announcement
(`male-announcement`), and the first mountain slide (`mountain-slide-one`). The
8 clones use the script's own generated voice-design clips, exact transcripts,
and receipt hashes as references: slide pages two and three
(`mountain-slide-two` and `mountain-slide-three`) clone page one
(`mountain-slide-one`) so one narrator reads the slide batch, while
`qingqing-clone`, `zhiyuan-clone`, `xingyu-clone`, `chenyi-clone`,
`qingqing-english-clone`, and `zhiyuan-english-clone` clone their corresponding
Mandarin or English greeting clips.

For `--set demo2` only, each generated waveform passes through the deterministic
`demo2-silence-trim-v1` silence post-process before saving and measuring signal
metrics (`--set original` is untouched). It evaluates 10 ms frames (`frame_ms`
10) against a voiced threshold 40.0 dB below the loudest frame with a 0.0001 RMS
floor (`threshold_db_below_loudest_frame` 40.0, `floor_rms` 0.0001), keeps up to
120 ms of leading and trailing margin (`edge_keep_ms` 120), shortens internal
unvoiced pauses longer than 700 ms down to 700 ms (`max_pause_ms` 700), joins cut
segments with a 10 ms linear crossfade (`crossfade_ms` 10), and applies a 5 ms
fade ramp at both outer edges (`edge_fade_ms` 5), recording the post-process
summary in each sample's `postprocess` receipt field.

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=8 MKL_NUM_THREADS=8 OPENBLAS_NUM_THREADS=8 \
python scripts/generate_voice_examples.py \
  --model /explicit/path/to/base/VoxCPM2 --output /explicit/output/directory \
  --set demo2 --device cpu --threads 8 --clone
```

The `--set demo2` tracks have the same validation limits as the original set:
focused tests check CLI input boundaries and hashes without loading a model,
recorded outputs receive signal checks and automated transcription review, and
listening quality, naturalness, distinct voice identities, and Taiwanese accent
are not established by automated ASR or by a successful render.

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

原始組合（`--set original`）都是 VoxCPM2 原創成年女性合成聲音，不是多個不同引擎。
中文提示要求自然台灣華語，包含迎賓、遊戲主持、故事、播報與三頁簡報；
另有英文迎賓。三頁簡報使用相同描述與各自記錄的種子，但不保證聲音身分完全一致。
可選的聲音延續只使用本程式剛生成的迎賓音訊，不使用私人或真人錄音。
閉環示範語音組合（`--set demo2`）共 16 段音軌（8 段聲音設計與 8 段合成參考克隆），
涵蓋晴晴、志遠、星羽與辰翼四位虛構示範角色的成年男女聲、兩段英文問候、男聲車站廣播與山景簡報第一頁；
8 段克隆音軌皆以本程式生成的聲音設計音檔、原始逐字稿與收據雜湊作為參考，其中山景簡報第二頁與第三頁克隆第一頁以維持同一旁白，其餘六段則分別克隆四位角色的中英文自我介紹。
此外，僅針對 `--set demo2`，生成波形會在寫入前執行 `demo2-silence-trim-v1` 靜音修剪後處理（`--set original` 保持原樣不變）：
以 10 毫秒音框（`frame_ms` 10）計算均方根音量，門檻設為最大音框以下 40.0 dB 且不低於 0.0001（`threshold_db_below_loudest_frame` 40.0、`floor_rms` 0.0001），
首尾各保留最多 120 毫秒邊界（`edge_keep_ms` 120），將中間超過 700 毫秒的靜音停頓裁減至 700 毫秒（`max_pause_ms` 700），
切點以 10 毫秒線性交叉淡化銜接（`crossfade_ms` 10），並在首尾兩端施加 5 毫秒漸入漸出（`edge_fade_ms` 5），同時將後處理紀錄寫入收據的 `postprocess` 欄位。
兩組範例的驗證限制相同：請實際試聽確認口音與音質；自動轉錄只能協助檢查內容。
