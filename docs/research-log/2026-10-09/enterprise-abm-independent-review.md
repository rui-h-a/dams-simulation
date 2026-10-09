# Enterprise ABM integration r1 獨立工程審查

結論：**恢復語義檢查未通過，需修復後重驗**。這是限定候選 slice 的工程判斷，不是正式模型／科學接受；未校準、企業各自財務與結構成長等已明示剩餘項目並未用來否決本次工程審查。

## 阻擋缺陷

**P2：day-0 恢復接受沒有發生過的同步，能通過所有現有 snapshot gate 並改變任務结果。**

- 固定 package SHA `d003d0116c8d68975c25611cccb4113618c0f52add5806bc44abdbfba09632a9`；原候選與日誌均未變動。
- 以 `fixture_config(us_offset=-8)` 建立 DE +1／US -8 非重疊工作日曆。對其 `model.state()` 複本，只改 `enterprise_runtime.tasks[1].coordination_done=[0]`；前置 task 0 尚未完成，day 0 沒有同步事件。
- `Model.restore`、`write_checkpoint`、`verify_snapshot`、`validate_persisted_state`、`Model.restore_checkpoint` 全部接受。兩條錯誤恢复路徑同源完全一致，沒有重新計算或繞過既有輸出 checksum。
- clean world 的 task 1 在 8 天內不完成；錯誤初態恢復兩路都在第 **43 clock hour** 完成 task 1，**coordination events=0，coordination units=0**。因此哈希一致不等於同步機制或初始化歷史有效。
- 原因：`dams_sim/enterprise_integration.py:463` 只檢查 `coordination_done` 是合法前置 ID 的子集；`:488` 只有 `engine.day>0` 才核對 runtime 與 journal。現有 `ent:init` 保存正確空同步状态，却從未在 day 0 校驗。
- **Postday 邊界**：只改 valid postday runtime 而不改 journal 會被 latest `enterprise_day_end.runtime_state_sha256` 拒絕；不是 checksum bypass。但 validator 沒有核對旗標與實際 `ent:sync` 歷史。此已接受的 day-0 錯誤初態延續到 day 8，兩路的 `validate_semantics` 與 final snapshot 仍通過，而旗標 `[0]`、全部同步事件為零；後續正常 day-end hash 封存了無根據的初態。
- 修復方向：此 synthetic version 的 day-0 runtime 必須與新生成初態及唯一 `ent:init` 完整一致；先校驗初始化歷史，再接受 restore／persisted gate。同步旗標應滿足前置完成及已記錄同步事件的必要條件。加以上非重疊日曆反證，確認錯誤初態在所有 restore／persisted 路徑被拒絕。

完整可重現輸入、clean 與兩個 corrupt final snapshots：`initial-sync-causal-repro/`；結果 `initial-sync-causal-repro/result.json`；控制程式 `reproduce_initial_sync.py`。命令：

```sh
python3 <private-workspace>/private-validation/enterprise-abm-independent-review-r1/reproduce_initial_sync.py
```

輸出目錄採不可覆寫策略；重驗前須在新 review 根目錄執行同程式，或只調整該程式的獨立輸出位置，不能覆寫本輪反證。

## 其他已重現問題

1. **P3，型別別名**：`enterprise_manager_person=False` 對 expected person 0 的 tuple equality 成立；`Model.restore` 接受後 `_topology` 靜默改成整數 0。位置 `enterprise_integration.py:481`，`longitudinal_model.py:643`。需對新增的整數／可空整數引用檢查實際型別，再作值比較。此反證只證明 direct restore 靜默正規化；**不宣稱繞過 grouped checkpoint 最終完整 digest 檢查**。
2. **P3，失敗恢復的 DB ownership**：新增 enterprise reference validator 拋錯後，新建 `ExactLedger` connection 仍可執行 `SELECT 1`，沒有 close。位置 `longitudinal_model.py:640`。這沿用既有 restore 的例外清理模式，尚未主張為相對原版新增的所有類型 leak；但本次新增拒絕路徑確實漏收 owned connection。20 tests 本次執行亦出現 Python 3.14 unclosed database ResourceWarning。外部傳入的 ledger ownership 與本函式新建 ledger 須分清。

證據：`controls/results.json`、`control_review.py`。

## 已通過與未測範圍

- 31 個 source／tests／runners 逐檔 SHA 全吻合；canonical manifest SHA `62f1b18ae95f75f351235a7ec06aa28e271730d771d00c7631bf755fba9ef475`；patch SHA `e1f9d50987b6f9c7b326af69a63abb69682a4aed5009f9e44e9bbafd328a8429`。package SHA 在控制前後相同。審查結束重新核對上游所有檔案亦未變動。
- 在自己的 source-only 複本，20 個原候選 tests 實際通過（1.450s）；有上述 ResourceWarning，並非全零警告。
- 5 個新增小型控制：24/48-hour shock 邊界、manager 空缺、初始容量內縮編後回補、3-hour work window 與 +14/−12 UTC offsets、兩個同步前置。每個都是 8 人／10 天，day 3 checkpoint；純 persisted gate、逐時個人容量、逐日 semantic gate 與不中斷／恢復最終 full SHA 一致皆通過。
- 未獨立重跑候選 30-second runtime、opt-out 原版差分或 patch 再套用；本輪閱讀相關上游證據，不把其執行冒稱本輪執行。未執行 native storage、pipeline／driver、付費雲端、五／十年或大型人口。
- 已明示的共享原財務／領域、historical burn-in、generator adapter、native storage、結構成長、估計／校準／holdout、driver／cost admission 與長期實驗，仍是後續科學或整合範圍。五個控制不填補这些項目。
- Claude 未呼叫；無 provider call、無 empirical target access、無付費資源、無 Git ref 或正式來源修改。所有新產物只在此私人 review 目錄。
