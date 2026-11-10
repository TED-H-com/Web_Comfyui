# Wav2Lip 影格對齊修補

本修補處理「部分影格偵測不到臉，卻回傳錯位、截短影片」的程式錯誤。它不增加動漫臉模型能力，也不保證真人影片的口型或畫質通過。內建工具契約仍是能清楚辨識真人臉部的影片。

來源為 [ShmuelRonen/ComfyUI_wav2lip](https://github.com/ShmuelRonen/ComfyUI_wav2lip/tree/4ed7fb15812acaf7d364d3a859c1523ad8b8274b)，固定版本 `4ed7fb15812acaf7d364d3a859c1523ad8b8274b`。目標檔案是 `Wav2Lip/wav2lip_node.py`；SHA-256 以 UTF-8、LF 換行計算：

| 狀態 | SHA-256 |
| --- | --- |
| 原始 | `9fc3a4297f4c99a5ae25028a27964b756b54b707546f865658610b4ded68056d` |
| 修補後 | `8e245edc70f41ec29c51732d4e72e7b36de15a86af0ab30401a90eb289ad3a4f` |

原節點的 `face_detect` 吞掉缺臉錯誤，將縮短的臉框陣列與完整影格陣列重新配對；`datagen` 再吞掉索引錯誤而略過語音片段。這會同時造成臉框錯位與輸出影格減少。修補在任何缺臉、無效臉框或數量不一致時直接回報中文錯誤，不輸出部分影片，也不將失敗影格存到磁碟。影格產生錯誤會向上傳遞，最終輸出數量必須等於語音 mel 片段數。

保留原節點的 `80/30` mel 取樣關係和既有工作流的 **30fps** 輸出。`sequential` 依語音長度映射完整影片，`repetitive` 按原影格順序重複。此修補不修改語音尾端 mel 視窗長度或 VHS 合成器的 `-shortest` 行為；因此不能以輸出影格數檢查替代完整音訊、時長和口型驗收。

先檢查明確指定的節點目錄：

```sh
python3 scripts/patch_wav2lip.py --node-dir /path/to/ComfyUI/custom_nodes/ComfyUI_wav2lip
```

確認 ComfyUI 已停止或沒有執行中工作後，明確套用並重新啟動 ComfyUI：

```sh
python3 scripts/patch_wav2lip.py --node-dir /path/to/ComfyUI/custom_nodes/ComfyUI_wav2lip --apply
```

預設僅檢查，不寫入。`--apply` 支援 Git checkout 和沒有 `.git` 的節點封裝，保留 LF／CRLF 與檔案權限，使用原子替換；已套用時回傳 `already_applied`。來源或產物雜湊不符會拒絕修改。`install.sh` 只在此選用節點已存在時套用，不下載此節點或模型。還原應重新取得上述固定版本的來源檔案，再重新啟動 ComfyUI；不要以反向套用覆寫其他人的後續修改。

驗證包含合成影格與假偵測器的離線回歸：缺臉不得錯配／略過影格、有效序列與循環模式保持對應、128 格批次邊界、輸出數量、未知版本拒絕、檢查不寫入及重複套用。沒有在這組測試內載入模型、執行 GPU 或存取帳戶／使用者媒體。

```sh
pytest-disktmp uv run --with pytest --with numpy pytest -q tests/test_wav2lip_patch.py
```

此處僅提供來源修補工具，不包含模型權重。上游 [Wav2Lip README 的使用限制](https://github.com/ShmuelRonen/ComfyUI_wav2lip/blob/4ed7fb15812acaf7d364d3a859c1523ad8b8274b/Wav2Lip/README.md#disclaimer) 將其程式／模型用途限制於個人、研究及非商業用途；本修補不改變那些條款。
