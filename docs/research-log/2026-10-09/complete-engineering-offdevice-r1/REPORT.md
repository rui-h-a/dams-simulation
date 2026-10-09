# 完整工程案例獨立故障域備份：有限驗收通過

已在新 GitHub prerelease `engineering-complete-3743d-20261009-r1` 保存 attempt-003／N=1000／world=20000／3743日工程案例的完整12個科學原輸出，並实际完整下載全部資產及公開 readback receipt。每個 logical filename 均由下載 tar 以原版 codec 解碼、回算全raw bytes／SHA，合計 **14,762,983,406 bytes**，12/12吻合。此工作完成不代表正式MC world/group或整體goal完成。

- Release：https://github.com/rui-h-a/dams-simulation/releases/tag/engineering-complete-3743d-20261009-r1
- 公開 readback receipt：https://github.com/rui-h-a/dams-simulation/releases/download/engineering-complete-3743d-20261009-r1/complete-readback-receipt.json
- Receipt bytes／SHA：6159／`7f305bc4d9ed011c429d980cefcc1ee941dfe7c61e61d76445462e7bcf668126`
- Tar bytes／SHA：1,002,874,880／`e9f18ff85e0e4e2b801a4c5a8e87de2d40216a97213103aabfc05a45aa96e7c3`
- 實際971個unique原encoded CIDs，payload 1,001,216,032 bytes；tar大小另包含metadata、header、padding。沿用原encoded bytes，沒有再編碼。
- 公開 reference tag已实际讀回為commit0902037cb41320b09f86873fbb4da30e5ae12c68；仍為prerelease、非latest（latest維持paper-2026-10-08）。原五資產ID／size／digest／state在receipt追加後保持一致，未clobber任何資產。

## 原始檔案與公開界線

原13個logical files全部有清單；**12科學原檔exact-public，私人原manifest不是第13個exact-public原檔**。7,146-byte原manifest／SHA86e90d8092923594444df61b66c26cb14d3065cfc67f54bb68b4461b41d2968a仍完整保存在原案例與private-evidence。公開manifest-sanitized.json是明示derivative，移除machine/platform/python/git_commit/git_dirty/container_image/execution_provenance；私人原manifest的encoded CID與原outer sealed操作內容均未進公開tar。portable-recipe.json以自身hash綁定公開roster，不冒充原Collector seal/group。

原12檔全raw byte privacy scan通過後才打包；公開source/recipe/notes亦已檢查。初次build在來源通用CLI flag的sk-子字串被保護性拒絕，失敗script/log/receipt保留。修正只容許 exact --boot-disk-provisioned-throughput flag、exact ff680…controller source SHA；科學raw與公開metadata掃描未放寬。

## 實際恢復與科學範圍

以下載回來的recover.py與下載tar執行，先固定held FD、完整archive SHA、bounded regular USTAR metadata、原codec/source SHA；每檔先validate_manifest，再依原chunk順序解碼，檢查每個chunk與全logical raw hash。預設只串流verify，未寫出新14.76GB raw複本；optional restore-dir仍可依附帶原restore_file API重建十二檔。本次未重跑SQLite／模型語意gate，引用同一case/source4daf/driver65dd/spec40eb/configa895/manifest86e9已通過的原individual engineering rawgate receipt；不把byte恢復當成新的科學驗收。

獨立source review發現held-FD與tar parsing bounds缺口，在打包前修復；原版本review、修正與最终utility hash保留。唯一本次source freeze核驗確認原source modules/config/manifest/producer proofs及3,528原encoded檔physical anchors未變。所有source/raw/歷史failed attempts保留；未修改模型、種子、參數、main/canonical Git或原計費／spool帳本。

## 有限門檻

Python AST syntax, archive build, complete encoded-content readbacks, all 12 exact raw recoveries, receipt download and source stability passed. No new scientific execution or independent sample is inferred. Original operation evidence is retained privately.
